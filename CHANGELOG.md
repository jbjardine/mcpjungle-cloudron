# Changelog

All notable changes to this project are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [2.3.7] - 2026-04-04

### Fixed

- Build the embedded `mcpjungle` gateway into a dedicated output file before copying it into the final image, so `/usr/local/bin/mcpjungle` is installed as an executable binary instead of a directory.
- Repair the `2.3.6` release packaging regression that prevented the gateway process from starting on Cloudron.

## [2.3.6] - 2026-04-04

### Changed

- Standardize the Cloudron runtime contract around `/app/data` for `HOME`, `PATH`, `TMPDIR`, locale, and XDG directories.
- Rebuild the embedded `mcpjungle` gateway from pinned upstream source instead of copying the prebuilt image directly.

### Fixed

- Make `mcpjungle-admin` resolve auth/config from the app data root instead of depending on the caller's shell `HOME`.
- Pass a canonical writable environment to managed `stdio` subprocesses and improve startup diagnostics with the effective runtime summary.
- Normalize ownership of managed registry and secret files after writes so Cloudron admin operations do not regress on permissions.
- Treat `register` timeouts as successful reconcile outcomes when the target server is already present, unchanged, and healthy.
- Patch stdio subprocess shutdown so `tools/invoke` does not hang indefinitely when a managed server keeps running after stdin is closed.
- Clarify stdio shutdown logs by distinguishing a real stderr EOF from the client intentionally closing the stderr pipe during cleanup.

## [2.3.0] - 2026-03-30

### Added

- Premium README with multi-IDE client configuration (Claude Code, Claude Desktop, Cursor, Windsurf).
- Complete CHANGELOG covering all versions from v1.0.0 through v2.3.0.
- CONTRIBUTING.md with development setup and guidelines.

### Fixed

- API key create/delete from dashboard: Go CLI expects positional argument, not `--name` flag.
- Admin API docstring updated with complete endpoint list.

### Changed

- POSTINSTALL.md rewritten with bridge port docs, CLI commands, and `.mcp.json` example.
- Hardened `.gitignore`: exclude `tmp/`, `.claude/`, `.cursor/`, `.vscode/`.
- Remove obsolete screenshots, session files, and duplicate version files.
- Release workflow uses auto-generated release notes instead of deleted `RELEASE_NOTES.md`.

## [2.2.1] - 2026-03-30

### Fixed

- Increase boot timeout to 60s (30s too short for heavy servers like n8n-mcp).
- Hide status badge on card hover to avoid overlap with toggle.

## [2.2.0] - 2026-03-30

### Added

- **Deferred boot reconcile**: servers register in the background so the dashboard is available immediately (~7s boot time).
- **Circuit breaker**: unhealthy server registrations are skipped after repeated failures to avoid blocking boot.

## [2.1.0] - 2026-03-30

### Changed

- Self-host icon via nginx instead of external CDN.
- Use glob pattern for nginx bridge includes (prevents startup failure when no bridge configs exist).

### Fixed

- Copy icon files into Docker image for favicon and branding.

## [2.0.0] - 2026-03-30

### Added

- **Complete UI redesign** from scratch: custom CSS design system replacing PicoCSS.
  - Dark OLED theme (`#0F172A` slate-900 background, `#22C55E` green-500 brand).
  - JetBrains Mono (headings/code) + IBM Plex Sans (body) typography.
  - CSS custom properties for all design tokens.
- **Enable/disable toggle** on each server card (CSS-only switch with ARIA).
- **Generic bridge port**: any MCP server can expose an HTTP bridge with auto-generated nginx proxy at `/bridge/<name>/`.
- **API Keys management** in dashboard: create and revoke API keys without CLI.
- **Native `<dialog>` elements** replacing browser `prompt()`/`alert()`/`confirm()`.
- Favicon support (`icon.svg` + `icon.png`).
- Improved relative time display ("just now" for < 5s).

### Fixed

- API keys: use CLI command instead of broken HTTP endpoint.
- Tool count display on server cards via Go API.
- Dialog centering with Cloudron sidebar offset.

### Breaking Changes

- **PicoCSS removed** - custom CSS design system replaces all previous styles.
- Dashboard HTML rewritten from scratch.

## [1.9.0] - 2026-03-30

### Added

- API Keys tab with create/revoke from the dashboard.
- Tool count badges on server cards.

### Fixed

- Fetch tool counts via Go API instead of slow CLI subprocess.
- Expanded card spans full width.
- Content Security Policy for inline styles.

## [1.8.0] - 2026-03-29

### Added

- Modern card layout with expandable server details.
- Boot splash page shown during container startup.

## [1.7.0] - 2026-03-29

### Security

- Harden for public release (7 fixes from multi-provider security audit):
  - Strip `X-Cloudron-User` header on API/MCP paths to prevent forgery.
  - Admin API binds `127.0.0.1:8082` only.
  - Secret values masked as `********` in all API responses.
  - Audit log excludes raw secret values.
  - Content Security Policy headers on dashboard.
  - Rate limiting on authentication endpoints.
  - Automatic registry backup before every mutation.

### Changed

- Universal base environment for all stdio servers.
- Auto-resolve registry URL from container network.

## [1.6.x] - 2026-03-29

### Added

- Stdio server startup diagnostics for silent failures.

### Fixed

