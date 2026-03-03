FROM ghcr.io/mcpjungle/mcpjungle:latest-stdio

# Cloudron runs apps as user cloudron (uid 1000)
# The base image already has Node.js 20, Python, uvx, npx

# Install additional tools that might be needed by MCP servers
USER root

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create Cloudron data directory for persistent storage
RUN mkdir -p /app/data /app/code

# Copy start script
COPY start.sh /app/code/start.sh
RUN chmod +x /app/code/start.sh

# Cloudron expects the app to listen on httpPort (8080)
EXPOSE 8080

CMD ["/app/code/start.sh"]
