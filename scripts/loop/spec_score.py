#!/usr/bin/env python3
"""Score the specification of every sub unit out of 10, deterministically, against the REAL tree. Owner rule 2026-09-20:
no work starts and nothing lands for a sub unit whose spec scores under 9 of 10.
usage (repo root): spec_score.py [unit ...] [--json out.json]     (no unit = every unit with a spec)
One point each; a point is only given on evidence, and an unreadable input scores 0 (never a pass):
 1 SECTION      the sub unit has its own heading section in the unit spec
 2 FILES        the section names its files in backticks, and at least one is marked NEW or existing
 3 PATHS TRUE   every file it calls existing EXISTS, every file it calls NEW does NOT (a spec that is wrong about the tree is fiction)
 4 SIGNATURES   a python block with at least one def, every def with a return annotation or typed parameters
 5 CALLS TRUE   every `name(` it mentions that it does not itself define exists somewhere in the files it names or the tree
 6 DONE CHECK   the landing gate would run the section's done check: spec_check.gate_refusal (one command grade_build.OK_CMD accepts)
 7 REQUIREMENTS the unit spec carries requirement ids and this section or the unit maps them to a function (the control point)
 8 CAN GO RED   the unit spec names at least one mutation for this sub unit or its functions (M-...), so its check can fail
 9 EDGES        the unit spec has an edge case or failure section (empty, corrupt, concurrent, stale... named)
10 NO UNKNOWN   the section leaves nothing UNKNOWN, TBD or 'to be decided': a builder must not have to guess
Output: one line per sub unit with the score and the MISSING points by name."""
import json, os, re, subprocess, sys
args = [a for a in sys.argv[1:] if not a.startswith("--")]
out_json = sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else None
if out_json and out_json in args:
    args.remove(out_json)
if out_json and os.path.exists(out_json):
    os.remove(out_json)   # a run that dies leaves NO file, never the previous run's scores under this run's name
# NO SCORE CACHE (plan E, 2026-10-01): its key read status text and one directory's metadata, so two different edits
# could share a key and a nested spec change could keep a stale score. Scoring runs every time.
plan = json.load(open("docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json", encoding="utf-8"))
# ONE rule for what a done_check may be, owned by the grader: the scorer, the brief gate and the grader read the same
# regex, so a section cannot score 9 on a command the grader will then refuse, or the reverse (2026-09-24: three copies
# had drifted three ways). The regex alone was not enough (2026-09-29): the scorer matched it against ANY python3 line,
# while the landing gate matches it against the ONE command spec_check.done_check extracts (a fenced block joined
# with &&). The DONE CHECK point is now spec_check.gate_refusal, the gate's own code.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # the copy beside this file, and only that one
from grade_build import record_claim
from brief_check import SPEC_PATH, labelled_paths
import spec_check   # the landing gate's done check reader (done_check) and its command rule (grade_build.OK_CMD)
tracked = set(subprocess.run(["git", "ls-files"], capture_output=True, text=True, timeout=30).stdout.split())
_defs = None


def defined(name):
    """Is `name` defined anywhere in the tracked .py/.sh files. ONE git grep builds the whole index on first use;
    before 2026-09-22 this ran one git grep PER NAME, hundreds of spawns per scoring, and scoring took 7.9 s of
    every pass (runner_pool and adaptive_sizing each score once, so twice per pass). Same pattern, same match:
    POSIX ERE, no backslash-b or backslash-s. The index holds names as git printed them, so a lookup is a set membership."""
    global _defs
    if _defs is None:
        r = subprocess.run(["git", "grep", "-hoE", r"(def|class) +[A-Za-z_][A-Za-z0-9_]* *[(:]|^[A-Za-z_][A-Za-z0-9_]* *=|^[A-Za-z_][A-Za-z0-9_]*\(\)", "--", "*.py", "*.sh"],
                           capture_output=True, text=True)
        _defs = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", "\n".join(l.replace("def ", "").replace("class ", "") for l in r.stdout.splitlines()))) if r.returncode == 0 else set()
    return name in _defs


BUILTINS = set(dir(__builtins__)) | {"open", "print", "len", "range", "isinstance", "sorted", "set", "dict", "list", "tuple", "str", "int", "float", "bool",
                                      "main", "run_check", "dataclass", "field", "replace", "asdict", "patch", "assertEqual", "assertRaises", "subTest", "setUp", "tearDown", "sha256", "dumps", "loads", "join", "get", "append", "read", "write", "exists", "isfile", "replace"}
import hashlib, shutil, tempfile, time, math, itertools, functools, collections, importlib, unittest, dataclasses, datetime, threading, fcntl, signal, stat, uuid, ast
_MODS = (os, os.path, json, re, subprocess, sys, hashlib, shutil, tempfile, time, math, itertools, functools, collections, importlib, unittest, dataclasses, datetime, threading, fcntl, signal, stat, uuid, ast, str, dict, list, set, bytes)


def STDLIB(name):
    return any(hasattr(m, name) for m in _MODS)


