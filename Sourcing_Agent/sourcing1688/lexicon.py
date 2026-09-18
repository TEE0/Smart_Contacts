"""English -> Chinese translation for 1688 apparel queries.

1688 is a Chinese-language marketplace. Searching it in English returns a thin,
unrepresentative slice of the catalogue (mostly listings written for exporters),
so query translation is not a nicety - it is the difference between seeing a few
hundred offers and seeing the actual market.

The lexicon is curated rather than machine-translated because 1688 sellers use
trade jargon: a "heavyweight oversized tee" is listed as 重磅宽松短袖T恤, never as
a literal translation of those three words.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Iterable

_DATA_FILE = Path(__file__).with_name("data") / "lexicon_apparel.json"

# Longest-match-first so "long sleeve tee" wins over "tee".
_TOKEN_SPLIT = re.compile(r"[^a-z0-9%+\u4e00-\u9fff]+")


@lru_cache(maxsize=1)
def load_lexicon() -> dict[str, dict[str, list[str]]]:
    raw = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if isinstance(v, dict) and not k.startswith("_")}


@lru_cache(maxsize=1)
def _phrase_index() -> dict[tuple[str, ...], tuple[str, str, list[str]]]:
    """Normalized token tuple -> (english phrase, group, chinese terms).

    Keys are normalized the same way user text is, so the lexicon entry
    "t-shirt" is found in the sentence "oversized t shirt" as well as
    "t-shirt".
    """
    index: dict[tuple[str, ...], tuple[str, str, list[str]]] = {}
    for group, mapping in load_lexicon().items():
        for phrase, zh in mapping.items():
            key = tuple(normalize(phrase).split())
            if key and key not in index:
                index[key] = (phrase, group, list(zh))
    return index


@lru_cache(maxsize=1)
def _max_phrase_len() -> int:
    return max((len(k) for k in _phrase_index()), default=1)


def normalize(text: str) -> str:
    """Lowercase, strip accents, split on anything that is not a word character.

    Accent folding matters: sellers and buyers write "bouclé" and "boucle"
    interchangeably, and a lexicon miss silently drops the requirement.
    """
    folded = unicodedata.normalize("NFKD", text or "").lower()
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return " ".join(t for t in _TOKEN_SPLIT.split(folded) if t)


def has_chinese(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text or "")


def lookup(term: str) -> list[str]:
    """Chinese alternates for a single English term, best match first."""
    tokens = tuple(normalize(term).split())
    if not tokens:
        return []
    hit = _phrase_index().get(tokens)
    if hit:
        return list(hit[2])
    matches = extract_terms(term)
    return list(matches[0][2]) if matches else []


_SUFFIX_RULES = (
    ("ies", "y"), ("ied", "y"), ("ing", ""), ("ied", ""), ("ed", ""), ("y", ""),
    ("es", ""), ("s", ""), ("ed", "e"), ("ing", "e"),
)


def _stem_variants(token: str) -> list[str]:
    """Cheap morphological fallback: "embroidered" -> "embroidery"/"embroider".

    A real stemmer is overkill here - the lexicon is closed and the failure mode
    that matters is silently dropping a word the buyer cared about.
    """
    out: list[str] = []
    for suffix, replacement in _SUFFIX_RULES:
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            out.append(token[: -len(suffix)] + replacement)
    return out


@lru_cache(maxsize=1)
def _stem_index() -> dict[str, tuple[str, str, list[str]]]:
    """Single-token lexicon entries keyed by every stem that reaches them."""
    index: dict[str, tuple[str, str, list[str]]] = {}
    for key, entry in _phrase_index().items():
        if len(key) != 1:
            continue
        token = key[0]
        for variant in [token, *_stem_variants(token)]:
            index.setdefault(variant, entry)
        # also index the stem of the lexicon word itself ("embroidery" -> "embroider")
        for variant in _stem_variants(token):
            index.setdefault(variant, entry)
    return index


def _stem_lookup(token: str) -> tuple[str, str, list[str]] | None:
    index = _stem_index()
    hit = index.get(token)
    if hit:
        return hit
    for variant in _stem_variants(token):
        hit = index.get(variant)
        if hit:
            return hit
    return None


def _scan(tokens: list[str]) -> list[tuple[int, int, tuple[str, str, list[str]]]]:
    """Left-to-right longest-match scan. Returns (start, token_span, entry)."""
    index = _phrase_index()
    window = _max_phrase_len()
    hits: list[tuple[int, int, tuple[str, str, list[str]]]] = []
    i = 0
    while i < len(tokens):
        for size in range(min(window, len(tokens) - i), 0, -1):
            entry = index.get(tuple(tokens[i : i + size]))
            if entry:
                hits.append((i, size, entry))
                i += size
                break
        else:
            entry = _stem_lookup(tokens[i])
            if entry:
                hits.append((i, 1, entry))
            i += 1
    return hits


def extract_terms(text: str) -> list[tuple[str, str, list[str]]]:
    """Find every lexicon phrase present in free text.

    Scans left to right taking the longest match at each position, so
    "long sleeve tee" is consumed as one phrase and never re-matched as "tee".
    Returns (matched_english, group, chinese_terms) in reading order, deduped.
    """
    found: list[tuple[str, str, list[str]]] = []
    seen: set[str] = set()
    for _start, _size, (phrase, group, zh) in _scan(normalize(text).split()):
        if phrase not in seen:
            seen.add(phrase)
            found.append((phrase, group, list(zh)))
    return found


_ORDER = {"audience": 0, "season": 1, "fit": 2, "detail": 3, "fabric": 4, "garment": 5, "commercial": 6}


def _ordered_matches(text: str) -> list[tuple[str, str, list[str]]]:
    matches = extract_terms(text)
    matches.sort(key=lambda m: _ORDER.get(m[1], 9))
    return matches


def join_terms(parts: list[str]) -> str:
    """Concatenate Chinese query tokens, dropping repeats and substrings.

    1688 keyword search treats the query as one string, so 纯棉纯棉T恤 scores
    worse than 纯棉T恤 - duplicate morphemes must not survive assembly.
    """
    kept: list[str] = []
    for part in parts:
        if not part or any(part in k for k in kept):
            continue
        kept = [k for k in kept if k not in part]
        kept.append(part)
    return "".join(kept)


def translate_phrase(text: str, max_terms: int = 6) -> str:
    """Best-effort Chinese rendering of an English sourcing phrase.

    Unknown words are dropped rather than transliterated - a junk token poisons
    a 1688 keyword search far more than a missing adjective does.
    """
    if has_chinese(text):
        return text.strip()
    matches = [m for m in _ordered_matches(text) if m[2]]
    # The garment is never dropped by truncation. A query that keeps "宽松重磅刺绣"
    # but loses "短袖T恤" searches for an adjective, which on 1688 returns noise
    # from every category at once.
    garments = [m for m in matches if m[1] == "garment"]
    modifiers = [m for m in matches if m[1] != "garment"]
    keep = garments[:1] + modifiers[: max(max_terms - len(garments[:1]), 0)]
    parts = [zh[0] for _p, _g, zh in sorted(keep, key=lambda m: _ORDER.get(m[1], 9))]
    return join_terms(parts)


def alternates(text: str, limit: int = 3) -> list[str]:
    """Alternate Chinese phrasings for the same brief (synonym sweep).

    1688 keyword search is literal; 卫衣 and 帽衫 surface different supplier
    populations for the same garment, so a serious sourcing run queries both.
    """
    if has_chinese(text):
        return []
    matches = _ordered_matches(text)
    garments = [m for m in matches if m[1] == "garment"]
    if not garments:
        return []
    primary = garments[0]
    base = translate_phrase(text)
    out: list[str] = []
    for zh_alt in primary[2][1:]:
        parts = [(zh_alt if phrase == primary[0] else zh[0]) for phrase, _g, zh in matches if zh]
        candidate = join_terms(parts)
        if candidate and candidate != base and candidate not in out:
            out.append(candidate)
        if len(out) >= limit:
            break
    return out


def describe_terms(text: str) -> dict[str, list[str]]:
    """Group the recognised terms - used to explain a query back to the user."""
    grouped: dict[str, list[str]] = {}
    for phrase, group, zh in extract_terms(text):
        grouped.setdefault(group, []).append(f"{phrase} -> {zh[0]}")
    return grouped


def unknown_terms(text: str) -> list[str]:
    """Input words the scan never consumed.

    Surfaced verbatim so the buyer can supply the Chinese term themselves - a
    dropped adjective ("ribbed", "bouclé") is exactly the kind of requirement
    that quietly turns a good shortlist into the wrong garment.
    """
    if has_chinese(text):
        return []
    tokens = normalize(text).split()
    consumed: set[int] = set()
    for start, size, _entry in _scan(tokens):
        consumed.update(range(start, start + size))
    stop = {
        "a", "an", "the", "for", "with", "and", "or", "of", "in", "on", "to", "my", "i",
        "want", "need", "looking", "find", "buy", "some", "good", "best", "quality",
        "supplier", "suppliers", "price", "cheap", "around", "under", "per", "piece",
        "pieces", "unit", "units", "usd", "cny", "rmb", "please", "from", "1688",
        "that", "is", "are", "at", "as", "it", "me", "we", "us", "our", "about",
    }
    leftovers = [t for i, t in enumerate(tokens) if i not in consumed and not t.isdigit() and t not in stop]
    return list(dict.fromkeys(leftovers))


def zh_terms_for(groups: Iterable[str], text: str) -> list[str]:
    wanted = set(groups)
    return [zh[0] for _p, g, zh in extract_terms(text) if g in wanted and zh]
