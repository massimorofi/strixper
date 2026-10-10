# Skills & MCP Integration Plan

## Overview

Extend the agent with a plugin system for **skills** (local Python modules) and **MCP servers** (remote tool servers), both exposed as `FunctionTool` to the OpenAI Agents SDK.

---

## 1. Skills (Local Plugin System)

### 1.1 Directory Structure
```
strixper/
├── skills/                    # <-- new directory (configurable via env)
│   ├── __init__.py
│   ├── builtin/               # shipped skills (read-only)
│   │   ├── __init__.py
│   │   ├── system_info.py
│   │   └── docker_utils.py
│   └── user/                  # user-installed skills (read-write)
│       ├── __init__.py
│       └── my_custom_skill.py
```

### 1.2 Skill Module Interface
Each skill is a Python module exporting:
```python
# skills/user/my_skill.py
from agents import FunctionTool, function_tool
from typing import Any

# Required: list of FunctionTool instances
TOOLS: list[FunctionTool] = []

# Optional: metadata
NAME = "my_skill"
VERSION = "1.0.0"
DESCRIPTION = "Does something useful"
REQUIRES_ACTIONS = False      # if True, only loaded when allow_actions=True
REQUIRES_FULL_ACCESS = False  # if True, only loaded when full_access=True

@function_tool
async def my_tool(param: str) -> str:
    """Tool description for the agent."""
    return f"Result: {param}"

TOOLS = [my_tool]
```

### 1.3 Discovery & Loading (`services/skill_loader.py`)
```python
def discover_skills(skills_dir: Path) -> list[SkillModule]:
    """Import all modules in skills_dir/{builtin,user}/"""
    # 1. Add skills_dir to sys.path
    # 2. Walk subdirectories, import each module
    # 3. Validate: has TOOLS list, all items are FunctionTool
    # 4. Return list of {name, module, tools, metadata}

def filter_tools_by_tier(skills, allow_actions, full_access) -> list[FunctionTool]:
    """Filter skills based on request tier."""
    # Include if:
    #   - not REQUIRES_ACTIONS and not REQUIRES_FULL_ACCESS (read-only)
    #   - REQUIRES_ACTIONS and allow_actions
    #   - REQUIRES_FULL_ACCESS and full_access
```

### 1.4 Integration Point
In `agent.py` → `_resolve_tools()`:
```python
from .services.skill_loader import discover_skills, filter_tools_by_tier

def _resolve_tools(state, body):
    tools = build_tools(state, body.allow_actions or body.full_access)
    if body.full_access:
        tools.extend(build_exec_tools(state, settings.agent_workspace))
    
    # NEW: Load skills
    skills_dir = Path(settings.skills_dir)  # env: SKILLS_DIR
    skills = discover_skills(skills_dir)
    tools.extend(filter_tools_by_tier(skills, body.allow_actions, body.full_access))
    
    return tools
```

### 1.5 Configuration
```python
# config.py additions
class Settings:
    skills_dir: str = os.getenv("SKILLS_DIR", 
        str(Path(__file__).resolve().parent.parent.parent / "skills"))
```

---

## 2. MCP Servers (Remote Tool Servers)

### 2.1 Architecture
```
┌─────────────────┐     JSON-RPC over stdio/HTTP     ┌──────────────┐
│  Agent (this    │ ◄──────────────────────────────► │  MCP Server  │
│   process)      │                                  │  (any lang)  │
└─────────────────┘                                  └──────────────┘
       │                                                   │
       ▼                                                   ▼
FunctionTool                                        Tools/Resources/
(wraps MCP call)                                    Prompts
```

