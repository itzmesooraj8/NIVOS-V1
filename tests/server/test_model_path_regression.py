"""Tests for model path regression and UnboundLocalError fixes in /v1/chat/completions."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock, patch
import pytest
from starlette.testclient import TestClient

from nivos.core.config import NivosConfig
from nivos.core.types import Message, Role
from nivos.server.app import create_app
from nivos.server.models import ChatCompletionRequest, ChatMessage


class FakeAsyncTokenIterator:
    """Async iterator for mocked streaming tokens."""

    def __init__(self, tokens: list[str]):
        self._tokens = list(tokens)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._tokens:
            raise StopAsyncIteration
        return self._tokens.pop(0)


class MockEngine:
    """Mock engine that records calls and yields tokens."""

    def __init__(self, token_response="Photosynthesis is the process by which green plants convert sunlight into chemical energy."):
        self.engine_id = "mock"
        self.token_response = token_response
        self.last_stream_messages = None
        self.last_stream_model = None

    def list_models(self):
        return ["qwen3.5:2b", "qwen3.5:4b", "qwen3.5:9b"]

    def stream(self, messages, model=None, temperature=0.7, max_tokens=1024):
        self.last_stream_messages = messages
        self.last_stream_model = model
        return FakeAsyncTokenIterator([self.token_response])

    def generate(self, messages, model=None, temperature=0.7, max_tokens=1024, **kwargs):
        return {"content": self.token_response, "model": model or "qwen3.5:4b"}


@pytest.fixture(autouse=True)
def mock_resident_memory_budget():
    with patch("nivos.server.tier_router.enforce_resident_memory_budget"):
        yield


@pytest.fixture
def test_client():
    mock_engine = MockEngine()
    config = NivosConfig()
    app = create_app(mock_engine, "qwen3.5:4b", config=config)
    app.state.engine = mock_engine
    return TestClient(app), mock_engine


def test_non_intent_message_to_local_model_streaming(test_client):
    """Test 1: Non-intent message like 'explain photosynthesis in 3 lines' streamed through /v1/chat/completions.

    MUST NOT fail with UnboundLocalError: cannot access local variable 'messages'.
    MUST return a real text answer from the model.
    """
    client, mock_engine = test_client
    payload = {
        "model": "qwen3.5:4b",
        "messages": [{"role": "user", "content": "explain photosynthesis in 3 lines"}],
        "stream": True,
    }
    resp = client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200

    content_chunks = []
    for line in resp.text.splitlines():
        if line.startswith("data: ") and not line.startswith("data: [DONE]"):
            data = json.loads(line[6:])
            for choice in data.get("choices", []):
                delta = choice.get("delta", {})
                content = delta.get("content")
                if content:
                    content_chunks.append(content)

    full_reply = "".join(content_chunks)
    assert "Error during generation" not in full_reply
    assert "cannot access local variable 'messages'" not in full_reply
    assert "Photosynthesis" in full_reply


def test_whats_up_what_can_you_do_for_me_streaming(test_client):
    """Test 2: 'whats up? what can you do for me?' streamed through /v1/chat/completions.

    MUST NOT fail with UnboundLocalError.
    """
    client, mock_engine = test_client
    payload = {
        "model": "qwen3.5:4b",
        "messages": [{"role": "user", "content": "whats up? what can you do for me?"}],
        "stream": True,
    }
    resp = client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200

    full_reply = ""
    for line in resp.text.splitlines():
        if line.startswith("data: ") and not line.startswith("data: [DONE]"):
            data = json.loads(line[6:])
            for choice in data.get("choices", []):
                content = choice.get("delta", {}).get("content")
                if content:
                    full_reply += content

    assert "Error during generation" not in full_reply
    assert "cannot access local variable 'messages'" not in full_reply
    assert len(full_reply.strip()) > 0


def test_non_intent_message_non_streaming(test_client):
    """Test 3: Non-streaming request to local model directly returns generated text."""
    client, mock_engine = test_client
    payload = {
        "model": "qwen3.5:4b",
        "messages": [{"role": "user", "content": "explain photosynthesis in 3 lines"}],
        "stream": False,
    }
    resp = client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "choices" in data
    assert "Photosynthesis" in data["choices"][0]["message"]["content"]


def test_tool_intents_branch(test_client):
    """Test 4: Tool intent (e.g. time/date) returns intent output without touching model or erroring."""
    client, mock_engine = test_client
    payload = {
        "model": "qwen3.5:4b",
        "messages": [{"role": "user", "content": "what is the time?"}],
        "stream": True,
    }
    resp = client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    full_reply = ""
    for line in resp.text.splitlines():
        if line.startswith("data: ") and not line.startswith("data: [DONE]"):
            data = json.loads(line[6:])
            for choice in data.get("choices", []):
                content = choice.get("delta", {}).get("content")
                if content:
                    full_reply += content
    assert "Error during generation" not in full_reply
    assert "The current time" in full_reply or "time" in full_reply.lower()


def test_heavy_job_branch(test_client):
    """Test 5: Heavy job / multi-step reasoning query passes messages to model cleanly."""
    client, mock_engine = test_client
    payload = {
        "model": "qwen3.5:4b",
        "messages": [{"role": "user", "content": "Analyze the geopolitical and economic consequences of semiconductor shortages in 2020-2022 and propose 3 mitigation policies"}],
        "stream": True,
    }
    resp = client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    full_reply = ""
    for line in resp.text.splitlines():
        if line.startswith("data: ") and not line.startswith("data: [DONE]"):
            data = json.loads(line[6:])
            for choice in data.get("choices", []):
                content = choice.get("delta", {}).get("content")
                if content:
                    full_reply += content
    assert "Error during generation" not in full_reply
    assert "cannot access local variable 'messages'" not in full_reply
    assert len(full_reply.strip()) > 0


def test_cloud_stream_branch_no_unbound_local():
    """Test 6: Cloud stream branch executes without UnboundLocalError."""
    from nivos.server.routes import _handle_stream

    req = ChatCompletionRequest(
        model="gemini-2.5-flash",
        messages=[ChatMessage(role="user", content="summarise the solar system")],
        stream=True,
    )
    engine = Mock()

    async def fake_cloud_stream(*args, **kwargs):
        yield "The solar system consists of the Sun and objects orbiting it."

    with patch("nivos.server.routes._uses_direct_cloud_router", return_value=True), \
         patch("nivos.server.cloud_router.stream_cloud_with_fallback", side_effect=fake_cloud_stream), \
         patch("nivos.server.cloud_router._load_keys", return_value={"GEMINI_API_KEY": "AIzaFakeKey"}):

        import asyncio

        async def run():
            resp = await _handle_stream(engine, "gemini-2.5-flash", req)
            tokens = []
            async for chunk in resp.body_iterator:
                if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
                    d = json.loads(chunk[6:])
                    c = d["choices"][0]["delta"].get("content")
                    if c:
                        tokens.append(c)
            return "".join(tokens)

        result = asyncio.run(run())
        assert "Error during generation" not in result
        assert "solar system" in result


def test_no_key_fallback_branch(test_client, monkeypatch):
    """Test 7: When requesting gemini without key, falls back to local model and answers."""
    client, mock_engine = test_client
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr("nivos.server.cloud_router._load_keys", lambda: {})

    payload = {
        "model": "gemini-2.5-flash",
        "messages": [{"role": "user", "content": "explain quantum computing in 2 lines"}],
        "stream": True,
    }
    resp = client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200

    full_reply = ""
    for line in resp.text.splitlines():
        if line.startswith("data: ") and not line.startswith("data: [DONE]"):
            data = json.loads(line[6:])
            for choice in data.get("choices", []):
                content = choice.get("delta", {}).get("content")
                if content:
                    full_reply += content

    assert "Error during generation" not in full_reply
    assert "cannot access local variable 'messages'" not in full_reply
    assert len(full_reply.strip()) > 0


def test_429_fallback_branch():
    """Test 8: When cloud returns 429, fallback to local model streams without UnboundLocalError."""
    import httpx
    from nivos.server.cloud_router import stream_cloud_with_fallback

    mock_resp = httpx.Response(status_code=429, request=httpx.Request("POST", "https://generativelanguage.googleapis.com"))
    http_err = httpx.HTTPStatusError("Rate limited", request=mock_resp.request, response=mock_resp)

    async def mock_stream_cloud(*args, **kwargs):
        raise http_err
        yield ""

    async def mock_stream_local(*args, **kwargs):
        yield "Local fallback token."

    with patch("nivos.server.cloud_router.stream_cloud", side_effect=mock_stream_cloud), \
         patch("nivos.server.cloud_router.stream_local", side_effect=mock_stream_local):

        import asyncio

        async def run():
            tokens = []
            async for tok in stream_cloud_with_fallback("gemini-2.5-flash", [Message(role=Role.USER, content="hello")]):
                tokens.append(tok)
            return "".join(tokens)

        text = asyncio.run(run())
        assert "Rate limit error" in text
        assert "Local fallback token" in text
