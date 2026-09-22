#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d ".venv" ]; then
    echo "Virtualenv .venv not found. Please install dependencies first."
    exit 1
fi

if [ ! -f "config/setting.toml" ]; then
    echo "Configuration config/setting.toml not found. Waiting for initialization."
    exit 1
fi

exec .venv/bin/python main.py
