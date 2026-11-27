# Benchmark results

## v0.3.0 hardening (2026-11-22)

**Platforms:** 38 tests × Python 3.8, 3.9, 3.10, 3.11, 3.12 and 3.13 × {sqlite, json} backends, all green, plus a 48-run soak with 0 failures. Covered: bash, dash and sh launchers; no ripgrep and no git; macOS-style symlinked `/tmp`; spaces and Unicode in repo and plugin paths; CRLF, BOM and Latin-1 files; `spawn` process start (macOS); 8–10 concurrent cold indexers; unwritable project directories; every `hooks.json` command executed through a shell. CI (`.github/workflows/test.yml`) runs on ubuntu, macos and windows.

**Languages** (`bench/lang_check.py`, 16 real repos): definition recall 100% for all 16; correct ranges 100% for Go, Rust, Java, Kotlin, C#, Swift, C++, PHP, TS and Dart, 99% for C and Scala, and 95% for shell (checker false alarms); 0 crashes across every command and hook.

**Scale:**

| repo | files | cold index | no-op refresh | `show` | `find` | `find` after an edit |
|---|---:|---:|---:|---:|---:|---:|
| Django | 4,248 | 4.7s | 0.28s | 0.19s | ~0.5s | ~0.5s |
| VS Code | 19,459 | 92s (background) | 1.2s | 0.30s | 0.6s | 0.8s |


## Live real-project test (v0.1 → v0.2, 2026-11-22)

Flask @49943b5 and Express @43fca52, 8 questions (`bench/real/tasks.json`). Real tool calls were made by Claude in a live session. Every tool output was captured and measured, and every answer was graded against the source.

| arm | correct | tokens into context | tool calls | rounds |
|---|---:|---:|---:|---:|
| A: grep + tight ranged Reads | 8/8 | 1,505 | 13 | 2 |
| A′: same greps + whole-file Reads (measured, not loaded) | 8/8 | 39,963 | 13 | 2 |
| B v0.1: find / refs / show (whole symbols) | 8/8 | 3,784 | 9 | 2 |
| B v0.2: + `show --grep`, `ndl grep` | 8/8 | 1,843 | 8 | 2 |

Caveats: a single run, and the answers were known in advance, which helps arm A most because its read ranges were chosen with hindsight. In arm B v0.2, one pattern initially used inside knowledge and was re-run with arm A's word (`tuple`) for fairness. Claude Code itself could not sign in from the test workspace (its network proxy blocks the OAuth token endpoint), so the `claude -p` A/B (`ab_claude_code.py --real`) still needs to run on a normal machine.

Conclusion: needlepoint roughly matches an expert surgical agent with 40% fewer calls, and uses about 22x fewer tokens than the whole-file habit.

# Benchmark results (v0.1.0, 2026-11-22)

Environment: Linux, Python 3.13, 2 CPUs. Token counts are estimated at 3.5 chars/token (±15%).

## `ndl bench` (built in; reproduce with `ndl bench --root <repo> -n 300`)

| Corpus | A. known-symbol read: baseline → ndl (median) | total reduction | resolve | B. find recall@1 / @5 (docstring queries, name words removed) | C. big-file outline vs Read (median) |
|---|---|---|---|---|---|
| CPython 3.11 stdlib, 678 files | 9,400 → 160 | 29.8x | 300/300 | 67% / 86% (n=300) | 1,239 vs 9,891 (−88%) |
| npm CLI, 285 JS files | 1,656 → 212 | 4.5x | 296/300 | 88% / 99% (n=68) | 270 vs 9,860 (−98%) |
| TypeScript lib .d.ts, 130 files | 8,009 → 157 | 28.9x | 66/66 | 89% / 89% (n=9) | 1,327 vs 10,930 (−89%) |

Baseline for A: `rg` for the definition plus a Read of the whole file (capped at 2,000 lines, like Claude Code's Read).

## Realistic queries (`bench/eval_queries.py`, 35 hand-written queries, CPython stdlib)

- needlepoint symbol recall@1 63%, recall@5 80%
- correct file in top 5: needlepoint **100%** vs grep-style baseline 83% (files ranked by how many query words they contain)
- Ranking parameters were swept over 36 settings. Recall@5 stayed at 28/35 for every one, so the ranking isn't tuned to this set.

## Six realistic questions, simulated tool path

| task | baseline (rg + Read file) | needlepoint (find + show) |
|---|---:|---:|
| get_close_matches cutoff filters | 27,359 | 762 |
| shouldRollover with maxBytes=0 | 20,939 | 537 |
| lru_cache invalid maxsize | 13,019 | 820 |
| protocol_version default | 16,888 | 312 |
| copytree defaults | 19,095 | 351 |
| json separators with indent | 5,807 | 1,192 |
| **total** | **103,107** | **3,974** (25.9x fewer) |

The baseline is the common whole-file path. An agent that uses `grep -n` plus ranged Reads would land in between. These are the same questions `ab_claude_code.py` asks; run it for real end-to-end numbers.

## Output condensing

400-test unittest run (3 failures, INFO logging on every test): 41,299 chars / 11,803 tokens → 2,281 chars / 612 tokens (−95%). 8/8 signal lines kept: both FAIL headers, the ERROR header, KeyError, both assertion messages, "Ran 400 tests" and "FAILED (failures=2, errors=1)".

## Speed

Index build: 3.3M tokens of Python in 2.3 s (cold); incremental refresh 0.1 s. Queries: 3–90 ms.
