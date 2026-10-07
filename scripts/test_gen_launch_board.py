#!/usr/bin/env python3
"""gen_launch_board.py renders the 1.1.0 board from a plan whose waves mix numbers and gate names, and its prose is dated:
notes from today are stamped with their time, notes from another day are stamped STALE, and no notes print NO-DATA.
Each case runs the generator end to end on a fixture repository under a scratch HOME, so nothing real is read.
Run: python3 -B scripts/test_gen_launch_board.py"""
import datetime, json, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.join(HERE, "gen_launch_board.py")
# THE TEMPLATE IS A FIXTURE: docs/plan is not in the exported tree, so the test carries every placeholder the generator
# fills; an unfilled one survives as "{{" and fails the render case.
SLOTS = ("NOW HEAD CHIPS G1 HOURS G2 FAMS WORKLOG NUNITS NCOMMITS LIOK LIIDS DONE PARTIAL SPECIFIED OPEN NSUBS FAILED "
         "NOTES_STAMP NOW_TEXT WAITING_TEXT RISKWATCH_TEXT WAITING_CARDS RISK_CARDS DECISION_CARDS").split()


class Board(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="launch-board-")
        self.repo, self.home = os.path.join(self.d, "repo"), os.path.join(self.d, "home")
        os.makedirs(os.path.join(self.repo, "docs", "plan", "board"))
        os.makedirs(os.path.join(self.home, ".claude", "evidence", "or-wave4"))
        with open(os.path.join(self.home, ".claude", "evidence", "or-wave4", "mechanical-grade.json"), "w") as f:
            json.dump([], f)
        with open(os.path.join(self.repo, "docs", "plan", "board", "launch-board-template.html"), "w") as f:
            f.write("<title>t</title>" + "".join("<p>{{%s}}</p>" % k for k in SLOTS))
        units = [{"id": "A1", "title": "a", "state": "DONE", "evidence": "ok", "wave": 1},
                 {"id": "A2", "title": "b", "state": "BLOCKED", "wave": "owner"},
                 {"id": "A3", "title": "c", "state": "DEFERRED", "wave": "after A1"}]
        with open(os.path.join(self.repo, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json"), "w") as f:
            json.dump({"units": units}, f)
        subprocess.run(["git", "init", "-q", self.repo], check=True)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def render(self, notes):
        path = os.path.join(self.repo, "docs", "plan", "board", "launch-board-notes.json")
        if notes is not None:
            with open(path, "w") as f:
                json.dump(notes, f)
        out = os.path.join(self.d, "board.html")
        r = subprocess.run([sys.executable, "-B", GEN, self.repo, out], capture_output=True, text=True,
                           env=dict(os.environ, HOME=self.home), timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return open(out, encoding="utf-8").read(), r.stdout

    def test_mixed_waves_render_and_every_state_is_counted(self):
        page, out = self.render({"as_of": datetime.date.today().isoformat() + " 10:00 JST"})
        self.assertIn("'BLOCKED': 1", out)
        self.assertIn("'DEFERRED': 1", out)
        self.assertIn("Wave owner", page)
        self.assertNotIn("{{", page)

    def test_notes_from_today_carry_their_stamp(self):
        page, _ = self.render({"as_of": datetime.date.today().isoformat() + " 10:00 JST", "now": "fresh words"})
        self.assertIn("Notes as of", page)
        self.assertNotIn("STALE", page)
        self.assertIn("fresh words", page)

    def test_notes_from_another_day_are_stamped_stale(self):
        page, _ = self.render({"as_of": "2000-01-01 10:00 JST"})
        self.assertIn("STALE: Notes as of 2000-01-01", page)

    def test_no_notes_file_is_no_data_never_old_prose(self):
        page, _ = self.render(None)
        self.assertIn("NO-DATA: no dated notes", page)


if __name__ == "__main__":
    unittest.main()
