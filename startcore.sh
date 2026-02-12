#!/bin/bash
# nebula_start.sh — start Nebula Core, optionally as specific user

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$SCRIPT_DIR"
RUN_USER="${1:-}"

cd "$BASE_DIR" || { echo "Failed to change directory to $BASE_DIR"; exit 1; }

if [ ! -f ".venv/bin/activate" ]; then
    echo "Virtual environment activation file not found at $BASE_DIR/.venv/bin/activate"
    exit 1
fi

if [ -n "$RUN_USER" ]; then
    echo "Starting Nebula core as user: $RUN_USER"
    sudo -u "$RUN_USER" bash -c "cd '$BASE_DIR' && source '$BASE_DIR'/.venv/bin/activate && python -m nebula_core"
else
    echo "Starting Nebula core as current user: $(whoami)"
    source "$BASE_DIR/.venv/bin/activate"
    python -m nebula_core
    if command -v deactivate >/dev/null 2>&1; then
        deactivate
    fi
fi

echo "Nebula core launcher finished."
