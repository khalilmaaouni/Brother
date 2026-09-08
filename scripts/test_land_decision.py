"""land_queue's structured governed landing path, driven backwards (P3a,
night 2026-09-07, docs/plan/runs/night-2026-09-07/design-P3.md and
codex-findings-P3.md). Mirrors test_land_queue.py's fixture style: a local
bare remote plays the forge, no network, no real pull request, per steering
law 9 (no live autonomous merge into Brother main tonight).
"""
import hashlib
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import land_queue as LQ  # noqa: E402
import fable_authority as FA  # noqa: E402

try:
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n" % os.path.basename(__file__))

import tempfile  # noqa: E402

REPO = "khalilmaaouni/fixture"
GREEN_GATE = 'echo "## required_fast exit 0"'
RED_GATE = 'echo "FAIL: 3 tests failed"'
EMPTY_GATE = "true"  # produces no captured output at all: classify_gate's NO-DATA case


def _git(args, cwd=None):
    proc = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (args, proc.stderr))
    return proc


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _new_bare_remote(d):
    remote = os.path.join(d, "remote.git")
    _git(["init", "--bare", "-q", remote])
    return remote


def _seed(remote, d, work_name="work"):
    """Base (main) with one commit, and a candidate branch 'cand' one
    commit ahead, both pushed to `remote`. Returns (base_sha, cand_sha)."""
    work = os.path.join(d, work_name)
    _git(["init", "-q", work])
    _git(["config", "user.email", "t@t"], work)
    _git(["config", "user.name", "t"], work)
    _write(os.path.join(work, "f.txt"), "base\n")
    _git(["add", "f.txt"], work)
    _git(["commit", "-q", "-m", "base"], work)
    _git(["remote", "add", "origin", remote], work)
    _git(["push", "-q", "origin", "HEAD:refs/heads/main"], work)
    base_sha = _git(["rev-parse", "HEAD"], work).stdout.strip()
    _git(["checkout", "-q", "-b", "cand"], work)
    _write(os.path.join(work, "f.txt"), "base\ncandidate\n")
    _git(["commit", "-q", "-am", "candidate"], work)
    cand_sha = _git(["rev-parse", "HEAD"], work).stdout.strip()
    _git(["push", "-q", "origin", "HEAD:refs/heads/cand"], work)
    return base_sha, cand_sha


def _grant(repository, base, action, now=None):
    """A live scoped delegation, the shape decide_land/resolve_authority
    expect: this IS the seam the design says P3a's own tests must use so
    they never depend on P3b's fable_authority.py landing in this
    worktree (a callable, injected as `authority_provider`, never a real
    fable_authority import)."""
    return {
        "repository": repository, "base": base, "action": action,
        "risk_ceiling": "low", "until": "2099-01-01T00:00:00+00:00",
        "merge_method": "merge", "granted_by": "test-fixture",
        "words": "fixture grant, never a live one",
    }


def _no_grant(repository, base, action, now=None):
    return None


def _mk(d):
    """One queue file, one gate_dir, one log path, ready to use."""
    queue = os.path.join(d, "queue.txt")
    open(queue, "w", encoding="utf-8").close()
    return queue, d, os.path.join(d, "land.log")


def _enqueue(queue, *ids):
    with open(queue, "w", encoding="utf-8") as fh:
        fh.write("\n".join(ids) + "\n")


def _record(gate_dir, n, repository=REPO):
    return LQ.read_land_record(LQ.land_record_path(gate_dir, LQ._slug(repository), n))


# ---------------------------------------------------------------- positive --