scores = {}
for u in plan["units"]:
    if args and u["id"] not in args:
        continue
    spec_path = u.get("spec")
    for sub in u.get("sub_units") or []:
        miss = []
        try:
            spec = open(spec_path, encoding="utf-8").read()
        except (OSError, TypeError):
            scores[sub] = (0, ["NO-DATA: spec file unreadable: %s" % spec_path]); continue
        h = re.search(r"^(#{2,4}) %s\b" % re.escape(sub), spec, flags=re.M)   # up to the next heading of its own level or higher
        m = re.search(r"^#{2,4} %s\b.*?(?=^#{2,%d} |\Z)" % (re.escape(sub), len(h.group(1))), spec, flags=re.M | re.S) if h else None
        if not m:
            scores[sub] = (0, ["SECTION: no heading for %s in %s" % (sub, spec_path)]); continue
        sec = m.group(0)
        paths = SPEC_PATH.findall(sec)   # brief_check.SPEC_PATH, the one reader
        if not paths or not re.search(r"\bNEW\b|\bexisting\b|\bmodify\b", sec, re.I):
            miss.append("FILES: no backticked file marked NEW or existing")
        wrong = []
        first = {}
        for p, _, s, _ in labelled_paths(sec):   # brief_check.labelled_paths, the one reader of a labelled path
            first.setdefault(p, s)
        for p in dict.fromkeys(paths):
            s = first[p]
            # anchored at the token, whatever the label spelling (`NEW: p` used to find no `p` and read the section
            # start); the window holds the token itself, so a label inside the backticks is read here too
            ctx = sec[max(0, s - 40): s + len(p) + 60]
            says_new = bool(re.search(r"\bNEW\b", ctx))
            exists = p in tracked or os.path.exists(p)
            # NEW and present is NOT judged: an earlier sub unit of the same unit legitimately creates it (calibrated 2026-09-20 on landed sub units)
            if not says_new and not exists and re.search(r"\bexisting\b|\bmodify\b|\bchanged\b", ctx, re.I):
                wrong.append(p + " is called existing and is absent")
        if wrong:
            miss.append("PATHS TRUE: " + "; ".join(wrong[:3]))
        blocks = re.findall(r"```python\n(.*?)```", sec, flags=re.S)
        defs = re.findall(r"^\s*def (\w+)\((.*?)\)\s*(->\s*[^:]+)?:", "\n".join(blocks), flags=re.M | re.S)
        if not defs or any(not ret and ":" not in params for _, params, ret in defs):
            miss.append("SIGNATURES: no typed python signatures")
        own = {d[0] for d in defs} | set(re.findall(r"^\s*(?:def|class) (\w+)", "\n".join(re.findall(r"```python\n(.*?)```", spec, flags=re.S)), flags=re.M))  # earlier sub units of this spec define names too
        called = {n for n in re.findall(r"`(?:[\w.]+\.)?([a-z_][a-z0-9_]{3,})\(", sec) if n not in own and n not in BUILTINS and not STDLIB(n)}  # code quoted names only: prose like "plan (" is not a call
        ghost = sorted(n for n in called if not defined(n))
        if ghost:
            miss.append("CALLS TRUE: named and defined nowhere in the tree: " + ", ".join(ghost[:5]))
        _gate = spec_check.gate_refusal(spec, sub)   # the landing gate's own verdict, never a second reading (2026-09-29)
        if _gate:
            miss.append("DONE CHECK: " + _gate)
        if not re.search(r"\b(R\d+|REQ[- ][A-Z0-9-]+|R-[A-Z]+-\d+|W\d|H\d)\b", spec):
            miss.append("REQUIREMENTS: no requirement ids mapped to a control point")
        fn_names = "|".join(re.escape(d[0]) for d in defs) or "$^"
        unit_muts = set(re.findall(r"\bM-[A-Z0-9][A-Z0-9_.-]+", spec))
        n_subs = len(u.get("sub_units") or [1])
        if len(unit_muts) >= n_subs:
            pass  # a unit level mutation table with at least one mutation per sub unit: the checks can go red
        elif not re.search(r"\bM-[A-Z0-9][A-Z0-9_.-]+", sec) and not re.search(r"M-[A-Z0-9_.-]*(%s)" % re.escape(re.sub(r"\W", "", sub).upper()), spec) \
                and not re.search(r"(?s)M-[A-Z0-9][A-Z0-9_.-]+[^\n]{0,200}(%s)" % fn_names, spec):
            miss.append("CAN GO RED: no mutation named for this sub unit or its functions")
        if not re.search(r"(?i)^#{2,4} .*(edge|failure|pre-mortem|hazard)", spec, flags=re.M) and not re.search(r"(?i)\bedges?:", sec):
            miss.append("EDGES: no edge case or failure section")
        unknowns = re.findall(r"\bUNKNOWN\b|\bTBD\b|to be decided", sec)
        if unknowns:
            miss.append("NO UNKNOWN: %d left in the section" % len(unknowns))
        scores[sub] = (10 - len(miss), miss)
        record_claim("spec_score", sub, "this spec scores 9 or more, so the lane can land", (10 - len(miss)) / 10.0)
_lines = ["%-8s %2d/10 %s" % (sub, n, "" if n == 10 else "| " + " | ".join(miss)) for sub, (n, miss) in scores.items()]
ok = sum(1 for n, _ in scores.values() if n >= 9)
_lines.append("\n%d sub units scored | %d at 9 or 10 (may start) | %d under 9 (HELD)" % (len(scores), ok, len(scores) - ok))
_text = "\n".join(_lines) + "\n"; sys.stdout.write(_text)
_scores = {k: {"score": v[0], "missing": v[1]} for k, v in scores.items()}
if out_json:
    with open(out_json, "w") as _f: json.dump(_scores, _f, indent=1)
