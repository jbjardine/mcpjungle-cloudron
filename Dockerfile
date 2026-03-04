FROM ghcr.io/mcpjungle/mcpjungle:latest-stdio AS mcpjungle

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
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20 via NodeSource
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install uv (which provides uvx)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && ln -sf /root/.local/bin/uv /usr/local/bin/uv \
    && ln -sf /root/.local/bin/uvx /usr/local/bin/uvx

# Ensure /app/data mount point exists for Cloudron volume
RUN mkdir -p /app/data /app/code

# Copy start script
COPY start.sh /app/code/start.sh
RUN chmod +x /app/code/start.sh

EXPOSE 8080

CMD ["/app/code/start.sh"]
