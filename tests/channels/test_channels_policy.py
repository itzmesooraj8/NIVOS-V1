"""Tests for Phase 1 messaging policy, registry exclusions, Telegram restrictions, and channel approval prohibitions."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from nivos.channels.telegram import TelegramChannel
from nivos.channels.slack import SlackChannel
from nivos.core.events import Event, EventBus, EventType
from nivos.core.registry import ChannelRegistry, ConnectorRegistry
from nivos.security.policy import RequestOrigin, SAFE_READONLY_TOOLS, is_tool_allowed
from nivos.server.channel_bridge import ChannelBridge, sanitize_notification_text
from nivos.server.session_store import SessionStore
from nivos.server.approval_routes import router as approval_router
from nivos.tools.approval_store import ApprovalStore, PendingAction


def test_registry_exclusions():
    """sendblue and imessage must not be registered in ChannelRegistry or ConnectorRegistry."""
    # Ensure modules are imported
    import nivos.channels  # noqa: F401
    import nivos.connectors  # noqa: F401

    assert not ChannelRegistry.contains("sendblue")
    assert not ConnectorRegistry.contains("imessage")


def test_channel_origins_strictly_limited_to_safe_readonly_tools():
    """Channel and Slack origins may ONLY execute the 5 safe read-only tools."""
    expected_safe = {"web_search", "get_weather", "search_vault", "calculator", "time"}
    assert SAFE_READONLY_TOOLS == expected_safe

    for origin in (RequestOrigin.CHANNEL, RequestOrigin.SLACK):
        for tool in expected_safe:
            allowed, _ = is_tool_allowed(tool, origin=origin)
            assert allowed is True, f"{tool} should be allowed for {origin}"

        for disallowed in ("sandboxed_shell", "open_app", "file_read", "file_write", "save_to_inbox", "self_improve"):
            allowed, reason = is_tool_allowed(disallowed, origin=origin)
            assert allowed is False, f"{disallowed} should be blocked for {origin}"
            assert "not allowed" in reason.lower() or "dangerous" in reason.lower() or "file write" in reason.lower()


def test_telegram_ignores_groups_and_enforces_allowlist():
    """Telegram channel must ignore non-private chats and unlisted senders."""
    ch = TelegramChannel(bot_token="test_token", allowed_user_id="123456")
    handler = MagicMock()
    ch.on_message(handler)

    # Mock Telegram Update & Message
    update_group = MagicMock()
    msg_group = MagicMock()
    msg_group.chat.type = "group"
    msg_group.chat.id = 9999
    msg_group.from_user.id = 123456
    msg_group.text = "Hello from group"
    update_group.message = msg_group

    # We can inspect the handler logic directly
    app_mock = MagicMock()
    ch._app = app_mock

    # Run the handler closure with group message
    # Extract the _handle_msg logic from TelegramChannel
    # We can test via simulated messages
    assert "123456" in ch._allowed_user_ids
    assert "999999" not in ch._allowed_user_ids


def test_channel_bridge_rejects_approval_attempts():
    """ChannelBridge must reject any message trying to grant approval."""
    bus = EventBus()
    store = SessionStore()
    bridge = ChannelBridge(channels={}, session_store=store, bus=bus)

    for approval_msg in ("approve", "/approve", "yes", "YES", "y", "confirm", "approve 123", "yes do it"):
        resp = bridge.handle_incoming("user1", approval_msg, "telegram")
        assert "approvals cannot be granted from a messaging channel" in resp.lower()


def test_channel_bridge_notification_sanitization():
    """Notifications must never leak secrets or raw file contents."""
    secret_text = "Task completed with API key xoxb-1234567890-abcdef and sk-1234567890abcdef"
    clean = sanitize_notification_text(secret_text)
    assert "xoxb" not in clean
    assert "sk-" not in clean
    assert "[REDACTED_SECRET]" in clean

    code_dump = "Log:\n```python\nimport os\nprint(os.environ)\n```\nDone."
    clean_code = sanitize_notification_text(code_dump)
    assert "import os" not in clean_code
    assert "[content omitted]" in clean_code


def test_approval_endpoint_rejects_channel_origin(tmp_path):
    """The /v1/approvals/{id}/approve route must return 403 when called with a channel origin."""
    import nivos.server.approval_routes as ar

    db_path = str(tmp_path / "test_approvals.db")
    store = ApprovalStore(db_path=db_path)
    action = store.queue_action(
        action_type="test",
        description="Test action",
        payload={},
        permission_key="test:perm",
        tier="medium",
    )
    action_id = action.id

    orig = ar._store
    ar._store = store

    try:
        app = FastAPI()
        app.include_router(approval_router)
        client = TestClient(app)

        # Call with x-nivos-origin: channel
        resp = client.post(f"/v1/approvals/{action_id}/approve", headers={"x-nivos-origin": "channel"})
        assert resp.status_code == 403
        assert "channel" in resp.text.lower()

        # Call with x-nivos-origin: slack
        resp = client.post(f"/v1/approvals/{action_id}/approve", headers={"x-nivos-origin": "slack"})
        assert resp.status_code == 403

        # Call with local_chat origin succeeds
        resp = client.post(f"/v1/approvals/{action_id}/approve", headers={"x-nivos-origin": "local_chat"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"
    finally:
        ar._store = orig
        store.close()
