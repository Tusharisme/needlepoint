# How needlepoint saves tokens without losing accuracy

## Principle: subtraction, not compression

Most token savers summarize, truncate or rewrite what is already in context, and that is where accuracy goes. needlepoint never alters content it shows. It only decides what to *load*, and anything it leaves out stays one exact call away:

| Waste in a typical session | needlepoint mechanism | Why accuracy holds |
|---|---|---|
| Reading a 2,000-line file for one function | `show` / ranged Read using symbol line ranges | The code shown is byte-exact, with real line numbers |
| Opening big files to understand their layout | Read guard returns a **full-coverage outline** | Every line falls in a listed range, so nothing is invisible |
| Re-reading files that are already in context | Session ledger dedupe; **diff-only** re-reads after a change | The content is already in context. Old version + diff = new version |
| 5-15 grep/read round trips to find code | `find` (BM25F + graph prior) and `pack` (one call) | Ranked exact spans plus a list of "other candidates" |
| 40k-char test or build logs | Condense: keep errors, failures, tracebacks and summary; collapse repeats | The full log is saved and queryable with `log --grep` |
| Re-deriving project knowledge every session | Notes anchored to code hashes | A note is shown as trusted only while its code is unchanged; otherwise it is flagged STALE |

Less noise in context also helps accuracy directly. Long-context studies ("context rot", lost-in-the-middle, distractor studies) show model reliability falls as irrelevant tokens grow, and multi-hop reasoning suffers most.

## Index

- Files come from `git ls-files -co --exclude-standard` (or a filesystem walk with ignore rules). Binary, minified and very large files (over 1.5 MB) are skipped.
- **Python** is parsed with `ast`, which gives exact start and end lines, decorators, signatures and docstrings. **About 20 other languages** (JS/TS/JSX, Go, Rust, Java, C#, Kotlin, Scala, Swift, C/C++/ObjC, PHP, Ruby, Elixir, Lua, shell, SQL, Markdown, YAML, TOML, Makefile/Dockerfile, CSS) use definition patterns plus a brace/indent matcher. The matcher is aware of strings, comments, char literals, JS regex literals and JSX. Doc comments directly above a definition become its doc.
- Incremental: a file is re-parsed only when its mtime or size changes. Parsing runs in parallel for large batches.
- **Graph prior**: an edge A→B is added when file A uses a name defined in only a few files including B. PageRank over these edges ranks central files for `map` and gives `find` a small boost.

## Storage and scale

The index lives in SQLite (Python's standard library on Ubuntu, macOS and Windows), with a JSON fallback. A refresh compares each file's mtime and size, re-parses only what changed, and recomputes PageRank only when more than 2% of files changed. Files that can't be indexed (binary, minified) are remembered, so they aren't re-checked. Search postings are packed per term and built lazily per *generation*, which is a hash of the file manifest, so concurrent processes agree on it. Changes since the last build are overlaid in memory until more than 5% of files differ. Measured: on Django (4.2k files), a no-op refresh takes 0.28s and `show` 0.19s; on VS Code (19.5k files, 78M tokens), `show` takes 0.30s, `find` 0.6s, `find` right after an edit 0.8s, and a cold index 92s (once, in the background).

## Platforms

The suite runs on Python 3.8–3.13 with both backends. It covers bash, dash and sh (and zsh by design: the launcher sits on PATH, so it never relies on word-splitting); no ripgrep or git; macOS-style symlinked paths; spaces and Unicode in repo and plugin paths; CRLF, BOM and Latin-1 files; macOS's `spawn` process start; concurrent indexing; and unwritable projects. CI on GitHub runs it on ubuntu, macos and windows.

## Search

BM25F over symbol documents. Fields are name (×4), docstring (×2), signature, qualified parent, path tail, and a bag of the identifiers used in the body. Identifiers are split on camelCase and snake_case and stemmed lightly. On top of BM25 come an exact-name boost, a query-coverage factor, a graph-centrality prior, and down-weighting for test files (unless you ask for tests) and for prose headings. The weights were swept over two evals and the results barely change across settings.

## Hooks (Claude Code)

| Hook | Matcher | Behaviour |
|---|---|---|
| SessionStart | all sources | Resets this session's read ledger (so it also resets after compaction), exports `NDL` and `NDL_SESSION` via `CLAUDE_ENV_FILE`, warms the index in the background, and adds about 90 tokens of guidance |
| PreToolUse | Read | 1) A requested range already read and unchanged → deny with a one-line notice. 2) The whole file was read before and has changed → deny with a unified diff, if the diff is under 40% of the file. 3) A whole-file Read over the threshold → deny with a full-coverage outline. Each denial arms a one-shot bypass, so repeating the identical call goes through |
| PostToolUse | Read | Records the range read and a snapshot of the file, for later dedupe and diffs |
| PostToolUse | Bash | Output over the threshold is condensed through `updatedToolOutput` (same shape as the original). The full log is kept. Commands where the full text is the point (`cat`, `sed`, `git diff`, …) are exempt |

Failure policy: any internal error exits 0 with no output, so the normal tool behaviour applies. Subagents use their own ledger key when `agent_id` is present. Without one, the bypass guarantees that at most one extra round trip happens.

## Known limits

- Search is lexical plus structural, with no embeddings. That keeps it zero-dependency, instant and deterministic, but phrasing with no lexical overlap can miss; rephrase or use `refs` and `rg`.
- Non-Python parsing uses heuristics. Unusual formatting can shift a symbol's end line, and the outline's gap rows make that visible.
- Callee resolution is name-based (same file first, then imported names), not type-resolved.
