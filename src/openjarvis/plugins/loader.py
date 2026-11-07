"""Simple plugin loader for Nivos.

Discovers, parses, and loads external plugins from:
- `<repo_root>/plugins/`
- `<config_dir>/plugins/`
"""

from __future__ import annotations

import importlib.util
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.core.paths import _find_source_root, get_config_dir
from openjarvis.core.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class PluginManifest:
    """Metadata describing an installed Nivos plugin."""

    name: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    entrypoint: Optional[str] = None
    tools: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    mcp_servers: List[Dict[str, Any]] = field(default_factory=list)
    enabled: bool = True
    path: str = ""
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "entrypoint": self.entrypoint,
            "tools": self.tools,
            "skills": self.skills,
            "mcp_servers": self.mcp_servers,
            "enabled": self.enabled,
            "path": self.path,
            "error": self.error,
        }


class PluginLoader:
    """Manages discovery and lifecycle of plugins."""

    def __init__(self, search_paths: Optional[List[Path]] = None) -> None:
        if search_paths is not None:
            self._search_paths = search_paths
        else:
            paths = []
            source_root = _find_source_root()
            if source_root is not None:
                paths.append(source_root / "plugins")
            paths.append(get_config_dir() / "plugins")
            self._search_paths = paths

        self._plugins: Dict[str, PluginManifest] = {}

    def get_search_paths(self) -> List[Path]:
        return list(self._search_paths)

    def discover_plugins(self) -> List[PluginManifest]:
        """Scan search paths for plugin packages and manifests."""
        discovered: Dict[str, PluginManifest] = {}

        for base_dir in self._search_paths:
            if not base_dir.exists() or not base_dir.is_dir():
                continue

            for item in base_dir.iterdir():
                if not item.is_dir() or item.name.startswith((".", "_")):
                    continue

                manifest = self._load_plugin_dir(item)
                if manifest:
                    discovered[manifest.name] = manifest

        self._plugins = discovered
        logger.info("Discovered %d Nivos plugins", len(discovered))
        return list(discovered.values())

    def _load_plugin_dir(self, plugin_dir: Path) -> Optional[PluginManifest]:
        """Parse plugin directory and load its manifest/entrypoint."""
        manifest_file = plugin_dir / "plugin.json"
        if not manifest_file.exists():
            manifest_file = plugin_dir / "manifest.json"

        data: Dict[str, Any] = {}
        if manifest_file.exists():
            try:
                data = json.loads(manifest_file.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Failed to parse %s: %s", manifest_file, exc)
                return PluginManifest(
                    name=plugin_dir.name,
                    path=str(plugin_dir),
                    error=f"Failed to parse manifest: {exc}",
                )

        name = data.get("name", plugin_dir.name)
        manifest = PluginManifest(
            name=name,
            version=data.get("version", "0.1.0"),
            description=data.get("description", ""),
            author=data.get("author", ""),
            entrypoint=data.get("entrypoint"),
            tools=list(data.get("tools", [])),
            skills=list(data.get("skills", [])),
            mcp_servers=list(data.get("mcp_servers", [])),
            enabled=data.get("enabled", True),
            path=str(plugin_dir),
        )

        if not manifest.enabled:
            return manifest

        # Attempt to load entrypoint module
        entrypoint_file = None
        if manifest.entrypoint:
            entrypoint_file = plugin_dir / manifest.entrypoint
        elif (plugin_dir / "main.py").exists():
            entrypoint_file = plugin_dir / "main.py"
        elif (plugin_dir / "__init__.py").exists():
            entrypoint_file = plugin_dir / "__init__.py"

        if entrypoint_file and entrypoint_file.exists():
            try:
                module_name = f"nivos_plugin_{manifest.name}"
                spec = importlib.util.spec_from_file_location(module_name, entrypoint_file)
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)

                    # Call setup/register hook if defined
                    hook = getattr(mod, "setup", None) or getattr(mod, "register", None)
                    if callable(hook):
                        hook(self)

                    # Check for exported tools
                    exported_tools = getattr(mod, "TOOLS", None)
                    if isinstance(exported_tools, (list, tuple)):
                        for t in exported_tools:
                            t_name = getattr(t, "tool_id", getattr(getattr(t, "spec", None), "name", None))
                            if t_name and t_name not in manifest.tools:
                                manifest.tools.append(str(t_name))
                                if not ToolRegistry.contains(t_name):
                                    ToolRegistry.register_value(t_name, t)
            except Exception as exc:
                manifest.error = f"Error loading entrypoint: {exc}"
                logger.warning("Error loading plugin %s from %s: %s", name, entrypoint_file, exc)

        return manifest

    def get_plugin(self, name: str) -> Optional[PluginManifest]:
        return self._plugins.get(name)

    def list_plugins(self) -> List[PluginManifest]:
        if not self._plugins:
            self.discover_plugins()
        return list(self._plugins.values())


_default_loader: Optional[PluginLoader] = None


def get_plugin_loader() -> PluginLoader:
    global _default_loader
    if _default_loader is None:
        _default_loader = PluginLoader()
    return _default_loader

