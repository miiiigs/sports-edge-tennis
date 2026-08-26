from __future__ import annotations

import re
import unicodedata


_WHITESPACE_RE = re.compile(r"\s+")
_PUNCTUATION_RE = re.compile(r"[.\-,'’]")
_SURNAME_PARTICLES = {
    "da",
    "de",
    "del",
    "della",
    "der",
    "di",
    "dos",
    "du",
    "la",
    "le",
    "st",
    "van",
    "von",
}


def collapse_whitespace(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value.strip())


def normalize_hash_text(value: str) -> str:
    return collapse_whitespace(unicodedata.normalize("NFKC", value))


def _strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_person_lookup(value: str) -> str:
    normalized = normalize_hash_text(_strip_accents(value)).casefold()
    normalized = _PUNCTUATION_RE.sub(" ", normalized)
    return collapse_whitespace(normalized)


def tennis_data_aliases(canonical_name: str) -> set[str]:
    aliases = {canonical_name}
    tokens = [token for token in collapse_whitespace(canonical_name).split(" ") if token]
    if len(tokens) < 2:
        return aliases

    surname_tokens = [tokens[-1]]
    index = len(tokens) - 2
    while index >= 0 and tokens[index].casefold() in _SURNAME_PARTICLES:
        surname_tokens.insert(0, tokens[index])
        index -= 1
    given_tokens = tokens[: index + 1]
    initials = "".join(token[0].upper() for token in given_tokens if token)
    surname = " ".join(surname_tokens)

    if initials:
        aliases.add(f"{surname} {initials}")
        aliases.add(f"{surname} {'.'.join(initials)}.")
        aliases.add(f"{surname}, {initials}")
        aliases.add(f"{surname}, {'.'.join(initials)}.")
    return aliases

