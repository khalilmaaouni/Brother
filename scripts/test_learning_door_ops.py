#!/usr/bin/env python3
"""OUT1.i integrator cases beside the frozen LearningAccess contract (test_workflows.py, the pack harness).

The frozen class drives invoke_capability with a fake handler only. These cases pin what it cannot reach: each learning
row runs the EXISTING lesson tools and decision recorder through the door's own route (door -> dream_bridge.py door ->
bm_learn.py / bm_recurrence.py / dream_record.py), against a REAL lesson store and a REAL decision journal built in a
temporary folder (never the owner's Vault or journals), and reads their own answers. Where a defect cannot be made to
happen by a real tool (a tool that still serves a forgotten lesson, a capture that comes back approved) a scripted
stand in tool is used and says so.

Pinned: a candidate is never worded or served as an established lesson; a forgotten lesson stops being served, is
checked, and what is kept and what is out of reach is said; an alternative nobody chose never gets an outcome; a read
creates nothing; authority is never widened (capture writes a proposal only, forget needs approve); the id lists, the
admission groups, the reach check and the shipped runtime name the same nine ids; and a consumer cannot reach any of
them before admission.

Run: python3 -B scripts/test_learning_door_ops.py
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
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
import dream_bridge  # noqa: E402
import reach_check  # noqa: E402

TOOLS = os.path.join(ROOT, "products", "brothermode", "tools")
IDS = {"learning.report", "learning.reuse", "learning.capture", "learning.recurrence", "learning.forget",
       "learning.decisions", "learning.replay", "learning.decision", "learning.outcome"}
AUTHORITY = {"learning.report": "read", "learning.reuse": "read", "learning.capture": "write",
             "learning.recurrence": "read", "learning.forget": "approve", "learning.decisions": "read",
             "learning.replay": "read", "learning.decision": "write", "learning.outcome": "write"}
GROUPS = {"learning-lessons": {"learning.report", "learning.reuse", "learning.capture", "learning.recurrence"},
          "learning-replay": {"learning.decisions", "learning.replay", "learning.decision", "learning.outcome"},
          "learning-governance": {"learning.forget"}}
DEFAULT_RECORDER = any(os.path.isfile(c) for c in dream_bridge.recorder_candidates())
needs_recorder = unittest.skipUnless(DEFAULT_RECORDER, "no dream recorder on the default search path (an export tree)")


def _load(name):
    spec = importlib.util.spec_from_file_location("t_" + name, os.path.join(TOOLS, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BML = _load("bm_learning")
STRIP = ("BROTHER_DREAM_RECORD_PATH", "BROTHER_JOURNAL_PATH", "BM_STATE_CHANGE_RECEIPT", "BM_APPROVAL_RECEIPT",
         "BROTHERMODE_FTS5", "BROTHERMODE_NO_FTS5", "BROTHER_RUN_DIR", "BROTHERMODE_ROOT")


class _Env(unittest.TestCase):
    """A throwaway home, project root, recurrence store and run folder, with the environment pointed at them."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="learning-door-")
        self.root = os.path.join(self.tmp, "proj")
        self.run_dir = os.path.join(self.tmp, "run")
        os.makedirs(self.root)
        os.makedirs(self.run_dir)
        env = {k: v for k, v in os.environ.items() if k not in STRIP}
        env.update(HOME=self.tmp, BROTHERMODE_ROOT=self.root,
                   BROTHERMODE_RECURRENCE_DB=os.path.join(self.tmp, "rec.sqlite3"))
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        dream_bridge.reset_for_tests()
        self.addCleanup(dream_bridge.reset_for_tests)

    def cli(self, script, *argv, ok=(0,)):
        proc = subprocess.run([sys.executable, "-B", os.path.join(TOOLS, script)] + list(argv), cwd=self.root,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, env=dict(os.environ))
        self.assertIn(proc.returncode, ok, "%s %s: %s" % (script, argv, proc.stderr))
        return proc.stdout

    def ask(self, handler, **inputs):
        return dream_bridge.door_invoke(handler, inputs)

    def snapshot(self):
        """Every file under the temp home and its bytes' digest: a read must leave this unchanged. SQLite's WAL
        shared memory index (*-shm) is skipped: readers write their read marks into it (Python 3.9's SQLite does,
        3.13's did not), while the store and the WAL itself, which are what a write would change, stay in."""
        seen = {}
        for base, _dirs, files in os.walk(self.tmp):
            for name in files:
                if name.endswith("-shm"):
                    continue
                path = os.path.join(base, name)
                with open(path, "rb") as fh:
                    seen[os.path.relpath(path, self.tmp)] = hashlib.sha256(fh.read()).hexdigest()
        return seen


class _Store(_Env):
    """_Env plus a real, initialised lesson store."""

    def setUp(self):
        _Env.setUp(self)
        self.cli("bm_store.py", "init")

    def candidate(self, trigger, action, because="a past mistake"):
        got = json.loads(self.cli("bm_learn.py", "capture", "--trigger", trigger, "--action", action, "--because",
                                  because, "--json"))
        return got["candidate_uuid"]

    def approve(self, trigger, action, because="a past mistake"):
        """A candidate taken through the owner's real question and receipt: an approved rule."""
        cand = self.candidate(trigger, action, because)
        token = json.loads(self.cli("bm_learn.py", "grant-approval", cand[:8], "--answer", "yes, approve it",
                                    "--json"))["token"]
        return json.loads(self.cli("bm_learn.py", "approve", cand[:8], "--receipt", token, "--ref",
                                   "owner said yes in chat", "--json"))

    def rules(self, *flags):
        return json.loads(self.cli("bm_learn.py", "rules", "--json", *flags))


def _sections(text):
    """The report's text cut at its three headings."""
    est = text.index("ESTABLISHED")
    prop = text.index("PROPOSED (")
    stale = text.find("STALE OR DISPUTED")
    return text[est:stale if 0 <= stale < prop else prop], text[prop:]


_FAKE_TOOL = '''#!/usr/bin/env python3
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
argv = sys.argv[1:]
key = argv[0] + (":all" if "--all" in argv else "")
with open(os.path.join(here, "calls.jsonl"), "a") as fh:
    fh.write(json.dumps({"argv": argv, "receipt_env": os.environ.get("BM_STATE_CHANGE_RECEIPT")}) + "\\n")
script = json.load(open(os.path.join(here, "script.json")))
state_path = os.path.join(here, "state.json")
state = json.load(open(state_path)) if os.path.exists(state_path) else {}
queue = script.get(key)
if queue is None:
    sys.stderr.write("no scripted reply for " + key + "\\n")
    sys.exit(2)
n = state.get(key, 0)
state[key] = n + 1
json.dump(state, open(state_path, "w"))
print(queue[min(n, len(queue) - 1)])
'''


