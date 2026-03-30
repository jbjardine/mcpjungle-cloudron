FROM ghcr.io/mcpjungle/mcpjungle:0.3.6-stdio AS mcpjungle

FROM cloudron/base:4.2.0@sha256:46da2fffb36353ef714f97ae8e962bd2c212ca091108d768ba473078319a47f4

# Copy MCPJungle binary from the official image
COPY --from=mcpjungle /mcpjungle /usr/local/bin/mcpjungle

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

ENV PYTHONPATH="/app/code"

EXPOSE 8080

CMD ["/app/code/start.sh"]
