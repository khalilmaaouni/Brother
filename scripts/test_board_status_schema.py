"""board_status refuses a board it cannot read instead of printing a success-shaped empty report (review item 9).

Measured 2026-09-26: `board_status.py --source docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json` exited 0 and counted nothing,
because this tool reads the features / rows / gates sections and the launch board carries `units`. Its headline line
came from another file, so the report looked like an answer. The contract: a source holding none of the sections
this tool counts is NO-DATA, exit 2, in every mode (text, --json, --item), and the reply names the keys it did find;
a board that does carry a known section keeps working exactly as before.
Run from the repository root: python3 -B scripts/test_board_status_schema.py
"""
import json, os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "scripts", "board_status.py")


def board(doc):
    d = tempfile.mkdtemp(prefix="bs-schema-")
    p = os.path.join(d, "board.json")
    with open(p, "w") as f:
        json.dump(doc, f)
    return d, p


def run(src, *extra):
    d = os.path.dirname(src)
    env = dict(os.environ, HOME=d, BROTHER_UNIT_TRACE=os.path.join(d, "trace.jsonl"))
    return subprocess.run([sys.executable, "-B", TOOL, "--source", src] + list(extra), cwd=d, env=env,
                          capture_output=True, text=True, timeout=120)


UNITS = {"schema": "launch", "units": [{"id": "H2", "state": "PARTIAL", "evidence": ""}, {"id": "H3", "state": "DONE", "evidence": "x"}]}


class UnknownSchema(unittest.TestCase):
    def test_text_mode_refuses(self):
        _, p = board(UNITS)
        r = run(p)
        self.assertEqual(r.returncode, 2, r.stdout[-300:])
        self.assertIn("NO-DATA", r.stdout + r.stderr)
        self.assertIn("units", r.stdout + r.stderr, "the refusal names the keys the source does carry")

    def test_json_mode_refuses(self):
        _, p = board(UNITS)
        r = run(p, "--json")
        self.assertEqual(r.returncode, 2)

    def test_item_mode_refuses(self):
        _, p = board(UNITS)
        r = run(p, "--item", "H2")
        self.assertEqual(r.returncode, 2)
        self.assertIn("NO-DATA", r.stdout + r.stderr)


class KnownSchemaStillWorks(unittest.TestCase):
    def test_features_board_reads(self):
        _, p = board({"features": [{"id": "F1", "name": "one", "status": "DONE", "evidence": "ran x, exit 0"}]})
        r = run(p, "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        json.loads(r.stdout)

    def test_item_on_a_features_board(self):
        _, p = board({"features": [{"id": "F1", "name": "one", "status": "DONE", "evidence": "ran x, exit 0"}]})
        r = run(p, "--item", "F1")
        self.assertEqual(r.returncode, 0, r.stderr[-300:])


if __name__ == "__main__":
    unittest.main(verbosity=1)