class GovernedLandingLandsAGreenCandidate(unittest.TestCase):
    def test_green_candidate_lands_and_the_receipt_names_the_verified_revision(self):
        d = tempfile.mkdtemp()
        remote = _new_bare_remote(d)
        base_sha, cand_sha = _seed(remote, d)
        queue, gate_dir, log = _mk(d)
        _enqueue(queue, "PR1")
        adapter = LQ.GitFixtureAdapter(remote)

        receipt_path = os.path.join(d, "receipt.json")
        _write(receipt_path, '{"ok": true}')
        submissions = {"PR1": {"id": "PR1", "branch": "cand", "owns": ["f.txt"],
                                "check_cmd": ["true"], "receipt_path": receipt_path}}

        outcomes = LQ.run_governed(queue, log, GREEN_GATE, REPO, "main", "merge", adapter,
                                    gate_dir=gate_dir, authority_provider=_grant,
                                    submissions=submissions)
        self.assertEqual(outcomes, [("PR1", LQ.LAND_PASS)])

        record = _record(gate_dir, "PR1")
        self.assertEqual(record["decision"], LQ.LAND_PASS)
        self.assertEqual(record["gated_head_sha"], cand_sha)
        self.assertEqual(record["gated_base_sha"], base_sha)
        self.assertTrue(record["merge_result"]["merge_command_succeeded"])
        self.assertIs(record["post_merge"]["merged_revision_verified"], True)
        self.assertEqual(record["decided_by"], "structured-adapter")
        self.assertEqual(
            record["delivery_receipt"],
            {"path": receipt_path, "sha256": hashlib.sha256(b'{"ok": true}').hexdigest()},
        )
        merged_sha = record["merge_result"]["merged_sha"]
        self.assertEqual(adapter.head_sha("main"), merged_sha)
        # the merge really did carry the candidate's own content forward
        show = _git(["--git-dir=%s" % remote, "show", "%s:f.txt" % merged_sha]).stdout
        self.assertIn("candidate", show)


# ----------------------------------------------------------- the negatives --

class GateFailNeverMerges(unittest.TestCase):
    def test_gate_fail_never_invokes_the_adapter_merge(self):
        d = tempfile.mkdtemp()
        remote = _new_bare_remote(d)
        base_sha, cand_sha = _seed(remote, d)
        queue, gate_dir, log = _mk(d)
        _enqueue(queue, "PR2")
        adapter = LQ.GitFixtureAdapter(remote)
        calls = []
        real_merge = adapter.merge
        adapter.merge = lambda *a, **k: (calls.append(1) or real_merge(*a, **k))

        outcomes = LQ.run_governed(queue, log, RED_GATE, REPO, "main", "merge", adapter,
                                    gate_dir=gate_dir, authority_provider=_grant,
                                    submissions={"PR2": {"id": "PR2", "branch": "cand"}})
        self.assertEqual(outcomes, [("PR2", LQ.LAND_FAIL)])
        self.assertEqual(calls, [], "the adapter's merge() must never be called on a FAIL gate")
        self.assertEqual(adapter.head_sha("main"), base_sha, "main must not have moved")
        record = _record(gate_dir, "PR2")
        self.assertEqual(record["decision"], LQ.LAND_FAIL)
        self.assertEqual(record["merge_result"], {})


class GateNoDataNeverMerges(unittest.TestCase):
    def test_gate_no_data_never_invokes_the_adapter_merge(self):
        d = tempfile.mkdtemp()
        remote = _new_bare_remote(d)
        base_sha, cand_sha = _seed(remote, d)
        queue, gate_dir, log = _mk(d)
        _enqueue(queue, "PR3")
        adapter = LQ.GitFixtureAdapter(remote)
        calls = []
        real_merge = adapter.merge
        adapter.merge = lambda *a, **k: (calls.append(1) or real_merge(*a, **k))

        outcomes = LQ.run_governed(queue, log, EMPTY_GATE, REPO, "main", "merge", adapter,
                                    gate_dir=gate_dir, authority_provider=_grant,
                                    submissions={"PR3": {"id": "PR3", "branch": "cand"}})
        self.assertEqual(outcomes, [("PR3", LQ.LAND_NO_DATA)])
        self.assertEqual(calls, [], "the adapter's merge() must never be called on a NO-DATA gate")
        self.assertEqual(adapter.head_sha("main"), base_sha)
        self.assertEqual(_record(gate_dir, "PR3")["decision"], LQ.LAND_NO_DATA)


