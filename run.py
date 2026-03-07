#!/usr/bin/env -S uv run python

"""Sinew poll-mode entrypoint for the ai-costs plugin."""

from ai_costs.cli import run_plugin

if __name__ == "__main__":
    run_plugin()
