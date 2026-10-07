#!/usr/bin/env python3
"""Tests for context_action_adapter.resolve and
mobile_hybrid_action_router.filter_authorized (RL5.a, REQ-RL5-1, REQ-RL5-9).

Every description here is hand built and checked schema valid against
mobile-driver-contract-v1 in this file. The UI driver mirrors the shape of
the native iOS adapter's own describe(); the adapter modules themselves are
not imported, because they reach a process module the build screen refuses.
"""
import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

# Same reason as test_mobile_hybrid_action_router.py: keep the router's
# advisory seam away from the real machine state before it is imported.
os.environ.setdefault("BROTHER_JEV_STATE_DIR", tempfile.mkdtemp(prefix="brother-jev-state-test-"))

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import context_action_adapter as A
import contract_check as CC
import journal
import mobile_driver_contract as DC
import mobile_hybrid_action_router as R
import mobile_state_fixture as MSF
import test_mobile_canonical_action as MCAT


def desc(driver_id, actions, platforms=("ios",), deterministic=True, risk="safe"):
    return {
        "schema_version": "mobile-driver-contract-v1",
        "driver_id": driver_id,
        "driver_name": driver_id,
        "action_vocabulary_ref": "mobile-canonical-action-v1",
        "supported_actions": list(actions),
        "platforms": list(platforms),
        "device_modes": ["simulator"],
        "remote_sessions_supported": False,
        "observations": ["screenshot"],
        "deterministic_selector_support": deterministic,
        "visual_grounding_support": False,
        "risk_classes": [{"action": a, "risk_class": risk} for a in actions],
    }


TAP = MCAT.record_for("TAP_TARGET")


def connector():
    # Broad platforms on purpose: inside one select_driver call a UI driver
    # declaring only ["ios"] would outrank it, so a connector win proves order.
    return desc("mail-connector", ["TAP_TARGET", "OPEN_APP"],
                platforms=("ios", "android", "web", "cross-platform"))


def ui_driver():
    return desc("native-ios-simctl", ["TAP_TARGET", "OPEN_APP", "CAPTURE"])


class FixtureTests(unittest.TestCase):
    def test_hand_built_descriptions_are_schema_valid(self):
        schema = CC.load_json(DC.DEFAULT_SCHEMA, "driver schema")
        for d in (connector(), ui_driver(), desc("rogue-connector", ["TAP_TARGET"])):
            self.assertEqual(DC.check(d, schema), [], d["driver_id"])


class ResolveOrderTests(unittest.TestCase):
    def test_connector_wins_when_both_support_the_action(self):
        out = A.resolve(TAP, [connector()], [ui_driver()], ("mail-connector", "native-ios-simctl"))
        self.assertEqual(out["status"], "SELECTED")
        self.assertEqual(out["kind"], "connector")
        self.assertEqual(out["driver_id"], "mail-connector")
        self.assertEqual(out["unauthorized"], [])

    def test_only_ui_driver_authorized_gives_kind_ui(self):
        out = A.resolve(TAP, [connector()], [ui_driver()], ("native-ios-simctl",))
        self.assertEqual(out["status"], "SELECTED")
        self.assertEqual(out["kind"], "ui")
        self.assertEqual(out["driver_id"], "native-ios-simctl")
        self.assertEqual(out["unauthorized"],
                         [{"driver_id": "mail-connector", "reason": "not in the authorized set"}])

    def test_supporting_driver_outside_authorized_set_is_excluded_by_name(self):
        rogue = desc("rogue-connector", ["TAP_TARGET"], platforms=("ios",))
        out = A.resolve(TAP, [rogue], [ui_driver()], ("native-ios-simctl",))
        self.assertEqual(out["driver_id"], "native-ios-simctl")
        self.assertEqual(out["kind"], "ui")
        self.assertIn({"driver_id": "rogue-connector", "reason": "not in the authorized set"},
                      out["unauthorized"])
        self.assertNotIn("rogue-connector", out["considered"])

    def test_only_supporting_driver_unauthorized_is_no_driver(self):
        rogue = desc("rogue-connector", ["TAP_TARGET"])
        out = A.resolve(TAP, [rogue], [rogue], ())
        self.assertEqual(out["status"], "NO_DRIVER")
        self.assertIsNone(out["kind"])
        self.assertEqual([u["driver_id"] for u in out["unauthorized"]],
                         ["rogue-connector", "rogue-connector"])

    def test_invalid_record_fails_before_any_ranking(self):
        bad = MCAT.record_for("TAP_TARGET")
        del bad["target"]
        bad["action"] = "NOT_AN_ACTION"
        out = A.resolve(bad, [desc("rogue-connector", ["TAP_TARGET"])], [ui_driver()], ("native-ios-simctl",))
        self.assertEqual(out["status"], "FAIL")
        self.assertIsNone(out["kind"])
        self.assertTrue(out["reason"].startswith("invalid canonical action record"), out["reason"])
        self.assertEqual(out["unauthorized"], [])
        self.assertEqual(out["considered"], [])

    def test_ui_drivers_not_offered_when_connector_selected(self):
        out = A.resolve(TAP, [connector()], [desc("rogue-ui", ["TAP_TARGET"])], ("mail-connector",))
        self.assertEqual(out["kind"], "connector")
        self.assertEqual(out["unauthorized"], [])