class HeadChangedAfterGateIsStale(unittest.TestCase):
    def test_head_changed_after_gate_is_stale_and_not_merged(self):
        """Reproduction 1 in codex-findings-P3: gate one SHA, push a fix,
        merge must refuse the new SHA it was never gated against."""
        d = tempfile.mkdtemp()
        remote = _new_bare_remote(d)
        base_sha, cand_sha = _seed(remote, d)
        queue, gate_dir, log = _mk(d)
        _enqueue(queue, "PR4")
        adapter = LQ.GitFixtureAdapter(remote)

        # An ordinary race: the author pushes a fix to `cand` the instant
        # after the head is captured, before the gate result is used.
        real_head_sha = adapter.head_sha
        calls = {"n": 0}

        def moving_head(ref):
            if ref != "cand":
                return real_head_sha(ref)
            calls["n"] += 1
            if calls["n"] == 2:
                work2 = os.path.join(d, "work2")
                _git(["clone", "-q", remote, work2])
                _git(["config", "user.email", "t@t"], work2)
                _git(["config", "user.name", "t"], work2)
                _git(["checkout", "-q", "cand"], work2)
                _write(os.path.join(work2, "f.txt"), "base\ncandidate\nfixed\n")
                _git(["commit", "-q", "-am", "fix"], work2)
                _git(["push", "-q", "origin", "cand"], work2)
            return real_head_sha(ref)

        adapter.head_sha = moving_head
        calls_merge = []
        real_merge = adapter.merge
        adapter.merge = lambda *a, **k: (calls_merge.append(1) or real_merge(*a, **k))

        outcomes = LQ.run_governed(queue, log, GREEN_GATE, REPO, "main", "merge", adapter,
                                    gate_dir=gate_dir, authority_provider=_grant,
                                    submissions={"PR4": {"id": "PR4", "branch": "cand"}})
        self.assertEqual(outcomes, [("PR4", LQ.LAND_STALE)])
        self.assertEqual(calls_merge, [], "a stale head must never reach adapter.merge")
        record = _record(gate_dir, "PR4")
        self.assertEqual(record["gated_head_sha"], cand_sha)
        self.assertNotEqual(real_head_sha("cand"), cand_sha, "the fixture really did move cand")


class BaseMovedForcesReGate(unittest.TestCase):
    def test_base_moved_forces_re_gate_and_does_not_reuse_the_receipt(self):
        d = tempfile.mkdtemp()
        remote = _new_bare_remote(d)
        base_sha, cand_sha = _seed(remote, d)
        queue, gate_dir, log = _mk(d)
        _enqueue(queue, "PR5")
        adapter = LQ.GitFixtureAdapter(remote)

        real_base_sha = adapter.base_sha
        calls = {"n": 0}

        def moving_base(base_branch):
            calls["n"] += 1
            if calls["n"] == 2:
                work2 = os.path.join(d, "work2")
                _git(["clone", "-q", remote, work2])
                _git(["config", "user.email", "t@t"], work2)
                _git(["config", "user.name", "t"], work2)
                _write(os.path.join(work2, "other.txt"), "unrelated\n")
                _git(["add", "other.txt"], work2)
                _git(["commit", "-q", "-m", "unrelated main advance"], work2)
                _git(["push", "-q", "origin", "HEAD:refs/heads/main"], work2)
            return real_base_sha(base_branch)

        adapter.base_sha = moving_base
        calls_merge = []
        real_merge = adapter.merge
        adapter.merge = lambda *a, **k: (calls_merge.append(1) or real_merge(*a, **k))

        outcomes = LQ.run_governed(queue, log, GREEN_GATE, REPO, "main", "merge", adapter,
                                    gate_dir=gate_dir, authority_provider=_grant,
                                    submissions={"PR5": {"id": "PR5", "branch": "cand"}})
        self.assertEqual(outcomes, [("PR5", LQ.LAND_BASE_MOVED)])
        self.assertEqual(calls_merge, [], "a moved base must never reach adapter.merge")
        record = _record(gate_dir, "PR5")
        self.assertEqual(record["gated_base_sha"], base_sha)
        self.assertNotEqual(real_base_sha("main"), base_sha, "the fixture really did move main")


class MissingAuthorityIsReadyForHuman(unittest.TestCase):
    def test_missing_authority_is_ready_for_human_not_merged(self):
        """Steering gate D: 'missing delegation -> READY-FOR-HUMAN'."""
        d = tempfile.mkdtemp()
        remote = _new_bare_remote(d)
        base_sha, cand_sha = _seed(remote, d)
        queue, gate_dir, log = _mk(d)
        _enqueue(queue, "PR6")
        adapter = LQ.GitFixtureAdapter(remote)
        calls = []
        real_merge = adapter.merge
        adapter.merge = lambda *a, **k: (calls.append(1) or real_merge(*a, **k))

        outcomes = LQ.run_governed(queue, log, GREEN_GATE, REPO, "main", "merge", adapter,
                                    gate_dir=gate_dir, authority_provider=_no_grant,
                                    submissions={"PR6": {"id": "PR6", "branch": "cand"}})
        self.assertEqual(outcomes, [("PR6", LQ.LAND_READY_FOR_HUMAN)])
        self.assertEqual(calls, [], "no grant must never reach adapter.merge")
        record = _record(gate_dir, "PR6")
        self.assertEqual(record["decision"], LQ.LAND_READY_FOR_HUMAN)
        self.assertEqual(record["authority"], {"status": "NO-DATA", "reason": "no live delegation"})

    def test_import_error_from_fable_authority_reads_as_no_data_not_allowed(self):
        """Until P3b lands fable_authority.delegation_for, the lazy import
        raises ImportError; resolve_authority must treat that as NO-DATA,
        never as an accidental grant (law 2)."""
        self.assertIsNone(LQ.resolve_authority(REPO, "main", "merge_or_release"))


