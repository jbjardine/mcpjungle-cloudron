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

# Canonical runtime contract for shells, supervisor, and all child processes.
export MCPJUNGLE_DATA_ROOT="${APP_HOME}"
export HOME="${APP_HOME}"
export PATH="/usr/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/sbin:/bin:/root/.local/bin"
export LANG="C.UTF-8"
export LC_ALL="C.UTF-8"
export TMPDIR="${TMPDIR:-/tmp}"
export XDG_CONFIG_HOME="${APP_HOME}/.config"
export XDG_CACHE_HOME="${APP_HOME}/.cache"
export XDG_DATA_HOME="${APP_HOME}/.local/share"

# MCPJungle listens on 8081; nginx fronts it on 8080
export PORT=8081

MANAGED_ROOT="${APP_HOME}/.mcpjungle-managed"
BUNDLES_ROOT="${APP_HOME}/mcp-bundles"
AUTH_CONF="${APP_HOME}/.mcpjungle.conf"

mkdir -p \
    "${MANAGED_ROOT}/work" \
    "${MANAGED_ROOT}/secrets" \
    "${BUNDLES_ROOT}" \
    "${XDG_CONFIG_HOME}" \
    "${XDG_CACHE_HOME}" \
    "${XDG_DATA_HOME}" \
    "${APP_HOME}/.npm" \
    2>/dev/null || true
chown -R cloudron:cloudron "${APP_HOME}" 2>/dev/null || true
chmod 700 "${MANAGED_ROOT}" "${MANAGED_ROOT}/work" "${MANAGED_ROOT}/secrets" "${BUNDLES_ROOT}" 2>/dev/null || true
chmod 600 "${MANAGED_ROOT}/registry.json" "${MANAGED_ROOT}/secrets/"*.json 2>/dev/null || true

# Auto-create .mcpjungle.conf if missing (first boot)
if [ ! -f "${AUTH_CONF}" ]; then
    echo "==> Creating .mcpjungle.conf (first boot)"
    ACCESS_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    cat > "${AUTH_CONF}" <<CONF
registry_url: http://127.0.0.1:8081
access_token: ${ACCESS_TOKEN}
CONF
fi
chmod 600 "${AUTH_CONF}" 2>/dev/null || true

# Ensure nginx bridges include file exists (empty is fine - populated by admin API)
touch "${MANAGED_ROOT}/nginx-bridges.conf"

# Remove default nginx site to avoid port conflicts
rm -f /etc/nginx/sites-enabled/default

# Ensure /run directory exists for nginx temp/pid files
mkdir -p /run

echo "==> DATABASE_URL configured from Cloudron PostgreSQL addon"

# Fix permissions before handing off to supervisor
chown -R cloudron:cloudron "${MANAGED_ROOT}" 2>/dev/null || true
chown -R cloudron:cloudron "${APP_HOME}/.local" 2>/dev/null || true
chown -R cloudron:cloudron "${APP_HOME}/.cache" 2>/dev/null || true
chown -R cloudron:cloudron "${APP_HOME}/.config" 2>/dev/null || true
chown -R cloudron:cloudron "${APP_HOME}/.npm" 2>/dev/null || true
chown cloudron:cloudron "${AUTH_CONF}" 2>/dev/null || true

# Generate admin session token (Python admin API reads it at startup)
ADMIN_TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(32))")
echo "$ADMIN_TOKEN" > "${MANAGED_ROOT}/admin-token"
chmod 600 "${MANAGED_ROOT}/admin-token"
export MCPJUNGLE_ADMIN_TOKEN="$ADMIN_TOKEN"
echo "==> Admin session token generated"

# Start supervisord IMMEDIATELY - nginx, gateway, admin-api all come up now.
# Boot reconcile runs as a separate oneshot supervisor program (non-blocking).
echo "==> Handing off to supervisord (nginx:8080, mcpjungle:8081, admin-api:8082)"
echo "==> Boot reconcile will run in background via supervisor (non-blocking)"
exec /usr/bin/supervisord --configuration /app/code/supervisor.conf --nodaemon
