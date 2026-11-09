"""CLI command to create Desktop and Start menu shortcuts for Nivos."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console

from nivos.core.config import DEFAULT_CONFIG_DIR

logger = logging.getLogger(__name__)
console = Console()


def find_browser_executable() -> tuple[Optional[Path], str]:
    """Find Edge or Chrome executable path, or return None for default browser."""
    # Check Edge
    edge_paths = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Microsoft"
        / "Edge"
        / "Application"
        / "msedge.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Microsoft"
        / "Edge"
        / "Application"
        / "msedge.exe",
    ]
    for p in edge_paths:
        if p.is_file():
            return p, "msedge"

    which_edge = shutil.which("msedge.exe") or shutil.which("msedge")
    if which_edge:
        return Path(which_edge), "msedge"

    # Check Chrome
    chrome_paths = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
    ]
    for p in chrome_paths:
        if p.is_file():
            return p, "chrome"

    which_chrome = shutil.which("chrome.exe") or shutil.which("chrome")
    if which_chrome:
        return Path(which_chrome), "chrome"

    return None, "default"


def setup_launcher_files(icon_src: Optional[Path] = None) -> tuple[Path, Path]:
    """Create persistent launch-nivos.ps1, launch-nivos.vbs, and nivos-icon.ico in ~/.nivos."""
    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Icon
    icon_dst = DEFAULT_CONFIG_DIR / "nivos-icon.ico"
    if icon_src and icon_src.is_file():
        shutil.copy2(icon_src, icon_dst)
    elif not icon_dst.exists():
        repo_icon = Path(__file__).resolve().parents[3] / "assets" / "nivos-icon.ico"
        if repo_icon.is_file():
            shutil.copy2(repo_icon, icon_dst)

    # 2. PowerShell launcher
    ps1_path = DEFAULT_CONFIG_DIR / "launch-nivos.ps1"
    ps1_content = """# Nivos launcher: checks if server is active, starts scheduled task if needed, opens app
$port = 8000
$running = $false
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $iar = $tcp.BeginConnect("127.0.0.1", $port, $null, $null)
    $wait = $iar.AsyncWaitHandle.WaitOne(500, $false)
    if ($wait -and $tcp.Connected) {
        $running = $true
    }
    $tcp.Close()
} catch {}

if (-not $running) {
    try {
        Start-ScheduledTask -TaskName nivos -ErrorAction SilentlyContinue
    } catch {
        schtasks /run /tn nivos 2>$null
    }
    # Wait briefly for port 8000
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 200
        try {
            $tcp = New-Object System.Net.Sockets.TcpClient
            $iar = $tcp.BeginConnect("127.0.0.1", $port, $null, $null)
            $wait = $iar.AsyncWaitHandle.WaitOne(300, $false)
            if ($wait -and $tcp.Connected) {
                $tcp.Close()
                break
            }
            $tcp.Close()
        } catch {}
    }
}

# Locate Edge or Chrome for app mode
$edge = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"
if (-not (Test-Path $edge)) { $edge = "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe" }
$chrome = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
if (-not (Test-Path $chrome)) { $chrome = "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe" }

if (Test-Path $edge) {
    Start-Process $edge -ArgumentList "--app=http://127.0.0.1:8000"
} elseif (Test-Path $chrome) {
    Start-Process $chrome -ArgumentList "--app=http://127.0.0.1:8000"
} else {
    Start-Process "http://127.0.0.1:8000"
}
"""
    ps1_path.write_text(ps1_content, encoding="utf-8")

    # 3. VBS silent launcher (runs ps1 with WindowStyle Hidden, 0 window flash)
    vbs_path = DEFAULT_CONFIG_DIR / "launch-nivos.vbs"
    ps1_escaped = str(ps1_path).replace('"', '""')
    vbs_content = (
        'Set WshShell = CreateObject("WScript.Shell")\r\n'
        f'WshShell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{ps1_escaped}""", 0, False\r\n'
    )
    vbs_path.write_text(vbs_content, encoding="utf-8")

    return vbs_path, icon_dst


def get_desktop_dirs() -> list[Path]:
    """Return user Desktop directories (including OneDrive redirected desktop if present)."""
    desktops: list[Path] = []
    try:
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        p_str = res.stdout.strip()
        if p_str and Path(p_str).is_dir():
            desktops.append(Path(p_str))
    except Exception:
        pass

    user_home = Path.home()
    onedrive_desktop = user_home / "OneDrive" / "Desktop"
    if onedrive_desktop.is_dir() and onedrive_desktop not in desktops:
        desktops.append(onedrive_desktop)

    standard_desktop = user_home / "Desktop"
    if standard_desktop.is_dir() and standard_desktop not in desktops:
        desktops.append(standard_desktop)

    return desktops


