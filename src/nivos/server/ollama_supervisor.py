"""Ollama process supervision, background reconnection, and redacted diagnostics."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional
import urllib.error
import urllib.request
import uuid

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

DEFAULT_SUPERVISOR_POLL_SECONDS = 10.0
MIN_RESTART_INTERVAL_SECONDS = 120.0  # Never restart Ollama more than once every 2 minutes

# Secret patterns scrubbed from every diagnostics report and log tail.
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsk-(?:ant-|or-|proj-)?[A-Za-z0-9_-]{8,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\btvly-[A-Za-z0-9_-]{8,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\bAIza[A-Za-z0-9_-]{12,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{8,}\b"), "[REDACTED_TOKEN]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{8,}\b"), "[REDACTED_TOKEN]"),
    (re.compile(r"(?:\bbot|\b)\d{6,}:[A-Za-z0-9_-]{16,}\b"), "[REDACTED_BOT_TOKEN]"),
    (re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._-]{8,}"), r"\1[REDACTED]"),
    (
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:API_KEY|SECRET|TOKEN|PASSWORD|PASSWD|WEBHOOK_URL|SIGNING_SECRET))\s*[:=]\s*[\"']?([^\s\"',;]{3,})[\"']?"
        ),
        r"\1=[REDACTED]",
    ),
    (
        re.compile(
            r"(?i)(--?(?:api[-_]?key|token|password|secret)\s+)([^\s\"']+)"
        ),
        r"\1[REDACTED]",
    ),
)


def sanitize_diagnostics_text(text: str) -> str:
    """Remove potential API keys, tokens, and passwords from text."""
    cleaned = text or ""
    for pattern, replacement in _SECRET_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def _get_ollama_host() -> str:
    try:
        from nivos.server.cloud_router import _ollama_host

        return _ollama_host()
    except Exception:
        raw = (
            os.environ.get("NIVOS_OLLAMA_HOST")
            or os.environ.get("OLLAMA_HOST")
            or "http://127.0.0.1:11434"
        ).strip()
        if raw and not raw.startswith(("http://", "https://")):
            raw = f"http://{raw}"
        return raw.rstrip("/") or "http://127.0.0.1:11434"


def _strip_registry_exe_value(raw: str) -> str:
    """Extract an executable path from a Windows registry Uninstall/AppPath string."""
    val = (raw or "").strip()
    if not val:
        return ""
    if val.startswith('"'):
        end = val.find('"', 1)
        if end > 1:
            val = val[1:end]
    elif "," in val and not val.lower().endswith(".exe"):
        val = val.split(",", 1)[0].strip()
    # Handle DisplayIcon trailing ",0"
    if "," in val:
        head, tail = val.rsplit(",", 1)
        if tail.strip().lstrip("-").isdigit():
            val = head.strip()
    return os.path.expandvars(val).strip('"').strip()


def find_ollama_app_exe() -> Optional[str]:
    """Locate ``ollama app.exe`` via the Windows Registry or standard install paths."""
    if sys.platform != "win32":
        return shutil.which("ollama")

    candidates: List[Path] = []

    local_app_data = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    program_files = os.environ.get("ProgramFiles") or r"C:\Program Files"
    program_files_x86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"

    # Standard per-user and system paths
    candidates.extend(
        [
            Path(local_app_data) / "Programs" / "Ollama" / "ollama app.exe",
            Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama app.exe",
            Path(program_files) / "Ollama" / "ollama app.exe",
            Path(program_files_x86) / "Ollama" / "ollama app.exe",
        ]
    )

    # Query Windows Registry (HKCU + HKLM App Paths, Uninstall, and Ollama keys)
    try:
        import winreg  # type: ignore[import-not-found]

        registry_roots = [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]
        subkeys = [
            r"Software\Microsoft\Windows\CurrentVersion\App Paths\ollama app.exe",
            r"Software\Microsoft\Windows\CurrentVersion\App Paths\ollama.exe",
            r"Software\Ollama",
        ]
        for root in registry_roots:
            for subkey in subkeys:
                try:
                    with winreg.OpenKey(root, subkey) as key:
                        for value_name in ("", "Path", "InstallLocation", "ExePath"):
                            try:
                                raw_val, _ = winreg.QueryValueEx(key, value_name)
                            except OSError:
                                continue
                            cleaned = _strip_registry_exe_value(str(raw_val))
                            if not cleaned:
                                continue
                            p = Path(cleaned)
                            if p.name.lower() == "ollama app.exe":
                                candidates.insert(0, p)
                            elif p.is_dir():
                                candidates.insert(0, p / "ollama app.exe")
                            elif p.name.lower() == "ollama.exe":
                                candidates.insert(0, p.parent / "ollama app.exe")
                except OSError:
                    continue

        uninstall_paths = [
            r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
            r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ]
        for root in registry_roots:
            for uninstall_path in uninstall_paths:
                try:
                    with winreg.OpenKey(root, uninstall_path) as parent:
                        idx = 0
                        while True:
                            try:
                                key_name = winreg.EnumKey(parent, idx)
                                idx += 1
                            except OSError:
                                break
                            try:
                                with winreg.OpenKey(parent, key_name) as item:
                                    display_name = ""
                                    try:
                                        display_name, _ = winreg.QueryValueEx(item, "DisplayName")
                                    except OSError:
                                        pass
                                    if "ollama" not in key_name.lower() and "ollama" not in str(display_name).lower():
                                        continue
                                    for val_name in ("InstallLocation", "DisplayIcon", "UninstallString"):
                                        try:
                                            raw_val, _ = winreg.QueryValueEx(item, val_name)
                                        except OSError:
                                            continue
                                        cleaned = _strip_registry_exe_value(str(raw_val))
                                        if not cleaned:
                                            continue
                                        p = Path(cleaned)
                                        if p.name.lower() == "ollama app.exe":
                                            candidates.insert(0, p)
                                        elif p.is_dir():
                                            candidates.insert(0, p / "ollama app.exe")
                                        else:
                                            candidates.insert(0, p.parent / "ollama app.exe")
                            except OSError:
                                continue
                except OSError:
                    continue
    except Exception as exc:
        logger.debug("Windows registry Ollama lookup failed: %s", exc)

    # Check PATH sibling as fallback
    for cmd_name in ("ollama app.exe", "ollama"):
        which_path = shutil.which(cmd_name)
        if which_path:
            wp = Path(which_path)
            if wp.name.lower() == "ollama app.exe":
                candidates.append(wp)
            else:
                candidates.append(wp.parent / "ollama app.exe")
                candidates.append(wp)

    seen: set[str] = set()
    for cand in candidates:
        try:
            key = str(cand).lower()
            if key in seen:
                continue
            seen.add(key)
            if cand.is_file():
                return str(cand)
        except OSError:
            continue
    return None


def spawn_ollama_detached(exe_path: str) -> None:
    """Launch Ollama completely detached from Nivos so Nivos teardown never kills it."""
    path = Path(exe_path)
    if sys.platform == "win32":
        detached_process = 0x00000008
        create_new_process_group = 0x00000200
        create_breakaway_from_job = 0x01000000
        args = [str(path)]
        if path.name.lower() == "ollama.exe":
            args.append("serve")
        try:
            subprocess.Popen(
                args,
                cwd=str(path.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=(
                    detached_process
                    | create_new_process_group
                    | create_breakaway_from_job
                ),
            )
            return
        except OSError:
            # Retry without breakaway flag if Windows Job prohibits breakaway.
            try:
                subprocess.Popen(
                    args,
                    cwd=str(path.parent),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    creationflags=detached_process | create_new_process_group,
                )
                return
            except OSError:
                if path.name.lower() == "ollama app.exe" and hasattr(os, "startfile"):
                    os.startfile(str(path))  # type: ignore[attr-defined]
                    return
                raise

    args = [str(path)] if path.name != "ollama" else [str(path), "serve"]
    subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )


def probe_ollama_api(host: Optional[str] = None, timeout: float = 2.0) -> Dict[str, Any]:
    """Probe Ollama HTTP API for reachability, version, and local models."""
    base = (host or _get_ollama_host()).rstrip("/")
    result: Dict[str, Any] = {
        "reachable": False,
        "host": base,
        "version": None,
        "models": [],
        "error": None,
    }
    try:
        req = urllib.request.Request(f"{base}/api/version", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
                result["version"] = payload.get("version")
                result["reachable"] = True
    except Exception as exc:
        result["error"] = str(exc)

    try:
        req = urllib.request.Request(f"{base}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
                raw_models = payload.get("models", [])
                result["models"] = [
                    m.get("name", "") for m in raw_models if isinstance(m, dict) and m.get("name")
                ]
                result["reachable"] = True
                result["error"] = None
    except Exception as exc:
        if not result["reachable"] and not result["error"]:
            result["error"] = str(exc)

    return result


def _unwrap_lazy_engine(eng: Any) -> Any:
    """Return the underlying LazyEngine if *eng* wraps one, else None."""
    visited: set[int] = set()
    current = eng
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if getattr(current, "is_lazy", False) is True or getattr(current, "engine_id", "") == "lazy":
            return current
        if type(current).__name__ == "LazyEngine":
            return current
        current = (
            getattr(current, "_engine", None)
            or getattr(current, "_inner", None)
            or getattr(current, "engine", None)
            or getattr(current, "inner", None)
        )
    return None


def reconnect_app_engine(app: Any, models: Optional[List[str]] = None) -> bool:
    """Refresh LazyEngine / app engine state after Ollama becomes reachable."""
    if app is None:
        return False
    state = getattr(app, "state", None)
    if state is None:
        return False
    engine = getattr(state, "engine", None)
    lazy = _unwrap_lazy_engine(engine)
    reconnected = False
    if lazy is not None:
        try:
            lazy._last_check = 0.0
            resolved = lazy._try_resolve()
            if resolved is not None:
                resolved_key, _resolved_eng = resolved
                if getattr(state, "engine_name", "") in ("", "lazy", "none", None):
                    state.engine_name = resolved_key or "ollama"
                reconnected = True
        except Exception as exc:
            logger.debug("LazyEngine background reconnect attempt failed: %s", exc)

    if models:
        cur_model = getattr(state, "model", "")
        if not cur_model or cur_model in ("lazy", "none"):
            state.model = models[0]
            reconnected = True
    return reconnected


@dataclass
class SupervisorEvent:
    timestamp: float
    time_str: str
    kind: str  # "stopped" | "restarting" | "restart_failed" | "reconnected"
    message: str
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "time": self.time_str,
            "kind": self.kind,
            "message": self.message,
            "reason": self.reason,
        }


class OllamaSupervisor:
    """Supervises the local Ollama engine, restarts it at most once per 2 minutes,
    logs events to Activity (TraceStore), and exposes banner/status state.
    """

    def __init__(
        self,
        app: Any = None,
        *,
        min_restart_interval_seconds: float = MIN_RESTART_INTERVAL_SECONDS,
        poll_interval_seconds: float = DEFAULT_SUPERVISOR_POLL_SECONDS,
        auto_restart: bool = True,
        probe_fn: Optional[Callable[[], Dict[str, Any]]] = None,
        find_exe_fn: Optional[Callable[[], Optional[str]]] = None,
        launcher_fn: Optional[Callable[[str], None]] = None,
        monotonic_fn: Callable[[], float] = time.monotonic,
        wall_clock_fn: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._app = app
        self.min_restart_interval_seconds = float(min_restart_interval_seconds)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.auto_restart = auto_restart
        self._probe_fn = probe_fn or probe_ollama_api
        self._find_exe_fn = find_exe_fn or find_ollama_app_exe
        self._launcher_fn = launcher_fn or spawn_ollama_detached
        self._monotonic = monotonic_fn
        self._wall_clock = wall_clock_fn

        self._lock = threading.RLock()
        self.reachable: Optional[bool] = None
        self.restarting: bool = False
        self.stopped_at_hhmm: Optional[str] = None
        self.banner_message: Optional[str] = None
        self.version: Optional[str] = None
        self.models: List[str] = []
        self.last_error: Optional[str] = None
        self.last_check_ts: Optional[float] = None
        self.last_restart_monotonic: Optional[float] = None
        self.restart_attempts: int = 0
        self.events: deque[SupervisorEvent] = deque(maxlen=50)
        self._was_ever_unreachable: bool = False

    def _record_event(
        self,
        *,
        kind: str,
        message: str,
        reason: str,
        now_dt: datetime,
        outcome: str = "success",
    ) -> None:
        ts = now_dt.timestamp()
        ev = SupervisorEvent(
            timestamp=ts,
            time_str=now_dt.isoformat(timespec="seconds"),
            kind=kind,
            message=message,
            reason=reason,
        )
        self.events.appendleft(ev)

        trace_store = getattr(getattr(self._app, "state", None), "trace_store", None)
        if trace_store is None:
            return
        try:
            from nivos.core.types import StepType, Trace, TraceStep

            trace = Trace(
                trace_id=f"ollama-{uuid.uuid4().hex[:12]}",
                query=f"[Engine Supervisor] {message}",
                agent="engine-supervisor",
                model="ollama",
                engine="ollama",
                steps=[
                    TraceStep(
                        step_type=StepType.ROUTE,
                        timestamp=ts,
                        duration_seconds=0.0,
                        input={"event": kind, "time": now_dt.strftime("%Y-%m-%d %H:%M:%S")},
                        output={"summary": message, "reason": reason},
                        metadata={"source": "ollama_supervisor"},
                    )
                ],
                result=reason or message,
                outcome=outcome,
                started_at=ts,
                ended_at=ts,
                total_latency_seconds=0.0,
                metadata={"event": kind, "time": ev.time_str, "reason": reason},
            )
            trace_store.save(trace)
        except Exception as exc:
            logger.debug("Failed saving supervisor event to TraceStore: %s", exc)

    def check_once(self) -> Dict[str, Any]:
        """Run one supervision cycle: probe Ollama, reconnect models, or restart if down."""
        probe = self._probe_fn()
        now_dt = self._wall_clock()
        now_mono = self._monotonic()
        reachable = bool(probe.get("reachable"))
        models = list(probe.get("models") or [])

        with self._lock:
            prev_reachable = self.reachable
            self.last_check_ts = now_dt.timestamp()
            self.reachable = reachable
            self.version = probe.get("version") or self.version

            if reachable:
                self.models = models
                self.last_error = None
                was_down = prev_reachable is False or self.restarting or self._was_ever_unreachable
                self.restarting = False
                self.stopped_at_hhmm = None
                self.banner_message = None

                # Always ensure LazyEngine / server state is synchronized when Ollama responds
                reconnect_app_engine(self._app, models)

                if was_down:
                    self._was_ever_unreachable = False
                    hhmm = now_dt.strftime("%H:%M")
                    msg = f"Ollama connected at {hhmm}"
                    reason = (
                        f"Ollama API reachable ({probe.get('host', '127.0.0.1:11434')}, "
                        f"{len(models)} models available)"
                    )
                    self._record_event(
                        kind="reconnected",
                        message=msg,
                        reason=reason,
                        now_dt=now_dt,
                        outcome="success",
                    )
                return self.get_status()

            # Unreachable branch
            self._was_ever_unreachable = True
            self.models = []
            reason = str(probe.get("error") or "Ollama API unreachable")
            self.last_error = reason
            hhmm = now_dt.strftime("%H:%M")

            can_restart = (
                self.auto_restart
                and (
                    self.last_restart_monotonic is None
                    or (now_mono - self.last_restart_monotonic) >= self.min_restart_interval_seconds
                )
            )
            if not can_restart:
                # If we haven't restarted recently, keep any existing banner during restart window,
                # otherwise surface stopped banner if an attempt is pending after cooldown.
                if prev_reachable is not False:
                    self._record_event(
                        kind="stopped",
                        message=f"Ollama stopped at {hhmm}",
                        reason=reason,
                        now_dt=now_dt,
                        outcome="failure",
                    )
                return self.get_status()

            # Attempt rate-limited restart (at most once per 2 minutes)
            self.last_restart_monotonic = now_mono
            self.restart_attempts += 1
            self.stopped_at_hhmm = hhmm
            banner = f"Ollama stopped at {hhmm} - restarting"
            self.banner_message = banner

            exe_path = self._find_exe_fn()
            if not exe_path:
                self.restarting = False
                fail_reason = f"{reason}; ollama app.exe not found in Registry or %LOCALAPPDATA%\\Programs\\Ollama"
                self._record_event(
                    kind="restart_failed",
                    message=banner,
                    reason=fail_reason,
                    now_dt=now_dt,
                    outcome="failure",
                )
                return self.get_status()

            try:
                self._launcher_fn(exe_path)
                self.restarting = True
                self._record_event(
                    kind="restarting",
                    message=banner,
                    reason=f"{reason}; launched {exe_path}",
                    now_dt=now_dt,
                    outcome="success",
                )
            except Exception as exc:
                self.restarting = False
                fail_reason = f"{reason}; failed to launch {exe_path}: {exc}"
                self._record_event(
                    kind="restart_failed",
                    message=banner,
                    reason=fail_reason,
                    now_dt=now_dt,
                    outcome="failure",
                )
            return self.get_status()

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "reachable": self.reachable,
                "restarting": self.restarting,
                "banner": self.banner_message,
                "stopped_at": self.stopped_at_hhmm,
                "version": self.version,
                "models": list(self.models),
                "model_count": len(self.models),
                "last_error": self.last_error,
                "last_check_ts": self.last_check_ts,
                "restart_attempts": self.restart_attempts,
                "events": [e.to_dict() for e in list(self.events)[:10]],
            }


def _tail_file_lines(path: Path, max_lines: int = 15) -> List[str]:
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            lines = deque(fh, maxlen=max_lines)
        return [sanitize_diagnostics_text(line.rstrip("\r\n")) for line in lines]
    except Exception as exc:
        return [f"<unreadable: {exc}>"]


def _get_nivos_version() -> str:
    try:
        from importlib.metadata import version

        return version("nivos")
    except Exception:
        pass
    try:
        import nivos

        return str(getattr(nivos, "__version__", "0.1.0"))
    except Exception:
        return "0.1.0"


def collect_diagnostics(app: Any = None) -> Dict[str, Any]:
    """Build a short, secret-free diagnostic report for Settings > Diagnostics."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    nivos_version = _get_nivos_version()
    probe = probe_ollama_api(timeout=2.0)

    # Ollama processes
    ollama_procs: List[str] = []
    try:
        import psutil

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = (proc.info.get("name") or "").strip()
                if name.lower() in {"ollama.exe", "ollama app.exe", "ollama"}:
                    ollama_procs.append(f"{name} (PID {proc.info['pid']})")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass

    # Disk & memory
    try:
        target_path = Path.home()
        du = shutil.disk_usage(target_path)
        disk_line = (
            f"Disk ({target_path.anchor or '/'}): "
            f"{du.free / (1024 ** 3):.1f} GB free / {du.total / (1024 ** 3):.1f} GB total"
        )
    except Exception as exc:
        disk_line = f"Disk: unavailable ({exc})"

    try:
        import psutil

        vm = psutil.virtual_memory()
        mem_line = (
            f"Memory: {vm.available / (1024 ** 3):.1f} GB available / "
            f"{vm.total / (1024 ** 3):.1f} GB total ({vm.percent:.0f}% used)"
        )
    except Exception:
        mem_line = "Memory: unavailable"

    # Log files (Nivos + Ollama)
    from nivos.core.config import get_config_dir

    local_app_data = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    log_candidates = [
        ("nivos server.log", get_config_dir() / "server.log"),
        ("nivos server.log (~)", Path.home() / ".nivos" / "server.log"),
        ("ollama server.log", local_app_data / "Ollama" / "server.log"),
        ("ollama app.log", local_app_data / "Ollama" / "app.log"),
        ("ollama server.log (~)", Path.home() / ".ollama" / "logs" / "server.log"),
    ]
    seen_logs: set[Path] = set()
    log_sections: List[str] = []
    for label, path in log_candidates:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen_logs or not path.is_file():
            continue
        seen_logs.add(resolved)
        tail = _tail_file_lines(path, max_lines=10)
        if tail:
            log_sections.append(f"[{label}]\n" + "\n".join(tail))

    supervisor = getattr(getattr(app, "state", None), "ollama_supervisor", None)
    sup_status = supervisor.get_status() if supervisor is not None else {}

    lines = [
        f"Nivos Diagnostics ({now_str})",
        f"Nivos Version: {nivos_version}",
        f"Python: {sys.version.split()[0]} ({sys.platform})",
        f"Ollama Processes: {', '.join(ollama_procs) if ollama_procs else 'none running'}",
        f"Ollama API ({probe['host']}): {'reachable' if probe['reachable'] else 'unreachable'}",
        f"Ollama Version: {probe.get('version') or sup_status.get('version') or 'unknown'}",
        f"Ollama Models ({len(probe.get('models') or [])}): {', '.join(probe.get('models') or []) or 'none'}",
        disk_line,
        mem_line,
    ]
    if probe.get("error"):
        lines.append(f"Ollama Error: {sanitize_diagnostics_text(str(probe['error']))}")
    if sup_status.get("banner"):
        lines.append(f"Supervisor Status: {sup_status['banner']}")
    if log_sections:
        lines.append("")
        lines.append("Recent Logs (redacted):")
        lines.extend(log_sections)

    report = sanitize_diagnostics_text("\n".join(lines))
    return {
        "generated_at": now_str,
        "nivos_version": nivos_version,
        "ollama": {
            "processes": ollama_procs,
            "reachable": probe["reachable"],
            "host": probe["host"],
            "version": probe.get("version"),
            "models": probe.get("models") or [],
            "error": sanitize_diagnostics_text(str(probe["error"])) if probe.get("error") else None,
        },
        "system": {
            "disk": disk_line,
            "memory": mem_line,
        },
        "report": report,
    }


