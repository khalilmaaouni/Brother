#!/usr/bin/env python3
"""Refuse the wall-clock stopwatch assertion pattern, by reading the AST.

WHY THIS EXISTS
  A test that asserts an absolute wall-clock duration (`assertLess(time.time()
  - started, 5.0)`) measures the MACHINE the suite happens to run on, not the
  code under test. It passes on a quiet laptop and fails on a busy one while
  the code has not changed at all. This exact defect has been found and fixed
  SEVEN times in ten days by seven different people in this repository. Review
  caught it seven times and prevented it zero times, because review runs
  after the code is written and a reviewer has to notice the shape by eye
  every single time. This tool is the machine that refuses the pattern before
  a human has to notice it again.

THE RULE
  Flag a comparison (`assertLess`, `assertLessEqual`, `assertTrue` with a
  comparison inside, or a bare `assert` with a comparison) where BOTH hold:

    1. one operand is a `BinOp` with `Sub`, where either side is a `Call` to
       `time.time`, `time.monotonic`, or `time.perf_counter` -- OR one
       operand is a `Name` whose most recent assignment in the SAME function
       scope has such a `BinOp(Sub, ...)` as its right hand side (the two
       line `elapsed = time.time() - start` idiom), AND
    2. the other operand is a numeric `Constant`.

  Condition 2 is what spares the approved fix shape: a ratio like
  `assertLess(large / small, 8.0)` puts the constant against a `BinOp(Div)`,
  never against a timing difference, so it is never flagged. `assertEqual`
  is never inspected at all, by name: a count check or a deterministic
  operation-count comparison is not this defect regardless of its operands.

  This is an AST check, not a regex, on purpose: a regex false-positives on
  every ratio assertion, every `assertLess(len(x), N)` count check, and every
  `assertLess(s.index(a), s.index(b))` ordering check, all of which are
  legitimate and appear in this repository today.

ESCAPE HATCHES
  ALLOWLIST_FILES and ALLOWLIST_DIR_PREFIXES below name real benchmark
  suites, exempt by path, visible here so an exemption shows up in a diff.
  A single line can also carry an inline exemption comment,
  `# walltime-lint: allow <reason>`; an empty reason is refused, meaning the
  line stays flagged, because a reason nobody typed is not a reason.

Python 3.9, standard library only. No network. No subprocess (this is a
shipping tool under tools/, and tools/test_bm.py bans that import here).
No em or en dashes anywhere in this file or its output.

Usage:
  python3 tools/bm_lint_walltime.py <path> [<path>...]

Walks directories for *.py files. Prints one line per violation as
"path:line: <the source line>". Exit 1 if any violation, exit 0 if none.
Exit 2, printing "NO-DATA: opened no files", if it opened zero files: a lint
that silently scanned nothing must never read as clean.
"""

import ast
import io
import os
import re
import sys

#: Real benchmark suites, verified to legitimately assert on measured wall
#: clock time. Exempt by exact path (relative to the current working
#: directory the lint is invoked from). Adding a name here is the moment an
#: exemption becomes visible in a diff, which is the point of keeping this
#: list in the source rather than in a config file nobody reviews.
ALLOWLIST_FILES = (
    "tools/bm_hookbench.py",
    "tools/bm_bench.py",
    "tools/test_bm_hookperf.py",
    "tools/test_bm_hookbench.py",
    "scripts/bench_env.py",
    "scripts/bench_e2e_lifecycle.py",
    "scripts/benchmark.py",
    "scripts/benchmark_comparative.py",
)

#: Directory prefixes exempt wholesale. "benchmark/" and everything under it.
ALLOWLIST_DIR_PREFIXES = (
    "benchmark/",
)

#: time.* callables that produce a timestamp or a monotonic instant. Matched
#: as a dotted attribute call, `time.time()` and friends, not a bare imported
#: name: the two line idiom and the direct call both go through `time.`.
TIMER_ATTRS = frozenset(("time", "monotonic", "perf_counter"))

