"""Module 2 — Tokenization.

Splits text into word tokens, remembering each token's ordinal position (for
phrase queries) and character offsets (for highlighting snippets).

A token is a run of letters/digits, optionally joined by apostrophes
(``don't``, ``moon's``).  Hyphens, underscores and punctuation separate tokens.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

_TOKEN_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)


@dataclass(frozen=True)
class Token:
    text: str      # surface form, exactly as written
    position: int  # 0-based ordinal position in the document
    start: int     # character offset (inclusive)
    end: int       # character offset (exclusive)


def tokenize(text: str) -> List[Token]:
    return [
        Token(m.group(), i, m.start(), m.end())
        for i, m in enumerate(_TOKEN_RE.finditer(text))
    ]
