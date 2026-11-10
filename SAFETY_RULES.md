# Safety Rules for Nivos Development

These rules are strictly enforced for all automated, agentic, and developer operations within this repository:

1. **Branch Isolation**:
   - Always work on dedicated feature branches (e.g., `feat/*`, `fix/*`).
   - Never commit, push, merge, or tag directly to `master` or `main`.

2. **Central Safety Model (Origin & Content Trust)**:
   - **Request Origin**: Every request entering Nivos is tagged with an origin: `local_chat` (typed by user in local web/desktop chat), `scheduled_task`, `slack`, or `channel`.
   - **Untrusted Content**: External text (web search snippets, notes from vaults, files, Slack messages) is treated as untrusted data and fenced as untrusted.
   - **Dangerous Tools**: `sandboxed_shell`, `docker_shell`, `open_app`, `self_improve`, and file writes outside `workspace/` and `Nivos-Inbox/` are classified as dangerous.
   - **Execution Invariant**: Dangerous tools can ONLY run on turns typed by the user in `local_chat`. They ALWAYS require explicit user approval.
   - **Channel & Schedule Prohibition**: Dangerous tools are NEVER available to scheduled tasks, Slack, external channels, or turns derived from untrusted content.
   - **Channel Policy & Prohibitions**:
     - Messaging channels (Slack, Telegram) are restricted strictly to read-only tools: `web_search`, `get_weather`, `search_vault`, `calculator`, and `time`.
     - Slack requires the minimal DM-only manifest (`im:*`, `chat:write`) and an allow-list of only the user's own Slack user ID.
     - Telegram requires private chats only (strictly ignores groups) and an allow-list of only the user's own Telegram user ID.
     - Approvals can NEVER be granted from a channel; approvals require interactive local chat turn confirmation.
     - Channel notifications on task finish or approval request must be sanitized with zero secrets or raw file contents.
     - iMessage and SMS (SendBlue) are excluded from registries, UI, and config.
   - **Central Policy Engine**: All tool executions are validated by `src/nivos/security/policy.py:is_tool_allowed`.

3. **Vault & Memory Integrity**:
   - `me.md` and `Nivos-Memory/me.md` are strictly read-only for the agent and can never be written, edited, or deleted under any circumstances.
   - Vault writes are restricted strictly to `Nivos-Inbox/` for creating NEW notes only (no overwrite, no delete, no path traversal).

4. **Docker Sandbox & Host Execution Invariants**:
   - When Docker is active, shell tools execute in an isolated Ubuntu container:
     - 2 GB memory limit, CPU limit, all capabilities dropped (`--cap-drop=ALL`).
     - Non-root user (1000:1000), no privileged mode, bridge network (never `--network host`), no Docker socket.
     - Mounts ONLY `workspace/`. Never mount vault, config directory, keys, or tokens; never pass secrets in environment variables.
     - pip, npm, and apt installs permitted strictly inside container.
     - Container stops automatically after 300s of idle inactivity.
   - Per-session auto-approve toggle for sandbox execution NEVER applies to host commands.
   - If Docker is offline, execution falls back to host workspace shell (`workspace/` only, `git/pip/npm` only) and ALWAYS requires explicit user confirmation.

5. **Secrets & Network Restraint**:
   - Never log, display, or leak API keys, tokens, or credentials.
   - No new outbound network connections except where explicitly specified by an approved phase.

6. **Testing & Verification**:
   - Run verification tests after every phase or feature:
     - Frontend: `npm test` and `npm run build` in `frontend/`.
     - Backend: `uv run nivos doctor` and relevant pytest suites.
   - Ensure clean builds and no regressions before committing changes.

7. **License & Integrity**:
   - Never edit or tamper with `LICENSE` (Apache 2.0).
   - Maintain documentation integrity and preserve existing code comments.
