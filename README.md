# sinew-broke

[![CI](https://github.com/dungle-scrubs/sinew-broke/actions/workflows/ci.yml/badge.svg)](https://github.com/dungle-scrubs/sinew-broke/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org)

Track AI spend, credits, quota windows, and subscription usage from your
status bar. A [Sinew](https://github.com/dungle-scrubs/sinew) plugin.

## Features

- **Multi-provider**: OpenAI API, Anthropic API, OpenRouter, Claude Code,
  GPT Subscription, GLM, MiniMax
- **Cost tracking**: Today/month/lifetime USD totals derived from local
  request ledger or authoritative APIs
- **Subscription windows**: 5h/7d usage percentages and reset times for
  Claude Code and GPT subscriptions
- **Credits & quotas**: OpenRouter balance, GLM quota, MiniMax plan remains
- **Request forwarding**: Transparent wrappers that forward real API requests
  and record token usage automatically
- **Versioned pricing**: Bundled per-model pricing tables for offline cost
  calculation

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- [Sinew](https://github.com/dungle-scrubs/sinew) (for status-bar mode)
- macOS (Keychain integration is macOS-only)

## Installation

This repo is meant to be cloned into a plugins directory that Sinew scans.
It is not meant to be installed from a package registry.

```bash
mkdir -p ~/dev/sinew-plugins
git clone https://github.com/dungle-scrubs/sinew-broke.git ~/dev/sinew-plugins/sinew-broke
cd ~/dev/sinew-plugins/sinew-broke
uv sync
```

Then point Sinew at the parent plugins directory:

```toml
[plugins]
paths = ["~/dev/sinew-plugins"]
```

## Sinew Configuration

Enable the providers you use in your Sinew config:

```toml
[plugins]
paths = ["~/dev"]

[[modules.right.right]]
type = "plugin"
plugin = "sinew-broke"
id = "sinew-broke"
settings.openai_api.enabled = true
settings.anthropic_api.enabled = true
settings.openrouter.enabled = false
settings.claude_code.enabled = false
settings.gpt_subscription.enabled = false
settings.glm.enabled = false
settings.minimax.enabled = false
```

Provider adapters are disabled by default. Turn on only the ones you use.

### Multiple Accounts

Claude Code and GPT Subscription support multiple accounts.

Claude Code accounts are auto-discovered from `~/.config/claude-work-dirs`
when that file points at additional Claude config directories. The plugin
tracks one Claude account per config dir, shells out to `claude auth status --json`
with `CLAUDE_CONFIG_DIR` for profile-scoped login status, then resolves auth
from legacy `.credentials.json` files, profile-local `.claude.json` metadata,
matching Tallow `auth.json` files when present, and the macOS Keychain. For example, this setup shows both `Claude Code (.claude)`
and `Claude Code (.claude-fuse)` in the popup:

```text
# ~/.config/claude-work-dirs
/Users/kevin/dev/fuse:/Users/kevin/.claude-fuse
```

Manual multi-account config still works when you want custom labels or need to
track GPT subscriptions too:

```toml
settings.claude_code = [
  { enabled = true, account_id = "personal", config_dir = "~/.claude" },
  { enabled = true, account_id = "fuse", config_dir = "~/.claude-fuse" },
]
```

Each account appears as a separate entry in the status bar popup.

## Commands

| Command | Description |
|---------|-------------|
| `./run.py` | Sinew poll-mode entrypoint |
| `uv run ai-costs status --json` | Dump normalized provider snapshots |
| `uv run ai-costs claude-auth-debug --json` | Inspect Claude profile auth and token resolution |
| `uv run ai-costs-openai record --model ... --cost-usd ...` | Append OpenAI ledger entry |
| `uv run ai-costs-anthropic record --model ... --cost-usd ...` | Append Anthropic ledger entry |
| `uv run ai-costs-openai forward --body-file request.json` | Forward OpenAI request and record usage |
| `uv run ai-costs-anthropic forward --body-file request.json` | Forward Anthropic request and record usage |

## Examples

Example request payloads live in `examples/`:

- `examples/openai-request.json`
- `examples/anthropic-request.json`

## Shell Integration

Optional zsh aliases for shorter commands:

```bash
./scripts/install-shell-integrations.sh
source ~/.zshrc
```

Adds: `aic-openai`, `aic-anthropic`, `aic-status`

## API Keys

API keys are resolved at runtime from (in order):

1. Explicit `--api-key` flag or plugin settings
2. Environment variables (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.)
3. JSON credential files and profile-local metadata (for Claude, e.g. `~/.claude/.credentials.json` and `~/.claude/.claude.json`)
4. macOS Keychain

For local development, use an `.env.op.local` file with
[opchain](https://github.com/dungle-scrubs/opchain) `op://` references.

## Known Limitations

- macOS only (Keychain integration, `~/Library/Application Support` paths)
- Shell integration assumes `$HOME/dev/sinew-plugins/sinew-broke`
- Pricing tables need manual updates when providers change rates
- Claude Code and GPT Subscription adapters depend on undocumented OAuth
  endpoints that may change

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## License

[MIT](LICENSE) © Kevin Frilot
