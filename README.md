# MCPJungle for Cloudron

[![Release](https://img.shields.io/github/v/release/jbjardine/mcpjungle-cloudron?sort=semver)](https://github.com/jbjardine/mcpjungle-cloudron/releases)
[![GHCR](https://img.shields.io/github/v/release/jbjardine/mcpjungle-cloudron?label=GHCR&sort=semver)](https://github.com/jbjardine/mcpjungle-cloudron/pkgs/container/mcpjungle-cloudron)
[![Cloudron](https://img.shields.io/badge/Cloudron-App-blue)](https://www.cloudron.io/)
[![MCPJungle](https://img.shields.io/badge/MCPJungle-Gateway-green)](https://github.com/mcpjungle/mcpjungle)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

<br/>
If this project is useful to you, you can support its development here:

[![Support](https://img.shields.io/badge/Support-Buy%20Me%20a%20Coffee-1f2937)](https://www.buymeacoffee.com/jbjardine)

## Overview

Cloudron-ready package for [MCPJungle](https://github.com/mcpjungle/mcpjungle), a self-hosted MCP Gateway that centralizes all your MCP servers behind a single authenticated HTTPS endpoint.

Register any MCP server (analytics, WordPress, custom tools...) and access them all from Claude Code, Claude Desktop, or any MCP-compatible client through one URL with Bearer token authentication.

## Highlights

- **Enterprise mode** with Bearer token authentication out of the box
- **One endpoint, all servers** — register unlimited MCP servers behind a single URL
- **Cloudron-native** — PostgreSQL addon for config, local storage for credentials
- **Managed MCP lifecycle** — install, update, remove, and reconcile servers with `mcpjungle-admin`
- **Persistent app-owned state** — managed MCP metadata lives in `/app/data/.mcpjungle-managed/`
- **Pre-installed runtimes** — Node.js 20, Python 3, uv/uvx ready for any MCP server
- **Streamable HTTP** — modern MCP transport with session management

## How It Works

MCPJungle runs on port `8080` in Enterprise mode. On first start it initializes the database schema and creates an admin user. The Cloudron image now adds an app-owned management layer that stores managed MCP definitions in `/app/data/.mcpjungle-managed/registry.json` and translates them into native MCPJungle `register`, `deregister`, `list`, and `export` operations.

At boot, the app:

1. Starts MCPJungle.
2. Waits for `GET /health` to turn green.
3. Runs `mcpjungle-admin reconcile` when `/app/data/.mcpjungle.conf` is present.
4. Reapplies only `managed=true` servers and leaves manually registered servers untouched.

```
Claude Code / Desktop
        │
        ▼
  https://mcpjungle.your-domain.com/mcp
  (Bearer token auth)
        │
        ▼
  ┌─────────────────┐
  │   MCPJungle GW  │
  │   (Enterprise)  │
  └───────┬─────────┘
          │
    ┌─────┼──────┐
    ▼     ▼      ▼
  MCP1  MCP2   MCP3
```

## Quick Start

### 1. Install on Cloudron

```bash
cloudron install --image ghcr.io/jbjardine/mcpjungle-cloudron:latest --location mcpjungle.example.com
```

### 2. Initialize Enterprise Mode

SSH into the container and run:

```bash
# Initialize the server
mcpjungle init-server

# Create admin user
mcpjungle login
# → Enter username/password when prompted

# Create a client token for Claude Code
mcpjungle create mcp-client --name "claude-code"
# → Save the Bearer token
```

### 3. Import any servers you already registered manually

If you already have MCP servers in MCPJungle, import them into the managed registry first:

```bash
mcpjungle-admin import-existing --all
```

This reads the live MCPJungle configuration using the native export/list flow and writes a persistent registry to `/app/data/.mcpjungle-managed/registry.json`.

Ambiguous servers are imported as `custom_command` entries so they stay manageable without rewriting them by hand.

### 4. Install managed MCP servers

Install a pinned npm-based MCP server:

```bash
mcpjungle-admin install \
  --type npm_package \
  --name wordpress-farniente \
  --description "WordPress MCP adapter" \
  --package @automattic/mcp-wordpress-remote \
  --env WP_API_URL=https://example.com/wp-json/mcp/server \
  --env WP_API_USERNAME=admin \
  --env WP_API_PASSWORD=secret
```

Install a pinned `uvx` server:

```bash
mcpjungle-admin install \
  --type uvx_package \
  --name analytics-mcp \
  --package analytics-mcp
```

Install a remote HTTP MCP:

```bash
mcpjungle-admin install \
  --type http_remote \
  --name ida-pro-mcp \
  --url http://192.168.60.204:13337/mcp
```

Managed state is written automatically, then the app reconciles it into native MCPJungle server registrations.

### 5. Update, inspect, or remove managed MCP servers

List managed entries:

```bash
mcpjungle-admin list-managed
```

Update a pinned package to a specific version:

```bash
mcpjungle-admin update wordpress-farniente --to 1.2.3
```

Ask the app to resolve and pin the latest package version:

```bash
mcpjungle-admin update analytics-mcp
```

Check registry, auth config, and live health:

```bash
mcpjungle-admin doctor
```

Remove a managed server:

```bash
mcpjungle-admin remove wordpress-farniente
```

If a reconcile or update fails its healthcheck, the app rolls back to the last known good MCPJungle config when possible and marks the entry in error.

### 6. Connect Claude Code

Add to your `.mcp.json`:

```json
{
  "mcpServers": {
    "mcpjungle": {
      "type": "streamable-http",
      "url": "https://mcpjungle.example.com/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_CLIENT_TOKEN"
      }
    }
  }
}
```

## Managed vs. Manual Servers

Managed servers are stored in `/app/data/.mcpjungle-managed/registry.json` and reapplied automatically after Cloudron restarts or image upgrades.

Manual servers that you register directly with `mcpjungle register --conf ...` still work. They remain standard MCPJungle servers, and `mcpjungle-admin reconcile` does not delete or rewrite them unless you explicitly import them into the managed registry.

## Managed Types

The first version of `mcpjungle-admin` supports five managed types:

- `npm_package`
- `uvx_package`
- `local_bundle`
- `http_remote`
- `custom_command`

Version behavior:

- `npm_package` and `uvx_package` are pinned in the managed registry.
- `http_remote` keeps only configuration and health state. Remote binaries stay external.
- `local_bundle` and `custom_command` support manual or command-based update hooks.

## Persistent Paths

- Managed registry: `/app/data/.mcpjungle-managed/registry.json`
- Working state and rollback temp files: `/app/data/.mcpjungle-managed/work/`
- Local bundles: `/app/data/mcp-bundles/<name>/`
- MCPJungle auth/config file used at boot reconcile: `/app/data/.mcpjungle.conf`

## Repository Structure

```
.
├── CloudronManifest.json   # Cloudron app manifest
├── Dockerfile              # Multi-stage build (mcpjungle + cloudron/base)
├── bin/mcpjungle-admin     # Admin CLI entrypoint
├── mcpjungle_admin/        # Managed registry and reconcile logic
├── start.sh                # Enterprise mode startup script
├── tests/                  # Unit tests for registry and reconcile logic
├── icon.png                # App icon (256x256)
├── icon.svg                # Icon source
└── README.md
```

## Build and Deploy (Cloudron)

Install the Cloudron CLI:

```bash
npm install -g cloudron
```

Build the image:

```bash
cloudron build
```

Then install or update:

```bash
cloudron install --image <image-tag> --location mcpjungle.example.com
# or
cloudron update --app mcpjungle.example.com --image <image-tag>
```

## Release Tarball

Releases are created manually (workflow dispatch) and attach a tarball of the Cloudron package.

From GitHub Actions, run the **release** workflow and set the tag (e.g. `v1.0.0`). The workflow creates a **draft** release with the tarball.

## GHCR Images

When a release is **published**, a GHCR image is built and pushed:

```
ghcr.io/jbjardine/mcpjungle-cloudron:<tag>
ghcr.io/jbjardine/mcpjungle-cloudron:latest
```

You can install directly from GHCR with Cloudron:

```bash
cloudron install --image ghcr.io/jbjardine/mcpjungle-cloudron:<tag> --location <app-domain>
```

## Contributing

PRs are welcome. Keep changes minimal and documented.

## License

MIT. See [LICENSE](LICENSE).

## Credits

Built on top of [MCPJungle](https://github.com/mcpjungle/mcpjungle).
