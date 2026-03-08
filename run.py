#!/bin/sh
set -eu

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if command -v uv >/dev/null 2>&1; then
  UV_BIN=$(command -v uv)
elif [ -x "$HOME/.local/bin/uv" ]; then
  UV_BIN=$HOME/.local/bin/uv
elif [ -x /opt/homebrew/bin/uv ]; then
  UV_BIN=/opt/homebrew/bin/uv
elif [ -x /usr/local/bin/uv ]; then
  UV_BIN=/usr/local/bin/uv
else
  echo "uv not found; install uv or add it to PATH" >&2
  exit 127
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR"
exec "$UV_BIN" run ai-costs plugin
