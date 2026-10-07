#!/usr/bin/env python3
"""What scripts/jev_ticket_triage.py must keep true.

Every forbidden term used here is FAKE and invented in this file. The
content gate's own test file records the incident where a scanner's
test fixtures leaked real private terms; this file does not repeat it.

The terms list is built in a temp directory and installed over the gate
module's DEFAULT_TERMS_PATH, so this suite runs unchanged on an export
tree with an empty HOME.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coe_outside_gate as gate  # noqa: E402
import jev_ticket_triage as jt  # noqa: E402

#: A fake category and a fake term. Not a real term from any list.
FAKE_TERMS = {"fake-j1b-terms": ["zzfakej1bterm"]}


class _Recorder(object):
    """A stand-in model: records every content it is shown, and returns a
    fixed answer (or raises it, when the answer is an Exception)."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, content):
        self.calls.append(content)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class _HostileItemsDict(dict):
    """A dict subclass whose OWN .items() raises the exact TypeError a
    hostile or unhashable key would surface: it is what makes a raw crash
    observable, so the refusing guard can be shown able to go red."""

    def items(self):
        raise TypeError("unhashable type: 'list'")


class TriageTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        terms_path = os.path.join(self._tmp.name, "terms.json")
        with open(terms_path, "w", encoding="utf-8") as handle:
            json.dump(FAKE_TERMS, handle)
        self._saved_terms_path = gate.DEFAULT_TERMS_PATH
        gate.DEFAULT_TERMS_PATH = terms_path
        self.addCleanup(self._restore_terms)

    def _restore_terms(self):
        gate.DEFAULT_TERMS_PATH = self._saved_terms_path


class RuleRouteTests(TriageTestCase):
    def test_first_declared_rule_wins(self):
        ticket = {"subject": "refund please", "body": "billing refund"}
        rules = [
            {"queue": "billing", "keywords": ["refund"]},
            {"queue": "escalation", "keywords": ["refund", "billing"]},
        ]
        self.assertEqual(jt.rule_route(ticket, rules), "billing")

    def test_none_when_no_rule_matches(self):
        rules = [{"queue": "billing", "keywords": ["zzz"]}]
        self.assertIsNone(jt.rule_route({"subject": "hello"}, rules))

    def test_rule_route_is_case_folded(self):
        rules = [{"queue": "billing", "keywords": ["Refund"]}]
        self.assertEqual(jt.rule_route({"subject": "REFUND", "body": ""}, rules), "billing")

    def test_empty_ticket_matches_nothing(self):
        rules = [{"queue": "billing", "keywords": ["x"]}]
        self.assertIsNone(jt.rule_route({}, rules))

    def test_subject_only_ticket(self):
        rules = [{"queue": "auth", "keywords": ["locked"]}]
        self.assertEqual(jt.rule_route({"subject": "locked account"}, rules), "auth")

    def test_every_keyword_must_appear(self):
        rules = [{"queue": "billing", "keywords": ["refund", "invoice"]}]
        self.assertIsNone(jt.rule_route({"subject": "refund please"}, rules))

    def test_rule_route_refuses_hostile_input(self):
        for bad_ticket in (None, "x", 7, [], 3.5, True):
            with self.assertRaises(ValueError):
                jt.rule_route(bad_ticket, [])
        for bad_rules in (None, "x", 7, {}, 1.5):
            with self.assertRaises(ValueError):
                jt.rule_route({"subject": "a"}, bad_rules)
        with self.assertRaises(ValueError):
            jt.rule_route({"subject": "a"}, [None])
        with self.assertRaises(ValueError):
            jt.rule_route({"subject": "a"}, [{"queue": "q"}])
        with self.assertRaises(ValueError):
            jt.rule_route({"subject": "a"}, [{"queue": "q", "keywords": []}])
        with self.assertRaises(ValueError):
            jt.rule_route({"subject": "a"}, [{"queue": "q", "keywords": [7]}])
        with self.assertRaises(ValueError):
            jt.rule_route({"subject": 7}, [])


