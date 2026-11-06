import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import SearchEngine
import tempfile
from engine.document_loader import (UnsupportedFile, documents_from_texts, extract_text,
                                    load_directory, load_texts, split_title)
from engine.inverted_index import build_index
from engine.phonetic import SoundexIndex, soundex
from engine.spelling import SpellChecker, edit_distance
from engine.normalizer import Normalizer, strip_accents
from engine.tokenizer import tokenize
from engine.wildcard import PermutermIndex, lookup_prefix

CORPUS = {
    "a": "Moon and tides\nThe moon pulls the tides. Monsoon rain follows the moon.",
    "b": "Bees\nBees make honey. The beehive hums; beeswax is useful.",
    "c": "Coral\nCoral reefs shelter fish. Café owners love coral art, the moon too.",
    "d": "Inverted index\nAn inverted index maps each term to documents. Index the index!",
}


def make_engine():
    return SearchEngine(load_texts(CORPUS))


class LoaderTests(unittest.TestCase):
    def test_split_title(self):
        self.assertEqual(split_title("# Hello\n\nBody here", "x"), ("Hello", "Body here"))
        self.assertEqual(split_title("   ", "fallback"), ("fallback", ""))

    def test_load_directory_ids_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "b.txt").write_text("Beta\nsecond", encoding="utf-8")
            (Path(tmp) / "a.md").write_text("# Alpha\nfirst", encoding="utf-8")
            (Path(tmp) / "skip.bin").write_text("nope", encoding="utf-8")
            docs = load_directory(tmp)
        self.assertEqual([d.source for d in docs], ["a.md", "b.txt"])
        self.assertEqual([d.doc_id for d in docs], [0, 1])

    def test_missing_directory(self):
        with self.assertRaises(FileNotFoundError):
            load_directory("/definitely/not/here")


class UploadParsingTests(unittest.TestCase):
    def test_text_and_markdown(self):
        self.assertEqual(extract_text("n.txt", b"\xef\xbb\xbfHi there"), "Hi there")
        self.assertEqual(extract_text("N.MD", "# T\ncafé".encode()), "# T\ncafé")

    def test_docx(self):
        import io, zipfile
        xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
               '<w:p><w:r><w:t>Title line</w:t></w:r></w:p>'
               '<w:p><w:r><w:t>Hello</w:t></w:r><w:r><w:t xml:space="preserve"> world</w:t></w:r></w:p>'
               '</w:body></w:document>')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml", xml)
        self.assertEqual(extract_text("a.docx", buf.getvalue()), "Title line\n\nHello world")

    def test_bad_files(self):
        with self.assertRaises(UnsupportedFile):
            extract_text("a.docx", b"not a zip")
        with self.assertRaises(UnsupportedFile):
            extract_text("a.pdf", b"not a pdf")
        with self.assertRaises(UnsupportedFile):
            extract_text("a.exe", b"MZ")

    def test_documents_from_texts_titles_and_ids(self):
        docs = documents_from_texts([("my-notes.txt", "First line\nBody"), ("x.txt", "   \n")])
        self.assertEqual(docs[0].title, "First line")
        self.assertEqual([d.doc_id for d in docs], [0, 1])
        self.assertEqual(docs[1].title, "X")


class EmptyEngineTests(unittest.TestCase):
    def test_empty_corpus_is_safe(self):
        e = SearchEngine([])
        for q in ["moon", "mo*", '"a b"', "a OR b NOT c", "", "(("]:
            for mode in ["auto", "boolean", "phrase", "wildcard", "soundex"]:
                self.assertEqual(e.search(q, 10, mode)["total"], 0)
        self.assertEqual(e.suggest("mo"), [])
        self.assertEqual(e.stats()["documents"], 0)
        self.assertIsNone(e.read_document(0))