### 2.2 MCP Client Wrapper (`services/mcp_client.py`)
```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from agents import FunctionTool, function_tool

class MCPServer:
    def __init__(self, name: str, command: str, args: list[str], env: dict = None):
        self.name = name
        self.command = command
        self.args = args
        self.env = env or {}
        self._session: ClientSession | None = None
    
    async def connect(self):
        params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env={**os.environ, **self.env}
        )
        self._read, self._write = await stdio_client(params).__aenter__()
        self._session = ClientSession(self._read, self._write)
        await self._session.initialize()
        # Fetch tool list
        self.tools = await self._session.list_tools()
    
    async def call_tool(self, name: str, arguments: dict) -> Any:
        return await self._session.call_tool(name, arguments)
    
    async def close(self):
        if self._session:
            await self._session.__aexit__(None, None, None)
```

### 2.3 FunctionTool Adapter
```python
def wrap_mcp_tool(server: MCPServer, tool_def) -> FunctionTool:
    """Convert MCP tool definition to OpenAI Agents FunctionTool."""
    
    @function_tool
    async def mcp_tool_wrapper(**kwargs) -> str:
        result = await server.call_tool(tool_def.name, kwargs)
        return json.dumps(result.content, ensure_ascii=False)
    
    # Copy metadata from MCP tool
    mcp_tool_wrapper.__name__ = f"mcp_{server.name}_{tool_def.name}"
    mcp_tool_wrapper.__doc__ = tool_def.description or f"MCP tool: {tool_def.name}"
    
    # Use tool_def.inputSchema for parameter validation (optional)
    return mcp_tool_wrapper
```

### 2.4 Configuration (JSON/YAML)
```json
// config/mcp_servers.json
{
  "servers": [
    {
      "name": "filesystem",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"],
      "enabled": true,
      "tier": "full_access"
    },
    {
      "name": "github",
      "command": "docker",
      "args": ["run", "-i", "--rm", "mcp/github"],
      "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"},
      "enabled": true,
      "tier": "actions"
    }
  ]
}
```

### 2.5 Connection Management
```python
# services/mcp_manager.py
class MCPManager:
    def __init__(self, config_path: Path):
        self.servers: dict[str, MCPServer] = {}
        self.config = load_config(config_path)
    
    async def start_all(self, tier: str):
        """Connect to all enabled servers matching tier."""
        for cfg in self.config["servers"]:
            if not cfg.get("enabled"): continue
            if cfg["tier"] not in ("read-only", tier): continue  # tier hierarchy
            
            server = MCPServer(cfg["name"], cfg["command"], cfg["args"], cfg.get("env"))
            await server.connect()
            self.servers[cfg["name"]] = server
    
    def get_tools(self) -> list[FunctionTool]:
        """All tools from all connected servers."""
        tools = []
        for server in self.servers.values():
            for tool_def in server.tools:
                tools.append(wrap_mcp_tool(server, tool_def))
        return tools
    
    async def shutdown(self):
        for server in self.servers.values():
            await server.close()
```

### 2.6 Integration Point
In `agent.py` → lifespan or `_resolve_tools()`:
```python
# Option A: Start at startup (persistent connections)
@asynccontextmanager
async def lifespan(app):
    mcp_manager = MCPManager(Path(settings.mcp_config))
    await mcp_manager.start_all("full_access")  # or based on request
    app.state.mcp = mcp_manager
    try:
        yield
    finally:
        await mcp_manager.shutdown()

# Option B: Per-request (lazy connect)
def _resolve_tools(state, body):
    tools = ...
    if body.full_access:
        tools.extend(app.state.mcp.get_tools())
    return tools
```

---

## 3. Unified Tool Registry

### 3.1 Single Source of Truth
```python
# services/tool_registry.py
class ToolRegistry:
    def __init__(self):
        self.builtin = []      # agent_tools.py + agent_exec_tools.py
        self.skills = []       # discovered from skills/
        self.mcp = []          # from MCP servers
    
    def get_tools(self, tier: str) -> list[FunctionTool]:
        tools = self.builtin_for_tier(tier)
        tools.extend(self.skills_for_tier(tier))
        if tier == "full_access":
            tools.extend(self.mcp)
        return tools
```

### 3.2 Tier Hierarchy
```
read-only  ⊂  actions  ⊂  full_access
```