class TriageVerdictTests(TriageTestCase):
    def test_none_scores_is_human_queue(self):
        self.assertEqual(jt.triage_verdict(None, ["billing"]), jt.HUMAN_QUEUE)

    def test_empty_scores_is_human_queue(self):
        self.assertEqual(jt.triage_verdict({}, ["billing"]), jt.HUMAN_QUEUE)

    def test_confident_top_in_declared_queues_wins(self):
        scores = {"billing": 0.9, "auth": 0.1}
        self.assertEqual(jt.triage_verdict(scores, ["billing", "auth"]), "billing")

    def test_exact_bar_is_confident(self):
        self.assertEqual(jt.triage_verdict({"billing": 0.8}, ["billing"]), "billing")

    def test_tie_at_the_top_is_human_queue(self):
        scores = {"billing": 0.9, "auth": 0.9}
        self.assertEqual(jt.triage_verdict(scores, ["billing", "auth"]), jt.HUMAN_QUEUE)

    def test_top_under_the_bar_is_human_queue(self):
        self.assertEqual(jt.triage_verdict({"billing": 0.79}, ["billing"]), jt.HUMAN_QUEUE)

    def test_all_zero_is_human_queue(self):
        scores = {"billing": 0.0, "auth": 0.0}
        self.assertEqual(jt.triage_verdict(scores, ["billing", "auth"]), jt.HUMAN_QUEUE)

    def test_undeclared_top_queue_is_human_queue(self):
        self.assertEqual(jt.triage_verdict({"secret": 0.99}, ["billing"]), jt.HUMAN_QUEUE)

    def test_duplicate_declared_queues_do_not_change_the_verdict(self):
        scores = {"billing": 0.9, "auth": 0.1}
        self.assertEqual(
            jt.triage_verdict(scores, ["billing", "billing", "auth"]), "billing")

    def test_single_entry_queue_list_works(self):
        self.assertEqual(jt.triage_verdict({"billing": 0.9}, ["billing"]), "billing")
        self.assertEqual(jt.triage_verdict({"auth": 0.9}, ["billing"]), jt.HUMAN_QUEUE)

    def test_verdict_refuses_hostile_scores(self):
        for bad in (float("nan"), float("inf"), True, "0.9", None, [0.9]):
            with self.assertRaises(ValueError):
                jt.triage_verdict({"billing": bad}, ["billing"])
        with self.assertRaises(ValueError):
            jt.triage_verdict("not a dict", ["billing"])
        with self.assertRaises(ValueError):
            jt.triage_verdict({"billing": 0.9}, [])
        with self.assertRaises(ValueError):
            jt.triage_verdict({"billing": 0.9}, None)
        with self.assertRaises(ValueError):
            jt.triage_verdict({"billing": 0.9}, ["billing"], confident_at=float("nan"))
        with self.assertRaises(ValueError):
            jt.triage_verdict({"billing": 0.9}, ["billing"], confident_at=True)

    def test_unhashable_key_in_scores_is_refused(self):
        # The red team's exact class of finding: a scores mapping whose own
        # read raises TypeError("unhashable type: 'list'"). Without the
        # marker-only guard in triage_verdict/_safe_items the raw
        # interpreter TypeError escapes; the module's own ValueError is
        # the refusal the specification demands (H2).
        with self.assertRaises(ValueError):
            jt.triage_verdict(_HostileItemsDict({"billing": 0.9}), ["billing"])

    def test_unhashable_queue_name_in_scores_is_refused_without_crash(self):
        # A name that is a list: refused by _require_str as ValueError long
        # before any membership test could try to hash it.
        class _ListKeyDict(dict):
            def items(self):
                return [([1, 2], 0.9)]
        with self.assertRaises(ValueError):
            jt.triage_verdict(_ListKeyDict({"billing": 0.9}), ["billing"])

    def test_non_pair_entry_is_refused(self):
        class _BadEntryDict(dict):
            def items(self):
                return ["not a pair"]
        with self.assertRaises(ValueError):
            jt.triage_verdict(_BadEntryDict({"billing": 0.9}), ["billing"])


