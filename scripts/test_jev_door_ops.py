#!/usr/bin/env python3
"""OUT1.h integrator cases beside the frozen CalibratedAccess contract (test_calibrated_access_contract.py).

The frozen class drives invoke_capability with a fake handler only. These cases pin what it cannot reach: the three
calibrated rows (status, review, deliver) run the EXISTING tools (jev_status.py --effective and jev_door.py) and read
their own answers; review and deliver need approve and answer BESIDE the check: the reply echoes the person's own
check verdict unchanged and is never release evidence; text reaches a tool on standard input, never its command
line; a value that is not valid, a missing approval or a missing prerequisite starts nothing; the capability list the
door can run, the admission groups, the reach check and the shipped runtime all name the same three ids.
The cases that run the real tools against files they can see change are in test_jev_door_real.py, which builds on
the fixtures here.

Hermetic. HOME and BROTHER_JEV_STATE_DIR are throwaway folders set before any product import. The real tools run as
subprocesses out of a copy of the runtime files (an installed layout whose data folder the test owns). The only
model boundary is a fixture bridge script that answers from a local table and counts its calls; the default bridge
path resolves under the throwaway HOME, where none exists, so no case can reach a provider.

Run: python3 -B scripts/test_jev_door_ops.py
"""
import atexit
import contextlib
import hashlib
import io
import json
import os
import shlex
import shutil
import sys
import tempfile
import unittest
import uuid
from datetime import date, timedelta
from unittest import mock

_HOME = tempfile.mkdtemp(prefix="jev-ops-home-")
atexit.register(shutil.rmtree, _HOME, True)
os.environ["HOME"] = _HOME
os.environ["BROTHER_JEV_STATE_DIR"] = os.path.join(_HOME, "jev-state")
for _var in ("BROTHER_TRANSPORTS", "BROTHER_DECISION_BRIDGE", "BROTHER_JEV_SEAMS_OFF", "FAKE_BRIDGE_SLEEP"):
    os.environ.pop(_var, None)
os.makedirs(os.path.join(_HOME, ".claude"))
with open(os.path.join(_HOME, ".claude", "coe-outside-gate-terms.json"), "w", encoding="utf-8") as _fh:
    json.dump({"vendor-fixture": ["ACMEWIDGET"]}, _fh)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import door  # noqa: E402
import jev_decide  # noqa: E402
import jev_door  # noqa: E402
import jev_seam  # noqa: E402
import jev_status  # noqa: E402
import reach_check  # noqa: E402

IDS = {"calibrated.status", "calibrated.review", "calibrated.deliver"}
RUNTIME_FILES = ("bridge_content_gate.py", "coe_outside_gate.py", "jev_calibration.py", "jev_cascade.py", "jev_checks.py", "jev_decide.py",
                 "jev_door.py", "jev_outcome.py", "jev_registry.py", "jev_seam.py", "jev_status.py")
FAILING_OUTPUT = "FAILED test_upload (tests.test_api)\nConnectionResetError: peer reset\n"
DONE_NOTE = "All done. The suite passed: 12 tests, 0 failures.\n"
TODAY = date(2026, 11, 2)
BRIDGE_SOURCE = '''import json, os, sys, time
with open(os.environ["FAKE_BRIDGE_LOG"], "a") as fh:
    fh.write("call\\n")
time.sleep(float(os.environ.get("FAKE_BRIDGE_SLEEP", "0")))
prob = float(os.environ.get("FAKE_BRIDGE_PROB", "0.95"))
payload = json.loads(sys.stdin.read())
print(json.dumps({"model": "%s-fixture", "usage": {"cost": 0.0},
                  "answers": {q: {"noul": prob, "confidence": prob} for q in payload["questions"]}}))
''' % jev_decide.MODEL_PREFIX


def setUpModule():
    # The one safety net: the default bridge must resolve inside the throwaway HOME, where none exists.
    assert jev_decide.DEFAULT_BRIDGE_PATH.startswith(_HOME), jev_decide.DEFAULT_BRIDGE_PATH
    assert not os.path.exists(jev_decide.DEFAULT_BRIDGE_PATH)
    assert jev_seam.JEV_STATE_DIR.startswith(_HOME), jev_seam.JEV_STATE_DIR


def snapshot(folder):
    """Every file under folder with its size, mtime and hash: what 'nothing was written' means."""
    seen = {}
    for base, dirs, files in os.walk(folder):
        for name in dirs + files:
            path = os.path.join(base, name)
            st = os.stat(path)
            digest = "dir"
            if os.path.isfile(path):
                with open(path, "rb") as fh:
                    digest = hashlib.sha256(fh.read()).hexdigest()
            seen[os.path.relpath(path, folder)] = (st.st_size, st.st_mtime_ns, digest)
    return seen


