#!/usr/bin/env python3
"""OUT1.k operation cases beside the frozen AssuranceAccess contract (scripts/test_assurance_access_contract.py).

The frozen class drives invoke_capability with a fake handler only. These cases pin what it cannot reach: the door's
assurance table and BrotherSBE's DOOR_HANDLERS agree (verb, inputs, permission), each routed operation reaches the
named existing subcommand with exactly the inputs given, a gate's refusal reaches the person in the gate's own words,
a NO-DATA line never becomes a PASS, preparing a handover never reports acceptance while acknowledging is its own
request needing the receiver and approve permission, the unbuilt exceptions command answers NO-DATA with a supported
alternative, a handler whose files this edition lacks runs nothing, and the supported list is generated from the
parser rather than typed.

Run: python3 -B scripts/test_assurance_door_ops.py
"""
import atexit
import io
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
SBE_ROOT = os.path.join(ROOT, "products", "brothersbe")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(SBE_ROOT, "src"))
import door  # noqa: E402


def _door_view(*capabilities):
    """The consumer view (door.consumer_admission) these routing tests run the door under: only `capabilities`
    are admitted, as an installed journey row or a maintainer's record would admit them (OUT1.n). No capabilities
    is the hidden case."""
    return mock.patch.object(door, "consumer_admission", return_value={
        "candidate": "test", "host": "claude", "capabilities": list(capabilities), "routes": set(), "queue": []})
from brothersbe import cli  # noqa: E402

TMP = tempfile.mkdtemp(prefix="assurance-door-")
atexit.register(shutil.rmtree, TMP, True)
D = os.path.join(TMP, "dir")
F = os.path.join(TMP, "file.json")
O = os.path.join(D, "out.json")
os.makedirs(D)
with open(F, "w") as _fh:
    _fh.write("{}")

READ_WRITE = {"approve": {"review_record", "handover_acknowledge", "handover_reject"},
              "write": {"verify", "intake", "impact", "review_route", "converge", "explain", "testkit_sheet",
                        "handover_prepare", "evidence_run", "work_start", "work_finish", "program_board", "map"}}
