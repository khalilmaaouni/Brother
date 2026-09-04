"""Adversarial fixtures for `sbe status --team` (spec:
docs/specs/2026-07-30-sbe-status-team.md). Written BEFORE the implementation:
on the day this landed, every scenario was red because the status subcommand
had no --team flag, and none was red on a fixture bug.

Every fixture builds its own throwaway git repository with one or more
dossiers under design/, each seeded through the real `sbe plan --write`, so
the team view is always read over artifacts the real tools produced.
"""
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
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

def _chmod_can_deny_reads():
    """True when chmod 000 on this host actually makes a file unreadable.

    The same idiom, and the same words, as `_chmod_can_deny_reads` in
    test_sbe_fence_hook.py. Asked of the filesystem rather than of
    sys.platform or of geteuid, because the question is not which platform
    this is, it is whether this host can construct the condition under test.
    As uid 0 it cannot: the mode is written and the file stays readable.

    A skip here is a genuine NO-DATA, not a pass: on a host where the scenario
    cannot be built, nothing was examined, and the skip line says so.
    """
    with tempfile.TemporaryDirectory() as d:
        probe = os.path.join(d, "probe")
        with open(probe, "w") as f:
            f.write("x")
        try:
            os.chmod(probe, 0o000)
            with open(probe):
                return False          # still readable, so chmod proved nothing
        except (OSError, PermissionError):
            return True
        finally:
            os.chmod(probe, 0o600)


HERE = os.path.dirname(os.path.abspath(__file__))
SBE = os.path.join(HERE, "..", "bin", "sbe")

INTAKE = {
    "answers": {"changes_contract": "n", "crosses_boundary": "n",
                "reversible_under_hour": "y", "touches_sensitive": "n",
                "consumers": "none"},
    "tier": "T1", "override": None, "override_reason": None,
}

ADR_TEMPLATE = """# ADR
## Context
x
## Decision
Serve the change from `%s`.
## Consequences
ok
"""

VERIFICATION = """| Claim this design makes | The check that proves it | When it runs |
|---|---|---|
| it answers | `python3 -c pass` | CI |
"""


def _run(argv, cwd=None, env=None):
    out = subprocess.run(argv, capture_output=True, text=True, cwd=cwd, env=env,
                         stdin=subprocess.DEVNULL, timeout=120)
    # Three values, not two: a two-value return reads as a possible
    # (verdict, evidence) pair to the honesty meta-test, which refuses any
    # such function sitting outside a check registry.
    return out.returncode, out.stdout + out.stderr, out.stderr


