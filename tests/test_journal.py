from __future__ import annotations

import subprocess
import sys

import pytest

from blastradius import Session
from blastradius.guard import guard


def _claim(session, effect="refund", payload=None):
    payload = payload or {"customer_id": "cust_17", "amount": 40}
    return session.journal.claim(
        session_id=session.id,
        effect_name=effect,
        intent_hash="hash",
        payload=payload,
    )


def test_recover_returns_pending_rows_and_changes_nothing(store, sess):
    claim = _claim(sess)
    rows = sess.journal.recover(session_id=sess.id)
    assert [row.id for row in rows] == [claim.row.id]
    assert store.get(claim.row.id).state == "pending"


def test_recover_assume_failed_records_the_decision(store, sess):
    claim = _claim(sess)
    sess.journal.recover(session_id=sess.id, assume="failed")
    row = store.get(claim.row.id)
    assert row.state == "failed"
    assert "assumed failed" in row.error


def test_recover_assume_done_records_the_decision(store, sess):
    claim = _claim(sess)
    sess.journal.recover(session_id=sess.id, assume="done")
    row = store.get(claim.row.id)
    assert row.state == "done"
    assert "assumed done" in row.error


def test_recover_rejects_an_invented_resolution(store, sess):
    with pytest.raises(ValueError):
        sess.journal.recover(session_id=sess.id, assume="probably fine")


def test_recovered_pending_row_is_no_longer_listed(store, sess):
    _claim(sess)
    sess.journal.recover(session_id=sess.id, assume="failed")
    assert sess.journal.pending(sess.id) == []


def test_journal_survives_a_separate_process(tmp_path):
    db = tmp_path / "journal.db"
    code = f"""
from blastradius import Session, guard

@guard(effect="refund")
def refund(customer_id, amount, *, session):
    return {{"ok": True, "amount": amount}}

session = Session(db_path={str(db)!r}, session_id="restart-1")
print(refund("cust_9", 25, session=session))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "ok" in completed.stdout

    calls = []

    @guard(effect="refund")
    def refund(customer_id, amount, *, session):
        calls.append(amount)
        return {"ok": False}

    session = Session(db_path=str(db), session_id="restart-1")
    assert refund("cust_9", 25, session=session) == {"ok": True, "amount": 25}
    assert calls == []


def test_store_rejects_unknown_columns(store):
    with pytest.raises(ValueError):
        store.update("eff_1", definitely_not_a_column=1)
