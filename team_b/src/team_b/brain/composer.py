"""The reply writer: every sentence the agent says, in the customer's style, from data/locales/<locale>.json.

The files are flat {key: text} with {placeholders}. PLACEHOLDERS is the contract between code and templates: for each
key that takes values it names them, and a test checks every locale against it (so an Arabizi template cannot
forget a number, and code cannot pass a value no template uses).

Naming: ask_<slot>, confirm_action_<capability> (confirm_action_default as the fallback), status_<order status>,
handoff_<escalation reason> (handoff_generic as the fallback).
"""

import json
import re
import string
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path

from team_b.config import PROJECT_ROOT
from team_b.contracts.evidence import Passage
from team_b.contracts.policy import PolicyDecision
from team_b.domain.understanding import Locale

LOCALES_DIR = PROJECT_ROOT / "data" / "locales"

PLACEHOLDERS: Mapping[str, frozenset[str]] = {
    "ask_generic": frozenset({"slot"}),
    "disambiguate_intent": frozenset({"a", "b"}),
    "disambiguate_order": frozenset({"orders"}),
    "order_status_in_transit": frozenset({"order_id", "status", "date"}),
    "order_status_delivered": frozenset({"order_id", "date"}),
    "order_status_other": frozenset({"order_id", "status"}),
    "late_policy_note": frozenset({"days"}),
    "confirm_action_create_return": frozenset({"order_id"}),
    "confirm_action_create_exchange": frozenset({"order_id"}),
    "confirm_action_create_refund": frozenset({"order_id", "amount"}),
    "confirm_action_cancel_order": frozenset({"order_id"}),
    "confirm_action_update_delivery_address": frozenset({"order_id", "address"}),
    "confirm_action_apply_voucher": frozenset({"order_id", "amount"}),
    "confirm_action_create_ticket": frozenset({"order_id"}),
    "confirm_action_default": frozenset({"action"}),
    "action_done": frozenset({"reference"}),
}


def placeholders_in(template: str) -> frozenset[str]:
    """The {names} a template expects."""
    return frozenset(name for _, name, _, _ in string.Formatter().parse(template) if name)


class ResponseComposer:
    def __init__(self, texts: Mapping[Locale, Mapping[str, str]]) -> None:
        missing = [locale.value for locale in Locale if locale not in texts]
        if missing:
            raise ValueError(f"no texts for locale(s): {', '.join(missing)}")
        self._texts = texts

    @classmethod
    def from_dir(cls, directory: Path = LOCALES_DIR) -> "ResponseComposer":
        return cls({locale: json.loads((directory / f"{locale.value}.json").read_text("utf-8")) for locale in Locale})

    def keys(self, locale: Locale) -> frozenset[str]:
        return frozenset(self._texts[locale])

    def has(self, locale: Locale, key: str) -> bool:
        return key in self._texts[locale]

    def t(self, locale: Locale, key: str, **values: object) -> str:
        """The text for `key` in `locale` with its placeholders filled. An unknown key or a missing value is a bug."""
        try:
            template = self._texts[locale][key]
        except KeyError:
            raise KeyError(f"no template {key!r} for locale {locale.value!r}") from None
        needed = placeholders_in(template)
        absent = needed - set(values)
        if absent:
            raise KeyError(f"template {key!r} needs {', '.join(sorted(absent))}")
        return template.format(**{name: values[name] for name in needed})

    def t_first(self, locale: Locale, keys: Sequence[str], **values: object) -> str:
        """The first key that exists, e.g. confirm_action_create_refund, else confirm_action_default."""
        for key in keys:
            if self.has(locale, key):
                return self.t(locale, key, **values)
        raise KeyError(f"no template among {list(keys)} for locale {locale.value!r}")

    def passage_block(self, passages: Sequence[Passage]) -> str:
        """Policy passages quoted exactly as written, each with its citation. Never reworded."""
        return "\n".join(f"“{p.text}” [{p.citation}]" for p in passages)

    def policy_message(self, locale: Locale, decision: PolicyDecision) -> str:
        """What to tell the customer about a rule checker answer: its own message (Arabizi customers get the Arabic
        text), or the generic policy_denied sentence when it sent none."""
        message = decision.user_message
        if message is not None:
            return (message.en if locale is Locale.EN else message.ar).strip() or self.t(locale, "policy_denied")
        return self.t(locale, "policy_denied")


@lru_cache(maxsize=1)
def default_composer() -> ResponseComposer:
    return ResponseComposer.from_dir()


def render(key: str, locale: Locale, **values: object) -> str:
    """Shortcut: the default composer's t(), with the key first (the way stages name what they want to say)."""
    return default_composer().t(locale, key, **values)


_KEY_IN_CODE = re.compile(r"""reply_key\s*=\s*["']([a-z_]+)["']""")


def keys_used_in(source: str) -> set[str]:
    """Literal template keys named in a piece of Python source (reply_key="..."). Used by the template tests."""
    return set(_KEY_IN_CODE.findall(source))
