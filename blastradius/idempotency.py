"""Intent-derived idempotency keys.

The key is a function of *what was asked*, never of *how many times it was
asked*. That is the whole point: a retry produces the same key, a genuinely
different request does not.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any


class IntentError(TypeError):
    """Raised when an argument cannot be normalised into a stable intent."""


def _normalise(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    if isinstance(value, Decimal):
        return _normalise(float(value))
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _normalise(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_normalise(item) for item in value), key=repr)
    if is_dataclass(value) and not isinstance(value, type):
        return _normalise(asdict(value))
    raise IntentError(
        f"cannot derive an intent from {type(value).__name__}; "
        "pass JSON-serialisable arguments (dict, list, str, int, float, bool, None)"
    )


def canonical_payload(payload: Mapping[str, Any]) -> str:
    """Sort-keyed JSON with normalised numbers, stable across processes."""
    return json.dumps(
        _normalise(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def canonical_intent(effect_name: str, payload: Mapping[str, Any]) -> str:
    """SHA-256 over the effect name and the canonical payload."""
    material = f"{effect_name}\x00{canonical_payload(payload)}".encode()
    return hashlib.sha256(material).hexdigest()


def intent_key(
    session_id: str, effect_name: str, payload: Mapping[str, Any]
) -> tuple[str, str, str]:
    """The journal's unique key for an intent: (session, effect, hash)."""
    return (session_id, effect_name, canonical_intent(effect_name, payload))


def idempotency_key(session_id: str, effect_name: str, intent_hash: str) -> str:
    """Key forwarded to a callee that understands idempotency keys."""
    material = f"{session_id}\x00{effect_name}\x00{intent_hash}".encode()
    return "br_" + hashlib.sha256(material).hexdigest()[:32]
