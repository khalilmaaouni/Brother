#!/usr/bin/env python3
"""OUT1.e integrator cases beside the frozen DataAccess contract (scripts/test_data_access_contract.py).

The frozen class drives invoke_capability with a fake handler only. These cases pin what it cannot reach: the door's
data capability table and bds.py's DOOR_HANDLERS agree, door_invoke refuses an unknown or shell shaped handler and
any untyped input before anything runs, consent fails closed, only a PASS with exit 0 AND output completes, a command
that reports its own NO-DATA never completes, and the real journey over scratch files reaches the existing tools: a
claim authored through the door keeps its booleans, numbers and lists, each pack runs inside check, the MDM review
and the Japanese identifier keys come back from the real commands, and a missing file is NO-DATA naming what to
supply.

Run: python3 -B scripts/test_data_door.py
"""
import importlib.util
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
DS = os.path.join(ROOT, "products", "brotherds")
EXAMPLES = os.path.join(DS, "examples")
sys.path.insert(0, HERE)
import door  # noqa: E402


def _door_view(*capabilities):
    """The consumer view (door.consumer_admission) these routing tests run the door under: only `capabilities`
    are admitted, as an installed journey row or a maintainer's record would admit them (OUT1.n). No capabilities
    is the hidden case."""
    return mock.patch.object(door, "consumer_admission", return_value={
        "candidate": "test", "host": "claude", "capabilities": list(capabilities), "routes": set(), "queue": []})

sys.path.insert(0, DS)
_spec = importlib.util.spec_from_file_location("bds_for_door_test", os.path.join(DS, "bds.py"))
bds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bds)

GOOD = {"verdict": "PASS", "output": "checked", "evidence_refs": ["e1"]}
#: what the real mdm-eval review prints (mdm_eval.py): JSON carrying precision_above and recall
REVIEW_JSON = json.dumps({"merge_threshold": 0.8, "n_rows": 4, "precision_above": {"estimate": 0.98, "lo": 0.9, "hi": 1.0},
                          "recall": {"recall": 0.5}, "strata": []}, indent=2) + "\n"
WRITES = {"data.author", "data.register", "data.score", "data.mdm_audit_plan", "data.mdm_resolve"}


class TableAgreement(unittest.TestCase):
    def test_every_door_capability_has_its_handler_with_the_same_inputs(self):
        door_rows = {c["handler"]: c for c in door.DATA_CAPABILITIES}
        self.assertEqual(sorted(bds.DOOR_HANDLERS), sorted(door_rows))
        for handler, (tool, _sub, required, _opt) in bds.DOOR_HANDLERS.items():
            self.assertEqual(list(required), door_rows[handler]["prerequisites"], handler)
            if os.path.isfile(os.path.join(DS, tool)):
                continue
            # an edition without this tool (the public export) answers NO-DATA and runs nothing
            calls = []
            got = bds.door_invoke(handler, {}, lambda argv, stdin_text: calls.append(argv) or (0, "x", ""))
            self.assertEqual(("NO-DATA", [], []), (got["verdict"], got["evidence_refs"], calls), handler)
            self.assertIn("not in this edition", got["output"], handler)

    def test_only_the_writing_capabilities_need_write(self):
        auth = {c["id"]: c["required_authority"] for c in door.DATA_CAPABILITIES}
        self.assertEqual({k for k, v in auth.items() if v == ["write"]}, WRITES)
        self.assertTrue(all(v in (["read"], ["write"]) for v in auth.values()))

    def test_the_frozen_contract_ids_are_the_review_row(self):
        row = next(c for c in door.DATA_CAPABILITIES if c["id"] == "data.mdm_review")
        self.assertEqual("data_existing_mdm_review", row["handler"])
        self.assertEqual(("bds.py", ("mdm-eval", "review")), bds.DOOR_HANDLERS["data_existing_mdm_review"][:2])

    def test_every_mdm_row_runs_an_mdm_command_never_help(self):
        for handler, (tool, sub, _req, _opt) in bds.DOOR_HANDLERS.items():
            if "_mdm_" in handler:
                self.assertTrue(tool.startswith("mdm_") or (sub and sub[0].startswith("mdm-")), handler)
            self.assertNotIn("help", sub)