def get_start_menu_dir() -> Path:
    """Return the user's Start Menu Programs directory."""
    try:
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Programs')"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        p_str = res.stdout.strip()
        if p_str and Path(p_str).is_dir():
            return Path(p_str)
    except Exception:
        pass

    appdata = os.environ.get("APPDATA")
    if appdata:
        p = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if p.is_dir():
            return p
    return Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def create_windows_shortcut(
    shortcut_path: Path,
    target_path: Path,
    arguments: str = "",
    icon_path: Optional[Path] = None,
    description: str = "Nivos - On-Device AI Assistant",
) -> bool:
    """Create a Windows .lnk shortcut via PowerShell WScript.Shell."""
    try:
        clean_args = arguments.replace("'", "''")
        lines = [
            "$w = New-Object -ComObject WScript.Shell",
            f"$s = $w.CreateShortcut('{str(shortcut_path)}')",
            f"$s.TargetPath = '{str(target_path)}'",
            f"$s.Arguments = '{clean_args}'",
            f"$s.Description = '{description}'",
        ]
        if icon_path and icon_path.exists():
            lines.append(f"$s.IconLocation = '{str(icon_path)},0'")
        lines.append("$s.Save()")
        script = "\r\n".join(lines)
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "-"],
            input=script,
            text=True,
            capture_output=True,
            timeout=10,
        )
        if res.returncode != 0:
            logger.warning("PowerShell error creating shortcut: %s", res.stderr)
            return False
        return shortcut_path.is_file()
    except Exception as exc:
        logger.warning("Failed to create shortcut at %s: %s", shortcut_path, exc)
        return False


@click.command("install-shortcut")
@click.option("--force", is_flag=True, default=False, help="Overwrite existing shortcuts")
def install_shortcut(force: bool) -> None:
    """Install Nivos Desktop and Start menu shortcuts with Edge/Chrome app mode."""
    if sys.platform != "win32":
        console.print("[yellow]Shortcut creation is currently supported on Windows.[/yellow]")
        return

    console.print("[bold cyan]Installing Nivos shortcuts...[/bold cyan]")

    browser_path, browser_name = find_browser_executable()
    if browser_name in ("msedge", "chrome"):
        console.print(f"  [green]✓[/green] Detected browser for app mode: [bold]{browser_name}[/bold]")
    else:
        console.print("  [yellow]![/yellow] Neither Edge nor Chrome detected; using default browser launcher")

    # Set up launcher and icon
    repo_root = Path(__file__).resolve().parents[3]
    icon_src = repo_root / "assets" / "nivos-icon.ico"
    vbs_path, icon_path = setup_launcher_files(icon_src=icon_src if icon_src.is_file() else None)

    created_any = False

    # 1. Desktop shortcut
    for dt in get_desktop_dirs():
        dt_shortcut = dt / "Nivos.lnk"
        if dt_shortcut.exists() and not force:
            console.print(f"  [dim]• Desktop shortcut already exists at: {dt_shortcut}[/dim]")
        else:
            success = create_windows_shortcut(
                shortcut_path=dt_shortcut,
                target_path=Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wscript.exe",
                arguments=f'"{vbs_path}"',
                icon_path=icon_path,
            )
            if success:
                console.print(f"  [green]✓[/green] Created Desktop shortcut: [bold]{dt_shortcut}[/bold]")
                created_any = True

    # 2. Start Menu shortcut
    start_menu = get_start_menu_dir()
    start_menu.mkdir(parents=True, exist_ok=True)
    start_shortcut = start_menu / "Nivos.lnk"
    if start_shortcut.exists() and not force:
        console.print(f"  [dim]• Start menu shortcut already exists at: {start_shortcut}[/dim]")
    else:
        success = create_windows_shortcut(
            shortcut_path=start_shortcut,
            target_path=Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wscript.exe",
            arguments=f'"{vbs_path}"',
            icon_path=icon_path,
        )
        if success:
            console.print(f"  [green]✓[/green] Created Start menu shortcut: [bold]{start_shortcut}[/bold]")
            created_any = True

    console.print("\n[bold green]✓ Nivos shortcuts successfully configured.[/bold green]")
    console.print("  Launches [cyan]http://127.0.0.1:8000[/cyan] in app window and starts the background task if needed.\n")
