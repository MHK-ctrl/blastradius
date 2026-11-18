# CORRECTIONS

Nothing has been corrected yet. This is the first published run, v0.1.0
(2026-11-18).

## Policy

- If a published number is wrong, it is replaced, and the old value, the new value
  and the reason are listed here with a date.
- If a run was invalid, its raw file stays in `index/results/` and a note is added
  here and to `index/results/summary.json`.
- Simulated runs are labelled SIMULATED on every page and in the summary JSON. They
  can never be promoted to model data retroactively; a live run gets a new file.
- Corrections are commits with a message starting `fix(index):` or
  `fix(report):`, so the change is visible in history.

## Known limitations stated up front (not corrections)

- The index is **SIMULATED** in v0.1.0: scripted policies, not LLMs.
- Cross-process exactly-once depends on the callee honouring the forwarded
  idempotency key. The demo's ledger shows this, and `DESIGN.md` says it plainly.
- Undo handlers live in the process that registered them. A rollback in a fresh
  process can only undo effects whose handlers were re-registered; the rest are
  reported as `no_handler` and counted as orphaned writes.
