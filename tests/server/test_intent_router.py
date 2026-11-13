"""Tests for the deterministic intent router (Step 2)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from nivos.core.config import NivosConfig
from nivos.core.types import ToolResult
from nivos.server.app import create_app
from nivos.server.intent_router import (
    IntentMatch,
    classify_intent,
    execute_intent,
)
from nivos.traces.store import TraceStore


def test_classify_intent_all_ten_intents() -> None:
    # 1. plain_chat
    for q in ("hi", "hello", "hey", "good!", "thanks", "thank you", "who are you", "cool"):
        m = classify_intent(q)
        assert m is not None, f"Expected plain_chat for '{q}'"
        assert m.intent == "plain_chat"

    # 2. time/date
    for q in ("what time is it?", "current time", "what is today's date", "what day is it"):
        m = classify_intent(q)
        assert m is not None, f"Expected time/date for '{q}'"
        assert m.intent == "time/date"

    # 3. system_stats
    for q in ("how much free ram", "ram usage", "check system ram", "cpu usage", "disk space"):
        m = classify_intent(q)
        assert m is not None, f"Expected system_stats for '{q}'"
        assert m.intent == "system_stats"

    # 4. open_app (open spotify must be open_app, never shell)
    m = classify_intent("open spotify")
    assert m is not None
    assert m.intent == "open_app"
    assert m.params["app"].lower() == "spotify"

    m = classify_intent("launch notepad")
    assert m is not None
    assert m.intent == "open_app"
    assert m.params["app"].lower() == "notepad"

    # 5. file_create
    m = classify_intent("create a file in workspace/hello.txt with hello world")
    assert m is not None
    assert m.intent == "file_create"
    assert "hello.txt" in m.params["path"]
    assert "hello world" in m.params["content"]

    # 6. save_note
    m = classify_intent("save note: buy milk and bread")
    assert m is not None
    assert m.intent == "save_note"
    assert "buy milk" in m.params["content"]

    # 7. search_vault
    m = classify_intent("search vault for project roadmap")
    assert m is not None
    assert m.intent == "search_vault"
    assert "project roadmap" in m.params["query"]

    # 8. weather
    m = classify_intent("what's the weather in Tokyo?")
    assert m is not None
    assert m.intent == "weather"
    assert m.params["location"].lower() == "tokyo"

    # 9. install_app
    m = classify_intent("install vlc via winget")
    assert m is not None
    assert m.intent == "install_app"
    assert "vlc" in m.params["app"].lower()

    # 10. web_search
    m = classify_intent("web search for python 3.12 release notes")
    assert m is not None
    assert m.intent == "web_search"
    assert "python 3.12" in m.params["query"]

    # Fallback / un-matched query
    assert classify_intent("write an essay analyzing Shakespeare's Hamlet") is None
    assert classify_intent("explain quantum computing in detail") is None


def test_open_spotify_uses_open_app_tool() -> None:
    match = classify_intent("open spotify")
    assert match is not None

    with patch("nivos.tools.open_app.OpenAppTool.execute") as mock_open:
        mock_open.return_value = ToolResult(
            tool_name="open_app",
            content="Launched Spotify",
            success=True,
        )
        res = execute_intent(match)
        mock_open.assert_called_once_with(app="spotify", origin="local_chat")
        assert res.tool_success is True
        assert "opened spotify" in res.reply_text.lower()


def test_file_create_receipt_guard(tmp_path: Path) -> None:
    """A file request must NEVER claim 'created' unless tool receipt says success."""
    match = classify_intent("create a text file in workspace/sample.txt with test data")
    assert match is not None

    # Case A: Tool failure -> MUST NOT claim created
    with patch("nivos.tools.file_write.FileWriteTool.execute") as mock_write:
        mock_write.return_value = ToolResult(
            tool_name="file_write",
            content="Permission denied: outside workspace",
            success=False,
        )
        res_fail = execute_intent(match)
        assert res_fail.tool_success is False
        assert "Created file" not in res_fail.reply_text
        assert "Failed to create file" in res_fail.reply_text

    # Case B: Tool success -> Confirm created
    with patch("nivos.tools.file_write.FileWriteTool.execute") as mock_write:
        mock_write.return_value = ToolResult(
            tool_name="file_write",
            content="Successfully wrote to sample.txt",
            success=True,
        )
        res_ok = execute_intent(match)
        assert res_ok.tool_success is True
        assert "Created file sample.txt in workspace/" in res_ok.reply_text


def test_intent_logs_to_activity_trace_store(tmp_path: Path) -> None:
    store = TraceStore(db_path=tmp_path / "traces.db")
    cfg = NivosConfig()

    match = classify_intent("how much free ram")
    assert match is not None

    res = execute_intent(match, config=cfg, trace_store=store)
    assert "RAM" in res.reply_text

    traces = store.list_traces(limit=10)
    assert len(traces) == 1
    assert traces[0].agent == "intent-router"
    assert traces[0].metadata.get("intent") == "system_stats"
    assert "RAM" in traces[0].result


def test_chat_completions_intent_router_streaming_and_non_streaming(tmp_path: Path) -> None:
    cfg = NivosConfig()
    cfg.traces.enabled = False
    app = create_app(MagicMock(health=lambda: True, list_models=lambda: []), "lazy", config=cfg)

    client = TestClient(app)

    # 1. Non-streaming plain chat
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "qwen3.5:4b", "messages": [{"role": "user", "content": "hi"}], "stream": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "Hello" in data["choices"][0]["message"]["content"]
    assert "Intent router" in data["routing_reason"]

    # 2. Streaming system stats
    resp_stream = client.post(
        "/v1/chat/completions",
        json={"model": "qwen3.5:4b", "messages": [{"role": "user", "content": "how much free ram"}], "stream": True},
    )
    assert resp_stream.status_code == 200
    text = resp_stream.text
    assert "data: " in text
    assert "RAM" in text
    assert "[DONE]" in text

