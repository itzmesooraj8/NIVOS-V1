"""MCP & Plugins management routes for Nivos.

Provides endpoints to list, add, remove MCP servers and discover plugins.
Discovered MCP tools are registered as native tools available to agents.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from openjarvis.core.paths import get_config_dir
from openjarvis.core.registry import ToolRegistry
from openjarvis.mcp.client import MCPClient
from openjarvis.mcp.transport import StdioTransport, StreamableHTTPTransport
from openjarvis.plugins.loader import get_plugin_loader
from openjarvis.tools.mcp_adapter import MCPToolAdapter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp"])

# Global map of server_name -> MCPClient to keep transport sessions alive
_active_clients: Dict[str, MCPClient] = {}
# Global map of server_name -> list of tool names registered by this server
_registered_server_tools: Dict[str, List[str]] = {}


class McpServerRequest(BaseModel):
    name: str
    command: Optional[str] = None
    args: Optional[List[str]] = None
    url: Optional[str] = None
    token: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    enabled: bool = True


def get_mcp_config_file() -> Path:
    return get_config_dir() / "mcp_servers.json"


def _read_configured_servers() -> List[Dict[str, Any]]:
    path = get_mcp_config_file()
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return []


def _write_configured_servers(servers: List[Dict[str, Any]]) -> None:
    path = get_mcp_config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(servers, indent=2), encoding="utf-8")


def _connect_and_register_server(cfg: Dict[str, Any]) -> List[str]:
    """Connect to an MCP server, discover its tools, and register them."""
    name = cfg.get("name", "unnamed")
    url = cfg.get("url")
    token = cfg.get("token")
    command = cfg.get("command")
    args = cfg.get("args") or []
    env = cfg.get("env")

    # Close any existing client for this server
    if name in _active_clients:
        try:
            _active_clients[name].close()
        except Exception:
            pass
        _active_clients.pop(name, None)

    # Clean up prior registered tools
    if name in _registered_server_tools:
        for t_name in _registered_server_tools[name]:
            if ToolRegistry.contains(t_name):
                ToolRegistry._entries().pop(t_name, None)
        _registered_server_tools.pop(name, None)

    if url:
        transport = StreamableHTTPTransport(url=url, token=token)
    elif command:
        transport = StdioTransport(command=[command] + args, env=env)
    else:
        raise ValueError("Server requires either 'command' or 'url'")

    client = MCPClient(transport)
    client.initialize()
    tool_specs = client.list_tools()

    registered_names: List[str] = []
    for spec in tool_specs:
        adapter = MCPToolAdapter(client, spec)
        # Register tool using its spec name so agents can call it directly
        tool_name = spec.name
        if not ToolRegistry.contains(tool_name):
            ToolRegistry.register_value(tool_name, adapter)
            registered_names.append(tool_name)
        else:
            # Prefixed fallback if name conflicts with built-in tool
            prefixed = f"mcp_{name}_{spec.name}"
            if not ToolRegistry.contains(prefixed):
                ToolRegistry.register_value(prefixed, adapter)
                registered_names.append(prefixed)

    _active_clients[name] = client
    _registered_server_tools[name] = registered_names
    logger.info("Registered %d tools from MCP server '%s'", len(registered_names), name)
    return registered_names


@router.get("/v1/mcp/servers")
async def list_mcp_servers() -> Dict[str, Any]:
    """List all configured MCP servers and active tools."""
    servers = _read_configured_servers()
    result = []
    for s in servers:
        name = s.get("name", "")
        active = name in _active_clients
        tools = _registered_server_tools.get(name, [])
        result.append({
            **s,
            "connected": active,
            "tools": tools,
            "tool_count": len(tools),
        })
    return {"servers": result, "count": len(result)}


@router.post("/v1/mcp/servers")
async def add_or_update_mcp_server(req: McpServerRequest) -> Dict[str, Any]:
    """Add or update an MCP server, connect to it, and register tools."""
    cfg = req.model_dump()
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Server name is required")

    discovered: List[str] = []
    error_msg: Optional[str] = None
    if req.enabled:
        try:
            discovered = _connect_and_register_server(cfg)
        except Exception as exc:
            error_msg = str(exc)
            logger.warning("Could not connect to MCP server '%s': %s", name, exc)

    # Persist in mcp_servers.json
    servers = _read_configured_servers()
    updated = False
    for i, s in enumerate(servers):
        if s.get("name") == name:
            servers[i] = cfg
            updated = True
            break
    if not updated:
        servers.append(cfg)
    _write_configured_servers(servers)

    return {
        "success": error_msg is None,
        "name": name,
        "discovered_tools": discovered,
        "count": len(discovered),
        "error": error_msg,
    }


@router.delete("/v1/mcp/servers/{server_name}")
async def delete_mcp_server(server_name: str) -> Dict[str, Any]:
    """Delete an MCP server, close its connection, and remove its tools."""
    # Close active connection
    if server_name in _active_clients:
        try:
            _active_clients[server_name].close()
        except Exception:
            pass
        _active_clients.pop(server_name, None)

    # Remove registered tools
    if server_name in _registered_server_tools:
        for t_name in _registered_server_tools[server_name]:
            if ToolRegistry.contains(t_name):
                ToolRegistry._entries().pop(t_name, None)
        _registered_server_tools.pop(server_name, None)

    # Update persistent config
    servers = _read_configured_servers()
    new_servers = [s for s in servers if s.get("name") != server_name]
    _write_configured_servers(new_servers)

    return {"success": True, "deleted": server_name}


# ---- Plugins endpoints ----


@router.get("/v1/plugins")
async def list_plugins() -> Dict[str, Any]:
    """List all installed and discovered plugins."""
    loader = get_plugin_loader()
    plugins = loader.list_plugins()
    return {"plugins": [p.to_dict() for p in plugins], "count": len(plugins)}


@router.post("/v1/plugins/reload")
async def reload_plugins() -> Dict[str, Any]:
    """Re-scan search paths and reload plugins."""
    loader = get_plugin_loader()
    plugins = loader.discover_plugins()
    return {"plugins": [p.to_dict() for p in plugins], "count": len(plugins)}

