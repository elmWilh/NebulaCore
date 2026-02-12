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

ensure_dependencies() {
    source "$BASE_DIR/.venv/bin/activate"
    if ! python -c "import pyotp" >/dev/null 2>&1; then
        echo "Missing Python dependencies in .venv, installing from requirements.txt..."
        python -m pip install -r "$BASE_DIR/requirements.txt" || {
            echo "Dependency installation failed."
            return 1
        }
    fi
    return 0
}

if [ -n "$RUN_USER" ]; then
    echo "Starting Nebula core as user: $RUN_USER"
    sudo -u "$RUN_USER" bash -c "cd '$BASE_DIR' && source '$BASE_DIR'/.venv/bin/activate && (python -c 'import pyotp' >/dev/null 2>&1 || python -m pip install -r '$BASE_DIR/requirements.txt') && python -m nebula_core"
else
    echo "Starting Nebula core as current user: $(whoami)"
    ensure_dependencies || exit 1
    python -m nebula_core
    if command -v deactivate >/dev/null 2>&1; then
        deactivate
    fi
fi

echo "Nebula core launcher finished."
