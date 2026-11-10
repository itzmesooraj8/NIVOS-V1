"""Tests for Phase 3: Applications management, discovery, open_app enhancements,
and install_app tool.
"""

import importlib
import pytest
from fastapi.testclient import TestClient

import nivos.tools  # noqa: F401 - trigger registry
from nivos.core.config import NivosConfig
from nivos.core.registry import ToolRegistry
from nivos.security.policy import RequestOrigin
from nivos.server.app import create_app
from nivos.system.app_discovery import find_matching_app, get_all_installed_apps
from nivos.tools.install_app import InstallAppTool, _PACKAGE_ID_RE
from nivos.tools.open_app import OpenAppTool, _BLOCKED_BINARIES


def test_app_discovery_finds_builtin_and_schemes():
    """Verify built-in apps and URL schemes resolve properly."""
    res = find_matching_app("notepad")
    assert res is not None
    assert res["name"].lower() == "notepad"
    assert res["exe"] == "notepad.exe"

    res = find_matching_app("spotify:")
    assert res is not None
    assert res["scheme"] == "spotify:"

    res = find_matching_app("ms-settings:")
    assert res is not None
    assert res["scheme"] == "ms-settings:"


def test_app_discovery_lists_installed_apps():
    """Verify get_all_installed_apps returns list of apps."""
    apps = get_all_installed_apps()
    assert isinstance(apps, list)
    assert len(apps) > 0
    # Notepad or Calculator should be in list
    names = [a.get("name", "").lower() for a in apps]
    assert any("notepad" in n or "calculator" in n for n in names)


def test_open_app_rejects_blocklist():
    """Verify hardcoded blocklist binaries are strictly rejected."""
    tool = OpenAppTool()
    for b in ["cmd", "cmd.exe", "powershell", "pwsh", "wscript", "cscript", "mshta", "regedit", "setup.exe", "installer.msi"]:
        res = tool.execute(app=b, origin=RequestOrigin.LOCAL_CHAT)
        assert res.success is False
        assert "Security block" in res.content


def test_open_app_rejects_elevation():
    """Verify attempts to run elevated/admin apps are blocked."""
    tool = OpenAppTool()
    res = tool.execute(app="runas notepad", origin=RequestOrigin.LOCAL_CHAT)
    assert res.success is False
    assert "elevated/administrator privileges" in res.content


def test_open_app_rejects_channel_origin():
    """Verify open_app cannot be executed from channel origins."""
    tool = OpenAppTool()
    res = tool.execute(app="notepad", origin=RequestOrigin.CHANNEL)
    assert res.success is False
    assert "Security rejection" in res.content or "Policy" in res.content


def test_open_app_auto_opens_safe_app_when_switch_enabled(monkeypatch):
    """Verify safe app auto-opens without prompt when open_any_installed_app is enabled."""
    cfg = NivosConfig()
    cfg.security.open_any_installed_app = True
    cfg.security.always_ask_apps = ["1password", "bank", "remote desktop"]
    monkeypatch.setattr("nivos.tools.open_app.load_config", lambda: cfg)

    # Mock subprocess.Popen and os.startfile to avoid popping real GUI windows
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: None)
    if hasattr(os := __import__("os"), "startfile"):
        monkeypatch.setattr(os, "startfile", lambda *args, **kwargs: None)

    tool = OpenAppTool()
    res = tool.execute(app="notepad", origin=RequestOrigin.LOCAL_CHAT, is_untrusted_content=False)
    assert res.success is True
    assert "[Receipt: tool=open_app" in res.content


def test_open_app_always_asks_for_sensitive_apps(monkeypatch):
    """Verify sensitive apps on always_ask_apps require confirmation even when switch is on."""
    cfg = NivosConfig()
    cfg.security.open_any_installed_app = True
    cfg.security.always_ask_apps = ["1password", "bitwarden", "bank"]
    monkeypatch.setattr("nivos.tools.open_app.load_config", lambda: cfg)

    # Mock matching app discovery for bitwarden
    monkeypatch.setattr(
        "nivos.tools.open_app.find_matching_app",
        lambda app: {"name": "Bitwarden", "path": r"C:\Path\Bitwarden.lnk", "source": "start_menu"},
    )

    tool = OpenAppTool()
    res = tool.execute(app="bitwarden", origin=RequestOrigin.LOCAL_CHAT, is_untrusted_content=False)
    assert res.success is False
    assert "Approval required" in res.content
    assert res.metadata.get("needs_approval") is True