#: capability -> (typed inputs, the argv the existing command line must be started with, its folder or None)
EXPECT = {
    "review": ({"path": D}, ["review", D], None),
    "review_record": ({"path": D, "reviewer": "R", "reviewer_type": "human", "result": "approved",
                       "accept_risk": ["x"], "findings_json": F},
                      ["review", D, "--reviewer=R", "--reviewer-type=human", "--result=approved", "--accept-risk=x",
                       "--findings-json=" + F, "--write"], None),
    "verify": ({"path": D, "since": "HEAD~1", "named_paths": ["a.py"]},
               ["verify", D, "--since=HEAD~1", "--path=a.py"], None),
    "gate": ({"gate": "migration", "path": D, "strict": True},
             ["gate", "migration", D, "--strict", "--no-decisions"], None),
    "design": ({"path": D, "strict": True}, ["design", D, "--strict"], None),
    "score": ({"path": D}, ["score", D, "--no-decisions"], None),
    "intake": ({"answers": F, "path": D}, ["intake", D, "--answers=" + F], None),
    "impact": ({"path": D, "base": "main", "head": "HEAD", "intake": F, "disposition": F, "strict": True},
               ["impact", D, "--base=main", "--head=HEAD", "--intake=" + F, "--disposition=" + F, "--strict"], None),
    "policy": ({"path": D, "base": "main", "head": "HEAD", "policy": F, "receipts_dir": D, "dossier": D, "intake": F,
                "approvals": F, "waivers": F, "decision": F, "strict_waivers": True},
               ["policy", "evaluate", D, "--base=main", "--head=HEAD", "--policy=" + F, "--receipts-dir=" + D,
                "--dossier=" + D, "--intake=" + F, "--approvals=" + F, "--waivers=" + F, "--decision=" + F,
                "--strict-waivers"], None),
    "review_route": ({"path": D, "base": "main", "head": "HEAD", "work_profile": "backend"},
                     ["review-route", D, "--base=main", "--head=HEAD", "--work-profile=backend"], None),
    "converge": ({"dossier": D, "base": "a", "head": "b", "cwd": D}, ["converge", D, "--base=a", "--head=b"], D),
    "explain": ({"target": "003", "path": D}, ["explain", "003", "--path=" + D], None),
    "status": ({"path": D, "team": True, "base": "main"}, ["status", D, "--team", "--base=main"], None),
    "scope_report": ({"cwd": D}, ["scope", "report"], D),
    "scope_verify": ({"base": "main", "head": "HEAD", "strict": True, "cwd": D},
                     ["scope", "verify", "--base=main", "--head=HEAD", "--strict"], D),
    "fences": ({"cwd": D}, ["fences"], D),
    "fence_audit": ({"cwd": D}, ["fence-audit"], D),
    "instruction_surface": ({"path": D, "base_ref": "main", "declared": F, "strict": True},
                            ["instruction-surface", D, "main", "--declared=" + F, "--strict"], None),
    "task_list": ({"cwd": D}, ["task", "list"], D),
    "task_check": ({"cwd": D}, ["task", "check"], D),
    "lineage": ({"artifact": "src/a.py", "path": D}, ["lineage", "src/a.py", "--path=" + D], None),
    "evidence_verify": ({"receipt": F, "strict": True, "cwd": D}, ["evidence", "verify", F, "--strict"], D),
    "evidence_show": ({"receipt": F, "cwd": D}, ["evidence", "show", F], D),
    "handover_show": ({"dossier": D}, ["handover", "show", D], None),
    "program_status": ({"path": D}, ["program", "status", D], None),
    "program_check": ({"path": D}, ["program", "check", D], None),
    "protections_verify": ({"repository": "o/r", "branch": "main", "cwd": D},
                           ["protections", "verify", "--repository=o/r", "--branch=main"], D),
    "pr_verify": ({"number": "12", "repo": "o/r", "cwd": D}, ["pr", "verify", "12", "--repo=o/r"], D),
    "work_check": ({"task_id": "T1", "change": "c1", "cwd": D}, ["work", "check", "T1", "--change=c1"], D),
    "doctor": ({"cwd": D}, ["doctor"], D),
    "book_check": ({"path": D}, ["book", D, "--check"], None),
    "plan": ({"dossier": D, "strict": True}, ["plan", D, "--strict"], None),
    "adopt": ({"path": D}, ["adopt", D], None),
    "work_brief": ({"plan": F, "task": "T1", "cwd": D}, ["work", "brief", "--plan=" + F, "--task=T1"], D),
    "testkit_sheet": ({"dossier": D}, ["testkit", "sheet", D], None),
    "handover_prepare": ({"dossier": D, "outgoing": "a@x", "receiver": "b@x"},
                         ["handover", "prepare", D, "--outgoing=a@x", "--receiver=b@x"], None),
    "handover_acknowledge": ({"dossier": D, "receiver": "b@x"}, ["handover", "acknowledge", D, "--receiver=b@x"], None),
    "handover_reject": ({"dossier": D, "receiver": "b@x", "reason": "no"},
                        ["handover", "reject", D, "--receiver=b@x", "--reason=no"], None),
    "evidence_run": ({"check": "unit", "out": O, "base": "a", "head": "b", "covers": ["f.py"], "kind": ["tests"],
                      "checks_file": F, "cwd": D},
                     ["evidence", "run", "--check=unit", "--out=" + O, "--base=a", "--head=b", "--covers=f.py",
                      "--kind=tests", "--checks-file=" + F], D),
    "work_start": ({"task_id": "T1", "plan": F, "agent": "me", "cwd": D},
                   ["work", "start", "T1", "--plan=" + F, "--agent=me"], D),
    "work_finish": ({"task_id": "T1", "change": "c1", "cwd": D}, ["work", "finish", "T1", "--change=c1"], D),
    "program_board": ({"path": D, "out": O}, ["program", "board", D, "--out=" + O], None),
    "map": ({"path": D, "out": O}, ["map", D, "--out=" + O], None),
}
UNROUTED_BY_SAMPLE = {"discover", "exceptions"}  # discovery and the unbuilt command run nothing through a command line
GOOD = {"verdict": "PASS", "output": "checked", "evidence_refs": ["e1"]}


def short(cap):
    return cap[len("assurance."):]


def scripted(code=0, out="PASS gate migration\n", err=""):
    """A run() seam that records every start and answers as the command line would."""
    calls = []

    def run(argv, cwd):
        calls.append((list(argv), cwd))
        return code, out, err
    run.calls = calls
    return run


def through(cap, typed, allow=None, **reply):
    """run_assurance_capability over the real door_invoke, with the command line scripted."""
    run = scripted(**reply)
    got = door.run_assurance_capability("assurance." + cap, None, typed, allow,
                                        lambda h, i: cli.door_invoke(h, i, run=run))
    return got, run.calls


def allowing(cap):
    return [a for a in ("write", "approve") if cap in READ_WRITE[a]]


