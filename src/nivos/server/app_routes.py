"""App launcher routes — allowed applications management for open_app."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from nivos.core.config import load_config
from nivos.server.routes import _persist_config_section

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/apps", tags=["apps"])


class AllowedAppUpdateReq(BaseModel):
    app: str = Field(..., description="Application name")
    action: str = Field("add", description="'add' or 'remove'")


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

