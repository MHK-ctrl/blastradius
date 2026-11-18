# CONTRIBUTING

## Ground rules

1. **Never publish a number you did not measure.** If a run fails, report the
   failure. If the data is simulated, the word SIMULATED appears next to it.
2. **Report the cost**, including when it looks bad.
3. **n and a 95% Wilson interval** next to every rate.
4. **Never disparage a framework, company or person.** Write "in our harness, at
   these versions, on these dates, N of M tasks produced a duplicate write."
5. **Raw runs are public.** Add scenarios and results; do not delete a bad run.
6. **No secrets.** Read keys from the environment only. `.env` is ignored.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/ruff check .
```

## Adding a scenario

Create `index/scenarios/<id>.yaml`:

```yaml
id: money-99
effect_type: money            # money | communication | records
task: "Refund $30 to cust_42"
scope: ["refund<=50"]
fault_pool: [timeout_once, rate_limit_once, truncated_body, stale_read, schema_drift]
plan:
  - tool: refund
    args: {customer_id: cust_42, amount: 30}
expected:
  - {kind: refund, customer_id: cust_42, amount: 30}
notes: "Why this task exists."
retries: 1
```

Rules for scenarios:

- `expected` is the **exact** final ledger. An impossible task has `expected: []`.
- Prefer one effect per task; multi-step tasks must still declare the full ledger.
- Do not add a scenario whose expected state depends on the framework being tested.

Then run `python -m index.runner --trials 5 --overhead` and
`python -m index.build_site`. Commit the new raw result file and the updated
`summary.json`.

## Adding an adapter

Do not guess an import. Confirm the path and signature against the installed
package or its documentation, and run the adapter once. If you cannot run it, add
it to the "not run, and why" list instead of shipping an unexercised file.

## Tests

A test must fail when the feature is deleted. Assert call counts, journal states,
call order, exit codes or ledger contents, not just that something did not raise.
No test may touch the internet; loopback is fine.
