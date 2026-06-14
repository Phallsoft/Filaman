# Task 03 — Auth & first-run setup

## Goal
Form-based auth with first-run user creation.

## Deliverables
- `app/auth.py`: bcrypt hash/verify, CSRF token helpers
- Global HTTP middleware:
  - No users exist → all traffic redirected to `/setup`
  - Not logged in → redirect to `/login`
  - `/static`, `/login`, `/setup` are public
- `app/routers/auth_routes.py`: GET/POST `/setup` (only when 0 users), GET/POST `/login`, POST `/logout`
- Signed session cookie via Starlette `SessionMiddleware` (SameSite=lax), CSRF hidden input on all forms.

## Notes
- Single shared inventory; Users table only gates login.