class ImportErrorDefaultsToNoData(unittest.TestCase):
    def test_resolve_authority_with_no_provider_and_no_fable_authority_function(self):
        result = LQ.resolve_authority(REPO, "main", "merge_or_release", authority_provider=None)
        self.assertIsNone(result)


class ResumeAfterMergeNeverMergesTwice(unittest.TestCase):
    def test_resume_after_a_merge_does_not_merge_twice(self):
        d = tempfile.mkdtemp()
        remote = _new_bare_remote(d)
        base_sha, cand_sha = _seed(remote, d)
        queue, gate_dir, log = _mk(d)
        _enqueue(queue, "PR7")
        adapter = LQ.GitFixtureAdapter(remote)

        outcomes = LQ.run_governed(queue, log, GREEN_GATE, REPO, "main", "merge", adapter,
                                    gate_dir=gate_dir, authority_provider=_grant,
                                    submissions={"PR7": {"id": "PR7", "branch": "cand"}})
        self.assertEqual(outcomes, [("PR7", LQ.LAND_PASS)])
        merged_sha = _record(gate_dir, "PR7")["merge_result"]["merged_sha"]

        calls = []
        real_merge = adapter.merge
        adapter.merge = lambda *a, **k: (calls.append(1) or real_merge(*a, **k))
        resumed = LQ.resume(gate_dir, REPO, "main", adapter, authority_provider=_grant)
        self.assertEqual(resumed, [("PR7", LQ.LAND_ALREADY_LANDED)])
        self.assertEqual(calls, [], "a resumed already-landed candidate must never merge again")
        self.assertEqual(adapter.head_sha("main"), merged_sha, "main must not have moved a second time")


# --------------------------------------------------------- legacy template --