def promotion_record(family, **changes):
    rec = {"family": family, "qtype": "noul", "risk_class": "medium", "model": "fixture-model",
           "signed_by": "owner", "signed_at": "2026-10-01T00:00:00Z", "flip_condition": "bound falls below target",
           "bound_at_signing": 0.96, "n_at_signing": 30, "review_by": (TODAY + timedelta(days=30)).isoformat()}
    rec.update(changes)
    return rec


class Base(unittest.TestCase):
    """A throwaway installed layout: <tmp>/runtime holds the real tool files, <tmp>/runtime/data the config."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jev-ops-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.runtime = os.path.join(self.tmp, "runtime")
        os.makedirs(os.path.join(self.runtime, "data"))
        for name in RUNTIME_FILES:
            shutil.copy(os.path.join(HERE, name), self.runtime)
        shutil.copy(os.path.join(ROOT, "data", "jev-registry.json"), os.path.join(self.runtime, "data"))
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(self.state)
        self.ledger = os.path.join(self.state, "ledger")
        self.marker = os.path.join(self.state, "canary-reset.json")
        self.budget = os.path.join(self.state, "jev-budget.json")
        self.promotions = os.path.join(self.tmp, "promotions.jsonl")
        self.seams = os.path.join(self.runtime, "data", "jev-seams.json")
        self.registry = os.path.join(self.runtime, "data", "jev-registry.json")
        self.bridge_log = os.path.join(self.tmp, "bridge.log")
        self.write_seams(J065="shadow", J102="shadow")
        patch = mock.patch.dict(os.environ, {"BROTHER_JEV_STATE_DIR": self.state, "FAKE_BRIDGE_LOG": self.bridge_log})
        patch.start()
        self.addCleanup(patch.stop)

    def write_seams(self, **modes):
        jev_seam._CONFIG_CACHE.pop(self.seams, None)  # the loader trusts a path for a second; a rewrite must show
        with open(self.seams, "w", encoding="utf-8") as fh:
            json.dump({"modes": modes, "daily_call_budget": 50, "canary_reset_marker_path": self.marker,
                       "budget_path": self.budget, "promotions_path": self.promotions}, fh)

    @staticmethod
    def put(path, text):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def edit_registry(self, entry_id, **changes):
        with open(self.registry, encoding="utf-8") as fh:
            rows = json.load(fh)
        for row in rows:
            if row["id"] == entry_id:
                row.update(changes)
        with open(self.registry, "w", encoding="utf-8") as fh:
            json.dump(rows, fh)

    def use_bridge(self, prob="0.95", sleep="0"):
        script = os.path.join(self.tmp, "bridge.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(BRIDGE_SOURCE)
        os.environ["BROTHER_DECISION_BRIDGE"] = "%s %s" % (shlex.quote(sys.executable), shlex.quote(script))
        os.environ["FAKE_BRIDGE_PROB"] = prob
        os.environ["FAKE_BRIDGE_SLEEP"] = sleep
        for var in ("BROTHER_DECISION_BRIDGE", "FAKE_BRIDGE_PROB", "FAKE_BRIDGE_SLEEP"):
            self.addCleanup(os.environ.pop, var, None)

    def bridge_calls(self):
        try:
            with open(self.bridge_log, encoding="utf-8") as fh:
                return len(fh.read().splitlines())
        except OSError:
            return 0

    def write_promotions(self, *rows):
        with open(self.promotions, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write((row if isinstance(row, str) else json.dumps(row)) + "\n")

    def state_now(self, today=TODAY, admitted=("review", "deliver")):
        """The readout with both door routes admitted unless a case says otherwise (admission is pinned on its own
        in StatusAskableOnlyWhenAdmitted)."""
        return jev_status.effective_state(self.seams, self.registry, self.ledger, env=dict(os.environ), today=today,
                                          admitted_routes=admitted)

    def seam(self, state, seam_id):
        return next(r for r in state["seams"] if r["id"] == seam_id)

    def ask(self, capability, typed=None, allow=None, run=None, outcome=None):
        invoke = lambda h, i: door.jev_invoke(h, i, run=run, root=self.runtime)  # noqa: E731
        return door.run_jev_capability(capability, outcome, typed, allow, invoke=invoke)

    def review(self, **typed):
        typed.setdefault("output", FAILING_OUTPUT)
        typed.setdefault("check_verdict", "FAIL")
        typed.setdefault("wait_s", 10)
        return self.ask("calibrated.review", typed, ["approve"])


class TheListIsOneList(Base):
    """Omit a supported capability from discovery and one of these goes red."""

    def test_the_door_runs_exactly_the_three_and_each_authority_is_as_designed(self):
        self.assertEqual(IDS, {c["id"] for c in door.JEV_CAPABILITIES})
        for row in door.JEV_CAPABILITIES:
            self.assertEqual(["read"] if row["id"] == "calibrated.status" else ["approve"],
                             row["required_authority"], row["id"])
            self.assertTrue(row["handler"].startswith("jev_existing_"), row)
            self.assertIsNone(row["outcome_input"], "a sentence is never the text to review: " + row["id"])

    def test_every_one_is_in_an_admission_group_and_in_the_reach_check(self):
        with open(os.path.join(ROOT, "docs", "plan", "ADMISSION.json"), encoding="utf-8") as fh:
            groups = json.load(fh)["entries"]
        self.assertEqual({"calibrated.status"}, set(groups["jev-observe"]["capabilities"]))
        self.assertEqual({"calibrated.review", "calibrated.deliver"}, set(groups["jev-review"]["capabilities"]))
        self.assertEqual("MAINTAINER", groups["jev-promotion"]["tier"], "no door route signs a promotion")
        self.assertEqual([], groups["jev-promotion"]["capabilities"])
        self.assertTrue(IDS <= set(reach_check.door_capability_ids()))

    def test_the_review_group_names_the_paid_call_an_approved_deliver_makes(self):
        """ADM1.b L4: the record that admits jev-review must name the spend, and until one exists the group's next
        verification says so; any record written later must carry a spend line naming calibrated.deliver."""
        with open(os.path.join(ROOT, "docs", "plan", "ADMISSION.json"), encoding="utf-8") as fh:
            entry = json.load(fh)["entries"]["jev-review"]
        said = entry.get("next_verification", "")
        for word in ("paid", "calibrated.deliver", "J102"):
            self.assertIn(word, said, word)
        if "admission" in entry:
            self.assertIn("calibrated.deliver", str(entry["admission"].get("spend", "")))

    def test_the_breaker_is_not_claimed_as_a_rule_kept_across_door_calls(self):
        """ADM1.b L1: the seam's breaker lives in one process's memory and each door call is a fresh process."""
        with open(door.REACH_REGISTRY, encoding="utf-8") as fh:
            reason = next(r for r in json.load(fh)["inventory"] if r["id"] == "jev-review")["reason"]
        self.assertNotIn("budget, breaker", reason)
        self.assertIn("breaker is not kept across door calls", reason)

    def test_an_unknown_id_names_every_calibrated_id(self):
        got = door.run_jev_capability("calibrated.nothing")
        for cap in IDS:
            self.assertIn(cap, got["output"])

    def test_every_tool_a_row_names_exists_here_and_in_the_shipped_runtime(self):
        for row in door.JEV_CAPABILITIES:
            self.assertTrue(os.path.isfile(os.path.join(door.HERE, row["tool"])), row)
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "bundle", "runtime", row["tool"])), row)

    def test_the_reach_registry_routes_the_rows_through_the_door_and_claims_no_consumer_state(self):
        with open(door.REACH_REGISTRY, encoding="utf-8") as fh:
            data = json.load(fh)
        routes = {c["id"]: c for c in data["capabilities"]}
        for cap_id, verb in (("second-opinion-status", "status"), ("second-opinion-review", "review"),
                             ("second-opinion-deliver", "deliver")):
            self.assertEqual("def run_jev_capability", routes[cap_id]["marker"])
            self.assertEqual("scripts/door.py", routes[cap_id]["command"])
            self.assertEqual(verb, routes[cap_id]["verb"])
        rows = {r["id"]: r for r in data["inventory"]}
        self.assertEqual("second-opinion-status", rows["jev-observe"]["routed"])
        self.assertEqual("second-opinion-review", rows["jev-review"]["routed"])
        self.assertIsNone(rows["jev-promotion"]["routed"])
        for name in ("jev-observe", "jev-review", "jev-promotion"):
            self.assertEqual("pending", rows[name]["disposition"], "no installed end to end run exists yet")
            self.assertEqual("NO-DATA", rows[name]["installed_execution"])

    def test_nothing_is_offered_to_a_consumer_until_it_earns_admission_and_a_hidden_call_starts_nothing(self):
        view = door.consumer_admission()
        for cap in IDS:
            self.assertFalse(door.capability_admitted(cap, view), cap)
        out = io.StringIO()
        with mock.patch.object(door, "jev_invoke", side_effect=AssertionError("must not run")), \
                contextlib.redirect_stdout(out):
            code = door.main(["--capability", "calibrated.status"])
        self.assertEqual(2, code)
        self.assertEqual("BLOCKED", json.loads(out.getvalue())["state"])


class Authority(Base):
    """Which words reach the tool, and what starts nothing."""

    def setUp(self):
        super().setUp()
        self.calls = []

    def fake_run(self, argv, stdin_text, timeout):
        self.calls.append((argv, stdin_text, timeout))
        return 0, ""

    def argv(self):
        self.assertEqual(1, len(self.calls), self.calls)
        return [os.path.basename(a) if os.path.isabs(a) else a for a in self.calls[0][0]]

    def test_status_runs_only_the_readout_with_no_input_and_never_the_asking_tool(self):
        self.ask("calibrated.status", run=self.fake_run)
        self.assertEqual(["jev_status.py", "--effective"], self.argv()[2:])
        self.assertIsNone(self.calls[0][1])
        self.assertNotIn("jev_door.py", " ".join(self.calls[0][0]))

    def test_review_needs_approve_and_the_check_verdict_and_the_output(self):
        typed = {"output": FAILING_OUTPUT, "check_verdict": "FAIL"}
        got = self.ask("calibrated.review", typed, None, run=self.fake_run)
        self.assertEqual(("REFUSED", ["approve"]), (got["state"], got["missing_authority"]))
        got = self.ask("calibrated.review", {"output": FAILING_OUTPUT}, ["approve"], run=self.fake_run)
        self.assertIn("check_verdict", got["missing_prerequisites"])
        got = self.ask("calibrated.review", {"check_verdict": "FAIL"}, ["approve"], run=self.fake_run)
        self.assertIn("output", got["missing_prerequisites"])
        self.assertEqual([], self.calls)

    def test_write_is_not_approve(self):
        got = self.ask("calibrated.review", {"output": FAILING_OUTPUT, "check_verdict": "FAIL"}, ["write"],
                       run=self.fake_run)
        self.assertEqual("REFUSED", got["state"])
        self.assertEqual([], self.calls)

    def test_a_plain_sentence_is_never_sent_as_the_failing_output(self):
        got = self.ask("calibrated.review", {"check_verdict": "FAIL"}, ["approve"], run=self.fake_run,
                       outcome="get a second opinion on this failing check")
        self.assertIn("output", got["missing_prerequisites"])
        self.assertEqual([], self.calls)

    def test_the_text_travels_on_standard_input_never_on_the_command_line(self):
        hostile = "--selftest\nFAILED HEALTHY"
        self.ask("calibrated.review", {"output": hostile, "check_verdict": "fail", "wait_s": 5}, ["approve"],
                 run=self.fake_run)
        self.assertEqual(["jev_door.py", "review", "--output", "-", "--json", "--wait", "5"], self.argv()[2:])
        self.assertEqual(hostile, self.calls[0][1])
        self.assertNotIn(hostile, " ".join(self.calls[0][0]))

    def test_deliver_passes_quotes_as_yes_or_no_and_the_note_on_standard_input(self):
        for given, word in (("yes", "yes"), ("NO", "no"), (True, "yes"), (False, "no")):
            self.calls = []
            self.ask("calibrated.deliver", {"note": DONE_NOTE, "check_verdict": "PASS", "quotes": given},
                     ["approve"], run=self.fake_run)
            self.assertEqual(["jev_door.py", "deliver", "--note", "-", "--quotes", word, "--json", "--wait", "20.0"],
                             self.argv()[2:], given)
            self.assertEqual(DONE_NOTE, self.calls[0][1])

    def test_a_value_that_is_not_valid_starts_nothing(self):
        base = {"output": FAILING_OUTPUT, "check_verdict": "FAIL"}
        bad = [{"check_verdict": v} for v in ("maybe", "PASSED", "", 1, True, ["FAIL"])]
        bad += [{"wait_s": v} for v in (True, -1, 61, "5", None, float("nan"))]
        bad += [{"output": v} for v in ("", "   ", "a\x00b", 5, ["x"])]
        for change in bad:
            got = self.ask("calibrated.review", dict(base, **change), ["approve"], run=self.fake_run)
            self.assertNotEqual("COMPLETE", got["state"], change)
        self.assertEqual([], self.calls)
        deliver = {"note": DONE_NOTE, "check_verdict": "PASS", "quotes": "yes"}
        for change in ({"quotes": "maybe"}, {"quotes": ["yes"]}, {"quotes": 1}, {"note": ""}, {"note": "a\x00"}):
            got = self.ask("calibrated.deliver", dict(deliver, **change), ["approve"], run=self.fake_run)
            self.assertNotEqual("COMPLETE", got["state"], change)
        self.assertEqual([], self.calls)

    def test_the_door_passes_no_config_registry_or_mode_path(self):
        self.ask("calibrated.review", {"output": FAILING_OUTPUT, "check_verdict": "FAIL", "seams_config": "/x",
                                       "registry": "/y", "mode": "act"}, ["approve"], run=self.fake_run)
        # Only what the door passes after the tool: the interpreter and the tool's own path (a random temp folder)
        # can hold any short word, so a substring check over the whole line failed by chance on a name like t-qk2xactl.
        passed = self.argv()[3:]
        self.assertEqual(["review", "--output", "-", "--json", "--wait", "20.0"], passed)
        for word in ("seams", "registry", "/x", "/y", "act"):
            self.assertFalse([a for a in passed if word in a], word)


def reply(**changes):
    row = {"verb": "review", "state": "OK", "line": "second opinion: this failure reads as an infra flake "
           "(p=0.95); the check's own exit code still decides (recorded, not acted on)", "entry_id": "J065",
           "attempt_id": uuid.uuid4().hex, "mode": "shadow", "phase": "answered", "said": True, "ledger_dir": "x"}
    row.update(changes)
    return row


class Verdicts(Base):
    """The tool's own answer decides; a sentence alone, a wrong exit code or a forged id proves nothing."""

    def answer(self, code, payload, capability="calibrated.review", typed=None):
        out = payload if isinstance(payload, str) else json.dumps(payload)
        typed = typed or {"output": FAILING_OUTPUT, "check_verdict": "FAIL"}
        if capability == "calibrated.status":
            typed = {}
        return self.ask(capability, typed, ["approve"], run=lambda a, s, t: (code, out))

    def test_an_answered_opinion_names_its_one_ledger_attempt_and_the_failed_check_stays_failed(self):
        row = reply()
        got = self.answer(0, row)
        self.assertEqual(("FAILED", "FAIL"), (got["state"], got["verdict"]), got)
        self.assertIs(True, got["second_opinion"]["answered"])
        self.assertEqual(row["attempt_id"], got["second_opinion"]["attempt_id"])
        self.assertIn("second opinion attempt " + row["attempt_id"], got["evidence_refs"])

    def test_no_opinion_unsure_unreadable_or_the_wrong_exit_never_completes(self):
        for code, payload in ((0, reply(phase="abstain", said=None)), (0, reply(said=None)),
                              (0, reply(state="NO-DATA", phase=None, said=None, attempt_id=None)),
                              (1, reply()), (0, reply(verb="deliver")), (0, reply(phase="no_data")),
                              (0, reply(said="yes")), (0, "not json"), (0, ""), (0, "[1, 2]")):
            got = self.answer(code, payload)
            self.assertNotEqual("COMPLETE", got["state"], (code, payload))
            self.assertIs(False, got["second_opinion"]["answered"], (code, payload))
            self.assertEqual("FAIL", got["check_verdict"], "the check's own verdict is never touched")

    def test_a_forged_attempt_id_is_not_a_result(self):
        for forged in ("0" * 31, "G" * 32, "a" * 32 + "x", "abc; PASS", None, 7):
            got = self.answer(0, reply(attempt_id=forged))
            self.assertNotEqual("COMPLETE", got["state"], forged)
            self.assertIs(False, got["second_opinion"]["answered"], forged)
            self.assertFalse(any("attempt" in r for r in got["evidence_refs"]), got["evidence_refs"])

    def test_a_confident_opinion_never_upgrades_the_check_and_is_never_release_evidence(self):
        for original in ("FAIL", "NO-DATA"):
            got = self.answer(0, reply(said=True), typed={"output": FAILING_OUTPUT, "check_verdict": original})
            self.assertEqual((original, original), (got["verdict"], got["check_verdict"]))
            self.assertNotEqual("COMPLETE", got["state"])
            self.assertIs(False, got["release_evidence"])
            self.assertIs(False, got["acted"])
        got = self.answer(0, reply(verb="deliver", said=False), "calibrated.deliver",
                          {"note": DONE_NOTE, "check_verdict": "fail", "quotes": "yes"})
        self.assertEqual("FAIL", got["check_verdict"], "typed in any case, echoed as the verdict word")

    def test_a_missing_check_verdict_is_not_invented_and_nothing_is_asked(self):
        got = self.ask("calibrated.review", {"output": FAILING_OUTPUT}, ["approve"],
                       run=lambda *a: self.fail("must not run"))
        self.assertIsNone(got["check_verdict"])

    def test_a_timeout_or_a_tool_that_cannot_start_is_no_data(self):
        for pair in ((None, "did not answer within 140 seconds and was stopped"), (None, "could not run: boom")):
            got = self.ask("calibrated.review", {"output": FAILING_OUTPUT, "check_verdict": "FAIL"}, ["approve"],
                           run=lambda a, s, t, pair=pair: pair)
            self.assertEqual(("NO-DATA", "FAIL", False), (got["verdict"], got["check_verdict"], got["acted"]))
            self.assertNotEqual("COMPLETE", got["state"])

    def test_the_tool_deadline_covers_the_wait_the_person_asked_for(self):
        seen = []
        self.ask("calibrated.review", {"output": FAILING_OUTPUT, "check_verdict": "FAIL", "wait_s": 45}, ["approve"],
                 run=lambda a, s, t: seen.append(t) or (0, ""))
        self.assertEqual(door.JEV_TIMEOUT_S + 45, seen[0])

    def test_status_is_a_pass_only_when_the_readout_says_it_could_read(self):
        good = {"kind": "jev_effective_state", "readable": True, "lines": ["second opinions: 1 seam(s)"]}
        self.assertEqual("COMPLETE", self.answer(0, good, "calibrated.status")["state"])
        for code, payload in ((2, dict(good, readable=False)), (0, dict(good, readable=False)),
                              (0, dict(good, kind="other")), (0, dict(good, lines=[])), (0, dict(good, lines=[1])),
                              (0, {}), (0, "x"), (1, good)):
            got = self.answer(code, payload, "calibrated.status")
            self.assertNotEqual("COMPLETE", got["state"], (code, payload))
        words = self.answer(2, dict(good, readable=False), "calibrated.status")["output"]
        self.assertIn("second opinions", words, "a readout that could not read still says why")


