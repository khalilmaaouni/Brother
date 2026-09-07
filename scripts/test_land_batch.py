#!/usr/bin/env python3
"""test_land_batch: drives scripts/land_batch.sh against a local bare git
"origin" and a fake `gh`, never the real GitHub API or the real brother-hub
checkout.

WHAT STANDS IN FOR WHAT. land_batch.sh's own git plumbing (worktree add,
fetching `pull/<n>/head`, `git merge --no-ff`, pushing the integration
branch) is exercised for real against a throwaway bare repo built per test:
that plumbing is exactly what this script is for, so mocking it would test
nothing. Only two things are faked:

  - `gh`: a small python stand-in on PATH (write_fake_gh below) that answers
    `pr view` (state/base/title), `pr create` (mints a PR number, records its
    head branch) and `pr merge` (refuses if a `.refuse` marker is present;
    otherwise, for a PR minted by `pr create`, performs the SAME merge a real
    GitHub merge commit performs: a plumbing `commit-tree` with two parents,
    the tree of the head branch, landed on `refs/heads/main` in the bare
    origin). `pr view --json state` computes MERGED the same way the real
    API does when not already recorded: is this PR's head commit now an
    ancestor of main. That is what lets land_batch.sh's one-integration-PR
    design work in this test exactly as it will for real: merging the ONE
    batch PR carries every included PR's own head commit into main, and each
    of them reads MERGED on the next `gh pr view` without land_batch.sh ever
    calling `gh pr merge` on their own numbers.
  - the three gate commands (scripts/bundle_runtime.py, required_fast.sh,
    scripts/gen_readiness_board.py) plus the two regeneration commands
    (scripts/system_doc.py, products/*/scripts/checksums.sh): tiny fixtures
    checked into the fixture repo's main branch. The three gate commands
    count the `pr*.marker` files present in the worktree and pass only when
    that count is at or under $FIXTURE_MAX_PRS (default effectively
    unlimited), so a batch that merges too many PRs at once gates red
    exactly the way a real aggregate check would, and a smaller bisected
    half gates green. system_doc.py writes SYSTEM.md from that same marker
    count, so a batch that changes the merged marker count also changes
    SYSTEM.md, which is what the regeneration-commit test below checks for.

Cases: a green batch merges all through one integration PR; a red batch
merges none up front and bisects, salvaging both halves, each through its
own integration PR; a conflicting PR is skipped (left for the serial
lander) while its sibling still lands; GitHub refusing the ONE integration
PR's merge leaves every included PR queued and nothing marked merged; a
batch whose regeneration changes SYSTEM.md still gates green and the
regenerated file rides along in the batch's own commit. A further case
covers the mid-PR refusal at the top of the script (the serial runner still
between START and END).
"""
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LAND_BATCH = os.path.join(HERE, "land_batch.sh")


def sh(args, cwd=None, env=None, check=True):
    r = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("%s failed (%s):\n%s\n%s" % (args, r.returncode, r.stdout, r.stderr))
    return r


# Only the git identity overrides: NOT merged with os.environ here, since
# run_batch() layers this on top of a PATH it already modified to put the
# fake `gh` first, and a copy of the original os.environ baked in at import
# time would silently clobber that PATH back to the real one.
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.t"}


def git_env():
    return dict(os.environ, **GIT_ENV)

# The three gate fixtures size themselves on LAND_BATCH_PRS (the PR numbers
# land_batch.sh's run_gate exports for exactly this gate run), never on
# marker files actually present in the tree. Gate-and-merge-half chains a
# real integration PR onto a real, moving main (needed so ancestry-based
# merge verification works at all), so a half b built after half a's real
# merge always sees half a's marker files too; sizing on file count would
# make half b's gate red for reasons that have nothing to do with half b's
# own PRs. Sizing on the PR list land_batch.sh is actually gating right now
# keeps each half's pass/fail decided by its own PRs alone, which is what
# these fixtures are meant to simulate.
FIXTURE_BUNDLE = (
    "#!/usr/bin/env python3\n"
    "import os, sys\n"
    "limit = int(os.environ.get('FIXTURE_MAX_PRS', '999999'))\n"
    "count = len(os.environ.get('LAND_BATCH_PRS', '').split())\n"
    "sys.exit(0 if count <= limit else 1)\n")