class LegacyTemplateWithRedGateNeverMerges(unittest.TestCase):
    def test_legacy_merge_template_is_never_invoked_on_a_red_gate(self):
        """codex-findings-P3 #1, driven directly against LQ.run() (the
        legacy --gate/--merge path), independent of test_land_queue.py's
        own coverage of the same fix."""
        d = tempfile.mkdtemp()
        queue = os.path.join(d, "queue.txt")
        log = os.path.join(d, "land.log")
        marker = os.path.join(d, "ran.marker")
        _enqueue(queue, "77")
        gate = os.path.join(d, "gate.sh")
        _write(gate, "#!/bin/bash\n" + RED_GATE)
        os.chmod(gate, 0o755)
        merge = os.path.join(d, "merge.sh")
        _write(merge, "#!/bin/bash\ntouch %s\necho state MERGED" % marker)
        os.chmod(merge, 0o755)

        rc = LQ.run(queue, log, "bash %s {n}" % gate, "bash %s {n}" % merge, concurrency=1)
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(marker))
        with open(log, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("gate verdict FAIL, merge template not invoked", text)


# ------------------------------------------------------------------- race --

class RaceBetweenFreshnessReadAndMerge(unittest.TestCase):
    def test_base_moving_between_the_freshness_read_and_the_merge_call_lands_nothing(self):
        """codex-findings-P3 #2: the freshness re-read and the merge call
        are two separate steps; a concurrent push landing in between must
        never be silently overwritten. `race_hook` fires from inside
        adapter.merge(), after the new commit object is built but
        immediately before the compare-and-swap update-ref, simulating a
        push to `main` (the BASE, not the candidate head) that lands in
        exactly that window (F11: renamed, the injected push moves the
        base, the old name said head)."""
        d = tempfile.mkdtemp()
        remote = _new_bare_remote(d)
        base_sha, cand_sha = _seed(remote, d)
        queue, gate_dir, log = _mk(d)
        _enqueue(queue, "PR8")
        adapter = LQ.GitFixtureAdapter(remote)

        def race():
            work2 = os.path.join(d, "work2")
            _git(["clone", "-q", remote, work2])
            _git(["config", "user.email", "t@t"], work2)
            _git(["config", "user.name", "t"], work2)
            _write(os.path.join(work2, "other.txt"), "concurrent\n")
            _git(["add", "other.txt"], work2)
            _git(["commit", "-q", "-m", "concurrent push"], work2)
            _git(["push", "-q", "origin", "HEAD:refs/heads/main"], work2)

        adapter.race_hook = race

        outcomes = LQ.run_governed(queue, log, GREEN_GATE, REPO, "main", "merge", adapter,
                                    gate_dir=gate_dir, authority_provider=_grant,
                                    submissions={"PR8": {"id": "PR8", "branch": "cand"}})
        self.assertEqual(outcomes, [("PR8", "MERGE-FAILED")])
        record = _record(gate_dir, "PR8")
        self.assertFalse(record["merge_result"]["merge_command_succeeded"])
        self.assertIn("compare-and-swap refused", record["merge_result"]["reason"])
        self.assertFalse(record.get("merged"))
        # main advanced exactly once, by the race, never by this run's own merge
        adapter.race_hook = None
        walk = _git(["--git-dir=%s" % remote, "log", "--oneline", "main"]).stdout
        self.assertNotIn(cand_sha[:7], walk.split("\n")[0], "the candidate must not have landed on main")


# --------------------------------------------------------------- crashes --

class CrashCases(unittest.TestCase):
    def test_crash_before_gate_leaves_the_candidate_recoverable(self):
        """pop_and_queue writes the QUEUED record before anything else
        happens; a crash right there still leaves a named record."""
        d = tempfile.mkdtemp()
        queue, gate_dir, log = _mk(d)
        _enqueue(queue, "PR9")
        n, path = LQ.pop_and_queue(queue, gate_dir, LQ._slug(REPO), REPO, "main",
                                    gate_cmd=GREEN_GATE, merge_method="merge")
        self.assertEqual(n, "PR9")
        record = LQ.read_land_record(path)
        self.assertEqual(record["state"], "QUEUED")
        self.assertIsNone(record["gated_head_sha"])
        self.assertEqual(LQ.queue_length(queue), 0, "the id left the queue; the record is now its home")

    def test_crash_during_gate_resumes_to_no_data_and_merges_nothing(self):
        d = tempfile.mkdtemp()
        remote = _new_bare_remote(d)
        base_sha, cand_sha = _seed(remote, d)
        queue, gate_dir, log = _mk(d)
        _enqueue(queue, "PR10")
        # Simulate: the candidate was popped and its head/base captured,
        # but the process died before any gate attempt completed.
        adapter = LQ.GitFixtureAdapter(remote)
        n, path = LQ.pop_and_queue(queue, gate_dir, LQ._slug(REPO), REPO, "main")
        record = LQ.read_land_record(path)
        record["submission"] = {"id": "PR10", "branch": "cand"}
        record["gated_head_sha"] = adapter.head_sha("cand")
        record["gated_base_sha"] = adapter.base_sha("main")
        LQ.write_land_record(path, record)

        outcomes = LQ.resume(gate_dir, REPO, "main", adapter, authority_provider=_grant)
        self.assertEqual(outcomes, [("PR10", "NO-DATA: never completed a gate, requeue by hand")])
        self.assertEqual(adapter.head_sha("main"), base_sha, "nothing merged")

    def test_crash_after_gate_before_merge_rechecks_freshness_on_resume(self):
        d = tempfile.mkdtemp()
        remote = _new_bare_remote(d)
        base_sha, cand_sha = _seed(remote, d)
        queue, gate_dir, log = _mk(d)
        _enqueue(queue, "PR11")
        adapter = LQ.GitFixtureAdapter(remote)
        n, path = LQ.pop_and_queue(queue, gate_dir, LQ._slug(REPO), REPO, "main")
        record = LQ.read_land_record(path)
        record["submission"] = {"id": "PR11", "branch": "cand"}
        record["gated_head_sha"] = adapter.head_sha("cand")
        record["gated_base_sha"] = adapter.base_sha("main")
        record["gate_results"] = [{"attempt": 1, "exit_code": 0, "verdict": LQ.GATE_PASS}]
        LQ.write_land_record(path, record)
        # The gate ran and passed, but the process died before merging.

        outcomes = LQ.resume(gate_dir, REPO, "main", adapter, authority_provider=_grant)
        self.assertEqual(outcomes, [("PR11", LQ.LAND_PASS)])
        record2 = LQ.read_land_record(path)
        self.assertTrue(record2["merged"])
        self.assertEqual(adapter.head_sha("main"), record2["merge_result"]["merged_sha"])

    def test_crash_after_remote_merge_resumes_to_already_landed(self):
        """The remote merge command succeeded, but the process died
        before land_queue recorded that. Resume must detect it from the
        forge itself, not from the (stale) local record, and must not
        merge a second time."""
        d = tempfile.mkdtemp()
        remote = _new_bare_remote(d)
        base_sha, cand_sha = _seed(remote, d)
        queue, gate_dir, log = _mk(d)
        _enqueue(queue, "PR12")
        adapter = LQ.GitFixtureAdapter(remote)
        n, path = LQ.pop_and_queue(queue, gate_dir, LQ._slug(REPO), REPO, "main")
        record = LQ.read_land_record(path)
        record["submission"] = {"id": "PR12", "branch": "cand"}
        record["gated_head_sha"] = adapter.head_sha("cand")
        record["gated_base_sha"] = adapter.base_sha("main")
        record["gate_results"] = [{"attempt": 1, "exit_code": 0, "verdict": LQ.GATE_PASS}]
        LQ.write_land_record(path, record)
        # Merge really happens on the remote, but the record is never
        # told (the exact gap "crash immediately after remote merge"
        # describes): merged=False stays on disk.
        ok, merged_sha, _ = adapter.merge(record["gated_head_sha"], "main", base_sha, method="merge")
        self.assertTrue(ok)

        calls = []
        real_merge = adapter.merge
        adapter.merge = lambda *a, **k: (calls.append(1) or real_merge(*a, **k))
        outcomes = LQ.resume(gate_dir, REPO, "main", adapter, authority_provider=_grant)
        self.assertEqual(outcomes, [("PR12", LQ.LAND_ALREADY_LANDED)])
        self.assertEqual(calls, [], "resume must never merge a second time once the forge already landed it")
        self.assertEqual(adapter.head_sha("main"), merged_sha)


# ---------------------------------------------------- repository refusal --

class RepositoryRefusalIsAnOutcome(unittest.TestCase):
    def test_repository_refusal_is_recorded_as_an_outcome_not_retried(self):
        """Steering 9.9: a forge policy refusal (branch protection, a
        required review, anything the forge itself declines) is an
        outcome, never something this module retries or works around."""
        d = tempfile.mkdtemp()
        remote = _new_bare_remote(d)
        base_sha, cand_sha = _seed(remote, d)
        queue, gate_dir, log = _mk(d)
        _enqueue(queue, "PR13")

        class RefusingAdapter(LQ.GitFixtureAdapter):
            def merge(self, *a, **k):
                return False, None, "REFUSED BY REPOSITORY POLICY: required review missing"

        adapter = RefusingAdapter(remote)
        outcomes = LQ.run_governed(queue, log, GREEN_GATE, REPO, "main", "merge", adapter,
                                    gate_dir=gate_dir, authority_provider=_grant,
                                    submissions={"PR13": {"id": "PR13", "branch": "cand"}})
        self.assertEqual(outcomes, [("PR13", "MERGE-FAILED")])
        record = _record(gate_dir, "PR13")
        self.assertIn("REFUSED BY REPOSITORY POLICY", record["merge_result"]["reason"])
        self.assertFalse(record["merge_result"]["merge_command_succeeded"])
        self.assertFalse(record.get("merged"))
        self.assertEqual(adapter.head_sha("main"), base_sha)


# ------------------------------------------------------------ F1: action --

class ActionWordAgreesBetweenCliAndResolveAuthority(unittest.TestCase):
    def test_a_grant_written_by_the_real_cli_is_found_with_no_injected_provider(self):
        """F1: land_queue.resolve_authority's real (non-test) path and the
        grant CLI must agree on the action word, or the authority half of
        the design is a no-op that only ever looks live because every
        test injects authority_provider. Driven with NO injected
        provider: the real fable_authority.delegation_for import is what
        answers, exactly like production."""
        d = tempfile.mkdtemp()
        delegations_log = os.path.join(d, "delegations.jsonl")
        cli = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "fable_authority.py"),
             "grant-merge", "--repository", REPO, "--base", "main", "--action", FA.MERGE_ACTION,
             "--risk-ceiling", "A2", "--until", "2099-01-01T00:00:00Z", "--merge-method", "merge",
             "--granted-by", "Khalil Maaouni", "--words", "land it tonight",
             "--delegations-log", delegations_log],
            capture_output=True, text=True,
        )
        self.assertEqual(cli.returncode, 0, cli.stdout + cli.stderr)
        self.assertIn("GRANTED", cli.stdout)

        saved = FA.DELEGATIONS_LOG
        FA.DELEGATIONS_LOG = delegations_log
        try:
            found = LQ.resolve_authority(REPO, "main", authority_provider=None)
        finally:
            FA.DELEGATIONS_LOG = saved
        self.assertIsNotNone(found, "the CLI's own --action word must be found by "
                                     "resolve_authority's own default action word")
        self.assertEqual(found["repository"], REPO)
        self.assertEqual(found["merge_method"], "merge")


