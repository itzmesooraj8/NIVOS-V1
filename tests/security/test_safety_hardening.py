"""Comprehensive tests for safety hardening:
1. Canonical path validation and traversal/symlink escape prevention across read, write, delete, patch.
2. Fail-closed behaviour on empty allowed_dirs.
3. Checkpoint failure enforcement blocking writes.
4. Tag-based sensitivity filtering for cloud router and exclusion of tagged content from 'Allow always'.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
import pytest

from nivos.core.types import Message
from nivos.security.policy import (
    is_path_safe_for_read,
    is_path_safe_for_write,
    is_path_safe_for_delete,
    is_path_safe_for_patch,
    RequestOrigin,
    is_tool_allowed,
)
from nivos.tools.file_read import FileReadTool
from nivos.tools.file_write import FileWriteTool
from nivos.tools.apply_patch import ApplyPatchTool
from nivos.server.cloud_router import (
    sanitize_messages_for_cloud,
    is_cloud_dispatch_auto_approved,
    wrap_sensitive_content,
    contains_sensitive_content,
)


# ---------------------------------------------------------------------------
# Test 1: Canonical path validation & traversal/symlink rejection
# ---------------------------------------------------------------------------


def test_rejects_substring_nivos_inbox_traversal_and_spoofing(tmp_path: Path):
    """Paths containing 'nivos-inbox' as a substring outside canonical vault inbox must be rejected."""
    traversal_paths = [
        "C:/evil/nivos-inbox/../../Windows/System32/evil.dll",
        "/tmp/fake-nivos-inbox/payload.sh",
        str(tmp_path / "nivos-inbox" / ".." / "escaped.txt"),
        "workspace/../nivos-inbox/../../secret.txt",
        "relative/path/with-nivos-inbox-in-name.txt",
    ]

    for p in traversal_paths:
        for check_fn in (
            is_path_safe_for_read,
            is_path_safe_for_write,
            is_path_safe_for_delete,
            is_path_safe_for_patch,
        ):
            safe, reason = check_fn(p)
            assert not safe, f"{check_fn.__name__} should have rejected '{p}', but returned safe ({reason})"


def test_rejects_symlink_escaping_authorized_directory(tmp_path: Path):
    """A symlink inside an authorized directory pointing outside must be rejected."""
    auth_dir = tmp_path / "authorized"
    auth_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    secret_file = outside_dir / "secret.txt"
    secret_file.write_text("top secret", encoding="utf-8")

    link = auth_dir / "symlink_to_outside"
    try:
        link.symlink_to(outside_dir, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation not supported or permitted on this platform")

    escaped_target = str(link / "secret.txt")
    safe, reason = is_path_safe_for_write(escaped_target, allowed_dirs=[str(auth_dir)])
    assert not safe, f"Escaping symlink should be rejected: {reason}"

    safe_read, r_reason = is_path_safe_for_read(escaped_target, allowed_dirs=[str(auth_dir)])
    assert not safe_read, f"Escaping symlink should be rejected for read: {r_reason}"


def test_permits_canonical_workspace_and_vault_inbox(tmp_path: Path, monkeypatch):
    """Canonical files strictly inside workspace/ or vault/Nivos-Inbox/ are permitted."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    vault = tmp_path / "vault"
    inbox = vault / "Nivos-Inbox"
    inbox.mkdir(parents=True)

    monkeypatch.setenv("NIVOS_WORKSPACE", str(ws))
    monkeypatch.setattr("nivos.tools.vault_tools._get_vault_path", lambda: vault)
    monkeypatch.setattr("nivos.security.policy._get_canonical_vault_inbox", lambda: inbox)

    valid_ws_file = str(ws / "valid.txt")
    valid_inbox_file = str(inbox / "note.md")

    assert is_path_safe_for_write(valid_ws_file)[0] is True
    assert is_path_safe_for_write(valid_inbox_file)[0] is True
    assert is_path_safe_for_read(valid_ws_file)[0] is True
    assert is_path_safe_for_read(valid_inbox_file)[0] is True


# ---------------------------------------------------------------------------
# Test 2: Fail closed on empty allowed_dirs
# ---------------------------------------------------------------------------


