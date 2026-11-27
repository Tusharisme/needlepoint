# needlepoint — lossless precision context

```
/plugin install needlepoint --marketplace Tusharisme/needlepoint
```

needlepoint cuts Claude Code's token use without losing accuracy. It keeps unneeded text out of context instead of compressing what is already there.

- **Hooks (automatic):** whole-file Reads of large files return a full-coverage outline first. Re-reads of unchanged code are deduped, and re-reads after an edit return only the diff. Huge Bash output is condensed (every error, failure and traceback kept) and the full log is saved. Repeating any intercepted call goes through, and errors fail open.
- **Skill + CLI (`ndl`):** `map`, `find` (ranked symbol search), `show` (exact symbol source, `--grep` for a focused view), `grep` (ripgrep tagged with enclosing functions), `outline`, `refs`, `pack` (one-shot task context within a token budget), `run`/`log`, hash-anchored `note`s that flag themselves stale, `stats` and `bench`.

Measured: about 30x fewer tokens to read a known symbol on large codebases, 88–98% smaller big-file views with 100% line coverage, 95% smaller test logs with all failures kept, and 100% correct-file-in-top-5 retrieval vs 83% for a grep-style baseline.

Zero dependencies (Python 3.8+ standard library), fully local, no telemetry. See the repository README for benchmarks, configuration and limits.
