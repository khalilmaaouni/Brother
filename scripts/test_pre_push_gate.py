#!/usr/bin/env python3
"""pre_push_gate.check_correctness, driven both ways over a scratch repo.

Written 2026-09-01 after the gate false-refused the first new branch pushed
since D9 landed: git's `--not` toggles, so `name --not --remotes --not tips`
made the imported-history tips POSITIVE and the gate scanned 565 commits of
immutable imported history as if they were outgoing. These tests pin both
directions: the imported exclusion really excludes, and a forbidden line in a
genuinely outgoing commit still refuses.

The forbidden trailer is assembled from parts everywhere in this file, because
a scanner's own test must not carry what the scanner forbids.
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pre_push_gate as G  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

TRAILER_LINE = "Co-" + "Authored" + "-By: Claude <no" + "reply@anthropic.com>"


def sh(args, cwd):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          timeout=60)


class ImportedHistoryStaysExcludedOnANewBranch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ppg-")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        for args in (["init", "-q", "-b", "main"],
                     ["config", "user.email", "a@b.c"],
                     ["config", "user.name", "t"]):
            sh(["git"] + args, cwd=self.repo)
        # A clean base commit, which is what main and the remote hold.
        with open(os.path.join(self.repo, "base.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("clean base\n")
        sh(["git", "add", "-A"], cwd=self.repo)
        sh(["git", "commit", "-q", "-m", "clean base"], cwd=self.repo)
        base = sh(["git", "rev-parse", "HEAD"], cwd=self.repo).stdout.strip()
        # The "imported" history: an ORPHAN commit whose FILE carries the
        # forbidden trailer, standing in for an immutable archived product
        # history. Orphan and absent from every remote ref, which is the real
        # shape that triggered the 2026-09-01 false refusal (the archive tips
        # were not reachable from any remote, so the toggled --not made their
        # whole histories positive).
        sh(["git", "checkout", "-q", "--orphan", "archive/imported"],
           cwd=self.repo)
        sh(["git", "rm", "-rfq", "."], cwd=self.repo)
        with open(os.path.join(self.repo, "imported.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("imported product history\n%s\n" % TRAILER_LINE)
        sh(["git", "add", "-A"], cwd=self.repo)
        sh(["git", "commit", "-q", "-m", "imported history"], cwd=self.repo)
        self.imported_tip = sh(["git", "rev-parse", "HEAD"],
                               cwd=self.repo).stdout.strip()
        sh(["git", "checkout", "-q", "main"], cwd=self.repo)
        # A remote ref that does NOT contain the new branch (the new-branch
        # shape), created directly rather than via a network remote.
        sh(["git", "update-ref", "refs/remotes/origin/main", base],
           cwd=self.repo)
        # The roots file the gate reads, naming the imported tip.
        plan = os.path.join(self.repo, "docs", "plan")
        os.makedirs(plan)
        with open(os.path.join(plan, "IMPORTED-HISTORY-ROOTS.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("# scratch roots\n%s imported\n" % self.imported_tip)
        # The new branch with one clean outgoing commit.
        sh(["git", "checkout", "-q", "-b", "wbs/scratch"], cwd=self.repo)
        with open(os.path.join(self.repo, "work.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("outgoing work\n")
        sh(["git", "add", "-A"], cwd=self.repo)
        sh(["git", "commit", "-q", "-m", "outgoing work"], cwd=self.repo)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def findings(self):
        return G.check_correctness(cwd=self.repo)

    def test_a_clean_new_branch_is_not_refused_for_imported_history(self):
        """The 2026-09-01 false refusal: the imported commit's trailer must
        not read as outgoing. Fails on the pre-fix double --not form."""
        blocks = [f for f in self.findings() if f[0] == "BLOCK"]
        self.assertEqual(blocks, [], blocks)

    def test_a_trailer_in_a_genuinely_outgoing_commit_still_refuses(self):
        """The exclusion must not neuter the scan: the same forbidden line
        added by the branch's own commit is a refusal. Also drives the real
        script end to end, since this is this suite's only seeded offender
        on a genuine branch: BLOCK maps to exit 1, never a softer code."""
        with open(os.path.join(self.repo, "bad.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("outgoing\n%s\n" % TRAILER_LINE)
        sh(["git", "add", "-A"], cwd=self.repo)
        sh(["git", "commit", "-q", "-m", "carries the line"], cwd=self.repo)
        blocks = [f for f in self.findings() if f[0] == "BLOCK"]
        self.assertTrue(any("attribution" in f[2] for f in blocks), blocks)
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "pre_push_gate.py"),
             "--cwd", self.repo],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
            timeout=60)
        self.assertEqual(proc.returncode, G.EXIT_BLOCKED,
                         proc.stdout + proc.stderr)


class DetachedHeadHasNoOutgoingRange(unittest.TestCase):
    """The bug battery round 6 found (E31): a battery run certifies a git
    worktree pinned at one SHA, whose HEAD is detached. git's abbrev-ref for
    a detached HEAD prints the literal string "HEAD", and origin/HEAD is
    itself a real ref (the remote's default-branch symref), so the old code
    took HEAD as a branch name and scanned origin/HEAD..HEAD, the whole
    branch, from a checkout that will never push. Both directions here: the
    finding-level NO-DATA, and the full script's exit code."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ppg-detached-")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        for args in (["init", "-q", "-b", "main"],
                     ["config", "user.email", "a@b.c"],
                     ["config", "user.name", "t"]):
            sh(["git"] + args, cwd=self.repo)
        with open(os.path.join(self.repo, "base.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("clean base\n")
        sh(["git", "add", "-A"], cwd=self.repo)
        sh(["git", "commit", "-q", "-m", "clean base"], cwd=self.repo)
        head = sh(["git", "rev-parse", "HEAD"], cwd=self.repo).stdout.strip()
        # A real origin/HEAD symref, exactly the shape a real clone carries,
        # so the collision this bug hinges on (origin/HEAD resolving) is
        # exercised for real rather than assumed absent.
        sh(["git", "update-ref", "refs/remotes/origin/main", head],
           cwd=self.repo)
        sh(["git", "symbolic-ref", "refs/remotes/origin/HEAD",
            "refs/remotes/origin/main"], cwd=self.repo)
        # The pinned-worktree shape itself: HEAD detached at one SHA.
        sh(["git", "checkout", "-q", "--detach", head], cwd=self.repo)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_detached_head_reads_no_data_not_the_whole_branch(self):
        findings = G.check_correctness(cwd=self.repo)
        self.assertEqual(len(findings), 1, findings)
        level, family, detail = findings[0]
        self.assertEqual((level, family), (G.NODATA, "correctness"), findings)
        self.assertIn("detached", detail)

    def test_detached_head_collision_reads_no_data_not_a_block(self):
        # E78: the collision check used to take the literal string "HEAD"
        # as a branch name and compare it against origin/HEAD (a real
        # symref), so a pinned worktree behind a moving origin/main BLOCKed
        # on a checkout that structurally cannot push at all.
        findings = G.check_collision(cwd=self.repo)
        self.assertEqual(len(findings), 1, findings)
        level, family, detail = findings[0]
        self.assertEqual((level, family), (G.NODATA, "collision"), findings)
        self.assertIn("detached", detail)

    def test_detached_head_gate_exits_clean_not_no_data_or_block(self):
        # E78: NO-DATA on a detached HEAD is the structurally correct
        # answer for a checkout that has no outgoing range at all, not a
        # real push whose state could not be read, so the full script
        # exits clean rather than refusing or reading NO-DATA.
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "pre_push_gate.py"),
             "--cwd", self.repo],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
            timeout=60)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, G.EXIT_OK, out)
        self.assertIn("NO-DATA", out)

    def test_detached_head_with_a_moved_origin_still_exits_clean(self):
        # The exact bug: origin/main advances past the pinned SHA while the
        # worktree stays detached at the old one (a battery run whose
        # certification predates a peer's merge landing on origin). Move
        # origin AHEAD on the local main branch, never the detached
        # checkout itself, then re-detach at the now-behind commit.
        pinned = sh(["git", "rev-parse", "HEAD"], cwd=self.repo).stdout.strip()
        sh(["git", "checkout", "-q", "main"], cwd=self.repo)
        sh(["git", "commit", "-q", "--allow-empty", "-m", "a peer's commit"],
           cwd=self.repo)
        # There is no real "origin" remote here (refs/remotes/origin/* were
        # written directly in setUp to model a clone); advance that ref by
        # hand rather than fetching, to model origin/main moving without a
        # push having happened from this checkout.
        moved = sh(["git", "rev-parse", "HEAD"], cwd=self.repo).stdout.strip()
        sh(["git", "update-ref", "refs/remotes/origin/main", moved],
           cwd=self.repo)
        sh(["git", "checkout", "-q", "--detach", pinned], cwd=self.repo)
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "pre_push_gate.py"),
             "--cwd", self.repo],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
            timeout=60)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, G.EXIT_OK, out)
        self.assertIn("NO-DATA", out)
        self.assertNotIn("BLOCK", out)