# --------------------------------------------------------- F2: NO-DATA revisions --

class NoDataRevisionIsNeverReadAsFresh(unittest.TestCase):
    def test_both_shas_unread_is_no_data_not_pass(self):
        """F2: None == None must never read as 'unchanged'."""
        candidate = {"gated_head_sha": None, "gated_base_sha": None, "merged": False}
        decision = LQ.decide_land(candidate, LQ.GATE_PASS, None, None, {"merge_method": "merge"},
                                   method="merge")
        self.assertEqual(decision, LQ.LAND_NO_DATA)

    def test_only_the_base_unread_is_still_no_data(self):
        candidate = {"gated_head_sha": "deadbeef", "gated_base_sha": None, "merged": False}
        decision = LQ.decide_land(candidate, LQ.GATE_PASS, "deadbeef", None, {"merge_method": "merge"},
                                   method="merge")
        self.assertEqual(decision, LQ.LAND_NO_DATA)


class GhAdapterRefusesAMissingGatedHeadSha(unittest.TestCase):
    def test_merge_with_a_none_sha_refuses_instead_of_raising(self):
        adapter = LQ.GhAdapter("owner/repo", cwd=".", run=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("adapter must refuse before ever building an argv list")))
        ok, merged_sha, reason = adapter.merge(None, "main", "basesha", method="merge", pr_ref=42)
        self.assertFalse(ok)
        self.assertIsNone(merged_sha)
        self.assertIn("gated_head_sha", reason)


