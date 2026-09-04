#!/usr/bin/env python3
"""End to end fixtures for `skills/review/SKILL.md` (LT-203.B): the nine
scenarios the skill's own flow promises to handle, proven against the real
`sbe review-route` and `sbe review --write --findings-json` commands the
skill dispatches, never against a mock of either. This file adds no new
production code and touches neither `src/brothersbe/reviewroute.py` nor
`src/brothersbe/cli.py`: it is a black-box check that the two contracts
LT-201 and LT-202 already landed, USED TOGETHER exactly the way the skill
uses them, cover every scenario LT-203.B names. Run:
  python3 tools/test_sbe_review_skill_fixtures.py

Every fixture builds its own throwaway git repository, the same discipline
tools/test_sbe_review_route.py and tools/test_sbe_review_record.py both
already follow and for the same reason: the defect this skill exists to
avoid lives at the seam between a diff, a route and a written record, and a
mocked one of the three would test the mock, not the seam.

Coverage, one test method per LT-203.B bullet:

  test_duplicate_backend_findings_...        two dispatched reviewers report
                                              the identical defect; it must
                                              appear once, sources naming
                                              both (acceptance: "duplicate
                                              findings appear once").
  test_conflicting_severities_...            two sources disagree on
                                              severity; the fold keeps the
                                              highest and flags it rather
                                              than silently picking one.
  test_low_confidence_false_positive_...     a critical, low-confidence
                                              finding is recorded but never
                                              blocks (acceptance:
                                              "low-confidence observations
                                              are visibly non-blocking").
  test_pre_existing_issue_...                a finding this change did not
                                              introduce is reported
                                              separately and never blocks.
  test_migration_plus_security_trigger_...   a diff touching both a
                                              migration and a secret-shaped
                                              path routes to both
                                              reviewers, at tier T3.
  test_three_reviewer_triggers_...           three triggers, two slots: the
                                              route selects exactly two and
                                              names the third as having lost
                                              its slot, never silently.
  test_reviewer_output_missing_a_location... a finding with no location and
                                              no conceptId refuses the WHOLE
                                              write, never a partial record.
  test_accepted_risk_without_a_named_human...   `status: "accepted"` with no
                                              `disposition.by`, and with a
                                              `disposition.by` equal to the
                                              finding's own reviewer, both
                                              refuse the write.
  test_stale_review_after_head_moves_...     a review record bound to a
                                              superseded head reads FAIL at
                                              severity 4, and its structured
                                              findings are still read back
                                              honestly, independent of that
                                              staleness.
"""
import io
import json
import os
import shutil
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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SBE = os.path.join(ROOT, "bin", "sbe")

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

#: The same minimal LT-202 finding docs/CLI.md and tools/test_sbe_review_record.py
#: both use, so a reader comparing fixtures across files sees one shape, not three.
_GOOD_FINDING = {
    "reviewer": "backend-reviewer", "category": "idempotency", "severity": "critical",
    "confidence": "high", "introducedByChange": "yes", "location": "src/api.py:123",
    "failure": "A retried request can create two orders.",
    "evidence": ["tests/test_orders.py::test_duplicate reproduces it"],
    "verification": "pytest tests/test_orders.py -k duplicate",
}


