# needlepoint 🪡 — read less, miss nothing

**Lossless precision context for Claude Code (and any agent that runs shell commands).**
needlepoint cuts tokens by keeping unneeded text *out* of context, instead of compressing what is already there. Nothing Claude relies on is paraphrased, summarized or silently dropped, so accuracy holds and often improves, because there is less noise to reason over.

```
/plugin marketplace add Tusharisme/needlepoint
/plugin install needlepoint@needlepoint
```

Zero dependencies (Python 3.8+ standard library). It is fully local, makes no network calls and has no telemetry. ripgrep is used when present. Tested on Ubuntu and macOS-style environments (Python 3.8–3.13), in bash, zsh, dash and Git Bash, and on 16 languages of real open-source code.

---

## Why another token tool?

Today's options each trade something away:

| Approach | Examples | What it cuts | Cost |
|---|---|---|---|
| Talk less | caveman-style skills | Output prose | Small share of total tokens. An independent paired test measured about 8.5% |
| Summarize tool output | sandbox/summary MCP servers | Logs, snapshots | The model sees a summary, not the data |
| Compress memory | AI-written session memories | Re-explaining context | Summaries go stale silently |
| IDE semantic tools | LSP-based MCP servers | Whole-file reads | Heavy setup (language servers, runtimes) |

**needlepoint takes a different approach: subtraction, not compression.**

1. **Never load what isn't needed.** Ranked search finds the symbol and `show` returns exactly its lines, so a whole file is never read for one function.
2. **Never load the same thing twice.** Re-reads of unchanged code are deduped, and re-reads after an edit return **only the diff**.
3. **Never hide anything.** Big files get a *full-coverage* outline in which every line sits in a listed range. Condensed command output keeps every error, failure and traceback, and the full log is one `--grep` away.
4. **Never trust stale knowledge.** Notes are anchored to a hash of the code they describe and flag themselves **⚠ STALE** when that code changes.

## Results

Measured with the bundled benchmarks (`bench/`). Token counts are estimates (±15%).

**Reading a known function or class** (300 random symbols per corpus; baseline = grep for the definition + Read the file):

| Corpus | Baseline median | needlepoint median | Total reduction | Resolved correctly |
|---|---:|---:|---:|---:|
| CPython 3.11 stdlib (678 files, 3.3M tokens) | 9,400 | 160 | **29.8x** | 300/300 |
| TypeScript lib `.d.ts` (130 files, 1.7M tokens) | 8,009 | 157 | **28.9x** | 66/66 |
| npm CLI source (285 JS files) | 1,656 | 212 | **4.5x** | 296/300 |

**Opening a large file:** the full-coverage outline is 88–98% smaller than the Read, with 100% line coverage.

**Finding code from a plain-language description** (35 hand-written developer queries on the CPython stdlib):

| | needlepoint `find` | grep-style baseline |
|---|---:|---:|
| Correct file in top 5 | **100%** | 83% |
| Correct symbol in top 5 / top 1 | 80% / 63% | n/a |
| Latency | ~15–90 ms | multiple rg calls and reads |

**Live real-project test** (Flask + Express, 8 questions, real tool calls in a Claude session, answers verified in source):

| | grep + tight ranged Reads | grep + whole-file Reads | needlepoint 0.2 |
|---|---:|---:|---:|
| Correct | 8/8 | 8/8 | 8/8 |
| Tokens into context | 1,505 | 39,963 | 1,843 |
| Tool calls | 13 | 13 | 8 |

The honest takeaway: an expert agent that greps precisely and reads tight ranges is already efficient, and needlepoint roughly matches it with 40% fewer calls. That baseline also had hindsight: I knew where the answers were when choosing read ranges. Against the common whole-file habit, needlepoint uses about 22x fewer tokens. The read-guard hook exists to make the efficient path the default. Version 0.1 used 3,784 tokens on this test because `show` printed whole functions, which led to the new `show --grep` focus mode.

**Six realistic questions** (simulated tool path, CPython stdlib): **103,107 → 3,974 tokens (25.9x fewer)**.

**Noisy test run** (400 tests, 3 failing, with logging): **11.8k → 0.6k tokens (95%)**, and every FAIL/ERROR line, traceback and summary line is kept.

**End-to-end A/B in Claude Code:** run `python bench/ab_claude_code.py --real --runs 2 --model sonnet` (Flask + Express) or `--setup` (stdlib). It answers the same questions with and without the plugin and reports tokens, cost, turns and accuracy. Please publish your numbers.

## What you get

### Automatic (plugin hooks, nothing to remember)

| When Claude… | needlepoint… |
|---|---|
| Reads a whole large source file (>~6k tokens) | Replies with its full-coverage outline. Claude then reads only the ranges it needs, or repeats the call to load everything |
| Re-reads lines it already has and that haven't changed | Replies with a one-line "already in your context" notice |
| Re-reads a file after editing it | Replies with **just the diff** |
| Runs a command with huge output | Condenses it (errors, failures, tracebacks, summary; repeats collapsed) and saves the full log |

Every intervention explains itself, and **repeating the identical call always goes through**. State resets on compaction, and errors fail open.

### On demand (the `needlepoint` skill / `ndl` CLI)

