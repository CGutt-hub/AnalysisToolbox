#!/bin/bash
# Toolbox-level reinjection wrapper
# Usage: ./reinject.sh <participant_id> [--pipeline-dir <dir>] [other atbx reinject args]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if command -v atbx >/dev/null 2>&1; then
    exec atbx reinject "$@"
fi

# Fallback for editable/local usage without installation
export PYTHONPATH="$PY_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m analysis_toolbox_cli.cli reinject "$@"
