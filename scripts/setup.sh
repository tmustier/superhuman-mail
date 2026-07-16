#!/usr/bin/env bash
# Bootstrap superhuman-mail: install a stable launcher and verify the CLI.
# Run from the repo root: ./scripts/setup.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN_DIR="${SHM_BIN_DIR:-$HOME/.local/bin}"
cd "$REPO_ROOT"

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required to run the self-contained shm launcher." >&2
    echo "Install it with: brew install uv" >&2
    exit 1
fi

mkdir -p "$BIN_DIR"
ln -sfn "$REPO_ROOT/shm" "$BIN_DIR/shm"

echo "Installed shm launcher at $BIN_DIR/shm"
echo "Dependencies are provisioned in uv's isolated cache on first use."

PATH="$BIN_DIR:$PATH" shm schema >/dev/null
echo "✓ shm CLI is working"

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo ""
    echo "Add $BIN_DIR to PATH, for example:"
    echo "  export PATH=\"$BIN_DIR:\$PATH\""
fi

if [ -f config.json ] || [ -n "${SUPERHUMAN_MAIL_CONFIG:-}" ]; then
    echo "✓ Superhuman Mail config found"
    PATH="$BIN_DIR:$PATH" shm doctor
else
    echo ""
    echo "No config found yet. With Superhuman running and signed in, run:"
    echo "  shm setup"
    echo "  shm doctor"
fi
