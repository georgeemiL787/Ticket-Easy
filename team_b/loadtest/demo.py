"""The conversations a virtual customer plays in the load test. Shared by the Locust file and the in-process runner.

Each conversation is a list of messages; the customer is a real demo customer with a real order, so the shop's answers
are realistic: lookups, policy questions, confirmed returns / exchanges / address changes, refunds that need a person.
Random choices come from a Random the caller seeds, so a run can be repeated.
"""

import json
import random
from dataclasses import dataclass
from pathlib import Path

BACKEND = json.loads((Path(__file__).parents[1] / "fixtures" / "shop_001" / "backend.json").read_text(encoding="utf-8"))
TENANT = "shop_001"
CUSTOMERS = {c["customer_id"]: c for c in BACKEND["customers"]}
ORDERS_OF: dict[str, list[dict]] = {}  # type: ignore[type-arg]
for _order in BACKEND["orders"]:
    ORDERS_OF.setdefault(_order["customer_id"], []).append(_order)

QUESTIONS = [
    "What is your return policy?",
    "How many days do I have to return an item?",
    "can I pay cash on delivery?",
    "what are your working hours?",
    "what is the refund policy?",
    "emta a2dar araga3 el montag?",
    "ممكن ارجع المنتج بعد كام يوم من الاستلام؟",
]


@dataclass(frozen=True)
class Conversation:
    name: str
    messages: tuple[str, ...]


def pick(rng: random.Random) -> Conversation:
    """One conversation for a random customer, drawn from the demo mix."""
    customer = rng.choice(sorted(CUSTOMERS))
    phone = CUSTOMERS[customer]["phone"]
    order = rng.choice(ORDERS_OF[customer])["order_id"]
    kind = rng.choices(
        [
            "status",
            "question",
            "arabizi_status",
            "return",
            "exchange",
            "address",
            "large_refund",
            "voucher",
            "greeting",
        ],
        weights=[22, 22, 8, 12, 6, 8, 4, 6, 12],
    )[0]
    if kind == "status":
        return Conversation(kind, (f"Where is my order {order}? My phone is {phone}", "thanks"))
    if kind == "arabizi_status":
        return Conversation(kind, (f"feen el order {order}, ra2mi {phone}",))
    if kind == "question":
        return Conversation(kind, (rng.choice(QUESTIONS), rng.choice(QUESTIONS)))
    if kind == "return":
        return Conversation(kind, (f"I want to return order {order}, the size is wrong", phone, "yes"))
    if kind == "exchange":
        return Conversation(
            kind, (f"I want to exchange order {order} for a bigger size, it is too small", phone, "yes")
        )
    if kind == "address":
        return Conversation(
            kind, (f"I want to change the delivery address of order {order} to 5 Nile Corniche, Maadi", phone, "yes")
        )
    if kind == "large_refund":
        return Conversation(kind, ("I want a refund for order NS-20934", "01098765405"))
    if kind == "voucher":
        return Conversation(kind, (f"My order {order} is late, I want a voucher of 50 EGP", phone, "yes"))
    return Conversation(kind, ("hello", "ahlan, ezayak"))
