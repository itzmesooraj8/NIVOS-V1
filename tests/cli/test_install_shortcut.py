"""Tests for Phase 7: nivos install-shortcut command and launcher generation."""

from pathlib import Path
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from nivos.cli.install_shortcut_cmd import (
    find_browser_executable,
    setup_launcher_files,
    install_shortcut,
    create_windows_shortcut,
)


def test_find_browser_executable():
    """Verify that Edge/Chrome or default browser detection returns a valid tuple."""
    path, name = find_browser_executable()
    assert name in ("msedge", "chrome", "default")
    if name != "default":
        assert path is not None
        assert isinstance(path, Path)


def test_setup_launcher_files(tmp_path, monkeypatch):
    """Verify that launcher scripts and icon are generated properly in config dir."""
    monkeypatch.setattr("nivos.cli.install_shortcut_cmd.DEFAULT_CONFIG_DIR", tmp_path)
    vbs_path, icon_dst = setup_launcher_files()

    assert vbs_path.exists()
    assert vbs_path.suffix == ".vbs"
    ps1_path = tmp_path / "launch-nivos.ps1"
    assert ps1_path.exists()
    content = ps1_path.read_text(encoding="utf-8")
    assert "127.0.0.1" in content
    assert "8000" in content
    assert "Start-ScheduledTask" in content
    assert "--app=http://127.0.0.1:8000" in content


def test_create_windows_shortcut_success(tmp_path):
    """Verify shortcut creation calls powershell with proper COM invocation."""
    shortcut = tmp_path / "test.lnk"
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        # Mock file creation
        shortcut.touch()
        result = create_windows_shortcut(
            shortcut_path=shortcut,
            target_path=Path("wscript.exe"),
            arguments="launch.vbs",
        )
        assert result is True
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert "powershell.exe" in args[0]


def test_install_shortcut_cli():
    """Verify install-shortcut command runs cleanly through Click runner."""
    runner = CliRunner()
    with patch("nivos.cli.install_shortcut_cmd.create_windows_shortcut", return_value=True):
        result = runner.invoke(install_shortcut, ["--force"])
        assert result.exit_code == 0
        assert "Nivos shortcuts" in result.output
