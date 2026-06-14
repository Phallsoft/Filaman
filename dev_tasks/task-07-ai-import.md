# Task 07 — AI import

## Goal
Create a spool from a product URL or pasted text via LLM extraction, with preview/edit.

## Deliverables
- `app/services/ai_import.py`:
  - `fetch_page_text(url)`: direct httpx fetch first (timeout, 2 MB cap, manual redirect validation, SSRF guard blocking private/loopback/link-local IPs, HTML→text strip); falls back to Bright Data MCP (`scrape_as_markdown` via streamable HTTP JSON-RPC) when `BRIGHTDATA_MCP_URL` is set and the direct fetch fails — or when it succeeds but extraction finds nothing (bot-protection pages)
  - `extract_fields(text)`: OpenAI-compatible `/chat/completions` call → JSON `{manufacturer, material, color_name, color_hex, sku, weight_g, qty}`
- `app/routers/ai_import.py`:
  - `GET /import`: URL field + paste textarea
  - `POST /import/parse`: fetch/extract → preview form, pre-filled and fully editable (datalists of existing values)
  - `POST /import/save`: case-insensitive find-or-create of manufacturer/material/color → spool + inventory (merge rule applies)
- Graceful failures: missing API key, fetch errors, unparseable LLM output, or empty extraction → flash error suggesting the user copy/paste the product text instead.

## Config
`AI_BASE_URL` (default OpenRouter), `AI_API_KEY`, `AI_MODEL`, `BRIGHTDATA_MCP_URL` (optional).
