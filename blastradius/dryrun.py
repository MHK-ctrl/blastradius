"""Dry-run shadow ledger.

In dry-run mode the guard never calls the wrapped function. It resolves the
intent, writes a row with ``dry_run=1``, and hands the agent a plausible
synthetic result so a real trajectory can finish. ``diff()`` turns those rows
into what would have been written.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .storage.base import DONE, EffectRow
from .storage.sqlite_store import dumps_canonical

_MONEY_KEYS = ("amount", "value", "total", "price", "charge")
_ECHO_KEYS = (
    "amount",
    "customer_id",
    "to",
    "subject",
    "audience",
    "record_id",
    "value",
    "reason",
)


def synthetic_result(
    effect_name: str, payload: Mapping[str, Any], intent_hash: str
) -> dict[str, Any]:
    """What the agent sees instead of a real effect."""
    result: dict[str, Any] = {
        "ok": True,
        "dry_run": True,
        "effect": effect_name,
        "id": f"dry_{intent_hash[:10]}",
    }
    for key in _ECHO_KEYS:
        if key in payload:
            result[key] = payload[key]
    return result


def shadow_row(
    *,
    session_id: str,
    effect_name: str,
    intent_hash: str,
    payload: Mapping[str, Any],
    row_id: str,
    latency_ms: float,
) -> EffectRow:
    now = datetime.now(UTC).isoformat(timespec="microseconds")
    result = synthetic_result(effect_name, payload, intent_hash)
    return EffectRow(
        id=row_id,
        session_id=session_id,
        effect_name=effect_name,
        intent_hash=intent_hash,
        payload=dumps_canonical(payload),
        state=DONE,
        result=dumps_canonical(result),
        latency_ms=latency_ms,
        created_at=now,
        updated_at=now,
        dry_run=1,
    )


def diff(store: Any, *, session_id: str | None = None) -> dict[str, Any]:
    """Per-effect counts, affected identifiers and total money moved."""
    rows = store.rows(session_id=session_id, dry_run=1)
    effects: dict[str, int] = {}
    identifiers: set[str] = set()
    money = 0.0
    for row in rows:
        effects[row.effect_name] = effects.get(row.effect_name, 0) + 1
        payload = row.payload_obj or {}
        if not isinstance(payload, Mapping):
            continue
        for key, value in payload.items():
            if key.endswith("_id") or key in ("id", "to", "email"):
                identifiers.add(str(value))
            if (
                key in _MONEY_KEYS
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
            ):
                money += float(value)
    return {
        "session_id": session_id,
        "total_effects": len(rows),
        "effects": effects,
        "identifiers": sorted(identifiers),
        "money_moved": round(money, 2),
        "dry_run": True,
    }
