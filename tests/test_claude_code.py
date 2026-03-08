from __future__ import annotations

import json
from pathlib import Path

from ai_costs.providers.claude_code import (
    ClaudeCodeAdapter,
    claude_auth_diagnostics,
    claude_auth_status,
    claude_profile_diagnostic,
)
from ai_costs.settings import (
    PluginSettings,
    ProviderSettings,
    discover_claude_code_configs,
)
from ai_costs.storage import Storage
from ai_costs.utils import resolve_token


def test_discover_claude_code_configs_uses_config_dirs_without_credentials_files(
    tmp_path: Path, monkeypatch
) -> None:
    default_dir = tmp_path / ".claude"
    default_dir.mkdir()
    fuse_dir = tmp_path / ".claude-fuse"
    fuse_dir.mkdir()

    work_dirs = tmp_path / "claude-work-dirs"
    work_dirs.write_text(f"/Users/kevin/dev/fuse:{fuse_dir}\n")

    monkeypatch.setattr("ai_costs.settings.CLAUDE_WORK_DIRS_PATH", work_dirs)
    monkeypatch.setattr("ai_costs.settings.DEFAULT_CLAUDE_CONFIG_DIR", str(default_dir))
    monkeypatch.setattr(
        "ai_costs.settings.DEFAULT_CLAUDE_AUTH_FILE",
        str(default_dir / ".credentials.json"),
    )

    configs = discover_claude_code_configs(ProviderSettings(enabled=True))

    assert len(configs) == 2
    assert configs[0].account_id == ".claude"
    assert configs[0].config_dir == str(default_dir)
    assert configs[0].auth_file == str(default_dir / ".credentials.json")
    assert configs[1].account_id == ".claude-fuse"
    assert configs[1].config_dir == str(fuse_dir)
    assert configs[1].auth_file == str(fuse_dir / ".credentials.json")


def test_claude_code_adapter_returns_incomplete_snapshot_from_profile_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    config_dir = tmp_path / ".claude-fuse"
    config_dir.mkdir()
    (config_dir / ".claude.json").write_text(
        json.dumps(
            {
                "oauthAccount": {
                    "emailAddress": "kevin@fuse.is",
                    "organizationName": "Fuse",
                    "billingType": "stripe_subscription",
                }
            }
        )
    )
    monkeypatch.setattr(
        "ai_costs.providers.claude_code.resolve_token",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "ai_costs.providers.claude_code.claude_auth_status",
        lambda *args, **kwargs: {},
    )

    settings = PluginSettings(
        claude_code=ProviderSettings(enabled=True, config_dir=str(config_dir))
    )

    snapshot = ClaudeCodeAdapter().fetch(settings, Storage(tmp_path / "runtime"))

    assert snapshot.status == "incomplete"
    assert snapshot.notes == [
        "profile .claude-fuse",
        "kevin@fuse.is",
        "Fuse",
        "billing stripe_subscription",
    ]


def test_claude_code_adapter_uses_auth_status_when_token_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    config_dir = tmp_path / ".claude-fuse"
    config_dir.mkdir()
    monkeypatch.setattr(
        "ai_costs.providers.claude_code.resolve_token",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "ai_costs.providers.claude_code.claude_auth_status",
        lambda *args, **kwargs: {
            "loggedIn": True,
            "email": "kevin@fuse.is",
            "orgName": "Fuse",
            "subscriptionType": "max",
            "authMethod": "claude.ai",
        },
    )

    settings = PluginSettings(
        claude_code=ProviderSettings(enabled=True, config_dir=str(config_dir))
    )

    snapshot = ClaudeCodeAdapter().fetch(settings, Storage(tmp_path / "runtime"))

    assert snapshot.status == "incomplete"
    assert snapshot.notes == [
        "profile .claude-fuse",
        "kevin@fuse.is",
        "Fuse",
        "subscription max",
        "auth claude.ai",
    ]


