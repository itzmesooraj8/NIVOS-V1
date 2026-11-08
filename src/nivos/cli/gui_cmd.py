"""``nivos gui`` — start and open the local graphical interface."""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import click
import psutil
from rich.console import Console

from nivos.core.config import DEFAULT_CONFIG_DIR
from nivos.security.file_utils import secure_write_json

# Records the frontend this command started, so a later run can tell its own
# leftover dev server apart from an unrelated application holding the port.
# ``npm run dev`` is npm -> cmd.exe -> node and only the leaf binds the port,
# so an interrupted run routinely leaves that leaf behind; see _reclaim_port.
_FRONTEND_STATE_FILE = DEFAULT_CONFIG_DIR / "frontend.json"

# How often to re-check that the API is still up while the frontend runs.
_API_POLL_SECONDS = 5.0

# How many consecutive failed probes mean the API is really gone. A single
# failure proves nothing: a loopback connect to a busy server intermittently
# stalls for seconds, and treating one timeout as death tears down a working
# GUI — a worse bug than the dead-backend one this watch exists to catch.
_API_FAILURES_BEFORE_DOWN = 3


def _frontend_dir() -> Path | None:
    """Find the source checkout's frontend directory."""
    configured = os.environ.get("NIVOS_FRONTEND_DIR")
    candidates = [Path(configured)] if configured else []
    candidates.append(Path(__file__).resolve().parents[3] / "frontend")
    for candidate in candidates:
        if candidate.is_dir() and (candidate / "package.json").is_file():
            return candidate
    return None


