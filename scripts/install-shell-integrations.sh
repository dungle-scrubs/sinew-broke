#!/bin/sh
set -eu

shell_rc=${1:-$HOME/.zshrc}
source_line='source "$HOME/dev/sinew-broke/shell/ai-costs.zsh"'

mkdir -p "$(dirname "$shell_rc")"
touch "$shell_rc"

if grep -Fq "$source_line" "$shell_rc"; then
  echo "Shell integration already installed in $shell_rc"
  exit 0
fi

printf '\n%s\n' "$source_line" >> "$shell_rc"
echo "Added ai-costs shell integration to $shell_rc"
