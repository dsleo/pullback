# Security

## Reporting Vulnerabilities

Please report security issues by opening a GitHub issue marked **[security]**, or email the maintainer directly if the issue is sensitive.

## API Key Safety

- Never commit `.env.local` or any file containing real API keys
- `.gitignore` covers `.env` and `.env.*` — keep it that way
- If you accidentally expose a key, rotate it immediately at the provider's dashboard
