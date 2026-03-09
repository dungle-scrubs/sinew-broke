"""CLI entrypoints for the ai-costs plugin and ledger helpers."""

from __future__ import annotations

import json
from typing import Annotated, Any

import typer

from ai_costs.models import UsageLedgerEntry
from ai_costs.providers.base import ProviderError
from ai_costs.providers.claude_code import claude_auth_diagnostics
from ai_costs.service import build_output, collect_snapshots
from ai_costs.settings import load_settings, runtime_dir
from ai_costs.storage import Storage
from ai_costs.wrappers import forward_anthropic_request, forward_openai_request

app = typer.Typer(no_args_is_help=True)
openai_app = typer.Typer(no_args_is_help=True)
anthropic_app = typer.Typer(no_args_is_help=True)
app.add_typer(openai_app, name="openai")
app.add_typer(anthropic_app, name="anthropic")


def build_storage() -> Storage:
    """Create the plugin storage handle from runtime settings."""

    settings = load_settings()
    return Storage(runtime_dir(settings))


def print_json(payload: Any) -> None:
    """Print machine-readable JSON without extra formatting noise.

    :param payload: JSON-serializable payload.
    :returns: Nothing.
    """

    typer.echo(json.dumps(payload, indent=2))


def print_claude_auth_diagnostics(diagnostics: list[dict[str, Any]]) -> None:
    """Render Claude auth diagnostics in a readable text format.

    :param diagnostics: Claude auth diagnostic payloads.
    :returns: Nothing.
    """

    for entry in diagnostics:
        auth_status = entry.get("authStatus") or {}
        resolution = entry.get("resolution") or {}
        metadata = entry.get("metadata") or {}
        sources = entry.get("sources") or {}
        typer.echo(f"{entry['accountId']} -> {entry['configDir']}")
        typer.echo(
            "  auth: "
            f"loggedIn={auth_status.get('loggedIn')} "
            f"email={auth_status.get('email')} "
            f"org={auth_status.get('orgName') or auth_status.get('orgId')} "
            f"subscription={auth_status.get('subscriptionType')}"
        )
        typer.echo(
            "  resolution: "
            f"resolved={resolution.get('resolved')} source={resolution.get('source')}"
        )
        typer.echo(
            "  metadata: "
            f"oauthAccount={metadata.get('hasOauthAccount')} "
            f"billing={metadata.get('billingType')}"
        )
        typer.echo("  env:")
        for env_entry in sources.get("environment", []):
            typer.echo(f"    - {env_entry['name']}: present={env_entry['present']}")
        typer.echo("  files:")
        for file_entry in sources.get("files", []):
            typer.echo(
                f"    - {file_entry['path']}: exists={file_entry['exists']} "
                f"json={file_entry['json']} hasToken={file_entry['hasToken']} "
                f"tokenKey={file_entry['tokenKey']}"
            )
        typer.echo("  keychain:")
        keychain = sources.get("keychain") or {}
        for key_entry in keychain.get("checks", []):
            typer.echo(
                f"    - service={keychain.get('service')} account={key_entry['account']} "
                f"found={key_entry['found']} format={key_entry['format']} "
                f"hasToken={key_entry['hasToken']} tokenKey={key_entry['tokenKey']}"
            )


@app.command()
def status(json_output: bool = typer.Option(False, "--json", help="Emit JSON")) -> None:
    """Show current normalized provider snapshots."""

    settings = load_settings()
    storage = Storage(runtime_dir(settings))
    snapshots = collect_snapshots(settings, storage)
    if json_output:
        print_json([snapshot.model_dump(exclude_none=True) for snapshot in snapshots])
        return
    for snapshot in snapshots:
        typer.echo(
            f"{snapshot.display_name}: {snapshot.status} ({snapshot.source_type})"
        )


@app.command("claude-auth-debug")
def claude_auth_debug(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON")
) -> None:
    """Inspect Claude auth resolution for each discovered Claude profile.

    :param json_output: Whether to emit JSON instead of text.
    :returns: Nothing.
    """

    settings = load_settings()
    diagnostics = claude_auth_diagnostics(settings)
    if json_output:
        print_json(diagnostics)
        return
    print_claude_auth_diagnostics(diagnostics)


@app.command()
def plugin() -> None:
    """Emit the Sinew plugin JSON payload."""

    run_plugin()


def run_plugin() -> None:
    """Run the poll-mode plugin and print one JSON response."""

    settings = load_settings()
    storage = Storage(runtime_dir(settings))
    snapshots = collect_snapshots(settings, storage)
    output = build_output(snapshots, storage)
    typer.echo(output.model_dump_json(exclude_none=True))


