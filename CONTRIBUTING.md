# Contributing

This started as a personal tool, shared publicly as-is. PRs and issues are
welcome, but note running it end-to-end needs a real Silpo account (the MCP
server it wraps has no sandbox/mock mode) — the test suite doesn't.

## Setup

```bash
uv sync
uv run pytest
```

## Guidelines

- **Tests are required for behavior changes.** This repo is TDD-first: a
  seam-level test (function or CLI `main()`, see `tests/` for the existing
  pattern) accompanies any new flag, command, or fixed bug — not just a
  manual check.
- Match the existing style: no comments explaining *what* code does (names
  should do that); comments only for non-obvious *why* (a schema gotcha, a
  workaround, an invariant).
- Silpo's MCP tool responses are the real contract this project is built
  against. If you're touching anything that calls `client.call(...)`, check
  `docs/mcp_schema.md` isn't tracked in this public repo (it's the
  live-verified schema notes, kept local) — describe the real shape you
  observed in your PR instead of guessing.
- Keep PRs scoped to one behavior. Small, reviewable diffs over sweeping
  refactors.

## Reporting bugs

Open an issue with the command you ran, the flags, and what happened vs.
what you expected. Redact anything from your own Silpo account (addresses,
order contents) you don't want public — this tool talks to a real account,
so its own output can contain personal data.

## License

By contributing, you agree your contribution is licensed under this
project's [MIT license](LICENSE).
