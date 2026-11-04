#!/usr/bin/env python3
"""OUT1.h: the real second opinion tools, run through the door against files these cases can see change.

Builds on the fixtures in test_jev_door_ops.py (a throwaway HOME, a copy of the runtime files as an installed layout,
a fixture bridge that counts its calls). Nothing here asks a provider: the default bridge path resolves under the
throwaway HOME, where none exists, and the only bridge is the counting fixture.

Status reads and asks nothing (no bridge call, no file written or created, no mode or config changed); the effective
mode it reports agrees with what the real jev_seam.consult does; each of off, a missing registry, a privacy refusal,
an expired or missing promotion, a missing calibration, a canary failure and a timeout is shown as it stands and
leaves the check verdict and authority untouched.

Run: python3 -B scripts/test_jev_door_real.py
"""
import json
import os
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_jev_door_ops as ops  # noqa: E402  (sets the throwaway HOME before any product import)
from test_jev_door_ops import Base, TODAY, promotion_record, snapshot  # noqa: E402
import jev_calibration  # noqa: E402
import jev_decide  # noqa: E402
import jev_registry  # noqa: E402
import jev_seam  # noqa: E402
import jev_status  # noqa: E402

setUpModule = ops.setUpModule


def now():
    return datetime.now(timezone.utc).isoformat().split("+")[0] + "Z"


class StatusReadsAndAsksNothing(Base):
    """Discovery is a read. Run for real, through the door."""

    def test_status_is_a_pass_asks_the_bridge_nothing_and_changes_no_file(self):
        self.use_bridge()
        before = (snapshot(self.state), snapshot(os.path.join(self.runtime, "data")), snapshot(ops._HOME))
        got = self.ask("calibrated.status")
        self.assertEqual(("COMPLETE", "PASS"), (got["state"], got["verdict"]), got)
        self.assertEqual(0, self.bridge_calls(), "discovery must not reach the bridge")
        self.assertEqual(before, (snapshot(self.state), snapshot(os.path.join(self.runtime, "data")),
                                  snapshot(ops._HOME)))
        self.assertFalse(os.path.exists(self.ledger), "a read creates no folder")

    def test_status_enables_no_seam_it_reports_the_config_as_it_stands(self):
        self.write_seams(J065="off", J102="shadow")
        got = self.ask("calibrated.status")
        seams = {r["id"]: r for r in got["state_detail"]["seams"]}
        self.assertEqual(("off", "off", False), (seams["J065"]["configured_mode"], seams["J065"]["effective_mode"],
                                                 seams["J065"]["eligible_now"]))
        with open(self.seams, encoding="utf-8") as fh:
            self.assertEqual("off", json.load(fh)["modes"]["J065"])

    def test_status_never_calls_the_seam_or_the_model_in_process_either(self):
        with mock.patch.object(jev_seam, "consult", side_effect=AssertionError("asked")), \
                mock.patch.object(jev_decide, "decide", side_effect=AssertionError("asked")), \
                mock.patch.object(jev_decide, "_default_runner", side_effect=AssertionError("asked")):
            state = self.state_now()
        self.assertTrue(state["readable"])
        self.assertIs(False, state["acted"])

    def test_each_seam_says_what_it_does_in_plain_words_and_shadow_is_never_an_action(self):
        self.use_bridge()
        self.write_seams(J065="shadow", J102="advise", J030="act")
        state = self.state_now()
        for r in state["seams"]:
            self.assertIn("deterministic", r["fallback"])
            self.assertTrue(r["line"].endswith("recorded, not acted on"), r["line"])
        shadow, advise, act = (self.seam(state, i) for i in ("J065", "J102", "J030"))
        self.assertIs(False, shadow["may_act"])
        self.assertIn("takes no action", shadow["operates"])
        self.assertIn("takes no action", advise["operates"])
        self.assertNotIn("automatic", shadow["operates"] + advise["operates"])
        self.assertIs(False, act["may_act"], "act without a signed promotion cannot act")
        self.assertIn("escalates", act["operates"])
        for line in state["lines"]:
            self.assertTrue(line.endswith("recorded, not acted on"), line)

    def test_a_registry_row_with_no_mode_is_an_intended_use_not_an_available_route(self):
        state = self.state_now()
        self.assertEqual({"J065", "J102"}, {r["id"] for r in state["seams"]})
        self.assertGreater(state["intended_uses_not_configured"], 100)
        self.assertTrue(any("intended uses, not available routes" in line for line in state["lines"]))

    def test_a_corrupt_missing_or_wrong_typed_input_reads_no_data_never_a_safe_default(self):
        damages = (("missing registry", lambda self: os.remove(self.registry)),
                   ("corrupt registry", lambda self: self.put(self.registry, "{not json")),
                   ("corrupt seams", lambda self: self.put(self.seams, "[1,")),
                   ("seams modes not a dict", lambda self: self.put(self.seams, '{"modes": ["J065"]}')),
                   ("missing seams", lambda self: os.remove(self.seams)))
        for name, damage in damages:
            self.setUp()
            damage(self)
            state = self.state_now()
            self.assertFalse(state["readable"], name)
            self.assertFalse(any(r["eligible_now"] for r in state["seams"]), name)
            got = self.ask("calibrated.status")
            self.assertEqual("NO-DATA", got["verdict"], name)
            self.assertNotEqual("COMPLETE", got["state"], name)
        self.write_seams(J065="bogus", J102=7, J030=None)
        self.assertEqual({"off"}, {r["effective_mode"] for r in self.state_now()["seams"]})


