"""Command-line interface for Nivos (Click-based)."""

from __future__ import annotations

import sys

import click

import nivos
from nivos.cli.scan_cmd import scan


def _invoked_command(argv: list[str]) -> str:
    """Return the first positional CLI token after global flags."""
    for arg in argv:
        if arg.startswith("-"):
            continue
        return arg
    return ""


# A data-boundary scan must be able to diagnose an invalid NIVOS_HOME.
# Importing the rest of the CLI eagerly would import core.config and resolve that
# path before the scan can turn the failure into a finding.
_DATA_BOUNDARY_BOOTSTRAP = (
    _invoked_command(sys.argv[1:]) == "scan" and "--data-boundaries" in sys.argv[1:]
)


def _should_skip_update_check(ctx: click.Context, argv: list[str]) -> bool:
    """Return true for commands whose diagnostics should remain local-only."""
    if "--research" in argv:
        return True
    return ctx.invoked_subcommand == "scan" and "--data-boundaries" in argv


@click.group(
    help="Nivos — modular AI assistant backend",
    invoke_without_command=True,
)
@click.version_option(version=nivos.__version__, prog_name="nivos")
@click.option("--verbose", is_flag=True, default=False, help="Enable debug logging")
@click.option("--quiet", is_flag=True, default=False, help="Suppress non-error output")
@click.option(
    "--pick-model",
    "pick_model_bare",
    is_flag=True,
    default=False,
    help=(
        "Bare ``nivos``: force interactive model list "
        "(overrides NIVOS_SKIP_MODEL_PICK)."
    ),
)
@click.pass_context
def cli(ctx: click.Context, verbose: bool, quiet: bool, pick_model_bare: bool) -> None:
    """Top-level CLI group."""
    from nivos.cli.log_config import setup_logging

    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["quiet"] = quiet
    ctx.obj["pick_model_bare"] = pick_model_bare
    setup_logging(verbose=verbose, quiet=quiet)

    # Check for updates on interactive commands. The banner is noise in
    # demo recordings of ``nivos ask --research``, so skip it whenever
    # the research flag is in argv (cheap argv sniff — Click hasn't
    # parsed the subcommand's args yet at this point). Also skip
    # ``nivos scan --data-boundaries`` because it is intended to be a
    # local application-data diagnostic with no outbound calls.
    import sys

    skip_update_check = _should_skip_update_check(ctx, sys.argv[1:])
    if not quiet and ctx.invoked_subcommand and not skip_update_check:
        import threading

        from nivos.cli._version_check import check_for_updates

        # Run the PyPI version poll off the hot path: on a cache miss it does
        # a blocking urlopen (up to 3s) that otherwise delays every command,
        # notably `nivos serve` startup (#263). It's best-effort and never
        # raises, and the nudge prints to stderr, so a daemon thread is safe —
        # for long-lived commands (serve) it finishes; for short commands that
        # exit first, the check is simply skipped this run (same as a miss).
        threading.Thread(
            target=check_for_updates,
            args=(ctx.invoked_subcommand,),
            daemon=True,
        ).start()

    # First-run guard — routes bare `nivos` to chat or init.
    if ctx.invoked_subcommand is None:
        from nivos.cli._first_run import check_and_route

        check_and_route(ctx)


