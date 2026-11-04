"""timing_hunt.py: find the tests whose verdict rests on a real clock.

WHY THIS EXISTS. On 2026-10-11 the 1.1.2 release check on a starved Linux runner failed one heartbeat test of
scripts/test_brother_run.py: a real timer thread at a 1.0 s interval, a 3 s worker, and an assertion that at least two
beats name the unit. The first beat fired before the unit was registered. That test was fixed, but nothing found the others
of its shape, and nothing stopped a new one from being pushed.

THE RULE. A test is FLAGGED when it holds a trigger AND a count or order assertion:
  heartbeat  a Heartbeat(...) whose interval is a positive number constant, with a .start() call in the same test
  timer      a threading.Timer(...) call
  thread     a threading.Thread(...) call together with a time.sleep(...) of a number below 5
  alarm      a signal.alarm(...) call
  select     a select.select(...) with a positive numeric timeout
A count or order assertion is an assert* call (or an assert statement) whose arguments hold len(...), .index(...) or
.count(...). A helper the test calls as self.<name>(...) or <name>(...), defined in the same class or module, is read as
part of the test (one level). A flagged test is COVERED when its source, or the line above its def, carries the comment
"# timing: deterministic because <seam>" with a seam word, or it drives time through a seam: a tick( call, a clock=
keyword, or a name containing fake_sleep.

CEILING, named: an interval computed at run time (interval=step) is not seen; the dynamic hunt
(hermetic_test_check.py --slow-host) is the net for it.

USE
  python3 scripts/timing_hunt.py PATH...                 one line per flagged test; exit 1 if any is UNCOVERED
  python3 scripts/timing_hunt.py --all                   the inventory with its method; exit 0
  python3 scripts/timing_hunt.py --all --write-baseline  writes scripts/timing_hunt_baseline.json (never hand edited)
Exit 2 (NO-DATA, the path named) when a file cannot be read or parsed: never clean.

THE BASELINE is the ratchet: it names today's uncovered tests so that only a NEW one is refused at the push
(hermetic_test_check.timing_results). It may only shrink. Footprint: reads files only; the baseline write is one temporary
file renamed into place.
"""
import argparse
import ast
import glob
import json
import os
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASELINE = "scripts/timing_hunt_baseline.json"
SCHEMA = "timing-hunt-baseline-v1"
MARKER = "# timing: deterministic because"
TRIGGERS = ("heartbeat", "timer", "thread", "alarm", "select")
SHAPES = ("scripts/test_*.py", "scripts/loop/test_*.py", "tests/**/test_*.py", "products/*/tools/test_*.py")
METHOD = ("method: ast over %s; flagged = a trigger (%s) plus an assert* holding len/.index/.count; covered = the marker "
          "comment, a tick( call, a clock= keyword or a fake_sleep name" % (", ".join(SHAPES), ", ".join(TRIGGERS)))


class Finding(object):
    """One flagged test."""

    def __init__(self, path, test, line, triggers, covered):
        self.path, self.test, self.line, self.triggers, self.covered = path, test, line, triggers, covered

    @property
    def id(self):
        return "%s::%s" % (self.path, self.test)

    def __repr__(self):
        return "Finding(%s, %s)" % (self.id, "COVERED" if self.covered else "UNCOVERED")


def _dotted(node):
    """'threading.Thread' for threading.Thread, 'Heartbeat' for Heartbeat, '' for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return head + "." + node.attr if head else node.attr
    return ""


def _number(node):
    """The value of a plain number constant (a bool is not one), else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    return None


def _arg(call, index, keyword):
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    return call.args[index] if len(call.args) > index else None


