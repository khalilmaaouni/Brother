#!/usr/bin/env python3
"""REQ-H-TIMEOUT (H4.d): a subprocess call a worker can read end to end carries a timeout, and a
call whose timeout cannot be read is refused, never counted clean.

The scan is static (ast over the source text), so a call written inside a string literal or a
comment is not a call, and a call whose timeout arrives only through a pack of keywords is not a
call whose timeout this scan can prove.

CORRUPT INPUT BLOCKS (house law): a call that carries timeout= AND also unpacks further keywords
with ** may still carry that timeout, or the pack may carry a SECOND one that Python refuses only
at call time. That cannot be read from the source, so it raises ValueError naming the line instead
of answering "clean". A call whose only timeout is a pack this scan cannot open is reported as a
call with no timeout, which is the blocking direction, never the safe one.

The loop modules scanned end to end are the ones whose every command site this sub unit could read
and edit. Loop modules whose command sites sit inside a block the brief did not show
(land_batch.py, check_wave.py, commit_scan.py, unit_runner.py, grade_build.py, spec_score.py,
salvage.py) are named in the build's unknowns: a scan that cannot read a call must not claim it
clean, and no edit may be placed inside a block that was not shown.
"""
import ast
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP_DIR = os.path.join(HERE, "loop")
SCANNED = ("runner_pool.py", "probe_wave.py", "diag_apply.py")
RUN_KINDS = frozenset(("run", "check_output", "call"))


def _split(fn):
    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
        return fn.value.id, fn.attr
    return None, None


def _has_timeout(call):
    for kw in call.keywords:
        if kw.arg == "timeout":
            return True
    return False


def _ambiguous_timeout(call):
    """True when the call names timeout= itself AND also unpacks further keywords with **: the pack
    may carry a second timeout (a repeated keyword Python refuses only at call time), so whether
    the timeout survives cannot be read from the source. Unreadable input is refused, never read
    as a clean call."""
    if not _has_timeout(call):
        return False
    for kw in call.keywords:
        if kw.arg is None:
            return True
    return False


def _detached(call):
    for kw in call.keywords:
        if kw.arg == "start_new_session" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return True
    return False


def _scopes(tree):
    out = {}

    def walk(node, key):
        out[id(node)] = key
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            key = node.name
        for child in ast.iter_child_nodes(node):
            walk(child, key)

    walk(tree, "<module>")
    return out


def calls_without_timeout(source):
    """Line numbers of subprocess.run, check_output, call and Popen calls whose arguments carry
    no timeout= (Popen: no wait(timeout=) or communicate(timeout=) on its result within the same
    function).

    Hostile or unparsable input REFUSES with ValueError naming the cause: a scan that cannot read
    its input is NO-DATA, never a clean bill of health. A call that carries timeout= and also
    unpacks ** keywords is corrupt input and is refused the same way, because the pack can carry a
    second timeout that the source does not show."""
    if not isinstance(source, str):
        raise ValueError("source must be a str, got %s" % type(source).__name__)
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        raise ValueError("source does not parse: %s" % exc)
    scope = _scopes(tree)
    popen_at = {}
    waited = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            base, attr = _split(node.value.func)
            if base == "subprocess" and attr == "Popen":
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        popen_at[id(node.value)] = (scope.get(id(node.value), "<module>"), tgt.id)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) and node.func.attr in ("wait", "communicate") \
                and _has_timeout(node):
            waited.add((scope.get(id(node), "<module>"), node.func.value.id))
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        base, attr = _split(node.func)
        if base != "subprocess":
            continue
        if _ambiguous_timeout(node):
            raise ValueError("line %d: the call carries timeout= and also unpacks ** keywords, so the timeout it runs with cannot be read; corrupt input is refused" % node.lineno)
        if attr in RUN_KINDS:
            if not _has_timeout(node):
                bad.append(node.lineno)
        elif attr == "Popen":
            if _has_timeout(node):
                continue
            if _detached(node):
                continue
            here = popen_at.get(id(node))
            if here is not None and here in waited:
                continue
            bad.append(node.lineno)
    return sorted(set(bad))


class CallsWithoutTimeoutTests(unittest.TestCase):
    def test_a_call_without_a_timeout_is_flagged(self):
        self.assertEqual(calls_without_timeout("import subprocess\nsubprocess.run([1])\n"), [2])

    def test_a_call_with_a_timeout_is_accepted(self):
        self.assertEqual(calls_without_timeout("import subprocess\nsubprocess.run([1], timeout=1)\n"), [])

    def test_a_call_named_in_a_string_or_a_comment_is_not_a_call(self):
        src = 'import subprocess\ns = "subprocess.run([1])"\n# subprocess.call([2])\n'
        self.assertEqual(calls_without_timeout(src), [])

    def test_a_popen_result_waited_with_a_timeout_is_accepted(self):
        src = "import subprocess\np = subprocess.Popen([1])\np.wait(timeout=1)\n"
        self.assertEqual(calls_without_timeout(src), [])

    def test_a_popen_result_never_waited_is_flagged(self):
        src = "import subprocess\np = subprocess.Popen([1])\n"
        self.assertEqual(calls_without_timeout(src), [2])

    def test_a_detached_popen_is_not_waited_for(self):
        src = "import subprocess\nsubprocess.Popen([1], start_new_session=True)\n"
        self.assertEqual(calls_without_timeout(src), [])

    def test_a_duplicated_timeout_keyword_is_refused_not_read_as_clean(self):
        with self.assertRaises(ValueError) as caught:
            calls_without_timeout('import subprocess\nsubprocess.run([1], timeout=1, **{"timeout": 2})\n')
        self.assertIn("line 2", str(caught.exception))
        with self.assertRaises(ValueError):
            calls_without_timeout('import subprocess\nextra = {"timeout": 2}\nsubprocess.Popen([1], timeout=1, **extra)\n')

    def test_hostile_input_is_refused_never_accepted(self):
        for bad in (None, 7, -1.5, float("nan"), True, b"import subprocess\n", ["x"], {"k": 1}, ("x",)):
            with self.assertRaises(ValueError):
                calls_without_timeout(bad)
        with self.assertRaises(ValueError):
            calls_without_timeout("def (:\n")

    def test_every_scanned_loop_module_call_carries_a_timeout(self):
        if not os.path.isdir(LOOP_DIR):
            self.skipTest("scripts/loop is not present in this checkout")
        problems = []
        for name in SCANNED:
            path = os.path.join(LOOP_DIR, name)
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as fh:
                raw = fh.read()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                problems.append("%s: not utf-8: %s" % (name, exc))
                continue
            try:
                lines = calls_without_timeout(text)
            except ValueError as exc:
                problems.append("%s: %s" % (name, exc))
                continue
            problems.extend("%s:%d" % (name, ln) for ln in lines)
        self.assertEqual(problems, [], "subprocess calls without a timeout: %s" % ", ".join(problems))


if __name__ == "__main__":
    unittest.main()