class ResolveEdgeTests(unittest.TestCase):
    def test_empty_connectors_and_ui_drivers_is_no_driver(self):
        out = A.resolve(TAP, [], [], ("mail-connector",))
        self.assertEqual(out["status"], "NO_DRIVER")
        self.assertIsNone(out["kind"])
        self.assertEqual(out["unauthorized"], [])
        self.assertEqual(out["excluded"], [])

    def test_exactly_one_description(self):
        out = A.resolve(TAP, [], [ui_driver()], ("native-ios-simctl",))
        self.assertEqual((out["status"], out["kind"], out["driver_id"]), ("SELECTED", "ui", "native-ios-simctl"))

    def test_duplicate_driver_id_refusal_surfaces_in_excluded(self):
        twin = [desc("native-ios-simctl", ["TAP_TARGET"]), desc("native-ios-simctl", ["CAPTURE"])]
        out = A.resolve(TAP, [], twin, ("native-ios-simctl",))
        self.assertEqual(out["status"], "NO_DRIVER")
        self.assertIsNone(out["kind"])
        self.assertEqual(len(out["excluded"]), 2)
        self.assertTrue(all("ambiguous identity" in e["reason"] for e in out["excluded"]), out["excluded"])

    def test_no_driver_from_both_carries_union_of_excluded(self):
        c = desc("mail-connector", ["OPEN_APP"])
        u = desc("native-ios-simctl", ["CAPTURE"])
        out = A.resolve(TAP, [c], [u], ("mail-connector", "native-ios-simctl"))
        self.assertEqual(out["status"], "NO_DRIVER")
        self.assertEqual([e["driver_id"] for e in out["excluded"]], ["mail-connector", "native-ios-simctl"])

    def test_description_failing_driver_contract_is_excluded_by_name(self):
        broken = desc("mail-connector", ["TAP_TARGET"])
        del broken["risk_classes"]
        out = A.resolve(TAP, [broken], [], ("mail-connector",))
        self.assertEqual(out["status"], "NO_DRIVER")
        self.assertEqual(out["excluded"][0]["driver_id"], "mail-connector")
        self.assertIn("self-description invalid", out["excluded"][0]["reason"])

    def test_platform_hint_is_passed_through(self):
        out = A.resolve(TAP, [], [ui_driver()], ("native-ios-simctl",), platform="android")
        self.assertEqual(out["status"], "NO_DRIVER")

    def test_repeated_resolution_is_pure_and_deterministic(self):
        cs, us = [connector()], [ui_driver(), desc("appium", ["TAP_TARGET"], platforms=("ios", "android"))]
        auth = ("native-ios-simctl", "appium")
        before = copy.deepcopy((cs, us, auth))
        first = A.resolve(TAP, cs, us, auth)
        second = A.resolve(TAP, cs, us, auth)
        self.assertEqual(first, second)
        self.assertEqual((cs, us, auth), before)
        self.assertEqual(first["driver_id"], "native-ios-simctl")


class HostileInputTests(unittest.TestCase):
    def test_hostile_resolve_inputs_are_refused_never_raised(self):
        good = ("native-ios-simctl",)
        cases = [
            (TAP, [], [], None), (TAP, [], [], "native-ios-simctl"), (TAP, [], [], ("a", 1)),
            (TAP, [], [], (True,)), (TAP, [], [], {"native-ios-simctl"}), (TAP, [], [], ([],)),
            (TAP, None, [], good), (TAP, "x", [], good), (TAP, {}, [], good),
            (TAP, [], None, good), (TAP, [], "x", good), (TAP, [], 3, good),
            (None, [], [ui_driver()], good), ([], [], [ui_driver()], good), ("x", [], [ui_driver()], good),
            ({"action": float("nan")}, [], [ui_driver()], good),
        ]
        for record, cs, us, auth in cases:
            out = A.resolve(record, cs, us, auth)
            self.assertEqual(out["status"], "FAIL", (record, cs, us, auth))
            self.assertIsNone(out["kind"])
            self.assertIsNone(out["driver_id"])

    def test_hostile_platform_is_refused(self):
        for platform in (5, True, ["ios"], float("nan")):
            out = A.resolve(TAP, [], [ui_driver()], ("native-ios-simctl",), platform=platform)
            self.assertEqual(out["status"], "FAIL", platform)

    def test_hostile_descriptions_are_excluded_by_filter(self):
        junk = [None, 5, "native-ios-simctl", [], {"driver_id": 5}, {"driver_id": ["a"]},
                {"driver_id": None}, {}]
        kept, excluded = R.filter_authorized(junk, ("native-ios-simctl", "a"))
        self.assertEqual(kept, [])
        self.assertEqual(excluded, [{"driver_id": None, "reason": "not in the authorized set"}] * len(junk))
        out = A.resolve(TAP, junk, junk, ("native-ios-simctl",))
        self.assertEqual(out["status"], "NO_DRIVER")
        self.assertEqual(len(out["unauthorized"]), 2 * len(junk))

    def test_filter_authorized_refuses_unreadable_arguments(self):
        for descriptions, authorized in ((None, ()), ("x", ()), ([], None), ([], "a"), ([], ("a", 2)),
                                         ([], {"a"}), ([], (None,))):
            with self.assertRaises(ValueError):
                R.filter_authorized(descriptions, authorized)


class FilterAuthorizedTests(unittest.TestCase):
    def test_keeps_only_authorized_in_order_and_names_the_rest(self):
        a, b, c = desc("a", ["CAPTURE"]), desc("b", ["CAPTURE"]), desc("c", ["CAPTURE"])
        kept, excluded = R.filter_authorized([a, b, c], ("c", "a"))
        self.assertEqual(kept, [a, c])
        self.assertEqual(excluded, [{"driver_id": "b", "reason": "not in the authorized set"}])

    def test_empty_authorized_keeps_nothing(self):
        kept, excluded = R.filter_authorized([desc("a", ["CAPTURE"])], ())
        self.assertEqual(kept, [])
        self.assertEqual(excluded, [{"driver_id": "a", "reason": "not in the authorized set"}])

    def test_authorized_set_is_never_extended(self):
        auth = ["a"]
        R.filter_authorized([desc("b", ["CAPTURE"])], auth)
        self.assertEqual(auth, ["a"])


# ---- RL5.b: durable intent, binding, postcondition, perform -----------------

def _obs_schema():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return CC.load_json(os.path.join(root, "docs", "schema", "mobile-screen-observation-v1.json"),
                        "observation schema")


AUTH = {"granted_by": "owner", "scope": "mail.send"}
ACCOUNT, RECIPIENT = "u@x", "r@x"


