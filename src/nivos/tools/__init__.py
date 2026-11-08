"""Tools primitive — tool system with ABC interface and built-in tools."""

from __future__ import annotations

from nivos.tools._stubs import BaseTool, ToolExecutor, ToolSpec

# Import built-in tools to trigger @ToolRegistry.register() decorators.
# Each is wrapped in try/except so the package loads even before the
# individual tool modules are created.
try:
    import nivos.tools.calculator  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.think  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.retrieval  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.llm_tool  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.file_read  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.web_search  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.weather  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.fx_macro  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.code_interpreter  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.code_interpreter_docker  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.repl  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.storage_tools  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.mcp_adapter  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.channel_tools  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.http_request  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.docker_shell_exec  # noqa: F401
    import nivos.tools.sandboxed_shell  # noqa: F401
    import nivos.tools.shell_exec  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.memory_manage  # noqa: F401
except ImportError:
    pass
try:
    import nivos.tools.user_profile_manage  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.skill_manage  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.file_write  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.apply_patch  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.git_tool  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.db_query  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.pdf_tool  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.image_tool  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.audio_tool  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.knowledge_tools  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.text_to_speech  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.digest_collect  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.scan_chunks  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.knowledge_sql  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.apple_calendar  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.knowledge_search  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.browser  # noqa: F401
    import nivos.tools.browser_axtree  # noqa: F401
except ImportError:
    pass

try:
    import nivos.tools.proactive_tools  # noqa: F401
except ImportError:
    pass

__all__ = ["BaseTool", "ToolExecutor", "ToolSpec"]
