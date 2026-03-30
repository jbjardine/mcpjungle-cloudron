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
export MCP_SERVER_INIT_REQ_TIMEOUT_SEC="${MCP_SERVER_INIT_REQ_TIMEOUT_SEC:-300}"

# Prefer distro Node.js over the legacy Cloudron base Node in PATH.
export PATH="/usr/bin:/root/.local/bin:/usr/local/bin:${PATH}"

# HOME for uvx cache, kept across restarts
export HOME="${APP_HOME}"

# MCPJungle listens on 8081; nginx fronts it on 8080
export PORT=8081

mkdir -p /app/data/.mcpjungle-managed/work /app/data/.mcpjungle-managed/secrets /app/data/mcp-bundles 2>/dev/null || true
chown -R cloudron:cloudron /app/data 2>/dev/null || true
chmod 700 /app/data/.mcpjungle-managed /app/data/.mcpjungle-managed/work /app/data/.mcpjungle-managed/secrets /app/data/mcp-bundles 2>/dev/null || true
chmod 600 /app/data/.mcpjungle-managed/registry.json /app/data/.mcpjungle-managed/secrets/*.json 2>/dev/null || true
# Auto-create .mcpjungle.conf if missing (first boot)
if [ ! -f /app/data/.mcpjungle.conf ]; then
    echo "==> Creating .mcpjungle.conf (first boot)"
    ACCESS_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    cat > /app/data/.mcpjungle.conf <<CONF
registry_url: http://127.0.0.1:8081
access_token: ${ACCESS_TOKEN}
CONF
fi
chmod 600 /app/data/.mcpjungle.conf 2>/dev/null || true

# Ensure nginx bridges include file exists (empty is fine - populated by admin API)
touch /app/data/.mcpjungle-managed/nginx-bridges.conf

# Remove default nginx site to avoid port conflicts
rm -f /etc/nginx/sites-enabled/default

# Ensure /run directory exists for nginx temp/pid files
mkdir -p /run

echo "==> DATABASE_URL configured from Cloudron PostgreSQL addon"

# Fix permissions before handing off to supervisor
chown -R cloudron:cloudron /app/data/.mcpjungle-managed 2>/dev/null || true
chown -R cloudron:cloudron /app/data/.local 2>/dev/null || true
chown -R cloudron:cloudron /app/data/.cache 2>/dev/null || true
chown -R cloudron:cloudron /app/data/.npm 2>/dev/null || true
chown cloudron:cloudron /app/data/.mcpjungle.conf 2>/dev/null || true

# Generate admin session token (Python admin API reads it at startup)
ADMIN_TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(32))")
echo "$ADMIN_TOKEN" > /app/data/.mcpjungle-managed/admin-token
chmod 600 /app/data/.mcpjungle-managed/admin-token
export MCPJUNGLE_ADMIN_TOKEN="$ADMIN_TOKEN"
echo "==> Admin session token generated"

# Start supervisord IMMEDIATELY - nginx, gateway, admin-api all come up now.
# Boot reconcile runs as a separate oneshot supervisor program (non-blocking).
echo "==> Handing off to supervisord (nginx:8080, mcpjungle:8081, admin-api:8082)"
echo "==> Boot reconcile will run in background via supervisor (non-blocking)"
exec /usr/bin/supervisord --configuration /app/code/supervisor.conf --nodaemon