class _FakeTools(object):
    """A scripted stand in for the lesson tools folder, used ONLY where a real tool cannot be made to misbehave (one
    that still serves a forgotten lesson, a capture that comes back approved). `script` maps a command (`rules:all`
    for `rules --all`) to the stdout it prints, one entry per call, the last repeating. bm_learning.py is the real
    one, so the door's own judgement of each answer is the real judgement."""

    def __init__(self, case):
        self.dir = tempfile.mkdtemp(prefix="fake-tools-")
        case.addCleanup(shutil.rmtree, self.dir, True)
        shutil.copy(os.path.join(TOOLS, "bm_learning.py"), self.dir)
        with open(os.path.join(self.dir, "bm_learn.py"), "w", encoding="utf-8") as fh:
            fh.write(_FAKE_TOOL)

    def script(self, replies):
        with open(os.path.join(self.dir, "script.json"), "w", encoding="utf-8") as fh:
            json.dump(replies, fh)

    def calls(self):
        path = os.path.join(self.dir, "calls.jsonl")
        if not os.path.isfile(path):
            return []  # the tool was never run
        with open(path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]


class TheListIsOneList(unittest.TestCase):
    """Omit a supported capability from discovery and one of these goes red."""

    def test_the_door_runs_exactly_the_nine_and_each_authority_is_as_designed(self):
        self.assertEqual(IDS, {c["id"] for c in door.LEARNING_CAPABILITIES})
        for row in door.LEARNING_CAPABILITIES:
            self.assertEqual([AUTHORITY[row["id"]]], row["required_authority"], row["id"])
            self.assertTrue(row["handler"].startswith("learning_existing_"), row)

    def test_every_row_names_a_bridge_handler_with_the_same_prerequisites(self):
        self.assertEqual({c["handler"] for c in door.LEARNING_CAPABILITIES}, set(dream_bridge.DOOR_HANDLERS))
        for row in door.LEARNING_CAPABILITIES:
            self.assertEqual(list(dream_bridge.DOOR_HANDLERS[row["handler"]]), row["prerequisites"], row["id"])

    def test_nothing_that_promotes_or_approves_is_a_row(self):
        for row in door.LEARNING_CAPABILITIES:
            self.assertNotRegex(row["id"], r"promote|approve|supersede|deprecate|rollback|judge|policy")

    def test_every_one_is_in_an_admission_group_and_in_the_reach_check(self):
        with open(os.path.join(ROOT, "docs", "plan", "ADMISSION.json"), encoding="utf-8") as fh:
            groups = json.load(fh)["entries"]
        for name, ids in GROUPS.items():
            self.assertEqual(ids, set(groups[name]["capabilities"]), name)
        self.assertTrue(IDS <= set(reach_check.door_capability_ids()))

    def test_an_unknown_learning_id_names_the_known_ones_including_every_learning_id(self):
        got = door.run_learning_capability("learning.nothing")
        for cap in IDS:
            self.assertIn(cap, got["output"])

    def test_the_bridge_and_the_report_and_replay_ship_in_the_runtime(self):
        runtime = os.path.join(ROOT, "bundle", "runtime")
        for name in ("dream_bridge.py", "dream_record.py", "dream_replay.py", "dream_report.py", "journal.py"):
            self.assertTrue(os.path.isfile(os.path.join(runtime, name)), name)
        for name in ("bm_learn.py", "bm_learning.py", "bm_recurrence.py", "bm_store.py"):
            self.assertTrue(os.path.isfile(os.path.join(runtime, "hooks", "brothermode", "tools", name)), name)

    def test_the_reach_registry_routes_the_learning_rows_through_the_door(self):
        with open(door.REACH_REGISTRY, encoding="utf-8") as fh:
            data = json.load(fh)
        routes = {c["id"]: c for c in data["capabilities"]}
        self.assertEqual("def run_learning_capability", routes["learning-access"]["marker"])
        self.assertEqual("scripts/door.py", routes["learning-access"]["command"])
        for name in GROUPS:
            row = next(r for r in data["inventory"] if r["id"] == name)
            self.assertEqual("learning-access", row["routed"])
            self.assertEqual("pending", row["disposition"], "no installed end to end run exists, so not consumer")
            self.assertEqual("NO-DATA", row["installed_execution"])

    def test_a_consumer_cannot_reach_any_learning_row_before_admission(self):
        view = door.consumer_admission()
        for cap in IDS:
            self.assertFalse(door.capability_admitted(cap, view), cap)
        for cap in sorted(IDS):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = door.main(["--capability", cap])
            self.assertEqual(2, code, cap)
            self.assertNotIn("COMPLETE", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            door.main(["--available"])
        self.assertNotIn("learning.", out.getvalue())
        with open(door.REACH_REGISTRY, encoding="utf-8") as fh:
            route = next(c for c in json.load(fh)["capabilities"] if c["id"] == "learning-access")
        for ask in [route["ask"]] + list(route["paraphrases"]):  # the unadmitted route is hidden, not named
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = door.main(["--route", ask])
            self.assertEqual(2, code, ask)
            self.assertTrue(out.getvalue().startswith("NO-DATA"), out.getvalue())
            self.assertNotIn("learning", out.getvalue())


class MainRoutesLearningIds(unittest.TestCase):
    """door.main sends learning.* ids to the learning route (admission is stubbed open: this pins the routing only,
    and the hidden test above pins the admission)."""

    def test_a_learning_id_reaches_run_learning_capability_and_no_other_runner(self):
        done = {"state": "COMPLETE", "verdict": "PASS"}
        with mock.patch.object(door, "capability_admitted", return_value=True), \
                mock.patch.object(door, "run_learning_capability", return_value=done) as learning, \
                mock.patch.object(door, "run_vault_capability", return_value=done) as vault, \
                mock.patch.object(door, "run_loop_capability", return_value=done) as loop, \
                contextlib.redirect_stdout(io.StringIO()):
            code = door.main(["--capability", "learning.report"])
        self.assertEqual(0, code)
        self.assertEqual(1, learning.call_count)
        self.assertEqual(0, vault.call_count + loop.call_count)


class Authority(unittest.TestCase):
    """The adapter never widens authority: a write or approve row refuses on read alone, and starts nothing."""

    def test_capture_needs_write_and_forget_needs_approve(self):
        calls = []
        spy = lambda h, i: calls.append(h) or {"verdict": "PASS", "output": "x", "evidence_refs": ["e"]}  # noqa: E731
        typed = {"trigger": "t", "action": "a", "rule": "abcdef", "answer": "yes"}
        self.assertEqual("REFUSED", door.run_learning_capability("learning.capture", None, typed, None, spy)["state"])
        self.assertEqual("REFUSED", door.run_learning_capability("learning.forget", None, typed, ["write"], spy)["state"])
        self.assertEqual([], calls)
        self.assertEqual("COMPLETE", door.run_learning_capability("learning.capture", None, typed, ["write"], spy)["state"])
        self.assertEqual("COMPLETE", door.run_learning_capability("learning.forget", None, typed, ["approve"], spy)["state"])
        self.assertEqual(["learning_existing_capture", "learning_existing_forget"], calls)

    def test_the_plain_outcome_fills_only_the_named_input_and_every_reply_states_the_limit(self):
        seen = {}
        spy = lambda h, i: seen.update(i) or {"verdict": "PASS", "output": "x", "evidence_refs": ["e"]}  # noqa: E731
        got = door.run_learning_capability("learning.reuse", "fix the door test", {}, None, spy)
        self.assertEqual({"task": "fix the door test"}, seen)
        self.assertIn("no model was asked", got["limit"])


class Standing(unittest.TestCase):
    """bm_learning.lesson_standing and lessons_view: the one place that says what applies."""

    def test_a_candidate_is_never_established_and_never_applies(self):
        for status in BML.CANDIDATE_STATUSES + ("unheard-of", None, 7, ["approved"]):
            got = BML.lesson_standing("candidate", status)
            self.assertNotEqual("established", got["standing"], status)
            self.assertFalse(got["applies"], status)
        self.assertEqual("proposed", BML.lesson_standing("candidate", "pending")["standing"])
        self.assertEqual("proposed", BML.lesson_standing("candidate", "under_review")["standing"])

    def test_only_an_injectable_rule_applies_and_the_rest_do_not(self):
        for state in BML.RULE_STATES:
            got = BML.lesson_standing("rule", state)
            self.assertEqual(state in BML.INJECTABLE_STATES, got["applies"], state)
            self.assertEqual(state in BML.INJECTABLE_STATES, got["standing"] == "established", state)
        for bad in ("nonsense", None, 3, ["approved"]):
            self.assertEqual({"standing": "unknown", "applies": False}, BML.lesson_standing("rule", bad))
        self.assertEqual({"standing": "unknown", "applies": False}, BML.lesson_standing("memory", "approved"))

    def test_the_view_places_every_row_once_and_counts_what_it_cannot_place(self):
        rules = [{"rule_uuid": "a", "state": "approved"}, {"rule_uuid": "b", "state": "contradicted"},
                 {"rule_uuid": "c", "state": "superseded"}, {"rule_uuid": "d", "state": "forgotten"},
                 {"rule_uuid": "e", "state": "mystery"}, "not a row"]
        cands = [{"candidate_uuid": "x", "status": "pending"}, {"candidate_uuid": "y", "status": "rejected"}, 5]
        apps = [{"rule_uuid": "a", "outcome": "rework"}, {"rule_uuid": "a", "outcome": "accepted"}]
        pair = {"a": {"rule_uuid": "a"}, "b": {"rule_uuid": "z"}}
        view = BML.lessons_view(rules, cands, {"contradictions": [pair], "duplicates": []}, apps)
        self.assertEqual(["a"], [r["rule_uuid"] for r in view["established"]])
        self.assertEqual({"rework": 1}, view["established"][0]["doubt"])
        self.assertEqual(["z"], view["established"][0]["conflicts_with"])
        self.assertEqual(["b"], [r["rule_uuid"] for r in view["in_conflict"]])
        self.assertEqual(["c"], [r["rule_uuid"] for r in view["retired"]])
        self.assertEqual((1, ["x"], 3), (view["forgotten"], [c["candidate_uuid"] for c in view["proposed"]],
                                         view["unreadable"]))

    def test_a_wrong_top_level_shape_is_refused_not_read_as_empty(self):
        for args in (({}, [], {}, []), ([], [], [], []), ([], {}, {}, []), ([], [], {}, None)):
            with self.assertRaises(ValueError):
                BML.lessons_view(*args)


class Report(_Store):
    def test_an_empty_store_says_nothing_was_learned_and_is_no_data(self):
        got = self.ask("learning_existing_report")
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertIn("nothing has been learned", got["output"])
        self.assertEqual([], got["evidence_refs"])

    def test_no_store_names_the_one_setup_step_and_creates_nothing(self):
        shutil.rmtree(os.path.join(self.root, ".brothermode"))
        before = self.snapshot()
        got = self.ask("learning_existing_report")
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertIn("bm_store.py", got["output"])
        self.assertEqual(before, self.snapshot())

    def test_an_approved_lesson_is_established_with_its_source_and_a_candidate_is_only_proposed(self):
        rule = self.approve("when editing the door", "run the access contract test", "a missed test shipped a false PASS")
        cand = self.candidate("when merging", "ask the owner first")
        before = self.snapshot()
        got = self.ask("learning_existing_report")
        self.assertEqual(before, self.snapshot(), "a report is a read: it must change nothing")
        self.assertEqual("PASS", got["verdict"], got)
        self.assertTrue(got["evidence_refs"])
        established, proposed = _sections(got["output"])
        self.assertIn(rule["rule_uuid"][:8], established)
        self.assertIn("run the access contract test", established)
        self.assertIn("founder_approval", established)  # provenance: where the approval came from
        self.assertIn("owner said yes in chat", established)
        self.assertIn("project:proj", established)  # where it applies
        self.assertNotIn(cand[:8], established)
        self.assertIn(cand[:8], proposed)
        self.assertIn("ask the owner first", proposed)
        self.assertIn("1 established, 1 proposed (not approved)", got["output"])
        self.assertIn("never applied until the owner says yes", proposed)

    def test_a_candidate_alone_never_reads_as_established(self):
        cand = self.candidate("when merging", "ask the owner first")
        got = self.ask("learning_existing_report")
        self.assertEqual("PASS", got["verdict"], got)
        established, proposed = _sections(got["output"])
        self.assertIn("(none)", established)
        self.assertNotIn(cand[:8], established)
        self.assertIn(cand[:8], proposed)
        self.assertIn("0 established", got["output"])

    def test_conflicting_and_retired_lessons_are_named_and_not_offered_as_established(self):
        a = self.approve("when formatting", "use tabs everywhere", "house style")
        b = self.approve("when formatting", "use spaces everywhere", "other house style")
        old = self.approve("when logging", "print the full stack", "old habit")
        self.cli("bm_learn.py", "link", a["rule_uuid"][:8], "contradicts", b["rule_uuid"][:8])
        token = json.loads(self.cli("bm_learn.py", "grant-state-receipt", "deprecate", old["rule_uuid"][:8],
                                    "--answer", "yes retire it", "--because", "outdated", "--json"))["token"]
        self.cli("bm_learn.py", "deprecate", old["rule_uuid"][:8], "--because", "outdated", "--receipt", token)
        got = self.ask("learning_existing_report")
        self.assertEqual("PASS", got["verdict"], got)
        established, _proposed = _sections(got["output"])
        self.assertIn("CONFLICTS with: %s" % b["rule_uuid"][:8], established)
        self.assertIn("CONFLICTS with: %s" % a["rule_uuid"][:8], established)
        stale = got["output"][got["output"].index("STALE OR DISPUTED"):got["output"].index("PROPOSED (")]
        self.assertIn(old["rule_uuid"][:8], stale)
        self.assertNotIn(old["rule_uuid"][:8], established)

    def test_the_report_names_the_unplaced_rows_instead_of_dropping_them(self):
        tools = _FakeTools(self)
        tools.script({"rules:all": [json.dumps([{"rule_uuid": "r" * 32, "state": "approved", "scope_type": "global",
                                              "trigger_text": "t", "action_text": "a", "because_text": "",
                                              "current_version": 1, "founder_approved_at": "now"}, "junk"])],
                      "candidates": ["[]"], "conflicts": ['{"contradictions": [], "duplicates": []}'],
                      "applications": ["[]"], "why": ['{"evidence": [], "versions": []}']})
        got = dream_bridge.door_invoke("learning_existing_report", {}, tools=tools.dir)
        self.assertEqual("PASS", got["verdict"], got)
        self.assertIn("UNREADABLE: 1 row(s)", got["output"])
        self.assertIn("NO-DATA: no evidence row", got["output"])

    def test_a_tool_that_answers_with_the_wrong_shape_is_a_failure_not_an_empty_report(self):
        tools = _FakeTools(self)
        tools.script({"rules:all": ['{"not": "a list"}']})
        got = dream_bridge.door_invoke("learning_existing_report", {}, tools=tools.dir)
        self.assertEqual("FAIL", got["verdict"], got)


class Reuse(_Store):
    def test_an_approved_applicable_lesson_is_offered_with_why_it_matched(self):
        rule = self.approve("when editing the door", "run the access contract test")
        got = self.ask("learning_existing_reuse", task="edit the door access contract")
        self.assertEqual("PASS", got["verdict"], got)
        self.assertIn(rule["rule_uuid"][:8], got["output"])
        self.assertIn("matched access, contract, door", got["output"])
        self.assertIn("Nothing was recorded as applied", got["output"])
        self.assertTrue(got["evidence_refs"])

    def test_an_unrelated_task_gets_no_lesson(self):
        self.approve("when editing the door", "run the access contract test")
        got = self.ask("learning_existing_reuse", task="water the garden plants")
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertIn("no approved lesson applies", got["output"])

    def test_a_proposal_nobody_approved_is_never_offered_for_reuse(self):
        self.candidate("when editing the door", "run the access contract test")
        got = self.ask("learning_existing_reuse", task="edit the door access contract")
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertNotIn("run the access contract test", got["output"])

    def test_a_lesson_the_tool_returns_in_a_state_that_cannot_apply_is_withheld_not_served(self):
        tools = _FakeTools(self)
        rows = [{"rule_uuid": "f" * 32, "state": "forgotten", "scope_type": "global", "trigger_text": "t",
                 "action_text": "SECRET-FORGOTTEN-ACTION", "because_text": ""},
                {"rule_uuid": "c" * 32, "state": "pending", "scope_type": "global", "trigger_text": "t",
                 "action_text": "SECRET-CANDIDATE-ACTION", "because_text": ""}]
        tools.script({"lookup": [json.dumps({"results": rows, "conflicts": []})]})
        got = dream_bridge.door_invoke("learning_existing_reuse", {"task": "anything"}, tools=tools.dir)
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertNotIn("SECRET", got["output"])
        self.assertIn("WITHHELD ffffffff", got["output"])
        self.assertIn("WITHHELD cccccccc", got["output"])

    def test_a_dash_or_control_character_in_the_task_is_never_a_flag_or_a_line_break(self):
        self.approve("when editing the door", "run the access contract test")
        got = self.ask("learning_existing_reuse", task="--json door contract access")
        self.assertIn(got["verdict"], ("PASS", "NO-DATA"), got)  # a value, not an option: the tool did not refuse it
        for task in ("door\nforged line", "door forged", "door\x85forged", "   "):
            got = self.ask("learning_existing_reuse", task=task)
            self.assertEqual("NO-DATA", got["verdict"], (task, got))
            self.assertIn("plain text", got["output"])


class Capture(_Store):
    def test_a_capture_is_a_proposal_that_is_not_a_rule_and_is_not_reused(self):
        got = self.ask("learning_existing_capture", trigger="when releasing", action="check the changelog",
                       because="a note was missed")
        self.assertEqual("PASS", got["verdict"], got)
        self.assertIn("PROPOSAL (not approved)", got["output"])
        pending = json.loads(self.cli("bm_learn.py", "candidates", "--json"))
        self.assertEqual(["pending"], [c["status"] for c in pending])
        self.assertEqual("check the changelog", pending[0]["proposed_action"])
        self.assertEqual([], self.rules("--all"), "capture must never create a rule")
        reuse = self.ask("learning_existing_reuse", task="releasing the changelog")
        self.assertEqual("NO-DATA", reuse["verdict"], reuse)

    def test_a_value_that_starts_with_a_dash_is_stored_as_text_not_taken_as_a_flag(self):
        got = self.ask("learning_existing_capture", trigger="--yes", action="-x never rewrite history")
        self.assertEqual("PASS", got["verdict"], got)
        row = json.loads(self.cli("bm_learn.py", "candidates", "--json"))[0]
        self.assertEqual(("--yes", "-x never rewrite history"), (row["proposed_trigger"], row["proposed_action"]))

    def test_control_and_separator_characters_capture_nothing(self):
        for bad in ("two\nlines", "sep arator", "nul\x00byte", "next\x85line", "   "):
            for field in ("trigger", "action"):
                inputs = {"trigger": "t", "action": "a"}
                inputs[field] = bad
                got = dream_bridge.door_invoke("learning_existing_capture", inputs)
                self.assertEqual("NO-DATA", got["verdict"], (field, bad, got))
        self.assertEqual([], json.loads(self.cli("bm_learn.py", "candidates", "--json")))

    def test_a_capture_the_tool_reports_as_approved_is_a_failure_not_a_proposal(self):
        tools = _FakeTools(self)
        tools.script({"capture": [json.dumps({"candidate_uuid": "c" * 32, "status": "approved"})]})
        got = dream_bridge.door_invoke("learning_existing_capture", {"trigger": "t", "action": "a"}, tools=tools.dir)
        self.assertEqual("FAIL", got["verdict"], got)
        self.assertEqual([], got["evidence_refs"])
        self.assertNotIn("PROPOSAL", got["output"])


class Forget(_Store):
    def forget(self, rule, answer="yes, forget it", because="no longer true", **more):
        return self.ask("learning_existing_forget", rule=rule["rule_uuid"][:8], answer=answer, because=because, **more)

    def test_a_forgotten_lesson_stops_being_served_and_the_retained_evidence_is_reported(self):
        rule = self.approve("when editing the door", "run the access contract test")
        served = self.ask("learning_existing_reuse", task="edit the door access contract")
        self.assertEqual("PASS", served["verdict"], served)
        got = self.forget(rule)
        self.assertEqual("PASS", got["verdict"], got)
        self.assertTrue(got["evidence_refs"])
        text = got["output"]
        self.assertIn("FORGOTTEN: lesson %s" % rule["rule_uuid"][:8], text)
        self.assertIn("it was served before", text)
        self.assertIn("Search index: none in use", text)
        self.assertIn("Kept under policy: a tombstone", text)
        self.assertIn("1 version(s), 1 evidence row(s)", text)
        self.assertIn("Not covered", text)
        self.assertIn("cannot check who typed it", text)
        # owned retrieval, read straight from the real tools afterwards, not from the door's own words
        self.assertEqual([], self.rules())
        self.assertEqual("forgotten", self.rules("--all")[0]["state"])
        look = json.loads(self.cli("bm_learn.py", "lookup", "--query", "edit the door access contract", "--json"))
        self.assertEqual([], look["results"])
        again = self.ask("learning_existing_reuse", task="edit the door access contract")
        self.assertEqual("NO-DATA", again["verdict"], again)
        report = self.ask("learning_existing_report")
        self.assertIn("1 forgotten", report["output"])
        self.assertNotIn(rule["rule_uuid"][:8], report["output"])

    def test_the_search_index_is_checked_when_one_is_in_use(self):
        rule = self.approve("when editing the door", "run the access contract test")
        with mock.patch.dict(os.environ, {"BROTHERMODE_FTS5": "1"}):
            got = self.forget(rule)
        self.assertEqual("PASS", got["verdict"], got)
        if "Search index (fts5)" not in got["output"]:  # this interpreter has no FTS5: the honest lexical line
            self.assertIn("Search index: none in use", got["output"])

    def test_the_receipt_token_never_reaches_a_command_line_and_the_owner_words_reach_the_tool(self):
        rule = self.approve("when editing the door", "run the access contract test")
        seen = []
        real = dream_bridge._tool_run

        def spy(tools, script, argv, env=None):
            code, out, err = real(tools, script, argv, env)
            seen.append((argv, env, out))
            return code, out, err

        with mock.patch.object(dream_bridge, "_tool_run", spy):
            got = self.forget(rule, answer="yes, drop that one please")
        self.assertEqual("PASS", got["verdict"], got)
        token = json.loads(next(o for a, e, o in seen if a[0] == "grant-state-receipt"))["token"]
        self.assertTrue(all(token not in " ".join(a) for a, _e, _o in seen))
        spent = next((a, e) for a, e, _o in seen if a[0] == "forget")
        self.assertEqual(token, spent[1]["BM_STATE_CHANGE_RECEIPT"])
        grant = next(a for a, _e, _o in seen if a[0] == "grant-state-receipt")
        self.assertIn("yes, drop that one please", grant)
        self.assertNotIn(token, json.dumps(got))

    def test_without_the_owners_words_nothing_is_forgotten(self):
        rule = self.approve("when editing the door", "run the access contract test")
        for answer in ("", "   ", "line\nbreak", "sep arator"):
            got = self.ask("learning_existing_forget", rule=rule["rule_uuid"][:8], answer=answer)
            self.assertEqual("NO-DATA", got["verdict"], (answer, got))
        self.assertEqual("approved", self.rules("--all")[0]["state"])

    def test_an_unknown_ambiguous_malformed_or_already_forgotten_id_changes_nothing(self):
        one = self.approve("when editing the door", "run the access contract test")
        two = self.approve("when logging", "print the stack")
        for ref in ("deadbeef", "NOTHEX", "-", "zz", "a", ""):
            got = self.ask("learning_existing_forget", rule=ref, answer="yes")
            self.assertEqual("NO-DATA", got["verdict"], (ref, got))
        common = os.path.commonprefix([one["rule_uuid"], two["rule_uuid"]])
        if len(common) >= 6:
            self.assertEqual("NO-DATA", self.ask("learning_existing_forget", rule=common, answer="yes")["verdict"])
        self.assertEqual(["approved", "approved"], sorted(r["state"] for r in self.rules("--all")))
        self.assertEqual("PASS", self.forget(one)["verdict"])
        second = self.forget(one)
        self.assertEqual("NO-DATA", second["verdict"], second)
        self.assertIn("forgotten already", second["output"])
        self.assertEqual("approved", next(r for r in self.rules("--all") if r["rule_uuid"] == two["rule_uuid"])["state"])

    def test_a_short_or_ambiguous_id_asks_the_store_nothing_destructive(self):
        """Scripted: a real store rarely gives two rules one prefix, and 1 to 5 digits is how a stray hit is made."""
        one = {"rule_uuid": "a" * 32, "state": "approved", "scope_type": "global", "trigger_text": "t",
               "action_text": "a"}
        two = dict(one, rule_uuid="a" * 31 + "b")
        for rules, ref in (([one], "aaaa"), ([one], "a"), ([one, two], "a" * 8), ([], "a" * 8)):
            tools = _FakeTools(self)
            tools.script({"rules:all": [json.dumps(rules)]})
            got = dream_bridge.door_invoke("learning_existing_forget", {"rule": ref, "answer": "yes"}, tools=tools.dir)
            self.assertEqual("NO-DATA", got["verdict"], (ref, got))
            read = [c["argv"][0] for c in tools.calls()]
            self.assertLessEqual(read, ["rules"], "only the list may be read: %s" % read)

    def test_a_candidate_cannot_be_forgotten_as_a_lesson(self):
        cand = self.candidate("when merging", "ask first")
        got = self.ask("learning_existing_forget", rule=cand[:8], answer="yes")
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertEqual([("pending", cand)], [(c["status"], c["candidate_uuid"])
                                               for c in json.loads(self.cli("bm_learn.py", "candidates", "--json"))])

    def _scripted(self, **overrides):
        rule = {"rule_uuid": "a" * 32, "state": "approved", "scope_type": "global", "trigger_text": "when t",
                "action_text": "do a", "because_text": "", "current_version": 1}
        tomb = dict(rule, state="forgotten", forgotten_at="2026-11-02T00:00:00Z")
        served = json.dumps({"results": [rule], "conflicts": []})
        gone = json.dumps({"results": [], "conflicts": []})
        replies = {"rules:all": [json.dumps([rule]), json.dumps([tomb])], "rules": [json.dumps([])],
                   "lookup": [served, gone], "grant-state-receipt": [json.dumps({"token": "TOKEN-123"})],
                   "forget": ["forgotten aaaaaaaa"], "index-status": [json.dumps({"available": False})],
                   "why": [json.dumps({"versions": [{}], "evidence": [{}]})], "applications": ["[]"]}
        replies.update(overrides)
        tools = _FakeTools(self)
        tools.script(replies)
        return tools, dream_bridge.door_invoke("learning_existing_forget", {"rule": "a" * 8, "answer": "yes"},
                                               tools=tools.dir)

    def test_the_scripted_happy_path_is_a_pass_so_the_next_two_failures_are_the_guards(self):
        tools, got = self._scripted()
        self.assertEqual("PASS", got["verdict"], got)
        self.assertEqual("TOKEN-123", [c for c in tools.calls() if c["argv"][0] == "forget"][0]["receipt_env"])

    def test_a_lesson_the_store_still_serves_after_a_forget_is_a_failure_not_a_pass(self):
        served = json.dumps({"results": [{"rule_uuid": "a" * 32, "state": "approved"}], "conflicts": []})
        _tools, got = self._scripted(lookup=[served, served])
        self.assertEqual("FAIL", got["verdict"], got)
        self.assertIn("STILL served", got["output"])
        self.assertEqual([], got["evidence_refs"])

    def test_a_lesson_still_in_the_active_list_after_a_forget_is_a_failure(self):
        live = json.dumps([{"rule_uuid": "a" * 32, "state": "approved"}])
        _tools, got = self._scripted(rules=[live])
        self.assertEqual("FAIL", got["verdict"], got)
        self.assertIn("STILL served", got["output"])

    def test_a_store_that_does_not_record_the_forget_is_a_failure(self):
        rule = {"rule_uuid": "a" * 32, "state": "approved", "scope_type": "global", "trigger_text": "t",
                "action_text": "a"}
        _tools, got = self._scripted(**{"rules:all": [json.dumps([rule])]})
        self.assertEqual("FAIL", got["verdict"], got)
        self.assertIn("still reads it as 'approved'", got["output"])

    def test_a_search_index_that_still_disagrees_blocks_the_pass(self):
        drift = json.dumps({"findings": [{"code": "fts-drift", "detail": "rule %s has a row" % ("a" * 32)}]})
        tools, got = self._scripted(**{"index-status": [json.dumps({"available": True, "indexed_rows": 1, "rules": 1})],
                                       "verify": [drift]})
        self.assertEqual("FAIL", got["verdict"], got)
        self.assertIn("search index still disagrees", got["output"])

    def test_a_refused_forget_is_relayed_and_nothing_is_claimed(self):
        tools = _FakeTools(self)
        rule = {"rule_uuid": "a" * 32, "state": "approved", "scope_type": "global", "trigger_text": "t",
                "action_text": "a"}
        tools.script({"rules:all": [json.dumps([rule])], "lookup": [json.dumps({"results": [rule]})],
                      "grant-state-receipt": [json.dumps({"token": "T"})]})  # no scripted forget: the tool exits 2
        got = dream_bridge.door_invoke("learning_existing_forget", {"rule": "a" * 8, "answer": "yes"}, tools=tools.dir)
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertNotIn("FORGOTTEN", got["output"])
        self.assertEqual([], got["evidence_refs"])

    def test_through_the_doors_own_route_forget_needs_approve_and_works_with_it(self):
        rule = self.approve("when editing the door", "run the access contract test")
        typed = {"rule": rule["rule_uuid"][:8], "answer": "yes, forget it"}
        refused = door.run_learning_capability("learning.forget", None, typed, ["write"])
        self.assertEqual("REFUSED", refused["state"], refused)
        self.assertEqual("approved", self.rules("--all")[0]["state"])
        done = door.run_learning_capability("learning.forget", None, typed, ["approve"])
        self.assertEqual(("COMPLETE", "PASS"), (done["state"], done["verdict"]), done)
        self.assertTrue(done["evidence_refs"])
        self.assertEqual("forgotten", self.rules("--all")[0]["state"])


class Recurrence(_Store):
    def test_with_no_receipt_store_it_says_so_and_creates_nothing(self):
        before = self.snapshot()
        got = self.ask("learning_existing_recurrence")
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertIn("nothing was created", got["output"])
        self.assertEqual(before, self.snapshot())
        self.assertFalse(os.path.exists(os.environ["BROTHERMODE_RECURRENCE_DB"]))

    def file_unit(self, name):
        self.cli("bm_recurrence.py", "record", "--unit", name, "--surfaced", "n1", "--applied", "n1",
                 "--before-first-write", "true", "--judge", "j", "--worker", "w", "--witness", "w1")

    def test_a_few_receipts_are_no_data_and_enough_give_the_tools_own_rate_with_its_limit(self):
        for n in range(2):
            self.file_unit("u%d" % n)
        few = self.ask("learning_existing_recurrence")
        self.assertEqual("NO-DATA", few["verdict"], few)
        self.assertIn("need at least 5", few["output"])
        for n in range(2, 5):
            self.file_unit("u%d" % n)
        got = self.ask("learning_existing_recurrence")
        self.assertEqual("PASS", got["verdict"], got)
        self.assertIn("pre-action memory application rate: 100.0% (5/5", got["output"])
        self.assertIn("prevented-recurrence is NOT measured", got["output"])
        self.assertTrue(got["evidence_refs"])


@needs_recorder
class Decisions(_Env):
    """A REAL decision journal (dream_record + journal.py) in a temporary run folder."""

    def record(self, kind, options, chosen, status=None, cost=None):
        rec = dream_bridge.recorder()
        event = rec.record_decision(self.run_dir, kind, {"task": "t"}, options, chosen, "v1", cost=cost)
        self.assertTrue(event)
        if status:
            self.assertTrue(rec.record_outcome(self.run_dir, event, status, "unit-test-grader", cost=cost))
        return event

    def world(self):
        self.record("route", ["cheap", "deep"], "cheap", "PASS", 0.5)
        self.record("route", ["cheap", "deep"], "cheap", "FAIL", 0.25)
        self.record("route", ["cheap", "deep"], "deep", "PASS", 2.0)
        self.record("route", ["cheap", "deep"], "deep")  # never graded

    def test_the_decision_report_is_the_recorders_own_summary(self):
        self.world()
        before = self.snapshot()
        got = self.ask("learning_existing_decisions", run_dir=self.run_dir)
        self.assertEqual(before, self.snapshot(), "a report is a read")
        self.assertEqual("PASS", got["verdict"], got)
        self.assertIn("decisions: 4", got["output"])
        self.assertIn("graded=3 ungraded=1", got["output"])
        self.assertIn("route / cheap: 1/2 = 0.5000", got["output"])
        self.assertTrue(got["evidence_refs"])

    def test_a_folder_with_no_journal_or_no_decision_is_no_data_and_stays_empty(self):
        before = self.snapshot()
        for inputs in ({"run_dir": self.run_dir}, {"run_dir": os.path.join(self.tmp, "absent")}, {"run_dir": "-x"}):
            got = self.ask("learning_existing_decisions", **inputs)
            self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "absent")))

    def test_a_torn_or_corrupt_journal_is_named_and_never_an_empty_success(self):
        journal = os.path.join(self.run_dir, "journal.jsonl")
        with open(journal, "w", encoding="utf-8") as fh:
            fh.write('{"type": "dream.decision", "event_id": "torn\n')  # a crash mid append: no readable line
        with contextlib.redirect_stderr(io.StringIO()):
            got = self.ask("learning_existing_decisions", run_dir=self.run_dir)
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertIn("no recorded decision", got["output"])
        self.assertNotIn("decisions: 0", got["output"])
        os.remove(journal)
        self.record("route", ["cheap", "deep"], "cheap", "PASS")
        artifact = next(os.path.join(b, f) for b, _d, fs in os.walk(os.path.join(self.run_dir, "dream")) for f in fs)
        with open(artifact, "w", encoding="utf-8") as fh:
            fh.write('{"chosen": "cheap", "truncated')
        got = self.ask("learning_existing_decisions", run_dir=self.run_dir)
        self.assertEqual("PASS", got["verdict"], got)
        self.assertIn("unreadable artifacts: 1", got["output"])
        self.assertNotIn("route / cheap", got["output"])  # a corrupt record earns no pass rate
        replay = self.ask("learning_existing_replay", run_dir=self.run_dir, alternative={"route": "cheap"})
        self.assertEqual("NO-DATA", replay["verdict"], replay)
        self.assertIn("left out of the count: unreadable 1", replay["output"])

    def test_replay_gives_the_real_outcome_only_where_the_alternative_was_actually_chosen(self):
        self.world()
        got = self.ask("learning_existing_replay", run_dir=self.run_dir, alternative={"route": "cheap"})
        self.assertEqual("PASS", got["verdict"], got)
        text = got["output"]
        self.assertIn('2 decision(s) where "cheap" WAS the recorded choice: recorded outcome PASS 1, FAIL 1', text)
        self.assertIn('1 decision(s) where the run chose something else: UNSUPPORTED', text)
        self.assertIn("none is inferred", text)
        self.assertIn("left out of the count: ungraded 1", text)  # the never graded deep decision
        # the deep decision graded PASS must not leak in as cheap's outcome
        self.assertNotIn("PASS 2", text)

    def test_an_alternative_nobody_chose_is_unsupported_and_no_outcome_is_invented(self):
        self.record("route", ["cheap", "deep"], "deep", "PASS")
        self.record("route", ["cheap", "deep"], "deep", "FAIL")
        got = self.ask("learning_existing_replay", run_dir=self.run_dir, alternative={"route": "cheap"})
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertEqual([], got["evidence_refs"])
        self.assertIn("2 decision(s) where the run chose something else: UNSUPPORTED", got["output"])
        self.assertIn("no recorded outcome exists for that alternative", got["output"])
        self.assertNotRegex(got["output"], r"recorded outcome PASS|WAS the recorded choice")

    def test_an_option_that_was_never_on_offer_and_a_kind_never_recorded_are_said_not_scored(self):
        self.world()
        never = self.ask("learning_existing_replay", run_dir=self.run_dir, alternative={"route": "turbo"})
        self.assertEqual("NO-DATA", never["verdict"], never)
        self.assertIn('"turbo" was never even among the options on offer', never["output"])
        other = self.ask("learning_existing_replay", run_dir=self.run_dir, alternative={"model": "big"})
        self.assertEqual("NO-DATA", other["verdict"], other)
        self.assertIn("no graded decision of this kind was recorded", other["output"])

    def test_one_supported_kind_does_not_vouch_for_an_unsupported_one(self):
        self.world()
        got = self.ask("learning_existing_replay", run_dir=self.run_dir,
                       alternative={"route": "cheap", "model": "big"})
        self.assertEqual("PASS", got["verdict"], got)
        model = got["output"][got["output"].index("model, had"):got["output"].index("route, had")]
        self.assertNotIn("recorded outcome", model)

    def test_a_malformed_alternative_or_run_replays_nothing(self):
        self.world()
        for alt in ({}, [], "route=cheap", {"route": ""}, {"": "cheap"}, {"route": 1}, {"route": ["cheap"]}):
            got = dream_bridge.door_invoke("learning_existing_replay", {"run_dir": self.run_dir, "alternative": alt})
            self.assertEqual("NO-DATA", got["verdict"], (alt, got))
            self.assertIn("alternative must name", got["output"], alt)
        got = self.ask("learning_existing_replay", run_dir=os.path.join(self.tmp, "absent"), alternative={"a": "b"})
        self.assertEqual("NO-DATA", got["verdict"], got)

    def test_recording_a_decision_then_its_outcome_joins_them_and_the_report_reads_them_back(self):
        made = self.ask("learning_existing_decision", run_dir=self.run_dir, kind="route", options=["cheap", "deep"],
                        chosen="cheap", policy_version="v1", observed={"task": "x"})
        self.assertEqual("PASS", made["verdict"], made)
        event = re.search(r"dream decision ([0-9a-f]+) in", made["evidence_refs"][0]).group(1)
        graded = self.ask("learning_existing_outcome", run_dir=self.run_dir, decision=event, status="PASS",
                          grader="unit-test-grader")
        self.assertEqual("PASS", graded["verdict"], graded)
        self.assertIn("cannot check that a deterministic check", graded["output"])
        report = self.ask("learning_existing_decisions", run_dir=self.run_dir)
        self.assertIn("graded=1 ungraded=0", report["output"])
        replay = self.ask("learning_existing_replay", run_dir=self.run_dir, alternative={"route": "cheap"})
        self.assertEqual("PASS", replay["verdict"], replay)

    def test_an_outcome_for_an_unknown_or_already_graded_decision_writes_nothing(self):
        event = self.record("route", ["cheap", "deep"], "cheap", "PASS")
        open_one = self.record("route", ["cheap", "deep"], "deep")
        before = self.snapshot()
        again = self.ask("learning_existing_outcome", run_dir=self.run_dir, decision=event, status="FAIL",
                         grader="g")
        self.assertEqual("FAIL", again["verdict"], again)
        self.assertIn("already has a recorded outcome", again["output"])
        ghost = self.ask("learning_existing_outcome", run_dir=self.run_dir, decision="0" * 32, status="PASS",
                         grader="g")
        self.assertEqual("NO-DATA", ghost["verdict"], ghost)
        bad = self.ask("learning_existing_outcome", run_dir=self.run_dir, decision=open_one, status="MAYBE",
                       grader="g")
        self.assertEqual("NO-DATA", bad["verdict"], bad)
        nograder = self.ask("learning_existing_outcome", run_dir=self.run_dir, decision=open_one, status="PASS",
                            grader="")
        self.assertEqual("NO-DATA", nograder["verdict"], nograder)
        self.assertEqual(before, self.snapshot())

    def test_a_decision_without_its_chosen_option_or_in_a_missing_folder_is_refused_and_writes_nothing(self):
        before = self.snapshot()
        got = self.ask("learning_existing_decision", run_dir=self.run_dir, kind="route", options=["a", "b"],
                       chosen="c", policy_version="v1")
        self.assertEqual("NO-DATA", got["verdict"], got)
        gone = self.ask("learning_existing_decision", run_dir=os.path.join(self.tmp, "absent"), kind="route",
                        options=["a"], chosen="a", policy_version="v1")
        self.assertEqual("NO-DATA", gone["verdict"], gone)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "absent")))

    def test_with_no_recorder_installed_it_is_no_data_by_name(self):
        with mock.patch.dict(os.environ, {"BROTHER_DREAM_RECORD_PATH": os.path.join(self.tmp, "nope.py")}):
            dream_bridge.reset_for_tests()
            got = self.ask("learning_existing_decisions", run_dir=self.run_dir)
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertIn("dream recorder not found", got["output"])