def _triggers(nodes):
    calls = [n for n in nodes if isinstance(n, ast.Call)]
    names = [_dotted(c.func) for c in calls]
    tails = [n.rsplit(".", 1)[-1] for n in names]
    found = []
    beats = [c for c, t in zip(calls, tails) if t == "Heartbeat" and (_number(_arg(c, 0, "interval")) or 0) > 0]
    if beats and "start" in tails:
        found.append("heartbeat")
    if "threading.Timer" in names:
        found.append("timer")
    sleeps = [_number(c.args[0]) for c, n in zip(calls, names) if n == "time.sleep" and c.args]
    if "threading.Thread" in names and any(s is not None and s < 5 for s in sleeps):
        found.append("thread")
    if "signal.alarm" in names:
        found.append("alarm")
    if any(n == "select.select" and (_number(_arg(c, 3, "timeout")) or 0) > 0 for c, n in zip(calls, names)):
        found.append("select")
    return found


def _counts(nodes):
    """True when an assert* call or an assert statement holds len(...), .index(...) or .count(...)."""
    for node in nodes:
        if isinstance(node, ast.Assert):
            inner = [node.test]
        elif isinstance(node, ast.Call) and _dotted(node.func).rsplit(".", 1)[-1].startswith("assert"):
            inner = list(node.args) + [kw.value for kw in node.keywords]
        else:
            continue
        for sub in inner:
            for n in ast.walk(sub):
                if isinstance(n, ast.Call) and (
                        (isinstance(n.func, ast.Name) and n.func.id == "len")
                        or (isinstance(n.func, ast.Attribute) and n.func.attr in ("index", "count"))):
                    return True
    return False


def _seamed(nodes):
    for node in nodes:
        if isinstance(node, ast.Call) and _dotted(node.func).rsplit(".", 1)[-1] == "tick":
            return True
        if isinstance(node, ast.keyword) and node.arg == "clock":
            return True
        for word in (getattr(node, "id", None), getattr(node, "attr", None), getattr(node, "arg", None),
                     getattr(node, "name", None)):
            if isinstance(word, str) and "fake_sleep" in word:
                return True
    return False


def _marked(lines, first, last):
    """The marker with a non empty seam word on any line from `first` (1 based, the line above the def included) to `last`."""
    for line in lines[max(first - 1, 0):last]:
        at = line.find(MARKER)
        if at >= 0 and line[at + len(MARKER):].strip():
            return True
    return False


