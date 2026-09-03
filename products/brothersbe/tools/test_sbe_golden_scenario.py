#!/usr/bin/env python3
"""LT-501: one end-to-end team scenario. Run: python3 tools/test_sbe_golden_scenario.py

Spec of record: `~/Downloads/BrotherSBE_Lean_Team_Execution_Plan_for_Fable_
2026-08-04.md` section 15, "LT-501: One end-to-end team scenario" (the pass
criteria list and LT-501.B). The scenario repository itself is built by
`tools/fixtures/golden-scenario/build_scenario.py`, a deterministic builder
rather than frozen bytes: every run lays out the same dossier and plan fresh,
in a `tempfile.mkdtemp()` this suite owns, and nothing here is a snapshot of
an earlier run.

The chain this suite drives, end to end, through the REAL engine (`bin/sbe`
as a subprocess, real git, real worktrees, nothing mocked, mirroring the
discipline `tools/test_sbe_work.py` and `tools/test_sbe_team_workflow.py`
already hold to):

    start -> next -> work -> evidence -> review -> fix -> converge ->
    handover -> acknowledge

"start" and "next" are BrotherSBE skills (`skills/start`, `skills/next`),
not CLI subcommands; this suite proves what those skills would read, not
what a Claude session would say, per the pass criterion's own instruction
("assert on the JSON next-action ladder, not on prose"): `_ladder_rung`
below is a narrow, explicitly-scoped mirror of a subset of `skills/next/
SKILL.md`'s priority ladder (rungs 2, 4, 6 and 7 only -- kickoff, the
internal work/evidence loop, review, and done), driven by the same `sbe
status --team --json` this project's own status machinery already produces.
It deliberately does not reimplement rungs 1, 3 and 5 (the doctor, design
and tier-evidence checks), which the golden scenario's own minimal, complete
dossier is built to never need. This narrower scope is stated here and again
in the class docstring below, not left implicit.

Scenario elements (mapped onto the plan `tools/fixtures/golden-scenario/
build_scenario.py` writes):

  - a backend service change (T01, writer, owns `src/widgets/service.py`);
  - a small SQL change (T02, writer, owns `migrations/0002_widget_index.sql`);
  - two ready disjoint tasks (T01 and T02: no dependency, disjoint owns);
  - one dependent task (T03, reviewer, `dependsOn: ["T01"]`, owns nothing);
  - one planted security-sensitive configuration edit (T04, writer, owns
    `.claude/team-config.json`, an authority surface `tools/sbe_instruction_
    surface.py` detects);
  - one evidence requirement (every task's `verificationCommands` are
    matched to a real receipt from `sbe evidence run` before `sbe work
    finish` will close it -- proven for all four, not just one);
  - one review finding (T02's migration is flagged twice, by two different
    reviewer sources review-route itself selected, deduplicated to one);
  - one ownership transfer (`sbe handover prepare` / `acknowledge`, from an
    outgoing owner to a named human receiver).

Max three writers by construction, not by policing after the fact: T01, T02
and T04 are the plan's only `role: writer` tasks; T03 is a reviewer. This
suite still re-reads the registry and re-derives the writer count and the
owns-overlap mechanically (`TestFullChain.test_...`, the registry section)
rather than trusting the plan's own shape, because the pass criterion says
"the task registry proves it," not "the plan promises it."

Two engine gaps this scenario originally SURFACED (rc.10, documented in
`docs/KNOWN-LIMITS.md`) were FIXED in rc.11, and the pins here were built to
flip with the fixes, which they did: `sbe converge`'s VERIFICATION dimension
(`src/brothersbe/converge.py`) now extracts `path` from each `coveredFiles`
entry (`sbe evidence run` writes `{path, sha256, note}` objects) before the
membership test, so a writer task's OWN receipt is credited with covering
its own owned path (T02's pin asserts the PASS finding and the absence of
the old does-not-cover text); and the evidence scan in
`src/brothersbe/status.py` resolves a receipt claimed by a task record's
`evidenceId` against that record's declared worktree, so a finished task's
own receipt no longer misreads as a severity-1 broken claim against root
HEAD (the status pin asserts zero severity-1 findings while the legitimate
NO-DATA blockers stay named exactly, AND that the plain status JSON names
the worktree resolution, so a scan that silently dropped the receipt still
fails). What legitimately remains FAIL is the inherent
property of a never-merged multi-branch team workflow: receipts for the
OTHER tasks bind to their own branch heads, never to the head `sbe
converge` assesses.
"""
import ast
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SBE = os.path.join(ROOT, "bin", "sbe")
FIXTURE_DIR = os.path.join(HERE, "fixtures", "golden-scenario")
if FIXTURE_DIR not in sys.path:
    sys.path.insert(0, FIXTURE_DIR)
import build_scenario as bs  # noqa: E402  (path setup has to come first)

OUTGOING = "alice@example.com"
RECEIVER = "bob@example.com"


# ---------------------------------------------------------------------------
# Shared fixture: one fresh scenario repository per test, mirroring
# tools/test_sbe_work.py and tools/test_sbe_team_workflow.py.
# ---------------------------------------------------------------------------

