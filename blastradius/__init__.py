"""blastradius — exactly-once, undoable side effects for AI agents."""

from __future__ import annotations

from .guard import (
    DuplicateCollapsed,
    EffectFailed,
    GuardError,
    PendingEffect,
    default_session,
    guard,
    reset_default_session,
    session,
)
from .journal import Journal
from .report import build_report
from .saga import UndoFailure, rollback
from .scope import Scope, ScopeError, ScopeViolation, Session
from .storage.base import EffectRow, JournalStore
from .storage.sqlite_store import SqliteStore

__version__ = "0.1.0"

__all__ = [
    "DuplicateCollapsed",
    "EffectFailed",
    "EffectRow",
    "GuardError",
    "Journal",
    "JournalStore",
    "PendingEffect",
    "Scope",
    "ScopeError",
    "ScopeViolation",
    "Session",
    "SqliteStore",
    "UndoFailure",
    "__version__",
    "build_report",
    "default_session",
    "guard",
    "reset_default_session",
    "rollback",
    "session",
]
