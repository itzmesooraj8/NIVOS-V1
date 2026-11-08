"""External-framework subprocess backends (Hermes Agent, OpenClaw)."""

from nivos.evals.backends.external.hermes_agent import HermesBackend
from nivos.evals.backends.external.openclaw import OpenClawBackend

__all__ = ["HermesBackend", "OpenClawBackend"]