class TableAgreement(unittest.TestCase):
    def test_every_door_row_has_its_handler_with_the_same_verb_inputs_and_permission(self):
        rows = {c["handler"]: c for c in door.ASSURANCE_CAPABILITIES}
        self.assertEqual(sorted(set(cli.DOOR_HANDLERS) | {cli.DOOR_DISCOVER}), sorted(rows))
        for hid, h in cli.DOOR_HANDLERS.items():
            row = rows[hid]
            self.assertEqual(("assurance." + hid[len("assurance_existing_"):], h["verb"], list(h["need"]),
                              [h["authority"]]),
                             (row["id"], row["verb"], row["prerequisites"], row["required_authority"]), hid)
            self.assertEqual(sorted(h["need"]), sorted(set(h["need"])), hid)
            self.assertTrue(set(h["need"]) <= set(n for (n, _k, _f) in h["args"]), hid)

    def test_four_verbs_each_carry_operations_and_nothing_else(self):
        verbs = {}
        for c in door.ASSURANCE_CAPABILITIES:
            verbs.setdefault(c["verb"], []).append(c["id"])
        self.assertEqual(["deliver", "next", "review", "status"], sorted(verbs))
        self.assertEqual(sorted(cli.DOOR_VERBS), sorted(verbs))

    def test_write_and_approve_are_exactly_the_operations_that_save_or_decide(self):
        auth = {short(c["id"]): c["required_authority"] for c in door.ASSURANCE_CAPABILITIES}
        for kind, caps in READ_WRITE.items():
            self.assertEqual(caps, {k for k, v in auth.items() if v == [kind]}, kind)
        self.assertTrue(all(v in (["read"], ["write"], ["approve"]) for v in auth.values()))

    def test_the_frozen_contract_ids_are_the_review_row(self):
        row = next(c for c in door.ASSURANCE_CAPABILITIES if c["id"] == "assurance.review")
        self.assertEqual("assurance_existing_review", row["handler"])
        self.assertEqual(("review",), cli.DOOR_HANDLERS["assurance_existing_review"]["words"])

    def test_every_sample_names_a_real_capability_and_every_capability_has_a_sample(self):
        caps = {short(c["id"]) for c in door.ASSURANCE_CAPABILITIES}
        self.assertEqual(caps - UNROUTED_BY_SAMPLE, set(EXPECT))

    def test_no_handler_passes_a_force_apply_or_free_form_command(self):
        for hid, h in cli.DOOR_HANDLERS.items():
            sent = list(h["fixed"]) + [f for (_n, _k, f) in h["args"]]
            for bad in ("--force", "--apply", "--why", "--set-deployed-ref", "--"):
                self.assertNotIn(bad, sent, hid)
            if "--write" in sent:  # only a named reviewer's recorded verdict, which needs approve
                self.assertEqual(("assurance_existing_review_record", "approve"), (hid, h["authority"]))


class EveryOperationReachesItsSubcommand(unittest.TestCase):
    pass


def _make(cap, typed, argv, folder):
    def test(self):
        got, calls = through(cap, typed, allowing(cap))
        self.assertEqual([(argv, folder)], calls)
        self.assertEqual("COMPLETE", got["state"])
        self.assertEqual("PASS", got["verdict"])
        self.assertEqual(next(c["verb"] for c in door.ASSURANCE_CAPABILITIES if c["id"] == "assurance." + cap),
                         got["verb"])
        label = " ".join(cli.DOOR_HANDLERS["assurance_existing_" + cap]["words"])
        self.assertTrue(got["evidence_refs"][0].startswith("sbe %s exit 0 stdout sha256:" % label), got)
        self.assertIn(argv[0], cli._door_commands())
    return test


for _cap, (_typed, _argv, _folder) in EXPECT.items():
    setattr(EveryOperationReachesItsSubcommand, "test_%s_reaches_%s" % (_cap, _argv[0].replace("-", "_")),
            _make(_cap, _typed, _argv, _folder))


