# Contributing

## Setup

```bash
git clone https://github.com/sittiev/audio_fidelity_mcp.git
cd audio_fidelity_mcp
uv sync          # installs runtime + dev dependencies
```

## Checks before a PR

```bash
uv run ruff check .        # lint
uv run pytest tests/ -q    # unit tests (pure-math core, fast)
```

The server itself needs no build step: `uv run audio_fidelity_mcp.py` runs it headless
over stdio (the `# /// script` block at the top of the file declares the dependencies).

## Commit style

Conventional Commits, imperative subject, ≤50 chars when possible
(`feat:`, `fix:`, `docs:`, `vendor:`, `build:`). Body only for non-obvious *why*.

## License

By contributing you agree your work is distributed under GPL-3.0-or-later.
The bundled `vendor/peaqb` is GPLv2 (its `COPYING` must ship with redistributions).
