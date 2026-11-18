from __future__ import annotations

import re

import pytest

from blastradius.idempotency import (
    IntentError,
    canonical_intent,
    canonical_payload,
    idempotency_key,
    intent_key,
)


def test_identical_attempts_collide():
    a = canonical_intent("refund", {"customer_id": "cust_17", "amount": 40})
    b = canonical_intent("refund", {"customer_id": "cust_17", "amount": 40})
    assert a == b


def test_key_order_does_not_matter():
    a = canonical_intent("refund", {"customer_id": "c", "amount": 40})
    b = canonical_intent("refund", {"amount": 40, "customer_id": "c"})
    assert a == b


def test_int_and_float_normalise_to_the_same_key():
    a = canonical_intent("refund", {"amount": 40})
    b = canonical_intent("refund", {"amount": 40.0})
    assert a == b
    assert canonical_payload({"amount": 40.0}) == canonical_payload({"amount": 40})


def test_different_amount_does_not_collide():
    assert canonical_intent("refund", {"amount": 40}) != canonical_intent(
        "refund", {"amount": 41}
    )


def test_different_session_does_not_collide():
    one = intent_key("s_a", "refund", {"amount": 40})
    two = intent_key("s_b", "refund", {"amount": 40})
    assert one[2] == two[2]
    assert one != two


def test_different_effect_does_not_collide():
    assert canonical_intent("refund", {"amount": 40}) != canonical_intent(
        "email", {"amount": 40}
    )


def test_nested_key_order_does_not_matter():
    a = canonical_intent("refund", {"meta": {"b": 2, "a": 1}})
    b = canonical_intent("refund", {"meta": {"a": 1, "b": 2}})
    assert a == b


def test_hash_is_64_lowercase_hex():
    digest = canonical_intent("refund", {"amount": 1})
    assert re.fullmatch(r"[0-9a-f]{64}", digest)


def test_bool_and_int_are_distinct_intents():
    assert canonical_intent("flag", {"value": True}) != canonical_intent("flag", {"value": 1})


def test_wire_key_is_stable_and_prefixed():
    digest = canonical_intent("refund", {"amount": 40})
    one = idempotency_key("s_a", "refund", digest)
    two = idempotency_key("s_a", "refund", digest)
    three = idempotency_key("s_b", "refund", digest)
    assert one == two
    assert one.startswith("br_")
    assert one != three


def test_unserialisable_argument_is_rejected_loudly():
    with pytest.raises(IntentError):
        canonical_intent("refund", {"amount": object()})
