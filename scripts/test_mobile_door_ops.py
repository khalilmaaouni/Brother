#!/usr/bin/env python3
"""OUT1.f integrator cases beside the frozen MobileAccess contract (scripts/test_mobile_access_contract.py).

The frozen class drives invoke_capability with a fake handler only. These cases pin what it cannot reach: the door's
mobile capability table names one existing script per operation and every operation reaches its handler with the
inputs given; nothing starts while a simulator, a device permission or an Appium server is missing; the visual only
route says a screenshot proves no interaction; an engine this install does not carry is NO-DATA, never a pass;
evidence travels; authority is never widened; and the real journey checker, reached through the door, passes a good
record and fails a bad one.

Hermetic: scripted fakes for the child process and the host probes, scratch files only, no HOME read, no network, no
simulator.

Run: python3 -B scripts/test_mobile_door_ops.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import door  # noqa: E402


def _door_view(*capabilities):
    """The consumer view (door.consumer_admission) these routing tests run the door under: only `capabilities`
    are admitted, as an installed journey row or a maintainer's record would admit them (OUT1.n). No capabilities
    is the hidden case."""
    return mock.patch.object(door, "consumer_admission", return_value={
        "candidate": "test", "host": "claude", "capabilities": list(capabilities), "routes": set(), "queue": []})

GOOD = {"verdict": "PASS", "output": "checked", "evidence_refs": ["e1", "e2"]}
ROWS = {r["id"]: r for r in door.MOBILE_CAPABILITIES}
#: every operation the 1.2.0 contract names, each by the row that routes it
NAMED = {"profile": "mobile.profile_detect", "profile writing": "mobile.profile_init", "journey": "mobile.journey",
         "action": "mobile.canonical_action", "plan": "mobile.plan", "ownership": "mobile.ownership",
         "driver contract": "mobile.driver_contract", "driver selection": "mobile.driver_select",
         "reference binding": "mobile.reference_init", "reference lock": "mobile.reference_lock",
         "simulator run": "mobile.run", "native device": "mobile.native_ios", "appium": "mobile.appium",
         "visual only": "mobile.visual_only", "native test bundle": "mobile.test_bundle_record",
         "design": "mobile.design_brief", "media": "mobile.design_media", "release state": "mobile.release_state",
         "passport": "mobile.passport", "claims": "mobile.claims_check"}
#: the existing script and sub command each operation must become (an independent copy of the table's intent)
EXPECTED = {
    "mobile.doctor": ("mobile_workflow", "doctor"),
    "mobile.profile_detect": ("mobile_project_profile", ""),
    "mobile.profile_init": ("mobile_workflow", "profile-init"),
    "mobile.reference_init": ("mobile_workflow", "reference-init"),
    "mobile.reference_record": ("mobile_workflow", "reference"),
    "mobile.reference_check": ("mobile_workflow", "check-reference"),
    "mobile.reference_lock": ("mobile_reference_lock", ""),
    "mobile.journey": ("mobile_journey_contract", ""),
    "mobile.canonical_action": ("mobile_canonical_action", ""),
    "mobile.plan": ("mobile_plan_compiler", ""),
    "mobile.ownership": ("mobile_ownership_resolver", ""),
    "mobile.driver_contract": ("mobile_driver_contract", ""),
    "mobile.driver_select": ("mobile_hybrid_action_router", ""),
    "mobile.screen_observation": ("mobile_screen_observation", ""),
    "mobile.run": ("mobile_workflow", "run"),
    "mobile.native_ios": ("mobile_native_ios_adapter", "run"),
    "mobile.appium": ("mobile_appium_adapter", "run"),
    "mobile.visual_only": ("mobile_visual_fallback_adapter", "run"),
    "mobile.state_apply": ("mobile_state_reset", "apply"),
    "mobile.state_uninstall": ("mobile_state_reset", "uninstall"),
    "mobile.state_install": ("mobile_state_reset", "install"),
    "mobile.state_checkpoint": ("mobile_state_reset", "capture-checkpoint"),
    "mobile.state_hooks": ("mobile_deterministic_hooks", ""),
    "mobile.state_fixture": ("mobile_state_fixture", ""),
    "mobile.test_route": ("mobile_test_route", ""),
    "mobile.toolchain": ("mobile_toolchain_probe", ""),
    "mobile.canary": ("mobile_xcode_canary_local", ""),
    "mobile.test_bundle_record": ("native_evidence", "record"),
    "mobile.test_bundle_validate": ("native_evidence", "validate"),
    "mobile.test_bundle_v2": ("native_evidence_v2", "wrap"),
    "mobile.design_search": ("mobile_design", "search"),
    "mobile.design_brief": ("mobile_design", "brief"),
    "mobile.design_media": ("mobile_design", "media"),
    "mobile.design_evidence": ("mobile_design", "evidence"),
    "mobile.design_staleness": ("mobile_design", "check-staleness"),
    "mobile.release_state": ("release_state_tracker", ""),
    "mobile.passport": ("journey_passport", ""),
    "mobile.claims_check": ("mobile_product_claims", "check"),
    "mobile.claims_register": ("mobile_product_claims", "register"),
    "mobile.claims_gate": ("mobile_product_claims", "gate"),
    "mobile.pack": ("mobile_workflow", "pack"),
    "mobile.verify_pack": ("mobile_workflow", "verify-pack"),
}
WRITES = {"mobile.doctor", "mobile.profile_init", "mobile.reference_init", "mobile.reference_record", "mobile.run",
          "mobile.native_ios", "mobile.appium", "mobile.visual_only", "mobile.state_apply", "mobile.state_uninstall",
          "mobile.state_install", "mobile.state_checkpoint", "mobile.state_hooks", "mobile.canary",
          "mobile.test_bundle_record", "mobile.design_search", "mobile.design_brief", "mobile.design_media",
          "mobile.design_evidence", "mobile.design_staleness", "mobile.pack"}
DEVICE_ROWS = {"mobile.native_ios", "mobile.appium", "mobile.state_apply", "mobile.state_uninstall",
               "mobile.state_install", "mobile.state_checkpoint", "mobile.state_hooks"}


def probe_all(result=True):
    return lambda name, value=None: result


def minimal(row):
    """Inputs that satisfy a row's prerequisites, as plain text (a fake invoke never opens them)."""
    return {name: ("x" if name != "outcome" else "make a brief") for name in row["prerequisites"]}


