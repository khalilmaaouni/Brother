#!/usr/bin/env python3
"""A pass says why the pool started what it started (found 2026-10-22 on the 1.1.7 proof run).

loop_pass.sh kept only the pool's START and `started` lines, so the driver log could not say why a pass opened one
runner where the work in progress cap allowed five: the binding cap (native seats over workers per round, floor 1)
and each skipped sub unit's reason were printed by runner_pool.py and dropped. The pass now writes the pool's own
`BUDGET cap` line and one line naming every skipped sub unit with its reason, both prefixed POOL.

The entry point is real: the lines of scripts/loop/loop_pass.sh between `# POOLWHY BEGIN` and `# POOLWHY END`, run by
bash with the pass's own POOL_ALL. No other process is started and no network is touched.

Run: python3 -B scripts/loop/test_pool_why_lines.py
"""
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PASS_PATH = os.path.join(HERE, "loop_pass.sh")

CAP = ("BUDGET cap 8 runner(s) | WIP cap 5 unit(s) | 0 alive, 0 READY to land | native seats 2, 6 worker(s) per round, "
       "seat cap 1 runner(s) | room for 1")
POOL = "\n".join((
    "SCOPE   ^(I3|I4)(\\.|$) | admits 2 of 9 open units",
    CAP,
    "ORDER   I33(4/0) I4(5/0)",
    "I33   I33.a    START",
    "I4    I4.a     skip: budget cap reached (1 new this pass)",
    "LC1   LC1.b    skip: lint blocking (council FIX-FIRST)",
    "started 1 | runners alive before this pass 0 | in scope units with no sub units yet (need a spec lane): []",
))


def _block():
    """The lines between the POOLWHY markers. Missing markers fail the test."""
    with open(PASS_PATH, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    try:
        a = lines.index("# POOLWHY BEGIN")
        b = lines.index("# POOLWHY END")
    except ValueError:
        raise AssertionError("loop_pass.sh carries no '# POOLWHY BEGIN' / '# POOLWHY END' markers around its pool lines")
    if b <= a + 1:
        raise AssertionError("the POOLWHY markers hold no lines")
    return "\n".join(lines[a + 1:b])


def _run(pool_all):
    tmp = tempfile.mkdtemp(prefix="poolwhy-block-")
    try:
        script = os.path.join(tmp, "block.sh")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/bash\n" + _block() + "\n")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": tmp, "POOL_ALL": pool_all}
        return subprocess.run(["bash", script], env=env, cwd=tmp, capture_output=True, text=True, timeout=60)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class TestPoolWhyLines(unittest.TestCase):
    def test_the_binding_cap_line_is_kept(self):
        r = _run(POOL)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("POOL    " + CAP, r.stdout.splitlines())

    def test_every_skipped_sub_unit_is_named_with_its_reason_on_one_line(self):
        r = _run(POOL)
        skipped = [x for x in r.stdout.splitlines() if x.startswith("POOL    skipped: ")]
        self.assertEqual(len(skipped), 1, r.stdout)
        self.assertIn("I4.a (budget cap reached (1 new this pass))", skipped[0])
        self.assertIn("LC1.b (lint blocking (council FIX-FIRST))", skipped[0])

    def test_start_lines_are_not_repeated(self):
        # the pass already echoes START and started; this block adds only the why
        r = _run(POOL)
        self.assertFalse([x for x in r.stdout.splitlines() if "START" in x or x.startswith("started")], r.stdout)

    def test_a_pool_that_printed_neither_adds_nothing(self):
        r = _run("I33   I33.a    START\nstarted 1 | runners alive before this pass 0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "", r.stdout)

    def test_no_line_starts_with_an_outcome_word(self):
        # the run watchers anchor LANDED, HELD and LOOP at line start; a pool line must never look like one
        r = _run(POOL + "\nHELD  HELD.a   skip: HELD by a unit named like an outcome")
        for line in r.stdout.splitlines():
            self.assertTrue(line.startswith("POOL    "), line)

    def test_a_long_skip_list_is_bounded(self):
        many = "\n".join("U%d    U%d.a     skip: budget cap reached (0 new this pass)" % (i, i) for i in range(60))
        r = _run(CAP + "\n" + many)
        skipped = [x for x in r.stdout.splitlines() if x.startswith("POOL    skipped: ")]
        self.assertEqual(len(skipped), 1, r.stdout)
        self.assertLessEqual(len(skipped[0]), 700)


if __name__ == "__main__":
    unittest.main()
