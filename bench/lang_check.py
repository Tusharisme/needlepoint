#!/usr/bin/env python3
"""Per-language quality check on real repos (no ground-truth parser needed).

usage: python3 bench/lang_check.py <lang>=<repo_dir> [...]
Reports, per repo: symbols found, definition recall (vs language keyword lines), brace-balanced
symbol bodies, partial overlaps, and crashes from every command and hook on the largest files.
"""
import json, os, random, re, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "plugins", "needlepoint", "skills", "needlepoint", "scripts", "ndl.py")
sys.path.insert(0, os.path.dirname(SCRIPT))
import ndl  # noqa: E402

# lines that certainly start a definition, per ndl language key (used for a recall proxy)
DEF = {
    "go": r"^func\s|^type\s+\w+\s+(struct|interface)\b",
    "rs": r"^\s*(pub(\([^)]*\))?\s+)?(async\s+|const\s+|unsafe\s+)*fn\s+\w+|^\s*(pub(\([^)]*\))?\s+)?(struct|enum|trait)\s+\w+",
    "java": r"^\s*(public|private|protected)\s+(static\s+)?(final\s+)?(abstract\s+)?(class|interface|enum|record)\s+\w+",
    "kt": r"^\s*((public|private|internal|protected|override|open|suspend|inline|actual|expect|operator)\s+)*fun\s+|^\s*((public|private|internal|data|sealed|abstract|open|enum|actual|expect)\s+)*(class|interface|object)\s+\w+",
    "swift": r"^\s*((public|private|internal|open|fileprivate|final|static|override|mutating|@\w+)\s+)*func\s+\w+|^\s*((public|private|internal|open|final)\s+)*(class|struct|enum|protocol|extension)\s+\w+",
    "php": r"^\s*((public|private|protected|static|final|abstract)\s+)*function\s+\w+|^\s*((final|abstract)\s+)*(class|interface|trait)\s+\w+",
    "rb": r"^\s*def\s+[\w.?!=]+|^\s*(class|module)\s+[A-Z]",
    "scala": r"^\s*((private|protected|final|override|implicit)\s+)*def\s+\w+|^\s*((final|sealed|abstract|case)\s+)*(class|object|trait)\s+\w+",
    "lua": r"^\s*(local\s+)?function\s+[\w.:]+",
    "sh": r"^\s*(function\s+)?[A-Za-z_][\w-]*\s*\(\)\s*\{?",
    "js": r"^\s*(export\s+)?(default\s+)?(async\s+)?function\s*\*?\s*\w+|^\s*(export\s+)?(default\s+)?(abstract\s+)?class\s+\w+",
    "c": r"^[A-Za-z_][\w\s\*]*\b\w+\s*\([^;]*\)\s*\{?\s*$",
    "py": r"^\s*(async\s+)?(def|class)\s+\w+",
}
BRACE = {"go", "rs", "java", "kt", "swift", "php", "js", "c", "scala", "sh"}

