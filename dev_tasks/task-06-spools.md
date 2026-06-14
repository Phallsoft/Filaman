# Task 06 — Spools & inventory

## Goal
Frictionless spool entry with quantities.

## Deliverables
- `app/routers/spools.py`:
  - `GET /` + `GET /spools`: searchable list (mfg/material/color/sku), swatches, qty column
  - `GET /spools/new`, `POST /spools`: form with FK dropdowns, each with inline "+ new" quick-create (htmx, no page leave); `qty` field on create
  - `GET /spools/{id}/edit`, `POST /spools/{id}`: edit incl. qty
  - `POST /spools/{id}/delete`
  - `POST /spools/{id}/adjust` (htmx ±): returns updated qty cell partial
- Duplicate merge: creating a spool identical on (mfg, material, color, weight, sku) increments qty instead of duplicating.
