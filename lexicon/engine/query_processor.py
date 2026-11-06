"""Module 5 — Query processing.

Query language
--------------
    moon tide            implicit AND
    moon OR monsoon      OR
    moon NOT tide        NOT   (also  -tide)
    "inverted index"     exact phrase (uses term positions)
    (a OR b) AND c       parentheses;  precedence  NOT > AND > OR
    mon*  *tion  c?ral   wildcards (expanded by the permuterm index)

Search modes (the buttons under the search box) narrow what is understood:

    auto       everything above (default)
    boolean    AND / OR / NOT and parentheses only; quotes and * ? are ignored
    phrase     the whole query is one exact phrase
    wildcard   every word is a pattern; boolean operators are ignored
    soundex    every word matches terms that sound alike (smith ~ smyth)

Pipeline:  lex -> parse (AST) -> evaluate with set algebra on postings
           -> rank matches by TF-IDF.

The parser is deliberately forgiving: dangling operators, stray or missing
parentheses and punctuation-only terms are ignored instead of raising errors.
Every stage is recorded in ``QueryResult.trace`` so the GUI can show it.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple, Union

from .inverted_index import InvertedIndex
from .normalizer import Normalizer
from .tokenizer import tokenize
from .phonetic import SoundexIndex, soundex
from .wildcard import PermutermIndex, is_wildcard

# --------------------------------------------------------------------------
# Lexer
# --------------------------------------------------------------------------
_LEXER = re.compile(r'"[^"]*"?|[()]|[^\s()"]+')
_OPERATORS = ("AND", "OR", "NOT")
MODES = ("auto", "boolean", "phrase", "wildcard", "soundex")
_QUOTES = re.compile(r'"')
_QUOTES_AND_WILDCARDS = re.compile(r'["*?]')


@dataclass(frozen=True)
class Lexeme:
    text: str
    kind: str  # "term" | "phrase" | "op" | "paren"


def lex(query: str) -> List[Lexeme]:
    out: List[Lexeme] = []
    for m in _LEXER.finditer(query):
        s = m.group()
        if s.startswith('"'):
            inner = s[1:-1] if len(s) > 1 and s.endswith('"') else s[1:]
            out.append(Lexeme(inner, "phrase"))
        elif s in ("(", ")"):
            out.append(Lexeme(s, "paren"))
        elif s in _OPERATORS:
            out.append(Lexeme(s, "op"))
        elif s == "-" and m.end() < len(query) and query[m.end()] in '"(':
            out.append(Lexeme("NOT", "op"))
        elif s.startswith("-") and len(s) > 1:
            out.append(Lexeme("NOT", "op"))
            out.append(Lexeme(s[1:], "term"))
        else:
            out.append(Lexeme(s, "term"))
    return out


# --------------------------------------------------------------------------
# AST
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Term:
    pattern: str
    wildcard: bool = False
    soundex: bool = False


@dataclass(frozen=True)
class Phrase:
    words: Tuple[str, ...]


@dataclass(frozen=True)
class Not:
    child: "Node"


@dataclass(frozen=True)
class And:
    children: Tuple["Node", ...]


@dataclass(frozen=True)
class Or:
    children: Tuple["Node", ...]


Node = Union[Term, Phrase, Not, And, Or]


def _combine(kind, left: Optional[Node], right: Optional[Node]) -> Optional[Node]:
    if left is None:
        return right
    if right is None:
        return left
    children: List[Node] = []
    for node in (left, right):
        if isinstance(node, kind):
            children.extend(node.children)
        else:
            children.append(node)
    return kind(tuple(children))


def render(node: Optional[Node], top: bool = True) -> str:
    if node is None:
        return ""
    if isinstance(node, Term):
        return node.pattern
    if isinstance(node, Phrase):
        return '"' + " ".join(node.words) + '"'
    if isinstance(node, Not):
        return "NOT " + render(node.child, False)
    joiner = " AND " if isinstance(node, And) else " OR "
    text = joiner.join(render(c, False) for c in node.children)
    return text if top else f"({text})"


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------
@dataclass
class QueryResult:
    ranked: List[Tuple[int, float]]   # (doc_id, score), best first
    positive_terms: Set[str]          # index terms that justify a match
    trace: Dict


class _Context:
    """Collects what happened while a query ran (for the GUI trace)."""

    def __init__(self) -> None:
        self.positive: Set[str] = set()
        self.lookups: Dict[str, Tuple[int, List[int], bool]] = {}
        self.expansions: List[Dict] = []
        self.normalized: List[Dict] = []


# --------------------------------------------------------------------------
# Parser (recursive descent, forgiving)
# --------------------------------------------------------------------------
class _Parser:
    def __init__(self, lexemes: List[Lexeme], normalizer: Normalizer, ctx: _Context,
                 mode: str = "auto"):
        self.lexemes = lexemes
        self.normalizer = normalizer
        self.ctx = ctx
        self.mode = mode
        self.pos = 0

    def _peek(self) -> Optional[Lexeme]:
        return self.lexemes[self.pos] if self.pos < len(self.lexemes) else None

    def _is(self, lx: Optional[Lexeme], kind: str, text: str) -> bool:
        return lx is not None and lx.kind == kind and lx.text == text

    def parse(self) -> Optional[Node]:
        node: Optional[Node] = None
        while self.pos < len(self.lexemes):
            node = _combine(And, node, self._or())
            if self._is(self._peek(), "paren", ")"):
                self.pos += 1  # stray ")"
        return node

    def _or(self) -> Optional[Node]:
        left = self._and()
        while self._is(self._peek(), "op", "OR"):
            self.pos += 1
            left = _combine(Or, left, self._and())
        return left

    def _and(self) -> Optional[Node]:
        node: Optional[Node] = None
        while True:
            lx = self._peek()
            if lx is None or self._is(lx, "op", "OR") or self._is(lx, "paren", ")"):
                break
            if self._is(lx, "op", "AND"):
                self.pos += 1
                continue
            node = _combine(And, node, self._unary())
        return node

    def _unary(self) -> Optional[Node]:
        if self._is(self._peek(), "op", "NOT"):
            self.pos += 1
            nxt = self._peek()
            if (nxt is None or self._is(nxt, "paren", ")")
                    or (nxt.kind == "op" and nxt.text in ("AND", "OR"))):
                return None
            child = self._unary()
            return Not(child) if child is not None else None
        return self._primary()

    def _primary(self) -> Optional[Node]:
        lx = self.lexemes[self.pos]
        self.pos += 1
        if lx.kind == "paren" and lx.text == "(":
            node = self._or()
            if self._is(self._peek(), "paren", ")"):
                self.pos += 1
            return node
        if lx.kind == "phrase":
            return self._resolve(lx.text, quoted=True)
        if lx.kind == "term":
            return self._resolve(lx.text, quoted=False)
        return None

    def _resolve(self, raw: str, quoted: bool) -> Optional[Node]:
        if not quoted and is_wildcard(raw):
            pattern = self.normalizer.normalize_pattern(raw)
            if not pattern:
                return None
            self.ctx.normalized.append({"from": raw, "to": pattern})
            return Term(pattern, True)

        words = [w for _, w in self.normalizer.normalize_tokens(tokenize(raw))]
        if not words:
            return None
        joined = " ".join(words)
        if quoted:
            self.ctx.normalized.append({"from": f'"{raw}"', "to": f'"{joined}"'})
        else:
            self.ctx.normalized.append({"from": raw, "to": joined})
        if len(words) == 1:
            return Term(words[0], soundex=self.mode == "soundex" and not quoted)
        return Phrase(tuple(words))


# --------------------------------------------------------------------------
# Processor
# --------------------------------------------------------------------------
class QueryProcessor:
    def __init__(self, index: InvertedIndex, wildcards: PermutermIndex,
                 normalizer: Normalizer, max_expansions: int = 64,
                 phonetics: Optional[SoundexIndex] = None):
        self.index = index
        self.wildcards = wildcards
        self.phonetics = phonetics or SoundexIndex(index.vocabulary())
        self.normalizer = normalizer
        self.max_expansions = max_expansions

    @staticmethod
    def _lexemes_for(query: str, mode: str) -> List[Lexeme]:
        """Lex *query* the way the chosen search mode understands it."""
        if mode == "boolean":
            return lex(_QUOTES_AND_WILDCARDS.sub(" ", query))
        if mode == "soundex":
            return lex(_QUOTES_AND_WILDCARDS.sub(" ", query))
        if mode == "wildcard":
            return [lx for lx in lex(_QUOTES.sub(" ", query)) if lx.kind == "term"]
        if mode == "phrase":
            text = _QUOTES.sub(" ", query).strip()
            return [Lexeme(text, "phrase")] if text else []
        return lex(query)

    def process(self, query: str, mode: str = "auto") -> QueryResult:
        if mode not in MODES:
            mode = "auto"
        ctx = _Context()
        lexemes = self._lexemes_for(query, mode)
        ast = _Parser(lexemes, self.normalizer, ctx, mode).parse()

        matched: Set[int] = set()
        if ast is not None:
            matched = self._eval(ast, False, ctx)

        ranked = self._rank(matched, ctx.positive)
        trace = {
            "mode": mode,
            "lexemes": [{"text": l.text, "kind": l.kind} for l in lexemes],
            "ast": render(ast),
            "normalized": ctx.normalized,
            "expansions": ctx.expansions,
            "lookups": [
                {"term": t, "df": df, "docs": docs, "negated": negated}
                for t, (df, docs, negated) in ctx.lookups.items()
            ],
        }
        return QueryResult(ranked, ctx.positive, trace)

    # ---- evaluation ---------------------------------------------------
    def _lookup(self, term: str, ctx: _Context, negated: bool) -> Set[int]:
        docs = self.index.doc_ids(term)
        previous = ctx.lookups.get(term)
        only_negated = negated and (previous[2] if previous else True)
        ctx.lookups[term] = (len(docs), sorted(docs), only_negated)
        return docs

    def _eval(self, node: Node, negated: bool, ctx: _Context) -> Set[int]:
        if isinstance(node, Term):
            if node.wildcard:
                matches = self.wildcards.expand(node.pattern)
                terms = matches[: self.max_expansions]
                ctx.expansions.append({
                    "kind": "wildcard",
                    "pattern": node.pattern,
                    "matches": terms,
                    "total": len(matches),
                    "truncated": len(matches) > len(terms),
                })
            elif node.soundex:
                matches = self.phonetics.expand(node.pattern)
                terms = matches[: self.max_expansions]
                if not terms:
                    terms = [node.pattern]   # no sound-alike: fall back to exact
                ctx.expansions.append({
                    "kind": "soundex",
                    "pattern": node.pattern,
                    "code": soundex(node.pattern),
                    "matches": matches[: self.max_expansions],
                    "total": len(matches),
                    "truncated": len(matches) > self.max_expansions,
                })
            else:
                terms = [node.pattern]
            docs: Set[int] = set()
            for term in terms:
                docs |= self._lookup(term, ctx, negated)
            if not negated:
                ctx.positive.update(terms)
            return docs

        if isinstance(node, Phrase):
            for word in node.words:
                self._lookup(word, ctx, negated)
            if not negated:
                ctx.positive.update(node.words)
            return self._phrase_docs(node.words)

        if isinstance(node, Not):
            universe = set(self.index.all_doc_ids())
            return universe - self._eval(node.child, not negated, ctx)

        sets = [self._eval(child, negated, ctx) for child in node.children]
        if isinstance(node, And):
            return set.intersection(*sets)
        return set.union(*sets)

    def _phrase_docs(self, words: Tuple[str, ...]) -> Set[int]:
        postings = [self.index.postings(w) for w in words]
        if any(not p for p in postings):
            return set()
        candidates = set(postings[0]).intersection(*postings[1:])
        found: Set[int] = set()
        for doc in candidates:
            starts = set(postings[0][doc])
            for offset in range(1, len(words)):
                starts &= {p - offset for p in postings[offset][doc]}
                if not starts:
                    break
            if starts:
                found.add(doc)
        return found

    # ---- ranking ------------------------------------------------------
    def _rank(self, matched: Set[int], positive: Set[str]) -> List[Tuple[int, float]]:
        scored = []
        for doc in matched:
            raw = sum(self.index.tfidf(t, doc) for t in positive)
            score = raw / math.sqrt(max(1, self.index.doc_length(doc)))
            scored.append((doc, score))
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored
