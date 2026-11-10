# Safety Rules for Nivos Development

These rules are strictly enforced for all automated, agentic, and developer operations within this repository:

1. **Branch Isolation**:
   - Always work on dedicated feature branches (e.g., `feat/*`, `fix/*`).
   - Never commit, push, merge, or tag directly to `master` or `main`.

2. **Central Safety Model (Origin & Content Trust)**:
   - **Request Origin**: Every request entering Nivos is tagged with an origin: `local_chat` (typed by user in local web/desktop chat), `scheduled_task`, `slack`, or `channel`.
   - **Untrusted Content**: External text (web search snippets, notes from vaults, files, Slack messages) is treated as untrusted data and fenced as untrusted.
   - **Dangerous Tools**: `sandboxed_shell`, `docker_shell`, `open_app`, `install_app`, `run_code`, `self_improve`, and file writes outside `workspace/` and `Nivos-Inbox/` are classified as dangerous.
   - **Execution Invariant**: Dangerous tools can ONLY run on turns typed by the user in `local_chat`. They ALWAYS require explicit user approval.
   - **Channel & Schedule Prohibition**: Dangerous tools are NEVER available to scheduled tasks, Slack, external channels, or turns derived from untrusted content.
   - **Untrusted Content Fencing**: Turns derived from untrusted content trigger fresh interactive user confirmation for dangerous actions even if "always allowed" or generally permitted.
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
     - Mounts strictly `workspace/` at `/workspace:rw` and named volume `nivos-sandbox-packages` at `/packages:rw` (`PYTHONUSERBASE=/packages`, size cap 5120 MB).
     - Never mount vault, config directory, keys, or tokens; never pass secrets in environment variables.
     - Persistent and per-session auto-approve toggles inside sandbox apply ONLY to commands from `local_chat` turns matching allowed permission rules; NEVER apply to host, channels, or scheduled tasks.
     - Security policy strictly denies piping downloads into shell (`curl ... | sh`, `wget ... | bash`) and destructive system commands (`rm -rf /`, fork bombs, etc.).
     - Container stops automatically after 300s of idle inactivity.
     - `run_code` tool executes Python/Bash inside the sandbox and outputs artifacts directly to `workspace/` with receipt logging.
   - If Docker is offline, execution falls back to host workspace shell (`workspace/` only, `git/pip/npm` only) and ALWAYS requires explicit user confirmation.

5. **Desktop Applications & Installation Security**:
   - **Hardcoded Blocklist**: Shells (`cmd`, `powershell`, `pwsh`, `bash`, `wsl`), script engines (`wscript`, `cscript`, `mshta`, `rundll32`), registry tools (`regedit`), installers (`msiexec`, `*.msi`, `*setup*`), and administrative elevation (`runas`, `elevate`) are strictly forbidden from launch.
   - **Launch Approvals**: Master switch `open_any_installed_app` (default ON for local chat) permits launching safe discovered apps without prompt, EXCEPT for apps on the `always_ask_apps` list (password managers, banking, remote desktop) which always require interactive confirmation.
   - **App Installation (`install_app`)**: Exact winget package IDs only (`Publisher.PackageName`). Must display Name, Publisher, Version, and Source, and require explicit approval once per package. Never runs silently (no `--silent` or `--quiet`), and never executes raw downloaded installer binaries directly.

6. **Secrets & Network Restraint**:
   - Never log, display, or leak API keys, tokens, or credentials.
   - No new outbound network connections except where explicitly specified by an approved phase.

7. **Testing & Verification**:
   - Run verification tests after every phase or feature:
     - Frontend: `npm test` and `npm run build` in `frontend/`.
     - Backend: `uv run nivos doctor` and relevant pytest suites.
   - Ensure clean builds and no regressions before committing changes.

8. **License & Integrity**:
   - Never edit or tamper with `LICENSE` (Apache 2.0).
   - Maintain documentation integrity and preserve existing code comments.