class GateRefusal(unittest.TestCase):
    def test_a_refusal_reaches_the_person_in_the_gates_own_words(self):
        said = "  migration FAIL  no reverse evidence for 0007_drop_col.sql [severity: gate]\n"
        got, _ = through("gate", {"gate": "migration", "path": D}, code=1, out=said)
        self.assertEqual(("FAILED", "FAIL", []), (got["state"], got["verdict"], got["evidence_refs"]))
        self.assertIn("no reverse evidence for 0007_drop_col.sql", got["output"])

    def test_a_reason_printed_on_stderr_is_kept_with_an_exit_one(self):
        got, _ = through("handover_acknowledge", {"dossier": D, "receiver": "b@x"}, ["approve"], code=1, out="",
                         err="sbe handover acknowledge: refused. ABSENT  approval: no 10-approval.json recorded")
        self.assertEqual("FAILED", got["state"])
        self.assertIn("ABSENT  approval: no 10-approval.json recorded", got["output"])

    def test_an_exit_zero_with_a_fail_line_is_still_a_fail(self):
        got, _ = through("design", {"path": D}, out="  numbers FAIL  headline figure has no derivation\n")
        self.assertEqual(("FAILED", "FAIL"), (got["state"], got["verdict"]))

    def test_a_nodata_line_never_becomes_a_pass(self):
        for caps, out in (("gate", "  migration NO-DATA  no migration-receipt.json read\n"),
                          ("gate", "  numbers PASS  ok\n  migration NO-DATA  nothing to read\n"),
                          ("gate", "  approval WAIVED  waived by x\n"),
                          ("design", "NO-DATA. scope: intake absent\n"),
                          ("status", "  NO-DATA. scope: no evidence store found\n"),
                          ("status", "  WAIVED. scope: x\n")):
            typed = {"gate": "migration", "path": D} if caps == "gate" else {"path": D}
            got, _ = through(caps, typed, out=out)
            self.assertEqual(("NO-DATA", "NO-DATA", []), (got["state"], got["verdict"], got["evidence_refs"]), out)

    def test_a_report_with_one_pass_among_missing_items_is_no_data_on_every_row(self):  # VERD1.a: was COMPLETE on status
        out = "  scope PASS  inside declared scope\n  NO-DATA. intake: absent\n"
        got, _ = through("status", {"path": D}, out=out)
        self.assertEqual("NO-DATA", got["state"])
        got, _ = through("design", {"path": D}, out=out)
        self.assertEqual("NO-DATA", got["state"])

    def test_exit_two_three_and_empty_answers_are_nodata_and_odd_exits_fail(self):
        for kw, state in (({"code": 2, "out": "usage"}, "NO-DATA"), ({"code": 3, "out": "NOT BUILT"}, "NO-DATA"),
                          ({"code": 0, "out": ""}, "NO-DATA"), ({"code": 139, "out": ""}, "FAILED")):
            got, _ = through("review", {"path": D}, **kw)
            self.assertEqual((state, []), (got["state"], got["evidence_refs"]), kw)

    def test_the_real_gate_over_an_empty_folder_says_nodata_and_writes_nothing(self):
        before = sorted(os.listdir(D))
        got = door.run_assurance_capability("assurance.gate", None, {"gate": "migration", "path": D}, None)
        self.assertEqual(("NO-DATA", "NO-DATA", []), (got["state"], got["verdict"], got["evidence_refs"]))
        self.assertIn("migration NO-DATA", got["output"])
        self.assertEqual(before, sorted(os.listdir(D)))


