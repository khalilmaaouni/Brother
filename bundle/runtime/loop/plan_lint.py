#!/usr/bin/env python3
"""Plan lint (RF-08) in report mode: every open unit is read with the closer's own screens before the loop spends.

usage: python3 scripts/loop/plan_lint.py --report docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json [--scope REGEX]

Report is the default mode and it never changes a verdict of the loop. BROTHER_RUNFLOW_LINT=block makes the pool's
admission hold on a BLOCKING or NO-DATA finding; any other value is treated as block and named on the mode line,
never as off.
"""
import argparse
import ast
import json
import os
import re
import shlex
import sys
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


_ALLOWED_HEAD = ("python3", "/usr/bin/python3")


def screen_done_check(cmd):
    """[[argv], ...] to run in order, or None when the command is not a shape we are willing to execute."""
    if not isinstance(cmd, str) or not cmd.strip():
        return None
    if re.search(r"[|;`$><\n\\]", cmd) or "&&&" in cmd:
        return None
    parts = [p.strip() for p in cmd.split("&&")]
    out = []
    for part in parts:
        if not part:
            return None
        try:
            argv = shlex.split(part)
        except ValueError:
            return None  # sbe: allow-silent an unparseable command is refused: None is this function's documented refusal, never a pass
        if not argv or argv[0] not in _ALLOWED_HEAD:
            return None
        for tok in argv[1:]:
            if tok.startswith(("/", "~")) or ".." in tok.split("/"):
                return None
            # -c runs whatever string follows it, which is the whole hole this screen exists to close: the
            # done_check comes from the plan, and model drafts write plan fields. Proved by a council seat
            # 2026-09-21, which slipped `python3 -c "import shutil"` past the earlier version.
            if tok == "-c" or tok.startswith("-c"):
                return None
        out.append(argv)
    return out


def lint_mode(env=None):
    """("report" | "block", note). Unset or "report" -> ("report", ""); "block" -> ("block", ""); any other
    value -> ("block", note naming the variable): an unknown value is treated as block, never as off."""
    if env is None:
        env = os.environ
    if not hasattr(env, "get"):
        raise ValueError("lint_mode: env must be a mapping, got %s" % type(env).__name__)
    value = env.get("BROTHER_RUNFLOW_LINT", "")
    if not isinstance(value, str):
        raise ValueError("lint_mode: BROTHER_RUNFLOW_LINT must be text, got %s" % type(value).__name__)
    if value in ("", "report"):
        return ("report", "")
    if value == "block":
        return ("block", "")
    return ("block", "NO-DATA: BROTHER_RUNFLOW_LINT=%s is not report or block" % value)


# ONE acceptance rule for a done check: the landing gate's own OK_CMD (grade_build) and the store's own sub_landed
# (plan_store). The lint kept private copies until 2026-09-30, and a mutation sweep showed either could drift with
# every test green; a lint that accepts what the gate refuses is a lint that lies.
# Both are read at call time through the module, never bound here: a bound copy is a copy.
import grade_build  # noqa: E402
import plan_store  # noqa: E402
import ev_gate  # noqa: E402  (FX-13.4: rule EV prices a round exactly as the runner does)
import brief_check  # noqa: E402  (NAMED-TEST asks the loop's own B2 which named tests it opens)

_SPEC_PATH = re.compile(r"`(?:[A-Za-z]+:\s*)?([A-Za-z0-9_./-]+\.(?:py|sh|json|md|jsonl))(?:\s+\((?:NEW|existing)\))?`")
_INSIDE_LABEL = re.compile(r"^`(NEW|existing):|\((NEW|existing)\)`$")
_AFTER_NEW = re.compile(r"\s+\(?NEW\b")


def named_paths(section):
    """Backticked paths in one section text. A section that is not text is REFUSED by name (ValueError)."""
    if not isinstance(section, str):
        raise ValueError("named_paths: section is not a str: %s" % type(section).__name__)
    return [q for q in dict.fromkeys(_SPEC_PATH.findall(section))
            if not q.startswith("docs/plan/specs/") and not q.endswith(".jsonl")]


def labelled_paths(text):
    """[(path, label, start, end)] for every path token in text."""
    if not isinstance(text, str):
        raise ValueError("labelled_paths: text is not a str: %s" % type(text).__name__)
    out = []
    for m in _SPEC_PATH.finditer(text):
        start, end = m.span()
        inside = _INSIDE_LABEL.search(m.group(0))
        if inside:
            label = inside.group(1) or inside.group(2)
        elif _AFTER_NEW.match(text, end):
            label = "NEW"
        else:
            j = start
            while j > 0 and text[j - 1].isspace():
                j -= 1
            word_before = j >= 4 and (text[j - 4].isalnum() or text[j - 4] == "_")
            label = "NEW" if j < start and text[max(0, j - 3):j] == "NEW" and not word_before else ""
        out.append((m.group(1), label, start, end))
    return out


# FX-13.2: NEW-EXISTS and SCREEN. The NEW label is read through this module's own labelled_paths, one reader
# for every spelling; a fenced block is judged by the grader's own unsafe(), never by a copy of its rules.
_SECTION_HEAD = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_FENCE_PY = re.compile(r"^```python\s*\n(.*?)^```\s*$", re.M | re.S)
_TICK_IMPORT = re.compile(r"`(import\s+[A-Za-z_][\w.]*|from\s+[A-Za-z_][\w.]*\s+import\s+[A-Za-z_][\w.]*)`")


def _section_by_sub(text, sub):
    """The body of the section whose heading names `sub` as a whole token, or None for any other input."""
    if not isinstance(text, str) or not isinstance(sub, str) or not sub:
        return None
    pat = re.compile(r"(?<![\w.-])" + re.escape(sub) + r"(?![\w-])")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _SECTION_HEAD.match(line)
        if not m or not pat.search(m.group(2)):
            continue
        level = len(m.group(1))
        body = []
        for j in range(i + 1, len(lines)):
            m2 = _SECTION_HEAD.match(lines[j])
            if m2 and len(m2.group(1)) <= level:
                break
            body.append(lines[j])
        return "\n".join(body)
    return None


def _section_new_and_screen(uid, sub, unit, section, root="."):
    """NEW-EXISTS and SCREEN findings for one section body. Anything that is not a record is refused with []."""
    findings = []
    if not isinstance(section, str) or not section:
        return findings
    if not isinstance(unit, dict):
        unit = {}
    try:
        labelled = labelled_paths(section)
    except ValueError:
        labelled = []
    for path, label, _start, _end in labelled:
        if label != "NEW" or not isinstance(path, str) or not path:
            continue
        if os.path.exists(os.path.join(root, path)):
            findings.append(_finding(uid, sub, "NEW-EXISTS", "BLOCKING",
                                     "%s is labelled NEW but the path exists in the tree" % path,
                                     "correct the label or remove the existing file"))
    py_named = [p for p in named_paths(section) if isinstance(p, str) and p.endswith(".py")]
    target = py_named[0] if py_named else "x.py"
    runners = unit.get("command_runners") or []
    for m in _FENCE_PY.finditer(section):
        block = m.group(1)
        if not block.strip():
            continue
        reason = grade_build.unsafe({"edits": [{"path": target, "new_file_content": block}]}, runners=runners)
        if reason:
            findings.append(_finding(uid, sub, "SCREEN", "BLOCKING",
                                     "the section python block is refused by the grader: %s" % reason,
                                     "rewrite the block to the grader's screen"))
    for m in _TICK_IMPORT.finditer(section):
        stmt = m.group(1)
        tick_reason = grade_build.unsafe({"edits": [{"path": target, "new_file_content": stmt + "\n"}]}, runners=runners)
        if tick_reason:
            findings.append(_finding(uid, sub, "SCREEN", "ADVISORY",
                                     "the section names %s, which the grader refuses: %s" % (stmt, tick_reason),
                                     "prose may name a module to forbid it; keep it out of a fence"))
    return findings


