# blastradius

**Exactly-once, undoable side effects for AI agents.**

An agent retried after a timeout and charged the customer twice. blastradius sits
between the agent and its tools: it journals every intended effect before it runs,
collapses duplicate intents, registers an undo per effect, previews a run against a
shadow ledger, and refuses out-of-scope calls in code rather than in a prompt.

> Capability is not the interesting number. Blast radius is.

## The demo, unedited

`python examples/demo_agent.py` — no API key, no network, nothing real charged.
Same task, same seed, same fault (the server applies the refund, then times out):

```
RUN A — no guard (seed 7, same task, same fault)
  attempt 1: TimeoutError: client timed out after 0.35s
  attempt 2: ok  {'ok': True, 'ledger_id': 2, 'kind': 'refund'}
  ledger   2 refund(s), $80.00 total

same seed, same fault, same task — only the guard changed

RUN B — blastradius guard on (seed 7)
  attempt 1: TimeoutError: client timed out after 0.35s
  attempt 2: ok  {'ok': True, 'ledger_id': 1, 'kind': 'refund', 'deduplicated': True}
  ledger   1 refund(s), $40.00 total
  report   duplicate_writes: 0 | collapsed: 0 | callee_deduped: 1 | retries_after_failure: 1
```

Read that last line carefully: the save came from the idempotency key the guard
forwarded and the server honouring it. In-process duplicate collapse is guaranteed
by the journal; cross-process exactly-once is a property of the callee.

## Quick start — three commands, under two minutes

```bash
pip install -e . && python examples/demo_agent.py && br report --db examples/demo.db --strict
```

Python 3.11+. No runtime dependencies.

## Use it

```python
from blastradius import Session, guard

def reverse_refund(result): ...       # your undo

@guard(effect="refund", undo=reverse_refund)
def issue_refund(customer_id, amount, *, session, idempotency_key=None):
    return payments.refund(customer_id, amount, idempotency_key=idempotency_key)

with Session(scope=["refund<=50"]) as session:
    issue_refund("cust_17", 40, session=session)   # journals, runs, journals
    issue_refund("cust_17", 40, session=session)   # returns the first result, no second call
    issue_refund("cust_17", 500, session=session)  # ScopeViolation, function never runs
```

The guard forwards `idempotency_key` to your tool only if the tool declares that
parameter. If it does not, the guard still collapses duplicates inside the session,
but it cannot stop a retry that crosses the process boundary.

Other entry points: `session.rollback()` (reverse-order undo, loud failures),
`with session.dry_run():` (shadow ledger, `session.dry_run_diff()`), and
`br report | br verify | br dryrun -- <cmd> | br index`.

## Measured cost

Measured on this machine, 2026-11-18 (see `index/results/summary.json`):

| | p50 | p95 |
|---|---|---|
| bare local tool call (n=150) | 0.265 ms | 0.342 ms |
| guarded local tool call (n=150) | 0.275 ms | 0.318 ms |
| added | **+0.010 ms (+3.7%)** | −0.024 ms (below noise) |

The in-process guard cost over 400 calls is **+28 µs p50 / +32 µs p95**. The p95
delta came out negative, which is run-to-run noise, not a speedup. The guard is
**1,256 lines** of Python (core modules, `wc -l`).

## The Blast Radius Index

45 scenarios × 5 trials × 2 arms, against a local fault-injection server.
**SIMULATED**: no model API key was present, so every arm is a deterministic
scripted policy, not an LLM. Same scenarios, same seeds, same faults in both arms.
The oracle is the server ledger, never the guard's journal.

| arm | tasks (n) | duplicate-or-unauthorised writes / 1,000 | 95% CI | pass^1 | pass^5 |
|---|---|---|---|---|---|
| guard off | 225 | **800.0** | 742.9 – 847.0 | 0.200 | 0.07 |
| guard on | 225 | **0.0** | 0.0 – 16.8 | 1.000 | 1.00 |

Raw per-trial JSON, with every ledger row, is in `index/results/`. The public site
is generated from that file by `index/build_site.py` into `docs/`.

**What this run does not show:** it does not measure any LLM or any agent
framework. LangGraph, the OpenAI Agents SDK and CrewAI are listed on the site under
"not run, and why" — no key was present and an unexercised adapter is worse than an
empty row.

## What it guarantees, and what it cannot

Guaranteed inside one process: the journal is written before the call; a duplicate
of a completed intent returns the stored result without calling the tool again; an
exception is journaled `failed` and re-raised unchanged; a crash between `pending`
and `done` is never silently resolved; rollback calls every undo newest-first and
raises if one fails.

Not guaranteed: effects written outside the decorator, a callee that ignores the
key, clock skew, retries that happen below your process, or an undo handler that
does not actually restore the world. `DESIGN.md` states each of these plainly.

## Development

```bash
pip install -e ".[dev]"
pytest -q
ruff check .
python -m index.runner --trials 5 --overhead
python -m index.build_site
```

CI runs the suite on Python 3.11 and 3.12, lints, and includes a guard-regression
job that requires the demo to duplicate without the guard and to stop duplicating
with it.

MIT. Built because everyone is measuring what agents can do, and almost nobody is
measuring what happens when they do it twice.