class TriageTests(TriageTestCase):
    def test_empty_queues_raises(self):
        with self.assertRaises(ValueError):
            jt.triage({"subject": "a"}, [], [])
        with self.assertRaises(ValueError):
            jt.triage({"subject": "a"}, None, [])
        with self.assertRaises(ValueError):
            jt.triage({"subject": "a"}, [7], [])

    def test_off_mode_goes_to_human_queue_and_never_asks(self):
        recorder = _Recorder({"billing": 0.99})
        result = jt.triage({"subject": "a"}, ["billing"], [], ask=recorder, mode="off")
        self.assertEqual(result["queue"], jt.HUMAN_QUEUE)
        self.assertFalse(result["consulted"])
        self.assertEqual(recorder.calls, [])

    def test_default_mode_is_off(self):
        result = jt.triage({"subject": "a"}, ["billing"], [])
        self.assertEqual(result["queue"], jt.HUMAN_QUEUE)
        self.assertFalse(result["consulted"])

    def test_empty_ticket_and_subject_only_ticket_default_to_human(self):
        self.assertEqual(jt.triage({}, ["billing"], [])["queue"], jt.HUMAN_QUEUE)
        self.assertEqual(
            jt.triage({"subject": "hello"}, ["billing"], [])["queue"], jt.HUMAN_QUEUE)

    def test_rule_match_is_never_overridden_by_the_model(self):
        recorder = _Recorder({"billing": 0.99})
        rules = [{"queue": "billing", "keywords": ["refund"]}]
        result = jt.triage(
            {"subject": "refund", "body": ""}, ["billing", "auth"], rules,
            ask=recorder, mode="assist")
        self.assertEqual(result["queue"], "billing")
        self.assertEqual(result["by"], "rule")
        self.assertFalse(result["consulted"])
        self.assertEqual(recorder.calls, [])

    def test_shadow_records_the_answer_but_keeps_human_queue(self):
        recorder = _Recorder({"billing": 0.99})
        result = jt.triage(
            {"subject": "clean subject"}, ["billing"], [],
            ask=recorder, mode="shadow")
        self.assertEqual(result["queue"], jt.HUMAN_QUEUE)
        self.assertEqual(result["scores"], {"billing": 0.99})
        self.assertTrue(result["consulted"])
        self.assertEqual(len(recorder.calls), 1)

    def test_assist_uses_the_model_scores_through_the_threshold(self):
        recorder = _Recorder({"billing": 0.9, "auth": 0.1})
        result = jt.triage(
            {"subject": "clean subject"}, ["billing", "auth"], [],
            ask=recorder, mode="assist")
        self.assertEqual(result["queue"], "billing")
        self.assertEqual(result["by"], "model")
        self.assertTrue(result["consulted"])

    def test_assist_below_the_bar_goes_to_human_queue(self):
        recorder = _Recorder({"billing": 0.4})
        result = jt.triage(
            {"subject": "clean subject"}, ["billing"], [],
            ask=recorder, mode="assist")
        self.assertEqual(result["queue"], jt.HUMAN_QUEUE)
        self.assertEqual(result["by"], "human-review")
        self.assertTrue(result["consulted"])

    def test_assist_undeclared_top_goes_to_human_queue(self):
        recorder = _Recorder({"secret": 0.99})
        result = jt.triage(
            {"subject": "clean subject"}, ["billing"], [],
            ask=recorder, mode="assist")
        self.assertEqual(result["queue"], jt.HUMAN_QUEUE)

    def test_assist_without_a_model_goes_to_human_queue(self):
        result = jt.triage(
            {"subject": "clean subject"}, ["billing"], [], ask=None, mode="assist")
        self.assertEqual(result["queue"], jt.HUMAN_QUEUE)
        self.assertFalse(result["consulted"])

    def test_a_malformed_answer_goes_to_human_queue(self):
        recorder = _Recorder("not a score map")
        result = jt.triage(
            {"subject": "clean subject"}, ["billing"], [],
            ask=recorder, mode="assist")
        self.assertEqual(result["queue"], jt.HUMAN_QUEUE)
        self.assertIsNone(result["scores"])

    def test_a_nan_answer_goes_to_human_queue(self):
        recorder = _Recorder({"billing": float("nan")})
        result = jt.triage(
            {"subject": "clean subject"}, ["billing"], [],
            ask=recorder, mode="assist")
        self.assertEqual(result["queue"], jt.HUMAN_QUEUE)

    def test_a_raising_model_goes_to_human_queue(self):
        recorder = _Recorder(RuntimeError("bridge down"))
        result = jt.triage(
            {"subject": "clean subject"}, ["billing"], [],
            ask=recorder, mode="assist")
        self.assertEqual(result["queue"], jt.HUMAN_QUEUE)
        self.assertFalse(result["consulted"])

    def test_a_hostile_score_mapping_from_the_model_is_no_data(self):
        # Same unhashable-key class of finding, arrived at through the
        # model's own answer: _clean_scores must convert the raw TypeError
        # into its own None (NO-DATA), never crash.
        recorder = _Recorder(_HostileItemsDict({"billing": 0.9}))
        result = jt.triage(
            {"subject": "clean subject"}, ["billing"], [],
            ask=recorder, mode="assist")
        self.assertEqual(result["queue"], jt.HUMAN_QUEUE)
        self.assertEqual(result["by"], "no-data")

    def test_personal_data_is_never_sent(self):
        recorder = _Recorder({"billing": 0.99})
        ticket = {"subject": "account", "body": "my token is zzfakej1bterm"}
        result = jt.triage(ticket, ["billing"], [], ask=recorder, mode="assist")
        self.assertEqual(result["queue"], jt.HUMAN_QUEUE)
        self.assertEqual(result["by"], "gate-refused")
        self.assertFalse(result["consulted"])
        self.assertEqual(recorder.calls, [])

    def test_unknown_mode_is_refused(self):
        for bad in ("advise", "ON", "", None, 1, True):
            with self.assertRaises(ValueError):
                jt.triage({"subject": "a"}, ["billing"], [], mode=bad)

    def test_non_callable_ask_is_refused(self):
        with self.assertRaises(ValueError):
            jt.triage({"subject": "a"}, ["billing"], [], ask="nope", mode="assist")

    def test_triage_refuses_hostile_ticket(self):
        for bad in (None, "x", [], 3, 1.5, True):
            with self.assertRaises(ValueError):
                jt.triage(bad, ["billing"], [])


if __name__ == "__main__":
    unittest.main()