class HandoverIsNotAcceptance(unittest.TestCase):
    PREP = {"dossier": D, "outgoing": "a@x", "receiver": "b@x"}

    def test_preparing_never_reports_acceptance_even_when_the_output_says_accepted(self):
        got, calls = through("handover_prepare", self.PREP, ["write"], out="PASS prepared; b@x accepted ownership\n")
        self.assertEqual("COMPLETE", got["state"])
        self.assertEqual((door.NODATA, False), (got["receiver_acceptance"], got["ownership_transferred"]))
        self.assertIn("not acceptance", got["note"])
        self.assertEqual([["handover", "prepare", D, "--outgoing=a@x", "--receiver=b@x"]], [c[0] for c in calls])

    def test_acknowledging_needs_the_receiver_and_launches_nothing_without_it(self):
        got, calls = through("handover_acknowledge", {"dossier": D}, ["approve"])
        self.assertEqual(([], ["receiver"], "BLOCKED"), (calls, got["missing_prerequisites"], got["state"]))
        self.assertEqual((False, door.NODATA), (got["ownership_transferred"], got["receiver_acceptance"]))

    def test_acknowledging_needs_approve_and_preparing_needs_write(self):
        for allow in (None, ["write"]):
            got, calls = through("handover_acknowledge", {"dossier": D, "receiver": "b@x"}, allow)
            self.assertEqual(("REFUSED", ["approve"], []), (got["state"], got["missing_authority"], calls), allow)
        got, calls = through("handover_prepare", self.PREP, ["approve"])
        self.assertEqual(("REFUSED", ["write"], []), (got["state"], got["missing_authority"], calls))

    def test_prepare_cannot_be_told_to_acknowledge_or_force(self):
        for extra in ({"acknowledge": True}, {"force": True}, {"why": "because"}):
            got, calls = through("handover_prepare", dict(self.PREP, **extra), ["write"])
            self.assertEqual(("NO-DATA", []), (got["state"], calls), extra)
            self.assertIn("is not an input of this operation", got["output"])
        got, calls = through("handover_acknowledge", {"dossier": D, "receiver": "b@x", "force": True}, ["approve"])
        self.assertEqual(("NO-DATA", []), (got["state"], calls))

    def test_acknowledging_is_its_own_call_with_its_own_command(self):
        got, calls = through("handover_acknowledge", {"dossier": D, "receiver": "b@x"}, ["approve"])
        self.assertEqual([["handover", "acknowledge", D, "--receiver=b@x"]], [c[0] for c in calls])
        self.assertEqual(("recorded", True), (got["receiver_acceptance"], got["ownership_transferred"]))
        self.assertIn("cannot check who is typing", got["note"])

    def test_rejecting_declines_and_ownership_stays(self):
        got, _ = through("handover_reject", {"dossier": D, "receiver": "b@x", "reason": "no"}, ["approve"])
        self.assertEqual(("declined", False), (got["receiver_acceptance"], got["ownership_transferred"]))

    def test_a_refused_acknowledgement_claims_no_transfer(self):
        got, _ = through("handover_acknowledge", {"dossier": D, "receiver": "b@x"}, ["approve"], code=1, out="",
                         err="refused: ABSENT  review: no review record is saved for this change")
        self.assertEqual(("FAILED", door.NODATA, False), (got["state"], got["receiver_acceptance"],
                                                           got["ownership_transferred"]))
        self.assertIn("ABSENT  review", got["output"])

    def test_the_real_journey_prepares_shows_and_is_refused_acceptance_over_missing_evidence(self):
        repo = os.path.join(TMP, "repo")
        doss = os.path.join(repo, "design", "c1")
        os.makedirs(doss)
        with open(os.path.join(doss, "00-intake.json"), "w") as fh:
            json.dump({"answers": {"changes_contract": "n", "crosses_boundary": "n", "reversible_under_hour": "y",
                                   "touches_sensitive": "n", "consumers": "none"}, "tier": "T1", "override": None,
                       "override_reason": None}, fh)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t.t", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@t.t")
        for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "init"]):
            subprocess.run(["git", "-C", repo] + cmd, check=True, env=env, stdin=subprocess.DEVNULL)
        got = door.run_assurance_capability("assurance.handover_prepare", None,
                                            dict(self.PREP, dossier=doss, outgoing="alice@example.com",
                                                 receiver="bob@example.com"), ["write"])
        # VERD1.a: the real `sbe handover prepare` prints no verdict word, so exit 0 alone is NO-DATA (was COMPLETE)
        self.assertEqual(("NO-DATA", []), (got["state"], got["evidence_refs"]), got)
        self.assertEqual((door.NODATA, False), (got["receiver_acceptance"], got["ownership_transferred"]))
        record = os.path.join(doss, "12-handover.json")
        self.assertTrue(os.path.isfile(record))
        shown = door.run_assurance_capability("assurance.handover_show", None, {"dossier": doss}, None)
        self.assertEqual("NO-DATA", shown["state"], shown)
        self.assertIn("prepared", shown["output"])
        asked = door.run_assurance_capability("assurance.handover_acknowledge", None,
                                              {"dossier": doss, "receiver": "bob@example.com"}, ["approve"])
        self.assertEqual(("FAILED", False, door.NODATA), (asked["state"], asked["ownership_transferred"],
                                                           asked["receiver_acceptance"]), asked)
        self.assertIn("ABSENT", asked["output"])
        with open(record) as fh:
            self.assertNotIn("accepted", json.dumps(json.load(fh).get("status", "")))


class UnbuiltIsNeverSupported(unittest.TestCase):
    def test_exceptions_answers_nodata_naming_itself_unbuilt_and_a_supported_alternative(self):
        for allow in (None, ["write", "approve"]):
            got, calls = through("exceptions", {}, allow)
            self.assertEqual(("NO-DATA", "NO-DATA", [], []), (got["state"], got["verdict"], got["evidence_refs"],
                                                               [c for c in calls]))
            self.assertIn("NOT BUILT", got["output"])
            self.assertIn("assurance.policy", got["output"])
            self.assertIn("assurance.status", got["output"])

    def test_the_real_command_line_agrees(self):
        got = door.run_assurance_capability("assurance.exceptions", None, {}, None)
        self.assertEqual(("NO-DATA", []), (got["state"], got["evidence_refs"]))
        self.assertIn("NOT BUILT", got["output"])

    def test_discovery_marks_it_unavailable_and_every_built_handler_supported(self):
        found = cli.door_discovery()
        states = {h["id"]: h["state"] for h in found["handlers"]}
        self.assertEqual("unavailable", states["assurance_existing_exceptions"])
        self.assertEqual({"supported"}, set(v for k, v in states.items() if k != "assurance_existing_exceptions"))
        row = next(h for h in found["handlers"] if h["id"] == "assurance_existing_exceptions")
        self.assertIn("assurance.policy", row["alternative"])
        self.assertIn("owner, approver or expiry", row["reason"])
        self.assertEqual("unavailable", next(c for c in found["commands"] if c["command"] == "exceptions")["state"])

    def test_the_same_handler_turns_supported_when_the_command_is_built(self):
        built = [(n, h, (lambda a: 0) if n == "exceptions" else r) for (n, h, r) in cli.COMMANDS]
        with mock.patch.object(cli, "COMMANDS", built):
            state = next(h["state"] for h in cli.door_discovery()["handlers"]
                         if h["id"] == "assurance_existing_exceptions")
            run = scripted(out="PASS owner x expires 2027-01-01\n")
            got = cli.door_invoke("assurance_existing_exceptions", {}, run=run)
        self.assertEqual(("supported", "PASS", [(["exceptions"], None)]), (state, got["verdict"], run.calls))


