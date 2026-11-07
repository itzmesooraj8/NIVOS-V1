# Development Tasks & Roadmap

Branch: `feat/workspace-shell-mcp`

## Safety & Governance
- [x] Create `SAFETY_RULES.md` (Branch isolation, test verification, command approval, license protection)
- [x] Create `TASKS.md` tracking development progress

## Feature 1: Workspace Upload Endpoint & Chat UI "+" Button
- [ ] Backend: Create `src/openjarvis/server/workspace_routes.py`
  - `POST /v1/workspace/upload`: Multipart file upload preserving relative paths under `workspace/` with path traversal protection
  - `GET /v1/workspace/files`: List files in `workspace/`
  - Mount router in `src/openjarvis/server/app.py`
- [ ] Frontend:
  - Add API client methods in `frontend/src/lib/api.ts` (`uploadToWorkspace`, `fetchWorkspaceFiles`)
  - Update `frontend/src/components/Chat/InputArea.tsx`: Add `+` button with file and folder upload options (`webkitdirectory`)
  - Add toast feedback and agent awareness of uploaded workspace files
- [ ] Tests & Verification:
  - Frontend build: `npm --prefix frontend run build`
  - Unit tests for upload & file list
- [ ] Summary report after Step 1

## Feature 2: Sandboxed Shell Tool Limited to `workspace/` with Approval Prompt
- [ ] Backend: Create `src/openjarvis/tools/sandboxed_shell.py`
  - Restrict allowed commands strictly to: `git clone`, `pip install`, `npm install`
  - Restrict working directory strictly to `workspace/` (create on demand, prevent `..` path traversal)
  - Interactive approve/deny prompt: Integration with `ApprovalStore` / proactive approval system before command execution
  - Register tool in `ToolRegistry` as `sandboxed_shell`
- [ ] Tests & Verification:
  - Unit tests in `tests/tools/test_sandboxed_shell.py` verifying whitelisted commands, path restrictions, and approval requirements
- [ ] Summary report after Step 2

## Feature 3: MCP Client Integration, Settings Page & Simple Plugin Loader
- [ ] Backend MCP Client & Tools:
  - Expose MCP server tools to agents like native skills
  - Add MCP server management endpoints:
    - `GET /v1/mcp/servers`: List configured servers and their discovered tools
    - `POST /v1/mcp/servers`: Add/update MCP server
    - `DELETE /v1/mcp/servers/{name}`: Remove MCP server
- [ ] Plugin Loader:
  - Create `src/openjarvis/plugins/loader.py` to scan and load plugins from `plugins/` and `~/.nivos/plugins/`
  - Plugin discovery manifest and dynamic tool registration
- [ ] Frontend Settings:
  - Add "MCP Servers & Plugins" section in `frontend/src/pages/SettingsPage.tsx`
  - Forms to view, add, and remove MCP servers and list active plugins
- [ ] Tests & Verification:
  - Frontend build: `npm --prefix frontend run build`
  - Backend tests for MCP routes & plugin loader
- [ ] Summary report after Step 3
