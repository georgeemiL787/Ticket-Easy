"""Duplicate generated tools are recognized by complete executable behavior, not by shared operations."""
import copy
from team_c.capabilities import behavior
from helpers.desk import desk_proposal, generated
from helpers.tool_requests import ask, proposal_count, triage


def runtime(target, ref):
    return dict(target=target, kind="runtime_argument", reference=ref, step_id=None, response_status=None)


def base():
    """AUTHORED: an order lookup feeding a status request, with one owner setting."""
    return dict(steps=[dict(id="s1", operation_id="get-order", purpose="Find the order", bindings=[runtime("path.order_id", "order")]),
                       dict(id="s2", operation_id="post-status", purpose="Ask for status", bindings=[
                           dict(target="body.order_ref", kind="previous_operation_output", reference="/order/id", step_id="s1", response_status="200"),
                           runtime("body.note", "note"),
                           dict(target="body.queue", kind="business_configuration", reference="queue", step_id=None, response_status=None)])],
                configuration=[dict(key="queue", value_json='"payments"')],
                outputs=[dict(name="status", step_id="s2", response_status="201", pointer="/status")])


def variant(change):
    c = copy.deepcopy(base())
    change(c)
    return c


def test_equivalent_tool_with_renamed_labels_and_reordered_bindings_is_the_same():
    def relabel(c):
        c["steps"][0]["bindings"][0]["reference"] = "order_number"
        c["steps"][1]["bindings"].reverse()
        c["steps"][1]["bindings"][1]["reference"] = "message"
        c["steps"][1]["bindings"][0]["reference"] = "team"
        c["configuration"] = [dict(key="team", value_json='  "payments" ')]
        c["outputs"][0]["name"] = "current_status"
    assert behavior(variant(relabel)) == behavior(base())


def test_meaningful_differences_are_preserved():
    changes = {
        "different output": lambda c: c["outputs"][0].update(pointer="/delivery"),
        "extra output": lambda c: c["outputs"].append(dict(name="all", step_id="s2", response_status="201", pointer="")),
        "different configuration value": lambda c: c["configuration"][0].update(value_json='"deliveries"'),
        "different binding target": lambda c: c["steps"][1]["bindings"][1].update(target="body.comment"),
        "different binding source": lambda c: c["steps"][1]["bindings"].__setitem__(1, runtime("body.note", "order")),
        "different previous pointer": lambda c: c["steps"][1]["bindings"][0].update(reference="/order/customer_id"),
        "different response status": lambda c: c["steps"][1]["bindings"][0].update(response_status="203"),
        "different step order": lambda c: c["steps"].reverse(),
    }
    original = behavior(base())
    for name, change in changes.items():
        assert behavior(variant(change)) != original, name


def test_order_of_outputs_step_ids_and_configuration_json_are_normalized():
    def two_outputs(c):
        c["outputs"].append(dict(name="order", step_id="s1", response_status="200", pointer="/order/id"))
    def reordered(c):
        two_outputs(c)
        c["outputs"].reverse()
        for s in c["steps"]:
            s["id"] = "renamed-" + s["id"]
        c["steps"][1]["bindings"][0]["step_id"] = "renamed-s1"
        for o in c["outputs"]:
            o["step_id"] = "renamed-" + o["step_id"]
    assert behavior(variant(reordered)) == behavior(variant(two_outputs))
    obj = lambda text: variant(lambda c: c["configuration"][0].update(value_json=text))
    assert behavior(obj('{"queue": "payments", "priority": [1, 2]}')) == behavior(obj('{ "priority":[1,2],"queue":"payments" }'))
    assert behavior(obj('{"priority": [1, 2]}')) != behavior(obj('{"priority": [2, 1]}'))
    assert behavior(obj("1")) != behavior(obj("true")) and behavior(obj('"1"')) != behavior(obj("1"))


def test_runtime_argument_sharing_kind_and_output_status_matter():
    def two_arguments(c):
        c["steps"][1]["bindings"].append(runtime("body.comment", "comment"))
    shared = lambda c: (two_arguments(c), c["steps"][1]["bindings"][-1].update(reference="note"))
    swapped = lambda c: (two_arguments(c), c["steps"][1]["bindings"][1].update(reference="comment"), c["steps"][1]["bindings"][-1].update(reference="note"))
    assert behavior(variant(shared)) != behavior(variant(two_arguments))
    assert behavior(variant(swapped)) == behavior(variant(two_arguments))
    assert behavior(variant(lambda c: c["steps"][1]["bindings"][2].update(kind="runtime_argument"))) != behavior(base())
    assert behavior(variant(lambda c: c["outputs"][0].update(response_status="200"))) != behavior(base())
    assert behavior(variant(lambda c: c["outputs"][0].update(step_id="s1"))) != behavior(base())
    assert behavior(variant(lambda c: c["steps"][1].update(operation_id="post-other"))) != behavior(base())


def test_unset_configuration_never_proves_a_duplicate():
    assert behavior(variant(lambda c: c["configuration"][0].update(value_json=None))) is None
    assert behavior(variant(lambda c: c["configuration"].clear())) is None


def test_generated_tool_on_the_same_operations_with_other_outputs_is_kept(desk):
    d = desk
    pid = generated(d)
    def more_outputs(inv, n):
        p = desk_proposal(inv, n)
        p["outputs"].append(dict(name="created_response", step_id="s2", response_status="201", pointer=""))
        return dict(proposals=[p], capability_gaps=[])
    d.provider.triage.append(triage("feasible", [d.lookup, d.create]))
    d.provider.generations.append(more_outputs)
    req = ask(d, "different-outputs-1").json()
    assert req["status"] == "proposed" and req["proposal_ids"] and pid not in req["proposal_ids"] and proposal_count(d) == 2
