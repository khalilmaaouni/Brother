#!/usr/bin/env python3
"""Assemble a brief that makes an adversary model write an EXECUTABLE hostile probe for one worker build.
Its output is run by probe_build.py, so every claim is observed. The adversary proposes, execution decides.
usage: probe_brief.py <spec.md> <sub> <build.json> <out.md>"""
import ast, json, os, re, sys


def public_surface(build_text):
    """The real public names of every Python module the build touches: defs with their parameters and classes from the
    file on disk, plus the defs and classes the build's own replacement text adds. One line per module.
    WHY (2026-09-24): 19 of 25 probes on one build were NO-DATA on attributes that do not exist, because the adversary
    saw the specification and the build JSON and never the module's surface. An unreadable build is NO-DATA, said."""
    try:
        b = json.loads(build_text)
    except ValueError:
        return "NO-DATA: the build json is unreadable"
    if not isinstance(b, dict): return "NO-DATA: the build json is not an object"
    lines, seen = [], set()
    items = [x for x in list(b.get("edits") or []) + list(b.get("tests") or []) if isinstance(x, dict)]
    # the module under test rides along with its test: a tests only build still probes the module the tests exercise
    for x in list(items):
        base = os.path.basename(str(x.get("path") or ""))
        if base.startswith("test_") and base.endswith(".py"):
            sib = os.path.join(os.path.dirname(str(x.get("path"))), base[len("test_"):])
            if os.path.isfile(sib): items.append({"path": sib})
    for item in items:
        path = str(item.get("path") or "")
        if not path.endswith(".py") or path in seen: continue
        seen.add(path); names = []
        src = ""
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh: src = fh.read()
            except OSError:
                src = ""
        try:
            tree = ast.parse(src) if src else None
        except SyntaxError:
            tree = None
        for n in (tree.body if tree else []):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_"):
                names.append("%s(%s)" % (n.name, ", ".join(a.arg for a in n.args.args)))
            elif isinstance(n, ast.ClassDef) and not n.name.startswith("_"):
                names.append("class " + n.name)
        for key in ("replace", "new_file_content"):
            for m in re.finditer(r"^(async def|def|class) ([A-Za-z_]\w*)(\([^)]*\))?", str(item.get(key) or ""), re.M):
                if m.group(2).startswith("_"): continue
                names.append(("class " + m.group(2) if m.group(1) == "class" else m.group(2) + (m.group(3) or "")) + " (added by this build)")
        lines.append("%s: %s" % (path, "; ".join(dict.fromkeys(names)) or "no public names found"))
    return "\n".join(lines) or "NO-DATA: the build names no python module"


def selftest():
    import tempfile
    d = tempfile.mkdtemp(prefix="surface-"); cwd = os.getcwd(); os.chdir(d)
    try:
        os.makedirs("pkg"); open("pkg/m.py", "w").write("def real(a, b):\n    return a\n\nclass Thing:\n    pass\n\ndef _private():\n    pass\n")
        b = json.dumps({"edits": [{"path": "pkg/m.py", "find": "x", "replace": "def added(x):\n    return x\n"}],
                        "tests": [{"path": "pkg/test_m.py", "new_file_content": "import unittest\nclass T(unittest.TestCase):\n    pass\n"}]})
        s = public_surface(b)
        cases = [("an existing def is listed with its parameters", "real(a, b)" in s),
                 ("an existing class is listed", "class Thing" in s),
                 ("a private name is not listed", "_private" not in s),
                 ("a def the build adds is listed and marked", "added(x) (added by this build)" in s),
                 ("a new test module's class is listed", "pkg/test_m.py: class T (added by this build)" in s),
                 ("a tests only build still lists the module under test", "pkg/m.py: real(a, b)" in public_surface(json.dumps({"tests": [{"path": "pkg/test_m.py", "new_file_content": "x"}]}))),
                 ("an unreadable build is NO-DATA", public_surface("{not json").startswith("NO-DATA")),
                 ("a build naming no module is NO-DATA", public_surface("{}").startswith("NO-DATA"))]
        big = "## unit\n\n### X.1 mine\nR1: `real(` must refuse None.\n\n### X.2 other\n" + ("filler line about nothing\n" * 3000) + "### X.3 third\nR9: added( returns x.\n"
        sl = spec_slice(big, "X.1", "pkg/m.py: real(a, b); added(x)", 20000)
        cases += [("a spec under budget is returned whole", spec_slice("## u\n### X.1 s\n", "X.1", "real(a)", 20000) == "## u\n### X.1 s\n"),
                  ("over budget the sub unit's section survives", "### X.1 mine" in sl and "R1: `real(`" in sl),
                  ("over budget the lines naming the surface survive from other sections", "R9: added(" in sl),
                  ("over budget the filler is gone and the dropped headings are named", "filler line" not in sl and "X.2 other" in sl and len(sl.encode()) <= 20000)]
    finally:
        os.chdir(cwd)
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


