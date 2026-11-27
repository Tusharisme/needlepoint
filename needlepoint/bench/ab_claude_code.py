#!/usr/bin/env python3
"""End-to-end A/B: the same questions answered by Claude Code with and without needlepoint.

usage (any OS, any shell — run from the repo root):
  python bench/ab_claude_code.py --real --runs 2 --model sonnet      # Flask + Express, 8 real-project questions
  python bench/ab_claude_code.py --setup --runs 3 --model sonnet     # builds a stdlib test repo in your temp dir
  python bench/ab_claude_code.py C:/path/to/repo --tasks my_tasks.json

Needs a logged-in `claude` CLI. Reports total tokens processed (input + cache read + cache write + output),
cost, turns and answer accuracy per mode. Tasks have regex-checkable answers on the CPython stdlib; pass
--tasks my_tasks.json ([{"q": "...", "check": "regex"}]) to use your own repo.
"""
import argparse, json, os, re, shutil, statistics, subprocess, sys, sysconfig, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(HERE, "..", "plugins", "needlepoint")
REAL = [("flask", "https://github.com/pallets/flask.git", "49943b5a94413a530761350f04e896f76e8d620e"),
        ("express", "https://github.com/expressjs/express.git", "43fca52d28a422c8be48fd489cbd4f2d6152bab6")]
TASKS = [
    {"q": "In difflib.get_close_matches, which three SequenceMatcher methods are used as successive cutoff filters, in order? Reply with only the three method names.",
     "check": r"real_quick_ratio[\s\S]*\bquick_ratio[\s\S]*\bratio"},
    {"q": "In logging/handlers.py, what does RotatingFileHandler.shouldRollover return when maxBytes is 0 (assume the file exists)? Reply in one short sentence.",
     "check": r"\b(0|False|zero)\b"},
    {"q": "What exception does functools.lru_cache raise when maxsize is neither an int, None nor a callable? Reply with only the exception name.",
     "check": r"TypeError"},
    {"q": "What is the default value of the protocol_version attribute of http.server.BaseHTTPRequestHandler? Reply with only the value.",
     "check": r"HTTP/1\.0"},
    {"q": "In shutil.copytree, what is the default copy_function and the default value of dirs_exist_ok? Reply in one line.",
     "check": r"copy2[\s\S]*False|False[\s\S]*copy2"},
    {"q": "In the json module, when indent is not None and separators is not given, what item separator and key separator does JSONEncoder use? Reply with only the two strings.",
     "check": r"','[\s\S]*': '|\",\"[\s\S]*\": \""},
]

def run(task, repo, mode, model):
    cmd = [shutil.which("claude") or "claude", "-p", task["q"], "--output-format", "json", "--max-turns", "30",
           "--allowedTools", "Read,Grep,Glob,Bash"]
    if model: cmd += ["--model", model]
    if mode == "needlepoint": cmd += ["--plugin-dir", os.path.abspath(PLUGIN)]
    t = time.time()
    p = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    try:
        d = json.loads(p.stdout)
    except ValueError:
        return {"ok": False, "tokens": 0, "cost": 0, "turns": 0, "secs": time.time() - t, "err": (p.stderr or p.stdout)[-300:]}
    u = d.get("usage") or {}
    toks = sum(u.get(k, 0) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"))
    return {"ok": bool(re.search(task["check"], d.get("result") or "")), "tokens": toks, "cost": d.get("total_cost_usd") or 0,
            "turns": d.get("num_turns") or 0, "secs": time.time() - t, "answer": (d.get("result") or "")[:160]}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", nargs="?"); ap.add_argument("--runs", type=int, default=2); ap.add_argument("--model")
    ap.add_argument("--setup", action="store_true", help="copy this Python's stdlib into a temp dir and use it as the repo")
    ap.add_argument("--real", action="store_true", help="clone Flask + Express at pinned commits and run bench/real/tasks.json")
    ap.add_argument("--tasks"); ap.add_argument("--out", default="ab_results.json")
    a = ap.parse_args()
    if a.real:
        base = os.path.join(tempfile.gettempdir(), "ndl_real")
        os.makedirs(base, exist_ok=True)
        for name, url, sha in REAL:
            d = os.path.join(base, name)
            if not os.path.isdir(d):
                print("cloning", url, "...")
                subprocess.run(["git", "clone", "-q", url, d], check=True)
            subprocess.run(["git", "-C", d, "checkout", "-q", sha], check=True)
        shutil.copy(os.path.join(HERE, "real", "tasks.json"), os.path.join(base, "tasks.json"))
        a.tasks = os.path.join(base, "tasks.json")
    if a.tasks and not a.repo: a.repo = os.path.dirname(os.path.abspath(a.tasks))
    if a.setup or not a.repo:
        a.repo = os.path.join(tempfile.gettempdir(), "ndl_pystd")
        if not os.path.isdir(a.repo):
            print("copying stdlib to", a.repo, "...")
            shutil.copytree(sysconfig.get_paths()["stdlib"], a.repo, ignore=shutil.ignore_patterns("site-packages", "__pycache__"))
            subprocess.run(["git", "init", "-q"], cwd=a.repo)
    if not shutil.which("claude"): sys.exit("claude CLI not found on PATH")
    tasks = json.load(open(a.tasks)) if a.tasks else TASKS
    if not a.tasks:
        subprocess.run([sys.executable, "-I", os.path.join(PLUGIN, "skills", "needlepoint", "scripts", "ndl.py"), "index", "--root", a.repo])
    rows = []
    for r in range(a.runs):
        for i, t in enumerate(tasks):
            for mode in (("baseline", "needlepoint") if r % 2 == 0 else ("needlepoint", "baseline")):
                repo = os.path.join(os.path.dirname(os.path.abspath(a.tasks)), t["repo"]) if t.get("repo") and a.tasks else a.repo
                res = run(t, repo, mode, a.model); res.update(task=i, mode=mode, run=r); rows.append(res)
                print("run %d task %d %-11s ok=%-5s tokens=%-9s turns=%-3s $%.4f %s" % (r, i, mode, res["ok"], res["tokens"], res["turns"], res["cost"], res.get("err", "")), flush=True)
    with open(a.out, "w", encoding="utf-8") as f: json.dump(rows, f, indent=1)
    print("\nmode          accuracy   median tokens   total tokens   total $   median turns")
    agg = {}
    for mode in ("baseline", "needlepoint"):
        m = [x for x in rows if x["mode"] == mode]
        agg[mode] = sum(x["tokens"] for x in m)
        print("%-12s  %5.0f%%     %12s   %12s   %7.3f   %5s" % (mode, 100 * sum(x["ok"] for x in m) / max(1, len(m)),
              "{:,}".format(int(statistics.median([x["tokens"] for x in m]))), "{:,}".format(agg[mode]),
              sum(x["cost"] for x in m), statistics.median([x["turns"] for x in m])))
    if agg.get("baseline"):
        print("\nneedlepoint used %.0f%% of baseline tokens" % (100 * agg["needlepoint"] / agg["baseline"]))

if __name__ == "__main__":
    main()
