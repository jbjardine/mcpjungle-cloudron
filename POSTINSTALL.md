MCPJungle is ready.

## Admin Dashboard

Manage your MCP servers at: `https://<your-domain>/admin`

The dashboard is protected by Cloudron SSO - only Cloudron users can access it.

## MCP Gateway Endpoint

Connect AI clients to: `https://<your-domain>/mcp`

Use a Bearer token for authentication. Create API keys from the dashboard (API Keys tab).

## Connect Claude Code

Add to your project's `.mcp.json`:

```json
{
  "mcpServers": {
    "mcpjungle": {
      "type": "streamable-http",
      "url": "https://<your-domain>/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_API_KEY"
      }
    }
  }
}
```

## Bridge Port

Servers with a bridge port get their own URL at `https://<your-domain>/bridge/<server-name>/`. Configure the bridge port in server settings.

## Useful Commands

```bash
mcpjungle-admin doctor          # Check health of all components
mcpjungle-admin list-managed    # List managed MCP servers
mcpjungle-admin reconcile       # Force re-register all managed servers
```

## Documentation

Full documentation: [README.md](https://github.com/jbjardine/mcpjungle-cloudron#readme)
