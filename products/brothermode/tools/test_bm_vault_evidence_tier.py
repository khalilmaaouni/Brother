#!/usr/bin/env python3
"""Calibration for THE EVIDENCE TIER AT RECALL (row LL-2, 2026-09-05):
bm_vault_contradiction.evidence_tier(), the one function bm_vault.py's
_print_hits and vault_recall_hook.py both call so the two surfaces agree on
the same lesson's verdict.

Fixtures are tiny invented "widget" notes in a tempfile vault, parsed
through the real parse_lesson()/vault_recall_hook._lesson_state(), never a
hand built lesson dict standing in for what the frontmatter parser and the
hook are actually supposed to produce.

No em or en dashes anywhere in this file.
"""
import datetime
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_contradiction as contradiction  # noqa: E402

HOOK_PATH = os.path.join(HERE, "vault_recall_hook.py")

sys.path.append(os.path.join(HERE, "../../../scripts"))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))

FUTURE = "2099-01-01"


def load_hook():
    """A fresh import of vault_recall_hook.py, the same pattern
    scripts/gauntlet_memory_poisoning.py's own load_hook() uses."""
    spec = importlib.util.spec_from_file_location(
        "vault_recall_hook_for_evidence_tier", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _note(lines_extra, body="Fixture lesson body, never advice this test wants followed."):
    lines = ["---"] + list(lines_extra) + ["---", "", body, ""]
    return "\n".join(lines)


class EvidenceTierAtRecall(unittest.TestCase):
    def setUp(self):
        self.vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)

    def _write(self, relpath, text):
        path = os.path.join(self.vault, relpath)
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def _lesson(self, relpath):
        lesson = contradiction.parse_lesson(os.path.join(self.vault, relpath))
        self.assertIsNotNone(lesson, "fixture %s failed to parse" % relpath)
        return lesson

    def _probe(self):
        return contradiction.make_evidence_probe(self.vault)

    # Tier EVIDENCED: evidence_locator present, currently holds, not superseded.
    def test_evidenced_when_locator_holds_and_not_superseded(self):
        self._write("proof.py", "WIDGET_TIMEOUT = 30\n")
        self._write("10-Lessons/a.md", _note([
            "name: widget timeout is 30",
            "evidence_locator: grep:proof.py:WIDGET_TIMEOUT = 30",
            "status: verified",
        ]))
        lesson = self._lesson("10-Lessons/a.md")
        tier, reason = contradiction.evidence_tier(lesson, self._probe())
        self.assertEqual(tier, contradiction.TIER_EVIDENCED)
        self.assertIn("holds", reason)

    # Tier UNVERIFIED: opted in (status declared) but no evidence_locator.
    def test_unverified_when_no_locator_declared(self):
        self._write("10-Lessons/a.md", _note([
            "name: widget timeout is 30",
            "status: verified",
        ]))
        lesson = self._lesson("10-Lessons/a.md")
        tier, reason = contradiction.evidence_tier(lesson, self._probe())
        self.assertEqual(tier, contradiction.TIER_UNVERIFIED)
        self.assertIn("no evidence_locator", reason)

    # Rule: an UNVERIFIED lesson is advisory and is never counted as
    # applied. Exercised at the hook, the surface the recurrence recorder
    # actually reads (state == "unverified" is what makes brother_run.py
    # count it declined rather than applied).
    def test_unverified_lesson_is_never_counted_as_applied_at_the_hook(self):
        tree = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tree, ignore_errors=True)
        with open(os.path.join(tree, "widget.py"), "w", encoding="utf-8") as fh:
            fh.write("WIDGET = 1\n")
        self._write("a.md", _note([
            "name: widget rule",
            "applies_to: [widget.py]",
            "status: verified",
        ]))
        hook = load_hook()
        state, line, _note_type = hook._lesson_state(
            "a", os.path.join(self.vault, "a.md"), tree)
        self.assertEqual(state, "unverified")
        self.assertIsNotNone(line)
        self.assertIn(contradiction.TIER_UNVERIFIED, line)

    # A note the tier resolver EVIDENCES still reaches "applied" at the
    # hook: the downgrade is one directional, evidence never blocks a
    # lesson applies_to already cleared.
    def test_evidenced_lesson_still_applies_at_the_hook(self):
        tree = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tree, ignore_errors=True)
        with open(os.path.join(tree, "widget.py"), "w", encoding="utf-8") as fh:
            fh.write("WIDGET_TIMEOUT = 30\n")
        self._write("a.md", _note([
            "name: widget rule",
            "applies_to: [widget.py]",
            "evidence_locator: grep:widget.py:WIDGET_TIMEOUT = 30",
            "status: verified",
        ]))
        hook = load_hook()
        state, line, _note_type = hook._lesson_state(
            "a", os.path.join(self.vault, "a.md"), tree)
        self.assertEqual(state, "applied")
        self.assertIsNone(line)

    # A plain lesson (no applies_to, no evidence_locator, no status) never
    # regresses off this new law: the recurrence gauntlet's own "memory on"
    # arm plants exactly this shape and must keep reading "applied".
    def test_plain_lesson_with_only_applies_to_still_applies_at_the_hook(self):
        tree = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tree, ignore_errors=True)
        with open(os.path.join(tree, "widget.py"), "w", encoding="utf-8") as fh:
            fh.write("WIDGET = 1\n")
        self._write("a.md", _note([
            "name: widget rule",
            "applies_to: [widget.py]",
        ]))
        hook = load_hook()
        state, line, _note_type = hook._lesson_state(
            "a", os.path.join(self.vault, "a.md"), tree)
        self.assertEqual(state, "applied")
        self.assertIsNone(line)

    # Tier REFUSED: evidence_locator names a file that does not exist.
    def test_refused_when_locator_names_a_missing_file(self):
        self._write("10-Lessons/a.md", _note([
            "name: widget timeout renamed",
            "evidence_locator: path:this-file-does-not-exist.txt",
            "status: verified",
        ]))
        lesson = self._lesson("10-Lessons/a.md")
        tier, reason = contradiction.evidence_tier(lesson, self._probe())
        self.assertEqual(tier, contradiction.TIER_REFUSED)
        self.assertIn("does not currently hold", reason)

    # Tier REFUSED: verified_at forged into the future.
    def test_refused_when_verified_at_is_in_the_future(self):
        self._write("10-Lessons/a.md", _note([
            "name: widget timeout reverified",
            "verified_at: %s" % FUTURE,
            "status: verified",
        ]))
        lesson = self._lesson("10-Lessons/a.md")
        tier, reason = contradiction.evidence_tier(lesson, self._probe())
        self.assertEqual(tier, contradiction.TIER_REFUSED)
        self.assertIn("forged", reason)

    # The same forged-date refusal on last_verified_at, E74's own field
    # rather than this resolver's verified_at, with no evidence_locator or
    # status declared at all: the exact shape
    # scripts/gauntlet_memory_poisoning.py's forged-future-verified-at
    # class plants, whose own fixture note documents that nothing used to
    # read this field for anything.
    def test_refused_when_last_verified_at_is_in_the_future_with_no_other_signal(self):
        self._write("10-Lessons/a.md", _note([
            "name: widget reverified in 2099",
            "last_verified_at: %s" % FUTURE,
        ]))
        lesson = self._lesson("10-Lessons/a.md")
        tier, reason = contradiction.evidence_tier(lesson, self._probe())
        self.assertEqual(tier, contradiction.TIER_REFUSED)
        self.assertIn("forged", reason)

    # Tier REFUSED: a duplicate slug filed in a different folder with
    # different content, via a caller-supplied duplicate_probe (the shape
    # bm_vault.py's own _make_duplicate_probe hands in).
    def test_refused_when_duplicate_probe_names_a_conflicting_copy(self):
        self._write("10-Lessons/shared.md", _note(
            ["name: shared timeout rule"], body="the timeout is 30"))
        self._write("20-Harvest/shared.md", _note(
            ["name: shared timeout is dead code"],
            body="the timeout is dead code, ignore it"))
        lesson = self._lesson("20-Harvest/shared.md")

        def duplicate_probe(_lsn):
            return os.path.join(self.vault, "10-Lessons/shared.md")

        tier, reason = contradiction.evidence_tier(lesson, self._probe(), duplicate_probe)
        self.assertEqual(tier, contradiction.TIER_REFUSED)
        self.assertIn("duplicates", reason)

    # A duplicate_probe finding an IDENTICAL copy (a harvest folder's own
    # near-duplicate of the same note) is not a poisoning shape and must
    # not refuse anything: only the caller ever calls this a duplicate, so
    # this only proves evidence_tier trusts what it is told.
    def test_duplicate_probe_returning_none_never_refuses(self):
        self._write("10-Lessons/a.md", _note(["name: widget lesson", "status: verified"]))
        lesson = self._lesson("10-Lessons/a.md")

        def duplicate_probe(_lsn):
            return None

        tier, _reason = contradiction.evidence_tier(lesson, self._probe(), duplicate_probe)
        self.assertEqual(tier, contradiction.TIER_UNVERIFIED)

    # RECALL_NO_DATA: a plain legacy lesson (neither evidence_locator nor
    # status declared) is never tiered at all, the identical opt-in test
    # recall_verdict's own _has_signal already applies for the identical
    # reason: a note never touched by this law is never downgraded by it.
    def test_no_data_when_neither_evidence_locator_nor_status_declared(self):
        self._write("10-Lessons/a.md", _note(["name: plain widget lesson"]))
        lesson = self._lesson("10-Lessons/a.md")
        tier, _reason = contradiction.evidence_tier(lesson, self._probe())
        self.assertEqual(tier, contradiction.RECALL_NO_DATA)

    # Tier UNVERIFIED: evidence holds but the note is marked superseded.
    def test_unverified_when_evidence_holds_but_status_is_superseded(self):
        self._write("proof.py", "WIDGET_TIMEOUT = 30\n")
        self._write("10-Lessons/a.md", _note([
            "name: widget timeout is 30",
            "evidence_locator: grep:proof.py:WIDGET_TIMEOUT = 30",
            "status: superseded",
        ]))
        lesson = self._lesson("10-Lessons/a.md")
        tier, reason = contradiction.evidence_tier(lesson, self._probe())
        self.assertEqual(tier, contradiction.TIER_UNVERIFIED)
        self.assertIn("superseded", reason)

    # An unparsable/unresolvable locator scheme reports UNVERIFIED, never
    # REFUSED: NO_DATA_EVIDENCE is "cannot check", not "checked and failed".
    def test_unverified_when_locator_scheme_is_unrecognized(self):
        self._write("10-Lessons/a.md", _note([
            "name: widget odd locator",
            "evidence_locator: carrier-pigeon:somewhere",
            "status: verified",
        ]))
        lesson = self._lesson("10-Lessons/a.md")
        tier, reason = contradiction.evidence_tier(lesson, self._probe())
        self.assertEqual(tier, contradiction.TIER_UNVERIFIED)
        self.assertIn("could not be checked", reason)


if __name__ == "__main__":
    unittest.main()