class WhyOneDidNotRun(Base):
    """The effective mode and the reasons, from the files as they stand."""

    def test_off_in_the_config(self):
        self.write_seams(J065="off")
        row = self.seam(self.state_now(), "J065")
        self.assertEqual(("off", False), (row["effective_mode"], row["eligible_now"]))
        self.assertTrue(any("switched off" in w for w in row["why_not"]))

    def test_missing_registry(self):
        os.remove(self.registry)
        state = self.state_now()
        row = self.seam(state, "J065")
        self.assertFalse(state["readable"])
        self.assertEqual("off", row["effective_mode"])
        self.assertTrue(any("not installed" in w for w in row["why_not"]), row["why_not"])

    def test_privacy_refusal_from_the_registry(self):
        self.edit_registry("J065", privacy="must_stay_on_machine")
        row = self.seam(self.state_now(), "J065")
        self.assertEqual("off", row["effective_mode"])
        self.assertTrue(any("registry refuses" in w for w in row["why_not"]), row["why_not"])
        self.assertEqual("must_stay_on_machine", row["privacy"])

    def test_canary_failure_forces_every_seam_off(self):
        with open(self.marker, "w", encoding="utf-8") as fh:
            json.dump({"reason": "drift"}, fh)
        state = self.state_now()
        self.assertTrue(state["canary"]["failed"])
        self.assertEqual({"off"}, {r["effective_mode"] for r in state["seams"]})
        self.assertTrue(any("canary" in w for r in state["seams"] for w in r["why_not"]))

    def test_an_advise_or_act_seam_whose_call_site_is_not_proven_to_read_the_answer_is_forced_off(self):
        self.edit_registry("J065", call_site_reads_answer=False)
        self.edit_registry("J102", call_site_reads_answer=True)
        self.write_seams(J065="advise", J102="advise")
        state = self.state_now()
        self.assertEqual("off", self.seam(state, "J065")["effective_mode"])
        self.assertEqual("advise", self.seam(state, "J102")["effective_mode"])
        self.write_seams(J065="shadow")
        self.assertEqual("shadow", self.seam(self.state_now(), "J065")["effective_mode"], "shadow is not forced")

    def test_a_missing_bridge_and_a_used_up_budget_each_block_a_request(self):
        self.use_bridge()
        ready = self.seam(self.state_now(), "J065")
        self.assertTrue(ready["eligible_now"], ready["why_not"])
        with open(self.budget, "w", encoding="utf-8") as fh:
            json.dump({"date": TODAY.isoformat(), "calls": 50}, fh)
        row = self.seam(self.state_now(), "J065")
        self.assertFalse(row["eligible_now"])
        self.assertTrue(any("budget is used up" in w for w in row["why_not"]))
        with open(self.budget, "w", encoding="utf-8") as fh:
            json.dump({"date": (TODAY - timedelta(days=1)).isoformat(), "calls": 50}, fh)
        self.assertTrue(self.seam(self.state_now(), "J065")["eligible_now"], "yesterday's count is not today's")
        os.remove(self.budget)
        del os.environ["BROTHER_DECISION_BRIDGE"]
        row = self.seam(self.state_now(), "J065")
        self.assertFalse(row["eligible_now"])
        self.assertTrue(any("no bridge" in w for w in row["why_not"]))

    def test_only_the_two_seams_the_door_wires_can_be_asked_from_it(self):
        self.use_bridge()
        self.write_seams(J065="shadow", J102="shadow", J030="shadow", J117="shadow")
        state = self.state_now()
        self.assertEqual({"J065", "J102"}, {r["id"] for r in state["seams"] if r["eligible_now"]})
        self.assertIsNone(self.seam(state, "J030")["askable_here"])

    def test_promotion_states_are_read_only_and_family_specific(self):
        self.write_seams(J065="act", J102="act")
        future, past = (TODAY + timedelta(days=5)).isoformat(), (TODAY - timedelta(days=1)).isoformat()
        revoke = {k: v for k, v in promotion_record("J065").items()
                  if k in ("family", "qtype", "risk_class", "model", "signed_by", "signed_at")}
        revoke["revoke"] = True
        cases = (("none", [], "none"),
                 ("active", [promotion_record("J065", review_by=future)], "active"),
                 ("the review day itself still counts", [promotion_record("J065", review_by=TODAY.isoformat())],
                  "active"),
                 ("expired", [promotion_record("J065", review_by=past)], "expired"),
                 ("revoked", [promotion_record("J065"), revoke], "revoked"),
                 ("another family's promotion", [promotion_record("J102", review_by=future)], "none"),
                 ("corrupt line", [promotion_record("J065"), "{not json"], "corrupt"))
        for name, rows, expected in cases:
            self.write_promotions(*rows)
            before = snapshot(self.tmp)
            row = self.seam(self.state_now(), "J065")
            self.assertEqual(expected, row["promotion"]["state"], name)
            self.assertEqual(expected == "active", row["may_act"], name)
            self.assertEqual(before, snapshot(self.tmp), name + ": reading a promotion writes nothing")

    def test_bytes_that_are_not_text_in_a_store_or_a_ledger_read_as_damage_never_crash_or_pass(self):
        self.write_seams(J065="act")
        with open(self.promotions, "wb") as fh:
            fh.write(b"\xff\xfe\x00 not utf-8\n")
        os.makedirs(self.ledger)
        with open(os.path.join(self.ledger, "decisions.jsonl"), "wb") as fh:
            fh.write(b"\xff\xfe garbage\n")
        row = self.seam(self.state_now(), "J065")
        self.assertEqual("corrupt", row["promotion"]["state"])
        self.assertIs(False, row["may_act"])
        self.assertEqual("NO-DATA", row["calibration"]["state"])
        self.assertEqual("COMPLETE", self.ask("calibrated.status")["state"])

    def test_calibration_coverage_is_missing_until_outcomes_exist_and_never_fabricated(self):
        self.assertEqual("NO-DATA", self.seam(self.state_now(), "J065")["calibration"]["state"])
        os.makedirs(self.ledger)
        dec, out = os.path.join(self.ledger, "decisions.jsonl"), os.path.join(self.ledger, "outcomes.jsonl")
        for _ in range(3):
            did = "J065:%s:%s" % (uuid.uuid4().hex[:16], uuid.uuid4().hex[:16])
            jev_calibration.append_decision(dec, {
                "id": did, "family": "J065", "qtype": "noul", "framing": "0123456789abcdef", "answer": True,
                "prob": 0.95, "confidence": 0.95, "model": "fixture-model", "cost": 0.0, "at": now()})
            jev_calibration.append_outcome(out, {"id": did, "correct": True, "source": "fixture", "at": now()},
                                           decisions_path=dec)
        cal = self.seam(self.state_now(), "J065")["calibration"]
        self.assertEqual("measured", cal["state"])
        self.assertEqual((3, 20, False), (cal["rows"][0]["outcomes"], cal["rows"][0]["needed"],
                                         cal["rows"][0]["meets_target"]))