| Source | read-only | actions | full_access |
|--------|-----------|---------|-------------|
| Builtin (agent_tools) | ✓ | ✓ | ✓ |
| Builtin actions | | ✓ | ✓ |
| Builtin exec (agent_exec_tools) | | | ✓ |
| Skills (REQUIRES_ACTIONS) | | ✓ | ✓ |
| Skills (REQUIRES_FULL_ACCESS) | | | ✓ |
| MCP servers (tier: "actions") | | ✓ | ✓ |
| MCP servers (tier: "full_access") | | | ✓ |

---

## 4. Implementation Steps

| Phase | Task | Files |
|-------|------|-------|
| 1 | Create `skills/` directory structure + `__init__.py` | `skills/builtin/__init__.py`, `skills/user/__init__.py` |
| 2 | Implement `SkillLoader` discovery | `backend/app/services/skill_loader.py` |
| 3 | Add `SKILLS_DIR` to config | `backend/app/config.py` |
| 4 | Integrate into `_resolve_tools()` | `backend/app/routers/agent.py` |
| 5 | Add MCP client dependency | `requirements.txt` → `mcp` |
| 6 | Implement `MCPServer` wrapper | `backend/app/services/mcp_client.py` |
| 7 | Implement `MCPManager` with config | `backend/app/services/mcp_manager.py` |
| 8 | Add `MCP_CONFIG` to config | `backend/app/config.py` |
| 9 | Integrate MCP into tool registry | `backend/app/routers/agent.py` or lifespan |
| 10 | Create example skill + MCP config | `skills/builtin/example.py`, `config/mcp_servers.json.example` |

---

## 5. Security Considerations

| Risk | Mitigation |
|------|------------|
| Skills execute arbitrary code | Skills run in same process - trust boundary is the deployment. User skills dir should be owned by trusted user. |
| MCP servers access host | MCP servers run in separate processes. Use Docker/containers for isolation. Deny patterns in `agent_exec_tools.py` don't apply to MCP. |
| Tool name collisions | Prefix MCP tools with `mcp_{server}_{tool}`, skills with `skill_{name}_{tool}`. |
| Resource exhaustion | Connection pooling, timeouts, max concurrent MCP calls. |

---

## 6. Example Usage

### User installs a skill:
```bash
# Create skill
mkdir -p skills/user/github_tools
cat > skills/user/github_tools/__init__.py << 'EOF'
from agents import FunctionTool, function_tool
import httpx

@function_tool
async def create_issue(repo: str, title: str, body: str) -> str:
    """Create a GitHub issue."""
    # ... implementation
    return f"Issue created in {repo}"

TOOLS = [create_issue]
NAME = "github_tools"
REQUIRES_FULL_ACCESS = True  # needs network
EOF
```

### Configure MCP server:
```json
// config/mcp_servers.json
{
  "servers": [{
    "name": "postgres",
    "command": "docker",
    "args": ["run", "-i", "--rm", "mcp/postgres", "postgresql://user:pass@host/db"],
    "enabled": true,
    "tier": "actions"
  }]
}
```

### Agent automatically gets new tools:
```python
# Request with full_access=true now has:
# - create_issue (from skill)
# - postgres_query (from MCP)
# All invoked identically via FunctionTool
```

---

## 7. Testing Strategy

1. **Unit**: SkillLoader discovers modules, filters by tier
2. **Integration**: MCPManager connects to test server (stdio), wraps tools
3. **E2E**: Agent request with `full_access=true` → tools from skill + MCP appear in `/agent/tools` and are callable
4. **Security**: Verify tier gating works (skill with `REQUIRES_ACTIONS` not available in read-only)

---

## 8. Future Extensions

- **Hot reload**: Watch `skills/` directory, reload on change
- **Skill marketplace**: Install from git/URL via CLI command
- **MCP over HTTP/SSE**: Support remote MCP servers (not just stdio)
- **Tool permissions**: Per-tool allow/deny lists in config
- **Skill dependencies**: `requirements.txt` per skill, auto-install