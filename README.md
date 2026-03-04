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
- **Pre-installed runtimes** — Node.js 20, Python 3, uv/uvx ready for any MCP server
- **Streamable HTTP** — modern MCP transport with session management

## How It Works

MCPJungle runs on port `8080` in Enterprise mode. On first start it initializes the database schema and creates an admin user. You then register MCP servers via CLI inside the container and create client tokens for authentication.

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

### 3. Register MCP Servers

Create a JSON config for each server:

```json
{
  "name": "my-mcp-server",
  "transport": "stdio",
  "command": "npx",
  "args": ["-y", "my-mcp-package@latest"],
  "env": {
    "API_KEY": "your-key"
  }
}
```

Then register it:

```bash
mcpjungle register --config /path/to/config.json
```

### 4. Connect Claude Code

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

## Repository Structure

```
.
├── CloudronManifest.json   # Cloudron app manifest
├── Dockerfile              # Multi-stage build (mcpjungle + cloudron/base)
├── start.sh                # Enterprise mode startup script
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
