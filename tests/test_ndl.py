#!/usr/bin/env python3
"""needlepoint test-suite (stdlib unittest).  run: python3 -m unittest discover -s tests -v"""
import io, json, os, shutil, subprocess, sys, tempfile, textwrap, unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "plugins", "needlepoint", "skills", "needlepoint", "scripts", "ndl.py")
sys.path.insert(0, os.path.dirname(SCRIPT))
import ndl  # noqa: E402

FILES = {
    "app/server.py": '''
        """HTTP server for the demo app."""
        import os

        MAX_RETRIES = 3

        class RequestHandler:
            """Parse incoming requests and dispatch them."""

            def parse_request(self, raw):
                """Parse the request line and the headers."""
                line, _, rest = raw.partition("\\n")
                return line.split(" "), self.parse_headers(rest)

            def parse_headers(self, text):
                return dict(l.split(": ", 1) for l in text.splitlines() if l)

        def retry_with_backoff(fn, attempts=MAX_RETRIES):
            """Call fn, retrying with exponential backoff."""
            for i in range(attempts):
                try:
                    return fn()
                except OSError:
                    pass
        ''',
    "web/client.ts": '''
        export interface Options { retries: number }
        /** Fetch JSON with retries. */
        export async function getJson(url: string, opts: Options): Promise<unknown> {
          const re = /\\/\\//g
          if (/}/.test(url)) { throw new Error("bad") }
          return fetch(url).then(r => r.json())
        }
        export class Api {
          constructor(base: string) { this.base = base }
          async user(id: string) {
            return getJson(this.base + "/users/" + id, { retries: 2 })
          }
        }
        export default function App() {
          return <div>{items.map(i => <Item key={i} />)}</div>
        }
        ''',
    "web/legacy.js": '''
        var res = Object.create(proto)

        /**
         * Send a response.
         */

        res.send = function send(body) {
          if (typeof body === 'object') { return this.json(body) }
          sendfile(res, file, opts, function (err) {
            done(err)
          })
        }
        ''',
    "svc/main.go": '''
        package svc

        // Store keeps things.
        type Store struct{ m map[string]string }

        func (s *Store) Get(k string) (string, bool) {
        	v, ok := s.m[k]
        	if v == "{" {
        		return "", false
        	}
        	return v, ok
        }
        ''',
    "core/lib.rs": '''
        pub struct Point<'a> { name: &'a str }
        impl<'a> Point<'a> {
            pub fn new(name: &'a str) -> Self {
                let c = '{';
                Point { name }
            }
        }
        ''',
    "README.md": '''
        # Demo
        Intro text.
        ## Install
        run it
        ''',
}


def mkrepo():
    d = tempfile.mkdtemp(prefix="ndltest")
    for rel, body in FILES.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(textwrap.dedent(body).lstrip("\n"))
    os.makedirs(os.path.join(d, ".git"))  # root marker; walk fallback lists files
    return d


def rm_repo(root, *idxs):
    """close sqlite handles first: Windows cannot delete an open index.db."""
    import gc
    for i in idxs:
        if getattr(i, "con", None) is not None: i.con.close()
    for sr in list(getattr(ndl, "_SEARCH", {}).values()):
        if getattr(sr, "con", None) is not None: sr.con.close()
    getattr(ndl, "_SEARCH", {}).clear()
    gc.collect()
    shutil.rmtree(root)


