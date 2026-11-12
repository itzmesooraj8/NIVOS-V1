"""Tests for Ollama process protection, engine supervisor, reconnect, and diagnostics."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from nivos.cli.daemon_cmd import _registered_pid_alive
from nivos.core.config import NivosConfig
from nivos.core.utils import (
    _windows_taskkill_safe,
    is_protected_ollama_process,
    terminate_process,
)
from nivos.engine.lazy import LazyEngine
from nivos.server.app import create_app
from nivos.server.ollama_supervisor import (
    MIN_RESTART_INTERVAL_SECONDS,
    OllamaSupervisor,
    collect_diagnostics,
    find_ollama_app_exe,
    sanitize_diagnostics_text,
)
from nivos.traces.store import TraceStore


def test_is_protected_ollama_process_and_terminate_refusal(monkeypatch) -> None:
    """terminate_process must never kill ollama.exe, ollama app.exe, or ollama."""

    class FakeProc:
        def __init__(self, pid: int, name: str, children=None):
            self.pid = pid
            self._name = name
            self._children = children or []

        def name(self) -> str:
            return self._name

        def exe(self) -> str:
            return rf"C:\Programs\Ollama\{self._name}"

        def cmdline(self):
            return [self.exe()]

        def children(self, recursive: bool = False):
            return self._children

    procs = {
        1111: FakeProc(1111, "ollama.exe"),
        2222: FakeProc(2222, "ollama app.exe"),
        3333: FakeProc(3333, "python.exe", children=[FakeProc(1111, "ollama.exe")]),
    }

    fake_psutil = SimpleNamespace(Process=lambda pid: procs[pid])
    monkeypatch.setitem(__import__("sys").modules, "psutil", fake_psutil)

    assert is_protected_ollama_process(1111) is True
    assert is_protected_ollama_process(2222) is True
    assert is_protected_ollama_process(3333) is False

    calls = []
    monkeypatch.setattr("nivos.core.utils.subprocess.run", lambda args, **kw: calls.append(args))
    monkeypatch.setattr("nivos.core.utils.process_alive", lambda pid: True)
    monkeypatch.setattr("nivos.core.utils.os.kill", lambda pid, sig: calls.append(("kill", pid, sig)))

    # Must refuse to terminate Ollama PIDs cleanly with zero subprocess/signal calls
    terminate_process(1111)
    terminate_process(2222)
    assert calls == []

    # When parent has an Ollama child, Windows taskkill must NOT pass /T
    _windows_taskkill_safe(3333, force=True)
    assert calls == [["taskkill", "/F", "/PID", "3333"]]

    # Daemon PID file guard must reject recycled PID owned by Ollama
    assert _registered_pid_alive(1111) is False
    assert _registered_pid_alive(2222) is False


def test_find_ollama_app_exe_localappdata(tmp_path: Path, monkeypatch) -> None:
    local_app = tmp_path / "LocalAppData"
    ollama_exe = local_app / "Programs" / "Ollama" / "ollama app.exe"
    ollama_exe.parent.mkdir(parents=True)
    ollama_exe.write_text("stub", encoding="utf-8")

    monkeypatch.setattr("nivos.server.ollama_supervisor.sys.platform", "win32")
    monkeypatch.setitem(__import__("sys").modules, "winreg", None)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "ProgramFiles"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "ProgramFiles86"))

    found = find_ollama_app_exe()
    assert found is not None
    assert Path(found) == ollama_exe


def test_supervisor_rate_limit_two_minutes_banner_and_activity(tmp_path: Path) -> None:
    store = TraceStore(db_path=tmp_path / "traces.db")
    app = SimpleNamespace(state=SimpleNamespace(trace_store=store, engine=None))

    state = {"reachable": False, "models": []}
    now_mono = [1000.0]
    launches: list[str] = []

    def probe_fn():
        if state["reachable"]:
            return {
                "reachable": True,
                "host": "http://127.0.0.1:11434",
                "version": "0.40.16",
                "models": state["models"],
                "error": None,
            }
        return {
            "reachable": False,
            "host": "http://127.0.0.1:11434",
            "version": None,
            "models": [],
            "error": "connection refused",
        }

    sup = OllamaSupervisor(
        app,
        min_restart_interval_seconds=MIN_RESTART_INTERVAL_SECONDS,
        probe_fn=probe_fn,
        find_exe_fn=lambda: r"C:\Users\me\AppData\Local\Programs\Ollama\ollama app.exe",
        launcher_fn=lambda exe: launches.append(exe),
        monotonic_fn=lambda: now_mono[0],
        wall_clock_fn=lambda: datetime(2026, 11, 12, 21, 15, 0),
    )

    # Initial failure triggers 1 launch and sets exact banner
    s1 = sup.check_once()
    assert len(launches) == 1
    assert s1["restarting"] is True
    assert s1["banner"] == "Ollama stopped at 21:15 - restarting"

    # Subsequent checks within 2 minutes must NEVER restart in a loop
    now_mono[0] += 30.0
    sup.check_once()
    now_mono[0] += 89.0  # total 119s < 120s
    sup.check_once()
    assert len(launches) == 1

    # Once >= 120s elapse while still down, a single next attempt is permitted
    now_mono[0] += 2.0  # total 121s >= 120s
    sup.check_once()
    assert len(launches) == 2

    # When Ollama recovers, banner clears and reconnect event is logged in Activity
    state["reachable"] = True
    state["models"] = ["qwen3.5:4b", "qwen3.5:2b"]
    s_ok = sup.check_once()
    assert s_ok["reachable"] is True
    assert s_ok["restarting"] is False
    assert s_ok["banner"] is None
    assert s_ok["models"] == ["qwen3.5:4b", "qwen3.5:2b"]

    traces = store.list_traces(limit=20)
    assert len(traces) >= 2
    summaries = [t.query for t in traces]
    assert any("Ollama stopped at 21:15 - restarting" in q for q in summaries)
    assert any("Ollama connected at 21:15" in q for q in summaries)
    assert all(t.metadata.get("time") and t.metadata.get("reason") for t in traces)


def test_server_reconnects_after_starting_while_ollama_off(monkeypatch) -> None:
    """Nivos started with Ollama off -> Ollama starts -> models appear without restart."""
    cfg = NivosConfig()
    cfg.traces.enabled = False
    lazy_engine = LazyEngine(cfg, "ollama")
    app = create_app(lazy_engine, "lazy", engine_name="lazy", config=cfg)

    online = {"up": False}

    class FakeOllamaEngine:
        engine_id = "ollama"
        is_lazy = False

        def health(self) -> bool:
            return online["up"]

        def list_models(self):
            return ["qwen3.5:4b", "qwen3.5:2b"] if online["up"] else []

        def can_serve(self, model: str) -> bool:
            return model in self.list_models()

    monkeypatch.setattr(
        "nivos.engine._discovery.get_engine",
        lambda c, k, fast=True: ("ollama", FakeOllamaEngine()) if online["up"] else None,
    )
    monkeypatch.setattr(
        "nivos.server.ollama_supervisor.probe_ollama_api",
        lambda host=None, timeout=2.0: {
            "reachable": online["up"],
            "host": "http://127.0.0.1:11434",
            "version": "0.40.16" if online["up"] else None,
            "models": ["qwen3.5:4b", "qwen3.5:2b"] if online["up"] else [],
            "error": None if online["up"] else "connection refused",
        },
    )
    async def _fake_list_local_models():
        return ["qwen3.5:4b", "qwen3.5:2b"] if online["up"] else []

    monkeypatch.setattr(
        "nivos.server.cloud_router.list_local_models",
        _fake_list_local_models,
    )

    sup = OllamaSupervisor(app, auto_restart=False)
    app.state.ollama_supervisor = sup

    client = TestClient(app)
    assert client.get("/v1/models").json()["data"] == []

    # Ollama starts up; next supervisor tick resolves LazyEngine automatically
    online["up"] = True
    res = client.post("/v1/engine/ollama/check")
    assert res.status_code == 200
    assert res.json()["reachable"] is True
    assert res.json()["models"] == ["qwen3.5:4b", "qwen3.5:2b"]

    models_resp = client.get("/v1/models")
    assert models_resp.status_code == 200
    ids = [m["id"] for m in models_resp.json()["data"]]
    assert "qwen3.5:4b" in ids
    assert "qwen3.5:2b" in ids


def test_diagnostics_redacts_secrets_and_includes_status(tmp_path: Path, monkeypatch) -> None:
    secret_log = tmp_path / "server.log"
    secret_log.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=sk-proj-verysecretkey123456",
                "Authorization: Bearer super_secret_bearer_token_999",
                "telegram bot123456789:AAF_VerySecretBotTokenValueXYZ",
                "2026-11-12 GET /api/tags 200 OK",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "MissingLocal"))
    monkeypatch.setattr("nivos.core.config.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "nivos.server.ollama_supervisor.probe_ollama_api",
        lambda timeout=2.0: {
            "reachable": True,
            "host": "http://127.0.0.1:11434",
            "version": "0.40.16",
            "models": ["qwen3.5:4b"],
            "error": None,
        },
    )

    cfg = NivosConfig()
    cfg.traces.enabled = False
    app = create_app(MagicMock(health=lambda: True, list_models=lambda: ["qwen3.5:4b"]), "qwen3.5:4b", config=cfg)

    client = TestClient(app)
    resp = client.get("/v1/diagnostics")
    assert resp.status_code == 200
    report = resp.json()["report"]

    assert "Nivos Version:" in report
    assert "Ollama Processes:" in report
    assert "Ollama API" in report
    assert "Ollama Version: 0.40.16" in report
    assert "Disk (" in report
    assert "Memory:" in report
    assert "GET /api/tags 200 OK" in report

    # Zero secret leakage
    assert "sk-proj-verysecretkey123456" not in report
    assert "super_secret_bearer_token_999" not in report
    assert "AAF_VerySecretBotTokenValueXYZ" not in report
    assert "123456789:AAF" not in report
    assert "[REDACTED" in report

    assert "[REDACTED_KEY]" in sanitize_diagnostics_text("key=sk-ant-abc1234567890xyz")

