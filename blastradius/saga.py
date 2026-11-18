"""Saga rollback: undo completed effects in reverse order.

Every undo is journaled. A failed undo is never swallowed: it marks the row
``unrolled_failed`` and is raised at the end as :class:`UndoFailure` after the
remaining effects have been attempted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .journal import utc_now
from .storage.base import DONE, UNROLLED, UNROLLED_FAILED


class UndoFailure(Exception):
    """One or more undo handlers failed during rollback."""

    def __init__(self, failures: list[tuple[str, str]]) -> None:
        summary = "; ".join(f"{effect}: {error}" for effect, error in failures)
        super().__init__(f"{len(failures)} undo(s) failed: {summary}")
        self.failures = failures


@dataclass
class RollbackReport:
    unrolled: list[str] = field(default_factory=list)
    skipped_no_handler: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


def rollback(session: Any) -> RollbackReport:
    """Walk this session's completed effects newest-first and undo them."""
    store = session.journal.store
    rows = [
        row
        for row in store.rows(session_id=session.id)
        if row.state == DONE and not row.collapsed and not row.dry_run and not row.duplicate_of
    ]
    rows.reverse()  # insertion order from the store, walked newest-first
    report = RollbackReport()
    for row in rows:
        if row.undo_state is not None:
            continue
        handler = session.journal.undo_handler(row.id)
        if handler is None:
            store.update(row.id, undo_state="no_handler", updated_at=utc_now())
            report.skipped_no_handler.append(row.effect_name)
            continue
        try:
            handler(row.result_obj)
        except Exception as exc:  # noqa: BLE001 - surfaced, not swallowed
            store.update(
                row.id,
                state=UNROLLED_FAILED,
                undo_state="failed",
                undo_error=f"{type(exc).__name__}: {exc}",
                updated_at=utc_now(),
            )
            report.failed.append((row.effect_name, f"{type(exc).__name__}: {exc}"))
            continue
        store.update(
            row.id,
            state=UNROLLED,
            undo_state="unrolled",
            updated_at=utc_now(),
        )
        report.unrolled.append(row.effect_name)
    if report.failed:
        raise UndoFailure(report.failed)
    return report
