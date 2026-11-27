# Changelog

## 0.3.1 — 2026-11-27 (Windows PowerShell + map quality)
- **PowerShell launcher** `scripts/ndl.ps1` (and `ndl.cmd` for cmd.exe). On Windows the SessionStart context now gives its full path, because `ndl` is only on PATH in Bash. Arguments like `'status|tuple'` reach ndl unchanged.
- **`map` ranks each file's symbols** by size, docs, in-file use and visibility instead of listing them in file order, so big entry points come before small private helpers.
- Fixed: editing a file reset its centrality to 0 (both backends), so the file you were working on dropped out of `map` and lost its search boost until the next big reindex.
- Fixed: `ndl run cmd "arg with spaces"` joined arguments without quoting. One argument is still run as a shell string; several are now quoted individually.
- Tests no longer inherit a live session's `NDL_SESSION`. `.gitattributes` keeps scripts LF and `.cmd` files CRLF. Tests 38 → 46.

## 0.3.0 — 2026-11-22 (cross-platform + 16-language + scale hardening)
- **Portable launcher** `ndl` on PATH via SessionStart. It works in zsh (macOS's default shell doesn't word-split `$NDL`), bash, dash and Git Bash, and picks python3, python or py. Hooks use it too.
- **SQLite index and search** (JSON fallback). VS Code-size repos: `show` 3.2s → 0.30s, memory 785 → 44 MB, refresh after an edit 51s → 0.44s. Incremental search overlay: `find` after an edit 20s → 0.8s.
- **Brace matcher rewrite**, validated on 16 real repos (Go, Rust, Java, Kotlin, C#, Swift, C, C++, PHP, Ruby, TS, Scala, Elixir, Lua, shell, Dart): range accuracy 87–97% → 95–100%, partial overlaps 166 → 3, 0 crashes. It now understands braces and `;` inside signatures (`interface{}`, `[T; 2]`, `= {}` defaults, closure defaults, named params, object return types including multi-line), brace-less and expression-bodied definitions, multi-line strings (Go/JS backticks, Rust strings, raw strings `r#"…"#` and `R"(…)"`, triple quotes), shell `#` comments and Dart single quotes. It falls back to the closing brace at the definition's indentation.
- New: JS/TS test blocks (`describe`/`it`/`test`) and RSpec blocks appear in outlines. C one-line functions. `@overload` stubs rank after the implementation.
- Fixed: CRLF files, Latin-1 files (`café` was mangled), macOS `/tmp` vs `/private/tmp` symlinks breaking dedupe, a cold-index race returning 0 results, a search-generation race, skipped files re-checked on every command (6s/51s no-op refresh), a guard miscalibration on files longer than one Read, and a BrokenPipe message when piping to `head`.
- Read-only projects fall back to `~/.cache/needlepoint`. `NDL_STATE_DIR`, `NDL_MAX_FILES` and `NDL_NO_SQLITE` added.
- Tests 25 → 38, run on Python 3.8–3.13 × {sqlite, json}. CI workflow for ubuntu, macos and windows.

## 0.2.0 — 2026-11-22 (after live testing on Flask and Express)
- New `show <symbol> --grep RX -C N`: focused view inside a symbol. The real-project test showed that whole-function `show` was the main cost.
- New `ndl grep RX [paths]`: ripgrep with each hit tagged by its enclosing symbol.
- Long Python docstrings fold into an explicit `⋯ docstring lines A-B folded` marker in `show` and `pack` (`--full` to expand).
- Python class attributes are indexed (`show Class.attr`).
- JS: functions assigned to properties (`res.send = function send(){}`, `Foo.prototype.bar = …`, `module.exports = function …`) are now indexed (Express went from 0 to 49 API functions). Calls are no longer mistaken for method definitions. JSDoc is attached across a blank line.
- Fixed: the `$NDL` shortcut contained quote characters that bash keeps literally (broken on all platforms). A test now runs it through bash.
- Fixed: Windows console/pipe encoding (UTF-8 output); hook stdin is decoded as UTF-8.
- SessionStart only warms the index in real projects (never in $HOME or /). File cap via NDL_MAX_FILES.
- A/B script: `--real` (Flask + Express at pinned commits), `--setup`, works in PowerShell.

## 0.1.0 — 2026-11-22
- First release: index (Python via ast, plus about 20 languages via structural patterns), BM25F search with a reference-graph prior.
- Commands: map, find, show, outline, refs, pack, note, run, log, stats, bench, doctor.
- Claude Code hooks: read guard (full-coverage outline), re-read dedupe and diff, Bash output condensing with full logs, session reset on compaction.
- Hash-anchored notes with stale detection.