class GoldenScenarioFixture(unittest.TestCase):
    """A fresh repository, a fresh dossier and a validated `08-plan.json`,
    built once per test through `build_scenario.build`, with no task started
    yet: every test decides for itself which of T01-T04 it drives, and how
    far."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-golden-")
        self.repo = os.path.join(self.tmp, "repo")
        self.worktree_dir = os.path.join(self.tmp, "worktrees")
        os.makedirs(self.worktree_dir)
        built = bs.build(self.repo, SBE)
        self.assertEqual(built["validate_code"], 0,
                         "fixture setup wrote a plan `sbe plan` itself refuses; this is a "
                         "fixture bug, not an LT-501 finding: %s" % built["validate_text"])
        self.base = built["base"]
        self.dossier = built["dossier"]
        self.plan_path = built["plan_path"]
        self.change_id = built["change_id"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- process helpers. Three values, not two: see build_scenario.run_sbe's
    # own docstring for why every runner helper in this project returns
    # three values rather than a (verdict, evidence)-shaped pair.

    def sbe(self, *argv):
        return bs.run_sbe(SBE, list(argv), cwd=self.repo)

    def work(self, *argv):
        return self.sbe("work", *argv, "--cwd", self.repo)

    def status_team(self):
        return self.sbe("status", self.repo, "--team", "--json")

    # -- task lifecycle, driving the real engine one step at a time --------

    def worktree_path_for(self, task_id):
        return os.path.join(self.worktree_dir,
                            "%s-sbe-%s" % (os.path.basename(self.repo), task_id))

    def branch_exists(self, name):
        out = subprocess.run(["git", "rev-parse", "--verify", "--quiet",
                              "refs/heads/" + name],
                             cwd=self.repo, capture_output=True, text=True)
        return out.returncode == 0

    def start_task(self, task_id, agent):
        code, text, _err = self.work("start", task_id, "--plan", self.plan_path,
                                     "--worktree-dir", self.worktree_dir, "--agent", agent)
        self.assertEqual(code, 0, "sbe work start %s failed: %s" % (task_id, text))
        return self.worktree_path_for(task_id)

    def run_evidence(self, task_id, argv, covers=None, out_name=None, kind="gate"):
        worktree = self.worktree_path_for(task_id)
        receipt = os.path.join(self.repo, ".sbe", "evidence",
                               out_name or ("%s-receipt.json" % task_id))
        extra = []
        for c in (covers or []):
            extra += ["--covers", c]
        code, text, _err = self.sbe("evidence", "run", "--out", receipt, "--kind", kind,
                                    *(extra + ["--cwd", worktree, "--"] + list(argv)))
        self.assertEqual(code, 0, "sbe evidence run for %s failed: %s" % (task_id, text))
        return receipt

    def finish_task(self, task_id):
        return self.work("finish", task_id)

    # -- registry, read directly: the single source of task state ----------

    def registry_path(self):
        return os.path.join(self.repo, ".sbe", "tasks.json")

    def registry(self):
        with io.open(self.registry_path(), encoding="utf-8") as fh:
            return json.load(fh)

    def record(self, task_id):
        for t in self.registry()["tasks"]:
            if t.get("id") == task_id:
                return t
        return None


# ---------------------------------------------------------------------------
# A narrow, explicitly-scoped mirror of skills/next/SKILL.md's priority
# ladder, described in full in the module docstring above.
# ---------------------------------------------------------------------------

def ladder_rung(team_report):
    """One of "kickoff", "work-continue", "review", "done", read purely from
    `team_report`, the parsed JSON `sbe status --team --json` already
    printed (never re-derived, never prose-matched).

    Mirrors skills/next/SKILL.md's rungs 2, 4, 6 and 7, and narrows rung 4
    further on purpose (stated here, not left implicit): the real rung 4
    treats a severity-8 ("ready", no registry record yet) finding the same
    as a severity-7 ("active", open) one whenever no severity-7 entry is
    present. This function only treats severity 7 as "work in flight";
    severity 8 alone ("something COULD start, nothing has") does not, by
    itself, force every other rung shut, because a real team routinely
    reviews work that has already landed while other ready tasks sit
    waiting for a different agent to pick up -- and because forcing this
    scenario to start all four tasks before review ever became reachable
    would prove nothing about the review step this suite is actually
    checking:

      - rung 2 ("no intake recorded"): no change discovered at all ->
        "kickoff", the skill that would create one;
      - rung 4, narrowed ("something is open right now"): a severity-7
        finding exists for some change -> "work-continue", DELIBERATELY not
        counted as a skill in `TestSkillLadderStaysWithinFour` below,
        because this is exactly what the pass criterion means by "next
        guides internal steps": the work, evidence, fix and converge loop
        is CLI, not a separate skill invocation;
      - rung 6 ("review not run"): a severity-11 finding exists whose
        verdict is not PASS -> "review";
      - rung 7 ("everything green"): none of the above -> "done", no skill
        named (next's own rung 7 recommends closing guidance, never a skill).

    This never reaches skills/next/SKILL.md's rungs 1 (doctor), 3 (design
    completeness) or 5 (tier evidence): the golden scenario's dossier is
    built complete from its first commit (`build_scenario.write_dossier`
    writes `00-intake.json` through `07-verification.md` together, one
    commit), so a real, undiminished implementation of those rungs would
    never fire against it either -- this function just does not carry the
    machinery to prove that independently, and says so here rather than
    silently.
    """
    changes = team_report.get("changes") or {}
    if not changes:
        return "kickoff"
    findings = team_report.get("findings") or []
    if any(f.get("severity") == 7 for f in findings):
        return "work-continue"
    review = [f for f in findings if f.get("severity") == 11]
    if any(f.get("verdict") != "PASS" for f in review):
        return "review"
    return "done"


class TestSkillLadderStaysWithinFour(GoldenScenarioFixture):
    """Pass criterion: "user invokes no more than four BrotherSBE skills in
    the happy path because next guides internal steps (assert on the JSON
    next-action ladder, not on prose)."

    Checkpointed at three real, distinct states of the SAME scenario, each
    read through a fresh `sbe status --team --json` subprocess: before the
    dossier exists (kickoff), mid-flight with T01 open (work-continue,
    explicitly not counted), and after a review record exists but is not
    yet approved (review). `handover` is counted unconditionally: LT-3's own
    goal states a handover is deliberate, user-requested, never something
    `next`'s ladder routes to on its own, so it is a fourth skill this
    scenario chooses to use, not one the ladder produces.
    """

    def _team_report(self):
        code, text, _err = self.status_team()
        self.assertIn(code, (0, 1), "sbe status --team --json must exit its documented "
                     "control-pass/control-fail code, not crash: %s" % text)
        return json.loads(text)

    def test_the_distinct_skills_this_scenario_needs_stay_at_four_or_fewer(self):
        touched = set()

        # Checkpoint A: nothing built yet at all -- a genuinely bare
        # repository, no dossier, in its OWN tempdir (never this fixture's
        # `self.repo`, which setUp already gave a dossier and plan).
        bare_dir = tempfile.mkdtemp(prefix="sbe-golden-bare-")
        try:
            bs.init_repo(bare_dir)
            code, text, _err = bs.run_sbe(SBE, ["status", bare_dir, "--team", "--json"])
            self.assertIn(code, (0, 1), text)
            bare_report = json.loads(text)
            rung = ladder_rung(bare_report)
            self.assertEqual(rung, "kickoff", bare_report)
            touched.add(rung)
        finally:
            shutil.rmtree(bare_dir, ignore_errors=True)

        # The dossier and plan already exist (setUp built them), so the
        # SECOND checkpoint is mid-flight: start T01 and read status again.
        self.start_task("T01", "alpha")
        mid_report = self._team_report()
        rung = ladder_rung(mid_report)
        self.assertEqual(rung, "work-continue", mid_report)
        # Deliberately NOT added to `touched`: this is the internal
        # work/evidence loop the pass criterion says `next` absorbs.

        worktree = self.worktree_path_for("T01")
        bs.write(worktree, bs.BACKEND_PATH,
                "def lookup(widget_id, catalog):\n    return catalog.get(widget_id)\n")
        bs.git(worktree, "add", "-A")
        bs.git(worktree, "commit", "-qm", "backend: add the lookup service")
        self.run_evidence("T01", bs.BACKEND_VERIFY_ARGV)
        code, text, _err = self.finish_task("T01")
        self.assertEqual(code, 0, text)

        code, text, _err = self.sbe(
            "review", self.dossier, "--write", "--reviewer", "Independent Reviewer",
            "--reviewer-type", "human", "--result", "changes-required")
        # `sbe review`'s exit code reflects its own mechanical score/gate
        # delegates over this deliberately minimal dossier, never whether
        # the record write itself succeeded (`_record_review` cannot move
        # the exit code, by construction): not asserted here, only that the
        # record landed, checked next through `sbe status --team --json`.
        review_report = self._team_report()
        rung = ladder_rung(review_report)
        self.assertEqual(rung, "review", review_report)
        touched.add(rung)

        # The deliberate, user-requested fourth skill: an ownership
        # transfer. Never produced by the ladder itself (see docstring).
        touched.add("handover")

        self.assertLessEqual(
            len(touched), 4,
            "the golden scenario's happy path touches more than four distinct "
            "BrotherSBE skills: %s" % sorted(touched))
        self.assertEqual(touched, {"kickoff", "review", "handover"},
                         "expected exactly kickoff, review and handover across this "
                         "scenario's happy path, with the work/evidence/fix/converge loop "
                         "absorbed as next's internal CLI guidance: %s" % sorted(touched))


# ---------------------------------------------------------------------------
# The full chain, one scenario, one test.
# ---------------------------------------------------------------------------

class TestFullChain(GoldenScenarioFixture):
    """start -> next -> work -> evidence -> review -> fix -> converge ->
    handover -> acknowledge, driven through the real engine over the golden
    scenario repository. Every pass criterion in the spec's own list is
    asserted somewhere in this one method, in the order the chain visits
    it, because the criteria are properties of ONE run of the scenario, not
    of nine independent ones."""

    def test_the_whole_chain_start_next_work_evidence_review_fix_converge_handover_acknowledge(self):
        # ---- start / next: nothing exists yet, then the dossier and plan
        # do (built by setUp). A fresh `sbe status --team --json` on the
        # bare repo (before setUp's dossier commit lands... it already has,
        # so this instead re-confirms the dossier IS discovered) grounds
        # "next" in the same JSON the ladder test above reads. ----
        code, text, _err = self.status_team()
        self.assertIn(code, (0, 1), text)
        report = json.loads(text)
        self.assertIn(self.change_id, report["changes"], report)

        # ---- work + evidence: T01, a ready, disjoint writer task. ----
        wt01 = self.start_task("T01", "alpha")
        bs.write(wt01, bs.BACKEND_PATH,
                "def lookup(widget_id, catalog):\n    return catalog.get(widget_id)\n")
        bs.git(wt01, "add", "-A")
        bs.git(wt01, "commit", "-qm", "backend: add the lookup service")
        self.run_evidence("T01", bs.BACKEND_VERIFY_ARGV)

        # review-route on T01's own diff: zero specialist reviewer is a
        # legal result (a plain internal service file trips no detector).
        head01_pre_finish = bs.git(wt01, "rev-parse", "HEAD")
        code, text, _err = self.sbe("review-route", self.repo, "--base", self.base,
                                    "--head", head01_pre_finish, "--json")
        self.assertEqual(code, 0, text)
        route01 = json.loads(text)
        self.assertTrue(route01["mechanicalOnly"], route01)
        self.assertIsNone(route01["primaryReviewer"], route01)

        code, text, _err = self.finish_task("T01")
        self.assertEqual(code, 0, text)
        record01 = self.record("T01")
        self.assertEqual(record01["status"], "closed", record01)
        self.assertNotIn("forced", record01, record01)

        # ---- work + evidence: T02, the second ready, disjoint writer
        # task (the small SQL change). ----
        wt02 = self.start_task("T02", "beta")
        bs.write(wt02, bs.SQL_PATH,
                "-- Add an index on widget id for faster lookups\n"
                "CREATE INDEX idx_widget_id ON widgets (id);\n")
        bs.git(wt02, "add", "-A")
        bs.git(wt02, "commit", "-qm", "sql: add the widget index migration")
        self.run_evidence("T02", bs.SQL_VERIFY_ARGV)
        head02_first = bs.git(wt02, "rev-parse", "HEAD")

        # review-route on T02's diff: exactly the reviewers the migration
        # and the embedded SQL actually trigger, never the full seven-agent
        # roster ("only required reviewers run").
        code, text, _err = self.sbe("review-route", self.repo, "--base", self.base,
                                    "--head", head02_first, "--json")
        self.assertEqual(code, 0, text)
        route02 = json.loads(text)
        self.assertEqual(route02["primaryReviewer"], "migration-reviewer", route02)
        self.assertEqual(route02["secondaryReviewer"], "data-reviewer", route02)
        self.assertFalse(route02["mechanicalOnly"], route02)

        # ---- review: one finding, from the two reviewers review-route
        # itself just selected, submitted as two raw entries sharing one
        # fingerprint. Deduplicated to ONE structured finding: "a duplicate
        # finding appears once." ----
        finding_a = {"reviewer": "migration-reviewer", "category": "missing-rollback",
                    "severity": "critical", "confidence": "high",
                    "introducedByChange": "yes",
                    "location": "%s:1" % bs.SQL_PATH,
                    "failure": "the index migration has no documented rollback step.",
                    "evidence": ["migrations/0002_widget_index.sql has no reverse "
                                "statement or comment"]}
        finding_b = dict(finding_a, reviewer="data-reviewer",
                        evidence=["confirmed independently against the same migration"])
        findings_path = os.path.join(self.tmp, "findings-open.json")
        with io.open(findings_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps([finding_a, finding_b]))
        # `sbe review`'s own exit code reflects its mechanical score/gate
        # delegates over this deliberately minimal dossier, never whether
        # the record write succeeded (`_record_review` cannot move the
        # exit code, by construction): not asserted here, only that the
        # record landed, checked directly below.
        self.sbe(
            "review", self.dossier, "--write", "--reviewer", "Independent Reviewer",
            "--reviewer-type", "human", "--result", "changes-required",
            "--findings-json", findings_path)
        review_record_path = os.path.join(self.dossier, "11-review.json")
        with io.open(review_record_path, encoding="utf-8") as fh:
            review_record = json.load(fh)
        structured = review_record["structuredFindings"]
        self.assertEqual(len(structured), 1,
                         "two raw findings sharing one fingerprint must dedupe to one "
                         "structured finding, and did not: %s" % structured)
        finding = structured[0]
        self.assertEqual(sorted(finding["sources"]), ["data-reviewer", "migration-reviewer"],
                         "the deduplicated finding must name both reviewer sources: %s"
                         % finding)
        self.assertEqual(finding["status"], "open", finding)

        # ---- fix: the finding is addressed by a second commit on T02's
        # own branch, a fresh receipt bound to the fixed commit, and a
        # second, independent review write marking the SAME finding
        # "fixed" with verification proof (LT-202: "no finding can mark
        # itself fixed without verification"). ----
        bs.write(wt02, bs.SQL_PATH,
                "-- Add an index on widget id for faster lookups\n"
                "CREATE INDEX idx_widget_id ON widgets (id);\n"
                "-- Rollback: DROP INDEX idx_widget_id;\n")
        bs.git(wt02, "add", "-A")
        bs.git(wt02, "commit", "-qm", "sql: document the rollback step")
        self.run_evidence("T02", bs.SQL_VERIFY_ARGV)
        code, text, _err = self.finish_task("T02")
        self.assertEqual(code, 0, text)
        record02 = self.record("T02")
        self.assertEqual(record02["status"], "closed", record02)
        head02_final = bs.git(wt02, "rev-parse", "HEAD")

        fixed = dict(finding_a, status="fixed", verification=bs.SQL_VERIFY)
        fixed_path = os.path.join(self.tmp, "findings-fixed.json")
        with io.open(fixed_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps([fixed]))
        code, text, _err = self.sbe(
            "review", self.dossier, "--write", "--reviewer", "Independent Reviewer",
            "--reviewer-type", "human", "--result", "approved",
            "--findings-json", fixed_path)
        with io.open(review_record_path, encoding="utf-8") as fh:
            review_record = json.load(fh)
        self.assertEqual(review_record["structuredFindings"][0]["status"], "fixed",
                         review_record)

        # ---- converge: run for real, over T02's own branch. SCOPE and
        # DATA are meaningfully PASS (the change stays in its declared
        # scope, and the migration drops nothing the data model still
        # documents). T02's OWN receipt now PASSes coverage: the
        # coveredFiles-shape gap this scenario originally discovered
        # (docs/KNOWN-LIMITS.md) was fixed in rc.11, and this pin was
        # built to flip with it. VERIFICATION still FAILs for the one
        # reason that is an inherent property of a never-merged,
        # multi-branch team workflow: T01/T03/T04's receipts bind to
        # their OWN branch heads, not the head assessed here. FINAL is
        # FAIL by the documented rule (FAIL beats everything). ----
        code, text, _err = self.sbe("converge", self.dossier, "--base", self.base,
                                    "--head", head02_final, "--cwd", self.repo, "--json")
        self.assertEqual(code, 1, text)
        converge_report = json.loads(text)
        by_name = {d["name"]: d for d in converge_report["dimensions"]}
        self.assertEqual(by_name["SCOPE"]["verdict"], "PASS", by_name["SCOPE"])
        self.assertEqual(by_name["DATA"]["verdict"], "PASS", by_name["DATA"])
        self.assertEqual(by_name["VERIFICATION"]["verdict"], "FAIL", by_name["VERIFICATION"])
        verification_detail = " ".join(f["detail"] for f in by_name["VERIFICATION"]["findings"])
        self.assertNotIn("does not cover", verification_detail,
                         "the coveredFiles-shape gap was fixed in rc.11; a does-not-cover "
                         "finding against T02's own receipt would mean it regressed: %s"
                         % verification_detail)
        self.assertIn("has a sealed receipt bound to %s" % head02_final[:12],
                      verification_detail,
                      "expected T02's own receipt to PASS coverage against its branch "
                      "head after the rc.11 fix: %s" % verification_detail)
        self.assertIn("binds to", verification_detail,
                      "expected the inherent wrong-head findings for the other tasks' "
                      "branch-bound receipts to remain: %s" % verification_detail)
        self.assertEqual(converge_report["final"], "FAIL", converge_report)

        # ---- work + evidence: T03, the dependent task. T01 (its
        # dependency) is already closed clean, so it may start; it owns
        # nothing, so no commit of its own is needed or legal. ----
        self.start_task("T03", "gamma")
        # `--covers README.md`, never a dossier path: every task branches
        # from `plan.baseCommit` (the SEED commit, before the dossier's own
        # commit), so T03's worktree -- like every task's -- never contains
        # the dossier files at all, only README.md from the seed. T01/T02/
        # T04 never notice, because they only ADD a new file each; T03 is
        # the one task that needs to name an EXISTING file, and README.md is
        # the one file guaranteed present at the commit every task actually
        # branches from.
        self.run_evidence("T03", bs.TEST_VERIFY_ARGV, covers=["README.md"])
        code, text, _err = self.finish_task("T03")
        self.assertEqual(code, 0, text)
        record03 = self.record("T03")
        self.assertEqual(record03["status"], "closed", record03)

        # ---- work + evidence: T04, the planted security-sensitive
        # configuration edit. It goes through the SAME ordinary task
        # lifecycle as every other writer task: scope declared, receipt
        # bound, closes clean. That is the point -- the task-scope
        # postcondition has no opinion about WHICH file a task owns, only
        # whether the diff matches the declaration, so a config edit
        # "passing" that control proves nothing about whether it should
        # have happened at all. ----
        wt04 = self.start_task("T04", "delta")
        bs.write(wt04, bs.AUTHORITY_PATH,
                "{\"team\": \"golden-scenario\", \"note\": \"local override\"}\n")
        bs.git(wt04, "add", "-A")
        bs.git(wt04, "commit", "-qm", "config: record local settings override")
        self.run_evidence("T04", bs.CONFIG_VERIFY_ARGV)
        code, text, _err = self.finish_task("T04")
        self.assertEqual(code, 0, text)
        record04 = self.record("T04")
        self.assertEqual(record04["status"], "closed", record04)
        head04 = bs.git(wt04, "rev-parse", "HEAD")

        # ---- the untrusted instruction edit is caught: a SEPARATE
        # control, `sbe instruction-surface`, unaware of the task registry,
        # over the SAME range T04 just closed clean. No --declared file, no
        # env var, no Approved-by/Reviewed-in trailer on T04's own commit:
        # FAIL, naming the undeclared authority surface. ----
        wt04_path = self.worktree_path_for("T04")
        code, text, _err = self.sbe("instruction-surface", wt04_path, self.base, "--strict")
        self.assertEqual(code, 1, text)
        self.assertIn("FAIL", text)
        self.assertIn(bs.AUTHORITY_PATH, text)
        live_hash_before = self._repo_head_hash()

        # ---- calibration: the SAME planted edit, in a scratch clone under
        # /tmp (never this suite's own worktree), with the control
        # neutralized (the path declared, a non-vacuous Approved-by:
        # trailer added on a detached HEAD): uncaught. Proves the FAIL
        # above is driven by the specific undeclared condition, not a
        # vacuous always-FAIL. The scratch clone is disposed of afterward,
        # and the live repository's own head is re-hashed to prove the
        # calibration never touched it (the same "red then hash-verified
        # restore then green" discipline `TestNoMergeOrPushInThisFixture`
        # below applies to its own scanner). ----
        scratch_dir = tempfile.mkdtemp(prefix="sbe-golden-calibration-")
        try:
            clone = subprocess.run(["git", "clone", "-q", self.repo, scratch_dir],
                                   capture_output=True, text=True)
            self.assertEqual(clone.returncode, 0, clone.stderr)
            branch = "sbe/%s/T04" % self.change_id
            checkout = subprocess.run(
                ["git", "checkout", "-q", "--detach", "origin/%s" % branch],
                cwd=scratch_dir, capture_output=True, text=True)
            self.assertEqual(checkout.returncode, 0, checkout.stderr)
            declared_path = os.path.join(scratch_dir, "declared.txt")
            with io.open(declared_path, "w", encoding="utf-8") as fh:
                fh.write(bs.AUTHORITY_PATH + "\n")
            amend = subprocess.run(
                ["git", "-c", "user.name=Golden Scenario Fixture",
                 "-c", "user.email=fixture@example.invalid",
                 "commit", "--amend", "-q", "-m",
                "config: record local settings override\n\n"
                "Approved-by: security-reviewer@example.invalid"],
                cwd=scratch_dir, capture_output=True, text=True)
            self.assertEqual(amend.returncode, 0, amend.stderr)
            code, text, _err = bs.run_sbe(
                SBE, ["instruction-surface", scratch_dir, self.base,
                     "--declared", declared_path, "--strict"])
            self.assertEqual(code, 0,
                             "the SAME planted edit, declared and reviewed in the scratch "
                             "copy, must no longer FAIL: %s" % text)
            self.assertNotIn("FAIL", text)
        finally:
            shutil.rmtree(scratch_dir, ignore_errors=True)
        self.assertEqual(self._repo_head_hash(), live_hash_before,
                         "the calibration scratch copy must never mutate this suite's own "
                         "scenario repository")

        # ---- max three writers; no overlapping edits, proved from the
        # task registry, not from the plan's own promise. ----
        registry = self.registry()
        writers = [t for t in registry["tasks"] if t.get("role") == "writer"]
        self.assertLessEqual(len(writers), 3, registry)
        owned_sets = [set(t.get("ownedPaths") or []) for t in writers]
        for i in range(len(owned_sets)):
            for j in range(i + 1, len(owned_sets)):
                self.assertEqual(owned_sets[i] & owned_sets[j], set(),
                                 "writers %s and %s share an owned path: %s"
                                 % (writers[i]["id"], writers[j]["id"], registry))
        self.assertEqual({t["id"] for t in registry["tasks"]}, set(bs.ALL_TASK_IDS), registry)
        for t in registry["tasks"]:
            self.assertEqual(t["status"], "closed", t)
            self.assertNotIn("forced", t, t)

        # ---- session restart: kill everything in-memory (there is nothing
        # to kill here but the previous subprocess), rebuild readers from
        # disk in a BRAND NEW subprocess, and confirm status recovers
        # without manual repair. ----
        code, text, _err = self.status_team()
        self.assertIn(code, (0, 1), text)
        restarted = json.loads(text)
        self.assertIn(self.change_id, restarted["changes"], restarted)
        completed = [f for f in restarted["findings"]
                    if f["severity"] == 9 and f["change"] == self.change_id]
        self.assertTrue(completed, "a fresh process must report this change complete purely "
                        "from what is on disk: %s" % restarted["findings"])

        # ---- handover: one ownership transfer. Receiver acknowledgment
        # required; ownership stays with the outgoing owner until then. ----
        code, text, _err = self.sbe("handover", "prepare", self.dossier,
                                    "--outgoing", OUTGOING, "--receiver", RECEIVER)
        self.assertEqual(code, 0, text)
        handover_path = os.path.join(self.dossier, "12-handover.json")
        with io.open(handover_path, encoding="utf-8") as fh:
            handover = json.load(fh)
        self.assertEqual(handover["status"], "prepared", handover)
        self.assertIsNone(handover["acknowledgment"], handover)
        self.assertEqual(handover["outgoingOwner"], OUTGOING, handover)
        self.assertEqual(handover["intendedReceiver"], RECEIVER, handover)

        code, text, _err = self.sbe("handover", "show", self.dossier, "--json")
        self.assertEqual(code, 0, text)
        shown = json.loads(text)
        self.assertEqual(shown["state"], "ok", shown)
        self.assertFalse(shown["stale"], shown)

        code, text, _err = self.sbe("handover", "acknowledge", self.dossier,
                                    "--receiver", RECEIVER)
        self.assertEqual(code, 0, text)
        with io.open(handover_path, encoding="utf-8") as fh:
            handover = json.load(fh)
        self.assertEqual(handover["status"], "acknowledged", handover)
        self.assertIsNotNone(handover["acknowledgment"], handover)
        self.assertEqual(handover["acknowledgment"]["receiver"], RECEIVER, handover)

        # ---- no merge, push, or production apply occurred anywhere in
        # this scenario's real git history (TestNoMergeLaw pattern, applied
        # to the repository's own reflog and log rather than to source, the
        # way tools/test_sbe_team_workflow.py's TestNoMergeOrPushExists
        # ImplementationPath already checks its own scratch repository). ----
        merges = bs.git(self.repo, "log", "--all", "--merges", "--oneline")
        self.assertEqual(merges, "", "no merge commit may exist anywhere in this "
                        "repository's history: %r" % merges)
        reflog = bs.git(self.repo, "reflog", "--all")
        merge_lines = [line for line in reflog.splitlines() if "merge" in line.lower()]
        self.assertEqual(merge_lines, [], "the reflog must show no merge action: %s"
                        % merge_lines)

    def _repo_head_hash(self):
        """sha256 of `git log --all --format=raw` over this suite's OWN
        scenario repository: a cheap, load-bearing proof that the
        calibration scratch clone never mutated it, mirroring the
        "hash-verified restore" discipline `TestNoMergeOrPushInThisFixture`
        applies to source text below."""
        out = subprocess.run(["git", "log", "--all", "--format=raw"], cwd=self.repo,
                             capture_output=True, text=True)
        return hashlib.sha256(out.stdout.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Team-mode CI postcondition (folded per docs/plans/2026-08-04-parity-
# triage-verdict.md row 34 into LT-501 acceptance): `sbe status --team
# --json` exits per its own documented contract, `status.team_blocking`:
# "exit 0 only when no finding has severity 1 to 6" -- a presence check by
# SEVERITY SLOT, independent of verdict (a severity-5 NO-DATA finding, "no
# approval report saved," blocks exactly as a severity-1 FAIL would).
#
# This suite discovered, while trying to build a genuinely clean scenario,
# that severity 5 ("no approval report is saved") fires unconditionally for
# any change with no `10-approval.json` -- which only a real `sbe pr verify`
# against a real GitHub PR ever writes, entirely outside a local scratch
# fixture's reach -- and that `sbe status --team`'s evidence scan
# (`_scan_evidence`, `evidence.verify(cwd=root)` in `src/brothersbe/status.
# py`) always compares a receipt's `headCommit` against the REPOSITORY
# ROOT's own currently checked-out HEAD, never against the task's own
# worktree branch the receipt was generated in. In this project's own `sbe
# work start`-created isolated-worktree-per-task model (every task branches
# into its own linked worktree, and `sbe work` never merges, rebases onto,
# or pushes to the root's own branch, by the same no-merge law this suite
# checks elsewhere), a receipt for a finished task's own verification
# therefore reads as a severity-1 "broken claim" the moment `sbe status
# --team` inspects it -- not because anything about the evidence is wrong,
# but because the root repository's own branch never advances to include
# that task's commits. Both are named in `docs/KNOWN-LIMITS.md`.
#
# The honest, provable claim left standing is the CONTRACT itself, not a
# demand that any particular state reach exit 0: the exit code always
# equals whether `findings` carries a severity-1-to-6 entry, calibrated at
# two real, different states of the same scenario (a bare dossier, and one
# after real task work landed), never a single snapshot.
# ---------------------------------------------------------------------------

def _team_blocking(report):
    """Mirrors `status.team_blocking` exactly (a presence check by severity
    slot, independent of verdict): kept here as a SEPARATE, hand-written
    reimplementation rather than imported, so this suite's own contract
    check does not just call the function it is trying to verify."""
    return any(1 <= f["severity"] <= 6 for f in (report.get("findings") or []))


#: The finding fields `docs/specs/2026-07-30-sbe-status-team.md` documents.
#: The real implementation (`status._finding`) also carries a "detail"
#: field the doc does not name; this suite asserts the documented fields
#: are ALL present (a superset check), never exact equality, so an
#: honest additive field is not a spurious failure here.
_DOCUMENTED_FINDING_FIELDS = {"change", "severity", "verdict", "evidence", "commit",
                              "owner", "nextAction", "basis"}


class TestTeamModeCIPostcondition(GoldenScenarioFixture):
    def test_the_exit_code_matches_the_severity_one_to_six_contract_across_two_real_states(self):
        # State 1: a bare dossier and plan, no task started at all.
        code, text, _err = self.status_team()
        self.assertIn(code, (0, 1), text)
        report = json.loads(text)
        self.assertEqual(code != 0, _team_blocking(report),
                         "sbe status --team --json's exit code must equal whether any "
                         "finding carries severity 1 to 6, and did not, over a bare "
                         "dossier: %s" % report)
        for f in report["findings"]:
            self.assertTrue(
                set(f.keys()) >= _DOCUMENTED_FINDING_FIELDS,
                "every finding must carry at least the documented JSON contract fields: %s"
                % f)

        # State 2: real task work has landed (T01 started, edited, evidenced
        # and finished for real through the engine).
        wt01 = self.start_task("T01", "alpha")
        bs.write(wt01, bs.BACKEND_PATH,
                "def lookup(widget_id, catalog):\n    return catalog.get(widget_id)\n")
        bs.git(wt01, "add", "-A")
        bs.git(wt01, "commit", "-qm", "backend: add the lookup service")
        self.run_evidence("T01", bs.BACKEND_VERIFY_ARGV)
        code, text, _err = self.finish_task("T01")
        self.assertEqual(code, 0, text)

        code, text, _err = self.status_team()
        self.assertIn(code, (0, 1), text)
        report = json.loads(text)
        self.assertEqual(code != 0, _team_blocking(report),
                         "sbe status --team --json's exit code must equal whether any "
                         "finding carries severity 1 to 6, and did not, after real task "
                         "work landed: %s" % report)
        # Since rc.11 the evidence scan resolves a receipt claimed by a task
        # record's evidenceId against that record's declared worktree, so a
        # finished task's own receipt verifies clean instead of misreading
        # as a severity-1 broken claim against root HEAD. This pin was built
        # to flip with that fix, and it flipped exactly this far: the state
        # STAYS blocking, for the legitimate NO-DATA reasons that were
        # always underneath (severity 5, no approval report; severity 6, no
        # convergence report), which is itself the proof that the fix
        # removed only the misreport and upgraded nothing else. Non-vacuity
        # is kept two ways: no severity-1 finding may remain while the
        # blocking severities are named exactly, and the plain status JSON
        # must PROVE the receipt was seen and resolved against T01's
        # declared worktree, so a scan that silently dropped the receipt
        # cannot pass here either.
        self.assertTrue(_team_blocking(report), report)
        self.assertEqual([f for f in report["findings"] if f["severity"] == 1], [],
                         "expected no severity-1 broken claim after the worktree "
                         "resolution fix: %s" % report["findings"])
        blocking_severities = sorted({f["severity"] for f in report["findings"]
                                      if 1 <= f["severity"] <= 6})
        self.assertEqual(blocking_severities, [5, 6],
                         "expected exactly the two legitimate NO-DATA blockers (missing "
                         "approval, missing convergence) and no verification misreport: "
                         "%s" % report["findings"])
        code_s, text_s, _err = self.sbe("status", self.repo, "--json")
        self.assertIn(code_s, (0, 1), text_s)
        plain = json.loads(text_s)
        sound = plain.get("soundEvidence") or []
        self.assertTrue(any("declared worktree" in (entry.get("finding") or "")
                            for entry in sound),
                        "expected T01's receipt to verify as sound evidence, resolved "
                        "against its task record's declared worktree: %s" % sound)


# ---------------------------------------------------------------------------
# TestNoMergeLaw pattern (tools/test_sbe_work.py:717, tools/test_sbe_team_
# workflow.py's own mirror), applied here to THIS suite's own two files:
# neither `tools/test_sbe_golden_scenario.py` nor `tools/fixtures/golden-
# scenario/build_scenario.py` may ever construct a merge, rebase, push or
# deploy git argv. Calibrated on a scratch mutated COPY, red then a
# hash-verified confirmation the real files were never touched, then green.
# ---------------------------------------------------------------------------

_FORBIDDEN_GIT_SUBCOMMANDS = ("merge", "rebase", "push", "deploy")

_SCANNED_FILES = (os.path.abspath(__file__),
                  os.path.join(FIXTURE_DIR, "build_scenario.py"))


def _find_forbidden_git_argv(source):
    """Every call site in `source` that could construct a forbidden git
    subcommand, scoped to the two shapes this suite's own helpers use for a
    subprocess call (`subprocess.run([...])` and a `git(cwd, *args)`-style
    helper call), mirrored from `tools/test_sbe_team_workflow.py`'s own
    `_find_forbidden_git_argv` rather than imported: suites in this project
    stay standalone."""
    tree = ast.parse(source)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_subprocess_run = (isinstance(func, ast.Attribute) and func.attr in ("run", "call",
                                                                                "Popen")
                             and isinstance(func.value, ast.Name)
                             and func.value.id == "subprocess")
        is_git_helper = isinstance(func, ast.Name) and func.id == "git"
        if is_subprocess_run and node.args:
            first = node.args[0]
            if (isinstance(first, (ast.List, ast.Tuple)) and first.elts
                    and isinstance(first.elts[0], ast.Constant)
                    and isinstance(first.elts[0].value, str)):
                word = first.elts[0].value.lower()
                if word == "git" and len(first.elts) > 1:
                    second = first.elts[1]
                    if (isinstance(second, ast.Constant)
                            and isinstance(second.value, str)
                            and second.value.lower() in _FORBIDDEN_GIT_SUBCOMMANDS):
                        hits.append("%s (line %d)" % (second.value.lower(), node.lineno))
                elif word in _FORBIDDEN_GIT_SUBCOMMANDS:
                    hits.append("%s (line %d)" % (word, node.lineno))
        elif is_git_helper and len(node.args) >= 2:
            sub = node.args[1]
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                word = sub.value.lower()
                if word in _FORBIDDEN_GIT_SUBCOMMANDS:
                    hits.append("%s (line %d)" % (word, node.lineno))
    return hits


class TestNoMergeOrPushInThisFixture(unittest.TestCase):
    def test_neither_fixture_file_constructs_a_merge_rebase_push_or_deploy_argv(self):
        for path in _SCANNED_FILES:
            with io.open(path, encoding="utf-8") as fh:
                source = fh.read()
            hits = _find_forbidden_git_argv(source)
            self.assertEqual(hits, [], "%s constructs git argv starting with a forbidden "
                            "subcommand: %s" % (path, ", ".join(hits)))

    def test_calibration_a_concat_built_merge_call_is_caught_on_a_scratch_copy_then_the_real_files_are_confirmed_unchanged(self):
        before_hashes = {}
        for path in _SCANNED_FILES:
            with io.open(path, "rb") as fh:
                before_hashes[path] = hashlib.sha256(fh.read()).hexdigest()

        scratch_dir = tempfile.mkdtemp(prefix="sbe-golden-nomerge-scratch-")
        try:
            mutated = (
                "import subprocess\n"
                "def _scratch_mutation(repo, branch):\n"
                "    subprocess.run(['git', 'merge', 'origin', branch], cwd=repo)\n")
            scratch_path = os.path.join(scratch_dir, "mutated_scratch_copy.py")
            with io.open(scratch_path, "w", encoding="utf-8") as fh:
                fh.write(mutated)
            with io.open(scratch_path, encoding="utf-8") as fh:
                reloaded = fh.read()
            hits = _find_forbidden_git_argv(reloaded)
        finally:
            shutil.rmtree(scratch_dir, ignore_errors=True)

        self.assertTrue(hits, "calibration failed: a literal merge subcommand passed to "
                        "subprocess.run(['git', 'merge', ...]) must be caught, and it was "
                        "not")

        after_hashes = {}
        for path in _SCANNED_FILES:
            with io.open(path, "rb") as fh:
                after_hashes[path] = hashlib.sha256(fh.read()).hexdigest()
        self.assertEqual(before_hashes, after_hashes,
                         "the calibration mutation ran only against a scratch copy under "
                         "/tmp; this suite's own real files must be byte-identical before "
                         "and after")


if __name__ == "__main__":
    unittest.main(verbosity=2)