def test_empty_allowed_dirs_fails_closed(tmp_path: Path):
    """Empty allowed_dirs list must deny all file access (fail closed)."""
    target = tmp_path / "any_file.txt"
    target.write_text("content", encoding="utf-8")

    read_tool = FileReadTool(allowed_dirs=[])
    r_res = read_tool.execute(path=str(target))
    assert r_res.success is False
    assert "denied" in r_res.content.lower() or "not allowed" in r_res.content.lower()

    write_tool = FileWriteTool(allowed_dirs=[])
    w_res = write_tool.execute(path=str(target), content="overwrite")
    assert w_res.success is False
    assert "denied" in w_res.content.lower() or "not allowed" in w_res.content.lower()
    assert target.read_text(encoding="utf-8") == "content"


# ---------------------------------------------------------------------------
# Test 3: Checkpoint failure blocks file writes
# ---------------------------------------------------------------------------


def test_checkpoint_failure_blocks_file_write(tmp_path: Path):
    """FileWriteTool must abort the write and return a clear error if create_checkpoint fails."""
    target = tmp_path / "target.txt"
    target.write_text("initial content", encoding="utf-8")
    tool = FileWriteTool(allowed_dirs=[str(tmp_path)])

    with patch(
        "nivos.storage.checkpoint_manager.create_checkpoint",
        side_effect=RuntimeError("Disk snapshot quota exceeded"),
    ):
        res = tool.execute(path=str(target), content="modified content")
        assert res.success is False
        assert "checkpoint" in res.content.lower()
        # Ensure file was NOT modified
        assert target.read_text(encoding="utf-8") == "initial content"


def test_checkpoint_failure_blocks_apply_patch(tmp_path: Path):
    """ApplyPatchTool must abort patching if create_checkpoint fails."""
    target = tmp_path / "code.py"
    target.write_text("line1\nline2\n", encoding="utf-8")
    patch_text = f"--- a/{target.name}\n+++ b/{target.name}\n@@ -1,2 +1,2 @@\n line1\n-line2\n+line2_modified\n"
    tool = ApplyPatchTool()

    with patch(
        "nivos.storage.checkpoint_manager.create_checkpoint",
        side_effect=RuntimeError("Checkpoint service unavailable"),
    ):
        res = tool.execute(path=str(target), patch=patch_text)
        assert res.success is False
        assert "checkpoint" in res.content.lower()
        assert target.read_text(encoding="utf-8") == "line1\nline2\n"


# ---------------------------------------------------------------------------
# Test 4: Sensitivity tags & exclusion of tagged content from 'Allow always'
# ---------------------------------------------------------------------------


def test_sensitivity_tag_sanitization():
    """Sanitization must cleanly strip tagged blocks without false positives on legitimate user text."""
    # Tagged sensitive content from me.md or vault note
    tagged_block = wrap_sensitive_content("User blood type: O+, highly confidential allergy.", source="me.md")
    system_prompt = f"You are Nivos.\n{tagged_block}\nPlease help the user."

    messages = [
        Message(role="system", content=system_prompt),
        Message(role="user", content="Can you review my obsidian vault notes and help me edit me.md?"),
    ]

    sanitized = sanitize_messages_for_cloud(messages)

    # Tagged sensitive content must be stripped
    assert "blood type: O+" not in sanitized[0].content
    assert "highly confidential" not in sanitized[0].content

    # Legitimate user prompt containing keywords 'obsidian' and 'me.md' must be preserved
    assert "Can you review my obsidian vault notes and help me edit me.md?" in sanitized[1].content


def test_always_allowed_provider_requires_approval_if_sensitive_content_present(tmp_path: Path, monkeypatch):
    """'Allow always' provider permission must NOT auto-approve if tagged sensitive content is present."""
    approvals_file = tmp_path / "cloud_approvals.json"
    import json
    approvals_file.write_text(json.dumps({"always_allowed": ["google"]}), encoding="utf-8")
    monkeypatch.setattr("nivos.server.cloud_router._APPROVALS_FILE", approvals_file)

    clean_msgs = [Message(role="user", content="Calculate 42 * 9")]
    # Without sensitive content, google is auto-approved
    assert is_cloud_dispatch_auto_approved("google", clean_msgs) is True

    # With tagged sensitive content, auto-approval MUST be denied
    sensitive_msgs = [
        Message(
            role="system",
            content=wrap_sensitive_content("Secret internal project roadmap", source="vault"),
        ),
        Message(role="user", content="Summarize this project."),
    ]
    assert is_cloud_dispatch_auto_approved("google", sensitive_msgs) is False
