#!/usr/bin/env python3
"""CUT2.h: one reuse rule and one approval, at the entry points the cut calls.

OneRuleOneApproval drives cut.reuse_record (the rehearsal record a --resume cut reuses), cut_driver.reuse_miss (the
kept phase entry, directly and through run()), cut.publish_tag (the tag push) and run_chain (which hands publish_tag
the owner's record), each against the real downstream piece: a throwaway git repository with a real covering
rehearsal record, the driver's own phase loop, and a real bare git repository standing in for the remote. Every
fixture lives under cut_rehearsal.scratch_root()/cut2h; nothing here tags, pushes or releases anything but those
scratch repositories, and the approvals are fixtures, never the owner's approval store.

Python 3.9, standard library only. No network. No em or en dashes.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cut as C  # noqa: E402
import cut_driver as D  # noqa: E402
import test_cut as TC  # noqa: E402

CR = C.CR
VERSION, TAG, HOUR = "9.9.9", "v9.9.9", 3600.0
GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
               GIT_AUTHOR_NAME="fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
               GIT_COMMITTER_NAME="fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")


def scratch():
    base = os.path.join(CR.scratch_root(), "cut2h")
    os.makedirs(base, exist_ok=True)
    return base


def git(repo, *args):
    """(rc, stdout) of a git command run in a fixture repository under the scratch root, and nowhere else."""
    assert os.path.realpath(repo).startswith(os.path.realpath(scratch()) + os.sep), "left the scratch root: %s" % repo
    got = subprocess.run(["git", "-C", repo] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         universal_newlines=True, env=GIT_ENV, timeout=60)
    return got.returncode, got.stdout.strip()


def inputs(**over):
    return dict({"candidate": "a" * 40, "checker": "c" * 64, "policy": "p" * 64, "interpreter": "py 3",
                 "environment": "e"}, **over)


def entry(made_at=1000.0, seconds=5.0, verdict="PASS", **over):
    ins = inputs()
    return dict({"verdict": verdict, "started": made_at, "seconds": seconds, "detail": "kept", "record": "",
                 "head_after": "", "inputs": ins, "key": D.evidence_key(ins)}, **over)


class Remote(object):
    """A real bare git repository as the remote, and a work repository as the pusher, both in the scratch root."""

    def __init__(self, base):
        self.work, self.bare = os.path.join(base, "work"), os.path.join(base, "remote.git")
        os.makedirs(self.work)
        os.makedirs(self.bare)
        assert git(self.bare, "init", "-q", "--bare")[0] == 0 and git(self.work, "init", "-q")[0] == 0
        with open(os.path.join(self.work, "f.txt"), "w", encoding="utf-8") as fh:
            fh.write("fixture")
        git(self.work, "add", "-A")
        assert git(self.work, "commit", "-q", "-m", "fixture")[0] == 0
        self.commit = git(self.work, "rev-parse", "HEAD")[1]
        self.pushes, self.lose = 0, False     # lose: a push whose connection dies before anything lands

    def ls_remote(self, tag):
        rc, out = git(self.bare, "for-each-ref", "--format=%(objectname)", "refs/tags/%s" % tag)
        if rc != 0:
            raise OSError("ls-remote failed")
        return out or None

    def push(self, tag, commit):
        self.pushes += 1
        if self.lose:
            raise OSError("synthetic: the connection dropped before the tag landed")
        assert git(self.work, "tag", tag, self.commit)[0] == 0
        return git(self.work, "push", "-q", self.bare, "refs/tags/%s" % tag)[0]


class OneRuleOneApproval(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t-", dir=scratch())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state = os.path.join(self.tmp, "state")

    def path(self, *parts):
        return os.path.join(self.tmp, *parts)

    # ---- reuse_record: a real covering rehearsal record in a throwaway repository ----------------------------

    def repo(self):
        """Makes the repository and evidence folder; returns the HEAD commit."""
        self.root, self.evidence = self.path("repo"), self.path("evidence")
        os.makedirs(self.evidence)
        for rel in CR.SCRIPT_DIRS:
            os.makedirs(os.path.join(self.root, rel))
            with open(os.path.join(self.root, rel, "x.txt"), "w", encoding="utf-8") as fh:
                fh.write(rel)
        assert git(self.root, "init", "-q")[0] == 0
        git(self.root, "add", "-A")
        assert git(self.root, "commit", "-q", "-m", "fixture")[0] == 0
        self.head = git(self.root, "rev-parse", "HEAD")[1]
        patch = mock.patch.dict(os.environ, {"BROTHER_CUT_TEST": "1", "BROTHER_CUT_EVIDENCE_DIR": self.evidence})
        patch.start()
        self.addCleanup(patch.stop)
        return self.head

    def record(self, verdict="READY", cut_py="", age_s=60):
        log = self.path("rehearsal.log")
        with open(log, "w", encoding="utf-8") as fh:
            fh.write("%s for %s\n" % (verdict, VERSION))
        return CR.record_rehearsal(VERSION, self.head, git(self.root, "rev-parse", "HEAD^{tree}")[1], 0, verdict, log,
                                   1.0, cut_py or CR.running_cut_py_sha256(), directory=self.evidence,
                                   now=time.time() - age_s,
                                   script_trees=CR._script_trees(self.root, self.head)[0])

    def test_a_warm_rerun_reuses_a_valid_record(self):
        head = self.repo()
        path = self.record()
        rec, got, why = C.reuse_record(self.root, VERSION, head)
        self.assertEqual(("", path, head), (why, got, rec["candidate_sha"]))

    def test_not_a_resume_reuses_nothing(self):
        self.repo()
        self.record()
        rec, got, why = C.reuse_record(self.root, VERSION, None)
        self.assertEqual((None, ""), (rec, got))
        self.assertTrue(why)

    def test_reuse_record_asks_shared_evidence_usable_and_obeys_it(self):
        head = self.repo()
        self.record()
        with mock.patch.object(D, "shared_evidence_usable", return_value=False) as rule:
            rec, got, why = C.reuse_record(self.root, VERSION, head)
        self.assertEqual(1, rule.call_count, "reuse_record did not ask the shared rule")
        self.assertEqual((None, ""), (rec, got))
        self.assertIn("shared evidence", why)

    def test_reuse_record_asks_for_this_cut_py_and_this_version(self):
        head = self.repo()
        self.record()
        with mock.patch.object(D, "shared_evidence_usable", return_value=True) as rule:
            C.reuse_record(self.root, VERSION, head)
        shared, expected = rule.call_args[0][0], rule.call_args[0][1]
        self.assertEqual({"checker": CR.running_cut_py_sha256(), "version": VERSION}, expected)
        self.assertEqual((expected, "PASS"), (shared["inputs"], shared["verdict"]))

    def test_a_record_past_the_shared_window_is_no_reuse(self):
        """The covering rule reads the real clock and accepts; the shared window, judged at `now`, does not."""
        head = self.repo()
        self.record()
        rec, got, why = C.reuse_record(self.root, VERSION, head, now=time.time() + 25 * HOUR)
        self.assertEqual((None, ""), (rec, got))
        self.assertIn("shared evidence", why)

    def test_a_log_altered_after_the_covering_check_is_no_reuse(self):
        head = self.repo()
        path = self.record()
        with open(CR.load_record(path)[0]["log_path"], "a", encoding="utf-8") as fh:
            fh.write("altered\n")
        with mock.patch.object(C, "_covering_record", return_value=(path, "")):
            rec, _path, why = C.reuse_record(self.root, VERSION, head)
        self.assertIsNone(rec)
        self.assertIn("shared evidence", why)

    def test_a_verdict_that_is_not_ready_is_no_reuse_even_when_covered(self):
        head = self.repo()
        path = self.record(verdict="NOT-READY")
        with mock.patch.object(C, "_covering_record", return_value=(path, "")):
            rec, _path, why = C.reuse_record(self.root, VERSION, head)
        self.assertIsNone(rec)
        self.assertIn("shared evidence", why)

    def test_a_record_that_ran_another_cut_py_is_no_reuse_even_when_covered(self):
        head = self.repo()
        path = self.record(cut_py="e" * 64)
        with mock.patch.object(C, "_covering_record", return_value=(path, "")):
            rec, _path, why = C.reuse_record(self.root, VERSION, head)
        self.assertIsNone(rec)
        self.assertIn("shared evidence", why)

    def test_a_rule_that_raises_is_no_reuse(self):
        head = self.repo()
        self.record()
        with mock.patch.object(D, "shared_evidence_usable", side_effect=RuntimeError("synthetic")):
            rec, _path, why = C.reuse_record(self.root, VERSION, head)
        self.assertIsNone(rec)
        self.assertTrue(why)

    # ---- reuse_miss: directly, and through the driver's own phase loop ----------------------------------------

    def test_a_fresh_pass_under_the_same_inputs_is_reused(self):
        self.assertEqual("", D.reuse_miss(entry(), inputs(), None, 1100.0))
        self.assertEqual("", D.reuse_miss(entry(verdict="REUSED"), inputs(), None, 1100.0))

    def test_reuse_miss_asks_shared_evidence_usable_and_obeys_it(self):
        with mock.patch.object(D, "shared_evidence_usable", return_value=False) as rule:
            miss = D.reuse_miss(entry(), inputs(), None, 1100.0)
        self.assertEqual(1, rule.call_count, "reuse_miss did not ask the shared rule")
        self.assertIn("shared evidence", miss)

    def test_an_entry_past_the_window_or_from_the_future_is_a_miss(self):
        made, window = 1005.0, D.MAX_STATE_AGE_HOURS * HOUR
        self.assertEqual("", D.reuse_miss(entry(), inputs(), None, made + window - 1))
        self.assertIn("shared evidence", D.reuse_miss(entry(), inputs(), None, made + window))
        self.assertIn("shared evidence", D.reuse_miss(entry(), inputs(), None, 1000.0))

    def test_a_key_that_does_not_match_its_inputs_is_a_miss(self):
        self.assertIn("shared evidence", D.reuse_miss(entry(key="f" * 64), inputs(), None, 1100.0))

    def test_an_entry_with_no_readable_time_is_a_miss(self):
        for bad in ({"started": None}, {"seconds": "5"}, {"started": True}):
            self.assertIn("shared evidence", D.reuse_miss(entry(**bad), inputs(), None, 1100.0), bad)

    def test_a_clock_is_read_only_when_a_window_is_judged(self):
        def boom():
            raise AssertionError("the clock was read for an entry that never reaches the window")
        self.assertEqual("no earlier attempt", D.reuse_miss(None, inputs(), None, boom))
        self.assertIn("changed", D.reuse_miss(entry(), inputs(checker="d" * 64), None, boom))
        self.assertEqual("", D.reuse_miss(entry(), inputs(), None, lambda: 1100.0))

    def test_a_warm_rerun_through_the_driver_reuses_every_valid_proof(self):
        ran = []

        def phase(name):
            return lambda state: (ran.append(name), ("PASS", "ran " + name))[1]
        table = dict((n, phase(n)) for n in ("ready", "freeze", "preflight"))
        first = D.run(VERSION, table, self.tmp, inputs(), False, clock=lambda: 2000.0)
        self.assertEqual((["ready", "freeze", "preflight"], ["PASS"] * 3), (ran, [v for _n, (v, _d) in first]))
        again = D.run(VERSION, table, self.tmp, inputs(), True, clock=lambda: 2000.0)
        self.assertEqual(["ready", "freeze", "preflight"], ran, "a valid proof was run again")
        self.assertEqual(["REUSED"] * 3, [v for _n, (v, _d) in again])

    def test_a_doubtful_proof_reruns_through_the_driver(self):
        ran = []
        table = {"ready": lambda state: (ran.append(1), ("PASS", "ran"))[1]}
        D.run(VERSION, table, self.tmp, inputs(), False, clock=lambda: 2000.0)
        with mock.patch.object(D, "shared_evidence_usable", return_value=False):
            again = D.run(VERSION, table, self.tmp, inputs(), True, clock=lambda: 2000.0)
        self.assertEqual(([1, 1], "PASS"), (ran, again[0][1][0]))

    # ---- publish_tag: a real bare remote, the owner's one use record handed in ---------------------------------

    def remote(self):
        self.r = Remote(self.path("r"))
        return self.r

    def publish(self, approval):
        return C.publish_tag(TAG, self.r.commit, self.r.push, self.r.ls_remote, self.state, approval=approval)

    def approval(self, **over):
        return dict(C.publish_approval(TAG, self.state), **over)

    def published(self):
        with open(os.path.join(self.state, "publish-%s.json" % TAG), encoding="utf-8") as fh:
            return json.load(fh)

    def test_a_valid_unused_approval_pushes_once_and_is_marked_used(self):
        self.remote()
        approval = self.approval()
        got = self.publish(approval)
        self.assertEqual("PUSHED", got[0], got)
        self.assertEqual((1, self.r.commit), (self.r.pushes, self.r.ls_remote(TAG)))
        self.assertIs(True, approval["used"], "the approval the caller holds was not marked used")
        rec = self.published()
        self.assertEqual(("pushed", True, approval["grant"]), (rec["state"], rec["approval_used"], rec["grant"]))

    def test_an_expired_approval_refuses_and_pushes_nothing(self):
        self.remote()
        got = self.publish(self.approval(expires=time.time() - 1))
        self.assertEqual("FAIL", got[0], got)
        self.assertIn("expired", got[1])
        self.assertEqual((0, None), (self.r.pushes, self.r.ls_remote(TAG)), "an expired approval pushed")

    def test_a_used_approval_refuses_and_pushes_nothing(self):
        self.remote()
        got = self.publish(self.approval(used=True))
        self.assertEqual("FAIL", got[0], got)
        self.assertIn("already used", got[1])
        self.assertEqual((0, None), (self.r.pushes, self.r.ls_remote(TAG)), "a used approval pushed")

    def test_an_approval_of_another_transaction_refuses(self):
        self.remote()
        got = self.publish(C.publish_approval(TAG, self.path("another-state")))
        self.assertEqual(("FAIL", 0), (got[0], self.r.pushes), got)

    def test_an_approval_that_is_not_a_record_or_is_malformed_never_pushes(self):
        self.remote()
        bad = ["yes", ["used"], 7, True, self.approval(used=None), self.approval(used="no"),
               self.approval(expires="soon"), self.approval(expires=None), self.approval(identity={})]
        for one in bad:
            self.assertEqual("FAIL", self.publish(one)[0], one)
        self.assertEqual(0, self.r.pushes)

    def test_an_approval_whose_grant_cannot_be_recorded_never_pushes(self):
        self.remote()
        for grant in (object(), 7, ["g"], {"g": 1}):
            self.assertEqual("FAIL", self.publish(self.approval(grant=grant))[0], grant)
        self.assertEqual(0, self.r.pushes)
        self.assertFalse(os.path.exists(os.path.join(self.state, "publish-%s.json" % TAG)))

    def test_the_used_flag_is_on_disk_before_the_push_runs(self):
        self.remote()
        seen, real = {}, self.r.push

        def push(tag, commit):
            seen["record"] = self.published()
            return real(tag, commit)
        self.r.push = push
        approval = self.approval()
        self.assertEqual("PUSHED", self.publish(approval)[0])
        got = seen["record"]
        self.assertEqual(("intent", True, approval["grant"]), (got["state"], got["approval_used"], got["grant"]))

    def test_a_rerun_of_the_same_approval_finds_the_intent_and_does_not_push_again(self):
        self.remote()
        self.r.lose = True
        approval = self.approval()
        self.assertEqual(("FAIL", 1), (self.publish(approval)[0], self.r.pushes))
        self.r.lose = False
        again = dict(approval, used=False)
        got = self.publish(again)
        self.assertEqual("FAIL", got[0], got)
        self.assertEqual(1, self.r.pushes, "the rerun pushed under an approval its intent record spent")
        self.assertIs(True, again["used"], "the rerun did not mark the approval it was handed used")
        self.assertIsNone(self.r.ls_remote(TAG))

    def test_a_rerun_persists_the_used_flag_of_an_intent_record_that_lacks_it(self):
        """An intent record from before records named their approval is spent: the rerun writes the flag and the
        approval's grant into it and pushes nothing."""
        self.remote()
        os.makedirs(self.state)
        with open(os.path.join(self.state, "publish-%s.json" % TAG), "w", encoding="utf-8") as fh:
            json.dump({"schema": C.PUBLISH_SCHEMA, "tag": TAG, "state": "intent", "commit": self.r.commit,
                       "note": "", "at": 1.0}, fh)
        approval = self.approval()
        got = self.publish(approval)
        self.assertEqual(("FAIL", 0), (got[0], self.r.pushes), got)
        rec = self.published()
        self.assertEqual(("intent", True, approval["grant"]), (rec["state"], rec["approval_used"], rec["grant"]))
        self.assertIs(True, approval["used"])

    def test_a_fresh_approval_over_an_intent_record_refuses_with_the_reconcile_message(self):
        """A push that may have half happened STOPS for the owner (publication_next's rule): a fresh grant over
        an intent record with no tag on the remote pushes nothing, whatever the approval reads."""
        self.remote()
        self.r.lose = True
        self.assertEqual("FAIL", self.publish(self.approval())[0])
        self.r.lose = False
        for fresh in (self.approval(), self.approval(grant="another-owner-code")):
            got = self.publish(fresh)
            self.assertEqual("FAIL", got[0], got)
            self.assertIn("the owner reconciles it and removes", got[1])
            self.assertIs(True, fresh["used"])
        self.assertEqual(1, self.r.pushes, "a fresh approval pushed over an unreconciled intent")
        self.assertIsNone(self.r.ls_remote(TAG))
        os.remove(os.path.join(self.state, "publish-%s.json" % TAG))      # the owner reconciles and removes it
        self.assertEqual(("PUSHED", 2), (self.publish(self.approval())[0], self.r.pushes))

    def test_an_intent_record_that_names_no_commit_stops_the_rerun_too(self):
        """The production shape: run_chain's commit reader answers None before the push, so the intent record names
        commit "". The STOP holds for it exactly as for one that names a commit: a fresh approval pushes nothing."""
        self.remote()
        self.r.lose = True
        none = lambda: None
        publish = lambda approval: C.publish_tag(TAG, none, self.r.push, self.r.ls_remote, self.state,
                                                 approval=approval)
        self.assertEqual(("FAIL", 1), (publish(self.approval())[0], self.r.pushes))
        self.assertEqual(("intent", ""), (self.published()["state"], self.published()["commit"]))
        self.r.lose = False
        fresh = self.approval(grant="another-owner-code")
        got = publish(fresh)
        self.assertEqual("FAIL", got[0], got)
        self.assertIn("the owner reconciles it and removes", got[1])
        self.assertIs(True, fresh["used"])
        self.assertEqual(1, self.r.pushes, "a rerun pushed over an unreconciled intent that names no commit")
        self.assertIsNone(self.r.ls_remote(TAG))

    def test_publish_approval_is_good_for_the_app_line_window(self):
        got = C.publish_approval(TAG, self.state, now=1000.0)
        self.assertEqual(1000.0 + C.APPROVAL_MAX_AGE_S, got["expires"])
        self.assertIs(False, got["used"])

    def test_a_tag_already_there_is_never_pushed_again_whatever_the_approval_reads(self):
        self.remote()
        self.assertEqual("PUSHED", self.publish(self.approval())[0])
        self.assertEqual(("REUSED", 1), (self.publish(self.approval(used=True))[0], self.r.pushes))

    def test_a_foreign_tag_refuses_with_a_valid_approval(self):
        self.remote()
        assert git(self.r.work, "commit", "-q", "--allow-empty", "-m", "other")[0] == 0
        assert git(self.r.work, "tag", TAG, git(self.r.work, "rev-parse", "HEAD")[1])[0] == 0
        assert git(self.r.work, "push", "-q", self.r.bare, "refs/tags/%s" % TAG)[0] == 0
        got = self.publish(self.approval())
        self.assertEqual(("FAIL", 0), (got[0], self.r.pushes), got)

    def test_no_approval_handed_refuses_and_conjures_none(self):
        self.remote()
        for none in (None, {}):
            got = self.publish(none)
            self.assertEqual("FAIL", got[0], got)
        got = C.publish_tag(TAG, self.r.commit, self.r.push, self.r.ls_remote, self.state)
        self.assertEqual("FAIL", got[0], got)
        self.assertEqual(0, self.r.pushes)
        self.assertFalse(os.path.exists(os.path.join(self.state, "publish-%s.json" % TAG)))

    # ---- run_chain: the production caller hands the record over -----------------------------------------------

    def test_run_chain_hands_publish_tag_the_owners_unused_unexpired_record(self):
        # test_cut's module fixture (a throwaway evidence folder, stubbed exports and canary) for this one case:
        # its module cleanups are taken back and run as this case's own, so the real covering rule the other cases
        # drive is never patched
        before = len(unittest.case._module_cleanups)
        TC.setUpModule()
        for fn, args, kw in reversed(unittest.case._module_cleanups[before:]):
            self.addCleanup(fn, *args, **kw)
        del unittest.case._module_cleanups[before:]
        seen, real = [], C.publish_tag

        def spy(tag, commit, push, ls_remote, state_dir, **kw):
            seen.append((tag, state_dir, dict(kw.get("approval") or {})))
            return real(tag, commit, push, ls_remote, state_dir, **kw)
        with mock.patch.object(C, "publish_tag", side_effect=spy), contextlib.redirect_stdout(io.StringIO()):
            TC._main(["--yes"], TC.Runner(TC.GREEN))
        self.assertEqual(1, len(seen), seen)
        tag, state_dir, approval = seen[0]
        self.assertEqual("v%s" % TC.VERSION, tag)
        self.assertEqual((False, True), (approval.get("used"), approval.get("expires", 0) > time.time()))
        self.assertEqual(C.publish_approval(tag, state_dir)["identity"], approval.get("identity"))


if __name__ == "__main__":
    unittest.main()
