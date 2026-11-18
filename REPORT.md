# REPORT — v0.1.0

## What ran

| Piece | Status |
|---|---|
| Core package (`blastradius/`) | implemented |
| Test suite | 101 tests, green locally |
| `ruff check .` | clean |
| Demo before/after | ran, real local ledger |
| Index harness (45 scenarios × 5 trials × 2 arms) | ran, **SIMULATED** |
| Static site build | ran, served locally, SIMULATED banner verified |
| CI workflow | written; both guard-regression behaviours proven locally |

## Real vs simulated, row by row

| Claim | Data source | Real or simulated |
|---|---|---|
| 2 refunds without the guard, 1 with it | local flaky server ledger | **real** local execution, scripted agent |
| Guard overhead +0.010 ms p50 (3.7%) | local benchmark, 150 calls per arm | **real** local measurement |
| In-process guard cost +28 µs p50 | 400 calls, in-memory journal | **real** local measurement |
| 800.0 duplicate-or-unauthorised writes per 1,000 (guard off) | index harness | **SIMULATED** |
| 0.0 per 1,000 (guard on) | index harness | **SIMULATED** |
| Any framework row | — | **not run** |

No LLM was called. No API key was read.

## Framework adapters

`examples/langgraph_agent.py` and `examples/openai_agents_agent.py` are **not
shipped**. Both packages are importable in the harness environment
(`langgraph.prebuilt.create_react_agent` and `agents.Agent/Runner/function_tool`
were checked), but neither adapter could be executed without a model API key. An
unexercised adapter would be a guess presented as a working example, so the rows
are listed as "not run, and why" on the site and in the summary JSON instead.
CrewAI was not installed.

## Known gaps

- The index is simulated; it is a harness demonstration, not a model benchmark.
- Cross-process exactly-once depends on the callee honouring the forwarded key.
- Undo handlers are process-local; a fresh-process rollback reports `no_handler`.
- Only the SQLite store ships; the `JournalStore` protocol is the extension point.
- The CI workflow has not executed on GitHub yet; it was validated locally by
  running each job's command. The first push is the real test.

## Reproduce

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/python examples/demo_agent.py
.venv/bin/python -m index.runner --trials 5 --overhead
.venv/bin/python -m index.build_site
```