def _section(spec_text, sub):
    """The spec section for one sub unit, or "" when the heading is absent."""
    if not isinstance(spec_text, str) or not isinstance(sub, str):
        return ""
    h = re.search(r"^(#{2,4}) %s\b" % re.escape(sub), spec_text, flags=re.M)
    if not h:
        return ""
    m = re.search(r"^#{2,4} %s\b.*?(?=^#{2,%d} |\Z)" % (re.escape(sub), len(h.group(1))), spec_text, flags=re.M | re.S)
    return m.group(0) if m else ""


def _section_done_check(spec_text, sub):
    """The section's done check command, or None."""
    sec = _section(spec_text, sub)
    m = re.search(r"(?i)done[- ]check[^\n`]*`((?:/usr/bin/)?python3 [^\n`]+)`", sec)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?i)done[- ]check[^\n]*\n+```[a-z]*\n(.*?)\n```", sec, flags=re.S)
    lines = [l.strip() for l in m.group(1).splitlines() if l.strip() and not l.strip().startswith("#")] if m else []
    if lines:
        return " && ".join(lines)
    m = re.search(r"(?im)done[- ]check[^\n]*\n+\s*((?:/usr/bin/)?python3 [^\n]+)", sec)
    return m.group(1).strip() if m else None


def _finding(unit, sub, rule, severity, text, fix):
    return {"unit": unit, "sub": sub, "rule": rule, "severity": severity, "text": text, "fix": fix}


def _short(value, limit=90):
    text = value if isinstance(value, str) else repr(value)
    return text[:limit]


def _line(finding):
    """One printed line. Every LINT line has the substring START changed to Start: loop_pass.sh keeps only the
    lines matching START from the pool output, and a lint line must never be counted as a started unit."""
    sub = finding["sub"] if isinstance(finding["sub"], str) and finding["sub"] else "-"
    line = "LINT %s %s %s %s: %s | FIX: %s" % (finding["severity"], finding["unit"], sub, finding["rule"],
                                               finding["text"], finding["fix"])
    return line.replace("START", "Start")


def _open_sub_ids(unit):
    """(ids, problem). problem is text when the unit's evidence cannot be read."""
    subs = unit.get("sub_units")
    if subs is None:
        subs = []
    if not isinstance(subs, list) or not all(isinstance(s, str) for s in subs):
        return ([], "the unit sub_units is not a list of ids")
    evidence = unit.get("evidence")
    if evidence is None:
        evidence = ""
    if not isinstance(evidence, str):
        return ([], "the unit evidence is not text")
    try:
        return ([s for s in subs if not plan_store.sub_landed(s, evidence)], "")
    except ValueError as exc:
        return ([], "the unit evidence cannot be read (%s)" % exc)


def _under(root, rel):
    """The path of a file the plan names, under `root`. A root that is not a text path is UNKNOWN, and an
    unknown input is answered with None, never with the current directory."""
    if not isinstance(root, str) or not root:
        return None
    if os.path.isabs(rel):
        return rel
    return os.path.join(root, rel)


def _spec_text(unit, root):
    """(text, problem, kind). kind is "missing" (a plan defect), "unreadable" (NO-DATA), or ""."""
    spec = unit.get("spec")
    if not isinstance(spec, str) or not spec:
        return (None, "the open unit names no spec", "missing")
    path = _under(root, spec)
    if path is None:
        return (None, "the repository root is not a text path, so the spec %s cannot be read" % spec, "unreadable")
    if not os.path.isfile(path):
        return (None, "the spec file %s is not on disk" % spec, "missing")
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        return (raw.decode("utf-8"), "", "")
    except (OSError, UnicodeDecodeError) as exc:
        return (None, "the spec file %s cannot be read (%s)" % (spec, exc), "unreadable")


def _done_integrity(unit, uid):
    """ADVISORY only: a DONE unit is never admitted, so these are the nobody was told half of row 74."""
    out = []
    remains = unit.get("remains")
    if isinstance(remains, str) and remains.strip().upper().startswith("NOT BUILT"):
        out.append(_finding(uid, "-", "DONE-INTEGRITY", "ADVISORY",
                            "the DONE unit remains starts NOT BUILT", "close it, or correct the state"))
    evidence = unit.get("evidence")
    if not isinstance(evidence, str) or "UNIT DONE" not in evidence:
        out.append(_finding(uid, "-", "DONE-INTEGRITY", "ADVISORY",
                            "no UNIT DONE closure line in the evidence", "run close_unit.py"))
        return out
    current = unit.get("done_check")
    current = current.strip() if isinstance(current, str) else ""
    m = re.search(r"UNIT DONE [^\n]*`([^`]+)` printed:", evidence)
    if m and m.group(1).strip() != current:
        out.append(_finding(uid, "-", "DONE-INTEGRITY", "ADVISORY",
                            "the closure ran a different command than the unit done_check today",
                            "re-run close_unit.py so the receipt matches the check"))
    if "printed: no result line" in evidence:
        out.append(_finding(uid, "-", "DONE-INTEGRITY", "ADVISORY",
                            "the closure quoted no result line", "re-run close_unit.py and keep its result line"))
    return out


def _inherited_red(unit, plan, uid):
    out = []
    entries = plan.get("inherited_reds") if isinstance(plan, dict) else None
    if not isinstance(entries, list):
        return out
    try:
        blob = json.dumps(unit, sort_keys=True, default=str)
    except (TypeError, ValueError):
        blob = ""
    for entry in entries:
        if not isinstance(entry, dict):
            out.append(_finding(uid, "-", "INHERITED-RED", "NO-DATA",
                                "an inherited_reds entry is not a record", "fix the plan inherited_reds list"))
            continue
        status = entry.get("status")
        if not isinstance(status, str):
            out.append(_finding(uid, "-", "INHERITED-RED", "NO-DATA",
                                "an inherited_reds entry has no readable status", "fix the plan"))
            continue
        if status.lower().startswith("closed"):
            continue
        suite = ""
        for key in ("suite", "path", "file", "name"):
            value = entry.get(key)
            if isinstance(value, str) and value:
                suite = value
                break
        if suite and suite in blob:
            out.append(_finding(uid, "-", "INHERITED-RED", "BLOCKING",
                                "the inherited red %s is not closed" % suite,
                                "close it, or remove this unit dependence on it"))
    return out


RULE_SHARED = "SHARED-FILE"


def _edge_list(unit, key):
    """(ids, ok). ok is False when `key` is present and is not a list of non empty text ids; absent is []."""
    value = unit.get(key)
    if value is None:
        return ([], True)
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        return ([], False)
    return (list(value), True)