class AttachedBranchBehindOriginStillBlocks(unittest.TestCase):
    """The regression this fix must not open: a REAL checkout (an attached
    branch) that is genuinely behind its remote must still refuse, exactly
    as before E78. Only a detached HEAD gets the exit-0 treatment."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ppg-behind-")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        for args in (["init", "-q", "-b", "main"],
                     ["config", "user.email", "a@b.c"],
                     ["config", "user.name", "t"]):
            sh(["git"] + args, cwd=self.repo)
        with open(os.path.join(self.repo, "base.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("clean base\n")
        sh(["git", "add", "-A"], cwd=self.repo)
        sh(["git", "commit", "-q", "-m", "clean base"], cwd=self.repo)
        behind_sha = sh(["git", "rev-parse", "HEAD"],
                        cwd=self.repo).stdout.strip()
        # origin has one more commit this branch does not: a real "behind".
        sh(["git", "commit", "-q", "--allow-empty", "-m", "origin's own commit"],
           cwd=self.repo)
        ahead = sh(["git", "rev-parse", "HEAD"], cwd=self.repo).stdout.strip()
        sh(["git", "update-ref", "refs/remotes/origin/main", ahead],
           cwd=self.repo)
        sh(["git", "symbolic-ref", "refs/remotes/origin/HEAD",
            "refs/remotes/origin/main"], cwd=self.repo)
        # main itself stays on the older commit: an attached branch, behind.
        sh(["git", "reset", "-q", "--hard", behind_sha], cwd=self.repo)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_an_attached_branch_behind_origin_still_blocks(self):
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "pre_push_gate.py"),
             "--cwd", self.repo],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
            timeout=60)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, G.EXIT_BLOCKED, out)
        self.assertIn("BLOCK", out)


class ADetachedPushIsScannedFromTheRefsOnStdin(unittest.TestCase):
    """E105, measured 2026-09-04 21:10: a push from a worktree with a
    detached HEAD printed "NO-DATA collision", "NO-DATA correctness" and
    "HEAD is detached (a pinned worktree); nothing pushes from here", and
    LANDED on the hub unscanned. The gate read the checkout's HEAD instead of
    the ref lines git feeds a pre-push hook on stdin, and a detached HEAD
    pushes perfectly well: git push <remote> <sha>:refs/heads/<branch>.

    Driven through a real git push against a real (local, bare) remote, so
    the ref lines come from git's own protocol rather than a hand-built
    string. The hook here calls the two families under test ALONE: the full
    gate's drift and remote-rules checks have nothing to say about a scratch
    repository and would report NO-DATA regardless, muddying the verdict (the
    same reason test_handback_guard.py runs a thin hook)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ppg-detached-push-")
        self.bare = os.path.join(self.tmp, "remote.git")
        self.repo = os.path.join(self.tmp, "repo")
        sh(["git", "init", "-q", "--bare", "-b", "main", self.bare],
           cwd=self.tmp)
        os.makedirs(self.repo)
        for args in (["init", "-q", "-b", "main"],
                     ["config", "user.email", "a@b.c"],
                     ["config", "user.name", "t"]):
            sh(["git"] + args, cwd=self.repo)
        with open(os.path.join(self.repo, "base.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("clean base\n")
        sh(["git", "add", "-A"], cwd=self.repo)
        sh(["git", "commit", "-q", "-m", "clean base"], cwd=self.repo)
        sh(["git", "remote", "add", "origin", self.bare], cwd=self.repo)
        sh(["git", "push", "-q", "origin", "main"], cwd=self.repo)
        base = sh(["git", "rev-parse", "HEAD"], cwd=self.repo).stdout.strip()
        # The shape that leaked: a pinned worktree checkout, HEAD detached.
        sh(["git", "checkout", "-q", "--detach", base], cwd=self.repo)
        hooks = os.path.join(self.tmp, "hooks")
        os.makedirs(hooks)
        runner = os.path.join(hooks, "two_families.py")
        with open(runner, "w", encoding="utf-8") as fh:
            fh.write(
                "import sys\n"
                "sys.path.insert(0, %r)\n"
                "import pre_push_gate as g\n"
                "text = sys.stdin.read()\n"
                "found = g.check_collision(cwd='.', stdin_text=text) + \\\n"
                "        g.check_correctness(cwd='.', stdin_text=text)\n"
                "for f in found:\n"
                "    print('%%-8s %%-12s %%s' %% f)\n"
                "sys.exit(1 if any(f[0] == g.BLOCK for f in found)\n"
                "         else 2 if any(f[0] == g.NODATA for f in found)\n"
                "         else 0)\n" % HERE)
        hook = os.path.join(hooks, "pre-push")
        with open(hook, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\nexec %s %s\n" % (sys.executable, runner))
        os.chmod(hook, 0o755)
        sh(["git", "config", "core.hooksPath", hooks], cwd=self.repo)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def commit(self, name, body):
        with open(os.path.join(self.repo, name), "w", encoding="utf-8") as fh:
            fh.write(body)
        sh(["git", "add", "-A"], cwd=self.repo)
        sh(["git", "commit", "-q", "-m", "a detached commit"], cwd=self.repo)

    def push(self):
        """The real thing: a detached HEAD pushed to a branch on the remote,
        which is exactly the command that landed unscanned."""
        proc = sh(["git", "push", "origin", "HEAD:refs/heads/wbs/scratch"],
                  cwd=self.repo)
        return proc.returncode, proc.stdout + proc.stderr

    def test_a_detached_push_carrying_a_dash_is_refused(self):
        # The dash is built from its code point for the same reason the gate
        # builds it: a scanner's test must not carry what the scanner forbids.
        self.commit("copy.md", "a line %s with the forbidden dash\n"
                    % chr(0x2014))
        code, out = self.push()
        self.assertNotEqual(code, 0, out)
        self.assertIn("BLOCK", out)
        self.assertIn("dash", out)

    def test_a_detached_push_carrying_a_trailer_is_refused(self):
        self.commit("bad.md", "outgoing\n%s\n" % TRAILER_LINE)
        code, out = self.push()
        self.assertNotEqual(code, 0, out)
        self.assertIn("BLOCK", out)
        self.assertIn("attribution", out)

    def test_a_clean_detached_push_is_allowed(self):
        # The other direction, and the one that keeps the fix honest: a
        # detached push with nothing forbidden in it must still go through,
        # scanned rather than waved past. Before the fix both families read
        # NO-DATA here, which is not a pass.
        self.commit("work.md", "ordinary outgoing work\n")
        code, out = self.push()
        self.assertEqual(code, 0, out)
        self.assertNotIn("BLOCK", out)
        self.assertNotIn("NO-DATA", out)

    def test_empty_stdin_is_a_no_op_not_a_scan(self):
        # No ref lines means no push in flight (the battery running the gate
        # as a plain command), and a detached checkout then genuinely has no
        # outgoing range: NO-DATA, which main() turns into a clean exit.
        self.commit("copy.md", "a line %s with the forbidden dash\n"
                    % chr(0x2014))
        self.assertEqual(G._pushed_updates(""), [])
        findings = G.check_correctness(cwd=self.repo, stdin_text="")
        self.assertEqual([f[0] for f in findings], [G.NODATA], findings)
        self.assertIn("detached", findings[0][2])
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "pre_push_gate.py"),
             "--cwd", self.repo],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
            timeout=60)
        self.assertEqual(proc.returncode, G.EXIT_OK,
                         proc.stdout + proc.stderr)


class ThePublicExampleValueIsNotASecret(unittest.TestCase):
    """KNOWN_PUBLIC_EXAMPLE_VALUES holds the access key id AWS prints in its
    own documentation, which the products' detection fixtures reproduce on
    purpose. The scan strips exactly that value; any other value of the
    same shape still matches. A value allowlist, never a path one."""

    def test_the_documented_example_is_stripped_and_another_value_is_not(self):
        example = G.KNOWN_PUBLIC_EXAMPLE_VALUES[0]
        other = "AKIA" + "Q" * 16
        text = "doc: %s and leak: %s" % (example, other)
        stripped = G.strip_public_examples(text)
        self.assertNotIn(example, stripped)
        self.assertIn(other, stripped)
        self.assertTrue(any(p.search(stripped) for p in G.SECRET_SHAPES))
        only_example = G.strip_public_examples("doc: %s" % example)
        self.assertFalse(any(p.search(only_example) for p in G.SECRET_SHAPES))


if __name__ == "__main__":
    unittest.main()
