"""Run every scenario with the guard off and on, against the same faulty ledger.

The oracle is the server's ledger, never the guard's journal: a task passes only
if the final ledger equals the scenario's declared expected state, exactly.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from blastradius import ScopeViolation, Session, build_report
from blastradius.guard import guard
from blastradius.storage.sqlite_store import SqliteStore
from examples.tools.flaky_server import RETRYABLE, FlakyClient, FlakyServer
from index.faults import fault_plan
from index.stats import pass_hat_k, per_1000, percentile

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "index" / "results"
SCENARIOS = ROOT / "index" / "scenarios"
DEFAULT_TRIALS = 5
DEFAULT_MAX_SPEND = 5.0
FAULT_POOL = ["timeout_once", "rate_limit_once", "truncated_body", "stale_read", "schema_drift"]


def load_scenarios(directory: Path) -> list[dict[str, Any]]:
    scenarios = []
    for path in sorted(directory.glob("*.yaml")):
        with path.open() as handle:
            data = yaml.safe_load(handle)
        data["_path"] = str(path)
        scenarios.append(data)
    if not scenarios:
        raise FileNotFoundError(f"no scenario YAML files in {directory}")
    return scenarios


def matches(ledger: list[dict[str, Any]], expected: list[dict[str, Any]]) -> bool:
    """Exact multiset match on the declared fields."""
    if len(ledger) != len(expected):
        return False
    remaining = list(ledger)
    for wanted in expected:
        for row in remaining:
            if all(row.get(key) == value for key, value in wanted.items()):
                remaining.remove(row)
                break
        else:
            return False
    return True


def _plain_tools(client: FlakyClient) -> dict[str, Any]:
    return {
        "refund": lambda args: client.refund(args["customer_id"], args["amount"]),
        "email": lambda args: client.email(args["to"], args["subject"], args["audience"]),
        "records": lambda args: client.record(args["record_id"], args["value"]),
    }


def _guarded_tools(client: FlakyClient, session: Session) -> dict[str, Any]:
    @guard(effect="refund")
    def refund(
        customer_id: str,
        amount: float,
        *,
        session: Session,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return client.refund(customer_id, amount, idempotency_key=idempotency_key)

    @guard(effect="email")
    def email(
        to: str,
        subject: str,
        audience: str,
        *,
        session: Session,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return client.email(to, subject, audience, idempotency_key=idempotency_key)

    @guard(effect="records")
    def records(
        record_id: str,
        value: Any,
        *,
        session: Session,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return client.record(record_id, value, idempotency_key=idempotency_key)

    return {
        "refund": lambda args: refund(**args, session=session),
        "email": lambda args: email(**args, session=session),
        "records": lambda args: records(**args, session=session),
    }


def _execute(tool: Any, args: dict[str, Any], retries: int) -> dict[str, Any]:
    attempts: list[str] = []
    for _ in range(retries + 1):
        try:
            return {"ok": True, "blocked": False, "result": tool(args), "attempts": attempts}
        except ScopeViolation as exc:
            return {"ok": False, "blocked": True, "error": str(exc), "attempts": attempts}
        except RETRYABLE as exc:
            attempts.append(f"{type(exc).__name__}: {exc}")
    return {
        "ok": False,
        "blocked": False,
        "error": attempts[-1] if attempts else "no attempt",
        "attempts": attempts,
    }


def run_trial(
    scenario: dict[str, Any],
    seed: int,
    arm: str,
    server: FlakyServer,
    retries: int,
) -> dict[str, Any]:
    server.reset(fault_plan(scenario, seed))
    client = FlakyClient(server.base_url)
    session = Session(
        scope=scenario.get("scope"),
        store=SqliteStore(":memory:"),
        session_id=f"{scenario['id']}-{seed}-{arm}",
    )
    tools = _guarded_tools(client, session) if arm == "on" else _plain_tools(client)
    outcomes = [
        _execute(tools[step["tool"]], step.get("args", {}), retries)
        for step in scenario.get("plan", [])
    ]
    ledger = server.ledger()
    expected = scenario.get("expected", [])
    passed = matches(ledger, expected)
    report = build_report(session.journal.store)
    session.close()
    return {
        "scenario": scenario["id"],
        "effect_type": scenario["effect_type"],
        "arm": arm,
        "seed": seed,
        "faults": fault_plan(scenario, seed),
        "expected": expected,
        "ledger": ledger,
        "outcomes": outcomes,
        "pass": passed,
        "journal": {
            "duplicate_writes": report["duplicate_writes"],
            "collapsed_duplicates": report["collapsed_duplicates"],
            "callee_deduped": report["callee_deduped"],
            "blocked_attempts": report["blocked_attempts"],
            "pending": report["pending"],
        },
    }


def summarise(trials: list[dict[str, Any]], k: int) -> dict[str, Any]:
    n = len(trials)
    failures = sum(1 for trial in trials if not trial["pass"])
    rate, low, high = per_1000(failures, n)
    by_scenario: dict[str, list[bool]] = defaultdict(list)
    for trial in trials:
        by_scenario[trial["scenario"]].append(trial["pass"])
    return {
        "n_tasks": n,
        "failures": failures,
        "pass_1": round((n - failures) / n, 4) if n else 0.0,
        "failures_per_1000": rate,
        "ci95_per_1000": [low, high],
        "pass_hat_k": pass_hat_k(by_scenario, k),
        "k": k,
    }


def _timings(fn: Any, iterations: int) -> list[float]:
    samples = []
    for index in range(iterations):
        started = time.perf_counter()
        fn(index)
        samples.append((time.perf_counter() - started) * 1000)
    return samples


def measure_overhead(iterations: int = 400, http_iterations: int = 150) -> dict[str, Any]:
    """Guard cost per effect: in-process, and against a real local tool call."""
    session = Session(store=SqliteStore(":memory:"))

    @guard(effect="noop")
    def guarded(value: int, *, session: Session) -> dict[str, Any]:
        return {"ok": True, "value": value}

    def bare(value: int) -> dict[str, Any]:
        return {"ok": True, "value": value}

    bare_inproc = _timings(bare, iterations)
    guarded_inproc = _timings(lambda index: guarded(index, session=session), iterations)
    session.close()

    in_process = {
        "iterations": iterations,
        "bare_p50_ms": round(percentile(bare_inproc, 0.50), 5),
        "guarded_p50_ms": round(percentile(guarded_inproc, 0.50), 5),
        "added_p50_ms": round(
            percentile(guarded_inproc, 0.50) - percentile(bare_inproc, 0.50), 5
        ),
        "added_p95_ms": round(
            percentile(guarded_inproc, 0.95) - percentile(bare_inproc, 0.95), 5
        ),
    }

    server = FlakyServer(seed=3).start()
    tool_session = Session(store=SqliteStore(":memory:"))
    try:
        client = FlakyClient(server.base_url)

        @guard(effect="refund")
        def guarded_refund(
            customer_id: str,
            amount: float,
            *,
            session: Session,
            idempotency_key: str | None = None,
        ) -> dict[str, Any]:
            return client.refund(customer_id, amount, idempotency_key=idempotency_key)

        def bare_refund(index: int) -> None:
            client.refund(f"cust_{index}", 1 + index % 40)

        def wrapped_refund(index: int) -> None:
            guarded_refund(f"guarded_{index}", 1 + index % 40, session=tool_session)

        bare_http = _timings(bare_refund, http_iterations)
        guarded_http = _timings(wrapped_refund, http_iterations)
        bare_p50 = percentile(bare_http, 0.50)
        guarded_p50 = percentile(guarded_http, 0.50)
        bare_p95 = percentile(bare_http, 0.95)
        guarded_p95 = percentile(guarded_http, 0.95)
        with_tool_call = {
            "iterations": http_iterations,
            "bare_p50_ms": round(bare_p50, 3),
            "bare_p95_ms": round(bare_p95, 3),
            "guarded_p50_ms": round(guarded_p50, 3),
            "guarded_p95_ms": round(guarded_p95, 3),
            "added_p50_ms": round(guarded_p50 - bare_p50, 3),
            "added_p95_ms": round(guarded_p95 - bare_p95, 3),
            "added_p50_pct": round((guarded_p50 / bare_p50 - 1) * 100, 1) if bare_p50 else None,
            "added_p95_pct": round((guarded_p95 / bare_p95 - 1) * 100, 1) if bare_p95 else None,
        }
    finally:
        tool_session.close()
        server.stop()

    return {"in_process": in_process, "with_tool_call": with_tool_call}


def build_summary(
    scenarios: list[dict[str, Any]],
    trials: int,
    overhead: dict[str, Any] | None,
    run_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw: list[dict[str, Any]] = []
    server = FlakyServer(seed=0).start()
    try:
        for scenario in scenarios:
            for index in range(trials):
                seed = int(scenario.get("seed", 1)) + index
                for arm in ("off", "on"):
                    raw.append(
                        run_trial(scenario, seed, arm, server, int(scenario.get("retries", 1)))
                    )
    finally:
        server.stop()

    arms = {}
    for arm in ("off", "on"):
        arms[arm] = summarise([trial for trial in raw if trial["arm"] == arm], trials)

    by_effect: dict[str, Any] = {}
    for effect_type in sorted({scenario["effect_type"] for scenario in scenarios}):
        rows = [trial for trial in raw if trial["effect_type"] == effect_type]
        by_effect[effect_type] = {
            arm: summarise([trial for trial in rows if trial["arm"] == arm], trials)
            for arm in ("off", "on")
        }

    per_scenario = []
    for scenario in scenarios:
        rows = [trial for trial in raw if trial["scenario"] == scenario["id"]]
        per_scenario.append(
            {
                "id": scenario["id"],
                "effect_type": scenario["effect_type"],
                "task": scenario["task"],
                "scope": scenario.get("scope"),
                "expected": scenario.get("expected", []),
                "trials_off": len([row for row in rows if row["arm"] == "off"]),
                "failures_off": sum(
                    1 for row in rows if row["arm"] == "off" and not row["pass"]
                ),
                "failures_on": sum(
                    1 for row in rows if row["arm"] == "on" and not row["pass"]
                ),
            }
        )

    simulated = True
    summary = {
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "simulated": simulated,
        "model": "scripted",
        "simulation_reason": (
            "no OPENAI_API_KEY or ANTHROPIC_API_KEY in the environment; every arm ran a "
            "deterministic scripted policy against the local flaky server"
        ),
        "framework": "raw function calling (scripted policy)",
        "python": platform.python_version(),
        "scenarios": len(scenarios),
        "trials_per_scenario": trials,
        "arms": arms,
        "by_effect_type": by_effect,
        "per_scenario": per_scenario,
        "overhead": overhead,
        "policy": {"max_retries_per_call": 1, "concurrency": 1},
        "not_run": [
            {
                "framework": "LangGraph",
                "reason": "no API key in the environment; an adapter was not exercised, "
                "so no number is published.",
            },
            {
                "framework": "OpenAI Agents SDK",
                "reason": "no API key in the environment; an adapter was not exercised, "
                "so no number is published.",
            },
            {
                "framework": "CrewAI",
                "reason": "not installed and not exercised in this run.",
            },
        ],
    }
    return summary, raw


def _print_table(summary: dict[str, Any]) -> None:
    header = (
        f"{'arm':<12}{'n':>6}{'dup-or-auth/1000':>20}{'95% CI':>22}"
        f"{'pass^1':>9}{'pass^' + str(summary['trials_per_scenario']):>9}"
    )
    print(header)
    print("-" * len(header))
    for arm in ("off", "on"):
        data = summary["arms"][arm]
        low, high = data["ci95_per_1000"]
        label = "guard off" if arm == "off" else "guard on"
        print(
            f"{label:<12}{data['n_tasks']:>6}{data['failures_per_1000']:>20.1f}"
            f"{f'[{low:.1f}, {high:.1f}]':>22}{data['pass_1']:>9.3f}"
            f"{data['pass_hat_k']:>9.2f}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Blast Radius Index")
    parser.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    parser.add_argument("--scenarios", default=str(SCENARIOS))
    parser.add_argument("--out", default=str(RESULTS))
    parser.add_argument(
        "--max-spend",
        type=float,
        default=DEFAULT_MAX_SPEND,
        help="hard cap in USD; the scripted model costs nothing",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the cost estimate and exit")
    parser.add_argument("--overhead", action="store_true", help="measure guard overhead too")
    args = parser.parse_args(argv)

    if args.dry_run:
        print("model: scripted (no API key present)")
        count = len(load_scenarios(Path(args.scenarios)))
        print(f"scenarios: {count} x {args.trials} trials x 2 arms")
        print("estimated spend: $0.00 (scripted model, local server)")
        return 0

    if args.max_spend < 0:
        print("max-spend must be >= 0", file=sys.stderr)
        return 2

    scenarios = load_scenarios(Path(args.scenarios))
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    overhead = measure_overhead() if args.overhead else None
    summary, raw = build_summary(scenarios, args.trials, overhead, run_id)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / f"{run_id}.json"
    raw_path.write_text(json.dumps({"summary": summary, "trials": raw}, indent=2) + "\n")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    print(f"run {run_id}  scenarios {len(scenarios)}  trials {args.trials}")
    print("model: scripted (SIMULATED — no API key present)")
    _print_table(summary)
    print(f"\nraw: {raw_path.relative_to(ROOT)}")
    print(f"summary: {(out_dir / 'summary.json').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