class TheDoorsOwnCommandLine(Base):
    """door.py --capability, the production entry point, with the admission decision stubbed to admit the rows (a
    consumer sees none of them until an installed run earns it). The tools are the real ones in scripts/."""

    def admitted(self):
        view = {"candidate": "c", "host": "h", "capabilities": sorted(IDS), "routes": set(), "queue": []}
        return mock.patch.object(door, "consumer_admission", return_value=view)

    def run_door(self, argv):
        out = io.StringIO()
        with self.admitted(), contextlib.redirect_stdout(out):
            code = door.main(argv)
        return code, json.loads(out.getvalue())

    def test_status_through_main_is_complete_and_reads_the_throwaway_state(self):
        code, got = self.run_door(["--capability", "calibrated.status"])
        self.assertEqual((0, "COMPLETE", False), (code, got["state"], got["acted"]), got)
        self.assertIn("J065", {r["id"] for r in got["state_detail"]["seams"]})

    def test_review_through_main_with_the_tracked_config_asks_nothing_and_keeps_the_verdict(self):
        code, got = self.run_door(["--capability", "calibrated.review", "--allow", "approve",
                                   "--input", "check_verdict=FAIL", "--input", "output=" + FAILING_OUTPUT])
        self.assertNotEqual("COMPLETE", got["state"], "J065 ships off, so nothing is asked")
        self.assertEqual(("FAIL", "FAIL", False, 1), (got["verdict"], got["check_verdict"], got["release_evidence"],
                                                      code))
        self.assertIs(False, got["second_opinion"]["answered"])
        self.assertFalse(os.path.exists(self.ledger), "nothing was asked, so nothing was written")

    def test_review_through_main_without_approve_is_refused_before_anything_runs(self):
        with mock.patch.object(door, "jev_invoke", side_effect=AssertionError("must not run")):
            code, got = self.run_door(["--capability", "calibrated.review", "--input", "check_verdict=FAIL",
                                       "--input", "output=" + FAILING_OUTPUT])
        self.assertEqual(("REFUSED", 1), (got["state"], code))

    def test_a_runtime_folder_with_a_space_in_its_path_works(self):
        spaced = os.path.join(self.tmp, "a folder with spaces")
        shutil.copytree(self.runtime, spaced)
        invoke = lambda h, i: door.jev_invoke(h, i, root=spaced)  # noqa: E731
        got = door.run_jev_capability("calibrated.status", None, {}, None, invoke=invoke)
        self.assertEqual("COMPLETE", got["state"], got)


