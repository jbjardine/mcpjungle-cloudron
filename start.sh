#!/bin/bash

echo "==> MCPJungle Cloudron App starting..."

# Debug: show mount points and /app structure
echo "==> Mount points:"
mount | grep /app || true
echo "==> /app contents:"
ls -la /app/ || true

# /app/data is auto-mounted writable by Cloudron (with cloudron/base)
if mkdir -p /app/data/credentials 2>/dev/null; then
    chown -R cloudron:cloudron /app/data 2>/dev/null || true
    echo "==> /app/data is writable"
else
    echo "==> WARNING: /app/data is NOT writable, using /tmp as fallback"
    export HOME="/tmp"
fi

set -euo pipefail

# Cloudron provides PostgreSQL addon env vars
export DATABASE_URL="${CLOUDRON_POSTGRESQL_URL}"

# Server mode
export SERVER_MODE="${SERVER_MODE:-enterprise}"

# Disable OpenTelemetry by default
export OTEL_ENABLED="${OTEL_ENABLED:-false}"

# Timeout for MCP server init (60s to give uvx time to download packages)
export MCP_SERVER_INIT_REQ_TIMEOUT_SEC="${MCP_SERVER_INIT_REQ_TIMEOUT_SEC:-60}"

# Make uv/uvx tools accessible
export PATH="/root/.local/bin:/usr/local/bin:${PATH}"

# HOME for uvx cache (persistent across restarts)
export HOME="/app/data"

echo "==> DATABASE_URL configured from Cloudron PostgreSQL addon"
echo "==> Starting MCPJungle gateway on port 8080..."

# Start MCPJungle
exec /usr/local/bin/mcpjungle start
