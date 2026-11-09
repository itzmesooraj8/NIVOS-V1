"""Unit tests for Phase 8: Self-Improvement Agent."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nivos.security.policy import RequestOrigin, is_tool_allowed
from nivos.self_dev.manager import SelfDevManager
from nivos.self_dev.policy import is_path_protected
from nivos.server.self_dev_routes import router as self_dev_router
from nivos.tools.self_improve import SelfImproveTool


def test_protected_paths_invariant():
    """Verify that all security, sandbox, approval, and me.md paths are strictly protected."""
    forbidden = [
        "SAFETY_RULES.md",
        "safety_rules.md",
        "nested/SAFETY_RULES.md",
        "src/nivos/security/policy.py",
        "src/nivos/security/guards.py",
        "src/nivos/sandbox/docker_sandbox.py",
        "src/nivos/tools/sandboxed_shell.py",
        "src/nivos/tools/open_app.py",
        "src/nivos/server/claim_guard.py",
        "src/nivos/server/vault_routes.py",
        "src/nivos/tools/vault_tool.py",
        "src/nivos/memory/search.py",
        "me.md",
        "Nivos-Memory/me.md",
        "c:/vault/me.md",
        ".env",
        "local.env",
        "config/credentials.json",
        "secrets.toml",
    ]
    for path in forbidden:
        is_prot, reason = is_path_protected(path)
        assert is_prot is True, f"Expected {path} to be protected, but was not! (reason: {reason})"
        assert reason != ""

    # Legitimate non-protected paths should pass
    allowed = [
        "src/nivos/tools/calculator.py",
        "frontend/src/App.tsx",
        "docs/overview.md",
        "tests/unit/test_calc.py",
    ]
    for path in allowed:
        is_prot, reason = is_path_protected(path)
        assert is_prot is False, f"Expected {path} to be allowed, but was protected: {reason}"


def test_self_dev_policy_rejection():
    """Verify central policy blocks self-improvement for Slack, schedules, and untrusted input."""
    # Slack origin
    allowed, reason = is_tool_allowed("self_improve", origin=RequestOrigin.SLACK)
    assert allowed is False
    assert "not allowed for origin 'slack'" in reason

    # Scheduled task origin
    allowed, reason = is_tool_allowed("self_improve", origin=RequestOrigin.SCHEDULED_TASK)
    assert allowed is False
    assert "Dangerous tool 'self_improve' is not allowed for origin 'scheduled_task'" in reason

    # Untrusted content origin
    allowed, reason = is_tool_allowed(
        "self_improve",
        origin=RequestOrigin.LOCAL_CHAT,
        is_untrusted_content=True,
    )
    assert allowed is False
    assert "cannot be executed from untrusted content" in reason

    # Protected file in params
    allowed, reason = is_tool_allowed(
        "self_improve",
        origin=RequestOrigin.LOCAL_CHAT,
        params={"files_to_modify": ["src/nivos/security/policy.py"]},
    )
    assert allowed is False
    assert "Self-improvement blocked from touching protected file" in reason


def test_manager_sanitization_and_hooks(tmp_path: Path):
    """Verify manager sanitizes branch topic and creates pre-commit hook."""
    manager = SelfDevManager(
        repo_dir=tmp_path / "repo",
        worktree_dir=tmp_path / "worktree",
        vault_inbox=tmp_path / "inbox",
    )
    assert manager.sanitize_topic("Fix Fast Indexing!!") == "fix-fast-indexing"
    assert manager.sanitize_topic("---speed_up---") == "speed_up"
    assert manager.sanitize_topic("") == "improvement"

    # Test pre-commit hook installation
    (tmp_path / "worktree" / ".git").mkdir(parents=True)
    manager.install_pre_commit_hook()
    hook_file = tmp_path / "worktree" / ".git" / "hooks" / "pre-commit"
    assert hook_file.exists()
    content = hook_file.read_text(encoding="utf-8")
    assert "PROTECTED=" in content
    assert "me\\.md" in content


def test_propose_lesson(tmp_path: Path):
    """Verify lessons are written as new notes to Nivos-Inbox/ with proper frontmatter."""
    inbox = tmp_path / "inbox"
    manager = SelfDevManager(
        repo_dir=tmp_path / "repo",
        worktree_dir=tmp_path / "worktree",
        vault_inbox=inbox,
    )

    p1 = manager.propose_lesson("search-speed", "Fast BM25 Caching", "Use an in-memory index cache.")
    assert p1.exists()
    assert p1.name == "lesson-search-speed.md"
    body1 = p1.read_text(encoding="utf-8")
    assert "title: Fast BM25 Caching" in body1
    assert "origin: self_dev" in body1
    assert "status: pending_review" in body1

    # Second proposal on same topic gets collision-proof numbered suffix
    p2 = manager.propose_lesson("search-speed", "Fast BM25 Caching v2", "Another lesson.")
    assert p2.exists()
    assert p2.name == "lesson-search-speed-1.md"
    assert p1.read_text(encoding="utf-8") == body1  # Original unmodified


@pytest.mark.asyncio
async def test_self_improve_tool():
    """Verify SelfImproveTool execution lifecycle and error handling."""
    tool = SelfImproveTool()

    # Rejected on Slack origin
    res = await tool.execute(topic="test-topic", origin=RequestOrigin.SLACK)
    assert res.success is False
    assert "Policy Block" in res.content

    # Blocked when protected file specified
    res = await tool.execute(
        topic="test-topic",
        origin=RequestOrigin.LOCAL_CHAT,
        user_approved=True,
        files_to_modify=["SAFETY_RULES.md"],
    )
    assert res.success is False
    assert "Security Block" in res.content
    assert "SAFETY_RULES.md" in res.content

    # Propose lesson via tool
    with patch.object(tool.manager, "propose_lesson", return_value=Path("C:/inbox/lesson-test.md")):
        res = await tool.execute(
            topic="test-topic",
            action="propose_lesson",
            origin=RequestOrigin.LOCAL_CHAT,
            user_approved=True,
            lesson_content="Always cache tokens.",
        )
        assert res.success is True
        assert "Receipt: tool=self_improve" in res.content
        assert "Proposed lesson saved" in res.content


def test_self_dev_api_routes(tmp_path: Path):
    """Test self-dev API endpoints in FastAPI server."""
    app = FastAPI()
    app.include_router(self_dev_router)
    client = TestClient(app)

    # 1. GET /v1/self-dev/status
    res = client.get("/v1/self-dev/status")
    assert res.status_code == 200
    data = res.json()
    assert "active" in data
    assert "worktree_path" in data

    # 2. GET /v1/self-dev/reviews
    res = client.get("/v1/self-dev/reviews")
    assert res.status_code == 200
    data = res.json()
    assert "current" in data
    assert "history" in data

    # 3. POST /v1/self-dev/start with protected file violation
    res = client.post(
        "/v1/self-dev/start",
        json={"topic": "hacks", "files_to_modify": ["src/nivos/security/policy.py"]},
    )
    assert res.status_code == 400
    assert "Protected path violation" in res.json()["detail"]

    # 4. POST /v1/self-dev/start with Slack origin header
    res = client.post(
        "/v1/self-dev/start",
        json={"topic": "slack-hack"},
        headers={"x-nivos-origin": "slack"},
    )
    assert res.status_code == 403
    assert "Self-improvement blocked" in res.json()["detail"]

    # 5. POST /v1/self-dev/propose-lesson
    with patch("nivos.self_dev.manager.DEFAULT_VAULT_INBOX", tmp_path / "inbox"):
        res = client.post(
            "/v1/self-dev/propose-lesson",
            json={
                "topic": "caching",
                "title": "Cache Lesson",
                "content": "Keep hot models in RAM.",
            },
        )
        assert res.status_code == 200
        assert res.json()["success"] is True
        assert "lesson-" in res.json()["file"]