FIXTURE_REQUIRED_FAST = (
    "#!/bin/sh\n"
    "limit=\"${FIXTURE_MAX_PRS:-999999}\"\n"
    "set -- $LAND_BATCH_PRS\n"
    "count=$#\n"
    "[ \"$count\" -le \"$limit\" ]\n")

FIXTURE_BOARD = (
    "#!/usr/bin/env python3\n"
    "import os, sys\n"
    "limit = int(os.environ.get('FIXTURE_MAX_PRS', '999999'))\n"
    "count = len(os.environ.get('LAND_BATCH_PRS', '').split())\n"
    "print('board ok' if count <= limit else 'board fail')\n"
    "sys.exit(0 if count <= limit else 1)\n")

# Regeneration fixtures: system_doc.py writes SYSTEM.md from the same marker
# count the gate fixtures read, so a batch that merges a different set of
# markers than the last regeneration saw produces a real diff to commit.
# checksums.sh (one copy per product) just needs to exit 0 and write its
# output file; land_batch.sh does not inspect its content.
FIXTURE_SYSTEM_DOC = (
    "#!/usr/bin/env python3\n"
    "import glob\n"
    "with open('SYSTEM.md', 'w') as f:\n"
    "    f.write('generated from %d markers\\n' % len(glob.glob('pr*.marker')))\n")

FIXTURE_CHECKSUMS = (
    "#!/bin/sh\n"
    "echo stub-checksum > \"$1\"\n")


def write_fake_gh(bin_dir, db_dir):
    path = os.path.join(bin_dir, "gh")
    body = '''#!/usr/bin/env python3
import os, subprocess, sys

DB = os.environ["FAKE_GH_DB"]
ORIGIN = os.environ["FAKE_GH_ORIGIN"]


def state_path(n):
    return os.path.join(DB, n + ".state")


def head_path(n):
    return os.path.join(DB, n + ".head")


def title_path(n):
    return os.path.join(DB, n + ".title")


def read_state(n):
    p = state_path(n)
    if os.path.exists(p):
        with open(p) as f:
            parts = f.read().strip().split("\\t")
            if len(parts) == 2:
                return parts
    return ["OPEN", "main"]


def write_state(n, state, base=None):
    cur = read_state(n)
    base = base if base is not None else cur[1]
    with open(state_path(n), "w") as f:
        f.write(state + "\\t" + base + "\\n")


def origin_git(args, check=True):
    r = subprocess.run(["git", "-C", ORIGIN] + args, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("git %r failed: %s" % (args, r.stderr))
    return r.stdout.strip()


def is_ancestor(ref):
    r = subprocess.run(["git", "-C", ORIGIN, "merge-base", "--is-ancestor", ref, "main"])
    return r.returncode == 0


def next_pr_number():
    counter = os.path.join(DB, "next_pr_number.txt")
    n = int(os.environ.get("FAKE_GH_NEXT_PR", "9001"))
    if os.path.exists(counter):
        with open(counter) as f:
            n = int(f.read().strip())
    with open(counter, "w") as f:
        f.write(str(n + 1))
    return n


argv = sys.argv[1:]
with open(os.path.join(DB, "calls.log"), "a") as f:
    f.write(" ".join(argv) + "\\n")


def opt(flag, default=None):
    return argv[argv.index(flag) + 1] if flag in argv else default


if argv[:2] == ["pr", "view"]:
    n = argv[2]
    state, base = read_state(n)
    if state != "MERGED":
        ref = "refs/pull/%s/head" % n
        r = subprocess.run(["git", "-C", ORIGIN, "rev-parse", "--verify", "-q", ref],
                            capture_output=True, text=True)
        if r.returncode == 0 and is_ancestor(ref):
            state = "MERGED"
            write_state(n, state, base)
    jq = opt("--jq", "")
    if jq == ".state":
        print(state)
    elif jq == ".title":
        tp = title_path(n)
        title = open(tp).read().strip() if os.path.exists(tp) else ("pr %s" % n)
        print(title)
    else:
        print(state + "\\t" + base)
    sys.exit(0)

if argv[:2] == ["pr", "create"]:
    head = opt("--head")
    base = opt("--base", "main")
    title = opt("--title", "")
    n = str(next_pr_number())
    write_state(n, "OPEN", base)
    with open(head_path(n), "w") as f:
        f.write(head or "")
    with open(title_path(n), "w") as f:
        f.write(title)
    print("https://github.com/fake/repo/pull/" + n)
    sys.exit(0)

if argv[:2] == ["pr", "merge"]:
    n = argv[2]
    if os.path.exists(os.path.join(DB, n + ".refuse")):
        sys.stderr.write("GraphQL: Pull Request is not mergeable (mergePullRequest)\\n")
        sys.exit(1)
    hp = head_path(n)
    if os.path.exists(hp):
        head_branch = open(hp).read().strip()
        main_tip = origin_git(["rev-parse", "main"])
        head_tip = origin_git(["rev-parse", "refs/heads/" + head_branch])
        tree = origin_git(["rev-parse", head_tip + "^{tree}"])
        new_sha = origin_git(["commit-tree", tree, "-p", main_tip, "-p", head_tip,
                               "-m", "Merge pull request #%s" % n])
        origin_git(["update-ref", "refs/heads/main", new_sha])
        if "--delete-branch" in argv:
            origin_git(["update-ref", "-d", "refs/heads/" + head_branch], check=False)
    write_state(n, "MERGED")
    print("https://github.com/fake/repo/pull/" + n)
    sys.exit(0)

sys.stderr.write("fake gh: unrecognized args: %r\\n" % (argv,))
sys.exit(1)
'''
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, 0o755)
    return path


