from __future__ import annotations

import pytest

from blastradius import ScopeViolation, Session, UndoFailure
from blastradius.guard import guard
from blastradius.storage.base import DONE, UNROLLED, UNROLLED_FAILED


def _effect(name, log, *, undo=None):
    @guard(effect=name, undo=undo)
    def action(value, *, session):
        return {"value": value}

    return action


def test_rollback_runs_undos_in_reverse_order(store):
    session = Session(store=store)
    order = []

    def undo_one(result):
        order.append(("one", result))

    def undo_two(result):
        order.append(("two", result))

    def undo_three(result):
        order.append(("three", result))

    _effect("one", order, undo=undo_one)(1, session=session)
    _effect("two", order, undo=undo_two)(2, session=session)
    _effect("three", order, undo=undo_three)(3, session=session)

    report = session.rollback()
    assert [name for name, _ in order] == ["three", "two", "one"]
    assert report.unrolled == ["three", "two", "one"]
    assert report.ok


def test_rollback_journals_each_undo(store):
    session = Session(store=store)
    undone = []

    @guard(effect="refund", undo=lambda result: undone.append(result))
    def refund(amount, *, session):
        return {"ok": True, "amount": amount}

    refund(10, session=session)
    refund(20, session=session)
    session.rollback()
    rows = store.rows(session_id=session.id)
    assert [row.state for row in rows] == [UNROLLED, UNROLLED]
    assert all(row.undo_state == "unrolled" for row in rows)
    assert undone == [{"ok": True, "amount": 20}, {"ok": True, "amount": 10}]


def test_failed_undo_is_loud_and_marked(store):
    session = Session(store=store)
    order = []

    def undo_ok(result):
        order.append(("ok", result))

    def undo_boom(result):
        order.append(("boom", result))
        raise RuntimeError("reversal rejected")

    @guard(effect="third", undo=undo_ok)
    def third(session):
        return {"n": 3}

    @guard(effect="second", undo=undo_boom)
    def second(session):
        return {"n": 2}

    @guard(effect="first", undo=undo_ok)
    def first(session):
        return {"n": 1}

    first(session=session)
    second(session=session)
    third(session=session)

    with pytest.raises(UndoFailure) as caught:
        session.rollback()
    assert "reversal rejected" in str(caught.value)
    assert [name for name, _ in order] == ["ok", "boom", "ok"]
    states = {row.effect_name: row.state for row in store.rows(session_id=session.id)}
    assert states == {"first": UNROLLED, "second": UNROLLED_FAILED, "third": UNROLLED}


def test_rollback_skips_blocked_and_failed_effects(store):
    session = Session(scope=["refund<=50"], store=store)
    called = []

    @guard(effect="refund", undo=lambda result: called.append(result))
    def refund(amount, *, session):
        if amount == 49:
            raise ValueError("gateway down")
        return {"amount": amount}

    refund(10, session=session)
    with pytest.raises(ValueError, match="gateway down"):
        refund(49, session=session)
    with pytest.raises(ScopeViolation):
        refund(500, session=session)

    report = session.rollback()
    assert report.unrolled == ["refund"]
    assert called == [{"amount": 10}]
    states = [row.state for row in store.rows(session_id=session.id)]
    assert states == [UNROLLED, "failed", "blocked"]


def test_double_rollback_does_not_undo_twice(store):
    session = Session(store=store)
    calls = []

    @guard(effect="refund", undo=lambda result: calls.append(result))
    def refund(session):
        return {"ok": True}

    refund(session=session)
    session.rollback()
    session.rollback()
    assert calls == [{"ok": True}]


def test_rollback_reports_effects_without_a_handler(store):
    session = Session(store=store)

    @guard(effect="refund")
    def refund(session):
        return {"ok": True}

    refund(session=session)
    report = session.rollback()
    assert report.skipped_no_handler == ["refund"]
    assert store.rows(session_id=session.id)[0].undo_state == "no_handler"


def test_rollback_state_is_visible_as_done_before_it_runs(store):
    session = Session(store=store)

    @guard(effect="refund", undo=lambda result: None)
    def refund(session):
        return {"ok": True}

    refund(session=session)
    assert store.rows(state=DONE)
    session.rollback()
    assert store.rows(state=DONE) == []
