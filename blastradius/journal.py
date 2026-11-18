"""Write-ahead journal.

Order is non-negotiable: insert ``pending``, then call the world, then mark
``done`` or ``failed``. A row left ``pending`` means the process died in the
middle and only a human (or an explicit caller decision) may resolve it.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .idempotency import canonical_payload
from .storage.base import (
    DONE,
    FAILED,
    PENDING,
    EffectRow,
    JournalStore,
)
from .storage.sqlite_store import SqliteStore


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


@dataclass
class Claim:
    """Result of trying to own an intent."""

    row: EffectRow
    event: threading.Event | None
    owner: bool
    prior: EffectRow | None


class Journal:
    """Durable effect journal over any :class:`JournalStore`."""

    def __init__(self, store: JournalStore | None = None, path: str | None = None) -> None:
        self.store: JournalStore = store if store is not None else SqliteStore(path or ":memory:")
        self._lock = threading.RLock()
        self._events: dict[tuple[str, str, str], threading.Event] = {}
        self._undo: dict[str, Any] = {}

    # -- lifecycle --------------------------------------------------------

    def claim(
        self,
        *,
        session_id: str,
        effect_name: str,
        intent_hash: str,
        payload: dict[str, Any],
        undoable: bool = False,
        idem_key_sent: bool = False,
    ) -> Claim:
        """Atomically decide what this attempt means.

        Returns an owner claim (a fresh ``pending`` row we must complete), a
        duplicate claim (prior row already ``done``), or a waiter (another
        thread in this process is mid-call for the same intent).
        """
        key = (session_id, effect_name, intent_hash)
        with self._lock:
            prior = self.store.latest(session_id, effect_name, intent_hash)
            if prior is not None and prior.state == PENDING:
                return Claim(prior, self._events.get(key), False, prior)
            if prior is not None and prior.state == DONE:
                return Claim(prior, None, False, prior)
            attempt = (prior.attempt + 1) if prior is not None else 1
            row = EffectRow(
                id=f"eff_{uuid.uuid4().hex}",
                session_id=session_id,
                effect_name=effect_name,
                intent_hash=intent_hash,
                payload=canonical_payload(payload),
                state=PENDING,
                attempt=attempt,
                created_at=utc_now(),
                updated_at=utc_now(),
                undoable=1 if undoable else 0,
                idem_key_sent=1 if idem_key_sent else 0,
            )
            self.store.insert(row)
            event = threading.Event()
            self._events[key] = event
            return Claim(row, event, True, prior)

    def complete(self, row: EffectRow, result_json: str, latency_ms: float) -> None:
        with self._lock:
            self.store.update(
                row.id,
                state=DONE,
                result=result_json,
                latency_ms=latency_ms,
                updated_at=utc_now(),
            )
            self._release(row)
        row.state = DONE
        row.result = result_json

    def fail(self, row: EffectRow, error: str, latency_ms: float) -> None:
        with self._lock:
            self.store.update(
                row.id,
                state=FAILED,
                error=error,
                latency_ms=latency_ms,
                updated_at=utc_now(),
            )
            self._release(row)
        row.state = FAILED
        row.error = error

    def mark_blocked(
        self,
        *,
        session_id: str,
        effect_name: str,
        intent_hash: str,
        payload: dict[str, Any],
        reason: str,
        latency_ms: float,
    ) -> EffectRow:
        row = EffectRow(
            id=f"eff_{uuid.uuid4().hex}",
            session_id=session_id,
            effect_name=effect_name,
            intent_hash=intent_hash,
            payload=canonical_payload(payload),
            state="blocked",
            error=reason,
            latency_ms=latency_ms,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self.store.insert(row)
        return row

    def _release(self, row: EffectRow) -> None:
        key = (row.session_id, row.effect_name, row.intent_hash)
        event = self._events.pop(key, None)
        if event is not None:
            event.set()

    # -- undo registry ----------------------------------------------------

    def register_undo(self, row_id: str, handler: Any) -> None:
        with self._lock:
            self._undo[row_id] = handler

    def undo_handler(self, row_id: str) -> Any | None:
        with self._lock:
            return self._undo.get(row_id)

    # -- recovery ---------------------------------------------------------

    def pending(self, session_id: str | None = None) -> list[EffectRow]:
        rows = self.store.rows(state=PENDING)
        if session_id is not None:
            rows = [row for row in rows if row.session_id == session_id]
        return rows

    def recover(
        self,
        *,
        session_id: str | None = None,
        assume: str | None = None,
    ) -> list[EffectRow]:
        """Return rows stuck in ``pending``.

        With ``assume=None`` nothing is mutated. ``assume="failed"`` or
        ``assume="done"`` is an explicit decision recorded on the row.
        """
        if assume not in (None, "failed", "done"):
            raise ValueError("assume must be None, 'failed' or 'done'")
        rows = self.pending(session_id)
        if assume is None:
            return rows
        for row in rows:
            note = f"recovered: assumed {assume} by caller at {utc_now()}"
            new_state = FAILED if assume == "failed" else DONE
            self.store.update(
                row.id,
                state=new_state,
                error=note,
                updated_at=utc_now(),
            )
            row.state = new_state
            row.error = note
        return rows
