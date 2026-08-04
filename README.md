# silpo-agent-cli

Personal CLI wrapper over the Silpo MCP server (`https://mcp.silpo.ua/mcp`).
See `idea.md`, `prd_reorder_optimizer.md`, and `CONTEXT.md` for the product
context and domain glossary.

## Setup

```
uv sync
uv run silpo-agent
```

First run of any command that touches the MCP server opens a browser for
OAuth2.1+PKCE login; the token is cached in the OS keyring afterward.

## Tests

```
uv run pytest
```