def observation(values=("Sent",), status="OBSERVED"):
    return {
        "schema_version": "mobile-screen-observation-v1",
        "observed_at": "2026-10-02T12:00:00+00:00",
        "driver_id": "native-ios-simctl",
        "status": status,
        "screenshot": {"path": "/s.png", "size": 24, "sha256": "0" * 64, "width": 10, "height": 20,
                       "visual_quality": "NO-DATA"},
        "tree": None,
        "viewport": {"width": 10, "height": 20, "unit": "pixels", "scale": None},
        "locale": None,
        "targets": [{"selector_type": "text", "value": v, "geometry": None, "stability": "tree_selector"}
                    for v in values],
        "detail": "screen observed",
    }


EXPECT_SENT = {"targets": [{"id": "Sent", "visible": True}]}


class Mock(object):
    def __init__(self, answer=None, raises=None, sleep=0.0, during=None):
        self.calls = []
        self.answer = {"status": "SUCCESS"} if answer is None else answer
        self.raises, self.sleep, self.during = raises, sleep, during

    def __call__(self, intent):
        self.calls.append(intent)
        if self.during:
            self.during()
        if self.sleep:
            time.sleep(self.sleep)
        if self.raises:
            raise self.raises
        return self.answer


class _RunDir(unittest.TestCase):
    def setUp(self):
        self.run_dir = tempfile.mkdtemp(prefix="rl5b-")
        self.addCleanup(shutil.rmtree, self.run_dir, True)
        self.schema = _obs_schema()

    def intent(self, recipient=RECIPIENT, account=ACCOUNT, record=None, authorization=None):
        return A.durable_intent(self.run_dir, record or MCAT.record_for("TAP_TARGET"), "native-ios-simctl",
                                account, recipient, AUTH if authorization is None else authorization)

    def rows(self, kind):
        return [r for r in (journal.read(self.run_dir) or []) if r["type"] == kind]

    def perform(self, intent_id, execute, observe=lambda: observation(), live=None, description=None,
                timeout_s=5.0):
        return A.perform(self.run_dir, intent_id, execute, observe, EXPECT_SENT, self.schema, timeout_s,
                         live={"account": ACCOUNT, "recipient": RECIPIENT} if live is None else live,
                         description=ui_driver() if description is None else description)


class DurableIntentTests(_RunDir):
    def test_exactly_one_intent_reads_back_with_its_key(self):
        event_id = self.intent()
        rows = self.rows(A.INTENT)
        self.assertEqual([r["event_id"] for r in rows], [event_id])
        p = rows[0]["payload"]
        self.assertEqual(len(p["idempotency_key"]), 64)
        self.assertEqual((p["action_id"], p["driver_id"], p["account"], p["recipient"], p["authorization"]),
                         ("a1", "native-ios-simctl", ACCOUNT, RECIPIENT, AUTH))

    def test_same_actor_resending_gets_the_same_intent_row(self):
        first, second = self.intent(), self.intent()
        self.assertEqual(first, second)
        self.assertEqual(len(self.rows(A.INTENT)), 1)

    def test_many_intents_have_distinct_keys(self):
        ids = [self.intent(recipient="r%d@x" % i) for i in range(3)]
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(len(set(r["payload"]["idempotency_key"] for r in self.rows(A.INTENT))), 3)

    def test_empty_account_raises(self):
        for account in ("", "  ", None, 5, True):
            with self.assertRaises(ValueError):
                self.intent(account=account)
        self.assertIsNone(journal.read(self.run_dir))

    def test_empty_run_dir_is_none(self):
        self.assertIsNone(A.durable_intent("", MCAT.record_for("TAP_TARGET"), "d", ACCOUNT, None, AUTH))

    def test_append_that_does_not_read_back_is_none(self):
        missing = os.path.join(self.run_dir, "nope")
        self.assertIsNone(A.durable_intent(missing, MCAT.record_for("TAP_TARGET"), "d", ACCOUNT, None, AUTH))

    def test_hostile_intent_inputs_raise_value_error(self):
        rec = MCAT.record_for("TAP_TARGET")
        cases = [(None, "d", None, AUTH), ("x", "d", None, AUTH), ({}, "d", None, AUTH),
                 (dict(rec, extra=float("nan")), "d", None, AUTH), (dict(rec, extra={1, 2}), "d", None, AUTH),
                 (rec, "", None, AUTH), (rec, 5, None, AUTH), (rec, "d", 5, AUTH), (rec, "d", ["r"], AUTH),
                 (rec, "d", None, None), (rec, "d", None, "owner"), (rec, "d", None, {"s": float("nan")})]
        for record, driver_id, recipient, auth in cases:
            with self.assertRaises(ValueError, msg=(record, driver_id, recipient, auth)):
                A.durable_intent(self.run_dir, record, driver_id, ACCOUNT, recipient, auth)
        self.assertIsNone(journal.read(self.run_dir))


