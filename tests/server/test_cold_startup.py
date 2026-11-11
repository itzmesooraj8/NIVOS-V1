"""Test cold startup responsiveness and /health answer within 15 seconds."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_cold_startup_health_within_15_seconds(tmp_path: Path):
    """Start the server cold (vault configured, Docker off, channels unset, no engine).

    Verifies the server binds and answers /health within 15 seconds.
    """
    port = _find_free_port()
    config_dir = tmp_path / "nivos_home"
    config_dir.mkdir(parents=True, exist_ok=True)

    vault_dir = tmp_path / "vault"
    vault_dir.mkdir(parents=True, exist_ok=True)
    (vault_dir / "Welcome.md").write_text("# Welcome to Vault\nCold startup test note.", encoding="utf-8")
    vault_posix = str(vault_dir).replace("\\", "/")

    # Minimal config: vault configured, channels unset, telemetry disabled, default settings
    config_toml = f"""
[server]
host = "127.0.0.1"
port = {port}

[obsidian]
vault_path = "{vault_posix}"

[telemetry]
enabled = false

[channel]
enabled = false
"""
    (config_dir / "config.toml").write_text(config_toml, encoding="utf-8")

    env = os.environ.copy()
    env["NIVOS_HOME"] = str(config_dir)
    env["OLLAMA_HOST"] = "http://127.0.0.1:59999"  # Deliberately dead port so Ollama is offline
    env["OPENAI_API_KEY"] = ""
    env["ANTHROPIC_API_KEY"] = ""
    env["GEMINI_API_KEY"] = ""
    env["GOOGLE_API_KEY"] = ""
    env["OPENROUTER_API_KEY"] = ""
    env["TELEGRAM_BOT_TOKEN"] = ""
    env["SLACK_BOT_TOKEN"] = ""

    cmd = [
        sys.executable,
        "-m",
        "nivos.cli",
        "serve",
        "--port",
        str(port),
    ]

    start_time = time.monotonic()
    log_file = config_dir / "test_serve.log"
    with open(log_file, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=lf,
            stderr=subprocess.STDOUT,
            text=True,
        )

        healthy = False
        deadline = start_time + 15.0

        try:
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    out = log_file.read_text(encoding="utf-8", errors="replace")
                    raise AssertionError(f"Server exited prematurely with code {proc.returncode}:\n{out}")

                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.0) as resp:
                        if resp.status == 200:
                            healthy = True
                            break
                except Exception:
                    pass

                time.sleep(0.3)

            elapsed = time.monotonic() - start_time
            if not healthy:
                out = log_file.read_text(encoding="utf-8", errors="replace")
                raise AssertionError(f"Server failed to become healthy on port {port} within 15 seconds (elapsed: {elapsed:.2f}s):\nLOG OUTPUT:\n{out}")
            assert elapsed < 15.0, f"Server took too long to become healthy: {elapsed:.2f}s"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
