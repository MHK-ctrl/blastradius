"""Turn the journal into the numbers the report and CI use."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from .storage.base import BLOCKED, DONE, FAILED, PENDING, UNROLLED, UNROLLED_FAILED

REQUIRED_KEYS = (
    "tasks",
    "effects",
    "duplicate_writes",
    "orphaned_writes",
    "blocked_attempts",
    "unrolled_sessions",
    "unrolled_failed",
    "p50_added_latency_ms",
    "p95_added_latency_ms",
)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    data = sorted(values)
    if len(data) == 1:
        return float(data[0])
    position = (len(data) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return float(data[low])
    weight = position - low
    return float(data[low] * (1 - weight) + data[high] * weight)


def build_report(store: Any) -> dict[str, Any]:
    all_rows = store.rows()
    dry_rows = [row for row in all_rows if row.dry_run]
    rows = [row for row in all_rows if not row.dry_run]

    groups: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
    for row in rows:
        groups[(row.session_id, row.effect_name, row.intent_hash)].append(row)

    duplicate_writes = 0
    for group in groups.values():
        executions = 0
        for row in group:
            if row.state in (UNROLLED, UNROLLED_FAILED):
                executions = 0
            elif row.state == DONE and not row.collapsed and not row.duplicate_of:
                if row.callee_deduped:
                    continue
                executions += 1
                if executions > 1:
                    duplicate_writes += 1

    rolled_back_sessions = {
        row.session_id
        for row in rows
        if row.state in (UNROLLED, UNROLLED_FAILED)
    }
    orphaned_writes = sum(
        1
        for row in rows
        if row.state == DONE
        and not row.collapsed
        and not row.duplicate_of
        and not row.callee_deduped
        and not row.undoable
        and row.session_id in rolled_back_sessions
    )

    latencies = [float(row.latency_ms) for row in rows]
    tasks = len({row.session_id for row in rows})
    effects = len(groups)

    return {
        "tasks": tasks,
        "effects": effects,
        "duplicate_writes": duplicate_writes,
        "orphaned_writes": orphaned_writes,
        "blocked_attempts": sum(1 for row in rows if row.state == BLOCKED),
        "unrolled_sessions": len(rolled_back_sessions),
        "unrolled_failed": sum(1 for row in rows if row.state == UNROLLED_FAILED),
        "p50_added_latency_ms": round(percentile(latencies, 0.50), 3),
        "p95_added_latency_ms": round(percentile(latencies, 0.95), 3),
        "collapsed_duplicates": sum(1 for row in rows if row.collapsed),
        "callee_deduped": sum(1 for row in rows if row.callee_deduped),
        "retries_after_failure": sum(
            1 for row in rows if row.attempt > 1 and row.state == DONE and not row.collapsed
        ),
        "pending": sum(1 for row in rows if row.state == PENDING),
        "failed": sum(1 for row in rows if row.state == FAILED),
        "dry_run_effects": len(dry_rows),
        "sessions": sorted({row.session_id for row in rows}),
    }


def format_report(data: dict[str, Any]) -> str:
    order = [
        ("tasks", "Tasks (distinct sessions)"),
        ("effects", "Effects (distinct intents)"),
        ("duplicate_writes", "Duplicate writes"),
        ("orphaned_writes", "Orphaned writes"),
        ("blocked_attempts", "Blocked attempts"),
        ("unrolled_sessions", "Unrolled sessions"),
        ("unrolled_failed", "Failed undos"),
        ("p50_added_latency_ms", "p50 guard latency (ms)"),
        ("p95_added_latency_ms", "p95 guard latency (ms)"),
        ("collapsed_duplicates", "Duplicates collapsed in-process"),
        ("callee_deduped", "Replays deduplicated by callee"),
        ("retries_after_failure", "Retries after a failed attempt"),
        ("pending", "Pending (unresolved crash rows)"),
        ("failed", "Failed effects"),
        ("dry_run_effects", "Dry-run effects (shadow ledger)"),
    ]
    width = max(len(label) for _, label in order) + 2
    lines = ["blastradius report", "-" * (width + 10)]
    for key, label in order:
        lines.append(f"{label:<{width}}{data[key]}")
    return "\n".join(lines)
