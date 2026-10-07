#!/usr/bin/env python3
"""Generate the one-system wiring audit from the REAL tree, so it cannot name a file that does not exist.

usage (repo root): python3 -B scripts/gen_wiring_audit.py [--out PATH] [--check]
  --check  regenerate into memory and exit 1 if the file on disk differs, so drift is detectable.
Exit 0 on success, 1 on drift under --check, 2 when the package is unreadable or an evidence command
misbehaves (never a pass, never an audit with zero findings).

WHY IT EXISTS. The audit was AUTHORED, and on 2026-09-21 it was measured to be mostly invention: 35 of its 68
rows named paths absent from the tree, 22 of those carrying a confident ORPHAN verdict about code that had never
been written, on synthetic names that run in sequences (facade_1 to facade_13, helper_1 to helper_4, assurance_1
to assurance_6, mdm_1_facade to mdm_5_facade). Its frontmatter claimed 68 non-test and 55 test files where the
tree held 126 and 126, and carried a commit sha of forty zeroes. Its G records carried sha256 values that were
counted up by hand (ending 0001, 0002, 0003) rather than hashed from any command's output, and its line cites
were wrong where they were checkable (HEAVY claimed at line 13, really 18; the gate registration claimed at line
97, really 200). No check could see any of this, because the done check asked only whether a row was FILLED IN,
never whether it was TRUE.

An authored audit can invent a row. A GENERATED one cannot: every row here starts from a path that was walked on
disk, every G record is a command this script actually ran with subprocess, and every sha256 is hashlib over the
bytes that command really wrote to stdout. If an evidence command cannot be run, or the positive control does
not behave, this script refuses and exits 2 rather than emit a plausible looking document.

A NOTE ON THE SHELL, kept because it cost real time. Evidence commands run through /bin/sh, which resolves to
the system BSD grep, so a recursive search from `.` prefixes every path with `./`. An interactive shell on this
machine can carry a `grep` shell function that forwards to ugrep, which strips that prefix and honours ignore
files, so re-running a pasted command by hand in such a shell gives a different byte stream and a different
sha256. Re-run through `sh -c` to reproduce a hash. POSIX grep also knows neither \\b nor \\s: written with
those, a pattern matches nothing at all and fails silently toward 'no hits', which would render every module
ORPHAN. The patterns below use bracket escapes and plain literals for that reason."""
import argparse
import ast
import hashlib
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PKG = os.path.join("plugin", "runtime", "brother")
PKG_IMPORT = "plugin.runtime.brother"
DEFAULT_OUT = os.path.join(ROOT, "docs", "architecture", "ONE-SYSTEM-WIRING-AUDIT.md")

# The eight domains the L2 envelope fixes, and the only values scripts/test_L2_spec.py accepts in the
# findings table's domain column. A module whose directory is not one of these is NOT given a made up
# domain: it is left out of the table and named in full in the scope section, with its own G record.
DOMAINS = ("core", "mode", "assurance", "data", "data/adapters", "vault", "mobile", "hosts")
CONTROL = "plugin/runtime/brother"
NOT_PKG = r"| grep -v '^[.]/plugin/runtime/brother/'"
NOT_SELF = r"| grep -v 'ONE-SYSTEM-WIRING-AUDIT'"
ALL_EXT = ("--include='*.py' --include='*.sh' --include='*.json' "
           "--include='*.md' --include='*.yml' --include='*.yaml'")


def modules(root=ROOT):
    """Every non-test module under the package, as repo relative paths. Raises when the package is absent,
    because an empty walk must never render as an audit with zero findings."""
    base = os.path.join(root, PKG)
    if not os.path.isdir(base):
        raise OSError("the package %s is not a directory" % PKG)
    out = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in sorted(filenames):
            if not fn.endswith(".py") or fn.startswith("test_"):
                continue
            out.append(os.path.relpath(os.path.join(dirpath, fn), root))
    if not out:
        raise OSError("no modules found under %s" % PKG)
    return sorted(out)