class EditionGuard(unittest.TestCase):
    def test_a_handler_whose_files_this_edition_lacks_answers_nodata_and_runs_nothing(self):
        checked = 0
        for cap, (typed, _argv, _folder) in EXPECT.items():
            if not cli.DOOR_COMMAND_FILES.get(cli.DOOR_HANDLERS["assurance_existing_" + cap]["words"][0]):
                continue
            run = scripted()
            with mock.patch.object(cli, "_door_present", return_value=False):
                got = cli.door_invoke("assurance_existing_" + cap, typed, run=run)
            self.assertEqual(("NO-DATA", [], []), (got["verdict"], got["evidence_refs"], run.calls), cap)
            self.assertIn("not in this edition", got["output"], cap)
            checked += 1
        self.assertEqual(len(EXPECT) - 1, checked)  # every operation but doctor needs a file

    def test_the_real_check_reads_the_installation_root(self):
        edition = os.path.join(TMP, "edition")
        os.makedirs(edition)
        run = scripted()
        with mock.patch.object(cli, "repo_root", return_value=edition):
            gone = cli.door_invoke("assurance_existing_gate", {"gate": "migration", "path": D}, run=run)
            os.makedirs(os.path.join(edition, "tools"))
            with open(os.path.join(edition, "tools", "sbe_gate.py"), "w") as fh:
                fh.write("")
            here = cli.door_invoke("assurance_existing_gate", {"gate": "migration", "path": D}, run=run)
        self.assertEqual(("NO-DATA", "PASS", 1), (gone["verdict"], here["verdict"], len(run.calls)))
        self.assertIn("tools/sbe_gate.py is not in this edition", gone["output"])

    def test_the_door_answers_nodata_when_no_brothersbe_sits_beside_it(self):
        with mock.patch.object(door, "assurance_tool", return_value=None):
            got = door.assurance_invoke("assurance_existing_gate", {})
        self.assertEqual(("NO-DATA", []), (got["verdict"], got["evidence_refs"]))
        self.assertIn("not installed beside the door", got["output"])


class DiscoveryIsGenerated(unittest.TestCase):
    def test_the_door_lists_what_the_command_line_declares(self):
        got = door.run_assurance_capability("assurance.discover", None, {}, None)
        self.assertEqual("COMPLETE", got["state"], got)
        found = json.loads(got["output"])
        self.assertEqual(sorted(cli.DOOR_HANDLERS), sorted(h["id"] for h in found["handlers"]))
        self.assertEqual(sorted(c["id"] for c in door.ASSURANCE_CAPABILITIES if not c["id"].endswith("discover")),
                         sorted(h["capability"] for h in found["handlers"]))
        self.assertEqual(sorted(n for (n, _h, _r) in cli.COMMANDS), sorted(c["command"] for c in found["commands"]))
        self.assertEqual({"supported", "unavailable", "not_routed"}, {c["state"] for c in found["commands"]})

    def test_a_subcommand_added_to_the_parser_is_listed_before_anyone_routes_it(self):
        extra = list(cli.COMMANDS) + [("zzfake", "a command added later", lambda a: 0)]
        with mock.patch.object(cli, "COMMANDS", extra):
            row = next(c for c in cli.door_discovery()["commands"] if c["command"] == "zzfake")
            self.assertEqual(("not_routed", []), (row["state"], row["handlers"]))
            with mock.patch.dict(cli.DOOR_HANDLERS, {"assurance_existing_zzfake": cli._handler(
                    "review", ("zzfake",), (("path", "dir", "pos"),), ("path",))}):
                found = cli.door_discovery()
                got = next(h for h in found["handlers"] if h["id"] == "assurance_existing_zzfake")
                self.assertEqual(("supported", "assurance.zzfake"), (got["state"], got["capability"]))
                self.assertEqual("supported", next(c for c in found["commands"] if c["command"] == "zzfake")["state"])
                run = scripted()
                self.assertEqual("PASS", cli.door_invoke("assurance_existing_zzfake", {"path": D}, run=run)["verdict"])
                self.assertEqual([(["zzfake", D], None)], run.calls)

    def test_a_handler_whose_command_this_parser_lacks_is_absent_and_runs_nothing(self):
        with mock.patch.dict(cli.DOOR_HANDLERS, {"assurance_existing_gone": cli._handler("review", ("gonecmd",))}):
            row = next(h for h in cli.door_discovery()["handlers"] if h["id"] == "assurance_existing_gone")
            run = scripted()
            got = cli.door_invoke("assurance_existing_gone", {}, run=run)
        self.assertEqual(("absent", "NO-DATA", []), (row["state"], got["verdict"], run.calls))
        self.assertIn("has no gonecmd command", got["output"])

    def test_commands_with_no_handler_are_said_to_be_not_routed(self):
        states = {c["command"]: c["state"] for c in cli.door_discovery()["commands"]}
        for name in ("init", "version", "record", "decide", "inspect-change"):
            self.assertEqual("not_routed", states[name], name)

    def test_the_door_command_is_not_one_of_the_public_commands(self):
        self.assertNotIn("door", [n for (n, _h, _r) in cli.COMMANDS])
        self.assertNotIn("door", cli.PASSTHROUGH)


