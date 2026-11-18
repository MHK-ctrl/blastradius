# PLAN — `blastradius` v0.1.0

Project root: `/Users/rubinakhan/Hamza/FREEBUFF_WORKSPACE/blastradius`
Workspace root: `/Users/rubinakhan/Hamza/FREEBUFF_WORKSPACE` (15 sibling projects exist; none are touched).

## Build order

| # | File(s) | Why first |
|---|---------|-----------|
| 0 | `PLAN.md`, `DESIGN.md` | Fix scope and semantics before code |
| 1 | `pyproject.toml`, `.gitignore`, `LICENSE` | Installable package, no secrets from commit 1 |
| 2 | `blastradius/storage/base.py`, `storage/sqlite_store.py` | Journal is the substrate for everything else |
| 3 | `blastradius/idempotency.py` | Intent hashing; pure functions, cheapest to test |
| 4 | `blastradius/journal.py` | Write-ahead protocol + explicit recovery |
| 5 | `blastradius/scope.py` | Capability tokens, enforced at the tool boundary |
| 6 | `blastradius/saga.py` | Reverse-order undo over journaled effects |
| 7 | `blastradius/dryrun.py` | Shadow ledger over the same journal |
| 8 | `blastradius/guard.py` | Ties 2–7 together behind one decorator |
| 9 | `blastradius/report.py`, `cli.py` | Measure the journal; `br` entry point |
| 10 | `examples/tools/flaky_server.py`, `examples/demo_agent.py` | Before/after proof, zero API keys |
| 11 | `index/scenarios/*.yaml`, `faults.py`, `runner.py`, `stats.py` | Measurement harness |
| 12 | `index/build_site.py`, `docs/` | Public site generated from raw results |
| 13 | `tests/` | 45+ assertions that fail when the feature is deleted |
| 14 | `.github/workflows/ci.yml` | pytest, ruff, guard-regression |
| 15 | `README.md`, `METHODOLOGY.md`, `CORRECTIONS.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `REPORT.md` | Honest documentation of what ran |

## Test list (one line each)

1. Identical attempts produce the same intent hash.
2. Payload key order does not change the hash.
3. `40` and `40.0` normalise to the same hash.
4. A different amount produces a different hash.
5. A different session produces a different intent key.
6. A different effect name produces a different hash.
7. Nested dict key order does not change the hash.
8. The hash is 64 lowercase hex characters.
9. A blocked scope attempt is journaled as `blocked` and never executes.
10. A duplicate intent returns the first stored result.
11. A duplicate intent does not re-execute the function.
12. A duplicate intent writes one `collapsed` row, not a second execution.
13. A duplicate in another session does execute.
14. `on_duplicate="raise"` raises `DuplicateCollapsed` carrying the stored result.
15. An exception is journaled `failed` and re-raised unchanged.
16. A retry after failure executes with `attempt=2`.
17. A `pending` row from a crash is returned by `recover()` without mutation.
18. `recover(assume="failed")` marks the row failed.
19. `recover(assume="done")` marks the row done and flags it.
20. The guard refuses to silently re-run an intent stuck in `pending`.
21. `rollback()` calls undos in reverse order (3-2-1).
22. `rollback()` journals each undo.
23. A failed undo marks `unrolled_failed` and raises `UndoFailure`.
24. `rollback()` skips blocked and failed effects.
25. A blocked effect does not get an undo call.
26. `refund<=50` allows 50 and 12.5.
27. `refund<=50` blocks 50.01 before the function runs.
28. A blocked call raises `ScopeViolation` with the effect name.
29. `refund<=*` allows any amount.
30. `email:internal` allows `audience="internal"`.
31. `email:internal` blocks `audience="external"`.
32. A session without a scope allows any effect.
33. A child session inherits and narrows the parent ceiling.
34. A child cannot widen the parent ceiling.
35. A child tag conflict is rejected at construction.
36. Dry run never calls the real function.
37. Dry run returns a plausible synthetic result.
38. `dry_run_diff()` counts effects, identifiers and money moved.
39. Dry run leaves no `done` rows in the real journal.
40. Report JSON contains the nine required keys.
41. Report counts tasks, effects and blocked attempts.
42. A clean guarded run reports `duplicate_writes == 0`.
43. `--strict` exits 1 when a duplicate write exists.
44. `--strict` exits 0 on a clean journal.
45. `br --help` exits 0 and lists the four commands.
46. `br report --json` emits parseable JSON.
47. `br verify` exits 1 and lists pending rows.
48. The journal survives a separate process restart.
49. The module-level default session works with no explicit session.
50. Two concurrent identical calls execute the function once.

## Commands (in order)

```
python3 -m venv .venv
.venv/bin/pip install -e . pytest ruff
.venv/bin/ruff check .
.venv/bin/pytest -q
.venv/bin/python examples/demo_agent.py
.venv/bin/br report --db examples/demo.db --json
.venv/bin/python -m index.runner --trials 5
.venv/bin/python -m index.build_site
git init && git add -A && git commit  (eight logical commits)
gh repo create blastradius --public --source=. --remote=origin --push   # only after explicit "publish"
```

## Where I expect to be blocked

- **No `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`** → the index runs scripted models against the local flaky server and is labelled SIMULATED on every page and number. No model data is claimed.
- **LangGraph / OpenAI Agents SDK / CrewAI** → adapters are only shipped if the import path and call signature are confirmed against the installed package. Otherwise the row is listed as "not run, and why" in `REPORT.md`. A guessed import is not shipped.
- **GitHub publishing** → hard gate: nothing is pushed, no Pages, no release until the user says "publish" (§12).
- **LinkedIn** → draft only, never posted by the agent.

## Risk list (where this could go wrong, and the guard against it)

1. **Presenting scripted data as model data.** Every generated page, table, CLI banner and README line carries `SIMULATED` when no API key was present; the run JSON records `"model": "scripted"`.
2. **A metric that flatters the guard.** Both arms run the same scenarios, seeds and fault sequences; the oracle is the server ledger, not the guard's journal; raw per-trial JSON is published.
3. **Idempotency keys mistaken for a proof.** Keys only work if the callee honours them. The report separates `collapsed` (in-process, guaranteed) from `callee_deduped` (declared by the callee) and the README says so.
4. **Tests that pass with the feature deleted.** Every behaviour test asserts an observable effect (call count, row state, exit code, call order), and the CI guard-regression job runs the demo with the guard removed and requires it to fail.
5. **Secret leakage.** Keys are read from the environment only, never persisted; `.env`, `*.db`, `index/results/live/` are ignored from the first commit; a grep-based secrets scan runs in the Phase 6 gate.
