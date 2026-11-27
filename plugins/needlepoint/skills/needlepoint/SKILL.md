---
name: needlepoint
description: Lossless precision context for code - explore and edit repositories with far fewer tokens and no loss of accuracy. Use whenever you need to locate, read, understand, trace or change code in a repo with more than a handful of files - "where is X implemented", reading a function or class, callers/callees, getting oriented in an unfamiliar codebase, opening large files, or running tests/builds with long output. Replaces grep + whole-file Read loops with ranked symbol search, exact symbol extraction, focused views inside big functions, full-coverage outlines, one-shot task packs, condensed command output that keeps every error, and hash-anchored notes that flag themselves stale.
---

# needlepoint — read less, miss nothing

Most of a coding session's tokens go to text that is never needed: whole files read for one function, grep floods, re-reads of unchanged code, 800-line test logs. needlepoint stops those tokens *entering* context instead of compressing what's there, so nothing you rely on is paraphrased or lost.

**CLI:** `ndl <command>` (the plugin puts it on PATH; `$NDL` works too). If `ndl` isn't found (the plugin was installed mid-session, so the SessionStart hook hasn't run yet), use `sh <this-skill-dir>/scripts/ndl <command>`. In PowerShell on Windows `ndl` is never on PATH: run `& '<this-skill-dir>\scripts
dl.ps1' <command>` (`ndl.cmd` serves cmd.exe). The index builds and refreshes itself incrementally. A first build takes seconds on a typical repo, or about 1.5 minutes on a 20k-file monorepo.

## Workflow

1. **Orient** (unfamiliar repo): `ndl map` — central files with their top symbols, within a token budget. Add `--focus "payments"` to centre the map on a topic.
2. **Locate**: `ndl find "words describing the behaviour"` returns ranked `path:start-end kind Name signature — doc` lines. Use both plain words and likely identifiers. If it misses, rephrase (2-3 tries) before falling back to `ndl grep`. Filters: `--in src/api`, `--kind class`.
3. **Understand in one call**: `ndl pack "the task or question" --budget 5000` gives exact line-numbered source for the most relevant code, what it calls, who calls it, and the other candidates.
4. **Read precisely**: `ndl show Name Class.method Class.attr path:Name path:120-180` (several targets in one call). Long Python docstrings fold into an explicit `⋯ docstring lines A-B folded` marker; use `--full` when the docs matter. `ndl outline path` shows a file's skeleton, where every line belongs to a listed range.
5. **Ask about a detail inside big code**: `ndl show Name --grep 'status|tuple' -C 3` returns only the matching lines inside that symbol, with exact line numbers. `ndl grep 'RX' [paths]` is ripgrep with each hit tagged by its enclosing function. Prefer these to a full `show` for one detail; on real projects they cost half as much.
6. **Check impact**: `ndl refs name` lists usages grouped by enclosing function, with `[DEF]` marking definitions.
7. **Edit**: call the Read tool with `offset`/`limit` set to the exact range you were given (a ranged Read satisfies Edit's read-first rule), then Edit.
8. **Run noisy commands**: `ndl run <cmd>` returns the exit code plus condensed output (errors, failures, tracebacks and summary kept; repeats collapsed) and a log id. Query the full log with `ndl log <id> --grep 'RX'` or `--lines A-B`.

## Rules that protect accuracy

- `show` and `pack` print **exact source with real line numbers**, never summaries. Signatures and outlines are only pointers: read the code before claiming what it does.
- One miss from `find` doesn't prove something is absent. Rephrase, or use `refs` or `grep`.
- If the top hit doesn't fully answer the question, look at the "other candidates" line.
- `show -f` reprints content that was deduped. Repeating a Read verbatim always goes through.
- After you learn something non-obvious (an invariant, a gotcha, why the code is odd), save it with `ndl note add path:Name "insight"`. Notes are anchored to a hash of that code. They appear next to it in show, pack and outline, and they flip to **⚠ STALE** when the code changes. Re-verify a stale note before relying on it. The notes file `.needlepoint/notes.json` can be committed so the team shares it.

## Automatic hooks (Claude Code plugin)

- **Read guard**: a whole-file Read of a large source file returns its full-coverage outline instead. Read the ranges you need, or repeat the identical Read to load everything.
- **Re-read dedupe**: re-reading content that is unchanged since you read it this session returns a one-line notice. If the file changed since, for example after your own Edit, you get only the diff.
- **Bash condense**: tool output over 12k characters is condensed the same way as `run`, and the full log is kept.
- These are safe by design. Every denial explains itself, repeating the call bypasses it, state resets on compaction, and errors fail open. Tune them with `NDL_READ_GUARD_TOKENS` and `NDL_BASH_CONDENSE_CHARS` (`0` turns either off).

Other commands: `ndl stats` (tokens saved), `ndl bench` (measure on this repo), `ndl doctor`. All flags are in `references/commands.md`; design notes are in `references/how-it-works.md`.