class TheEffectiveModeIsWhatTheSeamDoes(Base):
    """effective off exactly when the real jev_seam.consult would make no call (the readout mirrors the seam, so a
    drift between them goes red here)."""

    def called_for(self):
        calls = []

        def runner(argv, stdin_text):
            calls.append(stdin_text)
            payload = json.loads(stdin_text)
            return 0, json.dumps({"model": jev_decide.MODEL_PREFIX + "-fixture", "usage": {"cost": 0.0},
                                  "answers": {q: {"noul": 0.9, "confidence": 0.9} for q in payload["questions"]}}), ""

        config = jev_seam.load_seams_config(self.seams)
        registry = jev_registry.load(self.registry) if os.path.isfile(self.registry) else []
        os.makedirs(self.ledger, exist_ok=True)
        jev_seam.consult("J065", {"log": "x", "rerun_history": "none"}, False, seams_config=config,
                         registry=registry, ledger_dir=self.ledger, runner=runner)
        jev_seam.drain(timeout=10)
        return len(calls) > 0

    def drop_field(self, entry_id, field):
        with open(self.registry, encoding="utf-8") as fh:
            rows = json.load(fh)
        for row in rows:
            if row["id"] == entry_id:
                row.pop(field, None)
        with open(self.registry, "w", encoding="utf-8") as fh:
            json.dump(rows, fh)

    def test_forced_off_is_the_registry_lint_rule_itself_not_a_copy(self):
        """ADM1.b L2: whatever jev_registry.lint reports as advise-act-never-read is what the readout forces off."""
        self.write_seams(J065="advise")
        self.drop_field("J065", "call_site_reads_answer")
        self.assertEqual("advise", self.seam(self.state_now(), "J065")["effective_mode"])
        real = jev_registry.lint

        def lint(registry, seams_config=None):
            return real(registry, seams_config) + [jev_registry.Finding("J065", "advise-act-never-read", "fixture")]
        with mock.patch.object(jev_registry, "lint", lint):
            row = self.seam(self.state_now(), "J065")
        self.assertEqual("off", row["effective_mode"])
        self.assertIn(jev_status.FORCED_OFF, row["why_not"])

    def test_a_registry_lint_cannot_read_forces_advise_off_and_leaves_shadow_alone(self):
        def broken(registry, seams_config=None):
            raise TypeError("fixture: lint cannot read this registry")
        for mode, expected in (("advise", "off"), ("act", "off"), ("shadow", "shadow")):
            self.write_seams(J065=mode)
            with mock.patch.object(jev_registry, "lint", broken):
                self.assertEqual(expected, self.seam(self.state_now(), "J065")["effective_mode"], mode)

    def test_off_shadow_advise_act_canary_forced_off_and_registry_refusal_agree_with_consult(self):
        scenarios = [("mode " + m, m, lambda: None) for m in ("off", "shadow", "advise", "act", "bogus")]
        scenarios += [
            ("canary", "shadow", lambda: self.put(self.marker, "{}")),
            ("advise not proven", "advise", lambda: self.edit_registry("J065", call_site_reads_answer=False)),
            ("act proven", "act", lambda: self.edit_registry("J065", call_site_reads_answer=True)),
            ("advise, the field absent", "advise", lambda: self.drop_field("J065", "call_site_reads_answer")),
            ("act, the field absent", "act", lambda: self.drop_field("J065", "call_site_reads_answer")),
            ("advise, the field a text false", "advise",
             lambda: self.edit_registry("J065", call_site_reads_answer="false")),
            ("act, the field a zero", "act", lambda: self.edit_registry("J065", call_site_reads_answer=0)),
            ("shadow, the field a text true", "shadow",
             lambda: self.edit_registry("J065", call_site_reads_answer="true")),
            ("must stay on machine", "shadow", lambda: self.edit_registry("J065", privacy="must_stay_on_machine")),
            ("missing registry", "shadow", lambda: os.remove(self.registry))]
        for name, mode, setup in scenarios:
            self.setUp()
            self.write_seams(J065=mode)
            setup()
            row = self.seam(self.state_now(), "J065")
            fresh = {"consecutive_failures": 0, "opened_at": None, "probing": False}
            with mock.patch.object(jev_seam, "_BREAKER_STATE", fresh):
                called = self.called_for()
            self.assertEqual(row["effective_mode"] != "off", called, "%s: %s" % (name, row))


