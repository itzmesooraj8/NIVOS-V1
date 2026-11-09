"""Slack channel configuration and lifecycle routes (Phase 5)."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from nivos.channels.slack_daemon import is_running, start_slack_daemon, stop_daemon
from nivos.core.config import load_config
from nivos.server.routes import _persist_config_section

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/channels/slack", tags=["slack"])

# Minimal DM-only Slack App Manifest
MINIMAL_DM_MANIFEST = {
    "display_information": {
        "name": "Nivos"
    },
    "features": {
        "app_home": {
            "home_tab_enabled": True,
            "messages_tab_enabled": True,
            "messages_tab_read_only_enabled": False,
        },
        "bot_user": {
            "display_name": "Nivos",
            "always_online": True,
        },
    },
    "oauth_config": {
        "scopes": {
            "bot": [
                "chat:write",
                "im:history",
                "im:read",
                "im:write",
            ]
        }
    },
    "settings": {
        "event_subscriptions": {
            "bot_events": ["message.im"]
        },
        "socket_mode_enabled": True,
    },
}


class SlackConfigUpdate(BaseModel):
    bot_token: Optional[str] = Field(None, description="Slack Bot User OAuth Token (xoxb-...)")
    app_token: Optional[str] = Field(None, description="Slack App-Level Token (xapp-...)")
    allowed_user_id: Optional[str] = Field(None, description="Single Slack Member ID permitted to talk to the agent.")


@router.get("/config")
async def get_slack_config(request: Request) -> Dict[str, Any]:
    """Return Slack channel configuration with tokens masked, never logged in cleartext."""
    cfg = load_config()
    ch_cfg = getattr(cfg, "channel", None)
    slack_cfg = getattr(ch_cfg, "slack", None) if ch_cfg else None

    bot_token = getattr(slack_cfg, "bot_token", "") if slack_cfg else ""
    app_token = getattr(slack_cfg, "app_token", "") if slack_cfg else ""
    allowed_user_id = getattr(slack_cfg, "allowed_user_id", "") if slack_cfg else ""

    running = is_running()

    return {
        "has_bot_token": bool(bot_token),
        "has_app_token": bool(app_token),
        "allowed_user_id": allowed_user_id,
        "daemon_running": running,
        "manifest": MINIMAL_DM_MANIFEST,
    }


@router.post("/config")
async def update_slack_config(req: SlackConfigUpdate, request: Request) -> Dict[str, Any]:
    """Update and persist Slack tokens and single-user allowlist in local config.toml."""
    cfg = load_config()
    ch_cfg = getattr(cfg, "channel", None)
    slack_cfg = getattr(ch_cfg, "slack", None) if ch_cfg else None

    current_bot = getattr(slack_cfg, "bot_token", "") if slack_cfg else ""
    current_app = getattr(slack_cfg, "app_token", "") if slack_cfg else ""
    current_uid = getattr(slack_cfg, "allowed_user_id", "") if slack_cfg else ""

    new_bot = req.bot_token.strip() if req.bot_token is not None and req.bot_token.strip() else current_bot
    new_app = req.app_token.strip() if req.app_token is not None and req.app_token.strip() else current_app
    new_uid = req.allowed_user_id.strip() if req.allowed_user_id is not None else current_uid

    try:
        _persist_config_section(
            "channel.slack",
            {
                "bot_token": new_bot,
                "app_token": new_app,
                "allowed_user_id": new_uid,
            },
        )
        logger.info("Updated Slack configuration (tokens masked, allowlist: %s)", new_uid)
    except Exception as exc:
        logger.warning("Failed to persist Slack config: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to persist Slack settings: {exc}")

    return {
        "success": True,
        "has_bot_token": bool(new_bot),
        "has_app_token": bool(new_app),
        "allowed_user_id": new_uid,
    }


@router.post("/start")
async def start_slack(request: Request) -> Dict[str, Any]:
    """Start the Slack daemon using saved credentials."""
    cfg = load_config()
    ch_cfg = getattr(cfg, "channel", None)
    slack_cfg = getattr(ch_cfg, "slack", None) if ch_cfg else None
    bot_token = getattr(slack_cfg, "bot_token", "") if slack_cfg else ""
    app_token = getattr(slack_cfg, "app_token", "") if slack_cfg else ""
    allowed_user_id = getattr(slack_cfg, "allowed_user_id", "") if slack_cfg else ""

    if not bot_token or not app_token:
        raise HTTPException(status_code=400, detail="Bot token and App token must be configured before starting Slack.")

    try:
        stop_daemon()
        pid = start_slack_daemon(
            bot_token=bot_token,
            app_token=app_token,
            allowed_user_id=allowed_user_id,
        )
        return {"success": True, "pid": pid, "daemon_running": True}
    except Exception as exc:
        logger.warning("Failed starting Slack daemon: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to start Slack daemon: {exc}")


@router.post("/stop")
async def stop_slack_endpoint(request: Request) -> Dict[str, Any]:
    """Stop the running Slack daemon."""
    stopped = stop_daemon()
    return {"success": stopped, "daemon_running": False}

