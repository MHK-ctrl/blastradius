"""The guard-regression gate: the bug is real without the guard, gone with it.

These tests use a local server on 127.0.0.1 only. Nothing leaves the machine.
"""

from __future__ import annotations

from examples import demo_agent


def _refund_count(ledger):
    return len([row for row in ledger if row["kind"] == "refund"])


def test_without_guard_the_retry_duplicates():
    ledger, money, trace = demo_agent._run_a()
    assert _refund_count(ledger) == 2
    assert money == 80.0
    assert len(trace) == 2


def test_with_guard_the_retry_does_not_duplicate():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as directory:
        ledger, money, trace, report = demo_agent._run_b(Path(directory) / "journal.db")
    assert _refund_count(ledger) == 1
    assert money == 40.0
    assert report["duplicate_writes"] == 0
    assert report["callee_deduped"] == 1


def test_removing_the_guard_reintroduces_the_duplicate(monkeypatch):
    import tempfile
    from pathlib import Path

    def passthrough(*args, **kwargs):
        def decorator(fn):
            return fn

        return decorator

    monkeypatch.setattr(demo_agent, "guard", passthrough)
    with tempfile.TemporaryDirectory() as directory:
        ledger, money, trace, _ = demo_agent._run_b(Path(directory) / "journal.db")
    assert _refund_count(ledger) == 2
    assert money == 80.0
