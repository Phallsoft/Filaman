# Task 04 — Base UI shell & theme

## Goal
Clean, modern, mobile-friendly layout with light/dark theme.

## Deliverables
- `app/templates/base.html`: responsive nav, flash messages, htmx + Pico.css
- Pico.css v2 (CDN) + `app/static/custom.css` overrides
- Theme toggle: respects `prefers-color-scheme`, manual toggle persisted in `localStorage`, applied via `data-theme`
- htmx for inline interactions (quick-create, qty adjust)
- Flash message helper (session-based) rendered in base layout.