class LandBatchFixture(unittest.TestCase):
    """Builds one throwaway bare 'origin' with the gate and regeneration
    fixtures on main, a clone to stand in for ~/brother-hub, and a fake
    `gh`."""

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="land-batch-test.")
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)

        self.origin = os.path.join(self.work, "origin.git")
        sh(["git", "init", "--bare", "-q", "-b", "main", self.origin])

        seed = os.path.join(self.work, "seed")
        sh(["git", "clone", "-q", self.origin, seed], env=git_env())
        os.makedirs(os.path.join(seed, "scripts"))
        self._write(seed, "scripts/bundle_runtime.py", FIXTURE_BUNDLE)
        self._write(seed, "scripts/required_fast.sh", FIXTURE_REQUIRED_FAST)
        self._write(seed, "scripts/gen_readiness_board.py", FIXTURE_BOARD)
        self._write(seed, "scripts/system_doc.py", FIXTURE_SYSTEM_DOC)
        self._write(seed, "products/brothermode/scripts/checksums.sh", FIXTURE_CHECKSUMS)
        self._write(seed, "products/brothersbe/scripts/checksums.sh", FIXTURE_CHECKSUMS)
        self._write(seed, "SYSTEM.md", "generated from 0 markers\n")
        self._write(seed, "README.md", "seed\n")
        sh(["git", "add", "-A"], cwd=seed, env=git_env())
        sh(["git", "commit", "-q", "-m", "seed"], cwd=seed, env=git_env())
        sh(["git", "push", "-q", "origin", "main"], cwd=seed, env=git_env())

        self.repo_dir = os.path.join(self.work, "repo")
        sh(["git", "clone", "-q", self.origin, self.repo_dir], env=git_env())

        self.evdir = os.path.join(self.work, "evidence")
        os.makedirs(self.evdir)

        self.bin_dir = os.path.join(self.work, "bin")
        os.makedirs(self.bin_dir)
        self.db_dir = os.path.join(self.work, "gh-db")
        os.makedirs(self.db_dir)
        write_fake_gh(self.bin_dir, self.db_dir)

    def _write(self, root, relpath, content):
        full = os.path.join(root, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
        if relpath.endswith(".sh") or relpath.endswith(".py"):
            os.chmod(full, 0o755)

    def make_pr(self, n, files, base_sha=None):
        """Branch from base_sha (default: current origin/main tip), add
        `files` (relpath -> content), push as a normal branch, then plant it
        at refs/pull/<n>/head the way GitHub would."""
        clone = os.path.join(self.work, "pr-src-%s" % n)
        sh(["git", "clone", "-q", self.origin, clone], env=git_env())
        if base_sha:
            sh(["git", "checkout", "-q", base_sha], cwd=clone, env=git_env())
        sh(["git", "checkout", "-q", "-b", "pr-branch-%s" % n], cwd=clone, env=git_env())
        for relpath, content in files.items():
            self._write(clone, relpath, content)
        sh(["git", "add", "-A"], cwd=clone, env=git_env())
        sh(["git", "commit", "-q", "-m", "pr %s" % n], cwd=clone, env=git_env())
        sha = sh(["git", "rev-parse", "HEAD"], cwd=clone, env=git_env()).stdout.strip()
        # Push straight to refs/pull/<n>/head, the way GitHub itself exposes
        # a PR's tip; the object only exists in origin once this lands.
        sh(["git", "push", "-q", "origin", "HEAD:refs/pull/%s/head" % n], cwd=clone, env=git_env())
        with open(os.path.join(self.db_dir, "%s.state" % n), "w") as f:
            f.write("OPEN\tmain\n")
        return sha

    def main_tip(self):
        return sh(["git", "rev-parse", "main"], cwd=self.origin, env=git_env()).stdout.strip()

    def run_batch(self, args, extra_env=None, fixture_max_prs=None):
        env = dict(os.environ)
        env["PATH"] = self.bin_dir + os.pathsep + env["PATH"]
        env["LAND_BATCH_REPO"] = self.repo_dir
        env["LAND_BATCH_REPO_SLUG"] = "fake/repo"
        env["LAND_BATCH_EVIDENCE"] = self.evdir
        env["FAKE_GH_DB"] = self.db_dir
        env["FAKE_GH_ORIGIN"] = self.origin
        env.update(GIT_ENV)
        if fixture_max_prs is not None:
            env["FIXTURE_MAX_PRS"] = str(fixture_max_prs)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(["bash", LAND_BATCH] + args, env=env,
                               capture_output=True, text=True)

    def queue_lines(self):
        p = os.path.join(self.evdir, "land-queue.txt")
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return [l.strip() for l in f if l.strip()]

    def land_log(self):
        p = os.path.join(self.evdir, "serial-land.log")
        if not os.path.exists(p):
            return ""
        with open(p) as f:
            return f.read()

    def pr_state(self, n):
        p = os.path.join(self.db_dir, "%s.state" % n)
        if not os.path.exists(p):
            return None
        with open(p) as f:
            return f.read().strip().split("\t")[0]

    def calls_log(self):
        p = os.path.join(self.db_dir, "calls.log")
        if not os.path.exists(p):
            return ""
        with open(p) as f:
            return f.read()


class GreenBatchMergesAll(LandBatchFixture):
    def test_two_independent_prs_gate_green_and_both_merge_via_one_integration_pr(self):
        self.make_pr("101", {"pr101.marker": "x\n"})
        self.make_pr("102", {"pr102.marker": "x\n"})
        r = self.run_batch(["101", "102"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.pr_state("101"), "MERGED")
        self.assertEqual(self.pr_state("102"), "MERGED")
        log = self.land_log()
        self.assertIn("merge 101: state MERGED", log)
        self.assertIn("LAND-101-END", log)
        self.assertIn("merge 102: state MERGED", log)
        self.assertIn("LAND-102-END", log)
        self.assertEqual(self.queue_lines(), [])
        # exactly one integration pull request created and merged, never a
        # direct merge call against 101 or 102's own PR numbers.
        calls = self.calls_log()
        self.assertEqual(calls.count("pr create"), 1)
        self.assertEqual(calls.count("pr merge"), 1)
        self.assertIn("Batch landing of 2 pull requests: 101, 102", calls)


class RegenerationRidesAlongInTheBatchCommit(LandBatchFixture):
    def test_a_batch_whose_regeneration_changes_system_md_still_gates_green(self):
        self.make_pr("111", {"pr111.marker": "x\n", "pr112.marker": "x\n"})
        r = self.run_batch(["111"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.pr_state("111"), "MERGED")
        # SYSTEM.md must have moved from the seed's "0 markers" to "2
        # markers" (this PR alone adds two marker files), and that change
        # must be reachable from main, i.e. it rode in the batch's own
        # commit on the integration branch that got merged.
        content = sh(["git", "show", "main:SYSTEM.md"], cwd=self.origin, env=git_env()).stdout
        self.assertEqual(content.strip(), "generated from 2 markers")


class RedBatchBisects(LandBatchFixture):
    def test_three_prs_red_as_one_batch_green_as_two_bisected_halves(self):
        self.make_pr("201", {"pr201.marker": "x\n"})
        self.make_pr("202", {"pr202.marker": "x\n"})
        self.make_pr("203", {"pr203.marker": "x\n"})
        # 3 merged files is over the limit for one gate, at or under it for
        # either bisected half (2, then 1).
        r = self.run_batch(["201", "202", "203"], fixture_max_prs=2)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("BATCH RED:", r.stdout)
        # both halves salvaged: all three still end up merged, each half
        # through its own integration pull request.
        self.assertEqual(self.pr_state("201"), "MERGED")
        self.assertEqual(self.pr_state("202"), "MERGED")
        self.assertEqual(self.pr_state("203"), "MERGED")
        self.assertEqual(self.queue_lines(), [])
        self.assertEqual(self.calls_log().count("pr create"), 2)

    def test_a_half_that_is_still_red_is_left_queued_the_other_half_lands(self):
        # Three independent, non-conflicting PRs, limit 1: the full batch
        # (3) is red, bisected half a ([221,222], 2 files) is STILL red (at
        # most two extra gate runs, no further recursion), half b ([223], 1
        # file) is green and lands for real.
        self.make_pr("221", {"pr221.marker": "x\n"})
        self.make_pr("222", {"pr222.marker": "x\n"})
        self.make_pr("223", {"pr223.marker": "x\n"})
        r = self.run_batch(["221", "222", "223"], fixture_max_prs=1)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("BATCH RED half a", r.stdout)
        self.assertEqual(self.pr_state("221"), "OPEN")
        self.assertEqual(self.pr_state("222"), "OPEN")
        self.assertEqual(self.pr_state("223"), "MERGED")
        self.assertIn("221", self.queue_lines())
        self.assertIn("222", self.queue_lines())
        self.assertNotIn("223", self.queue_lines())
        # only half b ever reached the integration-PR route.
        self.assertEqual(self.calls_log().count("pr create"), 1)


class ConflictingPRIsSkipped(LandBatchFixture):
    def test_second_pr_editing_the_same_line_is_skipped_first_still_lands(self):
        self.make_pr("301", {"shared.txt": "X\n"})
        self.make_pr("302", {"shared.txt": "Y\n"})
        r = self.run_batch(["301", "302"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("conflict", r.stdout.lower())
        self.assertEqual(self.pr_state("301"), "MERGED")
        self.assertEqual(self.pr_state("302"), "OPEN")
        self.assertEqual(self.queue_lines(), ["302"])
        self.assertNotIn("LAND-302", self.land_log())


class IntegrationPRRefusalLeavesEverythingQueued(LandBatchFixture):
    def test_github_refusing_the_integration_pr_merge_leaves_everything_queued(self):
        self.make_pr("401", {"pr401.marker": "x\n"})
        self.make_pr("402", {"pr402.marker": "x\n"})
        # The fake gh mints PR numbers from FAKE_GH_NEXT_PR (default 9001);
        # this batch's only `pr create` call will mint exactly that number
        # for the integration PR, so a refuse marker placed on it ahead of
        # time simulates GitHub refusing THAT merge, before either 401 or
        # 402's own number is ever touched.
        with open(os.path.join(self.db_dir, "9001.refuse"), "w") as f:
            f.write("refused\n")
        r = self.run_batch(["401", "402"])
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("REFUSED", r.stdout + r.stderr)
        calls = self.calls_log()
        self.assertEqual(calls.count("pr create"), 1)
        self.assertEqual(calls.count("pr merge"), 1)
        self.assertNotEqual(self.pr_state("401"), "MERGED")
        self.assertNotEqual(self.pr_state("402"), "MERGED")
        self.assertIn("401", self.queue_lines())
        self.assertIn("402", self.queue_lines())
        self.assertNotIn("LAND-401", self.land_log())
        self.assertNotIn("LAND-402", self.land_log())


class SerialRunnerMidPRRefusesToStart(LandBatchFixture):
    def test_a_start_with_no_end_and_a_live_pid_refuses(self):
        self.make_pr("501", {"pr501.marker": "x\n"})
        with open(os.path.join(self.evdir, "serial-queue.log"), "w") as f:
            f.write("12:00:00 END 499\n12:00:05 START 500\n")
        r = self.run_batch(["501"], extra_env={"LAND_BATCH_SERIAL_PID": str(os.getpid())})
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("NO-DATA", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.evdir, "land-batch.lock")))

    def test_a_dead_recorded_pid_lets_the_batch_take_over(self):
        self.make_pr("502", {"pr502.marker": "x\n"})
        with open(os.path.join(self.evdir, "serial-queue.log"), "w") as f:
            f.write("12:00:00 END 499\n12:00:05 START 500\n")
        # Spawn and immediately reap a short-lived process to get a pid that
        # is guaranteed dead by the time we check it.
        p = subprocess.Popen(["true"])
        p.wait()
        r = self.run_batch(["502"], extra_env={"LAND_BATCH_SERIAL_PID": str(p.pid)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.pr_state("502"), "MERGED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
