#!/bin/bash
# Toolbox-level reinjection wrapper
# Usage: ./reinject.sh <participant_id> [--pipeline-dir <dir>] [other gitref reinject args]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if command -v gitref >/dev/null 2>&1; then
    exec gitref reinject "$@"
fi

# Fallback for editable/local usage without installation
export PYTHONPATH="$PY_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m gitref_toolbox.cli reinject "$@"