def after_closure(plan: dict) -> Dict[str, set]:
    """unit id to the set of unit ids it transitively comes after (after and depends_on), cycles tolerated (a
    cycle member sees the others, never itself); a plan whose units are not records raises ValueError. A malformed
    edge list contributes no edges here: shared_file_findings is what reports it."""
    units = plan.get("units") if isinstance(plan, dict) else None
    if not isinstance(units, list) or not all(isinstance(u, dict) for u in units):
        raise ValueError("after_closure: the plan units are not a list of records")
    edges = {}
    for unit in units:
        uid = unit.get("id")
        if isinstance(uid, str) and uid:
            edges.setdefault(uid, set()).update(_edge_list(unit, "after")[0] + _edge_list(unit, "depends_on")[0])
    closure = {}
    for uid in edges:
        seen, todo = set(), list(edges[uid])
        while todo:
            nxt = todo.pop()
            if nxt in seen:
                continue
            seen.add(nxt)
            todo.extend(edges.get(nxt, ()))
        seen.discard(uid)
        closure[uid] = seen
    return closure


def shared_file_findings(plan: dict) -> List[dict]:
    """One BLOCKING finding per (path, unit A, unit B) where the path is in both owns, neither unit is DONE, and
    neither is in the other's after closure; the fix names the after to add. Paths under docs/plan/specs/ are
    exempt (the planner's own files). A malformed owns (or after, depends_on, id) is one NO-DATA finding for that
    unit and the pairs it is in are not judged. The finding is filed under the unit that comes first in the plan
    and carries "pair" (both ids) so admission can match either. A plan that is not a record with a units list
    raises ValueError; entries of units that are not records are the per unit rules' to report, not judged here."""
    units = plan.get("units") if isinstance(plan, dict) else None
    if not isinstance(units, list):
        raise ValueError("shared_file_findings: the plan units are not a list")
    records = [u for u in units if isinstance(u, dict) and u.get("state") != "DONE"]
    closure = after_closure({"units": [u for u in units if isinstance(u, dict)]})
    out, owners, bad = [], {}, set()
    for index, unit in enumerate(records):
        uid = unit.get("id")
        owns = unit.get("owns")
        problem = ""
        if not (isinstance(uid, str) and uid):
            uid, problem = "-", "the unit id is not text"
        elif owns is not None and (not isinstance(owns, list) or not all(isinstance(o, str) and o.strip() for o in owns)):
            problem = "the unit owns is not a list of non empty text entries"
        elif not (_edge_list(unit, "after")[1] and _edge_list(unit, "depends_on")[1]):
            problem = "the unit after or depends_on is not a list of unit ids"
        if problem:
            bad.add(index)
            out.append(dict(_finding(uid, "-", RULE_SHARED, "NO-DATA", problem + ", so its shared files were not judged",
                                     "fix the unit in the plan"), pair=[uid]))
            continue
        for path in dict.fromkeys(os.path.normpath(o) for o in (owns or [])):
            if not path.startswith("docs/plan/specs/"):
                owners.setdefault(path, []).append(index)
    for path, idxs in sorted(owners.items()):
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                first, second = records[idxs[a]], records[idxs[b]]
                fid, sid = first["id"], second["id"]
                if sid in closure.get(fid, ()) or fid in closure.get(sid, ()):
                    continue
                out.append(dict(_finding(fid, "-", RULE_SHARED, "BLOCKING",
                                         "%s is in the owns of both %s and %s with no after between them" % (path, fid, sid),
                                         "add %s to the after of %s (or the reverse), so the two never build at once"
                                         % (fid, sid)), pair=[fid, sid]))
    return out


RULE_AFTER = "AFTER-ORDER"


def after_order_findings(plan: dict) -> List[dict]:
    """AFTER-ORDER (2026-10-14): a unit's `after` must be a list of unit ids, every one a unit the plan holds and every
    one also in its `depends_on`, because the pool holds a unit on depends_on and two release plans in a row were
    written with `after` filled and `depends_on` empty (the order the plan states was not the order enforced). The pool
    reads the union since the same day (runner_pool.depends_held); this is the plan side, so the two files agree and a
    reader of either sees one order. BLOCKING, never advisory: a plan whose order is stated twice and disagrees has no
    order. DONE units are not judged (they are not built again), a unit whose id is not text is the per unit rules'.
    Each finding carries "pair" ([uid]) so admission can match it. A plan whose units are not a list raises ValueError."""
    units = plan.get("units") if isinstance(plan, dict) else None
    if not isinstance(units, list):
        raise ValueError("after_order_findings: the plan units are not a list")
    held = {u.get("id") for u in units if isinstance(u, dict) and isinstance(u.get("id"), str)}
    out = []
    for unit in units:
        uid = unit.get("id") if isinstance(unit, dict) else None
        if not (isinstance(uid, str) and uid) or unit.get("state") == "DONE":
            continue
        after, ok = _edge_list(unit, "after")
        fix = "copy the unit's after list into its depends_on in the plan, so the pool and the lint read one order"
        if not ok:
            out.append(dict(_finding(uid, "-", RULE_AFTER, "BLOCKING",
                                     "the unit after is not a list of unit ids (text entries)", fix), pair=[uid]))
            continue
        deps = unit.get("depends_on")
        deps = deps if isinstance(deps, list) else []
        missing = [a for a in dict.fromkeys(after) if a not in deps]
        if missing:
            out.append(dict(_finding(uid, "-", RULE_AFTER, "BLOCKING",
                                     "the unit after names %s, which its depends_on does not" % ", ".join(missing), fix),
                            pair=[uid]))
        absent = [a for a in dict.fromkeys(after) if a not in held]
        if absent:
            out.append(dict(_finding(uid, "-", RULE_AFTER, "BLOCKING",
                                     "the unit after names %s, which the plan does not hold" % ", ".join(absent),
                                     "correct the name, or remove it from the unit's after and depends_on in the plan"),
                            pair=[uid]))
    return out


def _test_targets(command):
    """[(relative file path, class name or None)] for every test the command names."""
    out = []
    if not isinstance(command, str):
        return out
    for m in re.finditer(r"([A-Za-z0-9_][A-Za-z0-9_./-]*test_[A-Za-z0-9_]*\.py)", command):
        out.append((m.group(1), None))
    for m in re.finditer(r"-m\s+unittest\s+([A-Za-z_][\w.]*)", command):
        parts = [p for p in m.group(1).split(".") if p]
        # module.Class.test_method: the module is every part before the first capitalised one, the class is that
        # part, and a method after it is not a file (probe 2026-09-30: the method form read as a path and BLOCKED).
        klass = None
        for i, part in enumerate(parts):
            if i > 0 and part[:1].isupper():
                klass = part
                parts = parts[:i]
                break
        if parts:
            out.append((".".join(parts).replace(".", os.sep) + ".py", klass))
    return out


def _is_string(node, value):
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value == value


def _is_main_guard(node):
    test = node.test
    if not isinstance(test, ast.Compare) or len(test.comparators) != 1:
        return False
    if not isinstance(test.left, ast.Name) or test.left.id != "__name__":
        return False
    return _is_string(test.comparators[0], "__main__")