@openai_app.command("record")
def record_openai(
    model: str = typer.Option(..., "--model", help="Model name"),
    cost_usd: float = typer.Option(..., "--cost-usd", help="Computed USD cost"),
    input_tokens: int | None = typer.Option(None, "--input-tokens"),
    cached_input_tokens: int | None = typer.Option(None, "--cached-input-tokens"),
    output_tokens: int | None = typer.Option(None, "--output-tokens"),
    account_id: str = typer.Option("default", "--account-id"),
) -> None:
    """Append one OpenAI ledger entry for local cost tracking."""

    record_entry(
        provider="openai_api",
        model=model,
        cost_usd=cost_usd,
        input_tokens=input_tokens,
        cache_read_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        account_id=account_id,
    )


@openai_app.command("forward")
def forward_openai(
    body_json: str | None = typer.Option(
        None, "--body-json", help="Inline JSON request body"
    ),
    body_file: str | None = typer.Option(
        None, "--body-file", help="Path to JSON request body"
    ),
    model: str | None = typer.Option(
        None, "--model", help="Override model name for pricing"
    ),
    endpoint: str = typer.Option("v1/responses", "--endpoint", help="OpenAI API path"),
    base_url: str = typer.Option(
        "https://api.openai.com", "--base-url", help="OpenAI API base URL"
    ),
    api_key: str | None = typer.Option(
        None, "--api-key", help="Override OPENAI_API_KEY"
    ),
    account_id: str = typer.Option("default", "--account-id"),
    header: Annotated[
        list[str] | None,
        typer.Option("--header", help="Extra header in 'Name: Value' form"),
    ] = None,
) -> None:
    """Forward a real OpenAI request and record ledger usage from the response."""

    settings = load_settings()
    storage = Storage(runtime_dir(settings))
    try:
        response_text = forward_openai_request(
            settings=settings,
            storage=storage,
            body_json=body_json,
            body_file=body_file,
            model=model,
            endpoint=endpoint,
            base_url=base_url,
            api_key=api_key,
            account_id=account_id,
            headers=header or [],
        )
    except ProviderError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(response_text)


@anthropic_app.command("record")
def record_anthropic(
    model: str = typer.Option(..., "--model", help="Model name"),
    cost_usd: float = typer.Option(..., "--cost-usd", help="Computed USD cost"),
    input_tokens: int | None = typer.Option(None, "--input-tokens"),
    cache_read_tokens: int | None = typer.Option(None, "--cache-read-tokens"),
    cache_write_tokens: int | None = typer.Option(None, "--cache-write-tokens"),
    output_tokens: int | None = typer.Option(None, "--output-tokens"),
    account_id: str = typer.Option("default", "--account-id"),
) -> None:
    """Append one Anthropic ledger entry for local cost tracking."""

    record_entry(
        provider="anthropic_api",
        model=model,
        cost_usd=cost_usd,
        input_tokens=input_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        output_tokens=output_tokens,
        account_id=account_id,
    )


@anthropic_app.command("forward")
def forward_anthropic(
    body_json: str | None = typer.Option(
        None, "--body-json", help="Inline JSON request body"
    ),
    body_file: str | None = typer.Option(
        None, "--body-file", help="Path to JSON request body"
    ),
    model: str | None = typer.Option(
        None, "--model", help="Override model name for pricing"
    ),
    endpoint: str = typer.Option(
        "v1/messages", "--endpoint", help="Anthropic API path"
    ),
    base_url: str = typer.Option(
        "https://api.anthropic.com", "--base-url", help="Anthropic API base URL"
    ),
    api_key: str | None = typer.Option(
        None, "--api-key", help="Override ANTHROPIC_API_KEY"
    ),
    account_id: str = typer.Option("default", "--account-id"),
    header: Annotated[
        list[str] | None,
        typer.Option("--header", help="Extra header in 'Name: Value' form"),
    ] = None,
) -> None:
    """Forward a real Anthropic request and record ledger usage from the response."""

    settings = load_settings()
    storage = Storage(runtime_dir(settings))
    try:
        response_text = forward_anthropic_request(
            settings=settings,
            storage=storage,
            body_json=body_json,
            body_file=body_file,
            model=model,
            endpoint=endpoint,
            base_url=base_url,
            api_key=api_key,
            account_id=account_id,
            headers=header or [],
        )
    except ProviderError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(response_text)


def record_entry(
    provider: str,
    model: str,
    cost_usd: float,
    input_tokens: int | None,
    cache_read_tokens: int | None,
    output_tokens: int | None,
    account_id: str,
    cache_write_tokens: int | None = None,
) -> None:
    """Insert a ledger entry and print the stored record."""

    storage = build_storage()
    entry = UsageLedgerEntry(
        provider=provider,
        account_id=account_id,
        model=model,
        input_tokens=input_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        source_type="derived_ledger",
    )
    storage.insert_ledger_entry(entry)
    print_json(entry.model_dump(exclude_none=True))


def main() -> None:
    """Run the main ai-costs CLI."""

    app()


def openai_wrapper_main() -> None:
    """Run the OpenAI ledger helper CLI."""

    openai_app()


def anthropic_wrapper_main() -> None:
    """Run the Anthropic ledger helper CLI."""

    anthropic_app()


if __name__ == "__main__":
    main()