PROBE_BUDGET = 60000   # bytes of prompt; 2026-09-24: DeepSeek probe calls stalled 3 of 7 at 600 s on 68 to 101 KB prompts


def spec_slice(spec, sub, surface, budget=PROBE_BUDGET):
    """The spec a probe writer needs when the whole one does not fit: the sub unit's own section plus every spec line
    that names a function of the public surface (the REQ and Refuses lines the probes are judged against), the dropped
    sections marked by heading. Under budget the whole spec is returned unchanged. Read only, never truncates a line."""
    if len(spec.encode("utf-8")) <= budget:
        return spec
    m = re.search(r"^#{2,4} %s\b.*?(?=^#{2,4} |\Z)" % re.escape(sub), spec, flags=re.M | re.S)
    section = m.group(0) if m else ""
    names = set(re.findall(r"([A-Za-z_][A-Za-z0-9_]{2,})\(", surface))
    keep = [l for l in spec.splitlines() if any((n + "(") in l or ("`" + n) in l for n in names)] if names else []
    headings = [h for h in re.findall(r"^#{2,4} (.+)$", spec, flags=re.M) if not h.startswith(sub)]
    return (section + "\n\nSPEC LINES NAMING THE PUBLIC SURFACE (the rest of the specification was sliced out to fit %d bytes; "
            "sections dropped by heading: %s):\n%s\n" % (budget, "; ".join(headings)[:2000] or "none", "\n".join(keep)))


if sys.argv[1:2] == ["--selftest"]: sys.exit(selftest())
spec_path, sub, build_path, out = sys.argv[1:5]
with open(spec_path, encoding="utf-8") as f: spec = f.read()
with open(build_path, encoding="utf-8") as f: build = f.read()
surface = public_surface(build)
spec = spec_slice(spec, sub, surface, PROBE_BUDGET - len(build.encode("utf-8")) - 6000)   # the build and the head keep their room
text = ("House laws: unknown, corrupt or missing input BLOCKS or is NO-DATA, never the safe case. No person, client or employer names. No long dashes.\n\n"
"You are the RED TEAM for ONE worker build of sub unit %s. A sandbox grader already showed the build agrees with its own tests. That is all it showed. "
"Your job is to write a Python PROBE SCRIPT that a machine will run against the build. You do not predict outcomes, the machine observes them.\n\n"
"OUTPUT: exactly one JSON object, nothing else: {\"probe_script\": \"<python source>\", \"expect_block\": [\"<label>\", ...], \"notes\": \"<120 words at most>\"}\n\n"
"THE HARNESS, already defined before your script runs (do not redefine it): fire(label, fn) calls fn() with no arguments, catches EVERYTHING, and prints what happened. "
"The repository root is on sys.path, so import the build's modules by their dotted path, and import the build's OWN TEST MODULE to reuse its fixture helpers.\n\n"
"WHAT TO FIRE, 25 to 45 probes, every label unique and specific:\n"
"1. One honest baseline call per public function, label starting with 'baseline', so a reader sees the happy path still works.\n"
"2. For each public function: wrong type, None, a bool where a number belongs, NaN and infinity, an empty container, a str where a list belongs, an unhashable value where a key belongs, a path shaped id like '../x', bytes that are not utf-8, a generator where a list belongs.\n"
"3. THE DEEP ONES, which find most real defects: build a VALID object with the test fixtures, then use dataclasses.replace (or a dict copy) to make exactly ONE field hostile, and pass that. Frozen dataclasses check nothing, so a record altered after parsing reaches the code unchecked. Cover every field of every record type the function reads.\n"
"4. Consistency attacks: two records that disagree (mixed ids in one group, duplicate keys, counts that cannot all be true), a value changed after its digest was computed.\n"
"5. For a CLI main(argv): a missing file, a directory, a binary file, an empty file, an unknown flag. Write any file you need under tempfile.mkdtemp().\n\n"
"RULES: standard library only. No network, no subprocess, no eval, no exec. Never touch a path outside tempfile.mkdtemp(). Wrap every call in a lambda so an exception at CALL time is caught by fire, "
"and build hostile values INSIDE the lambda when building them can itself raise. "
"In expect_block list the labels where the specification says the input must be blocked (so a machine RETURNED line for that label is a wrong accept). "
"Where the section carries a 'Refuses:' line, expect_block is EXACTLY those inputs and nothing else; where it carries an 'Accepts:' line, never list those. "
"Without either line, list only inputs the section's own text blocks in words; an input the specification never mentions is not a wrong accept (2026-09-23: 977 of 1250 findings in one night were accepts the specification never ruled on).\n\n"
"PUBLIC SURFACE, the only names you may probe (a name not listed here does not exist in the build and reads as NO-DATA, which proves nothing):\n%s\n\n"
"THE WHOLE UNIT SPECIFICATION:\n%s\n\nTHE BUILD (JSON of edits, tests, mutations):\n%s\n" % (sub, surface, spec, build))
with open(out, "w", encoding="utf-8") as f: f.write(text)
print(len(text.encode()))
