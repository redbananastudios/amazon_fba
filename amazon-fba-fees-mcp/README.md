# amazon-fba-fees-mcp

MCP server exposing Amazon SP-API fee estimation, full profitability calculation (including VAT and ROI), and optional Google Sheets logging.

## Tools

- `estimate_fees` — FBA fee breakdown for an ASIN at a given selling price.
- `calculate_profitability` — fees + VAT + ROI, given cost and optional shipping.
- `save_to_sheet` — append the result of either tool to a configured Google Sheet.

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `SP_API_CLIENT_ID` | yes | SP-API LWA client id |
| `SP_API_CLIENT_SECRET` | yes | SP-API LWA client secret |
| `SP_API_REFRESH_TOKEN` | yes | SP-API refresh token for the seller |
| `GOOGLE_SHEETS_CREDENTIALS` | no | Path to Google service-account JSON; enables `save_to_sheet` |
| `GOOGLE_SHEET_ID` | no | Target sheet id; enables `save_to_sheet` |

Defaults: marketplace is UK (`A1F83G8C2ARO7P`) unless overridden per-call via `marketplace_id`.

For this workspace, all SP-API values live in `F:\My Drive\workspace\credentials.env` and are synced into `~/.claude/settings.json` by `sync-credentials.ps1`. Never hardcode.

## Build & run

```
npm install
npm run build
npm start
```

Tests: `npm test` (vitest, mocks SP-API and Sheets).

## Registering with Claude Code

Add to `.mcp.json` at the workspace root:

```json
{
  "mcpServers": {
    "amazon-fba-fees": {
      "command": "node",
      "args": ["<absolute path to this folder>/dist/index.js"],
      "env": {
        "SP_API_CLIENT_ID": "${SP_API_CLIENT_ID}",
        "SP_API_CLIENT_SECRET": "${SP_API_CLIENT_SECRET}",
        "SP_API_REFRESH_TOKEN": "${SP_API_REFRESH_TOKEN}"
      }
    }
  }
}
```
