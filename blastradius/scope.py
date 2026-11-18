"""Capability scope tokens, enforced in code at the tool boundary.

A scope is an allow-list. ``refund<=50`` permits refund effects up to 50,
``refund<=*`` permits any amount, ``email:internal`` permits email effects whose
payload says ``audience="internal"``. An effect that is not in the scope is
denied. Narrowing a scope can only remove capability, never add it.
"""

from __future__ import annotations

import os
import re
import threading
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from . import dryrun
from .journal import Journal
from .storage.sqlite_store import SqliteStore

_TOKEN = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_.-]*)(?P<op><=|:)(?P<value>.+)$")
_MONEY_KEYS = ("amount", "value", "total", "price", "charge")


class ScopeError(ValueError):
    """Malformed scope token, or a narrowing that would widen capability."""


class ScopeViolation(Exception):
    """An effect was refused before it ran."""

    def __init__(self, effect_name: str, reason: str, scope: Scope | None = None) -> None:
        super().__init__(f"{effect_name} blocked by scope: {reason}")
        self.effect_name = effect_name
        self.reason = reason
        self.scope = scope


@dataclass(frozen=True)
class Scope:
    """Allow-list of effects and their constraints."""

    ceilings: Mapping[str, float | None] = field(default_factory=dict)
    tags: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, tokens: Sequence[str] | Scope | None) -> Scope:
        if tokens is None:
            return cls()
        if isinstance(tokens, Scope):
            return tokens
        if isinstance(tokens, str):
            tokens = [tokens]
        ceilings: dict[str, float | None] = {}
        tags: dict[str, str] = {}
        for token in tokens:
            match = _TOKEN.match(token.strip())
            if not match:
                raise ScopeError(
                    f"cannot parse scope token {token!r}; expected 'effect<=number', "
                    "'effect<=*' or 'effect:tag'"
                )
            name, op, value = match.group("name"), match.group("op"), match.group("value")
            if op == "<=":
                if value == "*":
                    ceilings[name] = None
                else:
                    try:
                        ceilings[name] = float(value)
                    except ValueError as exc:
                        raise ScopeError(f"scope ceiling for {name!r} is not a number") from exc
            else:
                if name in tags and tags[name] != value:
                    raise ScopeError(f"conflicting tags for effect {name!r}")
                tags[name] = value
        return cls(ceilings=ceilings, tags=tags)

    @property
    def is_empty(self) -> bool:
        return not self.ceilings and not self.tags

    def allows(self, effect_name: str, payload: Mapping[str, Any]) -> tuple[bool, str | None]:
        if self.is_empty:
            return True, None
        if effect_name not in self.ceilings and effect_name not in self.tags:
            return False, f"effect {effect_name!r} is not in scope"
        if effect_name in self.ceilings:
            ceiling = self.ceilings[effect_name]
            if ceiling is not None:
                amount = next(
                    (
                        payload[key]
                        for key in _MONEY_KEYS
                        if isinstance(payload.get(key), (int, float))
                    ),
                    None,
                )
                if amount is None or isinstance(amount, bool):
                    return False, f"{effect_name} requires a numeric 'amount' in the payload"
                if float(amount) > ceiling:
                    return False, f"amount {amount} exceeds ceiling {ceiling:g}"
        if effect_name in self.tags:
            required = self.tags[effect_name]
            audience = payload.get("audience")
            if audience != required:
                return False, f"requires audience={required!r}, got {audience!r}"
        return True, None

    def narrow(self, tokens: Sequence[str] | Scope | None) -> Scope:
        """Intersect with another scope. Only ever removes capability."""
        child = Scope.parse(tokens)
        ceilings: dict[str, float | None] = dict(self.ceilings)
        for name, ceiling in child.ceilings.items():
            if name not in ceilings:
                ceilings[name] = ceiling
                continue
            parent = ceilings[name]
            if parent is None:
                ceilings[name] = ceiling
            elif ceiling is None:
                ceilings[name] = parent
            else:
                ceilings[name] = min(parent, ceiling)
        tags: dict[str, str] = dict(self.tags)
        for name, tag in child.tags.items():
            if name in tags and tags[name] != tag:
                raise ScopeError(
                    f"cannot narrow {name!r}: parent requires {tags[name]!r}, child {tag!r}"
                )
            tags[name] = tag
        return Scope(ceilings=ceilings, tags=tags)

    def describe(self) -> list[str]:
        tokens = [f"{name}:{tag}" for name, tag in self.tags.items()]
        tokens += [
            f"{name}<={'*' if ceiling is None else f'{ceiling:g}'}"
            for name, ceiling in self.ceilings.items()
        ]
        return sorted(tokens)


def default_db_path() -> str:
    return os.environ.get("BLASTRADIUS_DB") or os.path.join(".blastradius", "journal.db")


class Session:
    """A run: one journal, one scope, one identity."""

    def __init__(
        self,
        scope: Sequence[str] | Scope | None = None,
        *,
        store: Any | None = None,
        db_path: str | None = None,
        session_id: str | None = None,
        journal: Journal | None = None,
    ) -> None:
        self.id = session_id or f"s_{uuid.uuid4().hex[:12]}"
        self.scope = Scope.parse(scope)
        self._owns_journal = journal is None and store is None
        if journal is not None:
            self.journal = journal
        elif store is not None:
            self.journal = Journal(store)
        else:
            self.journal = Journal(SqliteStore(db_path or default_db_path()))
        self._dry = False
        self._lock = threading.RLock()

    # -- session tree -----------------------------------------------------

    def child(self, scope: Sequence[str] | Scope | None = None) -> Session:
        """A narrower view of the same run (same journal, same session id)."""
        child = Session.__new__(Session)
        child.id = self.id
        child.scope = self.scope.narrow(scope)
        child.journal = self.journal
        child._owns_journal = False
        child._dry = self._dry
        child._lock = threading.RLock()
        return child

    # -- dry run ----------------------------------------------------------

    @property
    def dry_run_active(self) -> bool:
        return self._dry

    @contextmanager
    def dry_run(self) -> Iterator[Session]:
        previous = self._dry
        self._dry = True
        try:
            yield self
        finally:
            self._dry = previous

    def dry_run_diff(self) -> dict[str, Any]:
        return dryrun.diff(self.journal.store, session_id=self.id)

    # -- rollback and lifecycle -------------------------------------------

    def rollback(self) -> Any:
        from .saga import rollback

        return rollback(self)

    def pending(self) -> list[Any]:
        return self.journal.pending(self.id)

    def recover(self, *, assume: str | None = None) -> list[Any]:
        return self.journal.recover(session_id=self.id, assume=assume)

    def close(self) -> None:
        if self._owns_journal:
            self.journal.store.close()

    def __enter__(self) -> Session:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
