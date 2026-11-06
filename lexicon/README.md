# Lexicon

A small search engine built on a positional inverted index, with a web interface that shows what the index is doing. Upload your own documents, type a query, and a grid lights up to show which documents contain each term. Below it, every processing step is listed.

There are no built-in documents: the app starts empty and searches whatever you add (`.txt`, `.md`, `.pdf`, `.docx`).

It supports wildcards (`mon*`, `*tion`, `c?ral`), exact phrases, `AND` / `OR` / `NOT`, parentheses, Soundex (sound-alike) matching, spell check, TF-IDF ranking, highlighted snippets, autocomplete and a full-document reader.

## Project structure

```
lexicon/
├── engine/                     # the search engine, one module per stage
│   ├── document_loader.py      # 1. read .txt/.md/.pdf/.docx into Document objects
│   ├── tokenizer.py            # 2. text -> tokens (with positions and offsets)
│   ├── normalizer.py           # 3. case folding, accent stripping, possessives
│   ├── inverted_index.py       # 4. positional inverted index + TF-IDF
│   ├── query_processor.py      # 5. lexer, parser, boolean/phrase evaluation, ranking
│   ├── wildcard.py             # 6. permuterm index for * and ? patterns
│   ├── phonetic.py             #    American Soundex codes + code -> terms index
│   ├── spelling.py             #    edit-distance spell checker over the vocabulary
│   └── search_engine.py        #    facade that wires the modules together
├── frontend/                   # 7. the GUI (plain HTML, CSS and JavaScript)
│   ├── index.html              #    search box, Search button, read-only results
│   ├── styles.css
│   ├── app.js
│   └── favicon.svg
├── tests/test_engine.py        # engine unit tests
├── tests/test_app.py           # upload / search / session API tests
├── app.py                      # Flask server: upload API, per-visitor sessions, search API, frontend
├── requirements.txt  Procfile  render.yaml  Dockerfile
```

Data flows left to right:
`document_loader -> tokenizer -> normalizer -> inverted_index`, and a query goes
`query_processor -> wildcard -> inverted_index -> ranked results`.

## Run it locally

```bash
python -m venv .venv && source .venv/bin/activate     # optional
pip install -r requirements.txt
python app.py                                          # http://localhost:8000
```

Run the tests with `python -m unittest discover -s tests`.

## Adding documents

Use **Choose files** or drag files onto the panel at the top of the page. Supported types are `.txt`, `.md`, `.pdf` (with a text layer; scans are rejected) and `.docx`. The first line of each document is its title. Files are indexed immediately, you can click a document to read it, remove one with its **×**, or **Remove all**. Uploading a file with the same name replaces the old one.

Each visitor gets a private index, tied to a random cookie and kept in memory. Nobody else can see or search it, and it is discarded after two hours of inactivity or when the server restarts. Nothing is written to disk.

Limits (edit the constants at the top of `app.py`): 8 MB per file, 32 MB per upload, 100 documents and 20 million characters per session, 200 concurrent sessions.

## Publish it on the web

The app is a standard Python web service, so any host that runs Python works. All three options below are already configured in the repo.

**Render (free tier):** push the project to GitHub, then in Render choose *New > Blueprint* and select the repo. `render.yaml` does the rest.

**Railway, Heroku, Fly and similar:** the `Procfile` is used automatically (`gunicorn app:app --workers 1 --threads 8`).

**Docker:** `docker build -t lexicon . && docker run -p 8000:8000 lexicon`

Notes for production: sessions live in the memory of one process, so keep the gunicorn setting at `--workers 1` (threads are fine); with several workers an upload could land in a different worker from the next search. The server sends a strict Content-Security-Policy, and `/healthz` is available for health checks. The page loads two fonts from Google Fonts and falls back to system fonts if they are unavailable.

## Query syntax

| Query | Meaning |
|---|---|
| `moon tide` | both words (AND is the default) |
| `moon OR monsoon` | either word |
| `moon NOT tide` or `moon -tide` | moon without tide |
| `"inverted index"` | exact phrase, using term positions |
| `(tide OR monsoon) AND sea` | grouping; precedence is NOT, then AND, then OR |
| `mon*`, `*tion`, `h*o` | `*` matches any run of letters |
| `c?ral` | `?` matches exactly one letter |

### Search type buttons

The buttons under the search box choose how the query is read. With none selected (the default) every syntax above works at once. Click the active button again to turn it off.

| Button | What it does |
|---|---|
| **Boolean** | Reads only `AND`, `OR`, `NOT` and parentheses. Quotes and `*` `?` are ignored. |
| **Phrase** | Everything typed is one exact phrase, e.g. `inverted index`. |
| **Wildcard** | Each word is a pattern (`mon*`, `c?ral`). Boolean operators are ignored, so words combine with AND. |
| **Soundex** | Each word matches indexed terms with the same Soundex code, so `monsoom` finds `monsoon`. Boolean operators still work. |
| **Spell check** | Compares each word with the index vocabulary and offers clickable corrections (edit distance, then document frequency). It also runs automatically when a search returns nothing. |

The chosen type is kept in the URL (`?q=monsoom&mode=soundex`) so a search can be shared.

Case, accents and possessives are ignored. The parser is forgiving: a dangling operator or unbalanced parenthesis is ignored rather than raising an error.

## How the main pieces work

- **Tokenizer** returns each token with its position (for phrase queries) and character offsets (for highlighting).
- **Normalizer** folds case (`Straße` becomes `strasse`), strips accents from Latin letters only (so Devanagari and other scripts are left intact) and removes `'s`. Stop-word removal exists but is off by default so phrases stay exact.
- **Inverted index** maps `term -> {doc_id: [positions]}`. Ranking is `(1 + log10 tf) * log10(1 + N/df)` summed over matching terms, divided by the square root of document length.
- **Soundex** keeps the first letter and encodes the following consonant sounds as three digits (`smith` and `smyth` are both `S530`). A `code -> terms` index is built at startup; terms that are not plain ASCII words get no code and match exactly.
- **Spell check** suggests only words that exist in the index. Candidates within a length-dependent edit distance (adjacent swaps count as one edit) are ordered by distance, then document frequency.
- **Wildcards** use a permuterm index: every term is stored under all rotations of `term$`, so a pattern like `h*o` becomes a prefix lookup for `o$h`. A regex then confirms each candidate, which also handles `?` and multiple stars.

## API

| Endpoint | Returns |
|---|---|
| `GET /api/search?q=...&limit=20` | ranked hits with snippets, plus a trace of every processing step |
| `GET /api/search?q=...&mode=soundex` | same, read as `auto` (default), `boolean`, `phrase`, `wildcard` or `soundex` |
| `GET /api/spell?q=coarl+reff` | per-word spelling suggestions and a fully corrected query |
| `GET /api/suggest?q=ind` | vocabulary terms with that prefix |
| `GET /api/doc/<id>?q=...&mode=...` | full document text with matched terms marked |
| `POST /api/documents` | multipart upload (field `files`, repeatable); returns `added`, `rejected` and new stats |
| `DELETE /api/documents/<id>` | remove one document |
| `DELETE /api/documents` | remove all documents |
| `GET /api/stats` | index statistics, document titles and upload limits |
| `GET /healthz` | health check |

All search endpoints use the caller's own documents (identified by the session cookie).
