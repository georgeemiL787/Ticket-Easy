"""Understanding without any AI: the always-available fallback.

RuleBasedNLU reads a message and returns an NLUResult: language, the intents in the order they appear, details
(order id, phone, amount), whether it is mainly a yes or a no, whether the customer wants a person, and how frustrated
they sound. Everything comes from the word lists in data/lexicon/default.json, so adding a word never needs code.

How intents are found: every keyword of every intent in the tenant's catalog is looked for in the normalized text.
Where two keywords overlap, the longer one wins ("ارجع فلوسي" is a refund, not a return). A negation word shortly
before a keyword ("I don't want to return it", "مش عايز ارجع") cancels an action or handoff intent. A question about
timing ("how many days do I have to return an item?") is a policy question, not the action, unless an order is named.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, Protocol

from team_b.brain.language import detect_language
from team_b.brain.lexicon import Lexicon, StyleWords, default_lexicon
from team_b.brain.text import find_spans, normalize
from team_b.domain.session import SessionState
from team_b.domain.tenant import TenantConfig
from team_b.domain.understanding import Frustration, IntentCandidate, Language, NLUResult

NEGATION_WINDOW = 3  # a negation counts when it is at most this many words before the keyword
BASE_CONFIDENCE = 0.7
SHORT_MESSAGE_WORDS = 3  # "agent please" is a request for a person even without a longer phrase
_SENTENCE_BREAK = re.compile(r"[.,;!?\n]")
_WORD = re.compile(r"[\w']+")
_PHONE = re.compile(r"(?<!\d)(?:\+?\s?20|0020)?[\s\-]*0?(1[0125](?:[\s\-]*\d){8})(?!\d)")
_AMOUNT = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?:egp|le|l\.e\.?|جنيه|جنيها|جنيهات|geneh|gnh|pounds?)(?!\w)"
    r"|(?<!\w)(?:egp|l\.e\.?)\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?!\w)"
)
_PUNCTUATION_RUN = re.compile(r"[!?]{3,}|!{2,}")
_ELONGATION = re.compile(r"([^\W\d_])\1{3,}")
_MAX_SPEECH_BREAK = 40  # characters of context looked at before a keyword when checking negation


class NLU(Protocol):
    async def understand(self, text: str, session: SessionState | None, tenant: TenantConfig) -> NLUResult: ...


@dataclass(frozen=True)
class _Hit:
    start: int
    end: int
    intent: str


@lru_cache(maxsize=64)
def _order_id_patterns(order_id_pattern: str, order_id_prefix: str) -> tuple[re.Pattern[str], ...]:
    """Patterns whose group "id" holds the part of the id after the prefix (the whole id when there is no prefix)."""
    tail = (
        order_id_pattern[len(order_id_prefix) :]
        if order_id_prefix and order_id_pattern.startswith(order_id_prefix)
        else ""
    )
    if not tail:
        return (re.compile(rf"(?<![a-z0-9])(?P<id>{order_id_pattern.lower()})(?![a-z0-9])"),)
    letters = re.escape(normalize(re.sub(r"[^A-Za-z0-9]", "", order_id_prefix)))
    prefixed = re.compile(rf"(?<![a-z0-9]){letters}[\s\-_]*(?P<id>{tail})(?!\d)")
    bare = re.compile(
        rf"(?:order|اوردر|الاوردر|طلب|الطلب|talab|رقم)\s*(?:number|no\.?|nr|#|رقم)?\s*[:#]?\s*(?<!\d)(?P<id>{tail})(?!\d)"
    )
    return prefixed, bare


def extract_order_ids(normalized: str, tenant: TenantConfig) -> list[tuple[int, int, str]]:
    """(start, end, canonical id) for every order id, in order. Accepts "NS 20877", "ns20877" and "order 20877"."""
    has_tail = bool(tenant.order_id_prefix) and tenant.order_id_pattern.startswith(tenant.order_id_prefix)
    prefix = tenant.order_id_prefix if has_tail else ""
    found: dict[int, tuple[int, int, str]] = {}
    for pattern in _order_id_patterns(tenant.order_id_pattern, tenant.order_id_prefix):
        for match in pattern.finditer(normalized):
            canonical = prefix + match.group("id") if has_tail else match.group("id").upper()
            if re.fullmatch(tenant.order_id_pattern, canonical):
                found.setdefault(match.start(), (match.start(), match.end(), canonical))
    unique: list[tuple[int, int, str]] = []
    for item in sorted(found.values()):
        if not unique or item[0] >= unique[-1][1]:
            unique.append(item)
    return unique


def extract_phone(normalized: str) -> str | None:
    """An Egyptian mobile number, written with +20, 0020, spaces or dashes, as 01XXXXXXXXX."""
    match = _PHONE.search(normalized)
    if match is None:
        return None
    return "0" + re.sub(r"\D", "", match.group(1))


def extract_amount(normalized: str) -> str | None:
    """A money amount next to EGP / LE / جنيه / geneh, as plain digits ("3,000 EGP" gives "3000")."""
    match = _AMOUNT.search(normalized)
    if match is None:
        return None
    return (match.group(1) or match.group(2)).replace(",", "")


class RuleBasedNLU:
    def __init__(self, lexicon: Lexicon | None = None) -> None:
        self._lexicon = lexicon or default_lexicon()

    async def understand(self, text: str, session: SessionState | None, tenant: TenantConfig) -> NLUResult:
        return self.understand_sync(text, session, tenant)

    def understand_sync(self, text: str, session: SessionState | None, tenant: TenantConfig) -> NLUResult:
        lex = self._lexicon
        normalized = normalize(text)
        language, confidence = detect_language(text, lex)
        if language is None:  # nothing to go on: keep the conversation's language
            language = session.language if session and session.language else Language(tenant.default_locale.value)
            confidence = 0.0

        entities, masked = self._entities(normalized, tenant)
        intents, wants_human = self._intents(normalized, tenant, bool(entities.get("order_id")))
        return NLUResult(
            language=language,
            language_confidence=confidence,
            intents=tuple(intents),
            entities=entities,
            affirmation=self._affirmation(normalized),
            wants_human=wants_human,
            frustration=self._frustration(text, normalized),
            method="rules",
        )

    # ---- details ----

    def _entities(self, normalized: str, tenant: TenantConfig) -> tuple[dict[str, str], str]:
        """The details found, and the text with order ids blanked out (so their digits are not read as a phone)."""
        entities: dict[str, str] = {}
        ids = extract_order_ids(normalized, tenant)
        masked = normalized
        for start, end, _ in reversed(ids):
            masked = masked[:start] + " " * (end - start) + masked[end:]
        if ids:
            entities["order_id"] = ids[0][2]
            if len(ids) > 1:
                entities["order_ids"] = ",".join(dict.fromkeys(i[2] for i in ids))
        if (phone := extract_phone(masked)) is not None:
            entities["phone"] = phone
        if (amount := extract_amount(masked)) is not None:
            entities["amount"] = amount
        return entities, masked

    # ---- intents ----

    def _hits(self, normalized: str, tenant: TenantConfig) -> list[_Hit]:
        """Keyword hits for the tenant's intents; overlapping hits are resolved in favour of the longer keyword."""
        candidates: list[_Hit] = []
        for intent in tenant.intents:
            words = self._lexicon.intents.get(intent)
            if words is None:
                continue
            for term in words.all():
                candidates += [_Hit(s, e, intent) for s, e in find_spans(normalized, term)]
        candidates.sort(key=lambda h: (-(h.end - h.start), h.start))
        accepted: list[_Hit] = []
        for hit in candidates:
            if all(hit.end <= other.start or hit.start >= other.end for other in accepted):
                accepted.append(hit)
        return sorted(accepted, key=lambda h: h.start)

    def _negated(self, normalized: str, start: int) -> bool:
        before = normalized[max(0, start - _MAX_SPEECH_BREAK) : start]
        breaks = list(_SENTENCE_BREAK.finditer(before))
        if breaks:
            before = before[breaks[-1].end() :]
        nearby = " ".join(_WORD.findall(before)[-NEGATION_WINDOW:])
        return any(find_spans(nearby, term) for term in self._lexicon.negation.all())

    def _intents(self, normalized: str, tenant: TenantConfig, has_order: bool) -> tuple[list[IntentCandidate], bool]:
        lex = self._lexicon
        hits = [
            h
            for h in self._hits(normalized, tenant)
            if not (tenant.intents[h.intent].kind in ("action", "handoff") and self._negated(normalized, h.start))
        ]
        wants = self._has(normalized, lex.want_markers)
        asks = self._has(normalized, lex.question_markers)
        asks_about_rule = self._has(normalized, lex.policy_timing_markers) and not has_order
        names: list[str] = []
        scores: dict[str, float] = {}
        for hit in hits:
            name = hit.intent
            confidence = BASE_CONFIDENCE + (0.1 if wants else 0.0)
            if tenant.intents[name].kind == "action" and asks_about_rule and "policy_question" in tenant.intents:
                name, confidence = "policy_question", BASE_CONFIDENCE + 0.05
            if name == "policy_question" and asks:
                confidence += 0.05
            if name in scores:
                scores[name] = min(0.95, scores[name] + 0.05)
                continue
            names.append(name)
            scores[name] = min(0.95, confidence)
        if "greeting" in scores and len(names) > 1:
            names.remove("greeting")
            del scores["greeting"]

        human = "human_request" in scores or self._short_human_request(normalized)
        if human and "human_request" in tenant.intents and "human_request" not in scores:
            names.append("human_request")
            scores["human_request"] = BASE_CONFIDENCE
        return [IntentCandidate(name=n, confidence=round(scores[n], 2)) for n in names], human

    def _short_human_request(self, normalized: str) -> bool:
        words = _WORD.findall(normalized)
        return 0 < len(words) <= SHORT_MESSAGE_WORDS and self._has(normalized, self._lexicon.human_markers)

    def _has(self, normalized: str, words: StyleWords) -> bool:
        return any(find_spans(normalized, term) for term in words.all())

    # ---- yes / no ----

    def _affirmation(self, normalized: str) -> Literal["yes", "no"] | None:
        """Yes or no, only when the whole message is one (polite filler allowed), not when it merely contains one."""
        lex = self._lexicon
        tokens = _WORD.findall(normalized)
        if not tokens or len(tokens) > 6:
            return None
        covered = {"yes": [False] * len(tokens), "no": [False] * len(tokens), "filler": [False] * len(tokens)}
        for label, words in (("no", lex.no), ("yes", lex.yes), ("filler", lex.filler)):
            for term in words.all():
                term_tokens = _WORD.findall(term)
                for i in range(len(tokens) - len(term_tokens) + 1):
                    if tokens[i : i + len(term_tokens)] == term_tokens:
                        for j in range(i, i + len(term_tokens)):
                            covered[label][j] = True
        said_yes, said_no = any(covered["yes"]), any(covered["no"])
        everything = all(y or n or f for y, n, f in zip(covered["yes"], covered["no"], covered["filler"], strict=True))
        if not everything or said_yes == said_no:
            return None
        return "yes" if said_yes else "no"

    # ---- frustration ----

    def _frustration(self, text: str, normalized: str) -> Frustration:
        """Strong words count 2, medium words 1, repeated !!! or ??? 1, ALL CAPS 1, stretched letters 1."""
        lex = self._lexicon.frustration
        score = 2 * sum(bool(find_spans(normalized, t)) for t in lex.strong.all())
        score += min(2, sum(bool(find_spans(normalized, t)) for t in lex.medium.all()))
        score += bool(_PUNCTUATION_RUN.search(normalized))
        score += bool(_ELONGATION.search(normalized))
        letters = [c for c in re.sub(r"\b[A-Za-z]{2,3}[-\s]?\d{4,6}\b", " ", text) if c.isascii() and c.isalpha()]
        score += len(letters) >= 8 and sum(c.isupper() for c in letters) / len(letters) >= 0.7
        return "high" if score >= 4 else "medium" if score >= 2 else "low"
