"""Module 8 — Spell checking.

Suggestions come from the index vocabulary itself, so every correction is a
word that really occurs in the documents.  Candidates are ranked by

    1. edit distance (Damerau-Levenshtein, adjacent swaps cost 1),
    2. document frequency (more common words first),
    3. alphabetical order.

Only candidates within ``max_distance`` edits are offered, and that limit
shrinks for short words so ``cat`` is not "corrected" to every 3-letter term.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional


def edit_distance(a: str, b: str, limit: Optional[int] = None) -> int:
    """Optimal-string-alignment distance; returns ``limit + 1`` once exceeded."""
    if a == b:
        return 0
    if limit is not None and abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: List[int] = []
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if limit is not None and min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def allowed_distance(word: str) -> int:
    """How many edits we tolerate for a word of this length."""
    if len(word) <= 3:
        return 1
    if len(word) <= 7:
        return 2
    return 3


class SpellChecker:
    def __init__(self, frequencies: Dict[str, int]):
        """*frequencies* maps each vocabulary term to its document frequency."""
        self._freq = dict(frequencies)
        # Only alphabetic terms are sensible suggestions.
        self._words = sorted(t for t in self._freq if t.isalpha())

    def is_known(self, word: str) -> bool:
        return word in self._freq

    def suggest(self, word: str, limit: int = 5) -> List[Dict]:
        """Closest vocabulary terms to *word*; empty if it is already known."""
        if not word or word in self._freq:
            return []
        max_d = allowed_distance(word)
        found = []
        for cand in self._words:
            if abs(len(cand) - len(word)) > max_d:
                continue
            d = edit_distance(word, cand, max_d)
            if d <= max_d:
                found.append((d, -self._freq[cand], cand))
        found.sort()
        return [{"term": cand, "distance": d, "df": -negdf}
                for d, negdf, cand in found[:limit]]