def _read_frontend_state() -> dict:
    """Return the recorded frontend, or {} when it is missing or malformed."""
    try:
        state = json.loads(_FRONTEND_STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(state, dict):
        return {}
    if (
        type(state.get("pid")) is not int
        or state["pid"] <= 0
        or type(state.get("port")) is not int
        or not 0 <= state["port"] <= 65535
    ):
        return {}
    return state


def _frontend_state_lock():
    from nivos.cli.daemon_cmd import _state_lock

    return _state_lock(_FRONTEND_STATE_FILE)


def clear_frontend_state(pid: int, launch_token: str | None) -> None:
    """Deregister a frontend, but only if it still owns the file."""
    with _frontend_state_lock():
        state = _read_frontend_state()
        if state.get("pid") == pid and state.get("launch_token") == launch_token:
            _FRONTEND_STATE_FILE.unlink(missing_ok=True)


def _process_identity(process: psutil.Process) -> dict | None:
    """Return an identity that cannot be confused with a recycled PID."""
    if sys.platform == "linux":
        from nivos.cli.daemon_cmd import _linux_process_identity

        # psutil's Linux epoch start time depends on the current boot epoch.
        # Keep the same clock-stable identity used by daemon bookkeeping.
        return _linux_process_identity(process.pid)
    return {"pid": process.pid, "created_at": process.create_time()}


def _inspect_frontend_processes(state: dict) -> tuple[list[psutil.Process], list[dict]]:
    """Separate verified owners from records whose identity is temporarily unknown."""
    if not isinstance(state.get("launch_token"), str) or not state["launch_token"]:
        return [], []
    records = state.get("processes")
    if not isinstance(records, list):
        return [], []
    owned, unknown = [], []
    for record in records:
        if not isinstance(record, dict) or type(record.get("pid")) is not int:
            continue
        if record["pid"] <= 0 or not isinstance(record.get("identity"), dict):
            continue
        try:
            process = psutil.Process(record["pid"])
            identity = _process_identity(process)
            if identity is None:
                unknown.append(record)
            elif (
                identity == record["identity"]
                and process.status() != psutil.STATUS_ZOMBIE
            ):
                # The Linux identity already checks start ticks and boot ID;
                # an epoch-based psutil comparison can change with the clock.
                if sys.platform != "linux" and not process.is_running():
                    continue
                owned.append(process)
        except psutil.NoSuchProcess:
            continue
        except (psutil.Error, OSError):
            unknown.append(record)
        except (ValueError, OverflowError):
            continue
    return owned, unknown


def _owned_processes(state: dict) -> list[psutil.Process]:
    """Recover only verified identities; a bare legacy PID is not ownership."""
    return _inspect_frontend_processes(state)[0]


def _frontend_processes(
    state: dict,
    live_process: subprocess.Popen | None = None,
    *,
    verified: list[psutil.Process] | None = None,
) -> list[psutil.Process]:
    """Include current descendants of verified owners and the live child we own."""
    owners = _owned_processes(state) if verified is None else verified
    processes = {process.pid: process for process in owners}
    if live_process is not None and live_process.poll() is None:
        try:
            # A live, unreaped Popen child belongs to this invocation. This
            # path also lets startup failure clean up before state is saved.
            processes[live_process.pid] = psutil.Process(live_process.pid)
        except psutil.Error:
            pass
    for process in list(processes.values()):
        try:
            for child in process.children(recursive=True):
                processes[child.pid] = child
        except psutil.Error:
            continue
    return list(processes.values())


def _terminate_frontend(
    state: dict, live_process: subprocess.Popen | None = None
) -> bool:
    owned, unknown = _inspect_frontend_processes(state)
    if unknown:
        raise click.ClickException(
            "Could not verify frontend process ownership; saved state was kept. "
            "Retry when process information is accessible."
        )
    processes = _frontend_processes(state, live_process, verified=owned)
    # POSIX launchers have a dedicated session. A verified surviving member
    # proves the group still belongs to this launch even after npm has exited.
    group = state.get("process_group")
    if os.name != "nt" and type(group) is int and group == state.get("pid"):
        for process in processes:
            try:
                if os.getpgid(process.pid) == group and process.is_running():
                    os.killpg(group, signal.SIGTERM)
                    break
            except (OSError, psutil.Error):
                continue
    for process in reversed(processes):
        with contextlib.suppress(psutil.Error):
            process.terminate()
    _, alive = psutil.wait_procs(processes, timeout=5.0)
    for process in alive:
        with contextlib.suppress(psutil.Error):
            process.kill()
    _, remaining = psutil.wait_procs(alive, timeout=1.0)
    if remaining:
        raise click.ClickException("Could not stop every owned frontend process.")
    if live_process is not None and live_process.poll() is None:
        live_process.terminate()
        try:
            live_process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            live_process.kill()
            live_process.wait(timeout=1.0)
    remaining_owned, unknown = _inspect_frontend_processes(state)
    if remaining_owned or unknown:
        raise click.ClickException(
            "Frontend ownership could not be cleared safely; saved state was kept."
        )
    return bool(processes)


class _FrontendOwner:
    """Track a launch's process identities without leaving stale exit hooks."""

    def __init__(self, process: subprocess.Popen, port: int) -> None:
        self.process = process
        self.state = {
            "pid": process.pid,
            "port": port,
            "launch_token": secrets.token_hex(16),
            "processes": [],
            "process_group": process.pid if os.name != "nt" else None,
        }
        self.registered = False
        self.closed = False

    def refresh(self) -> None:
        previous = {record["pid"]: record for record in self.state["processes"]}
        # A denied/transient read is not evidence that a process ended. Keep
        # its last verified identity so a later stop/reclaim can retry safely.
        owned, unknown = _inspect_frontend_processes(self.state)
        records = {record["pid"]: record for record in unknown}
        for process in _frontend_processes(self.state, self.process, verified=owned):
            try:
                identity = _process_identity(process)
                if process.pid in previous:
                    # Never replace a recorded identity with a newer process
                    # that recycled this PID during refresh.
                    if (
                        identity is None
                        or identity == previous[process.pid]["identity"]
                    ):
                        records[process.pid] = previous[process.pid]
                elif identity is not None and process.is_running():
                    confirmed = _process_identity(process)
                    if confirmed == identity:
                        records[process.pid] = {
                            "pid": process.pid,
                            "identity": identity,
                        }
            except psutil.NoSuchProcess:
                continue
            except (psutil.Error, OSError):
                if process.pid in previous:
                    records[process.pid] = previous[process.pid]
        updated = list(records.values())
        if updated == self.state["processes"]:
            return
        self.state["processes"] = updated
        if self.registered:
            with _frontend_state_lock():
                current = _read_frontend_state()
                if current.get("pid") == self.state["pid"] and (
                    current.get("launch_token") == self.state["launch_token"]
                ):
                    secure_write_json(_FRONTEND_STATE_FILE, self.state)

    def register(self) -> None:
        self.refresh()
        if not any(
            record["pid"] == self.process.pid for record in self.state["processes"]
        ):
            raise click.ClickException(
                "Could not verify the frontend process identity."
            )
        with _frontend_state_lock():
            current = _read_frontend_state()
            if current.get("launch_token") != self.state["launch_token"]:
                owned, unknown = _inspect_frontend_processes(current)
                if owned or unknown:
                    raise click.ClickException(
                        "Another frontend is still registered. Stop that graphical "
                        "session before starting a new one."
                    )
            secure_write_json(_FRONTEND_STATE_FILE, self.state)
        self.registered = True

    def shutdown(self) -> None:
        if self.closed:
            return
        self.closed = True
        _terminate_frontend(self.state, self.process)
        clear_frontend_state(self.state["pid"], self.state["launch_token"])


def stop_frontend(*, state: dict | None = None) -> int | None:
    """Stop the recorded frontend, optionally using a port check's exact snapshot.

    Unknown ownership keeps the saved state and raises so a later call can
    retry. Returns the launch PID stopped, or None for a stale registration.
    """
    state = _read_frontend_state() if state is None else state
    pid = state.get("pid")
    if pid is None:
        return None
    stopped = _terminate_frontend(state)
    clear_frontend_state(pid, state.get("launch_token"))
    return pid if stopped else None


def _port_free(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", port))
    except OSError:
        return False
    return True


def _port_owner_pid(port: int) -> int | None:
    """Best-effort PID listening on *port*, for a message the user can act on."""
    try:
        if sys.platform == "win32":
            res = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True,
                text=True,
                check=False,
            )
            out = res.stdout if isinstance(res.stdout, str) else ""
            for line in out.splitlines():
                parts = line.split()
                if (
                    len(parts) >= 5
                    and parts[0].upper() == "TCP"
                    and parts[3].upper() == "LISTENING"
                    and parts[1].endswith(f":{port}")
                ):
                    return int(parts[4])
            return None
        res = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            check=False,
        )
        out = res.stdout if isinstance(res.stdout, str) else ""
        parts = out.split()
        return int(parts[0]) if parts else None
    except (OSError, ValueError, TypeError):
        return None


