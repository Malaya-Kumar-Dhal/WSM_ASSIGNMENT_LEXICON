"""Facade that wires all modules together and shapes results for the GUI."""
from __future__ import annotations

import re
import time
from typing import Dict, List, Optional, Sequence, Set

from .document_loader import Document, load_directory
from .inverted_index import InvertedIndex, build_index
from .normalizer import Normalizer
from .phonetic import SoundexIndex
from .query_processor import QueryProcessor
from .spelling import SpellChecker
from .tokenizer import Token, tokenize
from .wildcard import PermutermIndex

MAX_QUERY_LENGTH = 200
_WS = re.compile(r"\s+")
_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)


def _segments(text: str, hits: Sequence[Token], start: int, end: int) -> List[Dict]:
    """Cut ``text[start:end]`` into ``{"t": str, "h": is_highlighted}`` pieces."""
    pieces: List[Dict] = []
    pos = start
    for hit in hits:
        if hit.start < start or hit.end > end:
            continue
        if hit.start > pos:
            pieces.append({"t": text[pos:hit.start], "h": False})
        pieces.append({"t": text[hit.start:hit.end], "h": True})
        pos = hit.end
    if pos < end:
        pieces.append({"t": text[pos:end], "h": False})

    for piece in pieces:
        piece["t"] = _WS.sub(" ", piece["t"])
    pieces = [p for p in pieces if p["t"]]
    if pieces:
        pieces[0]["t"] = pieces[0]["t"].lstrip()
        pieces[-1]["t"] = pieces[-1]["t"].rstrip()
    return [p for p in pieces if p["t"]]


