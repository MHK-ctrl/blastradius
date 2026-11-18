"""Deterministic fault selection: same scenario and seed, same fault."""

from __future__ import annotations

import random
from typing import Any

ENDPOINTS = {"refund": "/refund", "email": "/email", "records": "/records"}


def endpoint_for(tool: str) -> str:
    try:
        return ENDPOINTS[tool]
    except KeyError as exc:
        raise KeyError(f"unknown tool {tool!r}; expected one of {sorted(ENDPOINTS)}") from exc


def fault_plan(scenario: dict[str, Any], seed: int) -> dict[str, list[str]]:
    """Faults to arm for one trial.

    An explicit ``faults`` map wins. Otherwise one fault is drawn, reproducibly,
    from the scenario's ``fault_pool`` using (scenario id, seed).
    """
    explicit = scenario.get("faults") or {}
    if explicit:
        return {ENDPOINTS[name]: list(faults) for name, faults in explicit.items()}

    pool = scenario.get("fault_pool") or []
    plan = scenario.get("plan") or []
    if not pool or not plan:
        return {}
    rng = random.Random(f"{scenario['id']}:{seed}")
    return {endpoint_for(plan[0]["tool"]): [rng.choice(pool)]}
