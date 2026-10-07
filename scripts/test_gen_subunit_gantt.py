#!/usr/bin/env python3
"""gen_subunit_gantt.py renders a plan whose waves mix numbers and gate names ("owner", "after ACC1"): numbers first, then
names, then units with no wave. Measured 2026-09-27: an int compared with a str raised TypeError and no board could be
generated. Runs the generator end to end with --data in a scratch directory under a scratch HOME.
Run: python3 -B scripts/test_gen_subunit_gantt.py"""
import json, os, shutil, subprocess, sys, tempfile, unittest

GEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop", "gen_subunit_gantt.py")


class MixedWaves(unittest.TestCase):
    def test_numbers_then_names_then_none(self):
        d = tempfile.mkdtemp(prefix="subunit-gantt-")
        try:
            os.makedirs(os.path.join(d, "docs", "plan"))
            sub = lambda i: [{"id": i + ".1", "state": "todo", "word": "", "score": None}]
            rows = [{"id": "N", "title": "none", "state": "OPEN", "subs": sub("N"), "wave": None, "depends_on": []},
                    {"id": "O", "title": "owner", "state": "OPEN", "subs": sub("O"), "wave": "owner", "depends_on": []},
                    {"id": "T", "title": "two", "state": "OPEN", "subs": sub("T"), "wave": 2, "depends_on": []},
                    {"id": "A", "title": "one", "state": "OPEN", "subs": sub("A"), "wave": 1, "depends_on": []}]
            data = os.path.join(d, "data.json")
            with open(data, "w") as f:
                json.dump(rows, f)
            r = subprocess.run([sys.executable, "-B", GEN, "--data", data], cwd=d, capture_output=True, text=True,
                               env=dict(os.environ, HOME=d), timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            page = open(os.path.join(d, "docs", "plan", "SUBUNIT-GANTT.html"), encoding="utf-8").read()
            at = [page.index(label) for label in ("Wave 1", "Wave 2", "Wave owner", "No wave assigned")]
            self.assertEqual(at, sorted(at), "waves out of order: %r" % at)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
