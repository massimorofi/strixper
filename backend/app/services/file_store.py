"""Workspace file storage for chat attachments and generated artifacts.

Everything the user uploads and everything the agent writes lives under one
root (``STRIXPER_WORKSPACE``, default ``AGENT_WORKSPACE``). Keeping a
single root means the agent's ``read_file`` sees exactly what the browser
uploaded -- two roots would put user files out of the agent's reach.

Security model: the workspace is the confinement boundary. Every path that
arrives from the client is resolved with ``os.path.realpath`` *before* it
is compared with the root, so a planted symlink resolves to its target and
is rejected if that target escapes. ``os.path.commonpath`` is used rather
than a string prefix check because ``/ws-evil`` startswith ``/ws``.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

from .vision_helper import sniff_mime

INDEX_FILENAME = ".strixper-index.json"
UPLOADS_DIR = "uploads"
OUTPUTS_DIR = "outputs"

# workspace_read cap, in characters of decoded text.
DEFAULT_READ_LIMIT = 1_000_000


class FileStoreError(Exception):
    """Raised for any rejected file operation. Carries an HTTP-friendly message."""


@dataclass
class FileRecord:
    id: str
    name: str
    size: int
    mime_type: str
    path: str  # workspace-relative
    created_at: float
    kind: str = "upload"  # "upload" | "output"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sanitize_filename(raw: str, fallback: str = "file") -> str:
    """Reduce a client-supplied filename to a safe basename.

    Strips directory components (including Windows separators), control
    characters, and leading dots so nothing lands as a hidden file or a
    traversal attempt.
    """
    if not raw:
        return fallback
    # Normalise Windows paths, then take the basename twice: a name like
    # "a/../../b" survives one split but not both passes.
    name = raw.replace("\\", "/")
    name = os.path.basename(name)
    name = os.path.basename(name)
    # Drop control characters and characters that break shells/terminals.
    name = "".join(ch for ch in name if ord(ch) >= 32 and ch not in '"*?<>|')
    name = name.strip().strip(".")
    if not name:
        return fallback
    return name[:180]


class FileStore:
    """Index-backed file store confined to a workspace root."""

    def __init__(
        self,
        root: str,
        max_file_bytes: int = 50 * 1024 * 1024,
        read_limit: int = DEFAULT_READ_LIMIT,
        ttl_days: int = 7,
    ) -> None:
        self.root = os.path.realpath(os.path.abspath(root))
        self.max_file_bytes = max_file_bytes
        self.read_limit = read_limit
        self.ttl_seconds = max(0, ttl_days) * 86400
        self._records: dict[str, FileRecord] = {}
        Path(self.root).mkdir(parents=True, exist_ok=True)
        Path(self.root, UPLOADS_DIR).mkdir(parents=True, exist_ok=True)
        Path(self.root, OUTPUTS_DIR).mkdir(parents=True, exist_ok=True)
        self._index_path = os.path.join(self.root, INDEX_FILENAME)
        self._load()

    # ---------------------------------------------------------------- index

    def _load(self) -> None:
        """Load the index; rebuild it from the tree if it is missing or corrupt."""
        try:
            with open(self._index_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            records = raw.get("files", raw) if isinstance(raw, dict) else raw
            loaded: dict[str, FileRecord] = {}
            if isinstance(records, dict):
                for fid, item in records.items():
                    rec = FileRecord(**item)
                    if os.path.exists(os.path.join(self.root, rec.path)):
                        loaded[fid] = rec
                self._records = loaded
                return
        except (OSError, ValueError, TypeError, KeyError):
            pass
        self._records = {}
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        """Discover files on disk and synthesise records for them."""
        for base, dirs, files in os.walk(self.root):
            # Never index our own bookkeeping or VCS metadata.
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
            for fname in files:
                if fname == INDEX_FILENAME:
                    continue
                full = os.path.join(base, fname)
                rel = self._rel(full)
                if rel is None:
                    continue
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                fid = uuid.uuid4().hex
                mime = sniff_mime(_head(full)) or _mime_from_ext(fname)
                kind = OUTPUTS_DIR if rel.startswith(OUTPUTS_DIR) else UPLOADS_DIR
                self._records[fid] = FileRecord(
                    id=fid,
                    name=fname,
                    size=st.st_size,
                    mime_type=mime,
                    path=rel,
                    created_at=st.st_mtime,
                    kind=kind,
                )
        self._persist()

    def _persist(self) -> None:
        payload = {"files": {fid: rec.to_dict() for fid, rec in self._records.items()}}
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=self.root, prefix=".strixper-index-", suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp_path, self._index_path)
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------- paths

    def _rel(self, full: str) -> Optional[str]:
        """Workspace-relative path for *full*, or None if outside the root."""
        full = os.path.realpath(os.path.abspath(full))
        try:
            common = os.path.commonpath([self.root, full])
        except ValueError:
            # Different drives on Windows, or mixed absolute/relative.
            return None
        if common != self.root:
            return None
        rel = os.path.relpath(full, self.root)
        return None if rel.startswith("..") else rel

    def resolve(self, rel_path: str) -> str:
        """Resolve a client-supplied relative path inside the workspace.

        Rejects absolute paths, traversal, and symlinks that point out of
        the tree. Returns the absolute path only when it is confined.
        """
        if rel_path is None:
            raise FileStoreError("no path supplied")
        candidate = str(rel_path).strip()
        if not candidate:
            raise FileStoreError("empty path")
        if os.path.isabs(candidate) or (len(candidate) > 1 and candidate[1] == ":"):
            raise FileStoreError("absolute paths are not allowed")
        if ".." in candidate.replace("\\", "/").split("/"):
            raise FileStoreError("path traversal ('..') is not allowed")

        joined = os.path.join(self.root, candidate)
        resolved = os.path.realpath(os.path.abspath(joined))
        rel = self._rel(resolved)
        if rel is None:
            raise FileStoreError("path escapes the workspace")
        return resolved

    # ------------------------------------------------------------- write

    def save(
        self,
        data: bytes,
        filename: str,
        subdir: str = UPLOADS_DIR,
        declared_mime: Optional[str] = None,
        kind: str = "upload",
    ) -> FileRecord:
        """Store *data* under ``<root>/<subdir>/<sanitised name>``."""
        if data is None:
            raise FileStoreError("no data supplied")
        if not data:
            raise FileStoreError("the file is empty")
        if len(data) > self.max_file_bytes:
            raise FileStoreError(
                f"file is {len(data) / 1_048_576:.1f} MB, the limit is "
                f"{self.max_file_bytes / 1_048_576:.1f} MB"
            )

        safe_name = sanitize_filename(filename)
        target_dir = self.resolve(subdir)
        os.makedirs(target_dir, exist_ok=True)

        # Never clobber: append a counter if the name is taken.
        final_name = self._unique_name(target_dir, safe_name)
        full = os.path.join(target_dir, final_name)
        rel = self._rel(full)
        if rel is None:
            raise FileStoreError("resolved path escapes the workspace")

        tmp_fd, tmp_path = tempfile.mkstemp(dir=target_dir, prefix=".tmp-")
        try:
            with os.fdopen(tmp_fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp_path, full)
        except OSError as exc:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise FileStoreError(f"could not write file: {exc}") from exc

        sniffed = sniff_mime(data)
        mime = sniffed or declared_mime or _mime_from_ext(final_name)

        rec = FileRecord(
            id=uuid.uuid4().hex,
            name=final_name,
            size=len(data),
            mime_type=mime,
            path=rel,
            created_at=time.time(),
            kind=kind,
        )
        self._records[rec.id] = rec
        try:
            self._persist()
        except FileStoreError:
            raise
        except OSError as exc:
            raise FileStoreError(f"could not update the file index: {exc}") from exc
        return rec

    def _unique_name(self, target_dir: str, safe_name: str) -> str:
        if not os.path.exists(os.path.join(target_dir, safe_name)):
            return safe_name
        stem, dot, ext = safe_name.partition(".")
        for i in range(1, 10_000):
            candidate = f"{stem}-{i}{dot}{ext}" if dot else f"{safe_name}-{i}"
            if not os.path.exists(os.path.join(target_dir, candidate)):
                return candidate
        raise FileStoreError("could not find a free filename")

    # -------------------------------------------------------------- read

    def get(self, file_id: str) -> Optional[FileRecord]:
        return self._records.get(file_id)

    def require(self, file_id: str) -> FileRecord:
        rec = self._records.get(file_id)
        if rec is None:
            raise FileStoreError(f"unknown file id: {file_id}")
        if not os.path.exists(os.path.join(self.root, rec.path)):
            raise FileStoreError(f"file is no longer on disk: {rec.name}")
        return rec

    def read_bytes(self, file_id: str) -> bytes:
        rec = self.require(file_id)
        full = os.path.join(self.root, rec.path)
        try:
            with open(full, "rb") as fh:
                return fh.read()
        except OSError as exc:
            raise FileStoreError(f"could not read {rec.name}: {exc}") from exc

    def list(self) -> list[dict[str, Any]]:
        return [rec.to_dict() for rec in self._records.values()]

    def browse(self, rel_path: str = ".") -> dict[str, Any]:
        """List one directory of the workspace."""
        target = self.resolve(rel_path if rel_path else ".")
        if not os.path.isdir(target):
            raise FileStoreError(f"not a directory: {rel_path}")
        entries: list[dict[str, Any]] = []
        try:
            names = sorted(os.listdir(target))
        except OSError as exc:
            raise FileStoreError(f"could not list {rel_path}: {exc}") from exc
        for name in names:
            if name in (INDEX_FILENAME,) or name.startswith(".tmp-"):
                continue
            full = os.path.join(target, name)
            rel = self._rel(full)
            if rel is None:
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            is_dir = os.path.isdir(full)
            entries.append(
                {
                    "name": name,
                    "path": rel,
                    "is_dir": is_dir,
                    "size": 0 if is_dir else st.st_size,
                    "mtime": st.st_mtime,
                    "mime_type": None if is_dir else (sniff_mime(_head(full)) or _mime_from_ext(name)),
                }
            )
        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
        return {"path": self._rel(target) or ".", "entries": entries}

    def read_text(self, rel_path: str) -> dict[str, Any]:
        """Read a text file from the workspace, capped at ``read_limit``."""
        target = self.resolve(rel_path)
        if not os.path.isfile(target):
            raise FileStoreError(f"not a file: {rel_path}")
        try:
            with open(target, "rb") as fh:
                raw = fh.read(self.read_limit + 1)
        except OSError as exc:
            raise FileStoreError(f"could not read {rel_path}: {exc}") from exc

        truncated = len(raw) > self.read_limit
        if truncated:
            raw = raw[: self.read_limit]
        if b"\x00" in raw:
            raise FileStoreError(
                f"{rel_path} looks like a binary file and cannot be shown as text"
            )
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                content = raw.decode("latin-1")
            except Exception as exc:  # pragma: no cover - latin-1 always decodes
                raise FileStoreError(f"{rel_path} is not readable text: {exc}") from exc
            encoding = "latin-1"
        else:
            encoding = "utf-8"
        return {"content": content, "encoding": encoding, "truncated": truncated}

    # ------------------------------------------------------------ delete

    def delete(self, file_id: str) -> bool:
        rec = self._records.pop(file_id, None)
        if rec is None:
            return False
        full = os.path.join(self.root, rec.path)
        try:
            if os.path.isfile(full) or os.path.islink(full):
                os.unlink(full)
        except OSError as exc:
            raise FileStoreError(f"could not delete {rec.name}: {exc}") from exc
        try:
            self._persist()
        except OSError as exc:
            raise FileStoreError(f"could not update the file index: {exc}") from exc
        return True

    def delete_path(self, rel_path: str, recursive: bool = False) -> str:
        """Delete a file, or a directory, inside the workspace.

        Directories are refused unless ``recursive`` is set, because silently
        removing a subtree is the one operation in this API that cannot be
        undone by the user.
        """
        target = self.resolve(rel_path)
        if target == self.root:
            raise FileStoreError("refusing to delete the workspace root")
        if os.path.isdir(target):
            if not recursive:
                if self._visible_entries(target):
                    raise FileStoreError(
                        f"{rel_path} is not empty; delete its contents first"
                    )
                # The directory looks empty to the user but may still hold the
                # store's own bookkeeping leftovers, which would make rmdir fail.
                for leftover in self._bookkeeping_entries(target):
                    try:
                        os.unlink(os.path.join(target, leftover))
                    except OSError as exc:
                        raise FileStoreError(
                            f"could not delete {rel_path}: {exc}"
                        ) from exc
                os.rmdir(target)
            else:
                shutil.rmtree(target)
            action = "directory"
        elif os.path.isfile(target) or os.path.islink(target):
            os.unlink(target)
            action = "file"
        else:
            raise FileStoreError(f"not found: {rel_path}")
        self._drop_records_under(rel_path)
        try:
            self._persist()
        except OSError as exc:
            raise FileStoreError(f"could not update the file index: {exc}") from exc
        return action

    def delete_paths(self, rel_paths: list[str], recursive: bool = False) -> dict[str, Any]:
        """Delete several paths in one call.

        Each path is attempted independently and reported separately, so one
        protected or missing entry does not discard the rest of the batch.
        """
        deleted: list[str] = []
        failed: list[dict[str, str]] = []
        # Deepest paths first: removing a directory's contents before the
        # directory itself means a selection that names both does not report
        # the children as "not found" after the parent already took them.
        ordered = sorted(
            (p for p in (x or "" for x in rel_paths) if p.strip()),
            key=lambda p: (p.count("/"), len(p)),
            reverse=True,
        )
        for path in ordered:
            try:
                self.delete_path(path, recursive=recursive)
                deleted.append(path)
            except FileStoreError as exc:
                failed.append({"path": path, "error": str(exc)})
        return {
            "deleted": deleted,
            "deleted_count": len(deleted),
            "failed": failed,
            "failed_count": len(failed),
        }

    @staticmethod
    def _visible_entries(target: str) -> list[str]:
        """Directory contents excluding the store's own bookkeeping files."""
        try:
            names = os.listdir(target)
        except OSError:
            return []
        return [
            n
            for n in names
            if n != INDEX_FILENAME and not n.startswith(".tmp-")
        ]

    @staticmethod
    def _bookkeeping_entries(target: str) -> list[str]:
        """The store's own bookkeeping files inside ``target``.

        These are invisible to the user, so an "empty" directory can still
        contain them; they must be removed before ``os.rmdir`` can succeed.
        """
        try:
            names = os.listdir(target)
        except OSError:
            return []
        return [
            n
            for n in names
            if n == INDEX_FILENAME or n.startswith(".tmp-")
        ]

    def _drop_records_under(self, rel_path: str) -> None:
        """Forget indexed files at ``rel_path`` or beneath it."""
        norm = rel_path.rstrip("/")
        doomed = [
            fid
            for fid, rec in self._records.items()
            if rec.path == rel_path
            or (norm and rec.path.startswith(norm + "/"))
        ]
        for fid in doomed:
            self._records.pop(fid, None)

    # ---------------------------------------------------------- retention

    def sweep(self, ttl_seconds: Optional[int] = None) -> int:
        """Remove files older than the TTL. Returns how many were deleted."""
        ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        if ttl <= 0:
            return 0
        cutoff = time.time() - ttl
        doomed = [fid for fid, rec in self._records.items() if rec.created_at < cutoff]
        for fid in doomed:
            rec = self._records[fid]
            try:
                full = os.path.join(self.root, rec.path)
                if os.path.isfile(full) or os.path.islink(full):
                    os.unlink(full)
            except OSError:
                pass
        for fid in doomed:
            self._records.pop(fid, None)
        if doomed:
            try:
                self._persist()
            except OSError:
                pass
        return len(doomed)

    def adopt_existing(self) -> int:
        """Index files already on disk that the index does not know about."""
        known = {rec.path for rec in self._records.values()}
        added = 0
        for base, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
            for fname in files:
                if fname == INDEX_FILENAME or fname.startswith(".tmp-"):
                    continue
                full = os.path.join(base, fname)
                rel = self._rel(full)
                if rel is None or rel in known:
                    continue
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                fid = uuid.uuid4().hex
                kind = OUTPUTS_DIR if rel.startswith(OUTPUTS_DIR) else UPLOADS_DIR
                self._records[fid] = FileRecord(
                    id=fid,
                    name=fname,
                    size=st.st_size,
                    mime_type=sniff_mime(_head(full)) or _mime_from_ext(fname),
                    path=rel,
                    created_at=st.st_mtime,
                    kind=kind,
                )
                added += 1
        if added:
            try:
                self._persist()
            except OSError:
                pass
        return added


_EXT_MIME = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".jsx": "text/javascript",
    ".ts": "text/typescript",
    ".tsx": "text/typescript",
    ".html": "text/html",
    ".css": "text/css",
    ".yaml": "application/x-yaml",
    ".yml": "application/x-yaml",
    ".xml": "application/xml",
    ".log": "text/plain",
    ".sh": "text/x-shellscript",
    ".pdf": "application/pdf",
    ".zip": "application/zip",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _mime_from_ext(name: str) -> str:
    return _EXT_MIME.get(os.path.splitext(name)[1].lower(), "application/octet-stream")


def _head(path: str, n: int = 32) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read(n)
    except OSError:
        return b""


_store: Optional[FileStore] = None


def get_store() -> FileStore:
    """Process-wide store, built from settings on first use."""
    global _store
    if _store is None:
        from ..config import settings

        _store = FileStore(
            settings.workspace_root,
            max_file_bytes=settings.max_file_size_mb * 1024 * 1024,
            read_limit=settings.max_workspace_read_bytes,
            ttl_days=settings.workspace_ttl_days,
        )
    return _store


def set_store(store: FileStore) -> None:
    """Install a store instance (used by tests)."""
    global _store
    _store = store
