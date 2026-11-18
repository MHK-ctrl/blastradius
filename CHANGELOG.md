# Changelog

## 0.1.0 — 2026-11-18

First release. What works:

- `@guard(effect=..., undo=..., scope=...)`: write-ahead journal, intent-derived
  idempotency keys, in-process duplicate collapse, exception journaling, optional
  `idempotency_key` forwarding to the callee.
- SQLite store, WAL mode, one `effects` table, canonical JSON payloads.
- `session.rollback()`: newest-first undo, each undo journaled, failed undos raise
  `UndoFailure` instead of being swallowed.
- Capability scopes: `refund<=50`, `refund<=*`, `email:internal`, child sessions
  that can only narrow.
- `with session.dry_run():` shadow ledger and `session.dry_run_diff()`.
- `br report`, `br verify`, `br dryrun -- <cmd>`, `br index --trials N`.
- 45-scenario index harness with deterministic faults, Wilson intervals and pass^k.
- Static site generated into `docs/`.

What does not work yet (stated, not hidden):

- Live model runs in the index. v0.1.0 published a SIMULATED run; the harness has a
  spend cap and a dry-run mode but no model backend.
- Undo handlers are process-local: a rollback in a new process cannot call handlers
  that were never re-registered.
- No Postgres/DynamoDB store. The protocol exists; only SQLite ships.
