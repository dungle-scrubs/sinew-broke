# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x | ✅ |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do not** open a public issue.
2. Email **kevin@dungle-scrubs.dev** with details.
3. Include steps to reproduce and potential impact.

You should receive a response within 48 hours. We will work with you to
understand the issue and coordinate a fix before any public disclosure.

## Scope

This plugin handles API keys and OAuth tokens for AI providers. Security
concerns include:

- Credential leakage through logs, error messages, or git history
- Unauthorized access to provider APIs
- SQLite database exposure containing usage data

## Design Decisions

- API keys are resolved at runtime from environment variables, 1Password
  (via opchain), or macOS Keychain — never stored in source files.
- The `.env.op.local` file contains only `op://` references, not actual
  secrets.
- The local SQLite database stores usage metrics and cost data, not
  credentials.
