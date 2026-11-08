#!/usr/bin/env bash
# nivos-wrapper.sh — symlinked to ~/.local/bin/nivos.
# Activates the managed venv and execs the real nivos CLI.

NIVOS_HOME="${NIVOS_HOME:-$HOME/.nivos}"
VENV="$NIVOS_HOME/.venv"

if [[ ! -d "$VENV" ]]; then
    echo "nivos: venv not found at $VENV" >&2
    echo "Re-run the installer: curl -fsSL https://nivos.github.io/Nivos/install.sh | bash" >&2
    exit 1
fi

exec "$VENV/bin/nivos" "$@"