def _reclaim_port(port: int) -> bool:
    """Free *port* when this command's own earlier frontend still holds it.

    A frontend killed outright never runs its cleanup. Saved process
    identities identify verified survivors of our earlier launch; a bare PID
    does not establish ownership after the operating system reuses it.
    """
    state = _read_frontend_state()
    if state.get("port") != port:
        return False
    if stop_frontend(state=state) is None:
        return _port_free(port)
    for _ in range(20):
        if _port_free(port):
            return True
        time.sleep(0.25)
    return False


def _check_frontend_port(port: int) -> None:
    """Reject a port already bound by another local application."""
    if _port_free(port):
        return
    if _reclaim_port(port):
        return
    owner = _port_owner_pid(port)
    held_by = f" It is held by PID {owner}." if owner else ""
    raise click.ClickException(
        f"Frontend port {port} is unavailable and was not started by "
        f"Nivos.{held_by} Stop it, or choose another port with "
        f"--frontend-port."
    )


def _api_healthy(port: int, timeout: float = 3.0) -> bool:
    """Return whether an API server is answering on *port*."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=timeout
        ) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _daemon_pid_alive(port: int, *, require_ready: bool = False) -> bool:
    """Whether the daemon registered on *port* is still a live process.

    Corroborates a run of failed health probes: a wedged-but-running server
    should not be reported as stopped, and only the daemon's own bookkeeping
    knows which process is supposed to be serving this port.
    """
    # Imported lazily; daemon_cmd imports this module for its own `stop`.
    from nivos.cli.daemon_cmd import _read_pid_unlocked, _read_state, _state_lock

    with _state_lock():
        state = _read_state()
        if state.get("port") != port or (require_ready and state.get("ready") is False):
            return False
        return _read_pid_unlocked() == state["pid"]


def _wait_for_port(
    process: subprocess.Popen[bytes],
    host: str,
    port: int,
    timeout: float = 20.0,
    *,
    on_poll=None,
) -> bool:
    """Wait for this launch's frontend process to listen on the requested port."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if on_poll is not None:
            on_poll()
        if process.poll() is not None:
            return False
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return process.poll() is None
        except OSError:
            time.sleep(0.2)
    return False


