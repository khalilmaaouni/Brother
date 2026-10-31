#!/usr/bin/env python3
"""integrate.scan_before_commit: the one place it differs from commit_scan.py (HD1.f).

commit_scan fails CLOSED on private terms: a machine with no list, or an installed
runtime with no term scanner, counts one hit on every text. integrate.py would then
refuse every lane there forever, so scan_before_commit reads that one reading as
NO-DATA, and only when it is the ONLY nonzero count AND a text naming nothing draws
the same hit. Each case isolates one side of that rule:

    python3 -B scripts/test_integrate_commit_gate.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import integrate as I  # noqa: E402

TERM = "zzhdone-term"


class CommitGate(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="commit-gate-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.home)
        os.makedirs(self.repo)
        with open(os.path.join(self.home, ".gitconfig"), "w") as fh:
            fh.write("[user]\n\temail = a@b.c\n\tname = t\n")
        self.env = {"HOME": self.home, "PATH": os.environ.get("PATH", "")}
        self.git("init", "-q")

    def git(self, *args):
        p = subprocess.run(["git"] + list(args), cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(0, p.returncode, p.stderr)

    def scan(self, text, names=None):
        if names is not None:
            with open(os.path.join(self.home, ".brothersbe-private-names"), "w") as fh:
                fh.write(names)
        with open(os.path.join(self.repo, "f.txt"), "w") as fh:
            fh.write(text)
        self.git("add", "f.txt")
        with mock.patch.dict(os.environ, self.env, clear=True):
            return I.scan_before_commit(["f.txt"], self.repo)

    def test_no_list_and_a_clean_text_is_ok_and_says_the_dimension_is_no_data(self):
        verdict, detail = self.scan("hello\n")
        self.assertEqual((I.SCAN_OK, True), (verdict, "NO-DATA" in detail), detail)

    def test_no_list_does_not_excuse_a_secret(self):
        verdict, detail = self.scan('t = "%s"\n' % ("gh" + "p_" + "Zk3Qm9Xv2Lr8Tb5Wn7Yc4Hd6Jf1Ps0Ga8Ue"))
        self.assertEqual(I.REFUSED, verdict, detail)

    def test_no_list_does_not_excuse_a_long_dash(self):
        verdict, detail = self.scan("a " + chr(0x2014) + " b\n")
        self.assertEqual(I.REFUSED, verdict, detail)

    def test_a_list_that_names_a_term_refuses_a_text_carrying_it(self):
        verdict, detail = self.scan("this carries %s here\n" % TERM, names=TERM + "\n")
        self.assertEqual(I.REFUSED, verdict, detail)

    def test_a_list_and_a_clean_text_is_ok_without_the_note(self):
        verdict, detail = self.scan("hello\n", names=TERM + "\n")
        self.assertEqual((I.SCAN_OK, False), (verdict, "NO-DATA" in detail), detail)


def token():
    # Built from pieces so this file never carries the shape its own commit gate refuses.
    return "gh" + "p_" + "Zk3Qm9Xv2Lr8Tb5Wn7Yc4Hd6Jf1Ps0Ga8Ue"


class LaneGate(unittest.TestCase):
    """integrate_one on a real lane: what the gate reads, and what it must never run."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="lane-gate-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.home)
        os.makedirs(self.repo)
        with open(os.path.join(self.home, ".gitconfig"), "w") as fh:
            fh.write("[user]\n\temail = a@b.c\n\tname = t\n")
        self.env = {"HOME": self.home, "PATH": os.environ.get("PATH", "")}
        self.git("init", "-q", "-b", "main")
        self.write("base.txt", "base\n")
        self.commit("base")

    def git(self, *args):
        p = subprocess.run(["git"] + list(args), cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(0, p.returncode, p.stderr)
        return p.stdout.strip()

    def write(self, name, text):
        with open(os.path.join(self.repo, name), "w") as fh:
            fh.write(text)

    def commit(self, message, *names):
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def lane(self, steps):
        """steps: [(message, {name: text or None})]; returns the commit ids, back on main."""
        self.git("checkout", "-qb", "lane/u1")
        shas = []
        for message, files in steps:
            for name, text in files.items():
                if text is None:
                    os.remove(os.path.join(self.repo, name))
                else:
                    self.write(name, text)
            shas.append(self.commit(message))
        self.git("checkout", "-q", "main")
        return shas

    def integrate(self):
        with mock.patch.dict(os.environ, self.env, clear=True):
            return I.integrate_one(self.repo, "lane/u1", {"id": "u1", "done_check": "true"})

    def test_a_planted_interpreter_file_in_the_lane_is_never_run(self):
        marker = os.path.join(self.tmp, "ran-planted-code")
        planted = "open(%r, 'w').write('x')\n" % marker
        self.lane([("add planted modules", {"grade_build.py": planted, "json.py": planted, "secret_scan.py": planted})])
        got = self.integrate()
        self.assertFalse(os.path.exists(marker), "the gate ran code the lane carried: %r" % got)
        self.assertEqual("INTEGRATED", got.get("verdict"), got)

    def test_a_python_path_in_the_environment_is_not_followed(self):
        marker = os.path.join(self.tmp, "ran-from-pythonpath")
        planted = os.path.join(self.tmp, "planted")
        os.makedirs(planted)
        for name in ("json.py", "re.py"):
            with open(os.path.join(planted, name), "w") as fh:
                fh.write("open(%r, 'w').write('x')\n" % marker)
        self.env["PYTHONPATH"] = planted
        self.lane([("add a file", {"a.txt": "a\n"})])
        got = self.integrate()
        self.assertFalse(os.path.exists(marker), "the gate followed PYTHONPATH: %r" % got)
        self.assertEqual("INTEGRATED", got.get("verdict"), got)

    def test_a_secret_added_then_removed_inside_the_lane_is_refused(self):
        before = self.git("rev-parse", "HEAD")
        shas = self.lane([("first", {"a.txt": "a\n"}), ("middle", {"leak.txt": "t = %r\n" % token()}),
                          ("last", {"leak.txt": None})])
        got = self.integrate()
        self.assertEqual("REFUSED", got.get("verdict"), got)
        self.assertIn(shas[1][:12], got.get("reason", ""), "the refusal does not name the commit")
        self.assertNotIn(shas[0][:12], got.get("reason", ""))
        self.assertEqual(before, self.git("rev-parse", "HEAD"), "canonical moved over a secret in the lane's history")

    def test_an_attribution_line_in_a_lane_commit_message_is_refused(self):
        self.lane([("add a file\n\n" + "Co-Authored" + "-By: someone <x@y.z>", {"a.txt": "a\n"})])
        got = self.integrate()
        self.assertEqual("REFUSED", got.get("verdict"), got)

    def runner(self, before_merge=None, merge_head=None):
        """A scripted git: the real one, except that `before_merge` runs once just before the first
        merge, and a `rev-parse MERGE_HEAD` answers `merge_head` when it is given."""
        state = {"done": False}

        def run(cmd):
            if before_merge and not state["done"] and cmd[1] == "merge" and "--no-commit" in cmd:
                state["done"] = True
                before_merge()
            if merge_head and cmd[1:] == ["rev-parse", "-q", "--verify", "MERGE_HEAD"]:
                return subprocess.CompletedProcess(cmd, 0, merge_head + "\n", "")
            return subprocess.run(cmd, cwd=self.repo, env=self.env, capture_output=True, text=True)
        return run

    def integrate_with(self, runner):
        with mock.patch.dict(os.environ, self.env, clear=True):
            return I.integrate_one(self.repo, "lane/u1", {"id": "u1", "done_check": "true"}, runner=runner)

    def test_a_lane_that_moves_after_the_scan_is_merged_at_the_scanned_commit(self):
        self.lane([("clean", {"a.txt": "a\n"})])
        self.git("checkout", "-q", "-b", "evil", "lane/u1")
        self.write("leak.txt", "t = %r\n" % token())
        self.commit("the lane after it moved")
        os.remove(os.path.join(self.repo, "leak.txt"))   # net clean: only the history carries it
        self.commit("and scrubbed")
        self.git("checkout", "-q", "main")
        got = self.integrate_with(self.runner(before_merge=lambda: self.git("branch", "-f", "lane/u1", "evil")))
        self.assertEqual("", self.git("log", "--format=%H", "-S" + token(), "HEAD"), "the moved lane's history landed: %r" % got)
        # the scanned commit is what was merged, so the unit lands as scanned and not as moved
        self.assertEqual(("INTEGRATED", True), (got.get("verdict"), "a.txt" in self.git("ls-tree", "-r", "--name-only", "HEAD")), got)

    def test_a_merge_that_is_not_the_scanned_commit_is_refused_and_canonical_stays(self):
        before = self.git("rev-parse", "HEAD")
        self.lane([("clean", {"a.txt": "a\n"})])
        got = self.integrate_with(self.runner(merge_head="0" * 40))
        self.assertEqual("REFUSED", got.get("verdict"), got)
        self.assertEqual(before, self.git("rev-parse", "HEAD"))
        self.assertEqual("", self.git("status", "--porcelain"), "the merge was left half done")

    def test_a_canonical_that_moved_during_the_merge_is_refused(self):
        self.lane([("clean", {"a.txt": "a\n"})])

        def move_canonical():
            self.write("other.txt", "o\n")
            self.git("add", "other.txt")
            self.git("commit", "-qm", "another writer")
        got = self.integrate_with(self.runner(before_merge=move_canonical))
        self.assertEqual("REFUSED", got.get("verdict"), got)

    def test_the_fence_is_asked_about_the_pinned_commit_not_the_branch_name(self):
        shas = self.lane([("clean", {"a.txt": "a\n"})])
        self.git("checkout", "-q", "-b", "evil", "lane/u1")
        self.write("leak.txt", "t = %r\n" % token())
        self.commit("the lane after it moved")
        self.git("checkout", "-q", "main")
        asked = []

        def fence(repo, before, lane, runner=None):
            asked.append(lane)
            self.git("branch", "-f", "lane/u1", "evil")   # the ref moves while the fence is being asked
            return None, None
        with mock.patch.object(I, "_interactive_fence_conflict", fence):
            got = self.integrate()
        self.assertEqual([shas[0]], asked, "the fence judged %r, not the commit that is scanned and merged" % (asked,))
        self.assertEqual(("INTEGRATED", ""), (got.get("verdict"), self.git("log", "--format=%H", "-S" + token(), "HEAD")), got)

    def test_a_lane_that_does_not_resolve_never_reaches_a_merge(self):
        seen = []

        def run(cmd):
            seen.append(list(cmd))
            return subprocess.run(cmd, cwd=self.repo, env=self.env, capture_output=True, text=True)
        with mock.patch.dict(os.environ, self.env, clear=True):
            got = I.integrate_one(self.repo, "lane/not-there", {"id": "u1", "done_check": "true"}, runner=run)
        self.assertEqual("CONFLICT", got.get("verdict"), got)
        self.assertEqual([], [c for c in seen if c[1] == "merge"], "an unresolved lane was merged by name")

    def test_a_clean_lane_of_several_commits_still_integrates(self):
        self.lane([("one", {"a.txt": "a\n"}), ("two", {"b.txt": "b\n"})])
        self.assertEqual("INTEGRATED", self.integrate().get("verdict"))


if __name__ == "__main__":
    unittest.main()
