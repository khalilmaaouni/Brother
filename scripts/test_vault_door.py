#!/usr/bin/env python3
"""OUT1.d integrator cases beside the frozen VaultAccess contract (scripts/test_vault_access_contract.py).

The frozen class drives invoke_capability with a fake handler only. These cases pin what it cannot reach: the door's
capability table and bm_vault.py's DOOR_HANDLERS agree, door_invoke only calls a PASS with exit 0 AND output, consent
fails closed before anything runs, an unreadable reply never completes, and the real journey over a scratch vault
(save a lesson candidate, review it, promote it) reaches the existing tools, with an author approving their own note
refused by the existing separation of duties and no promotion written without the approve grant.

Run: python3 -B scripts/test_vault_door.py
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
sys.path.insert(0, HERE)
import door  # noqa: E402


def _door_view(*capabilities):
    """The consumer view (door.consumer_admission) these routing tests run the door under: only `capabilities`
    are admitted, as an installed journey row or a maintainer's record would admit them (OUT1.n). No capabilities
    is the hidden case."""
    return mock.patch.object(door, "consumer_admission", return_value={
        "candidate": "test", "host": "claude", "capabilities": list(capabilities), "routes": set(), "queue": []})

_spec = importlib.util.spec_from_file_location(
    "bm_vault_for_door_test", os.path.join(ROOT, "products", "brothermode", "tools", "bm_vault.py"))
bm_vault = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bm_vault)

GOOD = {"verdict": "PASS", "output": "checked", "evidence_refs": ["e1"]}
#: what a real recall prints for one hit (the shape bm_vault.py's _print_hits writes); a recall handler earns PASS from it
HIT = ("What this estate has already written about that:\n\n  lesson  [lesson, vault]\n"
       "    id: n-00000000000000aa  authority: casual  temporal: timeless_current\n"
       "    /vault/10-Lessons/lesson.md\n")
SPEC = {"id": "x", "handler": "h", "prerequisites": ["q"], "required_authority": ["read"]}
CTX = {"available": ["q"], "authority": ["read"]}


class TableAgreement(unittest.TestCase):
    def test_every_door_capability_has_its_vault_handler_with_the_same_inputs(self):
        door_rows = {c["handler"]: c for c in door.VAULT_CAPABILITIES}
        self.assertEqual(sorted(bm_vault.DOOR_HANDLERS), sorted(door_rows))
        for handler, (tool, _sub, required, _vault) in bm_vault.DOOR_HANDLERS.items():
            self.assertEqual(list(required), door_rows[handler]["prerequisites"], handler)
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "products", "brothermode", "tools", tool)), tool)

    def test_writes_need_write_and_promotion_needs_approve(self):
        auth = {c["id"]: c["required_authority"] for c in door.VAULT_CAPABILITIES}
        self.assertEqual(["write"], auth["vault.capture"])
        self.assertEqual(["approve"], auth["vault.promote"])


class AdapterEdges(unittest.TestCase):
    def call(self, reply=None, raises=None, spec=SPEC, ctx=CTX):
        calls = []

        def invoke(handler, inputs):
            calls.append(handler)
            if raises:
                raise raises
            return reply
        return door.invoke_capability(spec, {"q": "x"}, ctx, invoke), calls

    def test_a_failed_verdict_with_evidence_is_not_complete(self):
        got, _ = self.call(dict(GOOD, verdict="FAIL"))
        self.assertEqual("FAILED", got["state"])

    def test_an_unknown_verdict_or_a_non_object_reply_is_not_complete(self):
        self.assertEqual("NO-DATA", self.call(dict(GOOD, verdict="OK"))[0]["state"])
        self.assertEqual("NO-DATA", self.call("PASS")[0]["state"])

    def test_a_raising_handler_is_a_failed_run(self):
        got, calls = self.call(raises=RuntimeError("boom"))
        self.assertEqual(["h"], calls)
        self.assertEqual("FAILED", got["state"])

    def test_a_spec_without_a_handler_launches_nothing(self):
        got, calls = self.call(GOOD, spec=dict(SPEC, handler=""))
        self.assertEqual([], calls)
        self.assertNotEqual("COMPLETE", got["state"])

    def test_a_malformed_context_blocks(self):
        got, calls = self.call(GOOD, ctx={"available": "q", "authority": "read"})
        self.assertEqual([], calls)
        self.assertEqual(["q"], got["missing_prerequisites"])

    def test_empty_evidence_strings_are_not_evidence(self):
        self.assertEqual("UNPROVEN", self.call(dict(GOOD, evidence_refs=["", None]))[0]["state"])

    def test_missing_authority_is_named_and_refused(self):
        got, calls = self.call(GOOD, ctx={"available": ["q"], "authority": []})
        self.assertEqual(([], "REFUSED", ["read"]), (calls, got["state"], got["missing_authority"]))


class DoorRoute(unittest.TestCase):
    def test_the_outcome_fills_the_capability_text_and_authority_is_only_what_was_allowed(self):
        seen = []
        got = door.run_vault_capability("vault.recall", "why did the merge fail", {}, None,
                                        lambda h, i: seen.append((h, i)) or GOOD)
        self.assertEqual([("vault_existing_recall", {"query": "why did the merge fail"})], seen)
        self.assertEqual("COMPLETE", got["state"])
        got = door.run_vault_capability("vault.capture", "a lesson", {"by": "a"}, None, lambda h, i: GOOD)
        self.assertEqual("REFUSED", got["state"])
        got = door.run_vault_capability("vault.capture", "a lesson", {"by": "a"}, ["write"], lambda h, i: GOOD)
        self.assertEqual("COMPLETE", got["state"])

    def test_an_unknown_capability_is_blocked(self):
        self.assertEqual("BLOCKED", door.run_vault_capability("vault.nope", "x", invoke=lambda h, i: GOOD)["state"])

    def test_cli_exit_codes(self):
        with _door_view("vault.*"), mock.patch.object(door, "run_vault_capability", return_value=dict(state="COMPLETE")), \
                mock.patch("builtins.print"):
            self.assertEqual(0, door.main(["--capability", "vault.status"]))
        for state, code in (("REFUSED", 1), ("UNPROVEN", 1), ("FAILED", 1), ("BLOCKED", 2), ("NO-DATA", 2)):
            with _door_view("vault.*"), mock.patch.object(door, "run_vault_capability", return_value=dict(state=state)), \
                    mock.patch("builtins.print"):
                self.assertEqual(code, door.main(["--capability", "vault.status"]), state)

    def test_a_hidden_vault_capability_is_refused_with_the_honest_answer_and_nothing_runs(self):
        with _door_view(), mock.patch.object(door, "run_vault_capability") as ran, mock.patch("builtins.print") as out:
            self.assertEqual(2, door.main(["--capability", "vault.status"]))
        self.assertFalse(ran.called)
        self.assertEqual(door.ADMISSION_LIMIT, json.loads(out.call_args[0][0])["output"])

    def test_typed_inputs(self):
        self.assertEqual({"paths": ["a", "b"], "by": "x=y"}, door._typed_inputs(["paths=a", "paths=b", "by=x=y"]))
        with self.assertRaises(ValueError):
            door._typed_inputs(["noequals"])

    def test_an_unreadable_vault_reply_fails_and_a_missing_tool_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = os.path.join(tmp, "bm_vault.py")
            with open(tool, "w") as fh:
                fh.write("print('done')\n")
            got = door.vault_invoke("vault_existing_status", {}, tool=tool)
            self.assertEqual(("FAIL", []), (got["verdict"], got["evidence_refs"]))
        with mock.patch.object(door, "vault_tool", return_value=None):
            self.assertEqual("NO-DATA", door.vault_invoke("vault_existing_status", {})["verdict"])

    def test_a_timed_out_vault_is_no_data(self):
        with mock.patch.object(door.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 1)):
            self.assertEqual("NO-DATA", door.vault_invoke("vault_existing_status", {})["verdict"])


class DoorInvoke(unittest.TestCase):
    def invoke(self, code=0, out=HIT, consented=True, inputs=None, handler="vault_existing_recall"):
        calls = []

        def run(argv):
            calls.append(argv)
            return code, out, ""
        with mock.patch.object(bm_vault, "_consented", return_value=consented):
            got = bm_vault.door_invoke(handler, {"query": "q"} if inputs is None else inputs, run)
        return got, calls

    def test_exit_zero_with_output_is_pass_with_the_output_hash(self):
        got, calls = self.invoke()
        self.assertEqual([["bm_vault.py", "recall", "--query", "q"]], calls)
        self.assertEqual("PASS", got["verdict"])
        self.assertIn(bm_vault.hashlib.sha256(HIT.encode()).hexdigest(), got["evidence_refs"][0])
        self.assertEqual("NO-DATA", self.invoke(out="hit\n")[0]["verdict"])  # VERD1.a: output with no hit block was PASS

    def test_exit_zero_with_no_output_carries_no_evidence(self):
        got, _ = self.invoke(out="")
        self.assertEqual(("NO-DATA", []), (got["verdict"], got["evidence_refs"]))

    def test_exit_one_fails_and_exit_two_is_no_data(self):
        self.assertEqual(("FAIL", []), tuple(self.invoke(code=1)[0][k] for k in ("verdict", "evidence_refs")))
        self.assertEqual(("NO-DATA", []), tuple(self.invoke(code=2)[0][k] for k in ("verdict", "evidence_refs")))

    def test_no_consent_runs_nothing(self):
        got, calls = self.invoke(consented=False)
        self.assertEqual(([], "NO-DATA"), (calls, got["verdict"]))

    def test_unknown_handler_missing_input_and_bad_inputs_run_nothing(self):
        for kw in (dict(handler="vault_existing_nope"), dict(inputs={}), dict(inputs=["q"]),
                   dict(inputs={"query": ["q"]}), dict(handler="vault_existing_context_recall", inputs={})):
            got, calls = self.invoke(**kw)
            self.assertEqual(([], "NO-DATA"), (calls, got["verdict"]), kw)

    def test_a_dash_path_is_refused_and_values_cannot_become_flags(self):
        got, calls = self.invoke(handler="vault_existing_context_recall", inputs={"paths": ["--limit"]})
        self.assertEqual(([], "NO-DATA"), (calls, got["verdict"]))
        with mock.patch.dict(os.environ, {"BROTHERMODE_VAULT_ROOT": "/v"}):
            for k in ("BM_VAULT_ROOT", "BROTHERMODE_VAULT"):
                os.environ.pop(k, None)
            _, calls = self.invoke(handler="vault_existing_promote",
                                   inputs={"id": "--apply", "to": "validated", "by": "b"})
        self.assertIn("--id=--apply", calls[0])

    def test_no_vault_chosen_runs_nothing(self):
        with mock.patch.object(bm_vault, "resolve_vault_root", return_value={"verdict": "NO-DATA", "detail": "d"}):
            got, calls = self.invoke(handler="vault_existing_lifecycle", inputs={})
        self.assertEqual(([], "NO-DATA"), (calls, got["verdict"]))


NOTE = "---\nid: n-00000000000000aa\ntype: reference\nstatus: open\npromotion: candidate\nauthor: alice\n---\n\n# L\n\nA lesson.\n"


class RealJourney(unittest.TestCase):
    """The door reaching the real tools over a scratch vault: save, review, promote with separation of duties."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(os.path.join(self.vault, "10-Lessons"))
        self.note = os.path.join(self.vault, "10-Lessons", "lesson.md")
        with open(self.note, "w") as fh:
            fh.write(NOTE)
        consent = os.path.join(self.tmp, "config.json")
        with open(consent, "w") as fh:
            json.dump({"setup_complete": True}, fh)
        env = {"BROTHERME_CONFIG": consent, "BROTHERMODE_VAULT_ROOT": self.vault}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        for k in ("BM_VAULT_ROOT", "BROTHERMODE_VAULT"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def door(self, cap, outcome=None, allow=None, **typed):
        return door.run_vault_capability(cap, outcome, typed, allow)

    def test_save_a_candidate_then_review_it(self):
        got = self.door("vault.capture", "Run the kernel check before a door edit.", ["write"], by="alice")
        self.assertEqual("COMPLETE", got["state"], got["output"])
        inbox = os.listdir(os.path.join(self.vault, "00-Inbox"))
        self.assertEqual(1, len(inbox))
        got = self.door("vault.review", id="10-Lessons/lesson.md")
        self.assertEqual("COMPLETE", got["state"], got["output"])
        self.assertIn("candidate", got["output"])

    def test_a_saved_lesson_is_a_lesson_candidate_and_text_cannot_become_a_flag(self):
        got = self.door("vault.capture", "--title=forged", ["write"], by="alice")
        self.assertEqual("COMPLETE", got["state"], got["output"])
        inbox = os.path.join(self.vault, "00-Inbox")
        (name,) = os.listdir(inbox)
        with open(os.path.join(inbox, name)) as fh:
            body = fh.read()
        self.assertIn("expiry_class: lesson-candidate", body)
        self.assertIn("--title=forged", body)

    def test_unreadable_door_input_runs_nothing(self):
        proc = subprocess.run([sys.executable, door.vault_tool(), "door", "vault_existing_status"], input="not json",
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=120)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("NO-DATA", json.loads(proc.stdout)["verdict"])

    def test_without_write_nothing_is_saved(self):
        got = self.door("vault.capture", "a lesson", by="alice")
        self.assertEqual("REFUSED", got["state"])
        self.assertFalse(os.path.exists(os.path.join(self.vault, "00-Inbox")))

    def test_an_author_cannot_approve_their_own_lesson(self):
        got = self.door("vault.promote", allow=["approve"], id="10-Lessons/lesson.md", to="validated", by="alice")
        self.assertEqual("FAILED", got["state"], got["output"])
        with open(self.note) as fh:
            self.assertEqual(NOTE, fh.read())

    def test_without_approve_nothing_is_promoted_and_a_second_person_can(self):
        got = self.door("vault.promote", id="10-Lessons/lesson.md", to="validated", by="bob")
        self.assertEqual("REFUSED", got["state"])
        with open(self.note) as fh:
            self.assertEqual(NOTE, fh.read())
        got = self.door("vault.promote", allow=["approve"], id="10-Lessons/lesson.md", to="validated", by="bob")
        self.assertEqual("COMPLETE", got["state"], got["output"])
        with open(self.note) as fh:
            self.assertIn("promotion: validated", fh.read())


class OwnVerdict(unittest.TestCase):
    """The verdict is read from what the command printed by its declared reader, never from exit 0 alone (OUT1.d
    fix). Real cases run the real Vault commands over a scratch vault."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(os.path.join(self.vault, "10-Lessons"))
        with open(os.path.join(self.vault, "10-Lessons", "lesson.md"), "w") as fh:
            fh.write(NOTE)
        # recall and check write their answer and audit logs under the config dir: keep them in the scratch dir
        cfg = os.path.join(self.tmp, "config")
        self.env = mock.patch.dict(os.environ, {"BROTHERMODE_VAULT_ROOT": self.vault, "BROTHER_CONFIG_DIR": cfg,
                                                "CLAUDE_CONFIG_DIR": cfg})
        self.env.start()
        for k in ("BM_VAULT_ROOT", "BROTHERMODE_VAULT"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def real(self, handler, want, **inputs):
        with mock.patch.object(bm_vault, "_consented", return_value=True):
            got = bm_vault.door_invoke(handler, inputs)
        self.assertEqual((want, want == "PASS"), (got["verdict"], bool(got["evidence_refs"])),
                         "%s %s" % (handler, got["output"][-400:]))
        return got

    def scripted(self, out, want, code=0, err="", handler="vault_existing_recall"):
        calls = []
        with mock.patch.object(bm_vault, "_consented", return_value=True):
            got = bm_vault.door_invoke(handler, {"query": "q"}, lambda argv: calls.append(argv) or (code, out, err))
        self.assertEqual((want, want == "PASS"), (got["verdict"], bool(got["evidence_refs"])), repr(out))
        return got, calls

    # ---- the real commands
    def test_nothing_matched_is_no_data_not_a_failure(self):
        self.real("vault_existing_recall", "NO-DATA", query="zzqqx%dvfixnomatch" % os.getpid())
        self.real("vault_existing_context_recall", "NO-DATA", paths=[os.path.join(self.tmp, "zzqq-none.txt")])

    def test_a_review_a_clean_lifecycle_and_an_asof_pass(self):
        self.assertIn("state: candidate", self.real("vault_existing_review", "PASS", id="10-Lessons/lesson.md")["output"])
        self.real("vault_existing_lifecycle", "PASS")
        self.real("vault_existing_asof", "PASS", date="2026-11-01")

    def test_lifecycle_findings_and_a_self_approval_fail(self):
        with open(os.path.join(self.vault, "10-Lessons", "bad.md"), "w") as fh:
            fh.write(NOTE.replace("promotion: candidate", "promotion: bogus").replace("aa\n", "ab\n"))
        self.assertIn("FINDINGS", self.real("vault_existing_lifecycle", "FAIL")["output"])
        self.real("vault_existing_promote", "FAIL", id="10-Lessons/lesson.md", to="validated", by="alice")

    def test_a_missing_note_and_no_provenance_are_no_data(self):
        self.real("vault_existing_review", "NO-DATA", id="10-Lessons/none.md")
        self.real("vault_existing_provenance", "NO-DATA", note="10-Lessons/lesson.md", attribute="promotion")

    # ---- the reader and the exit codes, scripted
    def test_a_no_data_or_fail_line_at_exit_zero_never_passes(self):
        for out, want in ((HIT, "PASS"), (HIT + "NO-DATA: seam module unavailable, mutation state unknown\n",
                          "NO-DATA"), ("NO-DATA What this estate has written\n", "NO-DATA"),
                          (HIT + "  VERDICT: FAIL\n", "FAIL"), (HIT + "latest run memory: NO-DATA: none\n", "PASS"),
                          ("NO-DATA x\nFAIL y\n", "FAIL"), ("hit\n", "NO-DATA")):
            self.scripted(out, want)

    def test_a_report_that_also_carries_findings_or_stale_notes_is_not_pass(self):  # VERD1.a
        for handler, good, bad in (
                ("vault_existing_review", "note: a.md\nstate: candidate\n", "note: a.md\nstate: candidate\nFINDINGS: 1\n"),
                ("vault_existing_lifecycle", "vault: /v\nnotes: 2\n  legacy       0\n",
                 "vault: /v\nnotes: 2\n  legacy       0\nFINDINGS, each named: 1\n"),
                ("vault_existing_retention", "census: 1 file-backed note(s) indexed, 0 stale\nclean: ok\n",
                 "STALE id=1 a.md\ncensus: 1 file-backed note(s) indexed, 1 stale\nclean: ok\n")):
            reader = bm_vault.DOOR_READERS[handler]
            self.assertEqual(("PASS", "NO-DATA"), (reader(good), reader(bad)), handler)

    def test_exit_one_reads_traceback_and_the_commands_own_no_data(self):
        got, _ = self.scripted("", "NO-DATA", code=1, err="Traceback (most recent call last):\n  x\nKeyError: k\n")
        self.assertIn("KeyError: k", got["output"])
        self.scripted("NO-DATA RECORDED FAILURES\n  Nothing matched.\n", "NO-DATA", code=1)
        self.scripted("REFUSED: separation of duties\n", "FAIL", code=1)

    def test_a_handler_without_a_reader_runs_nothing(self):
        for table in ({}, dict(bm_vault.DOOR_VERDICT_READERS, vault_existing_recall="no_such_reader")):
            with mock.patch.object(bm_vault, "DOOR_VERDICT_READERS", table):
                _, calls = self.scripted(HIT, "NO-DATA")
            self.assertEqual([], calls)

    def test_every_handler_names_a_known_reader(self):
        self.assertEqual(sorted(bm_vault.DOOR_HANDLERS), sorted(bm_vault.DOOR_VERDICT_READERS))
        for handler, reader in bm_vault.DOOR_VERDICT_READERS.items():
            self.assertIn(reader, bm_vault.DOOR_READERS, handler)

    def test_the_marker_readers_answer_as_brotherds_does(self):
        # each product's own success marker, then the same downgrades: no marker or no output is NO-DATA, a
        # NO-DATA line beside it is NO-DATA, a FAIL line beside it is FAIL, and `x: NO-DATA y` mid-line is not a line
        bds_path = os.path.join(ROOT, "products", "brotherds", "bds.py")
        pairs = [(bm_vault.DOOR_READERS["vault_existing_recall"], HIT)]
        if os.path.isfile(bds_path):  # an edition without BrotherDS pins the Vault reader alone
            sys.path.insert(0, os.path.dirname(bds_path))
            spec = importlib.util.spec_from_file_location("bds_for_vault_door_test", bds_path)
            bds = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(bds)
            pairs.append((bds.DOOR_READERS["registered"], "registered 'w' from claim C1 in /w/r.json\n"))
        for read, hit in pairs:
            for suffix, want in (("", "PASS"), ("NO-DATA: x\n", "NO-DATA"), ("  FAIL     G5.x\n", "FAIL"),
                                 ("VERDICT: NO-DATA\n", "NO-DATA"), ("a: NO-DATA b\n", "PASS")):
                self.assertEqual(want, read(hit + suffix), repr(suffix))
            self.assertEqual("NO-DATA", read(""))
            self.assertEqual("NO-DATA", read("{}\n"))


if __name__ == "__main__":
    unittest.main()