def _tests(tree):
    """(qualified name, function node, helpers by name) for every test* function at module level or in a class."""
    module_funcs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            yield node.name, node, module_funcs
        elif isinstance(node, ast.ClassDef):
            methods = {n.name: n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            helpers = dict(module_funcs, **methods)
            for name, fn in methods.items():
                if name.startswith("test"):
                    yield node.name + "." + name, fn, helpers


def _body_nodes(fn, helpers):
    """Every node of `fn`, plus every node of a helper it calls by name or as self.<name> (one level)."""
    nodes = list(ast.walk(fn))
    for call in [n for n in nodes if isinstance(n, ast.Call)]:
        f = call.func
        name = (f.id if isinstance(f, ast.Name)
                else f.attr if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "self"
                else None)
        helper = helpers.get(name) if name else None
        if helper is not None and helper is not fn and not helper.name.startswith("test"):
            nodes.extend(ast.walk(helper))
    return nodes


def scan_text(path, text):
    # type: (str, str) -> List[Finding]
    """The flagged tests of one file. Raises SyntaxError (or ValueError for a NUL byte) for an unparsable file."""
    tree = ast.parse(text, filename=path)
    lines = text.splitlines()
    out = []
    for name, fn, helpers in _tests(tree):
        nodes = _body_nodes(fn, helpers)
        triggers = _triggers(nodes)
        if not triggers or not _counts(nodes):
            continue
        first = min([fn.lineno] + [d.lineno for d in fn.decorator_list]) - 1
        covered = _seamed(nodes) or _marked(lines, first, getattr(fn, "end_lineno", fn.lineno))
        out.append(Finding(path, name, fn.lineno, triggers, covered))
    return out


def scan_paths(paths, root):
    # type: (List[str], str) -> Tuple[List[Finding], List[str]]
    """(findings, unreadable paths). A file that cannot be read, decoded or parsed is listed, never read as clean."""
    found, bad = [], []
    for rel in paths:
        full = rel if os.path.isabs(rel) else os.path.join(root, rel)
        try:
            with open(full, encoding="utf-8") as fh:
                text = fh.read()
            found.extend(scan_text(rel, text))
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
            bad.append(rel)
    return found, bad


def test_files(root):
    # type: (str) -> List[str]
    """The scanned set, repository relative, sorted."""
    hits = set()
    for shape in SHAPES:
        for full in glob.glob(os.path.join(root, shape), recursive=True):
            if os.path.isfile(full):
                hits.add(os.path.relpath(full, root).replace(os.sep, "/"))
    return sorted(hits)


def inventory_files(root):
    # type: (str) -> List[str]
    """The test files holding at least one flagged test, covered or not. An unreadable file is listed too: the dynamic hunt
    then runs it, and its own run reports what it finds, rather than the file silently leaving the inventory."""
    found, bad = scan_paths(test_files(root), root)
    return sorted({f.path for f in found} | set(bad))


def baseline_ids(path):
    # type: (str) -> Optional[List[str]]
    """The baseline's test ids, or None when it is missing, unreadable, not this schema, or holds a non string id."""
    try:
        with open(path, encoding="utf-8") as fh:
            return parse_baseline(fh.read())
    except (OSError, ValueError):   # ValueError: a file that is not UTF-8
        return None


def parse_baseline(text):
    # type: (str) -> Optional[List[str]]
    """baseline_ids over text already read (a git show of an earlier version), None when it is not a valid baseline."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("tests"), list):
        return None
    if not all(isinstance(t, str) for t in data["tests"]):
        return None
    return list(data["tests"])


def write_baseline(path, ids):
    """Write the baseline atomically: a temporary file in the same folder, renamed into place."""
    folder = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".timing_hunt_baseline-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"schema": SCHEMA, "tests": sorted(set(ids))}, fh, indent=1)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class _InputParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "NO-DATA: %s\n" % message)


def _row(f):
    return "%-9s %s  line %d  %s" % ("COVERED" if f.covered else "UNCOVERED", f.id, f.line, ",".join(f.triggers))


def main(argv=None):
    # type: (Optional[List[str]]) -> int
    ap = _InputParser(description="Find tests whose verdict rests on a real clock.")
    ap.add_argument("paths", nargs="*", help="test files, repository relative or absolute")
    ap.add_argument("--all", action="store_true", help="print the inventory of every test file")
    ap.add_argument("--write-baseline", action="store_true", help="with --all: write %s" % BASELINE)
    args = ap.parse_args(argv)
    if args.write_baseline and not args.all:
        print("NO-DATA: --write-baseline needs --all")
        return 2
    if args.all == bool(args.paths):
        print("NO-DATA: name test files, or --all (not both)")
        return 2
    paths = test_files(ROOT) if args.all else args.paths
    found, bad = scan_paths(paths, ROOT)
    for rel in bad:
        print("NO-DATA   %s  could not be read or parsed" % rel)
    if not args.all:
        for f in found:
            print(_row(f))
        return 2 if bad else (1 if any(not f.covered for f in found) else 0)
    by = dict((t, 0) for t in TRIGGERS)  # type: Dict[str, int]
    for f in found:
        for t in f.triggers:
            by[t] += 1
    covered = sum(1 for f in found if f.covered)
    print(METHOD)
    print("files scanned: %d" % len(paths))
    print("by trigger: " + ", ".join("%s: %d" % (t, by[t]) for t in TRIGGERS))
    print("flagged: %d  covered: %d  uncovered: %d" % (len(found), covered, len(found) - covered))
    for f in found:
        print(_row(f))
    if bad:
        return 2
    if args.write_baseline:
        write_baseline(os.path.join(ROOT, BASELINE), [f.id for f in found if not f.covered])
        print("wrote %s" % BASELINE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
