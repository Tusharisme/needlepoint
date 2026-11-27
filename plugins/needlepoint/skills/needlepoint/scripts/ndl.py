#!/usr/bin/env python3
"""needlepoint (ndl) - lossless precision context for coding agents.

Cut tokens by never loading what isn't needed - instead of compressing what is.
Zero dependencies (Python 3.8+ stdlib). Optional: ripgrep for faster `refs`.

Commands:  index | map | find | show | outline | refs | pack | note | log | run | stats | bench | doctor
Hooks:     hook session-start | hook pre-read | hook post-read | hook post-bash
"""
import argparse, ast, difflib, gzip, hashlib, json, math, os, re, shutil, subprocess, sys, time
from collections import Counter, defaultdict
try:
    import sqlite3
except ImportError:  # some minimal Python builds (e.g. pyenv without libsqlite3-dev) ship without it
    sqlite3 = None
if os.environ.get("NDL_NO_SQLITE"): sqlite3 = None   # force the JSON backend (testing)

VERSION = "0.3.0"
STATE = ".needlepoint"
IDX_VER = 21
MAX_BYTES = 1_500_000
try: MAX_FILES = int(os.environ.get("NDL_MAX_FILES", "60000"))
except ValueError: MAX_FILES = 60000
CPT = 3.5  # chars per token (estimate for code, +-15%)
IGNORE_DIRS = {".git", ".hg", ".svn", STATE, "node_modules", "__pycache__", ".venv", "venv", "env", ".env",
               "dist", "build", "out", "target", ".next", ".nuxt", ".cache", ".tox", ".mypy_cache",
               ".pytest_cache", ".idea", ".vscode", "coverage", "vendor", "bower_components", ".gradle",
               ".terraform", "site-packages", ".ruff_cache", ".parcel-cache", ".turbo", ".svelte-kit"}

def tok(s):
    return int(len(s) / CPT) + 1 if s else 0

def sha(s):
    if isinstance(s, str): s = s.encode("utf-8", "replace")
    return hashlib.sha1(s).hexdigest()[:12]

# ----------------------------------------------------------------------------- languages
EXT = {"py": "py", "pyi": "py", "js": "js", "jsx": "js", "mjs": "js", "cjs": "js", "ts": "js", "tsx": "js",
       "mts": "js", "cts": "js", "vue": "js", "svelte": "js", "go": "go", "rs": "rs", "java": "java",
       "kt": "kt", "kts": "kt", "scala": "scala", "cs": "java", "swift": "swift", "c": "c", "h": "c",
       "cc": "c", "cpp": "c", "cxx": "c", "hpp": "c", "hh": "c", "hxx": "c", "m": "c", "mm": "c",
       "php": "php", "rb": "rb", "lua": "lua", "sh": "sh", "bash": "sh", "zsh": "sh", "sql": "sql",
       "md": "md", "mdx": "md", "rst": "txt", "txt": "txt", "yaml": "yaml", "yml": "yaml", "toml": "toml",
       "ini": "toml", "cfg": "toml", "json": "data", "jsonc": "data", "xml": "data", "html": "data",
       "htm": "data", "css": "css", "scss": "css", "less": "css", "proto": "java", "graphql": "js",
       "gql": "js", "tf": "js", "dart": "dart", "ex": "rb", "exs": "rb", "r": "txt", "jl": "rb",
       "ipynb": None, "lock": None, "map": None, "svg": None}
NAMES = {"dockerfile": "make", "makefile": "make", "gnumakefile": "make", "justfile": "make",
         "rakefile": "rb", "gemfile": "rb", "cmakelists.txt": "txt"}

def lang_of(path):
    b = os.path.basename(path).lower()
    if b in NAMES: return NAMES[b]
    if b.endswith(".min.js") or b.endswith(".min.css"): return None
    ext = b.rsplit(".", 1)[-1] if "." in b else ""
    return EXT.get(ext)

J = r"[A-Za-z_$][\w$]*"
MODS_JAVA = r"(?:(?:public|private|protected|internal|static|final|abstract|synchronized|native|override|virtual|async|sealed|extern|unsafe|new|default|readonly|partial|open|suspend|inline|operator|infix|tailrec|lateinit|const|data|inner|external|actual|expect)\s+)*"
CTRL = {"if", "for", "while", "switch", "catch", "return", "new", "else", "throw", "do", "case", "sizeof",
        "typeof", "function", "await", "yield", "try", "with", "delete", "in", "of", "elif", "until", "unless",
        "foreach", "using", "lock", "fixed", "assert", "super", "this", "constructor_", "match", "loop"}