# --------------------------------------------------- F3: method and risk scope --

class ReadyForHumanWhenGrantMergeMethodDiffers(unittest.TestCase):
    def test_a_squash_only_grant_never_authorizes_a_full_merge(self):
        authority = {"merge_method": "squash", "risk_ceiling": "A2"}
        candidate = {"gated_head_sha": "h", "gated_base_sha": "b", "merged": False}
        decision = LQ.decide_land(candidate, LQ.GATE_PASS, "h", "b", authority, method="merge")
        self.assertEqual(decision, LQ.LAND_READY_FOR_HUMAN)


class ReadyForHumanWhenRiskCeilingBelowCandidateClass(unittest.TestCase):
    def test_an_a1_grant_never_authorizes_an_a3_candidate(self):
        authority = {"merge_method": "merge", "risk_ceiling": "A1"}
        candidate = {"gated_head_sha": "h", "gated_base_sha": "b", "merged": False, "risk_class": "A3"}
        decision = LQ.decide_land(candidate, LQ.GATE_PASS, "h", "b", authority, method="merge")
        self.assertEqual(decision, LQ.LAND_READY_FOR_HUMAN)

    def test_a_matching_ceiling_still_passes(self):
        authority = {"merge_method": "merge", "risk_ceiling": "A2"}
        candidate = {"gated_head_sha": "h", "gated_base_sha": "b", "merged": False, "risk_class": "A2"}
        decision = LQ.decide_land(candidate, LQ.GATE_PASS, "h", "b", authority, method="merge")
        self.assertEqual(decision, LQ.LAND_PASS)


# ---------------------------------------------------------- F5: classify_gate --