class CalibratedVerdictIsTheCheck(Base):
    """ADM1.b, through door.main as a caller runs it (admission stubbed open, the tool's process boundary answering
    from a table): the row's verdict, state and exit code are the person's typed check verdict, whatever the second
    opinion said or whether one came at all. The opinion lives only under second_opinion."""

    EXPECT = {"PASS": ("COMPLETE", 0), "FAIL": ("FAILED", 1), "NO-DATA": ("NO-DATA", 2)}
    admitted = TheDoorsOwnCommandLine.admitted
    run_door = TheDoorsOwnCommandLine.run_door

    def door_with(self, capability, check, tool_reply):
        typed = (["--input", "output=" + FAILING_OUTPUT] if capability == "calibrated.review"
                 else ["--input", "note=" + DONE_NOTE, "--input", "quotes=yes"])
        out = json.dumps(tool_reply)
        with mock.patch.object(door, "_jev_run", lambda argv, stdin_text, timeout: (0, out)):
            return self.run_door(["--capability", capability, "--allow", "approve",
                                  "--input", "check_verdict=" + check] + typed)

    def opinions(self, verb):
        entry = "J065" if verb == "review" else "J102"
        return (("agrees", reply(verb=verb, entry_id=entry, said=True)),
                ("dissents", reply(verb=verb, entry_id=entry, said=False)),
                ("not answered", reply(verb=verb, entry_id=entry, state="NO-DATA", phase="no_data", said=None,
                                       attempt_id=None)))

    def test_every_check_verdict_with_every_opinion_reads_as_the_check(self):
        for capability in ("calibrated.review", "calibrated.deliver"):
            verb = capability.split(".")[1]
            for check, (state, exit_code) in self.EXPECT.items():
                for name, tool_reply in self.opinions(verb):
                    case = (capability, check, name)
                    code, got = self.door_with(capability, check, tool_reply)
                    self.assertEqual((check, state, exit_code), (got["verdict"], got["state"], code), case)
                    self.assertEqual(name != "not answered", got["second_opinion"]["answered"], case)
                    self.assertEqual("check verdict %s, as the person typed it" % check, got["evidence_refs"][0],
                                     "the row's evidence names where its verdict came from: %s" % (case,))
                    self.assertIs(False, got["release_evidence"], case)

    def test_a_failed_check_with_an_answered_opinion_never_reads_passed(self):
        for capability in ("calibrated.review", "calibrated.deliver"):
            verb = capability.split(".")[1]
            code, got = self.door_with(capability, "FAIL", reply(verb=verb, said=True))
            self.assertIs(True, got["second_opinion"]["answered"], capability)
            self.assertNotEqual("PASS", got["verdict"], capability)
            self.assertNotEqual("COMPLETE", got["state"], capability)
            self.assertNotEqual(0, code, capability)

    def test_a_no_data_check_with_an_answered_opinion_reads_no_data(self):
        code, got = self.door_with("calibrated.review", "no-data", reply(said=True))
        self.assertIs(True, got["second_opinion"]["answered"])
        self.assertEqual(("NO-DATA", "NO-DATA", 2), (got["verdict"], got["state"], code))

    def test_a_passed_check_with_a_dissenting_opinion_still_reads_passed_and_the_dissent_is_kept(self):
        dissent = reply(verb="deliver", entry_id="J102", said=False)
        code, got = self.door_with("calibrated.deliver", "PASS", dissent)
        self.assertEqual(("PASS", "COMPLETE", 0), (got["verdict"], got["state"], code))
        self.assertEqual((False, dissent["attempt_id"], dissent["line"]),
                         (got["second_opinion"]["said"], got["second_opinion"]["attempt_id"],
                          got["second_opinion"]["line"]))

    def test_a_tool_that_never_answered_is_never_a_pass_whatever_the_check(self):
        for check in self.EXPECT:
            with mock.patch.object(door, "_jev_run", lambda a, s, t: (None, "could not run: boom")):
                code, got = self.run_door(["--capability", "calibrated.review", "--allow", "approve", "--input",
                                           "check_verdict=" + check, "--input", "output=" + FAILING_OUTPUT])
            self.assertEqual(("NO-DATA", "NO-DATA", 2), (got["verdict"], got["state"], code), check)

    def test_through_main_with_the_real_tool_and_a_bridge_that_answers_the_check_still_decides(self):
        """No scripted reply: the real jev_door.py out of the throwaway runtime (J065 and J102 in shadow), the fixture
        bridge answering, door.main reading the result."""
        self.use_bridge(prob="0.95")
        cases = (("calibrated.review", "FAIL", ["--input", "output=" + FAILING_OUTPUT]),
                 ("calibrated.deliver", "FAIL", ["--input", "note=" + DONE_NOTE, "--input", "quotes=yes"]),
                 ("calibrated.deliver", "PASS", ["--input", "note=" + DONE_NOTE, "--input", "quotes=yes"]),
                 ("calibrated.review", "NO-DATA", ["--input", "output=" + FAILING_OUTPUT]))
        for number, (capability, check, typed) in enumerate(cases, 1):
            with mock.patch.object(door, "HERE", self.runtime):
                code, got = self.run_door(["--capability", capability, "--allow", "approve", "--input",
                                           "check_verdict=" + check, "--input-json", "wait_s=10"] + typed)
            state, exit_code = self.EXPECT[check]
            self.assertIs(True, got["second_opinion"]["answered"], (capability, check, got))
            self.assertEqual(number, self.bridge_calls(), "each case really asked the bridge once")
            self.assertEqual((check, state, exit_code), (got["verdict"], got["state"], code), (capability, check))

    def test_a_check_verdict_that_is_not_one_of_the_three_is_never_a_pass_even_if_a_handler_answers(self):
        answered = lambda h, i: {"verdict": "PASS", "output": "x", "evidence_refs": ["t exit 0"]}  # noqa: E731
        got = door.run_jev_capability("calibrated.review", None, {"output": FAILING_OUTPUT, "check_verdict": "PASSED"},
                                      ["approve"], invoke=answered)
        self.assertEqual(("NO-DATA", "NO-DATA"), (got["verdict"], got["state"]))