# (regex, kind, end-mode)  end-mode: brace | indent | line | semi | key | section
RULES = {
    "js": [
        (r"^\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:async\s+)?function\s*\*?\s*(?P<n>" + J + ")", "function", "brace"),
        (r"^\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?class\s+(?P<n>" + J + ")", "class", "brace"),
        (r"^\s*(?:export\s+)?(?:declare\s+)?(?:const\s+)?(?:interface|enum|namespace|module)\s+(?P<n>" + J + ")", "type", "brace"),
        (r"^\s*(?:export\s+)?(?:declare\s+)?type\s+(?P<n>" + J + r")\s*(?:<[^=]*>)?\s*=", "type", "brace!"),
        (r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<n>" + J + r")\s*(?::[^=]+)?=\s*(?:async\s+)?(?:function\b|\([^)]*\)\s*(?::[^=]+)?=>|" + J + r"\s*=>|\(\s*$)", "function", "brace!"),
        (r"^(?:export\s+)?(?:const|let|var)\s+(?P<n>" + J + ")", "var", "brace!"),
        (r"^\s*(?:module\.)?exports\.(?P<n>" + J + r")\s*=", "var", "brace!"),
        (r"^\s*(?:module\.)?exports\s*=\s*(?:async\s+)?function\s*\*?\s*(?P<n>" + J + ")", "function", "brace"),
        (r"^\s*(?P<recv>" + J + r"(?:\.prototype)?(?:\." + J + r")*?)\.(?P<n>" + J + r")\s*=\s*(?:async\s+)?(?:function\b|\([^)]*\)\s*=>|" + J + r"\s*=>)", "method", "brace"),
        (r"^\s+(?:(?:static|async|public|private|protected|readonly|override|abstract|get|set)\s+)*\*?(?P<n>#?" + J + r")\s*(?:<[^>()]*>)?\s*\((?:[^()]|\([^()]*\))*\)\s*(?::\s*[^{;=]+)?\s*\{(?:.*\}\s*)?$", "method", "brace"),
        (r"^\s+(?P<n>" + J + r")\s*[:=]\s*(?:async\s+)?(?:function\b|\([^)]*\)\s*=>)", "method", "brace!"),
        (r"^\s*(?:describe|context|suite|it|test|specify)(?:\.(?:only|skip|each\([^)]*\)|concurrent))?\s*\(\s*(['\"`])(?P<n>.{1,100}?)\1\s*,", "test", "brace"),
    ],
    "go": [
        (r"^func\s+\((?P<recv>[^)]*)\)\s*(?P<n>[A-Za-z_]\w*)", "method", "brace"),
        (r"^func\s+(?P<n>[A-Za-z_]\w*)", "function", "brace"),
        (r"^type\s+(?P<n>[A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s+(?:struct|interface)\b", "class", "brace"),
        (r"^type\s+(?P<n>[A-Za-z_]\w*)\b", "type", "line"),
        (r"^(?:var|const)\s+(?P<n>[A-Za-z_]\w*)", "var", "line"),
    ],
    "rs": [
        (r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:default\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?(?:extern\s+\"[^\"]*\"\s+)?fn\s+(?P<n>[A-Za-z_]\w*)", "function", "brace"),
        (r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|union|trait)\s+(?P<n>[A-Za-z_]\w*)", "class", "brace"),
        (r"^\s*(?:unsafe\s+)?impl\b(?:\s*<[^>]*>)?\s+(?:[\w:<>, &']+?\s+for\s+)?(?P<n>[A-Za-z_]\w*)", "impl", "brace"),
        (r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+(?P<n>\w+)", "module", "brace"),
        (r"^\s*macro_rules!\s*(?P<n>\w+)", "macro", "brace"),
        (r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const|static)\s+(?:mut\s+)?(?P<n>[A-Z_][A-Z0-9_]*)", "var", "brace"),
        (r"^\s*(?:pub(?:\([^)]*\))?\s+)?type\s+(?P<n>\w+)", "type", "brace"),
    ],
    "java": [
        (r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*" + MODS_JAVA + r"(?:class|interface|enum|record|struct|@interface|message|service|mixin|extension)\s+(?P<n>[A-Za-z_]\w*)", "class", "brace"),
        (r"^\s*" + MODS_JAVA + r"(?:<[^>]+>\s+)?[\w<>\[\],.?]+(?:\s*<[^>]*>)?(?:\[\])*\s+(?P<n>[A-Za-z_]\w*)\s*\((?:[^;]*$|[^)]*\)\s*(?:throws[^{]*)?\{.*\}\s*$)", "method", "brace"),
        (r"^\s*rpc\s+(?P<n>\w+)", "method", "line"),
    ],
    "kt": [
        (r"^\s*" + MODS_JAVA + r"(?:class|interface|object|enum\s+class|sealed\s+class|data\s+class)\s+(?P<n>[A-Za-z_]\w*)", "class", "brace"),
        (r"^\s*" + MODS_JAVA + r"fun\s+(?:<[^>]+>\s*)?(?:[\w.<>]+\.)?(?P<n>[A-Za-z_]\w*)\s*\(", "function", "brace"),
    ],
    "scala": [
        (r"^\s*(?:(?:private|protected|final|sealed|abstract|implicit|case|override|lazy)\s+)*(?:class|object|trait)\s+(?P<n>[A-Za-z_]\w*)", "class", "brace"),
        (r"^\s*(?:(?:private|protected|final|override|implicit)\s+)*def\s+(?P<n>[A-Za-z_]\w*)", "function", "brace"),
    ],
    "swift": [
        (r"^\s*(?:(?:public|private|fileprivate|internal|open|final|static|class|override|mutating|@\w+)\s+)*(?:class|struct|enum|protocol|extension|actor)\s+(?P<n>[A-Za-z_]\w*)", "class", "brace"),
        (r"^\s*(?:(?:public|private|fileprivate|internal|open|final|static|class|override|mutating|@\w+)\s+)*func\s+(?P<n>[A-Za-z_]\w*)", "function", "brace"),
    ],
    "c": [
        (r"^\s*(?:template\s*<[^>]*>\s*)?(?:typedef\s+)?(?:class|struct|union|enum(?:\s+class)?)\s+(?:\w+\s+)*?(?P<n>[A-Za-z_]\w*)\s*(?:final\s*)?(?::[^;{]*)?\{?\s*$", "class", "brace"),
        (r"^\s*namespace\s+(?P<n>[\w:]+)", "module", "brace"),
        (r"^\s{0,4}(?!(?:if|for|while|switch|return|else|do|case|typedef|using)\b)[A-Za-z_][\w\s\*&:<>,~]*?\b(?P<n>~?[A-Za-z_]\w*(?:::~?\w+)*)\s*\((?:[^;]*$|[^()]*\)\s*(?:const\s*)?\{.*\}\s*$)", "function", "brace"),
        (r"^\s*#\s*define\s+(?P<n>[A-Za-z_]\w*)", "macro", "line"),
        (r"^\s*@(?:interface|implementation|protocol)\s+(?P<n>\w+)", "class", "objc"),
    ],
    "php": [
        (r"^\s*(?:(?:abstract|final|readonly)\s+)*(?:class|interface|trait|enum)\s+(?P<n>[A-Za-z_]\w*)", "class", "brace"),
        (r"^\s*(?:(?:public|private|protected|static|final|abstract)\s+)*function\s+&?(?P<n>[A-Za-z_]\w*)", "function", "brace"),
    ],
    "rb": [
        (r"^\s*def\s+(?P<n>(?:self\.)?[\w.?!=\[\]<>+-]+)", "method", "indent"),
        (r"^\s*(?:class|module)\s+(?P<n>[\w:]+)", "class", "indent"),
        (r"^\s*(?:defp?|defmodule|defmacro)\s+(?P<n>[\w.?!]+)", "function", "indent"),
        (r"^\s*function\s+(?P<n>[\w.!]+)", "function", "indent"),
        (r"^\s*(?:RSpec\.)?(?:describe|context|it|specify|test)\s*\(?\s*(['\"])(?P<n>.{1,100}?)\1.*\bdo\b", "test", "indent"),
    ],
    "lua": [(r"^\s*(?:local\s+)?function\s+(?P<n>[\w.:]+)", "function", "indent")],
    "sh": [(r"^\s*(?:function\s+)?(?P<n>[A-Za-z_][\w:-]*)\s*\(\)\s*\{?", "function", "brace"),
           (r"^\s*function\s+(?P<n>[\w:-]+)", "function", "brace")],
    "sql": [(r"(?i)^\s*create\s+(?:or\s+replace\s+)?(?:temp(?:orary)?\s+)?(?:table|view|materialized\s+view|function|procedure|index|unique\s+index|trigger|type|schema)\s+(?:if\s+not\s+exists\s+)?(?P<n>[\w.\"`]+)", "table", "semi")],
    "yaml": [(r"^(?P<n>[A-Za-z_][\w.-]*)\s*:", "key", "key")],
    "toml": [(r"^\[+(?P<n>[^\]]+)\]+", "section", "section")],
    "make": [(r"^(?P<n>[A-Za-z_][\w./-]*)\s*:(?!=)", "target", "key"),
             (r"^(?P<n>FROM|RUN|CMD|ENTRYPOINT|COPY|ENV|ARG)\b", "step", "line")],
    "css": [(r"^(?P<n>[.#@A-Za-z][^{;]{0,80}?)\s*\{\s*$", "rule", "brace")],
}
RULES["dart"] = RULES["java"]
CRX = {k: [(re.compile(r), kd, m) for r, kd, m in v] for k, v in RULES.items()}
SQUOTE_STR = {"js", "php", "sh", "rb", "lua", "py", "yaml", "css", "dart"}

CONT_END = ("(", "=", "=>", ",", "[", "{", "?", ":", "&&", "||", "+", "|", "<", ".", "->", "\\")
HEADER_NEXT = re.compile(r"^\s*(\{|->|:|=>|throws\b|where\b|const\b|noexcept\b|override\b|final\b|&|requires\b|extends\b|implements\b|with\b|async\b|\)|\.)")

def _code_tail(s):
    return re.sub(r"\s*(//.*|/\*.*?\*/\s*)$", "", s).rstrip()

def _next_code(lines, j, limit=4):
    for x in range(j + 1, min(len(lines), j + 1 + limit)):
        t = lines[x].strip()
        if t and not t.startswith(("//", "/*", "*", "#")): return lines[x]
    return ""

def indent_close(lines, i, limit=6000):
    """first line after i that is just a closing brace at the definition's indentation (common in all styles)."""
    ind = _indent(lines[i])
    for j in range(i + 1, min(len(lines), i + limit)):
        t = lines[j].strip()
        if t[:1] == "}" and _indent(lines[j]) == ind and re.match(r"^\}[;,)\]]*\s*(//.*|#.*)?$", t): return j
        if t and _indent(lines[j]) < ind and not t.startswith(("#", "//", "*", "/*")): return None
    return None

RAW_RS = re.compile(r'r(#*)"')
RAW_CPP = re.compile(r'R"([^(\s]{0,16})\(')

def brace_end(lines, i, squote, maxscan=6000, strict=False, regex=False, hashc=False, persist="", call=False, raw=None):
    """(end_line_index, has_body). Body = first '{' outside (), [] and outside a header-only {...} (types, defaults)."""
    depth, started, inblk, trip, pd, open_j = 0, False, False, None, 0, -1
    n = len(lines)
    instr = None
    for j in range(i, min(n, i + maxscan)):
        s = lines[j]; k = 0; L = len(s)
        if instr and instr not in persist: instr = None
        while k < L:
            c = s[k]
            if inblk:
                if c == "*" and s.startswith("*/", k): inblk = False; k += 2; continue
                k += 1; continue
            if trip:
                if s.startswith(trip, k): k += len(trip); trip = None; continue
                k += 1; continue
            if instr:
                if c == "\\": k += 2; continue
                if c == instr: instr = None
                k += 1; continue
            if c == "/" and k + 1 < L:
                nx = s[k + 1]
                if nx == "/": break
                if nx == "*": inblk = True; k += 2; continue
                if regex:
                    pv = s[:k].rstrip()
                    if not pv or pv[-1] in "(,=:[!&|?{;+-*%~^" or pv.endswith(("return", "typeof", "case")):
                        k += 1; cls = False
                        while k < L:
                            ch = s[k]
                            if ch == "\\": k += 2; continue
                            if ch == "[": cls = True
                            elif ch == "]": cls = False
                            elif ch == "/" and not cls: break
                            k += 1
                        k += 1; continue
            if c == "#" and (hashc and (k == 0 or s[k - 1] in " \t") or (squote and not started and k == 0)): break
            if raw == "rs" and c == "r" and (k == 0 or not (s[k - 1].isalnum() or s[k - 1] == "_")):
                m_ = RAW_RS.match(s, k)
                if m_: trip = '"' + m_.group(1); k = m_.end(); continue
            if raw == "c" and c == "R" and k + 1 < L and s[k + 1] == '"':
                m_ = RAW_CPP.match(s, k)
                if m_: trip = ")" + m_.group(1) + '"'; k = m_.end(); continue
            if (c == '"' or c == "'") and s.startswith(c * 3, k): trip = c * 3; k += 3; continue
            if c == '"' or c == "`": instr = c; k += 1; continue
            if c == "'":
                if squote or (k + 2 < L and s[k + 2] == "'") or (k + 1 < L and s[k + 1] == "\\"):
                    instr = c; k += 1; continue
            if not started:
                if c in "([": pd += 1
                elif c in ")]": pd = max(0, pd - 1)
                elif pd > 0 and not (call and c == "{"): pass   # braces / ';' inside params, generics, array types
                elif c == "{": depth = 1; started = True; open_j = j
                elif c == ";": return j, False         # declaration / prototype, no body
                k += 1; continue
            if c == "{": depth += 1
            elif c == "}":
                depth -= 1
                if depth <= 0:
                    rest = _code_tail(s[k + 1:]).strip()
                    if rest and (j == open_j and rest[0] not in ";),(." or re.match(r"^(\||&|\{|=>|->|extends\b|implements\b|where\b)", rest)):
                        started = False; depth = 0; k += 1; continue   # '{...}' was part of the header (type literal / default)
                    return j, True
            k += 1
        if not started and not trip and not inblk:
            code = _code_tail(s)
            if strict and pd == 0 and code.endswith(",") and "=>" in code: return j, False
            if pd > 0 or code.endswith(CONT_END) or (strict is False and not code and j > i):
                pass
            elif HEADER_NEXT.match(_next_code(lines, j)):
                pass
            else:
                return j, False                          # header ended without a body (expression-bodied / declaration)
        if not started and j - i >= (80 if pd > 0 else 12): return i, False
    if started:                                       # never closed: strings/macros confused the count
        alt = indent_close(lines, i)
        if alt is not None: return alt, True
    return min(n - 1, i), started

def _indent(s):
    return len(s) - len(s.lstrip(" \t"))

def indent_end(lines, i):
    base = _indent(lines[i]); last = i
    for j in range(i + 1, len(lines)):
        s = lines[j]
        if not s.strip(): continue
        ind = _indent(s)
        if ind <= base:
            if ind == base and re.match(r"\s*(end\b|\}|\)|\])", s): return j
            return last
        last = j
    return last

def parse_regex(lang, lines):
    rules = CRX.get(lang)
    if not rules: return []
    squote = lang in SQUOTE_STR
    syms = []
    n = len(lines)
    for i, line in enumerate(lines):
        if not line or len(line) > 400: continue
        for rx, kind, mode in rules:
            m = rx.match(line)
            if not m: continue
            name = m.group("n")
            base = name.split("::")[-1].lstrip("#~")
            if base in CTRL and lang != "sh" and not re.search(r"\b(fn|def|defp|func|function|fun|sub|proc)\b", line[:m.start("n")]): break
            parent = None
            if "recv" in m.groupdict() and m.group("recv"):
                pt = [w for w in re.findall(r"[A-Za-z_$][\w$]*", m.group("recv")) if w not in ("prototype", "exports", "module")]
                parent = pt[-1] if pt else None
                if lang == "js" and not parent: kind = "function"
            if lang == "sh" and mode == "brace" and indent_close(lines, i) is not None:
                e, body = indent_close(lines, i), True
            elif mode in ("brace", "brace!"):
                e, body = brace_end(lines, i, squote, strict=(mode == "brace!"), regex=(lang == "js"), hashc=lang in ("sh", "php"),
                                    persist={"go": "`", "js": "`", "rs": '"'}.get(lang, ""), call=(kind == "test"),
                                    raw=lang if lang in ("rs", "c") else None)
            elif mode == "indent": e, body = indent_end(lines, i), True
            elif mode == "semi":
                e = i
                while e < n - 1 and ";" not in lines[e]: e += 1
                body = True
            elif mode in ("key", "section"):
                e = i
                for j in range(i + 1, n):
                    sj = lines[j]
                    if mode == "key" and sj and not sj[0].isspace() and not sj.startswith("#") and not sj.startswith("-") and sj.strip() and not sj.startswith("}"):
                        break
                    if mode == "section" and sj.startswith("["): break
                    e = j
                while e > i and not lines[e].strip(): e -= 1
                body = True
            elif mode == "objc":
                e = i
                for j in range(i + 1, n):
                    if lines[j].startswith("@end"): e = j; break
                body = True
            else: e, body = i, True
            if kind in ("method", "function") and mode == "brace" and not body:
                if lang in ("java", "c", "js"):
                    if lang == "js" and kind == "method": break
                    kind = "decl"
            if lang == "js" and kind == "method" and _indent(line) == 0: kind = "function"
            if kind == "var" and re.search(r"=\s*(?:await\s+)?(?:require|import)\s*\(", line): break  # imports are not symbols
            sig = re.sub(r"\s+", " ", line.strip())
            if sig.endswith("{"): sig = sig[:-1].rstrip()
            syms.append([base if kind != "impl" else name, kind, i + 1, e + 1, parent, sig[:180], lead_doc(lines, i)])
            break
    return _nest(syms)

CMT = re.compile(r"^\s*(///?|/\*\*?|\*/?|#(?!\[|!|include|define|if|end|pragma)|--|;;)\s?")

def lead_doc(lines, i):
    """first sentence of the comment block right above line i (JSDoc, //, #, ///, --)."""
    j = i - 1; block = []
    if j >= 1 and not lines[j].strip() and CMT.match(lines[j - 1]): j -= 1  # allow one blank line after the comment
    while j >= 0 and i - j <= 40:
        l = lines[j]
        st = l.strip()
        if not st: break
        if st.startswith("@") and not st.startswith("@param"): j -= 1; continue  # decorators/annotations
        if not CMT.match(l): break
        block.append(st); j -= 1
    for st in reversed(block):
        t = CMT.sub("", st).strip().strip("*/").strip()
        if t and not t.startswith("@") and len(t) > 3 and not re.match(r"^[-=*#~]{3,}$", t) and not t.lower().startswith(("eslint", "prettier", "istanbul", "ts-ignore", "noinspection", "copyright", "license")):
            return t[:160]
    return ""

def parse_md(lines):
    heads = []; fence = False
    for i, l in enumerate(lines):
        if l.startswith("```") or l.startswith("~~~"): fence = not fence; continue
        if fence: continue
        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", l)
        if m: heads.append((i, len(m.group(1)), m.group(2)))
    syms = []
    for k, (i, lv, t) in enumerate(heads):
        e = len(lines) - 1
        for i2, lv2, _ in heads[k + 1:]:
            if lv2 <= lv: e = i2 - 1; break
        while e > i and not lines[e].strip(): e -= 1
        syms.append([t[:80], "h%d" % lv, i + 1, e + 1, None, lines[i].strip()[:120], ""])
    return _nest(syms)

def _nest(syms):
    syms.sort(key=lambda s: (s[2], -s[3]))
    stack = []
    for s in syms:
        while stack and not (stack[-1][2] <= s[2] and s[3] <= stack[-1][3]):
            stack.pop()
        if s[4] is None and stack:
            t = stack[-1]
            s[4] = (t[4] + "." + t[0]) if t[4] else t[0]
        stack.append(s)
    return syms

def parse_py(text, lines):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return _py_fallback(lines)
    syms = []
    longdoc = parse_py.longdoc = {}

    def header(node):
        start = node.lineno
        b0 = node.body[0].lineno if node.body else node.end_lineno
        if b0 <= start: h = lines[start - 1].strip()
        else:
            parts = []
            for x in lines[node.lineno - 1:b0 - 1]:
                x = x.strip()
                if not x or x.startswith("#"): continue
                parts.append(x)
                if re.sub(r"\s*#.*$", "", x).endswith(":"): break
            h = " ".join(parts)
        h = re.sub(r"\s+", " ", h)
        if h.endswith(":") and "lambda" not in h[-3:]: h = h[:-1]
        return h[:200]

    def visit(node, parent, pkind, depth):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = min([d.lineno for d in ch.decorator_list] + [ch.lineno])
                if isinstance(ch, ast.ClassDef): kind = "class"
                else: kind = "method" if pkind == "class" else ("function" if pkind is None else "inner")
                doc = ""
                try:
                    d = ast.get_docstring(ch)
                    if d:
                        doc = d.strip().split("\n")[0][:160]
                        longdoc[(ch.name, start)] = " ".join(d.strip().split("\n\n")[0].split())[:400]
                except Exception: pass
                syms.append([ch.name, kind, start, ch.end_lineno, parent, header(ch), doc])
                if depth < 3:
                    visit(ch, (parent + "." + ch.name) if parent else ch.name, kind, depth + 1)
            elif pkind == "class" and isinstance(ch, (ast.Assign, ast.AnnAssign)):
                tg = ch.targets if isinstance(ch, ast.Assign) else [ch.target]
                for t in tg:
                    for nm in ([t] if isinstance(t, ast.Name) else (t.elts if isinstance(t, ast.Tuple) else [])):
                        if isinstance(nm, ast.Name):
                            syms.append([nm.id, "attr", ch.lineno, ch.end_lineno, parent,
                                         re.sub(r"\s+", " ", " ".join(x.strip() for x in lines[ch.lineno - 1:ch.end_lineno]))[:160], ""])
            elif depth == 0 and isinstance(ch, (ast.Assign, ast.AnnAssign)):
                tg = ch.targets if isinstance(ch, ast.Assign) else [ch.target]
                for t in tg:
                    for nm in ([t] if isinstance(t, ast.Name) else (t.elts if isinstance(t, ast.Tuple) else [])):
                        if isinstance(nm, ast.Name):
                            syms.append([nm.id, "var", ch.lineno, ch.end_lineno, None,
                                         re.sub(r"\s+", " ", lines[ch.lineno - 1].strip())[:160], ""])
            elif depth == 0 and isinstance(ch, (ast.If, ast.Try, ast.With, getattr(ast, "TryStar", ast.Try))):
                visit(ch, parent, pkind, depth)
    visit(tree, None, None, 0)
    syms.sort(key=lambda s: (s[2], -s[3]))
    return syms

def _py_fallback(lines):
    syms = []
    for i, l in enumerate(lines):
        m = re.match(r"^(\s*)(?:async\s+)?(def|class)\s+(\w+)", l)
        if m:
            syms.append([m.group(3), "class" if m.group(2) == "class" else "function", i + 1,
                         indent_end(lines, i) + 1, None, l.strip()[:160], ""])
    return _nest(syms)

ID_RX = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
KW = set("""self this cls None True False and not for while return import from def class if else elif try except
finally with as pass break continue lambda yield async await const let var function new null undefined true false
public private protected static void int string bool float double char long short byte struct enum interface
package func type nil err error fmt impl pub mut use mod crate super where match loop the and arg args kwargs
len str dict list set tuple print range object any end then local require module exports default export case
switch throw throws catch extends implements instanceof typeof delete include define ifdef endif ifndef""".split())

def decode(raw):
    """utf-8 (BOM stripped) or, if that fails, latin-1 so names like caf\u00e9 survive."""
    try: return raw.decode("utf-8-sig")
    except UnicodeDecodeError: return raw.decode("latin-1")

def split_lines(text):
    ls = text.split("\n")
    if ls and ls[-1] == "": ls.pop()
    return [l[:-1] if l.endswith("\r") else l for l in ls]

def parse_file(path, rel):
    lang = lang_of(rel)
    if lang is None: return None
    try:
        with open(path, "rb") as f: raw = f.read(MAX_BYTES + 1)
    except OSError: return None
    if len(raw) > MAX_BYTES or b"\x00" in raw[:8192]: return None
    text = decode(raw)
    lines = split_lines(text)
    nl = len(lines)
    minified = nl > 0 and len(text) / max(nl, 1) > 400
    syms = []
    if not minified:
        try:
            if lang == "py": syms = parse_py(text, lines)
            elif lang == "md": syms = parse_md(lines)
            elif lang in ("txt", "data"): syms = []
            else: syms = parse_regex(lang, lines)
        except Exception:
            if os.environ.get("NDL_DEBUG"): raise
            syms = []
    ids = [] if minified or lang in ("data", "txt") else list({m for m in ID_RX.findall(text) if m not in KW})[:4000]
    bags = []
    ld = getattr(parse_py, "longdoc", {}) if lang == "py" else {}
    for sy in syms:
        c = Counter(m for m in ID_RX.findall("\n".join(lines[sy[2] - 1:min(sy[3], sy[2] + 400)])) if m not in KW and m != sy[0])
        extra = ld.get((sy[0], sy[2]), "")
        bags.append((extra + " " + " ".join(w for w, _ in c.most_common(24))).strip())
    head = ""
    for l in lines[:40]:
        s = l.strip().strip("#/*-!\"' ")
        if len(s) > 12 and not s.startswith(("import", "from ", "use ", "package", "require", "include", "<?php", "usr/bin")):
            head = s[:160]; break
    return {"l": lang, "n": nl, "t": tok(text), "h": sha(raw), "s": syms, "b": bags, "i": ids, "d": head}

# ----------------------------------------------------------------------------- state & index
def find_root(start=None):
    if os.environ.get("NDL_ROOT"): return os.path.abspath(os.environ["NDL_ROOT"])
    d = os.path.realpath(start or os.getcwd())
    cur = d
    while True:
        if os.path.isdir(os.path.join(cur, ".git")) or os.path.isdir(os.path.join(cur, STATE)): return cur
        nxt = os.path.dirname(cur)
        if nxt == cur: return d
        cur = nxt

_STATE_BASE = {}

def state_base(root):
    """<root>/.needlepoint, or a per-project cache dir when the project isn't writable."""
    if root in _STATE_BASE: return _STATE_BASE[root]
    base = os.environ.get("NDL_STATE_DIR") and os.path.join(os.environ["NDL_STATE_DIR"], sha(os.path.abspath(root)))
    if not base:
        base = os.path.join(root, STATE)
        try:
            os.makedirs(base, exist_ok=True)
            probe = os.path.join(base, ".w%d" % os.getpid())
            with open(probe, "w") as f: f.write("")
            os.remove(probe)
        except OSError:
            cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
            base = os.path.join(cache, "needlepoint", sha(os.path.abspath(root)))
    _STATE_BASE[root] = base
    return base

def sdir(root, *sub):
    p = os.path.join(state_base(root), *sub)
    os.makedirs(p, exist_ok=True)
    gi = os.path.join(state_base(root), ".gitignore")
    if not os.path.exists(gi):
        try:
            with open(gi, "w", encoding="utf-8") as f: f.write("# needlepoint state. notes.json is meant to be shared with your team.\n*\n!.gitignore\n!notes.json\n")
        except OSError: pass
    return p

def atomic_write(path, data, gz=False):
    tmp = path + ".%d.tmp" % os.getpid()
    if gz:
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=3) as f: f.write(data)
    else:
        with open(tmp, "w", encoding="utf-8") as f: f.write(data)
    os.replace(tmp, path)

def load_json(path, gz=False, default=None):
    try:
        if gz:
            with gzip.open(path, "rt", encoding="utf-8") as f: return json.load(f)
        with open(path, encoding="utf-8") as f: return json.load(f)
    except (OSError, ValueError, EOFError):
        return default

def list_files(root):
    out = None
    if shutil.which("git") and os.path.isdir(os.path.join(root, ".git")):
        try:
            r = subprocess.run(["git", "-C", root, "ls-files", "-co", "--exclude-standard", "-z"],
                               capture_output=True, timeout=30)
            if r.returncode == 0:
                out = [p for p in r.stdout.decode("utf-8", "replace").split("\0") if p]
                out = [p for p in out if not any(seg in IGNORE_DIRS for seg in p.split("/")[:-1])]
        except (OSError, subprocess.SubprocessError):
            out = None
    if out is None:
        out = []
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in IGNORE_DIRS and not d.startswith(".")]
            for fn in fns:
                out.append(os.path.relpath(os.path.join(dp, fn), root).replace(os.sep, "/"))
            if len(out) > 4 * MAX_FILES: break
    out = [p for p in out if lang_of(p) is not None]
    if len(out) > MAX_FILES:
        print("[ndl] %d files; indexing the first %d (set NDL_MAX_FILES to raise)" % (len(out), MAX_FILES), file=sys.stderr)
        out = out[:MAX_FILES]
    return out

def _parse_job(args):
    root, rel = args
    return rel, parse_file(os.path.join(root, rel), rel)

class _DbFiles:
    """dict-like, lazily loaded view of the files table (only rows actually used are decoded)."""
    def __init__(self, con):
        self.con = con; self._c = {}; self._rels = None; self._all = False

    def _relset(self):
        if self._rels is None: self._rels = set(r for (r,) in self.con.execute("SELECT rel FROM files"))
        return self._rels

    def _load(self, rel):
        row = self.con.execute("SELECT rec, pr, m, z FROM files WHERE rel = ?", (rel,)).fetchone()
        if not row: return None
        rec = json.loads(row[0]); rec["pr"] = row[1]; rec["m"] = row[2]; rec["z"] = row[3]
        self._c[rel] = rec
        return rec

    def get(self, rel, default=None):
        if rel in self._c: return self._c[rel]
        if self._all or rel not in self._relset(): return default
        rec = self._load(rel)
        return default if rec is None else rec

    def __getitem__(self, rel):
        rec = self.get(rel)
        if rec is None: raise KeyError(rel)
        return rec

    def __contains__(self, rel): return rel in self._c or rel in self._relset()
    def __iter__(self): return iter(sorted(self._relset()))
    def __len__(self): return len(self._relset())
    def keys(self): return list(self)

    def items(self):
        if not self._all:
            for rel, rec, pr, m, z in self.con.execute("SELECT rel, rec, pr, m, z FROM files"):
                if rel not in self._c:
                    r = json.loads(rec); r["pr"] = pr; r["m"] = m; r["z"] = z; self._c[rel] = r
            self._all = True; self._rels = set(self._c)
        return list(self._c.items())

    def values(self): return [v for _, v in self.items()]

class _DbDefs:
    def __init__(self, con): self.con = con
    def get(self, name, default=None):
        r = self.con.execute("SELECT rel, k FROM defs WHERE name = ?", (name,)).fetchall()
        return r or default
    def nocase(self, name):
        return self.con.execute("SELECT rel, k FROM defs WHERE name = ? COLLATE NOCASE", (name,)).fetchall()

class Index:
    def __init__(self, root, data=None, con=None):
        self.root = root; self.con = con; self._defs = None
        if con is not None:
            self.files = _DbFiles(con)
            self.gen = (con.execute("SELECT v FROM meta WHERE k = 'gen'").fetchone() or [""])[0]
        else:
            self.files = data["files"]; self.gen = data.get("gen")

    @property
    def data(self):  # what search building needs: {"files": mapping, "gen": ...}
        return {"files": self.files, "gen": self.gen}

    @property
    def defs(self):
        if self.con is not None: return _DbDefs(self.con)
        if self._defs is None:
            d = defaultdict(list)
            for rel, rec in self.files.items():
                for k, s in enumerate(rec["s"]): d[s[0]].append((rel, k))
            self._defs = d
        return self._defs

    def defs_nocase(self, name):
        if self.con is not None: return self.defs.nocase(name)
        tl = name.lower()
        return [loc for n, locs in self.defs.items() if n.lower() == tl for loc in locs]

    def qname(self, s):
        return (s[4] + "." + s[0]) if s[4] else s[0]

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v);
CREATE TABLE IF NOT EXISTS files(rel TEXT PRIMARY KEY, m INTEGER, z INTEGER, pr REAL, rec TEXT);
CREATE TABLE IF NOT EXISTS ids(rel TEXT PRIMARY KEY, data TEXT);
CREATE TABLE IF NOT EXISTS defs(name TEXT, rel TEXT, k INTEGER, kind TEXT);
CREATE INDEX IF NOT EXISTS defs_name ON defs(name);
CREATE INDEX IF NOT EXISTS defs_rel ON defs(rel);
CREATE TABLE IF NOT EXISTS skip(rel TEXT PRIMARY KEY, m INTEGER, z INTEGER);
"""

def _open_db(root, force=False):
    path = os.path.join(sdir(root), "index.db")
    con = sqlite3.connect(path, timeout=120)
    try:
        con.executescript(SCHEMA)
        ver = con.execute("SELECT v FROM meta WHERE k = 'ver'").fetchone()
    except sqlite3.DatabaseError:            # corrupt file: start over
        con.close(); os.remove(path)
        con = sqlite3.connect(path, timeout=120); con.executescript(SCHEMA); ver = None
    if force or not ver or ver[0] != IDX_VER:
        with con:
            for t in ("files", "ids", "defs", "skip", "meta"): con.execute("DELETE FROM %s" % t)
            con.execute("INSERT OR REPLACE INTO meta VALUES ('ver', ?)", (IDX_VER,))
    return con

def build_index(root, quiet=True, force=False):
    if sqlite3 is None: return _build_index_json(root, quiet, force)
    con = _open_db(root, force)
    manifest = {rel: (m, z) for rel, m, z in con.execute("SELECT rel, m, z FROM files")}
    skipped = {rel: (m, z) for rel, m, z in con.execute("SELECT rel, m, z FROM skip")}
    rels = list_files(root)
    todo, present = [], set()
    for rel in rels:
        try: st = os.stat(os.path.join(root, rel))
        except OSError: continue
        if st.st_size > MAX_BYTES: continue
        present.add(rel)
        key = (st.st_mtime_ns, st.st_size)
        if manifest.get(rel) == key or skipped.get(rel) == key: continue
        todo.append((rel, st))
    removed = [r for r in manifest if r not in present]
    gone_skip = [r for r in skipped if r not in present]
    if not todo and not removed and not gone_skip:
        return Index(root, con=con), 0
    t0 = time.time()
    res = _parse_many(root, todo)
    stmap = {rel: st for rel, st in todo}
    parsed = {rel: rec for rel, rec in res if rec is not None}
    with con:
        con.execute("BEGIN IMMEDIATE")
        for rel in removed + [r for r, _ in todo]:
            con.execute("DELETE FROM files WHERE rel = ?", (rel,)); con.execute("DELETE FROM defs WHERE rel = ?", (rel,))
            con.execute("DELETE FROM ids WHERE rel = ?", (rel,)); con.execute("DELETE FROM skip WHERE rel = ?", (rel,))
        for rel in gone_skip: con.execute("DELETE FROM skip WHERE rel = ?", (rel,))
        for rel, rec in res:
            st = stmap[rel]
            if rec is None:
                con.execute("INSERT OR REPLACE INTO skip VALUES (?,?,?)", (rel, st.st_mtime_ns, st.st_size)); continue
            ids = rec.pop("i", [])
            con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?)", (rel, st.st_mtime_ns, st.st_size, 0.0, json.dumps(rec, separators=(",", ":"))))
            con.execute("INSERT OR REPLACE INTO ids VALUES (?,?)", (rel, json.dumps(ids)))
            con.executemany("INSERT INTO defs VALUES (?,?,?,?)", ((sy[0], rel, k, sy[1]) for k, sy in enumerate(rec["s"])))
        nfiles = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        if len(parsed) + len(removed) > max(3, nfiles // 50):      # big change: recompute centrality
            files = {}
            for rel, data in con.execute("SELECT rel, data FROM ids"): files[rel] = {"i": json.loads(data), "s": []}
            for name, rel, kind in con.execute("SELECT name, rel, kind FROM defs"):
                if rel in files: files[rel]["s"].append([name, kind])
            _pagerank({"files": files})
            con.executemany("UPDATE files SET pr = ? WHERE rel = ?", ((f["pr"], rel) for rel, f in files.items()))
        h = hashlib.sha1()   # generation = hash of the manifest: concurrent builders of the same content agree
        for rel, m, z in con.execute("SELECT rel, m, z FROM files ORDER BY rel"): h.update(("%s\0%d\0%d\n" % (rel, m, z)).encode("utf-8", "replace"))
        con.execute("INSERT OR REPLACE INTO meta VALUES ('gen', ?)", (h.hexdigest()[:12],))
    if not quiet:
        print("[ndl] indexed %d files (%d parsed) in %.1fs" % (nfiles, len(todo), time.time() - t0), file=sys.stderr)
    return Index(root, con=con), len(todo)

def _parse_many(root, todo):
    jobs = [(root, rel) for rel, _ in todo]
    if len(jobs) > 300 and (os.cpu_count() or 1) > 1:
        try:
            from concurrent.futures import ProcessPoolExecutor
            import multiprocessing
            ctx = multiprocessing.get_context(os.environ.get("NDL_MP_START") or None)  # "spawn" = macOS/Windows default
            with ProcessPoolExecutor(max_workers=min(8, os.cpu_count() or 2), mp_context=ctx) as ex:
                return list(ex.map(_parse_job, jobs, chunksize=32))
        except Exception:
            pass
    return [_parse_job(j) for j in jobs]

def _build_index_json(root, quiet=True, force=False):
    sd = sdir(root)
    ipath = os.path.join(sd, "index.json.gz")
    old = None if force else load_json(ipath, gz=True)
    if old and old.get("v") != IDX_VER: old = None
    oldf = old["files"] if old else {}
    olds = old.get("skip", {}) if old else {}
    rels = list_files(root)
    files, todo, skip = {}, [], {}
    for rel in rels:
        try: st = os.stat(os.path.join(root, rel))
        except OSError: continue
        if st.st_size > MAX_BYTES: continue
        o = oldf.get(rel)
        if o and o.get("m") == st.st_mtime_ns and o.get("z") == st.st_size: files[rel] = o
        elif olds.get(rel) == [st.st_mtime_ns, st.st_size]: skip[rel] = olds[rel]      # known binary/minified/unparseable
        else: todo.append((rel, st))
    changed = bool(todo) or len(files) != len(oldf)
    t0 = time.time()
    if todo:
        jobs = [(root, rel) for rel, _ in todo]
        res = []
        if len(jobs) > 300 and (os.cpu_count() or 1) > 1:
            try:
                from concurrent.futures import ProcessPoolExecutor
                import multiprocessing
                ctx = multiprocessing.get_context(os.environ.get("NDL_MP_START") or None)  # "spawn" = macOS/Windows default
                with ProcessPoolExecutor(max_workers=min(8, os.cpu_count() or 2), mp_context=ctx) as ex:
                    res = list(ex.map(_parse_job, jobs, chunksize=32))
            except Exception:
                res = [_parse_job(j) for j in jobs]
        else:
            res = [_parse_job(j) for j in jobs]
        stmap = {rel: st for rel, st in todo}
        for rel, rec in res:
            if rec is None:
                skip[rel] = [stmap[rel].st_mtime_ns, stmap[rel].st_size]; continue
            rec["m"] = stmap[rel].st_mtime_ns; rec["z"] = stmap[rel].st_size
            files[rel] = rec
    if not changed and old:
        if skip != olds:
            old["skip"] = skip; atomic_write(ipath, json.dumps(old, separators=(",", ":")), gz=True)
        return Index(root, old), 0
    gen = sha("".join("%s%s%s" % (r, f.get("m"), f.get("z")) for r, f in sorted(files.items())))
    data = {"v": IDX_VER, "root": root, "built": time.time(), "files": files, "skip": skip, "gen": gen}
    reparsed = sum(1 for rel, _ in todo if rel in files)
    if old and reparsed <= max(3, len(files) // 50) and len(files) >= len(oldf) - 3:
        for rel in files: files[rel].setdefault("pr", 0.0)        # small change: keep centrality, skip PageRank
    else:
        _pagerank(data)
    atomic_write(ipath, json.dumps(data, separators=(",", ":")), gz=True)   # search data is rebuilt lazily (gen check)
    if not quiet:
        print("[ndl] indexed %d files (%d parsed) in %.1fs" % (len(files), len(todo), time.time() - t0), file=sys.stderr)
    return Index(root, data), len(todo)

def _pagerank(data):
    files = data["files"]
    defmap = defaultdict(set)
    for rel, rec in files.items():
        for s in rec["s"]:
            if len(s[0]) >= 4 and s[1] in ("function", "class", "method", "type", "var", "impl", "macro", "table", "decl", "inner"):
                defmap[s[0]].add(rel)
    names = list(files)
    pos = {r: i for i, r in enumerate(names)}
    N = len(names) or 1
    out = [defaultdict(float) for _ in names]
    for rel, rec in files.items():
        a = pos[rel]
        for ident in rec.get("i", []):
            tg = defmap.get(ident)
            if not tg or len(tg) > 4: continue
            w = 1.0 / len(tg)
            for b in tg:
                if b != rel: out[a][pos[b]] += w
    for row in out:
        for k in row: row[k] = math.sqrt(row[k])
    pr = [1.0 / N] * N
    for _ in range(24):
        nxt = [0.15 / N] * N
        sink = 0.0
        for a, row in enumerate(out):
            tot = sum(row.values())
            if tot <= 0: sink += pr[a]; continue
            for b, w in row.items(): nxt[b] += 0.85 * pr[a] * w / tot
        add = 0.85 * sink / N
        pr = [x + add for x in nxt]
    mx = max(pr) if pr else 1
    for rel in names: files[rel]["pr"] = round(pr[pos[rel]] / mx, 5)

# ----------------------------------------------------------------------------- search
SPLIT = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
STOP = set("""the a an of to in for on and or is are be by with from as at it its this that these those which what
how where when do does into via use used using code file files function functions method methods class classes
implement implementation implemented return returns given some all any each find where there their them then
should would could can will shall may must one two new make makes""".split())

def stem(w):
    if len(w) <= 3: return w
    if w.endswith("ies") and len(w) > 4: w = w[:-3] + "y"
    elif w.endswith("sses"): w = w[:-2]
    elif w.endswith("es") and len(w) > 4 and w[-3] in "sxzh": w = w[:-2]
    elif w.endswith("s") and not w.endswith("ss") and len(w) > 3: w = w[:-1]
    if w.endswith("ing") and len(w) > 5: w = w[:-3]
    elif w.endswith("ed") and len(w) > 4: w = w[:-2]
    if w.endswith("e") and len(w) > 3: w = w[:-1]
    return w

_WCACHE = {}

def _word_terms(w):
    r = _WCACHE.get(w)
    if r is None:
        parts = [p for seg in w.split("_") for p in SPLIT.findall(seg)] or [w]
        full = [stem(p.lower()) for p in parts if len(p) >= 2]
        nostop = tuple(stem(p.lower()) for p in parts if len(p) >= 2 and p.lower() not in STOP)
        extra = ()
        if len(parts) > 1:
            j = w.lower().replace("_", "")
            if len(j) > 3: extra = (j,)
        r = (tuple(full) + extra, nostop + extra)
        if len(_WCACHE) < 500000: _WCACHE[w] = r
    return r

def terms(text, keep_stop=False):
    out = []; ix = 0 if keep_stop else 1; get = _WCACHE.get
    for w in WORD.findall(text or ""):
        r = get(w)
        out.extend((r or _word_terms(w))[ix])
    return out

def _search_docs(data):
    """yield (rel, sym_index or -1, {term: weighted tf}, length) for every searchable document."""
    for rel, rec in data["files"].items():
        c = {}
        for t in terms(rel.rsplit(".", 1)[0].replace("/", " ")): c[t] = c.get(t, 0) + 2
        for t in terms(rec.get("d", "")): c[t] = c.get(t, 0) + 1
        yield rel, -1, c
        lastdir = rel.rsplit("/", 2)
        ptail = terms(" ".join(lastdir[-2:]).rsplit(".", 1)[0])
        bags = rec.get("b") or []
        for k, s in enumerate(rec["s"]):
            if s[1] == "var" and s[0].startswith("__"): continue
            c = {}
            if k < len(bags):
                for t in set(terms(bags[k])): c[t] = 1
            for t in terms(s[0]): c[t] = c.get(t, 0) + 4
            if s[4]:
                for t in terms(s[4]): c[t] = c.get(t, 0) + 1
            for t in terms(s[5]): c[t] = c.get(t, 0) + 1
            for t in terms(s[6]): c[t] = c.get(t, 0) + 2
            for t in ptail: c[t] = c.get(t, 0) + 1
            yield rel, k, c

def _search_path(root, gen):
    return os.path.join(sdir(root), "search-%s.%s" % (gen or "x", "db" if sqlite3 else "json.gz"))

def _postings_for(items):
    """items: iterable of (rel, rec) -> (docs [(rel, k)], lens array, {term: array[doc, tf, ...]})"""
    from array import array
    docs, lens, post = [], array("i"), {}
    for rel, k, c in _search_docs({"files": dict(items)}):
        di = len(docs); docs.append((rel, k)); lens.append(sum(c.values()) or 1)
        for t, f in c.items():
            pl = post.get(t)
            if pl is None: pl = post[t] = array("i")
            pl.append(di); pl.append(f)
    return docs, lens, post

def _build_search(root, data):
    from array import array
    gen = data.get("gen")
    dst = _search_path(root, gen)
    tmp = dst + ".%d.tmp" % os.getpid()
    files = data["files"]
    docs, lens, post = _postings_for(files.items())
    avg = (sum(lens) / len(lens)) if lens else 1
    if sqlite3:
        flist = sorted({r for r, _ in docs}); fpos = {r: i for i, r in enumerate(flist)}
        docfile = array("i", (fpos[r] for r, _ in docs))
        con = sqlite3.connect(tmp)
        con.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;"
                          "CREATE TABLE docs(id INTEGER PRIMARY KEY, rel TEXT, si INTEGER);"
                          "CREATE TABLE post(term TEXT PRIMARY KEY, data BLOB) WITHOUT ROWID;"
                          "CREATE TABLE meta(k TEXT PRIMARY KEY, v);"
                          "CREATE TABLE cov(rel TEXT PRIMARY KEY, m INTEGER, z INTEGER, fi INTEGER);")
        con.executemany("INSERT INTO docs VALUES (?,?,?)", ((i, r, k) for i, (r, k) in enumerate(docs)))
        con.executemany("INSERT INTO post VALUES (?,?)", ((t, pl.tobytes()) for t, pl in post.items()))
        con.executemany("INSERT INTO cov VALUES (?,?,?,?)", ((r, files[r].get("m"), files[r].get("z"), fpos.get(r, -1)) for r in files))
        con.executemany("INSERT INTO meta VALUES (?,?)", [("gen", gen or ""), ("avg", avg), ("n", len(docs)),
                                                          ("lens", lens.tobytes()), ("docfile", docfile.tobytes())])
        con.commit(); con.close()
    else:
        atomic_write(tmp, json.dumps({"gen": gen, "docs": docs, "lens": list(lens), "avg": avg,
                                      "post": {t: list(pl) for t, pl in post.items()}}, separators=(",", ":")), gz=True)
    try: os.replace(tmp, dst)
    except OSError:  # another process built the same generation and has it open (Windows)
        try: os.remove(tmp)
        except OSError: pass
    for x in os.listdir(sdir(root)):   # drop other generations once they are a minute old (others may still read them)
        fx = os.path.join(sdir(root), x)
        try:
            if x.startswith("search") and fx != dst and time.time() - os.path.getmtime(fx) > 60: os.remove(fx)
        except OSError: pass

class _Search:
    """postings for a base generation, plus an in-memory overlay for files changed since (sqlite backend)."""
    def __init__(self, path):
        from array import array
        self.path = path; self.excl = set(); self.delta = None
        if sqlite3:
            import pathlib  # read-only: a missing file must fail, never be silently recreated empty
            self.con = sqlite3.connect(pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro", uri=True, timeout=60)
            meta = dict(self.con.execute("SELECT k, v FROM meta"))
            self.N = int(meta["n"]); self.avg = float(meta["avg"])
            self.lens = array("i"); self.lens.frombytes(meta["lens"])
            self.docfile = array("i"); self.docfile.frombytes(meta["docfile"])
        else:
            S = load_json(path, gz=True)
            self.S = S; self.N = len(S["docs"]); self.avg = S["avg"]; self.lens = S["lens"]

    def overlay(self, idx):
        """bring the base up to date with the current index; returns False if a full rebuild is better."""
        if not sqlite3 or idx.con is None: return True
        cov = {r: (m, z, fi) for r, m, z, fi in self.con.execute("SELECT rel, m, z, fi FROM cov")}
        cur = {r: (m, z) for r, m, z in idx.con.execute("SELECT rel, m, z FROM files")}
        changed = [r for r, mz in cur.items() if cov.get(r, (None, None))[:2] != mz]
        gone = [r for r in cov if r not in cur]
        if len(changed) + len(gone) > max(300, len(cur) // 20): return False
        self.excl = {cov[r][2] for r in changed + gone if r in cov and cov[r][2] >= 0}
        if changed:
            docs, lens, post = _postings_for((r, idx.files[r]) for r in changed)
            self.delta = (self.N, docs, lens, post)   # delta doc ids start at N
        return True

    def postings(self, t):
        """flat [doc, tf, doc, tf, ...]"""
        if sqlite3:
            from array import array
            row = self.con.execute("SELECT data FROM post WHERE term = ?", (t,)).fetchone()
            a = array("i")
            if row: a.frombytes(row[0])
            if self.excl:
                ex, df = self.excl, self.docfile
                b = array("i")
                for x in range(0, len(a), 2):
                    if df[a[x]] not in ex: b.append(a[x]); b.append(a[x + 1])
                a = b
            if self.delta:
                off, _, _, dpost = self.delta
                for x in range(0, len(dpost.get(t, ())), 2):
                    a.append(dpost[t][x] + off); a.append(dpost[t][x + 1])
            return a
        return self.S["post"].get(t, ())

    def doclen(self, d):
        if self.delta and d >= self.delta[0]: return self.delta[2][d - self.delta[0]]
        return self.lens[d]

    def where(self, ids):
        """{doc: (rel, sym_index)} for the given doc ids"""
        if not sqlite3: return {d: tuple(self.S["docs"][d]) for d in ids}
        out, base = {}, []
        for d in ids:
            if self.delta and d >= self.delta[0]: out[d] = tuple(self.delta[1][d - self.delta[0]])
            else: base.append(d)
        for i in range(0, len(base), 500):
            ch = base[i:i + 500]
            for d, r, k in self.con.execute("SELECT id, rel, si FROM docs WHERE id IN (%s)" % ",".join("?" * len(ch)), ch):
                out[d] = (r, k)
        return out

_SEARCH = {}

def _latest_search(root):
    d = sdir(root)
    c = [os.path.join(d, x) for x in os.listdir(d) if x.startswith("search-") and not x.endswith(".tmp")]
    return max(c, key=os.path.getmtime) if c else None

def _searcher(idx):
    key = (idx.root, idx.gen)
    if key in _SEARCH: return _SEARCH[key]
    sr = None
    path = _search_path(idx.root, idx.gen)
    cand = path if os.path.exists(path) else (_latest_search(idx.root) if sqlite3 and idx.con is not None else None)
    if cand:
        try:
            sr = _Search(cand)
            if not (sqlite3 and idx.con is not None) and getattr(sr, "S", {}).get("gen") != idx.gen: sr = None
            elif not sr.overlay(idx): sr = None
        except Exception:
            sr = None
    if sr is None:
        _build_search(idx.root, idx.data)
        sr = _Search(path)
    _SEARCH[key] = sr
    return sr

def is_test(rel):
    r = rel.lower()
    return bool(re.search(r"(^|/)(tests?|__tests__|spec|specs|testing)/|(^|/)test_|_test\.|\.test\.|\.spec\.|tests?\.py$", r))

W = {"k1": 1.6, "b": 0.55, "exact": 3.0, "subset": 2.0, "cov": 0.7, "pr": 0.25, "test": 0.75, "doc": 0.7}
def search(idx, query, k=10, kind=None, inglob=None):
    if not idx.files: return []
    sr = _searcher(idx)
    qt = terms(query)
    if not qt: qt = terms(query, keep_stop=True)
    N = sr.N; avg = sr.avg
    k1, b = W["k1"], W["b"]
    scores = defaultdict(float); hits = defaultdict(int); dl = sr.doclen
    for t in set(qt):
        pl = sr.postings(t)
        if not pl: continue
        n_t = len(pl) // 2
        idf = math.log(1 + (N - n_t + 0.5) / (n_t + 0.5))
        qw = qt.count(t)
        for x in range(0, len(pl), 2):
            di = pl[x]; f = pl[x + 1]
            scores[di] += qw * idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl(di) / avg))
            hits[di] += 1
    search.total = len(scores)
    if len(scores) > 3000:   # re-rank only the strongest BM25 candidates (boosts are bounded)
        keep = sorted(scores, key=scores.get, reverse=True)[:3000]
        scores = {d: scores[d] for d in keep}
    where = sr.where(scores.keys())
    qraw = {w.lower() for w in WORD.findall(query)}
    qset = set(qt); want_test = any(w.startswith("test") for w in qraw)
    res = []
    for di, sc in scores.items():
        if di not in where: continue
        rel, si = where[di]
        rec = idx.files.get(rel)
        if rec is None: continue
        if inglob and not _glob(rel, inglob): continue
        if si >= 0:
            if si >= len(rec["s"]): continue
            s = rec["s"][si]
            if kind and not s[1].startswith(kind): continue
            nm = s[0].lower()
            if nm in qraw or (s[4] and (s[4] + "." + s[0]).lower() in qraw): sc += W["exact"]
            ntoks = set(terms(s[0]))
            if ntoks and ntoks <= qset: sc += W["subset"]
            if s[1] in ("var", "attr", "key", "step", "decl", "rule"): sc *= 0.85
            elif s[1].startswith("h"): sc *= W["doc"]
        elif kind: continue
        else: sc *= 0.7
        cov = hits[di] / max(1, len(qset))
        sc *= (1 - W["cov"]) + W["cov"] * cov
        sc *= 1 + W["pr"] * math.log1p(9 * rec.get("pr", 0))
        if not want_test and is_test(rel): sc *= W["test"]
        res.append((sc, rel, si))
    res.sort(key=lambda x: -x[0])
    return res[:k] if k else res

def _glob(rel, g):
    import fnmatch
    return any(fnmatch.fnmatch(rel, x) or rel.startswith(x.rstrip("*/") + "/") or rel == x for x in g.split(","))

# ----------------------------------------------------------------------------- rendering helpers
def read_lines(root, rel):
    try:
        with open(os.path.join(root, rel), "rb") as f:
            return split_lines(decode(f.read()))
    except OSError:
        return []

def numbered(lines, a, b, fold=None):
    out = []; i = a; b = min(b, len(lines))
    while i <= b:
        if fold and i in fold:
            j = fold[i]
            out.append("\t⋯ docstring lines %d-%d folded (%d lines; `show --full` or Read offset=%d limit=%d)" % (i, j, j - i + 1, i, j - i + 1))
            i = j + 1; continue
        out.append("%d\t%s" % (i, lines[i - 1].rstrip())); i += 1
    return "\n".join(out)

DOC_OPEN = re.compile(r'^\s*[rRuUbB]{0,2}("""|\'\'\')')

