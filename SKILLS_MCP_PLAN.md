# Skills & MCP Integration Plan

## Overview

Add two distinct extension mechanisms to Agent mode:

1. **Skills** are drop-in folders containing a `SKILL.md` and optional
   reference material/scripts. They provide on-demand instructions; they are
   not imported as Python modules and do not implicitly add executable tools.
2. **MCP servers** expose tools through the MCP protocol. Their configuration
   is managed in the AI-Chat Extensions view and persisted by the backend.

The agent remains the existing OpenAI Agents SDK agent talking to the selected
local inference engine. Skills provide progressive instructions; built-in and
MCP tools provide callable capabilities. Keep those concepts separate.

**Review verdict:** the initial proposal had the right feature areas but was
not safe or specific enough to implement as written. The corrected plan uses
standard drop-in skill folders, structured MCP session management, redacted
persistent configuration, and explicit access/security gates. MCP management
is gated by a separately configured high-entropy admin token; since the
dashboard's other APIs remain unauthenticated, do not expose the dashboard to
untrusted networks. Use localhost/SSH tunneling or HTTPS for admin sessions.

---

## 1. Skills (Drop-in Agent Skill Folders)

### 1.1 Directory Structure
```
skills/                         # host-mounted; configurable via SKILLS_DIR
├── summarize-research/
│   ├── SKILL.md                # required manifest + instructions
│   ├── references/
│   │   └── source-checklist.md
│   └── scripts/                # optional; only run through permitted tools
└── another-skill/
    └── SKILL.md
```

### 1.2 File format and behavior

Use the common Agent Skills `SKILL.md` convention, not a Strixper-specific
Python module API. Require YAML frontmatter with `name` and `description`;
the name must match the containing folder. The Markdown body contains the
workflow. References, assets, and scripts are optional. Do not `import` or
execute files during discovery.

Discovery scans only immediate child directories of `SKILLS_DIR`. Parse
frontmatter with a safe YAML loader and validate a small allowed metadata
schema and field-size limits; reject malformed frontmatter, duplicate names,
and paths escaping the skill directory. Ignore symlinks that resolve outside
the configured root. Surface invalid folders and parse errors in the
Extensions UI instead of silently dropping them.

Re-scan on each `GET /agent/skills` and before each agent run (or use a short
metadata cache invalidated by an explicit rescan). This makes a newly dropped
folder available without restarting Strixper. Do not cache skill bodies across
requests unless keyed by file modification time.

### 1.3 Progressive loading

Expose a small built-in `list_skills` tool and `load_skill(name)` tool, or
provide equivalent SDK-supported dynamic instructions. At agent start, include
only installed skill names and short descriptions in the system instructions.
When a skill is relevant, the model calls `load_skill` to receive its complete
`SKILL.md` body. Load an optional reference/script only through a path-confined
`read_skill_resource(name, relative_path)` tool when the instructions require
it. Bound file sizes and response bytes. Treat loaded content as untrusted
guidance subordinate to system policy and request-level tool permissions.

This keeps every skill's full instructions out of every request's prompt while
making all installed skills discoverable. A skill contributes instructions,
not new Python tools. If a future plugin needs to contribute callable code,
design a separate, explicitly trusted extension API with its own opt-in and
security review.

### 1.4 Host and Docker setup

Add `SKILLS_DIR` to settings, defaulting to a dedicated directory inside the
repository for local development. For Docker, mount a host directory at a
stable container path (for example `./skills:/app/skills:ro`) and set
`SKILLS_DIR=/app/skills`; document that users install a skill by dropping its
folder there. Resolve the host path relative to the script's own location,
not the shell's current working directory. Keep skills read-only in the
container by default; skills that need to create files should use the existing
agent workspace.

### 1.5 API and Extensions UI

Add `GET /api/v1/agent/skills`, returning each installed skill's name,
description, and validation status (never arbitrary code execution). Add a
**Conversation / Extensions** sub-navigation inside AI Chat. The Skills view
shows the configured directory, discovered skills, manifest errors, and a
rescan action. Installing/removing files remains a filesystem operation:
users drop/remove folders in `SKILLS_DIR`, then rescan. Do not add an upload
installer in the first version.

---

## 2. MCP Servers (stdio-first Tool Integration)

### 2.1 Architecture
```
┌─────────────────┐       MCP JSON-RPC             ┌──────────────┐
│  Agent (this    │ ◄──────────────────────────────► │  MCP Server  │
│   process)      │                                  │  (any lang)  │
└─────────────────┘                                  └──────────────┘
       │                                                   │
       ▼                                                   ▼
SDK-native MCP server or a typed
FunctionTool adapter (wraps MCP call)
```

### 2.2 Connection and tool integration