class DoorRoute(unittest.TestCase):
    def test_a_zero_is_given_and_authority_is_only_what_was_allowed(self):
        seen = []
        got = door.run_data_capability("data.score", None, {"claim_path": "c.json", "actual": 0}, ["write"],
                                       lambda h, i: seen.append((h, i)) or GOOD)
        self.assertEqual([("data_existing_score", {"claim_path": "c.json", "actual": 0})], seen)
        self.assertEqual("COMPLETE", got["state"])
        got = door.run_data_capability("data.score", None, {"claim_path": "c.json", "actual": 0}, None,
                                       lambda h, i: GOOD)
        self.assertEqual(("REFUSED", ["write"]), (got["state"], got["missing_authority"]))

    def test_the_outcome_fills_the_text_and_every_reply_says_experimental(self):
        seen = []
        got = door.run_data_capability("data.mdm_normalize", "株式会社テスト商事", {"kind": "company", "locale": "ja"},
                                       None, lambda h, i: seen.append(i) or GOOD)
        self.assertEqual("株式会社テスト商事", seen[0]["text"])
        self.assertIn("experimental", got["status"])

    def test_an_unknown_data_capability_is_blocked(self):
        got = door.run_data_capability("data.nope", "x", invoke=lambda h, i: GOOD)
        self.assertEqual("BLOCKED", got["state"])
        self.assertIn("data.mdm_review", got["output"])

    def test_cli_routes_data_ids_to_the_data_route_with_typed_inputs(self):
        seen = []

        def runner(cap, outcome, typed, allow):
            seen.append((cap, typed, allow))
            return dict(state="COMPLETE")
        with _door_view("data.*", "vault.*"), mock.patch.object(door, "run_data_capability", side_effect=runner), \
                mock.patch("builtins.print"):
            code = door.main(["--capability", "data.mdm_review", "--input", "review_csv=r.csv",
                              "--input-json", "merge_threshold=0.8"])
        self.assertEqual(0, code)
        self.assertEqual([("data.mdm_review", {"review_csv": "r.csv", "merge_threshold": 0.8}, None)], seen)
        with _door_view("data.*", "vault.*"), mock.patch.object(door, "run_data_capability", return_value=dict(state="FAILED")), \
                mock.patch.object(door, "run_vault_capability") as vault, mock.patch("builtins.print"):
            self.assertEqual(1, door.main(["--capability", "data.check"]))
        self.assertFalse(vault.called)

    def test_a_hidden_data_capability_is_refused_with_the_honest_answer_and_nothing_runs(self):
        with _door_view(), mock.patch.object(door, "run_data_capability") as ran, mock.patch("builtins.print") as out:
            self.assertEqual(2, door.main(["--capability", "data.check"]))
        self.assertFalse(ran.called)
        self.assertEqual(door.ADMISSION_LIMIT, json.loads(out.call_args[0][0])["output"])

    def test_typed_json_inputs_keep_their_types(self):
        got = door._typed_inputs([], ['claim={"a": true, "n": 2, "l": [1]}', "flag=false"])
        self.assertEqual({"claim": {"a": True, "n": 2, "l": [1]}, "flag": False}, got)
        with self.assertRaises(ValueError):
            door._typed_inputs([], ["x={not json"])

    def test_a_missing_tool_is_no_data_and_an_unreadable_reply_fails(self):
        with mock.patch.object(door, "data_tool", return_value=None):
            got = door.data_invoke("data_existing_check", {})
        self.assertEqual("NO-DATA", got["verdict"])
        self.assertIn("install", got["output"])
        with tempfile.TemporaryDirectory() as tmp:
            tool = os.path.join(tmp, "bds.py")
            with open(tool, "w") as fh:
                fh.write("print('done')\n")
            got = door.data_invoke("data_existing_check", {}, tool=tool)
        self.assertEqual(("FAIL", []), (got["verdict"], got["evidence_refs"]))

    def test_the_checkout_tool_is_found(self):
        self.assertEqual(os.path.join(ROOT, "products", "brotherds", "bds.py"), door.data_tool())


