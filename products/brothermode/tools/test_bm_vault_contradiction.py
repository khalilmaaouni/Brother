#!/usr/bin/env python3
"""Calibration for tools/bm_vault_contradiction.py, the vault contradiction
resolver (founder steering 2026-09-05, sections 6 to 11).

Ten cases, C1 to C10, each a fixture built from scratch inside a temp
directory: invented lesson notes about a fictional "widget alpha" and
"widget beta", never a real benchmark or production name. Every fixture
carries its own tiny "evidence" file (a fake config, a fake decision doc, a
fake passing/failing script) so evidence_probe has something real to read,
never a mocked-out result standing in for what the resolver is supposed to
determine on its own.

No em or en dashes anywhere in this file.
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_contradiction as contradiction  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit,
# same convention test_bm_vault_survivorship.py already uses.
sys.path.append(os.path.join(HERE, "../../../scripts"))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))

# MUTATION CONTROL (ship item 4). Set BM_CONTRADICTION_MUTATION_CHECK=1 to
# replace the real precedence law with a naive stub that always applies the
# first lesson in a conflict, ignoring evidence entirely. This never ships:
# it patches the already-imported test-process module only, after import,
# and exists to prove these tests are not vacuous. Documented command:
#
#   BM_CONTRADICTION_MUTATION_CHECK=1 python3 test_bm_vault_contradiction.py -v
#
# Under that flag, test_c2_current_code_proves_b_apply_b_only FAILS: the
# naive stub picks lesson A (listed first in that test's conflict_set)
# where the real law picks B, because current evidence favors B.
if os.environ.get("BM_CONTRADICTION_MUTATION_CHECK") == "1":
    def _naive_first_wins(conflict_set, evidence_probe):
        lessons = list(conflict_set)
        return contradiction.Decision(
            contradiction.APPLY, lessons[0],
            "MUTATED: naive first-wins, evidence never consulted")
    contradiction.resolve = _naive_first_wins


def _note(lesson_id, statement, scope, status="unverified", source="fixture",
          source_type="transcript", verified_at="NO-DATA",
          verified_against="NO-DATA", evidence_locator=None, contradicts=None,
          supersedes=None):
    lines = ["---", "lesson_id: %s" % lesson_id, "statement: %s" % statement,
              "scope: %s" % scope, "source: %s" % source,
              "source_type: %s" % source_type, "status: %s" % status,
              "verified_at: %s" % verified_at,
              "verified_against: %s" % verified_against]
    if evidence_locator is not None:
        lines.append("evidence_locator: %s" % evidence_locator)
    if contradicts:
        lines.append("contradicts: " + ", ".join("[[%s]]" % c for c in contradicts))
    if supersedes:
        lines.append("supersedes: " + ", ".join("[[%s]]" % s for s in supersedes))
    lines.append("---")
    lines.append("")
    lines.append("Fixture lesson body, never advice this test wants followed.")
    lines.append("")
    return "\n".join(lines)


class BmVaultContradictionTenCases(unittest.TestCase):
    def setUp(self):
        self.vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)

    def _write(self, name, text):
        path = os.path.join(self.vault, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def _lesson(self, name):
        lesson = contradiction.parse_lesson(os.path.join(self.vault, name))
        self.assertIsNotNone(lesson, "fixture %s failed to parse" % name)
        return lesson

    def _probe(self):
        return contradiction.make_evidence_probe(self.vault)

    # C1: two opposite lessons, current test proves A, apply A only.
    def test_c1_current_test_proves_a_apply_a_only(self):
        self._write("check_timeout.py", "import sys\nsys.exit(0)\n")
        self._write("config.py", "TIMEOUT = 30\n")
        self._write("a.md", _note("L1", "the widget alpha timeout is 30 seconds",
                                    "widget-alpha-timeout",
                                    evidence_locator="test:check_timeout.py",
                                    contradicts=["L2"]))
        self._write("b.md", _note("L2", "the widget alpha timeout is 60 seconds",
                                    "widget-alpha-timeout",
                                    evidence_locator="grep:config.py:TIMEOUT = 60",
                                    contradicts=["L1"]))
        a, b = self._lesson("a.md"), self._lesson("b.md")
        conflicts = contradiction.find_conflicts([a, b])
        self.assertEqual(len(conflicts), 1)
        decision = contradiction.resolve(conflicts[0], self._probe())
        self.assertEqual(decision.verdict, contradiction.APPLY)
        self.assertEqual(decision.winner["lesson_id"], "L1")

    # C2: current code proves B, apply B only.
    def test_c2_current_code_proves_b_apply_b_only(self):
        self._write("config.py", "TIMEOUT = 60\n")
        self._write("a.md", _note("L1", "the widget alpha timeout is 30 seconds",
                                    "widget-alpha-timeout",
                                    evidence_locator="grep:config.py:TIMEOUT = 30",
                                    contradicts=["L2"]))
        self._write("b.md", _note("L2", "the widget alpha timeout is 60 seconds",
                                    "widget-alpha-timeout",
                                    evidence_locator="grep:config.py:TIMEOUT = 60",
                                    contradicts=["L1"]))
        a, b = self._lesson("a.md"), self._lesson("b.md")
        conflicts = contradiction.find_conflicts([a, b])
        self.assertEqual(len(conflicts), 1)
        decision = contradiction.resolve(conflicts[0], self._probe())
        self.assertEqual(decision.verdict, contradiction.APPLY)
        self.assertEqual(decision.winner["lesson_id"], "L2")

    # C3: both evidence locators missing, withhold both.
    def test_c3_both_evidence_locators_missing_withhold_both(self):
        self._write("a.md", _note("L1", "the widget alpha timeout is 30 seconds",
                                    "widget-alpha-timeout", contradicts=["L2"]))
        self._write("b.md", _note("L2", "the widget alpha timeout is 60 seconds",
                                    "widget-alpha-timeout", contradicts=["L1"]))
        a, b = self._lesson("a.md"), self._lesson("b.md")
        self.assertEqual(a["evidence_locator"], contradiction.NO_DATA)
        self.assertEqual(b["evidence_locator"], contradiction.NO_DATA)
        conflicts = contradiction.find_conflicts([a, b])
        decision = contradiction.resolve(conflicts[0], self._probe())
        self.assertEqual(decision.verdict, contradiction.WITHHOLD)
        self.assertIsNone(decision.winner)

    # C4: one verified lesson and one newer unverified, verified wins.
    def test_c4_one_verified_one_newer_unverified_verified_wins(self):
        self._write("a.md", _note("L1", "the widget alpha timeout is 30 seconds",
                                    "widget-alpha-timeout", status="verified",
                                    verified_at="2026-01-01", contradicts=["L2"]))
        self._write("b.md", _note("L2", "the widget alpha timeout is 60 seconds",
                                    "widget-alpha-timeout", status="unverified",
                                    verified_at="2026-09-01", contradicts=["L1"]))
        a, b = self._lesson("a.md"), self._lesson("b.md")
        conflicts = contradiction.find_conflicts([a, b])
        decision = contradiction.resolve(conflicts[0], self._probe())
        self.assertEqual(decision.verdict, contradiction.APPLY)
        self.assertEqual(decision.winner["lesson_id"], "L1",
                          "newer must not beat verified: the law is evidence and "
                          "authority, never recency")

    # C5: one superseded and one current, current wins.
    def test_c5_one_superseded_one_current_current_wins(self):
        self._write("a.md", _note("L1", "the widget alpha timeout is 30 seconds",
                                    "widget-alpha-timeout", status="superseded",
                                    contradicts=["L2"]))
        self._write("b.md", _note("L2", "the widget alpha timeout is 60 seconds",
                                    "widget-alpha-timeout", status="current",
                                    contradicts=["L1"]))
        a, b = self._lesson("a.md"), self._lesson("b.md")
        conflicts = contradiction.find_conflicts([a, b])
        decision = contradiction.resolve(conflicts[0], self._probe())
        self.assertEqual(decision.verdict, contradiction.APPLY)
        self.assertEqual(decision.winner["lesson_id"], "L2")

    # C6: two current verified lessons with different scopes, apply by scope.
    def test_c6_different_scopes_apply_by_scope_never_a_conflict(self):
        self._write("a.md", _note("L1", "the widget alpha timeout is 30 seconds",
                                    "widget-alpha-timeout", status="verified",
                                    contradicts=["L2"]))
        self._write("b.md", _note("L2", "the widget beta timeout is 30 seconds",
                                    "widget-beta-timeout", status="verified",
                                    contradicts=["L1"]))
        a, b = self._lesson("a.md"), self._lesson("b.md")
        conflicts = contradiction.find_conflicts([a, b])
        self.assertEqual(conflicts, [], "different scopes must never conflict, "
                                          "whatever a mistaken contradicts: field says")

    # C7: same scope and genuinely ambiguous evidence, withhold and escalate.
    def test_c7_genuinely_ambiguous_evidence_escalate(self):
        self._write("config.py", "MODE = fast\nMODE = safe\n")
        self._write("a.md", _note("L1", "the widget alpha mode is fast",
                                    "widget-alpha-mode",
                                    evidence_locator="grep:config.py:MODE = fast",
                                    contradicts=["L2"]))
        self._write("b.md", _note("L2", "the widget alpha mode is safe",
                                    "widget-alpha-mode",
                                    evidence_locator="grep:config.py:MODE = safe",
                                    contradicts=["L1"]))
        a, b = self._lesson("a.md"), self._lesson("b.md")
        conflicts = contradiction.find_conflicts([a, b])
        decision = contradiction.resolve(conflicts[0], self._probe())
        self.assertEqual(decision.verdict, contradiction.ESCALATE)
        self.assertIsNone(decision.winner)

    # C8: old authoritative decision contradicted by new current code, expose
    # the conflict, never silently choose historical memory.
    def test_c8_old_decision_contradicted_by_current_code_exposes_not_silent(self):
        self._write("decision.md", "# Widget Alpha decision\nSTATUS=SUPERSEDED\n")
        self._write("code.py", "TIMEOUT_MS = 5000\n")
        self._write("a.md", _note("L1", "the widget alpha timeout is 30000ms",
                                    "widget-alpha-timeout", status="verified",
                                    source_type="decision_record",
                                    evidence_locator="grep:decision.md:STATUS=CURRENT",
                                    contradicts=["L2"]))
        self._write("b.md", _note("L2", "the widget alpha timeout is 5000ms",
                                    "widget-alpha-timeout",
                                    evidence_locator="grep:code.py:TIMEOUT_MS = 5000",
                                    contradicts=["L1"]))
        a, b = self._lesson("a.md"), self._lesson("b.md")
        conflicts = contradiction.find_conflicts([a, b])
        decision = contradiction.resolve(conflicts[0], self._probe())
        self.assertEqual(decision.verdict, contradiction.APPLY)
        self.assertEqual(decision.winner["lesson_id"], "L2",
                          "current code must win over a decision record whose own "
                          "evidence no longer holds, regardless of its status field")

    # C9: stale source anchor, the lesson cannot auto-apply.
    def test_c9_stale_source_anchor_cannot_auto_apply(self):
        # removed.py never exists in this fixture: the anchor is gone.
        self._write("a.md", _note("L1", "the widget alpha flag old is set",
                                    "widget-alpha-flag",
                                    evidence_locator="grep:removed.py:OLD_FLAG = True",
                                    contradicts=["L2"]))
        self._write("b.md", _note("L2", "the widget alpha flag old is unset",
                                    "widget-alpha-flag", status="verified",
                                    contradicts=["L1"]))
        a, b = self._lesson("a.md"), self._lesson("b.md")
        probe = self._probe()
        self.assertEqual(probe(a), contradiction.FAILS,
                          "a stale anchor must FAIL, never NO-DATA and never HOLDS")
        conflicts = contradiction.find_conflicts([a, b])
        decision = contradiction.resolve(conflicts[0], self._probe())
        self.assertEqual(decision.verdict, contradiction.APPLY)
        self.assertEqual(decision.winner["lesson_id"], "L2",
                          "the stale-anchor lesson must never be the winner")

    # C10: current evidence changes after recall, re-evaluate before application.
    def test_c10_evidence_changes_after_recall_reevaluated(self):
        self._write("flag.py", "FLAG = ON\n")
        self._write("a.md", _note("L1", "the widget alpha flag is on",
                                    "widget-alpha-flag",
                                    evidence_locator="grep:flag.py:FLAG = ON",
                                    contradicts=["L2"]))
        self._write("b.md", _note("L2", "the widget alpha flag is off",
                                    "widget-alpha-flag",
                                    evidence_locator="grep:flag.py:FLAG = OFF",
                                    contradicts=["L1"]))
        a, b = self._lesson("a.md"), self._lesson("b.md")
        conflicts = contradiction.find_conflicts([a, b])

        first = contradiction.resolve(conflicts[0], self._probe())
        self.assertEqual(first.verdict, contradiction.APPLY)
        self.assertEqual(first.winner["lesson_id"], "L1")

        # Evidence changes on disk, as it would between two separate recalls.
        self._write("flag.py", "FLAG = OFF\n")
        second = contradiction.resolve(conflicts[0], self._probe())
        self.assertEqual(second.verdict, contradiction.APPLY)
        self.assertEqual(second.winner["lesson_id"], "L2",
                          "resolve() must re-read live evidence, never replay the "
                          "first call's verdict")

    # Bonus: the CLI's own clean path, so the shipped entry point is not
    # left completely untested by the ten cases above (all of which call
    # find_conflicts/resolve directly).
    def test_cli_reports_clean_on_no_conflicts(self):
        self._write("a.md", _note("L1", "the widget alpha timeout is 30 seconds",
                                    "widget-alpha-timeout"))
        rc = contradiction.main(["resolve", self.vault])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
