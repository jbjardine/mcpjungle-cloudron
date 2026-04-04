# Upstream Patches

This app rebuilds the embedded `mcpjungle` binary from pinned upstream sources so the image can carry two small, reviewable fixes:

- `patches/mcp-go/0001-stdio-close-timeout.patch`
  Adds a bounded shutdown for stdio subprocesses. If a managed stdio server does not exit after stdin is closed, the transport now sends `SIGTERM`, then `SIGKILL` after a short grace period.
- `patches/mcpjungle/0001-stderr-shutdown-log.patch`
  Distinguishes a real stderr EOF from the client intentionally closing the stderr pipe during shutdown, which avoids misleading "exited gracefully" logs.

The sources are pinned in `Dockerfile` with:

- `MCPJUNGLE_REF=fe0e92f9d37d523687f4df48833d25dfc8a66df8` (`mcpjungle` tag `0.3.6`)
- `MCP_GO_REF=a1dd4efa3cc999c162642c4bd19016219d837072` (`mcp-go` tag `v0.41.1`)

These patches were validated on the Cloudron staging clone `mcp-runtime-smoke.vps.jbprive.fr` on April 4, 2026:

- `n8n-api__n8n_health_check` returned `HTTP 200` in about `2.3s`
- `n8n-api__tools_documentation` returned `HTTP 200` in about `2.2s`
- no lingering `n8n-mcp` subprocesses remained after the requests completed