class StatusAskableOnlyWhenAdmitted(Base):
    """ADM1.b AB2: calibrated.status calls a seam askable through the door only on a route consumer admission shows.
    Through door.main with the real jev_status.py out of the throwaway runtime (J065 and J102 in shadow, a bridge
    installed, so admission is the only thing that differs)."""

    def status_with(self, admitted):
        view = {"candidate": "c", "host": "h", "capabilities": ["calibrated.status"] + list(admitted),
                "routes": set(), "queue": []}
        out = io.StringIO()
        with mock.patch.object(door, "consumer_admission", return_value=view), \
                mock.patch.object(door, "HERE", self.runtime), contextlib.redirect_stdout(out):
            code = door.main(["--capability", "calibrated.status"])
        got = json.loads(out.getvalue())
        self.assertEqual((0, "COMPLETE"), (code, got["state"]), got)
        return got["state_detail"]

    def askable(self, state):
        return {r["id"]: (r["askable_here"], r["eligible_now"]) for r in state["seams"] if r["id"] in ("J065", "J102")}

    def test_a_route_hidden_from_consumers_is_never_listed_askable(self):
        self.use_bridge()
        state = self.status_with([])
        self.assertEqual({"J065": (None, False), "J102": (None, False)}, self.askable(state))
        self.assertIn("0 askable now through the door", state["lines"][0])
        self.assertTrue(any("door route review is not admitted" in line for line in state["lines"]))

    def test_each_admitted_route_and_only_it_is_askable(self):
        self.use_bridge()
        self.assertEqual({"J065": ("review", True), "J102": (None, False)},
                         self.askable(self.status_with(["calibrated.review"])))
        self.assertEqual({"J065": ("review", True), "J102": ("deliver", True)},
                         self.askable(self.status_with(["calibrated.review", "calibrated.deliver"])))

    def test_the_readout_alone_admits_nothing_and_refuses_an_unknown_route(self):
        self.use_bridge()
        state = self.state_now(admitted=None)
        self.assertFalse(any(r["eligible_now"] or r["askable_here"] for r in state["seams"]))
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            jev_status.main(["--effective", "--admitted", "everything"])


