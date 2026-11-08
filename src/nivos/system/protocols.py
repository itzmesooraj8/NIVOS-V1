"""Structural protocols for substituting fakes in place of NivosSystem."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, List, Optional, Protocol

if TYPE_CHECKING:
    from nivos.core.config import NivosConfig
    from nivos.core.events import EventBus
    from nivos.engine._stubs import InferenceEngine
    from nivos.security.capabilities import CapabilityPolicy
    from nivos.sessions.session import SessionStore
    from nivos.tools._stubs import BaseTool
    from nivos.tools.storage._stubs import MemoryBackend
    from nivos.traces.collector import TraceCollector
    from nivos.traces.store import TraceStore


class OrchestratorDeps(Protocol):
    """Minimum surface of NivosSystem that QueryOrchestrator depends on.

    Tests can satisfy this with a lightweight class — no need to construct
    the full NivosSystem dataclass or materialize every subsystem.
    """

    config: NivosConfig
    bus: EventBus
    engine: InferenceEngine
    engine_key: str
    model: str
    agent_name: str
    tools: List[BaseTool]
    memory_backend: Optional[MemoryBackend]
    capability_policy: Optional[CapabilityPolicy]
    rate_limiter: Optional[Any]
    session_store: Optional[SessionStore]
    trace_store: Optional[TraceStore]
    trace_collector: Optional[TraceCollector]  # written by _run_agent

    # Optional attribute (getattr with default) — declared for type clarity.
    _skill_few_shot_examples: Any
