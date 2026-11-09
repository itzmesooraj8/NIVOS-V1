"""Slack Socket Mode daemon — listens for DMs and responds with DeepResearch.

Run as a standalone process or import start_slack_daemon() to spawn
from the server. Uses slack-bolt for reliable Socket Mode handling.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import sys
from pathlib import Path
from typing import Any

from nivos.core.paths import get_config_dir

logger = logging.getLogger(__name__)

_PID_FILE = str(get_config_dir() / "slack-daemon.pid")


def _to_slack_fmt(text: str) -> str:
    """Convert markdown to Slack mrkdwn format."""
    # Headers → bold
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    # Bold: **text** → *text*
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    # Strikethrough: ~~text~~ → ~text~
    text = re.sub(r"~~(.+?)~~", r"~\1~", text)
    # Links: [text](url) → <url|text>
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r"<\2|\1>", text)
    # Remove LaTeX
    text = re.sub(r"\$\$.+?\$\$", "", text)
    text = re.sub(r"\$(.+?)\$", r"\1", text)
    # Clean whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def run_slack_daemon(
    bot_token: str,
    app_token: str,
    allowed_user_id: str = "",
    model: str = "qwen3.5:9b",
) -> None:
    """Run the Slack daemon (blocking). Handles DMs strictly for allow-listed user."""
    import threading
    import time

    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    from nivos.agents.deep_research import DeepResearchAgent
    from nivos.core.config import load_config
    from nivos.core.events import EventBus, EventType
    from nivos.engine.ollama import OllamaEngine
    from nivos.security import setup_security
    from nivos.security.policy import SAFE_READONLY_TOOLS, RequestOrigin
    from nivos.server.agent_manager_routes import (
        _build_deep_research_tools,
    )

    # Write PID
    pid_path = Path(_PID_FILE)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()))

    # Build agent with strictly read-only tools
    config = load_config()
    bus = EventBus(record_history=False)
    security = setup_security(config, OllamaEngine(), bus)
    engine = security.engine
    raw_tools = _build_deep_research_tools(engine=engine, model=model)
    # Strictly filter down to SAFE_READONLY_TOOLS
    tools = [
        t for t in raw_tools
        if getattr(t, "tool_id", getattr(t, "name", "")) in SAFE_READONLY_TOOLS
    ]
    agent = DeepResearchAgent(
        engine=engine,
        model=model,
        tools=tools,
        max_turns=5,
        bus=bus,
        capability_policy=security.capability_policy,
        rate_limiter=security.rate_limiter,
        agent_id="channel:slack",
    )
    logger.info("Slack daemon: agent ready with %d read-only tools (allowlist user: %s)", len(tools), allowed_user_id)

    app = App(token=bot_token)

    processing = threading.Event()
    last_message_time = 0.0
    rate_lock = threading.Lock()

    @app.event("message")
    def handle_dm(event: dict, say: Any) -> None:
        nonlocal last_message_time
        user_id = event.get("user", "")
        ch_type = event.get("channel_type", "")
        ch_id = event.get("channel", "")

        is_dm = ch_type == "im" or ch_id.startswith("D")
        is_bot = "bot_id" in event or event.get("subtype") == "bot_message"

        if not is_dm or is_bot:
            return

        # Check single-user allowlist
        if allowed_user_id and user_id != allowed_user_id:
            logger.warning(
                "Slack DM from unauthorized user %s ignored (allow-listed: %s)",
                user_id,
                allowed_user_id,
            )
            return

        # Rate limiting: 1 message per 1.5s
        with rate_lock:
            now = time.time()
            if now - last_message_time < 1.5:
                logger.warning("Slack DM rate limited for user %s", user_id)
                return
            last_message_time = now

        text = event.get("text", "")
        if not text:
            return

        ts = event.get("ts", "")
        logger.info("Slack DM from %s: %s", user_id, text[:60])

        bus.publish(
            EventType.CHANNEL_MESSAGE_RECEIVED,
            {
                "channel": "slack",
                "sender": user_id,
                "content": text,
                "message_id": ts,
                "origin": "slack",
                "is_untrusted_content": True,
            },
        )

        say(text="Message received! Working on it now...", thread_ts=ts)
        processing.set()

        # Progress updater
        stop = threading.Event()

        def _progress() -> None:
            while not stop.is_set():
                stop.wait(60)
                if stop.is_set():
                    break
                if processing.is_set():
                    say(
                        text="Still working! Will reply ASAP",
                        thread_ts=ts,
                    )

        pt = threading.Thread(target=_progress, daemon=True)
        pt.start()

        try:
            result = agent.run(text)
            reply = _to_slack_fmt(result.content or "No results found.")
        except Exception as exc:
            reply = f"Error: {exc}"
            logger.error("Slack daemon error: %s", exc)
        finally:
            processing.clear()
            stop.set()

        say(text=reply, thread_ts=ts)
        logger.info("Slack DM reply sent (%d chars)", len(reply))

    handler = SocketModeHandler(app, app_token)

    # Graceful shutdown
    def _stop(signum: int, frame: Any) -> None:
        logger.info("Slack daemon stopping...")
        handler.close()
        if pid_path.exists():
            pid_path.unlink()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    logger.info("Slack daemon started, listening for DMs...")
    handler.start()  # Blocks until close()


def start_slack_daemon(
    bot_token: str,
    app_token: str,
    allowed_user_id: str = "",
    model: str = "qwen3.5:9b",
) -> int:
    """Spawn the Slack daemon as a background subprocess.

    Returns the PID of the spawned process.
    """
    import subprocess

    cmd = [
        sys.executable,
        "-m",
        "nivos.channels.slack_daemon",
        "--bot-token",
        bot_token,
        "--app-token",
        app_token,
        "--model",
        model,
    ]
    if allowed_user_id:
        cmd.extend(["--allowed-user-id", allowed_user_id])

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return proc.pid


def is_running() -> bool:
    """Check if the Slack daemon is running."""
    pid_path = Path(_PID_FILE)
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        pid_path.unlink(missing_ok=True)
        return False


def stop_daemon() -> bool:
    """Stop the running Slack daemon."""
    pid_path = Path(_PID_FILE)
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        pid_path.unlink(missing_ok=True)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        pid_path.unlink(missing_ok=True)
        return False


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--bot-token", required=True)
    parser.add_argument("--app-token", required=True)
    parser.add_argument("--allowed-user-id", default="")
    parser.add_argument("--model", default="qwen3.5:9b")
    args = parser.parse_args()

    run_slack_daemon(
        bot_token=args.bot_token,
        app_token=args.app_token,
        allowed_user_id=args.allowed_user_id,
        model=args.model,
    )
