# Security Model

## Architecture

MCPJungle-Cloudron runs three processes inside a single Cloudron container:

| Process | Port | User | Role |
|---------|------|------|------|
| nginx | 8080 (external) | root | Reverse proxy, rate limiting, header stripping |
| mcpjungle | 8081 (internal) | cloudron | MCP gateway (Go binary) |
| admin-api | 8082 (internal) | cloudron | Python REST API for dashboard |

## Authentication

### Admin Dashboard (`/admin/`)
Protected by **Cloudron proxyAuth**. Only authenticated Cloudron workspace users
can access the dashboard. Cloudron's reverse proxy injects `X-Cloudron-User`.

### Admin API (`/_api/`)
**Not** behind proxyAuth. Authenticated via Bearer token only.
nginx strips `X-Cloudron-User` on this path to prevent header forgery.
The Bearer token is a 64-char hex token generated at each container boot.

### MCP Endpoint (`/mcp`)
Authenticated via MCPJungle's own Bearer token system (API keys created via
`mcpjungle create mcp-client`). nginx strips `X-Cloudron-User` on this path.

## Known Limitations

### Same-UID Isolation
All three processes and all managed MCP servers run as the `cloudron` Unix user.
This means:
- A compromised MCP server process **can read** other servers' secret files
  (`/app/data/.mcpjungle-managed/secrets/*.json`)
- A compromised MCP server process **can read** the admin token and the gateway
  access token

**Mitigation**: This is a Cloudron platform limitation (apps run as a single UID).
Only install MCP servers you trust. Do not install untrusted third-party MCP
packages on a production instance that holds sensitive API keys.

### Error Message Leakage
Server startup errors may contain fragments of environment variables or internal
paths. The admin API truncates and redacts sensitive patterns (tokens, passwords,
API keys) from error messages, but some information may still be visible to
authenticated admin users.

### Admin Token in Dashboard HTML
The admin Bearer token is injected into the dashboard HTML at serve time.
The response is marked `Cache-Control: no-store` and protected by CSP, but
browser extensions or devtools could observe it. The token is regenerated on
every container restart.

## Secret Storage

- Secrets are stored in `/app/data/.mcpjungle-managed/secrets/<server>.json`
  with `0600` permissions
- The admin token file is `0600` permissions
- Sensitive env vars (matching TOKEN, SECRET, PASSWORD, API_KEY, etc.) are
  automatically detected and:
  - Stored in separate secret files (not in registry.json)
  - Masked as `********` in API responses
  - Stripped from `last_known_good` cached configs
  - Excluded from audit log entries

## Reporting Vulnerabilities

If you find a security vulnerability, please report it privately via
GitHub Security Advisories on the repository, or email the maintainer directly.
Do not open a public issue for security vulnerabilities.