def route(capability, typed=None, allow=None, probe=None, reply=GOOD, outcome=None):
    calls = []

    def invoke(handler, inputs):
        calls.append((handler, dict(inputs)))
        return dict(reply)
    got = door.run_mobile_capability(capability, outcome, typed, allow, invoke, probe or probe_all())
    return got, calls


class Table(unittest.TestCase):
    def test_ids_handlers_and_scripts_are_unique_and_named_alike(self):
        ids = [r["id"] for r in door.MOBILE_CAPABILITIES]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), len({r["handler"] for r in door.MOBILE_CAPABILITIES}))
        for r in door.MOBILE_CAPABILITIES:
            self.assertEqual("mobile_existing_" + r["id"].split(".", 1)[1], r["handler"])
            self.assertTrue(os.path.isfile(os.path.join(HERE, r["script"] + ".py")), r["script"])

    def test_every_sub_command_is_one_the_script_really_has(self):
        for r in door.MOBILE_CAPABILITIES:
            with open(os.path.join(HERE, r["script"] + ".py"), encoding="utf-8") as fh:
                text = fh.read()
            for word in r["sub"]:
                self.assertIn('"%s"' % word, text, "%s has no sub command %s" % (r["script"], word))

    def test_every_prerequisite_is_an_input_the_script_is_given(self):
        for r in door.MOBILE_CAPABILITIES:
            names = {a[0] for a in r["args"]}
            self.assertTrue(set(r["prerequisites"]) <= names, r["id"])
            for name, kind, form in r["args"]:
                self.assertIn(kind, ("file", "dir", "out", "str", "files", "list", "int", "num", "switch", "rest"))
                self.assertTrue(form == "pos" or form.startswith("--"), (r["id"], name))

    def test_each_operation_becomes_exactly_the_script_and_sub_command_it_names(self):
        self.assertEqual(sorted(EXPECTED), sorted(ROWS))
        for cap, (script, sub) in EXPECTED.items():
            self.assertEqual((script, sub), (ROWS[cap]["script"], " ".join(ROWS[cap]["sub"])), cap)

    def test_every_named_operation_has_its_route(self):
        for what, cap in NAMED.items():
            self.assertIn(cap, ROWS, what)
        self.assertGreaterEqual(len(ROWS), 40)

    def test_only_the_writing_routes_need_write_and_nothing_needs_more_than_one_grant(self):
        auth = {c: r["required_authority"] for c, r in ROWS.items()}
        self.assertEqual(WRITES, {c for c, a in auth.items() if a == ["write"]})
        self.assertTrue(all(a in (["read"], ["write"]) for a in auth.values()))
        self.assertEqual(DEVICE_ROWS, {c for c, r in ROWS.items() if r["device"]})

    def test_the_frozen_contract_ids_are_the_journey_row(self):
        self.assertEqual("mobile_existing_journey", ROWS["mobile.journey"]["handler"])
        self.assertEqual(["read"], ROWS["mobile.journey"]["required_authority"])

    def test_the_reach_rows_are_consumer_routes_that_own_every_script(self):
        with open(os.path.join(ROOT, "docs", "plan", "REACH.json"), encoding="utf-8") as fh:
            reach = json.load(fh)
        rows = [r for r in reach["inventory"] if r["id"].startswith("mobile-")]
        self.assertEqual({"mobile-plan", "mobile-run", "mobile-evidence"}, {r["id"] for r in rows})
        owned = set()
        for r in rows:
            self.assertEqual(("consumer", "mobile-check"), (r["disposition"], r["routed"]), r["id"])
            self.assertEqual("NO-DATA", r["installed_execution"], r["id"])
            owned.update(r["source_owners"])
        for r in door.MOBILE_CAPABILITIES:
            self.assertIn("scripts/%s.py" % r["script"], owned, r["script"])
        cap = next(c for c in reach["capabilities"] if c["id"] == "mobile-check")
        self.assertEqual(("review", "scripts/door.py", "def run_mobile_capability"),
                         (cap["verb"], cap["command"], cap["marker"]))
        for goal in [cap["ask"]] + cap["paraphrases"]:
            got = door.recommend(goal)
            self.assertEqual(("mobile-check", True), (got["route"], got["delivered"]), goal)

    def test_the_door_help_names_the_mobile_route_and_the_device_grant(self):
        with mock.patch("sys.stdout"), self.assertRaises(SystemExit):
            door.main(["--help"])
        got = []
        with _door_view("mobile.*"), mock.patch.object(door, "run_mobile_capability",
                                                       side_effect=lambda *a: got.append(a) or {"state": "x"}), \
                mock.patch("builtins.print"):
            door.main(["--capability", "mobile.run", "--allow", "write", "--allow", "device", "--input", "repo=r"])
        self.assertEqual([("mobile.run", None, {"repo": "r"}, ["write", "device"])], got)


def _per_operation(cap):
    def case(self):
        row = ROWS[cap]
        typed = minimal(row)
        if row["device"]:
            typed["device"] = "SIM-1"
        allow = ["write"] if row["required_authority"] == ["write"] else None
        got, calls = route(cap, typed, allow)
        self.assertEqual([(row["handler"], typed)], calls)
        self.assertEqual(("COMPLETE", row["handler"], ["e1", "e2"]), (got["state"], got["handler"],
                                                                        got["evidence_refs"]))
        self.assertEqual("NO-DATA", got["human_acceptance"])
    return case


for _cap in sorted(ROWS):
    setattr(Table, "test_route_" + _cap.replace(".", "_"), _per_operation(_cap))


