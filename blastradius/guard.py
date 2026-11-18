"""The guard: exactly-once, scoped, undoable effects for tool calls."""

from __future__ import annotations

import functools
import inspect
import json
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from . import idempotency
from .dryrun import shadow_row
from .journal import Journal
from .scope import Scope, ScopeViolation, Session
from .storage.base import PENDING
from .storage.sqlite_store import dumps_canonical

_DEFAULT_SESSION: Session | None = None
_DEFAULT_LOCK = threading.Lock()


class GuardError(RuntimeError):
    """Base class for guard-level refusals."""


class PendingEffect(GuardError):
    """An earlier attempt crashed mid-call; the outcome is unknown."""


class EffectFailed(GuardError):
    """A concurrent attempt for the same intent already failed."""


class DuplicateCollapsed(GuardError):
    """Raised instead of returning a stored result when on_duplicate='raise'."""

    def __init__(self, effect_name: str, result: Any) -> None:
        super().__init__(f"{effect_name} was already applied; returning the stored result")
        self.effect_name = effect_name
        self.result = result


def default_session() -> Session:
    """Module-level session for quick starts and single-process scripts."""
    global _DEFAULT_SESSION
    with _DEFAULT_LOCK:
        if _DEFAULT_SESSION is None:
            _DEFAULT_SESSION = Session()
        return _DEFAULT_SESSION


def reset_default_session() -> None:
    """Drop the module-level session (tests, long-lived workers)."""
    global _DEFAULT_SESSION
    with _DEFAULT_LOCK:
        if _DEFAULT_SESSION is not None:
            _DEFAULT_SESSION.close()
        _DEFAULT_SESSION = None


@contextmanager
def session(
    *,
    scope: Sequence[str] | Scope | None = None,
    db_path: str | None = None,
    session_id: str | None = None,
) -> Iterator[Session]:
    """``with session(scope=["refund<=50"]) as s: ...``"""
    created = Session(scope, db_path=db_path, session_id=session_id)
    try:
        yield created
    finally:
        created.close()


def _payload(signature: inspect.Signature, bound: inspect.BoundArguments) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for name, value in bound.arguments.items():
        if name in ("session", "idempotency_key"):
            continue
        payload[name] = value
    return payload


def guard(
    effect: str | Callable[..., Any] | None = None,
    *,
    undo: Callable[[Any], Any] | None = None,
    scope: Sequence[str] | Scope | None = None,
    on_duplicate: str = "return",
    wait_seconds: float = 30.0,
) -> Any:
    """Wrap a side-effecting function.

    ``@guard(effect="refund", undo=reverse_refund, scope="refund<=50")``
    """
    if callable(effect) and not isinstance(effect, str):
        function = effect
        return guard(function.__name__)(function)
    if not effect:
        raise TypeError("guard() requires an effect name, e.g. @guard(effect='refund')")
    if on_duplicate not in ("return", "raise"):
        raise ValueError("on_duplicate must be 'return' or 'raise'")

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        signature = inspect.signature(fn)
        accepts_session = "session" in signature.parameters
        accepts_key = "idempotency_key" in signature.parameters or any(
            param.kind is inspect.Parameter.VAR_KEYWORD
            for param in signature.parameters.values()
        )
        effect_name = str(effect)
        scope_override = Scope.parse(scope)

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            sess: Session = kwargs.get("session") or default_session()
            if accepts_session:
                if kwargs.get("session") is None:
                    kwargs["session"] = sess
            else:
                kwargs.pop("session", None)

            bound = signature.bind(*args, **kwargs)
            payload = _payload(signature, bound)

            allowed, reason = _check(sess, scope_override, effect_name, payload)
            if not allowed:
                elapsed = _ms(started, started)
                sess.journal.mark_blocked(
                    session_id=sess.id,
                    effect_name=effect_name,
                    intent_hash=idempotency.canonical_intent(effect_name, payload),
                    payload=payload,
                    reason=reason or "out of scope",
                    latency_ms=elapsed,
                )
                raise ScopeViolation(effect_name, reason or "out of scope", sess.scope)

            _, _, intent_hash = idempotency.intent_key(sess.id, effect_name, payload)
            wire_key = idempotency.idempotency_key(sess.id, effect_name, intent_hash)

            if sess.dry_run_active:
                row = shadow_row(
                    session_id=sess.id,
                    effect_name=effect_name,
                    intent_hash=intent_hash,
                    payload=payload,
                    row_id=f"dry_{uuid.uuid4().hex}",
                    latency_ms=_ms(started, started),
                )
                sess.journal.store.insert(row)
                return json.loads(row.result or "{}")

            claim = sess.journal.claim(
                session_id=sess.id,
                effect_name=effect_name,
                intent_hash=intent_hash,
                payload=payload,
                undoable=undo is not None,
                idem_key_sent=accepts_key,
            )

            if not claim.owner:
                return _resolve_duplicate(
                    sess, claim.row, claim.event, effect_name, payload, intent_hash,
                    on_duplicate, wait_seconds, started,
                )

            if accepts_key:
                kwargs["idempotency_key"] = wire_key
            before_call = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                after = time.perf_counter()
                overhead = _overhead_ms(started, before_call, after)
                sess.journal.fail(claim.row, f"{type(exc).__name__}: {exc}", overhead)
                raise
            after = time.perf_counter()
            overhead = _overhead_ms(started, before_call, after)
            result_json = _serialise(result)
            deduped = _declares_dedup(result)
            sess.journal.complete(claim.row, result_json, overhead)
            if deduped:
                from .journal import utc_now

                sess.journal.store.update(
                    claim.row.id, callee_deduped=1, updated_at=utc_now()
                )
            if undo is not None:
                sess.journal.register_undo(claim.row.id, undo)
            return result

        def _check(sess: Session, override: Scope, name: str, payload: Mapping[str, Any]) -> Any:
            allowed, reason = sess.scope.allows(name, payload)
            if not allowed:
                return False, reason
            if not override.is_empty:
                return override.allows(name, payload)
            return True, None

        return wrapper

    return decorator


