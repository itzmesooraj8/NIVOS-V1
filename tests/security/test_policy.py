"""Unit tests for the central origin and untrusted-content security policy."""

import pytest
from pathlib import Path

from nivos.security.policy import (
    DANGEROUS_TOOLS,
    PROTECTED_WRITE_PATTERNS,
    RequestOrigin,
    SAFE_READONLY_TOOLS,
    is_path_safe_for_write,
    is_tool_allowed,
)


def test_local_chat_safe_tools_allowed():
    """Verify that safe read-only tools are allowed for local chat."""
    for tool in SAFE_READONLY_TOOLS:
        allowed, reason = is_tool_allowed(tool, origin=RequestOrigin.LOCAL_CHAT)
        assert allowed is True


def test_local_chat_dangerous_tools_allowed_with_approval():
    """Verify that dangerous tools are allowed for local chat with approval requirement."""
    for tool in DANGEROUS_TOOLS:
        allowed, reason = is_tool_allowed(tool, origin=RequestOrigin.LOCAL_CHAT)
        assert allowed is True
        assert "approval" in reason.lower()


def test_slack_origin_blocks_dangerous_tools():
    """Verify that requests originating from Slack cannot run any dangerous tools."""
    for tool in DANGEROUS_TOOLS:
        allowed, reason = is_tool_allowed(tool, origin=RequestOrigin.SLACK)
        assert allowed is False
        assert "not allowed for origin 'slack'" in reason


def test_slack_origin_blocks_file_writes():
    """Verify that Slack requests cannot write files."""
    allowed, reason = is_tool_allowed(
        "file_write",
        origin=RequestOrigin.SLACK,
        params={"path": "workspace/notes.txt", "content": "hi"},
    )
    assert allowed is False
    assert "not allowed for origin 'slack'" in reason


def test_slack_origin_allows_readonly_tools():
    """Verify that Slack requests may execute read-only tools."""
    for tool in ["web_search", "get_weather", "search_vault", "calculator", "time"]:
        allowed, reason = is_tool_allowed(tool, origin=RequestOrigin.SLACK)
        assert allowed is True


def test_scheduled_task_blocks_dangerous_tools_and_writes():
    """Verify that scheduled tasks cannot run dangerous tools or write files."""
    for tool in list(DANGEROUS_TOOLS) + ["file_write"]:
        allowed, reason = is_tool_allowed(tool, origin=RequestOrigin.SCHEDULED_TASK)
        assert allowed is False


def test_untrusted_content_blocks_dangerous_tools():
    """Verify that prompts derived from untrusted content cannot execute dangerous tools."""
    for tool in DANGEROUS_TOOLS:
        allowed, reason = is_tool_allowed(
            tool,
            origin=RequestOrigin.LOCAL_CHAT,
            is_untrusted_content=True,
        )
        assert allowed is False
        assert "untrusted content" in reason.lower()


def test_untrusted_content_blocks_file_write():
    """Verify that prompts derived from untrusted content cannot write files."""
    allowed, reason = is_tool_allowed(
        "file_write",
        origin=RequestOrigin.LOCAL_CHAT,
        is_untrusted_content=True,
        params={"path": "workspace/hello.txt", "content": "test"},
    )
    assert allowed is False
    assert "untrusted content" in reason.lower()


def test_protects_me_md_always():
    """Verify that me.md is protected and cannot be written under any circumstances."""
    for path in [
        "me.md",
        "C:/Nivos/NIVOS-vault/Nivos-Memory/me.md",
        "Nivos-Memory/me.md",
        "workspace/me.md",
    ]:
        safe, reason = is_path_safe_for_write(path)
        assert safe is False
        assert "me.md" in reason.lower()

        allowed, r = is_tool_allowed(
            "file_write",
            origin=RequestOrigin.LOCAL_CHAT,
            params={"path": path, "content": "fake"},
        )
        assert allowed is False


def test_blocks_sensitive_files():
    """Verify that writing to .env or keys is blocked."""
    for path in [".env", ".env.local", "id_rsa", "credentials.json"]:
        safe, _ = is_path_safe_for_write(path)
        assert safe is False


def test_allowed_write_locations():
    """Verify that writes inside workspace/ and Nivos-Inbox/ are permitted."""
    assert is_path_safe_for_write("workspace/sub/output.txt")[0] is True
    assert is_path_safe_for_write("C:/Nivos/NIVOS-vault/Nivos-Inbox/note.md")[0] is True