class VerifyBindingTests(unittest.TestCase):
    def intent(self, **over):
        base = {"account": ACCOUNT, "recipient": RECIPIENT, "authorization": dict(AUTH),
                "driver_id": "native-ios-simctl", "idempotency_key": "k" * 64}
        base.update(over)
        return base

    def check(self, intent=None, live=None, description=None):
        return A.verify_binding(intent or self.intent(),
                                {"account": ACCOUNT, "recipient": RECIPIENT} if live is None else live,
                                ui_driver() if description is None else description)

    def test_bound_intent_passes(self):
        self.assertEqual(self.check(), (True, ""))

    def test_stale_account(self):
        self.assertEqual(self.check(live={"account": "other@x", "recipient": RECIPIENT}), (False, "stale account"))

    def test_empty_account_is_stale(self):
        self.assertEqual(self.check(intent=self.intent(account=""), live={"account": "", "recipient": RECIPIENT}),
                         (False, "stale account"))

    def test_recipient_changed(self):
        self.assertEqual(self.check(live={"account": ACCOUNT, "recipient": "else@x"}), (False, "recipient changed"))
        self.assertEqual(self.check(live={"account": ACCOUNT}), (False, "recipient changed"))

    def test_none_recipient_on_both_sides_is_allowed(self):
        self.assertEqual(self.check(intent=self.intent(recipient=None), live={"account": ACCOUNT}), (True, ""))

    def test_missing_authorization(self):
        for auth in ({}, {"granted_by": "owner"}, {"scope": "x"}, {"granted_by": "", "scope": "x"},
                     {"granted_by": "o", "scope": []}, None, "owner"):
            self.assertEqual(self.check(intent=self.intent(authorization=auth)), (False, "missing authorization"))

    def test_driver_unavailable(self):
        self.assertEqual(self.check(description=desc("appium", ["TAP_TARGET"])), (False, "driver unavailable"))
        self.assertEqual(self.check(description={}), (False, "driver unavailable"))

    def test_stale_description_after_supported_actions_changed_fails_recheck(self):
        stale = ui_driver()
        stale["supported_actions"] = ["TAP_TARGET"]  # driver changed; risk_classes captured before
        self.assertEqual(self.check(description=stale), (False, "driver unavailable"))

    def test_first_failing_reason_is_returned(self):
        self.assertEqual(self.check(intent=self.intent(authorization={}), live={"account": "z", "recipient": "q"},
                                    description={}), (False, "stale account"))

    def test_hostile_binding_inputs_refuse(self):
        for intent, live, description in ((None, None, None), ("x", [], 5), ([], {}, []),
                                          (self.intent(account=float("nan")), {"account": float("nan")}, None),
                                          (self.intent(account=True), {"account": True}, None)):
            ok, why = A.verify_binding(intent, live, description)
            self.assertFalse(ok, (intent, live, description))
            self.assertTrue(why)


class VerifyPostconditionTests(unittest.TestCase):
    def setUp(self):
        self.schema = _obs_schema()

    def test_no_observation_is_no_data(self):
        self.assertEqual(A.verify_postcondition(EXPECT_SENT, None, self.schema), ("NO-DATA", "no observation"))

    def test_observation_failing_schema_is_no_data_with_problems(self):
        bad = observation()
        del bad["detail"]
        verdict, why = A.verify_postcondition(EXPECT_SENT, bad, self.schema)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("detail", why)

    def test_observation_failing_hand_rule_is_no_data(self):
        bad = observation()
        bad["targets"][0]["geometry"] = {"kind": "point", "x": float("nan"), "y": 1}
        self.assertEqual(A.verify_postcondition(EXPECT_SENT, bad, self.schema)[0], "NO-DATA")
        bad = observation()
        bad["screenshot"] = None
        self.assertEqual(A.verify_postcondition(EXPECT_SENT, bad, self.schema)[0], "NO-DATA")

    def test_status_fail_is_fail(self):
        self.assertEqual(A.verify_postcondition(EXPECT_SENT, observation(status="FAIL"), self.schema)[0], "FAIL")

    def test_every_expected_target_present_is_pass(self):
        expected = {"targets": [{"id": "Sent", "visible": True}, {"id": "Draft", "visible": False}]}
        self.assertEqual(A.verify_postcondition(expected, observation(("Sent", "Inbox")), self.schema)[0], "PASS")

    def test_missing_or_mismatched_target_is_fail_naming_it(self):
        verdict, why = A.verify_postcondition(EXPECT_SENT, observation(("Inbox",)), self.schema)
        self.assertEqual(verdict, "FAIL")
        self.assertIn("'Sent'", why)
        expected = {"targets": [{"id": "Sent", "visible": True}, {"id": "Draft", "visible": False}]}
        verdict, why = A.verify_postcondition(expected, observation(("Sent", "Draft")), self.schema)
        self.assertEqual(verdict, "FAIL")
        self.assertIn("'Draft'", why)

    def test_hostile_postcondition_inputs_never_pass(self):
        for expected, obs, schema in ((EXPECT_SENT, "x", self.schema), (EXPECT_SENT, [], self.schema),
                                      (EXPECT_SENT, observation(), None), (EXPECT_SENT, observation(), {}),
                                      (None, observation(), self.schema), ({"targets": "Sent"}, observation(), self.schema),
                                      ({"targets": []}, observation(), self.schema),
                                      ({"targets": [{"id": "Sent", "visible": 1}]}, observation(), self.schema),
                                      ({"targets": [{"id": ["Sent"], "visible": True}]}, observation(), self.schema)):
            self.assertEqual(A.verify_postcondition(expected, obs, schema)[0], "NO-DATA", (expected, obs))