def _resolve_duplicate(
    sess: Session,
    row: Any,
    event: threading.Event | None,
    effect_name: str,
    payload: Mapping[str, Any],
    intent_hash: str,
    on_duplicate: str,
    wait_seconds: float,
    started: float,
) -> Any:
    if row.state == PENDING:
        if event is None:
            raise PendingEffect(
                f"{effect_name} for session {sess.id} is stuck in 'pending' (row {row.id}); "
                "resolve it with session.recover(assume='failed'|'done') before retrying"
            )
        event.wait(wait_seconds)
        latest = sess.journal.store.latest(sess.id, effect_name, intent_hash)
        if latest is None or latest.state == PENDING:
            raise PendingEffect(
                f"{effect_name} for session {sess.id} is still pending after "
                f"{wait_seconds:g}s; resolve it with session.recover(...)"
            )
        row = latest
        if row.state == "failed":
            raise EffectFailed(f"{effect_name} failed in a concurrent attempt: {row.error}")

    stored = row.result_obj
    elapsed = _ms(started, started)
    sess.journal.store.insert(
        shadow_collapse(sess.id, effect_name, intent_hash, payload, row, elapsed)
    )
    if on_duplicate == "raise":
        raise DuplicateCollapsed(effect_name, stored)
    return stored


def shadow_collapse(
    session_id: str,
    effect_name: str,
    intent_hash: str,
    payload: Mapping[str, Any],
    prior: Any,
    latency_ms: float,
) -> Any:
    from .journal import utc_now
    from .storage.base import DONE, EffectRow

    now = utc_now()
    return EffectRow(
        id=f"eff_{uuid.uuid4().hex}",
        session_id=session_id,
        effect_name=effect_name,
        intent_hash=intent_hash,
        payload=idempotency.canonical_payload(payload),
        state=DONE,
        result=prior.result,
        duplicate_of=prior.id,
        collapsed=1,
        attempt=prior.attempt,
        latency_ms=latency_ms,
        created_at=now,
        updated_at=now,
    )


def _serialise(value: Any) -> str:
    return dumps_canonical(value)


def _declares_dedup(result: Any) -> bool:
    return isinstance(result, Mapping) and bool(result.get("deduplicated"))


def _ms(start: float, end: float) -> float:
    return round((end - start) * 1000, 3)


def _overhead_ms(start: float, before_call: float, after_call: float) -> float:
    """Guard cost only: setup + hashing + journal I/O, excluding the call."""
    return round(((before_call - start) + (time.perf_counter() - after_call)) * 1000, 3)


__all__ = [
    "DuplicateCollapsed",
    "EffectFailed",
    "GuardError",
    "Journal",
    "PendingEffect",
    "default_session",
    "guard",
    "reset_default_session",
    "session",
]
