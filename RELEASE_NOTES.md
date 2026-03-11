## What's New

- Add `mcpjungle-admin` for app-owned MCP lifecycle management
- Persist managed MCP state in `/app/data/.mcpjungle-managed/registry.json`
- Add managed types for `npm_package`, `uvx_package`, `local_bundle`, `http_remote`, and `custom_command`
- Reconcile managed servers automatically at boot after MCPJungle `/health` is green
- Keep manually registered MCPJungle servers untouched during reconcile
- Add import flow for existing MCPJungle servers with `mcpjungle-admin import-existing --all`
- Add rollback-aware reconcile and update flow based on native `register` / `deregister` operations
- Add unit tests for registry management, type detection, and reconcile rollback
