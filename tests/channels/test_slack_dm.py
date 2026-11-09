"""Tests for Slack Direct Messages channel, user allowlist, and policy restrictions (Phase 5)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from nivos.channels.slack import SlackChannel
from nivos.security.policy import RequestOrigin, is_tool_allowed
from nivos.server.app import create_app
from nivos.server.slack_routes import MINIMAL_DM_MANIFEST


def test_minimal_dm_manifest():
    """Verify that manifest is strictly minimal DM-only: no channels, groups, or mentions."""
    scopes = MINIMAL_DM_MANIFEST["oauth_config"]["scopes"]["bot"]
    assert set(scopes) == {"chat:write", "im:history", "im:read", "im:write"}
    assert "channels:read" not in scopes
    assert "groups:read" not in scopes
    assert "app_mentions:read" not in scopes

    bot_events = MINIMAL_DM_MANIFEST["settings"]["event_subscriptions"]["bot_events"]
    assert bot_events == ["message.im"]


def test_slack_policy_readonly_tools_only():
    """Slack-origin requests can only use read-only tools; dangerous tools are blocked."""
    # Permitted read-only tools
    for safe_tool in ["web_search", "get_weather", "search_vault", "calculator", "time"]:
        allowed, reason = is_tool_allowed(
            safe_tool,
            origin=RequestOrigin.SLACK,
            is_untrusted_content=True,
        )
        assert allowed is True, f"Expected {safe_tool} to be allowed for Slack"

    # Blocked dangerous / write tools
    for dangerous in ["sandboxed_shell", "shell_exec", "open_app", "file_write", "save_to_inbox"]:
        allowed, reason = is_tool_allowed(
            dangerous,
            origin=RequestOrigin.SLACK,
            is_untrusted_content=True,
        )
        assert allowed is False, f"Expected {dangerous} to be blocked for Slack"
        assert "not allowed" in reason or "not permitted" in reason or "untrusted" in reason


def test_slack_injection_in_dm_blocked():
    """Injection attempt in Slack DM to execute shell or open apps must be blocked."""
    # Simulating turn coming from Slack DM containing prompt injection
    allowed_shell, reason_shell = is_tool_allowed(
        "sandboxed_shell",
        origin=RequestOrigin.SLACK,
        is_untrusted_content=True,
    )
    assert not allowed_shell
    assert "not allowed for origin 'slack'" in reason_shell or "untrusted content" in reason_shell or "not permitted" in reason_shell

    allowed_open, reason_open = is_tool_allowed(
        "open_app",
        origin=RequestOrigin.SLACK,
        is_untrusted_content=True,
    )
    assert not allowed_open


def test_slack_user_allowlist_ignores_unauthorized_user(monkeypatch):
    """Slack channel ignores messages from users not in the allowlist."""
    mock_slack_sdk = MagicMock()
    mock_socket_mode = MagicMock()
    mock_req_mod = MagicMock()
    mock_resp_mod = MagicMock()
    mock_web = MagicMock()

    monkeypatch.setitem(sys.modules, "slack_sdk", mock_slack_sdk)
    monkeypatch.setitem(sys.modules, "slack_sdk.socket_mode", mock_socket_mode)
    monkeypatch.setitem(sys.modules, "slack_sdk.socket_mode.request", mock_req_mod)
    monkeypatch.setitem(sys.modules, "slack_sdk.socket_mode.response", mock_resp_mod)
    monkeypatch.setitem(sys.modules, "slack_sdk.web", mock_web)

    registered_listeners = []
    mock_client = MagicMock()
    mock_client.socket_mode_request_listeners = registered_listeners
    mock_socket_mode.SocketModeClient.return_value = mock_client

    bus = MagicMock()
    channel = SlackChannel(
        bot_token="xoxb-test",
        app_token="xapp-test",
        allowed_user_id="U_ALLOWED_OWNER",
        bus=bus,
    )

    received_messages = []
    channel.on_message(lambda m: received_messages.append(m))

    # Pre-set stop event so _socket_mode_loop exits immediately after registering listener
    channel._stop_event.set()
    channel._socket_mode_loop()

    # The listener callback registered with socket_mode_request_listeners
    assert len(registered_listeners) == 1
    listener = registered_listeners[0]

    # 1. Message from unauthorized user U_ATTACKER
    req_unauth = MagicMock()
    req_unauth.type = "events_api"
    req_unauth.envelope_id = "env-1"
    req_unauth.payload = {
        "event": {
            "type": "message",
            "user": "U_ATTACKER",
            "text": "run sandboxed_shell rm -rf /",
            "ts": "123.456",
            "channel": "D12345",
            "channel_type": "im",
        }
    }
    listener(mock_client, req_unauth)
    # Should NOT be delivered to handlers
    assert len(received_messages) == 0

    # 2. Message from allowlisted user U_ALLOWED_OWNER
    req_auth = MagicMock()
    req_auth.type = "events_api"
    req_auth.envelope_id = "env-2"
    req_auth.payload = {
        "event": {
            "type": "message",
            "user": "U_ALLOWED_OWNER",
            "text": "What is the weather in Palakkad?",
            "ts": "123.457",
            "channel": "D12345",
            "channel_type": "im",
        }
    }
    listener(mock_client, req_auth)
    assert len(received_messages) == 1
    msg = received_messages[0]
    assert msg.sender == "U_ALLOWED_OWNER"
    assert msg.metadata.get("origin") == "slack"
    assert msg.metadata.get("is_untrusted_content") is True


def test_slack_routes_config(monkeypatch):
    """Test /v1/channels/slack/config GET and POST."""
    app = create_app(engine=MagicMock(), model="test-model")
    client = TestClient(app)

    # 1. GET /v1/channels/slack/config
    resp = client.get("/v1/channels/slack/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "has_bot_token" in data
    assert "allowed_user_id" in data
    assert "manifest" in data

    # 2. POST /v1/channels/slack/config
    persisted = {}

    def fake_persist(sec, vals):
        persisted[sec] = vals

    monkeypatch.setattr("nivos.server.slack_routes._persist_config_section", fake_persist)

    resp_post = client.post(
        "/v1/channels/slack/config",
        json={
            "bot_token": "xoxb-mock",
            "app_token": "xapp-mock",
            "allowed_user_id": "U123456",
        },
    )
    assert resp_post.status_code == 200
    assert resp_post.json()["has_bot_token"] is True
    assert resp_post.json()["allowed_user_id"] == "U123456"
    assert persisted["channel.slack"]["allowed_user_id"] == "U123456"