class TokenizerNormalizerTests(unittest.TestCase):
    def test_positions_and_offsets(self):
        text = "Hello, don't stop-now"
        toks = tokenize(text)
        self.assertEqual([t.text for t in toks], ["Hello", "don't", "stop", "now"])
        self.assertEqual([t.position for t in toks], [0, 1, 2, 3])
        self.assertEqual(text[toks[1].start:toks[1].end], "don't")

    def test_case_folding_accents_possessive(self):
        n = Normalizer()
        self.assertEqual(n.normalize("MOON's"), "moon")
        self.assertEqual(n.normalize("Café"), "cafe")
        self.assertEqual(n.normalize("Straße"), "strasse")
        self.assertEqual(n.normalize("don’t"), "don't")

    def test_non_latin_marks_preserved(self):
        self.assertEqual(strip_accents("हिन्दी"), "हिन्दी")

    def test_stopwords_optional(self):
        self.assertEqual(Normalizer().normalize("the"), "the")
        self.assertEqual(Normalizer(remove_stopwords=True).normalize("the"), "")

    def test_pattern_normalisation(self):
        self.assertEqual(Normalizer().normalize_pattern("MON**"), "mon*")


class IndexTests(unittest.TestCase):
    def test_postings_positions_and_stats(self):
        docs = load_texts({"x": "Cat\ncat dog cat"})
        idx = build_index(docs, tokenize, Normalizer())
        self.assertEqual(idx.postings("cat"), {0: [0, 1, 3]})
        self.assertEqual(idx.df("dog"), 1)
        self.assertEqual(idx.tf("cat", 0), 3)
        self.assertEqual(idx.doc_length(0), 4)
        self.assertEqual(idx.vocabulary(), ["cat", "dog"])


class WildcardTests(unittest.TestCase):
    vocab = ["hello", "help", "hell", "shell", "bee", "bees", "beeswax", "coral", "choral", "moral"]

    def setUp(self):
        self.p = PermutermIndex(self.vocab)

    def test_prefix(self):
        self.assertEqual(self.p.expand("hel*"), ["hell", "hello", "help"])

    def test_suffix(self):
        self.assertEqual(self.p.expand("*ral"), ["choral", "coral", "moral"])

    def test_middle(self):
        self.assertEqual(self.p.expand("h*o"), ["hello"])

    def test_single_char(self):
        self.assertEqual(self.p.expand("c?ral"), ["coral"])
        self.assertEqual(self.p.expand("?ell"), ["hell"])

    def test_multiple_stars_and_all(self):
        self.assertEqual(self.p.expand("*e*s"), ["bees"])
        self.assertEqual(self.p.expand("*e*l"), ["hell", "shell"])
        self.assertEqual(len(self.p.expand("*")), len(self.vocab))

    def test_no_match_and_plain(self):
        self.assertEqual(self.p.expand("zz*"), [])
        self.assertEqual(self.p.expand("bee"), ["bee"])
        self.assertEqual(self.p.expand("nope"), [])

    def test_lookup_prefix(self):
        self.assertEqual(lookup_prefix("hel*o"), "o$hel")
        self.assertEqual(lookup_prefix("*ing"), "ing$")
        self.assertEqual(lookup_prefix("hel*"), "$hel")


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.e = make_engine()

    def docs(self, q):
        return sorted(h["source"] for h in self.e.search(q)["hits"])

    def test_implicit_and(self):
        self.assertEqual(self.docs("moon coral"), ["c"])

    def test_or(self):
        self.assertEqual(self.docs("honey OR coral"), ["b", "c"])

    def test_not(self):
        self.assertEqual(self.docs("moon NOT coral"), ["a"])
        self.assertEqual(self.docs("moon -coral"), ["a"])

    def test_precedence_and_parens(self):
        self.assertEqual(self.docs("honey OR moon AND coral"), ["b", "c"])
        self.assertEqual(self.docs("(honey OR moon) AND coral"), ["c"])

    def test_phrase(self):
        self.assertEqual(self.docs('"inverted index"'), ["d"])
        self.assertEqual(self.docs('"index inverted"'), [])
        self.assertEqual(self.docs('"the moon"'), ["a", "c"])

    def test_wildcards_in_queries(self):
        self.assertEqual(self.docs("mon*"), ["a"])
        self.assertEqual(self.docs("bee*"), ["b"])
        self.assertEqual(self.docs("c?ral"), ["c"])
        self.assertEqual(self.docs("bee* AND NOT honey"), [])

    def test_case_and_accent_insensitive(self):
        self.assertEqual(self.docs("CAFE"), ["c"])
        self.assertEqual(self.docs("MOON'S"), ["a", "c"])

    def test_forgiving_parser(self):
        self.assertEqual(self.docs("moon AND"), ["a", "c"])
        self.assertEqual(self.docs("(moon"), ["a", "c"])
        self.assertEqual(self.docs("moon)"), ["a", "c"])
        self.assertEqual(self.docs("&& moon !!"), ["a", "c"])
        self.assertEqual(self.docs(""), [])
        self.assertEqual(self.docs("   "), [])

    def test_ranking_prefers_more_occurrences(self):
        hits = self.e.search("index")["hits"]
        self.assertEqual(hits[0]["source"], "d")
        self.assertEqual(hits[0]["relevance"], 1.0)

    def test_trace_contents(self):
        t = self.e.search("Mon* AND coral")["trace"]
        self.assertEqual(t["ast"], "mon* AND coral")
        self.assertEqual(t["expansions"][0]["pattern"], "mon*")
        self.assertIn("monsoon", t["expansions"][0]["matches"])
        terms = {row["term"]: row for row in t["lookups"]}
        self.assertEqual(terms["coral"]["df"], 1)
        self.assertEqual(terms["coral"]["docs"], [2])
        self.assertFalse(terms["coral"]["negated"])

    def test_trace_marks_negated_terms(self):
        rows = {r["term"]: r for r in self.e.search("moon NOT coral")["trace"]["lookups"]}
        self.assertTrue(rows["coral"]["negated"])
        self.assertFalse(rows["moon"]["negated"])


