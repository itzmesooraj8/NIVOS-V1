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
   - **Central Policy Engine**: All tool executions are validated by `src/nivos/security/policy.py:is_tool_allowed`.

3. **Vault & Memory Integrity**:
   - `me.md` and `Nivos-Memory/me.md` are strictly read-only for the agent and can never be written, edited, or deleted under any circumstances.
   - Vault writes are restricted strictly to `Nivos-Inbox/` for creating NEW notes only (no overwrite, no delete, no path traversal).

4. **Secrets & Network Restraint**:
   - Never log, display, or leak API keys, tokens, or credentials.
   - No new outbound network connections except where explicitly specified by an approved phase.

5. **Testing & Verification**:
   - Run verification tests after every phase or feature:
     - Frontend: `npm test` and `npm run build` in `frontend/`.
     - Backend: `uv run nivos doctor` and relevant pytest suites.
   - Ensure clean builds and no regressions before committing changes.

6. **License & Integrity**:
   - Never edit or tamper with `LICENSE` (Apache 2.0).
   - Maintain documentation integrity and preserve existing code comments.
