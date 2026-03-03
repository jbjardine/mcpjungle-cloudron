#!/bin/bash
set -u

echo "==> MCPJungle Cloudron App starting..."
echo "==> Filesystem check:"
ls -la /app/data/ 2>&1 || echo "==> /app/data not accessible"
mount | grep /app/data || echo "==> no mount for /app/data"

# Cloudron provides PostgreSQL addon env vars
export DATABASE_URL="${CLOUDRON_POSTGRESQL_URL}"

# Server mode
export SERVER_MODE="${SERVER_MODE:-development}"

# Disable OpenTelemetry by default
export OTEL_ENABLED="${OTEL_ENABLED:-false}"

# Timeout for MCP server init
export MCP_SERVER_INIT_REQ_TIMEOUT_SEC="${MCP_SERVER_INIT_REQ_TIMEOUT_SEC:-30}"

# Persistent credentials directory (don't crash if fails)
mkdir -p /app/data/credentials 2>/dev/null || echo "==> WARN: could not create /app/data/credentials"

echo "==> DATABASE_URL configured from Cloudron PostgreSQL addon"
echo "==> Starting MCPJungle gateway on port 8080..."

# Start MCPJungle
exec /mcpjungle start
