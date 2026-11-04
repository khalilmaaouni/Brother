#!/usr/bin/env python3
"""CUT2.f edges: publication decides its next step from read back state and one bound approval.

cut_driver.publication_next is driven directly on the edges the frozen contract leaves open, and through the real
routed path, cut.publish_tag, which run_chain calls for the tag: a scripted remote (a push counter and an ls_remote
answer) stands in for git, so nothing here creates a tag, pushes, runs gh or reads a network.

Python 3.9, standard library only. No network. No em or en dashes.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cut as C  # noqa: E402
import cut_driver as D  # noqa: E402

NOW = 100
CANDIDATE = "a" * 40
FINAL = D.FINAL_STEP


def state(**kw):
    identity = dict(candidate=CANDIDATE, export="b" * 64, body="c" * 64)
    base = dict(ready=True, identity=identity, approval=dict(identity=dict(identity), expires=200, used=False),
                remote_commit=None, artifacts_verified=False, receipt_written=False)
    base.update(kw)
    return base


def act(st, now=NOW):
    return D.publication_next(st, now)["action"]


class Decisions(unittest.TestCase):
    def test_a_valid_unused_approval_pushes(self):
        self.assertEqual("PUSH", act(state()))

    def test_an_approval_differing_only_in_export_never_pushes(self):
        st = state()
        st["approval"]["identity"]["export"] = "d" * 64
        self.assertEqual("ASK_APPROVAL", act(st))

    def test_an_approval_differing_only_in_body_never_pushes(self):
        st = state()
        st["approval"]["identity"]["body"] = "d" * 64
        self.assertEqual("ASK_APPROVAL", act(st))

    def test_an_approval_expiring_exactly_now_never_pushes(self):
        st = state()
        st["approval"]["expires"] = NOW
        self.assertEqual("ASK_APPROVAL", act(st))

    def test_a_used_approval_with_everything_else_valid_refuses(self):
        st = state()
        st["approval"]["used"] = True
        self.assertEqual("REFUSE", act(st))

    def test_a_remote_commit_of_the_wrong_length_or_case_refuses(self):
        for bad in ("a" * 39, "a" * 41, "A" * 40, CANDIDATE.upper(), "", 7):
            got = D.publication_next(state(remote_commit=bad), NOW)
            self.assertEqual("REFUSE", got["action"], bad)
            self.assertIn("40 lowercase hex", got["reason"], "a malformed read was judged as a commit: %r" % (bad,))

    def test_verified_artifacts_with_no_remote_tag_are_not_the_final_step(self):
        got = act(state(artifacts_verified=True, receipt_written=True))
        self.assertNotIn(got, (FINAL, "PUSH"))
        self.assertEqual("REFUSE", act(state(artifacts_verified=True)))

    def test_a_receipt_without_verified_artifacts_is_not_the_final_step(self):
        self.assertEqual("VERIFY", act(state(remote_commit=CANDIDATE, receipt_written=True)))

    def test_a_state_that_is_not_a_record_refuses(self):
        for bad in (None, [], "state", 3):
            self.assertEqual("REFUSE", act(bad), bad)

    def test_not_ready_prepares_before_any_approval_is_asked(self):
        self.assertEqual("PREPARE", act(state(ready=False, approval=None)))
        self.assertEqual("PREPARE", act({"approval": None}))
        self.assertEqual("REFUSE", act(state(ready="yes")))

    def test_an_exact_remote_tag_verifies_whatever_the_approval_reads(self):
        for approval in (None, dict(identity={}, expires=0, used=True)):
            self.assertEqual("VERIFY", act(state(remote_commit=CANDIDATE, approval=approval)))

    def test_a_malformed_approval_or_clock_never_pushes(self):
        self.assertEqual("REFUSE", act(state(approval="yes")))
        for used in (None, 0, "no"):
            st = state()
            st["approval"]["used"] = used
            self.assertEqual("REFUSE", act(st), used)
        for expires in (None, "200", True, float("inf"), float("nan")):
            st = state()
            st["approval"]["expires"] = expires
            self.assertEqual("REFUSE", act(st), expires)
        self.assertEqual("REFUSE", act(state(), now="100"))
        self.assertEqual("REFUSE", act(state(identity={"candidate": CANDIDATE})))

    def test_an_identity_that_raises_on_read_refuses_and_never_raises(self):
        class Boom(dict):
            def get(self, *a):
                raise RuntimeError("synthetic")
        self.assertEqual("REFUSE", act(Boom()))


class RoutedPath(unittest.TestCase):
    """cut.publish_tag, the function run_chain calls for the tag, decides through publication_next."""
    TAG = "v9.9.9"

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cut2f-publish-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.remote = {"tags": {}, "pushes": 0}

    def push(self, tag, commit):
        self.remote["pushes"] += 1
        self.remote["tags"][tag] = CANDIDATE
        raise OSError("synthetic: the connection dropped after the tag landed")

    def ls_remote(self, tag):
        return self.remote["tags"].get(tag)

    def publish(self):
        return C.publish_tag(self.TAG, CANDIDATE, self.push, self.ls_remote, self.dir,
                             approval=C.publish_approval(self.TAG, self.dir))

    def test_a_resume_after_a_lost_connection_verifies_with_zero_further_pushes(self):
        got = self.publish()
        self.assertEqual("PUSHED", got[0], got)
        self.assertEqual(1, self.remote["pushes"])
        seen = []
        real = D.publication_next

        def spy(st, now):
            out = real(st, now)
            seen.append(out["action"])
            return out
        with mock.patch.object(D, "publication_next", side_effect=spy):
            got = self.publish()
        self.assertEqual("REUSED", got[0], got)
        self.assertEqual(["VERIFY"], seen, "the resume did not decide through publication_next")
        self.assertEqual(1, self.remote["pushes"], "the resume pushed again")

    def test_a_foreign_tag_refuses_with_zero_pushes(self):
        self.remote["tags"][self.TAG] = "d" * 40
        seen = []
        real = D.publication_next

        def spy(st, now):
            out = real(st, now)
            seen.append(out["action"])
            return out
        with mock.patch.object(D, "publication_next", side_effect=spy):
            got = self.publish()
        self.assertEqual("FAIL", got[0], got)
        self.assertIn("foreign", got[1])
        self.assertEqual(["REFUSE"], seen)
        self.assertEqual(0, self.remote["pushes"])
        self.assertEqual("d" * 40, self.remote["tags"][self.TAG])

    def test_a_refusal_from_publication_next_pushes_nothing(self):
        with mock.patch.object(D, "publication_next", return_value={"action": "REFUSE", "reason": "synthetic"}):
            got = self.publish()
        self.assertEqual("FAIL", got[0], got)
        self.assertEqual(0, self.remote["pushes"], "publish_tag pushed past publication_next's refusal")

    def test_a_raising_publication_next_pushes_nothing(self):
        with mock.patch.object(D, "publication_next", side_effect=RuntimeError("synthetic")):
            got = self.publish()
        self.assertEqual("FAIL", got[0], got)
        self.assertEqual(0, self.remote["pushes"])

    def test_a_lost_connection_reconciles_only_on_verify(self):
        calls = []

        def decide(st, now):
            calls.append(st["approval"]["used"])
            return {"action": "PUSH" if not st["approval"]["used"] else "REFUSE", "reason": "synthetic"}
        with mock.patch.object(D, "publication_next", side_effect=decide):
            got = self.publish()
        self.assertEqual("FAIL", got[0], "a lost connection reconciled without publication_next's VERIFY: %r" % (got,))
        self.assertEqual([False, True], calls, "the attempt was not consulted as used after the push")
        self.assertEqual(1, self.remote["pushes"], "the lost connection was retried")


if __name__ == "__main__":
    unittest.main()
