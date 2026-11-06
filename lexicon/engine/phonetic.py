"""Module 7 — Phonetic matching (American Soundex).

Soundex maps a word to a four-character code: its first letter followed by
three digits that stand for the consonant sounds that come next.  Words that
sound alike share a code, so ``smith`` and ``smyth`` both become ``S530``.

    b f p v          -> 1        l      -> 4
    c g j k q s x z  -> 2        m n    -> 5
    d t              -> 3        r      -> 6

Vowels and ``y`` are dropped but separate two letters with the same digit;
``h`` and ``w`` are dropped and do *not* separate them.  Only plain ASCII
letters are encoded, so terms in other scripts or with digits get no code and
are matched exactly.
"""
from __future__ import annotations

from typing import Dict, Iterable, List

_CODES = {}
for _digit, _letters in (("1", "bfpv"), ("2", "cgjkqsxz"), ("3", "dt"),
                         ("4", "l"), ("5", "mn"), ("6", "r")):
    for _ch in _letters:
        _CODES[_ch] = _digit


def soundex(word: str) -> str:
    """Soundex code of *word*, or ``""`` if it is not a plain ASCII word."""
    if not word or not word.isascii() or not word.isalpha():
        return ""
    word = word.lower()
    code = word[0].upper()
    previous = _CODES.get(word[0], "")
    for ch in word[1:]:
        digit = _CODES.get(ch, "")
        if digit and digit != previous:
            code += digit
            if len(code) == 4:
                break
        if ch not in "hw":      # h and w do not break a run of equal digits
            previous = digit
    return code.ljust(4, "0")


class SoundexIndex:
    """``code -> vocabulary terms`` lookup built once at startup."""

    def __init__(self, vocabulary: Iterable[str]):
        self._by_code: Dict[str, List[str]] = {}
        for term in sorted(set(vocabulary)):
            code = soundex(term)
            if code:
                self._by_code.setdefault(code, []).append(term)

    def __len__(self) -> int:
        return len(self._by_code)

    def expand(self, word: str) -> List[str]:
        """Sorted vocabulary terms that sound like *word* (may include it)."""
        code = soundex(word)
        return list(self._by_code.get(code, ())) if code else []
