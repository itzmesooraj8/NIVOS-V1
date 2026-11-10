"""App launcher routes — application settings, discovery, and allow/ask management for open_app."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from nivos.core.config import load_config
from nivos.server.routes import _persist_config_section
from nivos.system.app_discovery import get_all_installed_apps

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/apps", tags=["apps"])


class AllowedAppUpdateReq(BaseModel):
    app: str = Field(..., description="Application name")
    action: str = Field("add", description="'add' or 'remove'")


class AppSettingsUpdateReq(BaseModel):
    open_any_installed_app: Optional[bool] = Field(None, description="Allow opening any safe installed app")
    always_ask_apps: Optional[List[str]] = Field(None, description="Apps that always require approval")
    allowed_apps: Optional[List[str]] = Field(None, description="Pre-approved allow list")


@router.get("/settings")
async def get_app_settings(request: Request) -> Dict[str, Any]:
    """Return complete app launcher settings."""
    cfg = load_config()
    open_any = getattr(cfg.security, "open_any_installed_app", True)
    always_ask = getattr(cfg.security, "always_ask_apps", None)
    if always_ask is None:
        always_ask = [
            "1password",
            "bitwarden",
            "keepass",
            "lastpass",
            "bank",
            "remote desktop",
            "teamviewer",
            "anydesk",
        ]
    allowed = getattr(cfg.security, "allowed_apps", []) or []

    return {
        "open_any_installed_app": bool(open_any),
        "always_ask_apps": sorted(list(set(a.lower() for a in always_ask))),
        "allowed_apps": sorted(list(set(a.lower() for a in allowed))),
        "blocklist": [
            "cmd",
            "powershell",
            "pwsh",
            "bash",
            "sh",
            "wsl",
            "wscript",
            "cscript",
            "mshta",
            "rundll32",
            "regedit",
            "msiexec",
            "installers (*.msi, *setup*)",
        ],
    }


@router.post("/settings")
async def update_app_settings(req: AppSettingsUpdateReq, request: Request) -> Dict[str, Any]:
    """Update and persist app launcher settings to config.toml."""
    cfg = load_config()
    current_sec = getattr(cfg, "security", None)

    updates: Dict[str, Any] = {}

    if req.open_any_installed_app is not None:
        updates["open_any_installed_app"] = bool(req.open_any_installed_app)

    if req.always_ask_apps is not None:
        cleaned_ask = sorted(list(set(a.strip().lower() for a in req.always_ask_apps if a.strip())))
        updates["always_ask_apps"] = cleaned_ask

    if req.allowed_apps is not None:
        cleaned_allowed = sorted(list(set(a.strip().lower() for a in req.allowed_apps if a.strip())))
        updates["allowed_apps"] = cleaned_allowed

    if updates:
        try:
            _persist_config_section("security", updates)
        except Exception as exc:
            logger.warning("Failed to persist app settings: %s", exc)
            raise HTTPException(status_code=500, detail=f"Failed to persist settings: {exc}")

    # Clear cache and return fresh settings
    load_config.cache_clear()
    cfg_fresh = load_config()
    return {
        "open_any_installed_app": getattr(cfg_fresh.security, "open_any_installed_app", True),
        "always_ask_apps": getattr(cfg_fresh.security, "always_ask_apps", []),
        "allowed_apps": getattr(cfg_fresh.security, "allowed_apps", []),
    }


@router.get("/installed")
async def get_installed_apps(request: Request) -> Dict[str, Any]:
    """Return all discovered applications installed on the system."""
    apps = get_all_installed_apps()
    return {"installed_apps": apps, "total": len(apps)}


@router.get("/allowed")
async def get_allowed_apps(request: Request) -> Dict[str, Any]:
    """Return the list of applications pre-approved to launch without prompt."""
    cfg = load_config()
    apps = getattr(cfg.security, "allowed_apps", []) or []
    return {"allowed_apps": sorted(list(set(a.lower() for a in apps)))}


@router.post("/allowed")
async def update_allowed_app(req: AllowedAppUpdateReq, request: Request) -> Dict[str, Any]:
    """Add or remove an application from the pre-approved allow-list."""
    app_clean = req.app.strip().lower()
    if not app_clean:
        raise HTTPException(status_code=400, detail="App name is required.")

    cfg = load_config()
    current_apps = set(getattr(cfg.security, "allowed_apps", []) or [])

    if req.action == "remove":
        current_apps.discard(app_clean)
    else:
        current_apps.add(app_clean)

    updated_list = sorted(list(current_apps))
    try:
        _persist_config_section("security", {"allowed_apps": updated_list})
    except Exception as exc:
        logger.warning("Failed to persist allowed_apps: %s", exc)

    return {"allowed_apps": updated_list}
