#!/usr/bin/env python3
"""PG1.d: spec_score.py --no-claim. A writer scores its own spec without recording a prediction claim, and the exit
code says whether it may stop (0) or repair (1); 2 when nothing scored. Without --no-claim nothing changes: the claim
is recorded and the exit is 0, which is what runner_pool.py reads.

The entry point under test is the scorer run as a subprocess (it is a script, not a module), in a throwaway git
repository holding a small plan, under a throwaway HOME. The prediction ledger is a stub module on PYTHONPATH that
appends one line per claim to a file inside the fixture, so a claim is observable and the real ledger is never written.
No docs/plan file of this repository is read.

Run: python3 -B scripts/loop/test_spec_score_no_claim.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SCORER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spec_score.py")
CHECKLIST = "\n".join("%s yes" % k for k in ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10", "S11", "S12"))
STUB = ("import os\n\n\ndef predict(predictor, subject, claim=None, confidence=None, **kw):\n"
        "    with open(os.environ['PG1D_CLAIMS'], 'a', encoding='utf-8') as fh:\n"
        "        fh.write('%s %s\\n' % (predictor, subject))\n")
FILES = ("scripts/x.py", "scripts/test_x.py")


def good(sub):
    """A section the scorer gives 10 (its unit spec carries the requirement, mutation and edge lines)."""
    return ("### %s The thing\n\nFiles: `scripts/x.py` (existing), `scripts/test_x.py` (existing)\n\n"
            "```python\ndef thing(value: int) -> int:\n    \"\"\"Doubles.\"\"\"\n```\n\n"
            "Done check: `python3 -B scripts/test_x.py`\n\n%s\n\n" % (sub, CHECKLIST))


def bad(sub):
    """A section the scorer gives well under 9: no files, no signatures, no done check, no checklist."""
    return "### %s The other thing\n\nIt does something. TBD.\n\n" % sub


TAIL = ("## Requirements\n\nR1 maps to `thing`.\n\n## Mutations\n\nM-X-ONE: thing returns 0. M-X-TWO: thing is skipped.\n\n"
        "## Edges and failures\n\nEdges: empty, corrupt, concurrent, stale.\n")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pg1d_")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.claims = os.path.join(self.root, "claims.txt")
        self.stub = os.path.join(self.root, "stub")
        os.makedirs(self.stub)
        with open(os.path.join(self.stub, "prediction_ledger.py"), "w", encoding="utf-8") as fh:
            fh.write(STUB)
        os.makedirs(os.path.join(self.root, "scripts"))
        os.makedirs(os.path.join(self.root, "docs", "plan", "specs"))
        for rel in FILES:
            with open(os.path.join(self.root, rel), "w", encoding="utf-8") as fh:
                fh.write("x = 1\n")

    def build(self, sections):
        """One unit X whose sub units are the keys of `sections` in order; the spec is their text plus TAIL."""
        subs = list(sections)
        unit = {"id": "X", "spec": "docs/plan/specs/X.md", "sub_units": subs, "worker": "the session"}
        with open(os.path.join(self.root, "docs", "plan", "T.json"), "w", encoding="utf-8") as fh:
            json.dump({"units": [unit]}, fh)
        with open(os.path.join(self.root, "docs", "plan", "specs", "X.md"), "w", encoding="utf-8") as fh:
            fh.write("# X\n\n## 2. Sub-units\n\n" + "".join(sections[s] for s in subs) + TAIL)
        for argv in (["git", "init", "-q"], ["git", "add", "-A"],
                     ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"]):
            done = subprocess.run(argv, cwd=self.root, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, (argv, done.stderr))

    def score(self, *argv):
        env = dict(os.environ, BROTHER_WBS="docs/plan/T.json", HOME=self.root, PYTHONPATH=self.stub,
                   PG1D_CLAIMS=self.claims)
        return subprocess.run([sys.executable, "-B", SCORER] + list(argv), cwd=self.root, env=env,
                              capture_output=True, text=True, timeout=300)

    def claimed(self):
        if not os.path.exists(self.claims):
            return []
        with open(self.claims, encoding="utf-8") as fh:
            return fh.read().split("\n")[:-1]


class TestNoClaim(Fixture):
    def test_the_fixture_scores_what_the_cases_assume(self):
        self.build({"X.a": good("X.a"), "X.b": bad("X.b")})
        out = self.score("X")
        lines = {l.split()[0]: l for l in out.stdout.splitlines() if l.startswith("X.")}
        self.assertIn(" 10/10", lines["X.a"], out.stdout + out.stderr)
        self.assertNotIn(" 10/10", lines["X.b"])
        self.assertIn("1 at 9 or 10 (may start) | 1 under 9 (HELD)", out.stdout)

    def test_no_claim_records_nothing(self):
        self.build({"X.a": good("X.a"), "X.b": good("X.b")})
        out = self.score("X", "--no-claim")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.claimed(), [])
        self.assertIn("X.a", out.stdout)
        self.assertIn("X.b", out.stdout)
        two = self.score("--no-claim", "--no-claim", "X")   # given twice, before the unit: still the same run
        self.assertEqual((two.returncode, self.claimed()), (0, []), two.stderr)

    def test_no_claim_exit_follows_the_floor(self):
        self.build({"X.a": good("X.a")})
        self.assertEqual(self.score("X", "--no-claim").returncode, 0)                  # one at 10, floor 9
        self.assertEqual(self.score("X", "--no-claim", "--floor", "10").returncode, 0)  # exactly at the floor
        self.assertEqual(self.score("X", "--no-claim", "--floor", "11").returncode, 1)  # nothing can pass
        self.assertEqual(self.score("X", "--no-claim", "--floor", "-1").returncode, 0)
        self.assertEqual(self.score("NOPE", "--no-claim").returncode, 2)               # nothing scored is never 0
        self.assertEqual(self.score("X Y", "--no-claim").returncode, 2)                # an id with a space matches nothing
        self.build({"X.a": good("X.a"), "X.b": bad("X.b")})
        one_low = self.score("X", "--no-claim")
        self.assertEqual(one_low.returncode, 1, one_low.stdout)                        # one under the floor
        self.assertIn("X.b", one_low.stdout)
        self.assertEqual(self.score("X", "--no-claim", "--floor", "0").returncode, 0)

    def test_without_no_claim_the_exit_is_zero_and_the_claim_is_recorded(self):
        self.build({"X.a": good("X.a"), "X.b": bad("X.b")})
        out = self.score("X")
        self.assertEqual(out.returncode, 0, out.stderr)                                # even with a sub unit under 9
        self.assertEqual(sorted(self.claimed()), ["spec_score X.a", "spec_score X.b"])
        self.assertEqual(self.score("NOPE").returncode, 0)                             # and with nothing scored
        self.assertEqual(self.score("X", "--floor", "11").returncode, 0)               # a floor alone changes no exit

    def test_an_unknown_flag_or_bad_floor_refuses(self):
        self.build({"X.a": good("X.a")})
        for argv in (["X", "--nope"], ["X", "--no-claims"], ["X", "--floor", "9.5"], ["X", "--floor", "nine"],
                     ["X", "--floor"], ["X", "--json"], ["X", "--json", "--no-claim"],
                     ["X", "--floor", "--no-claim"], ["X", "--floor=9"]):
            out = self.score(*argv)
            self.assertEqual(out.returncode, 2, (argv, out.stdout, out.stderr))
            self.assertIn("usage: spec_score.py", out.stderr)
            self.assertEqual((out.stdout, self.claimed()), ("", []), argv)             # refused before scoring anything

    def test_json_output_is_written_in_both_modes(self):
        self.build({"X.a": good("X.a"), "X.b": bad("X.b")})
        for flags, code in (([], 0), (["--no-claim"], 1)):
            path = os.path.join(self.root, "out%d.json" % code)
            out = self.score("X", "--json", path, *flags)
            self.assertEqual(out.returncode, code, out.stderr)
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(sorted(data), ["X.a", "X.b"])
            self.assertEqual(data["X.a"]["score"], 10)
            self.assertLess(data["X.b"]["score"], 9)
        before = self.claimed()
        stale = os.path.join(self.root, "stale.json")
        with open(stale, "w", encoding="utf-8") as fh:
            fh.write("{\"old\": 1}")
        self.score("NOPE", "--json", stale, "--no-claim")
        with open(stale, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {})                                         # no stale scores under this run's name
        self.assertEqual(self.claimed(), before)


if __name__ == "__main__":
    unittest.main()