def run_cli(root, *args, env=None):
    e = dict(os.environ); e.update(env or {})
    r = subprocess.run([sys.executable, "-I", SCRIPT, "--root", root] + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace", env=e)
    return r.stdout + r.stderr


def hook(root, event, payload, env=None):
    e = dict(os.environ, NDL_NO_WARM="1"); e.update(env or {})
    payload.setdefault("cwd", root); payload.setdefault("session_id", "T")
    r = subprocess.run([sys.executable, "-I", SCRIPT, "hook", event], input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8", errors="replace", env=e)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else None


class TestParsing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = mkrepo()
        cls.idx, _ = ndl.build_index(cls.root)

    @classmethod
    def tearDownClass(cls):
        rm_repo(cls.root, cls.idx)

    def syms(self, rel):
        return {self.idx.qname(s): (s[1], s[2], s[3]) for s in self.idx.files[rel]["s"]}

    def test_python_exact_ranges(self):
        s = self.syms("app/server.py")
        self.assertEqual(s["RequestHandler"][0], "class")
        self.assertEqual(s["RequestHandler.parse_request"][:1], ("method",))
        self.assertIn("retry_with_backoff", s)
        self.assertEqual(s["MAX_RETRIES"][0], "var")

    def test_ts_regex_jsx_and_classes(self):
        s = self.syms("web/client.ts")
        self.assertEqual(s["getJson"][1:], (3, 7))       # regex literals with braces/slashes
        self.assertEqual(s["Api"][1:], (8, 13))
        self.assertIn("Api.user", s)
        self.assertIn("Api.constructor", s)             # one-line method
        self.assertEqual(s["App"][1:], (14, 16))         # JSX self-closing + closing tags

    def test_js_property_assigned_functions(self):
        s = self.syms("web/legacy.js")
        self.assertEqual(s["res.send"][1:], (7, 12))
        self.assertNotIn("res.send.sendfile", s)          # a call, not a definition
        doc = [x for x in self.idx.files["web/legacy.js"]["s"] if x[0] == "send"][0][6]
        self.assertEqual(doc, "Send a response.")

    def test_go_method_receiver(self):
        s = self.syms("svc/main.go")
        self.assertEqual(s["Store.Get"][1:], (6, 12))    # brace inside string ignored

    def test_rust_lifetimes_and_char_literals(self):
        s = self.syms("core/lib.rs")
        self.assertIn("Point.new", s)                    # 'new' is a keyword elsewhere, not after fn
        self.assertEqual(s["Point.new"][1:], (3, 6))

    def test_markdown_sections(self):
        s = self.syms("README.md")
        self.assertIn("Demo.Install", s)

    def test_outline_covers_every_line(self):
        with open(os.path.join(self.root, "flat.py"), "w") as f:
            for i in range(700): f.write("def f%d(x):\n    return x\n\n" % i)
        self.idx, _ = ndl.build_index(self.root)
        for rel in list(FILES) + ["flat.py"]:
            rec = self.idx.files[rel]
            text = ndl.outline_text(self.idx, rel)
            import re
            covered = set()
            for a, b in re.findall(r"^\s+(\d+)-(\d+)\s", text, re.M):
                covered.update(range(int(a), int(b) + 1))
            lines = ndl.read_lines(self.root, rel)
            nonblank = {i for i, l in enumerate(lines, 1) if l.strip(" \t\"'#/*-=!")}
            self.assertTrue(nonblank <= covered, (rel, sorted(nonblank - covered)))


class TestRetrieval(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = mkrepo()
        cls.idx, _ = ndl.build_index(cls.root)

    @classmethod
    def tearDownClass(cls):
        rm_repo(cls.root, cls.idx)

    def top(self, q):
        _, rel, si = ndl.search(self.idx, q, k=1)[0]
        return rel + ":" + self.idx.qname(self.idx.files[rel]["s"][si])

    def test_find_by_description(self):
        self.assertEqual(self.top("parse request line and headers"), "app/server.py:RequestHandler.parse_request")
        self.assertEqual(self.top("exponential backoff"), "app/server.py:retry_with_backoff")
        self.assertEqual(self.top("fetch json"), "web/client.ts:getJson")

    def test_search_without_sqlite_fallback(self):
        saved = ndl.sqlite3
        try:
            ndl.sqlite3 = None; ndl._SEARCH.clear()
            self.assertEqual(self.top("exponential backoff"), "app/server.py:retry_with_backoff")
        finally:
            ndl.sqlite3 = saved; ndl._SEARCH.clear()

    def test_search_overlay_after_edit(self):
        if ndl.sqlite3 is None: self.skipTest("sqlite backend only")
        run_cli(self.root, "find", "backoff")                               # base search data exists
        state = os.path.join(self.root, ".needlepoint")
        base = sorted(x for x in os.listdir(state) if x.startswith("search-"))
        p = os.path.join(self.root, "app", "fresh.py")
        with open(p, "w") as f:
            f.write('def reconcile_ledger_entries(book):\n    """Match ledger entries."""\n    return book\n')
        self.assertIn("reconcile_ledger_entries", run_cli(self.root, "find", "reconcile ledger entries"))
        self.assertIn("retry_with_backoff", run_cli(self.root, "find", "exponential backoff"))
        self.assertEqual(base, sorted(x for x in os.listdir(state) if x.startswith("search-")))  # no base rebuild
        os.remove(p)
        self.assertNotIn("reconcile_ledger_entries", run_cli(self.root, "find", "reconcile ledger entries"))

    def test_resolve_targets(self):
        self.assertEqual(ndl.resolve(self.idx, "Store.Get")[0][2:], (6, 12))
        self.assertEqual(ndl.resolve(self.idx, "web/client.ts:user")[0][1][0], "user")
        self.assertEqual(ndl.resolve(self.idx, "app/server.py:3-4")[0][2:], (3, 4))

    def test_show_is_exact_source(self):
        out = run_cli(self.root, "show", "retry_with_backoff")
        lines = ndl.read_lines(self.root, "app/server.py")
        for ln in out.splitlines()[1:]:
            n, _, code = ln.partition("\t")
            if not n: continue  # fold marker line
            self.assertEqual(code, lines[int(n) - 1].rstrip())

    def test_class_attribute_symbols(self):
        p = os.path.join(self.root, "app", "cfg.py")
        with open(p, "w") as f:
            f.write('class Settings:\n    """Config."""\n    timeout: int = 30\n    mode = "fast"\n\n    def get(self):\n        return self.mode\n')
        idx, _ = ndl.build_index(self.root)
        self.assertEqual(ndl.resolve(idx, "Settings.mode")[0][2:], (4, 4))
        self.assertEqual(ndl.resolve(idx, "Settings.timeout")[0][1][1], "attr")
        os.remove(p); ndl.build_index(self.root)

    def test_docstring_folding_is_explicit(self):
        p = os.path.join(self.root, "app", "docs.py")
        with open(p, "w") as f:
            f.write('def documented(x):\n    """Summary line.\n\n' + "".join("    detail %d\n" % i for i in range(12)) + '    """\n    return x + 1\n')
        ndl.build_index(self.root)
        out = run_cli(self.root, "show", "documented")
        self.assertIn("docstring lines 4-16 folded", out)
        self.assertIn("return x + 1", out)
        self.assertIn("Summary line.", out)
        self.assertIn("detail 11", run_cli(self.root, "show", "documented", "--full"))
        os.remove(p); ndl.build_index(self.root)

    def test_pack_includes_callee(self):
        out = run_cli(self.root, "pack", "parse request", "--budget", "1500")
        self.assertIn("RequestHandler.parse_request", out)
        self.assertIn("parse_headers", out)

    def test_show_focus_and_grep(self):
        out = run_cli(self.root, "show", "retry_with_backoff", "--grep", "except", "-C", "0")
        self.assertIn("except OSError", out)
        self.assertNotIn("for i in range", out)
        self.assertIn("of 7 lines shown", out)
        g = run_cli(self.root, "grep", "retry|backoff", "app/server.py")
        self.assertIn("[DEF retry_with_backoff]", g)

    def test_refs(self):
        out = run_cli(self.root, "refs", "getJson")
        self.assertIn("[DEF]", out)
        self.assertIn("[in Api.user]", out)

    def test_incremental_reindex(self):
        p = os.path.join(self.root, "app", "extra.py")
        with open(p, "w") as f: f.write("def brand_new_function():\n    return 1\n")
        idx, n = ndl.build_index(self.root)
        self.assertEqual(n, 1)
        self.assertTrue(ndl.resolve(idx, "brand_new_function"))
        os.remove(p)


class TestNotes(unittest.TestCase):
    def test_stale_detection(self):
        root = mkrepo()
        try:
            out = run_cli(root, "note", "add", "Store.Get", "returns false for '{'")
            self.assertIn("anchored", out)
            self.assertIn("✓", run_cli(root, "note", "ls"))
            p = os.path.join(root, "svc", "main.go")
            with open(p) as f: s = f.read().replace('return "", false', 'return "x", false')
            with open(p, "w") as f: f.write(s)
            self.assertIn("STALE", run_cli(root, "note", "ls"))
        finally:
            shutil.rmtree(root)


class TestCondense(unittest.TestCase):
    def test_keeps_all_signal(self):
        lines = ["ok test_%d" % i for i in range(3000)]
        lines[1200] = "FAILED test_1200 - AssertionError: expected 3 got 4"
        lines[2500:2500] = ["Traceback (most recent call last):", '  File "x.py", line 3, in f', "    boom()", "KeyError: 'k'"]
        lines.append("=== 1 failed, 2999 passed in 3.2s ===")
        text = "\n".join(lines)
        out, did = ndl.condense(text, 6000)
        self.assertTrue(did)
        self.assertLess(len(out), len(text) / 8)
        for must in ("FAILED test_1200", "KeyError: 'k'", 'File "x.py", line 3', "1 failed, 2999 passed"):
            self.assertIn(must, out)

    def test_small_output_untouched(self):
        self.assertEqual(ndl.condense("hello\nworld", 6000), ("hello\nworld", False))


class TestHooks(unittest.TestCase):
    def setUp(self):
        self.root = mkrepo()
        self.big = os.path.join(self.root, "app", "big.py")
        with open(self.big, "w") as f:
            for i in range(200):
                f.write("def function_number_%d(x):\n    '''doc %d'''\n    y = x * %d + len(str(x))\n    if y > 10:\n        y -= 3\n"
                        "    for k in range(3):\n        y += k * %d\n    return y\n\n" % (i, i, i, i))

    def tearDown(self):
        shutil.rmtree(self.root)

    def read(self, path, **kw):
        ti = {"file_path": path}; ti.update(kw)
        return hook(self.root, "pre-read", {"tool_name": "Read", "tool_input": ti})

    def post(self, path, **kw):
        ti = {"file_path": path}; ti.update(kw)
        hook(self.root, "post-read", {"tool_name": "Read", "tool_input": ti, "tool_response": {}})

    def test_guard_then_bypass(self):
        d = self.read(self.big)
        self.assertEqual(d["permissionDecision"], "deny")
        self.assertIn("function_number_10", d["permissionDecisionReason"])
        self.assertIsNone(self.read(self.big))           # identical repeat goes through
        self.assertIsNone(self.read(self.big, offset=10, limit=20))  # ranged reads are never guarded

    def test_dedupe_and_no_false_positive(self):
        small = os.path.join(self.root, "app", "server.py")
        self.post(self.big, offset=1, limit=300)
        d = self.read(self.big, offset=100, limit=50)     # inside what was read -> dedupe
        self.assertEqual(d["permissionDecision"], "deny")
        self.assertIn("already in your context", d["permissionDecisionReason"])
        self.assertIsNone(self.read(self.big, offset=900, limit=50))  # never read -> allowed
        self.assertIsNone(self.read(small))               # tiny / unread file -> allowed

    def test_delta_after_edit(self):
        self.post(self.big)
        with open(self.big) as f: s = f.read()
        with open(self.big, "w") as f: f.write(s.replace("y = x * 7 +", "y = x * 77 +"))
        d = self.read(self.big)
        self.assertIn("changed since your earlier Read", d["permissionDecisionReason"])
        self.assertIn("+    y = x * 77", d["permissionDecisionReason"])

    def test_session_start_resets_and_exports(self):
        self.post(self.big)
        envf = os.path.join(self.root, "envfile")
        d = hook(self.root, "session-start", {"source": "compact"}, env={"CLAUDE_ENV_FILE": envf})
        self.assertIn("needlepoint active", d["additionalContext"])
        with open(envf) as f: self.assertIn("export NDL=", f.read())
        d = self.read(self.big)                            # ledger reset: guard applies again, not dedupe
        self.assertNotIn("already in your context", d["permissionDecisionReason"])

    def test_bash_condense_shape(self):
        out = "\n".join("line %d ok" % i for i in range(4000)) + "\nERROR: boom at step 3\n"
        d = hook(self.root, "post-bash", {"tool_name": "Bash", "tool_input": {"command": "make test"},
                                          "tool_response": {"stdout": out, "stderr": "", "interrupted": False, "isImage": False}})
        new = d["updatedToolOutput"]
        self.assertEqual(sorted(new), ["interrupted", "isImage", "stderr", "stdout"])
        self.assertIn("ERROR: boom at step 3", new["stdout"])
        self.assertLess(len(new["stdout"]), len(out) / 4)
        self.assertIsNone(hook(self.root, "post-bash", {"tool_name": "Bash", "tool_input": {"command": "cat big.log"},
                                                        "tool_response": {"stdout": out, "stderr": ""}}))

    def test_hooks_fail_open(self):
        r = subprocess.run([sys.executable, "-I", SCRIPT, "hook", "pre-read"], input="not json", capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestPlatform(unittest.TestCase):
    """Ubuntu/macOS/Windows-shell differences: shells, missing tools, symlinks, odd paths and files."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ndl plat ")           # space in path, like '/Users/x/My Projects'
        self.root = os.path.join(self.tmp, "ünï repo")
        os.makedirs(self.root)
        for rel, body in FILES.items():
            p = os.path.join(self.root, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f: f.write(textwrap.dedent(body).lstrip("\n"))
        os.makedirs(os.path.join(self.root, ".git"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _envfile(self, script=SCRIPT):
        envf = os.path.join(self.tmp, "env file")
        payload = json.dumps({"session_id": "P1", "cwd": self.root})
        subprocess.run([sys.executable, "-I", script, "hook", "session-start"], input=payload, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=dict(os.environ, CLAUDE_ENV_FILE=envf, NDL_NO_WARM="1"), check=True)
        return envf

    @unittest.skipIf(os.name == "nt", "POSIX shells/symlinks")
    def test_launcher_in_posix_shells(self):
        envf = self._envfile()
        for sh in ("bash", "dash", "sh"):
            if not shutil.which(sh): continue
            for cmd in ('ndl find "exponential backoff"', '$NDL find "exponential backoff"'):
                r = subprocess.run([sh, "-c", '. "%s" && cd "%s" && %s' % (envf, self.root, cmd)], capture_output=True, text=True, encoding="utf-8", errors="replace")
                self.assertIn("retry_with_backoff", r.stdout, (sh, cmd, r.stderr))

    @unittest.skipIf(os.name == "nt", "POSIX shells/symlinks")
    def test_plugin_path_with_spaces_and_unicode(self):
        d = os.path.join(self.tmp, "plug ins", "nëedle", "scripts")
        shutil.copytree(os.path.dirname(SCRIPT), d)
        envf = self._envfile(os.path.join(d, "ndl.py"))
        r = subprocess.run(["bash", "-c", '. "%s" && cd "%s" && ndl show Store.Get' % (envf, self.root)], capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertIn("func (s *Store) Get", r.stdout, r.stderr)

    def _bare_path(self, keep):
        b = os.path.join(self.tmp, "bin"); os.makedirs(b, exist_ok=True)
        for tool in keep:
            w = shutil.which(tool)
            if w: os.symlink(w, os.path.join(b, tool))
        return b

    @unittest.skipIf(os.name == "nt", "POSIX shells/symlinks")
    def test_without_ripgrep_and_git(self):
        path = self._bare_path(["sh"])                            # neither rg nor git available
        shutil.rmtree(os.path.join(self.root, ".git"))
        env = dict(os.environ, PATH=path)
        out = run_cli(self.root, "refs", "getJson", env=env)
        self.assertIn("[in Api.user]", out)
        out = run_cli(self.root, "grep", "backoff|retries", env=env)
        self.assertIn("[DEF retry_with_backoff]", out)
        self.assertIn("RequestHandler.parse_request", run_cli(self.root, "pack", "parse request", env=env))

    @unittest.skipIf(os.name == "nt", "POSIX shells/symlinks")
    def test_symlinked_paths_like_macos_tmp(self):
        link = os.path.join(self.tmp, "link")
        os.symlink(self.root, link)                              # cwd via symlink, file via real path (and vice versa)
        big = os.path.join(self.root, "app", "big.py")
        with open(big, "w") as f:
            for i in range(200): f.write("def f%d(x):\n    y = x * %d\n    for k in range(3):\n        y += k\n    if y > 9:\n        y -= 1\n    return y\n\n" % (i, i))
        pay = {"session_id": "S", "cwd": link, "tool_name": "Read", "tool_input": {"file_path": big}}
        d = hook(link, "pre-read", dict(pay))
        self.assertEqual(d["permissionDecision"], "deny")
        hook(link, "post-read", dict(pay, tool_input={"file_path": os.path.join(link, "app", "big.py"), "offset": 1, "limit": 50}))
        d = hook(self.root, "pre-read", dict(pay, cwd=self.root, tool_input={"file_path": big, "offset": 5, "limit": 40}))
        self.assertIsNotNone(d)                                   # same file seen through another path -> still deduped
        self.assertIn("already in your context", d["permissionDecisionReason"])

    def test_odd_files_never_crash(self):
        def w(rel, data):
            with open(os.path.join(self.root, rel), "wb") as f: f.write(data)
        w("crlf.py", b"class A:\r\n    def m(self):\r\n        return 1\r\n\r\ndef f():\r\n    return 2\r\n")
        w("crlf.ts", b"export function g(a: number) {\r\n  return a\r\n}\r\nexport class K {\r\n  run() {\r\n    return 1\r\n  }\r\n}\r\n")
        w("bom.py", b"\xef\xbb\xbfdef with_bom():\n    return 1\n")
        w("latin.py", "def caf\u00e9_latin():\n    s = '\u00e9t\u00e9'\n    return s\n".encode("latin-1"))
        w("empty.py", b"")
        w("only_comments.js", b"// nothing here\n/* still nothing */\n")
        w("min.js", b"var a=1;" * 20000)
        w("binary.py", b"\x00\x01\x02 def nope(): pass")
        w("broken.js", b"function open() {\n  if (x) {\n    return '{'\n// never closed\n")
        w("broken.py", b"def ok():\n    return 1\n\ndef bad(:\n    pass\n")
        w("deep.go", b"package p\n\nfunc Deep() {\n" + b"{\n" * 300 + b"}\n" * 300 + b"}\n")
        idx, _ = ndl.build_index(self.root)
        names = {idx.qname(s) for r in idx.files.values() for s in r["s"]}
        for must in ("A.m", "f", "g", "K.run", "with_bom", "ok", "Deep", "caf\u00e9_latin"):
            self.assertIn(must, names)
        self.assertNotIn("binary.py", idx.files)
        self.assertEqual(idx.files["min.js"]["s"], [])
        for rel in ("crlf.py", "crlf.ts", "broken.js", "broken.py", "deep.go", "latin.py", "empty.py"):
            ndl.outline_text(idx, rel)
        self.assertIn("return 2", run_cli(self.root, "show", "crlf.py:f"))
        self.assertNotIn("\r", run_cli(self.root, "show", "crlf.ts:g"))

    def test_concurrent_cold_indexing(self):
        procs = [subprocess.Popen([sys.executable, "-I", SCRIPT, "--root", self.root, "find", "backoff"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace") for _ in range(8)]
        for p in procs:
            out, err = p.communicate(timeout=120)
            self.assertEqual(p.returncode, 0, err)
            self.assertIn("retry_with_backoff", out)

    def test_unwritable_project_falls_back_to_cache(self):
        with open(os.path.join(self.root, ".needlepoint"), "w") as f: f.write("not a dir")   # makes the project state dir unusable
        cache = os.path.join(self.tmp, "cache")
        out = run_cli(self.root, "find", "backoff", env={"XDG_CACHE_HOME": cache})
        self.assertIn("retry_with_backoff", out)
        self.assertTrue(os.path.isdir(os.path.join(cache, "needlepoint")))


class TestLanguageRegressions(unittest.TestCase):
    """Each case is a construct that broke symbol ranges on a real repo (cobra, serde_json, fmt, zod, Alamofire,
    okio, scopt, dart-lang/http, nvm, jq, express). Expect (qualified name, start, end)."""
    CASES = [
        ("go", 'func AddTemplateFunc(name string, f interface{}) {\n\tm[name] = f\n}\n', ("AddTemplateFunc", 1, 3)),
        ("go", 'func Gen(buf io.Writer) {\n\tw(buf, `{\nraw { brace\n`)\n}\nfunc Next() {}\n', ("Gen", 1, 5)),
        ("rs", 'fn split(x: u64) -> [Limb; 2] {\n    [a(x), b(x)]\n}\n', ("split", 1, 3)),
        ("rs", 'fn t() {\n    let a = r#"{ "x": [ }"#;\n    a\n}\n', ("t", 1, 4)),
        ("rs", 'fn s() {\n    let j = "{\n        \\"a\\": 1\n    ";\n}\n', ("s", 1, 5)),
        ("c", 'auto write(Out out, const specs& s = {}) -> Out {\n  return out;\n}\n', ("write", 1, 3)),
        ("c", 'void f() {\n  auto s = R"x({ "}" })x";\n}\nint h() { return 1; }\n', ("h", 4, 4)),
        ("js", 'function p(input: unknown): { name: string; age: number } | typeof X {\n  return X;\n}\n', ("p", 1, 3)),
        ("js", 'export function lazy<T>(g: () => T): {\n  value: T;\n} {\n  return { value: g() };\n}\n', ("lazy", 1, 5)),
        ("js", 'const cases = [\n  {\n    run: (i) => void sink.push(i),\n    leak: true,\n  },\n];\n', ("cases.run", 3, 3)),
        ("js", 'describe("x (y) tests", () => {\n  it("works", async () => {\n    ok()\n  })\n})\n', ("x (y) tests", 1, 5)),
        ("js", 'function t() {\n  const css = `\n  a { color: red;\n  `\n  return css\n}\n', ("t", 1, 6)),
        ("swift", 'func setup(for r: Request,\n           ok: @escaping () -> Bool = { true }) {\n    run(r)\n}\n', ("setup", 1, 4)),
        ("kt", 'class F {\n  override fun toString() = "F(${x})"\n\n  fun close() {\n    closed = true\n  }\n}\n', ("F.toString", 2, 2)),
        ("kt", 'fun q() {\n  val s = """\n    { not a brace\n  """\n}\n', ("q", 1, 5)),
        ("scala", 'object E {\n  case class Report(msg: String) extends Effect\n  case class Stop(s: Int) extends Effect\n}\n', ("E.Report", 2, 2)),
        ("dart", "static Engine build({\n  int? size,\n  bool? fast,\n}) {\n  final s = '}';\n  return Engine(size);\n}\n", ("build", 1, 7)),
        ("sh", 'nvm_check() {\n  [ -z "${X%%"$D"/*}" ] && return 0\n  # a comment with a { brace\n  echo ok\n}\n', ("nvm_check", 1, 5)),
        ("sh", 'try() {\n  "$@" || exit 1\n}\n', ("try", 1, 3)),
        ("java", 'class A {\n  List<String> names() { return List.of(); }\n}\n', ("A.names", 2, 2)),
    ]

    def test_cases(self):
        for lang, code, (name, a, b) in self.CASES:
            syms = ndl.parse_regex(lang, ndl.split_lines(code))
            got = {((s[4] + ".") if s[4] else "") + s[0]: (s[2], s[3]) for s in syms}
            self.assertIn(name, got, (lang, code, got))
            self.assertEqual(got[name], (a, b), (lang, name, code))

    def test_crlf_and_overlaps(self):
        for lang, code, _ in self.CASES:
            syms = ndl.parse_regex(lang, ndl.split_lines(code.replace("\n", "\r\n")))
            ss = sorted(syms, key=lambda s: (s[2], -s[3]))
            for i, x in enumerate(ss):           # ranges are nested or disjoint, never crossing
                for y in ss[i + 1:]:
                    if y[2] > x[3]: break
                    self.assertLessEqual(y[3], x[3], (lang, x, y))


class TestConcurrencyStress(unittest.TestCase):
    def test_parallel_find_and_edit(self):
        """readers keep working while another process edits files and rebuilds (fresh session start + first command)."""
        root = mkrepo()
        try:
            procs = []
            for i in range(10):
                if i % 3 == 0:
                    p = os.path.join(root, "app", "gen%d.py" % i)
                    with open(p, "w") as f: f.write("def generated_backoff_%d():\n    return %d\n" % (i, i))
                procs.append(subprocess.Popen([sys.executable, "-I", SCRIPT, "--root", root, "find", "exponential backoff"],
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace"))
            for p in procs:
                out, err = p.communicate(timeout=120)
                self.assertEqual(p.returncode, 0, err)
                self.assertIn("retry_with_backoff", out, err)
        finally:
            shutil.rmtree(root)


class TestHooksConfig(unittest.TestCase):
    """Runs every command in hooks/hooks.json the way Claude Code does: through a shell, with CLAUDE_PLUGIN_ROOT set."""

    @unittest.skipIf(os.name == "nt", "POSIX shell test")
    def test_hooks_json_commands_execute(self):
        plugin = os.path.abspath(os.path.join(HERE, "..", "plugins", "needlepoint"))
        with open(os.path.join(plugin, "hooks", "hooks.json")) as f: cfg = json.load(f)
        root = mkrepo()
        try:
            big = os.path.join(root, "app", "big.py")
            with open(big, "w") as f:
                for i in range(200): f.write("def f%d(x):\n    y = x * %d\n    for k in range(3):\n        y += k\n    if y > 9:\n        y -= 1\n    return y\n\n" % (i, i))
            payloads = {"SessionStart": {"source": "startup"},
                        "PreToolUse": {"tool_name": "Read", "tool_input": {"file_path": big}},
                        "PostToolUse": {"tool_name": "Read", "tool_input": {"file_path": big}, "tool_response": {}}}
            env = dict(os.environ, CLAUDE_PLUGIN_ROOT=plugin, CLAUDE_ENV_FILE=os.path.join(root, "envf"), NDL_NO_WARM="1")
            seen = set()
            for event, groups in cfg["hooks"].items():
                for g in groups:
                    for h in g["hooks"]:
                        pay = dict(payloads[event], session_id="HJ", cwd=root, hook_event_name=event)
                        if g.get("matcher") == "Bash":
                            pay = dict(pay, tool_name="Bash", tool_input={"command": "make"},
                                       tool_response={"stdout": "ok\n" * 9000 + "ERROR: x\n", "stderr": "", "interrupted": False, "isImage": False})
                        r = subprocess.run(h["command"], shell=True, input=json.dumps(pay), capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=h.get("timeout", 30))
                        self.assertEqual(r.returncode, 0, (event, r.stderr))
                        if r.stdout.strip():
                            out = json.loads(r.stdout)["hookSpecificOutput"]
                            self.assertEqual(out["hookEventName"], event)
                            seen.add(event + ":" + (g.get("matcher") or ""))
            self.assertIn("SessionStart:", seen)
            self.assertIn("PreToolUse:Read", seen)          # big file -> outline-first denial
            self.assertIn("PostToolUse:Bash", seen)         # long output -> condensed
            with open(os.path.join(root, "envf")) as f: self.assertIn("export PATH=", f.read())
        finally:
            shutil.rmtree(root)