class ReviewAndDeliverEndToEnd(Base):
    """The real jev_door.py, a fixture bridge, a throwaway ledger. Every case keeps the check verdict."""

    def assert_beside_the_check(self, got, original="FAIL"):
        self.assertEqual(original, got["check_verdict"])
        self.assertIs(False, got["release_evidence"])
        self.assertIs(False, got["acted"])

    def test_a_review_second_opinion_is_recorded_as_its_own_ledger_result(self):
        self.use_bridge(prob="0.95")
        got = self.review()
        self.assertEqual(("FAILED", "FAIL"), (got["state"], got["verdict"]), "the check decides, not the opinion")
        self.assert_beside_the_check(got)
        opinion = got["second_opinion"]
        self.assertIs(True, opinion["answered"])
        self.assertEqual(("J065", "answered", True), (opinion["entry_id"], opinion["phase"], opinion["said"]))
        self.assertEqual(1, self.bridge_calls())
        with open(os.path.join(self.ledger, "attempts.jsonl"), encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        mine = [r for r in rows if r.get("attempt_id") == opinion["attempt_id"]]
        self.assertTrue(any(r.get("phase") == "answered" for r in mine), mine)

    def test_a_confident_flake_opinion_leaves_a_failed_check_failed(self):
        self.use_bridge(prob="0.99")
        got = self.review(check_verdict="FAIL")
        self.assertEqual(("FAILED", "FAIL"), (got["state"], got["verdict"]))
        self.assertTrue(got["second_opinion"]["said"], "the opinion says flake")
        self.assert_beside_the_check(got, "FAIL")

    def test_a_delivery_second_opinion_runs_the_deliver_seam(self):
        self.use_bridge(prob="0.9")
        got = self.ask("calibrated.deliver", {"note": ops.DONE_NOTE, "check_verdict": "PASS", "quotes": "yes",
                                              "wait_s": 10}, ["approve"])
        self.assertEqual(("COMPLETE", "J102"), (got["state"], got["second_opinion"]["entry_id"]))
        self.assert_beside_the_check(got, "PASS")

    def test_off_asks_nothing_and_says_so(self):
        self.use_bridge()
        self.write_seams(J065="off", J102="shadow")
        got = self.review()
        self.assertNotEqual("COMPLETE", got["state"])
        self.assertIn("off", got["output"])
        self.assertEqual(0, self.bridge_calls())
        self.assert_beside_the_check(got)
        self.assertFalse(os.path.exists(os.path.join(self.ledger, "attempts.jsonl")))

    def test_each_refusal_asks_nothing_and_keeps_the_check_verdict(self):
        scenarios = (("missing registry", lambda: os.remove(self.registry)),
                     ("registry privacy refusal", lambda: self.edit_registry("J065", privacy="must_stay_on_machine")),
                     ("canary failure", lambda: self.put(self.marker, "{}")),
                     ("private term in the text", lambda: None))
        for name, damage in scenarios:
            self.setUp()
            self.use_bridge()
            damage()
            text = ops.FAILING_OUTPUT + ("ACMEWIDGET leaked\n" if name == "private term in the text" else "")
            got = self.review(output=text)
            self.assertNotEqual("COMPLETE", got["state"], name)
            self.assertEqual(0, self.bridge_calls(), name)
            self.assert_beside_the_check(got)

    def test_a_slow_bridge_times_out_and_still_keeps_the_check_verdict(self):
        self.use_bridge(sleep="3")
        got = self.review(wait_s=0.5)
        self.assertEqual(("FAIL", False), (got["verdict"], got["second_opinion"]["answered"]))
        self.assertIn("no answer within", got["second_opinion"]["line"])
        self.assert_beside_the_check(got)

    def test_act_with_an_expired_or_missing_promotion_and_no_calibration_changes_nothing(self):
        self.use_bridge()
        self.write_seams(J065="act", J102="shadow")
        for rows in ([], [promotion_record("J065", review_by=(TODAY - timedelta(days=400)).isoformat())]):
            self.write_promotions(*rows)
            got = self.review()
            self.assert_beside_the_check(got)
            row = self.seam(self.ask("calibrated.status")["state_detail"], "J065")
            self.assertIs(False, row["may_act"])
            self.assertEqual("act", row["configured_mode"])


if __name__ == "__main__":
    unittest.main()