class TheLowLevelCommand(Base):
    """jev_door.py keeps its one line; --json adds only the ledger row's name."""

    def run_main(self, argv, stdin_text=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(sys, "stdin", io.StringIO(stdin_text or "")):
            code = jev_door.main(argv)
        return code, out.getvalue()

    def test_the_default_output_is_still_one_line_and_json_adds_no_verdict_field(self):
        missing = os.path.join(self.tmp, "absent.txt")
        code, text = self.run_main(["review", "--output", missing])
        self.assertEqual((0, 1), (code, len(text.splitlines())))
        code, text = self.run_main(["review", "--output", missing, "--json"])
        got = json.loads(text)
        self.assertEqual(0, code)
        self.assertEqual({"verb", "state", "line", "entry_id", "attempt_id", "mode", "phase", "said"},
                         set(got))
        self.assertEqual("NO-DATA", got["state"])
        self.assertIsNone(got["attempt_id"])

    def test_a_dash_reads_standard_input_and_nothing_else(self):
        """Real text on standard input is read (the tracked config has J065 off, so the line says so); a blank one
        is nothing to ask. A file named dash is not what the dash means."""
        code, text = self.run_main(["review", "--output", "-", "--json", "--wait", "0"], FAILING_OUTPUT)
        self.assertIn("is off in the Jev seams config", json.loads(text)["line"])
        code, text = self.run_main(["review", "--output", "-", "--json", "--wait", "0"], "   ")
        self.assertIn("nothing to ask", json.loads(text)["line"])
        code, text = self.run_main(["deliver", "--note", "-", "--quotes", "yes", "--json", "--wait", "0"], "   ")
        self.assertIn("nothing to ask", json.loads(text)["line"])


if __name__ == "__main__":
    unittest.main()