class Prerequisites(unittest.TestCase):
    """Nothing starts while the host cannot do the work, and the reply says how to supply what is missing."""

    def run_args(self, **extra):
        typed = {"repo": "r", "profile": "p", "reference": "f", "out": "o"}
        typed.update(extra)
        return typed

    def test_a_missing_simulator_starts_nothing_and_says_how_to_get_one(self):
        for found in (False, None):
            got, calls = route("mobile.run", self.run_args(), ["write"], probe=probe_all(found))
            self.assertEqual([], calls)
            self.assertEqual(("BLOCKED", ["simulator"]), (got["state"], got["missing_prerequisites"]))
            self.assertEqual("", got["prepare"][0]["route"])
            self.assertIn("simulator", got["prepare"][0]["how"])
            self.assertEqual({"capability": "mobile.run", "inputs": ["out", "profile", "reference", "repo"]},
                             got["resume"])
        got, calls = route("mobile.run", self.run_args(), ["write"])
        self.assertEqual(("COMPLETE", 1), (got["state"], len(calls)))

    def test_a_missing_profile_names_the_route_that_makes_it(self):
        typed = self.run_args()
        del typed["profile"]
        got, calls = route("mobile.run", typed, ["write"])
        self.assertEqual(([], ["profile"]), (calls, got["missing_prerequisites"]))
        self.assertEqual("mobile.profile_init", got["prepare"][0]["route"])
        got, _ = route("mobile.run", {}, ["write"])
        self.assertEqual(["repo", "profile", "reference", "out", ], got["missing_prerequisites"][:4])

    def test_the_simulator_is_asked_for_by_name_when_one_is_named(self):
        asked = []

        def probe(name, value=None):
            asked.append((name, value))
            return True
        typed = {"record": "r", "device": "AB-12", "out": "o"}
        route("mobile.native_ios", typed, ["write"], probe=probe)
        self.assertEqual([("simulator", "AB-12"), ("simulator", "AB-12")], asked)

    def test_a_physical_device_needs_its_own_permission_and_write_does_not_grant_it(self):
        typed = {"record": "r", "device": "PHONE-1", "out": "o"}
        listed = lambda name, value=None: value != "PHONE-1"  # noqa: E731
        got, calls = route("mobile.native_ios", typed, ["write"], probe=listed)
        self.assertEqual(([], "REFUSED", ["device"]), (calls, got["state"], got["missing_authority"]))
        self.assertIn("device", got["output"])
        got, calls = route("mobile.native_ios", typed, ["write", "approve"], probe=listed)
        self.assertEqual(([], "REFUSED"), (calls, got["state"]))
        got, calls = route("mobile.native_ios", typed, ["write", "device"], probe=listed)
        self.assertEqual(("COMPLETE", [("mobile_existing_native_ios", typed)]), (got["state"], calls))
        self.assertEqual("PHONE-1", got["identity"]["device"])

    def test_a_flagged_physical_device_or_an_android_target_needs_the_permission_too(self):
        for extra in ({"physical_device": True}, {"platform": "android"}):
            typed = dict({"bundle_id": "com.example.app", "out": "o"}, **extra)
            got, calls = route("mobile.state_uninstall", typed, ["write"])
            self.assertEqual(([], "REFUSED", ["device"]), (calls, got["state"], got["missing_authority"]), extra)
            got, calls = route("mobile.state_uninstall", typed, ["write", "device"])
            self.assertEqual(("COMPLETE", 1), (got["state"], len(calls)), extra)
        typed = {"bundle_id": "com.example.app", "out": "o", "physical_device": "yes"}
        got, calls = route("mobile.state_uninstall", typed, ["write"])
        self.assertEqual(("COMPLETE", 1), (got["state"], len(calls)))

    def test_a_simulator_flagged_as_physical_still_needs_the_permission(self):
        typed = {"record": "r", "device": "SIM", "out": "o", "physical_device": True}
        got, calls = route("mobile.native_ios", typed, ["write"])
        self.assertEqual(([], "REFUSED", ["device"]), (calls, got["state"], got["missing_authority"]))

    def test_a_listed_simulator_never_needs_the_device_permission(self):
        got, calls = route("mobile.native_ios", {"record": "r", "device": "SIM", "out": "o"}, ["write"])
        self.assertEqual(("COMPLETE", [], 1), (got["state"], got["missing_authority"], len(calls)))

    def test_an_unreachable_appium_starts_nothing_and_a_reachable_one_runs(self):
        asked = []

        def probe(name, value=None):
            asked.append((name, value))
            return name != "appium"
        typed = {"record": "r", "out": "o", "server": "http://127.0.0.1:9"}
        got, calls = route("mobile.appium", typed, ["write"], probe=probe)
        self.assertEqual(([], ["appium"], [("appium", "http://127.0.0.1:9")]),
                         (calls, got["missing_prerequisites"], asked))
        self.assertIn("Appium", got["prepare"][0]["how"])
        got, calls = route("mobile.appium", {"record": "r", "out": "o"}, ["write"])
        self.assertEqual(("COMPLETE", 1), (got["state"], len(calls)))

    def test_a_missing_input_or_a_missing_write_grant_starts_nothing(self):
        got, calls = route("mobile.journey", {})
        self.assertEqual(([], ["record"]), (calls, got["missing_prerequisites"]))
        got, calls = route("mobile.doctor", {"repo": "r", "out": "o"})
        self.assertEqual(([], "REFUSED", ["write"]), (calls, got["state"], got["missing_authority"]))
        got, calls = route("mobile.nope", {"record": "r"})
        self.assertEqual(([], "BLOCKED"), (calls, got["state"]))
        self.assertIn("mobile.journey", got["output"])

    def test_an_optional_out_turns_a_read_route_into_a_write_route(self):
        for cap, base in (("mobile.plan", {"record": "r"}), ("mobile.passport", minimal(ROWS["mobile.passport"])),
                          ("mobile.release_state", {}), ("mobile.claims_register", minimal(ROWS["mobile.claims_register"]))):
            got, calls = route(cap, base)
            self.assertEqual(("COMPLETE", 1), (got["state"], len(calls)), cap)
            got, calls = route(cap, dict(base, out="o"))
            self.assertEqual(([], "REFUSED", ["write"]), (calls, got["state"], got["missing_authority"]), cap)
            got, calls = route(cap, dict(base, out="o"), ["write"])
            self.assertEqual(("COMPLETE", 1), (got["state"], len(calls)), cap)

    def test_the_callers_inputs_and_grants_are_never_changed(self):
        typed, allow = {"record": "r", "device": "PHONE-1", "out": "o"}, ["write"]
        route("mobile.native_ios", typed, allow, probe=lambda n, v=None: False)
        self.assertEqual(({"record": "r", "device": "PHONE-1", "out": "o"}, ["write"]), (typed, allow))
        self.assertEqual(["write"], ROWS["mobile.native_ios"]["required_authority"])
        self.assertEqual(["record", "device", "out"], ROWS["mobile.native_ios"]["prerequisites"])