class AuthorityAndEvidence(unittest.TestCase):
    def test_reads_need_nothing_and_writes_and_approvals_are_never_filled_in(self):
        for cap, (typed, _argv, _folder) in EXPECT.items():
            need = allowing(cap)
            got, calls = through(cap, typed, None)
            if need:
                self.assertEqual(("REFUSED", need, []), (got["state"], got["missing_authority"], calls), cap)
            else:
                self.assertEqual(("COMPLETE", 1), (got["state"], len(calls)), cap)
        got, calls = through("verify", {"path": D}, ["approve"])
        self.assertEqual(("REFUSED", ["write"], []), (got["state"], got["missing_authority"], calls))
        got, calls = through("review_record", {"path": D, "reviewer": "r", "reviewer_type": "human",
                                               "result": "approved"}, ["write"])
        self.assertEqual(("REFUSED", ["approve"], []), (got["state"], got["missing_authority"], calls))

    def test_evidence_is_carried_through_and_a_pass_without_it_is_unproven(self):
        got = door.run_assurance_capability("assurance.review", None, {"path": D}, None,
                                            lambda h, i: dict(GOOD, evidence_refs=["e1", "e2"]))
        self.assertEqual((["e1", "e2"], "checked"), (got["evidence_refs"], got["output"]))
        got = door.run_assurance_capability("assurance.review", None, {"path": D}, None,
                                            lambda h, i: dict(GOOD, evidence_refs=[]))
        self.assertEqual("UNPROVEN", got["state"])

    def test_the_command_line_evidence_names_the_command_the_output_and_each_file_given(self):
        run = scripted(out="  scope PASS  fine\n")
        got = cli.door_invoke("assurance_existing_policy", {"path": D, "policy": F}, run=run)
        import hashlib
        self.assertEqual("sbe policy evaluate exit 0 stdout sha256:%s"
                         % hashlib.sha256(b"  scope PASS  fine\n").hexdigest(), got["evidence_refs"][0])
        self.assertIn(D + " (folder)", got["evidence_refs"])
        with open(F, "rb") as fh:
            self.assertIn("%s sha256:%s" % (F, hashlib.sha256(fh.read()).hexdigest()), got["evidence_refs"])
        for verdict_run in (scripted(code=1, out="x"), scripted(code=2, out="x"), scripted(out="n NO-DATA y")):
            self.assertEqual([], cli.door_invoke("assurance_existing_policy", {"path": D}, run=verdict_run)
                             ["evidence_refs"])