Pin a compatible MCP Python SDK version and verify the installed OpenAI Agents
SDK's MCP integration before implementation. Prefer native MCP support where
it meets Strixper's lifecycle and permission needs. Otherwise construct the
SDK's `FunctionTool` with the MCP tool's `inputSchema`; do **not** use an
untyped `@function_tool async def wrapper(**kwargs)`, which cannot reliably
generate a valid argument schema. Normalize schemas for the selected local
engine (including recursively removing unsupported JSON Schema `default`
annotations, as already required for GUFO) and bound/serialize MCP content and
error results.

Use supported SDK context managers for `stdio_client` and `ClientSession`,
managed with `AsyncExitStack` or equivalent structured lifetime. Never call
`__aenter__`/`__aexit__` manually without retaining and closing the owning
context manager. Apply connection, initialization, tool-call, and shutdown
timeouts; close all sessions and child processes during FastAPI lifespan
shutdown.

### 2.3 Configuration and secret references

Persist non-secret server configuration in a versioned JSON store under the
existing mounted backend data directory (for example
`backend/data/mcp_servers.json`). Write updates atomically and restrict file
permissions. Example:

```json
{
  "version": 1,
  "servers": [{
    "id": "github",
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-github"],
    "env": {"GITHUB_TOKEN": {"secretRef": "GITHUB_TOKEN"}},
    "enabled": false,
    "minimum_access": "actions"
  }]
}
```

Support stdio first. Include an explicit transport discriminator so
Streamable HTTP can be added later; do not advertise remote HTTP support until
implemented. Store no secret values in this file or in API responses. Resolve
explicit environment references using strict syntax and report missing
references without echoing values. A future secret store can replace
environment references without changing the UI contract.

### 2.4 Backend management API

Add an `/api/v1/agent/mcp-servers` API for listing redacted configurations,
creating/updating/deleting a server, enabling/disabling it, testing its
connection, and reading runtime status (connected, disabled, or error with a
redacted diagnostic). Validate IDs, command/args/env schemas, transport,
access level, and duplicate IDs. Test-connection must create a temporary
session and always close it. Reconfiguration should validate and connect the
replacement before swapping out a working connection where possible; failed
edits must not silently erase the last known-good config.

These are privileged administrative operations, not ordinary chat endpoints.
Protect all management endpoints with authentication and authorization. Until
the dashboard has general user authentication and authorization, protect
these endpoints with a separately configured high-entropy admin bearer token
and disable management when it is unset. The dashboard's other endpoints
remain unauthenticated, so operate it only on a trusted network and use
localhost/SSH tunneling or HTTPS to protect the token in transit. A browser
confirmation dialog is not authorization.

### 2.5 Manager lifecycle and isolation

Initialize one manager in FastAPI lifespan and close it in `finally`. Enabled
servers connect lazily on the first eligible agent request and remain
connected until disabled, reconfigured, or application shutdown. This avoids
launching unused servers during startup. A server failing to initialize must
be marked unavailable without taking down the API or unrelated MCP servers.
Serialize configuration updates and reconnection per server; avoid
reconnecting all servers for a single edit. Each call routes to its matching
active session and reports unavailable, timeout, and tool errors explicitly.

The manager may enumerate connected tools for each agent request, but must
not start or stop servers based only on that request's access tier. Filter
tool availability per request using the global chat access and the server's
configured minimum access level.

### 2.6 MCP UI inside AI Chat

Add an **MCP Servers** section to the AI-Chat Extensions view. Show server
name, transport, enabled state, configured access level, connection status,
and a safe error summary. Provide add/edit/delete, enable/disable, and
test-connection controls. Never render secret values after save. Show a clear
warning before enabling a stdio command: it runs as the backend user inside
the Strixper container and inherits that container's access (including
mounted volumes and, where present, the Docker socket). The dashboard has no general authentication boundary suitable for untrusted
network exposure; do not expose MCP administration to an untrusted network.
Protect the bearer token in transit using localhost/SSH tunneling or HTTPS.

---

## 3. Tool Resolution and Permission Model

Resolve built-in tools and eligible MCP tools per request in the existing
agent tool-resolution path. Skill discovery is not tool registration: the
available skill descriptions and bounded skill-loading tools are provided as
agent context. Avoid a second registry that duplicates existing tool
definitions; extract a shared resolver only if needed to keep chat and
`/agent/tools` behavior consistent.

### 3.1 Tier Hierarchy
```
read-only  ⊂  actions  ⊂  full_access
```

| Source | read-only | actions | full_access |
|--------|-----------|---------|-------------|
| Builtin (agent_tools) | ✓ | ✓ | ✓ |
| Builtin actions | | ✓ | ✓ |
| Builtin exec (agent_exec_tools) | | | ✓ |
| Skill discovery/loading | ✓ | ✓ | ✓ |
| MCP server minimum access: read-only | ✓ | ✓ | ✓ |
| MCP server minimum access: actions | | ✓ | ✓ |
| MCP server minimum access: full_access | | | ✓ |