class Honesty(unittest.TestCase):
    def test_the_visual_only_route_says_a_screenshot_proves_no_interaction(self):
        typed = minimal(ROWS["mobile.visual_only"])
        got, calls = route("mobile.visual_only", typed, ["write"])
        self.assertEqual("COMPLETE", got["state"])
        self.assertIn("proves no tap, no interaction and no human acceptance", got["output"])
        self.assertEqual(("NO-DATA", "NO-DATA"), (got["interaction"], got["human_acceptance"]))
        self.assertIn("VISUAL ONLY", got["limit"])
        self.assertTrue(got["output"].startswith("checked"))
        self.assertNotIn("interaction", route("mobile.run", {"repo": "r", "profile": "p", "reference": "f",
                                                             "out": "o"}, ["write"])[0])

    def test_the_visual_only_route_says_it_even_when_the_script_failed(self):
        got, _ = route("mobile.visual_only", minimal(ROWS["mobile.visual_only"]), ["write"],
                       reply={"verdict": "FAIL", "output": "bad", "evidence_refs": []})
        self.assertEqual(("FAILED", "NO-DATA"), (got["state"], got["interaction"]))
        self.assertIn("proves no tap", got["output"])

    def test_every_answer_says_human_acceptance_is_no_data(self):
        for cap in ("mobile.journey", "mobile.run", "mobile.passport", "mobile.release_state"):
            got, _ = route(cap, minimal(ROWS[cap]), ["write"])
            self.assertEqual("NO-DATA", got["human_acceptance"], cap)
            self.assertIn("human acceptance is NO-DATA", got["limit"], cap)

    def test_host_and_identities_are_exact(self):
        got, _ = route("mobile.state_uninstall", {"bundle_id": "com.example.App", "device": "SIM-1", "out": "o"},
                       ["write"])
        self.assertEqual(("SIM-1", "com.example.App"), (got["identity"]["device"], got["identity"]["bundle_id"]))
        self.assertTrue(got["identity"]["host"].startswith(sys.platform))

    def test_evidence_travels_and_a_pass_without_any_is_unproven(self):
        refs = ["receipt sha256:aa", "log sha256:bb", "third"]
        got, _ = route("mobile.journey", {"record": "r"}, reply={"verdict": "PASS", "output": "ok",
                                                                 "evidence_refs": refs})
        self.assertEqual(refs, got["evidence_refs"])
        got, _ = route("mobile.journey", {"record": "r"}, reply={"verdict": "PASS", "output": "ok",
                                                                 "evidence_refs": []})
        self.assertEqual("UNPROVEN", got["state"])

    def test_no_data_and_fail_replies_never_complete(self):
        for verdict, state in (("NO-DATA", "NO-DATA"), ("FAIL", "FAILED"), ("MAYBE", "NO-DATA")):
            got, _ = route("mobile.journey", {"record": "r"}, reply={"verdict": verdict, "output": "x",
                                                                     "evidence_refs": ["e"]})
            self.assertEqual((state, verdict if verdict != "MAYBE" else "NO-DATA"), (got["state"], got["verdict"]))

    def test_an_outcome_fills_only_the_input_the_row_names(self):
        got, calls = route("mobile.design_brief", {"board": "b", "out": "o"}, ["write"], outcome="a calmer onboarding")
        self.assertEqual("a calmer onboarding", calls[0][1]["outcome"])
        got, calls = route("mobile.design_brief", {"board": "b", "out": "o", "outcome": "mine"}, ["write"],
                           outcome="other")
        self.assertEqual("mine", calls[0][1]["outcome"])
        got, calls = route("mobile.journey", {"record": "r"}, outcome="ignored")
        self.assertNotIn("outcome", calls[0][1])


