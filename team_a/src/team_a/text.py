"""Arabic/English/Arabizi text normalization and tokenization.

The same normalization runs at index time and query time, so both sides match.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

from team_a.config import settings

_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")
_TATWEEL = "ـ"
_CHAR_MAP = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ى": "ي",
        "ة": "ه",
        "ؤ": "و",
        "ئ": "ي",
        **{chr(0x0660 + i): str(i) for i in range(10)},  # Arabic-Indic digits
        **{chr(0x06F0 + i): str(i) for i in range(10)},  # Persian digits
    }
)
_TOKEN = re.compile(r"[\w]+", re.UNICODE)
_ARABIC_CHARS = re.compile(r"[؀-ۿ]")
_LATIN_CHARS = re.compile(r"[A-Za-z]")
_ARABIC_PREFIXES = ("وال", "بال", "فال", "كال", "لل", "ال")

def normalize(text: str) -> str:
    text = _DIACRITICS.sub("", text).replace(_TATWEEL, "")
    text = text.translate(_CHAR_MAP).lower()
    return re.sub(r"\s+", " ", text).strip()


STOPWORDS = frozenset(
    normalize(w)
    for w in """
    the a an of to in on at for is are was be it this that and or with from by as can i my
    you your we our me do does did have has will would what how
    في من على إلى عن مع هو هي انا انت احنا ده دي دا ايه اللي الي و او ما لا لو هل كان يكون
    el ana enta enty e7na da dy di eh ely elly w walla fe fi men 3ala 3an ma2 law ya
    3ayez 3ayz 3awz 3ayza 3awza 3ayzeen momken mumkin
    """.split()
)


def _light_stem(token: str) -> str:
    for prefix in _ARABIC_PREFIXES:
        if token.startswith(prefix) and len(token) - len(prefix) >= 3:
            return token[len(prefix):]
    return token


def tokenize(text: str) -> list[str]:
    tokens = _TOKEN.findall(normalize(text))
    return [_light_stem(t) for t in tokens if t not in STOPWORDS and t != "_"]


def detect_language(text: str) -> str:
    arabic = len(_ARABIC_CHARS.findall(text))
    latin = len(_LATIN_CHARS.findall(text))
    total = arabic + latin
    if total == 0:
        return "mixed"
    if arabic / total > 0.8:
        return "ar"
    if latin / total > 0.8:
        return "en"
    return "mixed"


@lru_cache(maxsize=1)
def _arabizi_map() -> dict[str, list[str]]:
    path: Path = settings.data_dir / "synonyms" / "arabizi.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    mapping: dict[str, list[str]] = {}
    for entry in raw["entries"]:
        expansions = [normalize(e) for e in entry["means"]]
        for variant in entry["variants"]:
            mapping[normalize(variant)] = expansions
    return mapping


def expand_query(query: str) -> str:
    """Append Arabic/English equivalents of Arabizi words found in the query.

    Embeddings handle Arabic<->English well but not Arabizi, and BM25 needs exact
    tokens, so the expanded text is what both retrievers see.
    """
    mapping = _arabizi_map()
    extra: list[str] = []
    for token in _TOKEN.findall(normalize(query)):
        extra.extend(mapping.get(token) or mapping.get(_light_stem(token), []))
    if not extra:
        return query
    return f"{query} {' '.join(dict.fromkeys(extra))}"