def doc_folds(lines, a, b, keep=2, minlen=8):
    """{first_folded_line: last_folded_line} for long python docstrings inside lines a..b (1-based)."""
    folds = {}
    i = a
    while i <= min(b, len(lines)):
        m = DOC_OPEN.match(lines[i - 1])
        prev = lines[i - 2].rstrip() if i >= 2 else ""
        if m and (prev.endswith(":") or i == a):
            q = m.group(1); rest = lines[i - 1][m.end():]
            if q in rest:
                i += 1; continue
            j = i + 1
            while j <= min(b, len(lines)) and q not in lines[j - 1]: j += 1
            if j - i + 1 >= minlen and j <= b:
                folds[i + keep] = j
            i = j + 1; continue
        i += 1
    return folds

def sym_text(lines, s):
    return "\n".join(l.rstrip() for l in lines[s[2] - 1:s[3]])

def fmt_sym(rel, s, with_doc=True):
    q = (s[4] + "." + s[0]) if s[4] else s[0]
    sig = s[5]
    out = "%s:%d-%d  %s %s" % (rel, s[2], s[3], s[1], q if (q not in sig or s[1].startswith("h")) else "")
    out = out.rstrip() + ("  " + sig if sig and not s[1].startswith("h") else "")
    if with_doc and s[6]: out += "  — " + s[6]
    return re.sub(r"\s{3,}", "  ", out)