class ClassifyGateReadsTheExitCode(unittest.TestCase):
    def test_nonzero_exit_with_a_green_marker_is_fail_not_pass(self):
        self.assertEqual(LQ.classify_gate(1, "## required_fast exit 0"), LQ.GATE_FAIL)

    def test_zero_exit_with_a_green_marker_still_passes(self):
        self.assertEqual(LQ.classify_gate(0, "## required_fast exit 0"), LQ.GATE_PASS)


# --------------------------------------------------------------- F7: broad catch --

class ResolveAuthorityCatchesAnyProviderException(unittest.TestCase):
    def test_a_raising_provider_reads_as_no_data_and_names_the_exception_class(self):
        def _raises(repository, base, action, now):
            raise ValueError("corrupt delegations store")

        result = LQ.resolve_authority(REPO, "main", authority_provider=_raises)
        self.assertIsNone(result)
        self.assertEqual(LQ.resolve_authority.last_error, "ValueError")


# ------------------------------------------------------- F8: merged-unverified --

class SquashMergeWithAnUnverifiablePostMergeCheckIsMergedUnverified(unittest.TestCase):
    def test_squash_with_a_base_moved_verification_gap_is_merged_unverified_not_pass(self):
        """F8: _verify_merged correctly returns None ('NO-DATA') when the
        post-merge tree-hash comparison cannot be attempted (here: the
        base moved again before verification could run, simulated
        directly on the adapter rather than via a second real push, since
        the point under test is the caller's handling of None, not the
        git mechanics already covered by the race test above). The land
        command DID succeed; the outcome word must say so is unverified,
        never claim PASS."""
        d = tempfile.mkdtemp()
        remote = _new_bare_remote(d)
        base_sha, cand_sha = _seed(remote, d)
        queue, gate_dir, log = _mk(d)
        _enqueue(queue, "PR14")

        class UnverifiableSquashAdapter(LQ.GitFixtureAdapter):
            def verify_merged(self, gated_head_sha, merged_sha, base_branch, method):
                return None, "NO-DATA: base moved again before verification could run"

        adapter = UnverifiableSquashAdapter(remote)

        def _squash_grant(repository, base, action, now=None):
            g = _grant(repository, base, action, now)
            g["merge_method"] = "squash"
            return g

        outcomes = LQ.run_governed(queue, log, GREEN_GATE, REPO, "main", "squash", adapter,
                                    gate_dir=gate_dir, authority_provider=_squash_grant,
                                    submissions={"PR14": {"id": "PR14", "branch": "cand"}})
        self.assertEqual(outcomes, [("PR14", LQ.LAND_MERGED_UNVERIFIED)])
        record = _record(gate_dir, "PR14")
        self.assertEqual(record["state"], "MERGED-UNVERIFIED")
        self.assertTrue(record["merge_result"]["merge_command_succeeded"])
        self.assertIsNone(record["post_merge"]["merged_revision_verified"])
        self.assertTrue(record["merged"], "the merge command really did land; resume must not retry it")


# --------------------------------------------------------------- F10: crash --

class CrashBetweenWriteAndPopLeavesBoth(unittest.TestCase):
    def test_a_crash_between_writing_the_record_and_removing_the_queue_line_leaves_both(self):
        """F10: the record must be written BEFORE the queue line is
        removed, not after, so a crash in between leaves a recoverable
        trace on both sides rather than an id popped with nothing
        naming it. Simulated by monkeypatching _remove_line to raise
        after the record write already happened."""
        d = tempfile.mkdtemp()
        queue, gate_dir, log = _mk(d)
        _enqueue(queue, "PR15")

        real_remove = LQ._remove_line

        def crash(*a, **k):
            raise RuntimeError("simulated crash between write and pop")

        LQ._remove_line = crash
        try:
            with self.assertRaises(RuntimeError):
                LQ.pop_and_queue(queue, gate_dir, LQ._slug(REPO), REPO, "main",
                                  gate_cmd=GREEN_GATE, merge_method="merge")
        finally:
            LQ._remove_line = real_remove

        # Both survive the crash: the line is still queued...
        self.assertEqual(LQ.queue_length(queue), 1)
        # ...and the record already exists, named and QUEUED.
        path = LQ.land_record_path(gate_dir, LQ._slug(REPO), "PR15")
        record = LQ.read_land_record(path)
        self.assertIsNotNone(record)
        self.assertEqual(record["state"], "QUEUED")


if __name__ == "__main__":
    unittest.main()
