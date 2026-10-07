#!/usr/bin/env python3
"""ACC4.a suite over scripts/pr_park_triage.py (spec docs/plan/specs/ACC4.md).

Hermetic: a fixture git repository stands in for hub/main, a fake `gh` first
on PATH records every verb and answers from files, and no network is used.
Runs under an empty HOME on both Pythons.

Exit contract, same shape as this estate's other suites: 0 all assertions
pass, 1 an assertion failed.

Python 3, stdlib only. No em or en dashes anywhere in this file.
"""
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pr_park_triage as T  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                     % os.path.basename(__file__))

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = "2026-10-02T12:00:00Z"
MISSING_OID = "0123456789abcdef0123456789abcdef01234567"

FAKE_GH = r'''#!/usr/bin/env python3
"""Fake gh: logs every call, answers `pr list` and `pr view` from FAKE_GH_DIR, refuses nothing."""
import json, os, sys
d = os.environ["FAKE_GH_DIR"]
args = sys.argv[1:]
with open(os.path.join(d, "gh.log"), "a") as fh:
    fh.write(" ".join(args) + "\n")
if os.path.exists(os.path.join(d, "gh.fail")):
    sys.stderr.write("gh: not logged in\n"); sys.exit(1)
verb = args[1] if len(args) > 1 and args[0] == "pr" else ""
if verb == "list":
    sys.stdout.write(open(os.path.join(d, "list.json")).read()); sys.exit(0)
if verb == "view":
    path = os.path.join(d, args[2] + ".json")
    if not os.path.exists(path):
        sys.stderr.write("no pull request %s\n" % args[2]); sys.exit(1)
    view = json.load(open(path))
    if "-q" in args:
        sys.stdout.write(str(view.get("state", "")) + "\n")
    else:
        sys.stdout.write(json.dumps(view))
    sys.exit(0)
if verb == "close":
    sys.exit(1 if os.path.exists(os.path.join(d, args[2] + ".refuse")) else 0)
sys.exit(0)
'''


def write_fake_gh(folder):
    """A fake gh on `folder`; returns an env with it first on PATH and FAKE_GH_DIR set."""
    path = os.path.join(folder, "gh")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(FAKE_GH)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    env = dict(os.environ)
    env["PATH"] = folder + os.pathsep + env.get("PATH", "")
    env["FAKE_GH_DIR"] = folder
    env["HOME"] = os.path.join(folder, "home")
    os.makedirs(env["HOME"], exist_ok=True)
    return env


def make_repo(folder):
    """main: base, m1. patch_equal: base + cherry-pick of m1. unrelated: base + c.
    Returns (ancestor_oid, patch_equal_oid, unrelated_oid)."""
    def git(*a):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                               "-c", "commit.gpgsign=false"] + list(a), cwd=folder, check=True,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()

    def commit(name, msg):
        with open(os.path.join(folder, name), "w") as fh:
            fh.write(name + "\n")
        git("add", name)
        git("commit", "-qm", msg)
        return git("rev-parse", "HEAD")

    git("init", "-q", "-b", "main")
    commit("base", "base")
    git("branch", "patch_equal")
    git("branch", "unrelated")
    ancestor = commit("m1", "m1")
    git("checkout", "-q", "patch_equal")
    git("cherry-pick", ancestor)
    patch_equal = git("rev-parse", "HEAD")
    git("checkout", "-q", "unrelated")
    unrelated = commit("c", "c")
    git("checkout", "-q", "main")
    return ancestor, patch_equal, unrelated


def pr(number, head, updated="2026-09-10T00:00:00Z", mergeable="CONFLICTING", draft=False, state="OPEN"):
    """A fixture record in the shape of `gh pr list --json`, idle 22 days at NOW unless told otherwise."""
    return {"number": number, "state": state, "updatedAt": updated, "isDraft": draft, "mergeable": mergeable,
            "headRefName": "b%d" % number, "headRefOid": head, "baseRefName": "main"}


