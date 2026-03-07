# ai-costs

User-local Sinew plugin for AI spend, credits, quota windows, and subscription
usage.

Provider adapters are disabled by default. Turn on only the ones you actually
use.

## Sinew config

```toml
[plugins]
paths = ["~/dev/sinew-plugins"]

[[modules.right.right]]
type = "plugin"
plugin = "ai-costs"
id = "ai-costs"
settings.openai_api.enabled = true
settings.anthropic_api.enabled = true
settings.openrouter.enabled = false
settings.claude_code.enabled = false
settings.gpt_subscription.enabled = false
settings.glm.enabled = false
settings.minimax.enabled = false
```

## Commands

- `./run.py` — Sinew poll-mode entrypoint
- `uv run ai-costs status --json` — dump normalized snapshots
- `uv run ai-costs-openai record --model ... --cost-usd ...` — append OpenAI ledger entries manually
- `uv run ai-costs-anthropic record --model ... --cost-usd ...` — append Anthropic ledger entries manually
- `uv run ai-costs-openai forward --body-file request.json` — forward a real OpenAI request and record usage automatically
- `uv run ai-costs-anthropic forward --body-file request.json` — forward a real Anthropic request and record usage automatically
- `bin/ai-costs-openai-forward ...` — thin shell wrapper around the OpenAI forwarder
- `bin/ai-costs-anthropic-forward ...` — thin shell wrapper around the Anthropic forwarder

## Examples

Example payloads live in `examples/`:

- `examples/openai-request.json`
- `examples/anthropic-request.json`

## Optional shell integration

If you want short commands in your shell, install the optional zsh integration:

```bash
cd ~/dev/sinew-plugins/ai-costs
./scripts/install-shell-integrations.sh
source ~/.zshrc
```

That adds:

- `aic-openai`
- `aic-anthropic`
- `aic-status`
