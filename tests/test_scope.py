from __future__ import annotations

import pytest

from blastradius import ScopeError, ScopeViolation, Session
from blastradius.guard import guard


def test_ceiling_allows_equal_and_lower_values(store):
    session = Session(scope=["refund<=50"], store=store)
    calls = []

    @guard(effect="refund")
    def refund(customer_id, amount, *, session):
        calls.append(amount)
        return {"ok": True}

    assert refund("c", 50, session=session) == {"ok": True}
    assert refund("c", 12.5, session=session) == {"ok": True}
    assert calls == [50, 12.5]


def test_ceiling_blocks_before_the_function_runs(store):
    session = Session(scope=["refund<=50"], store=store)
    calls = []

    @guard(effect="refund")
    def refund(customer_id, amount, *, session):
        calls.append(amount)
        return {"ok": True}

    with pytest.raises(ScopeViolation, match="refund"):
        refund("c", 50.01, session=session)
    assert calls == []
    rows = store.rows(session_id=session.id)
    assert len(rows) == 1
    assert rows[0].state == "blocked"
    assert "exceeds ceiling 50" in rows[0].error


def test_star_ceiling_allows_any_amount(store):
    session = Session(scope=["refund<=*"], store=store)

    @guard(effect="refund")
    def refund(amount, *, session):
        return {"ok": True}

    assert refund(10_000, session=session) == {"ok": True}


def test_tag_allows_only_the_declared_audience(store):
    session = Session(scope=["email:internal"], store=store)
    calls = []

    @guard(effect="email")
    def email(to, audience, *, session):
        calls.append(audience)
        return {"ok": True}

    assert email("team@corp", "internal", session=session) == {"ok": True}
    with pytest.raises(ScopeViolation):
        email("press@example.com", "external", session=session)
    assert calls == ["internal"]
    assert store.rows(session_id=session.id)[-1].state == "blocked"


def test_effect_outside_the_scope_is_denied(store):
    session = Session(scope=["refund<=50"], store=store)

    @guard(effect="email")
    def email(to, audience, *, session):
        return {"ok": True}

    with pytest.raises(ScopeViolation, match="not in scope"):
        email("team@corp", "internal", session=session)


def test_unscoped_session_allows_any_effect(store, sess):
    @guard(effect="anything")
    def act(value, *, session):
        return value

    assert act("x", session=sess) == "x"


def test_child_session_narrows_the_ceiling(store):
    parent = Session(scope=["refund<=50"], store=store)
    child = parent.child(["refund<=25"])
    assert child.id == parent.id
    assert child.scope.ceilings["refund"] == 25
    calls = []

    @guard(effect="refund")
    def refund(amount, *, session):
        calls.append(amount)
        return {"ok": True}

    refund(20, session=child)
    with pytest.raises(ScopeViolation):
        refund(30, session=child)
    refund(30, session=parent)
    assert calls == [20, 30]


def test_child_cannot_widen_the_ceiling(store):
    parent = Session(scope=["refund<=50"], store=store)
    child = parent.child(["refund<=500"])
    assert child.scope.ceilings["refund"] == 50


def test_child_tag_conflict_is_rejected(store):
    parent = Session(scope=["email:internal"], store=store)
    with pytest.raises(ScopeError, match="cannot narrow"):
        parent.child(["email:external"])


def test_malformed_scope_token_is_rejected(store):
    with pytest.raises(ScopeError):
        Session(scope=["refund less than fifty"], store=store)
