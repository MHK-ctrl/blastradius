from __future__ import annotations

import json
import threading
import time

import pytest

from blastradius import DuplicateCollapsed, Session
from blastradius.guard import EffectFailed, PendingEffect, guard, reset_default_session
from blastradius.idempotency import canonical_intent


def test_row_is_pending_while_the_function_runs(store, sess):
    observed = {}

    @guard(effect="refund")
    def refund(customer_id, amount, *, session):
        observed["state"] = store.rows(session_id=session.id)[-1].state
        return {"ok": True}

    assert refund("cust_17", 40, session=sess) == {"ok": True}
    assert observed["state"] == "pending"
    rows = store.rows(session_id=sess.id)
    assert [row.state for row in rows] == ["done"]
    assert json.loads(rows[0].result) == {"ok": True}


def test_duplicate_returns_first_result_without_re_executing(store, sess):
    calls = []

    @guard(effect="refund")
    def refund(customer_id, amount, *, session):
        calls.append(amount)
        return {"ok": True, "amount": amount}

    first = refund("cust_17", 40, session=sess)
    second = refund("cust_17", 40, session=sess)
    assert first == second == {"ok": True, "amount": 40}
    assert calls == [40]
    rows = store.rows(session_id=sess.id)
    assert len(rows) == 2
    assert rows[1].collapsed == 1
    assert rows[1].duplicate_of == rows[0].id
    expected = canonical_intent("refund", {"customer_id": "cust_17", "amount": 40})
    assert rows[1].intent_hash == expected


def test_same_intent_in_a_different_session_executes(store):
    calls = []

    @guard(effect="refund")
    def refund(customer_id, amount, *, session):
        calls.append(amount)
        return {"ok": True}

    one, two = Session(store=store), Session(store=store)
    refund("cust_17", 40, session=one)
    refund("cust_17", 40, session=two)
    assert calls == [40, 40]


def test_on_duplicate_raise_carries_the_stored_result(store, sess):
    @guard(effect="refund", on_duplicate="raise")
    def refund(customer_id, amount, *, session):
        return {"ok": True, "amount": amount}

    refund("cust_17", 40, session=sess)
    with pytest.raises(DuplicateCollapsed) as caught:
        refund("cust_17", 40, session=sess)
    assert caught.value.result == {"ok": True, "amount": 40}


def test_exception_is_journaled_failed_and_reraised_unchanged(store, sess):
    @guard(effect="refund")
    def refund(customer_id, amount, *, session):
        raise ValueError("gateway said no")

    with pytest.raises(ValueError, match="gateway said no"):
        refund("cust_17", 40, session=sess)
    rows = store.rows(session_id=sess.id)
    assert len(rows) == 1
    assert rows[0].state == "failed"
    assert "ValueError: gateway said no" in rows[0].error


def test_retry_after_failure_executes_with_attempt_two(store, sess):
    attempts = []

    @guard(effect="refund")
    def refund(customer_id, amount, *, session):
        attempts.append(amount)
        if len(attempts) == 1:
            raise TimeoutError("read timed out")
        return {"ok": True}

    with pytest.raises(TimeoutError):
        refund("cust_17", 40, session=sess)
    assert refund("cust_17", 40, session=sess) == {"ok": True}
    rows = store.rows(session_id=sess.id)
    assert [(row.state, row.attempt) for row in rows] == [("failed", 1), ("done", 2)]


def test_guard_refuses_to_guess_on_a_pending_row(store, sess):
    payload = {"customer_id": "cust_17", "amount": 40}
    sess.journal.claim(
        session_id=sess.id,
        effect_name="refund",
        intent_hash=canonical_intent("refund", payload),
        payload=payload,
    )
    calls = []

    @guard(effect="refund")
    def refund(customer_id, amount, *, session):
        calls.append(1)
        return {"ok": True}

    with pytest.raises(PendingEffect):
        refund("cust_17", 40, session=sess)
    assert calls == []
    assert store.rows()[0].state == "pending"


def test_concurrent_identical_calls_execute_once(store, sess):
    calls = []

    @guard(effect="refund")
    def refund(customer_id, amount, *, session):
        calls.append(amount)
        time.sleep(0.15)
        return {"ok": True, "amount": amount}

    barrier = threading.Barrier(2)
    results = []
    errors = []

    def run():
        barrier.wait()
        try:
            results.append(refund("cust_17", 40, session=sess))
        except EffectFailed as exc:  # pragma: no cover - would be a regression
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(calls) == 1
    assert results == [{"ok": True, "amount": 40}] * 2


def test_payload_excludes_session_and_idempotency_key(store, sess):
    @guard(effect="refund")
    def refund(customer_id, amount, *, session):
        return {"ok": True}

    refund("cust_17", 40, session=sess)
    assert store.rows()[0].payload_obj == {"customer_id": "cust_17", "amount": 40}


def test_tool_without_session_parameter_works(store, sess):
    @guard(effect="ping")
    def ping(value):
        return {"ok": True, "value": value}

    assert ping(3, session=sess) == {"ok": True, "value": 3}
    assert store.rows()[0].effect_name == "ping"


def test_idempotency_key_is_forwarded_to_a_tool_that_accepts_it(store, sess):
    seen = {}

    @guard(effect="refund")
    def refund(customer_id, amount, *, session, idempotency_key=None):
        seen["key"] = idempotency_key
        return {"ok": True}

    refund("cust_17", 40, session=sess)
    assert seen["key"].startswith("br_")
    assert store.rows()[0].idem_key_sent == 1


def test_module_level_default_session(monkeypatch, tmp_path):
    monkeypatch.setenv("BLASTRADIUS_DB", str(tmp_path / "journal.db"))
    reset_default_session()
    calls = []

    @guard(effect="refund")
    def refund(customer_id, amount):
        calls.append(amount)
        return {"ok": True}

    assert refund("cust_17", 40) == {"ok": True}
    assert refund("cust_17", 40) == {"ok": True}
    assert calls == [40]


def test_guard_works_without_parentheses(store, sess):
    calls = []

    @guard
    def ping(value):
        calls.append(value)
        return value * 2

    assert ping(2, session=sess) == 4
    assert ping(2, session=sess) == 4
    assert calls == [2]


def test_callee_declared_dedup_is_recorded(store, sess):
    @guard(effect="refund")
    def refund(customer_id, amount, *, session):
        return {"ok": True, "deduplicated": True}

    refund("cust_17", 40, session=sess)
    assert store.rows()[0].callee_deduped == 1