class EngineTests(unittest.TestCase):
    def test_snippet_highlights_match(self):
        e = make_engine()
        hit = e.search("honey")["hits"][0]
        marked = [s["t"] for s in hit["snippet"] if s["h"]]
        self.assertEqual(marked, ["honey"])

    def test_suggest_orders_by_frequency(self):
        e = make_engine()
        self.assertEqual(e.suggest("ind")[0]["term"], "index")
        self.assertEqual(e.suggest("zzz"), [])

    def test_read_document_has_paragraphs(self):
        e = make_engine()
        doc = e.read_document(0, "moon")
        self.assertTrue(doc["paragraphs"])
        self.assertTrue(any(s["h"] for s in doc["paragraphs"][0]))
        self.assertIsNone(e.read_document(99))

    def test_from_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "one.txt").write_text("Monsoon\nRain over the sea.", encoding="utf-8")
            e = SearchEngine.from_directory(tmp)
        s = e.stats()
        self.assertEqual(s["documents"], len(s["titles"]))
        self.assertEqual(s["sources"], ["one.txt"])
        self.assertEqual(e.search("mon*")["total"], 1)


def ids(result):
    return sorted(h["doc_id"] for h in result["hits"])


class PhoneticTests(unittest.TestCase):
    def test_reference_codes(self):
        for word, code in [("Robert", "R163"), ("Rupert", "R163"), ("Ashcraft", "A261"),
                           ("Tymczak", "T522"), ("Pfister", "P236"), ("Lee", "L000")]:
            self.assertEqual(soundex(word), code, word)

    def test_non_ascii_and_digits_have_no_code(self):
        self.assertEqual(soundex("हिन्दी"), "")
        self.assertEqual(soundex("abc123"), "")
        self.assertEqual(soundex(""), "")

    def test_index_expand(self):
        idx = SoundexIndex(["smith", "smyth", "moon"])
        self.assertEqual(idx.expand("smythe"), ["smith", "smyth"])
        self.assertEqual(idx.expand("zzzz"), [])