def _func_name(call):
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _parse_findings(uid, sub, path, source, klass):
    """A named test that does not parse as 3.9, defines no TestCase test method before its main guard, or
    does not define a class named on the command line before it."""
    out = []
    try:
        tree = ast.parse(source, filename=path, feature_version=(3, 9))
    except SyntaxError as exc:
        out.append(_finding(uid, sub, "TEST-FOUND", "BLOCKING",
                            "the test file %s does not parse as Python 3.9 (%s)" % (path, exc),
                            "fix the syntax, or drop a construct 3.9 cannot parse"))
        return out
    guard = len(tree.body)
    for i, node in enumerate(tree.body):
        if isinstance(node, ast.If) and _is_main_guard(node):
            guard = i
            break
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            if _func_name(node.value) == "main":
                guard = i
                break
    defined = set()
    cases = set()  # classes that are a TestCase, directly or through a base defined earlier in the same file
    tested = False
    for i, node in enumerate(tree.body):
        if i >= guard:
            break
        if not isinstance(node, ast.ClassDef):
            continue
        defined.add(node.name)
        is_test_case = False
        for base in node.bases:
            if isinstance(base, ast.Attribute) and base.attr == "TestCase":
                is_test_case = True
            elif isinstance(base, ast.Name) and (base.id == "TestCase" or base.id in cases):
                is_test_case = True
        if is_test_case:
            cases.add(node.name)
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name.startswith("test"):
                    tested = True
                    break
    if not tested:
        out.append(_finding(uid, sub, "TEST-FOUND", "BLOCKING",
                            "the test file %s defines no unittest.TestCase test method before its main guard" % path,
                            "define the test class above unittest.main()"))
    if klass and klass not in defined:
        out.append(_finding(uid, sub, "TEST-FOUND", "BLOCKING",
                            "the class %s named on the command line is not defined before the main guard of %s" % (klass, path),
                            "name a class that runs, or move it above the main guard"))
    return out


def _named_test_findings(uid, sub, command, sec, root):
    """A named test that is absent and not labelled NEW, or that cannot be read."""
    out = []
    labels = {}
    try:
        for path, label, _, _ in labelled_paths(sec):
            # a later bare mention of a path never undoes its label (FX-31.5, 2026-10-03: a mutation line naming the
            # NEW test bare made it read as missing)
            if label or os.path.normpath(path) not in labels:
                labels[os.path.normpath(path)] = label
    except ValueError:
        labels = {}
    new_basenames = set([os.path.basename(p) for p in labels if labels[p] == "NEW"])
    for path, klass in _test_targets(command):
        key = os.path.normpath(path)
        full = _under(root, path)
        if full is None:
            out.append(_finding(uid, sub, "TEST-FOUND", "NO-DATA",
                                "the repository root is not a text path, so the test %s cannot be read" % path,
                                "pass a real root"))
            continue
        if not os.path.isfile(full):
            if labels.get(key) == "NEW" or (os.sep not in key and os.path.basename(key) in new_basenames):
                continue
            out.append(_finding(uid, sub, "TEST-FOUND", "BLOCKING",
                                "the command names the test file %s, which is not on disk" % path,
                                "write the file, or label it NEW in the section"))
            continue
        try:
            with open(full, "rb") as fh:
                source = fh.read().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            out.append(_finding(uid, sub, "TEST-FOUND", "BLOCKING",
                                "the test file %s cannot be read (%s)" % (path, exc), "fix the file"))
            continue
        out.extend(_parse_findings(uid, sub, path, source, klass))
    return out


RULE_NAMED_TEST = "NAMED-TEST"


def _named_test_rule_findings(uid, sub, spec_text, root):
    """NAMED-TEST (2026-10-14): a test file the section names that the loop's brief check (B2 IMPORTS SHOWN) would have
    to open but cannot find WITHHOLDS the sub unit at loop time, after a paid run has started (LL1.c and WR1.a). The
    decision is brief_check.tests_to_read, the very function B2 calls, with this tree's root as the place a path exists
    in, so the writer side and the loop side cannot disagree. FAIL DIRECTION: a section the reader refuses is BLOCKING."""
    section = brief_check.section_of(spec_text, sub)
    if section is None:
        return []   # no section: SECTION-CHECK already says so
    base = root if isinstance(root, str) and root else None

    def exists(path):
        return base is not None and os.path.lexists(os.path.join(base, path))

    try:
        wanted = brief_check.tests_to_read(section, exists=exists)
    except ValueError as exc:
        return [_finding(uid, sub, RULE_NAMED_TEST, "BLOCKING", "the section cannot be read for its tests (%s)" % exc,
                         "fix the section text")]
    return [_finding(uid, sub, RULE_NAMED_TEST, "BLOCKING",
                     "the section names the test file %s, which is not in the tree and not labelled NEW there, so the "
                     "loop's brief check would WITHHOLD this sub unit" % path,
                     "name the test by its full path, label it NEW, or describe a fixture file in words, not as a "
                     "backticked tree path")
            for path in dict.fromkeys(wanted) if not exists(path)]


def _section_findings(uid, sub, unit, spec_text, root):
    """The rules for ONE open section: its check, the tests it names, and the touch set it names."""
    out = []
    sec = _section(spec_text, sub)
    if not sec:
        out.append(_finding(uid, sub, "SECTION-CHECK", "BLOCKING",
                            "the spec names no section for this sub unit", "add the section to the spec"))
        return out
    command = _section_done_check(spec_text, sub)
    if command is None:
        out.append(_finding(uid, sub, "SECTION-CHECK", "BLOCKING",
                            "the section names no done check", "add one python3 test command to the section"))
    elif not grade_build.OK_CMD.match(command):
        out.append(_finding(uid, sub, "SECTION-CHECK", "BLOCKING",
                            "the section done check is not one command the landing gate runs: %s" % _short(command),
                            "use one python3 [-B] test command, not a chain or a fence"))
    else:
        out.extend(_named_test_findings(uid, sub, command, sec, root))
    paths = named_paths(sec)
    if not paths:
        out.append(_finding(uid, sub, "TOUCH", "BLOCKING",
                            "the section names no backticked path, so its touch set is unknown",
                            "name every file the section touches, backticked"))
    out.extend(_named_test_rule_findings(uid, sub, spec_text, root))
    out.extend(hard_findings(uid, sub, unit, spec_text))
    return out


# LL1.a PROTECTED-TOUCH: a spec that tells a loop build to write a path the landing refuses (or regenerates itself)
# is refused before the spend, not after the paid session. Measured 2026-10-12/13: 3 of 5 post-session refusals
# were builds that wrote scripts/check_all.sh. The pool's own reader of a write set (runner_pool.write_set) is
# re-implemented here because runner_pool imports this module; test_plan_lint_protected keeps the two equal.
# FAIL DIRECTION: a section with no Built by line is loop built, wrong typed input is NO-DATA; both block.
PROTECTED_EXTRA = ("bundle/", "system.md", "scripts/check_all.sh")   # the loop's GENERATED set, case folded
GENERATED_BASENAMES = ("checksums", "checksums.sha256")             # per product manifests, regenerated by checksums.sh
RULE_PROTECTED = "PROTECTED-TOUCH"
RULE_OWNS = "OWNS"
_BUILT_BY = re.compile(r"(?im)^[ \t]*Built by:[ \t]*(.*)$")
_BY_LOOP = re.compile(r"(?i)brother[.\s-]?loop")
_BY_SESSION = re.compile(r"(?i)\bsession\b")
_WRITE_HEAD = re.compile(r"^(?:Owns|Files):", re.M)
_READ_ONLY = re.compile(r"(?i)read[ -]only|unchanged|^\W*reads\b")
_EDIT_VERB = re.compile(
    r"(?i)\b(?:edit(?:s|ed|ing)?|chang(?:e|es|ed|ing)|modif(?:y|ies|ied|ying)|add(?:s|ed|ing)?|"
    r"append(?:s|ed|ing)?|register(?:s|ed|ing)?|patch(?:es|ed|ing)?|rewrit(?:e|es|ing|ten)|rewrote|"
    r"updat(?:e|es|ed|ing)|regenerat(?:e|es|ed|ing)|writ(?:e|es|ing|ten)|wrote)\b")
