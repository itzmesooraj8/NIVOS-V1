"""Tests for PluginLoader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjarvis.core.registry import ToolRegistry
from openjarvis.plugins.loader import PluginLoader, PluginManifest


def test_discover_plugins_from_directory(tmp_path):
    # Create two plugin directories in tmp_path
    plugin_a = tmp_path / "plugin-alpha"
    plugin_a.mkdir()
    (plugin_a / "plugin.json").write_text(
        json.dumps({
            "name": "plugin-alpha",
            "version": "1.2.0",
            "description": "Alpha test plugin",
            "tools": ["alpha_tool"],
        }),
        encoding="utf-8",
    )

    plugin_b = tmp_path / "plugin-beta"
    plugin_b.mkdir()
    (plugin_b / "manifest.json").write_text(
        json.dumps({
            "name": "plugin-beta",
            "version": "0.5.0",
            "description": "Beta test plugin",
            "enabled": True,
        }),
        encoding="utf-8",
    )

    loader = PluginLoader(search_paths=[tmp_path])
    plugins = loader.discover_plugins()

    assert len(plugins) == 2
    names = {p.name for p in plugins}
    assert "plugin-alpha" in names
    assert "plugin-beta" in names

    alpha = loader.get_plugin("plugin-alpha")
    assert alpha is not None
    assert alpha.version == "1.2.0"
    assert "alpha_tool" in alpha.tools


def test_plugin_with_python_entrypoint(tmp_path):
    plugin_dir = tmp_path / "py-plugin"
    plugin_dir.mkdir()

    (plugin_dir / "plugin.json").write_text(
        json.dumps({
            "name": "py-plugin",
            "version": "1.0.0",
            "entrypoint": "main.py",
        }),
        encoding="utf-8",
    )

    py_code = """
from openjarvis.tools._stubs import BaseTool, ToolSpec
from openjarvis.core.types import ToolResult

class DynamicPluginTool(BaseTool):
    tool_id = "dynamic_plugin_tool"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="dynamic_plugin_tool", description="Dynamic tool from plugin")

    def execute(self, **kwargs) -> ToolResult:
        return ToolResult(tool_name="dynamic_plugin_tool", content="pong", success=True)

TOOLS = [DynamicPluginTool()]
"""
    (plugin_dir / "main.py").write_text(py_code, encoding="utf-8")

    loader = PluginLoader(search_paths=[tmp_path])
    plugins = loader.discover_plugins()

    assert len(plugins) == 1
    p = plugins[0]
    assert p.name == "py-plugin"
    assert "dynamic_plugin_tool" in p.tools
    assert ToolRegistry.contains("dynamic_plugin_tool")