DOC_QUOTES = ('"""', "'''")
SELF_KIND = re.compile(r"^(?:@\S+\s+)*(?:(?:export|default|public|private|protected|internal|static|async|pub(?:\([^)]*\))?|abstract|final|open|override|unsafe|const|declare|sealed|data|inline|suspend)\s+)*(?:def|class|function|func|fn|struct|enum|trait|impl|interface|type|module|namespace|fun|object|record|protocol|extension|macro_rules!|mod|#|CREATE|create)\b")

def outline_text(idx, rel, lines=None, max_lines=160, notes=True, docs=True):
    rec = idx.files.get(rel)
    if rec is None: return None
    lines = lines if lines is not None else read_lines(idx.root, rel)
    syms = rec["s"]
    out = ["%s — %d lines, ~%s tokens, %s" % (rel, rec["n"], "{:,}".format(rec["t"]), rec["l"])]
    depth_of = {}
    stack = []
    for s in sorted(syms, key=lambda s: (s[2], -s[3])):
        while stack and not (stack[-1][2] <= s[2] and s[3] <= stack[-1][3]): stack.pop()
        depth_of[id(s)] = len(stack); stack.append(s)
    tops = [s for s in syms if depth_of.get(id(s), 0) == 0]
    cov = 1
    rows = []

    def gap(a, b):
        if b < a: return
        prev = ""; code = False; first_code = ""
        for i in range(a, b + 1):
            if i - 1 >= len(lines): break
            t = lines[i - 1].strip()
            if t and not CMT.match(lines[i - 1]) and not t.startswith(DOC_QUOTES): code = True
            if not prev and t.strip(" \t\"'#/*-=!"): prev = t[:70]
            if code and not first_code and t and not CMT.match(lines[i - 1]): first_code = t[:70]
        if prev: rows.append((a, b, 0, "  %d-%d · %s" % (a, b, (first_code or prev) if code else "(comment)")))

    for s in tops:
        if s[2] > cov: gap(cov, s[2] - 1)
        cov = max(cov, s[3] + 1)
    if cov <= rec["n"]: gap(cov, rec["n"])
    for s in syms:
        depth = depth_of.get(id(s), 0)
        sig = s[5] if s[5] and not s[1].startswith("h") else ("#" * int(s[1][1:]) + " " + s[0] if s[1][1:].isdigit() else s[0])
        kind = "" if SELF_KIND.match(sig) else s[1] + " "
        ln = "  " * depth + "  %d-%d %s%s" % (s[2], s[3], kind, sig)
        if s[6] and docs: ln += "  — " + s[6][:90]
        rows.append((s[2], s[3], depth, ln))
    rows.sort(key=lambda r: (r[0], r[2]))
    total = len(rows)
    lvl = 8
    while len(rows) > max_lines and lvl >= 1:  # drop deepest nesting first (parents still cover those lines)
        rows = [r for r in rows if r[2] < lvl]
        lvl -= 1
    if len(rows) > max_lines:  # collapse the middle into one explicit range so coverage stays 100%
        head_n = max(1, max_lines - 16); tail_n = 15
        mid = rows[head_n:len(rows) - tail_n]
        names = [re.sub(r"^\s*\d+-\d+\s+\S+\s+", "", r[3])[:40] for r in (mid[0], mid[-1])]
        a_, b_ = min(r[0] for r in mid), max(r[1] for r in mid)
        rows = rows[:head_n] + [(a_, b_, 0, "  %d-%d  … %d entries collapsed (%s … %s) — ndl outline %s --all, or read the range"
                                 % (a_, b_, len(mid), names[0], names[1], rel))] + rows[len(rows) - tail_n:]
    body = [r[3] for r in rows]
    if len(rows) < total and "collapsed" not in body[-16 if len(body) > 16 else 0:][0:1].__str__():
        hidden = total - len(rows)
        if hidden > 0 and not any("collapsed" in b for b in body):
            body.append("  (%d nested entries hidden — parents above cover their lines; ndl outline %s --all)" % (hidden, rel))
    out += body
    if notes:
        for nline in notes_for(idx, rel): out.append("  📝 " + nline)
    return "\n".join(out)

