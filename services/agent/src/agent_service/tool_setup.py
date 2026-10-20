"""From a published the tools service tool to a bundle tool: its name, the ToolSpec the core sees and the binding.

The tools service describes every tool it builds (name, input and output schemas, which steps read and which write).
This module turns that into what the agent needs, without any model: a stable tool name, a ToolSpec for the core (the
built-in shop declares no tool it does not know) and a ToolBindingRef naming the the tools service proposal. Credentials
never
pass through here: bundles hold ids only.
"""

import re
from typing import Any

from agent_service.domain.bundle import BundleTool, ToolBindingRef
from agent_service.domain.tenant import TenantConfig

TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{2,62}$")


def slug(text: str) -> str:
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    name = re.sub(r"[^a-z0-9]+", "_", words.lower()).strip("_")
    if not name or not name[0].isalpha():
        name = "tool_" + name
    return name[:62].rstrip("_") or "tool"


def unique_name(wanted: str, taken: set[str]) -> str:
    name = slug(wanted)
    candidate, number = name, 2
    while candidate in taken:
        candidate, number = f"{name[:58]}_{number}", number + 1
    return candidate


def plain_schema(node: Any) -> Any:
    """A the tools service schema (OpenAPI flavour) as plain JSON Schema: `nullable` becomes a type list, titles are
    dropped."""
    if isinstance(node, list):
        return [plain_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {
        k: plain_schema(v)
        for k, v in node.items()
        if k not in ("title", "nullable", "readOnly", "writeOnly", "xml", "example")
    }
    if node.get("nullable") and isinstance(out.get("type"), str):
        out["type"] = [out["type"], "null"]
    return out


def operation_kind(item_class: str, methods: list[str]) -> str:
    if item_class == "read":
        return "read"
    if "DELETE" in methods:
        return "delete"
    return "create" if "POST" in methods else "update"


def reference_field(output_names: list[str]) -> str | None:
    """The output that names the new or changed record, when the tool returns one."""
    for name in output_names:
        if re.search(r"(^|_)(id|ref|reference)$", name):
            return name
    return None


def tool_for(
    *, name: str, item: dict[str, Any], artifact: dict[str, Any], business_id: str
) -> tuple[BundleTool, dict[str, Any]]:
    """The bundle entry and its ToolSpec for one published tool (`item`: the batch item, `artifact`: the tools
    service's)."""
    content = artifact.get("content") or {}
    methods = [str(s.get("method", "")).upper() for s in content.get("steps") or []]
    kind = operation_kind(str(item.get("class", "read")), methods)
    output_schema = plain_schema(content.get("output_schema") or {"type": "object", "properties": {}})
    outputs = list((output_schema.get("properties") or {}).keys()) if isinstance(output_schema, dict) else []
    reference = reference_field(outputs) if kind != "read" else None
    if kind == "create" and reference is None:
        kind = "update"  # a create must return the new record's reference; without one it is checked as a change
    risk = {"read": "low", "write": "medium", "risky": "high"}.get(str(item.get("class")), "high")
    summary = item.get("summary") or {}
    spec = {
        "name": name,
        "capability": name,
        "title": str(summary.get("name") or content.get("name") or name)[:120],
        "description": str(summary.get("purpose") or content.get("purpose") or content.get("description") or "")[:600],
        "operation_kind": kind,
        "risk": risk,
        "requires_identity": True,
        "exposes_personal_data": True,
        "human_only": False,
        "enabled": True,
        "input_schema": plain_schema(content.get("input_schema") or {"type": "object", "properties": {}}),
        "output_schema": output_schema,
    }
    binding = ToolBindingRef(
        business_id=business_id,
        proposal_id=str(artifact.get("proposal_id") or item.get("proposal_id")),
        environments=("sandbox",),
        fields={n: n for n in outputs},
        reference_field=reference,
        spec=spec,
    )
    permission = "read" if kind == "read" else "confirm"
    return BundleTool(name=name, source="tools", binding=binding, permission=permission, risk=risk), spec  # type: ignore[arg-type]


def allow(config: TenantConfig, names: list[str]) -> TenantConfig:
    """The configuration with these tools allowed (a business that allows every tool is left as it is)."""
    allowed = config.permissions.allowed_tools
    if "*" in allowed:
        return config
    merged = tuple(dict.fromkeys([*allowed, *names]))
    return config.model_copy(update={"permissions": config.permissions.model_copy(update={"allowed_tools": merged})})