def _ensure_frontend_dependencies(frontend: Path, npm: str) -> None:
    """Install frontend dependencies when this checkout has not been bootstrapped."""
    vite = (
        frontend
        / "node_modules"
        / ".bin"
        / ("vite.cmd" if sys.platform == "win32" else "vite")
    )
    if vite.exists():
        return
    click.echo("Installing graphical frontend dependencies...", err=True)
    result = subprocess.run(
        [npm, "install", "--no-audit", "--no-fund"],
        cwd=frontend,
        check=False,
    )
    if result.returncode != 0:
        raise click.ClickException(
            "Could not install frontend dependencies. "
            "Run `npm install` in the frontend directory."
        )


def _static_dir() -> Path:
    """Path to the server's static frontend directory."""
    return Path(__file__).resolve().parent.parent / "server" / "static"


def _is_static_outdated(frontend: Path, static_dir: Path) -> bool:
    """Return True if static/ is missing, missing index.html, or older than frontend sources."""
    if not static_dir.is_dir():
        return True
    index_file = static_dir / "index.html"
    if not index_file.is_file():
        return True
    try:
        static_mtime = index_file.stat().st_mtime
    except OSError:
        return True

    check_targets = [
        frontend / "index.html",
        frontend / "package.json",
        frontend / "vite.config.ts",
    ]
    src_dir = frontend / "src"
    if src_dir.is_dir():
        check_targets.extend(src_dir.rglob("*"))

    for p in check_targets:
        try:
            if p.is_file() and p.stat().st_mtime > static_mtime:
                return True
        except OSError:
            continue
    return False


def build_frontend(frontend: Path, console: Console | None = None) -> None:
    """Build the graphical frontend into server/static using npm run build."""
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm is None:
        raise click.ClickException(
            "Node.js/npm is required to build the frontend. Install Node.js 22 or newer."
        )
    _ensure_frontend_dependencies(frontend, npm)
    if console:
        console.print("[cyan]Building frontend assets (npm run build)...[/cyan]")
    result = subprocess.run([npm, "run", "build"], cwd=frontend, check=False)
    if result.returncode != 0:
        raise click.ClickException("Failed to build frontend assets.")
    if console:
        console.print("[green]Frontend assets built successfully.[/green]")


def _configured_server_endpoint() -> tuple[str, int]:
    """Read host and port from config.toml without triggering hardware detection."""
    from nivos.core.config import DEFAULT_CONFIG_PATH

    host, port = "127.0.0.1", 8000
    try:
        import tomllib

        if DEFAULT_CONFIG_PATH.is_file():
            data = tomllib.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
            server_section = data.get("server", {})
            if isinstance(server_section, dict):
                if "host" in server_section and isinstance(server_section["host"], str):
                    host = server_section["host"]
                if "port" in server_section:
                    port = int(server_section["port"])
    except Exception:
        pass
    return host, port


@click.command("build-ui")
def build_ui() -> None:
    """Build the graphical frontend assets into the static directory."""
    console = Console(stderr=True)
    frontend = _frontend_dir()
    if frontend is None:
        raise click.ClickException(
            "The graphical frontend is not available in this installation. "
            "Run this command from a source checkout."
        )
    build_frontend(frontend, console)