# ----------------------------------------------------------------------------- notes (hash-anchored memory)
def _notes_path(root): return os.path.join(sdir(root), "notes.json")

def notes_load(root): return load_json(_notes_path(root), default=[]) or []

def _anchor_hash(lines, s):
    return sha("\n".join(l.rstrip() for l in lines[s[2] - 1:s[3]]))

def note_status(idx, n):
    rec = idx.files.get(n["file"])
    if rec is None: return "orphaned", None
    lines = read_lines(idx.root, n["file"])
    if not n.get("sym"):
        return ("valid" if rec["h"] == n["hash"] else "stale"), None
    cands = [s for s in rec["s"] if idx.qname(s) == n["sym"]]
    if not cands: return "orphaned", None
    for s in cands:
        if _anchor_hash(lines, s) == n["hash"]: return "valid", s
    return "stale", cands[0]

def notes_for(idx, rel, sym=None):
    out = []
    for n in notes_load(idx.root):
        if n["file"] != rel or (sym and n.get("sym") != sym): continue
        st, _ = note_status(idx, n)
        if st == "valid": out.append("[%s] %s%s" % (n["id"], (n["sym"] + ": ") if n.get("sym") and not sym else "", n["text"]))
        elif st == "stale": out.append("[%s] ⚠ STALE (code changed since noted — re-verify) %s" % (n["id"], n["text"]))
    return out

# ----------------------------------------------------------------------------- target resolution
def resolve(idx, target):
    """-> list of (rel, sym|None, a, b)"""
    m = re.match(r"^(.+?):(\d+)(?:-(\d+))?$", target)
    if m and m.group(1) in idx.files or (m and os.path.exists(os.path.join(idx.root, m.group(1)))):
        rel = _rel(idx, m.group(1)); a = int(m.group(2)); b = int(m.group(3) or a)
        return [(rel, None, a, b)]
    if ":" in target and not target.startswith(":"):
        f, _, nm = target.partition(":")
        nm = nm.lstrip(":")
        rel = _rel(idx, f)
        rec = idx.files.get(rel)
        if rec:
            c = [s for s in rec["s"] if idx.qname(s) == nm] or [s for s in rec["s"] if s[0] == nm] or \
                [s for s in rec["s"] if idx.qname(s).endswith("." + nm)]
            return [(rel, s, s[2], s[3]) for s in c]
    rel = _rel(idx, target)
    if rel in idx.files: return [(rel, None, 0, 0)]
    out = []
    base = target.split(".")[-1]
    for rel, k in idx.defs.get(base, []):
        s = idx.files[rel]["s"][k]
        q = idx.qname(s)
        if q == target or (q.endswith("." + target)) or (base == target and s[0] == target):
            out.append((rel, s, s[2], s[3]))
    if not out:
        for rel, k in idx.defs_nocase(target):
            s = idx.files[rel]["s"][k]; out.append((rel, s, s[2], s[3]))
    rank = {"class": 0, "function": 1, "method": 1, "impl": 2, "type": 2}
    def overload(x):
        if not x[1]: return False
        try: return bool(re.match(r"\s*@(?:\w+\.)*overload\b", read_lines(idx.root, x[0])[x[1][2] - 1]))
        except IndexError: return False
    out.sort(key=lambda x: (overload(x), rank.get(x[1][1], 3) if x[1] else 3, is_test(x[0]), -idx.files[x[0]].get("pr", 0)))
    # drop symbols nested in another candidate
    return out

def _rel(idx, p):
    if os.path.isabs(p):
        try: p = os.path.relpath(p, idx.root)
        except ValueError: pass
    p = p.replace(os.sep, "/")
    while p.startswith("./"): p = p[2:]
    return p

# ----------------------------------------------------------------------------- session ledger (dedupe)
def _ledger_dir(root, sid):
    sid = re.sub(r"[^\w.-]", "_", sid or "nosession")[:80]
    return sdir(root, "session", sid)

def ledger_get(root, sid, key):
    return load_json(os.path.join(_ledger_dir(root, sid), sha(key) + ".json"))

def ledger_put(root, sid, key, obj):
    atomic_write(os.path.join(_ledger_dir(root, sid), sha(key) + ".json"), json.dumps(obj))

def stat_log(root, event, saved, extra=None):
    try:
        rec = {"ts": int(time.time()), "ev": event, "saved": int(saved)}
        if extra: rec.update(extra)
        with open(os.path.join(sdir(root), "stats.jsonl"), "a", encoding="utf-8") as f: f.write(json.dumps(rec) + "\n")
    except OSError:
        pass

def unified(old, new, ctx=2):
    d = list(difflib.unified_diff(old, new, "before", "after", n=ctx, lineterm=""))
    return "\n".join(d[2:]) if d else ""

# ----------------------------------------------------------------------------- commands
def get_index(args_root=None, quiet=True):
    root = find_root(args_root)
    idx, _ = build_index(root, quiet=quiet)
    return idx

def cmd_index(a):
    root = find_root(a.root)
    t = time.time()
    idx, n = build_index(root, quiet=True, force=a.force)
    syms = sum(len(r["s"]) for r in idx.files.values())
    toks = sum(r["t"] for r in idx.files.values())
    print("[ndl] %s: %d files, %d symbols, ~%s tokens of source; %d (re)parsed in %.2fs" %
          (root, len(idx.files), syms, "{:,}".format(toks), n, time.time() - t))

def cmd_map(a):
    idx = get_index(a.root)
    F = idx.files
    budget = a.budget
    langs = Counter(r["l"] for r in F.values())
    toks = sum(r["t"] for r in F.values())
    out = ["[ndl map] %s — %d files, %d symbols, ~%s tokens. langs: %s" % (
        os.path.basename(idx.root) or idx.root, len(F), sum(len(r["s"]) for r in F.values()),
        "{:,}".format(toks), ", ".join("%s %d" % kv for kv in langs.most_common(6)))]
    dirs = defaultdict(lambda: [0, 0])
    for rel, r in F.items():
        parts = rel.split("/")
        d = "/".join(parts[:2]) if len(parts) > 2 else (parts[0] if len(parts) > 1 else ".")
        dirs[d][0] += 1; dirs[d][1] += r["t"]
    out.append("dirs: " + "  ".join("%s(%d)" % (d, v[0]) for d, v in sorted(dirs.items(), key=lambda kv: -kv[1][1])[:24]))
    score = {rel: r.get("pr", 0) for rel, r in F.items()}
    if a.focus:
        boost = defaultdict(float)
        for sc, rel, si in search(idx, a.focus, k=60): boost[rel] += sc
        mx = max(boost.values()) if boost else 1
        score = {rel: 0.3 * score[rel] + (boost.get(rel, 0) / mx) for rel in F}
        out.append("focus: %r" % a.focus)
    out.append("key files (most central%s; path, lines, top symbols):" % (" to focus" if a.focus else ""))
    used = tok("\n".join(out))
    for rel in sorted(F, key=lambda r: -score[r]):
        if score[rel] <= 0 and a.focus: break
        r = F[rel]
        tops = [s for s in r["s"] if not s[4] and s[1] not in ("var", "key", "step")][:10]
        if not tops: tops = [s for s in r["s"] if not s[4]][:6]
        line = "%s (%d) %s" % (rel, r["n"], ", ".join(("%s%s" % ({"class": "C:", "type": "T:"}.get(s[1], ""), s[0])) for s in tops))
        if used + tok(line) > budget: break
        out.append(line); used += tok(line) + 1
    print("\n".join(out))

