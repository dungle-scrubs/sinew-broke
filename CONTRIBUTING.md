# Contributing to sinew-broke

Thanks for your interest in contributing! This document covers the basics.

## Development Setup

```bash
# Clone and install
git clone https://github.com/dungle-scrubs/sinew-broke.git
cd sinew-broke
uv sync --all-extras

# Run tests
uv run pytest

# Lint and format
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# Type check
uv run ty check src/
```

## Commit Conventions

This project uses [Conventional Commits](https://www.conventionalcommits.org/)
for automated changelog generation via release-please.

| Prefix | Purpose | Version Bump |
|--------|---------|-------------|
| `feat:` | New feature | minor (patch pre-1.0) |
| `fix:` | Bug fix | patch |
| `feat!:` | Breaking change | major (minor pre-1.0) |
| `docs:` | Documentation only | changelog only |
| `chore:` | Maintenance | changelog only |
| `test:` | Test changes | changelog only |
| `refactor:` | Code restructure | changelog only |

Examples:

```
feat: add DeepSeek provider adapter
fix: handle empty usage payload from OpenAI
docs: add shell integration instructions
chore: update pricing tables
```

## Pull Requests

1. Fork the repo and create a branch from `main`.
2. Make your changes with tests where appropriate.
3. Ensure `uv run pytest` passes and `uv run ruff check` is clean.
4. Open a PR against `main`.

## Adding a Provider Adapter

1. Create `src/ai_costs/providers/<name>.py` implementing `ProviderAdapter`.
2. Add the adapter to `src/ai_costs/providers/__init__.py`.
3. Add a `ProviderSettings` entry in `src/ai_costs/settings.py`.
4. Register the adapter in `FIXED_ORDER` in `src/ai_costs/service.py`.
5. Add tests in `tests/`.

## Updating Pricing Tables

Pricing JSON files live in `src/ai_costs/pricing/`. Add new models or update
rates as providers change their pricing. Each file has a `version` field — bump
it when making changes.
