from __future__ import annotations

from blastradius import Session
from blastradius.guard import guard


def test_dry_run_never_executes_the_function(store):
    session = Session(store=store)
    calls = []

    @guard(effect="refund")
    def refund(customer_id, amount, *, session):
        calls.append(amount)
        return {"ok": True}

    with session.dry_run():
        result = refund("cust_17", 40, session=session)
    assert calls == []
    assert result["dry_run"] is True
    assert result["ok"] is True
    assert result["amount"] == 40
    assert result["id"].startswith("dry_")


def test_dry_run_leaves_no_real_effects(store):
    session = Session(store=store)

    @guard(effect="refund")
    def refund(customer_id, amount, *, session):
        return {"ok": True}

    with session.dry_run():
        refund("cust_17", 40, session=session)
    assert store.rows(dry_run=0) == []
    assert len(store.rows(dry_run=1)) == 1


def test_dry_run_diff_counts_effects_identifiers_and_money(store):
    session = Session(store=store)

    @guard(effect="refund")
    def refund(customer_id, amount, *, session):
        return {"ok": True}

    @guard(effect="email")
    def email(to, audience, *, session):
        return {"ok": True}

    with session.dry_run():
        refund("cust_17", 40, session=session)
        refund("cust_18", 25.5, session=session)
        email("team@corp", "internal", session=session)

    diff = session.dry_run_diff()
    assert diff["total_effects"] == 3
    assert diff["effects"] == {"refund": 2, "email": 1}
    assert diff["identifiers"] == ["cust_17", "cust_18", "team@corp"]
    assert diff["money_moved"] == 65.5


def test_state_is_restored_after_the_dry_run_block(store):
    session = Session(store=store)
    calls = []

    @guard(effect="refund")
    def refund(amount, *, session):
        calls.append(amount)
        return {"ok": True}

    with session.dry_run():
        refund(10, session=session)
    assert session.dry_run_active is False
    refund(10, session=session)
    assert calls == [10]


def test_dry_run_does_not_collapse_against_real_effects(store):
    session = Session(store=store)
    calls = []

    @guard(effect="refund")
    def refund(amount, *, session):
        calls.append(amount)
        return {"ok": True}

    refund(10, session=session)
    with session.dry_run():
        refund(10, session=session)
    assert calls == [10]
    assert session.dry_run_diff()["total_effects"] == 1
