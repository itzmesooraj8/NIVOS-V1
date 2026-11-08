"""Skill source resolvers — Hermes, OpenClaw, generic GitHub."""

from nivos.skills.sources.base import ResolvedSkill, SourceResolver
from nivos.skills.sources.github import GitHubResolver
from nivos.skills.sources.hermes import HERMES_REPO_URL, HermesResolver
from nivos.skills.sources.openclaw import OPENCLAW_REPO_URL, OpenClawResolver

__all__ = [
    "GitHubResolver",
    "HERMES_REPO_URL",
    "HermesResolver",
    "OPENCLAW_REPO_URL",
    "OpenClawResolver",
    "ResolvedSkill",
    "SourceResolver",
]
