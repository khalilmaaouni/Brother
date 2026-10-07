"""What the Convoy finding packet must keep true.

One fixture per guard, each tripping exactly one condition, because a fixture that
trips two guards proves neither. Everything runs against real throwaway git
repositories built in tempfile directories (never the checkout), and the CLI is
driven through main(), the entry point where the control lives.
"""
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import convoy  # noqa: E402

TARGET = "def add(a, b):\n    return a + b - 1\n"
TESTS = '''\
import time
import unittest

import target


class T(unittest.TestCase):
    def test_bug(self):
        self.assertEqual(target.add(1, 1), 3, "ADD-IS-WRONG")

    def test_ok(self):
        self.assertTrue(True)

    def test_slow(self):
        time.sleep(30)
        self.assertEqual(target.add(1, 1), 3, "ADD-IS-WRONG")
'''
FIXED_TARGET = "def add(a, b):\n    return a + b + 1\n"


def _git(cwd, *args):
    done = subprocess.run(["git"] + list(args), cwd=cwd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, universal_newlines=True)
    if done.returncode != 0:
        raise AssertionError("git %s failed: %s" % (" ".join(args), done.stdout))
    return done.stdout.strip()


class Fixture(unittest.TestCase):
    """A repository whose base commit holds a real bug and a real control."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = os.path.join(self._tmp.name, "repo")
        os.makedirs(self.repo)
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "t@example.invalid")
        _git(self.repo, "config", "user.name", "t")
        _git(self.repo, "config", "commit.gpgsign", "false")
        self._write("target.py", TARGET)
        self._write("test_target.py", TESTS)
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "base")
        self.base = _git(self.repo, "rev-parse", "HEAD")

    def _write(self, name, text):
        with open(os.path.join(self.repo, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def packet(self, **over):
        with open(os.path.join(self.repo, "test_target.py"), "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        p = {"id": "F-1", "base_sha": self.base, "behaviour": "add drops one",
             "test_path": "test_target.py", "test_id": "test_target.T.test_bug",
             "expected_failure_text": "ADD-IS-WRONG",
             "control_test_id": "test_target.T.test_ok", "digest": digest}
        p.update(over)
        return p

    def worktrees(self):
        return [ln for ln in _git(self.repo, "worktree", "list").splitlines() if ln]


class Packet(Fixture):
    def test_accepted(self):
        verdict, reason = convoy.accept(self.packet(), self.repo)
        self.assertEqual((verdict, reason), ("ACCEPTED", reason), reason)

    def test_accepted_runs_on_base_not_on_head(self):
        # HEAD fixes the bug, so only a run on base_sha still sees the failure.
        self._write("target.py", FIXED_TARGET)
        _git(self.repo, "commit", "-q", "-am", "fix")
        verdict, reason = convoy.accept(self.packet(), self.repo)
        self.assertEqual(verdict, "ACCEPTED", reason)

    def test_worktree_removed_after_accept_and_after_refusal(self):
        convoy.accept(self.packet(), self.repo)
        convoy.accept(self.packet(digest="0" * 64), self.repo)
        self.assertEqual(len(self.worktrees()), 1, self.worktrees())

    def test_no_temp_directory_left_behind(self):
        scratch = os.path.join(self._tmp.name, "scratch")
        os.makedirs(scratch)
        saved = tempfile.tempdir
        tempfile.tempdir = scratch
        try:
            convoy.accept(self.packet(), self.repo)
            convoy.accept(self.packet(test_id="test_target.T.test_ok"), self.repo)
        finally:
            tempfile.tempdir = saved
        self.assertEqual(os.listdir(scratch), [])

    def test_test_green_on_base(self):
        verdict, reason = convoy.accept(self.packet(test_id="test_target.T.test_ok"), self.repo)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("exit", reason)

    def test_control_red(self):
        verdict, reason = convoy.accept(
            self.packet(control_test_id="test_target.T.test_bug"), self.repo)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("control", reason)

    def test_expected_text_absent(self):
        verdict, reason = convoy.accept(
            self.packet(expected_failure_text="NOT-IN-ANY-OUTPUT"), self.repo)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("expected_failure_text", reason)

    def test_digest_mismatch(self):
        verdict, reason = convoy.accept(self.packet(digest="0" * 64), self.repo)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("digest", reason)

    def test_unknown_base(self):
        verdict, reason = convoy.accept(self.packet(base_sha="deadbeef" * 5), self.repo)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("base_sha", reason)

    def test_option_shaped_base_is_refused_not_passed_to_git(self):
        verdict, reason = convoy.accept(self.packet(base_sha="--all"), self.repo)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("base_sha", reason)

    def test_test_file_absent_at_base(self):
        verdict, reason = convoy.accept(self.packet(test_path="no_such_test.py"), self.repo)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("test_path", reason)

    def test_missing_field(self):
        for field in ("id", "base_sha", "behaviour", "test_path", "test_id",
                      "expected_failure_text", "control_test_id", "digest"):
            with self.subTest(field=field):
                p = self.packet()
                del p[field]
                verdict, reason = convoy.accept(p, self.repo)
                self.assertEqual(verdict, "NO-DATA")
                self.assertIn(field, reason)

    def test_empty_or_non_string_field(self):
        for value in ("", 7, None, ["x"]):
            with self.subTest(value=value):
                verdict, reason = convoy.accept(self.packet(behaviour=value), self.repo)
                self.assertEqual(verdict, "NO-DATA")
                self.assertIn("behaviour", reason)

    def test_not_an_object(self):
        verdict, reason = convoy.accept(["not", "an", "object"], self.repo)
        self.assertEqual(verdict, "NO-DATA")

    def test_timeout(self):
        verdict, reason = convoy.accept(
            self.packet(test_id="test_target.T.test_slow"), self.repo, timeout=2)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("timeout", reason)


class Dedupe(unittest.TestCase):
    def _p(self, pid, test_id="t.A.x", base="b1"):
        return {"id": pid, "test_id": test_id, "base_sha": base}

    def test_keeps_first_of_two_equal_keys(self):
        out = convoy.dedupe([self._p("first"), self._p("second")])
        self.assertEqual([p["id"] for p in out], ["first"])

    def test_keeps_distinct_keys_in_order(self):
        packets = [self._p("a"), self._p("b", test_id="t.A.y"), self._p("c", base="b2")]
        self.assertEqual([p["id"] for p in convoy.dedupe(packets)], ["a", "b", "c"])

    def test_empty(self):
        self.assertEqual(convoy.dedupe([]), [])

    def test_packets_lacking_a_key_are_never_merged(self):
        out = convoy.dedupe([{"id": "x"}, {"id": "y"}])
        self.assertEqual([p["id"] for p in out], ["x", "y"])


class Cli(Fixture):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = convoy.main(argv)
        return code, out.getvalue(), err.getvalue()

    def _json(self, name, obj):
        path = os.path.join(self._tmp.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(obj if isinstance(obj, str) else json.dumps(obj))
        return path

    def test_packet_accept_accepted(self):
        path = self._json("p.json", self.packet())
        code, out, _ = self._run(["packet-accept", path, "--repo", self.repo])
        self.assertEqual((code, out.strip()), (0, "ACCEPTED F-1"))

    def test_packet_accept_no_data(self):
        path = self._json("p.json", self.packet(digest="0" * 64))
        code, out, _ = self._run(["packet-accept", path, "--repo", self.repo])
        self.assertEqual(code, 2)
        self.assertTrue(out.startswith("NO-DATA F-1: "), out)

    def test_packet_accept_unreadable_json(self):
        for name, body in (("bad.json", "{not json"), ("arr.json", "[1, 2]")):
            with self.subTest(name=name):
                code, out, _ = self._run(["packet-accept", self._json(name, body),
                                          "--repo", self.repo])
                self.assertEqual(code, 2)
                self.assertTrue(out.startswith("NO-DATA "), out)

    def test_packet_accept_missing_file(self):
        code, out, _ = self._run(["packet-accept", os.path.join(self._tmp.name, "nope.json"),
                                  "--repo", self.repo])
        self.assertEqual(code, 2)
        self.assertTrue(out.startswith("NO-DATA "), out)

    def test_packet_dedupe(self):
        a = self._json("a.json", self.packet(id="A"))
        b = self._json("b.json", self.packet(id="B"))
        c = self._json("c.json", self.packet(id="C", test_id="test_target.T.test_ok"))
        code, out, _ = self._run(["packet-dedupe", a, b, c])
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(), ["A", "C", "UNIQUE 2 of 3"])


if __name__ == "__main__":
    unittest.main()
