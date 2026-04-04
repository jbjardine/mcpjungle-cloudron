FROM golang:1.26.1-bookworm AS mcpjungle-builder

ARG MCPJUNGLE_REF=fe0e92f9d37d523687f4df48833d25dfc8a66df8
ARG MCP_GO_REF=a1dd4efa3cc999c162642c4bd19016219d837072

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY patches /tmp/patches

RUN git clone https://github.com/mcpjungle/mcpjungle.git /tmp/mcpjungle \
    && cd /tmp/mcpjungle \
    && git checkout "${MCPJUNGLE_REF}"

RUN git clone https://github.com/mark3labs/mcp-go.git /tmp/mcp-go \
    && cd /tmp/mcp-go \
    && git checkout "${MCP_GO_REF}" \
    && git apply /tmp/patches/mcp-go/0001-stdio-close-timeout.patch

RUN cd /tmp/mcpjungle \
    && git apply /tmp/patches/mcpjungle/0001-stderr-shutdown-log.patch \
    && go mod edit -replace github.com/mark3labs/mcp-go=/tmp/mcp-go \
    && go mod tidy \
    && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /tmp/mcpjungle-bin .

FROM cloudron/base:4.2.0@sha256:46da2fffb36353ef714f97ae8e962bd2c212ca091108d768ba473078319a47f4

# Build MCPJungle from pinned upstream source so we can carry minimal stdio patches.
COPY --from=mcpjungle-builder /tmp/mcpjungle-bin /usr/local/bin/mcpjungle

# Install Python 3, Node.js 20, and uv/uvx for MCP servers
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    ca-certificates \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/nginx/sites-enabled/default

# Install Node.js 20 via NodeSource
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install uv (which provides uvx)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && cp /root/.local/bin/uv /usr/local/bin/uv \
    && cp /root/.local/bin/uvx /usr/local/bin/uvx \
    && chmod 755 /usr/local/bin/uv /usr/local/bin/uvx

# Ensure /app/data mount point exists for Cloudron volume
RUN mkdir -p /app/data /app/code

# Copy Cloudron manifest and app icons
COPY CloudronManifest.json /app/code/CloudronManifest.json
COPY icon.png /app/code/icon.png
COPY icon.svg /app/code/icon.svg

# Copy app-owned MCP management tooling
COPY mcpjungle_admin /app/code/mcpjungle_admin
COPY bin/mcpjungle-admin /usr/local/bin/mcpjungle-admin
COPY bin/lazy-mcp-wrapper /usr/local/bin/lazy-mcp-wrapper
RUN sed -i 's/\r$//' /usr/local/bin/mcpjungle-admin /usr/local/bin/lazy-mcp-wrapper \
    && chmod +x /usr/local/bin/mcpjungle-admin /usr/local/bin/lazy-mcp-wrapper

# Copy nginx and supervisor configs
COPY nginx.conf /app/code/nginx.conf
COPY supervisor.conf /app/code/supervisor.conf

# Copy admin static files
COPY admin/static /app/code/admin/static

# Copy start script
COPY start.sh /app/code/start.sh
RUN sed -i 's/\r$//' /app/code/start.sh \
    && chmod +x /app/code/start.sh

ENV APP_HOME="/app/data" \
    HOME="/app/data" \
    MCPJUNGLE_DATA_ROOT="/app/data" \
    LANG="C.UTF-8" \
    LC_ALL="C.UTF-8" \
    TMPDIR="/tmp" \
    XDG_CONFIG_HOME="/app/data/.config" \
    XDG_CACHE_HOME="/app/data/.cache" \
    XDG_DATA_HOME="/app/data/.local/share" \
    PATH="/usr/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/sbin:/bin:/root/.local/bin" \
    PYTHONPATH="/app/code"

EXPOSE 8080

CMD ["/app/code/start.sh"]
