"""Stand-in for the Team A safety screen: topics that must always go to a human.

Keyword and phrase matching over fixtures/<tenant>/risk.json, per category and per style (en, ar, arabizi).
Text and terms are normalized (spelling variants, diacritics, digits) and matched as whole words or phrases; an
Arabic term also matches with the attached article or small words (al-, wa-, bi-, li-, lil-). A second pass
collapses stretched letters ("scaaam") so a customer cannot slip past by lengthening a word.
flagged is true when any matched category is mandatory. No AI is involved.
If the risk file cannot be read the screen raises UpstreamError (the brain then blocks actions): never "clear".
"""

import json
import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from team_b.brain.text import normalize
from team_b.contracts.errors import UpstreamError
from team_b.contracts.evidence import RiskAssessment

SERVICE = "safety_screen"
STYLES = ("en", "ar", "arabizi")
_STRETCH = re.compile(r"(.)\1{2,}")


# Arabic attaches the article and small words: "المحامي" (the lawyer), "للمحكمه" (to the court).
_ARABIC_PREFIX = "(?:[وف]?[بكل]?ال|[وف]?لل|[وفبكسل])?"


@lru_cache(maxsize=4096)
def _term_regex(term: str) -> re.Pattern[str]:
    prefix = _ARABIC_PREFIX if "ء" <= term[0] <= "ي" else ""
    return re.compile(rf"(?<!\w){prefix}{re.escape(term)}(?!\w)")


def _found(text: str, term: str) -> bool:
    return _term_regex(term).search(text) is not None


def _collapse(text: str) -> str:
    return _STRETCH.sub(r"\1", text)


class _Category:
    def __init__(self, name: str, spec: dict[str, Any]) -> None:
        self.name = name
        self.mandatory = spec.get("mandatory_escalation", True) is not False
        # (original term, normalized term, normalized term with stretched letters collapsed)
        self.terms = [
            (term, normalize(term), _collapse(normalize(term)))
            for style in STYLES
            for term in spec.get(style, [])
            if normalize(term)
        ]


class SafetyScreenStandin:
    def __init__(self, fixtures_dir: Path) -> None:
        self._dir = fixtures_dir
        self._tenants: dict[str, list[_Category]] = {}
        self._fail = 0

    # ---- the safety screen operation (part of the EvidenceProvider plug) ----

    async def classify_risk(
        self, tenant_id: str, message: str, *, request_id: str, conversation_id: str | None = None
    ) -> RiskAssessment:
        self._maybe_fail()
        text = normalize(message)
        collapsed = _collapse(text)
        categories: list[str] = []
        matched: list[str] = []
        mandatory = False
        for category in self._categories(tenant_id):
            hits = [term for term, norm, short in category.terms if _found(text, norm) or _found(collapsed, short)]
            if hits:
                categories.append(category.name)
                matched.extend(hits)
                mandatory = mandatory or category.mandatory
        return RiskAssessment(
            flagged=mandatory, categories=tuple(categories), matched_terms=tuple(dict.fromkeys(matched)),
            method="keywords",
        )  # fmt: skip

    # ---- failure switch ----

    def fail_next(self, times: int = 1) -> None:
        """The next `times` calls raise UpstreamError(retryable=True)."""
        if times < 1:
            raise ValueError("times must be at least 1")
        self._fail += times

    def reset(self) -> None:
        self._fail = 0

    def inject(self, spec: Mapping[str, Any]) -> None:
        """Apply a switch from a scenario file, e.g. {"switch": "fail_next", "times": 1}."""
        switch = spec.get("switch")
        if spec.get("operation", "classify_risk") != "classify_risk":
            raise ValueError("safety_screen has only the operation classify_risk")
        if switch == "fail_next":
            self.fail_next(int(spec.get("times", 1)))
        elif switch == "reset":
            self.reset()
        else:
            raise ValueError(f"unknown safety_screen switch: {switch!r}")

    def _maybe_fail(self) -> None:
        if self._fail > 0:
            self._fail -= 1
            raise UpstreamError(SERVICE, "BACKEND_UNAVAILABLE", "injected failure on classify_risk", retryable=True)

    # ---- data ----

    def _categories(self, tenant_id: str) -> list[_Category]:
        if tenant_id not in self._tenants:
            path = self._dir / tenant_id / "risk.json"
            if not path.is_file():
                raise UpstreamError(SERVICE, "TENANT_NOT_FOUND", f"no risk topics for tenant {tenant_id}")
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))["categories"]
                self._tenants[tenant_id] = [_Category(name, spec) for name, spec in raw.items()]
            except (OSError, ValueError, KeyError, AttributeError, TypeError) as exc:
                raise UpstreamError(SERVICE, "RISK_INVALID", f"risk topics of {tenant_id} cannot be read") from exc
        return self._tenants[tenant_id]
