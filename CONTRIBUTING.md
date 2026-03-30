# Contributing to MCPJungle for Cloudron

Thanks for your interest in contributing! This guide covers everything you need to get started.

## Development Setup

### Prerequisites

- [Cloudron CLI](https://docs.cloudron.io/packaging/cli/) (`npm install -g cloudron`)
- Docker (for building images)
- Python 3.11+ (for admin module development)
- A Cloudron instance for testing

### Local Development

```bash
git clone https://github.com/jbjardine/mcpjungle-cloudron.git
cd mcpjungle-cloudron

# Run tests
python -m pytest tests/ -v

# Build the Docker image
cloudron build
```

### Project Structure

```
mcpjungle_admin/     # Python admin module (REST API, CLI, reconciler)
admin/static/        # Dashboard UI (single-file SPA)
tests/               # Unit tests
start.sh             # Container entrypoint
nginx.conf           # Reverse proxy configuration
supervisor.conf      # Process management
```

## Making Changes

1. **Fork and branch** from `main`.
2. **Keep changes focused**: one feature or fix per PR.
3. **Add tests** for new functionality in `tests/`.
4. **Update CHANGELOG.md** under an `[Unreleased]` section.
5. **Test on a real Cloudron instance** if your change affects boot, networking, or process management.

## Code Style

- **Python**: follow existing conventions in `mcpjungle_admin/` (standard library where possible, no heavy dependencies).
- **HTML/CSS/JS**: the dashboard is a single-file SPA in `admin/static/index.html` using vanilla JS and CSS custom properties. No framework, no build step.
- **Shell**: `start.sh` runs in `bash`. Keep it portable and well-commented.

## Reporting Issues

- Use [GitHub Issues](https://github.com/jbjardine/mcpjungle-cloudron/issues) for bugs and feature requests.
- For security vulnerabilities, see [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
