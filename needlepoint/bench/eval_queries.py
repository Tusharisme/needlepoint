#!/usr/bin/env python3
"""Realistic-query retrieval eval on the CPython 3.11 stdlib.

usage: python3 bench/eval_queries.py /path/to/copy/of/python3.11/lib
Compares needlepoint `find` with a grep-style baseline (rank files by how many
query words they contain - roughly what an agent gets from a few rg calls).
"""
import os, re, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "needlepoint", "skills", "needlepoint", "scripts"))
import ndl  # noqa: E402

Q = [
    ("parse an http request line and headers", ["http/server.py:BaseHTTPRequestHandler.parse_request"]),
    ("compute longest matching block between two sequences", ["difflib.py:SequenceMatcher.find_longest_match"]),
    ("produce unified diff output", ["difflib.py:unified_diff"]),
    ("authenticate to smtp server with username and password", ["smtplib.py:SMTP.login"]),
    ("copy a directory tree recursively", ["shutil.py:copytree"]),
    ("wrap text paragraph to a given width", ["textwrap.py:wrap", "textwrap.py:TextWrapper.wrap", "textwrap.py:fill"]),
    ("read csv rows as dictionaries", ["csv.py:DictReader"]),
    ("submit task to thread pool executor", ["concurrent/futures/thread.py:ThreadPoolExecutor.submit"]),
    ("url encode query parameters", ["urllib/parse.py:urlencode"]),
    ("split url into scheme netloc path", ["urllib/parse.py:urlsplit", "urllib/parse.py:urlparse"]),
    ("least recently used cache decorator", ["functools.py:lru_cache"]),
    ("serialize object to json string", ["json/__init__.py:dumps", "json/encoder.py:JSONEncoder.encode"]),
    ("base64 encode bytes", ["base64.py:b64encode"]),
    ("temporary directory removed automatically", ["tempfile.py:TemporaryDirectory"]),
    ("match filename against shell wildcard pattern", ["fnmatch.py:fnmatch"]),
    ("push item onto heap", ["heapq.py:heappush"]),
    ("insert into sorted list keeping it sorted", ["bisect.py:insort_right", "bisect.py:insort_left"]),
    ("add a command line option to the parser", ["argparse.py:_ActionsContainer.add_argument"]),
    ("rotate log file when it reaches a size", ["logging/handlers.py:RotatingFileHandler"]),
    ("deep copy an object recursively", ["copy.py:deepcopy"]),
    ("gzip compress bytes in memory", ["gzip.py:compress"]),
    ("random sample without replacement", ["random.py:Random.sample"]),
    ("check whether a path is a symbolic link", ["posixpath.py:islink", "genericpath.py:islink"]),
    ("walk a directory tree top down", ["os.py:walk"]),
    ("create tuple subclass with named fields", ["collections/__init__.py:namedtuple"]),
    ("count how many times each element occurs", ["collections/__init__.py:Counter"]),
    ("run a command and capture its output with timeout", ["subprocess.py:run"]),
    ("tls context with sane default certificate verification", ["ssl.py:create_default_context"]),
    ("parse xml document from a string", ["xml/etree/ElementTree.py:XML", "xml/etree/ElementTree.py:fromstring"]),
    ("pretty print nested data structure", ["pprint.py:pprint", "pprint.py:PrettyPrinter"]),
    ("suggest close matches for a misspelled word", ["difflib.py:get_close_matches"]),
    ("read ini config file", ["configparser.py:RawConfigParser.read"]),
    ("blocking queue get with timeout", ["queue.py:Queue.get"]),
    ("convert html character references to unicode", ["html/__init__.py:unescape"]),
    ("percent decode a url component", ["urllib/parse.py:unquote"]),
]
STOPW = ndl.STOP

def main(root):
    idx, _ = ndl.build_index(root)
    # grep-style baseline corpus
    texts = {rel: open(os.path.join(root, rel), encoding="utf-8", errors="replace").read().lower()
             for rel, r in idx.files.items() if r["l"] == "py"}
    s1 = s5 = f5_ndl = f5_grep = 0
    t = time.time()
    for q, gold in Q:
        gfiles = {g.split(":")[0] for g in gold}
        res = ndl.search(idx, q, k=20)
        names = []
        for _, rel, si in res:
            if si >= 0: names.append(rel + ":" + idx.qname(idx.files[rel]["s"][si]))
        hit = [i for i, n in enumerate(names) if n in gold]
        if hit and hit[0] == 0: s1 += 1
        if hit and hit[0] < 5: s5 += 1
        files = list(dict.fromkeys(r for _, r, _ in res))[:5]
        if gfiles & set(files): f5_ndl += 1
        words = [w for w in re.findall(r"[a-z]+", q.lower()) if w not in STOPW and len(w) > 2]
        sc = sorted(texts, key=lambda r: (-sum(1 for w in words if w in texts[r]), -sum(texts[r].count(w) for w in words) / (1 + len(texts[r]) / 1000)))
        if gfiles & set(sc[:5]): f5_grep += 1
        print("%-58s %s" % (q[:58], ("@%d" % (hit[0] + 1)) if hit else "miss  top: " + (names[0] if names else "-")))
    n = len(Q)
    print("\nneedlepoint symbol recall@1 %d/%d (%.0f%%)  recall@5 %d/%d (%.0f%%)" % (s1, n, 100 * s1 / n, s5, n, 100 * s5 / n))
    print("file recall@5: needlepoint %d/%d (%.0f%%)  vs grep-style baseline %d/%d (%.0f%%)" % (f5_ndl, n, 100 * f5_ndl / n, f5_grep, n, 100 * f5_grep / n))
    print("%.0f ms/query" % (1000 * (time.time() - t) / n))

if __name__ == "__main__":
    main(os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "."))
