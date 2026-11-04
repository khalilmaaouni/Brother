#!/usr/bin/env python3
"""scripts/burn_guard.py main() reads the cap through read_cap (STAB1.g review): an expired live grant funds no
lane. Before the fix main read daily_cap alone and ignored until, so a grant that lapsed in 2000 printed LANES 8 and
exited 0 while read_cap answered FAIL with 0.0 for the same file. Run as the program, in a sandbox state root."""
import json, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "burn_guard.py")


class MainReadsTheOneCapReader(unittest.TestCase):
    def setUp(self):
        self.st = tempfile.mkdtemp(prefix="burn-main-expiry-")
        self.addCleanup(shutil.rmtree, self.st, True)
        with open(os.path.join(self.st, "openrouter-ledger.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("")

    def run_main(self, grant):
        with open(os.path.join(self.st, "cap-grant.json"), "w", encoding="utf-8") as fh:
            json.dump(grant, fh)
        env = dict(os.environ, BROTHER_OR_STATE_ROOT=self.st)
        return subprocess.run([sys.executable, "-B", TOOL, "--stop-hour", "7"], env=env, capture_output=True,
                              text=True, timeout=120)

    def test_an_expired_live_grant_funds_no_lane(self):
        r = self.run_main({"daily_cap": 20, "until": "2000-01-01T00:00:00+09:00", "grant_note": "n"})
        self.assertEqual(3, r.returncode, r.stdout + r.stderr)
        self.assertEqual("0", r.stdout.strip().splitlines()[-1], r.stdout)
        self.assertNotIn("LANES", r.stdout)
        self.assertIn("expired", r.stdout)

    def test_a_grant_with_no_until_funds_no_lane(self):
        r = self.run_main({"daily_cap": 20})
        self.assertEqual(3, r.returncode, r.stdout + r.stderr)
        self.assertNotIn("LANES", r.stdout)

    def test_a_live_grant_still_funds(self):  # control: the fix must not refuse every grant
        r = self.run_main({"daily_cap": 20, "until": "2999-01-01T00:00:00+09:00", "grant_note": "n"})
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertIn("LANES", r.stdout)
        self.assertIn("of 20.00 USD", r.stdout)


if __name__ == "__main__":
    unittest.main()