class EntryPoint(_Store):
    """The route the door really runs: door -> dream_bridge.py door HANDLER (a subprocess) -> the lesson tools."""

    def test_the_default_route_reports_through_the_adapter_with_evidence(self):
        self.approve("when editing the door", "run the access contract test")
        got = door.run_learning_capability("learning.report")
        self.assertEqual(("COMPLETE", "PASS"), (got["state"], got["verdict"]), got)
        self.assertTrue(got["evidence_refs"])
        self.assertIn("run the access contract test", got["output"])
        self.assertIn("no model was asked", got["limit"])

    def test_the_plain_outcome_reaches_reuse_and_capture_as_typed_input(self):
        self.approve("when editing the door", "run the access contract test")
        got = door.run_learning_capability("learning.reuse", "edit the door access contract")
        self.assertEqual("COMPLETE", got["state"], got)
        made = door.run_learning_capability("learning.capture", None, {"trigger": "when x", "action": "do y"}, ["write"])
        self.assertEqual("COMPLETE", made["state"], made)

    def test_a_missing_prerequisite_starts_nothing_and_a_dead_bridge_is_no_data(self):
        got = door.run_learning_capability("learning.reuse")
        self.assertNotEqual("COMPLETE", got["state"])
        self.assertIn("task", got["missing_prerequisites"])
        with mock.patch.object(door, "learning_tool", return_value=None):
            absent = door.run_learning_capability("learning.report")
        self.assertEqual(("NO-DATA", []), (absent["verdict"], absent["evidence_refs"]), absent)
        self.assertIn("not installed beside the door", absent["output"])
        broken = door.learning_invoke("learning_existing_report", {}, tool=os.path.join(self.tmp, "absent.py"))
        self.assertNotEqual("PASS", broken["verdict"])  # a tool path that is not a file never completes

    def test_the_bridge_as_a_process_answers_one_json_line_and_an_unknown_handler_is_no_data(self):
        proc = subprocess.run([sys.executable, "-B", os.path.join(HERE, "dream_bridge.py"), "door", "nonsense"],
                              input="{}", stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                              env=dict(os.environ))
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("NO-DATA", json.loads(proc.stdout)["verdict"])
        junk = subprocess.run([sys.executable, "-B", os.path.join(HERE, "dream_bridge.py"), "door",
                               "learning_existing_report"], input="not json", stdout=subprocess.PIPE,
                              universal_newlines=True, env=dict(os.environ))
        self.assertEqual("NO-DATA", json.loads(junk.stdout)["verdict"])


if __name__ == "__main__":
    unittest.main()
