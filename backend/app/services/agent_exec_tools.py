"""Research and execution tools for the agentic chat.

This is the "full access" tier. The read-only tools in ``agent_tools`` can
only look at the machine; the tools here can go out to the internet, run
shell commands, and write and execute code.

They are registered only when the caller sets ``full_access`` and, because
they are strictly more powerful than the engine start/stop tools, that flag
implies ``allow_actions`` as well.

Security posture -- read this before enabling the tier:

  The dashboard container has ``/var/run/docker.sock`` mounted. Anything
  running as this user can therefore ask the Docker daemon for a container
  with the host mounted into it, which is root on the host. ``run_shell``
  does not add a new privilege boundary; it removes one that the
  ``allow_actions`` tier was implicitly relying on.

  The ``_DENIED_RE`` patterns below are a guard against a model typing
  something catastrophic by accident. They are a speed bump, not a
  sandbox, and are deliberately not presented to the user as protection.

Everything here is bounded: commands get a hard timeout, output gets
clipped, and fetches get a byte cap. That keeps one runaway tool call from
either hanging the request or flooding the model's context.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
from typing import Any, Optional

import httpx
from agents import FunctionTool, function_tool

DEFAULT_COMMAND_TIMEOUT = 120.0
MAX_COMMAND_TIMEOUT = 900.0
WEB_TIMEOUT = 20.0
MAX_FETCH_BYTES = 400_000
MAX_OUTPUT_CHARS = 12_000

# TLS verification for fetch_url/search_web. On by default. Set
# AGENT_TLS_VERIFY=0 only if a deployment sits behind a TLS-terminating
# proxy whose CA is not in the container trust store.
_TLS_UNSAFE = os.getenv("AGENT_TLS_VERIFY", "1").strip().lower()
_TLS_VERIFY = _TLS_UNSAFE not in ("0", "false", "no", "off")

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Catastrophic-and-never-useful patterns. See the module docstring: this is
# accident insurance, not a security boundary.
_DENIED_PATTERNS = (
    r"\brm\s+(-[a-zA-Z]*\s+)*(/|/\*|~)(\s|$)",   # rm -rf /
    r"\bmkfs(\.|\s)",
    r"\bdd\b.*\bof=/dev/",
    r"\b(shutdown|reboot|halt|poweroff)\b",
    r">\s*/dev/sd[a-z]\b",
    r"\bchmod\s+(-[a-zA-Z]*\s+)*R\s+777\s+/(\s|$)",
)
_DENIED_RE = tuple(re.compile(p) for p in _DENIED_PATTERNS)

_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|noscript|svg|iframe)\b.*?</\1>", re.S | re.I
)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACES_RE = re.compile(r"[ \t\r\f\v]+")


def _clip(value: Any, limit: int = MAX_OUTPUT_CHARS) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        text = str(value)
    if len(text) > limit:
        return text[:limit] + f"\n... [truncated, {len(text)} chars total]"
    return text


def _denied(command: str) -> Optional[str]:
    for rx in _DENIED_RE:
        if rx.search(command):
            return f"refused: command matches destructive pattern {rx.pattern!r}"
    return None


def html_to_text(raw: str) -> str:
    """Crude but adequate HTML -> readable text.

    Not a browser. It drops scripts and styles, turns block-level tags into
    newlines, strips the remaining tags, and unescapes entities. Good
    enough for an LLM to read a page; not a substitute for a DOM.
    """
    # Script/style before comments: a JS block containing `-->` would
    # otherwise break the </script> boundary and leak code into the text.
    text = _SCRIPT_STYLE_RE.sub(" ", raw)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(
        r"</(p|div|li|h[1-6]|tr|section|article|header|footer)>", "\n", text, flags=re.I
    )
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    # Normalise per line, then drop the empty lines the block tags left behind.
    lines = [_SPACES_RE.sub(" ", ln).strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return text.strip()


def _resolve(path: str, workspace: str) -> str:
    """Resolve against the workspace unless an absolute path was given."""
    candidate = path if os.path.isabs(path) else os.path.join(workspace, path)
    return os.path.realpath(candidate)


def _unwrap_redirect(href: str) -> str:
    """Extract the real target from a search-engine redirect link.

    DuckDuckGo uses ``//duckduckgo.com/l/?uddg=<encoded>`` and Bing uses
    ``<url>&url=<encoded>``; both hide the destination behind an
    intermediate hop.
    """
    from urllib.parse import parse_qs, unquote, urlparse

    if "uddg=" in href:
        try:
            q = parse_qs(urlparse(href, scheme="https").query)
            if q.get("uddg"):
                return unquote(q["uddg"][0])
        except Exception:  # noqa: BLE001
            pass
    return href


def build_exec_tools(state: Any, workspace: str) -> list[FunctionTool]:
    """Build the full-access tools bound to a working directory.

    Args:
        state: Live runtime state, unused by most of these tools but kept
            for signature symmetry with ``build_tools``.
        workspace: Directory created for this deployment that the agent
            uses for downloaded and generated files.
    """
    os.makedirs(workspace, exist_ok=True)

    # -- web ---------------------------------------------------------------

    @function_tool
    async def fetch_url(url: str, as_text: bool = True) -> str:
        """Fetch a web page over HTTP(S) and return its content.

        Args:
            url: The absolute URL to fetch. Redirects are followed.
            as_text: Convert HTML to readable text (default). Set false to
                get the raw body, e.g. for JSON APIs.

        Returns the body, truncated if very long, along with the status
        code and the final URL after redirects.
        """
        if not re.match(r"^https?://", url, re.I):
            return json.dumps({"error": "url must start with http:// or https://"})
        try:
            async with httpx.AsyncClient(
                timeout=WEB_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
                verify=_TLS_VERIFY,
            ) as client:
                resp = await client.get(url)
        except Exception as exc:  # noqa: BLE001 - report any network failure to the model
            return json.dumps({"error": f"{type(exc).__name__}: {exc}", "url": url})

        body = resp.text[:MAX_FETCH_BYTES]
        if as_text:
            body = html_to_text(body)
        return _clip(
            {
                "status": resp.status_code,
                "final_url": str(resp.url),
                "content_type": resp.headers.get("content-type", ""),
                "body": body,
            }
        )

    async def _scrape(engine: str, query: str) -> list:
        """Scrape one search engine's HTML result page.

        Keyed on the anchor markup each engine actually emits. Brave lists
        organic results as plain external anchors; Bing wraps each one in an
        ``<h2>``. Both are regex-scraped, so a redesign can silently break
        them -- that is why the caller tries several engines in order.
        """
        if engine == "brave":
            url = "https://search.brave.com/search"
            pattern = r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>'
            blocked = ("search.brave", "brave.com", "accounts.google")
        elif engine == "duckduckgo":
            url = "https://html.duckduckgo.com/html/"
            pattern = (
                r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
            )
            blocked = ("duckduckgo.com/l", "duckduckgo.com/#")
        else:
            url = "https://www.bing.com/search"
            pattern = r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
            blocked = ("bing.com", "microsoft.com", "go.microsof")

        async with httpx.AsyncClient(
            timeout=WEB_TIMEOUT, follow_redirects=True, verify=_TLS_VERIFY
        ) as c:
            resp = await c.get(
                url,
                params={"q": query},
                headers={
                    "User-Agent": _UA,
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml",
                },
            )

        out = []
        for href, raw in re.findall(pattern, resp.text, re.S):
            title = html.unescape(_TAG_RE.sub("", raw)).strip()
            if len(title) < 12:
                continue
            href = html.unescape(href)
            # DuckDuckGo and some Bing links are redirect wrappers; unwrap
            # the real target so fetch_url gets a usable address.
            href = _unwrap_redirect(href)
            if not href.startswith("http"):
                continue
            if any(b in href for b in blocked):
                continue
            out.append({"title": title[:200], "url": href})
        return out

    @function_tool
    async def search_web(query: str, max_results: int = 8) -> str:
        """Search the web and return result titles and links.

        Args:
            query: The search query.
            max_results: How many results to return (1-20).

        Returns a list of {title, url} pairs. Follow up with fetch_url on
        the links that look relevant -- the returned URLs may be redirect
        wrappers, which fetch_url follows automatically.
        """
        wanted = max(1, min(max_results, 20))
        results = []
        seen = set()
        errors = []
        # Brave first: it renders real organic results for technical
        # queries. Bing is kept as a fallback in case Brave changes layout
        # or rate-limits us.
        for engine in ("brave", "duckduckgo", "bing"):
            try:
                page = await _scrape(engine, query)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{engine}: {type(exc).__name__}: {exc}")
                continue
            for item in page:
                key = item["url"].split("#")[0]
                if key in seen:
                    continue
                seen.add(key)
                results.append(item)
                if len(results) >= wanted:
                    break
            if len(results) >= wanted:
                break

        if not results:
            detail = "; ".join(errors) if errors else "no results parsed"
            return json.dumps({"error": detail, "query": query, "results": []})
        return _clip({"query": query, "count": len(results), "results": results})

    # -- shell -------------------------------------------------------------

    @function_tool
    async def run_shell(command: str, timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT) -> str:
        """Run a shell command and return its output.

        Args:
            command: A bash command line. Runs in the workspace directory
                unless you cd elsewhere.
            timeout_seconds: Kill the command past this many seconds
                (1-900).

        Returns stdout, stderr, and the exit code. The command runs
        non-interactively: anything expecting a prompt or a TTY will hang
        until the timeout, so avoid interactive programs.
        """
        refusal = _denied(command)
        if refusal:
            return json.dumps({"error": refusal, "command": command})

        limit = max(1.0, min(timeout_seconds, MAX_COMMAND_TIMEOUT))
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash",
                "-c",
                command,
                cwd=workspace,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "CI": "1", "PAGER": "cat", "DEBIAN_FRONTEND": "noninteractive"},
            )
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"failed to start command: {exc}"})

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=limit)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:  # noqa: BLE001
                pass
            return json.dumps(
                {"error": f"timed out after {limit:.0f}s and was killed", "command": command}
            )

        out = stdout.decode("utf-8", "replace")
        err = stderr.decode("utf-8", "replace")
        return _clip(
            {
                "exit_code": proc.returncode,
                "stdout": out[:MAX_OUTPUT_CHARS],
                "stderr": err[:MAX_OUTPUT_CHARS],
            }
        )

    # -- python ------------------------------------------------------------

    @function_tool
    async def run_python(code: str, timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT) -> str:
        """Write a Python script into the workspace and run it.

        Args:
            code: The Python source to execute.
            timeout_seconds: Kill the script past this many seconds
                (1-900).

        The script runs with the dashboard's own virtualenv interpreter, so
        ``httpx``, ``json`` and everything else in requirements.txt are
        available. Prefer this over run_shell for anything non-trivial: the
        source is saved to a real file you can inspect or re-run, and there
        are no shell-quoting hazards.
        """
        import hashlib

        digest = hashlib.sha256(code.encode("utf-8")).hexdigest()[:12]
        path = os.path.join(workspace, f"agent_{digest}.py")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(code)
        except OSError as exc:
            return json.dumps({"error": f"could not write script: {exc}"})

        limit = max(1.0, min(timeout_seconds, MAX_COMMAND_TIMEOUT))
        try:
            proc = await asyncio.create_subprocess_exec(
                "python3",
                path,
                cwd=workspace,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"failed to start python: {exc}"})

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=limit)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:  # noqa: BLE001
                pass
            return json.dumps(
                {"error": f"script timed out after {limit:.0f}s", "script": path}
            )

        return _clip(
            {
                "script": path,
                "exit_code": proc.returncode,
                "stdout": stdout.decode("utf-8", "replace")[:MAX_OUTPUT_CHARS],
                "stderr": stderr.decode("utf-8", "replace")[:MAX_OUTPUT_CHARS],
            }
        )

    # -- files -------------------------------------------------------------

    @function_tool
    async def write_file(path: str, content: str, append: bool = False) -> str:
        """Write a text file.

        Args:
            path: File path. Relative paths resolve inside the workspace.
            content: The text to write.
            append: Append instead of overwriting.

        Returns the resolved absolute path and the bytes written.
        """
        target = _resolve(path, workspace)
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        try:
            with open(target, "a" if append else "w", encoding="utf-8") as fh:
                fh.write(content)
        except OSError as exc:
            return json.dumps({"error": f"could not write {target}: {exc}"})
        return json.dumps(
            {"path": target, "bytes": len(content.encode("utf-8")), "mode": "append" if append else "write"}
        )

    @function_tool
    async def read_file(path: str, max_bytes: int = 60_000) -> str:
        """Read a text file.

        Args:
            path: File path. Relative paths resolve inside the workspace.
            max_bytes: Read at most this many bytes.

        Returns the file contents, or an error if it cannot be read.
        """
        target = _resolve(path, workspace)
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as fh:
                data = fh.read(max_bytes)
        except OSError as exc:
            return json.dumps({"error": f"could not read {target}: {exc}"})
        return _clip({"path": target, "content": data})

    @function_tool
    async def list_directory(path: str = ".") -> str:
        """List a directory.

        Args:
            path: Directory path. Relative paths resolve inside the
                workspace.

        Returns the entries with their type and size.
        """
        target = _resolve(path, workspace)
        try:
            names = sorted(os.listdir(target))
        except OSError as exc:
            return json.dumps({"error": f"could not list {target}: {exc}"})
        entries = []
        for name in names:
            full = os.path.join(target, name)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            kind = "dir" if os.path.isdir(full) else ("link" if os.path.islink(full) else "file")
            entries.append({"name": name, "type": kind, "bytes": st.st_size})
        return _clip({"path": target, "count": len(entries), "entries": entries})

    return [fetch_url, search_web, run_shell, run_python, write_file, read_file, list_directory]