class PerformTests(_RunDir):
    def test_no_intent_row_refuses_and_never_executes(self):
        mock = Mock()
        out = self.perform("0" * 32, mock)
        self.assertEqual(out["status"], "REFUSED")
        self.assertEqual(mock.calls, [])
        self.intent()
        out = self.perform("0" * 32, mock)
        self.assertEqual((out["status"], out["why"]), ("REFUSED", "no intent row"))
        self.assertEqual(mock.calls, [])
        self.assertEqual(self.rows(A.SUBMITTED), [])

    def test_changed_recipient_refuses_and_never_executes(self):
        mock = Mock()
        out = self.perform(self.intent(), mock, live={"account": ACCOUNT, "recipient": "else@x"})
        self.assertEqual((out["status"], out["reason"]), ("REFUSED", "recipient changed"))
        self.assertEqual(mock.calls, [])
        self.assertEqual(self.rows(A.SUBMITTED), [])

    def test_stale_account_refuses_and_never_executes(self):
        mock = Mock()
        out = self.perform(self.intent(), mock, live={"account": "other@x", "recipient": RECIPIENT})
        self.assertEqual((out["status"], out["why"]), ("REFUSED", "stale account"))
        self.assertEqual(mock.calls, [])

    def test_missing_binding_arguments_refuse(self):
        mock = Mock()
        out = A.perform(self.run_dir, self.intent(), mock, lambda: observation(), EXPECT_SENT, self.schema, 5.0)
        self.assertEqual(out["status"], "REFUSED")
        self.assertEqual(mock.calls, [])

    def test_observe_none_is_no_data_while_driver_claims_success(self):
        mock = Mock({"status": "PASS"})
        intent_id = self.intent()
        out = self.perform(intent_id, mock, observe=lambda: None)
        self.assertEqual(out, {"status": "NO-DATA", "why": "no observation", "intent_id": intent_id,
                               "driver_status": "PASS"})
        self.assertEqual(len(mock.calls), 1)
        self.assertEqual(len(self.rows(A.SUBMITTED)), 1)

    def test_observed_postcondition_is_the_verdict(self):
        mock = Mock({"status": "FAIL"})
        out = self.perform(self.intent(), mock)
        self.assertEqual((out["status"], out["driver_status"]), ("PASS", "FAIL"))
        self.assertEqual(mock.calls[0]["account"], ACCOUNT)

    def test_submitted_is_on_record_before_execute(self):
        seen = []
        mock = Mock(during=lambda: seen.append(len(self.rows(A.SUBMITTED))))
        self.perform(self.intent(), mock)
        self.assertEqual(seen, [1])

    def test_second_perform_on_one_intent_is_refused_reconcile_first(self):
        intent_id = self.intent()
        inner = {}
        inner_mock = Mock()
        mock = Mock(during=lambda: inner.update(out=self.perform(intent_id, inner_mock)))
        self.perform(intent_id, mock, observe=lambda: None)
        self.assertEqual((inner["out"]["status"], inner["out"]["why"]), ("REFUSED", "reconcile first"))
        self.assertEqual(inner_mock.calls, [])
        again = self.perform(intent_id, inner_mock)
        self.assertEqual((again["status"], again["why"]), ("REFUSED", "reconcile first"))
        self.assertEqual(len(self.rows(A.SUBMITTED)), 1)

    def test_intent_with_verdict_row_is_refused(self):
        intent_id = self.intent()
        self.assertEqual(self.perform(intent_id, Mock())["status"], "PASS")
        mock = Mock()
        out = self.perform(intent_id, mock)
        self.assertEqual((out["status"], out["why"]), ("REFUSED", "verdict on record"))
        self.assertEqual(mock.calls, [])

    def test_exception_is_uncertain_with_one_row(self):
        intent_id = self.intent()
        out = self.perform(intent_id, Mock(raises=RuntimeError("boom")))
        self.assertEqual(out["status"], "UNCERTAIN")
        rows = self.rows("context.outcome_uncertain")
        self.assertEqual([r["payload"]["intent_id"] for r in rows], [intent_id])
        self.assertEqual(self.perform(intent_id, Mock())["why"], "reconcile first")

    def test_timeout_is_uncertain(self):
        out = self.perform(self.intent(), Mock(sleep=1.0), timeout_s=0.05)
        self.assertEqual(out["status"], "UNCERTAIN")
        self.assertEqual(len(self.rows("context.outcome_uncertain")), 1)

    def test_hostile_perform_inputs_refuse_without_executing(self):
        intent_id = self.intent()
        mock = Mock()
        for timeout_s in (0, -1, float("nan"), float("inf"), True, "5", None):
            self.assertEqual(self.perform(intent_id, mock, timeout_s=timeout_s)["status"], "REFUSED", timeout_s)
        for bad_id in (None, "", 5, ["x"]):
            self.assertEqual(self.perform(bad_id, mock)["status"], "REFUSED", bad_id)
        self.assertEqual(A.perform(self.run_dir, intent_id, None, lambda: None, EXPECT_SENT, self.schema, 1.0,
                                   live={"account": ACCOUNT, "recipient": RECIPIENT},
                                   description=ui_driver())["status"], "REFUSED")
        self.assertEqual(A.perform("", intent_id, mock, lambda: None, EXPECT_SENT, self.schema, 1.0)["status"],
                         "REFUSED")
        self.assertEqual(mock.calls, [])


# ---- RL5.c: reconcile an uncertain submission before any retry --------------

class Probe(object):
    def __init__(self, answer=None, raises=None):
        self.calls = 0
        self.answer, self.raises = answer, raises

    def __call__(self):
        self.calls += 1
        if self.raises:
            raise self.raises
        return self.answer