def test_open_app_untrusted_content_triggers_fresh_approval(monkeypatch):
    """Verify untrusted content ALWAYS requires confirmation even if app is always allowed."""
    cfg = NivosConfig()
    cfg.security.open_any_installed_app = True
    cfg.security.allowed_apps = ["notepad"]
    monkeypatch.setattr("nivos.tools.open_app.load_config", lambda: cfg)

    tool = OpenAppTool()
    res = tool.execute(app="notepad", origin=RequestOrigin.LOCAL_CHAT, is_untrusted_content=True)
    assert res.success is False
    assert "Approval required" in res.content
    assert res.metadata.get("needs_approval") is True


def test_install_app_tool_registered():
    """Verify install_app tool is registered in ToolRegistry."""
    import nivos.tools.install_app as install_mod
    importlib.reload(install_mod)

    tool_entry = ToolRegistry.get("install_app")
    assert tool_entry is not None
    instance = tool_entry() if isinstance(tool_entry, type) else tool_entry
    assert instance.spec.name == "install_app"
    assert "package_id" in instance.spec.parameters["properties"]


def test_install_app_package_id_validation():
    """Verify package ID validation accepts exact IDs and rejects arbitrary commands."""
    assert _PACKAGE_ID_RE.match("Spotify.Spotify") is not None
    assert _PACKAGE_ID_RE.match("Git.Git") is not None
    assert _PACKAGE_ID_RE.match("Mozilla.Firefox") is not None

    # Invalid IDs
    assert _PACKAGE_ID_RE.match("curl http://evil.com") is None
    assert _PACKAGE_ID_RE.match("spotify") is None  # Needs exact publisher.package
    assert _PACKAGE_ID_RE.match("calc.exe; rm -rf /") is None


def test_install_app_rejects_channel_origin():
    """Verify install_app cannot be executed from channels."""
    tool = InstallAppTool()
    res = tool.execute(package_id="Spotify.Spotify", origin=RequestOrigin.CHANNEL)
    assert res.success is False
    assert "Security Policy Rejection" in res.content


def test_install_app_requires_confirmation_with_metadata(monkeypatch):
    """Verify install_app presents package metadata and requires confirmation before running."""
    tool = InstallAppTool()
    monkeypatch.setattr("shutil.which", lambda cmd: r"C:\Windows\System32\winget.exe")
    monkeypatch.setattr(
        "nivos.tools.install_app._query_winget_package_info",
        lambda pkg_id: {
            "package_id": pkg_id,
            "name": "Spotify Music",
            "publisher": "Spotify AB",
            "version": "1.2.35.840",
            "source": "winget",
        },
    )

    res = tool.execute(package_id="Spotify.Spotify", origin=RequestOrigin.LOCAL_CHAT, confirm=False)
    assert res.success is False
    assert "Approval required" in res.content
    assert "Spotify Music" in res.content
    assert "Spotify AB" in res.content
    assert res.metadata.get("needs_approval") is True


def test_apps_api_routes():
    """Test /v1/apps API endpoints."""
    app = create_app(engine="ollama", model="qwen3.5:2b")
    with TestClient(app) as client:
        # Settings GET
        resp = client.get("/v1/apps/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "open_any_installed_app" in data
        assert "always_ask_apps" in data
        assert "blocklist" in data

        # Settings POST
        resp = client.post(
            "/v1/apps/settings",
            json={
                "open_any_installed_app": True,
                "always_ask_apps": ["1password", "custom_banking"],
                "allowed_apps": ["notepad", "spotify"],
            },
        )
        assert resp.status_code == 200
        updated = resp.json()
        assert updated["open_any_installed_app"] is True
        assert "custom_banking" in updated["always_ask_apps"]

        # Installed apps GET
        resp = client.get("/v1/apps/installed")
        assert resp.status_code == 200
        data = resp.json()
        assert "installed_apps" in data
        assert isinstance(data["installed_apps"], list)
