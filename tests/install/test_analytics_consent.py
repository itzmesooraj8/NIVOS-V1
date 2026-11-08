"""Terminal answers, unattended init, and persisted runtime analytics choices (disabled)."""

from __future__ import annotations

import tomllib
from nivos.cli import _analytics_consent


def test_ask_analytics_consent_always_false() -> None:
    assert _analytics_consent.ask_analytics_consent() is False


def test_apply_analytics_choice_always_disabled() -> None:
    content = '# Keep this comment\n[engine]\ndefault = "ollama"\n'
    result = _analytics_consent.apply_analytics_choice(content)
    data = tomllib.loads(result)
    assert data["analytics"]["enabled"] is False
    assert data["analytics"]["choice_recorded"] is True
    assert "# Keep this comment" in result


def test_decline_replaces_existing_enabled_value_without_losing_settings() -> None:
    content = "# Keep this comment\n[analytics]\nenabled = true\nflush_at_size = 7\n"
    result = _analytics_consent.apply_analytics_choice(content, False, prompt=False)
    data = tomllib.loads(result)
    assert data["analytics"] == {
        "enabled": False,
        "flush_at_size": 7,
        "choice_recorded": True,
    }
    assert "# Keep this comment" in result