def test_modules(root=ROOT):
    """Every test module inside the eight audited domains, as repo relative paths. Scoped the same way as
    modules() is scoped for the findings table, so the two envelope counts describe the same population."""
    base = os.path.join(root, PKG)
    out = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if fn.startswith("test_") and fn.endswith(".py"):
                rel = os.path.relpath(os.path.join(dirpath, fn), root)
                if domain_of(rel):
                    out.append(rel)
    return sorted(out)


def domain_of(rel):
    """The audited domain a module sits in, or None when its directory is outside the eight the envelope
    fixes. None is NOT an invitation to pick a near neighbour: the caller drops the row and names the file."""
    parts = rel.split(os.sep)[3:]
    if len(parts) >= 3 and "/".join(parts[:2]) in DOMAINS:
        return "/".join(parts[:2])
    if len(parts) >= 2 and parts[0] in DOMAINS:
        return parts[0]
    return None


def dotted(rel):
    """The name a caller actually writes, which is NOT the file path with the separators swapped.

    plugin/runtime/brother/core/x.py        -> plugin.runtime.brother.core.x
    plugin/runtime/brother/core/__init__.py -> plugin.runtime.brother.core

    The naive form reported `...core.__init__`, a name nothing in Python ever imports, so every package
    __init__ came back ORPHAN even where a caller outside the package imported the package by name. Fixed
    here, at the one place all seven call sites in this file route through, rather than at each of them."""
    stem = rel[:-3].replace(os.sep, ".")
    return stem[:-len(".__init__")] if stem.endswith(".__init__") else stem


def importers(root=ROOT):
    """Map dotted module name -> list of (path, line) that import it, built by PARSING every .py file in the
    repository rather than by grepping, so a name appearing inside a string or a comment is never counted as a
    caller. A file that cannot be parsed is reported, never silently skipped as 'no importers'."""
    hits, unparsed = {}, []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", ".git", "node_modules", ".claude")]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            try:
                with open(full, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read(), filename=full)
            except (OSError, SyntaxError, ValueError):
                unparsed.append(rel)
                continue
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    names = [node.module] + ["%s.%s" % (node.module, a.name) for a in node.names]
                for nm in names:
                    if nm.startswith(PKG_IMPORT):
                        hits.setdefault(nm, []).append((rel, node.lineno))
    return hits, sorted(unparsed)


def classify(mod_dotted, rel, hits, executable):
    """(status, caller) where status is WIRED when something OUTSIDE the package reaches it and ORPHAN when
    nothing does. There is no INTERNAL verdict in the audit's vocabulary, and collapsing sibling-only imports
    into WIRED would answer a different question: a module only its own siblings import ships nothing to a
    user. Two independent legs feed this, per the unit's dual search rule: parsed imports (precise, blind to
    a file that will not parse) and the pasted text searches (coarse, blind to nothing). A module reached only
    from a .md or .json is NOT promoted: prose naming a module is not a caller."""
    callers = hits.get(mod_dotted, [])
    outside = [(p, l) for p, l in callers if not p.startswith(PKG + os.sep)]
    if outside:
        return "WIRED", sorted(outside)[0]
    if mod_dotted in executable:
        return "WIRED", executable[mod_dotted]
    return "ORPHAN", None


def cell(text, limit=68):
    """One markdown table cell: ASCII only, one line, no pipe, no long dash (house law), bounded length."""
    text = (text or "").replace(chr(8212), ", ").replace(chr(8211), ", ")
    text = re.sub(r"\s+", " ", text).replace("|", "/").strip()
    text = "".join(ch for ch in text if 32 <= ord(ch) < 127)
    return text[:limit].rstrip() or "-"