- Auto-inject `HOME` env var for all stdio servers.
- Use internal port 8081 for reconcile during startup.
- Resolve symlinks and set `NODE_PATH` for npm-installed binaries.
- Increase timeouts to 300s for heavy npm MCP servers.

## [1.5.0] - 2026-03-28

### Added

- **Auto-install**: packages (uvx/npm) are pre-installed automatically on Add Server.
- Isolated dependencies per package (separate node_modules and venvs).
- Background worker pool for non-blocking installs.
- Dashboard shows installing status with pulsing indicator.
- Reinstall button on failure.
- `POST /servers/<name>/reinstall` endpoint.

### Fixed

- Set `HOME=/app/data` for uvx/npm subprocesses (cloudron user permissions).
- Copy uv/uvx binaries in Dockerfile instead of symlinking to `/root`.

## [1.4.0] - 2026-03-28

### Fixed

- Add Server form type values aligned with backend `MANAGED_TYPES`.
- Human-readable type labels in server list (npm, uvx, http...).
- Dialog CSS spacing and form submission compatibility.

## [1.3.0] - 2026-03-28

### Added

- **Admin Dashboard**: web-based management UI at `/admin`, protected by Cloudron SSO (proxyAuth).
- **Internal nginx reverse proxy**: routes `/admin` to dashboard, `/_api` to Python API, `/mcp` to MCPJungle gateway with SSE streaming support.
- **Supervisord process management**: nginx, MCPJungle, and admin API managed as supervised processes with auto-restart.
- **File locking** on `registry.json`: exclusive `fcntl.flock()` with backup before every mutation, `fsync` before atomic rename.
- **Schema v2 migration**: automatic migration from v1 to v2 on first load.
- **Credential management CLI**: `mcpjungle-admin creds-set` and `creds-list`.
- **Admin REST API**: `ThreadingHTTPServer` on port 8082 with endpoints for server CRUD, credentials, health, audit, reconcile.
- **Audit logging**: append-only JSON log of all admin mutations.
- Boot-time registry validation.

### Security

- Admin API binds `127.0.0.1:8082` only (not exposed externally).
- All admin API requests require `X-Cloudron-User` header (Cloudron SSO).
- Credential values never returned in API responses (masked with `********`).
- Audit log never contains raw secret values.

### Breaking Changes

- **Reinstall required**: adding proxyAuth addon requires Cloudron app reinstall (not just update).

## [1.2.0] - 2026-03-28

### Added

- Auto-update for managed npm and uvx MCP servers at boot and via `mcpjungle-admin auto-update`.
- Tool Groups: auto-create one group per registered MCP server.
- lazy-mcp configuration generator for client-side token reduction.

### Changed

- Pin MCPJungle upstream to `0.3.6-stdio` for reproducible builds.
- Boot sequence now includes sync-groups, optional auto-update, and lazy-mcp config generation.

## [1.1.0] - 2026-03-28

### Added

- App-owned managed MCP lifecycle management for Cloudron deployments.
- Generic managed file binding support.

### Security

- Moved managed secrets out of `registry.json` to separate files with `0600` permissions.
- Stripped sensitive values from rollback snapshots and CLI output.

## [1.0.0] - 2026-03-04

### Added

- Initial release of `mcpjungle-admin` for app-owned MCP lifecycle management.
- Persist managed MCP state in `/app/data/.mcpjungle-managed/registry.json`.
- Five managed types: `npm_package`, `uvx_package`, `local_bundle`, `http_remote`, `custom_command`.
- Automatic reconcile at boot after MCPJungle `/health` is green.
- Manually registered MCPJungle servers untouched during reconcile.
- Import existing servers with `mcpjungle-admin import-existing --all`.
- Rollback-aware reconcile and update flow.
- Unit tests for registry management, type detection, and reconcile rollback.

[Unreleased]: https://github.com/jbjardine/mcpjungle-cloudron/compare/v2.3.7...HEAD
[2.3.7]: https://github.com/jbjardine/mcpjungle-cloudron/compare/v2.3.6...v2.3.7
[2.3.6]: https://github.com/jbjardine/mcpjungle-cloudron/compare/v2.3.5...v2.3.6
[2.3.0]: https://github.com/jbjardine/mcpjungle-cloudron/compare/v2.2.1...v2.3.0
[2.2.1]: https://github.com/jbjardine/mcpjungle-cloudron/compare/v2.2.0...v2.2.1
[2.2.0]: https://github.com/jbjardine/mcpjungle-cloudron/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/jbjardine/mcpjungle-cloudron/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/jbjardine/mcpjungle-cloudron/compare/v1.9.0...v2.0.0
[1.9.0]: https://github.com/jbjardine/mcpjungle-cloudron/compare/v1.8.0...v1.9.0
[1.8.0]: https://github.com/jbjardine/mcpjungle-cloudron/compare/v1.7.0...v1.8.0
[1.7.0]: https://github.com/jbjardine/mcpjungle-cloudron/compare/v1.6.2...v1.7.0
[1.6.x]: https://github.com/jbjardine/mcpjungle-cloudron/compare/v1.5.0...v1.6.5
[1.5.0]: https://github.com/jbjardine/mcpjungle-cloudron/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/jbjardine/mcpjungle-cloudron/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/jbjardine/mcpjungle-cloudron/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/jbjardine/mcpjungle-cloudron/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/jbjardine/mcpjungle-cloudron/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/jbjardine/mcpjungle-cloudron/releases/tag/v1.0.0
