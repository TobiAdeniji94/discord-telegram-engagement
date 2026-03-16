# Security Operations Guide

## Credential Rotation Checklist

Rotate these values immediately if they were exposed:

- `GEMINI_API_KEY`
- `XAI_API_KEY`
- `DISCORD_BOT_TOKEN`
- `TWITTERAPI_IO_API_KEY`
- `TWSCRAPE_PASSWORD`
- `TWSCRAPE_EMAIL_PASSWORD`
- `TWSCRAPE_COOKIES`
- `X_AUTH_TOKEN`
- `X_CSRF_TOKEN`
- `X_COOKIE`

After rotating:

1. Update runtime secret storage and deployment environment variables.
2. Invalidate old keys/sessions at each provider.
3. Restart the bot and verify startup checks pass.

## Secret Storage Rules

- Never commit `.env` to version control.
- Keep only `.env.example` in the repo.
- Use environment injection from your deployment platform or a dedicated secret manager.

## Secret Scanning

Run local scan:

```powershell
scripts\scan-secrets.ps1
```

Enable pre-commit hook:

```bash
pre-commit install
pre-commit run --all-files
```

CI runs gitleaks on pull requests and pushes via `.github/workflows/secret-scan.yml`.

## Discord Authorization Defaults

Use strict mode by default:

- `DISCORD_COMMAND_AUTH_MODE=enforce`
- configure `DISCORD_ALLOWED_USER_IDS` or `DISCORD_ALLOWED_ROLE_IDS`
- configure `DISCORD_ALLOWED_CHANNEL_IDS`
- keep `DISCORD_REQUIRE_PENDING_CHANNEL_MATCH=true`