class InputTyping(unittest.TestCase):
    def refused(self, handler, inputs, why):
        run = scripted()
        got = cli.door_invoke(handler, inputs, run=run)
        self.assertEqual(("NO-DATA", []), (got["verdict"], run.calls), inputs)
        self.assertIn(why, got["output"], inputs)

    def test_each_wrongly_shaped_input_is_refused_before_anything_starts(self):
        dashed = os.path.join(TMP, "dashed")  # a real file whose name starts with a dash: only the dash guard can refuse
        os.makedirs(dashed, exist_ok=True)
        with open(os.path.join(dashed, "-r.json"), "w") as fh:
            fh.write("{}")
        self.refused("assurance_existing_evidence_show", {"receipt": "-r.json", "cwd": dashed}, "starts with a dash")
        self.refused("assurance_existing_review", {"path": "-rf"}, "folder that is not there")
        self.refused("assurance_existing_review", {"path": 7}, "must be non-empty text")
        self.refused("assurance_existing_review", {"path": os.path.join(D, "missing")}, "folder that is not there")
        self.refused("assurance_existing_review", {}, "missing input: path")
        self.refused("assurance_existing_review", {"path": D, "strict": True}, "not an input of this operation")
        self.refused("assurance_existing_design", {"path": D, "strict": "yes"}, "true or false")
        self.refused("assurance_existing_verify", {"path": D, "named_paths": "a.py"}, "list of non-empty text")
        self.refused("assurance_existing_impact", {"path": D, "intake": os.path.join(D, "no.json")},
                     "file that is not there")
        self.refused("assurance_existing_gate", {"gate": "nonesuch", "path": D}, "must be one of numbers")
        self.refused("assurance_existing_gate", {"gate": "-x", "path": D}, "must be one of")
        self.refused("assurance_existing_fences", {"cwd": os.path.join(D, "missing")}, "folder that is not there")
        self.refused("nonesuch", {}, "no BrotherSBE handler is named")

    def test_a_value_that_starts_with_a_dash_is_safe_only_inside_an_equals_flag(self):
        run = scripted()
        cli.door_invoke("assurance_existing_impact", {"path": D, "base": "-x"}, run=run)
        self.assertEqual([(["impact", D, "--base=-x"], None)], run.calls)

    def test_a_relative_cwd_and_a_relative_file_resolve_against_it(self):
        run = scripted()
        got = cli.door_invoke("assurance_existing_evidence_show", {"receipt": "file.json", "cwd": TMP}, run=run)
        self.assertEqual(("PASS", [(["evidence", "show", "file.json"], TMP)]), (got["verdict"], run.calls))

    def test_inputs_that_are_not_a_set_of_named_values_are_refused(self):
        run = scripted()
        for bad in (None, [], "x"):
            self.assertEqual("NO-DATA", cli.door_invoke("assurance_existing_review", bad, run=run)["verdict"])
        self.assertEqual([], run.calls)

    def test_a_command_that_cannot_start_or_times_out_is_nodata(self):
        for exc in (OSError("no such launcher"), subprocess.TimeoutExpired("sbe", 1)):
            def run(argv, cwd, exc=exc):
                raise exc
            got = cli.door_invoke("assurance_existing_review", {"path": D}, run=run)
            self.assertEqual(("NO-DATA", []), (got["verdict"], got["evidence_refs"]))


class DoorEntry(unittest.TestCase):
    def ask(self, handler, text):
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(text)), mock.patch("sys.stdout", out):
            code = cli.main(["door", handler])
        return code, json.loads(out.getvalue())

    def test_sbe_door_reads_json_on_stdin_and_prints_one_json_reply(self):
        code, reply = self.ask("assurance_existing_exceptions", "{}")
        self.assertEqual((0, "NO-DATA"), (code, reply["verdict"]))
        code, reply = self.ask("assurance_existing_review", "not json")
        self.assertEqual((0, "NO-DATA"), (code, reply["verdict"]))
        self.assertIn("named values", reply["output"])

    def test_door_main_sends_assurance_ids_to_the_assurance_route_only(self):
        seen = []
        stub = lambda cap, outcome, typed, allow: seen.append(cap) or {"state": "COMPLETE"}  # noqa: E731
        with _door_view("assurance.*"), mock.patch.object(door, "run_assurance_capability", stub), \
                mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(0, door.main(["--capability", "assurance.gate", "--input", "path=x"]))
        self.assertEqual(["assurance.gate"], seen)

    def test_a_hidden_assurance_capability_is_refused_with_the_honest_answer_and_nothing_runs(self):
        out = io.StringIO()
        with _door_view(), mock.patch.object(door, "run_assurance_capability") as ran, mock.patch("sys.stdout", out):
            self.assertEqual(2, door.main(["--capability", "assurance.gate", "--input", "path=x"]))
        self.assertFalse(ran.called)
        self.assertEqual(door.ADMISSION_LIMIT, json.loads(out.getvalue())["output"])

    def test_an_unknown_capability_lists_the_assurance_ids_too(self):
        got = door.run_assurance_capability("assurance.nonesuch")
        self.assertEqual("BLOCKED", got["state"])
        self.assertIn("assurance.handover_prepare", got["output"])


class ReachRows(unittest.TestCase):
    def test_the_assurance_rows_are_consumer_routes_to_registered_capabilities(self):
        import reach_check
        with open(os.path.join(ROOT, "docs", "plan", "REACH.json")) as fh:
            data = json.load(fh)
        caps = {c["id"]: c for c in data["capabilities"]}
        inv = {r["id"]: r for r in data["inventory"]}
        for row, route in (("assurance-review", "assurance-check"), ("assurance-control", "assurance-check"),
                           ("assurance-handover", "team-handover")):
            self.assertEqual(("consumer", route, "NO-DATA"), (inv[row]["disposition"], inv[row]["routed"],
                                                               inv[row]["installed_execution"]), row)
            self.assertEqual([], reach_check.check_row(caps[route]), route)
        self.assertEqual(("review", "deliver"), (caps["assurance-check"]["verb"], caps["team-handover"]["verb"]))


if __name__ == "__main__":
    unittest.main()
