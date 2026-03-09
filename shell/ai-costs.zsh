# Source this file from ~/.zshrc if you want short wrapper commands.

export PATH="$HOME/dev/sinew-plugins/sinew-broke/bin:$PATH"

alias aic-openai='uv run --directory "$HOME/dev/sinew-plugins/sinew-broke" ai-costs-openai'
alias aic-anthropic='uv run --directory "$HOME/dev/sinew-plugins/sinew-broke" ai-costs-anthropic'
alias aic-status='uv run --directory "$HOME/dev/sinew-plugins/sinew-broke" ai-costs status --json'
