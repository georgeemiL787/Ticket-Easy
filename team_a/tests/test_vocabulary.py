"""The shared rule vocabulary covers every tenant's facts and only ever grows."""

import json

from team_a.config import settings

VOCAB = json.loads((settings.data_dir / "rules" / "vocabulary.json").read_text(encoding="utf-8"))

# The vocabulary before noon_eg was added: nothing in it may be removed or narrowed.
BEFORE_NOON = {
    "order_status": ["pending", "processing", "shipped", "delivered", "returned", "cancelled"],
    "product_category": ["clothing", "shoes", "accessories", "underwear", "swimwear", "perfume", "cosmetics", "gift_card"],
    "item_condition": ["unused", "used", "damaged_on_arrival", "defective"],
}
BEFORE_FACTS = {"order_status", "delivered_at", "expected_delivery_date", "days_since_delivery", "days_late",
                "order_total", "product_category", "is_clearance", "item_condition", "hours_since_delivery"}


def test_vocabulary_only_grew():
    assert BEFORE_FACTS <= set(VOCAB["facts"])
    for fact, values in BEFORE_NOON.items():
        assert set(values) <= set(VOCAB["facts"][fact]["values"]), fact
    assert set(VOCAB["actions"]) >= {"create_return", "create_refund", "cancel_order", "apply_voucher"}


def test_every_noon_scenario_fact_and_value_is_in_the_vocabulary():
    scenarios = json.loads((settings.mock_dir("noon_eg") / "noon_scenarios.json").read_text(encoding="utf-8"))
    for s in scenarios["scenarios"]:
        cai = s.get("check_action_input")
        if not cai:
            continue
        assert cai["tool"] in VOCAB["actions"], s["scenario_id"]
        for name, value in cai["facts"].items():
            spec = VOCAB["facts"].get(name)
            assert spec is not None, f"{s['scenario_id']}: fact {name} missing from vocabulary"
            if spec["type"] == "enum":
                assert value in spec["values"], f"{s['scenario_id']}: {name}={value!r} not in vocabulary"


def test_every_guardrail_fact_is_in_the_vocabulary():
    for line in (settings.data_dir / "benchmark" / "guardrails_shop_001.jsonl").read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        assert set(case["request"].get("facts", {})) <= set(VOCAB["facts"]), case["id"]