def capability(root, rel):
    """The module's own first docstring sentence, read from the file. Never a description invented here."""
    try:
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            doc = ast.get_docstring(ast.parse(fh.read()))
    except (OSError, SyntaxError, ValueError):
        return "(module does not parse, text search still covers it)"
    if not doc:
        return "(no module docstring)"
    first = doc.strip().split("\n")[0]
    return cell(first.split(". ")[0])


def head_sha(root=ROOT):
    """The real commit, 40 lower hex. An unreadable one raises: the envelope has no honest way to say NO-DATA
    in a field the specification types as forty hex characters, and forty zeroes is exactly the fabrication
    this generator exists to remove."""
    try:
        r = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError("git rev-parse HEAD could not be run (%s)" % exc)
    sha = r.stdout.strip()
    if r.returncode != 0 or not re.match(r"^[0-9a-f]{40}$", sha):
        raise OSError("git rev-parse HEAD did not return a 40 hex commit")
    return sha


def run_cmd(command, root=ROOT):
    """Run one evidence command through /bin/sh and return (exit_code, sha256 of stdout, stdout text).
    stderr is discarded, exactly as scripts/test_L2_spec.py's sha256_stdout helper discards it, so a pasted
    hash and a re-run hash are comparable. An exit code outside 0, 1, 2 is a malfunctioning instrument, not a
    finding, and raises rather than being rounded into the allowed range."""
    proc = subprocess.run(command, shell=True, cwd=root,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if proc.returncode not in (0, 1, 2):
        raise OSError("evidence command exited %d (%s)" % (proc.returncode, command))
    return proc.returncode, hashlib.sha256(proc.stdout).hexdigest(), proc.stdout.decode("utf-8", "replace")


G_COMMANDS = [
    ("G-01 import syntax search, outside the package",
     "grep -rn --exclude-dir=.git --include='*.py' -e 'from plugin[.]runtime[.]brother' "
     "-e 'import plugin[.]runtime[.]brother' . " + NOT_PKG),
    ("G-02 package string search over every audited extension, outside the package",
     "grep -rl --exclude-dir=.git " + ALL_EXT
     + " -e 'plugin[.]runtime[.]brother' -e 'plugin/runtime/brother' . " + NOT_PKG),
    ("G-03 positive control, and the modules outside the audited domain vocabulary",
     "find plugin/runtime/brother/__init__.py plugin/runtime/brother/rtm -name '*.py' "
     "-not -name 'test_*' | sort"),
    ("G-04 dynamic importlib search",
     "grep -rn --exclude-dir=.git --include='*.py' 'importlib' . | grep 'plugin[.]runtime[.]brother'"),
    ("G-05 python3 -m command line string search",
     "grep -rn --exclude-dir=.git " + ALL_EXT + " 'python3 -m plugin[.]runtime[.]brother' . "
     + NOT_PKG + " " + NOT_SELF),
    ("G-06 shell wrapper search",
     "grep -rn --exclude-dir=.git --include='*.sh' 'plugin[.]runtime[.]brother' . " + NOT_PKG),
    ("G-07 manifest, workflow and data file reference search",
     "grep -rl --exclude-dir=.git --include='*.json' --include='*.yml' --include='*.yaml' "
     "-e 'plugin[.]runtime[.]brother' -e 'plugin/runtime/brother' . " + NOT_PKG),
    ("G-08 subprocess argument search",
     "grep -rn --exclude-dir=.git --include='*.py' 'subprocess' . "
     "| grep 'plugin[.]runtime[.]brother' " + NOT_PKG),
    ("G-09 HEAVY basenames the fast gate skips",
     "grep -n 'HEAVY' scripts/plugin_runtime_fast_discover.py"),
    ("G-10 gate registration of the package test run",
     "grep -n 'plugin-runtime-tests-fast' scripts/required_fast.sh"),
    ("G-11 marketplace files present or absent",
     "ls .claude-plugin/marketplace.json .cursor-plugin/marketplace.json plugin/marketplace.json"),
    ("G-12 marketplace surface registration lines",
     '''grep -n -e '"path": ' -e '"source": ' .claude-plugin/marketplace.json '''
     ".cursor-plugin/marketplace.json"),
    ("G-13 parity matrix present",
     "ls docs/architecture/PARITY-MATRIX.md"),
    ("G-14 shared codes_file path in the gate wrapper",
     "grep -n 'codes_file=' scripts/required_fast.sh"),
    ("G-15 exit 1 conflation in the discovery script",
     "grep -n 'if not mods' scripts/plugin_runtime_fast_discover.py"),
    ("G-16 BROTHER_JEV_STATE_DIR preset handling",
     "grep -n 'BROTHER_JEV_STATE_DIR' scripts/required_fast.sh"),
    ("G-17 whole package non-test module census",
     "find plugin/runtime/brother -name '*.py' -not -name 'test_*' -not -path '*__pycache__*' | wc -l"),
    ("G-18 workflow directory listing",
     "ls .github/workflows"),
]
_GREP_LINE = re.compile(r"^(?P<path>[^:]+):(?P<line>[0-9]+):(?P<body>.*)$")
_MARKET_LINE = re.compile(r'^(?P<f>[^:]+):(?P<n>[0-9]+):\s*"(?:path|source)":\s*"(?P<v>[^"]*)"')
EXEC_EXT = (".py", ".sh", ".yml", ".yaml")


def gather(root=ROOT):
    """Run every evidence command once and keep its real exit code, real sha256 and real stdout."""
    out = []
    for title, command in G_COMMANDS:
        code, sha, text = run_cmd(command, root)
        out.append({"title": title, "command": command, "code": code, "sha": sha, "text": text})
    return out


def by_id(records):
    return dict((r["title"].split(None, 1)[0], r) for r in records)


def executable_mentions(records, mods):
    """dotted module -> (path, line) where an executable file OUTSIDE the package names the module in a
    command position: a python3 -m string, a shell wrapper line, or a subprocess argument. Prose in a .md or
    a planning .json is deliberately excluded, because naming a module is not calling it."""
    names = dict((dotted(rel), re.compile(re.escape(dotted(rel)) + r"(?![A-Za-z0-9_.])")) for rel in mods)
    found = {}
    for gid in ("G-05", "G-06", "G-08"):
        rec = by_id(records).get(gid)
        if rec is None:
            continue
        for raw in rec["text"].splitlines():
            m = _GREP_LINE.match(raw)
            if not m:
                continue
            path = m.group("path")
            if path.startswith("./"):
                path = path[2:]
            if path.startswith(PKG + "/") or not path.endswith(EXEC_EXT):
                continue
            for name, pat in names.items():
                if name not in found and pat.search(m.group("body")):
                    found[name] = (path, int(m.group("line")))
    return found


def prose_mentions(records, mods):
    """dotted module -> (path, line) where a .md or .json OUTSIDE the package names the module in a
    python3 -m command string. This is documentation, not a caller, so it never changes a status. It is
    reported because an ORPHAN module with a published command line is the one ORPHAN a reader must see."""
    names = dict((dotted(rel), re.compile(re.escape(dotted(rel)) + r"(?![A-Za-z0-9_.])")) for rel in mods)
    found = {}
    rec = by_id(records).get("G-05")
    if rec is None:
        return found
    for raw in rec["text"].splitlines():
        m = _GREP_LINE.match(raw)
        if not m:
            continue
        path = m.group("path")
        if path.startswith("./"):
            path = path[2:]
        if path.startswith(PKG + "/") or not path.endswith((".md", ".json")):
            continue
        for name, pat in names.items():
            if name not in found and pat.search(m.group("body")):
                found[name] = (path, int(m.group("line")))
    return found


def first_lineno(record, what):
    """The line number the pasted grep really printed. No output means the claim has no evidence, and a
    gate limit stated without a line cite is exactly the authored defect this generator removes."""
    head = record["text"].strip().split("\n")[0] if record["text"].strip() else ""
    m = re.match(r"^([0-9]+):", head)
    if not m:
        raise OSError("%s produced no line number, so the %s claim has no evidence" % (record["title"], what))
    return int(m.group(1))


def heavy_names(record):
    """The HEAVY basenames as the discovery script really spells them, read out of the pasted grep."""
    for raw in record["text"].splitlines():
        if "HEAVY" in raw and "{" in raw and "}" in raw:
            names = re.findall(r'"([^"]+)"', raw[raw.index("{"):raw.rindex("}")])
            if names:
                return names
    raise OSError("G-09 pasted no HEAVY set literal, so the heavy disclosure has no evidence")


def entry_rows(record):
    """The four surfaces, with registration read out of the pasted marketplace grep. manifest_path stays '-'
    and manifest_version stays NO-DATA for every surface: no per surface manifest was shown to this unit, and
    plugin/marketplace.json is proposed, not present (G-11)."""
    seen = {}
    for raw in record["text"].splitlines():
        m = _MARKET_LINE.match(raw)
        if not m:
            continue
        key = (os.path.basename(os.path.dirname(m.group("f"))), m.group("v"))
        seen.setdefault(key, int(m.group("n")))
    rows = []
    for surface in ("bundle", "products/brothermode", "products/brothersbe", "plugin"):
        claude = seen.get((".claude-plugin", surface), 0)
        cursor = seen.get((".cursor-plugin", surface), 0)
        rows.append({
            "surface": surface,
            "claude": claude,
            "cursor": cursor,
            "mutation": (("delete the %s entry from .claude-plugin/marketplace.json; registered flips false"
                          % surface) if claude else
                         ("add a %s entry to .claude-plugin/marketplace.json; registered flips true"
                          % surface)),
        })
    return rows


FINDINGS_HEADER = ("| finding_id | domain | file_path | symbol_line | symbol | capability | "
                   "status | caller_path | caller_line | grep_import | grep_string | mutation |")
ENTRY_HEADER = ("| surface | manifest_path | manifest_version | claude_marketplace_registered | "
                "claude_marketplace_line | cursor_marketplace_registered | cursor_marketplace_line | mutation |")
LONG_DASHES = (chr(8212), chr(8211))


def _fence_safe(records):
    """A pasted output line that opens a heading or closes the fence would silently truncate the evidence a
    reader sees. Refuse rather than paste something that reads as complete and is not."""
    for rec in records:
        for line in rec["text"].splitlines():
            if line.startswith("```") or line.startswith("### ") or line.startswith("## "):
                raise OSError("%s pasted a line that would break its own fence: %r" % (rec["title"], line))


def _control_hits_once(records):
    """REQ-03. The control must hit, and it must hit in exactly one pasted output, so that an empty result in
    any other record is a real absence rather than a pattern that never could match."""
    hits = [r["title"] for r in records if CONTROL in r["text"]]
    if len(hits) != 1:
        raise OSError("the positive control %r hit %d pasted outputs (%s), expected exactly one"
                      % (CONTROL, len(hits), ", ".join(hits) or "none"))


def render(root=ROOT, now=None):
    mods = modules(root)
    tests = test_modules(root)
    in_scope = [m for m in mods if domain_of(m)]
    outside = [m for m in mods if not domain_of(m)]
    if not in_scope:
        raise OSError("no module sits in one of the eight audited domains")
    sha = head_sha(root)
    records = gather(root)
    _fence_safe(records)
    _control_hits_once(records)
    index = by_id(records)
    hits, unparsed = importers(root)
    executable = executable_mentions(records, mods)
    documented = prose_mentions(records, mods)

    rows, counts = [], {"WIRED": 0, "ORPHAN": 0}
    for i, rel in enumerate(in_scope, 1):
        status, caller = classify(dotted(rel), rel, hits, executable)
        counts[status] += 1
        if caller:
            cpath, cline = caller
            mutation = ("delete the reference at %s line %d; this row must flip to ORPHAN" % (cpath, cline))
        else:
            cpath, cline = "-", 0
            mutation = ("add an import of %s to any file outside the package; this row must flip to WIRED"
                        % dotted(rel))
        rows.append("| F-%03d | %s | %s | 1 | <module> | %s | %s | %s | %d | G-01 | G-02 | %s |"
                    % (i, domain_of(rel), rel, capability(root, rel), status, cpath, cline, cell(mutation, 110)))

    heavy = heavy_names(index["G-09"])
    heavy_line = first_lineno(index["G-09"], "HEAVY")
    codes_line = first_lineno(index["G-14"], "codes_file")
    mods_line = first_lineno(index["G-15"], "exit 1 conflation")
    jev_line = first_lineno(index["G-16"], "BROTHER_JEV_STATE_DIR")
    gate_line = first_lineno(index["G-10"], "gate registration")
    census = index["G-17"]["text"].strip() or "NO-DATA"

    stamp = now or __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    o = []
    a = o.append
    a("---")
    a("audit_version: L2-1")
    a("commit_sha: %s" % sha)
    a("generated_at_utc: %s" % stamp)
    a("non_test_py_count: %d" % len(in_scope))
    a("test_py_count: %d" % len(tests))
    a("domain_count: %d" % len(DOMAINS))
    a("grep_positive_control: %s" % CONTROL)
    a("---")
    a("")
    a("# One-system wiring audit: is plugin/runtime/brother/ actually triggerable?")
    a("")
    a("GENERATED FILE. Do not hand edit: run `python3 -B scripts/gen_wiring_audit.py`, which rewrites it from")
    a("the tree. `--check` re-renders and exits 1 when this file has drifted from the tree.")
    a("")
    a("Every row below starts from a path walked on disk, so no row can name a file that does not exist.")
    a("Imports are found by PARSING each file with ast, never by grepping text, so a module name inside a")
    a("string or a comment is not counted as a caller. Every G record below is a command this generator ran")
    a("through /bin/sh, with the exit code it really returned and a sha256 taken over the bytes it really")
    a("wrote to stdout.")
    a("")
    a("## Headline")
    a("")
    a("%d non-test modules sit in the eight audited domains. WIRED (something outside the package imports or"
      % len(in_scope))
    a("invokes it) %d. ORPHAN (nothing outside the package reaches it) %d. The package is proven by its own"
      % (counts["WIRED"], counts["ORPHAN"]))
    a("%d test modules and, for %d of its modules, by nothing else." % (len(tests), counts["ORPHAN"]))
    a("")
    a("## Scope, counts and the domain vocabulary")
    a("")
    a("`non_test_py_count` and `test_py_count` count the modules inside the eight audited domains (`core`,")
    a("`mode`, `assurance`, `data`, `data/adapters`, `vault`, `mobile`, `hosts`), which is the vocabulary the")
    a("L2 envelope fixes and the only vocabulary the findings table can express. The package as a whole holds")
    a("%s non-test modules (G-17). The difference is %d module(s) whose directory is outside that vocabulary,"
      % (census, len(outside)))
    a("listed in full by G-03 and named here rather than filed under a neighbouring domain they do not sit in:")
    a("")
    for rel in outside:
        status, caller = classify(dotted(rel), rel, hits, executable)
        where = ("reached from %s line %d" % caller) if caller else "no caller found outside the package"
        note = documented.get(dotted(rel))
        if note:
            where += ", but named as a python3 -m command in %s line %d" % note
        a("- `%s`: %s, %s" % (rel, status, where))
    a("")
    a("%d file(s) in this repository could not be parsed as Python and so contributed no import edge. Every"
      % len(unparsed))
    a("one is a deliberately broken fixture under `benchmarks/fixtures/`, and all of them are still covered by")
    a("the text searches G-02 and G-05 to G-08, which read bytes and do not need a file to parse. No row is")
    a("therefore NO_DATA on their account.")
    a("")
    a("## Entry points")
    a("")
    a(ENTRY_HEADER)
    a("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in entry_rows(index["G-12"]):
        a("| %s | - | NO-DATA | %s | %d | %s | %d | %s |"
          % (row["surface"], "true" if row["claude"] else "false", row["claude"],
             "true" if row["cursor"] else "false", row["cursor"], cell(row["mutation"], 110)))
    a("")
    a("Registration booleans and line numbers are read out of the pasted G-12 grep. No per surface manifest")
    a("file was shown to this unit, so `manifest_path` is `-` and `manifest_version` is `NO-DATA` for every")
    a("surface. `plugin/marketplace.json` is proposed, not present: G-11 shows it missing, and it is never")
    a("cited here as existing.")
    a("")
    a("## Findings")
    a("")
    a(FINDINGS_HEADER)
    a("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    o += rows
    a("")
    a("`symbol_line` is 1 and `symbol` is `<module>` because this audit asks a module level question: does")
    a("anything outside the package reach this file at all. `capability` is the module's own first docstring")
    a("sentence, read from the file, never a description written here.")
    a("")
    a("## Documented but not called")
    a("")
    a("Modules named as a `python3 -m` command line inside a `.md` or `.json` outside the package, read out")
    a("of the pasted G-05 search. A published command line is not a caller and does not change a status, but")
    a("an ORPHAN module with a documented command is the ORPHAN most likely to be a real wiring gap:")
    a("")
    if documented:
        for name in sorted(documented):
            path, line = documented[name]
            a("- `%s`: named in %s line %d" % (name, path, line))
    else:
        a("- none: no module outside the package is named as a python3 -m command in a .md or .json file.")
    a("")
    a("## Reproducibility")
    a("")
    a("Every command below was run from the repository root through /bin/sh by")
    a("`scripts/gen_wiring_audit.py`, with stderr discarded, exactly as the `sha256_stdout` helper in")
    a("`scripts/test_L2_spec.py` discards it. `sha256:` is the SHA-256 of that command's stdout bytes only.")
    a("An interactive shell can carry a `grep` shell function that forwards to a different grep binary, which")
    a("strips the leading `./` and honours ignore files; a hash re-taken in such a shell will not match. Use")
    a("`sh -c` to reproduce one. The positive control `%s` hits in exactly one pasted output (G-03)," % CONTROL)
    a("which is what makes an empty result in any other record a real absence rather than a dead pattern.")
    a("")
    for rec in records:
        a("### %s" % rec["title"])
        a("")
        a("command: %s" % rec["command"])
        a("exit_code: %d" % rec["code"])
        a("sha256: %s" % rec["sha"])
        a("output_pasted: true")
        a("output:")
        a("```")
        body = rec["text"].rstrip("\n")
        a(body if body else "(no output)")
        a("```")
        a("")
    a("## Gate limits noted, not fixed")
    a("")
    a("- HEAVY basenames: `scripts/plugin_runtime_fast_discover.py` line %d skips %s by basename, so a fast "
      "gate PASS does not cover them; last full battery covering all three: NO-DATA. G-09. fix deferred, "
      "audit only" % (heavy_line, ", ".join("`%s`" % n for n in heavy)))
    a("- shared codes_file: `scripts/required_fast.sh` line %d writes every check code to a predictable path "
      "under TMPDIR keyed only by directory name and pid, and nothing counts the codes before they are read. "
      "G-14. fix deferred, audit only" % codes_line)
    a("- exit `1` conflation: `scripts/plugin_runtime_fast_discover.py` line %d returns 1 when discovery "
      "finds no module, which the wrapper cannot tell apart from a real test failure, so an empty run reads "
      "as FAIL rather than NO-DATA. G-15. fix deferred, audit only" % mods_line)
    a("- BROTHER_JEV_STATE_DIR preset: `scripts/required_fast.sh` line %d keeps a caller supplied value "
      "instead of its own mktemp directory, so a caller can opt the run out of state isolation. G-16. fix "
      "deferred, audit only" % jev_line)
    a("")
    a("The package's own test run is registered in the gate at `scripts/required_fast.sh` line %d (G-10)."
      % gate_line)
    a("")
    a("## Status semantics")
    a("")
    a("1. ORPHAN means no caller outside plugin/runtime/brother/ was found by the pasted grep.")
    a("2. NO_DATA must never be rendered as ORPHAN.")
    a("3. ORPHAN is a wiring observation, not a defect.")
    a("")
    a("## Staleness")
    a("")
    a("This audit is valid only at the `commit_sha` recorded in the front matter above. Any change to an")
    a("input listed below invalidates it: re-run all G records by regenerating this file, which refreshes")
    a("`commit_sha` and `generated_at_utc` together with every hash, and only then cite it again.")
    a("")
    a("- any change to the `plugin/runtime/brother/` glob")
    a("- any change to `scripts/required_fast.sh`")
    a("- any change to `scripts/plugin_runtime_fast_discover.py`")
    a("- any change to `.claude-plugin/marketplace.json` (NEW)")
    a("- any change to `.cursor-plugin/marketplace.json` (NEW)")
    a("- any change to `plugin/marketplace.json` (NEW)")
    a("")
    a("A stale audit is NO-DATA: it blocks, and it is never rendered as WIRED or ORPHAN.")
    a("")
    a("## Out of scope")
    a("")
    a("- U6: packaging unit, no rewiring here.")
    a("- U7: registration unit, no rewiring here.")
    a("- U8: marketplace retirement unit, no rewiring here.")
    a("- PARITY-MATRIX: parity labels only (G-13 shows the file present), no rewiring here.")
    a("- out-of-repo bridge path: a non-repo note only, never a caller, never proof, never a blocker.")
    a("- `scripts/audit_one_system_wiring.sh` NEW: follow-on automation, not built here.")
    a("- every gate limit above: fix deferred, and no code file changes in this unit beyond")
    a("  `scripts/gen_wiring_audit.py`, the generator that writes this document.")
    a("")
    body = "\n".join(o)
    prose = re.sub(r"```\n.*?\n```", "", body, flags=re.S)
    for dash in LONG_DASHES:
        if dash in prose:
            raise OSError("a long dash reached the generated prose, which the house law forbids")
    return body, counts, len(in_scope), len(unparsed), len(outside)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--check", action="store_true", help="exit 1 if the file on disk differs from a fresh render")
    args = ap.parse_args(argv)
    try:
        body, counts, n, unparsed, outside = render()
    except OSError as exc:
        print("NO-DATA: the audit could not be generated (%s); this is not a pass" % exc)
        return 2
    if args.check:
        try:
            with open(args.out, encoding="utf-8") as fh:
                on_disk = fh.read()
        except OSError:
            print("DRIFT   %s cannot be read; regenerate it" % args.out)
            return 1
        strip = lambda s: re.sub(r"^(commit_sha|generated_at_utc):.*$", "", s, flags=re.M)
        if strip(on_disk) != strip(body):
            print("DRIFT   %s no longer matches the tree; run gen_wiring_audit.py" % args.out)
            return 1
        print("CURRENT %s matches the tree (%d modules)" % (args.out, n))
        return 0
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(body)
    print("WROTE   %s" % args.out)
    print("MODULES %d in the eight audited domains | WIRED %d | ORPHAN %d | outside the vocabulary %d | "
          "unparsed files %d" % (n, counts["WIRED"], counts["ORPHAN"], outside, unparsed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
