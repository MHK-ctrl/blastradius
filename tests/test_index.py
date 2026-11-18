from __future__ import annotations

from pathlib import Path

import pytest

from index.faults import endpoint_for, fault_plan
from index.runner import load_scenarios, matches

SCENARIOS = Path(__file__).resolve().parents[1] / "index" / "scenarios"


@pytest.fixture(scope="module")
def scenarios():
    return load_scenarios(SCENARIOS)


def test_at_least_forty_scenarios_are_present(scenarios):
    assert len(scenarios) >= 40


def test_scenario_ids_are_unique(scenarios):
    ids = [scenario["id"] for scenario in scenarios]
    assert len(ids) == len(set(ids))


def test_all_three_effect_types_are_covered(scenarios):
    assert {scenario["effect_type"] for scenario in scenarios} == {
        "money",
        "communication",
        "records",
    }


def test_every_scenario_declares_an_expected_ledger(scenarios):
    for scenario in scenarios:
        assert "expected" in scenario
        assert isinstance(scenario["expected"], list)


def test_impossible_and_do_nothing_tasks_exist(scenarios):
    empty = [scenario for scenario in scenarios if scenario["expected"] == []]
    assert len(empty) >= 6
    doing_nothing = [scenario for scenario in scenarios if not scenario.get("plan")]
    assert len(doing_nothing) >= 3


def test_matches_accepts_an_exact_ledger():
    ledger = [{"kind": "refund", "customer_id": "c", "amount": 40}]
    expected = [{"kind": "refund", "customer_id": "c", "amount": 40}]
    assert matches(ledger, expected)


def test_matches_rejects_a_duplicate_row():
    ledger = [
        {"kind": "refund", "customer_id": "c", "amount": 40},
        {"kind": "refund", "customer_id": "c", "amount": 40},
    ]
    assert not matches(ledger, [{"kind": "refund", "customer_id": "c", "amount": 40}])


def test_matches_rejects_a_missing_row():
    assert not matches([], [{"kind": "email", "to": "a@b"}])


def test_matches_rejects_a_wrong_value():
    ledger = [{"kind": "refund", "amount": 41}]
    assert not matches(ledger, [{"kind": "refund", "amount": 40}])


def test_matches_is_order_independent():
    ledger = [{"kind": "a"}, {"kind": "b"}]
    assert matches(ledger, [{"kind": "b"}, {"kind": "a"}])


def test_fault_plan_is_deterministic_per_scenario_and_seed():
    scenario = {
        "id": "x",
        "fault_pool": ["timeout_once", "rate_limit_once"],
        "plan": [{"tool": "refund", "args": {}}],
    }
    assert fault_plan(scenario, 11) == fault_plan(scenario, 11)


def test_fault_plan_varies_across_seeds():
    scenario = {
        "id": "x",
        "fault_pool": ["timeout_once", "rate_limit_once", "truncated_body"],
        "plan": [{"tool": "refund", "args": {}}],
    }
    seen = {tuple(fault_plan(scenario, seed)["/refund"]) for seed in range(12)}
    assert len(seen) > 1


def test_explicit_faults_win_over_the_pool():
    scenario = {
        "id": "x",
        "faults": {"email": ["stale_read"]},
        "plan": [{"tool": "email", "args": {}}],
    }
    assert fault_plan(scenario, 1) == {"/email": ["stale_read"]}


def test_empty_plan_means_no_faults():
    assert fault_plan({"id": "x", "fault_pool": ["timeout_once"], "plan": []}, 1) == {}


def test_unknown_tool_is_rejected():
    with pytest.raises(KeyError):
        endpoint_for("wire_money")
