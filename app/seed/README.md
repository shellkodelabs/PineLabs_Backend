# Mock Data Seed / Import

Imports the PineLab frontend's existing mock data
(`PineLabs_Frontend/src/data/*.js`) into PostgreSQL, for local
development only. This is Part 6 of the project — it populates the
schema built in Part 5. It does not create any API, repository, or
service code.

## Why a Node script is involved

`binSeries.js` and `sopData.js` don't just declare static arrays — they
*generate* hundreds of rows at import time (a deterministic PRNG
`mulberry32`, `Array.from` loops, string interpolation). Re-implementing
that generation logic in Python would risk silently producing different
data than what the running frontend actually computes. So
`export_frontend_data.mjs` runs the **real** frontend files with Node and
serializes their exports to JSON; `mock_data_loader.py` then reads that
JSON. No dataset is retyped, reconstructed, or hand-copied anywhere in
this importer.

## Files

| File | Purpose |
|---|---|
| `export_frontend_data.mjs` | Node script — imports the real `binSeries.js`, `sopData.js`, `users.js`, `revisions.js` and prints their exports as one JSON object |
| `mock_data_loader.py` | Runs the Node script, parses its output, validates it against the backend schema's constraints, computes the BIN↔Merchant and revision↔user matches |
| `seed_from_mock_data.py` | Orchestrates the import: wipe → insert (merchants → BIN → SOP → users → revisions) → reconciliation report |

## Running it

```bash
python -m app.seed.seed_from_mock_data
```

Requires `DATABASE_URL` pointing at a real PostgreSQL database with the
Part 5 migration already applied, and `node` on PATH. By default the
frontend source is located at `../PineLabs_Frontend/src/data` relative
to this repo (the actual on-disk layout); override with the
`FRONTEND_DATA_DIR` environment variable if your checkout differs.

## Idempotency strategy

**Chosen approach: full wipe + re-insert, inside one transaction.**

Considered alternatives:
- **Upsert** (`ON CONFLICT DO UPDATE`) — works cleanly for `merchants`
  (unique `name`), `bin_records` (unique `(bin_iin, merchant_prefix)`),
  and `users` (unique `email`), which all have real natural keys. It
  does **not** work for `sop_column_groups`, `sop_columns`, or
  `sop_rows` — none of these have any natural key at all; a group's
  identity is purely "the Nth group of this sheet," a row has no
  identity beyond its position. Upserting them would require inventing
  a synthetic natural key (e.g. hashing row content) that doesn't exist
  in the source data — effectively inventing structure the frontend
  doesn't have.
- **Deterministic matching** (e.g. reusing the frontend's string ids as
  DB ids) — explicitly ruled out by the task: frontend ids are
  demo/application ids, not meant to become BIGINT primary keys.
- **Full wipe + re-insert (chosen)** — since the entire dataset is
  fully and deterministically reproducible from the frontend source
  every time, and this is a development-only convenience script (never
  intended to run against a database with real, independently-created
  data), clearing and rebuilding is both the simplest and the most
  *correct* option — it can't leave stale rows, half-updated JSONB
  blobs, or orphaned groups/columns behind.

Implementation: `TRUNCATE TABLE ... RESTART IDENTITY CASCADE` across all
9 tables, then a fresh insert of everything, all inside one database
transaction (rolled back automatically on any failure — see
`seed_from_mock_data.main()`). Verified safe to run repeatedly: running
it twice in a row produces byte-identical row counts both times.

**Safety guard:** the wipe step refuses to run when
`Settings.ENVIRONMENT == "production"`. No new database constraint was
added to support this script — the guard is entirely at the application
level, per the task's instruction not to add production constraints
solely for a seed script.

## What is NOT imported, and why

- **`user_sop_sheet_access`** — `users.js` has no `access` field. Access
  data only ever exists transiently in the frontend's
  `CreateUserModal.jsx` component state and is never persisted anywhere
  in `src/data/`. Zero rows are seeded; this is reported explicitly by
  the reconciliation report rather than silently left unmentioned.
- **`users.mobile`** — not present in `users.js` (only
  `id/name/email/role/status/lastActive`). Left `NULL`.
- **`users.last_active_at`** — `users.js`'s `lastActive` is a relative
  display string (`"2 min ago"`, `"1 hr ago"`, `"—"`) with no captured
  reference timestamp anywhere in the source. It cannot be converted to
  an absolute `TIMESTAMPTZ` without inventing a value, so it is left
  `NULL` for every user.
- **`revisions.entity_id`** — the mock's `target` field is free text
  (e.g. `"BIN 401288 · HDFC Regalia"`) with no numeric id anywhere in
  the source that could be attached to a specific row. Left `NULL` for
  every revision rather than guessed.

## Documented assumption: revision timestamps

`revisions.js`'s `timestamp` strings (`"YYYY-MM-DD HH:mm"`) carry no
timezone information. They are interpreted as **UTC** when converted to
`TIMESTAMPTZ` — the source gives no basis for any other zone, and this
is stated here explicitly rather than applied silently. (When read back
through a Postgres session configured for a different timezone, e.g.
IST, the displayed clock time will differ from the literal source
string — the underlying instant is unchanged.)

## BIN ↔ Merchant matching

Per the Part 3/5 design, `bin_records.merchant_id` is nullable and never
forced. The importer sets it only where a BIN record's `issuer` string
exactly matches a merchant's `name`, case-insensitively (`.strip().lower()`
equality) — no fuzzy matching, no partial matching. See the final import
report for the actual match rate observed.