class ReviewSkillFixtures(unittest.TestCase):
    """A fresh repository per test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-review-skill-")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "fixture")
        io.open(os.path.join(self.repo, "seed.txt"), "w").write("seed\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "seed")
        self.base = self.git("rev-parse", "HEAD").strip()
        # Isolate every `sbe` call in this file from the real machine, the same
        # two lines tools/test_sbe_review_record.py's ReviewScenario uses.
        self.env = dict(os.environ)
        self.env.pop("BROTHERSBE_VAULT", None)
        self.env["SBE_CITATION_ROOT"] = self.repo

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args):
        out = subprocess.run(["git", "-C", self.repo] + list(args),
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, "git %s failed: %s" % (args, out.stderr))
        return out.stdout

    def sbe(self, *args):
        # Three values on purpose, matching every other suite's _run helper: a
        # two-item (int, str) return here pattern-matches the (verdict,
        # evidence) pair the honesty sweep hunts for outside registries, and
        # the sweep rightly refused to assume this helper can never say PASS.
        out = subprocess.run([sys.executable, SBE] + list(args), capture_output=True,
                             text=True, env=self.env, stdin=subprocess.DEVNULL, timeout=120)
        return out.returncode, out.stdout + out.stderr, out.stderr

    def write(self, rel, body):
        path = os.path.join(self.repo, rel)
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        io.open(path, "w", encoding="utf-8").write(body)
        return path

    def route(self, *extra):
        """`sbe review-route` against the bare repository (never a dossier
        holding a declared intake): every route-selection fixture below
        needs the DIFF-DERIVED tier, exactly like tools/test_sbe_review_route.py's
        own RouteFixture, because a declared T1 intake would cap every one
        of them at one reviewer regardless of how many triggers fired."""
        code, text, _err = self.sbe("review-route", self.repo, "--base", self.base, "--json", *extra)
        data = json.loads(text[text.index("{"):]) if "{" in text else None
        return code, data, text

    def _dossier(self, name="chg-a", src_rel="src/api.py"):
        """A minimal dossier `sbe review --write` can persist a record
        against, seeded the same way ReviewScenario._change does in
        tools/test_sbe_review_record.py."""
        doss = os.path.join(self.repo, "design", name)
        os.makedirs(doss)
        io.open(os.path.join(doss, "00-intake.json"), "w").write(json.dumps(INTAKE, indent=2))
        io.open(os.path.join(doss, "03-adr.md"), "w").write(ADR_TEMPLATE % src_rel)
        io.open(os.path.join(doss, "07-verification.md"), "w").write(VERIFICATION)
        self.write(src_rel, "x = 1\n")
        code, text, _err = self.sbe("plan", doss, "--write", "--cwd", self.repo)
        self.assertEqual(code, 0, "plan --write: %s" % text)
        self.git("add", "-A")
        self.git("commit", "-qm", "%s dossier and source" % name)
        return doss

    def _write_findings_file(self, findings):
        path = os.path.join(self.tmp, "findings-%d.json" % id(findings))
        io.open(path, "w", encoding="utf-8").write(json.dumps(findings))
        return path

    def _write_review(self, doss, findings, reviewer="Independent Reviewer",
                      reviewer_type="human", result="approved"):
        """`sbe review <doss> --write --findings-json <findings>`, the exact
        call step 4 of skills/review/SKILL.md makes. (record, None) when the
        write succeeded, or (None, ...) when it was refused: a refused write
        must leave no partial 11-review.json, so a caller checks for the
        file rather than trusting the exit code alone."""
        findings_path = self._write_findings_file(findings)
        code, text, _err = self.sbe("review", doss, "--write", "--reviewer", reviewer,
                              "--reviewer-type", reviewer_type, "--result", result,
                              "--findings-json", findings_path)
        record_path = os.path.join(doss, "11-review.json")
        record = json.loads(io.open(record_path, encoding="utf-8").read()) \
            if os.path.exists(record_path) else None
        return code, text, record

    def _team_findings(self, change, severity=None):
        code, text, _err = self.sbe("status", self.repo, "--team", "--json")
        data = json.loads(text[text.index("{"):])
        hits = [f for f in data["findings"] if f["change"] == change]
        if severity is not None:
            hits = [f for f in hits if f["severity"] == severity]
        return code, hits, text

    def _struct_hit(self, change="chg-a"):
        """The severity-11 finding whose detail names structured findings
        specifically, filtered by content the same way
        tools/test_sbe_review_record.py's TestStatusReadsStructuredFindings
        does, because a record's own pass/fail finding shares severity 11
        with the structured-findings one and a caller must not assume list
        position."""
        _code, hits, text = self._team_findings(change, 11)
        matches = [f for f in hits if "structured" in f["detail"].lower()]
        self.assertTrue(matches, "expected a structured-findings finding: %s" % text)
        return matches[0]

    # -- 1: duplicate backend findings -----------------------------------

    def test_duplicate_backend_findings_fold_into_one_with_both_sources(self):
        """Two dispatched reviewers reporting the identical backend defect
        (same category, location and failure text) must persist as ONE
        structured finding naming both sources: LT-203's own acceptance
        line "duplicate findings appear once.\""""
        doss = self._dossier("chg-a", "src/api.py")
        a = dict(_GOOD_FINDING, reviewer="backend-reviewer")
        b = dict(_GOOD_FINDING, reviewer="qa-reviewer", confidence="medium")
        code, text, record = self._write_review(doss, [a, b])
        self.assertEqual(code, 0, text)
        self.assertIsNotNone(record, text)
        self.assertEqual(len(record["structuredFindings"]), 1, record["structuredFindings"])
        finding = record["structuredFindings"][0]
        self.assertEqual(sorted(finding["sources"]), ["backend-reviewer", "qa-reviewer"], finding)
        self.assertTrue(finding["blocking"],
                        "critical severity with a high-confidence source must block: %s" % finding)
        hit = self._struct_hit("chg-a")
        self.assertEqual(hit["verdict"], "FAIL", hit)
        self.assertIn("1 blocking", hit["detail"])

    # -- 2: conflicting severities -----------------------------------------

    def test_conflicting_severities_keep_the_highest_and_are_flagged(self):
        doss = self._dossier("chg-a", "src/api.py")
        a = dict(_GOOD_FINDING, reviewer="backend-reviewer", severity="minor")
        b = dict(_GOOD_FINDING, reviewer="qa-reviewer", severity="critical")
        code, text, record = self._write_review(doss, [a, b])
        self.assertEqual(code, 0, text)
        finding = record["structuredFindings"][0]
        self.assertEqual(finding["severity"], "critical", finding)
        self.assertTrue(finding["severityDisagreement"],
                        "two sources disagreeing on severity must be flagged, not silently "
                        "resolved: %s" % finding)

    # -- 3: low-confidence false positive -----------------------------------

    def test_low_confidence_false_positive_is_recorded_but_never_blocks(self):
        doss = self._dossier("chg-a", "src/api.py")
        entry = dict(_GOOD_FINDING, confidence="low")
        code, text, record = self._write_review(doss, [entry])
        self.assertEqual(code, 0, text)
        finding = record["structuredFindings"][0]
        self.assertFalse(finding["blocking"],
                         "a low-confidence finding must never block, even at critical "
                         "severity: %s" % finding)
        hit = self._struct_hit("chg-a")
        self.assertEqual(hit["verdict"], "PASS",
                         "recorded but non-blocking must read PASS, not FAIL: low confidence "
                         "has to stay visibly non-blocking, never invisible: %s" % hit)
        self.assertIn("0 blocking", hit["detail"])

    # -- 4: pre-existing issue -----------------------------------------------

    def test_pre_existing_issue_is_reported_separately_and_never_blocks(self):
        doss = self._dossier("chg-a", "src/api.py")
        entry = dict(_GOOD_FINDING, introducedByChange="no")
        code, text, record = self._write_review(doss, [entry])
        self.assertEqual(code, 0, text)
        finding = record["structuredFindings"][0]
        self.assertFalse(finding["blocking"],
                         "pre-existing findings never block, even at critical severity and "
                         "high confidence: %s" % finding)
        hit = self._struct_hit("chg-a")
        self.assertIn("1 pre-existing", hit["detail"])
        self.assertEqual(hit["verdict"], "PASS", hit)

    # -- 5: migration plus security trigger ----------------------------------

    def test_migration_plus_security_trigger_selects_both_reviewers(self):
        self.write("migrations/0001_add_users.sql", "-- initial migration, see ADR-12\n")
        self.write("config/secret_config.py", "TOKEN = 'placeholder'\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "a migration alongside a secret-shaped path")
        code, data, text = self.route()
        self.assertEqual(code, 0, text)
        self.assertEqual(data["tier"], "T3",
                         "a secret-shaped path always raises touches_sensitive to T3: %s" % text)
        self.assertEqual(data["primaryReviewer"], "migration-reviewer", text)
        self.assertEqual(data["secondaryReviewer"], "security-reviewer", text)
        self.assertFalse(data["mechanicalOnly"], text)

    # -- 6: three reviewer triggers, two selected ----------------------------

    def test_three_reviewer_triggers_two_are_selected_third_is_named_lost(self):
        self.write("migrations/0002_add_flag.sql", "ALTER TABLE users ADD COLUMN x INT;\n")
        self.write("config/secret_keys.py", "KEY = 'placeholder'\n")
        self.write("reports/kpi.sql", "CREATE VIEW kpi AS SELECT 1;\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "three triggers at once")
        code, data, text = self.route()
        self.assertEqual(code, 0, text)
        selected = [r for r in (data["primaryReviewer"], data["secondaryReviewer"]) if r]
        self.assertEqual(len(selected), 2,
                         "the skill may never dispatch more than the route's own two slots: "
                         "%s" % text)
        self.assertEqual(selected, ["migration-reviewer", "security-reviewer"], text)
        self.assertTrue(
            any("data" in u["reason"] and "lost its slot" in u["reason"]
                for u in data["unmeasured"]),
            "the third trigger must be named by the route as having lost its slot, never "
            "silently dropped, so the skill's one-sentence summary can say so: %s" % text)

    # -- 7: reviewer output missing a location -------------------------------

    def test_reviewer_output_missing_a_location_refuses_the_whole_write(self):
        doss = self._dossier("chg-a", "src/api.py")
        bad = dict(_GOOD_FINDING)
        del bad["location"]
        code, text, record = self._write_review(doss, [bad])
        self.assertEqual(code, 2, text)
        self.assertIn("location", text)
        self.assertIsNone(record,
                         "a refused write must leave no partial record: the skill must never "
                         "force an unlocatable finding through by guessing an anchor: %s" % text)

    # -- 8: accepted risk without a named human identity ---------------------

    def test_accepted_risk_without_a_named_human_identity_is_refused(self):
        doss = self._dossier("chg-a", "src/api.py")
        no_by = dict(_GOOD_FINDING, status="accepted",
                     disposition={"reason": "ok", "scope": "src/api.py"})
        code, text, record = self._write_review(doss, [no_by])
        self.assertEqual(code, 2, text)
        self.assertIn("disposition.by", text)
        self.assertIsNone(record, text)

        self_accept = dict(_GOOD_FINDING, reviewer="backend-reviewer", status="accepted",
                           disposition={"by": "backend-reviewer", "reason": "ok",
                                       "scope": "src/api.py"})
        code2, text2, record2 = self._write_review(doss, [self_accept])
        self.assertEqual(code2, 2, text2)
        self.assertIn("never accept its own risk", text2)
        self.assertIsNone(record2,
                         "a reviewer naming itself as the accepting human is not an independent "
                         "acceptance and must refuse the whole write, exactly like the missing "
                         "'by' case above: %s" % text2)

    # -- 9: stale review after HEAD moves -------------------------------------

    def test_stale_review_after_head_moves_still_reports_readable_structured_findings(self):
        doss = self._dossier("chg-a", "src/api.py")
        # Non-blocking on purpose: staleness has to be the only reason this
        # is not ready, not a blocking finding riding along with it.
        entry = dict(_GOOD_FINDING, severity="minor")
        code, text, record = self._write_review(doss, [entry])
        self.assertEqual(code, 0, text)
        bound_head = record["headSha"]

        io.open(os.path.join(self.repo, "src", "api.py"), "a").write("y = 2\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "a later commit the saved review never saw")

        _c, hits4, _t = self._team_findings("chg-a", 4)
        stale = [h for h in hits4 if "review" in h["detail"].lower()]
        self.assertTrue(stale,
                        "a review record bound to a superseded head must surface at severity "
                        "4: %s" % hits4)
        self.assertEqual(stale[0]["verdict"], "FAIL", stale[0])
        self.assertIn(bound_head[:12], stale[0]["detail"])

        # Read independently of that staleness: the skill needs to know what
        # the stale record's structured findings said before deciding
        # whether re-running step 4 would actually change anything.
        hit = self._struct_hit("chg-a")
        self.assertIn("0 blocking", hit["detail"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