class Invoke(unittest.TestCase):
    """mobile_invoke with a scripted child: what runs, and what each answer becomes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.rec = os.path.join(self.tmp, "rec.json")
        with open(self.rec, "w") as fh:
            fh.write("{}")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def invoke(self, handler="mobile_existing_journey", inputs=None, code=0, out="PASS: ok\n", err=""):
        calls = []

        def run(argv, timeout):
            calls.append((argv, timeout))
            if isinstance(code, BaseException):
                raise code
            return code, out, err
        inputs = {"record": self.rec} if inputs is None else inputs
        return door.mobile_invoke(handler, inputs, run), calls

    def test_the_argv_is_the_python_the_script_the_sub_command_then_the_inputs(self):
        got, calls = self.invoke()
        self.assertEqual([[sys.executable, os.path.join(HERE, "mobile_journey_contract.py"), self.rec]],
                         [c[0] for c in calls])
        self.assertEqual("PASS", got["verdict"])
        _, calls = self.invoke("mobile_existing_reference_check", {"repo": self.tmp, "reference": self.rec})
        self.assertEqual(["check-reference", "--repo=" + self.tmp, "--reference=" + self.rec], calls[0][0][2:])
        _, calls = self.invoke("mobile_existing_profile_init", {"repo": self.tmp, "out": "o.json",
                               "expected_test": ["test://a", "test://b"], "only_testing": ["T/x"],
                               "simulator_name": "iPhone 16"})
        self.assertEqual(["profile-init", "--repo=" + self.tmp, "--out=o.json", "--simulator-name=iPhone 16",
                          "--expected-test=test://a", "--expected-test=test://b", "--only-testing=T/x"],
                         calls[0][0][2:])
        _, calls = self.invoke("mobile_existing_reference_lock", {"observation": self.rec, "bundle_id": "com.x.App"})
        self.assertEqual([self.rec, "com.x.App"], calls[0][0][2:])

    def test_numbers_switches_and_the_test_command_keep_their_shape(self):
        _, calls = self.invoke("mobile_existing_design_media", {"asset": self.rec, "role": "runtime", "out": "o",
                               "max_bytes": 100, "max_duration": 2.5})
        self.assertEqual(["media", "--asset=" + self.rec, "--role=runtime", "--max-bytes=100", "--max-duration=2.5",
                          "--out=o"], calls[0][0][2:])
        _, calls = self.invoke("mobile_existing_test_route", {"route_path": self.rec, "render_guide": True})
        self.assertEqual([self.rec, "--render-guide"], calls[0][0][2:])
        _, calls = self.invoke("mobile_existing_test_route", {"route_path": self.rec, "render_guide": False})
        self.assertEqual([self.rec], calls[0][0][2:])
        _, calls = self.invoke("mobile_existing_test_bundle_record", {
            "repo": self.tmp, "out": "o", "result_bundle": "b.xcresult",
            "command": ["xcodebuild", "test", "-scheme", "App"]})
        self.assertEqual(["record", "--repo=" + self.tmp, "--out=o", "--result-bundle=b.xcresult",
                          "--command", "xcodebuild", "test", "-scheme", "App"], calls[0][0][2:])
        self.assertEqual(3600, calls[0][1])
        _, calls = self.invoke()
        self.assertEqual(600, calls[0][1])

    def test_wrongly_typed_inputs_run_nothing(self):
        bad = [("mobile_existing_design_media", {"asset": self.rec, "role": "runtime", "out": "o", "max_bytes": "9"}),
               ("mobile_existing_design_media", {"asset": self.rec, "role": "runtime", "out": "o", "max_bytes": True}),
               ("mobile_existing_design_media", {"asset": self.rec, "role": "runtime", "out": "o", "max_bytes": 1.5}),
               ("mobile_existing_design_media", {"asset": self.rec, "role": "runtime", "out": "o",
                                                 "max_duration": float("nan")}),
               ("mobile_existing_design_media", {"asset": self.rec, "role": "runtime", "out": "o",
                                                 "max_duration": float("inf")}),
               ("mobile_existing_test_route", {"route_path": self.rec, "render_guide": "yes"}),
               ("mobile_existing_profile_init", {"repo": self.tmp, "out": "o", "expected_test": "test://a"}),
               ("mobile_existing_profile_init", {"repo": self.tmp, "out": "o", "expected_test": [3]}),
               ("mobile_existing_driver_select", {"record": self.rec, "driver_describe": [self.rec, "/no/such"]}),
               ("mobile_existing_journey", {"record": ["a"]}),
               ("mobile_existing_journey", {"record": 7}),
               ("mobile_existing_journey", {"record": self.rec, "schema": "/no/such/schema.json"}),
               ("mobile_existing_reference_check", {"repo": os.path.join(self.tmp, "nope"), "reference": self.rec}),
               ("mobile_existing_test_bundle_record", {"repo": self.tmp, "out": "o", "result_bundle": "b",
                                                       "command": "xcodebuild test"}),
               ("mobile_existing_test_bundle_record", {"repo": self.tmp, "out": "o", "result_bundle": "b",
                                                       "command": ["rm", "-rf", "/"]}),
               ("mobile_existing_test_bundle_record", {"repo": self.tmp, "out": "o", "result_bundle": "b",
                                                       "command": ["xcodebuild", ""]})]
        for handler, inputs in bad:
            got, calls = self.invoke(handler, inputs)
            self.assertEqual(([], "NO-DATA", []), (calls, got["verdict"], got["evidence_refs"]), (handler, inputs))
            self.assertIn("nothing was started", got["output"])

    def test_a_missing_file_names_what_to_supply(self):
        got, calls = self.invoke(inputs={"record": os.path.join(self.tmp, "absent.json")})
        self.assertEqual([], calls)
        self.assertIn("supply that file", got["output"])
        self.assertIn("absent.json", got["output"])

    def test_a_positional_word_starting_with_a_dash_is_never_an_option_but_free_text_may(self):
        got, calls = self.invoke("mobile_existing_reference_lock", {"observation": self.rec, "bundle_id": "--help"})
        self.assertEqual(([], "NO-DATA"), (calls, got["verdict"]))
        self.assertIn("dash", got["output"])
        _, calls = self.invoke("mobile_existing_design_search", {"board": self.rec, "out": "o", "query": "-dark"})
        self.assertIn("--query=-dark", calls[0][0])

    def test_an_unknown_or_shell_shaped_handler_runs_nothing(self):
        for handler in ("mobile_existing_nope", "mobile_existing_journey; rm -rf /", "../x", "", None, 3):
            got, calls = self.invoke(handler)
            self.assertEqual(([], "NO-DATA"), (calls, got["verdict"]), handler)
        got, calls = self.invoke(inputs=["x"])
        self.assertEqual(([], "NO-DATA"), (calls, got["verdict"]))

    def test_exit_codes_and_empty_output(self):
        self.assertEqual(("FAIL", []), tuple(self.invoke(code=1, out="FAIL: bad\n", err="boom")[0][k]
                                            for k in ("verdict", "evidence_refs")))
        got, _ = self.invoke(code=2, out="NO-DATA: nothing\n")
        self.assertEqual(("NO-DATA", []), (got["verdict"], got["evidence_refs"]))
        self.assertIn("nothing", got["output"])
        got, _ = self.invoke(code=2, out="", err="usage: x")
        self.assertIn("usage", got["output"])
        self.assertEqual(("NO-DATA", []), tuple(self.invoke(out="  \n")[0][k] for k in ("verdict", "evidence_refs")))
        self.assertEqual("FAIL", self.invoke(code=7)[0]["verdict"])

    def test_a_script_that_reports_its_own_no_data_or_fail_never_passes_at_exit_zero(self):
        for out, want in (("mobile_workflow: NO-DATA: no simulator\n", "NO-DATA"), ("NO-DATA: x\n", "NO-DATA"),
                          ("mobile_x: FAIL: y\n", "FAIL"), ('{"status": "NO-DATA"}', "NO-DATA"),
                          ('{"status": "LOW_CONFIDENCE"}', "NO-DATA"), ('{"status": "NOT_ATTEMPTED"}', "NO-DATA"),
                          ('{"status": "NO-OP"}', "NO-DATA"), ('{"status": "UNSUPPORTED"}', "FAIL"),
                          ('{"status": "NO_DRIVER"}', "FAIL"), ('{"status": "FAIL"}', "FAIL")):
            got, _ = self.invoke(out=out)
            self.assertEqual((want, []), (got["verdict"], got["evidence_refs"]), out)
        for out in ('{"status": "PASS"}', "PASS: ok\n", '{"verdict": "PASS"}'):
            self.assertEqual("PASS", self.invoke(out=out)[0]["verdict"], out)
        # VERD1.a: exit 0 whose output carries no PASS verdict of the script's own was PASS
        for out in ('{"status": "GROUNDED"}', '{"status": 3}', '[1]'):
            self.assertEqual("NO-DATA", self.invoke(out=out)[0]["verdict"], out)

    def test_a_toolchain_with_nothing_found_and_a_release_with_no_pass_are_no_data(self):
        none = json.dumps({"ios_toolchain_status": "none", "android_toolchain_status": "none"})
        some = json.dumps({"ios_toolchain_status": "partial", "android_toolchain_status": "none"})
        self.assertEqual("NO-DATA", self.invoke("mobile_existing_toolchain", {}, out=none)[0]["verdict"])
        self.assertEqual("PASS", self.invoke("mobile_existing_toolchain", {}, out=some)[0]["verdict"])  # its own marker
        odd = json.dumps({"ios_toolchain_status": "unknown", "android_toolchain_status": "none"})
        self.assertEqual("NO-DATA", self.invoke("mobile_existing_toolchain", {}, out=odd)[0]["verdict"])
        complete = json.dumps({"ios_toolchain_status": "complete", "android_toolchain_status": "none"})
        self.assertEqual("PASS", self.invoke("mobile_existing_toolchain", {}, out=complete)[0]["verdict"])
        self.assertEqual("NO-DATA", self.invoke("mobile_existing_journey", out=none)[0]["verdict"])  # was PASS
        states = {"states": {"UPLOADED": {"verdict": "NO-DATA"}, "INSTALLED": {"verdict": "NO-DATA"}}}
        self.assertEqual("NO-DATA", self.invoke("mobile_existing_release_state", {}, out=json.dumps(states))[0]["verdict"])
        states["states"]["INSTALLED"]["verdict"] = "PASS"
        self.assertEqual("PASS", self.invoke("mobile_existing_release_state", {}, out=json.dumps(states))[0]["verdict"])
        self.assertEqual("NO-DATA", self.invoke("mobile_existing_journey", out=json.dumps(states))[0]["verdict"])  # was PASS

    def test_a_fail_on_either_stream_or_a_crash_after_a_pass_is_never_pass(self):  # VERD1.a review round 1
        for out, err, want in (("report written\n", "test_a: PASS\ntest_b: FAIL\n", "FAIL"),
                               ('{"status": "PASS", "verdict": "FAIL"}', "", "FAIL"),
                               ('{"status": "PASS", "checks": [{"name": "x", "status": "FAIL"}]}', "", "FAIL"),
                               ("PASS: ok\n", "Traceback (most recent call last):\n  File x\nKeyError: k\n", "NO-DATA"),
                               ("PASS: ok\nTraceback (most recent call last):\n  x\n", "", "NO-DATA"),
                               ("PASS: ok\n", "test_a: PASS\n", "PASS")):
            got, _ = self.invoke(out=out, err=err)
            self.assertEqual((want, want == "PASS"), (got["verdict"], bool(got["evidence_refs"])), (out, err))

    def test_a_script_that_prints_no_pass_word_earns_pass_from_its_own_marker(self):  # VERD1.a review round 1
        # each shape is what the REAL script prints (mobile_*.py main), measured 2026-11-01
        profile = {"confidence": "low", "platforms": ["ios"], "schema_version": "mobile-project-profile-v1"}
        lock = {"artifact_identity": {"status": "observed_installed"}, "source_provenance": {"status": "UNRESOLVED"}}
        fixture = {"schema_version": "mobile-state-fixture-v1", "fixture_id": "f"}
        done = {"completeness": {"failed_count": 0, "no_data_count": 0, "headline": "h"}}
        cases = (("mobile_existing_profile_detect", {"project_dir": self.tmp}, json.dumps(profile), "", "PASS"),
                 ("mobile_existing_profile_detect", {"project_dir": self.tmp},
                  json.dumps(dict(profile, confidence="none")), "", "NO-DATA"),
                 ("mobile_existing_reference_lock", {"observation": self.rec, "bundle_id": "com.x.App"},
                  json.dumps(lock), "", "PASS"),
                 ("mobile_existing_reference_lock", {"observation": self.rec, "bundle_id": "com.x.App"},
                  json.dumps({"artifact_identity": {"status": "UNRESOLVED"}}), "", "NO-DATA"),
                 ("mobile_existing_driver_select", {"record": self.rec, "driver_describe": [self.rec]},
                  json.dumps({"status": "SELECTED", "driver_id": "native_ios"}), "", "PASS"),
                 ("mobile_existing_driver_select", {"record": self.rec, "driver_describe": [self.rec]},
                  json.dumps({"status": "SELECTED_LATER"}), "", "NO-DATA"),
                 ("mobile_existing_visual_only", {"record": self.rec, "screenshot": self.rec, "out": "o"},
                  json.dumps({"status": "GROUNDED"}), "", "PASS"),
                 ("mobile_existing_state_fixture", {"fixture_path": self.rec}, json.dumps(fixture), "", "PASS"),
                 ("mobile_existing_state_fixture", {"fixture_path": self.rec}, json.dumps(fixture),
                  "PROBLEMS:\n - x\n", "NO-DATA"),
                 ("mobile_existing_test_route", {"route_path": self.rec, "render_guide": True},
                  "# Test route: r1\n\nwhat it reaches\n", "", "PASS"),
                 ("mobile_existing_passport", {"journey_contract": self.rec, "reference_lock": self.rec,
                                               "native_evidence_v2": self.rec, "physical_device_evidence": self.rec},
                  json.dumps(done), "all dimensions PASS", "PASS"),
                 ("mobile_existing_passport", {"journey_contract": self.rec, "reference_lock": self.rec,
                                               "native_evidence_v2": self.rec, "physical_device_evidence": self.rec},
                  json.dumps({"completeness": {"failed_count": 0, "no_data_count": 2}}), "", "NO-DATA"))
        for handler, inputs, out, err, want in cases:
            got, calls = self.invoke(handler, inputs, out=out, err=err)
            self.assertEqual(1, len(calls), (handler, out))
            self.assertEqual((want, want == "PASS"), (got["verdict"], bool(got["evidence_refs"])), (handler, out, err))

    def test_pass_carries_the_command_the_output_hash_and_every_named_file(self):
        receipt = os.path.join(self.tmp, "receipt.json")
        with open(receipt, "w") as fh:
            fh.write("{}")
        got, _ = self.invoke("mobile_existing_reference_check", {"repo": self.tmp, "reference": self.rec},
                             out="mobile_workflow: PASS: ok\n" + receipt + "\n")
        refs = got["evidence_refs"]
        self.assertTrue(refs[0].startswith("mobile_workflow.py check-reference exit 0 stdout sha256:"), refs)
        self.assertEqual(64, len(refs[0].rsplit(":", 1)[1]))
        self.assertTrue(any(r.startswith(self.rec + " sha256:") for r in refs), refs)
        self.assertTrue(any(r.startswith(receipt + " sha256:") for r in refs), refs)
        got, _ = self.invoke("mobile_existing_pack", {"run": self.tmp, "out": os.path.join(self.tmp, "o.tgz")})
        self.assertIn("%s (folder)" % self.tmp, got["evidence_refs"])

    def test_every_file_of_a_list_is_hashed_into_the_evidence(self):
        other = os.path.join(self.tmp, "driver-b.json")
        with open(other, "w") as fh:
            fh.write("[]")
        got, calls = self.invoke("mobile_existing_driver_select", {"record": self.rec, "driver_describe": [self.rec, other]},
                                 out=json.dumps({"status": "SELECTED", "driver_id": "native_ios", "reason": "r", "tier": 1}))
        self.assertEqual(["--record=" + self.rec, "--driver-describe=" + self.rec, "--driver-describe=" + other],
                         calls[0][0][2:])
        self.assertEqual("PASS", got["verdict"])
        self.assertTrue(any(r.startswith(other + " sha256:") for r in got["evidence_refs"]), got["evidence_refs"])

    def test_a_timeout_or_an_unstartable_script_is_no_data(self):
        for exc in (subprocess.TimeoutExpired("x", 1), OSError("gone")):
            got, _ = self.invoke(code=exc)
            self.assertEqual(("NO-DATA", []), (got["verdict"], got["evidence_refs"]))
        self.assertIn("did not answer", self.invoke(code=subprocess.TimeoutExpired("x", 1))[0]["output"])

    def test_an_engine_this_install_does_not_carry_is_no_data_naming_the_script(self):
        with tempfile.TemporaryDirectory() as bare, mock.patch.object(door, "HERE", bare):
            self.assertIsNone(door.mobile_tool("mobile_journey_contract"))
            got, calls = self.invoke()
            self.assertEqual(([], "NO-DATA", []), (calls, got["verdict"], got["evidence_refs"]))
            self.assertIn("mobile_journey_contract.py", got["output"])
            self.assertIn("checkout", got["output"])
            self.assertIn("is not installed beside the door", got["output"])
            with mock.patch.object(door, "HERE", bare):
                got = door.run_mobile_capability("mobile.journey", None, {"record": self.rec})
            self.assertEqual(("NO-DATA", "NO-DATA", []), (got["state"], got["verdict"], got["evidence_refs"]))
            self.assertNotEqual("COMPLETE", got["state"])
        self.assertEqual(os.path.join(HERE, "mobile_workflow.py"), door.mobile_tool("mobile_workflow"))


class Probe(unittest.TestCase):
    LISTING = {"devices": {"iOS-18": [{"udid": "AB-12", "name": "iPhone 16"}], "iOS-17": [{"udid": "CD-34"}, "junk"]}}

    def listed(self, text=None, code=0):
        proc = mock.Mock(returncode=code, stdout=json.dumps(self.LISTING) if text is None else text, stderr="")
        return mock.patch.object(door.subprocess, "run", return_value=proc), \
            mock.patch.object(door.shutil, "which", return_value="/usr/bin/xcrun")

    def test_simulator_found_missing_or_unaskable(self):
        run, which = self.listed()
        with run, which:
            self.assertIs(True, door.mobile_probe("simulator"))
            self.assertIs(True, door.mobile_probe("simulator", "ab-12"))
            self.assertIs(False, door.mobile_probe("simulator", "ZZ-99"))
        run, which = self.listed('{"devices": {}}')
        with run, which:
            self.assertIs(False, door.mobile_probe("simulator"))
        for text, code in (("not json", 0), (json.dumps(self.LISTING), 1), ('{"nope": 1}', 0), ("[]", 0)):
            run, which = self.listed(text, code)
            with run, which:
                self.assertIsNone(door.mobile_probe("simulator"), (text, code))
        with mock.patch.object(door.shutil, "which", return_value=None):
            self.assertIsNone(door.mobile_probe("simulator"))
        with mock.patch.object(door.shutil, "which", return_value="/usr/bin/xcrun"), \
                mock.patch.object(door.subprocess, "run", side_effect=OSError("gone")):
            self.assertIsNone(door.mobile_probe("simulator"))
        self.assertIsNone(door.mobile_probe("something_else"))

    def test_appium_answers_only_when_the_status_page_does(self):
        seen = []

        class Resp:
            def __init__(self, status):
                self.status = status

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(url, timeout=None):
            seen.append((url, timeout))
            return Resp(200)
        with mock.patch("urllib.request.urlopen", opener):
            self.assertIs(True, door.mobile_probe("appium"))
            self.assertIs(True, door.mobile_probe("appium", "https://host:4723/"))
        self.assertEqual([("http://127.0.0.1:4723/status", 3), ("https://host:4723/status", 3)], seen)
        with mock.patch("urllib.request.urlopen", lambda url, timeout=None: Resp(503)):
            self.assertIs(False, door.mobile_probe("appium"))
        with mock.patch("urllib.request.urlopen", side_effect=OSError("refused")):
            self.assertIs(False, door.mobile_probe("appium"))
        with mock.patch("urllib.request.urlopen") as opened:
            self.assertIs(False, door.mobile_probe("appium", "file:///etc/passwd"))
            self.assertFalse(opened.called)


class RealEngine(unittest.TestCase):
    """The door reaching the real checkers over scratch files: nothing mocked but the host probes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        sys.path.insert(0, HERE)
        import test_mobile_journey_contract as T
        outcome = os.path.join(self.tmp, "outcome.json")
        with open(outcome, "w") as fh:
            json.dump(T.VALID_OUTCOME, fh)
        self.journey = os.path.join(self.tmp, "journey.json")
        with open(self.journey, "w") as fh:
            json.dump(dict(T.BASE_JOURNEY, outcome_contract_ref=outcome), fh)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_good_journey_completes_with_the_checkers_own_words_and_hashes(self):
        got = door.run_mobile_capability("mobile.journey", None, {"record": self.journey})
        self.assertEqual("COMPLETE", got["state"], got["output"])
        self.assertIn("validates as mobile-journey-contract-v1", got["output"])
        self.assertTrue(got["evidence_refs"][0].startswith("mobile_journey_contract.py exit 0 stdout sha256:"))
        self.assertTrue(any(r.startswith(self.journey + " sha256:") for r in got["evidence_refs"]))

    def test_a_bad_journey_fails_and_a_missing_one_is_no_data(self):
        with open(self.journey) as fh:
            bad = json.load(fh)
        bad["journey_id"] = 3
        with open(self.journey, "w") as fh:
            json.dump(bad, fh)
        got = door.run_mobile_capability("mobile.journey", None, {"record": self.journey})
        self.assertEqual(("FAILED", "FAIL", []), (got["state"], got["verdict"], got["evidence_refs"]))
        self.assertIn("problem", got["output"])
        got = door.run_mobile_capability("mobile.journey", None, {"record": os.path.join(self.tmp, "none.json")})
        self.assertEqual(("NO-DATA", []), (got["state"], got["evidence_refs"]))

    def test_the_real_engine_for_the_plan_and_the_screen_checker_answer_through_the_door(self):
        got = door.run_mobile_capability("mobile.plan", None, {"record": self.journey})
        self.assertEqual("COMPLETE", got["state"], got["output"])
        self.assertIsInstance(json.loads(got["output"].split("\nPASS")[0]), list)
        got = door.run_mobile_capability("mobile.screen_observation", None, {"record": self.journey})
        self.assertEqual("FAILED", got["state"], got["output"])

    def test_the_visual_only_route_over_the_real_adapter_says_it_proves_no_interaction(self):
        record = os.path.join(self.tmp, "action.json")
        with open(record, "w") as fh:
            json.dump({"x": 1}, fh)
        shot = os.path.join(self.tmp, "s.png")
        open(shot, "wb").close()
        got = door.run_mobile_capability("mobile.visual_only", None, {
            "record": record, "screenshot": shot, "out": os.path.join(self.tmp, "ev")}, ["write"])
        self.assertNotEqual("COMPLETE", got["state"], got["output"])
        self.assertIn("proves no tap, no interaction and no human acceptance", got["output"])


