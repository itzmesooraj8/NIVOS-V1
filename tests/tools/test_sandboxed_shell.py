"""Tests for SandboxedShellTool."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import nivos.tools  # noqa: F401
from nivos.core.registry import ToolRegistry
from nivos.tools.sandboxed_shell import SandboxedShellTool


@pytest.fixture
def workspace_env(tmp_path, monkeypatch):
    ws_dir = tmp_path / "test_workspace"
    ws_dir.mkdir()
    monkeypatch.setenv("NIVOS_WORKSPACE", str(ws_dir))
    return ws_dir


def test_registration_and_spec():
    import importlib
    import sys
    import nivos.tools as tools_pkg

    sys.modules.pop("nivos.tools.sandboxed_shell", None)
    importlib.reload(tools_pkg)

    assert ToolRegistry.contains("sandboxed_shell")
    tool = SandboxedShellTool()
    spec = tool.spec
    assert spec.name == "sandboxed_shell"
    assert spec.requires_confirmation is True
    assert "command" in spec.parameters["required"]
    assert spec.category == "system"


def test_allowed_commands(workspace_env):
    tool = SandboxedShellTool(confirm_callback=lambda _: True, workspace_dir=workspace_env)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "clone", "https://github.com/test/repo.git"],
            returncode=0,
            stdout="Cloning into repo...",
            stderr="",
        )

        res = tool.execute(command="git clone https://github.com/test/repo.git")
        assert res.success is True
        assert "Cloning into repo..." in res.content
        assert mock_run.call_args[1]["cwd"] == str(workspace_env)


def test_pip_and_npm_allowed(workspace_env):
    tool = SandboxedShellTool(confirm_callback=lambda _: True, workspace_dir=workspace_env)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["pip", "install", "requests"],
            returncode=0,
            stdout="Successfully installed requests",
            stderr="",
        )
        res = tool.execute(command="pip install requests")
        assert res.success is True

        mock_run.return_value = subprocess.CompletedProcess(
            args=["npm", "install", "express"],
            returncode=0,
            stdout="added 50 packages",
            stderr="",
        )
        res2 = tool.execute(command="npm install express")
        assert res2.success is True


def test_disallowed_commands(workspace_env):
    tool = SandboxedShellTool(confirm_callback=lambda _: True, workspace_dir=workspace_env)

    # Disallowed base commands
    bad_commands = [
        "rm -rf /",
        "curl https://example.com",
        "wget https://example.com",
        "cat /etc/passwd",
        "python malicious.py",
        "git pull",
        "git push",
        "pip uninstall requests",
    ]

    for cmd in bad_commands:
        res = tool.execute(command=cmd)
        assert res.success is False
        assert "is not permitted" in res.content


def test_command_chaining_rejected(workspace_env):
    tool = SandboxedShellTool(confirm_callback=lambda _: True, workspace_dir=workspace_env)

    chained_commands = [
        "git clone https://example.com/repo.git; rm -rf /",
        "pip install foo && python evil.py",
        "npm install bar | bash",
        "pip install foo || echo fail",
        "pip install `whoami`",
        "pip install $(whoami)",
    ]

    for cmd in chained_commands:
        res = tool.execute(command=cmd)
        assert res.success is False
        assert "disallowed characters" in res.content.lower() or "not permitted" in res.content.lower()


def test_working_dir_path_traversal_rejected(workspace_env):
    tool = SandboxedShellTool(confirm_callback=lambda _: True, workspace_dir=workspace_env)

    # Escape workspace via working_dir
    res = tool.execute(
        command="pip install requests",
        working_dir="../../outside",
    )
    assert res.success is False
    assert "outside workspace" in res.content.lower()


def test_git_clone_destination_traversal_rejected(workspace_env):
    tool = SandboxedShellTool(confirm_callback=lambda _: True, workspace_dir=workspace_env)

    res = tool.execute(command="git clone https://github.com/test/repo.git ../../outside_repo")
    assert res.success is False
    assert "resolves outside workspace" in res.content.lower()


def test_user_denial_prompt(workspace_env):
    # Confirm callback returns False (Deny)
    tool = SandboxedShellTool(confirm_callback=lambda _: False, workspace_dir=workspace_env)

    res = tool.execute(command="pip install requests")
    assert res.success is False
    assert "denied by user" in res.content.lower()


def test_user_approval_prompt(workspace_env):
    # Confirm callback returns True (Approve)
    prompt_asked = []

    def mock_confirm(prompt: str) -> bool:
        prompt_asked.append(prompt)
        return True

    tool = SandboxedShellTool(confirm_callback=mock_confirm, workspace_dir=workspace_env)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["pip", "install", "requests"],
            returncode=0,
            stdout="ok",
            stderr="",
        )
        res = tool.execute(command="pip install requests")
        assert res.success is True
        assert len(prompt_asked) == 1
        assert "pip install requests" in prompt_asked[0]