_NEGATION = re.compile(r"(?i)\b(?:never|not|no|without)\b")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s|\n\s*\n")


def built_by_loop(section: str) -> bool:
    """False only when the section has a Built by line that names the session and not Brother.Loop. A missing line, an
    empty section, a non text section and a builder the lint does not recognise are loop built: doubt blocks."""
    if not isinstance(section, str):
        return True
    m = _BUILT_BY.search(section)
    return True if m is None else bool(_BY_LOOP.search(m.group(1)) or not _BY_SESSION.search(m.group(1)))


def write_paths(section: str) -> List[str]:
    """Normalised paths of the section's Owns: or Files: paragraph (to a blank line or a Reads: label), minus a path
    whose own annotation (the text up to the next path) says read only, unchanged, or opens with reads. Order kept."""
    if not isinstance(section, str):
        return []
    out = []
    for head in _WRITE_HEAD.finditer(section):
        para = re.split(r"\n\s*\n|\bReads?:", section[head.end():], maxsplit=1)[0]
        hits = list(_SPEC_PATH.finditer(para))
        for i, hit in enumerate(hits):
            note = para[hit.end():hits[i + 1].start() if i + 1 < len(hits) else len(para)]
            if not _READ_ONLY.search(note):
                out.append(os.path.normpath(hit.group(1)))
    return list(dict.fromkeys(out))


def _read_spans(section):
    """[(start, end)] of every Reads: paragraph, and the (start, end) of the annotation that follows each path."""
    spans = []
    for m in re.finditer(r"\bReads?:", section):
        end = re.search(r"\n\s*\n", section[m.end():])
        spans.append((m.start(), len(section) if end is None else m.end() + end.start()))
    hits = list(_SPEC_PATH.finditer(section))
    for i, hit in enumerate(hits):
        stop = hits[i + 1].start() if i + 1 < len(hits) else len(section)
        if _READ_ONLY.search(section[hit.end():min(stop, hit.end() + 80)]):
            spans.append((hit.start(), hit.end()))
    return spans


def prose_touch_paths(section: str) -> List[str]:
    """Backticked paths that follow, within 60 characters and the same sentence, an edit verb, unless that stretch
    holds never, not, no or without. A path in a Reads paragraph or annotated read only is not a touch."""
    if not isinstance(section, str):
        return []
    skip = _read_spans(section)
    out = []
    for m in _SPEC_PATH.finditer(section):
        if any(a <= m.start() < b for a, b in skip):
            continue
        window = section[max(0, m.start() - 60):m.start()]
        cuts = list(_SENTENCE_END.finditer(window))
        if cuts:
            window = window[cuts[-1].end():]
        if _EDIT_VERB.search(window) and not _NEGATION.search(window):
            out.append(os.path.normpath(m.group(1)))
    return list(dict.fromkeys(out))


def protected_reason(path: str) -> str:
    """"" for a path a loop build may write, else a short reason. The adapter's own grade_build.dest decides (read from
    the module at call time, never a copy of its lists), then the loop's generated set."""
    if not isinstance(path, str) or not path:
        return "not a path"
    full, why = grade_build.dest(os.sep + "ll1-no-such-root", path)
    if full is None:
        shadow = re.search(r"\((.*)\)$", why)
        if why.startswith("path is one the landing trusts"):
            return "a path the landing trusts" + (" (%s)" % shadow.group(1) if shadow else "")
        return "a path the grader refuses (%s)" % why.split(":")[0]
    rel = os.path.normpath(path).casefold()
    if any(rel == e or (e.endswith("/") and rel.startswith(e)) for e in PROTECTED_EXTRA) \
            or os.path.basename(rel) in GENERATED_BASENAMES:
        return "a generated file the landing regenerates itself"
    return ""


def _owns_findings(uid: str, sub: str, unit: dict, section: str) -> List[dict]:
    """LL1.b OWNS: a path the section touches that is not in the unit's owns. Every section, whoever builds it."""
    owns = unit.get("owns")
    # NO OWNS IS NEVER CLEAN (2026-10-14): a unit with no owns (absent, empty, or not a list of non empty text) leaves
    # the rule nothing to judge a section against, so it used to read clean at admission. It BLOCKS, whichever way the
    # list is wrong; the old ADVISORY and its REPORT_ABSENT_OWNS knob are gone (the fixtures carry owns now).
    if not isinstance(owns, list) or not owns or not all(isinstance(o, str) and o.strip() for o in owns):
        return [_finding(uid, sub, RULE_OWNS, "BLOCKING",
                         "the unit names no owns, or its owns is not a non empty list of non empty text entries, "
                         "so the rule cannot judge its sections",
                         "add the paths the unit writes to its owns list in the plan")]
    exact = {os.path.normpath(o) for o in owns if not o.endswith("/")}
    prefixes = [os.path.normpath(o) + "/" for o in owns if o.endswith("/")]
    out = []
    for path in list(dict.fromkeys(write_paths(section) + prose_touch_paths(section))):
        if path.startswith("docs/plan/specs/") or path in exact or any(path.startswith(p) for p in prefixes):
            continue
        out.append(_finding(uid, sub, RULE_OWNS, "BLOCKING",
                            "%s is named by the section but is not in the unit's owns" % path,
                            "add the path to the unit's owns in the plan, or remove it from the section; "
                            "reads belong on a Reads line"))
    return out


def hard_findings(uid: str, sub: str, unit: dict, spec_text: str) -> List[dict]:
    """PROTECTED-TOUCH (loop built sections only) then OWNS for ONE section. The one place the rules live: the lint
    report and admission reach it through _section_findings. A section that cannot be found is SECTION-CHECK's."""
    uid = uid if isinstance(uid, str) and uid else "-"
    sub = sub if isinstance(sub, str) and sub else "-"
    if not isinstance(unit, dict) or not isinstance(spec_text, str):
        return [_finding(uid, sub, RULE_PROTECTED, "NO-DATA",
                         "the unit or the spec text is not readable (%s, %s)" % (type(unit).__name__, type(spec_text).__name__),
                         "fix the plan or the spec file")]
    section = _section(spec_text, sub)
    if not section:
        return []
    out = []
    if built_by_loop(section):
        for path in list(dict.fromkeys(write_paths(section) + prose_touch_paths(section))):
            reason = protected_reason(path)
            if reason:
                out.append(_finding(uid, sub, RULE_PROTECTED, "BLOCKING",
                                    "%s is %s, which a loop build may not write" % (path, reason),
                                    "remove the path from the section: a new test is registered in the check battery by "
                                    "the landing itself (land_batch.register), and bundle/runtime is regenerated by "
                                    "python3 scripts/bundle_runtime.py; or build this sub unit on the session route"))
    out.extend(_owns_findings(uid, sub, unit, section))
    return out


# FX-13.4: rule EV. The runner's own gate stops the spend before round 0 and tells nobody; this is the told half. The
# figures are the runner's (ev_gate.round_cost_from_env, ev_gate.landing_value), the history its ledger, read only once
# the ledger is shown readable: an unreadable ledger is NO-DATA, never the fresh prior history() would answer.
LEDGER_PATH = "~/.claude/evidence/unit-ledger.jsonl"


