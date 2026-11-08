"""Smoke test that the tmp_nivos_home fixture works."""

from __future__ import annotations

from pathlib import Path

from nivos.core import config as config_mod


def test_fixture_redirects_default_config_dir(tmp_nivos_home: Path) -> None:
    assert config_mod.DEFAULT_CONFIG_DIR == tmp_nivos_home
    assert tmp_nivos_home.exists()
    assert (tmp_nivos_home / ".state").exists()
    assert (tmp_nivos_home / ".state" / "models").exists()


def test_fixture_redirects_config_path(tmp_nivos_home: Path) -> None:
    assert config_mod.DEFAULT_CONFIG_PATH == tmp_nivos_home / "config.toml"