```
ndl map [--focus "billing"]          # token-budgeted repo overview (PageRank over the reference graph)
ndl find "retry with backoff"        # ranked symbols: path:start-end kind Name signature — doc
ndl pack "why do refunds double-post" --budget 5000
                                      # one call: exact source of the best hit + what it calls + callers + other candidates
ndl show Invoice.total api/routes.ts:createUser src/db.py:120-180
ndl outline src/payments/service.py  # skeleton; every line in a range
ndl show Invoice.total --grep "tax|round" -C 2   # only the relevant lines inside a big function
ndl grep "retry|backoff" src/      # ripgrep, each hit tagged with its enclosing function
ndl refs charge_card                 # usages grouped by enclosing function, [DEF] marked
ndl run pytest -q                    # condensed output + full log id
ndl log 3fa9c2e1 --grep "Error|FAIL" # query any full log
ndl note add Invoice.total "amounts are in cents; tax added in Invoice.finalize"
ndl stats | bench | doctor
```

Languages: Python (exact, via `ast`), JS/TS/JSX/TSX, Go, Rust, Java, C#, Kotlin, Scala, Swift, Dart, C/C++/Obj-C, PHP, Ruby, Elixir, Lua, shell, SQL, Markdown, YAML, TOML, Makefile/Dockerfile and CSS.

**Measured on real repos** (`bench/lang_check.py`): definition recall is a proxy against each language's definition keywords; ranges count as correct when the body's braces balance and it ends on the closing brace.

| Language (repo) | Symbols | Def recall | Correct ranges | Crashes |
|---|---:|---:|---:|---:|
| Go (cobra) | 650 | 100% | 100% | 0 |
| Rust (serde_json) | 2,162 | 100% | 100% | 0 |
| Java (gson) | 4,386 | 100% | 100% | 0 |
| Kotlin (okio) | 4,055 | 100% | 100% | 0 |
| C# (Polly) | 5,144 | 100% | 100% | 0 |
| Swift (Alamofire) | 2,933 | 100% | 100% | 0 |
| C (jq) | 1,654 | 100% | 99% | 0 |
| C++ (fmt) | 6,102 | 100% | 100% | 0 |
| PHP (guzzle) | 3,378 | 100% | 100% | 0 |
| TypeScript (zod) | 8,931 | 100% | 100% | 0 |
| Scala (scopt) | 427 | 100% | 99% | 0 |
| Dart (dart-lang/http) | 5,620 | 100% | 100% | 0 |
| Shell (nvm) | 222 | 100% | 95%* | 0 |
| Ruby, Elixir, Lua (sinatra, plug, inspect.lua) | 4,431 | 100% | indent-based | 0 |

\*The shell figures are mostly checker false alarms: those functions do end on their closing `}`.

**Scale:** Django (4.2k files): `show` 0.19s, no-op refresh 0.28s. VS Code (19.5k files, 78M tokens): `show` 0.30s, `find` 0.6s, `find` right after an edit 0.8s, a 21k-line file outlined in under 5k tokens, and a cold index of 92s (once, in the background).

## Install

**Claude Code plugin (recommended: skill + hooks):**
```
/plugin marketplace add Tusharisme/needlepoint
/plugin install needlepoint@needlepoint
```
**Skill only** (any agent that supports skills; no hooks): copy `plugins/needlepoint/skills/needlepoint` into `~/.claude/skills/`, or run `npx skills add Tusharisme/needlepoint`.

**Plain CLI:** `python3 plugins/needlepoint/skills/needlepoint/scripts/ndl.py --help`

Requirements: Python 3.8+ as `python3`, `python` or `py`. **macOS and Linux:** works out of the box; macOS's built-in `/usr/bin/python3` (3.9) is fine. **Windows:** needs Git Bash, which Claude Code requires anyway; Windows is covered by CI but less battle-tested.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `NDL_READ_GUARD_TOKENS` | 6000 | Whole-file Reads above this size get an outline first (`0` turns it off) |
| `NDL_BASH_CONDENSE_CHARS` | 12000 | Bash output above this size is condensed (`0` turns it off) |
| `NDL_OUTLINE_MAX` | 220 | Maximum rows in an outline reply |
| `NDL_ROOT` | nearest `.git` | Forces the project root |

State lives in `.needlepoint/`, which ships with its own `.gitignore`. Commit `.needlepoint/notes.json` to share verified knowledge with your team.

## Safety and privacy

- Fully local: no network and no telemetry. Hooks run Python in isolated mode (`-I`), so a repository can't hijack them by planting modules.
- Fail-open: an internal error means the tool behaves exactly as if needlepoint weren't installed.
- Every denial says why and how to bypass it, so Claude never gets stuck.

## Limits (honest)

- Search is lexical plus structural (BM25F, identifier splitting, reference-graph prior), with no embeddings. That makes it instant, deterministic and dependency-free, but a phrasing with zero word overlap can miss. Claude is told to rephrase or fall back to `rg`.
- Non-Python parsing is heuristic. Odd formatting can shift a symbol's end line, and the outline's gap rows make this visible.
- Callee and caller links are name-based, not type-resolved.

## Development

```
python3 -m unittest discover -s tests -v                       # 38 tests (also: NDL_NO_SQLITE=1 for the JSON backend)
python3 plugins/needlepoint/skills/needlepoint/scripts/ndl.py bench --root /path/to/repo
python3 bench/eval_queries.py /path/to/copy/of/cpython/Lib
claude plugin validate . && claude plugin validate ./plugins/needlepoint
```

MIT licensed.
