"""Stand-in for the Team A safety screen: topics that must always go to a human.

Keyword and phrase matching over fixtures/<tenant>/risk.json, per category and per style (en, ar, arabizi).
Text and terms are normalized (spelling variants, diacritics, digits) and matched as whole words or phrases; an
Arabic term also matches with the attached article or small words (al-, wa-, bi-, li-, lil-). A second pass
collapses stretched letters ("scaaam"), joins letters spread out with spaces or dots ("s c a m") and reads
look-alikes ("fr4ud"), so a customer cannot slip past by disguising a word.
A category may list exempt phrases (the shop's own "compensation voucher"): a keyword inside one is ignored.
flagged is true when any matched category is mandatory. No AI is involved.
If the risk file cannot be read the screen raises UpstreamError (the brain then blocks actions): never "clear".
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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


def _spans(text: str, term: str) -> list[tuple[int, int]]:
    return [m.span() for m in _term_regex(term).finditer(text)]


def _found(text: str, term: str, exempt: Sequence[tuple[int, int]] = ()) -> bool:
    """Does `term` occur outside the exempt phrases (a match that lies inside one of them does not count)?"""
    return any(not any(s >= a and e <= b for a, b in exempt) for s, e in _spans(text, term))


def _collapse(text: str) -> str:
    return _STRETCH.sub(r"\1", text)


# Letters spread out with spaces or marks ("s c a m", "f.r.a.u.d", "n.a.s.b"): three or more single characters in a row.
_SPREAD = re.compile(r"(?<!\w)(?:[^\W_][\s.\-_*]+){2,}[^\W_](?!\w)")
_SEPARATORS = re.compile(r"[\s.\-_*]+")
_LOOK_ALIKES = str.maketrans({"@": "a", "4": "a", "3": "e", "0": "o", "1": "i", "$": "s", "5": "s"})
_LATIN_TOKEN = re.compile(r"(?<!\w)(?=[^\s]*[a-z]{2})[a-z0-9@$]+(?!\w)")


def _joined(text: str, *, keep_first: bool = False) -> str:
    """Spread-out letters put back together: "s c a m" -> "scam", "n.a.s.b" -> "nasb".

    keep_first leaves the first character of each run apart ("a s c a m" -> "a scam"), for a word that follows a
    one-letter word."""

    def join(match: re.Match[str]) -> str:
        run = match.group(0)
        if not keep_first:
            return _SEPARATORS.sub("", run)
        first, _, rest = re.split(r"([\s.\-_*]+)", run, maxsplit=1)
        return first + " " + _SEPARATORS.sub("", rest)

    return _SPREAD.sub(join, text)


def _folded(text: str) -> str:
    """Look-alike characters read as the letters they imitate, inside Latin words only ("fr4ud" -> "fraud")."""
    return _LATIN_TOKEN.sub(lambda m: m.group(0).translate(_LOOK_ALIKES), text)


@dataclass(frozen=True)
class _Variant:
    text: str
    english_only: bool  # look-alike folding is only trusted for English words (Arabizi uses 3, 5, 7 as letters)


def _variants(text: str) -> list[_Variant]:
    """The other ways the same message may be read when someone hides a word. Only those that differ from the text."""
    joined = _joined(text)
    candidates = [
        _Variant(joined, False),
        _Variant(_joined(text, keep_first=True), False),
        _Variant(_folded(text), True),
        _Variant(_folded(joined), True),
    ]
    return [v for i, v in enumerate(candidates) if v.text != text and v not in candidates[:i]]


class _Category:
    def __init__(self, name: str, spec: dict[str, Any]) -> None:
        self.name = name
        self.mandatory = spec.get("mandatory_escalation", True) is not False
        # (original term, normalized term, the term with stretched letters collapsed, is it an English term)
        self.exempt = [normalize(e) for e in spec.get("exempt", []) if normalize(e)]
        self.terms = [
            (term, normalize(term), _collapse(normalize(term)), style == "en")
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
        variants = _variants(text)
        categories: list[str] = []
        matched: list[str] = []
        mandatory = False
        for category in self._categories(tenant_id):
            skip = [span for phrase in category.exempt for span in _spans(text, phrase)]
            skip_short = [span for phrase in category.exempt for span in _spans(collapsed, _collapse(phrase))]
            hits = [
                term
                for term, norm, short, _ in category.terms
                if _found(text, norm, skip) or _found(collapsed, short, skip_short)
            ]
            for variant in variants:  # the message dressed up to dodge the list
                hits += [
                    term
                    for term, norm, short, english in category.terms
                    if (english or not variant.english_only)
                    and (_found(variant.text, norm) or _found(_collapse(variant.text), short))
                ]
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
