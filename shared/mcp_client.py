"""
MCP Client for Feature 9: MCP Integration.

Connects to one or more MCP servers (started as subprocesses via stdio transport)
and exposes their tools to the agent loop in shared/agent.py.

TRANSPORT OVERVIEW:
  stdio — the MCP server runs as a subprocess; we communicate via stdin/stdout.
          Best for local tools and scripts. What we use in this course.
          The server process lives for the duration of the connection.

  SSE (Server-Sent Events) — the MCP server runs as a persistent HTTP service;
          the client connects via HTTP. Best for remote/shared tools accessible
          over a network (e.g., a company-hosted tool server).

  Streamable HTTP — newer bidirectional HTTP transport. Best for production
          deployments of MCP servers that serve many clients simultaneously.

  → We use stdio. To connect to a remote MCP server, switch to SSE transport:
    the connect_to_server() function signature stays the same, but the
    internals would use mcp.client.sse.sse_client() instead of stdio_client().

MCP SDK ECOSYSTEM:
  Python MCP SDK (package: "mcp") — official, what we use here.
    Supports stdio, SSE, and streamable-HTTP transports.
    Best for Python server-side integrations and scripts.

  TypeScript/JS SDK (npm: "@modelcontextprotocol/sdk") — official.
    Used when building MCP servers or clients in Node.js.
    Many popular servers (filesystem, Git, Postgres, GitHub) are TS-based;
    our Python client can connect to them via stdio without knowing TypeScript.

  Other SDKs: Rust, Go, Java, C# — community-maintained.
    See docs/mcp-setup-guide.md for the full SDK comparison table.

PUBLIC API:
  connect_to_server(server_command)  → registers a server in SERVER_REGISTRY
  list_mcp_tools()                  → list all tools from all registered servers
  call_mcp_tool(tool_name, args)    → invoke one tool and return its result
  get_mcp_tool_schemas()            → return OpenAI-format schemas for LLM tool calling
"""
import asyncio
import json
import os
import sys
from typing import Any, Optional

# Ensure subprocess support on Windows event loop (needed for anyio/open_process)
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        # If policy cannot be set for any reason, continue and let errors surface
        pass

# ---------------------------------------------------------------------------
# MCP SDK import — graceful fallback if not installed
# ---------------------------------------------------------------------------
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False
    ClientSession = None
    StdioServerParameters = None
    stdio_client = None


# =============================================================================
# Server registry
#
# Each entry: {
#   "name":    str   — human-readable name shown in the UI
#   "command": list[str] — how to start the server subprocess
#   "enabled": bool  — False entries are listed but not connected
#   "transport": str — "stdio" (all entries here), "sse" for remote servers
# }
#
# To add your own server, append an entry here (or set ENABLE_DOMAIN_MCP_SERVER=true).
# =============================================================================

def _build_server_registry() -> list[dict]:
    demo_server_cmd = [sys.executable, "-m", "shared.mcp_demo_server"]
    registry = [
        {
            "name":      "demo",
            "command":   demo_server_cmd,
            "enabled":   True,
            "transport": "stdio",
            "description": "Course demo server — weather, news, exchange rate (all mock data)",
        }
    ]
    # Domain server is opt-in via ENABLE_DOMAIN_MCP_SERVER=true in .env
    if os.getenv("ENABLE_DOMAIN_MCP_SERVER", "false").lower() == "true":
        registry.append({
            "name":      "domain",
            "command":   [sys.executable, "-m", "shared.domain_mcp_server"],
            "enabled":   True,
            "transport": "stdio",
            "description": "Your domain MCP server — replace placeholder tools with real ones",
        })
    return registry

SERVER_REGISTRY: list[dict] = _build_server_registry()


# =============================================================================
# Cache: tool list per server (populated lazily on first use)
# =============================================================================
_tool_cache: dict[str, list[dict]] = {}  # server_name → list of tool dicts


# =============================================================================
# Core client functions
# =============================================================================

async def _list_tools_from_server(server_entry: dict) -> list[dict]:
    """Connect to a server subprocess, list its tools, and disconnect."""
    if not _MCP_AVAILABLE:
        raise ImportError("The 'mcp' package is not installed. Run: pip install mcp")

    params = StdioServerParameters(
        command=server_entry["command"][0],
        args=server_entry["command"][1:],
    )
    tools: list[dict] = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            for tool in result.tools:
                tools.append({
                    "name":        tool.name,
                    "description": tool.description or "",
                    "inputSchema": tool.inputSchema or {},
                    "server":      server_entry["name"],
                })
    return tools