def _ledger_problem(path):
    """"" when the ledger is absent (a cold start: the prior is right) or readable as utf-8 text, else the reason."""
    if not isinstance(path, str) or not path:
        return "the ledger path is not text"
    if not os.path.lexists(path):
        return ""
    try:
        with open(path, "rb") as fh:
            fh.read().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return "the ledger %s cannot be read (%s)" % (path, type(exc).__name__)
    return ""


def _ev_findings(uid, sub, open_count, unit, root, ledger, env):
    """[] or one finding: ADVISORY when should_continue refuses the next round, NO-DATA when a figure is unreadable."""
    def finding(severity, text):
        fix = ("the owner answers it in FX-50 (sets BROTHER_VALUE_PER_LANDING), or the sub unit is parked"
               if severity == "ADVISORY" else "make the ledger and BROTHER_VALUE_PER_LANDING readable")
        return [_finding(uid, sub, "EV", severity, text, fix)]
    if not isinstance(sub, str) or not sub:
        return finding("NO-DATA", "the sub unit id is not text")
    path = os.path.expanduser(LEDGER_PATH) if ledger is None else ledger
    problem = _ledger_problem(path)
    if problem:
        return finding("NO-DATA", problem)
    try:
        value = ev_gate.landing_value(open_count, env)
    except ValueError as exc:
        return finding("NO-DATA", "the landing value cannot be read (%s)" % exc)
    spec = unit.get("spec")
    try:
        since = os.path.getmtime(_under(root, spec) or "") if isinstance(spec, str) and spec else 0.0
    except (OSError, TypeError, ValueError):
        since = 0.0
    rounds, passes = ev_gate.history(sub, ledger_path=path, since=since)
    go, reason = ev_gate.should_continue(rounds, passes, round_cost=ev_gate.round_cost_from_env(env), value=value, env=env)
    if go:
        return []
    return finding("ADVISORY", "no attemptable round for %s after %d rounds (%d passing): \"%s\"; FX-50 is where the "
                               "owner answers it" % (sub, rounds, passes, reason))


def lint_unit(unit, plan, root=".", subs=None, council=None, council_dir=None, ledger=None, env=None):
    """The findings for one unit, as a list. An input that is not a record is answered with a NO-DATA finding."""
    if not isinstance(unit, dict):
        return [_finding("-", "-", "UNIT-CHECK", "NO-DATA",
                         "the unit is not a record (%s)" % type(unit).__name__, "fix the plan")]
    uid = unit.get("id")
    uid = uid if isinstance(uid, str) and uid else "-"
    findings = []
    if unit.get("state") == "DONE":
        findings.extend(_done_integrity(unit, uid))
        return findings
    command = unit.get("done_check")
    steps = screen_done_check(command)
    if steps is None:
        findings.append(_finding(uid, "-", "UNIT-CHECK", "BLOCKING",
                                 "the unit done_check is not a shape the closer will run: %s" % _short(command),
                                 "replace it with one python3 test command"))
    findings.extend(_inherited_red(unit, plan, uid))
    if subs is None:
        open_subs, problem = _open_sub_ids(unit)
    elif isinstance(subs, list) and all(isinstance(s, str) for s in subs):
        open_subs, problem = (list(subs), "")
    else:
        open_subs, problem = ([], "the sub unit list is not a list of ids")
    if problem:
        findings.append(_finding(uid, "-", "SECTION-CHECK", "NO-DATA", problem, "fix the plan"))
    text, spec_problem, spec_kind = _spec_text(unit, root)
    if steps is not None:
        findings.extend(_named_test_findings(uid, "-", command, text or "", root))
    if council is not None:
        findings.extend(_council_findings(uid, text, council, council_dir))
    if open_subs:
        if spec_kind == "missing":
            findings.append(_finding(uid, "-", "SPEC-FILE", "BLOCKING", spec_problem,
                                     "create the spec file or correct the path"))
        elif spec_kind == "unreadable":
            findings.append(_finding(uid, "-", "SPEC-FILE", "NO-DATA", spec_problem, "fix the spec file"))
        if ledger is not None:
            findings.extend(_ev_findings(uid, open_subs[0], len(open_subs), unit, root, ledger, env))
    return findings


def lint_plan(plan_path, root=".", scope=".", council_path=None, council_dir=None, ledger=None, env=None,
              premise=False, runner=None):
    """{"findings": [...], "read": {...}, "not_run": [...]}."""
    findings = []
    read = {"units": 0, "sections": 0}
    not_run = ["COUNCIL", "COUNCIL-ABSENT", "EV", "ROLE-CONTENT", "PREMISE", "SEEN-RED"]
    if ledger is not None:
        not_run.remove("EV")
    try:
        if not isinstance(plan_path, str) or not plan_path:
            raise ValueError("the plan path is not text")
        with open(plan_path, "rb") as fh:
            raw = fh.read()
        plan = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return {"findings": [], "read": read, "not_run": not_run, "exit": 3,
                "reason": "%s: %s" % (type(exc).__name__, exc)}
    if not isinstance(plan, dict) or not isinstance(plan.get("units"), list):
        return {"findings": [], "read": read, "not_run": not_run, "exit": 3, "reason": "the units are not a list"}
    if scope is None:
        scope = "."
    if not isinstance(scope, str):
        findings.append(_finding("-", "-", "SCOPE", "NO-DATA", "the scope is not text", "fix the scope"))
        scope_re = re.compile(r"\A\Z")
    else:
        try:
            scope_re = re.compile(scope)
        except re.error as exc:
            findings.append(_finding("-", "-", "SCOPE", "NO-DATA",
                                     "the scope is not a regular expression (%s)" % exc, "fix the scope"))
            scope_re = re.compile(r"\A\Z")
    findings.extend(f for f in shared_file_findings(plan) if scope_re.search(f["unit"]))
    findings.extend(f for f in after_order_findings(plan) if scope_re.search(f["unit"]))
    for unit in plan["units"]:
        if not isinstance(unit, dict):
            findings.append(_finding("-", "-", "UNIT-CHECK", "NO-DATA", "a plan unit is not a record", "fix the plan"))
            continue
        uid = unit.get("id")
        uid = uid if isinstance(uid, str) and uid else "-"
        if not scope_re.search(uid):
            continue
        read["units"] += 1
        if unit.get("state") == "DONE":
            findings.extend(lint_unit(unit, plan, root=root, subs=[], env=env))
            continue
        findings.extend(lint_unit(unit, plan, root=root, ledger=ledger, env=env))
        open_subs, problem = _open_sub_ids(unit)
        if problem:
            continue
        text, spec_problem, kind = _spec_text(unit, root)
        if kind:
            continue
        for sub in open_subs:
            read["sections"] += 1
            findings.extend(_section_findings(uid, sub, unit, text, root))
            findings.extend(_section_new_and_screen(uid, sub, unit, _section_by_sub(text, sub) or "", root))
    return {"findings": findings, "read": read, "not_run": not_run, "exit": 0}


