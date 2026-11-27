# needlepoint command reference

All commands take `--root DIR`. By default the root is the nearest parent directory containing `.git` or `.needlepoint`. Every command refreshes the index incrementally first.

| Command | What it returns | Useful flags |
|---|---|---|
| `map` | Repo stats, directory sizes, then the most central files (import/reference-graph PageRank) with their top-level symbols, cut to a token budget | `--budget 1200`, `--focus "topic"` |
| `find <words…>` | Ranked symbols: `path:start-end kind Qualified.name signature — doc` | `-k 8`, `--in glob[,glob]`, `--kind function\|class\|method\|type\|h2…` |
| `show <target…>` | Exact source with line numbers. Targets: `Name`, `Class.method`, `path:Name`, `path:120-180`, `path` (gives the outline) | `--grep RX -C N` (only matching lines inside the symbol, with context), `-f` (bypass session dedupe), `--full` (don't fold long docstrings), `--all` (every match), `--ctx N`, `--sig` (header only) |
| `outline <path…>` | Skeleton of a file or directory. Ranges cover 100% of the lines: code outside any symbol appears as `a-b · first line` | `--all` (don't hide deep nesting) |
| `grep <regex> [paths…]` | ripgrep (case-insensitive), each hit tagged `[in Enclosing.symbol]` or `[DEF Name]` | `--limit 40` |
| `refs <name>` | Textual usages (word-boundary, via ripgrep when available) grouped by file, each tagged with its enclosing symbol or `[DEF]` | `--limit 40` |
| `pack <task…>` | One-shot context: the best hit, then what it actually calls (same file or imported), then other strong hits, all as exact source. Classes too big for the budget appear as a member skeleton. Then callers/callees as signatures, then other candidates | `--budget 5000`, `--max 8` |
| `note add <target> "text"` | Stores a note anchored to the hash of that symbol's code | |
| `note ls [filter]` | Every note marked ✓ valid, ⚠ STALE (code changed) or ✗ ORPHANED (symbol gone) | |
| `note rm <id>` | Deletes a note | |
| `run <cmd…>` | Runs through the shell. Returns the exit code and condensed output, and saves the full log | `--budget 1500` (tokens) |
| `log [id]` | With no id, lists recent logs. Query one with `--grep RX` (plus `--ctx N`) or `--lines A-B` | |
| `stats` | Estimated tokens kept out of context, by mechanism | |
| `bench` | Measures savings and retrieval accuracy on the current repo | `-n 200`, `--seed` |
| `doctor` | Environment and configuration check | |

## Environment variables

- `PATH` / `NDL`: the plugin's SessionStart hook puts the `ndl` launcher on PATH and sets `NDL=ndl`. The launcher works in bash, zsh, dash and Git Bash, and picks `python3`, `python` or `py`.
- `NDL_SESSION`: the session id. Enables `show` dedupe/delta within a session.
- `NDL_READ_GUARD_TOKENS` (default 6000): whole-file Reads above this size return an outline. `0` disables.
- `NDL_OUTLINE_MAX` (default 220): maximum number of outline rows in a read-guard reply.
- `NDL_BASH_CONDENSE_CHARS` (default 12000): Bash output above this size is condensed. `0` disables.
- `NDL_ROOT`: forces the project root.
- `NDL_STATE_DIR`: keeps state outside the project (default `<root>/.needlepoint`; read-only projects fall back to `~/.cache/needlepoint/<hash>` automatically).
- `NDL_MAX_FILES` (default 60000): maximum files indexed.
- `NDL_NO_WARM=1`: don't build the index in the background at session start.
- `NDL_NO_SQLITE=1`: forces the JSON backend (used automatically when Python was built without `sqlite3`).
- `NDL_DEBUG=1`: makes hook errors raise instead of failing open.

## State (`.needlepoint/` in the project root)

- `index.db` and `search-<generation>.db`: SQLite symbol index and packed BM25 postings. Rebuildable. Commands read only the rows they need, so `show` stays at about 0.3s even on a 20k-file repo. Edits since the last search build are overlaid in memory, with no rebuild. Without `sqlite3`: `index.json.gz` and `search-*.json.gz`.
- `notes.json`: hash-anchored notes. **Commit it** to share knowledge with the team. The bundled `.gitignore` ignores everything except this file.
- `session/<id>/`: per-session read ledger. Reset on session start and on compaction.
- `logs/`: full outputs of condensed commands (the last 200 are kept).
- `stats.jsonl`: savings events.