class Cli(unittest.TestCase):
    def test_a_mobile_id_goes_to_the_mobile_route_and_the_exit_code_follows_the_state(self):
        for state, code in (("COMPLETE", 0), ("FAILED", 1), ("REFUSED", 1), ("UNPROVEN", 1), ("BLOCKED", 2),
                            ("NO-DATA", 2)):
            with _door_view("mobile.*"), mock.patch.object(door, "run_mobile_capability", return_value={"state": state}) as mobile, \
                    mock.patch.object(door, "run_vault_capability") as vault, \
                    mock.patch.object(door, "run_data_capability") as data, mock.patch("builtins.print"):
                self.assertEqual(code, door.main(["--capability", "mobile.journey", "--input", "record=r"]), state)
            self.assertEqual((1, False, False), (mobile.call_count, vault.called, data.called))
        for cap, other in (("vault.status", "run_vault_capability"), ("data.check", "run_data_capability")):
            with _door_view("vault.*", "data.*"), mock.patch.object(door, "run_mobile_capability") as mobile, \
                    mock.patch.object(door, other, return_value={"state": "COMPLETE"}), mock.patch("builtins.print"):
                door.main(["--capability", cap])
            self.assertFalse(mobile.called, cap)

    def test_a_hidden_mobile_capability_is_refused_with_the_honest_answer_and_nothing_runs(self):
        with _door_view(), mock.patch.object(door, "run_mobile_capability") as ran, mock.patch("builtins.print") as out:
            self.assertEqual(2, door.main(["--capability", "mobile.run", "--input", "repo=r"]))
        self.assertFalse(ran.called)
        self.assertEqual(door.ADMISSION_LIMIT, json.loads(out.call_args[0][0])["output"])

    def test_typed_inputs_keep_their_types_through_the_command_line(self):
        seen = []
        with _door_view("mobile.*"), mock.patch.object(door, "run_mobile_capability",
                                                       side_effect=lambda *a: seen.append(a) or {"state": "x"}), \
                mock.patch("builtins.print"):
            door.main(["--capability", "mobile.profile_init", "--input", "repo=r", "--input-json",
                       'expected_test=["test://a"]', "--input-json", "physical_device=true"])
        self.assertEqual({"repo": "r", "expected_test": ["test://a"], "physical_device": True}, seen[0][2])

    def test_the_command_line_end_to_end_reports_a_blocked_run(self):
        bare = tempfile.mkdtemp(prefix="mobile-ops-bare-")  # no xcrun on PATH: the simulator probe cannot ask
        env = dict(os.environ, HOME=bare, PATH=bare)
        # the real command line entry, run in a fresh interpreter with the capability admitted the way an installed
        # journey row would admit it (a bare checkout admits nothing, which test_a_hidden_mobile_capability covers)
        entry = ("import sys; sys.path.insert(0, %r); import door; "
                 "door.consumer_admission = lambda *a, **k: {'candidate': 't', 'host': 'claude', "
                 "'capabilities': ['mobile.*'], 'routes': set(), 'queue': []}; "
                 "sys.exit(door.main(['--capability', 'mobile.run']))" % HERE)
        proc = subprocess.run([sys.executable, "-B", "-c", entry],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=env,
                              timeout=120)
        self.assertEqual(2, proc.returncode, proc.stderr)
        got = json.loads(proc.stdout)
        self.assertEqual(("BLOCKED", "NO-DATA"), (got["state"], got["verdict"]))
        self.assertIn("repo", got["missing_prerequisites"])
        shutil.rmtree(bare, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
