"""Pluggable journal storage.

The library needs five operations from a store and nothing else, so a
Postgres or DynamoDB backend is a small adapter rather than a rewrite.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# Lifecycle states required by the protocol. Rows representing a suppressed
# duplicate keep state "done" and carry collapsed=1 instead.
PENDING = "pending"
DONE = "done"
FAILED = "failed"
BLOCKED = "blocked"
UNROLLED = "unrolled"
UNROLLED_FAILED = "unrolled_failed"

STATES = (PENDING, DONE, FAILED, BLOCKED, UNROLLED, UNROLLED_FAILED)


@dataclass
class EffectRow:
    """One journal row. Payload and result are canonical JSON text."""

    id: str
    session_id: str
    effect_name: str
    intent_hash: str
    payload: str
    state: str
    result: str | None = None
    error: str | None = None
    undo_state: str | None = None
    undo_error: str | None = None
    attempt: int = 1
    created_at: str = ""
    updated_at: str = ""
    latency_ms: int = 0
    duplicate_of: str | None = None
    collapsed: int = 0
    callee_deduped: int = 0
    idem_key_sent: int = 0
    undoable: int = 0
    dry_run: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def payload_obj(self) -> Any:
        return json.loads(self.payload) if self.payload else None

    @property
    def result_obj(self) -> Any:
        if self.result is None:
            return None
        try:
            return json.loads(self.result)
        except json.JSONDecodeError:
            return self.result


@runtime_checkable
class JournalStore(Protocol):
    """Minimal durable interface the journal relies on."""

    def init(self) -> None: ...

    def insert(self, row: EffectRow) -> None: ...

    def update(self, effect_id: str, **fields: Any) -> None: ...

    def get(self, effect_id: str) -> EffectRow | None: ...

    def latest(self, session_id: str, effect_name: str, intent_hash: str) -> EffectRow | None: ...

    def rows(
        self,
        *,
        session_id: str | None = None,
        state: str | None = None,
        dry_run: int | None = None,
    ) -> list[EffectRow]: ...

    def session_ids(self) -> list[str]: ...

    def close(self) -> None: ...
