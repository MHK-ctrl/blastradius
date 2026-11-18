from __future__ import annotations

from index.stats import pass_hat_k, per_1000, percentile, wilson


def test_wilson_zero_failures_has_a_nonzero_upper_bound():
    low, high = wilson(0, 225)
    assert low == 0.0
    assert 0.015 < high < 0.018


def test_wilson_all_failures_has_a_nonone_lower_bound():
    low, high = wilson(225, 225)
    assert high == 1.0
    assert 0.982 < low < 0.985


def test_wilson_is_symmetric_around_a_half():
    low, high = wilson(50, 100)
    assert abs((1 - high) - low) < 1e-12


def test_wilson_empty_sample():
    assert wilson(0, 0) == (0.0, 0.0)


def test_per_1000_scales_the_interval():
    rate, low, high = per_1000(0, 225)
    assert rate == 0.0
    assert low == 0.0
    assert 16.0 < high < 17.5


def test_pass_hat_k_all_passed_is_one():
    outcomes = {"a": [True, True, True], "b": [True, True, True]}
    assert pass_hat_k(outcomes, 3) == 1.0


def test_pass_hat_k_requires_every_trial():
    assert pass_hat_k({"a": [True, True, False, False, False]}, 5) == 0.0


def test_pass_hat_k_uses_the_unbiased_estimate():
    # C(3,2)/C(4,2) = 3/6
    assert pass_hat_k({"a": [True, True, True, False]}, 2) == 0.5


def test_pass_hat_k_skips_scenarios_with_too_few_trials():
    assert pass_hat_k({"a": [True, True]}, 5) == 0.0


def test_percentile_edges():
    assert percentile([1.0, 2.0, 3.0], 0.0) == 1.0
    assert percentile([1.0, 2.0, 3.0], 1.0) == 3.0