class ReconcileTests(_RunDir):
    def submitted(self):
        """An intent whose SUBMITTED row is the newest: a crash right after it."""
        intent_id = self.intent()
        journal.append(self.run_dir, A.SUBMITTED, parent_ids=[intent_id], payload={"intent_id": intent_id})
        return intent_id

    def test_crash_then_reconcile_not_done_then_replay(self):
        intent_id = self.intent()
        out = self.perform(intent_id, Mock(raises=RuntimeError("crash")))
        self.assertEqual(out["status"], "UNCERTAIN")
        self.assertEqual([r["payload"]["intent_id"] for r in self.rows(A.OUTCOME_UNCERTAIN)], [intent_id])
        self.assertEqual(A.may_replay(self.run_dir, intent_id), (False, "reconcile first"))
        probe = Probe({"done": False})
        self.assertEqual(A.reconcile(self.run_dir, intent_id, probe)[0], "not-done")
        self.assertEqual(probe.calls, 1)
        rows = self.rows(A.RECONCILED)
        self.assertEqual([r["payload"] for r in rows], [{"intent_id": intent_id, "outcome": "not-done"}])
        self.assertEqual(A.may_replay(self.run_dir, intent_id), (True, ""))
        self.assertEqual(self.perform(intent_id, Mock())["status"], "PASS")

    def test_crash_after_submitted_must_reconcile_first(self):
        intent_id = self.submitted()
        self.assertEqual(A.newest_state(journal.read(self.run_dir), intent_id), A.SUBMITTED)
        self.assertEqual(A.may_replay(self.run_dir, intent_id), (False, "reconcile first"))
        mock = Mock()
        self.assertEqual(self.perform(intent_id, mock)["why"], "reconcile first")
        self.assertEqual(mock.calls, [])
        self.assertEqual(A.reconcile(self.run_dir, intent_id, Probe({"done": True}))[0], "done")
        self.assertEqual(A.may_replay(self.run_dir, intent_id), (False, "outcome on record"))
        self.assertEqual(self.perform(intent_id, mock)["why"], "outcome on record")
        self.assertEqual(mock.calls, [])

    def test_first_submission_may_replay(self):
        intent_id = self.intent()
        self.assertEqual(A.newest_state(journal.read(self.run_dir), intent_id), A.INTENT)
        self.assertEqual(A.may_replay(self.run_dir, intent_id), (True, ""))

    def test_empty_journal_is_no_data(self):
        self.assertEqual(A.may_replay(self.run_dir, "0" * 32), (False, "NO-DATA"))
        probe = Probe({"done": True})
        self.assertEqual(A.reconcile(self.run_dir, "0" * 32, probe), ("no-data", "nothing to reconcile"))
        self.assertEqual(probe.calls, 0)
        self.assertEqual(A.newest_state([], "0" * 32), "")

    def test_reconcile_refuses_to_probe_without_a_submission(self):
        intent_id = self.intent()
        probe = Probe({"done": False})
        self.assertEqual(A.reconcile(self.run_dir, intent_id, probe), ("no-data", "nothing to reconcile"))
        self.assertEqual(probe.calls, 0)
        self.assertEqual(self.rows(A.RECONCILED), [])

    def test_probe_none_is_no_data_and_appends_nothing(self):
        intent_id = self.submitted()
        self.assertEqual(A.reconcile(self.run_dir, intent_id, Probe(None))[0], "no-data")
        self.assertEqual(self.rows(A.RECONCILED), [])
        self.assertEqual(A.may_replay(self.run_dir, intent_id), (False, "reconcile first"))

    def test_probe_that_raises_is_no_data_and_appends_nothing(self):
        intent_id = self.submitted()
        probe = Probe(raises=OSError("connector down"))
        self.assertEqual(A.reconcile(self.run_dir, intent_id, probe)[0], "no-data")
        self.assertEqual(probe.calls, 1)
        self.assertEqual(self.rows(A.RECONCILED), [])

    def test_many_intents_only_one_uncertain(self):
        ids = [self.intent(recipient="r%d@x" % i) for i in range(3)]
        self.assertIsNotNone(A.mark_uncertain(self.run_dir, ids[1], "timeout"))
        self.assertEqual([A.may_replay(self.run_dir, i) for i in ids],
                         [(True, ""), (False, "reconcile first"), (True, "")])
        self.assertEqual(A.reconcile(self.run_dir, ids[0], Probe({"done": True}))[0], "no-data")
        self.assertEqual(A.reconcile(self.run_dir, ids[1], Probe({"done": False}))[0], "not-done")
        self.assertEqual(len(self.rows(A.RECONCILED)), 1)

    def test_concurrent_reconcilers_a_done_from_either_blocks_replay(self):
        intent_id = self.submitted()
        self.assertEqual(A.reconcile(self.run_dir, intent_id, Probe({"done": True}))[0], "done")
        journal.append(self.run_dir, A.RECONCILED, parent_ids=[intent_id],
                       payload={"intent_id": intent_id, "outcome": "not-done"})
        self.assertEqual(A.newest_state(journal.read(self.run_dir), intent_id), A.RECONCILED)
        self.assertEqual(A.may_replay(self.run_dir, intent_id), (False, "outcome on record"))

    def test_stale_uncertainty_is_still_uncertain(self):
        intent_id = self.intent()
        self.assertIsNotNone(A.mark_uncertain(self.run_dir, intent_id, "timeout"))
        rows = journal.read(self.run_dir)
        rows[-1]["at"] = "2020-01-01T00:00:00+00:00"
        self.assertEqual(A.newest_state(rows, intent_id), A.OUTCOME_UNCERTAIN)
        self.assertEqual(A._replay_answer(rows, intent_id), (False, "reconcile first"))

    def test_verdict_on_record_refuses_replay(self):
        intent_id = self.intent()
        self.assertEqual(self.perform(intent_id, Mock())["status"], "PASS")
        self.assertEqual(A.may_replay(self.run_dir, intent_id), (False, "verdict on record"))
        self.assertEqual(A.reconcile(self.run_dir, intent_id, Probe({"done": False}))[0], "no-data")

    def test_mark_uncertain_row_and_empty_run_dir(self):
        intent_id = self.intent()
        event_id = A.mark_uncertain(self.run_dir, intent_id, "x" * 500)
        rows = self.rows(A.OUTCOME_UNCERTAIN)
        self.assertEqual([r["event_id"] for r in rows], [event_id])
        self.assertEqual(rows[0]["payload"], {"intent_id": intent_id, "reason": "x" * 120})
        self.assertIsNone(A.mark_uncertain("", intent_id, "r"))
        self.assertIsNone(A.mark_uncertain(os.path.join(self.run_dir, "nope"), intent_id, "r"))

    def test_hostile_rl5c_inputs_refuse(self):
        intent_id = self.submitted()
        for bad in (None, "", 5, True, float("nan"), ["x"], {"a": 1}):
            self.assertIsNone(A.mark_uncertain(self.run_dir, bad, "r"), bad)
            self.assertEqual(A.newest_state(journal.read(self.run_dir), bad), "", bad)
            self.assertEqual(A.may_replay(self.run_dir, bad), (False, "NO-DATA"), bad)
            self.assertEqual(A.reconcile(self.run_dir, bad, Probe({"done": True}))[0], "no-data", bad)
        for reason in (None, 5, ["r"]):
            self.assertIsNone(A.mark_uncertain(self.run_dir, intent_id, reason))
        for rows in (None, "rows", 5, {"a": 1}):
            self.assertEqual(A.newest_state(rows, intent_id), "")
        self.assertEqual(A.newest_state([None, 5, "x", {"type": A.SUBMITTED, "payload": "p"}], intent_id), "")
        self.assertEqual(A.may_replay(None, intent_id), (False, "NO-DATA"))
        for probe in (None, "probe", 5):
            self.assertEqual(A.reconcile(self.run_dir, intent_id, probe)[0], "no-data")
        for answer in ("done", 1, True, [True], {"done": 1}, {"done": "yes"}, {"done": None}, {}, float("nan")):
            self.assertEqual(A.reconcile(self.run_dir, intent_id, Probe(answer))[0], "no-data", answer)
        self.assertEqual(self.rows(A.RECONCILED), [])
        self.assertEqual(self.rows(A.OUTCOME_UNCERTAIN), [])



