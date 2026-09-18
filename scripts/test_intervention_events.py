#!/usr/bin/env python3
"""Tests for scripts/intervention_events.py (DOM-20.01).

Drives the module directly against a temp run directory, the way
test_side_effect_ledger.py and test_run_journal_chain.py drive their own
modules: no network, no real subprocess, no engine around it.
"""
import inspect
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import intervention_events as ie  # noqa: E402
import journal  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))

REPO_ROOT = os.path.dirname(HERE)


class InterventionTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="intervention-events-test-")
        self.run_dir = os.path.join(self.tmp, "run")
        os.makedirs(self.run_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestPinnedVocabulary(unittest.TestCase):
    """Rule 5 of the worker contract, and the TEST RULE it comes with:
    every category and every required field, pinned against a literal
    expected set, so a category silently added or dropped fails here."""

    def test_categories_are_exactly_the_pinned_six(self):
        self.assertEqual(ie.INTERVENTION_CATEGORIES, frozenset((
            "credential",
            "authorization",
            "clarification",
            "correction",
            "manual_continuation",
            "external_action",
        )))

    def test_required_fields_are_exactly_the_pinned_three(self):
        self.assertEqual(ie.REQUIRED_FIELDS,
                         ("category", "reason", "could_continue_without_it"))

    def test_event_type_is_pinned(self):
        self.assertEqual(ie.EVENT_TYPE, "human.intervention")


class TestRecordAndRead(InterventionTestCase):
    """The mutation target: dropping a field from the written payload, or
    reading the wrong event type key, must make one of these fail."""

    def test_recorded_intervention_carries_all_three_required_fields(self):
        ie.record_intervention(self.run_dir, "credential",
                               "needed the deploy key from the keychain",
                               False)
        events = ie.read_interventions(self.run_dir)
        self.assertEqual(len(events), 1)
        payload = events[0]["payload"]
        self.assertEqual(payload["category"], "credential")
        self.assertEqual(payload["reason"],
                         "needed the deploy key from the keychain")
        self.assertIs(payload["could_continue_without_it"], False)
        self.assertEqual(events[0]["type"], "human.intervention")

    def test_reason_is_stripped_of_surrounding_whitespace(self):
        ie.record_intervention(self.run_dir, "clarification",
                               "  which branch to target  ", True)
        events = ie.read_interventions(self.run_dir)
        self.assertEqual(events[0]["payload"]["reason"],
                         "which branch to target")

    def test_extra_fields_are_merged_into_the_payload(self):
        ie.record_intervention(self.run_dir, "correction", "fixed a typo",
                               True, unit_id="DOM-20.01")
        events = ie.read_interventions(self.run_dir)
        self.assertEqual(events[0]["payload"]["unit_id"], "DOM-20.01")

    def test_append_only_order_is_preserved(self):
        ie.record_intervention(self.run_dir, "credential", "first", True)
        ie.record_intervention(self.run_dir, "correction", "second", False)
        ie.record_intervention(self.run_dir, "external_action", "third", True)
        events = ie.read_interventions(self.run_dir)
        self.assertEqual([e["payload"]["reason"] for e in events],
                         ["first", "second", "third"])

    def test_non_intervention_events_are_filtered_out(self):
        journal.append(self.run_dir, "unit.done", payload={"unit": "x"})
        ie.record_intervention(self.run_dir, "manual_continuation",
                               "re-ran after a stop", False)
        journal.append(self.run_dir, "unit.done", payload={"unit": "y"})
        events = ie.read_interventions(self.run_dir)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["reason"], "re-ran after a stop")

    def test_read_returns_none_when_no_journal_exists_at_all(self):
        empty_dir = os.path.join(self.tmp, "no-journal-here")
        os.makedirs(empty_dir)
        self.assertIsNone(ie.read_interventions(empty_dir))

    def test_read_returns_empty_list_when_journal_exists_with_no_intervention(self):
        journal.append(self.run_dir, "unit.done", payload={"unit": "x"})
        events = ie.read_interventions(self.run_dir)
        self.assertEqual(events, [])
        self.assertIsNotNone(events)


class TestRefusals(InterventionTestCase):
    """An intervention with no reason, or an unknown category, is refused
    rather than recorded as a default. Every refusal below is checked
    twice: the exception fires, AND nothing landed in the journal."""

    def _nothing_recorded(self):
        events = ie.read_interventions(self.run_dir)
        self.assertIn(events, ([], None))

    def test_unknown_category_is_refused(self):
        with self.assertRaises(ValueError):
            ie.record_intervention(self.run_dir, "founder_vibe_check",
                                   "a reason", True)
        self._nothing_recorded()

    def test_empty_category_is_refused(self):
        with self.assertRaises(ValueError):
            ie.record_intervention(self.run_dir, "", "a reason", True)
        self._nothing_recorded()

    def test_missing_reason_is_refused(self):
        with self.assertRaises(ValueError):
            ie.record_intervention(self.run_dir, "credential", "", True)
        self._nothing_recorded()

    def test_whitespace_only_reason_is_refused(self):
        with self.assertRaises(ValueError):
            ie.record_intervention(self.run_dir, "credential", "   ", True)
        self._nothing_recorded()

    def test_none_reason_is_refused(self):
        with self.assertRaises(ValueError):
            ie.record_intervention(self.run_dir, "credential", None, True)
        self._nothing_recorded()

    def test_could_continue_without_it_rejects_non_bool_standins(self):
        for bad_value in (1, 0, "yes", "no", None, 1.0):
            with self.assertRaises(ValueError):
                ie.record_intervention(self.run_dir, "credential", "a reason",
                                       bad_value)
        self._nothing_recorded()

    def test_extra_field_colliding_with_a_required_name_is_refused(self):
        # category, reason and could_continue_without_it are named
        # parameters, not **extra_fields, so Python itself refuses a
        # second value for one of them before record_intervention's body
        # ever runs: this is the language's own collision guard, not a
        # check this module has to write.
        with self.assertRaises(TypeError):
            ie.record_intervention(self.run_dir, "credential", "a reason",
                                   True, category="a second category")
        self._nothing_recorded()


class TestNoDefaultLogPath(unittest.TestCase):
    """A sibling builder polluted a real observable today by giving its
    own module a default path nobody overrode in a test. This module
    carries no such default: run_dir has no default value at all, so
    every call must name a real path and there is nothing implicit to
    write into. Checked structurally (the signature has no default) and
    by absence (the naive guess at a stray path beside this module does
    not exist after the whole suite has run)."""

    def test_run_dir_has_no_default_value(self):
        sig = inspect.signature(ie.record_intervention)
        self.assertIs(sig.parameters["run_dir"].default,
                      inspect.Parameter.empty)
        sig = inspect.signature(ie.read_interventions)
        self.assertIs(sig.parameters["run_dir"].default,
                      inspect.Parameter.empty)

    def test_no_stray_journal_lands_beside_the_module_or_at_repo_root(self):
        for candidate in (
            os.path.join(HERE, journal.JOURNAL_FILENAME),
            os.path.join(REPO_ROOT, journal.JOURNAL_FILENAME),
        ):
            self.assertFalse(os.path.exists(candidate),
                             "a real journal file exists at %r; this "
                             "module's calls must only ever land in a "
                             "caller-supplied run_dir" % (candidate,))


if __name__ == "__main__":
    unittest.main(verbosity=2)