class SpellingTests(unittest.TestCase):
    def test_edit_distance(self):
        self.assertEqual(edit_distance("coral", "coral"), 0)
        self.assertEqual(edit_distance("coarl", "coral"), 1)      # swap
        self.assertEqual(edit_distance("tide", "tides"), 1)
        self.assertEqual(edit_distance("abcdef", "uvwxyz", 2), 3)  # gave up early

    def test_suggestions_rank_by_distance_then_frequency(self):
        sp = SpellChecker({"coral": 3, "choral": 9, "moon": 5})
        self.assertEqual([x["term"] for x in sp.suggest("coarl")][:1], ["coral"])
        self.assertEqual(sp.suggest("moon"), [])   # known word
        self.assertEqual(sp.suggest("qqqqqq"), [])

    def test_engine_spellcheck_corrects_query(self):
        e = make_engine()
        r = e.spellcheck("moom AND cafe")
        self.assertFalse(r["ok"])
        self.assertEqual(r["words"][0]["suggestions"][0]["term"], "moon")
        self.assertEqual(r["corrected"], "moon AND cafe")
        clean = e.spellcheck("moon tides")
        self.assertTrue(clean["ok"])
        self.assertIsNone(clean["corrected"])

    def test_spellcheck_skips_operators_and_wildcards(self):
        e = make_engine()
        r = e.spellcheck("mo* OR xqzv? NOT moon")
        self.assertEqual([w["word"] for w in r["words"]], ["moon"])


class SearchModeTests(unittest.TestCase):
    def setUp(self):
        self.e = make_engine()

    def test_default_mode_is_auto(self):
        self.assertEqual(self.e.search("moon")["mode"], "auto")
        self.assertEqual(self.e.search("moon", mode="bogus")["mode"], "auto")

    def test_boolean_ignores_wildcards_and_quotes(self):
        r = self.e.search("moon AND NOT coral", mode="boolean")
        self.assertEqual(r["total"], 1)
        self.assertEqual(self.e.search("moo*", mode="boolean")["total"], 0)  # "moo" is not a term
        self.assertEqual(self.e.search('"moon"', mode="boolean")["total"],
                         self.e.search("moon")["total"])

    def test_phrase_mode_is_one_exact_phrase(self):
        self.assertEqual(ids(self.e.search("inverted index", mode="phrase")), [3])
        self.assertEqual(self.e.search("index inverted", mode="phrase")["total"], 0)
        # operators are literal words inside a phrase
        self.assertEqual(self.e.search("tides AND moon", mode="phrase")["total"], 0)
        self.assertEqual(ids(self.e.search("moon AND tides", mode="phrase")), [0])  # literally "moon and tides"

    def test_wildcard_mode_ignores_operators(self):
        r = self.e.search("mon* OR bee*", mode="wildcard")   # OR dropped -> implicit AND
        self.assertEqual(r["total"], 0)
        self.assertEqual(self.e.search("mon*", mode="wildcard")["total"], 1)
        self.assertEqual(self.e.search("c?ral", mode="wildcard")["total"], 1)

    def test_soundex_mode_finds_sound_alikes(self):
        self.assertEqual(self.e.search("monsoom", mode="auto")["total"], 0)
        r = self.e.search("monsoom", mode="soundex")
        self.assertEqual(ids(r), [0])
        exp = r["trace"]["expansions"][0]
        self.assertEqual((exp["kind"], exp["code"]), ("soundex", soundex("monsoom")))
        self.assertIn("monsoon", exp["matches"])
        # highlighted term is the real indexed word
        marked = [s["t"] for s in r["hits"][0]["snippet"] if s["h"]]
        self.assertIn("Monsoon", marked)

    def test_soundex_mode_still_supports_boolean(self):
        r = self.e.search("monsoom AND NOT coral", mode="soundex")
        self.assertEqual(ids(r), [0])
        self.assertEqual(self.e.search("monsoom AND bees", mode="soundex")["total"], 0)

    def test_soundex_without_alike_falls_back_to_exact(self):
        self.assertEqual(self.e.search("zzzz", mode="soundex")["total"], 0)

    def test_reader_highlights_in_mode(self):
        doc = self.e.read_document(0, "monsoom", "soundex")
        self.assertTrue(any(s["h"] for p in doc["paragraphs"] for s in p))


if __name__ == "__main__":
    unittest.main()
