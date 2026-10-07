#!/usr/bin/env python3
"""MG1.e tests: reachability from main is proven with git, never read from the state field.

Real throwaway repositories (a bare hub and a clone) built in a temp folder: no network, no home folder."""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import merge_reach  # noqa: E402

NAN = float("nan")
HOSTILE = [None, 0, True, -1, NAN, "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object()]
IDENT = ["-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "commit.gpgsign=false"]
NO_SIGN = ("-c", "tag.gpgsign=false")   # a machine that signs every tag would ask a plain `git tag` for a message


class Fixture(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="merge-reach-test.")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.hub = os.path.join(self.root, "hub.git")
        self.clone = os.path.join(self.root, "clone")
        os.mkdir(self.hub)
        self.git(self.hub, "init", "--quiet", "--bare")
        os.mkdir(self.clone)
        self.git(self.clone, "init", "--quiet")
        self.git(self.clone, "checkout", "--quiet", "-b", "main")
        self.git(self.clone, "remote", "add", "hub", self.hub)
        self.base = self.commit("base.txt", "base")
        self.git(self.clone, "push", "--quiet", "hub", "main")

    def git(self, cwd, *args):
        done = merge_reach._default_runner(["git"] + list(IDENT[:0]) + list(args), cwd=cwd)
        self.assertEqual(done.returncode, 0, "git %s: %s" % (args, done.stderr))
        return done.stdout.strip()

    def gitc(self, *args):
        done = merge_reach._default_runner(["git"] + IDENT + list(args), cwd=self.clone)
        self.assertEqual(done.returncode, 0, "git %s: %s" % (args, done.stderr))
        return done.stdout.strip()

    def commit(self, name, text):
        with open(os.path.join(self.clone, name), "w") as handle:
            handle.write(text)
        self.gitc("add", name)
        self.gitc("commit", "--quiet", "-m", text)
        return self.gitc("rev-parse", "HEAD")

    def tree(self, rev):
        return self.gitc("rev-parse", rev + "^{tree}")

    def entry(self, pr, sha, tree=None):
        return {"pr": pr, "sha": sha, "tree": tree or self.tree(sha)}

    def prove(self, entries, state="OPEN", **kw):
        calls = []

        def state_of(pr):
            calls.append(pr)
            return state
        rows = merge_reach.prove_merged(self.clone, entries, state_of, **kw)
        return rows, calls

    def feature(self, name, filename):
        self.gitc("checkout", "--quiet", "-b", name, "main")
        sha = self.commit(filename, name)
        self.git(self.clone, "push", "--quiet", "hub", name)
        self.gitc("checkout", "--quiet", "main")
        return sha


class IsReachable(Fixture):
    def test_exit_zero_is_true_and_exit_one_is_false(self):
        feat = self.feature("feat", "f.txt")
        self.assertIs(merge_reach.is_reachable(self.clone, self.base, "main"), True)
        self.assertIs(merge_reach.is_reachable(self.clone, feat, "main"), False)

    def test_any_other_exit_is_none(self):
        class Done:
            def __init__(self, rc):
                self.returncode, self.stdout = rc, ""
        sha = "a" * 40
        for rc, want in ((0, True), (1, False), (128, None), (2, None), (-9, None)):
            got = merge_reach.is_reachable("/x", sha, "hub/main", lambda argv, cwd=None, rc=rc: Done(rc))
            self.assertIs(got, want, rc)

    def test_unknown_object_is_none_not_false(self):
        self.assertIsNone(merge_reach.is_reachable(self.clone, "b" * 40, "main"))

    def test_runner_that_raises_is_none(self):
        def boom(argv, cwd=None):
            raise OSError("no git")
        self.assertIsNone(merge_reach.is_reachable("/x", "a" * 40, "hub/main", boom))


class ProveMerged(Fixture):
    def test_fast_forward_is_proven_with_its_own_commit(self):
        feat = self.feature("feat", "f.txt")
        self.gitc("merge", "--ff-only", "--quiet", feat)
        later = self.commit("later.txt", "later")
        self.git(self.clone, "push", "--quiet", "hub", "main")
        rows, calls = self.prove([self.entry(1, feat)], state="OPEN")
        self.assertEqual(rows[0]["status"], "PROVEN")
        self.assertEqual(rows[0]["merge_commit"], feat)
        self.assertNotEqual(rows[0]["merge_commit"], later)
        self.assertEqual(calls, [], "the state field is never read to decide PROVEN")

    def test_merge_commit_is_proven_and_named(self):
        feat = self.feature("feat", "f.txt")
        self.commit("m.txt", "moved main")
        self.gitc("merge", "--no-ff", "--quiet", "-m", "merge feat", feat)
        merge = self.gitc("rev-parse", "HEAD")
        self.commit("after.txt", "after")
        self.git(self.clone, "push", "--quiet", "hub", "main")
        rows, calls = self.prove([self.entry(2, feat, self.tree(merge))], state="OPEN")
        self.assertEqual(rows[0]["status"], "PROVEN", rows)
        self.assertEqual(rows[0]["merge_commit"], merge)
        self.assertEqual(calls, [])

    def test_stacked_pr_is_stranded_and_stops_the_batch(self):
        stacked = self.feature("stacked", "s.txt")
        other = self.feature("other", "o.txt")
        rows, calls = self.prove([self.entry(3, stacked), self.entry(4, other)], state="MERGED")
        self.assertEqual([r["status"] for r in rows], ["STRANDED", "NO-DATA"])
        self.assertEqual(calls, [3], "the state names STRANDED and the batch stops there")

    def test_open_pr_not_on_main_is_not_merged(self):
        feat = self.feature("feat", "f.txt")
        rows, _calls = self.prove([self.entry(5, feat)], state="OPEN")
        self.assertEqual(rows[0]["status"], "NOT-MERGED")

    def test_a_fetched_tag_named_like_main_cannot_prove_reachability(self):
        # M3, review 2026-10-05: the short name hub/main resolves refs/tags/hub/main before refs/remotes/hub/main, so a
        # tag of that name on an unmerged head made it PROVEN; the full remote tracking ref is read instead
        feat = self.feature("feat", "f.txt")
        self.git(self.clone, "push", "--quiet", "hub", "%s:refs/tags/hub/main" % feat)
        # the check itself fetches no tag any more (F6, 2026-10-06), so the tag arrives the way it would in practice:
        # by an ordinary fetch at some earlier time. Without this line the case would pass for the wrong reason.
        self.git(self.clone, "fetch", "--quiet", "hub", "refs/tags/hub/main:refs/tags/hub/main")
        self.assertEqual(self.gitc("rev-parse", "refs/tags/hub/main"), feat)
        rows, _calls = self.prove([self.entry(5, feat)], state="OPEN")
        self.assertEqual(rows[0]["status"], "NOT-MERGED", rows)

    def test_merged_state_never_makes_proven(self):
        feat = self.feature("feat", "f.txt")
        rows, _calls = self.prove([self.entry(5, feat)], state="MERGED")
        self.assertNotEqual(rows[0]["status"], "PROVEN")

    def test_unreadable_state_for_an_unreachable_sha_is_no_data(self):
        feat = self.feature("feat", "f.txt")

        def broken(pr):
            raise RuntimeError("gh down")
        rows = merge_reach.prove_merged(self.clone, [self.entry(5, feat)], broken)
        self.assertEqual(rows[0]["status"], "NO-DATA")
        rows = merge_reach.prove_merged(self.clone, [self.entry(5, feat)], lambda pr: None)
        self.assertEqual(rows[0]["status"], "NO-DATA")

    def test_fetch_failure_is_no_data_even_with_a_stale_local_ref(self):
        feat = self.feature("feat", "f.txt")
        self.gitc("merge", "--ff-only", "--quiet", feat)
        self.git(self.clone, "push", "--quiet", "hub", "main")
        self.git(self.clone, "fetch", "--quiet", "hub")
        self.assertIs(merge_reach.is_reachable(self.clone, feat, "hub/main"), True)
        self.git(self.clone, "remote", "set-url", "hub", os.path.join(self.root, "gone.git"))
        rows, calls = self.prove([self.entry(1, feat), self.entry(2, self.base)], state="MERGED")
        self.assertEqual([r["status"] for r in rows], ["NO-DATA", "NO-DATA"])
        self.assertEqual(calls, [])

    def test_post_merge_tree_drift_prints_and_stops(self):
        """After a merge, main's tree is compared with the gated tree: a mismatch is TREE-DRIFT and stops."""
        feat = self.feature("feat", "f.txt")
        other = self.feature("other", "o.txt")
        self.commit("moved.txt", "main moved under the merge")
        self.gitc("merge", "--no-ff", "--quiet", "-m", "merge feat", feat)
        merge = self.gitc("rev-parse", "HEAD")
        self.gitc("merge", "--no-ff", "--quiet", "-m", "merge other", other)
        self.git(self.clone, "push", "--quiet", "hub", "main")
        gated_without_the_moved_base = self.tree(feat)
        self.assertNotEqual(gated_without_the_moved_base, self.tree(merge))
        rows, _calls = self.prove([self.entry(6, feat, gated_without_the_moved_base), self.entry(7, other)],
                                  state="MERGED")
        self.assertEqual([r["status"] for r in rows], ["TREE-DRIFT", "NO-DATA"])
        self.assertEqual(rows[0]["merge_commit"], merge)
        report = merge_reach.format_report(rows)
        self.assertIn("TREE-DRIFT #6", report)
        self.assertEqual(merge_reach.main(["check", self.clone, "hub", "6", feat, gated_without_the_moved_base,
                                           "MERGED"]), 1)

    def test_runner_that_cannot_answer_is_no_data(self):
        class Done:
            def __init__(self, rc, out=""):
                self.returncode, self.stdout = rc, out

        def runner(argv, cwd=None):
            return Done(0) if argv[1] == "fetch" else Done(128)
        rows = merge_reach.prove_merged("/x", [{"pr": 1, "sha": "a" * 40, "tree": "b" * 40}], lambda p: "MERGED",
                                        runner=runner)
        self.assertEqual(rows[0]["status"], "NO-DATA")

    def test_report_lists_every_status_with_the_merge_commit(self):
        rows = [{"pr": 1, "sha": "a" * 40, "status": "PROVEN", "merge_commit": "c" * 40, "detail": "d"},
                {"pr": 2, "sha": "b" * 40, "status": "STRANDED", "merge_commit": None, "detail": "d"},
                {"pr": 3, "sha": "b" * 40, "status": "NOT-MERGED", "merge_commit": None},
                {"pr": 4, "sha": "b" * 40, "status": "NO-DATA", "merge_commit": None}]
        report = merge_reach.format_report(rows)
        for word in ("PROVEN #1", "STRANDED #2", "NOT-MERGED #3", "NO-DATA #4", "c" * 40, "PROVEN: 1 of 4"):
            self.assertIn(word, report)

    def test_cli_check_exit_codes(self):
        feat = self.feature("feat", "f.txt")
        self.gitc("merge", "--ff-only", "--quiet", feat)
        self.git(self.clone, "push", "--quiet", "hub", "main")
        tree = self.tree(feat)
        self.assertEqual(merge_reach.main(["check", self.clone, "hub", "1", feat, tree, "MERGED"]), 0)
        self.assertEqual(merge_reach.main(["check", self.clone, "hub", "1", "b" * 40, tree, "MERGED"]), 2)
        self.assertEqual(merge_reach.main(["check"]), 2)


class TheCheckFetchesOnlyMain(Fixture):
    """F6, review 2026-10-06: `check` ran a full `git fetch hub`, which also updates every other branch and tag. One
    stale ref of no interest here failed that fetch AFTER the merge had landed, so a merge that was fine read "fetch of
    hub failed", "DONE: 0 of 1", exit 1. These cases run scripts/merge_reach.py check as its own process, the way
    merge_verified.sh starts it, against a hub another clone has pushed the merge to."""

    def setUp(self):
        super().setUp()
        self.feat = self.feature("feat", "f.txt")
        self.pusher = os.path.join(self.root, "pusher")
        os.mkdir(self.pusher)
        for args in (("init", "--quiet"), ("remote", "add", "hub", self.hub), ("fetch", "--quiet", "hub"),
                     ("checkout", "--quiet", "-B", "main", "hub/main")):
            self.push_git(*args)

    def push_git(self, *args):
        done = merge_reach._default_runner(["git"] + IDENT + list(args), cwd=self.pusher)
        self.assertEqual(done.returncode, 0, "git %s: %s" % (args, done.stderr))
        return done.stdout.strip()

    def land(self):
        """The merge lands on the hub from the other clone: this clone's own hub/main is stale until `check` fetches."""
        self.push_git("merge", "--no-ff", "--quiet", "-m", "merge feat", self.feat)
        self.push_git("push", "--quiet", "hub", "main")
        return self.push_git("rev-parse", "HEAD^{tree}")

    def check(self, tree, state="MERGED"):
        done = merge_reach._default_runner([sys.executable, "-B", os.path.join(HERE, "merge_reach.py"), "check",
                                            self.clone, "hub", "1", self.feat, tree, state])
        return done.returncode, done.stdout + done.stderr

    def refs(self):
        return self.gitc("for-each-ref", "--format=%(refname) %(objectname)")

    def test_a_stale_ref_of_another_branch_no_longer_fails_a_merge_that_landed(self):
        # hub/topic is known here; on the hub topic became a folder of branches, so a full fetch cannot write
        # refs/remotes/hub/topic/sub beside the stale refs/remotes/hub/topic and exits 1
        self.gitc("config", "fetch.prune", "false")   # the git default, pinned: a pruning fetch would hide the stale ref
        self.push_git("push", "--quiet", "hub", "main:topic")
        self.gitc("fetch", "--quiet", "hub")
        self.push_git("push", "--quiet", "hub", ":topic")
        self.push_git("push", "--quiet", "hub", "main:topic/sub")
        full = merge_reach._default_runner(["git", "fetch", "--quiet", "hub"], cwd=self.clone)
        self.assertNotEqual(full.returncode, 0, "the fixture must make a full fetch fail, or it proves nothing")
        rc, out = self.check(self.land())
        self.assertEqual(rc, 0, out)
        self.assertIn("PROVEN #1", out)

    def test_a_moved_tag_no_longer_fails_a_merge_that_landed(self):
        # a clone that fetches every tag (remote.hub.tagOpt) refuses to move one it holds: the whole fetch exits 1
        self.push_git(*NO_SIGN, "tag", "v1", "main")
        self.push_git("push", "--quiet", "hub", "v1")
        self.gitc("config", "remote.hub.tagOpt", "--tags")
        self.gitc("fetch", "--quiet", "hub")
        tree = self.land()
        self.push_git(*NO_SIGN, "tag", "--force", "v1", "main")
        self.push_git("push", "--quiet", "--force", "hub", "v1")
        full = merge_reach._default_runner(["git", "fetch", "--quiet", "hub"], cwd=self.clone)
        self.assertNotEqual(full.returncode, 0, "the fixture must make a full fetch fail, or it proves nothing")
        rc, out = self.check(tree)
        self.assertEqual(rc, 0, out)
        self.assertIn("PROVEN #1", out)

    def test_only_the_main_ref_is_written(self):
        tree = self.land()
        self.push_git("push", "--quiet", "hub", "main:other")
        self.push_git(*NO_SIGN, "tag", "v-new", "main")
        self.push_git("push", "--quiet", "hub", "v-new")
        before = self.refs().splitlines()
        rc, out = self.check(tree)
        self.assertEqual(rc, 0, out)
        changed = sorted(set(before) ^ set(self.refs().splitlines()))
        self.assertEqual([line.split()[0] for line in changed], ["refs/remotes/hub/main"] * 2, changed)

    def test_main_is_read_fresh_whatever_this_clone_is_configured_to_fetch(self):
        # a clone that tracks another branch only (git clone --single-branch) never updates hub/main by a bare fetch:
        # the stale ref said NOT-MERGED for a merge that had landed. The refspec is spelled, so the answer is the hub's.
        self.gitc("config", "remote.hub.fetch", "+refs/heads/feat:refs/remotes/hub/feat")
        rc, out = self.check(self.land())
        self.assertEqual(rc, 0, out)
        self.assertIn("PROVEN #1", out)

    def test_a_hub_that_cannot_be_reached_is_still_no_data(self):
        # the fail closed half, unchanged: no fetch, no verdict, whatever the stale local ref would have said
        tree = self.land()
        self.gitc("fetch", "--quiet", "hub")
        self.assertIs(merge_reach.is_reachable(self.clone, self.feat, "refs/remotes/hub/main"), True)
        self.gitc("remote", "set-url", "hub", os.path.join(self.root, "gone.git"))
        rc, out = self.check(tree)
        self.assertEqual(rc, 2, out)
        self.assertIn("NO-DATA #1", out)
        self.assertIn("fetch of hub failed", out)


class ShellWiring(unittest.TestCase):
    def test_merge_verified_calls_check_and_counts_only_when_proven(self):
        with open(os.path.join(HERE, "merge_verified.sh")) as handle:
            text = handle.read()
        self.assertIn('"$PY_REACH" check', text)
        self.assertIn("TREE-DRIFT", text)
        check = text.index('"$PY_REACH" check')
        self.assertLess(check, text.index("done_=$((done_+1))", check))
        self.assertIn("not proven merged", text)


class Hostile(unittest.TestCase):
    OK = (ValueError, LookupError, SystemExit, merge_reach.MergeReachError)

    def test_every_public_function_refuses_hostile_input(self):
        for name in ("is_reachable", "prove_merged", "format_report", "main"):
            func = getattr(merge_reach, name)
            for value in HOSTILE:
                for nargs in (1, 2, 3, 4, 5, 7):
                    try:
                        func(*([value] * nargs))
                    except self.OK:
                        pass
                    except TypeError as exc:
                        if "positional argument" in str(exc):
                            continue
                        self.fail("%s(%r x%d) crashed: %r" % (name, value, nargs, exc))
                    except Exception as exc:  # noqa: BLE001
                        self.fail("%s(%r x%d) crashed: %r" % (name, value, nargs, exc))

    def test_bad_entries_are_refused_not_accepted(self):
        good = {"pr": 1, "sha": "a" * 40, "tree": "b" * 40}
        for bad in ("x", None, [None], [{}], [dict(good, sha="x")], [dict(good, tree=True)], [dict(good, pr=True)],
                    [dict(good, pr=NAN)], [dict(good, sha=["a" * 40])]):
            with self.assertRaises(merge_reach.MergeReachError):
                merge_reach.prove_merged("/x", bad, lambda p: "OPEN")
        with self.assertRaises(merge_reach.MergeReachError):
            merge_reach.prove_merged("/x", [good], None)
        for bad in (True, 0, NAN, "x", b"x", None, [], object()):
            with self.assertRaises(merge_reach.MergeReachError):
                merge_reach.is_reachable("/x", bad)
        for bad in (0, b"x", object()):
            with self.assertRaises(SystemExit):
                merge_reach.main(bad)


if __name__ == "__main__":
    unittest.main()
