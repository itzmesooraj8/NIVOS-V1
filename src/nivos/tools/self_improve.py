"""Self-improvement agent tool — strictly isolated developer agent.

Rules:
1. Operates ONLY inside dedicated git worktree (C:\\Nivos\\self-dev) on branches self/<topic>.
2. Never works in main folder, never operates on master/main, never merges automatically.
3. Protected path invariant: SAFETY_RULES.md, policy/security, approval code, sandbox/shell/open_app,
   vault guards, and me.md can NEVER be modified.
4. Execution allowed ONLY on LOCAL_CHAT typed by the user, with explicit operator approval.
5. Runs verification tests and proposes lessons to Nivos-Inbox/.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from nivos.core.events import EventType, get_event_bus
from nivos.core.registry import ToolRegistry
from nivos.core.types import ToolResult
from nivos.security.policy import RequestOrigin, is_tool_allowed
from nivos.self_dev.manager import SelfDevManager
from nivos.self_dev.policy import is_path_protected
from nivos.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)


@ToolRegistry.register("self_improve")
class SelfImproveTool(BaseTool):
    """Isolated self-improvement tool operating in C:\\Nivos\\self-dev."""

    name = "self_improve"
    description = (
        "Operates the self-improvement agent in an isolated git worktree (C:\\Nivos\\self-dev). "
        "Creates branches self/<topic>, verifies protected paths invariant, runs tests, "
        "and proposes lessons into Nivos-Inbox/."
    )

    spec = ToolSpec(
        name="self_improve",
        description=description,
        parameters={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Short topic slug for improvement (e.g. 'optimize-indexing')",
                },
                "description": {
                    "type": "string",
                    "description": "Explanation of changes or developer goal",
                },
                "action": {
                    "type": "string",
                    "enum": ["start", "test", "propose_lesson", "discard", "status"],
                    "description": "Action to perform in the worktree",
                    "default": "start",
                },
                "files_to_modify": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of target files to be modified",
                },
                "lesson_content": {
                    "type": "string",
                    "description": "Lesson markdown content to propose to Nivos-Inbox/",
                },
            },
            "required": ["topic"],
        },
    )

    def __init__(self, manager: Optional[SelfDevManager] = None) -> None:
        self.manager = manager or SelfDevManager()

    async def execute(
        self,
        topic: str,
        description: str = "",
        action: str = "start",
        files_to_modify: Optional[List[str]] = None,
        lesson_content: Optional[str] = None,
        origin: RequestOrigin | str = RequestOrigin.LOCAL_CHAT,
        is_untrusted_content: bool = False,
        user_approved: bool = False,
        **kwargs: Any,
    ) -> ToolResult:
        """Execute self-improvement operation with strict safety invariants."""
        # 1. Check central policy
        allowed, reason = is_tool_allowed(
            "self_improve",
            origin=origin,
            is_untrusted_content=is_untrusted_content,
            user_approved=user_approved,
            params={"topic": topic, "action": action},
        )
        if not allowed:
            _log_self_dev_activity(topic, action, status="policy_blocked", error=reason)
            return ToolResult(tool_name="self_improve", content=f"Policy Block: {reason}", success=False)

        # 2. Check protected paths invariant
        if files_to_modify:
            safe, violations = self.manager.validate_paths_for_edit(files_to_modify)
            if not safe:
                err_msg = (
                    "Security Block: Self-improvement is forbidden from modifying protected paths:\n"
                    + "\n".join(f"  • {v}" for v in violations)
                )
                _log_self_dev_activity(topic, action, status="blocked_protected_path", error=err_msg)
                return ToolResult(tool_name="self_improve", content=err_msg, success=False)

        # 3. Handle actions
        clean_action = (action or "start").lower().strip()

        try:
            if clean_action == "start":
                worktree_p, branch = self.manager.ensure_worktree(topic)
                receipt = f"[Receipt: tool=self_improve, branch={branch}, worktree={worktree_p}, status=started]"
                msg = (
                    f"{receipt}\n"
                    f"Initialized self-improvement worktree on branch '{branch}'.\n"
                    f"Location: {worktree_p}\n"
                    "Pre-commit safety hook installed. Main repo and master branch remain untouched."
                )
                _log_self_dev_activity(topic, clean_action, status="started")
                return ToolResult(
                    tool_name="self_improve",
                    content=msg,
                    success=True,
                    metadata={"branch": branch, "worktree": str(worktree_p), "status": "started"},
                )

            elif clean_action == "test":
                worktree_p, branch = self.manager.ensure_worktree(topic)
                test_res = self.manager.run_tests(worktree_p)
                diff_summary = self.manager.get_diff_summary(worktree_p)
                receipt = f"[Receipt: tool=self_improve, branch={branch}, tests={'passed' if test_res['success'] else 'failed'}, status=verified]"
                msg = (
                    f"{receipt}\n"
                    f"Verification for branch '{branch}':\n"
                    f"Test Result: {'PASSED' if test_res['success'] else 'FAILED'} (exit code {test_res['exit_code']})\n"
                    f"Summary:\n{test_res['summary']}\n\n"
                    f"Changes summary:\n{diff_summary[:2000]}"
                )
                _log_self_dev_activity(topic, clean_action, status="tested")
                return ToolResult(
                    tool_name="self_improve",
                    content=msg,
                    success=test_res["success"],
                    metadata={"branch": branch, "tests": test_res},
                )

            elif clean_action == "propose_lesson":
                if not lesson_content:
                    return ToolResult(
                        tool_name="self_improve",
                        content="Missing required 'lesson_content' to propose a lesson.",
                        success=False,
                    )
                lesson_path = self.manager.propose_lesson(
                    topic=topic,
                    lesson_title=description or f"Lesson on {topic}",
                    content=lesson_content,
                )
                receipt = f"[Receipt: tool=self_improve, topic={topic}, lesson_path={lesson_path.name}, status=proposed]"
                msg = (
                    f"{receipt}\n"
                    f"Proposed lesson saved into inbox: {lesson_path}\n"
                    "It will appear in the Memory Review screen for operator approval."
                )
                _log_self_dev_activity(topic, clean_action, status="lesson_proposed")
                return ToolResult(tool_name="self_improve", content=msg, success=True)

            elif clean_action == "discard":
                branch = f"self/{self.manager.sanitize_topic(topic)}"
                self.manager.cleanup_worktree(force=True)
                receipt = f"[Receipt: tool=self_improve, branch={branch}, status=discarded]"
                _log_self_dev_activity(topic, clean_action, status="discarded")
                return ToolResult(
                    tool_name="self_improve",
                    content=f"{receipt}\nDiscarded worktree and changes for topic '{topic}'.",
                    success=True,
                )

            else:  # status
                worktree_p = self.manager.worktree_dir
                exists = worktree_p.exists()
                diff_sum = self.manager.get_diff_summary(worktree_p) if exists else "None"
                receipt = f"[Receipt: tool=self_improve, worktree={worktree_p}, active={exists}, status=status_checked]"
                return ToolResult(
                    tool_name="self_improve",
                    content=f"{receipt}\nWorktree active: {exists}\n{diff_sum}",
                    success=True,
                )

        except Exception as exc:
            logger.exception("Self-improvement tool execution failed: %s", exc)
            _log_self_dev_activity(topic, clean_action, status="failed", error=str(exc))
            return ToolResult(tool_name="self_improve", content=f"Self-improvement failed: {exc}", success=False)


def _log_self_dev_activity(
    topic: str,
    action: str,
    status: str,
    error: Optional[str] = None,
) -> None:
    """Log self-dev operation to activity event stream."""
    try:
        bus = get_event_bus()
        bus.publish(
            EventType.TOOL_EXECUTE,
            {
                "tool": "self_improve",
                "topic": topic,
                "action": action,
                "status": status,
                "error": error,
            },
        )
    except Exception:
        pass


__all__ = ["SelfImproveTool"]
