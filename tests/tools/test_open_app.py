"""Tests for open_app tool and security boundaries."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from nivos.core.config import load_config
from nivos.security.policy import RequestOrigin
from nivos.server.app import create_app
from nivos.tools.open_app import OpenAppTool, _BLOCKED_BINARIES


@pytest.fixture
def open_app_tool():
    return OpenAppTool()


def test_blocked_binaries(open_app_tool):
    """Dangerous executables and shells must be strictly blocked."""
    for binary in ["cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "regedit", "mshta", "rundll32", "bash", "wsl"]:
        res = open_app_tool.execute(app=binary, origin=RequestOrigin.LOCAL_CHAT)
        assert not res.success
        assert "Security block" in res.content
        assert "forbidden" in res.content


def test_blocked_scripts_and_installers(open_app_tool):
    """Scripts and installer binaries must be strictly blocked."""
    dangerous = ["test.bat", "run.ps1", "script.vbs", "setup.exe", "install.msi", "installer_tool"]
    for d in dangerous:
        res = open_app_tool.execute(app=d, origin=RequestOrigin.LOCAL_CHAT)
        assert not res.success
        assert "Security block" in res.content


def test_blocked_elevation(open_app_tool):
    """Elevation requests (runas) must be strictly blocked."""
    res = open_app_tool.execute(app="runas notepad.exe", origin=RequestOrigin.LOCAL_CHAT)
    assert not res.success
    assert "Security block" in res.content
    assert "elevated" in res.content.lower()


def test_target_path_traversal_blocked(open_app_tool):
    """Target paths with '..' traversal or outside allowed dirs must be blocked."""
    res = open_app_tool.execute(
        app="notepad",
        target="../../Windows/System32/drivers/etc/hosts",
        approval="once",
        origin=RequestOrigin.LOCAL_CHAT,
    )
    assert not res.success
    assert "Security block" in res.content
    assert "traversal" in res.content.lower() or "must be within" in res.content.lower()


def test_untrusted_content_and_external_origin_blocked(open_app_tool):
    """Untrusted content or non-local origins must be rejected by central policy."""
    # From untrusted content (e.g. from web or file)
    res_untrusted = open_app_tool.execute(
        app="notepad",
        approval="once",
        origin=RequestOrigin.LOCAL_CHAT,
        is_untrusted_content=True,
    )
    assert not res_untrusted.success
    assert "Security rejection" in res_untrusted.content

    # From Slack origin
    res_slack = open_app_tool.execute(
        app="notepad",
        approval="once",
        origin=RequestOrigin.SLACK,
        is_untrusted_content=False,
    )
    assert not res_slack.success
    assert "Security rejection" in res_slack.content

    # From Scheduled Task origin
    res_sched = open_app_tool.execute(
        app="notepad",
        approval="once",
        origin=RequestOrigin.SCHEDULED_TASK,
        is_untrusted_content=False,
    )
    assert not res_sched.success
    assert "Security rejection" in res_sched.content


def test_approval_flow_prompt_and_denial(open_app_tool, monkeypatch):
    """First launch without approval prompts Once/Always/Deny; Deny stops launch."""
    # Mock load_config to ensure app is not in allowed_apps
    mock_cfg = MagicMock()
    mock_cfg.security.allowed_apps = []
    monkeypatch.setattr("nivos.tools.open_app.load_config", lambda: mock_cfg)

    # 1. No approval flag -> requires approval
    res_prompt = open_app_tool.execute(app="notepad", origin=RequestOrigin.LOCAL_CHAT)
    assert not res_prompt.success
    assert res_prompt.metadata.get("needs_approval") is True
    assert "Approval required" in res_prompt.content

    # 2. Denied by user
    res_deny = open_app_tool.execute(app="notepad", approval="deny", origin=RequestOrigin.LOCAL_CHAT)
    assert not res_deny.success
    assert "denied by user" in res_deny.content.lower()


@patch("subprocess.Popen")
def test_approval_once_launches(mock_popen, open_app_tool, monkeypatch):
    """Approval='once' launches the safe app."""
    mock_cfg = MagicMock()
    mock_cfg.security.allowed_apps = []
    monkeypatch.setattr("nivos.tools.open_app.load_config", lambda: mock_cfg)

    res = open_app_tool.execute(app="notepad", approval="once", origin=RequestOrigin.LOCAL_CHAT)
    assert res.success
    assert "Receipt: tool=open_app" in res.content
    assert mock_popen.called


@patch("subprocess.Popen")
def test_pre_approved_app_launches_without_prompt(mock_popen, open_app_tool, monkeypatch):
    """If app is in allowed_apps, it launches without needing approval='once'."""
    mock_cfg = MagicMock()
    mock_cfg.security.allowed_apps = ["notepad", "calc"]
    monkeypatch.setattr("nivos.tools.open_app.load_config", lambda: mock_cfg)

    res = open_app_tool.execute(app="notepad", origin=RequestOrigin.LOCAL_CHAT)
    assert res.success
    assert "Receipt: tool=open_app" in res.content
    assert mock_popen.called


def test_allowed_schemes(open_app_tool, monkeypatch):
    """Allowed schemes (ms-settings:, spotify:) work with approval."""
    mock_cfg = MagicMock()
    mock_cfg.security.allowed_apps = []
    monkeypatch.setattr("nivos.tools.open_app.load_config", lambda: mock_cfg)

    with patch("os.startfile", create=True) as mock_startfile, \
         patch("webbrowser.open") as mock_webbrowser:
        res = open_app_tool.execute(app="ms-settings:", approval="once", origin=RequestOrigin.LOCAL_CHAT)
        assert res.success
        assert "Receipt: tool=open_app" in res.content


def test_app_api_routes(tmp_path, monkeypatch):
    """Test /v1/apps/allowed GET and POST endpoints."""
    app = create_app(engine=MagicMock(), model="test-model")
    client = TestClient(app)

    # Mock _persist_config_section and load_config
    stored_apps = ["notepad"]

    def fake_load():
        cfg = MagicMock()
        cfg.security.allowed_apps = list(stored_apps)
        return cfg

    def fake_persist(sec, data):
        if sec == "security" and "allowed_apps" in data:
            stored_apps.clear()
            stored_apps.extend(data["allowed_apps"])

    monkeypatch.setattr("nivos.server.app_routes.load_config", fake_load)
    monkeypatch.setattr("nivos.server.app_routes._persist_config_section", fake_persist)

    # 1. GET /v1/apps/allowed
    resp = client.get("/v1/apps/allowed")
    assert resp.status_code == 200
    assert resp.json()["allowed_apps"] == ["notepad"]

    # 2. POST /v1/apps/allowed add
    resp_add = client.post("/v1/apps/allowed", json={"app": "spotify", "action": "add"})
    assert resp_add.status_code == 200
    assert "spotify" in resp_add.json()["allowed_apps"]

    # 3. POST /v1/apps/allowed remove
    resp_rem = client.post("/v1/apps/allowed", json={"app": "notepad", "action": "remove"})
    assert resp_rem.status_code == 200
    assert "notepad" not in resp_rem.json()["allowed_apps"]
    assert "spotify" in resp_rem.json()["allowed_apps"]
