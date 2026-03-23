#!/usr/bin/env bash
# Bootstrap superhuman-mail: create venv, install deps, verify CLI works.
# Run from the repo root: ./scripts/setup.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "Setting up superhuman-mail in $REPO_ROOT"

# Create venv if needed
if [ ! -d .venv ]; then
    echo "Creating Python venv..."
    python3 -m venv .venv
fi

# Install package
echo "Installing dependencies..."
.venv/bin/pip install -q -e .

# Verify shm is runnable
echo "Verifying shm..."
.venv/bin/shm schema > /dev/null 2>&1 && echo "✓ shm CLI is working" || echo "✗ shm CLI failed"

# Check for config
if [ -f config.json ]; then
    echo "✓ config.json found"
    .venv/bin/shm doctor
else
    echo "✗ config.json not found"
    echo ""
    echo "Copy config.example.json to config.json and fill in your values."
    echo "See the _help fields in config.example.json for where to find each value."
    echo ""
    echo "Or set SUPERHUMAN_MAIL_CONFIG to point to an existing config file:"
    echo "  export SUPERHUMAN_MAIL_CONFIG=/path/to/config.json"
fi
