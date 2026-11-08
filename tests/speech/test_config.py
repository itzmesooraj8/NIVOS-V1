"""Tests for speech configuration."""

from nivos.core.config import NivosConfig, SpeechConfig


def test_speech_config_defaults():
    cfg = SpeechConfig()
    assert cfg.backend == "auto"
    assert cfg.model == "base"
    assert cfg.language == ""
    assert cfg.device == "auto"
    assert cfg.compute_type == "float16"


def test_nivos_config_has_speech():
    cfg = NivosConfig()
    assert hasattr(cfg, "speech")
    assert isinstance(cfg.speech, SpeechConfig)
    assert cfg.speech.backend == "auto"


def test_nivos_system_has_speech_backend():
    """NivosSystem has a speech_backend attribute."""
    from nivos.system import NivosSystem

    assert "speech_backend" in NivosSystem.__dataclass_fields__
