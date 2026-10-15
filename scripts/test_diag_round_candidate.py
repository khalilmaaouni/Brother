#!/usr/bin/env python3
"""diag_round --code-root (owner decision 2026-10-14): the diagnostician runs outside a proof from the deployed frozen
candidate, never from the landing tree, and a parked sub unit still gets its diagnosis note.

Drives diag_round.main() itself (the entry point), with the brief tool and the model call stubbed: no model, no network.
A scratch landing tree T (the cwd) carries a TRIPWIRE or_fanout.py; a candidate C carries a stub fan out that writes the
answer a model would give. Real diag_apply runs the proving grep in T (data only) and writes the note through diag_notes.
Guard cases isolate one condition each: the module is missing, the path is relative, the path is the landing tree, the path
is inside it. Run: python3 -B scripts/test_diag_round_candidate.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import diag_round as D  # noqa: E402

FANOUT_REL = os.path.join("plugin", "runtime", "brother", "core", "or_fanout.py")
STUB_FANOUT = '''import json, os, sys
open(os.path.join(os.environ["HOME"], "fanout-witness"), "a").write(os.getcwd() + "\\n")
for job in json.load(open(sys.argv[1])):
    os.makedirs(os.path.dirname(job["out"]), exist_ok=True)
    json.dump({"fact": "token is defined", "prove": "grep token sample.txt", "expect": "token", "hint": "see sample.txt"}, open(job["out"], "w"))
'''
TRIPWIRE = 'import os\nopen(os.path.join(os.environ["HOME"], "tripwire"), "a").write(os.getcwd() + "\\n")\n'
STUB_BRIEF = '''import json, os, sys
out = sys.argv[1]
open(os.path.join(os.environ["HOME"], "brief-witness"), "a").write("asked\\n")
json.dump([{"id": "diag-X.a", "out": out + "/out/X.a.json"}], open(out + "/jobs.json", "w"))
print("1 job(s) -> jobs.json")
'''


def w(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)


class Round(unittest.TestCase):
    def setUp(self):
        root = os.path.join(os.path.expanduser("~"), ".claude", "brother-scratch")
        os.makedirs(root, exist_ok=True)
        self.box = tempfile.mkdtemp(prefix="run-diag-cand-", dir=root)
        self.addCleanup(shutil.rmtree, self.box, True)
        self.home = os.path.join(self.box, "home")
        self.tree = os.path.join(self.box, "tree")
        self.cand = os.path.join(self.box, "cand")
        os.makedirs(os.path.join(self.home, ".claude", "evidence", "unit-runs"))
        spec = os.path.join(self.box, "X.md")
        w(spec, "# X\n### X.a\nDone check: `python3 -B scripts/test_x.py`\n")
        w(os.path.join(self.tree, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json"),
          json.dumps({"units": [{"id": "X", "state": "PARTIAL", "spec": spec, "sub_units": ["X.a"], "evidence": ""}]}))
        w(os.path.join(self.tree, "sample.txt"), "token\n")
        w(os.path.join(self.tree, FANOUT_REL), TRIPWIRE)     # a landing tree fan out: it must never run
        w(os.path.join(self.cand, FANOUT_REL), STUB_FANOUT)
        w(os.path.join(self.box, "stub_brief.py"), STUB_BRIEF)
        self.saved = (os.getcwd(), dict(os.environ), D.brief_cmd, D.BIN)
        self.addCleanup(self.restore)
        os.environ.update(HOME=self.home)
        for k in [k for k in os.environ if k.startswith("BROTHER_")]:
            del os.environ[k]
        os.chdir(self.tree)
        D.brief_cmd = lambda out, subs, dry=False, limit=None: [sys.executable, os.path.join(self.box, "stub_brief.py"), out]
        D.apply_cmd = lambda out: [sys.executable, os.path.join(HERE, "loop", "diag_apply.py"), out]

    def restore(self):
        os.chdir(self.saved[0])
        os.environ.clear(); os.environ.update(self.saved[1])
        D.brief_cmd = self.saved[2]
        D.apply_cmd = lambda out: [sys.executable, os.path.join(D.BIN, "diag_apply.py"), out]

    def read(self, name):
        p = os.path.join(self.home, name)
        if not os.path.isfile(p):
            return ""
        with open(p, encoding="utf-8") as fh:
            return fh.read()

    def go(self, code_root):
        return D.main(["--limit", "6", "--code-root", code_root, "--out", os.path.join(self.box, "out")])

    def test_a_parked_sub_unit_gets_a_note_and_the_fan_out_runs_from_the_candidate(self):
        rc = self.go(self.cand)
        self.assertEqual(rc, 0)
        self.assertEqual(self.read("fanout-witness").strip(), os.path.realpath(self.cand))
        self.assertEqual(self.read("tripwire"), "", "landing tree code ran")
        note = os.path.join(self.home, ".claude", "evidence", "diag-notes", "X.txt")
        with open(note, encoding="utf-8") as fh:
            self.assertIn("X.a: VERIFIED FACT: token is defined see sample.txt", fh.read())

    def refused(self, code_root):
        rc = self.go(code_root)
        self.assertEqual(rc, 2)
        self.assertEqual(self.read("brief-witness") + self.read("fanout-witness") + self.read("tripwire"), "")

    def test_a_candidate_without_the_fan_out_module_is_refused(self):
        empty = os.path.join(self.box, "empty"); os.makedirs(empty)
        self.refused(empty)

    def test_a_relative_code_root_is_refused(self):
        self.refused(os.path.relpath(self.cand, self.tree))

    def test_the_landing_tree_as_code_root_is_refused(self):
        self.refused(self.tree)

    def test_a_code_root_inside_the_landing_tree_is_refused(self):
        inner = os.path.join(self.tree, "inner")
        w(os.path.join(inner, FANOUT_REL), TRIPWIRE)
        self.refused(inner)


if __name__ == "__main__":
    unittest.main()
