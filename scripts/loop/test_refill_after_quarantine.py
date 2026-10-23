#!/usr/bin/env python3
"""A refused landing that only quarantined its own builds refills the pool (1.1.7 proof run, 2026-10-22 04h07).

The run ended UNPRODUCTIVE on one bad build. I3.b was READY, its landing went RED on a neighbour suite that is green on
the base (scripts/test_runner_pool_guard.py, 99 tests OK on 3.13 and 3.9 at hub 3674ca8e5), land_batch quarantined it
("failed the gates on its own, so it is not offered again"), reverted its paths and refused. loop_pass.sh then read
every refusal as "the tree needs a decision", refilled nothing, found no runner alive and exited 45, and the driver
ended the run with four sub units never started. A refusal whose every picked build was quarantined, with the worktree
fingerprint unchanged since before the landing, is a build's failure, not the tree's: the pool refills.

The entry point is real: the lines of scripts/loop/loop_pass.sh between `# REFILL BEGIN` and `# REFILL END`, run by bash
with the pass's own variables and stand ins for its two helpers (sentry and live_runners). No other process starts.

Run: python3 -B scripts/loop/test_refill_after_quarantine.py
"""
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PASS_PATH = os.path.join(HERE, "loop_pass.sh")

REFUSED = ("NOTE    I3.b       landing refusal 1 recorded as a diagnosis note: the next brief carries it\n"
           "QUARANTINED I3.b: it failed the gates on its own, so it is not offered again\n"
           "REFUSED: nothing committed, gates RED (neighbour scripts/test_runner_pool_guard.py on py3); reverted 5 "
           "tracked and removed 0 created path(s); every build json is still on disk | log x")
PICK_ONE = "/e/unit-runs/I3.b-040217/round0/out/I3.b-r0-build.json"


def _block():
    with open(PASS_PATH, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    try:
        a = lines.index("# REFILL BEGIN")
        b = lines.index("# REFILL END")
    except ValueError:
        raise AssertionError("loop_pass.sh carries no '# REFILL BEGIN' / '# REFILL END' markers around its refusal block")
    if b <= a + 1:
        raise AssertionError("the REFILL markers hold no lines")
    return "\n".join(lines[a + 1:b])


def _run(lb=1, landed=REFUSED, pick=PICK_ONE, tree_same=True, live="0"):
    """Run the block; a pass the block does not end prints FELL THROUGH (the refill that follows it) and exits 0."""
    tmp = tempfile.mkdtemp(prefix="refill-block-")
    try:
        script = os.path.join(tmp, "block.sh")
        stubs = ("sentry() { [ \"$1\" = verify ] && [ \"$2\" = FP0 ] && [ \"$TREE_SAME\" = 1 ]; }\n"
                 "live_runners() { echo \"$LIVE\"; }\n")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/bash\n" + stubs + _block() + "\necho 'FELL THROUGH'\nexit 0\n")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": tmp, "LB": str(lb), "LANDED_LINE": landed,
               "PICK": pick, "FP_BEFORE": "FP0", "TREE_SAME": "1" if tree_same else "0", "LIVE": live,
               "NO_TABLE": "NO-TABLE"}
        return subprocess.run(["bash", script], env=env, cwd=tmp, capture_output=True, text=True, timeout=60)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class TestRefillAfterQuarantine(unittest.TestCase):
    def test_a_refusal_of_only_quarantined_builds_on_an_unchanged_tree_refills(self):
        r = _run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("FELL THROUGH", r.stdout)
        self.assertNotIn("NOT refilled", r.stdout)

    def test_a_tree_that_changed_still_needs_a_decision(self):
        r = _run(tree_same=False)
        self.assertEqual(r.returncode, 45, r.stdout)
        self.assertIn("NOT refilled", r.stdout)

    def test_a_refusal_with_no_quarantine_still_needs_a_decision(self):
        r = _run(landed="REFUSED: nothing committed, stray paths: x | log y")
        self.assertEqual(r.returncode, 45, r.stdout)
        self.assertIn("NOT refilled", r.stdout)

    def test_one_picked_build_not_quarantined_still_needs_a_decision(self):
        pick = PICK_ONE + " /e/unit-runs/LC3.a-1/round0/out/LC3.a-r1-build.json"
        r = _run(pick=pick)
        self.assertEqual(r.returncode, 45, r.stdout)

    def test_a_quarantine_line_for_a_similar_name_does_not_count(self):
        # I3.b must not be satisfied by I3.bb or I3xb
        landed = REFUSED.replace("QUARANTINED I3.b:", "QUARANTINED I3xb:")
        r = _run(landed=landed)
        self.assertEqual(r.returncode, 45, r.stdout)

    def test_a_unit_id_with_a_hyphen_is_read(self):
        pick = "/e/unit-runs/FX-31.a-1/round0/out/FX-31.a-r2-build.json"
        landed = REFUSED.replace("I3.b", "FX-31.a")
        r = _run(pick=pick, landed=landed)
        self.assertIn("FELL THROUGH", r.stdout)

    def test_a_refusal_with_runners_alive_stays_quiet(self):
        r = _run(landed="REFUSED: parity", live="2")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("FELL THROUGH", r.stdout)

    def test_a_hold_and_a_landing_fall_through_as_before(self):
        self.assertIn("FELL THROUGH", _run(landed="nothing landed: 1 build(s) held").stdout)
        self.assertIn("FELL THROUGH", _run(lb=0, landed="LANDED  X.a | ...").stdout)

    def test_a_quarantine_beside_a_refusal_for_another_reason_still_needs_a_decision(self):
        # adversarial review, 2026-10-22: a parity or conflict refusal leaves the worktree fingerprint as it was, so the
        # fingerprint alone cannot tell it from a build's own failure; only the gates RED refusal may refill
        for other in ("REFUSED: parity: local 1a2b is ahead of hub 3c4d", "REFUSED: merge conflict in x"):
            landed = REFUSED.replace("REFUSED: nothing committed, gates RED", other)
            self.assertEqual(_run(landed=landed).returncode, 45, other)
            both = REFUSED + "\n" + other
            self.assertEqual(_run(landed=both).returncode, 45, "beside a gates RED line: " + other)

    def test_a_quarantine_with_no_refusal_line_is_an_unknown_refusal(self):
        landed = "QUARANTINED I3.b: it failed the gates on its own, so it is not offered again"
        self.assertEqual(_run(landed=landed).returncode, 45)

    def test_an_empty_pick_never_counts_as_quarantined(self):
        r = _run(pick="")
        self.assertEqual(r.returncode, 45, r.stdout)


if __name__ == "__main__":
    unittest.main()
