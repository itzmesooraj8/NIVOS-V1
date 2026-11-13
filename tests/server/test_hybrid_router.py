"""Unit tests for Step 5: Hybrid router (cloud for heavy jobs).

Tests cover:
1. Intent classification: detects heavy jobs (big code gen, 3D/shaders, long reasoning, long documents).
2. Cloud key detection & advisory when no key is set (offers deep tier qwen3.5:9b with warning).
3. Gemini free tier priority and approval prompt ("This will send your message to <provider>. Continue?").
4. Approval responses ("Allow once", "Allow always for this provider", "Cancel", "Run locally").
5. Privacy invariant: Never send vault notes or me.md to the cloud.
6. Rate limit (429) error handling: plain warning and fallback to local model.
7. Activity logging: records engine and data_left_device in TraceStore.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from nivos.core.types import Message
from nivos.server.cloud_router import (
    clear_provider_approvals,
    get_available_cloud_provider,
    is_provider_always_allowed,
    sanitize_messages_for_cloud,
    set_provider_always_allowed,
    stream_cloud_with_fallback,
)
from nivos.server.intent_router import classify_intent, execute_intent
from nivos.traces.store import TraceStore


@pytest.fixture(autouse=True)
def _reset_approvals(tmp_path):
    approvals_file = tmp_path / "cloud_approvals.json"
    with patch("nivos.server.cloud_router._APPROVALS_FILE", approvals_file):
        clear_provider_approvals()
        yield approvals_file


def test_heavy_job_classification():
    """Verify heavy jobs are classified correctly and standard queries remain on local intents."""
    # 1. Big code generation
    match1 = classify_intent("build a 3D page with shaders")
    assert match1 is not None
    assert match1.intent == "heavy_job"

    match2 = classify_intent("create a complete ray tracer in C++")
    assert match2 is not None
    assert match2.intent == "heavy_job"

    # 2. Long reasoning
    match3 = classify_intent("provide a step-by-step mathematical proof that the square root of 2 is irrational")
    assert match3 is not None
    assert match3.intent == "heavy_job"

    # 3. Long documents
    match4 = classify_intent("write a comprehensive report on the future of generative AI in healthcare")
    assert match4 is not None
    assert match4.intent == "heavy_job"

    # 4. Standard queries must NOT be classified as heavy jobs (local remains default)
    assert classify_intent("hi").intent == "plain_chat"
    assert classify_intent("what is the time?").intent == "time/date"
    assert classify_intent("how much ram is free?").intent == "system_stats"
    assert classify_intent("open spotify").intent == "open_app"
    assert classify_intent("create a file in workspace/test.txt with hello").intent == "file_create"


def test_heavy_job_no_cloud_key():
    """When no cloud key is set in Settings, state so and offer to run locally on deep tier."""
    with patch("nivos.server.cloud_router._load_keys", return_value={}):
        match = classify_intent("build a 3D page with shaders")
        assert match is not None

        trace_store = TraceStore(":memory:")
        res = execute_intent(match, trace_store=trace_store)

        assert "No cloud API key is configured" in res.reply_text
        assert "deep tier (qwen3.5:9b)" in res.reply_text
        assert "slow on CPU" in res.reply_text
        assert res.metadata.get("data_left_device") is False

        # Verify trace records data did not leave device
        traces = trace_store.list_traces()
        assert len(traces) == 1
        assert traces[0].metadata.get("data_left_device") is False


def test_heavy_job_gemini_priority_and_approval_prompt():
    """When a cloud key is set (Gemini free tier first), show confirmation prompt."""
    fake_keys = {"GEMINI_API_KEY": "fake_gemini_key_for_testing"}
    with patch("nivos.server.cloud_router._load_keys", return_value=fake_keys):
        provider_info = get_available_cloud_provider()
        assert provider_info is not None
        prov_id, prov_name, default_model = provider_info
        assert prov_id == "google"
        assert prov_name == "Google Gemini"
        assert default_model == "gemini-2.5-flash"

        match = classify_intent("build a 3D page with shaders")
        trace_store = TraceStore(":memory:")
        res = execute_intent(match, trace_store=trace_store)

        assert "This will send your message to Google Gemini. Continue?" in res.reply_text
        assert "• Allow once" in res.reply_text
        assert "• Allow always for this provider" in res.reply_text
        assert "• Cancel" in res.reply_text
        assert res.metadata.get("needs_approval") is True
        assert res.metadata.get("data_left_device") is False


def test_heavy_job_approval_responses():
    """Verify handling of Allow once, Allow always, Cancel, and Run locally."""
    fake_keys = {"GEMINI_API_KEY": "fake_key"}
    with patch("nivos.server.cloud_router._load_keys", return_value=fake_keys):
        # 1. Allow once
        m_once = classify_intent("Allow once")
        assert m_once is not None and m_once.intent == "cloud_approval_once"
        r_once = execute_intent(m_once)
        assert "Confirmed. Sending your request to Google Gemini" in r_once.reply_text
        assert "(Allow once)" in r_once.reply_text
        assert r_once.metadata.get("data_left_device") is True

        # 2. Allow always
        m_always = classify_intent("Allow always for this provider")
        assert m_always is not None and m_always.intent == "cloud_approval_always"
        r_always = execute_intent(m_always)
        assert "has been set to 'Allow always'" in r_always.reply_text
        assert is_provider_always_allowed("google") is True

        # 3. Cancel
        m_cancel = classify_intent("Cancel")
        assert m_cancel is not None and m_cancel.intent == "cloud_approval_cancel"
        r_cancel = execute_intent(m_cancel)
        assert "Cancelled cloud request" in r_cancel.reply_text
        assert r_cancel.metadata.get("data_left_device") is False

        # 4. Run locally
        m_local = classify_intent("run locally on the deep tier")
        assert m_local is not None and m_local.intent == "run_locally"
        r_local = execute_intent(m_local)
        assert "Routing to local deep tier (qwen3.5:9b)" in r_local.reply_text
        assert r_local.metadata.get("data_left_device") is False


def test_heavy_job_always_allowed():
    """If provider is marked as always allowed, bypass prompt and route immediately."""
    fake_keys = {"GEMINI_API_KEY": "fake_key"}
    with patch("nivos.server.cloud_router._load_keys", return_value=fake_keys):
        set_provider_always_allowed("google")
        match = classify_intent("build a 3D page with shaders")
        res = execute_intent(match)
        assert "Routing heavy job to Google Gemini" in res.reply_text
        assert "Always-allow permission active" in res.reply_text
        assert res.metadata.get("data_left_device") is True


def test_never_send_vault_notes_or_me_md():
    """Messages sent to the cloud must NEVER include vault notes or me.md profile."""
    messages = [
        Message(
            role="system",
            content=(
                "You are Nivos.\n"
                "## User Profile (from me.md)\n"
                "- Name: Alice\n"
                "- Sensitive details from vault\n"
                "## Instructions\n"
                "Answer concisely."
            ),
        ),
        Message(role="user", content="Here is a note from my vault: [Vault Note: Secret Project]"),
        Message(role="user", content="build a 3D page with shaders"),
    ]

    sanitized = sanitize_messages_for_cloud(messages)
    combined = " ".join(m.content for m in sanitized)

    assert "me.md" not in combined.lower()
    assert "user profile" not in combined.lower()
    assert "sensitive details" not in combined.lower()
    assert "secret project" not in combined.lower()
    # Ensure the user's task remains
    assert "build a 3D page with shaders" in combined


@pytest.mark.asyncio
async def test_gemini_rate_limit_429_fallback_to_local():
    """Catch 429 rate limit error, inform user plainly, and fallback to local model."""
    req_mock = MagicMock()
    req_mock.status_code = 429
    error_resp = httpx.Response(status_code=429, request=req_mock)
    http_error = httpx.HTTPStatusError("Rate limit exceeded", request=req_mock, response=error_resp)

    # Mock stream_cloud to raise 429 and stream_local to yield local tokens
    async def _mock_stream_cloud(*args, **kwargs):
        raise http_error
        yield ""

    async def _mock_stream_local(*args, **kwargs):
        yield "const scene = new THREE.Scene();"

    with patch("nivos.server.cloud_router.stream_cloud", side_effect=_mock_stream_cloud):
        with patch("nivos.server.cloud_router.stream_local", side_effect=_mock_stream_local):
            tokens = []
            async for token in stream_cloud_with_fallback(
                "gemini-2.5-flash",
                [Message(role="user", content="build a 3D page with shaders")],
            ):
                tokens.append(token)

            full_text = "".join(tokens)
            # Verify plain user warning
            assert "Rate limit error (429: Too Many Requests) received from Google Gemini" in full_text
            assert "Falling back to local model (qwen3.5:9b)" in full_text
            # Verify local fallback content was yielded
            assert "const scene = new THREE.Scene();" in full_text


def test_activity_logging_records_engine_and_data_left_device():
    """Verify TraceStore accurately logs which engine answered and if data left device."""
    trace_store = TraceStore(":memory:")
    fake_keys = {"GEMINI_API_KEY": "fake_key"}

    with patch("nivos.server.cloud_router._load_keys", return_value=fake_keys):
        set_provider_always_allowed("google")
        match = classify_intent("build a 3D page with shaders")
        res = execute_intent(match, trace_store=trace_store)

        traces = trace_store.list_traces()
        assert len(traces) == 1
        trace = traces[0]
        assert trace.engine == "Google Gemini"
        assert trace.metadata.get("data_left_device") is True
        assert trace.steps[0].output.get("data_left_device") is True
        assert trace.steps[0].output.get("engine") == "Google Gemini"
