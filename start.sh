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
    export APP_HOME="/app/data"
else
    echo "==> WARNING: /app/data is NOT writable, using /tmp as fallback"
    export APP_HOME="/tmp"
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

# Prefer distro Node.js over the legacy Cloudron base Node in PATH.
export PATH="/usr/bin:/root/.local/bin:/usr/local/bin:${PATH}"

# HOME for uvx cache, kept across restarts
export HOME="${APP_HOME}"

mkdir -p /app/data/.mcpjungle-managed/work /app/data/mcp-bundles 2>/dev/null || true
chmod 700 /app/data/.mcpjungle-managed /app/data/.mcpjungle-managed/work /app/data/mcp-bundles 2>/dev/null || true
chmod 600 /app/data/.mcpjungle-managed/registry.json /app/data/.mcpjungle-managed/secrets/*.json 2>/dev/null || true
if [ -f /app/data/.mcpjungle.conf ]; then
    chmod 600 /app/data/.mcpjungle.conf 2>/dev/null || true
fi

echo "==> DATABASE_URL configured from Cloudron PostgreSQL addon"
echo "==> Starting MCPJungle gateway on port 8080..."

wait_for_gateway_health() {
    local attempts=60
    local delay=2
    local url="http://127.0.0.1:8080/health"

    for ((i=1; i<=attempts; i++)); do
        if curl -fsS "$url" >/dev/null 2>&1; then
            echo "==> MCPJungle healthcheck is green"
            return 0
        fi
        sleep "$delay"
    done

    echo "==> MCPJungle healthcheck did not become ready in time"
    return 1
}

/usr/local/bin/mcpjungle start &
MCPJUNGLE_PID=$!

cleanup() {
    if kill -0 "$MCPJUNGLE_PID" >/dev/null 2>&1; then
        kill "$MCPJUNGLE_PID" >/dev/null 2>&1 || true
    fi
}

trap cleanup INT TERM

if wait_for_gateway_health; then
    if [ -f /app/data/.mcpjungle.conf ]; then
        echo "==> Reconciling managed MCP state from /app/data/.mcpjungle-managed"
        if ! /usr/local/bin/mcpjungle-admin reconcile; then
            echo "==> WARNING: managed reconcile reported errors"
        fi
    else
        echo "==> No /app/data/.mcpjungle.conf found, skipping managed reconcile"
    fi
else
    echo "==> WARNING: skipping managed reconcile because MCPJungle is not healthy yet"
fi

wait "$MCPJUNGLE_PID"