class SearchEngine:
    def __init__(self, documents: Sequence[Document],
                 normalizer: Optional[Normalizer] = None):
        started = time.perf_counter()
        self.normalizer = normalizer or Normalizer()
        self.documents: List[Document] = list(documents)
        self._by_id = {d.doc_id: d for d in self.documents}
        self.index: InvertedIndex = build_index(self.documents, tokenize, self.normalizer)
        self.wildcards = PermutermIndex(self.index.vocabulary())
        self.phonetics = SoundexIndex(self.index.vocabulary())
        self.speller = SpellChecker({t: self.index.df(t) for t in self.index.vocabulary()})
        self.processor = QueryProcessor(self.index, self.wildcards, self.normalizer,
                                        phonetics=self.phonetics)
        self.build_ms = (time.perf_counter() - started) * 1000

    @classmethod
    def from_directory(cls, path, **kwargs) -> "SearchEngine":
        return cls(load_directory(path), **kwargs)

    # ---- public API ---------------------------------------------------
    def search(self, query: str, limit: int = 20, mode: str = "auto") -> Dict:
        query = (query or "").strip()[:MAX_QUERY_LENGTH]
        started = time.perf_counter()
        result = self.processor.process(query, mode)

        top_score = result.ranked[0][1] if result.ranked else 0.0
        hits = []
        for doc_id, score in result.ranked[:limit]:
            doc = self._by_id[doc_id]
            matched = sorted(t for t in result.positive_terms
                             if doc_id in self.index.postings(t))
            hits.append({
                "doc_id": doc_id,
                "title": doc.title,
                "source": doc.source,
                "score": round(score, 4),
                "relevance": round(score / top_score, 4) if top_score > 0 else 0.0,
                "snippet": self._snippet(doc, result.positive_terms),
                "matched": matched[:8],
            })

        trace = result.trace
        trace["matched_docs"] = [doc_id for doc_id, _ in result.ranked]
        return {
            "query": query,
            "mode": trace["mode"],
            "total": len(result.ranked),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "hits": hits,
            "trace": trace,
        }

    def read_document(self, doc_id: int, query: str = "", mode: str = "auto") -> Optional[Dict]:
        doc = self._by_id.get(doc_id)
        if doc is None:
            return None
        positive = self.processor.process((query or "")[:MAX_QUERY_LENGTH], mode).positive_terms
        text = doc.body
        hits = self._hit_tokens(text, positive)

        paragraphs: List[List[Dict]] = []
        pos = 0
        spans = []
        for m in re.finditer(r"\n\s*\n", text):
            spans.append((pos, m.start()))
            pos = m.end()
        spans.append((pos, len(text)))
        for start, end in spans:
            segs = _segments(text, hits, start, end)
            if segs:
                paragraphs.append(segs)
        return {"doc_id": doc_id, "title": doc.title, "source": doc.source,
                "words": len(tokenize(doc.indexed_text)), "paragraphs": paragraphs}

    def spellcheck(self, query: str, limit: int = 5) -> Dict:
        """Check every plain word of *query* against the index vocabulary.

        Operators, quoted-phrase quotes and wildcard patterns are left alone.
        ``corrected`` is the query with each unknown word replaced by its best
        suggestion (or ``None`` if nothing needed fixing).
        """
        query = (query or "").strip()[:MAX_QUERY_LENGTH]
        words: List[Dict] = []
        pieces: List[str] = []
        pos = 0
        changed = False
        for m in _WORD.finditer(query):
            raw = m.group()
            before = query[m.start() - 1] if m.start() > 0 else ""
            after = query[m.end()] if m.end() < len(query) else ""
            if raw in ("AND", "OR", "NOT") or (before and before in "*?") or (after and after in "*?"):
                continue
            term = self.normalizer.normalize(raw)
            if not term:
                continue
            known = self.speller.is_known(term)
            suggestions = [] if known else self.speller.suggest(term, limit)
            words.append({"word": raw, "known": known, "suggestions": suggestions})
            if suggestions:
                pieces.append(query[pos:m.start()])
                pieces.append(suggestions[0]["term"])
                pos = m.end()
                changed = True
        pieces.append(query[pos:])
        return {
            "query": query,
            "words": words,
            "ok": all(w["known"] for w in words),
            "corrected": "".join(pieces) if changed else None,
        }

    def suggest(self, prefix: str, limit: int = 8) -> List[Dict]:
        prefix = self.normalizer.normalize(prefix or "")
        if not prefix:
            return []
        terms = self.wildcards.prefix_terms(prefix)
        ranked = sorted(terms, key=lambda t: (-self.index.df(t), t))[:limit]
        return [{"term": t, "df": self.index.df(t)} for t in ranked]

    def stats(self) -> Dict:
        return {
            "documents": self.index.num_docs,
            "terms": self.index.num_terms,
            "postings": self.index.num_postings,
            "tokens": self.index.num_tokens,
            "permuterm_entries": len(self.wildcards),
            "soundex_codes": len(self.phonetics),
            "build_ms": round(self.build_ms, 2),
            "titles": [d.title for d in self.documents],
            "sources": [d.source for d in self.documents],
            "words": [self.index.doc_length(d.doc_id) for d in self.documents],
        }

    # ---- snippets -----------------------------------------------------
    def _hit_tokens(self, text: str, positive: Set[str]) -> List[Token]:
        if not positive:
            return []
        return [t for t in tokenize(text) if self.normalizer.normalize(t.text) in positive]

    def _snippet(self, doc: Document, positive: Set[str], width: int = 230) -> List[Dict]:
        text = doc.body or doc.title
        hits = self._hit_tokens(text, positive)

        start = 0
        if hits:
            best_i, best_count, j = 0, 0, 0
            for i, hit in enumerate(hits):
                j = max(j, i)
                while j < len(hits) and hits[j].start < hit.start + width - 40:
                    j += 1
                if j - i > best_count:
                    best_i, best_count = i, j - i
            start = max(0, hits[best_i].start - 40)
            if start > 0:
                space = text.rfind(" ", max(0, start - 20), start)
                if space != -1:
                    start = space + 1

        end = min(len(text), start + width)
        if end < len(text):
            space = text.find(" ", end, end + 20)
            if space != -1:
                end = space

        pieces = _segments(text, hits, start, end)
        if pieces and start > 0:
            pieces[0]["t"] = "…" + pieces[0]["t"]
        if pieces and end < len(text):
            pieces[-1]["t"] = pieces[-1]["t"] + "…"
        return pieces