@click.command()
@click.option(
    "-p",
    "--port",
    "port",
    default=None,
    type=click.IntRange(1, 65535),
    help="Port number for the Nivos server (defaults to config.toml or 8000).",
)
@click.option(
    "--api-port",
    default=None,
    type=click.IntRange(1, 65535),
    help="Alias for --port (backward compatibility).",
)
@click.option(
    "--dev",
    is_flag=True,
    default=False,
    help="Run in development mode with Vite dev server on port 5173.",
)
@click.option(
    "--frontend-port",
    default=5173,
    show_default=True,
    type=click.IntRange(1, 65535),
    help="Port for the Vite dev server (only used with --dev).",
)
@click.option("--no-server", is_flag=True, help="Do not start the API server.")
@click.option("--no-browser", is_flag=True, help="Only start without opening the browser.")
def gui(
    port: int | None,
    api_port: int | None,
    dev: bool,
    frontend_port: int,
    no_server: bool,
    no_browser: bool,
) -> None:
    """Start the browser-based graphical mode in the default browser.

    In normal use, the app and API run on a single port (8000, configurable in
    config.toml), serving the built frontend from server/static.
    Use --dev for development mode with Vite on port 5173.
    """
    console = Console(stderr=True)
    cfg_host, cfg_port = _configured_server_endpoint()
    effective_port = (
        port if port is not None else (api_port if api_port is not None else cfg_port)
    )
    bind_host = cfg_host

    frontend = _frontend_dir()
    static_dir = _static_dir()

    if not dev:
        # Single-port mode: ensure static build is present and up to date
        if frontend is not None:
            if _is_static_outdated(frontend, static_dir):
                console.print(
                    "[cyan]Frontend assets are missing or outdated. Building...[/cyan]"
                )
                build_frontend(frontend, console)
        elif not static_dir.is_dir() or not (static_dir / "index.html").is_file():
            raise click.ClickException(
                "The graphical frontend is not available in this installation. "
                "Download the Nivos desktop app or run this command "
                "from a source checkout."
            )

        # Check / reuse / start API server on effective_port
        if not no_server and _api_healthy(effective_port):
            if not _daemon_pid_alive(effective_port, require_ready=True):
                raise click.ClickException(
                    f"API port {effective_port} is already serving an unregistered service. "
                    "Stop it or choose another port. Use --no-server only when you "
                    "intend to use an externally managed Nivos backend."
                )
            console.print(
                f"[green]Reusing the API server already running on port {effective_port}.[/green]"
            )
            no_server = True

        launched_api = not no_server
        if launched_api:
            uv = shutil.which("uv")
            if uv is None:
                cmd = [
                    sys.executable,
                    "-m",
                    "nivos.cli",
                    "start",
                    "--port",
                    str(effective_port),
                ]
            else:
                cmd = [
                    uv,
                    "run",
                    "--extra",
                    "desktop",
                    "nivos",
                    "start",
                    "--port",
                    str(effective_port),
                ]
            server = subprocess.run(
                cmd,
                cwd=frontend.parent if frontend else None,
                check=False,
            )
            if server.returncode != 0:
                raise click.ClickException("Could not start the Nivos API server.")

            # Wait for API to become healthy
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                if _api_healthy(effective_port):
                    break
                time.sleep(0.2)
            if not _api_healthy(effective_port):
                raise click.ClickException("The API server did not become healthy.")

        url = (
            f"http://[{bind_host}]:{effective_port}"
            if ":" in bind_host
            else f"http://{bind_host}:{effective_port}"
        )
        console.print(f"[green]Nivos graphical mode is ready:[/green] {url}")
        if not no_browser:
            webbrowser.open(url)
        return

    # Development mode (--dev): starts Vite dev server
    if frontend is None:
        raise click.ClickException(
            "The graphical frontend source is not available in this installation. "
            "Run this command from a source checkout."
        )
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm is None:
        raise click.ClickException(
            "Node.js/npm is required for graphical dev mode. Install Node.js 22 or newer."
        )
    _check_frontend_port(frontend_port)
    _ensure_frontend_dependencies(frontend, npm)

    # `nivos start` exits non-zero when a daemon is already registered, so
    # without this a perfectly healthy API turned into the misleading "Could
    # not start the Nivos API server." Adopt it instead.
    if not no_server and _api_healthy(effective_port):
        if not _daemon_pid_alive(effective_port, require_ready=True):
            raise click.ClickException(
                f"API port {effective_port} is already serving an unregistered service. "
                "Stop it or choose another port. Use --no-server only when you "
                "intend to use an externally managed Nivos backend."
            )
        console.print(
            f"[green]Reusing the API server already running on port {effective_port}.[/green]"
        )
        no_server = True

    launched_api = not no_server
    if launched_api:
        uv = shutil.which("uv")
        if uv is None:
            raise click.ClickException(
                "uv is required to start the API with desktop dependencies. "
                "Install uv or use --no-server with an already-running API."
            )
        server = subprocess.run(
            [
                uv,
                "run",
                "--extra",
                "desktop",
                "nivos",
                "start",
                "--port",
                str(effective_port),
            ],
            cwd=frontend.parent,
            check=False,
        )
        if server.returncode != 0:
            raise click.ClickException("Could not start the Nivos API server.")

    env = os.environ.copy()
    # Let browser requests use Vite's same-origin proxy at any frontend port.
    # VITE_API_URL is exposed to browser code, so clear an inherited override.
    env["VITE_API_URL"] = ""
    env["NIVOS_VITE_PROXY_TARGET"] = f"http://127.0.0.1:{effective_port}"
    spawn_kwargs = {}
    if sys.platform == "win32":
        spawn_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        spawn_kwargs["start_new_session"] = True
    process = subprocess.Popen(
        [
            npm,
            "run",
            "dev",
            "--",
            "--host",
            "127.0.0.1",
            "--port",
            str(frontend_port),
            "--strictPort",
        ],
        cwd=frontend,
        env=env,
        **spawn_kwargs,
    )
    owner = _FrontendOwner(process, frontend_port)
    shutdown = owner.shutdown
    handlers = {}
    atexit.register(shutdown)
    try:
        for signum in (signal.SIGTERM, getattr(signal, "SIGBREAK", None)):
            if signum is not None:
                with contextlib.suppress(OSError, ValueError):
                    previous = signal.getsignal(signum)
                    signal.signal(signum, lambda *_: sys.exit(1))
                    handlers[signum] = previous
        owner.register()
        if not _wait_for_port(
            process, "127.0.0.1", frontend_port, on_poll=owner.refresh
        ):
            raise click.ClickException(
                "The graphical frontend did not start on the requested port."
            )
        owner.refresh()
        url = f"http://127.0.0.1:{frontend_port}"
        console.print(f"[green]Nivos graphical mode is ready (dev):[/green] {url}")
        if not no_browser:
            webbrowser.open(url)

        watch_api = _api_healthy(effective_port)
        failures = 0
        while True:
            owner.refresh()
            try:
                process.wait(timeout=_API_POLL_SECONDS)
                break
            except subprocess.TimeoutExpired:
                pass
            healthy = _api_healthy(effective_port)
            if healthy:
                watch_api = True
                failures = 0
                continue
            if not watch_api and not launched_api:
                continue
            failures += 1
            if failures < _API_FAILURES_BEFORE_DOWN or _daemon_pid_alive(effective_port):
                continue
            console.print(
                "[red]The API server stopped or failed to start; "
                "shutting down the frontend.[/red]\n"
                f"  Check {DEFAULT_CONFIG_DIR / 'server.log'}, "
                f"then run 'nivos gui' again."
            )
            raise SystemExit(1)
    except KeyboardInterrupt:
        pass
    finally:
        atexit.unregister(shutdown)
        for signum, previous in handlers.items():
            with contextlib.suppress(OSError, ValueError):
                signal.signal(signum, previous)
        shutdown()
