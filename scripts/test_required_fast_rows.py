#!/usr/bin/env python3
"""C5: rows the whole gate (scripts/required_fast.sh) must carry, pinned by reading the gate itself.

WHY. scripts/test_land_batch_callsites.py was registered in check_all.sh only, so the whole gate
every merge runs never ran it (1.1.6 plan, PLAN-DESIGN section 6), and the first draft of the fix
named a test path that did not exist. A row that is absent, runs a different command, or names a
file that is not on disk is refused here, so neither failure can come back unseen.

gate_rows(script_text) maps each `run_check "<name>" <command...>` line to its command words; it
answers None for anything that is not text, and row_problems treats None as a problem, never as a
clean gate. A run_check line whose name has no closing quote, or whose command cannot be split, is
not a row. A row name the required table does not know is ignored.
"""
import os
import re
import shlex
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GATE = os.path.join(HERE, "required_fast.sh")

#: The rows the whole gate must carry: name to the exact command words.
REQUIRED = {
    "land-batch-callsites-self": ["python3", "scripts/test_land_batch_callsites.py"],
    "bm-hookperf": ["python3", "-B", "products/brothermode/tools/test_bm_hookperf.py"],
    "bm-hookbench": ["python3", "-B", "products/brothermode/tools/test_bm_hookbench.py"],
    "hook-latency": ["python3", "-B", "scripts/test_hook_latency.py"],
    # 2026-10-23: the cut's approval suite went red in check_all.sh alone and the 1.1.7 cut never saw it
    "cut-app-approval-self": ["python3", "-B", "scripts/test_cut_app_approval.py"],
}

_ROW = re.compile(r'^\s*run_check\s+"([^"\n]+)"\s+(\S.*)$')


def read_gate(path):
    """The gate's text, or None when it cannot be read as UTF-8 text."""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError, TypeError, ValueError):
        return None


def gate_rows(script_text):
    """{row name: [command words]} for every run_check line, or None when the input is not text."""
    if not isinstance(script_text, str):
        return None
    rows = {}
    for line in script_text.splitlines():
        match = _ROW.match(line)
        if not match:
            continue
        try:
            words = shlex.split(match.group(2), comments=True)
        except ValueError:
            continue
        if words:
            rows[match.group(1)] = words
    return rows


def row_problems(rows, root, required):
    """One sentence per required row that is absent, runs another command, or names a test file
    that is missing or outside root. Unreadable rows (None or not a dict) are always a problem."""
    if not isinstance(rows, dict):
        return ["the gate could not be read, so no required row can be confirmed"]
    problems = []
    root_real = os.path.realpath(root)
    for name in sorted(required):
        words = rows.get(name)
        if words is None:
            problems.append("row %s is absent from the gate" % name)
            continue
        if list(words) != list(required[name]):
            problems.append("row %s runs %r, expected %r" % (name, words, required[name]))
        for word in words:
            if not word.endswith(".py"):
                continue
            target = os.path.realpath(os.path.join(root_real, word))
            if os.path.isabs(word) or not target.startswith(root_real + os.sep):
                problems.append("row %s names %s, outside the tree" % (name, word))
            elif not os.path.isfile(target):
                problems.append("row %s names %s, which is not on disk" % (name, word))
    return problems


class RequiredFastRows(unittest.TestCase):
    CMD = REQUIRED["land-batch-callsites-self"]
    # the fixture tests judge the parser and the judge against this one row; the real gate
    # test judges every row in REQUIRED
    ONE = {"land-batch-callsites-self": CMD}

    def test_the_real_gate_has_the_row(self):
        rows = gate_rows(read_gate(GATE))
        self.assertIsNotNone(rows, "scripts/required_fast.sh could not be read")
        self.assertGreater(len(rows), 1)
        self.assertEqual(row_problems(rows, ROOT, REQUIRED), [])

    def test_a_missing_test_file_is_refused(self):
        # the exact command, judged against a root that lacks the file (docs/ has no scripts/),
        # then against the real tree that has it: only the file check differs between the two
        rows = {"land-batch-callsites-self": list(self.CMD)}
        problems = row_problems(rows, os.path.join(ROOT, "docs"), self.ONE)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("not on disk", problems[0])
        self.assertEqual(row_problems(rows, ROOT, self.ONE), [])

    def test_the_old_phantom_path_is_refused(self):
        phantom = ["python3", "scripts/loop/test_land_batch_callsites.py"]
        problems = row_problems({"land-batch-callsites-self": phantom}, ROOT, self.ONE)
        self.assertTrue(any("not on disk" in p for p in problems), problems)

    def test_an_unreadable_gate_is_a_problem(self):
        # read only fixtures, so the suite also runs in a read only sandbox: a missing file, a
        # directory, and a binary that is not UTF-8 text (the interpreter running this suite)
        self.assertIsNone(read_gate(os.path.join(HERE, "no_such_gate_c5.sh")))
        self.assertIsNone(read_gate(HERE))
        self.assertIsNone(read_gate(os.path.realpath(sys.executable)))
        self.assertIsNone(read_gate(None))
        for hostile in (None, 42, b'run_check "x" true', ["run_check"]):
            self.assertIsNone(gate_rows(hostile), hostile)
        # unreadable is a problem even when nothing is required
        self.assertTrue(row_problems(None, ROOT, {}))
        self.assertTrue(row_problems(None, ROOT, self.ONE))
        self.assertIn("could not be read", row_problems(None, ROOT, self.ONE)[0])

    def test_an_empty_gate_names_the_row_absent(self):
        rows = gate_rows("")
        self.assertEqual(rows, {})
        self.assertEqual(row_problems(rows, ROOT, self.ONE),
                         ["row land-batch-callsites-self is absent from the gate"])

    def test_a_different_command_is_refused(self):
        rows = gate_rows('run_check "land-batch-callsites-self" python3 scripts/test_land_batch.py\n')
        problems = row_problems(rows, ROOT, self.ONE)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("expected", problems[0])

    def test_a_path_outside_the_tree_is_refused(self):
        for word in ("../test_land_batch_callsites.py", "/etc/test_land_batch_callsites.py"):
            problems = row_problems({"land-batch-callsites-self": ["python3", word]}, ROOT, self.ONE)
            self.assertTrue(any("outside the tree" in p for p in problems), (word, problems))

    def test_corrupt_comment_and_unknown_lines(self):
        text = "\n".join([
            '# run_check "land-batch-callsites-self" python3 scripts/test_land_batch_callsites.py',
            'run_check "land-batch-callsites-self python3 scripts/test_land_batch_callsites.py',
            'run_check "unknown-row" python3 scripts/does_not_exist.py',
            'run_check "bad-quote" python3 "scripts/x.py',
            '  run_check "indented" true  # trailing comment',
        ])
        rows = gate_rows(text)
        self.assertEqual(rows, {"unknown-row": ["python3", "scripts/does_not_exist.py"],
                                "indented": ["true"]})
        self.assertEqual(row_problems(rows, ROOT, self.ONE),
                         ["row land-batch-callsites-self is absent from the gate"])
        good = text + '\nrun_check "land-batch-callsites-self" python3 scripts/test_land_batch_callsites.py\n'
        self.assertEqual(row_problems(gate_rows(good), ROOT, self.ONE), [])


if __name__ == "__main__":
    unittest.main()