def check(lang, root):
    t = time.time()
    idx, _ = ndl.build_index(root, force=True)
    build = time.time() - t
    files = {r: v for r, v in idx.files.items() if v["l"] not in ("md", "data", "txt", "yaml", "toml", "css", "make")}
    nsym = sum(len(v["s"]) for v in files.values())
    # recall proxy
    want = got = 0; misses = []
    for rel, rec in files.items():
        rx = DEF.get(rec["l"])
        if not rx: continue
        lines = ndl.read_lines(root, rel)
        starts = {s[2] for s in rec["s"]}
        code = ndl.code_only("\n".join(lines), rec["l"]).split("\n")
        for i, l in enumerate(code, 1):
            if i <= len(lines) and re.match(rx, l) and lines[i - 1].strip() == l.strip():
                if rec["l"] == "c" and re.match(r"^\s*(if|for|while|switch|return|else)\b", l): continue
                want += 1
                if i in starts or (i - 1) in starts or any(s[2] <= i <= s[2] + 3 and s[1] in ("function", "method", "decl") for s in rec["s"]): got += 1
                elif len(misses) < 6: misses.append("%s:%d %s" % (rel, i, l.strip()[:90]))
    # brace balance of bodies + partial overlaps
    bal = tot = 0; bad = []; overl = 0
    for rel, rec in files.items():
        lines = ndl.read_lines(root, rel)
        ss = sorted(rec["s"], key=lambda s: (s[2], -s[3]))
        for i, a in enumerate(ss):
            for b in ss[i + 1:i + 40]:
                if b[2] > a[3]: break
                if b[3] > a[3]: overl += 1
        if rec["l"] not in BRACE: continue
        for s in rec["s"]:
            if s[1] not in ("function", "method", "class", "impl", "test"): continue
            body = ndl.code_only("\n".join(lines[s[2] - 1:s[3]]), rec["l"])
            if "{" not in body: continue
            tot += 1
            body = re.sub(r"'(?:\\.|[^'\\\n])'", "", body)  # char literals
            ok = body.count("{") == body.count("}") and "}" in lines[s[3] - 1]
            bal += ok
            if not ok and len(bad) < 5: bad.append("%s:%d-%d %s" % (rel, s[2], s[3], ndl.idx_q(s) if hasattr(ndl, "idx_q") else s[0]))
    # crash test every command + hooks on the biggest files
    crashes = []
    big = sorted(files, key=lambda r: -files[r]["t"])[:4]
    syms = [(r, s) for r in files for s in files[r]["s"] if s[1] in ("function", "method", "class")]
    random.Random(1).shuffle(syms)
    cmds = [["map", "--budget", "600"], ["find", "parse value from string"], ["find", "error handling"],
            ["pack", "how are errors reported", "--budget", "2500"], ["grep", "TODO|FIXME"]]
    cmds += [["outline", r] for r in big]
    cmds += [["show", "%s:%s" % (r, idx.qname(s))] for r, s in syms[:5]]
    cmds += [["show", "%s:%s" % (r, idx.qname(s)), "--grep", "return", "-C", "1"] for r, s in syms[5:7]]
    cmds += [["refs", s[0]] for _, s in syms[7:9]]
    for c in cmds:
        p = subprocess.run([sys.executable, "-I", SCRIPT, "--root", root] + c, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode != 0 or "Traceback" in p.stderr:
            crashes.append("%s: %s" % (" ".join(c)[:60], (p.stderr.strip().splitlines() or ["?"])[-1][:120]))
    for r in big:
        for ev, extra in (("pre-read", {}), ("post-read", {"tool_response": {}})):
            pay = dict({"session_id": "LC", "cwd": root, "tool_name": "Read", "tool_input": {"file_path": os.path.join(root, r)}}, **extra)
            p = subprocess.run([sys.executable, "-I", SCRIPT, "hook", ev], input=json.dumps(pay), capture_output=True, text=True,
                               env=dict(os.environ, NDL_DEBUG="1"))
            if p.returncode != 0: crashes.append("hook %s %s: %s" % (ev, r, (p.stderr.strip().splitlines() or ["?"])[-1][:120]))
            elif p.stdout.strip(): json.loads(p.stdout)
    return dict(lang=lang, files=len(files), syms=nsym, build=build, recall=(got, want), bal=(bal, tot), overl=overl,
                crashes=crashes, misses=misses, bad=bad)

if __name__ == "__main__":
    rows = []
    for arg in sys.argv[1:]:
        lang, _, root = arg.partition("=")
        r = check(lang, os.path.abspath(root)); rows.append(r)
        print("%-7s files %4d  syms %6d  build %4.1fs  def-recall %5s  balanced %5s  overlaps %d  crashes %d" % (
            lang, r["files"], r["syms"], r["build"],
            "%.0f%%" % (100 * r["recall"][0] / r["recall"][1]) if r["recall"][1] else "n/a",
            "%.0f%%" % (100 * r["bal"][0] / r["bal"][1]) if r["bal"][1] else "n/a", r["overl"], len(r["crashes"])), flush=True)
        for k in ("crashes", "misses", "bad"):
            for x in r[k]: print("     %s: %s" % (k[:-2] if k != "bad" else "unbalanced", x))
