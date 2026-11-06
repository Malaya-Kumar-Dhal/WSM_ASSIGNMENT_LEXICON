"""Module 6 — Wildcard processing (permuterm index).

Every term ``t`` is stored under all rotations of ``t + "$"``::

    hello$  ello$h  llo$he  lo$hel  o$hell  $hello

A wildcard query is rotated so that its *last* wildcard sits at the end, which
turns it into a plain **prefix lookup** on the sorted rotations:

    hel*o   ->  hel*o$  ->  o$hel*     prefix "o$hel"  ->  hello
    *ing    ->  *ing$   ->  ing$*      prefix "ing$"
    hel*    ->  hel*$   ->  $hel*      prefix "$hel"

Candidates are then confirmed with a regex, which also handles ``?``
(exactly one character) and patterns with several ``*``.
"""
from __future__ import annotations

import re
from bisect import bisect_left
from typing import Iterable, List, Tuple

WILDCARD_CHARS = "*?"


def is_wildcard(text: str) -> bool:
    return any(c in text for c in WILDCARD_CHARS)


def pattern_to_regex(pattern: str) -> "re.Pattern[str]":
    parts = []
    for ch in pattern:
        if ch == "*":
            parts.append(".*")
        elif ch == "?":
            parts.append(".")
        else:
            parts.append(re.escape(ch))
    return re.compile("".join(parts), re.DOTALL)


def lookup_prefix(pattern: str) -> str:
    """Permuterm prefix that every match of *pattern* must start with."""
    query = pattern + "$"
    last = max(i for i, c in enumerate(query) if c in WILDCARD_CHARS)
    rotated = query[last + 1:] + query[:last + 1]
    first = min(i for i, c in enumerate(rotated) if c in WILDCARD_CHARS)
    return rotated[:first]


class PermutermIndex:
    def __init__(self, vocabulary: Iterable[str]):
        self._vocab = set(vocabulary)
        entries: List[Tuple[str, str]] = []
        for term in self._vocab:
            marked = term + "$"
            for i in range(len(marked)):
                entries.append((marked[i:] + marked[:i], term))
        entries.sort()
        self._keys = [rotation for rotation, _ in entries]
        self._terms = [term for _, term in entries]

    def __len__(self) -> int:
        return len(self._keys)

    def expand(self, pattern: str) -> List[str]:
        """Sorted list of vocabulary terms matching *pattern*."""
        if not is_wildcard(pattern):
            return [pattern] if pattern in self._vocab else []

        prefix = lookup_prefix(pattern)
        matcher = pattern_to_regex(pattern)
        found = set()
        i = bisect_left(self._keys, prefix)
        while i < len(self._keys) and self._keys[i].startswith(prefix):
            term = self._terms[i]
            if term not in found and matcher.fullmatch(term):
                found.add(term)
            i += 1
        return sorted(found)

    def prefix_terms(self, prefix: str) -> List[str]:
        """All terms starting with *prefix* (used for autocomplete)."""
        i = bisect_left(self._keys, "$" + prefix)
        out = []
        while i < len(self._keys) and self._keys[i].startswith("$" + prefix):
            out.append(self._terms[i])
            i += 1
        return out