class Landed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="acc4-repo-")
        cls.ancestor, cls.patch_equal, cls.unrelated = make_repo(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def test_a2_landed_is_proven_by_git(self):
        self.assertIs(T.is_landed(self.ancestor, "main", cwd=self.tmp), True)
        self.assertIs(T.is_landed(self.patch_equal, "main", cwd=self.tmp), True)
        self.assertIs(T.is_landed(self.unrelated, "main", cwd=self.tmp), False)

    def test_absent_object_and_bad_input_are_none_never_true(self):
        self.assertIsNone(T.is_landed(MISSING_OID, "main", cwd=self.tmp))
        self.assertIsNone(T.is_landed("", "main", cwd=self.tmp))
        self.assertIsNone(T.is_landed(None, "main", cwd=self.tmp))
        self.assertIsNone(T.is_landed("not a sha", "main", cwd=self.tmp))
        self.assertIsNone(T.is_landed(self.ancestor, "no_such_ref", cwd=self.tmp))

    def test_cherry_nonzero_or_odd_line_is_none(self):
        with mock.patch.object(T, "_git", side_effect=[(1, ""), (128, "fatal")]):
            self.assertIsNone(T.is_landed(self.unrelated, "main", cwd=self.tmp))
        with mock.patch.object(T, "_git", side_effect=[(1, ""), (0, "- abc1234\nwarning: odd\n")]):
            self.assertIsNone(T.is_landed(self.unrelated, "main", cwd=self.tmp))
        with mock.patch.object(T, "_git", side_effect=[(1, ""), (0, "")]):
            self.assertIsNone(T.is_landed(self.unrelated, "main", cwd=self.tmp))
        with mock.patch.object(T, "_git", side_effect=[(1, ""), (0, "- abc1234\n+ def5678\n")]):
            self.assertIs(T.is_landed(self.unrelated, "main", cwd=self.tmp), False)
        with mock.patch.object(T, "_git", side_effect=[(1, ""), (0, "- abc1234\n- def5678\n")]):
            self.assertIs(T.is_landed(self.unrelated, "main", cwd=self.tmp), True)


class Classify(unittest.TestCase):
    def test_a1_twelve_records_twelve_classes(self):
        fixtures = [
            (pr(1, "a"), True, None, 0), (pr(2, "b"), False, None, 1), (pr(3, "c"), False, None, 6),
            (pr(4, "d"), False, None, 7), (pr(5, "e"), False, None, 15), (pr(6, "f"), None, None, 15),
            (pr(7, "g"), False, "listed", 15), (pr(8, "h", mergeable="MERGEABLE"), False, "listed", 15),
            (pr(9, "i"), False, "listed", 0), (pr(10, "j"), True, "listed", 0),
            (pr(11, "k"), False, None, None), (pr(12, "l", draft=True), False, None, 3),
        ]
        classes = [T.classify_pr(*f) for f in fixtures]
        self.assertEqual(len(classes), len(fixtures))
        self.assertTrue(all(c in T.CLASSES for c in classes), classes)
        self.assertEqual(classes, ["LANDED", "LIVE", "LIVE", "IDLE-UNCLASSIFIED", "IDLE-UNCLASSIFIED",
                                   "NO-DATA", "SUPERSEDED", "IDLE-UNCLASSIFIED", "IDLE-UNCLASSIFIED",
                                   "LANDED", "NO-DATA", "LIVE"])

    def test_a5_a_listing_is_corroborated_never_trusted(self):
        self.assertNotEqual(T.classify_pr(pr(9, "i"), False, "listed", 0), "SUPERSEDED")
        self.assertEqual(T.classify_pr(pr(9, "i"), False, "listed", 0), "IDLE-UNCLASSIFIED")
        self.assertEqual(T.classify_pr(pr(9, "i"), False, "listed", T.IDLE_DAYS), "SUPERSEDED")
        self.assertEqual(T.classify_pr(pr(9, "i", mergeable="UNKNOWN"), False, "listed", 30), "IDLE-UNCLASSIFIED")

    def test_pr_874_shape_reads_live_under_the_idle_line(self):
        row = T.triage([pr(874, MISSING_OID, updated="2026-09-26T10:40:17Z")], {}, "main", NOW)[0]
        self.assertEqual(row["class"], "NO-DATA")  # head absent locally: never landed, never live by default
        with mock.patch.object(T, "is_landed", return_value=False):
            row = T.triage([pr(874, MISSING_OID, updated="2026-09-26T10:40:17Z")], {}, "main", NOW)[0]
        self.assertEqual(row["class"], "LIVE")
        self.assertEqual(row["idle_days"], "6")


class Triage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="acc4-triage-")
        cls.ancestor, cls.patch_equal, cls.unrelated = make_repo(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def rows(self, prs, listed=None, **kw):
        return T.triage(prs, listed or {}, "main", NOW, cwd=self.tmp, **kw)

    def test_zero_one_and_many(self):
        self.assertEqual(self.rows([]), [])
        self.assertEqual(len(self.rows([pr(1, self.ancestor)])), 1)
        self.assertEqual(len(self.rows([pr(n, self.unrelated) for n in range(1, 101)])), 100)

    def test_a3_write_verbs_are_refused(self):
        for verb in ("close", "edit", "comment", "merge"):
            with self.assertRaises(ValueError):
                T.triage([], {}, "main", NOW, verb=verb)
            with self.assertRaises(ValueError):
                T.gh_pr(verb, ["1"], gh_bin="/nonexistent/gh")
        self.assertEqual(T.refuse_write_verb("list"), "list")
        self.assertEqual(T.refuse_write_verb("view"), "view")

    def test_a6_missing_close_list_raises_never_empty(self):
        with self.assertRaises(FileNotFoundError):
            self.rows([pr(1, self.unrelated)], close_list_path=os.path.join(self.tmp, "absent.txt"))
        with self.assertRaises(FileNotFoundError):
            T.read_close_list(os.path.join(self.tmp, "absent.txt"))

    def test_close_list_is_read_and_corroborated(self):
        path = os.path.join(self.tmp, "close_list.txt")
        with open(path, "w") as fh:
            fh.write("# header\n\n7 Superseded: landed by its port\n9 Superseded: too\n")
        rows = self.rows([pr(7, self.unrelated), pr(9, self.unrelated, updated="2026-10-02T11:00:00Z")],
                         close_list_path=path)
        self.assertEqual([r["class"] for r in rows], ["SUPERSEDED", "IDLE-UNCLASSIFIED"])
        self.assertEqual(rows[0]["reason"], "Superseded: landed by its port")
        self.assertIn("uncorroborated listing", rows[1]["reason"])

    def test_edges_future_unparseable_draft_unset_mergeable(self):
        rows = self.rows([pr(1, self.unrelated, updated="2027-01-01T00:00:00Z"),
                          pr(2, self.unrelated, updated="yesterday"),
                          pr(3, self.unrelated, updated="2026-10-01T00:00:00Z", draft=True, mergeable=None)])
        self.assertEqual([r["class"] for r in rows], ["NO-DATA", "NO-DATA", "LIVE"])
        self.assertIn("(draft)", rows[2]["reason"])
        self.assertEqual(rows[2]["mergeable"], "unset")
        with self.assertRaises(ValueError):
            T.triage([], {}, "main", "not a time")

    def test_a4_park_list_feeds_the_closer_unchanged(self):
        listed = {5: "Superseded: proven\nby port", 6: "Superseded: live"}
        rows = self.rows([pr(1, self.ancestor), pr(2, self.patch_equal), pr(3, self.unrelated),
                          pr(5, self.unrelated), pr(6, self.unrelated, updated="2026-10-02T11:00:00Z"),
                          pr(7, MISSING_OID)], listed)
        text = T.render_park_list(rows)
        lines = [l for l in text.splitlines() if not l.startswith("#")]
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertRegex(line, r"^[0-9]+ .+$")
            self.assertNotIn("\n", line)
        numbers = {int(l.split()[0]) for l in lines}
        landed = {int(r["number"]) for r in rows if r["class"] == "LANDED"}
        self.assertTrue(numbers <= (set(listed) | landed), numbers)
        self.assertEqual(numbers, {1, 2, 5})
        self.assertIn("5 Superseded: proven by port", lines)
        self.assertEqual(T.render_park_list([]).splitlines(), [T.PARK_HEADER])


class Command(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="acc4-cmd-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.ancestor, self.patch_equal, self.unrelated = make_repo(self.repo)
        self.env = write_fake_gh(self.tmp)
        self.out = os.path.join(self.tmp, "park.txt")

    def run_cmd(self, *extra):
        cmd = [sys.executable, "-B", os.path.join(HERE, "pr_park_triage.py"), "--main-ref", "main",
               "--now", NOW, "--out", self.out] + list(extra)
        return subprocess.run(cmd, cwd=self.repo, env=self.env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)

    def write_list(self, prs):
        import json
        with open(os.path.join(self.tmp, "list.json"), "w") as fh:
            json.dump(prs, fh)

    def log(self):
        path = os.path.join(self.tmp, "gh.log")
        return open(path).read().splitlines() if os.path.exists(path) else []

    def test_a3_the_generator_runs_only_read_verbs(self):
        self.write_list([pr(1, self.ancestor), pr(2, self.unrelated), pr(3, self.unrelated, updated="2026-10-02T11:00:00Z")])
        close_list = os.path.join(self.tmp, "close.txt")
        with open(close_list, "w") as fh:
            fh.write("2 Superseded: by port\n600 Superseded: already closed\n")
        p = self.run_cmd("--close-list", close_list)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        verbs = [line.split()[1] for line in self.log() if line.startswith("pr ")]
        self.assertTrue(verbs, "the fake gh was never called")
        self.assertTrue(set(verbs) <= set(T.READ_VERBS), verbs)
        self.assertIn("#1 LANDED", p.stdout)
        self.assertIn("#2 SUPERSEDED", p.stdout)
        self.assertIn("#3 LIVE", p.stdout)
        self.assertIn("SKIP #600", p.stdout)
        self.assertIn("open 3: LANDED=1 SUPERSEDED=1 LIVE=1 IDLE-UNCLASSIFIED=0 NO-DATA=0", p.stdout)
        body = [l for l in open(self.out).read().splitlines() if not l.startswith("#")]
        self.assertEqual(body, ["1 Landed: head %s is on main" % self.ancestor, "2 Superseded: by port"])

    def test_missing_close_list_and_dead_gh_are_no_data(self):
        self.write_list([])
        p = self.run_cmd("--close-list", os.path.join(self.tmp, "absent.txt"))
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("NO-DATA", p.stdout)
        self.assertFalse(os.path.exists(self.out))
        open(os.path.join(self.tmp, "gh.fail"), "w").close()
        p = self.run_cmd()
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("NO-DATA", p.stdout)
        os.remove(os.path.join(self.tmp, "gh.fail"))
        for bad in ("{}", "not json", '[{"title": "no number"}]'):  # an answer that is not a list of PRs
            with open(os.path.join(self.tmp, "list.json"), "w") as fh:
                fh.write(bad)
            p = self.run_cmd()
            self.assertEqual(p.returncode, 2, bad + "\n" + p.stdout + p.stderr)
            self.assertIn("NO-DATA", p.stdout)
            self.assertFalse(os.path.exists(self.out), bad)

    def test_zero_open_and_truncation_warning(self):
        self.write_list([])
        p = self.run_cmd()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("open 0:", p.stdout)
        self.assertEqual(open(self.out).read().splitlines(), [T.PARK_HEADER])
        self.write_list([pr(n, self.unrelated) for n in range(1, 3)])
        p = self.run_cmd("--limit", "2")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("WARNING: 2 pull requests read with --limit 2", p.stdout)


if __name__ == "__main__":
    unittest.main()