class DoorInvoke(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.csv = os.path.join(self.tmp, "r.csv")
        with open(self.csv, "w") as fh:
            fh.write("score,label\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def invoke(self, handler="data_existing_mdm_review", inputs=None, code=0, out=REVIEW_JSON, consented=True):
        calls = []

        def run(argv, stdin_text):
            calls.append((argv, stdin_text))
            return code, out, ""
        inputs = {"review_csv": self.csv, "merge_threshold": 0.8} if inputs is None else inputs
        # argv shaping is tested whatever this edition carries; the edition cases below call the real check
        with mock.patch.object(bds, "_door_consented", return_value=consented), \
                mock.patch.object(bds, "_door_tool_present", return_value=True):
            got = bds.door_invoke(handler, inputs, run)
        return got, calls

    def test_a_tool_absent_from_this_edition_is_no_data_and_never_launches(self):
        calls = []
        with mock.patch.object(bds, "DOOR_HERE", self.tmp), mock.patch.object(bds, "_door_consented", return_value=True):
            got = bds.door_invoke("data_existing_mdm_review", {"review_csv": self.csv, "merge_threshold": 0.8},
                                  lambda argv, stdin_text: calls.append(argv) or (0, "x", ""))
        self.assertEqual(("NO-DATA", [], []), (got["verdict"], got["evidence_refs"], calls))
        self.assertIn("bds.py is not in this edition", got["output"])

    def test_exit_zero_with_output_is_pass_with_the_output_hash(self):
        got, calls = self.invoke()
        self.assertEqual([(["bds.py", "mdm-eval", "review", self.csv, "--merge-threshold=0.8"], "")], calls)
        self.assertEqual("PASS", got["verdict"])
        self.assertIn(bds.hashlib.sha256(REVIEW_JSON.encode()).hexdigest(), got["evidence_refs"][0])
        # VERD1.a: exit 0 with output that carries none of the review's own keys was PASS
        self.assertEqual("NO-DATA", self.invoke(out="{}\n")[0]["verdict"])
        self.assertIn("experimental", got["status"])

    def test_a_command_reporting_its_own_no_data_never_passes(self):
        got, _ = self.invoke(out="claim x\n\nVERDICT NO-DATA\n")
        self.assertEqual(("NO-DATA", []), (got["verdict"], got["evidence_refs"]))
        self.assertEqual("PASS", self.invoke(out=REVIEW_JSON)[0]["verdict"])
        self.assertEqual("NO-DATA", self.invoke(out="VERDICT PASS\n")[0]["verdict"])  # was PASS: no review keys

    def test_exit_codes_and_empty_output(self):
        self.assertEqual(("FAIL", []), tuple(self.invoke(code=1)[0][k] for k in ("verdict", "evidence_refs")))
        self.assertEqual(("NO-DATA", []), tuple(self.invoke(code=2)[0][k] for k in ("verdict", "evidence_refs")))
        self.assertEqual(("NO-DATA", []), tuple(self.invoke(out="")[0][k] for k in ("verdict", "evidence_refs")))

    def test_an_unknown_or_shell_shaped_handler_runs_nothing(self):
        for handler in ("data_existing_nope", "data_existing_check; rm -rf /", "../bds.py", "", None, 3,
                        "data_existing_mdm_review --help"):
            got, calls = self.invoke(handler=handler)
            self.assertEqual(([], "NO-DATA"), (calls, got["verdict"]), handler)

    def test_no_consent_runs_nothing(self):
        got, calls = self.invoke(consented=False)
        self.assertEqual(([], "NO-DATA"), (calls, got["verdict"]))
        self.assertIn("consent", got["output"])

    def test_untyped_inputs_run_nothing(self):
        for inputs in (["x"], {}, {"review_csv": self.csv}, {"review_csv": self.csv, "merge_threshold": "0.8"},
                       {"review_csv": self.csv, "merge_threshold": True},
                       {"review_csv": self.csv, "merge_threshold": float("nan")},
                       {"review_csv": [self.csv], "merge_threshold": 0.8},
                       {"review_csv": "--help", "merge_threshold": 0.8}):
            got, calls = self.invoke(inputs=inputs)
            self.assertEqual(([], "NO-DATA"), (calls, got["verdict"]), inputs)

    def test_a_missing_file_names_what_to_supply(self):
        missing = os.path.join(self.tmp, "absent.csv")
        got, calls = self.invoke(inputs={"review_csv": missing, "merge_threshold": 0.8})
        self.assertEqual([], calls)
        self.assertIn("supply", got["output"])
        self.assertIn(missing, got["output"])

    def test_a_claim_object_travels_as_json_and_must_be_an_object(self):
        claim = {"id": "C", "experiment": {"aa_test_run": True, "arms": [{"users": 10}]}}
        got, calls = self.invoke("data_existing_author", {"dest": os.path.join(self.tmp, "c.json"), "claim": claim})
        self.assertEqual(["bds.py", "author", os.path.join(self.tmp, "c.json"), "--json"], calls[0][0])
        self.assertEqual(claim, json.loads(calls[0][1]))
        got, calls = self.invoke("data_existing_author", {"dest": "c.json", "claim": "id=C"})
        self.assertEqual(([], "NO-DATA"), (calls, got["verdict"]))

    def test_score_lists_and_optional_inputs_keep_their_shape(self):
        claim = os.path.join(self.tmp, "c.json")
        open(claim, "w").close()
        _, calls = self.invoke("data_existing_score", {"claim_path": claim, "actual": 0})
        self.assertEqual(["bds.py", "score", claim, "0", "not stated", "not stated"], calls[0][0])
        _, calls = self.invoke("data_existing_mdm_resolve", {"inputs": self.csv, "out": "o.csv"})
        self.assertEqual(["mdm_jan_lookup.py", "resolve", "--input", self.csv, "--out=o.csv"], calls[0][0])
        _, calls = self.invoke("data_existing_mdm_evaluate", {"labelled_csv": self.csv, "results_csv": self.csv})
        self.assertEqual(["bds.py", "mdm-audit", "evaluate", self.csv, "--results=%s" % self.csv], calls[0][0])
        _, calls = self.invoke("data_existing_mdm_validate", {"kind": "gtin", "values": ["-1", "49"]})
        self.assertEqual(["mdm_validate.py", "gtin", "-1", "49"], calls[0][0])
        got, calls = self.invoke("data_existing_mdm_resolve", {"inputs": [self.csv, "/nope.csv"], "out": "o.csv"})
        self.assertEqual(([], "NO-DATA"), (calls, got["verdict"]))
        got, calls = self.invoke("data_existing_mdm_classify", {"names": ["--selftest"]})
        self.assertEqual(([], "NO-DATA"), (calls, got["verdict"]))
        for names in ([3], ["tea", ""]):
            got, calls = self.invoke("data_existing_mdm_classify", {"names": names})
            self.assertEqual(([], "NO-DATA"), (calls, got["verdict"]), names)
        _, calls = self.invoke("data_existing_mdm_classify", {"names": ["tea", "coffee"]})
        self.assertEqual(["mdm_jan_lookup.py", "classify", "tea", "coffee"], calls[0][0])
        _, calls = self.invoke("data_existing_mdm_resolve", {"inputs": [self.csv, self.csv], "out": "o.csv"})
        self.assertEqual(["mdm_jan_lookup.py", "resolve", "--input", self.csv, self.csv, "--out=o.csv"], calls[0][0])
        _, calls = self.invoke("data_existing_score", {"claim_path": claim, "actual": 2.5, "observed_by": "ops lead",
                                                       "observed_on": "2026-09-30"})
        self.assertEqual(["bds.py", "score", claim, "2.5", "ops lead", "2026-09-30"], calls[0][0])

    def test_a_written_path_or_a_word_starting_with_a_dash_runs_nothing(self):
        for handler, inputs in (("data_existing_author", {"dest": "--help", "claim": {"id": "C"}}),
                                ("data_existing_mdm_resolve", {"inputs": self.csv, "out": "-o.csv"}),
                                ("data_existing_mdm_normalize", {"kind": "--selftest", "locale": "ja", "text": "x"})):
            got, calls = self.invoke(handler, inputs)
            self.assertEqual(([], "NO-DATA"), (calls, got["verdict"]), inputs)

    def test_free_text_may_start_with_a_dash_and_reaches_the_command_unchanged(self):
        _, calls = self.invoke("data_existing_mdm_normalize", {"kind": "person", "locale": "latin", "text": "-Ann"})
        self.assertEqual(["mdm_normalize.py", "key", "person", "latin", "-Ann"], calls[0][0])

    def test_a_timed_out_or_unstartable_command_is_no_data(self):
        for exc in (subprocess.TimeoutExpired("x", 1), OSError("gone")):
            with mock.patch.object(bds, "_door_consented", return_value=True):
                got = bds.door_invoke("data_existing_mdm_review", {"review_csv": self.csv, "merge_threshold": 0.8},
                                      mock.Mock(side_effect=exc))
            self.assertEqual(("NO-DATA", []), (got["verdict"], got["evidence_refs"]))


class Consent(unittest.TestCase):
    def test_consent_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = os.path.join(tmp, "config.json")
            for body, want in (('{"setup_complete": true}', True), ('{"setup_complete": "true"}', False),
                               ("not json", False), (None, False)):
                if body is not None:
                    with open(cfg, "w") as fh:
                        fh.write(body)
                elif os.path.exists(cfg):
                    os.remove(cfg)
                with mock.patch.dict(os.environ, {"BROTHERME_CONFIG": cfg}):
                    self.assertEqual(want, bds._door_consented(), body)
            with mock.patch.dict(os.environ, {"BROTHERME_CONFIG": cfg}), \
                    mock.patch.object(bds.vault_bridge, "_TOOLS", None):
                with open(cfg, "w") as fh:
                    fh.write('{"setup_complete": true}')
                self.assertFalse(bds._door_consented())
            # tools found but no setup.py beside them: the load fails, and that is not consent
            with mock.patch.dict(os.environ, {"BROTHERME_CONFIG": cfg}), \
                    mock.patch.object(bds.vault_bridge, "_TOOLS", os.path.join(tmp, "tools")):
                self.assertFalse(bds._door_consented())


class RealJourney(unittest.TestCase):
    """The door reaching the real tools over scratch files, consent given through a scratch config."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        consent = os.path.join(self.tmp, "config.json")
        with open(consent, "w") as fh:
            json.dump({"setup_complete": True}, fh)
        self.env = mock.patch.dict(os.environ, {"BROTHERME_CONFIG": consent})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def door(self, cap, allow=None, outcome=None, **typed):
        return door.run_data_capability(cap, outcome, typed, allow)

    def example(self, name):
        with open(os.path.join(EXAMPLES, name)) as fh:
            return json.load(fh)

    def test_a_claim_authored_through_the_door_keeps_its_types_and_runs_its_pack(self):
        claim = self.example("example-experiment-lift.json")
        dest = os.path.join(self.tmp, "claims", "x.json")
        got = self.door("data.author", ["write"], dest=dest, claim=claim)
        self.assertIn(got["state"], ("COMPLETE", "NO-DATA"), got["output"])
        with open(dest) as fh:
            written = json.load(fh)
        self.assertIs(True, written["experiment"]["aa_test_run"])
        self.assertEqual(claim["experiment"], written["experiment"])
        self.assertIn("PASS     X4.design_hygiene", got["output"])

    def test_without_write_no_claim_is_written(self):
        dest = os.path.join(self.tmp, "x.json")
        got = self.door("data.author", dest=dest, claim=self.example("example-experiment-lift.json"))
        self.assertEqual("REFUSED", got["state"])
        self.assertFalse(os.path.exists(dest))

    def test_each_pack_runs_inside_check(self):
        for name, gate in (("example-experiment-lift.json", "X1.srm"), ("example-detection-rare-event.json",
                                                                        "D1.confusion"),
                           ("example-master-data-merge.json", "M1.threshold_bands"),
                           ("example-pipeline-reconciled.json", "P1.contract")):
            got = self.door("data.check", claim_path=os.path.join(EXAMPLES, name))
            self.assertIn(gate, got["output"], name)
            verdict = got["output"].split("VERDICT ")[-1].split()[0]
            self.assertEqual({"PASS": "COMPLETE", "FAIL": "FAILED", "NO-DATA": "NO-DATA"}[verdict], got["state"], name)

    def test_a_false_merge_overclaim_fails_through_the_door(self):
        got = self.door("data.check", claim_path=os.path.join(EXAMPLES, "example-mdm-overclaim.json"))
        self.assertEqual("FAILED", got["state"])
        self.assertIn("FAIL     M7.precision_evidence", got["output"])

    def test_the_mdm_review_is_derived_by_the_real_command(self):
        got = self.door("data.mdm_review", review_csv=os.path.join(EXAMPLES, "review-sample-mdm.csv"),
                        merge_threshold=0.8)
        self.assertEqual("COMPLETE", got["state"], got["output"])
        self.assertIn("bds.py mdm-eval review exit 0 stdout sha256:", got["evidence_refs"][0])
        self.assertEqual(0.962, round(json.loads(got["output"])["precision_above"]["estimate"], 3))

    def test_japanese_identifiers_keep_the_locale_rules(self):
        keys = [self.door("data.mdm_normalize", outcome=name, kind="company", locale="ja")["output"]
                for name in ("株式会社テスト商事", "（株）テスト商事", "テスト商会")]
        self.assertEqual(keys[0], keys[1])
        self.assertNotEqual(keys[0], keys[2])
        got = self.door("data.mdm_validate", kind="gtin", values=["4901234567894", "4901234567890"])
        rows = [json.loads(line) for line in got["output"].splitlines()]
        self.assertEqual([True, False], [r["valid"] for r in rows])

    def test_a_missing_file_is_no_data_naming_what_to_supply(self):
        got = self.door("data.check", claim_path=os.path.join(self.tmp, "absent.json"))
        self.assertEqual("NO-DATA", got["state"])
        self.assertIn("supply that file", got["output"])

    def test_unreadable_door_input_runs_nothing(self):
        proc = subprocess.run([sys.executable, door.data_tool(), "door", "data_existing_check"], input="not json",
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=120)
        self.assertEqual(0, proc.returncode, proc.stderr)
        reply = json.loads(proc.stdout)
        self.assertEqual("NO-DATA", reply["verdict"])
        self.assertIn("not a set of named values", reply["output"])

    def test_author_json_refuses_anything_but_one_object(self):
        dest = os.path.join(self.tmp, "x.json")
        proc = subprocess.run([sys.executable, door.data_tool(), "author", dest, "--json"], input="[1]",
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=120)
        self.assertEqual((2, False), (proc.returncode, os.path.exists(dest)), proc.stdout + proc.stderr)
        self.assertIn("REFUSED", proc.stdout)



class OwnVerdict(unittest.TestCase):
    """The verdict is the command's own, read by the reader its handler declares, never exit 0 alone (OUT1.e fix).
    Real cases run the real commands; a tool this edition lacks answers NO-DATA by the edition guard."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def real(self, handler, want, **inputs):
        if not os.path.isfile(os.path.join(DS, bds.DOOR_HANDLERS[handler][0])):
            want = "NO-DATA"  # this edition does not carry the tool: nothing runs
        with mock.patch.object(bds, "_door_consented", return_value=True):
            got = bds.door_invoke(handler, inputs)
        self.assertEqual((want, want == "PASS"), (got["verdict"], bool(got["evidence_refs"])),
                         "%s %s" % (handler, got["output"][-400:]))
        return got

    def scripted(self, handler, out, want, code=0, err=""):
        calls = []
        claim = os.path.join(self.tmp, "c.json")
        with open(claim, "w") as fh:
            fh.write("{}")
        inputs = {"data_existing_mdm_validate": {"kind": "gtin", "values": ["1"]},
                  "data_existing_ledger": {"directory": self.tmp},
                  "data_existing_mdm_review": {"review_csv": claim, "merge_threshold": 0.8}}.get(
                      handler, {"claim_path": claim})
        with mock.patch.object(bds, "_door_consented", return_value=True), \
                mock.patch.object(bds, "_door_tool_present", return_value=True):
            got = bds.door_invoke(handler, inputs, lambda argv, stdin_text: calls.append(argv) or (code, out, err))
        self.assertEqual((want, want == "PASS"), (got["verdict"], bool(got["evidence_refs"])), repr(out))
        return got, calls

    # ---- the real commands
    def test_a_fail_receipt_and_a_fail_check_never_pass(self):
        hero = os.path.join(EXAMPLES, "hero-number.json")
        got = self.real("data_existing_receipt", "FAIL", claim_path=hero)
        self.assertIn("FAIL     G5.rederivation", got["output"])
        self.real("data_existing_check", "FAIL", claim_path=hero)

    def test_a_check_reporting_no_data_at_exit_zero_is_no_data(self):
        # no example in examples/ checks PASS (measured 2026-11-01); the PASS line is pinned by the scripted cases
        self.real("data_existing_check", "NO-DATA", claim_path=os.path.join(EXAMPLES, "example-experiment-lift.json"))

    def test_an_empty_ledger_is_no_data_and_a_resolved_one_passes(self):
        self.real("data_existing_ledger", "NO-DATA", directory=self.tmp)
        with open(os.path.join(self.tmp, "c.json"), "w") as fh:
            json.dump({"id": "T", "statement": "s", "outcome": {"state": "HELD"}}, fh)
        self.assertIn("100%", self.real("data_existing_ledger", "PASS", directory=self.tmp)["output"])

    def test_validate_fails_an_invalid_value_and_passes_valid_ones(self):
        self.real("data_existing_mdm_validate", "FAIL", kind="gtin", values=["-1", "49"])
        self.real("data_existing_mdm_validate", "PASS", kind="gtin", values=["4901234567894"])

    def test_a_corrupt_claim_or_a_directory_crashes_to_no_data(self):
        bad = os.path.join(self.tmp, "bad.json")
        with open(bad, "w") as fh:
            fh.write("{not json")
        for handler in ("data_existing_check", "data_existing_receipt"):
            for path in (bad, self.tmp):
                got = self.real(handler, "NO-DATA", claim_path=path)
                self.assertIn("crashed", got["output"], (handler, path))

    def test_a_produced_command_passes_only_as_having_run(self):
        self.real("data_existing_mdm_classify", "PASS", names=["tea"])  # NO-DATA where the edition lacks the tool

    # ---- each reader, scripted
    def test_verdict_line_shapes(self):
        for out, want in (("VERDICT: NO-DATA\n", "NO-DATA"), ("  VERDICT NO-DATA\n", "NO-DATA"),
                          ("VERDICT FAIL\n", "FAIL"), ("VERDICT PASS\n", "PASS"), ("claim x\n", "NO-DATA"),
                          ("VERDICT PASS\nVERDICT FAIL\n", "FAIL"), ("VERDICT FAIL\nVERDICT PASS\n", "PASS"),
                          ("VERDICT: FAIL\n", "FAIL"), ("  VERDICT PASS\n", "PASS"),
                          ("VERDICT PASS\n  VERDICT: NO-DATA\n", "NO-DATA")):
            self.scripted("data_existing_check", out, want)

    def test_gate_row_shapes(self):
        for out, want in (("PASS     G1.x  a\nNO-DATA  G3.y  b\n", "NO-DATA"),
                          ("PASS     G1.x  a\nFAIL     G5.y  b\nNO-DATA  G3.z\n", "FAIL"),
                          ("| FAIL | G5.rederivation | x |\n| PASS | G1.x | y |\n", "FAIL"),
                          ("PASS     G1.x  a\nPASS     M7.y  b\n", "PASS"),
                          ("PASS     G1.x\n| Verdict | **FAIL** |\n", "FAIL"),
                          ("# Claim receipt\nno gates here\n", "NO-DATA")):
            self.scripted("data_existing_receipt", out, want)

    def test_claim_rate_shapes(self):
        for out, want in (("VERIFIED CLAIM RATE  NO-DATA\n", "NO-DATA"),
                          ("VERIFIED CLAIM RATE  50%  (1 held of 2 resolved)\n", "PASS"),
                          ("UNREADABLE, excluded\nVERIFIED CLAIM RATE  50%\n", "NO-DATA"),
                          ("CLAIM OUTCOME\n", "NO-DATA")):
            self.scripted("data_existing_ledger", out, want)

    def test_json_valid_shapes(self):
        for out, want in (('{"valid": true}\n{"valid": true}\n', "PASS"), ('{"valid": true}\n{"valid": false}\n', "FAIL"),
                          ('{"valid": true}\nnot json\n', "NO-DATA"), ('{"ok": true}\n', "NO-DATA"),
                          ('{"valid": "true"}\n', "NO-DATA"), ("[1]\n", "NO-DATA")):
            self.scripted("data_existing_mdm_validate", out, want)

    def test_produced_shapes(self):
        for out, want in ((REVIEW_JSON, "PASS"), ("{}\n", "NO-DATA"), ("NO-DATA: no rows\n", "NO-DATA"),
                          ("{}\n  FAIL     G5.x  y\n", "FAIL"), ("{}\nVERDICT: NO-DATA\n", "NO-DATA"),
                          ("not json\n", "NO-DATA")):
            self.scripted("data_existing_mdm_review", out, want)

    def test_score_reads_held_as_pass_and_missed_as_fail(self):  # VERD1.a: a missed outcome contradicts the claim
        claim = os.path.join(tempfile.mkdtemp(prefix="verd1a-score-"), "c.json")
        self.addCleanup(shutil.rmtree, os.path.dirname(claim), True)
        with open(claim, "w", encoding="utf-8") as fh:
            json.dump({"id": "C2", "statement": "s"}, fh)
        for out, want in (("OUTCOME HELD\n", "PASS"), ("OUTCOME MISSED\n", "FAIL"), ("OUTCOME UNSCOREABLE\n", "NO-DATA"),
                          ("scored\n", "NO-DATA"), ("", "NO-DATA")):
            with mock.patch.object(bds, "_door_consented", return_value=True), \
                    mock.patch.object(bds, "_door_tool_present", return_value=True):
                got = bds.door_invoke("data_existing_score", {"claim_path": claim, "actual": 1},
                                      lambda a, i, o=out: (0, o, ""))
            self.assertEqual(want, got["verdict"], repr(out))

    def _csv_run(self, handler, inputs, out):
        with mock.patch.object(bds, "_door_consented", return_value=True), \
                mock.patch.object(bds, "_door_tool_present", return_value=True):
            return bds.door_invoke(handler, inputs, lambda argv, stdin_text: (0, out, ""))

    def test_a_figure_the_tool_could_not_measure_is_not_evidence(self):  # VERD1.a review round 1
        self.csv = os.path.join(self.tmp, "r.csv")
        with open(self.csv, "w") as fh:
            fh.write("score,label\n")
        # REAL shapes: `bds.py mdm-eval review` and `mdm-audit evaluate` over a header-only CSV (score,label)
        empty_review = json.dumps({"merge_threshold": 0.8, "n_rows": 0, "precision_above": None, "recall": {
            "covers_below_threshold": False, "matches_above": 0.0, "matches_total": 0.0, "recall": None},
            "strata": []}, indent=2) + "\n"
        empty_evaluate = json.dumps({"pathway_review": {}, "strata": [], "unlabelled": 0, "unmatched_review": {
            "coverage_state": "NO-DATA", "matchable_missed": 0, "population": 0, "reviewed_population": 0},
            "verifier": {"confirmed_review": None, "rejected_review": None}}, indent=1) + "\n"
        cases = (("data_existing_mdm_review", {"review_csv": self.csv, "merge_threshold": 0.8}, empty_review, "NO-DATA"),
                 ("data_existing_mdm_review", {"review_csv": self.csv, "merge_threshold": 0.8},
                  empty_review.replace('"precision_above": null', '"precision_above": {"estimate": 0.9}'), "NO-DATA"),
                 ("data_existing_mdm_drift", {"reference_csv": self.csv, "current_csv": self.csv},
                  '{"psi": "NO-DATA", "score_drift": "NO-DATA"}\n', "NO-DATA"),
                 ("data_existing_mdm_drift", {"reference_csv": self.csv, "current_csv": self.csv},
                  '{"psi": 0.1, "score_drift": {"current": [1]}}\n', "PASS"),
                 ("data_existing_mdm_clusters", {"gold_csv": self.csv},
                  '{"gold_clusters": {"pred": [], "true": []}, "pairwise": {"f1": null, "tp": 0}}\n', "NO-DATA"),
                 ("data_existing_mdm_evaluate", {"labelled_csv": self.csv}, empty_evaluate, "NO-DATA"),
                 ("data_existing_mdm_classify", {"names": ["x"]},
                  '{"name": "Cola", "packaging_type": null, "reason": "r"}\n', "NO-DATA"),
                 ("data_existing_mdm_classify", {"names": ["x"]},
                  '{"name": "", "packaging_type": "other", "reason": "empty product name"}\n', "PASS"))
        for handler, inputs, out, want in cases:
            got = self._csv_run(handler, inputs, out)
            self.assertEqual((want, want == "PASS"), (got["verdict"], bool(got["evidence_refs"])), (handler, out[:60]))

    def test_an_audit_over_no_rows_and_a_figure_with_nothing_inside_are_not_evidence(self):  # VERD1.a review round 2
        self.csv = os.path.join(self.tmp, "a.csv")
        with open(self.csv, "w") as fh:
            fh.write("source_id,reference_id,pathway,status,score\n")
        # REAL shape: `mdm-audit` over that header-only CSV prints n_rows 0 with empty bands and a NO-DATA estimate
        empty_audit = json.dumps({"capture_recapture": {"state": "NO-DATA"}, "input_sha256": "ab" * 32, "n_rows": 0,
                                  "pathway_band": {}, "segments": []}) + "\n"
        audit = {"input_sha256": "ab" * 32}
        cases = (("data_existing_mdm_audit", empty_audit, "NO-DATA"),
                 ("data_existing_mdm_audit", json.dumps(dict(audit, n_rows=12)) + "\n", "PASS"),
                 ("data_existing_mdm_audit_plan", json.dumps(dict(audit, n_rows=0, review_plan=[{"id": 1}])) + "\n",
                  "NO-DATA"),
                 ("data_existing_mdm_audit_plan", json.dumps(dict(audit, n_rows=3, review_plan={"row_count": 2})) + "\n",
                  "PASS"),
                 # REAL shape (review round 3): one row whose status is neither MATCH nor NO_MATCH plans 0 rows
                 ("data_existing_mdm_audit_plan", json.dumps(dict(audit, n_rows=1, review_plan={
                     "row_count": 0, "strata": "none"})) + "\n", "NO-DATA"),
                 ("data_existing_mdm_audit", json.dumps(dict(audit, n_rows=True)) + "\n", "NO-DATA"),
                 ("data_existing_mdm_audit", json.dumps(dict(audit, n_rows=0.5)) + "\n", "NO-DATA"),
                 ("data_existing_mdm_review", '{"precision_above": {"estimate": null}, "recall": {"recall": 0.5}}\n',
                  "NO-DATA"),
                 ("data_existing_mdm_drift", '{"psi": {"value": null}, "score_drift": {"current": [1]}}\n', "NO-DATA"),
                 ("data_existing_mdm_drift", '{"psi": 0.0, "score_drift": {"current": [0]}}\n', "PASS"))
        inputs = {"data_existing_mdm_audit": {"results_csv": self.csv},
                  "data_existing_mdm_audit_plan": {"results_csv": self.csv, "plan": "p.json"},
                  "data_existing_mdm_review": {"review_csv": self.csv, "merge_threshold": 0.8},
                  "data_existing_mdm_drift": {"reference_csv": self.csv, "current_csv": self.csv}}
        for handler, out, want in cases:
            got = self._csv_run(handler, inputs[handler], out)
            self.assertEqual((want, want == "PASS"), (got["verdict"], bool(got["evidence_refs"])), (handler, out[:70]))

    def test_a_claim_id_with_spaces_still_reads_as_registered(self):  # VERD1.a review round 1
        claim = os.path.join(self.tmp, "c.json")
        with open(claim, "w") as fh:
            fh.write("{}")
        got = self._csv_run("data_existing_register", {"claim_path": claim},
                            "registered 'weekly_orders' from claim Weekly orders lift in /w/metric-registry.json\n")
        self.assertEqual("PASS", got["verdict"])

    def test_classify_and_normalize_earn_pass_only_from_their_own_shape(self):  # VERD1.a
        row = '{"name": "Cola", "packaging_type": "loose", "reason": "r"}\n'
        for out, want in ((row, "PASS"), (row + row, "PASS"), ('{"name": "Cola"}\n', "NO-DATA"), ("[1]\n", "NO-DATA"),
                          (row + "not json\n", "NO-DATA"), ("", "NO-DATA")):
            with mock.patch.object(bds, "_door_consented", return_value=True), \
                    mock.patch.object(bds, "_door_tool_present", return_value=True):
                got = bds.door_invoke("data_existing_mdm_classify", {"names": ["x"]}, lambda a, i, o=out: (0, o, ""))
            self.assertEqual((want, want == "PASS"), (got["verdict"], bool(got["evidence_refs"])), repr(out))
        for kind, text in (("company", "株式会社テスト商事"), ("phone", "03-1234-5678")):
            import mdm_normalize
            want = mdm_normalize.company_key(text, "ja") if kind == "company" else mdm_normalize.phone_key(text)
            for out, verdict in ((want + "\n", "PASS"), (want + "x\n", "NO-DATA")):
                with mock.patch.object(bds, "_door_consented", return_value=True), \
                        mock.patch.object(bds, "_door_tool_present", return_value=True):
                    got = bds.door_invoke("data_existing_mdm_normalize", {"kind": kind, "locale": "ja", "text": text},
                                          lambda a, i, o=out: (0, o, ""))
                self.assertEqual(verdict, got["verdict"], (kind, out))

    def test_a_traceback_at_exit_one_is_no_data_and_a_plain_exit_one_fails(self):
        got, _ = self.scripted("data_existing_check", "", "NO-DATA", code=1,
                               err="Traceback (most recent call last):\n  File x\nValueError: bad\n")
        self.assertIn("ValueError: bad", got["output"])
        self.scripted("data_existing_check", "VERDICT FAIL\n", "FAIL", code=1)

    def test_a_handler_without_a_reader_runs_nothing(self):
        for name in (None, "no_such_reader"):
            table = dict(bds.DOOR_VERDICT_READERS)
            if name is None:
                del table["data_existing_check"]
            else:
                table["data_existing_check"] = name
            with mock.patch.object(bds, "DOOR_VERDICT_READERS", table):
                _, calls = self.scripted("data_existing_check", "VERDICT PASS\n", "NO-DATA")
            self.assertEqual([], calls, name)

    def test_every_handler_names_a_known_reader(self):
        self.assertEqual(sorted(bds.DOOR_HANDLERS), sorted(bds.DOOR_VERDICT_READERS))
        for handler, reader in bds.DOOR_VERDICT_READERS.items():
            self.assertIn(reader, bds.DOOR_READERS, handler)

if __name__ == "__main__":
    unittest.main()
