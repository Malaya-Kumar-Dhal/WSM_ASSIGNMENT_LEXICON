(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const MAX_ROWS = 14;
  const THEME_KEY = "lexicon-theme";

  /** Tiny hyperscript helper. Builds DOM nodes without innerHTML, so
   *  document text and query text can never inject markup. */
  function h(tag, props = {}, ...kids) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value === false || value == null) continue;
      if (key === "class") el.className = value;
      else if (key === "dataset") Object.assign(el.dataset, value);
      else el.setAttribute(key, value === true ? "" : value);
    }
    for (const kid of kids.flat()) {
      if (kid == null || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  const els = {
    form: $("#search-form"),
    input: $("#q"),
    btn: $("#search-btn"),
    suggest: $("#suggest"),
    grid: $("#grid"),
    caption: $("#caption"),
    count: $("#count"),
    cards: $("#cards"),
    plain: $("#plain"),
    viewCards: $("#view-cards"),
    viewText: $("#view-text"),
    process: $("#process"),
    out: {
      parse: $("#out-parse"),
      tokenize: $("#out-tokenize"),
      fold: $("#out-fold"),
      wild: $("#out-wild"),
      post: $("#out-post"),
    },
    tools: [...document.querySelectorAll(".tool[data-mode]")],
    modeHint: $("#mode-hint"),
    spellBtn: $("#spell-btn"),
    spell: $("#spell"),
    theme: $("#theme"),
    docCount: $("#doc-count"),
    reader: $("#reader"),
    readerTitle: $("#reader-title"),
    readerMeta: $("#reader-meta"),
    readerBody: $("#reader-body"),
    readerClose: $("#reader-close"),
    drop: $("#drop"),
    pick: $("#pick"),
    fileInput: $("#file-input"),
    clearDocs: $("#clear-docs"),
    upStatus: $("#upload-status"),
    docList: $("#doclist"),
  };

  const state = { titles: [], sources: [], words: [], query: "", mode: "auto", data: null, reqId: 0, controller: null, caption: "", busy: false };
  const IDLE_CAPTION = "Add documents and run a search to fill the grid. Each column is one document.";

  const MODES = {
    auto: {
      hint: "All query types work together. Pick one above to search that way only.",
      placeholder: 'Try a word, "an exact phrase" or wor*',
    },
    boolean: {
      hint: "Boolean: combine words with AND, OR, NOT and parentheses, e.g. (cats OR dogs) AND home.",
      placeholder: "Try cats AND NOT dogs",
    },
    phrase: {
      hint: "Phrase: everything you type is one exact phrase, words side by side and in order.",
      placeholder: "Try an exact phrase",
    },
    wildcard: {
      hint: "Wildcard: * matches any run of letters and ? matches exactly one, e.g. wor* or c?t.",
      placeholder: "Try wor* or *tion or c?t",
    },
    soundex: {
      hint: "Soundex: finds words that sound like yours, so a misspelling such as monsoom still finds monsoon.",
      placeholder: "Try a word spelled how it sounds",
    },
  };

  function setMode(mode) {
    state.mode = MODES[mode] ? mode : "auto";
    els.tools.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === state.mode)));
    els.modeHint.textContent = MODES[state.mode].hint;
    els.input.placeholder = state.titles.length ? MODES[state.mode].placeholder : "Add documents to start searching";
  }

  /* ---------------------------------------------------------------- theme */
  function applyTheme(theme, save) {
    document.documentElement.dataset.theme = theme;
    const next = theme === "dark" ? "light" : "dark";
    els.theme.textContent = next === "dark" ? "Dark mode" : "Light mode";
    els.theme.setAttribute("aria-label", `Switch to ${next} mode`);
    if (save) {
      try { localStorage.setItem(THEME_KEY, theme); } catch { /* storage unavailable */ }
    }
  }
  (function initTheme() {
    let saved = null;
    try { saved = localStorage.getItem(THEME_KEY); } catch { /* ignore */ }
    const system = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    applyTheme(saved === "dark" || saved === "light" ? saved : system, false);
  })();
  els.theme.addEventListener("click", () => {
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark", true);
  });

  /* ----------------------------------------------------------------- grid */
  function setCaption(text) {
    state.caption = text;
    els.caption.textContent = text;
  }

  function clearHot() {
    els.grid.querySelectorAll(".hot").forEach((c) => c.classList.remove("hot"));
  }
  function setHot(col) {
    clearHot();
    if (col == null) return;
    els.grid.querySelectorAll(`.cell[data-col="${col}"]`).forEach((c) => c.classList.add("hot"));
  }

  function makeRow(kind, labelNode, cellOn, rowIndex, extraClass = "") {
    const row = h("div", { class: `row ${kind} ${extraClass}`.trim() }, labelNode);
    const cols = state.titles.length;
    for (let c = 0; c < cols; c++) {
      const on = cellOn(c);
      const cell = h("div", { class: on ? "cell on" : "cell", dataset: { col: c } });
      cell.style.setProperty("--d", c + rowIndex * 2);
      row.append(cell);
    }
    return row;
  }

  function renderIdleGrid() {
    els.grid.style.setProperty("--cols", state.titles.length || 12);
    els.grid.className = "grid idle";
    els.grid.replaceChildren(
      ...[0, 1, 2, 3].map((r) =>
        makeRow("ghost-row", h("div", { class: "label" }, h("span", { class: "t" })), () => false, r)
      )
    );
    setCaption(state.titles.length ? IDLE_CAPTION : "Add documents to get started. Each column will be one document.");
  }

  function renderGrid(trace, total) {
    const cols = state.titles.length;
    els.grid.style.setProperty("--cols", cols);

    const wildTerms = new Set(trace.expansions.flatMap((e) => e.matches));
    const shown = trace.lookups.slice(0, MAX_ROWS);
    const rows = shown.map((lk, r) => {
      const docs = new Set(lk.docs);
      const kind = lk.negated ? "neg" : wildTerms.has(lk.term) ? "wild" : "term";
      const label = h("div", { class: "label" },
        h("span", { class: "t", title: lk.term }, lk.term),
        h("span", { class: "n" }, String(lk.df)));
      return makeRow(kind, label, (c) => docs.has(c), r);
    });

    const matched = new Set(trace.matched_docs);
    const resultLabel = h("div", { class: "label" },
      h("span", { class: "t" }, "Result"),
      h("span", { class: "n" }, String(total)));
    const resultRow = makeRow("result", resultLabel, (c) => matched.has(c), shown.length, "result");

    const parts = [...rows];
    if (trace.lookups.length > shown.length) {
      parts.push(h("p", { class: "more" },
        `Showing ${shown.length} of ${trace.lookups.length} terms. The result row counts all of them.`));
    }
    parts.push(h("div", { class: "divider" }), resultRow);

    els.grid.replaceChildren(...parts);
    els.grid.className = "grid";
    void els.grid.offsetWidth; // restart the sweep animation
    els.grid.classList.add("play");

    const t = trace.lookups.length;
    if (!t) {
      setCaption("Nothing to look up in that query.");
    } else {
      setCaption(
        `${t} ${t === 1 ? "term" : "terms"} looked up. ` +
        `${total} ${total === 1 ? "document matches" : "documents match"}.`
      );
    }
  }

  els.grid.addEventListener("pointerover", (e) => {
    const cell = e.target.closest(".cell");
    if (!cell) return;
    const col = Number(cell.dataset.col);
    setHot(col);
    els.caption.textContent = state.titles[col] || "";
  });
  els.grid.addEventListener("pointerleave", () => {
    clearHot();
    els.caption.textContent = state.caption;
  });
  els.grid.addEventListener("click", (e) => {
    const cell = e.target.closest(".row.result .cell.on");
    if (cell) openReader(Number(cell.dataset.col));
  });

  /* ---------------------------------------------------- processing steps */
  const muted = (text) => h("span", { class: "none" }, text);
  const queryChip = (term, cls, df) =>
    h("button", { type: "button", class: `term-chip ${cls || ""} ${df === 0 ? "zero" : ""}`.trim(), dataset: { q: term }, title: `Search for ${term}` },
      term, df != null ? h("i", {}, String(df)) : null);

  function renderSteps(trace) {
    const o = els.out;
    o.parse.replaceChildren(trace.ast ? h("code", { class: "ast" }, trace.ast) : muted("Nothing to parse."));

    o.tokenize.replaceChildren(
      ...(trace.lexemes.length
        ? trace.lexemes.map((l) =>
            h("span", { class: `lx lx-${l.kind}` }, l.kind === "phrase" ? `"${l.text}"` : l.text))
        : [muted("No tokens.")])
    );

    o.fold.replaceChildren(
      ...(trace.normalized.length
        ? trace.normalized.map((n) =>
            h("div", { class: "fold" },
              h("span", { class: "from" }, n.from), "→", h("span", { class: "to" }, n.to)))
        : [muted("Nothing to fold.")])
    );

    o.wild.replaceChildren(
      ...(trace.expansions.length
        ? trace.expansions.map((x) =>
            h("div", { class: "expansion" },
              h("code", {}, x.pattern,
                x.kind === "soundex" ? h("span", { class: "code" }, `Soundex ${x.code}`) : null),
              x.total === 0
                ? muted(x.kind === "soundex"
                    ? "No indexed term sounds like this word."
                    : "No indexed term fits this pattern.")
                : [
                    ...x.matches.map((m) => queryChip(m, x.kind === "soundex" ? "sound" : "wild")),
                    x.truncated ? muted(`and ${x.total - x.matches.length} more`) : null,
                  ]))
        : [muted("No wildcards or sound-alikes in this query.")])
    );

    const lookups = trace.lookups.slice(0, 40);
    o.post.replaceChildren(
      ...(lookups.length
        ? [
            ...lookups.map((lk) => queryChip(lk.term, lk.negated ? "neg" : "", lk.df)),
            trace.lookups.length > lookups.length
              ? muted(`and ${trace.lookups.length - lookups.length} more terms`) : null,
          ].filter(Boolean)
        : [muted("No terms to look up.")])
    );
    els.process.hidden = false;
  }

  /* -------------------------------------------------------------- results */
  const pluralize = (n, one, many) => `${n} ${n === 1 ? one : many}`;

  function entry(hit, rank) {
    const snippet = h("p", { class: "snippet" },
      hit.snippet.map((s) => (s.h ? h("mark", {}, s.t) : s.t)));

    const meter = hit.score > 0
      ? h("span", { class: "rel" },
          h("span", { class: "meter", "aria-hidden": "true" }, h("i")),
          `${Math.round(hit.relevance * 100)}% relevance`)
      : null;
    if (meter) meter.querySelector("i").style.width = `${Math.round(hit.relevance * 100)}%`;

    return h("li", { class: "entry", dataset: { doc: hit.doc_id } },
      h("span", { class: "rank", "aria-hidden": "true" }, String(rank)),
      h("div", {},
        h("h3", {}, h("button", { type: "button", class: "linklike", dataset: { open: hit.doc_id } }, hit.title)),
        h("div", { class: "meta" }, h("span", {}, hit.source), meter),
        snippet,
        hit.matched.length
          ? h("div", { class: "tags" },
              h("span", {}, "Matched terms:"),
              hit.matched.map((t) => h("button", { type: "button", class: "linklike", dataset: { q: t } }, t)))
          : null));
  }

  function emptyState(query) {
    return h("li", { class: "notice" },
      h("p", {}, `Nothing matched “${query}”. Try fewer words, a wildcard such as ${(query.split(/\s+/)[0] || "word").slice(0, 3)}*, or join terms with OR.`));
  }

  function toPlainText(data) {
    const rule = "-".repeat(64);
    const lines = [
      `Lexicon: ${pluralize(data.total, "result", "results")} for: ${data.query}   (${data.elapsed_ms.toFixed(2)} ms)`,
      rule,
    ];
    if (!data.total) lines.push("", "No documents matched.");
    data.hits.forEach((hit, i) => {
      const snippet = hit.snippet.map((s) => (s.h ? `[${s.t}]` : s.t)).join("");
      lines.push(
        "",
        `${String(i + 1).padStart(2, "0")}. ${hit.title}` +
          (hit.score > 0 ? `   (relevance ${Math.round(hit.relevance * 100)}%)` : ""),
        `    file: ${hit.source}`,
        `    ${snippet}`
      );
      if (hit.matched.length) lines.push(`    matched: ${hit.matched.join(", ")}`);
    });
    if (data.total > data.hits.length) {
      lines.push("", `Showing the first ${data.hits.length} of ${data.total} results.`);
    }
    return lines.join("\n");
  }

  function renderResults(data) {
    state.data = data;
    const n = data.total;
    els.count.replaceChildren(
      n === 0 ? "No results" : pluralize(n, "result", "results"),
      h("span", { class: "time" }, `${data.elapsed_ms.toFixed(2)} ms`));

    els.cards.replaceChildren(
      ...(n === 0
        ? [emptyState(data.query)]
        : [
            ...data.hits.map((hit, i) => entry(hit, i + 1)),
            n > data.hits.length
              ? h("li", { class: "notice" }, `Showing the first ${data.hits.length} of ${n} results.`)
              : null,
          ]
      ).filter(Boolean) // replaceChildren(null) would insert the text "null"
    );
    els.plain.value = toPlainText(data);
  }

  function resetResults() {
    state.data = null;
    els.count.replaceChildren("Results");
    els.cards.replaceChildren(
      h("li", { class: "notice" },
        state.titles.length
          ? ["Results appear here, best match first. Press ", h("kbd", {}, "/"), " to jump to the search box."]
          : "Add some documents first, then results appear here, best match first."));
    els.plain.value = "";
    els.process.hidden = true;
    hideSpell();
    renderIdleGrid();
  }

  function showError(message) {
    els.count.replaceChildren("Search unavailable");
    els.cards.replaceChildren(h("li", { class: "notice error", role: "alert" }, message));
    els.plain.value = message;
    renderIdleGrid();
    setCaption("The grid fills in once a search succeeds.");
  }

  function setView(view) {
    const cards = view === "cards";
    els.cards.hidden = !cards;
    els.plain.hidden = cards;
    els.viewCards.setAttribute("aria-pressed", String(cards));
    els.viewText.setAttribute("aria-pressed", String(!cards));
  }
  els.viewCards.addEventListener("click", () => setView("cards"));
  els.viewText.addEventListener("click", () => setView("text"));

  els.cards.addEventListener("pointerover", (e) => {
    const li = e.target.closest(".entry");
    els.cards.querySelectorAll(".hot-entry").forEach((x) => x.classList.remove("hot-entry"));
    if (li) {
      li.classList.add("hot-entry");
      setHot(Number(li.dataset.doc));
    }
  });
  els.cards.addEventListener("pointerleave", () => {
    els.cards.querySelectorAll(".hot-entry").forEach((x) => x.classList.remove("hot-entry"));
    clearHot();
  });
  els.cards.addEventListener("click", (e) => {
    const open = e.target.closest("[data-open]");
    if (open) openReader(Number(open.dataset.open));
  });

  /* --------------------------------------------------------------- search */
  function setUrl(q, replace) {
    const modePart = state.mode !== "auto" ? `&mode=${state.mode}` : "";
    const url = q ? `?q=${encodeURIComponent(q)}${modePart}` : location.pathname;
    if (replace) history.replaceState(null, "", url);
    else history.pushState(null, "", url);
    document.title = q ? `${q} | Lexicon` : "Lexicon: search with the index showing";
  }

  async function runSearch(raw, { push = true } = {}) {
    const q = raw.trim();
    closeSuggest();
    state.query = q;
    if (!q) {
      resetResults();
      if (push) setUrl("", false);
      return;
    }

    state.controller?.abort();
    state.controller = new AbortController();
    const id = ++state.reqId;
    els.btn.disabled = true;

    try {
      const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&limit=20&mode=${state.mode}`, {
        signal: state.controller.signal,
      });
      if (!res.ok) throw new Error("The search server returned an error. Try again in a moment.");
      const data = await res.json();
      if (id !== state.reqId) return;
      renderResults(data);
      renderGrid(data.trace, data.total);
      renderSteps(data.trace);
      if (push) setUrl(q, false);
      if (data.total === 0) runSpell(q, { auto: true });
      else if (!els.spell.hidden) runSpell(q, { auto: false });
    } catch (err) {
      if (err.name === "AbortError") return;
      showError(err.message === "Failed to fetch"
        ? "Couldn't reach the search server. Check your connection and search again."
        : err.message);
    } finally {
      if (id === state.reqId) els.btn.disabled = !state.titles.length;
    }
  }

  function runQuery(q) {
    els.input.value = q;
    runSearch(q);
  }

  els.form.addEventListener("submit", (e) => {
    e.preventDefault();
    runSearch(els.input.value);
  });

  document.addEventListener("click", (e) => {
    const fix = e.target.closest("[data-fix]");
    if (fix) { applyFix(fix.dataset.word, fix.dataset.fix); return; }
    const all = e.target.closest("[data-fix-all]");
    if (all) { els.input.value = all.dataset.fixAll; runSearch(els.input.value); return; }

    const trigger = e.target.closest("[data-q]");
    if (!trigger) return;
    // Example chips choose their own search type; other links keep the current one.
    if (trigger.classList.contains("chip")) setMode(trigger.dataset.mode || "auto");
    runQuery(trigger.dataset.q);
    if (trigger.dataset.spell) runSpell(trigger.dataset.q, { auto: false });
  });

  els.tools.forEach((btn) =>
    btn.addEventListener("click", () => {
      // Clicking the active button turns it off and returns to "all types".
      setMode(state.mode === btn.dataset.mode ? "auto" : btn.dataset.mode);
      if (els.input.value.trim()) runSearch(els.input.value);
      els.input.focus();
    })
  );

  document.addEventListener("keydown", (e) => {
    if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "")) return;
    e.preventDefault();
    els.input.focus();
    els.input.select();
  });

  window.addEventListener("popstate", () => {
    const params = new URLSearchParams(location.search);
    const q = params.get("q") || "";
    setMode(params.get("mode") || "auto");
    els.input.value = q;
    runSearch(q, { push: false });
  });

  /* ---------------------------------------------------------- suggestions */
  const sug = { items: [], active: -1, timer: 0, controller: null };

  function chunkAtCaret() {
    const value = els.input.value;
    const caret = els.input.selectionStart ?? value.length;
    const m = /[^\s()"]*$/.exec(value.slice(0, caret));
    let chunk = m[0];
    let start = caret - chunk.length;
    if (chunk.startsWith("-")) { chunk = chunk.slice(1); start += 1; }
    return { chunk, start, end: caret };
  }

  function closeSuggest() {
    clearTimeout(sug.timer);
    sug.controller?.abort();
    sug.items = [];
    sug.active = -1;
    els.suggest.hidden = true;
    els.suggest.replaceChildren();
    els.input.setAttribute("aria-expanded", "false");
    els.input.removeAttribute("aria-activedescendant");
  }

  function markActive() {
    [...els.suggest.children].forEach((li, i) => li.setAttribute("aria-selected", String(i === sug.active)));
    if (sug.active >= 0) els.input.setAttribute("aria-activedescendant", `sug-${sug.active}`);
    else els.input.removeAttribute("aria-activedescendant");
  }

  function showSuggest(items, chunk) {
    if (!items.length || (items.length === 1 && items[0].term === chunk.toLowerCase())) {
      closeSuggest();
      return;
    }
    sug.items = items;
    sug.active = -1;
    els.suggest.replaceChildren(
      ...items.map((it, i) =>
        h("li", { role: "option", id: `sug-${i}`, "aria-selected": "false", dataset: { i } },
          h("div", {}, h("b", {}, it.term.slice(0, chunk.length)), it.term.slice(chunk.length)),
          h("span", {}, pluralize(it.df, "doc", "docs"))))
    );
    els.suggest.hidden = false;
    els.input.setAttribute("aria-expanded", "true");
  }

  function pickSuggestion(i) {
    const item = sug.items[i];
    if (!item) return;
    const { start, end } = chunkAtCaret();
    const value = els.input.value;
    els.input.value = value.slice(0, start) + item.term + value.slice(end);
    const caret = start + item.term.length;
    els.input.setSelectionRange(caret, caret);
    closeSuggest();
    els.input.focus();
  }

  els.input.addEventListener("input", () => {
    clearTimeout(sug.timer);
    const { chunk } = chunkAtCaret();
    if (chunk.length < 2 || !/^[\p{L}\p{N}']+$/u.test(chunk)) {
      closeSuggest();
      return;
    }
    sug.timer = setTimeout(async () => {
      sug.controller?.abort();
      sug.controller = new AbortController();
      try {
        const res = await fetch(`/api/suggest?q=${encodeURIComponent(chunk)}`, { signal: sug.controller.signal });
        const data = await res.json();
        if (chunkAtCaret().chunk === chunk) showSuggest(data.suggestions || [], chunk);
      } catch { /* aborted or offline: suggestions are optional */ }
    }, 110);
  });

  els.input.addEventListener("keydown", (e) => {
    const open = !els.suggest.hidden;
    if (e.key === "ArrowDown" && open) {
      e.preventDefault();
      sug.active = (sug.active + 1) % sug.items.length;
      markActive();
    } else if (e.key === "ArrowUp" && open) {
      e.preventDefault();
      sug.active = (sug.active - 1 + sug.items.length) % sug.items.length;
      markActive();
    } else if (e.key === "Enter" && open && sug.active >= 0) {
      e.preventDefault();
      pickSuggestion(sug.active);
    } else if (e.key === "Escape" && open) {
      closeSuggest();
    }
  });

  els.suggest.addEventListener("mousedown", (e) => {
    const li = e.target.closest("li");
    if (!li) return;
    e.preventDefault(); // keep focus in the input
    pickSuggestion(Number(li.dataset.i));
  });
  els.input.addEventListener("blur", () => setTimeout(closeSuggest, 120));

  /* ---------------------------------------------------------- spell check */
  const escapeRe = (t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  function applyFix(word, term) {
    const re = new RegExp(`(^|[^\\p{L}\\p{N}*?])${escapeRe(word)}(?![\\p{L}\\p{N}*?])`, "u");
    els.input.value = els.input.value.replace(re, (_, pre) => pre + term);
    runSpell(els.input.value, { auto: false });
  }

  function hideSpell() {
    els.spell.hidden = true;
    els.spell.replaceChildren();
    els.spellBtn.setAttribute("aria-expanded", "false");
  }

  async function runSpell(raw, { auto }) {
    const q = raw.trim();
    if (!q) {
      if (auto) return;
      els.spell.replaceChildren(h("p", {}, "Type a query first, then check its spelling."));
      els.spell.hidden = false;
      els.spellBtn.setAttribute("aria-expanded", "true");
      return;
    }
    try {
      const res = await fetch(`/api/spell?q=${encodeURIComponent(q)}`);
      if (!res.ok) throw new Error("spell check failed");
      const data = await res.json();
      if (els.input.value.trim() !== q && auto) return; // the query moved on
      const bad = data.words.filter((w) => !w.known);
      const fixable = bad.filter((w) => w.suggestions.length);

      if (auto && !fixable.length) return; // nothing useful to say after a miss
      if (!data.words.length) {
        els.spell.replaceChildren(h("p", {}, "No plain words to check in that query."));
      } else if (!bad.length) {
        els.spell.replaceChildren(
          h("p", { class: "ok" }, `Spelling looks fine. ${pluralize(data.words.length, "word is", "words are")} in the index.`));
      } else {
        const rows = bad.map((w) =>
          h("div", { class: "spell-word" },
            h("span", { class: "bad" }, w.word),
            w.suggestions.length
              ? [
                  h("span", { class: "arrow", "aria-hidden": "true" }, "→"),
                  h("span", { class: "sr" }, "did you mean"),
                  w.suggestions.map((sg) =>
                    h("button", { type: "button", class: "term-chip", dataset: { word: w.word, fix: sg.term },
                                  title: `Replace ${w.word} with ${sg.term}` },
                      sg.term, h("i", {}, pluralize(sg.df, "doc", "docs")))),
                ]
              : h("span", { class: "none" }, "no close match in the index")));
        const parts = [
          h("p", {}, auto ? "No results. Did you mean:" : "These words are not in the index:"),
          ...rows,
        ];
        if (data.corrected) {
          parts.push(h("div", { class: "spell-fix" },
            h("button", { type: "button", class: "tool", dataset: { fixAll: data.corrected } },
              "Use best corrections and search"),
            h("code", {}, data.corrected)));
        }
        els.spell.replaceChildren(...parts);
      }
      els.spell.hidden = false;
      els.spellBtn.setAttribute("aria-expanded", "true");
    } catch {
      if (!auto) {
        els.spell.replaceChildren(h("p", {}, "Couldn't run the spell check. Try again."));
        els.spell.hidden = false;
      }
    }
  }

  els.spellBtn.addEventListener("click", () => {
    if (!els.spell.hidden) { hideSpell(); return; }
    runSpell(els.input.value, { auto: false });
  });

  /* --------------------------------------------------------------- reader */
  async function openReader(docId) {
    try {
      const res = await fetch(`/api/doc/${docId}?q=${encodeURIComponent(state.query)}&mode=${state.mode}`);
      if (!res.ok) throw new Error("not found");
      const doc = await res.json();
      els.readerTitle.textContent = doc.title;
      els.readerMeta.textContent = `${doc.source}, ${pluralize(doc.words, "word", "words")}`;
      els.readerBody.replaceChildren(
        ...doc.paragraphs.map((segs) =>
          h("p", {}, segs.map((s) => (s.h ? h("mark", {}, s.t) : s.t))))
      );
      els.readerBody.scrollTop = 0;
      if (!els.reader.open) els.reader.showModal();
    } catch {
      setCaption("Couldn't open that document. Try again.");
    }
  }
  els.readerClose.addEventListener("click", () => els.reader.close());
  els.reader.addEventListener("click", (e) => {
    if (e.target === els.reader) els.reader.close(); // click on the backdrop
  });

  /* ------------------------------------------------------------ documents */
  const plural = (n, one, many) => pluralize(n, one, many);

  function applyStats(s) {
    state.titles = s.titles || [];
    state.sources = s.sources || [];
    state.words = s.words || [];
    const n = s.documents;
    const has = n > 0;
    els.docCount.textContent = n;
    els.input.disabled = !has;
    els.btn.disabled = !has;
    els.spellBtn.disabled = !has;
    els.input.placeholder = has ? MODES[state.mode].placeholder : "Add documents to start searching";
    els.clearDocs.hidden = !has;
    renderDocList();
  }

  function renderDocList() {
    els.docList.hidden = !state.titles.length;
    els.docList.replaceChildren(
      ...state.titles.map((title, i) =>
        h("li", { class: "doc" },
          h("button", { type: "button", class: "linklike doc-name", dataset: { open: i }, title: `Open ${state.sources[i]}` },
            h("span", { class: "doc-title" }, title),
            h("span", { class: "doc-src" }, `${state.sources[i]} · ${plural(state.words[i] || 0, "word", "words")}`)),
          h("button", { type: "button", class: "doc-x", dataset: { remove: i },
                        "aria-label": `Remove ${state.sources[i]}` }, "×"))));
  }

  function setStatus(parts, isError) {
    els.upStatus.classList.toggle("err", !!isError);
    els.upStatus.replaceChildren(...parts);
  }

  /** Re-run the current query (or reset) after the document set changed. */
  function afterDocsChanged() {
    renderIdleGrid();
    if (state.query && state.titles.length) runSearch(state.query, { push: false });
    else resetResults();
  }

  async function uploadFiles(fileList) {
    const files = [...fileList];
    if (!files.length || state.busy) return;
    state.busy = true;
    els.pick.disabled = true;
    setStatus([`Indexing ${plural(files.length, "file", "files")}…`]);
    const body = new FormData();
    files.forEach((f) => body.append("files", f, f.name));
    try {
      const res = await fetch("/api/documents", { method: "POST", body });
      let data = {};
      try { data = await res.json(); } catch { /* non-JSON error page */ }
      if (data.stats) applyStats(data.stats);
      const added = data.added || [];
      const rejected = data.rejected || [];
      if (!res.ok && !data.stats) {
        setStatus([data.error || "The upload failed. Try again."], true);
      } else {
        const parts = [];
        if (added.length) parts.push(`Added ${plural(added.length, "document", "documents")}. `);
        if (rejected.length) {
          parts.push(h("span", { class: "rej" },
            rejected.map((r) => h("span", {}, `${r.name}: ${r.error} `))));
        }
        setStatus(parts, !added.length);
        if (added.length) afterDocsChanged();
      }
    } catch {
      setStatus(["Couldn't reach the server. Check your connection and try again."], true);
    } finally {
      state.busy = false;
      els.pick.disabled = false;
      els.fileInput.value = "";
    }
  }

  async function removeDocs(index) {
    try {
      const res = await fetch(index == null ? "/api/documents" : `/api/documents/${index}`, { method: "DELETE" });
      if (!res.ok) throw new Error("failed");
      const data = await res.json();
      applyStats(data.stats);
      setStatus([index == null ? "Removed all documents." : "Document removed."]);
      afterDocsChanged();
    } catch {
      setStatus(["Couldn't remove that document. Try again."], true);
    }
  }

  els.pick.addEventListener("click", () => els.fileInput.click());
  els.fileInput.addEventListener("change", () => uploadFiles(els.fileInput.files));
  els.clearDocs.addEventListener("click", () => removeDocs(null));
  els.docList.addEventListener("click", (e) => {
    const rm = e.target.closest("[data-remove]");
    if (rm) { removeDocs(Number(rm.dataset.remove)); return; }
    const open = e.target.closest("[data-open]");
    if (open) openReader(Number(open.dataset.open));
  });

  ["dragenter", "dragover"].forEach((t) =>
    els.drop.addEventListener(t, (e) => {
      if (!e.dataTransfer?.types?.includes("Files")) return;
      e.preventDefault();
      els.drop.classList.add("over");
    }));
  ["dragleave", "drop"].forEach((t) =>
    els.drop.addEventListener(t, (e) => {
      e.preventDefault();
      if (t === "dragleave" && els.drop.contains(e.relatedTarget)) return;
      els.drop.classList.remove("over");
    }));
  els.drop.addEventListener("drop", (e) => uploadFiles(e.dataTransfer?.files || []));
  // A file dropped outside the zone should not make the browser navigate away.
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());

  /* ----------------------------------------------------------------- boot */
  async function boot() {
    setView("cards");
    const params = new URLSearchParams(location.search);
    setMode(params.get("mode") || "auto");
    try {
      const res = await fetch("/api/stats");
      applyStats(await res.json());
    } catch { /* stats are optional; the page still works */ }
    renderIdleGrid();

    const q = params.get("q");
    if (q && state.titles.length) {
      els.input.value = q;
      runSearch(q, { push: false });
    } else if (state.titles.length && matchMedia("(pointer: fine)").matches) {
      els.input.focus();
    }
  }
  boot();
})();