async def list_mcp_tools(use_cache: bool = True) -> list[dict]:
    """
    Return the list of available tools from all enabled MCP servers.

    Each entry is a dict with: name, description, inputSchema, server.

    =========================================================================
    TODO (starter): Implement this function.

    For each entry in SERVER_REGISTRY where entry["enabled"] is True:
      - Call: tools = await _list_tools_from_server(entry)
      - Extend the results list with the returned tools
      - Cache results in _tool_cache[entry["name"]] = tools

    On exception from one server: log a warning and continue (don't fail
    the whole list if one server is down).

    Return the combined list of all tools across all enabled servers.

    Hint: use asyncio.gather() or a simple loop with individual try/except blocks.
    =========================================================================
    """
    if not _MCP_AVAILABLE:
        return []

    all_tools: list[dict] = []
    for entry in SERVER_REGISTRY:
        if not entry.get("enabled"):
            continue
        name = entry["name"]
        if use_cache and name in _tool_cache:
            all_tools.extend(_tool_cache[name])
            continue
        try:
            tools = await _list_tools_from_server(entry)
            _tool_cache[name] = tools
            all_tools.extend(tools)
        except Exception as exc:
            print(f"[mcp_client] Warning: could not list tools from '{name}': {exc}", file=sys.stderr)
    return all_tools


async def _call_tool_on_server(server_entry: dict, tool_name: str, arguments: dict) -> dict:
    """Connect to a server subprocess, call a tool, and return the result."""
    if not _MCP_AVAILABLE:
        raise ImportError("The 'mcp' package is not installed. Run: pip install mcp")

    params = StdioServerParameters(
        command=server_entry["command"][0],
        args=server_entry["command"][1:],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            # MCP returns a list of content items; extract text from the first.
            if result.content:
                raw = result.content[0].text
                try:
                    return json.loads(raw)
                except (json.JSONDecodeError, AttributeError):
                    return {"result": str(raw)}
            return {"result": None}


async def call_mcp_tool(tool_name: str, arguments: dict) -> dict:
    """
    Invoke a named MCP tool and return its result as a dict.

    Looks up which server owns the tool from the cache, then connects
    and calls it.

    =========================================================================
    TODO (starter): Implement this function.

    1. Ensure the tool cache is populated: await list_mcp_tools()
    2. Find the server that owns tool_name:
       for entry in _tool_cache items, look for a tool with name == tool_name.
       When found, note the server name and find the matching SERVER_REGISTRY entry.
    3. If not found: return {"error": f"Tool '{tool_name}' not found in any MCP server"}
    4. Call: result = await _call_tool_on_server(server_entry, tool_name, arguments)
    5. Return result.
    =========================================================================
    """
    # Populate cache if needed.
    if not _tool_cache:
        await list_mcp_tools()

    # Find which server owns this tool.
    owner_name: Optional[str] = None
    for server_name, tools in _tool_cache.items():
        if any(t["name"] == tool_name for t in tools):
            owner_name = server_name
            break

    if owner_name is None:
        return {"error": f"Tool '{tool_name}' not found in any connected MCP server."}

    server_entry = next((s for s in SERVER_REGISTRY if s["name"] == owner_name), None)
    if server_entry is None:
        return {"error": f"Server '{owner_name}' not found in registry."}

    try:
        return await _call_tool_on_server(server_entry, tool_name, arguments)
    except Exception as exc:
        return {"error": str(exc)}


async def get_mcp_tool_schemas() -> list[dict]:
    """
    Return OpenAI-format tool schemas for all available MCP tools.

    The LLM receives these alongside the Feature 7 local tool schemas —
    it cannot tell which are "local" vs "MCP". They're just tools.
    """
    tools = await list_mcp_tools()
    schemas = []
    for tool in tools:
        schemas.append({
            "type": "function",
            "function": {
                "name":        tool["name"],
                "description": f"[MCP:{tool['server']}] {tool['description']}",
                "parameters":  tool.get("inputSchema", {"type": "object", "properties": {}}),
            },
        })
    return schemas