`minimum_access` is an administrator-controlled availability gate, not proof
that every server tool is safe at that level. MCP servers can expose
destructive tools regardless of their metadata. Treat each enabled server as
trusted and apply the request-level access mode in addition to its configured
minimum.

---

## 4. Implementation Steps

| Phase | Deliverable | Acceptance check |
|-------|-------------|------------------|
| 0 | Protected MCP management boundary | Require an admin token and disable writes when unset; document that the rest of the dashboard is unauthenticated and require localhost/SSH tunneling or HTTPS for administration. |
| 1 | Skills manifest parser/discovery and `SKILLS_DIR` | Drop a folder into the mounted directory and discover it on next scan; malformed/duplicate/path-escape cases report errors and no code is imported. |
| 2 | Skill list/load/resource tools and `GET /agent/skills` | Agent discovers and loads a skill on demand; resource paths stay inside its root and response sizes are bounded. |
| 3 | AI-Chat Conversation / Extensions view | Skills, validation status, directory, and rescan are visible without disrupting chat. |
| 4 | MCP dependency/version decision, validated config store, CRUD/status/test APIs | Config persists across restart; secrets are redacted; invalid edits are rejected; test sessions always close. |
| 5 | MCP manager, schema adapter/native integration, lifespan cleanup | Test servers work independently; a failed server does not break healthy ones; GUFO-compatible schemas/timeouts are verified. |
| 6 | MCP Servers UI and request access gating | CRUD, test, status, enablement, and access level work end-to-end; disabled/ineligible tools are absent from runs. |
| 7 | Docker/local docs and end-to-end tests | Host-mounted skill folders work in Docker; backend shutdown closes MCP subprocesses; existing built-in tools still work. |

---

## 5. Security Considerations

| Risk | Mitigation |
|------|------------|
| Untrusted skill instructions/resources | Skills are data, not sandboxed prompts; they can attempt prompt injection. Keep folders trusted, bound loaded content, and never treat their text as system policy. Scripts run only through separately permission-gated tools. |
| MCP stdio command execution | An enabled command runs as the backend user with container-mounted access. Require explicit administrative enablement and a warning; MCP is not sandboxed and is not covered by shell-tool deny rules. |
| Secrets | Store only environment-variable references; redact config/status/errors and never log resolved values. |
| Unsafe MCP schemas/results | Validate schemas, normalize unsupported keywords (including `default`), bound input/output, and report errors explicitly. |
| Tool name collisions | Prefix MCP tools with normalized unique `mcp_{server}_{tool}` names and detect collisions. Skills add no dynamic callable tools. |
| Resource exhaustion / hangs | Enforce connection/call timeouts, response-size limits, maximum active servers/tools/concurrent calls, and orderly process cleanup. |
| Administrative API exposure | Existing dashboard authentication/network limits apply; do not expose MCP management to untrusted users or networks. |

---

## 6. Example Usage

### User installs a skill:
```bash
mkdir -p skills/research-review/references
cat > skills/research-review/SKILL.md <<'EOF'
---
name: research-review
description: Research a topic, compare sources, and produce a cited summary.
---

When asked to research, gather multiple independent sources, record URLs,
and distinguish verified facts from inference.
EOF
```

### Configure an MCP server:

Use the AI-Chat **Extensions → MCP Servers** form to add a server. For stdio,
provide command/arguments, environment variable references, enabled state, and
minimum access level. Test it before enabling. Credentials are configured in
the backend environment; they are not pasted into or stored in the JSON
configuration.

---

## 7. Testing Strategy

1. **Skills unit tests**: frontmatter, required fields/folder-name match, duplicate names, malformed manifests, symlink/path traversal rejection, size limits, rescans, and resource containment.
2. **MCP unit tests**: config validation/atomic persistence, secret redaction and missing references, schema normalization (including nested `default`), tool-name collisions, and bounded result serialization.
3. **MCP lifecycle tests**: stdio initialize/call/timeout/failed startup/reconfigure/shutdown; verify all contexts and child processes close and one broken server does not affect healthy servers.
4. **Agent integration tests**: selected engine receives the right tools for each access level; disabled/ineligible servers are absent; skills load progressively; `/agent/tools` matches actual resolution.
5. **UI/API tests**: CRUD, test connection, status, error reporting, secret redaction, rescan, and compatibility with existing AI Chat interactions.

---

## 8. Future Extensions

- **Hot reload**: Watch `skills/` rather than rescanning each request
- **Skill marketplace**: Install from git/URL via CLI command
- **MCP Streamable HTTP**: Support remote MCP servers after stdio lifecycle is stable
- **Tool permissions**: Per-tool allow/deny lists in config
- **Executable skill extensions**: A separately trusted and explicitly installed plugin format, if instruction/resource skills prove insufficient