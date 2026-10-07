#!/usr/bin/env python3
"""READY admits, READY-UNPROBED does not, in the two readers a earlier sweep did not reach.

WHY THIS ONE. A run's STATUS file opens with a status WORD. "READY" means a build is ready to land.
"READY-UNPROBED" means the adversarial probe stage produced nothing runnable, so the build is not
trustworthy and must never be treated as ready. The second string STARTS WITH the first, so every
reader written as `startswith("READY")` silently accepts the one word that means "not proven yet".

Nine readers were corrected on 2026-09-21 (runner_pool.py, loop_done.py, pass_digest.py and their
twins). TWO were outside that sweep's scope and kept the prefix:

  scripts/loop/gen_subunit_gantt.py   counted READY-UNPROBED in the board's "ready to land" tile.
                                      Cosmetic in the sense that nothing lands from it, and not
                                      cosmetic in the sense that the board then reported work
                                      waiting to land that land_batch.py refuses by name. A board
                                      a reader learns to discount is worse than no board.
  scripts/prediction_ledger.py        resolved a prediction as PASSED on READY-UNPROBED, writing an
                                      unprobed build into the calibration data as a successful
                                      outcome. That is the data later used to decide which
                                      predictor is trustworthy, and a poisoned ledger reads exactly
                                      like a good one.

Each reader is pinned twice, admits on READY and refuses on READY-UNPROBED, because a test that only
checks the refusal passes on a reader that refuses everything.

The board is driven through the process boundary: gen_subunit_gantt.py does its work at import time
and has no importable entry point, so the fixture is a plan plus a run folder in a temp directory
with HOME pointed at it. The ledger is called directly, with its ledger path injected and HOME
patched, because harvest() expands the runs directory at call time.

Run: python3 scripts/test_status_word_readers.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GANTT = os.path.join(HERE, "loop", "gen_subunit_gantt.py")
sys.path.insert(0, HERE)
import prediction_ledger as L  # noqa: E402

# Distinctive ids: a real `unit_runner.py D3 D3.1` alive on this machine would otherwise be able to
# change the state the board derives for a fixture sub unit.
UNIT, SUB = "ZTEST9", "ZTEST9.1"


def write_status(home, sub, text):
    """One run folder with one STATUS file, under the evidence tree both readers expand from HOME."""
    d = os.path.join(home, ".claude", "evidence", "unit-runs", sub + "-120000")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "STATUS"), "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    return d


class BoardReadyTile(unittest.TestCase):
    """gen_subunit_gantt.py: the "ready to land" tile counts exactly READY."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="status-word-board-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.home = os.path.join(self.dir, "home")
        os.makedirs(os.path.join(self.home, ".claude", "evidence"))
        os.makedirs(os.path.join(self.dir, "docs", "plan"))
        with open(os.path.join(self.dir, "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"units": [{"id": UNIT, "title": "fixture", "state": "OPEN",
                                  "sub_units": [SUB], "evidence": "", "wave": 1,
                                  "depends_on": []}]}, fh)

    def ready_tile(self, status_text):
        """The rendered value of the "ready to land" tile for one sub unit in that status."""
        write_status(self.home, SUB, status_text)
        env = dict(os.environ, HOME=self.home, PYTHONDONTWRITEBYTECODE="1")
        for k in [k for k in env if k.startswith("GIT_")]:
            env.pop(k)
        r = subprocess.run([sys.executable, "-B", GANTT], cwd=self.dir, env=env,
                           capture_output=True, text=True, timeout=180)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(os.path.join(self.dir, "docs/plan/SUBUNIT-GANTT.html"), encoding="utf-8") as fh:
            page = fh.read()
        m = re.search(r'<b>(\d+)</b><span>ready to land</span>', page)
        self.assertIsNotNone(m, "the board has no 'ready to land' tile to read")
        return int(m.group(1))

    def test_ready_is_counted_as_ready_to_land(self):
        # the refusal below must not be a tile that is always zero
        self.assertEqual(self.ready_tile("READY /b/%s-r1-build.json" % SUB), 1)

    def test_ready_unprobed_is_not_counted_as_ready_to_land(self):
        self.assertEqual(self.ready_tile("READY-UNPROBED /b/%s-r1-build.json" % SUB), 0)


class LedgerHarvest(unittest.TestCase):
    """prediction_ledger.py: harvest() resolves a prediction as passed only on exactly READY."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="status-word-ledger-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.home = os.path.join(self.dir, "home")
        os.makedirs(os.path.join(self.home, ".claude", "evidence"))
        self.path = os.path.join(self.dir, "brother-predictions.jsonl")

    def outcome(self, status_text):
        """The recorded actual for one pending prediction, or None when harvest declined to resolve."""
        write_status(self.home, SUB, status_text)
        L.predict("grade", SUB, "pass", confidence=0.9, question="buildable", path=self.path, now=1)
        real = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        try:
            L.harvest(path=self.path)
        finally:
            if real is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = real
        rows = [r for r in L.load(self.path) if r.get("kind") == "resolve"]
        return rows[0]["actual"] if rows else None

    def test_ready_resolves_the_prediction_as_a_pass(self):
        # the refusal below must not be a harvester that resolves nothing
        self.assertEqual(self.outcome("READY /b/%s-r1-build.json" % SUB), "pass")

    def test_ready_unprobed_never_resolves_the_prediction_as_a_pass(self):
        # an unprobed build is an unknown: declining leaves the row for a later harvest to read a
        # real verdict, and a "fail" here would poison the same calibration in the other direction
        self.assertNotEqual(self.outcome("READY-UNPROBED /b/%s-r1-build.json" % SUB), "pass")


if __name__ == "__main__":
    unittest.main()