def test_claude_auth_status_uses_config_dir_override(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / ".claude-fuse"
    config_dir.mkdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-leak")
    calls: list[dict[str, str]] = []

    class FakeResult:
        """Lightweight subprocess result for auth status tests."""

        def __init__(self, stdout: str) -> None:
            self.stdout = stdout
            self.returncode = 0

    def fake_run(*args, **kwargs):
        calls.append(kwargs["env"])
        return FakeResult('{"loggedIn": true, "email": "kevin@fuse.is"}')

    monkeypatch.setattr("ai_costs.providers.claude_code.subprocess.run", fake_run)

    status = claude_auth_status(config_dir)

    assert status == {"loggedIn": True, "email": "kevin@fuse.is"}
    assert calls[0]["CLAUDE_CONFIG_DIR"] == str(config_dir)
    assert "ANTHROPIC_API_KEY" not in calls[0]


def test_resolve_token_uses_profile_keychain_account_before_generic(
    monkeypatch,
) -> None:
    calls: list[tuple[str, str | None]] = []

    def fake_keychain_secret(service: str, account: str | None = None) -> str | None:
        calls.append((service, account))
        if account == "profile-account":
            return json.dumps({"access_token": "profile-token"})
        if account is None:
            return json.dumps({"access_token": "shared-token"})
        return None

    monkeypatch.setattr("ai_costs.utils.keychain_secret", fake_keychain_secret)

    token = resolve_token(
        None,
        [],
        [],
        ["access_token"],
        keychain_services=["Claude Code-credentials"],
        keychain_accounts=["profile-account"],
    )

    assert token == "profile-token"
    assert calls == [("Claude Code-credentials", "profile-account")]


def test_resolve_token_can_skip_generic_keychain_fallback(monkeypatch) -> None:
    def fake_keychain_secret(service: str, account: str | None = None) -> str | None:
        if account is None:
            return json.dumps({"access_token": "shared-token"})
        return None

    monkeypatch.setattr("ai_costs.utils.keychain_secret", fake_keychain_secret)

    token = resolve_token(
        None,
        [],
        [],
        ["access_token"],
        keychain_services=["Claude Code-credentials"],
        keychain_accounts=["missing-profile-account"],
        allow_generic_keychain=False,
    )

    assert token is None


def test_claude_profile_diagnostic_reports_token_resolution_failures(
    tmp_path: Path, monkeypatch
) -> None:
    config_dir = tmp_path / ".claude-fuse"
    config_dir.mkdir()
    (config_dir / ".claude.json").write_text(
        json.dumps(
            {
                "oauthAccount": {
                    "emailAddress": "kevin@fuse.is",
                    "organizationName": "Fuse",
                    "billingType": "stripe_subscription",
                }
            }
        )
    )

    monkeypatch.setattr(
        "ai_costs.providers.claude_code.claude_auth_status_result",
        lambda *args, **kwargs: {
            "returncode": 0,
            "hasStdout": True,
            "payload": {
                "loggedIn": True,
                "email": "kevin@fuse.is",
                "orgName": "Fuse",
                "subscriptionType": "max",
                "authMethod": "claude.ai",
            },
        },
    )
    monkeypatch.setattr(
        "ai_costs.providers.claude_code.keychain_secret",
        lambda *args, **kwargs: None,
    )

    diagnostic = claude_profile_diagnostic(
        ProviderSettings(enabled=True, config_dir=str(config_dir))
    )

    assert diagnostic["resolution"] == {
        "resolved": False,
        "source": None,
        "allowGenericKeychain": False,
    }
    claude_json_entry = next(
        entry
        for entry in diagnostic["sources"]["files"]
        if entry["path"].endswith(".claude.json")
    )
    assert claude_json_entry["hasToken"] is False
    assert diagnostic["sources"]["keychain"]["accounts"] == ["kevin@fuse.is"]
    assert diagnostic["sources"]["keychain"]["checks"][0]["hasToken"] is False


def test_claude_profile_diagnostic_uses_tallow_auth_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    config_dir = tmp_path / ".claude-fuse"
    config_dir.mkdir()
    tallow_dir = tmp_path / ".tallow-fuse"
    tallow_dir.mkdir()
    (tallow_dir / "auth.json").write_text(
        json.dumps(
            {
                "anthropic": {
                    "type": "oauth",
                    "access": "tallow-access-token",
                    "refresh": "tallow-refresh-token",
                }
            }
        )
    )
    monkeypatch.setattr(
        "ai_costs.providers.claude_code.claude_auth_status_result",
        lambda *args, **kwargs: {
            "returncode": 0,
            "hasStdout": True,
            "payload": {
                "loggedIn": True,
                "email": "kevin@fuse.is",
                "orgName": "Fuse",
                "subscriptionType": "max",
                "authMethod": "claude.ai",
            },
        },
    )
    monkeypatch.setattr(
        "ai_costs.providers.claude_code.keychain_secret",
        lambda *args, **kwargs: None,
    )

    diagnostic = claude_profile_diagnostic(
        ProviderSettings(enabled=True, config_dir=str(config_dir))
    )

    tallow_entry = next(
        entry
        for entry in diagnostic["sources"]["files"]
        if entry["path"].endswith(".tallow-fuse/auth.json")
    )
    assert tallow_entry["hasToken"] is True
    assert tallow_entry["tokenKey"] == "access"
    assert diagnostic["resolution"]["source"].endswith(".tallow-fuse/auth.json")


def test_claude_auth_diagnostics_forces_disabled_config_enabled(
    tmp_path: Path, monkeypatch
) -> None:
    default_dir = tmp_path / ".claude"
    default_dir.mkdir()
    monkeypatch.setattr("ai_costs.settings.CLAUDE_WORK_DIRS_PATH", tmp_path / "missing")
    monkeypatch.setattr("ai_costs.settings.DEFAULT_CLAUDE_CONFIG_DIR", str(default_dir))
    monkeypatch.setattr(
        "ai_costs.settings.DEFAULT_CLAUDE_AUTH_FILE",
        str(default_dir / ".credentials.json"),
    )
    monkeypatch.setattr(
        "ai_costs.providers.claude_code.claude_profile_diagnostic",
        lambda config: {"accountId": config.account_id, "enabled": config.enabled},
    )

    diagnostics = claude_auth_diagnostics(
        PluginSettings(claude_code=ProviderSettings(enabled=False))
    )

    assert diagnostics == [{"accountId": ".claude", "enabled": True}]
