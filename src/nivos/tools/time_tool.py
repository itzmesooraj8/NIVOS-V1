"""Time tool — Instant local time, date, and timezone query."""

from __future__ import annotations

from datetime import datetime
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

from nivos.core.config import load_config
from nivos.core.registry import ToolRegistry
from nivos.core.types import ToolResult
from nivos.tools._stubs import BaseTool, ToolSpec


@ToolRegistry.register("get_current_time")
class CurrentTimeTool(BaseTool):
    """Instant current time, date, and timezone tool."""

    tool_id = "get_current_time"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="get_current_time",
            description=(
                "Get the exact current local time, date, day of the week, and timezone. "
                "Instant execution with no external network or model call."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": (
                            "Optional timezone name (e.g. 'Asia/Kolkata', 'UTC', 'America/New_York'). "
                            "Defaults to user's configured timezone."
                        ),
                    },
                },
                "required": [],
            },
            category="system",
        )

    def execute(self, **params: Any) -> ToolResult:
        tz_param = (params.get("timezone") or "").strip()
        tz_name = tz_param
        if not tz_name:
            try:
                cfg = load_config()
                user_cfg = getattr(cfg, "user", None)
                if user_cfg and getattr(user_cfg, "timezone", ""):
                    tz_name = user_cfg.timezone
            except Exception:
                pass
        if not tz_name:
            tz_name = "Asia/Kolkata"

        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
            tz_name = "UTC"

        now = datetime.now(tz)
        time_str = now.strftime("%I:%M %p").lstrip("0")
        date_str = now.strftime("%B %d, %Y")
        day_name = now.strftime("%A")

        content = (
            f"Current time: {time_str}\n"
            f"Date: {date_str}\n"
            f"Day of week: {day_name}\n"
            f"Timezone: {tz_name}"
        )
        return ToolResult(
            tool_name="get_current_time",
            content=content,
            metadata={
                "time": time_str,
                "date": date_str,
                "day": day_name,
                "timezone": tz_name,
                "iso": now.isoformat(),
            },
            success=True,
        )


__all__ = ["CurrentTimeTool"]

