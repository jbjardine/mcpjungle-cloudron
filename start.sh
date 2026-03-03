#!/bin/bash
set -eu

echo "==> MCPJungle Cloudron App starting..."

# Cloudron provides PostgreSQL addon env vars
# Construct DATABASE_URL from Cloudron's environment
export DATABASE_URL="${CLOUDRON_POSTGRESQL_URL}"

# Server mode
export SERVER_MODE="${SERVER_MODE:-development}"

# Disable OpenTelemetry by default
export OTEL_ENABLED="${OTEL_ENABLED:-false}"

# Timeout for MCP server init
export MCP_SERVER_INIT_REQ_TIMEOUT_SEC="${MCP_SERVER_INIT_REQ_TIMEOUT_SEC:-30}"

# Persistent credentials directory
mkdir -p /app/data/credentials

echo "==> DATABASE_URL configured from Cloudron PostgreSQL addon"
echo "==> Credentials directory: /app/data/credentials/"
echo "==> Starting MCPJungle gateway on port 8080..."

# Start MCPJungle
exec /mcpjungle start
