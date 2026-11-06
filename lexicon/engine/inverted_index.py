"""Module 4 — Inverted index construction.

A *positional* inverted index::

    term -> { doc_id -> [position, position, ...] }

Positions make exact phrase queries possible; list lengths give term
frequencies for TF-IDF ranking.
"""
from __future__ import annotations

import math
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .document_loader import Document
from .normalizer import Normalizer
from .tokenizer import Token


class InvertedIndex:
    def __init__(self) -> None:
        self._postings: Dict[str, Dict[int, List[int]]] = {}
        self._doc_lengths: Dict[int, int] = {}
        self._vocab_cache: Optional[List[str]] = None

    # ---- construction -------------------------------------------------
    def add_document(self, doc_id: int, terms: Iterable[Tuple[str, int]]) -> None:
        """Index one document from ``(term, position)`` pairs."""
        length = 0
        for term, position in terms:
            self._postings.setdefault(term, {}).setdefault(doc_id, []).append(position)
            length += 1
        self._doc_lengths[doc_id] = length
        self._vocab_cache = None

    # ---- lookups ------------------------------------------------------
    def postings(self, term: str) -> Dict[int, List[int]]:
        """``{doc_id: [positions]}`` for *term* (internal dict — do not mutate)."""
        return self._postings.get(term, {})

    def doc_ids(self, term: str) -> set:
        return set(self._postings.get(term, ()))

    def all_doc_ids(self) -> List[int]:
        return sorted(self._doc_lengths)

    def df(self, term: str) -> int:
        return len(self._postings.get(term, ()))

    def tf(self, term: str, doc_id: int) -> int:
        return len(self._postings.get(term, {}).get(doc_id, ()))

    def doc_length(self, doc_id: int) -> int:
        return self._doc_lengths.get(doc_id, 0)

    def vocabulary(self) -> List[str]:
        if self._vocab_cache is None:
            self._vocab_cache = sorted(self._postings)
        return self._vocab_cache

    # ---- ranking ------------------------------------------------------
    def tfidf(self, term: str, doc_id: int) -> float:
        tf = self.tf(term, doc_id)
        df = self.df(term)
        if not tf or not df:
            return 0.0
        return (1.0 + math.log10(tf)) * math.log10(1.0 + self.num_docs / df)

    # ---- statistics ---------------------------------------------------
    @property
    def num_docs(self) -> int:
        return len(self._doc_lengths)

    @property
    def num_terms(self) -> int:
        return len(self._postings)

    @property
    def num_postings(self) -> int:
        return sum(len(docs) for docs in self._postings.values())

    @property
    def num_tokens(self) -> int:
        return sum(self._doc_lengths.values())


def build_index(documents: Sequence[Document],
                tokenizer: Callable[[str], List[Token]],
                normalizer: Normalizer) -> InvertedIndex:
    """Run every document through tokenizer -> normalizer -> index."""
    index = InvertedIndex()
    for doc in documents:
        tokens = tokenizer(doc.indexed_text)
        pairs = ((term, token.position) for token, term in normalizer.normalize_tokens(tokens))
        index.add_document(doc.doc_id, pairs)
    return index
