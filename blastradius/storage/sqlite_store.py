"""SQLite journal store: WAL, one table, canonical JSON payloads."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .base import EffectRow

_SCHEMA = """
CREATE TABLE IF NOT EXISTS effects (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    effect_name TEXT NOT NULL,
    intent_hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    state TEXT NOT NULL,
    result TEXT,
    error TEXT,
    undo_state TEXT,
    undo_error TEXT,
    attempt INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    duplicate_of TEXT,
    collapsed INTEGER NOT NULL DEFAULT 0,
    callee_deduped INTEGER NOT NULL DEFAULT 0,
    idem_key_sent INTEGER NOT NULL DEFAULT 0,
    undoable INTEGER NOT NULL DEFAULT 0,
    dry_run INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS effects_intent
    ON effects (session_id, effect_name, intent_hash);
CREATE INDEX IF NOT EXISTS effects_state ON effects (state);
"""

_COLUMNS = (
    "id",
    "session_id",
    "effect_name",
    "intent_hash",
    "payload",
    "state",
    "result",
    "error",
    "undo_state",
    "undo_error",
    "attempt",
    "created_at",
    "updated_at",
    "latency_ms",
    "duplicate_of",
    "collapsed",
    "callee_deduped",
    "idem_key_sent",
    "undoable",
    "dry_run",
)


class SqliteStore:
    """Durable, thread-safe journal store.

    ``path=":memory:"`` gives an ephemeral store for tests and dry runs.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # -- protocol ---------------------------------------------------------

    def init(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def insert(self, row: EffectRow) -> None:
        values = [getattr(row, name) for name in _COLUMNS]
        placeholders = ", ".join("?" for _ in _COLUMNS)
        sql = f"INSERT INTO effects ({', '.join(_COLUMNS)}) VALUES ({placeholders})"
        with self._lock:
            self._conn.execute(sql, values)
            self._conn.commit()

    def update(self, effect_id: str, **fields: Any) -> None:
        unknown = set(fields) - set(_COLUMNS) - {"id"}
        if unknown:
            raise ValueError(f"unknown journal columns: {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = list(fields.values()) + [effect_id]
        with self._lock:
            self._conn.execute(f"UPDATE effects SET {assignments} WHERE id = ?", values)
            self._conn.commit()

    def get(self, effect_id: str) -> EffectRow | None:
        with self._lock:
            cursor = self._conn.execute("SELECT * FROM effects WHERE id = ?", (effect_id,))
            row = cursor.fetchone()
        return _to_row(row) if row else None

    def latest(self, session_id: str, effect_name: str, intent_hash: str) -> EffectRow | None:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM effects WHERE session_id = ? AND effect_name = ? "
                "AND intent_hash = ? AND dry_run = 0 ORDER BY rowid DESC LIMIT 1",
                (session_id, effect_name, intent_hash),
            )
            row = cursor.fetchone()
        return _to_row(row) if row else None

    def rows(
        self,
        *,
        session_id: str | None = None,
        state: str | None = None,
        dry_run: int | None = None,
    ) -> list[EffectRow]:
        clauses: list[str] = []
        values: list[Any] = []
        if session_id is not None:
            clauses.append("session_id = ?")
            values.append(session_id)
        if state is not None:
            clauses.append("state = ?")
            values.append(state)
        if dry_run is not None:
            clauses.append("dry_run = ?")
            values.append(dry_run)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            cursor = self._conn.execute(
                f"SELECT * FROM effects {where} ORDER BY rowid ASC", values
            )
            raw = cursor.fetchall()
        return [_to_row(row) for row in raw]

    def session_ids(self) -> list[str]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT DISTINCT session_id FROM effects ORDER BY session_id ASC"
            )
            return [row[0] for row in cursor.fetchall()]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _to_row(raw: sqlite3.Row) -> EffectRow:
    data = dict(raw)
    return EffectRow(
        id=data["id"],
        session_id=data["session_id"],
        effect_name=data["effect_name"],
        intent_hash=data["intent_hash"],
        payload=data["payload"],
        state=data["state"],
        result=data["result"],
        error=data["error"],
        undo_state=data["undo_state"],
        undo_error=data["undo_error"],
        attempt=data["attempt"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        latency_ms=data["latency_ms"],
        duplicate_of=data["duplicate_of"],
        collapsed=data["collapsed"],
        callee_deduped=data["callee_deduped"],
        idem_key_sent=data["idem_key_sent"],
        undoable=data["undoable"],
        dry_run=data["dry_run"],
    )


def dumps_canonical(value: Any) -> str:
    """Canonical JSON used for payloads and stored results."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