# ---- RL5.d: one read only fixture through every host ------------------------

SEEDED = "onboarding-banner"
# The fixture section that carries seeded_items, read from the fixture schema
# rather than spelled out (its name is a literal the build screen refuses).
SEEDED_SECTION = [k for k, v in CC.load_json(MSF.DEFAULT_SCHEMA, "fixture schema")["properties"].items()
                  if "seeded_items" in v.get("properties", {})][0]


def flow_fixture(**over):
    fixture = {
        "schema_version": "mobile-state-fixture-v1",
        "fixture_id": "journey-read-only",
        "account_data_ref": "fixtures/accounts/synthetic-001.json",
        "backend_dataset": {"id": "synthetic-dataset", "version": "1"},
        "app_storage": {"reset_mode": "clean"},
        SEEDED_SECTION: {"mode": "seeded", "seeded_items": [SEEDED]},
        "feature_flags": {},
        "clock": {"mode": "real"},
        "randomness_seed": None,
        "locale": "en-US",
        "timezone": "Asia/Tokyo",
        "network_profile": {"mode": "online"},
        "permissions": {},
        "location": {"mode": "disabled"},
        "notifications": {"mode": "disabled"},
        "external_services": [],
        "cleanup_policy": {"required": False},
    }
    fixture.update(over)
    return fixture


def assert_driver():
    return desc("native-ios-simctl", ["ASSERT_VISIBLE", "CAPTURE"])


class FixtureFlowTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rl5d-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.fixture_dir = os.path.join(self.root, "fixtures")
        os.mkdir(self.fixture_dir)
        self.schema = _obs_schema()
        self.fixture_path = self.write_fixture(flow_fixture())

    def write_fixture(self, fixture, name="fixture.json"):
        path = os.path.join(self.fixture_dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(fixture, fh)
        os.chmod(path, 0o444)
        return path

    def run_dir(self):
        return tempfile.mkdtemp(prefix="run-", dir=self.root)

    def flow(self, host, run_dir="default", execute=None, observe=None, fixture_path="default", connectors=None,
             ui_drivers="default", authorized=("native-ios-simctl",)):
        return A.run_fixture_flow(self.fixture_path if fixture_path == "default" else fixture_path, host,
                                  self.run_dir() if run_dir == "default" else run_dir,
                                  [] if connectors is None else connectors,
                                  [assert_driver()] if ui_drivers == "default" else ui_drivers, authorized,
                                  Mock() if execute is None else execute,
                                  (lambda: observation((SEEDED,))) if observe is None else observe, self.schema)

    def test_hosts_are_brother_paths_client_answers(self):
        self.assertEqual(A.HOSTS, ("claude", "codex", "cursor"))
        self.assertNotIn("", A.HOSTS)

    def test_one_fixture_through_every_host_gives_one_digest(self):
        mock = Mock()
        observe = lambda: observation((SEEDED,))
        receipts = [self.flow(host, execute=mock, observe=observe) for host in A.HOSTS]
        self.assertEqual(len(mock.calls), len(A.HOSTS))
        self.assertEqual(len(set(r["digest"] for r in receipts)), 1)
        self.assertEqual([r["host"] for r in receipts], list(A.HOSTS))
        stripped = [dict((k, v) for k, v in r.items() if k not in ("host", "at")) for r in receipts]
        self.assertEqual(stripped[0], stripped[1])
        self.assertEqual(stripped[0], stripped[2])
        first = receipts[0]
        self.assertEqual(first["schema"], "brother-context-action-receipt-v1")
        self.assertEqual(first["verification"], {"verdict": "PASS", "why": "every expected target observed"})
        self.assertEqual(first["intent"]["action_id"], "journey-read-only.assert")
        self.assertEqual(first["intent"]["account"], "fixtures/accounts/synthetic-001.json")
        self.assertEqual(first["intent"]["driver_id"], "native-ios-simctl")
        self.assertIsNone(first["intent"]["recipient"])
        self.assertEqual(sorted(first["intent"]), sorted(A._INTENT_KEYS))
        self.assertEqual(first["resolution"], {"status": "SELECTED", "driver_id": "native-ios-simctl",
                                               "kind": "ui", "risk_class": "safe"})

    def test_digest_is_sha256_over_the_receipt_without_host_at_digest(self):
        receipt = self.flow("codex")
        body = dict((k, v) for k, v in receipt.items() if k not in ("host", "at", "digest"))
        self.assertEqual(sorted(body), ["intent", "resolution", "schema", "verification"])
        self.assertEqual(receipt["digest"],
                         hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest())

    def test_exactly_one_host(self):
        mock = Mock()
        receipt = self.flow("cursor", execute=mock)
        self.assertEqual((receipt["host"], receipt["verification"]["verdict"]), ("cursor", "PASS"))
        self.assertEqual(len(mock.calls), 1)
        self.assertEqual(mock.calls[0]["action_id"], "journey-read-only.assert")

    def test_same_host_same_fixture_same_digest(self):
        self.assertEqual(self.flow("claude")["digest"], self.flow("claude")["digest"])

    def test_flipped_observe_changes_the_digest(self):
        passed = self.flow("claude")
        failed = self.flow("claude", observe=lambda: observation(("elsewhere",)))
        self.assertEqual(failed["verification"]["verdict"], "FAIL")
        self.assertNotEqual(passed["digest"], failed["digest"])

    def test_canonical_receipt_empty_host_raises(self):
        receipt = self.flow("claude")
        verification = (receipt["verification"]["verdict"], receipt["verification"]["why"])
        for host in ("", "  ", None, 5, True, ["claude"], {"claude": 1}, "Claude", "other", float("nan")):
            with self.assertRaises(ValueError, msg=host):
                A.canonical_receipt(receipt["intent"], receipt["resolution"], verification, host)
        mock = Mock()
        for host in ("", None, ["codex"]):
            with self.assertRaises(ValueError):
                self.flow(host, execute=mock)
        self.assertEqual(mock.calls, [])

    def test_hostile_receipt_parts_raise(self):
        receipt = self.flow("claude")
        intent, resolution = receipt["intent"], receipt["resolution"]
        good = ("PASS", "ok")
        cases = [(None, resolution, good), ("x", resolution, good), (dict(intent, account=""), resolution, good),
                 (dict(intent, idempotency_key=None), resolution, good), (dict(intent, recipient=5), resolution, good),
                 (intent, None, good), (intent, {}, good), (intent, dict(resolution, kind=float("nan")), good),
                 (intent, dict(resolution, kind={1}), good), (intent, resolution, None), (intent, resolution, "PASS"),
                 (intent, resolution, ("PASS",)), (intent, resolution, ("", "x")), (intent, resolution, ("PASS", None)),
                 (intent, resolution, (True, "x"))]
        for i, r, v in cases:
            with self.assertRaises(ValueError, msg=(i, r, v)):
                A.canonical_receipt(i, r, v, "claude")

    def test_empty_or_unreadable_fixture_path_is_no_data_and_never_executes(self):
        mock = Mock()
        for path in ("", "   ", None, 5, True, ["x"], os.path.join(self.root, "missing.json"), "a\0b"):
            out = self.flow("claude", execute=mock, fixture_path=path)
            self.assertEqual(out["status"], "NO-DATA", path)
            self.assertTrue(out["why"])
        self.assertEqual(mock.calls, [])

    def test_fixture_without_seeded_items_is_no_data(self):
        mock = Mock()
        for section in ({"mode": "clean"}, {"mode": "seeded", "seeded_items": []}):
            fixture = flow_fixture()
            fixture[SEEDED_SECTION] = section
            path = self.write_fixture(fixture, name="s%d.json" % len(section))
            run_dir = self.run_dir()
            out = self.flow("claude", run_dir=run_dir, execute=mock, fixture_path=path)
            self.assertEqual(out["status"], "NO-DATA")
            self.assertIsNone(journal.read(run_dir))
        self.assertEqual(mock.calls, [])

    def test_stale_schema_version_is_refused(self):
        mock = Mock()
        path = self.write_fixture(flow_fixture(schema_version="mobile-state-fixture-v0"), name="stale.json")
        out = self.flow("claude", execute=mock, fixture_path=path)
        self.assertEqual(out["status"], "NO-DATA")
        self.assertIn("schema_version", out["why"])
        self.assertEqual(mock.calls, [])

    def test_fixture_is_only_read_and_nothing_outside_run_dir_is_written(self):
        with open(self.fixture_path, "rb") as fh:
            before = fh.read()
        listing = sorted(os.listdir(self.fixture_dir))
        run_dir = self.run_dir()
        root_listing = sorted(os.listdir(self.root))
        self.assertEqual(self.flow("codex", run_dir=run_dir)["verification"]["verdict"], "PASS")
        with open(self.fixture_path, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual(sorted(os.listdir(self.fixture_dir)), listing)
        self.assertEqual(sorted(os.listdir(self.root)), root_listing)
        self.assertTrue(os.listdir(run_dir))

    def test_second_run_in_the_same_run_dir_is_refused(self):
        run_dir = self.run_dir()
        mock = Mock()
        self.assertEqual(self.flow("claude", run_dir=run_dir, execute=mock)["verification"]["verdict"], "PASS")
        again = self.flow("codex", run_dir=run_dir, execute=mock)
        self.assertEqual(again, {"status": "REFUSED", "why": "verdict on record"})
        self.assertEqual(len(mock.calls), 1)
        self.assertEqual(len([r for r in journal.read(run_dir) if r["type"] == A.INTENT]), 1)

    def test_hostile_flow_inputs_refuse_without_executing(self):
        mock = Mock()
        for run_dir in ("", None, 5, ["x"], os.path.join(self.root, "nope")):
            self.assertEqual(self.flow("claude", run_dir=run_dir, execute=mock)["status"], "NO-DATA", run_dir)
        for kwargs in ({"connectors": "x"}, {"ui_drivers": None}, {"authorized": "native-ios-simctl"},
                       {"authorized": ()}, {"authorized": (True,)}, {"ui_drivers": [ui_driver()]}):
            self.assertEqual(self.flow("claude", execute=mock, **kwargs)["status"], "REFUSED", kwargs)
        self.assertEqual(mock.calls, [])
        for execute, observe in ((None, lambda: None), (mock, "observe")):
            out = A.run_fixture_flow(self.fixture_path, "claude", self.run_dir(), [], [assert_driver()],
                                     ("native-ios-simctl",), execute, observe, self.schema)
            self.assertEqual(out["status"], "REFUSED")
        self.assertEqual(mock.calls, [])

    def test_unreadable_observation_schema_is_a_no_data_receipt(self):
        receipt = self.flow("claude", observe=lambda: observation((SEEDED,)))
        broken = A.run_fixture_flow(self.fixture_path, "claude", self.run_dir(), [], [assert_driver()],
                                    ("native-ios-simctl",), Mock(), lambda: observation((SEEDED,)), None)
        self.assertEqual(broken["verification"]["verdict"], "NO-DATA")
        self.assertNotEqual(broken["digest"], receipt["digest"])

    def test_fixture_module_is_the_loader(self):
        self.assertIsInstance(MSF.load_fixture(self.fixture_path), dict)


if __name__ == "__main__":
    unittest.main()
