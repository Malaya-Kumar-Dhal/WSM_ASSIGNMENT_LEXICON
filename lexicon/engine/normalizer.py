"""Module 3 — Text normalisation & case folding.

Turns surface tokens into canonical index terms:

* Unicode case folding (``Straße`` -> ``strasse``)
* accent stripping for Latin text (``café`` -> ``cafe``); scripts such as
  Devanagari keep their combining marks, so non-Latin text is not damaged
* typographic apostrophes -> ``'`` and possessive ``'s`` removed
* optional stop-word removal (off by default so phrase queries stay exact)
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Optional, Tuple

from .tokenizer import Token

DEFAULT_STOPWORDS = frozenset(
    "a an and are as at be by for from has he in is it its of on or that the "
    "to was were will with".split()
)

_POSSESSIVE = re.compile(r"'s$")
_PATTERN_JUNK = re.compile(r"_|[^\w'*?]", re.UNICODE)
_STAR_RUN = re.compile(r"\*+")


def strip_accents(text: str) -> str:
    """Remove combining marks that follow Latin base characters."""
    out: List[str] = []
    for ch in unicodedata.normalize("NFKD", text):
        if unicodedata.combining(ch) and out and ord(out[-1]) < 0x250:
            continue
        out.append(ch)
    return unicodedata.normalize("NFC", "".join(out))


def fold(text: str) -> str:
    """Case-fold, strip accents, unify apostrophes."""
    return strip_accents(text.replace("’", "'").casefold())


class Normalizer:
    def __init__(self, remove_stopwords: bool = False,
                 stopwords: Optional[Iterable[str]] = None):
        self.remove_stopwords = remove_stopwords
        self.stopwords = frozenset(stopwords) if stopwords is not None else DEFAULT_STOPWORDS

    def normalize(self, term: str) -> str:
        """Canonical form of one term; ``""`` means "drop this token"."""
        term = _POSSESSIVE.sub("", fold(term))
        if self.remove_stopwords and term in self.stopwords:
            return ""
        return term

    def normalize_tokens(self, tokens: Iterable[Token]) -> List[Tuple[Token, str]]:
        """Pair each surviving token with its normalised term."""
        pairs: List[Tuple[Token, str]] = []
        for token in tokens:
            term = self.normalize(token.text)
            if term:
                pairs.append((token, term))
        return pairs

    def normalize_pattern(self, pattern: str) -> str:
        """Normalise a wildcard pattern, keeping ``*`` and ``?``."""
        pattern = _PATTERN_JUNK.sub("", fold(pattern))
        return _STAR_RUN.sub("*", pattern)
