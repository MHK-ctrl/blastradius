#!/usr/bin/env python3
"""The whole pitch in one screen.

Task: refund $40 to cust_17. The server applies the refund, then times out.
The agent retries.

Run A — no guard:              ledger shows two refunds.
Run B — blastradius guard on:  ledger shows one refund.

The scripted model is a deterministic policy, not an LLM: no API key, no
network, nothing real is charged.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from blastradius import Session, guard
from blastradius.report import build_report

TASK = {"customer_id": "cust_17", "amount": 40}
FAULTS = {"/refund": ["timeout_once"]}
SEED = 7
MAX_RETRIES = 1


def _load_tools() -> Any:
    """Import examples.tools no matter how this file was launched."""
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from examples.tools import flaky_server

    return flaky_server


flaky = _load_tools()
FlakyClient = flaky.FlakyClient
FlakyServer = flaky.FlakyServer
RETRYABLE = flaky.RETRYABLE


class ScriptedModel:
    """A one-step policy: call refund(customer_id, amount), retry once on fault."""

    def __init__(self, call: dict[str, Any], max_retries: int = MAX_RETRIES) -> None:
        self.call = call
        self.max_retries = max_retries

    def run(self, tool: Callable[..., dict[str, Any]], trace: list[str]) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                result = tool(**self.call)
            except RETRYABLE as exc:
                last = exc
                trace.append(f"attempt {attempt + 1}: {type(exc).__name__}: {exc}")
                continue
            trace.append(f"attempt {attempt + 1}: ok  {result}")
            return result
        assert last is not None
        raise last


def _run_a() -> tuple[list[dict[str, Any]], float, list[str]]:
    trace: list[str] = []
    server = FlakyServer(faults=FAULTS, seed=SEED).start()
    try:
        client = FlakyClient(server.base_url)

        def refund(customer_id: str, amount: float) -> dict[str, Any]:
            return client.refund(customer_id, amount)

        ScriptedModel(TASK).run(refund, trace)
        return server.ledger(), server.money(), trace
    finally:
        server.stop()


def _run_b(db: Path) -> tuple[list[dict[str, Any]], float, list[str], dict[str, Any]]:
    trace: list[str] = []
    server = FlakyServer(faults=FAULTS, seed=SEED).start()
    session = Session(scope=["refund<=50"], db_path=str(db), session_id="demo-run-b")
    try:
        client = FlakyClient(server.base_url)

        @guard(effect="refund", scope="refund<=50")
        def refund(
            customer_id: str,
            amount: float,
            *,
            session: Session,
            idempotency_key: str | None = None,
        ) -> dict[str, Any]:
            return client.refund(customer_id, amount, idempotency_key=idempotency_key)

        def call(**kwargs: Any) -> dict[str, Any]:
            return refund(**kwargs, session=session)

        ScriptedModel(TASK).run(call, trace)
        report = build_report(session.journal.store)
        return server.ledger(), server.money(), trace, report
    finally:
        server.stop()
        session.close()


def _block(title: str, ledger: list[dict[str, Any]], money: float, trace: list[str]) -> str:
    lines = [title]
    for line in trace:
        lines.append(f"  {line}")
    refunds = [row for row in ledger if row["kind"] == "refund"]
    lines.append(f"  ledger   {len(refunds)} refund(s), ${money:.2f} total")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="blastradius before/after demo")
    parser.add_argument(
        "--db",
        default=str(Path(__file__).resolve().parent / "demo.db"),
        help="journal path for the guarded run",
    )
    args = parser.parse_args(argv)
    db = Path(args.db)
    if db.exists():
        db.unlink()

    ledger_a, money_a, trace_a = _run_a()
    ledger_b, money_b, trace_b, report = _run_b(db)

    block_a = _block("RUN A — no guard (seed 7, same task, same fault)", ledger_a, money_a, trace_a)
    block_b = _block("RUN B — blastradius guard on (seed 7)", ledger_b, money_b, trace_b)
    block_b += (
        "\n  report   duplicate_writes: "
        f"{report['duplicate_writes']} | collapsed: {report['collapsed_duplicates']} | "
        f"callee_deduped: {report['callee_deduped']} | "
        f"retries_after_failure: {report['retries_after_failure']}"
    )
    print(block_a)
    print("\nsame seed, same fault, same task — only the guard changed\n")
    print(block_b)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