router = APIRouter(tags=["engine-supervisor"])


@router.get("/v1/engine/ollama/status")
async def get_ollama_supervisor_status(request: Request) -> Dict[str, Any]:
    """Return live Ollama reachability, restart banner state, and recent supervisor events."""
    supervisor = getattr(request.app.state, "ollama_supervisor", None)
    if supervisor is None:
        probe = probe_ollama_api(timeout=2.0)
        reconnect_app_engine(request.app, probe.get("models") or [])
        return {
            "reachable": probe["reachable"],
            "restarting": False,
            "banner": None,
            "stopped_at": None,
            "version": probe.get("version"),
            "models": probe.get("models") or [],
            "model_count": len(probe.get("models") or []),
            "last_error": probe.get("error"),
            "events": [],
        }
    return supervisor.get_status()


@router.post("/v1/engine/ollama/check")
async def trigger_ollama_supervisor_check(request: Request) -> Dict[str, Any]:
    """Run an immediate supervisor check + engine reconnect pass."""
    import asyncio

    supervisor = getattr(request.app.state, "ollama_supervisor", None)
    if supervisor is None:
        probe = await asyncio.to_thread(probe_ollama_api, timeout=2.0)
        reconnect_app_engine(request.app, probe.get("models") or [])
        return {
            "reachable": probe["reachable"],
            "restarting": False,
            "banner": None,
            "version": probe.get("version"),
            "models": probe.get("models") or [],
            "model_count": len(probe.get("models") or []),
            "last_error": probe.get("error"),
        }
    return await asyncio.to_thread(supervisor.check_once)


@router.get("/v1/diagnostics")
async def get_diagnostics(request: Request) -> Dict[str, Any]:
    """Return a short, secret-free diagnostic report for Settings > Diagnostics."""
    import asyncio

    return await asyncio.to_thread(collect_diagnostics, request.app)

