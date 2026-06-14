# Task 09 — Polish, docs & verification

## Goal
Final UX pass and shipping docs.

## Deliverables
- Mobile/responsive pass on all pages; empty states
- README: features, quickstart (`docker compose up`), env var reference
- Smoke test checklist:
  1. Fresh start → `/setup` → create user → login/logout
  2. Lookup CRUD + quick-create from spool form without leaving page
  3. Create spool qty 3 → inventory 3; ± adjust
  4. AI import (URL and pasted text) → preview → save creates dependent records
  5. Backup → reset (typed confirmation) → restore → data returns
  6. Theme toggle, mobile viewport
  7. Negative: bad password, invalid restore file, missing AI key