def cmd_find(a):
    idx = get_index(a.root)
    q = " ".join(a.query)
    res = search(idx, q, k=0, kind=a.kind, inglob=a.inside)
    shown = res[:a.k]
    out = ['[ndl find] "%s" — top %d of %s matches' % (q, len(shown), "{:,}".format(getattr(search, "total", len(res))))]
    for i, (sc, rel, si) in enumerate(shown, 1):
        if si < 0:
            r = idx.files[rel]
            out.append("%2d. %s  (file, %d lines)%s" % (i, rel, r["n"], ("  — " + r["d"][:100]) if r.get("d") else ""))
        else:
            out.append("%2d. %s" % (i, fmt_sym(rel, idx.files[rel]["s"][si])))
    if not shown: out.append("no matches. try other words, `ndl refs <identifier>`, or rg.")
    print("\n".join(out))

def _sid():
    return os.environ.get("NDL_SESSION")

def show_one(idx, rel, s, a_, b_, force=False, ctx=0, sig_only=False, full=False, focus=None, fctx=3):
    lines = read_lines(idx.root, rel)
    if not lines: return "%s: unreadable or empty" % rel
    if s is None and a_ == 0:
        return outline_text(idx, rel, lines)
    a_ = max(1, a_ - ctx); b_ = min(len(lines), b_ + ctx)
    if s is not None:
        head = "── " + fmt_sym(rel, s)
        q = idx.qname(s)
    else:
        head = "── %s:%d-%d" % (rel, a_, b_); q = None
    if sig_only and s is not None:
        return head
    if focus:
        rx = re.compile(focus, re.I)
        hits = [i for i in range(a_, b_ + 1) if rx.search(lines[i - 1])]
        if not hits:
            return head + "\n(no lines match /%s/ in %d-%d; drop --grep to see all %d lines)" % (focus, a_, b_, b_ - a_ + 1)
        keep = sorted({j for i in hits for j in range(max(a_, i - fctx), min(b_, i + fctx) + 1)})
        out, prev = [head], None
        for j in keep:
            if prev is not None and j != prev + 1: out.append("\t…")
            out.append("%d\t%s" % (j, lines[j - 1].rstrip())); prev = j
        out.append("(%d of %d lines shown — matches for /%s/ ±%d; drop --grep for the whole symbol)" % (len(keep), b_ - a_ + 1, focus, fctx))
        stat_log(idx.root, "show-focus", tok(numbered(lines, a_, b_)) - tok("\n".join(out)))
        return "\n".join(out)
    body = numbered(lines, a_, b_, None if full or idx.files.get(rel, {}).get("l") != "py" else doc_folds(lines, a_, b_))
    sid = _sid()
    note = notes_for(idx, rel, q) if q else []
    extra = ("\n" + "\n".join("📝 " + x for x in note)) if note else ""
    if sid and not force:
        key = "show|%s|%s|%d" % (rel, q or "", a_ if not q else 0)
        prev = ledger_get(idx.root, sid, key)
        cur = lines[a_ - 1:b_]
        if prev:
            if prev["h"] == sha("\n".join(cur)):
                stat_log(idx.root, "show-dedupe", tok(body))
                return "%s\n≡ unchanged since shown earlier this session (%d lines). `-f` to reprint." % (head, b_ - a_ + 1)
            d = unified(prev["lines"], cur)
            if d and tok(d) < 0.5 * tok(body):
                ledger_put(idx.root, sid, key, {"h": sha("\n".join(cur)), "lines": cur})
                stat_log(idx.root, "show-delta", tok(body) - tok(d))
                return "%s\nΔ changed since shown earlier — diff only (`-f` for full):\n%s%s" % (head, d, extra)
        ledger_put(idx.root, sid, key, {"h": sha("\n".join(cur)), "lines": cur})
    return head + "\n" + body + extra

def cmd_show(a):
    idx = get_index(a.root)
    outs = []
    for t in a.targets:
        c = resolve(idx, t)
        if not c:
            r = search(idx, t, k=5)
            outs.append("✗ no symbol %r. closest:\n" % t + "\n".join("  " + (fmt_sym(rel, idx.files[rel]["s"][si]) if si >= 0 else rel) for _, rel, si in r))
            continue
        if len(c) > 1 and not a.all:
            tot = sum(x[3] - x[2] + 1 for x in c[:3])
            if len(c) <= 3 and tot <= 150:
                for rel, s, x, y in c: outs.append(show_one(idx, rel, s, x, y, a.force, a.ctx, a.sig, a.full, a.grep, a.C))
            else:
                outs.append("%d definitions match %r — showing #1; others (use path:Name, or --all):\n" % (len(c), t) +
                            "\n".join("  " + fmt_sym(rel, s, False) for rel, s, _, _ in c[1:12]))
                rel, s, x, y = c[0]; outs.append(show_one(idx, rel, s, x, y, a.force, a.ctx, a.sig, a.full, a.grep, a.C))
            continue
        for rel, s, x, y in (c if a.all else c[:1]):
            outs.append(show_one(idx, rel, s, x, y, a.force, a.ctx, a.sig, a.full, a.grep, a.C))
    print("\n".join(outs))

def cmd_outline(a):
    idx = get_index(a.root)
    outs = []
    for p in a.paths:
        rel = _rel(idx, p)
        if rel not in idx.files:
            # directory outline
            pre = rel.rstrip("/") + "/"
            sub = [r for r in idx.files if r.startswith(pre)]
            if not sub: outs.append("✗ %s not indexed" % p); continue
            for r in sorted(sub)[:60]:
                rec = idx.files[r]
                outs.append("%s (%d) %s" % (r, rec["n"], ", ".join(s[0] for s in rec["s"] if not s[4])[:200]))
            continue
        outs.append(outline_text(idx, rel, max_lines=100000 if a.all else 160))
    print("\n".join(outs))

def enclosing(rec, line):
    best = None
    for s in rec["s"]:
        if s[2] <= line <= s[3] and (best is None or s[3] - s[2] <= best[3] - best[2]): best = s
    return best

def find_refs(idx, name, limit=None, regex=False, paths=None):
    hits = []
    rg = shutil.which("rg")
    if rg:
        try:
            mode = ["-i"] if regex else ["-w", "-F"]
            tgt = [os.path.join(idx.root, p_) for p_ in paths] if paths else [idx.root]
            r = subprocess.run([rg, "-n", "-H"] + mode + ["--no-heading", "--color", "never", "-M", "300", "-e", name] + tgt,
                               capture_output=True, timeout=60)
            for ln in r.stdout.decode("utf-8", "replace").splitlines():
                m = re.match(r"^(.*?):(\d+):(.*)$", ln)
                if not m: continue
                rel = os.path.relpath(m.group(1), idx.root).replace(os.sep, "/")
                if rel in idx.files: hits.append((rel, int(m.group(2)), m.group(3)))
        except (OSError, subprocess.SubprocessError):
            rg = None
    if not rg:
        rx = re.compile(name, re.I) if regex else re.compile(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])")
        for rel in idx.files:
            if paths and not any(rel == p_ or rel.startswith(p_.rstrip("/") + "/") for p_ in paths): continue
            for i, l in enumerate(read_lines(idx.root, rel), 1):
                if rx.search(l): hits.append((rel, i, l))
    hits.sort()
    return hits

def cmd_grep(a):
    a.name = a.pattern; a.regex = True
    cmd_refs(a)

def cmd_refs(a):
    idx = get_index(a.root)
    regex = getattr(a, "regex", False)
    name = a.name if regex else a.name.split(".")[-1].split(":")[-1]
    hits = find_refs(idx, name, regex=regex, paths=[_rel(idx, p_) for p_ in getattr(a, "paths", None) or []] or None)
    byf = defaultdict(list)
    for h in hits: byf[h[0]].append(h)
    order = sorted(byf, key=lambda r: (is_test(r), -idx.files[r].get("pr", 0)))
    out = ['[ndl %s] "%s": %d hits in %d files%s' % ("grep" if regex else "refs", name, len(hits), len(byf), " (textual; same-name symbols included)" if hits and not regex else "")]
    shown = 0
    for rel in order:
        rec = idx.files[rel]
        if shown >= a.limit:
            break
        out.append(rel)
        for _, ln, txt in byf[rel]:
            if shown >= a.limit: break
            e = enclosing(rec, ln)
            tag = ""
            if e is not None:
                if e[2] == ln and (regex or e[0] == name): tag = "[DEF %s] " % idx.qname(e) if regex else "[DEF] "
                else: tag = "[in %s] " % idx.qname(e)
            out.append("  %d %s%s" % (ln, tag, txt.strip()[:140]))
            shown += 1
    if shown < len(hits):
        printed = set(o for o in out if not o.startswith("  "))
        out.append("… %d more hits: " % (len(hits) - shown) + ", ".join("%s(%d)" % (r, len(byf[r])) for r in order if r not in printed)[:700])
    print("\n".join(out))

STR_PY = re.compile(r'(?s)("""|\'\'\')(?:.*?)\1|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|#[^\n]*')
STR_C = re.compile(r'(?s)/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\\n])*"|`(?:\\.|[^`\\])*`|\'(?:\\.|[^\'\\\n])*\'')
CALL = re.compile(r"(\.)?\b([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(|<[^<>()]*>\s*\()|\bnew\s+([A-Z][A-Za-z0-9_]*)|\b([A-Z][A-Za-z0-9_]{2,})\b")

def code_only(text, lang):
    rx = STR_PY if lang in ("py", "rb", "sh", "yaml", "toml") else STR_C
    return rx.sub(" ", text)

def callees(idx, rel, s, lines, maxn=8):
    """symbols actually called/instantiated by s: same file first, then names the file imports."""
    rec = idx.files[rel]
    body = code_only(sym_text(lines, s), rec["l"])
    head = "\n".join(lines[:min(len(lines), 300)])
    out = []; seen = set()
    qn = idx.qname(s)
    for m in CALL.finditer(body):
        attr = bool(m.group(1)); name = m.group(2) or m.group(3) or m.group(4)
        if not name or name in KW or name == s[0] or name in seen or name in CTRL: continue
        seen.add(name)
        locs = idx.defs.get(name) or []
        if not locs: continue
        same = [(r2, k) for r2, k in locs if r2 == rel]
        pick = None
        if same:
            # prefer a member of the same class for attribute calls, else top-level
            cand = [idx.files[rel]["s"][k] for _, k in same]
            cand = [c for c in cand if not (s[2] <= c[2] <= s[3])]
            if cand:
                if attr:
                    own = s[4] if s[1] in ("method", "inner") else qn
                    mine = [c for c in cand if c[4] and own and c[4] == own]
                    if mine: pick = (rel, mine[0])
                else:
                    pick = (rel, sorted(cand, key=lambda c: (c[4] is not None, c[2]))[0])
        if pick is None and not attr and len(locs) <= 6:
            imported = re.search(r"\b(import|from|require|use|include)\b[^\n]*\b%s\b" % re.escape(name), head)
            if imported or len(locs) == 1:
                r2, k = sorted(locs, key=lambda x: (is_test(x[0]), -idx.files[x[0]].get("pr", 0)))[0]
                pick = (r2, idx.files[r2]["s"][k])
        if pick and pick[1][1] not in ("var", "key", "step", "h1", "h2", "h3"):
            out.append(pick)
        if len(out) >= maxn: break
    return out

def cmd_pack(a):
    idx = get_index(a.root)
    q = " ".join(a.query)
    budget = a.budget
    res = [r for r in search(idx, q, k=40) if r[2] >= 0]
    if any(not idx.files[r]["s"][si][1].startswith("h") for _, r, si in res[:10]):  # prefer code over prose
        res = sorted(((sc * (0.6 if idx.files[r]["s"][si][1].startswith("h") else 1), r, si) for sc, r, si in res), key=lambda x: -x[0])
    out = ['[ndl pack] "%s" — budget ~%d tokens. Exact line-numbered source for the most relevant code, then related signatures.' % (q, budget)]
    used = tok(out[0]); included = []; spans = []
    cache = {}
    def L(rel):
        if rel not in cache: cache[rel] = read_lines(idx.root, rel)
        return cache[rel]
    def inside(rel, a_, b_):
        return any(r == rel and x <= a_ and b_ <= y for r, x, y in spans)
    if not res:
        print(out[0] + "\nno matches. try `ndl find` with other words."); return
    top = res[0][0]
    # order: best hit, then what it actually calls (graph neighbours), then other strong hits
    ranked = []; seenk = set()
    def push(k, sc):
        if k not in seenk: seenk.add(k); ranked.append((k, sc))
    for n_, (sc, rel, si) in enumerate(res):
        if n_ > 0 and sc < 0.5 * top: break
        push((rel, si), sc)
        if n_ < 2:
            s = idx.files[rel]["s"][si]
            for r2, s2 in callees(idx, rel, s, L(rel), maxn=6):
                push((r2, idx.files[r2]["s"].index(s2)), 0.5 * top)
    for sc, rel, si in res: push((rel, si), min(sc, 0.29 * top))
    deferred = []
    for (rel, si), sc in ranked:
        if len(included) >= a.max: deferred.append((rel, si)); continue
        s = idx.files[rel]["s"][si]
        if s[0] in ("__all__", "__version__", "__author__"): continue
        if inside(rel, s[2], s[3]): continue
        if sc < 0.3 * top: deferred.append((rel, si)); continue
        lines = L(rel)
        body = numbered(lines, s[2], s[3], doc_folds(lines, s[2], s[3]) if idx.files[rel]["l"] == "py" else None)
        cost = tok(body) + 20
        remaining = budget - used
        if cost <= remaining * (0.7 if not included else 0.9):
            txt = "── " + fmt_sym(rel, s) + "\n" + body
            nts = notes_for(idx, rel, idx.qname(s))
            if nts: txt += "\n" + "\n".join("📝 " + x for x in nts)
            out.append(txt); used += cost; included.append((rel, s)); spans.append((rel, s[2], s[3]))
        elif s[1] in ("class", "impl", "type", "module") and remaining > 400:
            qn = idx.qname(s)
            members = [x for x in idx.files[rel]["s"] if x[4] and (x[4] == qn or x[4].startswith(qn + "."))]
            sk = ["── " + fmt_sym(rel, s) + "  [too large for budget: skeleton — `ndl show %s:Name` for a member]" % rel]
            sk += ["  %d-%d %s %s" % (x[2], x[3], x[1], x[5]) for x in members[:40]]
            t = "\n".join(sk)
            if tok(t) < remaining * 0.5:
                out.append(t); used += tok(t); spans.append((rel, s[2], s[2]))
            else: deferred.append((rel, si))
        else:
            deferred.append((rel, si))
        if budget - used < 200: break
    rel_lines = []
    for rel, s in included[:3]:
        for r2, s2 in callees(idx, rel, s, L(rel), maxn=6):
            if not inside(r2, s2[2], s2[3]): rel_lines.append("  %s uses → %s" % (s[0], fmt_sym(r2, s2, False)))
        if len(s[0]) >= 4:
            hs = [h for h in find_refs(idx, s[0]) if not (h[0] == rel and s[2] <= h[1] <= s[3])]
            if len(hs) > 60: continue  # generic name: callers list would be noise
            callers = []
            for r2, ln, _ in hs:
                e = enclosing(idx.files[r2], ln)
                if e and e[0] == s[0]: continue
                nm = ("%s:%d [in %s]" % (r2, ln, idx.qname(e))) if e else "%s:%d" % (r2, ln)
                if nm not in callers: callers.append(nm)
                if len(callers) >= 5: break
            if callers: rel_lines.append("  %s used by (%d refs): %s" % (s[0], len(hs), "; ".join(callers)))
    if rel_lines:
        sect = ["related (signatures only — `ndl show` any of them):"]
        for ln in dict.fromkeys(rel_lines):
            if used + tok(ln) > budget + 300: break
            sect.append(ln); used += tok(ln)
        out.append("\n".join(sect))
    more = []
    for rel, si in deferred:
        s = idx.files[rel]["s"][si]
        if inside(rel, s[2], s[3]): continue
        more.append("%s:%d-%d %s" % (rel, s[2], s[3], idx.qname(s)))
        if len(more) >= 10: break
    if more: out.append("other candidates (not shown): " + " | ".join(more))
    print("\n\n".join(out))

