#!/usr/bin/env python3
"""The three verdicts of donecheck_recon.py, each from a fixture only that verdict can produce."""
import json, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(HERE, "donecheck_recon.py")


def run(path):
    p = subprocess.run([sys.executable, CHECK, "--file", path], capture_output=True, text=True)
    return p.returncode, p.stdout


class Verdicts(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="recon-")

    def write(self, items):
        path = os.path.join(self.dir, "r.json")
        with open(path, "w") as fh:
            json.dump({"items": items}, fh)
        return path

    def test_pass_when_every_item_disposed_and_integrate_checks_exit_zero(self):
        rc, out = run(self.write([{"id": "a", "disposition": "DROP-SAFE"}, {"id": "b", "disposition": "INTEGRATE", "check": "true"}]))
        self.assertEqual((rc, out.splitlines()[-1][:4]), (0, "PASS"), out)

    def test_fail_on_an_item_with_no_disposition(self):
        rc, out = run(self.write([{"id": "a", "disposition": "DROP-SAFE"}, {"id": "b"}]))
        self.assertEqual(rc, 1, out); self.assertIn("no disposition: b", out)

    def test_fail_on_an_integrate_item_whose_check_fails(self):
        rc, out = run(self.write([{"id": "b", "disposition": "INTEGRATE", "check": "false"}]))
        self.assertEqual(rc, 1, out); self.assertIn("not contained", out)

    def test_no_data_on_missing_corrupt_or_empty_file(self):
        self.assertEqual(run(os.path.join(self.dir, "absent.json"))[0], 2)
        bad = os.path.join(self.dir, "bad.json"); open(bad, "w").write("{not json")
        self.assertEqual(run(bad)[0], 2)
        self.assertEqual(run(self.write([]))[0], 2)

    def test_no_data_on_an_integrate_item_with_no_check(self):
        rc, out = run(self.write([{"id": "b", "disposition": "INTEGRATE"}]))
        self.assertEqual(rc, 2, out); self.assertIn("no check", out)


if __name__ == "__main__":
    unittest.main()