#: The assertion callables inspected. assertEqual is deliberately absent:
#: a deterministic operation-count comparison is not this defect no matter
#: what its operands look like, and the calibration set proves it must not
#: be touched (tools/test_bm_fence_hook.py's per-record cost assertion).
ASSERT_METHOD_NAMES = frozenset(("assertLess", "assertLessEqual", "assertTrue"))

#: The inline exemption comment. Group 1 is the reason; an exemption with an
#: empty (or whitespace-only) reason is refused, so the offending line stays
#: flagged rather than silently passing because someone typed the tag alone.
_INLINE_ALLOW_RE = re.compile(r"#\s*walltime-lint:\s*allow\b(.*)$")


class LintError(Exception):
    """A path could not be read or parsed. Reported, never worked around."""


# ---------------------------------------------------------------------------
# AST shape recognition.
# ---------------------------------------------------------------------------

def _is_timer_call(node):
    """True for `time.time()`, `time.monotonic()`, `time.perf_counter()`."""
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in TIMER_ATTRS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "time")


def _is_timing_binop(node):
    """True for `<timer call> - X` or `X - <timer call>`."""
    return (isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Sub)
            and (_is_timer_call(node.left) or _is_timer_call(node.right)))


def _is_numeric_constant(node):
    return (isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool))


def _is_timing_operand(node, assignments, usage_lineno):
    """True when `node` is itself a timing difference, or is a Name whose
    most recent assignment strictly before `usage_lineno`, in this same
    function scope, assigns one."""
    if _is_timing_binop(node):
        return True
    if isinstance(node, ast.Name):
        candidates = [entry for entry in assignments.get(node.id, ())
                     if entry[0] < usage_lineno]
        if not candidates:
            return False
        candidates.sort(key=lambda entry: entry[0])
        _, value = candidates[-1]
        return _is_timing_binop(value)
    return False


def _is_violation(op1, op2, assignments, lineno):
    return ((_is_timing_operand(op1, assignments, lineno)
             and _is_numeric_constant(op2))
            or (_is_timing_operand(op2, assignments, lineno)
                and _is_numeric_constant(op1)))


def _call_name(func_node):
    """The bare name of a call target: `assertLess` for both `self.x()` and
    a directly imported `x()`."""
    if isinstance(func_node, ast.Attribute):
        return func_node.attr
    if isinstance(func_node, ast.Name):
        return func_node.id
    return None


def _operands_of(node):
    """The two operands to check for one assert-shaped node, or None if
    `node` is not a shape this lint inspects at all."""
    if isinstance(node, ast.Assert):
        test = node.test
        if isinstance(test, ast.Compare) and len(test.ops) == 1:
            return test.left, test.comparators[0]
        return None
    if isinstance(node, ast.Call):
        name = _call_name(node.func)
        if name in ("assertLess", "assertLessEqual") and len(node.args) >= 2:
            return node.args[0], node.args[1]
        if name == "assertTrue" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Compare) and len(arg.ops) == 1:
                return arg.left, arg.comparators[0]
        return None
    return None


# ---------------------------------------------------------------------------
# Scope walking. Assignments are resolved per function (or per module, for
# top level code), never crossing into or out of a nested function's own
# scope, matching the rule's "same function scope" clause.
# ---------------------------------------------------------------------------

def _own_scope_nodes(body):
    """Every descendant statement/expression reachable from `body` without
    crossing into a nested function, async function, lambda, or class body:
    those own their own scope and are walked separately."""
    stack = list(body)
    out = []
    while stack:
        node = stack.pop()
        out.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return out


def _scan_scope(body, source_lines, violations):
    nodes = _own_scope_nodes(body)
    assignments = {}
    for node in nodes:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            assignments.setdefault(node.targets[0].id, []).append(
                (node.lineno, node.value))
    for node in nodes:
        operands = _operands_of(node)
        if operands is None:
            continue
        op1, op2 = operands
        if not _is_violation(op1, op2, assignments, node.lineno):
            continue
        if _exempted_inline(source_lines, node):
            continue
        violations.append(node.lineno)


