from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from blastradius import Session, reset_default_session
from blastradius.storage import EffectRow, SqliteStore


@pytest.fixture(autouse=True)
def _clean_default_session():
    yield
    reset_default_session()


@pytest.fixture
def store():
    database = SqliteStore(":memory:")
    yield database
    database.close()


@pytest.fixture
def sess(store):
    return Session(store=store)


def make_row(
    *,
    session_id: str = "s1",
    effect: str = "refund",
    intent_hash: str = "h1",
    state: str = "done",
    **kwargs,
) -> EffectRow:
    now = datetime.now(UTC).isoformat(timespec="microseconds")
    return EffectRow(
        id=f"eff_{uuid.uuid4().hex}",
        session_id=session_id,
        effect_name=effect,
        intent_hash=intent_hash,
        payload="{}",
        state=state,
        created_at=now,
        updated_at=now,
        **kwargs,
    )