cli.add_command(scan, "scan")
if not _DATA_BOUNDARY_BOOTSTRAP:
    from nivos.cli._bootstrap import bootstrap_cmd
    from nivos.cli.add_cmd import add
    from nivos.cli.agent_cmd import agent
    from nivos.cli.ask import ask
    from nivos.cli.bench_cmd import bench
    from nivos.cli.channel_cmd import channel
    from nivos.cli.channels_cmd import channels
    from nivos.cli.chat_cmd import chat
    from nivos.cli.compose_cmd import compose
    from nivos.cli.config_cmd import config
    from nivos.cli.connect_cmd import connect
    from nivos.cli.daemon_cmd import restart, start, status, stop
    from nivos.cli.digest_cmd import digest
    from nivos.cli.doctor_cmd import doctor
    from nivos.cli.eval_cmd import eval_group
    from nivos.cli.feedback_cmd import feedback_group
    from nivos.cli.gateway_cmd import gateway
    from nivos.cli.gui_cmd import build_ui, gui
    from nivos.cli.host_cmd import host
    from nivos.cli.init_cmd import init
    from nivos.cli.memory_cmd import memory
    from nivos.cli.mine_cmd import mine
    from nivos.cli.model import model
    from nivos.cli.operators_cmd import operators
    from nivos.cli.optimize_cmd import optimize_group
    from nivos.cli.pearl_cmd import pearl
    from nivos.cli.quickstart_cmd import quickstart
    from nivos.cli.registry_cmd import registry
    from nivos.cli.scheduler_cmd import scheduler
    from nivos.cli.self_update_cmd import self_update
    from nivos.cli.serve import serve
    from nivos.cli.skill_cmd import skill
    from nivos.cli.telemetry_cmd import telemetry
    from nivos.cli.tool_cmd import tool
    from nivos.cli.vault_cmd import vault
    from nivos.cli.workflow_cmd import workflow

    cli.add_command(init, "init")
    cli.add_command(ask, "ask")
    cli.add_command(chat, "chat")
    cli.add_command(serve, "serve")
    cli.add_command(model, "model")
    cli.add_command(memory, "memory")
    cli.add_command(mine, "mine")
    cli.add_command(pearl, "pearl")
    cli.add_command(telemetry, "telemetry")
    cli.add_command(bench, "bench")
    cli.add_command(channel, "channel")
    cli.add_command(channels, "channels")
    cli.add_command(scheduler, "scheduler")
    cli.add_command(doctor, "doctor")
    cli.add_command(agent, "agents")
    cli.add_command(workflow, "workflow")
    cli.add_command(skill, "skill")
    cli.add_command(start, "start")
    cli.add_command(stop, "stop")
    cli.add_command(restart, "restart")
    cli.add_command(status, "status")
    cli.add_command(vault, "vault")
    cli.add_command(add, "add")
    cli.add_command(operators, "operators")
    cli.add_command(eval_group, "eval")
    cli.add_command(host, "host")
    cli.add_command(quickstart, "quickstart")
    cli.add_command(optimize_group, "optimize")
    cli.add_command(feedback_group, "feedback")
    cli.add_command(compose, "compose")
    cli.add_command(gateway, "gateway")
    cli.add_command(gui, "gui")
    cli.add_command(build_ui, "build-ui")
    cli.add_command(tool, "tool")
    cli.add_command(registry, "registry")
    cli.add_command(config, "config")
    cli.add_command(connect, "connect")
    cli.add_command(digest, "digest")

    # Deep Research setup pulls the ingestion pipeline (embeddings/numpy). Guard
    # it so an import-time dependency failure cannot take down the whole CLI.
    try:
        from nivos.cli.deep_research_setup_cmd import deep_research_setup

        cli.add_command(deep_research_setup, "deep-research-setup")
        cli.add_command(deep_research_setup, "research")
    except Exception as _dr_exc:
        import logging as _logging

        _logging.getLogger(__name__).debug(
            "deep-research command unavailable: %s", _dr_exc
        )
    cli.add_command(self_update, "self-update")
    cli.add_command(bootstrap_cmd, "_bootstrap")

    # Gateway CLI commands (lazy import to avoid pulling starlette)
    try:
        from nivos.cli.auth_cmd import auth

        cli.add_command(auth, "auth")
    except ImportError:
        pass

    try:
        from nivos.cli.tunnel_cmd import tunnel

        cli.add_command(tunnel, "tunnel")
    except ImportError:
        pass


def main() -> None:
    """Entry point registered as ``nivos`` console script."""
    import sys

    if sys.platform == "win32":
        for _stream in (sys.stdout, sys.stderr):
            if hasattr(_stream, "reconfigure"):
                try:
                    _stream.reconfigure(encoding="utf-8", errors="replace")
                except (AttributeError, OSError):
                    pass
    cli()


__all__ = ["cli", "main"]