# FX-13.3: COUNCIL, COUNCIL-ABSENT and ROLE-CONTENT. The verdict file and the prompts are the ones spec_council.py
# writes: one entry per unit id, and every seat's prompt embeds the whole spec text that seat judged, so when no
# prompt holds the current text the verdict is about an older spec. The roles file is the one loop_roles.py reads.
# loop_roles is not imported: the grader's screen refuses it as an import of a build, so _roles_path and
# CONTENT_CLASSES mirror loop_roles.roles_path and loop_roles.ORDER, and a change there is made here too.
COUNCIL_PATH = "~/.claude/evidence/spec-council.json"
COUNCIL_DIR = "~/.claude/evidence/spec-council"
_COUNCIL_HELD = ("FIX-FIRST", "DO-NOT-BUILD")
_COUNCIL_KNOWN = _COUNCIL_HELD + ("DESIGN-CLEAR", "NO-DATA")
CONTENT_CLASSES = ("public", "internal", "private")
_ROUTER_CLASSES = {"PUBLIC": "public", "INTERNAL": "internal", "PRIVATE": "private"}
# The roles with a run time call site under scripts/loop: (role, file, how that site passes its content class).
# The orchestrator and the documenter act outside the loop and have none; they are checked for a known class only.
ROLE_SITES = (
    ("worker", "scripts/loop/unit_runner.py", "sensitivity"),
    ("adversary", "scripts/loop/probe_wave.py", "sensitivity"),
    ("checker", "scripts/loop/check_wave.py", "call_one"),
    ("finisher", "scripts/loop/finish_run.py", "call_one"),
)


def load_council(path=None):
    """(entries, problem): the verdict mapping keyed by unit id and "", or None and why it cannot be read. A missing,
    unreadable, undecodable or non object file is never an empty mapping, which would read as "no verdict"."""
    if path is None:
        path = os.path.expanduser(COUNCIL_PATH)
    if not isinstance(path, str) or not path:
        return (None, "the council path is not a path (%s)" % type(path).__name__)
    try:
        with open(path, "rb") as fh:
            data = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        return (None, "the council file %s cannot be read (%s)" % (path, type(exc).__name__))
    if not isinstance(data, dict):
        return (None, "the council file %s is not a JSON object" % path)
    return (data, "")


def _council_findings(uid, text, council, council_dir):
    """COUNCIL and COUNCIL-ABSENT for one open unit. `council` is the loaded mapping, or a path load_council reads;
    `text` is the unit's current spec text. FIX-FIRST, DO-NOT-BUILD and NO-DATA hold with every blocker claim; a
    verdict no prompt of which holds the current text is STALE; a verdict that cannot be matched is NO-DATA."""
    def nodata(why):
        return _finding(uid, "-", "COUNCIL", "NO-DATA", why, "re-run the spec council on this unit's current spec")
    if isinstance(council, str):
        council, problem = load_council(council)
        if problem:
            return [nodata(problem)]
    if not isinstance(council, dict):
        return [nodata("the council verdicts are not a mapping (%s)" % type(council).__name__)]
    if uid == "-" or uid in (".", "..") or "/" in uid or os.sep in uid:
        return [nodata("the unit id %r cannot name a council entry" % uid)]
    if uid not in council:
        return [_finding(uid, "-", "COUNCIL-ABSENT", "ADVISORY", "no spec council verdict exists for this unit",
                         "run the spec council on its spec")]
    entry = council[uid]
    state = entry.get("state") if isinstance(entry, dict) else None
    if not isinstance(state, str) or state not in _COUNCIL_KNOWN:
        return [nodata("the council entry has no known state (%s)" % repr(state)[:60])]
    if council_dir is None:
        council_dir = os.path.expanduser(COUNCIL_DIR)
    if not isinstance(council_dir, str) or not council_dir:
        return [nodata("the council prompts directory is not a path (%s)" % type(council_dir).__name__)]
    findings = []
    if state in _COUNCIL_HELD or state == "NO-DATA":
        blockers = entry.get("blockers")
        blockers = blockers if isinstance(blockers, list) else []
        claims = [b["claim"] for b in blockers if isinstance(b, dict) and isinstance(b.get("claim"), str)]
        if state == "NO-DATA":
            said = "council NO-DATA: the council could not judge this spec"
        else:
            said = "council %s: %s" % (state, "; ".join(claims) or "no blocker claim was recorded")
        findings.append(_finding(uid, "-", "COUNCIL", "BLOCKING", said,
                                 "answer every claim in the spec, then re-run the spec council"))
    needle = text.strip().encode("utf-8") if isinstance(text, str) else b""
    if not needle:
        return findings   # no spec text: SPEC-FILE already reports a missing or unreadable spec
    prompts = os.path.join(council_dir, uid, "prompts")
    try:
        names = sorted(n for n in os.listdir(prompts) if n.endswith(".md"))
    except OSError as exc:
        return findings + [nodata("no council prompts can be read at %s (%s)" % (prompts, type(exc).__name__))]
    if not names:
        return findings + [nodata("no council prompt file in %s, so the verdict cannot be matched to a spec" % prompts)]
    for name in names:
        try:
            with open(os.path.join(prompts, name), "rb") as fh:
                blob = fh.read()
        except OSError as exc:
            return findings + [nodata("the council prompt %s cannot be read (%s)" % (name, type(exc).__name__))]
        if needle in blob:
            return findings
    findings.append(_finding(uid, "-", "COUNCIL", "BLOCKING",
                             "STALE: no council prompt holds the current spec text, so the %s verdict judged an older spec"
                             % state, "re-run the spec council on the current spec"))
    return findings


def _roles_path():
    """loop_roles.roles_path, mirrored: the environment, the tree's roles file, the home copy, the tracked fixture."""
    env = os.environ.get("BROTHER_LOOP_ROLES")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(os.path.dirname(os.path.dirname(here)), "docs", "plan", "loop-roles.json"),
              os.path.expanduser("~/.claude/loop-roles.json"),
              os.path.join(os.path.dirname(here), "fixtures", "loop-roles-fixture.json")):
        if os.path.isfile(p):
            return p
    return ""