def _exempted_inline(source_lines, node):
    """True when a `# walltime-lint: allow <reason>` comment carrying a non
    empty reason sits on any line the flagged node spans. An empty reason
    is refused: found but not honoured, so the line stays flagged."""
    start = node.lineno
    end = getattr(node, "end_lineno", None) or start
    for lineno in range(start, end + 1):
        if lineno < 1 or lineno > len(source_lines):
            continue
        match = _INLINE_ALLOW_RE.search(source_lines[lineno - 1])
        if match:
            return bool(match.group(1).strip())
    return False


def scan_source(source, filename="<source>"):
    """Every violation line number in one file's source text.

    Raises LintError on a syntax error rather than silently skipping the
    file, so a file this lint cannot parse is a refusal, not a clean bill."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        raise LintError("%s does not parse: %s" % (filename, exc))
    source_lines = source.splitlines()
    violations = []
    _scan_scope(tree.body, source_lines, violations)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _scan_scope(node.body, source_lines, violations)
    violations = sorted(set(violations))
    return [(lineno, source_lines[lineno - 1] if 0 < lineno <= len(source_lines)
             else "") for lineno in violations]


# ---------------------------------------------------------------------------
# Paths: walking, allowlisting, and the CLI.
# ---------------------------------------------------------------------------

def _normalize_rel(path):
    rel = os.path.relpath(path, os.getcwd())
    return rel.replace(os.sep, "/")


def is_allowlisted(rel_path):
    if rel_path in ALLOWLIST_FILES:
        return True
    for prefix in ALLOWLIST_DIR_PREFIXES:
        if rel_path == prefix.rstrip("/") or rel_path.startswith(prefix):
            return True
    return False


def iter_py_files(paths):
    """Every *.py path under the given files/directories, walked in a stable
    (sorted) order so output is deterministic across runs."""
    for path in paths:
        if os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                dirs[:] = sorted(d for d in dirs
                                 if d not in (".git", "__pycache__")
                                 and not d.startswith("."))
                for name in sorted(files):
                    if name.endswith(".py"):
                        yield os.path.join(root, name)
        elif os.path.isfile(path):
            if path.endswith(".py"):
                yield path
        else:
            sys.stderr.write("bm_lint_walltime: no such path: %s\n" % path)


def lint_paths(paths):
    """Scan every *.py file under `paths`.

    Returns (violations, opened) where violations is a sorted list of
    (rel_path, lineno, source_line) and opened is the count of files this
    tool actually read, allowlisted or not: a lint that walked nothing must
    be told apart from a lint that walked files and found them clean."""
    violations = []
    opened = 0
    for file_path in iter_py_files(paths):
        opened += 1
        rel = _normalize_rel(file_path)
        try:
            with io.open(file_path, encoding="utf-8") as handle:
                source = handle.read()
        except (IOError, OSError) as exc:
            sys.stderr.write("bm_lint_walltime: cannot read %s: %s\n"
                             % (file_path, exc))
            # Fail CLOSED: a file this lint cannot read is a finding, not
            # a skip. The 2026-08-11 cross-family audit showed that with
            # every file unreadable the run exited 0 while claiming to
            # have checked the tree.
            violations.append((rel, 0, "UNCHECKABLE: cannot read (%s)"
                               % exc))
            continue
        if is_allowlisted(rel):
            continue
        try:
            file_violations = scan_source(source, filename=file_path)
        except LintError as exc:
            sys.stderr.write("bm_lint_walltime: %s\n" % exc)
            violations.append((rel, 0, "UNCHECKABLE: cannot parse (%s)"
                               % exc))
            continue
        for lineno, line in file_violations:
            violations.append((rel, lineno, line))
    violations.sort(key=lambda entry: (entry[0], entry[1]))
    return violations, opened


def main(argv):
    if any(arg in ("-h", "--help") for arg in argv):
        sys.stdout.write(__doc__)
        return 0
    if not argv:
        sys.stderr.write("bm_lint_walltime: usage: bm_lint_walltime.py "
                         "<path> [<path>...]\n")
        return 2
    violations, opened = lint_paths(argv)
    if opened == 0:
        sys.stdout.write("NO-DATA: opened no files\n")
        return 2
    for rel, lineno, line in violations:
        sys.stdout.write("%s:%d: %s\n" % (rel, lineno, line.rstrip()))
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
