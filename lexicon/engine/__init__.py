"""Lexicon — a modular positional inverted-index search engine.

Pipeline (one module per stage):

    document_loader  ->  tokenizer  ->  normalizer  ->  inverted_index
                                                          |
                         search_engine  <-  query_processor  <-  wildcard
"""
from .search_engine import SearchEngine

__all__ = ["SearchEngine"]
