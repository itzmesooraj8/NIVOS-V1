"""Windows application discovery and matching for open_app and Settings UI.

Auto-discovers installed applications via:
1. Windows Start Menu (.lnk shortcuts in User and System Programs directories)
2. Safe built-in Windows applications (Notepad, Calculator, Paint, File Explorer)
3. Windows Package Manager (winget list) with TTL caching
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Standard built-in safe Windows utility apps
_SAFE_WINDOWS_APPS: Dict[str, str] = {
    "notepad": "notepad.exe",
    "calc": "calc.exe",
    "calculator": "calc.exe",
    "mspaint": "mspaint.exe",
    "paint": "mspaint.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "cmd": "cmd.exe",  # included only for lookup; blocked by policy
    "powershell": "powershell.exe",
}

# Whitelisted URL schemes
_ALLOWED_SCHEMES = ("ms-settings:", "spotify:", "mailto:")

# Cache for winget discovered packages (5 min TTL)
_WINGET_CACHE: List[Dict[str, str]] = []
_WINGET_CACHE_TIME: float = 0.0
_CACHE_TTL_SECONDS: float = 300.0

# Ignore non-application shortcut titles
_IGNORE_SHORTCUT_PATTERNS = re.compile(
    r"\b(uninstall|read\s*me|help|documentation|website|release\s*notes|manual|setup|license)\b",
    re.IGNORECASE,
)


def get_start_menu_apps() -> List[Dict[str, str]]:
    """Scan User and System Start Menu directories for desktop application shortcuts (.lnk)."""
    search_dirs: List[Path] = []

    appdata = os.environ.get("APPDATA")
    if appdata:
        p1 = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if p1.is_dir():
            search_dirs.append(p1)

    progdata = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    if progdata:
        p2 = Path(progdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if p2.is_dir():
            search_dirs.append(p2)

    discovered: Dict[str, Dict[str, str]] = {}

    for sdir in search_dirs:
        for root, _, files in os.walk(sdir):
            for f in files:
                if not f.lower().endswith(".lnk"):
                    continue
                stem = f[:-4].strip()
                if not stem or _IGNORE_SHORTCUT_PATTERNS.search(stem):
                    continue
                lower_name = stem.lower()
                if lower_name not in discovered:
                    discovered[lower_name] = {
                        "name": stem,
                        "path": str(Path(root) / f),
                        "source": "start_menu",
                    }

    return sorted(list(discovered.values()), key=lambda x: x["name"].lower())


def get_winget_installed_apps(force_refresh: bool = False) -> List[Dict[str, str]]:
    """Discover installed applications using winget list, with in-memory caching."""
    global _WINGET_CACHE, _WINGET_CACHE_TIME

    now = time.time()
    if not force_refresh and _WINGET_CACHE and (now - _WINGET_CACHE_TIME) < _CACHE_TTL_SECONDS:
        return _WINGET_CACHE

    if not shutil.which("winget"):
        return []

    try:
        proc = subprocess.run(
            ["winget", "list", "--accept-source-agreements", "--disable-interactivity"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if proc.returncode != 0:
            return _WINGET_CACHE

        lines = proc.stdout.splitlines()
        if len(lines) < 3:
            return _WINGET_CACHE

        # Locate header separator
        header_idx = -1
        for i, line in enumerate(lines[:10]):
            if line.startswith("---") or "---" in line:
                header_idx = i
                break

        results: List[Dict[str, str]] = []
        if header_idx > 0 and header_idx + 1 < len(lines):
            # Parse rows following separator
            for line in lines[header_idx + 1 :]:
                parts = line.split()
                if len(parts) >= 2:
                    # Approximation: first word(s) is Name, followed by ID containing dot or letters
                    name = parts[0]
                    pkg_id = parts[1] if len(parts) > 1 else ""
                    ver = parts[2] if len(parts) > 2 else ""
                    results.append({
                        "name": name,
                        "id": pkg_id,
                        "version": ver,
                        "source": "winget",
                    })

        _WINGET_CACHE = results
        _WINGET_CACHE_TIME = now
        return results
    except Exception as exc:
        logger.debug("Failed scanning winget list: %s", exc)
        return _WINGET_CACHE


def get_all_installed_apps() -> List[Dict[str, Any]]:
    """Return unified deduplicated list of discovered applications."""
    seen_names: Set[str] = set()
    unified: List[Dict[str, Any]] = []

    # 1. Start menu shortcuts
    for item in get_start_menu_apps():
        lname = item["name"].lower()
        if lname not in seen_names:
            seen_names.add(lname)
            unified.append(item)

    # 2. Safe Windows utilities
    safe_builtins = [
        {"name": "Notepad", "exe": "notepad.exe", "source": "windows_builtin"},
        {"name": "Calculator", "exe": "calc.exe", "source": "windows_builtin"},
        {"name": "Paint", "exe": "mspaint.exe", "source": "windows_builtin"},
        {"name": "File Explorer", "exe": "explorer.exe", "source": "windows_builtin"},
    ]
    for b in safe_builtins:
        lname = b["name"].lower()
        if lname not in seen_names:
            seen_names.add(lname)
            unified.append(b)

    # 3. Winget apps (add if not already discovered by Start menu)
    for w in get_winget_installed_apps():
        lname = w["name"].lower()
        if lname not in seen_names and len(w["name"]) > 2:
            seen_names.add(lname)
            unified.append(w)

    return sorted(unified, key=lambda x: str(x.get("name", "")).lower())


def find_matching_app(app_query: str) -> Optional[Dict[str, Any]]:
    """Match a user application request (e.g. 'spotify', 'notepad') to an installed application."""
    clean = app_query.strip()
    if not clean:
        return None

    clean_lower = clean.lower()

    # 1. URL Scheme check
    for scheme in _ALLOWED_SCHEMES:
        if clean_lower.startswith(scheme):
            return {
                "name": clean,
                "scheme": clean,
                "source": "scheme",
            }

    # If user passed scheme without colon e.g. "spotify", check if "spotify:" or Spotify app exists
    # 2. Check built-in Windows safe apps
    if clean_lower in _SAFE_WINDOWS_APPS:
        exe = _SAFE_WINDOWS_APPS[clean_lower]
        return {
            "name": clean.capitalize(),
            "exe": exe,
            "source": "windows_builtin",
        }

    # 3. Check Start menu shortcuts
    start_apps = get_start_menu_apps()

    # Exact match first
    for a in start_apps:
        if a["name"].lower() == clean_lower:
            return a

    # Substring / token match (e.g. "spotify" in "Spotify", "chrome" in "Google Chrome")
    for a in start_apps:
        if clean_lower in a["name"].lower() or a["name"].lower() in clean_lower:
            return a

    # 4. If query is "spotify", allow falling back to spotify: scheme
    if clean_lower == "spotify":
        return {
            "name": "Spotify",
            "scheme": "spotify:",
            "source": "scheme",
        }

    return None