def cmd_note(a):
    idx = get_index(a.root)
    notes = notes_load(idx.root)
    if a.action == "add":
        if not a.target or not a.text:
            print("usage: ndl note add <symbol|path[:Name]> \"text\""); return
        c = resolve(idx, a.target)
        if not c: print("✗ cannot resolve %r" % a.target); return
        rel, s, _, _ = c[0]
        lines = read_lines(idx.root, rel)
        nid = "n%d" % (max([int(n["id"][1:]) for n in notes if n["id"][1:].isdigit()] + [0]) + 1)
        n = {"id": nid, "file": rel, "sym": idx.qname(s) if s else None,
             "hash": _anchor_hash(lines, s) if s else idx.files[rel]["h"], "text": " ".join(a.text), "ts": int(time.time())}
        notes.append(n)
        atomic_write(_notes_path(idx.root), json.dumps(notes, indent=1))
        print("📝 %s anchored to %s%s (auto-flags STALE if that code changes)" % (nid, rel, (":" + n["sym"]) if n["sym"] else ""))
    elif a.action == "rm":
        keep = [n for n in notes if n["id"] != a.target]
        atomic_write(_notes_path(idx.root), json.dumps(keep, indent=1))
        print("removed" if len(keep) < len(notes) else "no such note")
    else:
        q = a.target
        cnt = Counter()
        for n in notes:
            if q and q.lower() not in (n["text"] + n["file"] + (n.get("sym") or "")).lower(): continue
            st, _ = note_status(idx, n); cnt[st] += 1
            mark = {"valid": "✓", "stale": "⚠ STALE", "orphaned": "✗ ORPHANED"}[st]
            print("[%s] %s %s%s — %s" % (n["id"], mark, n["file"], (":" + n["sym"]) if n.get("sym") else "", n["text"]))
        if not notes: print("no notes. add: ndl note add <symbol> \"insight\"")

LOG_KEEP = re.compile(r"(?i)(\berror\b|\berr!|exception|traceback|\bfail(ed|ure|ing)?\b|fatal|panic|assert|\bwarn(ing)?\b|"
                      r"denied|not found|no such|cannot|can't|unable|undefined|segfault|segmentation|abort|killed|"
                      r"timed? ?out|refused|\bE\s{2,}|^E\s|✗|✘|×|\bFAILED\b|\bERROR\b|expected|actual|mismatch|"
                      r"^\s*at\s+\S+\s*\(|^\s*File \"|line \d+|:\d+:\d+|exit (code|status)|errno|\bTS\d{4}\b|"
                      r"unresolved|conflict|rejected|missing|invalid|deprecat|summary|passed|tests? run|\d+ (passed|failed|errors?))")
NORM = re.compile(r"0x[0-9a-fA-F]+|[0-9a-f]{8,}|\d+(\.\d+)*")

def condense(text, budget_chars=6000, head=6, tail=24):
    lines = text.split("\n")
    n = len(lines)
    if len(text) <= budget_chars: return text, False
    keep = set(range(min(head, n))) | set(range(max(0, n - tail), n))
    tb = False
    for i, l in enumerate(lines):
        if "Traceback (most recent call last)" in l or re.match(r"^(panic:|thread '.*' panicked)", l):
            j = i
            while j < n and j - i < 60:
                keep.add(j); j += 1
                if j < n and lines[j] and not lines[j].startswith((" ", "\t")) and j > i + 1:
                    keep.add(j); break
        elif LOG_KEEP.search(l):
            for d in (-1, 0, 1):
                if 0 <= i + d < n: keep.add(i + d)
    # collapse repetitive runs
    out = []; i = 0; omitted = 0
    order = sorted(keep)
    prev = -1
    sig_prev = None; rep = 0; last = ""
    def flush():
        if rep == 1: out.append(last)
        elif rep > 1: out.append("    … ×%d similar lines (last: %s)" % (rep, last.strip()[:120]))
    for i in order:
        if i != prev + 1 and prev != -1:
            flush(); rep = 0
            gap = i - prev - 1
            out.append("[… %d lines omitted …]" % gap); omitted += gap
        l = lines[i][:400]
        sg = NORM.sub("#", l.strip())
        if sg == sig_prev and sg:
            rep += 1; last = l
        else:
            flush(); rep = 0
            out.append(l)
        sig_prev = sg; prev = i
    flush()
    res = "\n".join(out)
    if len(res) > budget_chars:
        # keep first part of important stuff + tail
        tl = "\n".join(lines[-tail:])
        hd = res[:max(0, budget_chars - len(tl) - 200)]
        res = hd.rsplit("\n", 1)[0] + "\n[… more matching lines truncated — see full log …]\n" + tl
    return res, True

def save_log(root, text, cmd=""):
    lid = sha(text + str(time.time()))[:8]
    p = os.path.join(sdir(root, "logs"), lid + ".log")
    with open(p, "w", encoding="utf-8", errors="replace") as f:
        if cmd: f.write("$ " + cmd + "\n")
        f.write(text)
    # keep last 200 logs
    try:
        d = sdir(root, "logs"); fs = sorted(os.listdir(d), key=lambda x: os.path.getmtime(os.path.join(d, x)))
        for x in fs[:-200]: os.remove(os.path.join(d, x))
    except OSError: pass
    return lid

def cmd_run(a):
    root = find_root(a.root)
    cmd = " ".join(a.command)
    t = time.time()
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.stderr else "")
    res, did = condense(text, a.budget * 4)
    if did:
        lid = save_log(root, text, cmd)
        stat_log(root, "run-condense", tok(text) - tok(res))
        print("[ndl run] exit=%d, %.1fs, %d lines → condensed %s→%s tokens. full: ndl log %s [--grep RX | --lines A-B]" %
              (p.returncode, time.time() - t, text.count("\n") + 1, "{:,}".format(tok(text)), "{:,}".format(tok(res)), lid))
    else:
        print("[ndl run] exit=%d, %.1fs" % (p.returncode, time.time() - t))
    print(res)
    sys.exit(p.returncode)

def cmd_log(a):
    root = find_root(a.root)
    d = sdir(root, "logs")
    if not a.id:
        for x in sorted(os.listdir(d), key=lambda x: -os.path.getmtime(os.path.join(d, x)))[:15]:
            with open(os.path.join(d, x), encoding="utf-8", errors="replace") as f: first = f.readline().strip()
            print(x[:-4], first[:100])
        return
    p = os.path.join(d, a.id + ".log")
    if not os.path.exists(p): print("no log %s" % a.id); return
    with open(p, encoding="utf-8", errors="replace") as f: lines = f.read().split("\n")
    if a.grep:
        rx = re.compile(a.grep, re.I)
        hits = [i for i, l in enumerate(lines) if rx.search(l)]
        keep = sorted({j for i in hits for j in range(max(0, i - a.ctx), min(len(lines), i + a.ctx + 1))})
        prev = -2
        for j in keep[:400]:
            if j != prev + 1: print("--")
            print("%d\t%s" % (j + 1, lines[j][:500])); prev = j
        print("[%d matching lines]" % len(hits))
    elif a.lines:
        x, _, y = a.lines.partition("-"); x = int(x); y = int(y or x)
        for j in range(x, min(y, len(lines)) + 1): print("%d\t%s" % (j, lines[j - 1]))
    else:
        print("%d lines. use --grep RX or --lines A-B (printing whole logs defeats the purpose)." % len(lines))

def cmd_stats(a):
    root = find_root(a.root)
    p = os.path.join(sdir(root), "stats.jsonl")
    agg = defaultdict(lambda: [0, 0])
    try:
        with open(p, encoding="utf-8") as f:
            for ln in f:
                try: r = json.loads(ln)
                except ValueError: continue
                agg[r["ev"]][0] += 1; agg[r["ev"]][1] += r.get("saved", 0)
    except OSError:
        pass
    if not agg: print("no events yet."); return
    tot = sum(v[1] for v in agg.values())
    print("[ndl stats] estimated tokens kept out of context (gross, ±15%%): %s" % "{:,}".format(tot))
    for k, (c, s) in sorted(agg.items(), key=lambda kv: -kv[1][1]):
        print("  %-16s %5d events  %10s tokens" % (k, c, "{:,}".format(s)))
    print("note: read-guard figures are gross — Claude then reads the spans it needs.")

def cmd_doctor(a):
    root = find_root(a.root)
    print("needlepoint %s  python %s  root=%s" % (VERSION, sys.version.split()[0], root))
    print("ripgrep: %s   git: %s" % (shutil.which("rg") or "missing (refs uses python fallback)", shutil.which("git") or "missing"))
    print("NDL=%s  NDL_SESSION=%s" % (os.environ.get("NDL", "-"), os.environ.get("NDL_SESSION", "-")))
    for ip in (os.path.join(state_base(root), "index.db"), os.path.join(state_base(root), "index.json.gz")):
        if os.path.exists(ip): print("index: %s (%.1f MB)" % (ip, os.path.getsize(ip) / 1e6)); break
    else: print("index: not built yet (any command builds it)")
    print("backend: %s" % ("sqlite %s" % sqlite3.sqlite_version if sqlite3 else "json (python built without sqlite3)"))
    print("read-guard threshold: %s tokens; bash condense threshold: %s chars" % (_env_int("NDL_READ_GUARD_TOKENS", 6000), _env_int("NDL_BASH_CONDENSE_CHARS", 12000)))

def _env_int(k, d):
    try: return int(os.environ.get(k, d))
    except ValueError: return d

# ----------------------------------------------------------------------------- benchmark
def cmd_bench(a):
    import random
    idx = get_index(a.root, quiet=False)
    rnd = random.Random(a.seed)
    pool = [(rel, s) for rel, r in idx.files.items() if not is_test(rel)
            for s in r["s"] if s[1] in ("function", "method", "class") and s[3] - s[2] >= 3]
    rnd.shuffle(pool)
    pool = pool[:a.n]
    # A. known-symbol lookup: naive = grep for definition + Read whole file; ndl = show
    naive = []; ndl = []; okA = 0
    for rel, s in pool:
        r = idx.files[rel]
        naive.append(min(r["t"], int(2000 * r["t"] / max(1, r["n"]))) + 40)
        c = resolve(idx, "%s:%s" % (rel, idx.qname(s)))
        shown = c if len(c) <= 3 else c[:1]          # `show` prints up to 3 same-name definitions (overloads, getter/setter)
        if any(x[2] == s[2] for x in shown):
            okA += 1
        lines = read_lines(idx.root, rel)
        ndl.append(tok("── " + fmt_sym(rel, s) + "\n" + numbered(lines, s[2], s[3])))
    # B. description lookup: query = docstring with symbol-name words removed
    qs = [(rel, s) for rel, r in idx.files.items() if not is_test(rel) for s in r["s"] if s[6] and len(s[6].split()) >= 4 and s[1] in ("function", "method", "class")]
    rnd.shuffle(qs); qs = qs[:a.n]
    r1 = r5 = 0
    tB = time.time()
    for rel, s in qs:
        nm = set(terms(s[0]))
        q = " ".join(w for w in s[6].split() if not (set(terms(w)) & nm))
        res = search(idx, q or s[6], k=5)
        ranks = [i for i, (_, r_, si) in enumerate(res) if r_ == rel and si >= 0 and idx.files[r_]["s"][si][2] == s[2]]
        if ranks and ranks[0] == 0: r1 += 1
        if ranks: r5 += 1
    tB = (time.time() - tB) / max(1, len(qs))
    # C. outline vs full read for large files
    big = [(rel, r) for rel, r in idx.files.items() if r["t"] > 6000 and r["l"] not in ("data", "txt") and len(r["s"]) >= 2]
    rnd.shuffle(big); big = big[:min(50, len(big))]
    otoks = [tok(outline_text(idx, rel)) for rel, _ in big]
    ftoks = [min(r["t"], int(2000 * r["t"] / max(1, r["n"]))) for _, r in big]
    med = lambda v: sorted(v)[len(v) // 2] if v else 0
    print("\n[ndl bench] %s — %d files" % (idx.root, len(idx.files)))
    print("A. read a known symbol (n=%d): naive grep+Read median %s tok  vs  ndl show median %s tok  → %.0f%% fewer; total %s vs %s (%.1fx); resolve accuracy %d/%d" % (
        len(pool), "{:,}".format(med(naive)), "{:,}".format(med(ndl)), 100 * (1 - sum(ndl) / max(1, sum(naive))),
        "{:,}".format(sum(naive)), "{:,}".format(sum(ndl)), sum(naive) / max(1, sum(ndl)), okA, len(pool)))
    print("B. find by description, name words removed (n=%d): recall@1 %.0f%%  recall@5 %.0f%%  avg %.0f ms/query" % (
        len(qs), 100 * r1 / max(1, len(qs)), 100 * r5 / max(1, len(qs)), tB * 1000))
    if big:
        print("C. large files >6k tok (n=%d): outline median %s tok vs Read median %s tok → %.0f%% fewer; outline covers 100%% of lines with ranges" % (
            len(big), "{:,}".format(med(otoks)), "{:,}".format(med(ftoks)), 100 * (1 - sum(otoks) / max(1, sum(ftoks)))))

# ----------------------------------------------------------------------------- hooks
def _hook_in():
    try: return json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
    except Exception: return {}

def _emit(obj):
    sys.stdout.write(json.dumps(obj)); sys.stdout.flush()

def _shell_path(p):
    """path usable inside a POSIX-shell PATH entry; on Windows Git Bash wants /c/Users/... (':' is the PATH separator)."""
    p = os.path.abspath(p)
    if os.name == "nt":
        drive, rest = os.path.splitdrive(p)
        p = ("/" + drive.rstrip(":").lower() if drive else "") + rest.replace("\\", "/")
    return p.replace('"', '\\"')

def _key_sid(h):
    return (h.get("session_id") or "nosession") + ("." + h["agent_id"] if h.get("agent_id") else "")

def hook_session_start(h):
    root = find_root(h.get("cwd"))
    sid = h.get("session_id") or "nosession"
    me = os.path.abspath(__file__)
    try:
        shutil.rmtree(os.path.join(state_base(root), "session", re.sub(r"[^\w.-]", "_", sid)[:80]), ignore_errors=True)
        base = os.path.join(state_base(root), "session")
        if os.path.isdir(base):
            for d in os.listdir(base):
                p = os.path.join(base, d)
                if time.time() - os.path.getmtime(p) > 3 * 86400: shutil.rmtree(p, ignore_errors=True)
    except OSError: pass
    envf = os.environ.get("CLAUDE_ENV_FILE")
    if envf:
        try:
            with open(envf, "a", encoding="utf-8") as f:
                f.write('export PATH="%s:$PATH"\nexport NDL=ndl\nexport NDL_SESSION=%s\n'
                        % (_shell_path(os.path.dirname(me)), re.sub(r"[^\w.-]", "_", sid)))
        except OSError: pass
    is_project = (os.path.isdir(os.path.join(root, ".git")) or any(os.path.exists(os.path.join(state_base(root), x)) for x in ("index.db", "index.json.gz"))) \
        and os.path.abspath(root) not in (os.path.expanduser("~"), os.path.abspath(os.sep))
    try:  # warm the index in the background (only for real projects, never $HOME or /)
        if not is_project or os.environ.get("NDL_NO_WARM"): raise OSError
        subprocess.Popen([sys.executable or "python3", "-I", me, "index", "--root", root], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True, cwd=root)
    except OSError: pass
    cli = "ndl" if envf else "sh '%s'" % _shell_path(os.path.join(os.path.dirname(me), "ndl"))
    _emit({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext":
           "needlepoint active (precision context). For code exploration prefer `%s find \"<what>\"`, `%s pack \"<task>\"`, "
           "`%s show <Symbol|file:Name|file:A-B>`, `%s outline <file>`, `%s refs <name>` over grep + whole-file Reads. "
           "Big-file Reads return a full-coverage outline first; unchanged re-Reads are deduped (repeat the identical call to force)." % ((cli,) * 5)}})

