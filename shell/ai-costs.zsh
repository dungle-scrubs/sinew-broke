# Source this file from ~/.zshrc if you want short wrapper commands.

export PATH="$HOME/dev/sinew-plugins/ai-costs/bin:$PATH"

alias aic-openai='uv run --directory "$HOME/dev/sinew-plugins/ai-costs" ai-costs-openai'
alias aic-anthropic='uv run --directory "$HOME/dev/sinew-plugins/ai-costs" ai-costs-anthropic'
alias aic-status='uv run --directory "$HOME/dev/sinew-plugins/ai-costs" ai-costs status --json'
