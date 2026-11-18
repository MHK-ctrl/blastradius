from __future__ import annotations

from conftest import make_row

from blastradius.report import REQUIRED_KEYS, build_report, percentile
from blastradius.storage.base import BLOCKED, DONE, UNROLLED, UNROLLED_FAILED


def test_report_contains_every_required_key(store):
    data = build_report(store)
    for key in REQUIRED_KEYS:
        assert key in data


def test_empty_journal_is_all_zeroes(store):
    data = build_report(store)
    assert data["tasks"] == 0
    assert data["effects"] == 0
    assert data["duplicate_writes"] == 0
    assert data["p50_added_latency_ms"] == 0.0


def test_tasks_and_effects_are_distinct_counts(store):
    store.insert(make_row(session_id="s1", effect="refund", intent_hash="h1"))
    store.insert(make_row(session_id="s1", effect="refund", intent_hash="h1", collapsed=1))
    store.insert(make_row(session_id="s1", effect="email", intent_hash="h2"))
    store.insert(make_row(session_id="s2", effect="refund", intent_hash="h1"))
    data = build_report(store)
    assert data["tasks"] == 2
    assert data["effects"] == 3


def test_second_completed_execution_is_a_duplicate_write(store):
    store.insert(make_row(intent_hash="h1", state=DONE))
    store.insert(make_row(intent_hash="h1", state=DONE, attempt=2))
    assert build_report(store)["duplicate_writes"] == 1


def test_callee_deduplicated_replay_is_not_a_duplicate_write(store):
    store.insert(make_row(intent_hash="h1", state=DONE, attempt=1))
    store.insert(make_row(intent_hash="h1", state=DONE, attempt=2, callee_deduped=1))
    data = build_report(store)
    assert data["duplicate_writes"] == 0
    assert data["callee_deduped"] == 1
    assert data["retries_after_failure"] == 1


def test_collapsed_rows_are_not_duplicate_writes(store):
    store.insert(make_row(intent_hash="h1", state=DONE))
    store.insert(make_row(intent_hash="h1", state=DONE, collapsed=1, duplicate_of="eff_x"))
    data = build_report(store)
    assert data["duplicate_writes"] == 0
    assert data["collapsed_duplicates"] == 1


def test_blocked_and_unrolled_counters(store):
    store.insert(make_row(session_id="s1", effect="email", intent_hash="h1", state=BLOCKED))
    store.insert(make_row(session_id="s1", effect="refund", intent_hash="h2", state=UNROLLED))
    store.insert(
        make_row(session_id="s1", effect="refund", intent_hash="h3", state=UNROLLED_FAILED)
    )
    data = build_report(store)
    assert data["blocked_attempts"] == 1
    assert data["unrolled_sessions"] == 1
    assert data["unrolled_failed"] == 1


def test_orphaned_write_is_a_done_effect_a_rollback_could_not_undo(store):
    store.insert(make_row(session_id="s1", intent_hash="h1", state=DONE, undoable=0))
    store.insert(make_row(session_id="s1", intent_hash="h2", state=UNROLLED, undoable=1))
    store.insert(make_row(session_id="s2", intent_hash="h3", state=DONE, undoable=0))
    data = build_report(store)
    assert data["orphaned_writes"] == 1


def test_latency_percentiles(store):
    for latency in (1.0, 2.0, 3.0, 4.0, 100.0):
        store.insert(make_row(intent_hash=f"h{latency}", latency_ms=latency))
    data = build_report(store)
    assert data["p50_added_latency_ms"] == 3.0
    assert data["p95_added_latency_ms"] > 4.0


def test_percentile_interpolates():
    assert percentile([1.0, 2.0], 0.5) == 1.5
    assert percentile([5.0], 0.95) == 5.0
    assert percentile([], 0.5) == 0.0


def test_dry_run_rows_are_excluded_from_metrics(store):
    store.insert(make_row(intent_hash="h1", dry_run=1))
    data = build_report(store)
    assert data["effects"] == 0
    assert data["dry_run_effects"] == 1
