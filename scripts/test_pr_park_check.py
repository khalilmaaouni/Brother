#!/usr/bin/env python3
"""ACC4.b suite over scripts/pr_park_state.py and scripts/close_superseded.sh
(spec docs/plan/specs/ACC4.md).

Hermetic: a fake `gh` first on PATH (from test_pr_park_triage) logs every
verb and answers from files; a fixture git repository stands in for
hub/main; no network. The real closer is never run against GitHub here.

Exit contract, same shape as this estate's other suites: 0 all assertions
pass, 1 an assertion failed.

Python 3, stdlib only. No em or en dashes anywhere in this file.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pr_park_state as S  # noqa: E402
from test_pr_park_triage import make_repo, pr, write_fake_gh  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                     % os.path.basename(__file__))

HERE = os.path.dirname(os.path.abspath(__file__))
CLOSER = os.path.join(HERE, "close_superseded.sh")
REASON = "Superseded: landed on main through its verified port"


def view(state, *comments):
    return {"state": state, "comments": [{"body": c} for c in comments]}


class ParkList(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="acc4b-list-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, text, name="list.txt"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return path

    def test_rows_comments_and_blanks(self):
        path = self.write("# header\n\n583 %s\n601 another reason\r\n" % REASON)
        self.assertEqual(S.read_park_list(path), [(583, REASON), (601, "another reason")])
        self.assertEqual(S.read_park_list(self.write("# only a header\n")), [])

    def test_refusals(self):
        for text in ("583\n", "583  \n", "abc reason\n", "583 bad\x01reason\n", "583 a\n583 b\n"):
            with self.assertRaises(ValueError, msg=repr(text)):
                S.read_park_list(self.write(text))
        with self.assertRaises(FileNotFoundError):
            S.read_park_list(os.path.join(self.tmp, "absent.txt"))


class Comment(unittest.TestCase):
    def test_closing_comment(self):
        self.assertEqual(S.closing_comment(view("CLOSED", "first", REASON)), REASON)
        self.assertIsNone(S.closing_comment(view("CLOSED")))
        self.assertIsNone(S.closing_comment(view("OPEN", REASON)))
        self.assertIsNone(S.closing_comment(view("MERGED", REASON)))
        self.assertIsNone(S.closing_comment({"state": "CLOSED", "comments": "odd"}))
        self.assertIsNone(S.closing_comment("not a view"))


class Errors(unittest.TestCase):
    def test_b2_listed_still_open_or_wrong_comment(self):
        rows = [(583, REASON), (601, REASON), (607, REASON), (679, REASON), (690, REASON)]
        closed = {601: REASON, 607: "a different comment", 679: "", 690: "Superseded:  landed on main\nthrough its verified port"}
        errors = S.park_errors([583, 874], rows, closed)
        self.assertEqual(len(errors), 3, errors)
        self.assertIn("#583 is listed in the park list but still OPEN", errors)
        self.assertTrue(any(e.startswith("#607") and "differs" in e for e in errors), errors)
        self.assertTrue(any(e.startswith("#679") and "no closing comment" in e for e in errors), errors)
        self.assertEqual(S.park_errors([874], rows, {n: REASON for n, _ in rows}), [])

    def test_merged_or_absent_needs_no_comment(self):
        self.assertEqual(S.park_errors([], [(903, REASON)], {}), [])

    def test_b4_the_count_bar_stays_and_is_never_the_only_check(self):
        rows = [(583, REASON)]
        errors = S.park_errors(list(range(1, 14)), rows, {583: REASON})
        self.assertEqual(errors, ["13 pull requests are open, over the bar of %d" % S.MAX_OPEN_PRS])
        self.assertEqual(S.park_errors(list(range(1, 13)), rows, {583: REASON}), [])
        self.assertEqual(S.park_errors([], [], {}), [])

    def test_b3_verdict_never_passes_without_gh(self):
        self.assertEqual(S.verdict([], False), "NO-DATA")
        self.assertEqual(S.verdict(["x"], False), "NO-DATA")
        self.assertEqual(S.verdict(["x"], True), "FAIL")
        self.assertEqual(S.verdict([], True), "PASS")


class Script(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="acc4b-script-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.ancestor, self.patch_equal, self.unrelated = make_repo(self.repo)
        self.env = write_fake_gh(self.tmp)
        self.env["PR_PARK_MAIN_REF"] = "main"
        self.env["PR_PARK_REPO"] = "o/r"
        self.list = os.path.join(self.tmp, "park.txt")

    def gh_files(self, prs, views):
        with open(os.path.join(self.tmp, "list.json"), "w") as fh:
            json.dump(prs, fh)
        for number, v in views.items():
            with open(os.path.join(self.tmp, "%d.json" % number), "w") as fh:
                json.dump(v, fh)

    def park(self, *lines):
        with open(self.list, "w") as fh:
            fh.write("# header\n" + "".join(l + "\n" for l in lines))

    def run_state(self, *args):
        cmd = [sys.executable, "-B", os.path.join(HERE, "pr_park_state.py"), self.list] + list(args)
        return subprocess.run(cmd, cwd=self.repo, env=self.env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)

    def run_closer(self, *args):
        return subprocess.run(["bash", CLOSER] + list(args), cwd=self.repo, env=self.env,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def log(self):
        path = os.path.join(self.tmp, "gh.log")
        return open(path).read().splitlines() if os.path.exists(path) else []

    def test_pass_after_the_owner_acted(self):
        self.gh_files([pr(874, self.unrelated, updated="2026-10-02T00:00:00Z", mergeable="UNKNOWN")],
                      {583: view("CLOSED", "earlier", REASON), 903: view("MERGED")})
        self.park("583 " + REASON, "903 " + REASON)
        p = self.run_state()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("PASS: 1 open (bar 12), 2 listed, 0 error(s)", p.stdout)
        verbs = [line.split()[1] for line in self.log() if line.startswith("pr ")]
        self.assertEqual(set(verbs), {"list", "view"}, verbs)

    def test_fail_listed_open_wrong_comment_and_new_idle(self):
        self.gh_files([pr(583, self.unrelated), pr(700, self.unrelated), pr(7, self.unrelated, updated="2026-10-01T00:00:00Z")],
                      {601: view("CLOSED", "wrong"), 999: view("CLOSED")})
        self.park("583 " + REASON, "601 " + REASON, "999 " + REASON)
        p = self.run_state()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("ERROR: #583 is listed in the park list but still OPEN", p.stdout)
        self.assertIn("ERROR: #601 is CLOSED but its last comment differs", p.stdout)
        self.assertIn("ERROR: #999 is CLOSED with no closing comment", p.stdout)
        self.assertIn("ERROR: #700 reads IDLE-UNCLASSIFIED and is not on the park list", p.stdout)
        self.assertNotIn("#7 reads", p.stdout)
        self.assertIn("FAIL:", p.stdout)

    def test_no_data_for_dead_gh_missing_list_and_unknown_number(self):
        self.gh_files([], {})
        self.park("583 " + REASON)
        open(os.path.join(self.tmp, "gh.fail"), "w").close()
        p = self.run_state()
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("NO-DATA", p.stdout)
        os.remove(os.path.join(self.tmp, "gh.fail"))
        p = self.run_state()  # 583 cannot be viewed: it never existed
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("NO-DATA: gh pr view 583", p.stdout)
        os.remove(self.list)
        p = self.run_state()
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("NO-DATA: park list", p.stdout)

    def test_empty_list_passes_only_within_the_bar(self):
        self.gh_files([pr(n, self.unrelated, updated="2026-10-02T00:00:00Z") for n in range(1, 13)], {})
        self.park()
        p = self.run_state()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.gh_files([pr(n, self.unrelated, updated="2026-10-02T00:00:00Z") for n in range(1, 14)], {})
        p = self.run_state()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("13 pull requests are open, over the bar", p.stdout)

    def test_b1_closer_dry_run_writes_nothing_and_real_run_closes_each_open_once(self):
        self.gh_files([], {583: view("OPEN"), 601: view("OPEN"), 903: view("MERGED")})
        self.park("583 " + REASON, "601 " + REASON, "903 " + REASON)
        p = self.run_closer("--dry-run", self.list)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("WOULD CLOSE #583: " + REASON, p.stdout)
        self.assertIn("WOULD CLOSE #601", p.stdout)
        self.assertIn("SKIP #903: state is MERGED", p.stdout)
        self.assertIn("DONE: 3 of 3 listed PR(s) would close", p.stdout)
        self.assertEqual([l for l in self.log() if l.startswith("pr close")], [])
        p = self.run_closer(self.list)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        closes = [l for l in self.log() if l.startswith("pr close")]
        self.assertEqual(closes, ["pr close 583 -R o/r --comment " + REASON,
                                  "pr close 601 -R o/r --comment " + REASON])
        self.assertIn("DONE: 3 of 3 listed PR(s) closed or already closed", p.stdout)

    def test_closer_stops_at_the_first_refusal_and_no_data_without_a_list(self):
        self.gh_files([], {583: view("OPEN"), 601: view("OPEN")})
        open(os.path.join(self.tmp, "583.refuse"), "w").close()
        self.park("583 " + REASON, "601 " + REASON)
        p = self.run_closer(self.list)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("STOP at #583: close refused", p.stdout)
        self.assertEqual(len([l for l in self.log() if l.startswith("pr close")]), 1)
        self.assertIn("DONE: 0 of 1", p.stdout)
        p = self.run_closer(os.path.join(self.tmp, "absent.txt"))
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("NO-DATA", p.stdout)
        os.remove(self.list)
        self.gh_files([], {999: view("OPEN")})
        self.park("999 " + REASON)
        open(os.path.join(self.tmp, "999.json"), "w").close()
        os.remove(os.path.join(self.tmp, "999.json"))
        p = self.run_closer(self.list)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("STOP at #999: cannot read it", p.stdout)


if __name__ == "__main__":
    unittest.main()
