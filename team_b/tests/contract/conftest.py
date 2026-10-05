"""Strict models for the Nile Style fixtures. Unknown fields are errors, so a typo in a fixture fails the test."""

import json
from datetime import date
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field

from team_b.contracts.policy import LocalizedText
from team_b.contracts.tools import ToolSpec
from team_b.domain.tenant import TenantConfig, TenantRegistry

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "shop_001"
TENANTS = ROOT / "config" / "tenants"
AS_OF = date(2026, 9, 28)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Document(Strict):
    document_id: str
    version: str
    effective_date: date
    current: bool
    language: Literal["ar", "en", "mixed"]


class PolicyPassage(Strict):
    passage_id: str = Field(pattern=r"^[a-z_]+@v\d+#(s\d+|q\d+)$")
    document_id: str
    version: str
    section: str = Field(min_length=1)
    language: Literal["ar", "en", "mixed"]
    text: str = Field(min_length=20)
    keywords: list[str] = Field(min_length=3)
    superseded: bool
    effective_date: date


class Condition(Strict):
    field: str
    op: Literal["<=", "<", ">=", ">", "==", "!=", "in", "not_in"]
    value: Any
    from_: Literal["facts", "arguments"] = Field(alias="from")


class Rule(Strict):
    rule_id: str = Field(pattern=r"^R-[A-Z0-9-]+$")
    action: str
    scope: str
    status: Literal["approved", "proposed"]
    applies_if: list[Condition]
    conditions: list[Condition]
    effect: Literal["allow", "deny", "require_human"]
    else_effect: Literal["allow", "deny", "require_human"]
    citation: str
    quote: str = Field(min_length=10)
    user_message: LocalizedText
    effective_date: date


class RiskCategory(Strict):
    mandatory_escalation: bool
    en: list[str] = Field(min_length=1)
    ar: list[str] = Field(min_length=1)
    arabizi: list[str] = Field(min_length=1)


class SynonymEntry(Strict):
    variants: list[str] = Field(min_length=1)
    means: list[str] = Field(min_length=1)
    origin: Literal["team_a", "team_b"]


class Customer(Strict):
    customer_id: str = Field(pattern=r"^C-\d{3}$")
    name: str
    phone: str = Field(pattern=r"^01[0125]\d{8}$")  # Egyptian mobile: 010 / 011 / 012 / 015
    preferred_language: Literal["en", "ar", "arabizi"]


class Order(Strict):
    order_id: str = Field(pattern=r"^NS-\d{4,6}$")
    customer_id: str
    order_status: Literal["pending", "processing", "shipped", "delivered", "returned", "cancelled"]
    payment_status: Literal["paid", "cod_pending", "refunded"]
    payment_method: Literal["card", "wallet", "instapay", "cod"]
    placed_at: date
    shipped_at: date | None
    delivered_at: date | None
    expected_delivery_date: date
    order_total: float = Field(gt=0)
    currency: Literal["EGP"]
    item_name: str
    size: str | None
    product_category: str
    is_clearance: bool
    item_condition: Literal["unused", "used", "damaged_on_arrival", "defective"]
    delivery_address: dict[str, str]
    tracking_number: str | None
    cancelled_at: date | None = None
    returned_at: date | None = None


class Ticket(Strict):
    ticket_id: str = Field(pattern=r"^T-\d{4}$")
    category: str
    customer_message: str = Field(min_length=10)
    resolution: str = Field(min_length=10)
    created_at: date


def read(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


@pytest.fixture(scope="session")
def policies() -> dict[str, Any]:
    return read("policies.json")


@pytest.fixture(scope="session")
def tenant() -> TenantConfig:
    return TenantRegistry.from_dir(TENANTS).get("shop_001")


@pytest.fixture(scope="session")
def tools() -> list[ToolSpec]:
    return [ToolSpec.model_validate(t) for t in read("tools.json")["tools"]]
