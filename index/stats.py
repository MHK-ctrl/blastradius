"""Statistics that are boring on purpose: Wilson intervals and pass^k."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion."""
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    low, high = max(0.0, centre - margin), min(1.0, centre + margin)
    if successes == 0:
        low = 0.0
    if successes == n:
        high = 1.0
    return (low, high)


def per_1000(failures: int, n: int) -> tuple[float, float, float]:
    """Rate per 1,000 tasks with its 95% interval, also per 1,000."""
    if n == 0:
        return (0.0, 0.0, 0.0)
    low, high = wilson(failures, n)
    return (round(failures / n * 1000, 1), round(low * 1000, 1), round(high * 1000, 1))


def pass_hat_k(outcomes_by_scenario: Mapping[str, Iterable[bool]], k: int) -> float:
    """Unbiased pass^k: C(c, k) / C(n, k), averaged over scenarios with n >= k."""
    values = []
    for outcomes in outcomes_by_scenario.values():
        results = list(outcomes)
        n = len(results)
        c = sum(1 for outcome in results if outcome)
        if n < k:
            continue
        total = math.comb(n, k)
        values.append(math.comb(c, k) / total if total else 0.0)
    return round(sum(values) / len(values), 4) if values else 0.0


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
