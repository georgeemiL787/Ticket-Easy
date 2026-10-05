"""Which details a request still needs, and where each tool argument comes from.

A tenant says, per intent, how each tool argument is filled (argument_map): slot:<name> is something the customer says,
fact:<name> is a real value read from the shop (an order total), identity:customer_id is who the verified customer is,
const:<value> is fixed. This module is the one place that turns that into (a) the list of details to ask for and
(b) the arguments of the tool call.

Facts beat the customer: an argument mapped to fact:<name> is never filled from what the customer said, so "refund
5000" on a 1250 order still refunds 1250. A required argument that has no source at all can never be filled, so the
request is unsupported (the caller hands it to a person).
"""

from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from team_b.contracts.tools import ToolSpec
from team_b.domain.session import SessionState
from team_b.domain.tenant import IntentSpec

QUESTION_PRIORITY = ("order_id", "phone", "item", "reason", "amount")
DEFAULT_IDENTITY_SLOTS = ("order_id", "phone")
SOURCE_KINDS = ("slot", "fact", "identity", "const")


class Resolution(NamedTuple):
    arguments: dict[str, Any]  # what could be filled, typed as the tool's input schema asks
    missing: tuple[str, ...]  # details to ask the customer for, in the order to ask them
    missing_facts: tuple[str, ...]  # shop facts that must be read first (the customer cannot supply them)
    unsourced: tuple[str, ...]  # required arguments nobody can ever fill: the request is unsupported
    needs_identity: bool  # an argument needs the verified customer and there is none yet


def parse_source(source: str) -> tuple[str, str]:
    """("slot", "order_id") for "slot:order_id". Any other form is a configuration error."""
    kind, _, name = source.partition(":")
    if kind not in SOURCE_KINDS or not name:
        raise ValueError(
            f"argument source {source!r} must be slot:<name>, fact:<name>, identity:<name> or const:<value>"
        )
    return kind, name


def _required_arguments(tool: ToolSpec | None) -> tuple[str, ...]:
    return tuple(tool.input_schema.get("required", ())) if tool is not None else ()


def argument_sources(intent: IntentSpec, tool: ToolSpec | None) -> dict[str, str]:
    """argument name -> source, for every mapped argument and every required one that can be filled from a slot.

    A required argument that is not in argument_map is filled from the slot of the same name when the intent lists
    that slot as required (so a lookup like get_order needs no map). Otherwise it has no source and is left out."""
    sources = dict(intent.argument_map)
    for argument in _required_arguments(tool):
        if argument not in sources and argument in intent.required_slots:
            sources[argument] = f"slot:{argument}"
    return sources


def required_slots(
    intent: IntentSpec,
    tool: ToolSpec | None,
    needs_identity: bool,
    identity_slots: Sequence[str] = DEFAULT_IDENTITY_SLOTS,
) -> list[str]:
    """The details this request needs from the customer, each once: the intent's own, those the tool's required
    arguments are taken from, and (when the customer is not verified yet) the identity slots."""
    sources = argument_sources(intent, tool)
    slots = list(intent.required_slots)
    for argument in _required_arguments(tool):
        source = sources.get(argument)
        if source is not None and (parsed := parse_source(source))[0] == "slot":
            slots.append(parsed[1])
    if needs_identity:
        slots.extend(identity_slots)
    return list(dict.fromkeys(slots))


def next_question(missing: Sequence[str]) -> str | None:
    """Which missing detail to ask for first: order id, phone, item, reason, amount, then the rest as given."""
    ordered = order_questions(missing)
    return ordered[0] if ordered else None


def order_questions(missing: Sequence[str]) -> tuple[str, ...]:
    """The missing details in the order they should be asked."""
    rank = {name: i for i, name in enumerate(QUESTION_PRIORITY)}
    return tuple(sorted(missing, key=lambda name: (rank.get(name, len(rank)), list(missing).index(name))))


_NOT_A_VALUE = object()


def _coerce(value: Any, schema: Mapping[str, Any]) -> Any:
    """The value typed as the schema says, or _NOT_A_VALUE when it cannot be (a customer wrote "abc" for an amount)."""
    kinds = schema.get("type", "string")
    kind = next((k for k in (kinds if isinstance(kinds, list) else [kinds]) if k != "null"), "string")
    text = str(value).strip()
    try:
        if kind == "integer":
            number = float(text.replace(",", ""))
            return int(number) if number == int(number) else _NOT_A_VALUE
        if kind == "number":
            number = float(text.replace(",", ""))
            return int(number) if number == int(number) else number
        if kind == "boolean":
            return {"true": True, "yes": True, "false": False, "no": False}.get(text.lower(), _NOT_A_VALUE)
    except ValueError:
        return _NOT_A_VALUE
    return str(value)


def resolve_arguments(
    intent: IntentSpec, tool: ToolSpec | None, session: SessionState, facts: Mapping[str, Any]
) -> Resolution:
    """Fill the tool's arguments from slots, facts, the verified identity and constants, and say what is missing."""
    sources = argument_sources(intent, tool)
    properties: Mapping[str, Any] = tool.input_schema.get("properties", {}) if tool is not None else {}
    required = set(_required_arguments(tool))
    arguments: dict[str, Any] = {}
    missing: list[str] = []
    missing_facts: list[str] = []
    needs_identity = False

    for argument, source in sources.items():
        if properties and argument not in properties:
            continue  # the tool does not take it
        kind, name = parse_source(source)
        value: Any = None
        if kind == "slot":
            value = session.slots.get(name)
        elif kind == "fact":
            value = facts.get(name)  # the customer's own words are never used for a fact
            if value is None:
                missing_facts.append(name)
        elif kind == "identity":
            if name == "customer_id" and session.identity.verified:
                value = session.identity.customer_id
            else:
                needs_identity = True
        else:
            value = name
        if value is None or (isinstance(value, str) and not value.strip()):
            if kind == "slot" and (argument in required or name in intent.required_slots):
                missing.append(name)
            continue
        typed = _coerce(value, properties.get(argument, {})) if properties else value
        if typed is _NOT_A_VALUE:
            if kind == "slot":
                missing.append(name)  # ask again: the answer was not usable
            continue
        arguments[argument] = typed

    unsourced = tuple(a for a in _required_arguments(tool) if a not in sources)
    return Resolution(
        arguments=arguments,
        missing=order_questions(list(dict.fromkeys(missing))),
        missing_facts=tuple(dict.fromkeys(missing_facts)),
        unsourced=unsourced,
        needs_identity=needs_identity,
    )