class TeamScenario(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-team-")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "fixture")
        io.open(os.path.join(self.repo, "seed.txt"), "w").write("seed\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "seed")

    def tearDown(self):
        reg = os.path.join(self.repo, ".sbe", "tasks.json")
        if os.path.exists(reg):
            os.chmod(reg, stat.S_IRUSR | stat.S_IWUSR)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args):
        code, text, _err = _run(["git", "-C", self.repo] + list(args))
        self.assertEqual(code, 0, "git %s failed: %s" % (args, text))
        return text

    def sbe(self, *args):
        return _run([sys.executable, SBE] + list(args))

    def team(self, *extra):
        code, text, err = self.sbe("status", self.repo, "--team", *extra)
        if "unrecognized arguments" in text:
            self.fail("sbe status has no --team flag yet: %s" % text.strip())
        return code, text, err

    def handover(self, *args):
        """LT-302.B fixtures build real `12-handover.json` records through
        the real `sbe handover` engine (mirroring `tools/test_sbe_handover.py`'s
        own `HandoverScenario.handover`), never a hand-typed JSON stand-in."""
        return self.sbe("handover", *args)

    def _change(self, name, src_rel):
        doss = os.path.join(self.repo, "design", name)
        os.makedirs(doss)
        io.open(os.path.join(doss, "00-intake.json"), "w").write(
            json.dumps(INTAKE, indent=2))
        io.open(os.path.join(doss, "03-adr.md"), "w").write(ADR_TEMPLATE % src_rel)
        io.open(os.path.join(doss, "07-verification.md"), "w").write(VERIFICATION)
        src = os.path.join(self.repo, src_rel)
        parent = os.path.dirname(src)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        io.open(src, "w").write("x = 1\n")
        code, text, _ = self.sbe("plan", doss, "--write", "--cwd", self.repo)
        self.assertEqual(code, 0, "plan --write for %s: %s" % (name, text))
        return doss

    def _open_record(self, task_id, owns, agent="alice", base_commit=None):
        """Seed an open registry record directly, the same technique the
        registry's own suite uses for states only real runs could otherwise
        produce."""
        reg_dir = os.path.join(self.repo, ".sbe")
        if not os.path.isdir(reg_dir):
            os.makedirs(reg_dir)
        reg = os.path.join(reg_dir, "tasks.json")
        data = {"schemaVersion": "1.0", "tasks": []}
        if os.path.exists(reg):
            data = json.loads(io.open(reg).read())
        data["tasks"].append({
            "id": task_id, "agent": agent, "role": "writer", "worktree": None,
            "ownedPaths": owns, "readOnlyPaths": [], "baseCommit": base_commit,
            "expiry": None, "status": "open", "verifyCommand": "python3 -c pass",
            "evidenceId": None, "openedAt": "2026-07-30T00:00:00Z", "closedAt": None,
        })
        io.open(reg, "w").write(json.dumps(data, indent=2))

    def _closed_record(self, task_id, owns, agent="alice", base_commit=None,
                       forced=False):
        """A CLOSED (clean, unless forced) registry record for a task id, the
        completed-task counterpart to `_open_record`."""
        reg_dir = os.path.join(self.repo, ".sbe")
        if not os.path.isdir(reg_dir):
            os.makedirs(reg_dir)
        reg = os.path.join(reg_dir, "tasks.json")
        data = {"schemaVersion": "1.0", "tasks": []}
        if os.path.exists(reg):
            data = json.loads(io.open(reg).read())
        record = {
            "id": task_id, "agent": agent, "role": "writer", "worktree": None,
            "ownedPaths": owns, "readOnlyPaths": [], "baseCommit": base_commit,
            "expiry": None, "status": "closed", "verifyCommand": "python3 -c pass",
            "evidenceId": None, "openedAt": "2026-07-30T00:00:00Z",
            "closedAt": "2026-07-30T01:00:00Z",
        }
        if forced:
            record["forced"] = {"who": "bob", "why": "test"}
        data["tasks"].append(record)
        io.open(reg, "w").write(json.dumps(data, indent=2))

    def _forced_record(self, task_id):
        reg_dir = os.path.join(self.repo, ".sbe")
        if not os.path.isdir(reg_dir):
            os.makedirs(reg_dir)
        reg = os.path.join(reg_dir, "tasks.json")
        data = {"schemaVersion": "1.0", "tasks": []}
        if os.path.exists(reg):
            data = json.loads(io.open(reg).read())
        data["tasks"].append({
            "id": task_id, "agent": "bob", "role": "writer", "worktree": None,
            "ownedPaths": ["src/x.py"], "readOnlyPaths": [], "baseCommit": None,
            "expiry": None, "status": "closed-forced", "verifyCommand": "",
            "evidenceId": None, "openedAt": "2026-07-30T00:00:00Z",
            "closedAt": "2026-07-30T01:00:00Z", "forced": True,
        })
        io.open(reg, "w").write(json.dumps(data, indent=2))


class TestDiscoveryAndOrdering(TeamScenario):
    def test_two_changes_are_listed_together_each_with_a_next_action(self):
        self._change("chg-a", "src/a.py")
        self._change("chg-b", "src/b.py")
        code, text, _ = self.team()
        self.assertIn("chg-a", text)
        self.assertIn("chg-b", text)
        self.assertGreaterEqual(text.count("next action"), 2, text)

    def test_the_human_output_is_deterministic(self):
        self._change("chg-a", "src/a.py")
        self._change("chg-b", "src/b.py")
        _code, first, _ = self.team()
        _code, second, _ = self.team()
        self.assertEqual(first, second, "no timestamps, no ordering jitter")

    def test_a_missing_plan_names_sbe_plan_as_the_next_action_not_an_error(self):
        doss = os.path.join(self.repo, "design", "bare")
        os.makedirs(doss)
        io.open(os.path.join(doss, "00-intake.json"), "w").write(
            json.dumps(INTAKE, indent=2))
        code, text, _ = self.team()
        self.assertEqual(code, 0, text)
        self.assertIn("sbe plan", text)

    def test_designRoots_profile_adds_a_second_directory_of_dossiers(self):
        # F2: a second, in-repo directory named by .sbe/team-profile.json
        # holds its own dossier, and both changes are discovered together.
        self._change("chg-a", "src/a.py")
        alt_root = os.path.join(self.repo, "other-designs")
        doss = os.path.join(alt_root, "chg-alt")
        os.makedirs(doss)
        io.open(os.path.join(doss, "00-intake.json"), "w").write(
            json.dumps(INTAKE, indent=2))
        sbe_dir = os.path.join(self.repo, ".sbe")
        if not os.path.isdir(sbe_dir):
            os.makedirs(sbe_dir)
        io.open(os.path.join(sbe_dir, "team-profile.json"), "w").write(
            json.dumps({"designRoots": ["other-designs"]}))
        code, text, _ = self.team()
        self.assertIn("chg-a", text)
        self.assertIn("chg-alt", text, "a designRoots entry must be discovered too: %s"
                                       % text)

    def test_a_designRoots_entry_escaping_the_repo_is_refused_and_not_walked(self):
        # M3: containment. An entry resolving outside the repository root
        # (here via ..) is REFUSED by name and its dossier never surfaces.
        self._change("chg-a", "src/a.py")
        outside = os.path.join(self.tmp, "outside")
        doss = os.path.join(outside, "chg-outside")
        os.makedirs(doss)
        io.open(os.path.join(doss, "00-intake.json"), "w").write(
            json.dumps(INTAKE, indent=2))
        sbe_dir = os.path.join(self.repo, ".sbe")
        if not os.path.isdir(sbe_dir):
            os.makedirs(sbe_dir)
        io.open(os.path.join(sbe_dir, "team-profile.json"), "w").write(
            json.dumps({"designRoots": ["../outside"]}))
        code, text, err = self.team("--json")
        self.assertNotIn("chg-outside", text,
                         "an escaping designRoots entry must never be walked: %s" % text)
        data = json.loads(text[text.index("{"):])
        refusals = [f for f in data["findings"]
                   if f["basis"] == "unavailable" and "../outside" in f["evidence"]]
        self.assertTrue(refusals, "the escaping entry must be REFUSED by name, not "
                                 "silently dropped: %s" % text)
        self.assertIn("chg-a", data["changes"])
        self.assertNotIn("chg-outside", data["changes"])


class TestPlanOnlyDiscovery(TeamScenario):
    """MAJOR A6: `_team_changes` keyed on `00-intake.json` alone, so a
    dossier holding a validated `08-plan.json` but no intake (deleted, or
    never recorded) was invisible to every caller of that function:
    `sbe status --team`, `sbe status` (CR-06 dossier discovery), and
    `sbe map`, which is built entirely from the team report."""

    def test_a_dossier_with_a_validated_plan_and_no_intake_is_discovered(self):
        doss = self._change("chg-a", "src/a.py")
        os.remove(os.path.join(doss, "00-intake.json"))
        code, text, _ = self.team("--json")
        data = json.loads(text[text.index("{"):])
        self.assertIn("chg-a", data["changes"],
                      "a dossier with a validated plan but no intake must still be "
                      "discovered, not read as though it does not exist: %s" % text)
        labeled = [p for p in (data.get("planOnly") or []) if p["change"] == "chg-a"]
        self.assertEqual(len(labeled), 1, data)
        self.assertIn("no intake recorded yet", labeled[0]["detail"], labeled)

    def test_a_dossier_with_neither_intake_nor_a_validated_plan_stays_invisible(self):
        # RESTORE-shaped: a bare, empty directory under design/ must never
        # be discovered just because a directory exists there.
        os.makedirs(os.path.join(self.repo, "design", "empty-dir"))
        code, text, _ = self.team("--json")
        data = json.loads(text[text.index("{"):])
        self.assertNotIn("empty-dir", data["changes"], text)


class TestCompletedTasksLine(TeamScenario):
    """MAJOR A7: task completion was never stated plainly anywhere in the
    team view; a reader had to notice the absence of a severity-8 "ready"
    finding and infer completion from silence."""

    def test_closed_clean_tasks_are_stated_plainly_in_the_team_view(self):
        self._change("chg-a", "src/a.py")
        self._closed_record("T01", ["src/a.py"], agent="alice")
        code, text, _ = self.team()
        self.assertIn("completed task", text.lower(),
                      "the plain team view must state task completion plainly: %s" % text)
        self.assertIn("T01", text)

        code, text, _ = self.team("--json")
        data = json.loads(text[text.index("{"):])
        self.assertIn("T01", data.get("completedTasks") or [], text)

    def test_a_forced_close_is_never_counted_as_a_completed_task(self):
        self._change("chg-a", "src/a.py")
        self._closed_record("T01", ["src/a.py"], agent="alice", forced=True)
        code, text, _ = self.team("--json")
        data = json.loads(text[text.index("{"):])
        self.assertNotIn("T01", data.get("completedTasks") or [], text)


class TestConflictsAndForced(TeamScenario):
    def test_overlapping_open_tasks_across_changes_is_a_scope_conflict_naming_both(self):
        self._change("chg-a", "src/shared.py")
        self._change("chg-b", "src/b.py")
        self._open_record("T01", ["src/shared.py"], agent="alice")
        self._open_record("T90", ["src/shared.py"], agent="bob")
        code, text, _ = self.team()
        self.assertEqual(code, 1, text)
        self.assertIn("src/shared.py", text)
        self.assertIn("alice", text)
        self.assertIn("bob", text)

    def test_a_forced_closure_prints_forced_in_the_team_view(self):
        self._change("chg-a", "src/a.py")
        self._forced_record("T01")
        _code, text, _ = self.team()
        self.assertIn("FORCED", text)


class TestFullSeveritySet(TeamScenario):
    """F9: severities 2 (merge blockers), 8 (ready tasks), 9 (completed
    changes) and 10 (next action, always exactly one per change)."""

    def test_an_open_tasks_scope_violation_is_a_severity_two_merge_blocker(self):
        doss = self._change("chg-a", "src/a.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "chg-a dossier and source")
        head = self.git("rev-parse", "HEAD").strip()
        self._open_record("T01", ["src/a.py"], agent="alice", base_commit=head)
        io.open(os.path.join(self.repo, "src", "extra.py"), "w").write("y = 2\n")
        code, text, _ = self.team("--json")
        self.assertEqual(code, 1, text)
        data = json.loads(text[text.index("{"):])
        hits = [f for f in data["findings"]
               if f["change"] == "chg-a" and f["severity"] == 2
               and "src/extra.py" in f["detail"]]
        self.assertTrue(hits, "an out-of-scope change on an open task must surface as a "
                              "severity 2 merge blocker naming the path: %s" % text)
        self.assertEqual(hits[0]["verdict"], "FAIL", hits[0])
        self.assertEqual(hits[0]["basis"], "observed", hits[0])

    def test_a_failing_evidence_receipt_is_a_severity_two_merge_blocker_attributed(self):
        self._change("chg-a", "src/a.py")
        # A receipt generated over a dirty tree is only ever NO-DATA (advisory)
        # per evidence.verify(); commit first so the exit code can be trusted.
        self.git("add", "-A")
        self.git("commit", "-qm", "chg-a dossier and source")
        out = os.path.join(self.repo, ".sbe", "evidence", "fail.json")
        code, text, _ = self.sbe(
            "evidence", "run", "--out", out, "--cwd", self.repo, "--covers", "src/a.py",
            "--", sys.executable, "-c", "import sys; sys.exit(1)")
        self.assertEqual(code, 1, "the fixture command must fail to earn a failing "
                                  "receipt: %s" % text)
        code, text, _ = self.team("--json")
        self.assertEqual(code, 1, text)
        data = json.loads(text[text.index("{"):])
        hits = [f for f in data["findings"] if f["severity"] == 2
               and "fail.json" in f["evidence"]]
        self.assertTrue(hits, "a failing receipt must surface as a severity 2 merge "
                              "blocker: %s" % text)
        self.assertEqual(hits[0]["change"], "chg-a",
                         "a receipt covering src/a.py must attribute to the change whose "
                         "plan owns src/a.py, not the shared bucket: %s" % text)

    def test_a_fresh_plan_lists_its_tasks_as_ready_with_no_open_record(self):
        self._change("chg-a", "src/a.py")
        code, text, _ = self.team("--json")
        data = json.loads(text[text.index("{"):])
        ready = [f for f in data["findings"]
                if f["change"] == "chg-a" and f["severity"] == 8]
        self.assertTrue(ready, "a fresh plan's tasks with no registry record must list "
                              "as ready: %s" % text)
        ids = {f["evidence"] for f in ready}
        self.assertIn("task T01", ids, text)

    def test_every_plan_task_closed_clean_is_a_completed_change(self):
        self._change("chg-a", "src/a.py")
        self._closed_record("T01", ["src/a.py"], agent="alice")
        self._closed_record("T02", [], agent="alice")
        code, text, _ = self.team("--json")
        data = json.loads(text[text.index("{"):])
        completed = [f for f in data["findings"]
                    if f["change"] == "chg-a" and f["severity"] == 9]
        self.assertTrue(completed, "every plan task closed clean must surface as a "
                                  "completed change: %s" % text)
        self.assertEqual(completed[0]["verdict"], "PASS", completed[0])
        ready = [f for f in data["findings"]
                if f["change"] == "chg-a" and f["severity"] == 8]
        self.assertFalse(ready, "a task with a closed record is not a ready task: %s"
                                % text)

    def test_a_forced_close_never_counts_as_completed(self):
        self._change("chg-a", "src/a.py")
        self._closed_record("T01", ["src/a.py"], agent="alice", forced=True)
        self._closed_record("T02", [], agent="alice")
        code, text, _ = self.team("--json")
        data = json.loads(text[text.index("{"):])
        completed = [f for f in data["findings"]
                    if f["change"] == "chg-a" and f["severity"] == 9]
        self.assertFalse(completed, "a FORCED close never satisfies completion: %s" % text)

    # A2 ROUND 2: "highest severity" is no longer a raw comparison of
    # `severity` numbers -- `severity` 5 (missing approval), 6 (missing
    # convergence) and 12 (missing evidence), in their NO-DATA ("not
    # attempted yet") flavor, are never the next action while a task this
    # plan declares is still active or ready to start (no severity-9
    # "completed changes" finding recorded for the same change), the fix
    # `_task_and_approval_candidates` makes in `src/brothersbe/status.py`
    # (BLOCKER/MAJOR A2, round 2): round 1's own fix for the ORIGINAL A2
    # let missing approval outrank an unstarted ready task on both
    # surfaces, because raw severity comparison (and this test's own prior
    # formula) ranked 5 ahead of 7/8 regardless of task state. This mirrors
    # `lifecycle.TEAM_SEVERITY_TO_RUNG` (`src/brothersbe/lifecycle.py`) by
    # its OWN published numbers, read off the JSON contract rather than by
    # importing that module, the same black-box discipline every other
    # fixture in this file already holds to.
    _RUNG_ORDER = {1: 0, 2: 10, 3: 20, 4: 30, 5: 40, 6: 50, 7: 60, 8: 70, 9: 90, 11: 80,
                   12: 55}

    def test_every_change_carries_exactly_one_severity_ten_next_action(self):
        self._change("chg-a", "src/a.py")
        self._change("chg-b", "src/b.py")
        code, text, _ = self.team("--json")
        data = json.loads(text[text.index("{"):])
        for name in ("chg-a", "chg-b"):
            own = [f for f in data["findings"] if f["change"] == name]
            tens = [f for f in own if f["severity"] == 10]
            self.assertEqual(len(tens), 1,
                             "exactly one severity-10 next action per change: %s" % text)
            self.assertEqual(tens[0]["basis"], "derived", tens[0])
            lower = [f for f in own if f["severity"] < 10]
            all_done = any(f["severity"] == 9 for f in own)
            eligible = [f for f in lower
                       if not (f["severity"] in (5, 6, 12) and f["verdict"] == "NO-DATA"
                              and not all_done)]
            top = min(eligible, key=lambda f: self._RUNG_ORDER[f["severity"]])
            # `evidence`, not `nextAction`: a winning severity 5/7/8 fact is
            # now DERIVED a second time by `_task_and_approval_candidates`
            # rather than copied verbatim from the matching severity 5/7/8
            # finding above (its wording carries a task id / "(READY TASK)"
            # style suffix that finding's own text does not), but both
            # always name the identical underlying evidence path (the same
            # 08-plan.json, 10-approval.json or "task <id>" either way), so
            # this still proves severity 10 was derived from the SAME fact,
            # not a generic filler.
            self.assertEqual(tens[0]["evidence"], top["evidence"],
                             "the severity-10 finding must actually be derived from %s's "
                             "own highest-priority finding, not a generic filler: %s"
                             % (name, text))


class TestMissingEvidenceRung(TeamScenario):
    """MAJOR A4: the team severity table had no rung for missing evidence
    (the plan's verification commands were never run under `sbe evidence
    run`), so that finding was crammed into severity 5, "missing
    approvals", alongside the genuinely separate fact that no approval
    report is saved. A fresh plan owes both at once, and both used to print
    under the SAME "5. MISSING APPROVALS" header, indistinguishable by
    severity: the plain-status side already keeps its own MISSING EVIDENCE
    section apart from MERGE BLOCKERS; team must mirror that separation."""

    def test_missing_evidence_gets_its_own_rung_not_crammed_into_missing_approvals(self):
        self._change("chg-a", "src/a.py")
        code, text, _ = self.team("--json")
        data = json.loads(text[text.index("{"):])
        own = [f for f in data["findings"] if f["change"] == "chg-a"]
        evidence_findings = [f for f in own if "no receipt exists yet" in f["detail"]]
        approval_findings = [f for f in own
                             if "no approval report is saved" in f["detail"]]
        self.assertTrue(evidence_findings, "a fresh plan with verification commands and "
                                          "no receipt must still report missing evidence: "
                                          "%s" % text)
        self.assertTrue(approval_findings, text)
        self.assertNotEqual(
            evidence_findings[0]["severity"], approval_findings[0]["severity"],
            "a missing-evidence finding must not share a severity slot with a "
            "missing-approval finding: %s" % text)


class TestEvidenceAndConvergence(TeamScenario):
    def test_a_plan_with_no_convergence_report_is_no_data_at_severity_six_not_pass(self):
        self._change("chg-a", "src/a.py")
        code, text, _ = self.team()
        self.assertEqual(code, 1, "an unexamined convergence must block: %s" % text)
        self.assertIn("sbe converge", text)
        self.assertNotIn("convergence PASS", text)
        # F3: the audit found a mutation that folds this finding's severity
        # from 6 down to 2 without any test noticing; assert the exact number.
        code, text, _ = self.team("--json")
        data = json.loads(text[text.index("{"):])
        conv = [f for f in data["findings"]
               if f["change"] == "chg-a" and "09-convergence.json" in (f["evidence"] or "")]
        self.assertTrue(conv, "expected a convergence finding naming 09-convergence.json "
                              "for chg-a: %s" % text)
        self.assertEqual(conv[0]["severity"], 6,
                         "an unexamined convergence report must be severity 6, not folded "
                         "into another slot: %s" % text)

    def test_a_stale_approval_report_is_derived_not_observed(self):
        doss = self._change("chg-a", "src/a.py")
        io.open(os.path.join(doss, "10-approval.json"), "w").write(json.dumps({
            "tool": "sbe pr verify", "repository": "example/repo", "number": 1,
            "headSha": "0" * 40, "final": "PASS", "controls": []}))
        _code, _text, _ = self.team()
        code, text, _ = self.team("--json")
        data = json.loads(text[text.index("{"):])
        stale = [f for f in data["findings"]
                 if f["change"] == "chg-a" and "approval" in f["evidence"].lower()
                 and f["basis"] == "derived"]
        self.assertTrue(stale, "an approval bound to another sha must surface as a "
                               "derived staleness finding: %s" % text)


class TestJsonContractAndExit(TeamScenario):
    def test_every_finding_carries_the_full_contract(self):
        self._change("chg-a", "src/a.py")
        code, text, _ = self.team("--json")
        data = json.loads(text[text.index("{"):])
        self.assertTrue(data["findings"], "a change with a plan yields findings")
        for f in data["findings"]:
            for key in ("change", "severity", "verdict", "evidence", "commit",
                        "owner", "nextAction", "basis"):
                self.assertIn(key, f, "finding missing %s: %s" % (key, f))
            self.assertIn(f["basis"], ("observed", "derived", "unavailable"))
            # 1..12, not 1..10: severity 11 is the review-record slot, added
            # deliberately OUTSIDE 1..6 (see TEAM_SEVERITIES in status.py) so
            # a missing review, which is every one of this repository's nine
            # merged pull requests to date, reads as NO-DATA and never as a
            # block. Severity 12 (MAJOR A4, "missing evidence") is INSIDE
            # the blocking set (see TEAM_BLOCKING_SEVERITIES in status.py)
            # even though it sits outside the contiguous 1..6 span, the same
            # way MISSING EVIDENCE already blocks plain `sbe status`.
            self.assertTrue(1 <= int(f["severity"]) <= 12, f)
        sevs = [int(f["severity"]) for f in data["findings"]]
        self.assertEqual(sevs, sorted(sevs), "most severe first, deterministic")

    def test_a_tree_with_only_low_severity_findings_exits_zero(self):
        doss = os.path.join(self.repo, "design", "bare")
        os.makedirs(doss)
        io.open(os.path.join(doss, "00-intake.json"), "w").write(
            json.dumps(INTAKE, indent=2))
        code, text, _ = self.team()
        self.assertEqual(code, 0, text)

    @unittest.skipUnless(_chmod_can_deny_reads(),
                         "this host cannot make a file unreadable with chmod, so "
                         "an unreadable registry cannot be constructed and this "
                         "scenario is unavailable here")
    def test_an_unreadable_registry_is_an_unavailable_finding_and_a_nonzero_exit(self):
        # This test PASSED VACUOUSLY as uid 0 until 2026-08-25. chmod 000 does
        # not deny root, so the registry stayed readable, and both assertions
        # below were satisfied by something else in the fixture: deleting the
        # chmod line entirely left the test green. It runs in the battery at
        # step 19, so it went green on every run while measuring nothing about
        # an unreadable registry.
        #
        # Two changes, and the second matters more than the skip. The guard
        # above makes an unbuildable premise a NO-DATA skip that names itself
        # rather than a false pass. The assertion below makes the premise
        # CHECKED rather than assumed, so even on a host where chmod does deny
        # reads, this test can never again certify an outcome it did not
        # actually set up.
        self._change("chg-a", "src/a.py")
        self._open_record("T01", ["src/a.py"])
        reg = os.path.join(self.repo, ".sbe", "tasks.json")
        os.chmod(reg, 0)
        with self.assertRaises((OSError, PermissionError),
                               msg="the registry is still readable after chmod 000, "
                                   "so the premise of this test was not built and "
                                   "anything it asserted below would be vacuous"):
            io.open(reg, encoding="utf-8").read()
        try:
            code, text, _ = self.team("--json")
            self.assertEqual(code, 1, text)
            data = json.loads(text[text.index("{"):])
            unavailable = [f for f in data["findings"] if f["basis"] == "unavailable"]
            self.assertTrue(unavailable,
                            "an unreadable registry must surface, not vanish: %s" % text)
        finally:
            os.chmod(reg, stat.S_IRUSR | stat.S_IWUSR)

    def test_team_makes_no_network_calls_by_construction(self):
        body = io.open(os.path.join(HERE, "..", "src", "brothersbe",
                                    "status.py"), encoding="utf-8").read()
        for needle in ("urllib", "http.client", "socket", "api.github.com"):
            self.assertNotIn(needle, body,
                             "status must read the estate, never the network")


class TestHandoverIntegration(TeamScenario):
    """LT-302.B: the smallest possible read path so `sbe status --team` can
    report whether ownership of each change is moving, and to whom, over a
    real `12-handover.json` the real `sbe handover` engine wrote. The
    result is a purely additive top-level `handover` list, never folded
    into the severity 1..11 `findings` list: every test here also reasserts
    the 1..11 contract `TestJsonContractAndExit` already pins, so this
    class cannot pass by silently widening that range.
    """

    def _handover_for(self, data, name):
        self.assertIn("handover", data, data)
        hits = [h for h in data["handover"] if h["change"] == name]
        self.assertEqual(len(hits), 1, "exactly one handover entry per change: %s"
                                       % data["handover"])
        return hits[0]

    def _assert_severity_contract_holds(self, data):
        for f in data["findings"]:
            self.assertTrue(1 <= int(f["severity"]) <= 12,
                            "a handover entry must never widen the findings severity "
                            "range: %s" % f)

    def test_no_handover_needed_is_reported_per_change_and_never_blocks(self):
        self._change("chg-a", "src/a.py")
        code, text, _ = self.team("--json")
        data = json.loads(text[text.index("{"):])
        entry = self._handover_for(data, "chg-a")
        self.assertEqual(entry["status"], "none", entry)
        self.assertIsNone(entry["stale"], entry)
        self.assertIn("no handover", entry["detail"], entry)
        self.assertIn("never a block", entry["detail"], entry)
        self._assert_severity_contract_holds(data)

    def test_a_prepared_handover_reads_prepared_and_awaiting_receiver(self):
        doss = self._change("chg-a", "src/a.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "chg-a dossier and source")
        code, text, _ = self.handover("prepare", doss, "--outgoing", "alice@example.com",
                                      "--receiver", "bob@example.com")
        self.assertEqual(code, 0, text)
        code, text, _ = self.team("--json")
        data = json.loads(text[text.index("{"):])
        entry = self._handover_for(data, "chg-a")
        self.assertEqual(entry["status"], "prepared", entry)
        self.assertFalse(entry["stale"], entry)
        self.assertIn("awaiting receiver", entry["detail"], entry)
        self.assertEqual(entry["outgoingOwner"], "alice@example.com", entry)
        self.assertEqual(entry["intendedReceiver"], "bob@example.com", entry)
        self._assert_severity_contract_holds(data)

    def test_a_stale_prepared_handover_is_named_without_widening_the_severity_range(self):
        doss = self._change("chg-a", "src/a.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "chg-a dossier and source")
        code, text, _ = self.handover("prepare", doss, "--outgoing", "alice@example.com",
                                      "--receiver", "bob@example.com")
        self.assertEqual(code, 0, text)
        io.open(os.path.join(self.repo, "unrelated.txt"), "w").write("more\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "advance head past the prepared handover")
        code, text, _ = self.team("--json")
        data = json.loads(text[text.index("{"):])
        entry = self._handover_for(data, "chg-a")
        self.assertEqual(entry["status"], "prepared", entry)
        self.assertTrue(entry["stale"], entry)
        self.assertIn("stale", entry["detail"], entry)
        self._assert_severity_contract_holds(data)

    def test_an_acknowledged_handover_reads_acknowledged(self):
        doss = self._change("chg-a", "src/a.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "chg-a dossier and source")
        self.handover("prepare", doss, "--outgoing", "alice@example.com",
                      "--receiver", "bob@example.com")
        code, text, _ = self.handover("acknowledge", doss, "--receiver", "bob@example.com")
        self.assertEqual(code, 0, text)
        code, text, _ = self.team("--json")
        data = json.loads(text[text.index("{"):])
        entry = self._handover_for(data, "chg-a")
        self.assertEqual(entry["status"], "acknowledged", entry)
        self.assertIn("acknowledged", entry["detail"], entry)
        self._assert_severity_contract_holds(data)

    def test_a_rejected_handover_reads_rejected(self):
        doss = self._change("chg-a", "src/a.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "chg-a dossier and source")
        self.handover("prepare", doss, "--outgoing", "alice@example.com",
                      "--receiver", "bob@example.com")
        code, text, _ = self.handover("reject", doss, "--receiver", "bob@example.com",
                                      "--reason", "not ready yet")
        self.assertEqual(code, 0, text)
        code, text, _ = self.team("--json")
        data = json.loads(text[text.index("{"):])
        entry = self._handover_for(data, "chg-a")
        self.assertEqual(entry["status"], "rejected", entry)
        self.assertIn("not ready yet", entry["detail"], entry)
        self._assert_severity_contract_holds(data)

    def test_an_acknowledged_handover_states_ownership_in_the_plain_text(self):
        # BLOCKER A3: the JSON handover array already carries status
        # "acknowledged", outgoingOwner and intendedReceiver, but the
        # plain-text team view (render_team) never read the handover list
        # at all, so a human running `sbe status --team` with no --json
        # never saw who now owns the change.
        doss = self._change("chg-a", "src/a.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "chg-a dossier and source")
        self.handover("prepare", doss, "--outgoing", "alice@example.com",
                      "--receiver", "bob@example.com")
        code, text, _ = self.handover("acknowledge", doss, "--receiver", "bob@example.com")
        self.assertEqual(code, 0, text)
        code, text, _ = self.team()
        self.assertIn("ownership", text.lower(),
                      "the plain team view must state who owns the change after an "
                      "acknowledged handover: %s" % text)
        self.assertIn("bob@example.com", text,
                      "the plain text must name who ownership moved to: %s" % text)
        self.assertIn("acknowledged", text)

    def test_two_changes_each_carry_their_own_independent_handover_entry(self):
        doss_a = self._change("chg-a", "src/a.py")
        self._change("chg-b", "src/b.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "both dossiers")
        self.handover("prepare", doss_a, "--outgoing", "alice@example.com",
                      "--receiver", "bob@example.com")
        code, text, _ = self.team("--json")
        data = json.loads(text[text.index("{"):])
        a_entry = self._handover_for(data, "chg-a")
        b_entry = self._handover_for(data, "chg-b")
        self.assertEqual(a_entry["status"], "prepared", a_entry)
        self.assertEqual(b_entry["status"], "none",
                         "chg-b must read its own absence, not chg-a's prepared record: %s"
                         % b_entry)


class TestPerChangeEvidenceScoping(TeamScenario):
    """LANE B-004, reproduced against the plain (non---team) `sbe status`
    (`build_report`), whose CR-06 dossier path used to consult
    `_scan_evidence`'s single GLOBAL `kindsCovered` for every discovered
    dossier: a gate receipt scoped to one dossier's own owned file cleared a
    SIBLING dossier's obligation too, purely because both dossiers
    consulted the same set in the same run. `test_sbe_status.py`'s own
    `TestDossierDiscovery` already pins the flat single-dossier layout's
    byte-identical output; kept out of this class on purpose.
    """

    def status(self, *extra):
        return self.sbe("status", self.repo, "--json", *extra)

    def _t2_dossier(self, name, src_rel):
        doss = self._change(name, src_rel)
        answers = dict(INTAKE["answers"])
        answers["changes_contract"] = "y"  # T2: owes design, gate and score
        io.open(os.path.join(doss, "00-intake.json"), "w").write(
            json.dumps({"answers": answers}, indent=2))
        return doss

    def _missing_gate(self, data, name):
        return [m for m in data["missingEvidence"]
               if m["finding"].startswith("dossier %s: " % name)
               and "hard gate" in m["finding"]]

    def _two_committed_t2_dossiers(self):
        self._t2_dossier("chg-a", "src/a.py")
        self._t2_dossier("chg-b", "src/b.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "both T2 dossiers and their source")

    def _gate_receipt(self, out_rel, covers):
        out = os.path.join(self.repo, out_rel)
        code, text, _ = self.sbe(
            "evidence", "run", "--out", out, "--cwd", self.repo, "--covers", covers,
            "--kind", "gate", "--", sys.executable, "-c", "import sys; sys.exit(0)")
        self.assertEqual(code, 0, "the fixture receipt must verify sound: %s" % text)
        return out

    def test_a_receipt_scoped_to_one_dossier_no_longer_clears_a_siblings_gate_obligation(self):
        # THE BUG, reproduced: chg-a's receipt used to clear chg-b's
        # obligation too, purely because both were discovered together.
        self._two_committed_t2_dossiers()
        self._gate_receipt(".sbe/evidence/gate-a.json", "src/a.py")

        code, text, _ = self.status()
        data = json.loads(text[text.index("{"):])
        a_gate, b_gate = self._missing_gate(data, "chg-a"), self._missing_gate(data, "chg-b")

        self.assertFalse(a_gate, "the receipt covers src/a.py, chg-a's own plan ownership: "
                                 "it must clear chg-a's gate obligation: %s" % text)
        self.assertTrue(b_gate, "chg-a's receipt must never clear chg-b's gate obligation "
                                "just because both were discovered together: %s" % text)
        self.assertIn("chg-a", b_gate[0]["finding"],
                     "the finding must name where the matching receipt landed, not stay "
                     "silent about a receipt that plainly exists: %s" % text)
        self.assertNotEqual(code, 0, text)

    def test_a_same_numbered_task_id_in_a_sibling_plan_does_not_borrow_the_claim(self):
        # `sbe plan --write` always numbers a dossier's first task "T01", so
        # BOTH dossiers derive a task of their own also called "T01". An id
        # match alone must not let a registry record claim a sibling's
        # obligation; its OWN ownedPaths must overlap that dossier's plan.
        self._two_committed_t2_dossiers()
        out = self._gate_receipt(".sbe/evidence/gate-claimed.json", "seed.txt")
        run_id = json.loads(io.open(out, encoding="utf-8").read())["runId"]
        reg = os.path.join(self.repo, ".sbe", "tasks.json")
        io.open(reg, "w").write(json.dumps({"schemaVersion": "1.0", "tasks": [{
            "id": "T01", "agent": "alice", "role": "writer", "worktree": None,
            "ownedPaths": ["src/a.py"], "readOnlyPaths": [], "baseCommit": None,
            "expiry": None, "status": "closed", "verifyCommand": "python3 -c pass",
            "evidenceId": run_id, "openedAt": "2026-07-30T00:00:00Z",
            "closedAt": "2026-07-30T01:00:00Z"}]}))

        code, text, _ = self.status()
        data = json.loads(text[text.index("{"):])
        a_gate, b_gate = self._missing_gate(data, "chg-a"), self._missing_gate(data, "chg-b")

        self.assertFalse(a_gate, "chg-a's own T01 record claims this receipt, and its "
                                 "ownedPaths (src/a.py) is chg-a's own plan: %s" % text)
        self.assertTrue(b_gate, "chg-b also derives its own T01 task, but the claiming "
                                "record's ownedPaths name chg-a's file, not chg-b's: an id "
                                "match alone must never borrow the claim: %s" % text)

    def test_a_receipt_attributable_to_no_dossier_stays_unscoped_and_clears_nothing(self):
        self._two_committed_t2_dossiers()
        self._gate_receipt(".sbe/evidence/gate-unscoped.json", "seed.txt")

        code, text, _ = self.status()
        data = json.loads(text[text.index("{"):])
        for name in ("chg-a", "chg-b"):
            hits = self._missing_gate(data, name)
            self.assertTrue(hits, "seed.txt is owned by neither plan: must clear neither "
                                  "dossier's gate obligation: %s" % text)
            self.assertIn("unscoped", hits[0]["finding"],
                         "an unscoped receipt must be named as such, never silently dropped "
                         "or silently allowed to clear an obligation: %s" % text)


class TestCanonicalNextAction(TeamScenario):
    """LANE C1 (B-003): one canonical next action.

    Reproduced before `lifecycle.py` existed: a dossier whose ONLY
    outstanding obligation is review (evidence complete for its declared
    tier, both plan tasks closed clean, convergence and approval both PASS
    and bound to the current head, no `11-review.json` written yet) got
    THREE different answers. Plain `sbe status` never looks at task or
    review state at all, so it read the dossier's one clean evidence
    receipt and said "no action; this receipt is sound evidence" --
    clean-reading, not review-pending. `sbe status --team`'s own severity-10
    finding picked the MINIMUM raw team-severity number among this change's
    other findings, and severity 9 ("completed changes": every plan task
    closed clean) sorts below severity 11 ("review record") as a bare
    integer, so it said "nothing left to do for this change; open a PR" --
    also wrong, because review had not run. Only `/brothersbe:next`'s own
    prose ladder, which checks team's severity 11 directly rather than
    severity 10, would have said "run review".

    Both machine surfaces must now agree: `build_report`'s `nextActionDetail`
    and `build_team_report`'s severity-10 finding are both derived through
    `lifecycle.reduce_next_action`, so they can no longer read the same
    recorded state two different ways.
    """

    def status(self, *extra):
        code, text, _err = self.sbe("status", self.repo, "--json", *extra)
        return code, json.loads(text[text.index("{"):]), text

    def _review_pending_dossier(self):
        # A real plan, over a real dossier, exactly like every other fixture
        # in this file (`_change`), committed so the evidence receipt below
        # verifies clean rather than NO-DATA over a dirty tree.
        name = "review-pending"
        doss = self._change(name, "src/reviewme.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "%s dossier and source" % name)
        head = self.git("rev-parse", "HEAD").strip()

        # Evidence complete for every kind the declared tier owes, over the
        # clean tree above, so MISSING EVIDENCE never fires on either
        # surface and the evidence-store scan sees sound, not broken,
        # evidence.
        out = os.path.join(self.repo, ".sbe", "evidence", "all.json")
        code, text, _ = self.sbe(
            "evidence", "run", "--out", out, "--cwd", self.repo,
            "--covers", "src/reviewme.py", "--kind", "design", "--kind", "gate",
            "--kind", "score", "--", sys.executable, "-c", "pass")
        self.assertEqual(code, 0, "the fixture receipt must verify sound: %s" % text)

        # `sbe plan --write` derives two tasks for this dossier (T01 owning
        # the source file, T02 the verification-only task); both closed
        # clean, so neither an active nor a ready task remains.
        self._closed_record("T01", ["src/reviewme.py"], agent="alice", base_commit=head)
        self._closed_record("T02", [], agent="alice", base_commit=head)

        # Convergence and approval both PASS, bound to the current head, so
        # neither outranks review on team's own ladder -- written directly,
        # the same technique `_open_record`/`_closed_record` already use for
        # a state only a real run would otherwise produce.
        io.open(os.path.join(doss, "09-convergence.json"), "w").write(
            json.dumps({"final": "PASS", "head": head}))
        io.open(os.path.join(doss, "10-approval.json"), "w").write(
            json.dumps({"headSha": head, "final": "PASS"}))

        # No 11-review.json: review is the one thing left undone.
        return name

    def test_review_pending_is_the_same_action_id_on_both_surfaces(self):
        name = self._review_pending_dossier()

        code, data, text = self.status()
        self.assertIn("nextActionDetail", data, text)
        self.assertNotIn("nothing blocking here", data["nextAction"],
                         "a dossier whose only obligation is review must never read as "
                         "clean: %s" % text)
        self.assertEqual(data["nextActionDetail"]["actionId"], "run-review", text)

        code, team_text, _ = self.team("--json")
        team_data = json.loads(team_text[team_text.index("{"):])
        tens = [f for f in team_data["findings"]
               if f["change"] == name and f["severity"] == 10]
        self.assertEqual(len(tens), 1, team_text)
        self.assertEqual(tens[0]["actionId"], "run-review",
                         "team's own severity-10 must not let severity 9 (\"completed "
                         "changes\") outrank severity 11 (\"review record\") by raw integer "
                         "comparison: %s" % team_text)

        self.assertEqual(data["nextActionDetail"]["actionId"], tens[0]["actionId"],
                         "plain status and team status must name the SAME next action for "
                         "the same dossier: status=%r team=%r"
                         % (data["nextActionDetail"]["actionId"], tens[0]["actionId"]))

    def _missing_approval_dossier(self):
        # BLOCKER A2: a dossier whose ONLY outstanding obligation is
        # approval (evidence complete for its declared tier, both plan
        # tasks closed clean, convergence PASS and bound to the current
        # head, no 10-approval.json ever saved). `build_report`'s own
        # candidate list is documented as covering sections 1 to 4
        # (broken claims, merge blockers, active conflicts, missing
        # evidence) plus the LANE C1 task/review ladder; it never asked
        # `_change_ladder_candidates` about approval at all, so plain
        # `sbe status` fell through to `finish` ("nothing blocking here")
        # while `sbe status --team` (and `sbe map`, which is built
        # entirely from the team report) named the SAME commit's own
        # severity-5 finding and recommended `resolve-missing-approval`.
        name = "approval-pending"
        doss = self._change(name, "src/approveme.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "%s dossier and source" % name)
        head = self.git("rev-parse", "HEAD").strip()

        out = os.path.join(self.repo, ".sbe", "evidence", "all.json")
        code, text, _ = self.sbe(
            "evidence", "run", "--out", out, "--cwd", self.repo,
            "--covers", "src/approveme.py", "--kind", "design", "--kind", "gate",
            "--kind", "score", "--", sys.executable, "-c", "pass")
        self.assertEqual(code, 0, "the fixture receipt must verify sound: %s" % text)

        self._closed_record("T01", ["src/approveme.py"], agent="alice", base_commit=head)
        self._closed_record("T02", [], agent="alice", base_commit=head)

        io.open(os.path.join(doss, "09-convergence.json"), "w").write(
            json.dumps({"final": "PASS", "head": head}))
        io.open(os.path.join(doss, "11-review.json"), "w").write(json.dumps({
            "headSha": head, "reviewer": "carol@example.com", "reviewerType": "human",
            "result": "approved",
        }))
        # No 10-approval.json: approval is the one thing left undone.
        return name

    def test_missing_approval_is_the_same_action_id_on_both_surfaces(self):
        name = self._missing_approval_dossier()

        code, data, text = self.status()
        self.assertIn("nextActionDetail", data, text)
        self.assertNotIn("nothing blocking here", data["nextAction"],
                         "a dossier whose only obligation is approval must never read as "
                         "clean: %s" % text)
        self.assertEqual(data["nextActionDetail"]["actionId"], "resolve-missing-approval",
                         text)

        code, team_text, _ = self.team("--json")
        team_data = json.loads(team_text[team_text.index("{"):])
        tens = [f for f in team_data["findings"]
               if f["change"] == name and f["severity"] == 10]
        self.assertEqual(len(tens), 1, team_text)
        self.assertEqual(tens[0]["actionId"], "resolve-missing-approval", team_text)

        self.assertEqual(data["nextActionDetail"]["actionId"], tens[0]["actionId"],
                         "plain status and team status must name the SAME next action for "
                         "the same commit: status=%r team=%r"
                         % (data["nextActionDetail"]["actionId"], tens[0]["actionId"]))


class TestThreeSurfaceConsistency(TeamScenario):
    """A2 ROUND 2: `sbe status`, `sbe status --team` and `sbe map` must name
    the SAME next action for the same change, across every state the round-1
    hostile verdict named. Round 1's own fix for BLOCKER A2 added an
    UNCONDITIONAL missing-approval candidate to `_change_ladder_candidates`,
    gated only on `08-plan.json` existing; since `RUNG_MISSING_APPROVAL` (40)
    outranks `RUNG_ACTIVE_TASK` (60) and `RUNG_READY_TASK` (70), a fresh
    plan whose tasks had not been started yet recommended chasing a pull-
    request approval on BOTH surfaces before anyone had started the work --
    the ORIGINAL journey-2 disease (see `TestCanonicalNextAction`'s own
    docstring), unified across both surfaces instead of cured.

    `_task_and_approval_candidates` (`src/brothersbe/status.py`) is now the
    ONE shared candidate builder both `build_report` and `build_team_report`
    call, so the two surfaces cannot diverge by construction; `sbe map` is
    built entirely from `build_team_report`'s own return value (see
    `src/brothersbe/mapgen.py`'s module docstring), so a match against team
    is a match against map too, verified here directly rather than assumed.
    """

    def _map_page(self):
        out_path = os.path.join(self.tmp, "map.html")
        code, text, _ = self.sbe("map", self.repo, "--out", out_path)
        self.assertEqual(code, 0, text)
        return io.open(out_path, encoding="utf-8").read()

    def _assert_three_surfaces_agree(self, name, why):
        code, text, _err = self.sbe("status", self.repo, "--json")
        data = json.loads(text[text.index("{"):])
        self.assertIn("nextActionDetail", data, text)

        code, team_text, _ = self.team("--json")
        team_data = json.loads(team_text[team_text.index("{"):])
        tens = [f for f in team_data["findings"]
               if f["change"] == name and f["severity"] == 10]
        self.assertEqual(len(tens), 1, team_text)

        plain_action_id = data["nextActionDetail"]["actionId"]
        team_action_id = tens[0]["actionId"]
        self.assertEqual(
            plain_action_id, team_action_id,
            "%s: plain status and team status must name the SAME next action "
            "for %r: status=%r team=%r" % (why, name, plain_action_id, team_action_id))

        page = self._map_page()
        # `sbe map` relativizes every string against the repository root
        # (see `mapgen._relativize`: `value.replace(prefix, "").replace(
        # root, ".")`, `prefix` being `root + os.sep`) before rendering it,
        # the SAME two-step transform applied here to team's own recorded
        # text, so the comparison is against what the page actually
        # carries, not against an absolute path the page deliberately
        # never bakes in.
        relativized_next_action = (
            tens[0]["nextAction"].replace(self.repo + os.sep, "").replace(self.repo, "."))
        self.assertIn(
            relativized_next_action, page,
            "%s: sbe map's own findings table must carry the IDENTICAL next "
            "action text team status just named for %r, since sbe map is "
            "built entirely from build_team_report's own return value: %r"
            % (why, name, relativized_next_action))
        return plain_action_id

    def test_a_fresh_intake_with_no_plan_yet_agrees_on_all_three_surfaces(self):
        name = "fresh-intake"
        doss = os.path.join(self.repo, "design", name)
        os.makedirs(doss)
        io.open(os.path.join(doss, "00-intake.json"), "w").write(
            json.dumps(INTAKE, indent=2))
        self.git("add", "-A")
        self.git("commit", "-qm", "%s intake only, no plan yet" % name)

        action_id = self._assert_three_surfaces_agree(name, "state 1: fresh intake only")
        self.assertEqual(action_id, "start-ready-task",
                         "a change with an intake and no plan yet must recommend "
                         "deriving one, the same next step team already named for it")

    def test_two_ready_tasks_unclaimed_with_no_evidence_yet_agrees_on_all_three_surfaces(
            self):
        # BLOCKER/MAJOR A2 round 2's own reproduction: a fresh plan, both
        # tasks ready and unclaimed, no registry record, no convergence, no
        # approval, no review, and (since a verification command is
        # declared and never run) no evidence either.
        name = "two-ready-tasks"
        self._change(name, "src/tworeadytasks.py")

        action_id = self._assert_three_surfaces_agree(
            name, "state 2: two ready tasks unclaimed")
        self.assertEqual(action_id, "start-ready-task",
                         "a fresh plan with two unstarted, unclaimed tasks and no "
                         "evidence, convergence, approval or review recorded yet must "
                         "recommend starting the ready task, not chasing an approval, a "
                         "convergence report or an evidence receipt nobody could have "
                         "produced yet")

    def test_a_task_closed_clean_with_evidence_agrees_on_all_three_surfaces(self):
        # Both tasks closed clean, a sound evidence receipt recorded, but
        # convergence, approval and review all left untouched (missing):
        # approval must still win, since it outranks both on `lifecycle`'s
        # own rung table once nothing is left for a task to do.
        name = "closed-clean-with-evidence"
        doss = self._change(name, "src/closedcleanwithevidence.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "%s dossier and source" % name)
        head = self.git("rev-parse", "HEAD").strip()

        out = os.path.join(self.repo, ".sbe", "evidence", "all.json")
        code, text, _ = self.sbe(
            "evidence", "run", "--out", out, "--cwd", self.repo,
            "--covers", "src/closedcleanwithevidence.py", "--kind", "design",
            "--kind", "gate", "--kind", "score", "--", sys.executable, "-c", "pass")
        self.assertEqual(code, 0, "the fixture receipt must verify sound: %s" % text)

        self._closed_record("T01", ["src/closedcleanwithevidence.py"], agent="alice",
                            base_commit=head)
        self._closed_record("T02", [], agent="alice", base_commit=head)

        action_id = self._assert_three_surfaces_agree(
            name, "state 3: task closed clean with evidence")
        self.assertEqual(action_id, "resolve-missing-approval",
                         "every task closed clean and evidence recorded: the only "
                         "thing left to do is seek approval, even with convergence and "
                         "review also unrecorded")

    def test_evidence_complete_but_approval_genuinely_missing_agrees_on_all_three_surfaces(
            self):
        # Evidence complete for the declared tier, both tasks closed clean,
        # convergence PASS and review approved, all bound to head: approval
        # is the ONE thing left undone, isolated from every other rung.
        name = "approval-genuinely-missing"
        doss = self._change(name, "src/approvalgenuinelymissing.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "%s dossier and source" % name)
        head = self.git("rev-parse", "HEAD").strip()

        out = os.path.join(self.repo, ".sbe", "evidence", "all.json")
        code, text, _ = self.sbe(
            "evidence", "run", "--out", out, "--cwd", self.repo,
            "--covers", "src/approvalgenuinelymissing.py", "--kind", "design",
            "--kind", "gate", "--kind", "score", "--", sys.executable, "-c", "pass")
        self.assertEqual(code, 0, "the fixture receipt must verify sound: %s" % text)

        self._closed_record("T01", ["src/approvalgenuinelymissing.py"], agent="alice",
                            base_commit=head)
        self._closed_record("T02", [], agent="alice", base_commit=head)

        io.open(os.path.join(doss, "09-convergence.json"), "w").write(
            json.dumps({"final": "PASS", "head": head}))
        io.open(os.path.join(doss, "11-review.json"), "w").write(json.dumps({
            "headSha": head, "reviewer": "carol@example.com", "reviewerType": "human",
            "result": "approved",
        }))
        # No 10-approval.json: approval is the one thing left undone.

        action_id = self._assert_three_surfaces_agree(
            name, "state 4: evidence complete, approval genuinely missing")
        self.assertEqual(action_id, "resolve-missing-approval",
                         "convergence PASS, review approved, evidence complete, and "
                         "still no approval saved: approval is the one thing left, and "
                         "must be named as such on every surface")


if __name__ == "__main__":
    unittest.main(verbosity=2)
