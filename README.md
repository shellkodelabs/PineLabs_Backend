# PineLab Backend

Backend API for the PineLab helpdesk-automation console — the internal
tool support agents use to look up card-issuer BIN data and merchant SOP
(standard operating procedure) workbooks. This repository is the backend
counterpart to `PineLabs_Frontend` (React + Vite), which currently has no
API integration and runs entirely on hardcoded mock data.

> **Status:** foundation only. No business endpoints, database models, or
> authentication are implemented yet — see [Project status](#project-status) below.

## Technology stack

| Concern | Choice |
|---|---|
| Language / framework | Python 3.10+, FastAPI |
| Database | PostgreSQL |
| ORM | SQLAlchemy 2.x |
| Validation / settings | Pydantic v2, pydantic-settings |
| Migrations | Alembic |
| Testing | pytest, httpx (via FastAPI's `TestClient`) |

## Architecture

```text
API Router  →  Service  →  Repository  →  SQLAlchemy  →  PostgreSQL
```

- **API layer** (`app/api/`) — FastAPI routers. Parses requests, calls a
  service, returns a response schema. No business logic, no SQL.
- **Service layer** (`app/services/`) — business rules, orchestrates one
  or more repositories.
- **Repository layer** (`app/repositories/`) — all database queries live
  here, expressed via SQLAlchemy.
- **`app/models/`** — SQLAlchemy ORM models (the schema).
- **`app/schemas/`** — Pydantic request/response models (the API contract).
- **`app/core/`** — configuration, the DB session factory, the (stub)
  security dependency, and the shared error-handling machinery.

Full architecture and database design rationale live in the project's
design documentation (Parts 1–3 of the PineLab backend design).

## Project status

- FastAPI app with CORS and a standard JSON error envelope
- A single working endpoint: `GET /health`
- SQLAlchemy engine/session wiring
- **All 9 database tables implemented and migrated**: `merchants`,
  `bin_records`, `sop_sheets`, `sop_column_groups`, `sop_columns`,
  `sop_rows`, `users`, `user_sop_sheet_access`, `revisions` — models in
  `app/models/`, initial schema in
  `migrations/versions/9dad23937653_create_initial_schema.py`
- A development-only authentication stub (`app/core/security.py`) —
  **not real authentication**; it grants a fake identity to every caller
  and must be replaced once the identity provider is confirmed

Repositories, services, business API endpoints, Pydantic schemas, seed
data, and real authentication are added in later stages of this project —
not yet present. No data has been seeded into the database yet.

## Local setup prerequisites

- Python 3.10+
- A running PostgreSQL instance (only required once real database work
  begins — the `/health` endpoint and its test do not need one)

## Setup

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# then edit .env with real local values (never commit this file)
```

## Environment configuration

Configuration is read from environment variables (and `.env` in
development) via `app/core/config.py`. See `.env.example` for the full
list with placeholder values:

| Variable | Purpose |
|---|---|
| `ENVIRONMENT` | `development` / `test` / `staging` / `production` |
| `DATABASE_URL` | SQLAlchemy Postgres connection string — required, no default |
| `CORS_ORIGINS` | Comma-separated list of frontend origins allowed to call the API |
| `API_V1_PREFIX` | API mount prefix, currently `/api/v1` |

## Running the application

```bash
uvicorn app.main:app --reload
```

## Health check

```text
GET /health

200 OK
{ "status": "ok" }
```

This endpoint does not touch the database, so it works even before
`DATABASE_URL` points at a real, reachable Postgres instance.

## Running tests

```bash
pytest
```

Only a health-check smoke test exists at this stage
(`tests/unit/test_health.py`). Database-backed integration tests are
added once models and repositories exist.

## Database migrations (Alembic)

Alembic is configured (`alembic.ini`, `migrations/env.py`) and ready to
generate migrations once models exist in `app/models/`. No migrations
have been created yet. Once models exist, the workflow will be:

```bash
# Generate a migration from the current SQLAlchemy models
alembic revision --autogenerate -m "describe the change"

# Review the generated script in migrations/versions/ before applying it

# Apply all pending migrations
alembic upgrade head

# Roll back the most recent migration
alembic downgrade -1
```

`alembic.ini` intentionally contains no database credentials — the real
`DATABASE_URL` is injected at runtime by `migrations/env.py` from the same
`Settings` object the application uses.

## API documentation

Once running locally, interactive API docs are available at:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

No business endpoints are documented here yet, since none exist.
