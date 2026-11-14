"""Tests for model speed optimizations and tier routing logic."""

from __future__ import annotations

from unittest.mock import Mock, patch
import pytest

from nivos.core.config import NivosConfig
from nivos.server.tier_router import TierRouter, has_tool_intent, filter_relevant_tools


@pytest.fixture
def tier_router():
    cfg = NivosConfig()
    return TierRouter(cfg)


def test_tier_router_short_factual_routes_to_2b(tier_router):
    """Short factual questions route to the 2B fast tier with tools disabled."""
    model, reason, allows_tools = tier_router.route_query(
        "What is the capital city of France?",
        requested_model="auto",
    )
    assert model == "qwen3.5:2b"
    assert "Fast tier" in reason
    assert allows_tools is False


def test_tier_router_typo_routes_to_2b(tier_router):
    """Typo-filled short questions route to 2B fast tier."""
    model, reason, allows_tools = tier_router.route_query(
        "wht is the deepst ocena on erath?",
        requested_model="auto",
    )
    assert model == "qwen3.5:2b"
    assert "Fast tier" in reason
    assert allows_tools is False


def test_tier_router_one_line_routes_to_2b(tier_router):
    """One-line requests route to 2B fast tier."""
    model, reason, allows_tools = tier_router.route_query(
        "explain photosynthesis in 3 lines",
        requested_model="auto",
    )
    assert model == "qwen3.5:2b"
    assert "Fast tier" in reason
    assert allows_tools is False


def test_tier_router_summary_routes_to_4b(tier_router):
    """Summaries route to the 4B main tier."""
    model, reason, allows_tools = tier_router.route_query(
        "Summarise this paragraph in one sentence: Artificial intelligence enables computers to learn from data.",
        requested_model="auto",
    )
    assert model == "qwen3.5:4b"
    assert "summary" in reason.lower()
    assert allows_tools is False


def test_tier_router_email_routes_to_4b(tier_router):
    """Email generation routes to the 4B main tier."""
    model, reason, allows_tools = tier_router.route_query(
        "write a short email to John thanking him for yesterday's project meeting",
        requested_model="auto",
    )
    assert model == "qwen3.5:4b"
    assert "email" in reason.lower()
    assert allows_tools is False


def test_tier_router_follow_up_with_history_routes_to_4b(tier_router):
    """Multi-turn follow-up queries route to 4B main tier."""
    model, reason, allows_tools = tier_router.route_query(
        "What was the fruit I just mentioned?",
        requested_model="auto",
        has_history=True,
    )
    assert model == "qwen3.5:4b"
    assert "follow-up" in reason.lower()
    assert allows_tools is False


def test_tier_router_9b_manual_choice_only(tier_router):
    """9B deep model is only selected when explicitly requested."""
    # When auto is requested, 9B is never auto-selected
    model_auto, _, _ = tier_router.route_query(
        "Explain quantum entanglement in complete detail.",
        requested_model="auto",
    )
    assert model_auto != "qwen3.5:9b"

    # When 9B is explicitly requested, it is respected
    model_9b, reason, allows_tools = tier_router.route_query(
        "Explain quantum entanglement in complete detail.",
        requested_model="qwen3.5:9b",
    )
    assert model_9b == "qwen3.5:9b"
    assert "deep tier" in reason.lower() or "manual override" in reason.lower()
    assert allows_tools is False


def test_tier_router_tool_intent_routes_to_4b_with_tools_enabled(tier_router):
    """Queries with tool intent route to 4B with allows_tools=True."""
    model, reason, allows_tools = tier_router.route_query(
        "search my vault for project planning notes",
        requested_model="auto",
    )
    assert model == "qwen3.5:4b"
    assert allows_tools is True


def test_filter_relevant_tools_empty_for_chat():
    """No tool schemas are emitted when no tool intent is matched."""
    tools = filter_relevant_tools("What is the capital city of France?")
    assert tools == []

    tools_explain = filter_relevant_tools("explain photosynthesis in 3 lines")
    assert tools_explain == []


def test_ollama_engine_defaults_think_false_and_keep_alive():
    """Ollama engine sets think=False and keep_alive='30m' by default."""
    from nivos.engine.ollama import OllamaEngine
    from nivos.core.types import Message, Role

    engine = OllamaEngine(host="http://127.0.0.1:11434")

    # Verify generate() payload contains think=False and keep_alive='30m'
    with patch.object(engine._client, "post") as mock_post:
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "model": "qwen3.5:2b",
            "message": {"content": "Hello!"},
            "eval_count": 5,
            "prompt_eval_count": 10,
        }
        mock_post.return_value = mock_resp

        engine.generate([Message(role=Role.USER, content="hi")], model="qwen3.5:2b")
        assert mock_post.called
        call_kwargs = mock_post.call_args[1]
        json_payload = call_kwargs.get("json", {})
        assert json_payload.get("think") is False
        assert json_payload.get("keep_alive") == "30m"
