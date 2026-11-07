# Safety Rules for Nivos Development

These rules are strictly enforced for all automated and developer operations within this repository:

1. **Branch Isolation**:
   - Always work on dedicated feature branches (e.g., `feat/*`, `fix/*`).
   - Never commit or push directly to `main` or `master`.

2. **Testing & Verification**:
   - Run verification tests after every feature or bug fix:
     - Frontend: `npm --prefix frontend run build` (and unit tests where applicable).
     - Backend: `uv run nivos doctor` and test suites (`pytest`).
   - Ensure clean builds and no test regressions before committing changes.

3. **Command Execution & Guardrails**:
   - No unapproved destructive or dangerous commands.
   - Shell operations performed by agents must be sandboxed and restricted to `workspace/`.
   - Command whitelists must be enforced (e.g., `git clone`, `pip install`, `npm install`).
   - Interactive user approval is required prior to executing system actions or shell tools.

4. **License & Integrity**:
   - Never edit or tamper with `LICENSE` (Apache 2.0).
   - Maintain documentation integrity and preserve code comments.