def hook_pre_read(h):
    ti = h.get("tool_input") or {}
    fp = ti.get("file_path")
    if not fp or not os.path.isfile(fp): return
    root = find_root(h.get("cwd"))
    if os.path.realpath(fp).startswith(os.path.realpath(state_base(root))): return
    lang = lang_of(fp)
    if lang is None: return
    try: size = os.path.getsize(fp)
    except OSError: return
    if size > 4 * MAX_BYTES: return
    sid = _key_sid(h)
    offset = ti.get("offset"); limit = ti.get("limit")
    fp = os.path.realpath(fp)
    key = "read|%s|%s|%s" % (fp, offset, limit)
    # one-shot bypass: an identical repeat right after a denial always goes through
    byp = ledger_get(root, sid, "bypass|" + key)
    if byp and byp.get("armed"):
        ledger_put(root, sid, "bypass|" + key, {"armed": False}); return
    with open(fp, "rb") as f: raw = f.read()
    if b"\x00" in raw[:8192]: return
    lines = split_lines(decode(raw))
    a_ = int(offset or 1); b_ = a_ + int(limit or 2000) - 1
    b_ = min(b_, len(lines))
    span_tok = tok("\n".join(lines[a_ - 1:b_]))
    fkey = "file|" + fp
    prev = ledger_get(root, sid, fkey)
    cur_h = sha(raw)
    def deny(reason, saved, ev):
        ledger_put(root, sid, "bypass|" + key, {"armed": True})
        stat_log(root, ev, saved)
        _emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                      "permissionDecisionReason": reason}})
    if prev and span_tok > 150:
        covered = any(r[0] <= a_ and b_ <= r[1] for r in prev["reads"])
        if covered and prev["h"] == cur_h:
            return deny("[needlepoint] %s lines %d-%d are unchanged since you Read them earlier in this session — "
                        "they're already in your context (saved ~%s tokens). If they are NOT in your context "
                        "(subagent, or after compaction), repeat this exact Read and it will go through."
                        % (os.path.basename(fp), a_, b_, "{:,}".format(span_tok)), span_tok, "read-dedupe")
        old = prev.get("snap")
        whole = old is not None and any(r[0] <= 1 and r[1] >= len(old) for r in prev["reads"])
        if whole and prev["h"] != cur_h and offset is None and limit is None:
            d = unified(old, lines, ctx=2)
            if d and tok(d) < 0.4 * span_tok:
                prev["h"] = cur_h; prev["snap"] = lines if len(raw) < 600_000 else None
                prev["reads"] = [[1, len(lines)]]  # old version + diff == the whole new version
                ledger_put(root, sid, fkey, prev)
                return deny("[needlepoint] %s changed since your earlier Read; everything else is identical. Diff "
                            "(hunk headers carry new line numbers):\n%s\nIf you need the full text anyway (e.g. Edit says "
                            "the file was modified), repeat this exact Read." % (os.path.basename(fp), d),
                            span_tok - tok(d), "read-delta")
    thr = _env_int("NDL_READ_GUARD_TOKENS", 6000)
    if offset is None and limit is None and thr > 0 and span_tok > thr and lang not in ("data", "txt"):
        idx = Index(root, {"files": {}})
        rel = os.path.relpath(fp, root).replace(os.sep, "/")
        rec = parse_file(fp, rel)
        if rec and len(rec["s"]) >= 2:
            idx.files[rel] = rec
            limit = (0.6 if len(lines) > b_ else 0.5) * span_tok   # outline must be clearly cheaper than this Read
            ol = outline_text(idx, rel, lines, max_lines=_env_int("NDL_OUTLINE_MAX", 220))
            if tok(ol) >= limit:
                ol = outline_text(idx, rel, lines, max_lines=_env_int("NDL_OUTLINE_MAX", 220), docs=False)
            if tok(ol) < limit:
                size = ("~%s tokens" % "{:,}".format(span_tok)) if len(lines) <= b_ else \
                    ("%d lines, ~%s tokens; a plain Read returns only lines %d-%d" % (len(lines), "{:,}".format(tok("\n".join(lines))), a_, b_))
                return deny("[needlepoint] %s is large (%s). Here is its complete outline — every line belongs to "
                            "a listed range. Read only the ranges you need with offset/limit (e.g. offset=120, limit=60), "
                            "or repeat this exact Read to load it anyway.\n%s"
                            % (os.path.basename(fp), size, ol), span_tok - tok(ol), "read-guard")

def hook_post_read(h):
    ti = h.get("tool_input") or {}
    fp = ti.get("file_path")
    if not fp or not os.path.isfile(fp) or lang_of(fp) is None: return
    root = find_root(h.get("cwd"))
    sid = _key_sid(h)
    try:
        with open(fp, "rb") as f: raw = f.read()
    except OSError: return
    if b"\x00" in raw[:8192] or len(raw) > 4 * MAX_BYTES: return
    lines = split_lines(decode(raw))
    a_ = int(ti.get("offset") or 1); b_ = min(len(lines), a_ + int(ti.get("limit") or 2000) - 1)
    fkey = "file|" + os.path.realpath(fp)
    prev = ledger_get(root, sid, fkey)
    cur_h = sha(raw)
    if not prev or prev["h"] != cur_h:
        prev = {"h": cur_h, "reads": [], "snap": None}
    prev["reads"].append([a_, b_])
    prev["snap"] = lines if len(raw) < 600_000 else None
    ledger_put(root, sid, fkey, prev)

NO_CONDENSE = re.compile(r"^\s*(cat|head|tail|sed|awk|less|more|bat|nl|jq|yq|xxd|od|diff|git\s+(diff|show|log\s+-p|blame)|"
                         r"python3?\s+\S*ndl\.py|\$NDL|.*\bndl\b)")

def hook_post_bash(h):
    thr = _env_int("NDL_BASH_CONDENSE_CHARS", 12000)
    if thr <= 0: return
    ti = h.get("tool_input") or {}
    cmd = ti.get("command") or ""
    if ti.get("run_in_background") or NO_CONDENSE.match(cmd): return
    tr = h.get("tool_response")
    if not isinstance(tr, dict): return
    out = tr.get("stdout") or ""; err = tr.get("stderr") or ""
    if len(out) + len(err) <= thr: return
    root = find_root(h.get("cwd"))
    full = out + (("\n[stderr]\n" + err) if err else "")
    lid = save_log(root, full, cmd)
    co, _ = condense(out, int(thr * 0.45)) if len(out) > thr * 0.45 else (out, False)
    ce, _ = condense(err, int(thr * 0.2)) if len(err) > thr * 0.2 else (err, False)
    hdr = ("[needlepoint] output condensed %s→%s tokens (errors/failures/tracebacks/summary kept, repeats collapsed). "
           "Nothing is lost: `$NDL log %s --grep 'RX'` or `--lines A-B` reads the full log.\n" %
           ("{:,}".format(tok(full)), "{:,}".format(tok(co) + tok(ce)), lid))
    new = dict(tr); new["stdout"] = hdr + co; new["stderr"] = ce
    stat_log(root, "bash-condense", tok(full) - tok(co) - tok(ce))
    _emit({"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": new}})

def cmd_hook(a):
    h = _hook_in()
    try:
        {"session-start": hook_session_start, "pre-read": hook_pre_read, "post-read": hook_post_read,
         "post-bash": hook_post_bash}[a.event](h)
    except Exception as e:  # never break the user's session
        if os.environ.get("NDL_DEBUG"): raise
        try: stat_log(find_root(h.get("cwd")), "hook-error", 0, {"err": repr(e)[:200], "hook": a.event})
        except Exception: pass
    sys.exit(0)

# ----------------------------------------------------------------------------- main
def _utf8_stdio():
    for st in (sys.stdout, sys.stderr):
        try: st.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass

def main(argv=None):
    _utf8_stdio()
    p = argparse.ArgumentParser(prog="ndl", description="needlepoint — lossless precision context for coding agents")
    p.add_argument("--root", help="project root (default: nearest .git upward)")
    sp = p.add_subparsers(dest="cmd")
    x = sp.add_parser("index", help="build/refresh the index (automatic in every command)"); x.add_argument("--force", action="store_true"); x.add_argument("--root", default=argparse.SUPPRESS)
    x = sp.add_parser("map", help="token-budgeted repo overview"); x.add_argument("--budget", type=int, default=1200); x.add_argument("--focus"); x.add_argument("--root", default=argparse.SUPPRESS)
    x = sp.add_parser("find", help="ranked symbol search by words/description"); x.add_argument("query", nargs="+"); x.add_argument("-k", type=int, default=8)
    x.add_argument("--kind"); x.add_argument("--in", dest="inside", help="path glob(s), comma-separated"); x.add_argument("--root", default=argparse.SUPPRESS)
    x = sp.add_parser("show", help="exact source of symbols / ranges"); x.add_argument("targets", nargs="+"); x.add_argument("-f", "--force", action="store_true")
    x.add_argument("--all", action="store_true"); x.add_argument("--ctx", type=int, default=0); x.add_argument("--sig", action="store_true")
    x.add_argument("--full", action="store_true", help="don't fold long docstrings")
    x.add_argument("--grep", help="only lines matching this regex (case-insensitive) inside each target, with context")
    x.add_argument("-C", type=int, default=3, help="context lines for --grep"); x.add_argument("--root", default=argparse.SUPPRESS)
    x = sp.add_parser("outline", help="full-coverage skeleton of files/dirs"); x.add_argument("paths", nargs="+"); x.add_argument("--all", action="store_true"); x.add_argument("--root", default=argparse.SUPPRESS)
    x = sp.add_parser("refs", help="usages grouped by enclosing symbol"); x.add_argument("name"); x.add_argument("--limit", type=int, default=40); x.add_argument("--root", default=argparse.SUPPRESS)
    x = sp.add_parser("grep", help="regex search; each hit tagged with its enclosing symbol"); x.add_argument("pattern"); x.add_argument("paths", nargs="*")
    x.add_argument("--limit", type=int, default=40); x.add_argument("--root", default=argparse.SUPPRESS)
    x = sp.add_parser("pack", help="one-shot context pack for a task, within a token budget"); x.add_argument("query", nargs="+")
    x.add_argument("--budget", type=int, default=5000); x.add_argument("--max", type=int, default=8); x.add_argument("--root", default=argparse.SUPPRESS)
    x = sp.add_parser("note", help="hash-anchored notes: add|ls|rm"); x.add_argument("action", choices=["add", "ls", "rm"]); x.add_argument("target", nargs="?"); x.add_argument("text", nargs="*"); x.add_argument("--root", default=argparse.SUPPRESS)
    x = sp.add_parser("run", help="run a command; condensed output, full log kept"); x.add_argument("command", nargs=argparse.REMAINDER); x.add_argument("--budget", type=int, default=1500); x.add_argument("--root", default=argparse.SUPPRESS)
    x = sp.add_parser("log", help="query a saved full log"); x.add_argument("id", nargs="?"); x.add_argument("--grep"); x.add_argument("--lines"); x.add_argument("--ctx", type=int, default=2); x.add_argument("--root", default=argparse.SUPPRESS)
    x = sp.add_parser("stats", help="estimated tokens saved"); x.add_argument("--root", default=argparse.SUPPRESS)
    x = sp.add_parser("bench", help="measure savings + retrieval accuracy on this repo"); x.add_argument("-n", type=int, default=200); x.add_argument("--seed", type=int, default=7); x.add_argument("--root", default=argparse.SUPPRESS)
    x = sp.add_parser("doctor", help="environment check"); x.add_argument("--root", default=argparse.SUPPRESS)
    x = sp.add_parser("hook", help="(internal) Claude Code hook entrypoints"); x.add_argument("event", choices=["session-start", "pre-read", "post-read", "post-bash"])
    a = p.parse_args(argv)
    if not a.cmd: p.print_help(); return
    if a.cmd == "run" and a.command and a.command[0] == "--": a.command = a.command[1:]
    fn = {"index": cmd_index, "map": cmd_map, "find": cmd_find, "show": cmd_show, "outline": cmd_outline, "refs": cmd_refs,
          "pack": cmd_pack, "grep": cmd_grep, "note": cmd_note, "run": cmd_run, "log": cmd_log, "stats": cmd_stats, "bench": cmd_bench,
          "doctor": cmd_doctor, "hook": cmd_hook}
    fn[a.cmd](a)

if __name__ == "__main__":
    try:
        main()
        sys.stdout.flush()
    except BrokenPipeError:   # output piped into head/less that closed early
        try: os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError: pass
        sys.exit(1)