def _site_value(node):
    """The content class one call site passes, or None when it is not a literal the lint can resolve."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return _ROUTER_CLASSES.get(node.attr)
    return None


def call_site_class(root, path, how):
    """(class, problem) for the call sites of one file: `how` is "call_one" (its 4th positional argument) or
    "sensitivity" (a job dict key, a keyword or a subscript assignment). A value that is not a string constant or a
    model_router class constant (R.PUBLIC, R.INTERNAL, R.PRIVATE), no site at all, or two sites that disagree is a
    problem: the class cannot be known, and an unknown class is never taken to agree."""
    if not isinstance(root, str) or not root or not isinstance(path, str) or not path:
        return (None, "the call site root or path is not a path")
    if not isinstance(how, str) or how not in ("call_one", "sensitivity"):
        return (None, "the call site kind %s is not call_one or sensitivity" % repr(how)[:40])
    try:
        with open(os.path.join(root, path), "rb") as fh:
            tree = ast.parse(fh.read().decode("utf-8"))
    except (OSError, ValueError, SyntaxError) as exc:
        return (None, "%s cannot be read or parsed (%s)" % (path, type(exc).__name__))
    values = []
    for node in ast.walk(tree):
        if how == "call_one":
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
                if name == "call_one":
                    values.append(_site_value(node.args[3]) if len(node.args) >= 4 else None)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "sensitivity":
                    values.append(_site_value(value))
        elif isinstance(node, ast.keyword) and node.arg == "sensitivity":
            values.append(_site_value(node.value))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)
                        and target.slice.value == "sensitivity"):
                    values.append(_site_value(node.value))
    if not values:
        return (None, "no %s call site found in %s" % (how, path))
    if None in values:
        return (None, "a %s call site in %s passes a value the lint cannot resolve" % (how, path))
    if len(set(values)) > 1:
        return (None, "the %s call sites in %s disagree (%s)" % (how, path, ", ".join(sorted(set(values)))))
    return (values[0], "")


def lint_roles(roles_path=None, root="."):
    """ROLE-CONTENT, plan wide (unit "-"): every role declares a content class in CONTENT_CLASSES and, for the roles
    in ROLE_SITES, the same class its call site passes. Unreadable input is a NO-DATA finding, never an empty list."""
    def finding(severity, why):
        return _finding("-", "-", "ROLE-CONTENT", severity, why,
                        "make the roles file and the call site name the same content class")
    if roles_path is None:
        roles_path = _roles_path()
    if not isinstance(roles_path, str) or not roles_path:
        return [finding("NO-DATA", "the roles file path is not a path (%s)" % type(roles_path).__name__)]
    if not isinstance(root, str) or not root:
        return [finding("NO-DATA", "the tree root is not a path (%s)" % type(root).__name__)]
    try:
        with open(roles_path, "rb") as fh:
            data = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        return [finding("NO-DATA", "the roles file %s cannot be read (%s)" % (roles_path, type(exc).__name__))]
    roles = data.get("roles") if isinstance(data, dict) else None
    if not isinstance(roles, dict) or not roles:
        return [finding("NO-DATA", "the roles file %s holds no roles mapping" % roles_path)]
    findings = []
    declared = {}
    for role, spec in roles.items():
        content = spec.get("content") if isinstance(spec, dict) else None
        if not isinstance(spec, dict) or "content" not in spec:
            findings.append(finding("BLOCKING", "role %s declares no content class" % role))
        elif not isinstance(content, str) or content not in CONTENT_CLASSES:
            findings.append(finding("BLOCKING", "role %s declares content %s, which is not one of %s"
                                    % (role, repr(content)[:40], ", ".join(CONTENT_CLASSES))))
        else:
            declared[role] = content
    for role, path, how in ROLE_SITES:
        if role not in roles:
            findings.append(finding("BLOCKING", "the roles file does not declare role %s, which has a call site in %s"
                                    % (role, path)))
            continue
        if role not in declared:
            continue   # its declaration is already a finding above
        value, problem = call_site_class(root, path, how)
        if problem:
            findings.append(finding("NO-DATA", "role %s: %s" % (role, problem)))
        elif value != declared[role]:
            findings.append(finding("BLOCKING", "role %s is declared %s in the roles file, but its call site in %s passes %s"
                                    % (role, declared[role], path, value)))
    return findings


def admission(plan, unit, sub, root=".", env=None, ledger=None, council=None, council_dir=None):
    """(lines, why) for the pool's per unit admission (FX-13.6): the unit's rules and the ONE section `sub` about to
    start, every input read fresh on every call (no receipt, no cache), so a unit added or edited after intake is linted
    by construction. Rule EV reads `ledger`, the unit ledger under HOME when None. Rule COUNCIL runs when `council` is
    given (the verdict file's path, or the loaded mapping), with the prompts under `council_dir`, COUNCIL_DIR when None.
    `why` is "LINT <RULE>: <text>" for the first BLOCKING or NO-DATA finding, else "". A sub unit id that is not text
    is NO-DATA, never a pass. START is changed to Start in `why` as in every line (loop_pass.sh counts a line carrying
    START as a started unit). It never raises: any exception becomes a NO-DATA line naming it."""
    try:
        if not isinstance(plan, dict) or not isinstance(unit, dict):
            lines = ["LINT NO-DATA: the plan or the unit is not a record"]
            return (lines, "LINT NO-DATA: the plan or the unit is not a record")
        if not (isinstance(sub, str) and sub):
            return (["LINT NO-DATA: the sub unit id is not text"], "LINT NO-DATA: the sub unit id is not text")
        uid = unit.get("id")
        uid = uid if isinstance(uid, str) and uid else "-"
        findings = lint_unit(unit, plan, root=root, council=council, council_dir=council_dir, env=env)
        text, problem, kind = _spec_text(unit, root)
        if kind == "unreadable" and not [f for f in findings if f["rule"] == "SPEC-FILE"]:
            findings.append(_finding(uid, sub, "SPEC-FILE", "NO-DATA", problem, "fix the spec file"))
        if isinstance(sub, str) and sub and text is not None:
            findings.extend(_section_findings(uid, sub, unit, text, root))
            findings.extend(_section_new_and_screen(uid, sub, unit, _section_by_sub(text, sub) or "", root))
            open_subs, problem = _open_sub_ids(unit)
            if not problem:
                findings.extend(_ev_findings(uid, sub, len(open_subs), unit, root, ledger, env))
        if isinstance(plan.get("units"), list):
            findings.extend(f for f in shared_file_findings(plan) if uid in f["pair"])
            findings.extend(f for f in after_order_findings(plan) if uid in f["pair"])
        lines = []
        why = ""
        for finding in findings:
            lines.append(_line(finding))
            if finding["severity"] in ("BLOCKING", "NO-DATA") and not why:
                why = ("LINT %s: %s" % (finding["rule"], finding["text"])).replace("START", "Start")
        return (lines, why)
    except Exception as exc:
        name = type(exc).__name__
        return (["LINT NO-DATA: %s" % name], "LINT NO-DATA: the lint could not run (%s)" % name)


def _argv(argv):
    if argv is None:
        return list(sys.argv[1:])
    if isinstance(argv, (list, tuple)) and all(isinstance(a, str) for a in argv):
        return list(argv)
    raise ValueError("main: argv must be None or a list of str, not %s" % type(argv).__name__)


def main(argv=None):
    """The report CLI. argparse stops the process on --help and on a bad argument by raising SystemExit; its
    code is RETURNED here, so a caller that calls main() directly never meets a raw SystemExit and the process
    exit code is unchanged (the entry point is sys.exit(main()))."""
    parser = argparse.ArgumentParser(description="Plan lint (RF-08) in report mode")
    parser.add_argument("--report", required=True, metavar="PLAN", help="the plan JSON to read")
    parser.add_argument("--scope", default=None, help="only unit ids matching this regular expression")
    parser.add_argument("--premise", action="store_true", help="run the premise suites (not run in this sub unit)")
    parser.add_argument("--council", default=None)
    parser.add_argument("--council-dir", dest="council_dir", default=None)
    parser.add_argument("--ledger", default=None)
    parser.add_argument("--roles", default=None)
    raw_argv = _argv(argv)
    try:
        args = parser.parse_args(raw_argv)
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else (0 if code is None else 2)
    scope = args.scope or os.environ.get("BROTHER_SCOPE") or "."
    report = lint_plan(args.report, root=".", scope=scope, council_path=args.council, council_dir=args.council_dir,
                       ledger=args.ledger or os.path.expanduser(LEDGER_PATH), env=os.environ, premise=args.premise)
    if report.get("exit") == 3 and not report["findings"]:
        print("LINT NO-DATA: the plan %s cannot be read (%s)" % (args.report, report.get("reason") or "unreadable"))
        return 3
    for finding in report["findings"]:
        print(_line(finding))
    blocking = len([f for f in report["findings"] if f["severity"] == "BLOCKING"])
    advisory = len([f for f in report["findings"] if f["severity"] == "ADVISORY"])
    nodata = len([f for f in report["findings"] if f["severity"] == "NO-DATA"])
    print("LINT SUMMARY blocking %d advisory %d no-data %d | units %d sections %d | scope %s | not run: %s"
          % (blocking, advisory, nodata, report["read"]["units"], report["read"]["sections"], scope,
             ", ".join(report["not_run"]) or "none"))
    return 3 if nodata else 0


if __name__ == "__main__":
    sys.exit(main())
