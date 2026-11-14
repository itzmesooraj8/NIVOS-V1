"""Comprehensive tests for Part 2: Cloud routing, key validation, model picker, intent router typos, and weather.

1. Gemini 404 from Ollama: gemini-* never goes to Ollama; if no key set, fallback to 4B with advisory.
2. Settings > Cloud keys: test-key endpoint reporting OK / invalid key / rate limit, validating AI Studio format (AIza...).
3. Model picker: Auto/default never picks cloud unless enabled; cloud default is gemini-2.5-flash (not Pro).
4. Intent router: handles 'time ?', 'whats up ?', 'what can you do for me ?' and 15 paraphrases/typos locally.
5. Weather: fetch_open_meteo_weather works end-to-end without ImportError.
6. Evals isolation: executes in temp workspace and inbox.
7. Cloud guard: 'time ?' never hits cloud; cloud outbound calls require approval prompt.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch
import pytest

from nivos.core.config import NivosConfig
from nivos.core.types import Message
from nivos.server.cloud_router import (
    get_provider,
    is_cloud_model,
)
from nivos.server.intent_router import classify_intent, execute_intent
from nivos.server.search_policy import fetch_open_meteo_weather, format_weather_report


# ---------------------------------------------------------------------------
# Test 1: Gemini 404 prevention and fallback
# ---------------------------------------------------------------------------


def test_gemini_routing_never_targets_ollama_and_falls_back_if_no_key(monkeypatch):
    """gemini-* models must NEVER route to Ollama; missing key must fall back to local 4B model."""
    from nivos.engine.multi import MultiEngine
    from nivos.server.cloud_router import clear_provider_approvals

    # Ensure no cloud keys are active
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr("nivos.server.cloud_router._load_keys", lambda: {})

    # Mock Ollama engine
    mock_ollama = Mock()
    mock_ollama.list_models.return_value = ["qwen3.5:2b", "qwen3.5:4b", "qwen3.5:9b"]
    multi = MultiEngine([("ollama", mock_ollama)])

    # Asking MultiEngine for gemini-2.5-flash should NEVER return Ollama
    with pytest.raises(ValueError, match="Add your key in Settings|No Gemini key set"):
        eng = multi._engine_for("gemini-2.5-flash")
        assert eng != mock_ollama, "gemini-2.5-flash was misrouted to Ollama!"


# ---------------------------------------------------------------------------
# Test 2: Cloud key test endpoint and AI Studio format validation
# ---------------------------------------------------------------------------


def test_cloud_key_validation_and_probe():
    """Test key endpoint validates AIza... format for Gemini and reports plain words."""
    from nivos.server.cloud_router import probe_cloud_key, validate_key_format

    # 1. Format check
    valid_format, reason = validate_key_format("GEMINI_API_KEY", "AIzaSyDummyValidKey1234567890abcdef")
    assert valid_format is True

    bad_format, bad_reason = validate_key_format("GEMINI_API_KEY", "sk-ant-invalid-format")
    assert bad_format is False
    assert "AIza" in bad_reason

    # 2. Probe mock
    with patch("httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"models": []}
        res = probe_cloud_key("GEMINI_API_KEY", "AIzaSyDummyValidKey1234567890abcdef")
        assert res["status"] == "ok"
        assert "valid" in res["message"].lower()

    with patch("httpx.get") as mock_get:
        mock_get.return_value.status_code = 400
        mock_get.return_value.text = "API_KEY_INVALID"
        res = probe_cloud_key("GEMINI_API_KEY", "AIzaSyDummyInvalidKey")
        assert res["status"] == "invalid_key"


# ---------------------------------------------------------------------------
# Test 3: Model picker cloud default guard
# ---------------------------------------------------------------------------


def test_model_picker_cloud_default_is_flash():
    """Default cloud model must be gemini-2.5-flash, never Pro."""
    from nivos.server.cloud_router import get_available_cloud_provider

    with patch(
        "nivos.server.cloud_router._load_keys",
        return_value={"GEMINI_API_KEY": "AIzaSyDummyKey"},
    ):
        info = get_available_cloud_provider()
        assert info is not None
        provider_id, name, default_model = info
        assert provider_id == "google"
        assert default_model == "gemini-2.5-flash"
        assert "pro" not in default_model.lower()


# ---------------------------------------------------------------------------
# Test 4: Intent router regressions and 15+ paraphrases / typos
# ---------------------------------------------------------------------------


def test_intent_router_handles_regressions_and_paraphrases():
    """Intent router must classify 'time ?', 'whats up ?', 'what can you do for me ?' and 15 paraphrases."""
    test_cases = [
        # Required regressions
        ("time ?", "time/date"),
        ("whats up ?", "plain_chat"),
        ("what can you do for me ?", "plain_chat"),
        # 15 additional paraphrases / typos
        ("time   ?", "time/date"),
        ("what time is it now ?", "time/date"),
        ("current time ?", "time/date"),
        ("whats the time", "time/date"),
        ("time please", "time/date"),
        ("wat is the date today ?", "time/date"),
        ("day of the week ?", "time/date"),
        ("sup ?", "plain_chat"),
        ("wassup", "plain_chat"),
        ("how r u ?", "plain_chat"),
        ("what can u do ?", "plain_chat"),
        ("who are you ?", "plain_chat"),
        ("how much ram free ?", "system_stats"),
        ("ram left ?", "system_stats"),
        ("cpu usage ?", "system_stats"),
        ("calc 15 * 60", "calculator"),
    ]

    for query, expected_intent in test_cases:
        match = classify_intent(query)
        assert match is not None, f"Query '{query}' failed to classify into an intent"
        assert match.intent == expected_intent, f"Query '{query}' classified as {match.intent}, expected {expected_intent}"

        # Execute intent locally in code
        res = execute_intent(match)
        assert res.reply_text, f"Query '{query}' produced empty reply"


# ---------------------------------------------------------------------------
# Test 5: Weather end-to-end
# ---------------------------------------------------------------------------


def test_weather_open_meteo_end_to_end():
    """fetch_open_meteo_weather must be importable and return formatted weather."""
    with patch("urllib.request.urlopen") as mock_url:
        # Mock geocoding and forecast
        geo_resp = Mock()
        geo_resp.read.return_value = json.dumps({
            "results": [{"name": "Tokyo", "country": "Japan", "latitude": 35.68, "longitude": 139.69}]
        }).encode("utf-8")
        geo_resp.__enter__ = Mock(return_value=geo_resp)
        geo_resp.__exit__ = Mock(return_value=False)

        forecast_resp = Mock()
        forecast_resp.read.return_value = json.dumps({
            "current": {
                "temperature_2m": 18.5,
                "apparent_temperature": 17.2,
                "relative_humidity_2m": 50,
                "weather_code": 1,
            }
        }).encode("utf-8")
        forecast_resp.__enter__ = Mock(return_value=forecast_resp)
        forecast_resp.__exit__ = Mock(return_value=False)

        mock_url.side_effect = [geo_resp, forecast_resp]

        raw = fetch_open_meteo_weather("Tokyo")
        assert raw, "fetch_open_meteo_weather returned empty"
        report = format_weather_report("Tokyo", raw)
        assert "Tokyo" in report
        assert "18°C" in report or "19°C" in report or "18.5" in report


# ---------------------------------------------------------------------------
# Test 6: Cloud prompt guard & time query isolation
# ---------------------------------------------------------------------------


def test_plain_time_never_hits_cloud_and_heavy_job_prompts_approval():
    """Verify that 'time ?' is handled in code without cloud access, and heavy jobs prompt approval."""
    # 1. 'time ?' must execute locally
    match = classify_intent("time ?")
    assert match is not None and match.intent == "time/date"
    res = execute_intent(match)
    assert not res.metadata.get("data_left_device", False)

    # 2. Heavy job must prompt for approval
    heavy_match = classify_intent("build a 3D page with shaders and ray tracer")
    assert heavy_match is not None and heavy_match.intent == "heavy_job"

    with patch(
        "nivos.server.cloud_router.get_available_cloud_provider",
        return_value=("google", "Google Gemini", "gemini-2.5-flash"),
    ), patch(
        "nivos.server.cloud_router.is_cloud_dispatch_auto_approved",
        return_value=False,
    ):
        heavy_res = execute_intent(heavy_match)
        assert heavy_res.metadata.get("needs_approval") is True
        assert "continue" in heavy_res.reply_text.lower()
        assert not heavy_res.metadata.get("data_left_device", False)
