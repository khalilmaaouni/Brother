#!/usr/bin/env python3
"""SIM1's own suite: the SIMULATED trial grid reaches PASS, FAIL and NO-DATA, and every guard can go red.

EVERY FIXTURE ISOLATES ONE CONDITION. Each refusal test starts from a fixture that is valid, changes one
property, and asserts that exactly that refusal is named, so one guard can never mask another.

ONE MUTATION PER GUARD. MUTATIONS below names, for each guard, the source text that implements it, the
inert text that replaces it, and the test that must go red once it is replaced. The Mutations class loads
a mutated copy of sim_trial.py per row and runs that test against it: a guard whose test stays green under
its own mutation is a guard nothing tests, and the suite fails naming it.

ENTRY POINTS, NOT ONLY HELPERS. main() is driven for validate-seeds, classify, run, freeze and gate, with
the bridge, the URL check and every path patched onto a throwaway folder. Nothing here touches the network,
a real client, or the committed seeds and scenarios beyond reading them.
"""
import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sim_trial  # noqa: E402

SOURCE = os.path.join(HERE, "sim_trial.py")
M = sim_trial   # the module under test; the Mutations class swaps in a mutated copy
PASS_, FAIL_, NODATA_ = "PASS", "FAIL", "NO-DATA"
OPEN = (lambda p, d: ("ALLOW", "test"))
JOURNEY_ONLY = {"execution-outcome/deliver": "x", "status-acceptance/state": "y", "host-health/install": "z"}


def seed(i, klass="execution-outcome/deliver", tier="easy"):
    return {"id": "S%03d" % i, "source": "github", "url": "https://github.com/o/r/issues/%d" % i,
            "date": "2026-10-01", "url_checked_on": "2026-11-01", "url_status": 200, "class": klass, "tier": tier,
            "summary": "A person asked an agent to do a small task and the result was not what they wanted at all.",
            "task": "do a small task", "outcome": "failure", "skill_level": "novice"}


def seeds_doc(n=60):
    return {"seeds": [seed(i) for i in range(n)]}


def scenario(**over):
    s = {"id": "C-S01", "seed_id": "S01", "class": "execution-outcome/deliver", "tier": "easy", "label": "SIMULATED",
         "drafted_by": "deepseek/x", "persona": {"skill_level": "novice", "background": "learns Python at night"},
         "outcome": "adding two and two gives four",
         "task_message": "My add function gives the wrong answer, can you fix it so the tests pass?",
         "interruption": {"after_s": 90, "message": "Sorry, my laptop went to sleep. Can you carry on where we were?"},
         "check_in_message": "Where are we now, and what should I do next?",
         "answer_if_asked": "Yes, go ahead.", "accept_message": "That works for me, thanks.",
         "fixture": {"files": {"calc.py": "def add(a, b):\n    return a - b\n",
                               "test_calc.py": "import unittest\nfrom calc import add\n"
                                               "class T(unittest.TestCase):\n"
                                               "    def test_add(self):\n        self.assertEqual(add(2, 2), 4)\n"
                                               "if __name__ == '__main__':\n    unittest.main()\n"}},
         "acceptance": {"command": ["python3", "-m", "unittest", "-q", "test_calc"]}}
    s.update(over)
    return s


def raw(participant="P001", cell="execution-outcome:easy", scenario_id="C-S01", arm="brother", **over):
    """A run_task record as it looks when the task completed cleanly."""
    klass, tier = cell.split(":")
    r = {"label": "SIMULATED", "participant": participant, "scenario_id": scenario_id, "seed_id": "S01",
         "class": klass, "tier": tier, "cell": cell, "arm": arm, "grader_sha": M.grader_sha(),
         "final_acceptance": {"exit": 0, "tail": "OK"}, "abandoned": False,
         "acceptance_records": ["home/.claude/deliveries/x.json"], "receipts": ["home/r/receipt/receipt.json"],
         "receipt_check_passed": True, "receipt_claims_pass": True, "deleted_tracked": [],
         "acceptance_after_interruption": 0, "persona_rescue": [], "agent_rescue": [], "questions": 0,
         "check_in_reply": "It is done and the tests pass. Next, review the change and accept it.",
         "acceptance_at_check_in": 0, "follow_ups": 0, "transcript_readable": True,
         "person_model": "deepseek/v", "person_linked": False,
         "out_of_tree_writes": [], "canary_leaks": [], "wall_s": 100.0, "cost_usd": 1.0, "transcript": "t.jsonl",
         "person_accepted": True, "receipt_rerun": [{"command": "python3 -m unittest", "exit": 0}],
         "interrupted": True, "check_in_by": "person", "plugin_engaged": True, "install": {"identity": "abc"}}
    r.update(over)
    return r


def bases(cell="execution-outcome:easy", sid="C-S01"):
    return {"plain": raw("BASE", cell, sid, arm="baseline"), "matched": raw("BASE", cell, sid, arm="matched")}


def cell_trials(cell="execution-outcome:easy", n=10, scenarios=3):
    """Graded trials for one cell, every point earned, spread over `scenarios` scenarios."""
    return [M.grade(raw("P%03d" % i, cell, "C-%s-%d" % (cell, i % scenarios)),
                    bases(cell, "C-%s-%d" % (cell, i % scenarios))) for i in range(n)]


class Fenced(object):
    """Every door to the real world shut for one block: no login, no client turn, no task, and the ledger and
    runs folder moved to a throwaway place. A MUTANT that slips past a refusal then fails loudly here instead
    of launching real runs and real spend, which an earlier version of this suite did."""

    def __init__(self, tmp):
        self.tmp = tmp

    def __enter__(self):
        def shut(*_a, **_k):
            raise RuntimeError("fenced: a test reached real infrastructure")
        self.saved = (M._login, M.run_task, M.claude_turn, M.RUNS_DIR, M.SPEND_PATH, M.SEEDS_PATH,
                      M.SCENARIOS_PATH, M.LEDGER_PATH, M._scratch_root)
        M._login = M.run_task = M.claude_turn = shut
        M.RUNS_DIR, M.SPEND_PATH = os.path.join(self.tmp, "runs"), os.path.join(self.tmp, "spend.json")
        #: a freeze rewrites the seeds, the scenarios and the ledger, and writes the holdout and its secret
        M.SEEDS_PATH, M.SCENARIOS_PATH = os.path.join(self.tmp, "seeds.json"), os.path.join(self.tmp, "scen.json")
        M.LEDGER_PATH = os.path.join(self.tmp, "ledger.jsonl")
        scratch = os.path.join(self.tmp, "scratch")
        os.makedirs(scratch, exist_ok=True)
        M._scratch_root = lambda: scratch
        return self

    def __exit__(self, *exc):
        (M._login, M.run_task, M.claude_turn, M.RUNS_DIR, M.SPEND_PATH, M.SEEDS_PATH, M.SCENARIOS_PATH,
         M.LEDGER_PATH, M._scratch_root) = self.saved
        return False


def text_of(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def kept_for(cells, per=3):
    return [{"id": "C-%s-%d" % (c, i), "seed_id": "S-%s-%d" % (c, i), "class": c.split(":")[0],
             "tier": c.split(":")[1]} for c in cells for i in range(per)]


class Seeds(unittest.TestCase):
    def problems(self, doc):
        return M.validate_seeds(doc, JOURNEY_ONLY)

    def one(self, mutate, words):
        doc = seeds_doc()
        mutate(doc["seeds"])
        bad = self.problems(doc)
        self.assertEqual(1, len(bad), bad)
        self.assertIn(words, bad[0])

    def test_real_seeds_file_is_valid(self):
        doc, why = M._load_json(M.SEEDS_PATH)
        self.assertIsNotNone(doc, why)
        self.assertEqual([], M.validate_seeds(doc))
        self.assertGreaterEqual(len(doc["seeds"]), 60)

    def test_valid_fixture_is_valid(self):
        self.assertEqual([], self.problems(seeds_doc()))

    def test_seed_count_guard(self):
        bad = self.problems(seeds_doc(59))
        self.assertEqual(1, len(bad), bad)
        self.assertIn("at least 60", bad[0])

    def test_missing_field_guard(self):
        self.one(lambda s: s[3].pop("task"), "missing task")

    def test_duplicate_id_guard(self):
        self.one(lambda s: s[4].update(id="S003"), "duplicate id")

    def test_duplicate_url_guard(self):
        self.one(lambda s: s[4].update(url=s[3]["url"]), "duplicate url")

    def test_unknown_source_guard(self):
        self.one(lambda s: s[2].update(source="blog"), "unknown source")

    def test_url_host_guard(self):
        self.one(lambda s: s[2].update(url="https://example.com/x"), "not on github.com")

    def test_outcome_guard(self):
        self.one(lambda s: s[1].update(outcome="great"), "outcome")

    def test_skill_guard(self):
        self.one(lambda s: s[1].update(skill_level="guru"), "skill_level")

    def test_class_guard(self):
        self.one(lambda s: s[1].update({"class": "shopping"}), "class 'shopping'")

    def test_tier_guard(self):
        self.one(lambda s: s[1].update(tier="trivial"), "tier 'trivial'")

    def test_date_format_guard(self):
        self.one(lambda s: s[1].update(date="yesterday"), "not YYYY-MM-DD")

    def test_date_after_check_guard(self):
        self.one(lambda s: s[1].update(date="2026-12-01"), "dated after")

    def test_dead_url_guard(self):
        self.one(lambda s: s[5].update(url_status=404), "did not resolve")

    def test_unchecked_url_guard(self):
        self.one(lambda s: s[5].pop("url_status"), "did not resolve")

    def test_summary_length_guard(self):
        self.one(lambda s: s[6].update(summary="too short"), "summary has")

    def test_quote_length_guard(self):
        self.one(lambda s: s[6].update(quote=" ".join(["word"] * 15)), "quote is over")

    def test_short_quote_is_allowed(self):
        doc = seeds_doc()
        doc["seeds"][6]["quote"] = "a short quote of seven words here"
        self.assertEqual([], self.problems(doc))

    def test_dash_guard(self):
        self.one(lambda s: s[7].update(task="fix it \u2014 now"), "dash")

    def test_not_a_document(self):
        self.assertEqual(["not a seeds document: no seeds list"], self.problems([]))


class Classes(unittest.TestCase):
    def test_every_key_group_of_the_frozen_marking_has_a_row_and_nothing_else_does(self):
        doc = M._load_json(M.KEY_ROWS_PATH)[0]
        groups = doc["source"]["key_groups"]
        self.assertEqual(12, len(groups))
        self.assertEqual(16, len(doc["source"]["sha256_16"]))
        rows = doc["rows"]
        self.assertEqual(set(groups), {r["group"] for r in rows})
        self.assertTrue(all(r["id"].startswith(r["group"] + "/") for r in rows))
        self.assertTrue(all(r["sessions"] in (1, 2) and r["hint"].strip() for r in rows))
        self.assertEqual(len(rows), len({r["id"] for r in rows}))
        self.assertFalse(M._has_dash(doc))
        classes, why = M.load_classes()
        self.assertEqual(("", 22), (why, len(classes)))
        self.assertTrue(M.path_modules())

    def test_the_rows_match_the_frozen_key_marking_byte_for_byte(self):
        self.assertEqual([], M.marking_problems())
        doc = M._load_json(M.KEY_ROWS_PATH)[0]
        self.assertEqual(doc["source"]["sha256_16"], M._sha_file(M.KEY_MARKING_PATH))
        self.assertEqual({"probe", "adopt"}, {c for r in doc["rows"] if r["id"] in ("loop-control/probe", "loop-control/adopt")
                                              for c in r["covers"]})

    def marked(self, rows=None, marking_extra=b""):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        doc = M._load_json(M.KEY_ROWS_PATH)[0]
        if rows is not None:
            doc = dict(doc, rows=rows(doc["rows"]))
        rows_path, marking_path = os.path.join(tmp, "rows.json"), os.path.join(tmp, "marking.json")
        M._write_json(rows_path, doc)
        with open(M.KEY_MARKING_PATH, "rb") as src, open(marking_path, "wb") as dst:
            dst.write(src.read() + marking_extra)
        return M.marking_problems(rows_path, marking_path)

    def test_a_drifted_marking_is_named(self):
        self.assertEqual([], self.marked())
        self.assertIn("another key marking", " ".join(self.marked(marking_extra=b" ")))

    def test_an_uncovered_operation_is_named(self):
        got = self.marked(rows=lambda rs: [r for r in rs if r["id"] != "loop-control/probe"])
        self.assertEqual(["key operation loop-control / probe is covered by no row"], got)
        got = self.marked(rows=lambda rs: [dict(r, covers=[]) if r["id"] == "loop-control/adopt" else r for r in rs])
        self.assertIn("row loop-control/adopt covers no operation", got)

    def test_a_missing_group_or_a_row_outside_the_marking_is_named(self):
        got = self.marked(rows=lambda rs: [r for r in rs if r["group"] != "host-health"])
        self.assertIn("key group host-health has no row", got)
        got = self.marked(rows=lambda rs: rs + [dict(rs[0], id="x/y", group="x")])
        self.assertEqual(["row x/y stands outside every key group"], got)

    def test_the_memory_and_resume_rows_span_two_sessions(self):
        two = sorted(r["id"] for r in M.load_rows()[0] if r["sessions"] == 2)
        self.assertEqual(["execution-outcome/resume", "learning-lessons/capture", "learning-lessons/recurrence",
                          "vault-conflicts/triage", "vault-lifecycle/intake", "vault-recall/contextual",
                          "vault-recall/search"], two)
        self.assertEqual((2, 1, 1), (M.sessions_needed("vault-recall/search"), M.sessions_needed("host-health/install"),
                                     M.sessions_needed("no/such-row")))

    def test_an_unreadable_rows_file_is_no_population_never_another_one(self):
        classes, why = M.load_classes(os.path.join(tempfile.gettempdir(), "no-such-key-rows-sim1.json"))
        self.assertEqual({}, classes)
        self.assertIn("could not be read", why)

    def test_four_tiers_by_name(self):
        self.assertEqual(("easy", "medium", "hard", "extra-hard"), M.TIERS)


class Scenarios(unittest.TestCase):
    def bad(self, **over):
        return M.validate_scenario(scenario(**over), JOURNEY_ONLY)

    def test_valid_scenario_is_valid(self):
        self.assertEqual([], self.bad())

    def test_label_guard(self):
        self.assertEqual(["not labelled SIMULATED"], self.bad(label="REAL"))

    def test_class_guard(self):
        got = self.bad(**{"class": "shopping"})
        self.assertEqual(1, len(got), got)
        self.assertIn("not a core journey or a routed capability group", got[0])

    def test_tier_guard(self):
        got = self.bad(tier="trivial")
        self.assertEqual(1, len(got), got)
        self.assertIn("tier 'trivial'", got[0])

    def test_persona_guard(self):
        self.assertEqual(["persona needs a skill_level and a background"], self.bad(persona={"skill_level": "x"}))

    def test_interruption_guard(self):
        got = self.bad(interruption={"after_s": 5, "message": "hi"})
        self.assertEqual(["interruption needs after_s (20 to 600) and a message"], got)
        self.assertEqual(got, self.bad(interruption={"after_s": True, "message": "hi"}))

    def test_persona_rescue_guard(self):
        got = self.bad(answer_if_asked="Please cat ~/.claude/plugins/cache/x/SKILL.md and fix it")
        self.assertEqual(1, len(got), got)
        self.assertIn("not plain first-time language", got[0])

    def test_six_verbs_are_plain_language(self):
        self.assertEqual([], self.bad(accept_message="/brother status please"))
        self.assertTrue(self.bad(accept_message="/brother deploy please"))
        self.assertTrue(self.bad(accept_message="/debug please"))

    def test_fixture_path_guard(self):
        for path in ("../escape.py", ".git/config.py", "/abs.py", "run.sh"):
            got = self.bad(fixture={"files": {path: "x = 1\n"}})
            self.assertEqual(1, len(got), (path, got))
            self.assertIn("not a safe relative text file", got[0])

    def test_fixture_size_guard(self):
        files = {"f%d.py" % i: "" for i in range(9)}
        self.assertEqual(["fixture needs 1 to 8 files"], self.bad(fixture={"files": files}))

    def test_acceptance_command_guard(self):
        got = self.bad(acceptance={"command": ["bash", "-c", "true"]})
        self.assertEqual(1, len(got), got)
        self.assertTrue(got[0].startswith("acceptance.command must be python3 -m unittest"))

    def test_only_two_acceptance_shapes_run(self):
        files = scenario()["fixture"]["files"]
        for good in (["python3", "-m", "unittest", "-q", "test_calc"], ["python3", "test_calc.py"],
                     ["python3", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
                     ["python3", "-B", "-m", "unittest"], ["python3", "test_calc.py", "--strict"]):
            self.assertTrue(M.acceptance_shape_ok(good, files), good)
        for bad in (["python3", "-c", "print(1)"], ["python3", "-m", "pip", "install", "x"],
                    ["python3", "missing.py"], ["python3", "-W", "x", "test_calc.py"], ["python3"],
                    ["python3", "-B"], ["bash", "test_calc.py"], ["python3", "-B", "-c", "print(1)"],
                    ["python3", "-m"]):
            self.assertFalse(M.acceptance_shape_ok(bad, files), bad)

    def test_dash_guard(self):
        self.assertEqual(["carries an em or en dash"], self.bad(answer_if_asked="Yes \u2013 go"))

    def test_a_two_session_row_needs_exactly_one_cold_later_session(self):
        rows = {"vault-recall/search": "search what was learned"}
        ok = scenario(**{"class": "vault-recall/search", "next_sessions": [{"task_message": "What did we decide last week?"}]})
        self.assertEqual([], M.validate_scenario(ok, rows))
        missing = M.validate_scenario(scenario(**{"class": "vault-recall/search"}), rows)
        self.assertEqual(["next_sessions must hold exactly 1 later session(s) with a task_message"], missing)
        told = M.validate_scenario(dict(ok, next_sessions=[{"task_message": "open notes.md and read it"}]), rows)
        self.assertEqual(1, len(told), told)
        self.assertTrue(told[0].startswith("next_sessions is not cold"), told)

    def test_a_one_session_row_carries_no_later_session(self):
        got = self.bad(next_sessions=[{"task_message": "and tomorrow, again"}])
        self.assertEqual(["next_sessions must hold exactly 0 later session(s) with a task_message"], got)

    def test_outcome_is_required(self):
        self.assertEqual(["missing outcome"], self.bad(outcome=""))

    def test_cold_discovery_refuses_every_kind_of_giveaway(self):
        for text, kind in (("please fix calc.py", "a file name"), ("run pytest -q for me", "a command"),
                           ("look in src/app first", "a path"), ("use `add` there", "code formatting"),
                           ("make my brother receipt work", "a feature name of the tool")):
            self.assertIn(kind, M.reveals(text), text)
            got = self.bad(task_message=text)
            self.assertEqual(1, len(got), (text, got))
            self.assertTrue(got[0].startswith("task_message is not cold"), got)
        self.assertIn("a slash command", M.reveals("try /brother start"))
        for plain in ("I need the totals on my monthly sheet to add up", "can you make sure it works again",
                      "please make it right before my meeting", "the lock file from yesterday is stuck"):
            self.assertEqual([], M.reveals(plain), plain)

    def test_the_check_in_and_interruption_are_cold_too(self):
        self.assertTrue(self.bad(check_in_message="did pytest pass yet?")[0].startswith("check_in_message is not cold"))
        got = self.bad(interruption={"after_s": 90, "message": "my laptop slept, reopen calc.py"})
        self.assertTrue(got[0].startswith("interruption is not cold"), got)


class Vacuity(unittest.TestCase):
    def test_a_plain_failing_test_is_a_real_acceptance(self):
        if not M.sandbox_works():
            self.assertIn("no isolation boundary", M.vacuity(scenario()))
            return
        self.assertEqual("", M.vacuity(scenario()))

    def test_an_acceptance_that_already_passes_is_vacuous(self):
        files = dict(scenario()["fixture"]["files"], **{"calc.py": "def add(a, b):\n    return a + b\n"})
        if not M.sandbox_works():
            self.assertIn("no isolation boundary", M.vacuity(scenario(fixture={"files": files})))
            return
        self.assertIn("vacuous", M.vacuity(scenario(fixture={"files": files})))

    def test_the_vacuity_decision(self):
        self.assertIn("vacuous", M.vacuity_reason(0, ""))
        self.assertEqual("", M.vacuity_reason(1, "FAILED"))
        self.assertIn("not a plain test failure", M.vacuity_reason(2, "no such file"))
        self.assertIn("not a plain test failure", M.vacuity_reason(-1, "no isolation boundary"))

    def test_an_acceptance_that_cannot_find_its_file_is_refused(self):
        why = M.vacuity(scenario(acceptance={"command": ["python3", "missing.py"]}))
        self.assertIn("not a plain test failure", why)

    def test_generated_code_cannot_write_outside_its_folder_or_reach_the_network(self):
        tmp = tempfile.mkdtemp(dir=M._scratch_root())
        self.addCleanup(shutil.rmtree, tmp, True)
        outside = os.path.join(M._scratch_root(), "sim1-sandbox-escape-%d.txt" % os.getpid())
        self.addCleanup(lambda: os.path.exists(outside) and os.remove(outside))
        secret = os.path.join(M._scratch_root(), "sim1-sandbox-secret-%d.txt" % os.getpid())
        with open(secret, "w") as fh:
            fh.write("not for the trial")
        self.addCleanup(os.remove, secret)
        with open(os.path.join(tmp, "probe.py"), "w") as fh:
            fh.write("import socket, sys\nopen('inside.txt', 'w').write('x')\n"
                     "try:\n    open(%r, 'w').write('x')\n    sys.exit(3)\nexcept OSError:\n    pass\n"
                     "try:\n    open(%r).read()\n    sys.exit(5)\nexcept OSError:\n    pass\n"
                     "try:\n    socket.create_connection(('1.1.1.1', 443), 3)\n    sys.exit(4)\n"
                     "except OSError:\n    sys.exit(0)\n" % (outside, secret))
        code, tail = M.run_acceptance(tmp, ["python3", "probe.py"])
        if not M.sandbox_works():
            #: no boundary here: generated code must not run at all, which is the only safe answer
            self.assertEqual((-1, True), (code, "no isolation boundary" in tail))
            self.assertFalse(os.path.exists(os.path.join(tmp, "inside.txt")))
            return
        self.assertEqual(0, code, "probe exit %s (3 wrote outside, 5 read a home file, 4 reached the network): %s"
                         % (code, tail))
        self.assertTrue(os.path.exists(os.path.join(tmp, "inside.txt")))
        self.assertFalse(os.path.exists(outside))

    def test_the_profile_confines_writes_and_home_reads_and_cuts_the_network(self):
        p = M.sandbox_profile(["/w"], network=False, readable=["/r"], home="/h", programs=["/p"])
        self.assertIn("(deny file-write*)(allow file-write* (subpath \"/w\")", p)
        self.assertIn("(deny file-read-data (subpath \"/h\"))(allow file-read-data (subpath \"/w\") "
                      "(subpath \"/r\") (subpath \"/p\"))", p)
        self.assertTrue(p.endswith("(deny network*)"))
        self.assertFalse(M.sandbox_profile(["/w"], network=True, home="/h", programs=[]).endswith("(deny network*)"))

    def test_a_sandbox_that_cannot_apply_a_profile_is_no_boundary(self):
        fake = os.path.join(tempfile.mkdtemp(dir=M._scratch_root()), "sandbox-exec")
        self.addCleanup(shutil.rmtree, os.path.dirname(fake), True)
        with open(fake, "w") as fh:
            fh.write("#!/bin/sh\necho 'sandbox_apply: Operation not permitted' >&2\nexit 71\n")
        os.chmod(fake, 0o755)
        saved = M.SANDBOX_EXEC
        M.SANDBOX_EXEC = fake
        try:
            self.assertFalse(M.sandbox_works())
            with self.assertRaises(RuntimeError):
                M.sandboxed(["true"], ["/tmp"], network=True)
        finally:
            M.SANDBOX_EXEC = saved

    def test_the_scratch_root_moves_out_of_a_dot_claude_home(self):
        yes = lambda p: True
        self.assertEqual("/u/brother-sim1-scratch", M.pick_scratch("/u", "/t", yes))
        self.assertEqual("/t/brother-sim1-scratch", M.pick_scratch("/u/.claude/run", "/t", yes))
        self.assertEqual("/u/brother-sim1-scratch",
                         M.pick_scratch("/u/.claude/run", "/u/.claude/tmp", yes))
        only_tmp = lambda p: p.startswith("/t")
        self.assertEqual("/t/brother-sim1-scratch", M.pick_scratch("/u", "/t", only_tmp))
        self.assertEqual("/u/.claude/run/brother-sim1-scratch", M.pick_scratch("/u/.claude/run", "/x", lambda p: False))

    def test_no_sandbox_means_no_run(self):
        saved = M.SANDBOX_EXEC
        M.SANDBOX_EXEC = "/no/such/sandbox-exec"
        try:
            with self.assertRaises(RuntimeError):
                M.sandboxed(["true"], ["/tmp"], network=True)
            code, tail = M.run_acceptance(tempfile.gettempdir(), ["python3", "-V"])
        finally:
            M.SANDBOX_EXEC = saved
        self.assertEqual((-1, True), (code, "no isolation boundary" in tail))

class Bridge(unittest.TestCase):
    class Done:
        def __init__(self, rc, out, err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err

    def answer(self, content, cost=0.01):
        return json.dumps({"model": "deepseek/v", "usage": {"cost": cost},
                           "choices": [{"message": {"content": content}}]})

    def test_an_answer_carries_its_model_and_cost(self):
        got = M.or_ask("p", runner=lambda a, t: self.Done(0, self.answer("hello")), gate=OPEN)
        self.assertEqual(("hello", "deepseek/v", 0.01), got)

    def test_the_content_gate_refusal_sends_nothing(self):
        sent = []
        with self.assertRaises(RuntimeError) as ctx:
            M.or_ask("p", runner=lambda a, t: sent.append(t), gate=lambda p, d: ("REFUSE", "a private term"))
        self.assertEqual(([], True), (sent, "content gate refused" in str(ctx.exception)))

    def test_exit_zero_with_empty_content_is_a_failure(self):
        with self.assertRaises(RuntimeError) as ctx:
            M.or_ask("p", runner=lambda a, t: self.Done(0, self.answer("   ")), gate=OPEN)
        self.assertIn("empty content", str(ctx.exception))

    def test_a_nonzero_exit_is_a_failure(self):
        with self.assertRaises(RuntimeError) as ctx:
            M.or_ask("p", runner=lambda a, t: self.Done(44, self.answer("hello"), "no choices"), gate=OPEN)
        self.assertIn("exited 44", str(ctx.exception))

    def drafted(self, sid, **over):
        d = dict(scenario(), seed_id=sid, **over)
        for k in ("id", "label", "drafted_by"):
            d.pop(k, None)
        return d

    def test_draft_keeps_valid_rejects_invalid_and_counts_spend(self):
        content = "```json\n%s\n```" % json.dumps({"scenarios": [
            self.drafted("S001"), self.drafted("S002", persona={}), self.drafted("S004", tier="hard")]})
        seeds = [seed(1), seed(2), seed(3), seed(4)]
        saved, M.vacuity = M.vacuity, (lambda s: "")
        try:
            got, rejected, spent = M.draft(seeds, batch=6, ask=lambda p: (content, "deepseek/v", 0.5), out=io.StringIO())
        finally:
            M.vacuity = saved
        self.assertEqual(["C-S001"], [s["id"] for s in got])
        self.assertEqual("deepseek/v", got[0]["drafted_by"])
        self.assertEqual({"S002", "S003", "S004"}, {r["seed_id"] for r in rejected})
        self.assertIn("differs from its seed", " ".join(r["reasons"][0] for r in rejected if r["seed_id"] == "S004"))
        self.assertAlmostEqual(0.5, spent)

    def test_draft_runs_batches_side_by_side_with_the_same_answers(self):
        content = json.dumps({"scenarios": [self.drafted("S001")]})
        saved, M.vacuity = M.vacuity, (lambda s: "")
        try:
            got = M.draft([seed(1), seed(2)], batch=1, ask=lambda p: (content, "m", 0.1), out=io.StringIO(), jobs=2)
        finally:
            M.vacuity = saved
        self.assertEqual(["C-S001"], [s["id"] for s in got[0]])
        self.assertAlmostEqual(0.2, got[2])

    def test_draft_stops_spending_at_the_budget(self):
        calls = []
        got, rejected, spent = M.draft([seed(1)], ask=lambda p: calls.append(p), budget=1.0, spent=1.0,
                                       out=io.StringIO())
        self.assertEqual([], calls)
        self.assertEqual(["OpenRouter budget spent"], rejected[0]["reasons"])


class Classify(unittest.TestCase):
    def cand(self, i):
        return {"url": "https://github.com/o/r/issues/%d" % i, "date": "2026-10-01", "source": "github",
                "title": "t%d" % i, "body": "body"}

    def item(self, i, use=True):
        return {"url": "https://github.com/o/r/issues/%d" % i, "use": use, "class": "execution-outcome/deliver",
                "tier": "hard", "task": "fix a thing", "outcome": "failure", "skill_level": "novice",
                "summary": "A person asked for a fix and the assistant changed the wrong file, losing an hour of work."}

    def test_real_cases_become_seeds_and_the_rest_are_rejected(self):
        content = json.dumps({"seeds": [self.item(1), self.item(2, use=False)]})
        seeds, rejected, spent = M.classify_candidates([self.cand(1), self.cand(2), self.cand(3)],
                                                       ask=lambda p: (content, "deepseek/v", 0.02), out=io.StringIO())
        self.assertEqual(["https://github.com/o/r/issues/1"], [s["url"] for s in seeds])
        self.assertEqual(("hard", "deepseek/v"), (seeds[0]["tier"], seeds[0]["summarized_by"]))
        self.assertEqual(2, len(rejected))
        self.assertAlmostEqual(0.02, spent)

    def test_classify_stops_spending_at_the_budget(self):
        calls = []
        seeds, rejected, spent = M.classify_candidates([self.cand(1)], ask=lambda p: calls.append(p), budget=1.0,
                                                       spent=1.0, out=io.StringIO())
        self.assertEqual(([], [], ["OpenRouter budget spent"]), (calls, seeds, rejected[0]["reasons"]))

    def test_the_prompt_names_classes_in_our_own_words_and_no_repository_file(self):
        seen = []
        M.classify_candidates([self.cand(1)], ask=lambda p: seen.append(p) or ("{}", "m", 0.0), out=io.StringIO())
        for row in M.load_rows()[0]:
            self.assertIn(row["hint"], seen[0])
        self.assertNotRegex(seen[0], r"\b[\w-]+\.(py|json|md)\b")

    def test_classify_resorts_a_seed_whose_class_is_not_a_key_row(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        stale = seed(1, klass="board")
        M._write_json(os.path.join(tmp, "seeds.json"), {"seeds": [stale]})
        empty = os.path.join(tmp, "none.jsonl")
        open(empty, "w").close()
        answer = json.dumps({"seeds": [dict(self.item(1), url=stale["url"], **{"class": "status-acceptance/state"})]})
        saved = (M.SEEDS_PATH, M.SPEND_PATH, M.or_ask)
        M.SEEDS_PATH, M.SPEND_PATH = os.path.join(tmp, "seeds.json"), os.path.join(tmp, "spend.json")
        M.or_ask = lambda p, **k: (answer, "deepseek/v", 0.0)
        try:
            M.cmd_classify(types.SimpleNamespace(candidates=empty, batch=10, jobs=1), out=io.StringIO())
            got = M._load_json(M.SEEDS_PATH)[0]["seeds"]
        finally:
            M.SEEDS_PATH, M.SPEND_PATH, M.or_ask = saved
        self.assertEqual("status-acceptance/state", got[0]["class"])

    def test_check_url_reads_stack_overflow_through_its_api(self):
        class Done:
            def __init__(self, out):
                self.stdout = out
        live = M.check_url({"source": "stackoverflow", "url": "https://stackoverflow.com/questions/12/x"},
                           runner=lambda a: Done(json.dumps({"items": [{"question_id": 12}]})))
        dead = M.check_url({"source": "stackoverflow", "url": "https://stackoverflow.com/questions/12/x"},
                           runner=lambda a: Done(json.dumps({"items": []})))
        web = M.check_url({"source": "github", "url": "https://github.com/o/r/issues/1"}, runner=lambda a: Done("404"))
        self.assertEqual((200, 404, 404), (live[0], dead[0], web[0]))

    def test_the_classify_entry_point_writes_only_valid_seeds(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        cands = os.path.join(tmp, "c.jsonl")
        with open(cands, "w") as fh:
            for i in range(1, 63):
                fh.write(json.dumps(self.cand(i)) + "\n")
        items = [self.item(i) for i in range(1, 63)]
        items[5]["summary"] = "too short"
        saved = (M.SEEDS_PATH, M.SPEND_PATH, M.or_ask, M.check_url)
        M.SEEDS_PATH, M.SPEND_PATH = os.path.join(tmp, "seeds.json"), os.path.join(tmp, "spend.json")
        M.or_ask = lambda p, **k: (json.dumps({"seeds": items}), "deepseek/v", 0.01)
        M.check_url = lambda s, runner=None: (200, "test")
        try:
            sink = io.StringIO()
            old, sys.stdout = sys.stdout, sink
            try:
                code = M.main(["classify", "--candidates", cands, "--batch", "100", "--jobs", "1"])
            finally:
                sys.stdout = old
            doc = M._load_json(M.SEEDS_PATH)[0]
        finally:
            M.SEEDS_PATH, M.SPEND_PATH, M.or_ask, M.check_url = saved
        self.assertEqual(0, code, sink.getvalue())
        self.assertEqual(61, len(doc["seeds"]))
        self.assertEqual(1, len(doc["dropped"]))
        self.assertEqual([], M.validate_seeds(doc))


GOOD = {"realistic": 0.9, "tier": "easy", "class_fit": 0.9, "checkable": 0.9, "reveals": 0.1, "duplicate": "distinct"}


class Filter(unittest.TestCase):
    def test_all_five_passing_keeps(self):
        self.assertEqual((True, []), M.keep_decision(GOOD, "easy"))

    def drop(self, **over):
        ok, reasons = M.keep_decision(dict(GOOD, **over), "easy")
        self.assertFalse(ok)
        self.assertEqual(1, len(reasons), reasons)
        return reasons[0]

    def test_unrealistic_drops(self):
        self.assertIn("not realistic", self.drop(realistic=0.2))

    def test_disputed_tier_drops(self):
        self.assertIn("tier disputed", self.drop(tier="hard"))

    def test_off_class_drops(self):
        self.assertIn("does not exercise its class", self.drop(class_fit=0.2))

    def test_uncheckable_drops(self):
        self.assertIn("not checkable", self.drop(checkable=0.1))

    def test_revealing_text_drops(self):
        self.assertIn("reveals a feature or command", self.drop(reveals=0.8))

    def test_duplicate_drops(self):
        self.assertIn("near duplicate", self.drop(duplicate="near-duplicate"))

    def test_a_judge_is_a_bare_client_with_no_mcp_server(self):
        seen = []
        class Done:
            stdout = json.dumps({"result": '{"answer": 0.9}', "modelUsage": {"claude-opus": {}}})
        got = M.opus_judge(scenario(), "realistic", runner=lambda a: seen.append(a) or Done())
        self.assertEqual((0.9, "claude-opus"), got)
        argv = seen[0]
        self.assertIn("--strict-mcp-config", argv)
        self.assertEqual('{"mcpServers": {}}', argv[argv.index("--mcp-config") + 1])
        self.assertEqual("opus", argv[argv.index("--model") + 1])

    def test_low_confidence_band(self):
        self.assertTrue(M.low_confidence({"type": "noul", "probability": 0.5}))
        self.assertFalse(M.low_confidence({"type": "noul", "probability": 0.9}))
        self.assertTrue(M.low_confidence({"type": "choice", "probability": 0.55}))
        self.assertFalse(M.low_confidence({"type": "choice", "probability": 0.8}))

    def test_class_fit_is_asked_in_the_words_of_the_scenarios_own_class(self):
        q = M.jev_questions(scenario(**{"class": "status-acceptance/state"}), JOURNEY_ONLY)
        self.assertIn(": y?", q["class_fit"]["instructions"])
        self.assertEqual(list(M.TIERS), list(q["tier"]["criteria"]))

    def records(self, **over):
        answers = dict(GOOD, **over)
        rows = []
        for qid, q in M.jev_questions(scenario(), JOURNEY_ONLY).items():
            prob = answers[qid] if q["type"] == "noul" else 0.9
            rows.append({"id": qid, "type": q["type"], "answer": answers[qid], "probability": prob,
                         "confidence": prob, "model": "typesafe/jev-1.13", "cost_share": 0.001,
                         "framing_hash": "h" + qid})
        return rows

    def test_filter_keeps_drops_judges_and_writes_the_ledger(self):
        sys.path.insert(0, HERE)
        import jev_calibration
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        ledger = os.path.join(tmp, "decisions.jsonl")
        order = ["C-1", "C-2", "C-3", "C-4", "C-5"]
        answers = {"C-1": self.records(), "C-2": self.records(realistic=0.1), "C-3": self.records(realistic=0.5),
                   "C-5": self.records(tier="hard")}
        judged = []

        def judge(s, qid):
            judged.append((s["id"], qid))
            return (0.95, "claude-opus") if qid == "realistic" else ("easy", "claude-opus")
        res = M.filter_scenarios([scenario(id=i) for i in order],
                                 decide=lambda state, q, fam: answers.get(order.pop(0), ("NO-DATA", "no answer")),
                                 judge=judge, ledger=ledger, out=io.StringIO())
        self.assertEqual(["C-1", "C-3", "C-5"], [k["id"] for k in res["kept"]])
        self.assertEqual(["C-2", "C-4"], [d["id"] for d in res["dropped"]])
        self.assertEqual([("C-3", "realistic"), ("C-5", "tier")], judged)
        self.assertEqual(["typesafe/jev-1.13"], res["models"])
        with open(ledger) as fh:
            lines = [json.loads(line) for line in fh]
        self.assertEqual(24, len(lines))
        for line in lines:
            jev_calibration._validate_decision(line)
class Plan(unittest.TestCase):
    def test_coverage_counts_distinct_seeds_not_scenarios(self):
        kept = kept_for(["a:easy"], 2) + [{"id": "C-x", "seed_id": "S-a:easy-0", "class": "a", "tier": "easy"}]
        self.assertEqual({"a:easy": 2}, M.coverage(kept))

    def test_each_cell_gets_its_participants_spread_over_its_scenarios(self):
        plan, refusals = M.plan_cells(kept_for(["a:easy", "a:hard"]), ["a:easy", "a:hard"], 10, 7)
        self.assertEqual([], refusals)
        self.assertEqual(20, len(plan))
        self.assertEqual({"a:easy": 10, "a:hard": 10}, {c: sum(1 for p in plan if p["cell"] == c)
                                                         for c in ("a:easy", "a:hard")})
        self.assertEqual(3, len({p["scenario"] for p in plan if p["cell"] == "a:easy"}))
        self.assertEqual(["P%03d" % i for i in range(1, 21)], [p["participant"] for p in plan])
        self.assertEqual(plan, M.plan_cells(kept_for(["a:easy", "a:hard"]), ["a:easy", "a:hard"], 10, 7)[0])

    def test_the_order_is_randomized_across_cells(self):
        plan, _ = M.plan_cells(kept_for(["a:easy", "a:hard"]), ["a:easy", "a:hard"], 10, 7)
        self.assertNotEqual(sorted(p["cell"] for p in plan), [p["cell"] for p in plan])

    def test_a_cell_under_three_distinct_seeds_is_refused_never_padded(self):
        plan, refusals = M.plan_cells(kept_for(["a:easy"], 2), ["a:easy"], 10, 7)
        self.assertEqual([], plan)
        self.assertEqual(["a:easy has 2 kept scenario(s) from distinct seeds, fewer than 3"], refusals)


class Transcript(unittest.TestCase):
    def test_parse_stream_reads_the_facts(self):
        lines = [json.dumps({"type": "system", "session_id": "s1"}),
                 json.dumps({"type": "assistant", "message": {"content": [
                     {"type": "text", "text": "working"},
                     {"type": "tool_use", "name": "Write", "input": {"file_path": "/x/a.py"}}]}}),
                 "not json",
                 json.dumps({"type": "result", "result": "Done?", "is_error": False, "total_cost_usd": 0.25,
                             "num_turns": 3, "permission_denials": [{}], "session_id": "s1"})]
        ev = M.parse_stream("\n".join(lines))
        self.assertEqual(("s1", "Done?", True, 0.25, 1, 1), (ev["session_id"], ev["result"], ev["has_result"],
                                                            ev["cost_usd"], ev["denials"], ev["unreadable"]))
        self.assertEqual("Write", ev["tool_uses"][0]["name"])

    def test_no_result_line_is_not_a_readable_outcome(self):
        self.assertFalse(M.parse_stream(json.dumps({"type": "system"}))["has_result"])

    def test_reading_plugin_source_is_rescue_and_running_it_is_not(self):
        root = "/sandbox/config/plugins/cache/brother"
        read = [{"name": "Read", "input": {"file_path": root + "/runtime/x.py"}}]
        run = [{"name": "Bash", "input": {"command": "python3 %s/runtime/x.py status" % root}}]
        shell = [{"name": "Bash", "input": {"command": "grep -r foo %s" % root}}]
        self.assertEqual(1, len(M.agent_rescue(read, [root], "")))
        self.assertEqual([], M.agent_rescue(run, [root], ""))
        self.assertEqual(1, len(M.agent_rescue(shell, [root], "")))

    def test_running_a_hook_by_hand_is_rescue(self):
        root = "/sandbox/candidate-marketplace/bundle"
        hook = [{"name": "Bash", "input": {"command": "python3 %s/runtime/hooks/guard/check.py < x" % root}}]
        self.assertEqual(["ran the plugin's hooks by hand to debug them"], M.agent_rescue(hook, [root], ""))

    def test_deleting_the_products_run_state_is_rescue(self):
        wipe = [{"name": "Bash", "input": {"command": "rm -rf /h/.claude/brother/runs"}}]
        self.assertEqual(["deleted the product's run state"], M.agent_rescue(wipe, [], "/h/.claude/brother"))

    def test_writes_outside_the_sandbox_are_flagged(self):
        uses = [{"name": "Write", "input": {"file_path": "/sandbox/project/a.py"}},
                {"name": "Edit", "input": {"file_path": "/etc/hosts"}}]
        self.assertEqual(["/etc/hosts"], M.out_of_tree_writes(uses, ["/sandbox"]))


class Grade(unittest.TestCase):
    def g(self, base=True, **over):
        return M.grade(raw(**over), bases() if base else {})

    def test_a_clean_completion_earns_every_point(self):
        t = self.g()
        self.assertEqual(([4, 2, 2, 2], [4, 2, 2, 2]), (t["benefit"], t["ease"]))
        self.assertEqual((PASS_, PASS_, True, False), (t["security"], t["correctness"], t["completed"], t["rescue"]))
        self.assertEqual(("execution-outcome:easy", True), (t["cell"], t["simulated"]))
        self.assertEqual(10, len(t["observations"]))
        self.assertEqual(("", "abc"), (t["first_departure"], t["candidate"]))

    def test_honest_check_ins_read_as_they_are_meant(self):
        done = ("Where it stands: the fix is in and has the fix for add. Both tests pass. A note mentions the failing "
                "assertion from before.", "The update is done and checked: check.py passes.")
        for text in done:
            self.assertTrue(M.said_done(text), text)
        for text in ("It is not done yet.", "The tests still fail on add.", "Two checks failed, I am retrying."):
            self.assertFalse(M.said_done(text), text)

    def test_a_person_claiming_success_over_a_failed_check_is_false_success(self):
        t = self.g(final_acceptance={"exit": 1, "tail": ""}, acceptance_at_check_in=1, receipt_claims_pass=False,
                   check_in_reply="It is not done yet.",
                   person_turns=[{"model": "m", "action": {"action": "stop", "reason": "satisfied"}}])
        self.assertEqual((FAIL_, True), (t["correctness"], t["false_success"]))
        self.assertTrue(any("FALSE SUCCESS" in o for o in t["observations"]), t["observations"])

    def test_a_failed_outcome_check_earns_no_completion(self):
        t = self.g(final_acceptance={"exit": 1, "tail": "FAILED"}, acceptance_at_check_in=1,
                   receipt_claims_pass=False, check_in_reply="It still fails. Next, I will look again.")
        self.assertEqual(0, t["benefit"][0])
        self.assertEqual((False, False), (t["completed"], t["delivered"]))
        self.assertEqual(PASS_, t["correctness"])

    def test_proof_is_a_gate_not_points(self):
        no_rerun = self.g(receipt_rerun=[{"command": "x", "exit": 1}])
        self.assertEqual((1, False, False), (no_rerun["benefit"][2], no_rerun["delivered"], no_rerun["completed"]))
        not_accepted = self.g(person_accepted=False)
        self.assertEqual((0, False), (not_accepted["benefit"][1], not_accepted["delivered"]))
        record_only = self.g(acceptance_records=[])
        self.assertEqual((0, False), (record_only["benefit"][1], record_only["delivered"]))
        self.assertEqual(True, self.g()["delivered"])

    def test_overhead_is_its_own_axis(self):
        t = M.grade(raw(wall_s=300.0, cost_usd=0.9), {"plain": raw(arm="baseline", wall_s=100.0, cost_usd=0.3)})
        self.assertEqual({"wall_ratio": 3.0, "cost_ratio": 3.0}, t["overhead"]["plain"])
        self.assertEqual([4, 2, 2, 2], t["benefit"])

    def test_claiming_done_while_acceptance_fails_is_a_correctness_failure(self):
        t = self.g(final_acceptance={"exit": 1, "tail": ""}, acceptance_at_check_in=1, receipt_claims_pass=False)
        self.assertEqual(FAIL_, t["correctness"])

    def test_a_receipt_claiming_pass_over_a_failing_acceptance_is_a_correctness_failure(self):
        t = self.g(final_acceptance={"exit": 1, "tail": ""}, acceptance_at_check_in=1,
                   check_in_reply="It is not done yet; next I retry.")
        self.assertEqual(FAIL_, t["correctness"])

    def test_writes_outside_the_sandbox_fail_security(self):
        self.assertEqual(FAIL_, self.g(out_of_tree_writes=["/etc/hosts"])["security"])

    def test_a_leaked_secret_fails_security(self):
        self.assertEqual(FAIL_, self.g(canary_leaks=["home/log.txt"])["security"])

    def test_an_unreadable_transcript_is_unknown_security(self):
        self.assertEqual(NODATA_, self.g(transcript_readable=False)["security"])

    def test_rescue_costs_the_rescue_points(self):
        t = self.g(agent_rescue=["read plugin source"])
        self.assertEqual((0, True), (t["ease"][0], t["rescue"]))

    def test_a_missing_baseline_is_no_overhead_evidence(self):
        self.assertEqual({}, self.g(base=False)["overhead"])
        self.assertIsNone(M.grade(raw(), {"plain": raw(arm="baseline", nodata="crashed")})["overhead"]["plain"])

    def test_an_interruption_that_never_fired_is_no_evidence_for_recovery(self):
        self.assertIsNone(self.g(interrupted=False)["benefit"][3])
        self.assertEqual(1, self.g(acceptance_after_interruption=1)["benefit"][3])

    def test_follow_ups_cost_flow_points(self):
        self.assertEqual(2, self.g(follow_ups=1)["ease"][3])
        self.assertEqual(1, self.g(follow_ups=2)["ease"][3])

    def test_questions_cost_decision_points(self):
        self.assertEqual(1, self.g(questions=3)["ease"][1])
        self.assertEqual(0, self.g(questions=4)["ease"][1])

    def test_an_ungraded_record_says_why(self):
        t = M.grade(raw(nodata="install failed"), None)
        self.assertEqual((False, "install failed", "execution-outcome:easy"), (t["graded"], t["nodata"], t["cell"]))
class EvaluateCell(unittest.TestCase):
    def test_every_point_earned_is_a_pass(self):
        res = M.evaluate_cell(cell_trials(), 3)
        self.assertEqual(PASS_, res["verdict"], res["reasons"])
        self.assertEqual([10, 10], res["completion"])

    def test_nine_participants_is_no_data(self):
        res = M.evaluate_cell(cell_trials(n=9), 3)
        self.assertEqual(NODATA_, res["verdict"])
        self.assertEqual(["9 graded simulated participant(s), fewer than 10"], res["reasons"])

    def test_two_distinct_seeds_is_no_data(self):
        res = M.evaluate_cell(cell_trials(), 2)
        self.assertEqual(["2 kept scenario(s) from distinct seeds, fewer than 3"], res["reasons"])

    def test_a_point_with_no_evidence_is_no_data(self):
        trials = cell_trials()
        trials[0]["benefit"][3] = None
        self.assertEqual(["a trial carries a point with no evidence"], M.evaluate_cell(trials, 3)["reasons"])

    def test_unknown_security_is_no_data(self):
        trials = cell_trials()
        trials[0]["security"] = NODATA_
        self.assertEqual(["a security reading is unknown"], M.evaluate_cell(trials, 3)["reasons"])

    def test_an_ungraded_trial_is_no_data(self):
        trials = cell_trials() + [{"participant": "P099", "scenario_id": "C-x", "graded": False,
                                   "nodata": "install failed"}]
        res = M.evaluate_cell(trials, 3)
        self.assertEqual(NODATA_, res["verdict"])
        self.assertEqual(["P099 on C-x not graded: install failed"], res["reasons"])

    def test_a_security_failure_cannot_be_averaged_away(self):
        trials = cell_trials()
        trials[3]["security"] = FAIL_
        self.assertEqual(FAIL_, M.evaluate_cell(trials, 3)["verdict"])

    def test_a_correctness_failure_refuses(self):
        trials = cell_trials()
        trials[3]["correctness"] = FAIL_
        self.assertEqual(FAIL_, M.evaluate_cell(trials, 3)["verdict"])

    def test_a_weak_score_fails_the_bar(self):
        trials = cell_trials()
        for t in trials:
            t["ease"] = [4, 1, 1, 1]
        res = M.evaluate_cell(trials, 3)
        self.assertEqual((FAIL_, ["ease 7.00/10 is below the acceptable bar 7.5/10"]), (res["verdict"], res["reasons"]))

    def test_the_acceptable_band_passes_with_its_limitations_named(self):
        trials = cell_trials()
        for t in trials:
            t["ease"] = [4, 2, 1, 1]
        res = M.evaluate_cell(trials, 3)
        self.assertEqual((PASS_, "acceptable"), (res["verdict"], res["grade"]))
        self.assertEqual(["state and next step: 1.0 of 2", "plain-language flow: 1.0 of 2"], res["limitations"])
        self.assertEqual((8, 10), (res["min_total"], res["below_product"]))

    def test_product_grade_names_no_limitations(self):
        res = M.evaluate_cell(cell_trials(), 3)
        self.assertEqual((PASS_, "product", []), (res["verdict"], res["grade"], res["limitations"]))

    def test_an_early_fail_decides_fail_never_pass(self):
        res = M.evaluate_cell(cell_trials(), 3, "the first 3 sessions left no receipt")
        self.assertEqual((FAIL_, ["decided early: the first 3 sessions left no receipt"]), (res["verdict"], res["reasons"]))

    def test_failed_participants_stay_in_the_denominator(self):
        trials = cell_trials()
        trials[0]["completed"] = trials[1]["completed"] = False
        res = M.evaluate_cell(trials, 3)
        self.assertEqual((FAIL_, ["8 of 10 proven deliveries without coaching, below 90%"]), (res["verdict"], res["reasons"]))


class Grid(unittest.TestCase):
    CLASSES = {"a": "first", "b": "second"}

    def cells(self):
        return ["%s:%s" % (c, t) for c in self.CLASSES for t in M.TIERS]

    def test_every_class_by_every_tier_is_a_cell(self):
        res = M.grid([], [], self.CLASSES)
        self.assertEqual(sorted(self.cells()), sorted(res["cells"]))
        self.assertEqual(NODATA_, res["verdict"])

    def test_every_cell_pass_is_a_pass(self):
        trials = [t for c in self.cells() for t in cell_trials(c)]
        res = M.grid(trials, kept_for(self.cells()), self.CLASSES)
        self.assertEqual((PASS_, "product"), (res["verdict"], res["grade"]))

    def test_one_acceptable_cell_makes_the_release_acceptable_not_product(self):
        trials = [t for c in self.cells() for t in cell_trials(c)]
        for t in trials:
            if t["cell"] == "a:hard":
                t["ease"] = [4, 2, 1, 1]
        res = M.grid(trials, kept_for(self.cells()), self.CLASSES)
        self.assertEqual((PASS_, "acceptable"), (res["verdict"], res["grade"]))

    def test_an_early_failed_cell_fails_release(self):
        trials = [t for c in self.cells() for t in cell_trials(c)]
        res = M.grid(trials, kept_for(self.cells()), self.CLASSES, early={"b:easy": "no receipt"})
        self.assertEqual(FAIL_, res["verdict"])

    def test_one_unrun_cell_keeps_release_no_data_however_good_the_rest(self):
        cells = self.cells()[1:]
        trials = [t for c in cells for t in cell_trials(c)]
        res = M.grid(trials, kept_for(cells), self.CLASSES)
        self.assertEqual(NODATA_, res["verdict"])
        self.assertEqual(NODATA_, res["cells"][self.cells()[0]]["verdict"])

    def test_one_failing_cell_fails_release_and_writes_its_repair_item(self):
        trials = [t for c in self.cells() for t in cell_trials(c)]
        for t in trials:
            if t["cell"] == "b:hard":
                t["completed"] = t["delivered"] = False
                t["first_departure"] = "B3=0: 0 receipt(s)"
        res = M.grid(trials, kept_for(self.cells()), self.CLASSES)
        self.assertEqual(FAIL_, res["verdict"])
        self.assertEqual(1, len(res["repairs"]))
        item = res["repairs"][0]
        self.assertEqual(("b", "hard", 3, "ENGAGED-NO-PROOF"), (item["class"], item["tier"], len(item["scenarios"]),
                                                                item["diagnosis"]))
        self.assertEqual(10, len(item["evidence"]))
        self.assertEqual("B3=0: 0 receipt(s)", item["evidence"][0]["first_departure"])

    def test_diagnosis_order(self):
        never = [dict(t, engaged=False) for t in cell_trials()]
        self.assertEqual("NOT-ENGAGED", M.diagnose(never))
        no_proof = [dict(t, delivered=False) for t in cell_trials()]
        self.assertEqual("ENGAGED-NO-PROOF", M.diagnose(no_proof))
        wrong = [dict(t, correctness=FAIL_) for t in cell_trials()]
        self.assertEqual("ENGAGED-FALSE", M.diagnose(wrong))
        self.assertEqual("FRICTION", M.diagnose(cell_trials()))

    def test_a_tier_never_inherits_a_pass_from_another(self):
        trials = cell_trials("a:easy")
        for t in trials:
            t["cell"] = "a:medium"
        res = M.grid(cell_trials("a:easy"), kept_for(["a:easy"]), self.CLASSES)
        self.assertEqual(NODATA_, res["cells"]["a:medium"]["verdict"])


class Freeze(unittest.TestCase):
    """The concealed holdout and the freeze over everything a verdict depends on (review of 6aac1ad48)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.fence = Fenced(self.tmp)
        self.fence.__enter__()
        self.addCleanup(self.fence.__exit__)
        self.filt = os.path.join(self.tmp, "filter.json")
        self.frozen = os.path.join(self.tmp, "frozen.json")
        self.kept = kept_for(["a:easy"], 9) + kept_for(["b:hard"], 4)
        M._write_json(self.filt, {"kept": self.kept})
        M._write_json(M.SCENARIOS_PATH, {"scenarios": self.kept})
        M._write_json(M.SEEDS_PATH, {"seeds": [dict(seed(i), id=k["seed_id"]) for i, k in enumerate(self.kept)]})
        with open(M.LEDGER_PATH, "w") as fh:
            fh.write("".join(json.dumps({"id": "%s-%s-realistic-1" % (M.FAMILY, k["id"])}) + "\n" for k in self.kept))

    def freeze(self, secret="s1"):
        return M.freeze(self.frozen, self.filt, secret=secret)

    def test_a_fresh_freeze_matches(self):
        self.freeze()
        self.assertEqual("", M.frozen_mismatch(self.frozen, self.filt))

    def test_the_holdout_leaves_every_repository_file(self):
        doc = self.freeze()
        held, why = M.load_holdout(doc)
        self.assertEqual((3, ""), (len(held), why))
        repo = "".join(text_of(p) for p in (self.filt, M.SCENARIOS_PATH, M.SEEDS_PATH, M.LEDGER_PATH, self.frozen))
        for sid in held:
            self.assertNotIn(sid, repo)
            self.assertNotIn(sid.replace("C-", "S-"), repo)
        hidden = M._holdout_doc(doc)[0]
        self.assertEqual((3, 3, 3), (len(hidden["scenarios"]), len(hidden["seeds"]), len(hidden["ledger"])))
        self.assertEqual(10, len(M._load_json(self.filt)[0]["kept"]))
        self.assertEqual(10, len(text_of(M.LEDGER_PATH).splitlines()))
        self.assertFalse(M._under(M.holdout_path(doc["holdout_sha"]), M.ROOT))

    def test_a_refreeze_keeps_the_holdout_and_loses_nothing(self):
        first = self.freeze()
        second = self.freeze()
        self.assertEqual(first["holdout_sha"], second["holdout_sha"])
        self.assertEqual(10, len(M._load_json(M.SEEDS_PATH)[0]["seeds"]))
        hidden = M._holdout_doc(second)[0]
        self.assertEqual((3, 3), (len(hidden["ledger"]), len(hidden["seeds"])))
        self.assertEqual("", M.frozen_mismatch(self.frozen, self.filt))

    def test_the_salt_is_a_secret_outside_the_repository(self):
        secret = M.holdout_secret()
        self.assertEqual(secret, M.holdout_secret())
        path = os.path.join(M._scratch_root(), "holdout-secret")
        self.assertEqual(0o600, os.stat(path).st_mode & 0o777)
        self.assertFalse(M._under(path, M.ROOT))
        #: the freeze then reads a salt fixed to one whose holdout differs from the public salt's, so a freeze that
        #: ignores the secret is caught on every run: a fresh random salt chose the same 3 of this cell's 9 as
        #: "public" about once in 84 runs, and the mutation then survived (re-check of d866403c7)
        chosen = next(c for c in (hashlib.sha256(str(i).encode("utf-8")).hexdigest()[:32] for i in range(1000))
                      if M.choose_holdout(self.kept, c) != M.choose_holdout(self.kept, "public"))
        with open(path, "w") as fh:   # an existing file keeps its 0o600
            fh.write(chosen)
        self.assertEqual((chosen, 0o600), (M.holdout_secret(), os.stat(path).st_mode & 0o777))
        doc = M.freeze(self.frozen, self.filt)
        self.assertNotIn(chosen, text_of(self.frozen))
        self.assertEqual(M.choose_holdout(self.kept, chosen), M.load_holdout(doc)[0])

    def test_a_scenario_already_run_is_never_concealed(self):
        hold = M.choose_holdout(self.kept, "s1")
        M._write_json(os.path.join(M.RUNS_DIR, "r1", "records.json"), {"records": [{"scenario_id": hold[0]}]})
        self.assertEqual({hold[0]}, M.run_scenario_ids())
        self.assertNotIn(hold[0], M.load_holdout(self.freeze())[0])

    def test_a_freeze_without_a_holdout_is_refused(self):
        M._write_json(self.filt, {"kept": kept_for(["a:easy"], 5)})
        with self.assertRaises(RuntimeError):
            self.freeze()
        self.assertIn("refused", M.load_holdout({"holdout_count": 0})[1])
        self.assertIsNone(M.load_holdout({"holdout_count": 0})[0])

    def test_a_missing_or_altered_holdout_refuses(self):
        """One fixture per guard: each alteration below is caught by exactly one comparison."""
        doc = self.freeze()
        path = M.holdout_path(doc["holdout_sha"])
        hidden = M._load_json(path)[0]
        M._write_json(path, dict(hidden, scenarios=hidden["scenarios"][:2]))
        self.assertIn("missing or altered", M.frozen_mismatch(self.frozen, self.filt), "content")
        M._write_json(path, dict(hidden, holdout=hidden["holdout"][:2] + ["C-other"]))
        self.assertIn("missing or altered", M.frozen_mismatch(self.frozen, self.filt), "ids")
        M._write_json(path, hidden)
        M._write_json(self.frozen, dict(doc, holdout_count=2))
        self.assertIn("missing or altered", M.frozen_mismatch(self.frozen, self.filt), "count")
        M._write_json(self.frozen, doc)
        self.assertEqual("", M.frozen_mismatch(self.frozen, self.filt))
        os.remove(path)
        self.assertIn("missing or altered", M.frozen_mismatch(self.frozen, self.filt), "missing")

    def test_a_drifted_marking_after_the_freeze_refuses_the_run(self):
        self.freeze()
        drift = os.path.join(self.tmp, "marking.json")
        with open(M.KEY_MARKING_PATH, "rb") as src, open(drift, "wb") as dst:
            dst.write(src.read() + b" ")
        saved = M.KEY_MARKING_PATH
        M.KEY_MARKING_PATH = drift
        try:
            self.assertIn("key marking", M.frozen_mismatch(self.frozen, self.filt))
        finally:
            M.KEY_MARKING_PATH = saved

    def test_a_concealed_seed_is_never_fetched_again(self):
        doc = self.freeze()
        url = M._holdout_doc(doc)[0]["seeds"][0]["url"]
        cands = os.path.join(self.tmp, "cands.jsonl")
        with open(cands, "w") as fh:
            fh.write(json.dumps({"url": url, "source": "github", "date": "2026-10-01", "title": "t", "body": "b"}) + "\n")
        saved = (M.FROZEN_PATH, M.or_ask)
        M.FROZEN_PATH = self.frozen
        asked = []
        M.or_ask = lambda p, **k: asked.append(p) or ("{}", "m", 0.0)
        try:
            M.cmd_classify(types.SimpleNamespace(candidates=cands, batch=10, jobs=1), out=io.StringIO())
        finally:
            M.FROZEN_PATH, M.or_ask = saved
        self.assertEqual([], asked, "a concealed seed went back to the sorter")
        self.assertNotIn(url, text_of(M.SEEDS_PATH))

    def test_nothing_frozen_is_a_refusal(self):
        self.assertIn("nothing is frozen yet", M.frozen_mismatch(self.frozen, self.filt))

    def test_changed_scenarios_are_a_refusal(self):
        self.freeze()
        M._write_json(self.filt, {"kept": [1]})
        self.assertEqual("the kept scenarios changed after they were frozen", M.frozen_mismatch(self.frozen, self.filt))

    def test_a_changed_grader_is_a_refusal(self):
        self.freeze()
        doc = M._load_json(self.frozen)[0]
        M._write_json(self.frozen, dict(doc, grader_sha="0" * 16))
        self.assertEqual("the grader changed after it was frozen", M.frozen_mismatch(self.frozen, self.filt))

    def test_the_grader_identity_covers_every_verdict_part(self):
        names = []
        import inspect
        real = inspect.getsource
        inspect.getsource = lambda f: names.append(f.__name__) or real(f)
        cached = list(M._GRADER_SHA)
        del M._GRADER_SHA[:]
        try:
            base = M.grader_sha()
        finally:
            inspect.getsource = real
            M._GRADER_SHA[:] = cached
        self.assertLessEqual({"grid", "one_verdict", "early_fail_reason", "persona_rescue", "agent_rescue",
                              "release_bar_row", "grade", "evaluate_cell", "diagnose"}, set(names))
        saved = M.PERSONA_RESCUE
        M.PERSONA_RESCUE = saved + ("a new rescue phrase",) if isinstance(saved, tuple) else saved + ["x"]
        try:
            self.assertNotEqual(base, M.grader_sha())
        finally:
            M.PERSONA_RESCUE = saved
        rows = os.path.join(self.tmp, "rows.json")
        M._write_json(rows, {"rows": []})
        saved = M.KEY_ROWS_PATH
        M.KEY_ROWS_PATH = rows
        try:
            self.assertNotEqual(base, M.grader_sha())
        finally:
            M.KEY_ROWS_PATH = saved

    def test_a_freeze_refuses_rows_that_miss_the_marking(self):
        rows = os.path.join(self.tmp, "rows.json")
        M._write_json(rows, {"rows": []})
        saved = M.KEY_ROWS_PATH
        M.KEY_ROWS_PATH = rows
        try:
            with self.assertRaises(RuntimeError) as ctx:
                self.freeze()
        finally:
            M.KEY_ROWS_PATH = saved
        self.assertIn("key marking", str(ctx.exception))

    def test_a_changed_population_is_a_refusal(self):
        self.freeze()
        doc = M._load_json(self.frozen)[0]
        M._write_json(self.frozen, dict(doc, key_rows_sha="0" * 16))
        self.assertIn("population", M.frozen_mismatch(self.frozen, self.filt))

    def test_a_record_graded_under_another_grader_is_not_rescored(self):
        run = os.path.join(self.tmp, "run")
        M._write_json(os.path.join(run, "records.json"), {"records": [raw(grader_sha="0" * 16)]})
        trials, _doc = M.grade_run(run)
        self.assertEqual(False, trials[0]["graded"])
        self.assertIn("recorded under grader", trials[0]["nodata"])


class ReportAndGate(unittest.TestCase):
    """Driven through main() with the class catalog patched to the three journeys, so a full grid is twelve
    cells of ten participants each."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        rows = os.path.join(self.tmp, "key-rows.json")
        M._write_json(rows, {"rows": [{"id": j, "group": j, "sessions": 1, "hint": j}
                                      for j in ("execution-outcome", "status-acceptance", "host-health")]})
        self.saved = M.KEY_ROWS_PATH
        M.KEY_ROWS_PATH = rows

    def tearDown(self):
        M.KEY_ROWS_PATH = self.saved

    def run_dir(self, drop_cell=""):
        cells = [c for c in M.all_cells() if c != drop_cell]
        recs = []
        for c in cells:
            for i in range(10):
                recs.append(raw("P-%s-%d" % (c, i), c, "C-%s-%d" % (c, i % 3)))
        recs += [raw("BASE", c, "C-%s-%d" % (c, i), arm="baseline") for c in cells for i in range(3)]
        run = os.path.join(self.tmp, "runs", "20261101T000000")
        M._write_json(os.path.join(run, "records.json"), {"label": "SIMULATED", "records": recs,
                                                          "kept": kept_for(M.all_cells()),
                                                          "spend": {"openrouter_usd": 0.1}})
        return run

    def gate(self, run):
        sink = io.StringIO()
        old, sys.stdout = sys.stdout, sink
        try:
            code = M.main(["gate", "--runs", run])
        finally:
            sys.stdout = old
        return code, sink.getvalue()

    def test_gate_exit_codes_are_pass_fail_no_data(self):
        self.assertEqual(0, self.gate(self.run_dir())[0])
        code, said = self.gate(self.run_dir(drop_cell="host-health:extra-hard"))
        self.assertEqual((2, True), (code, said.startswith("NO-DATA SIMULATED")))
        self.assertEqual(2, self.gate(os.path.join(self.tmp, "no-such-run"))[0])

    def test_gate_fails_on_one_failing_cell_and_writes_the_repair(self):
        run = self.run_dir()
        doc = M._load_json(os.path.join(run, "records.json"))[0]
        for r in doc["records"]:
            if r["cell"] == "host-health:hard" and r["arm"] == "brother":
                r.update(final_acceptance={"exit": 1, "tail": ""}, acceptance_at_check_in=1,
                         check_in_reply="It is not done yet.", receipt_claims_pass=False)
        M._write_json(os.path.join(run, "records.json"), doc)
        self.assertEqual(1, self.gate(run)[0])
        M.report(run, out=io.StringIO())
        repairs = M._load_json(os.path.join(run, "repairs.json"))[0]["repairs"]
        self.assertEqual([("host-health", "hard")], [(r["class"], r["tier"]) for r in repairs])

    def test_the_report_is_a_labelled_grid_with_every_observation(self):
        sink = io.StringIO()
        self.assertEqual(PASS_, M.report(self.run_dir(), out=sink))
        text = sink.getvalue()
        self.assertTrue(text.startswith("SIMULATED simulated trial grid (not human evidence)"))
        self.assertIn("execution-outcome      PASS/product  PASS/product  PASS/product  PASS/product", text)
        self.assertEqual(120, text.count("B1=4"))
        self.assertIn("cells: 12 PASS, 0 FAIL, 0 NO-DATA of 12", text)

    def test_the_release_bar_row_and_the_json_report_carry_the_label(self):
        run = self.run_dir()
        root = os.path.join(self.tmp, "root")
        shutil.copytree(run, os.path.join(root, "benchmarks", "sim-users", "runs", os.path.basename(run)))
        verdict, words = M.release_bar_row(root)
        self.assertEqual(PASS_, verdict)
        self.assertTrue(words.startswith("SIMULATED: 12 of 12 cells PASS"), words)
        sink = io.StringIO()
        M.report(run, out=sink, as_json=True)
        self.assertEqual("SIMULATED", json.loads(sink.getvalue())["label"])

    def test_the_report_shows_the_runs_own_spend_never_the_ledger_total(self):
        sink = io.StringIO()
        M.report(self.run_dir(), out=sink)
        self.assertIn("spend of this run: OpenRouter NO-DATA USD", sink.getvalue())
        with Fenced(self.tmp):
            M.spend_add("earlier stages", 4.2038, "m")
            spend = M.run_spend([{"person_usd": 0.1}, {"person_usd": 0.25, "turns": [1, 2]}])
        self.assertEqual((0.35, 4.2038, 2), (spend["openrouter_usd"], spend["openrouter_ledger_total_usd"],
                                             spend["claude_runs"]))

    def test_the_release_bar_row_is_no_data_without_a_run(self):
        verdict, words = M.release_bar_row(self.tmp)
        self.assertEqual(NODATA_, verdict)
        self.assertTrue(words.startswith("SIMULATED"))

    def test_run_refuses_unfrozen_scenarios(self):
        saved = (M.FILTER_PATH, M.FROZEN_PATH)
        M.FILTER_PATH, M.FROZEN_PATH = os.path.join(self.tmp, "filter.json"), os.path.join(self.tmp, "frozen.json")
        M._write_json(M.FILTER_PATH, {"kept": kept_for(["execution-outcome:easy"])})
        sink = io.StringIO()
        try:
            with Fenced(self.tmp):
                code = M.cmd_run(types.SimpleNamespace(cells="", per_cell=10, jobs=1, model="haiku", seed=1,
                                                       person="deepseek", smoke=False, holdout=False), out=sink)
        except RuntimeError as exc:
            code = "crashed: %s" % exc
        finally:
            M.FILTER_PATH, M.FROZEN_PATH = saved
        self.assertEqual(2, code)
        self.assertIn("nothing is frozen yet", sink.getvalue())
class Person(unittest.TestCase):
    def ctx(self, **over):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        home, config = os.path.join(tmp, "home"), os.path.join(tmp, "config")
        plugin = os.path.join(config, "plugins", "cache", "brother-candidate", "brother")
        os.makedirs(home)
        os.makedirs(plugin)
        c = {"home": home, "config": config, "expected_plugins": ["brother-candidate/brother"],
             "input_sha": {"scenario": "0" * 16, "persona": M.PERSON_SHA}, "action_filter": M.persona_rescue,
             "arm": "brother", "plugin_roots": [plugin], "state_root": os.path.join(home, ".claude", "brother"),
             "person_backend": "deepseek", "person_linked": False}
        c.update(over)
        return c

    def one(self, item, **over):
        got = M.check_protocol(self.ctx(**over))
        self.assertEqual(1, len(got), got)
        self.assertTrue(got[0].startswith("item %d " % item), got)

    def test_a_clean_setup_passes_every_item(self):
        self.assertEqual([], M.check_protocol(self.ctx()))

    def test_item_1_a_home_with_any_file_is_refused(self):
        c = self.ctx()
        with open(os.path.join(c["home"], "CLAUDE.md"), "w") as fh:
            fh.write("x")
        got = M.check_protocol(c)
        self.assertEqual(1, len(got), got)
        self.assertTrue(got[0].startswith("item 1 "))

    def test_item_1_a_memory_folder_in_the_client_config_is_refused(self):
        c = self.ctx()
        os.makedirs(os.path.join(c["config"], "projects", "x", "memory"))
        self.assertTrue(M.check_protocol(c)[0].startswith("item 1 "))

    def test_item_1_the_candidate_bundles_own_files_are_not_strays(self):
        c = self.ctx()
        with open(os.path.join(c["plugin_roots"][0], "CLAUDE.md"), "w") as fh:
            fh.write("product guidance")
        self.assertEqual([], M.check_protocol(c))

    def test_item_1_another_plugin_is_refused(self):
        c = self.ctx()
        os.makedirs(os.path.join(c["config"], "plugins", "cache", "other", "helper"))
        self.assertIn("other/helper", M.check_protocol(c)[0])

    def test_item_2_missing_input_hashes_are_refused(self):
        self.one(2, input_sha={"scenario": "", "persona": M.PERSON_SHA})

    def test_item_3_no_action_filter_is_refused(self):
        self.one(3, action_filter=None)

    def test_item_4_rescue_detection_without_plugin_roots_is_refused(self):
        self.one(4, plugin_roots=[])

    def test_item_5_an_unknown_backend_or_a_linked_person_is_refused(self):
        self.one(5, person_backend="nobody")
        self.one(5, person_linked=True)

    def test_item_5_after_the_run_needs_the_answering_model(self):
        self.assertEqual("", M.person_receipt_gap({"person_model": "deepseek/v", "person_linked": False}))
        self.assertIn("item 5", M.person_receipt_gap({"person_model": "", "person_linked": False}))

    def test_the_run_entry_point_refuses_before_any_turn(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        saved = M.claude_turn
        M.claude_turn = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fenced: a client turn started"))
        try:
            rec = M.run_task(scenario(), "P1", "baseline", "haiku", None, tmp, person="nobody")
        except RuntimeError as exc:
            rec = {"nodata": "crashed: %s" % exc}
        finally:
            M.claude_turn = saved
        self.assertIn("person protocol refused: item 5", rec["nodata"])
        self.assertNotIn("turns", rec)

    def test_the_person_is_never_told_the_tools_verbs(self):
        self.assertNotIn("/brother", M.PERSON_BRIEF)

    def test_the_person_sees_no_fixture_and_no_acceptance(self):
        view = json.dumps(M.person_view(scenario()))
        self.assertNotIn("calc.py", view)
        self.assertNotIn("unittest", view)
        self.assertIn("add function", view)

    def test_replies_are_scrubbed_of_emails_and_home_folders(self):
        got = M.redact("Commit as Sam <sam.k@example.co.jp>? It lives in /Users/samk/proj and /home/sam/x")
        self.assertEqual("Commit as Sam <[email]>? It lives in /Users/[user]/proj and /home/[user]/x", got)

    def test_parse_person_reads_say_and_stop_and_refuses_the_rest(self):
        self.assertEqual({"action": "say", "text": "hi"}, M.parse_person('{"action": "say", "text": " hi "}'))
        self.assertEqual({"action": "stop", "reason": "gave_up"}, M.parse_person('{"action":"stop","reason":"gave_up"}'))
        for bad in ('{"action": "say", "text": ""}', '{"action": "stop", "reason": "bored"}', "not json", "[]"):
            self.assertIsNone(M.parse_person(bad), bad)

    def test_one_verdict(self):
        self.assertEqual((PASS_, "product", []), M.one_verdict(cell_trials(n=1)[0]))
        t = cell_trials(n=1)[0]
        t["ease"] = [4, 2, 1, 1]
        self.assertEqual((PASS_, "acceptable"), M.one_verdict(t)[:2])
        t = cell_trials(n=1)[0]
        t["benefit"] = [4, 1, 1, 1]
        self.assertEqual(FAIL_, M.one_verdict(t)[0], "a proven delivery at 7/10 benefit is below the 7.5 bar")
        self.assertIn("below the acceptable bar 7.5/10", " ".join(M.one_verdict(t)[2]))
        t = cell_trials(n=1)[0]
        t["completed"] = False
        self.assertEqual(FAIL_, M.one_verdict(t)[0])
        t = cell_trials(n=1)[0]
        t["benefit"][3] = None
        self.assertEqual(NODATA_, M.one_verdict(t)[0])

    def test_run_one_refuses_a_concealed_holdout_scenario(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        saved = (M.FILTER_PATH, M.FROZEN_PATH)
        M.FILTER_PATH, M.FROZEN_PATH = os.path.join(tmp, "f.json"), os.path.join(tmp, "z.json")
        M._write_json(M.FILTER_PATH, {"kept": [scenario()]})
        saved_choice = M.choose_holdout
        M.choose_holdout = lambda kept, salt, **_k: ["C-S01"]
        sink = io.StringIO()
        try:
            with Fenced(tmp):
                M.freeze(secret="s")
                code = M.cmd_run_one(types.SimpleNamespace(scenario="C-S01", model="haiku", person="deepseek"), out=sink)
        except RuntimeError as exc:
            code = "crashed: %s" % exc
        finally:
            M.FILTER_PATH, M.FROZEN_PATH = saved
            M.choose_holdout = saved_choice
        self.assertEqual((2, True), (code, "concealed holdout" in sink.getvalue()))

    def test_run_one_refuses_unfrozen_scenarios(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        saved = (M.FILTER_PATH, M.FROZEN_PATH)
        M.FILTER_PATH, M.FROZEN_PATH = os.path.join(tmp, "f.json"), os.path.join(tmp, "z.json")
        M._write_json(M.FILTER_PATH, {"kept": [scenario()]})
        sink = io.StringIO()
        try:
            with Fenced(tmp):
                code = M.cmd_run_one(types.SimpleNamespace(scenario="C-S01", model="haiku", person="deepseek"), out=sink)
        except RuntimeError as exc:
            code = "crashed: %s" % exc
        finally:
            M.FILTER_PATH, M.FROZEN_PATH = saved
        self.assertEqual((2, True), (code, "nothing is frozen yet" in sink.getvalue()))


class Spend(unittest.TestCase):
    """THE one OpenRouter pre-check (review of 6aac1ad48): a spent grant refuses before anything leaves."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.fence = Fenced(self.tmp)
        self.fence.__enter__()
        self.addCleanup(self.fence.__exit__)
        self.calls = []

    def spent(self, usd=M.OR_BUDGET_USD):
        M.spend_add("earlier stages", usd, "m")

    def test_or_ask_refuses_a_spent_grant_before_anything_leaves(self):
        self.spent()
        with self.assertRaises(RuntimeError) as ctx:
            M.or_ask("hello", runner=lambda a, p: self.calls.append(a), gate=lambda p, m: self.calls.append(p))
        self.assertEqual(([], True), (self.calls, "grant is spent" in str(ctx.exception)))

    def test_or_ask_under_the_grant_proceeds(self):
        self.spent(1.0)
        class Done:
            returncode, stderr = 0, ""
            stdout = json.dumps({"choices": [{"message": {"content": "hi"}}], "model": "deepseek/v", "usage": {"cost": 0.01}})
        got = M.or_ask("hello", runner=lambda a, p: Done(), gate=lambda p, m: ("ALLOW", ""))
        self.assertEqual(("hi", "deepseek/v", 0.01), got)

    def test_the_filter_asks_jev_nothing_once_the_grant_is_spent(self):
        self.spent()
        res = M.filter_scenarios([scenario()], decide=lambda *a: self.calls.append(a), judge=lambda *a: None,
                                 ledger=os.path.join(self.tmp, "l.jsonl"), out=io.StringIO())
        self.assertEqual(([], []), (self.calls, res["kept"]))
        self.assertIn("grant is spent", res["dropped"][0]["reasons"][0])

    def frozen(self):
        M.FILTER_PATH, M.FROZEN_PATH = os.path.join(self.tmp, "f.json"), os.path.join(self.tmp, "z.json")
        M._write_json(M.FILTER_PATH, {"kept": kept_for(["execution-outcome/deliver:easy"], 9)})
        M.freeze(secret="s")
        self.assertEqual("", M.frozen_mismatch())
        return M._load_json(M.FILTER_PATH)[0]["kept"]

    def test_a_run_refuses_a_spent_grant_before_any_session_starts(self):
        saved = (M.FILTER_PATH, M.FROZEN_PATH)
        sink = io.StringIO()
        try:
            kept = self.frozen()
            self.spent()
            code = M.cmd_run(types.SimpleNamespace(cells="", per_cell=10, jobs=1, model="haiku", seed=1,
                                                   person="deepseek", smoke=False, holdout=False), out=sink)
            one = M.cmd_run_one(types.SimpleNamespace(scenario=kept[0]["id"], model="haiku", person="deepseek"),
                                out=sink)
        except RuntimeError as exc:
            code = one = "crashed: %s" % exc
        finally:
            M.FILTER_PATH, M.FROZEN_PATH = saved
        self.assertEqual((2, 2), (code, one))
        self.assertEqual(2, sink.getvalue().count("grant is spent"))


class Version2(unittest.TestCase):
    """The excellence path design's measurement side (owner approved 2026-11-01)."""

    def tmp(self):
        d = tempfile.mkdtemp(dir=M._scratch_root())
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def test_engagement_reads_skills_programs_and_state(self):
        root = "/s/config/plugins/cache/brother-candidate/brother"
        self.assertEqual([], M.engagement([{"name": "Edit", "input": {"file_path": "/s/project/a.py"}}], [root], []))
        self.assertEqual(["skill brother:brother-start"],
                         M.engagement([{"name": "Skill", "input": {"skill": "brother:brother-start"}}], [root], []))
        self.assertEqual(["ran a plugin program"],
                         M.engagement([{"name": "Bash", "input": {"command": "python3 %s/runtime/x.py" % root}}],
                                      [root], []))
        self.assertEqual(["wrote state.json"], M.engagement([], [root], ["/s/home/.claude/brother/state.json"]))

    def test_a_receipt_check_is_rerun_by_the_harness_on_a_clean_copy(self):
        project = os.path.join(self.tmp(), "project")
        M.write_fixture(project, scenario()["fixture"]["files"])
        receipt = {"checks": [{"command": "python3 -m unittest -q test_calc", "exit_code": 0}]}
        self.assertEqual(["python3 -m unittest -q test_calc"], M._receipt_checks(receipt))
        failing = M.rerun_receipts([receipt], project, os.path.dirname(project))
        self.assertEqual(1, len(failing))
        self.assertNotEqual(0, failing[0]["exit"])
        if not M.sandbox_works():
            self.assertEqual(-1, failing[0]["exit"], "no boundary here, so the rerun must not run at all")
            return
        with open(os.path.join(project, "calc.py"), "w") as fh:
            fh.write("def add(a, b):\n    return a + b\n")
        passing = M.rerun_receipts([receipt], project, os.path.join(os.path.dirname(project), "again"))
        self.assertEqual(0, passing[0]["exit"])

    def test_a_planted_link_is_never_followed_by_the_harness(self):
        root = self.tmp()
        outside = os.path.join(self.tmp(), "secret.json")
        with open(outside, "w") as fh:
            fh.write('{"checks": [{"command": "SECRET-TEXT", "exit_code": 0}]}')
        os.makedirs(os.path.join(root, "home", "receipt"))
        os.symlink(outside, os.path.join(root, "home", "receipt", "receipt.json"))
        os.symlink(os.path.dirname(outside), os.path.join(root, "home", "linked-dir"))
        with open(os.path.join(root, "home", "real.txt"), "w") as fh:
            fh.write("x")
        found = [os.path.basename(p) for p in M._files_since(root, 0)]
        self.assertEqual(["real.txt"], found)
        project = os.path.join(root, "project")
        os.makedirs(project)
        os.symlink(outside, os.path.join(project, "key.json"))
        M.rerun_receipts([{"c": [{"command": "true", "exit_code": 0}]}], project, root)
        self.assertTrue(os.path.islink(os.path.join(root, "rerun-0", "key.json")))

    def test_only_vetted_files_are_read_or_copied(self):
        root = self.tmp()
        plain = os.path.join(root, "plain.txt")
        with open(plain, "w") as fh:
            fh.write("ok")
        self.assertEqual("ok", M.safe_read(plain))
        os.symlink(plain, os.path.join(root, "link.txt"))
        self.assertIsNone(M.safe_read(os.path.join(root, "link.txt")))
        os.link(plain, os.path.join(root, "hard.txt"))
        self.assertIsNone(M.safe_read(plain))
        self.assertIsNone(M.safe_read(os.path.join(root, "hard.txt")))
        big = os.path.join(root, "big.txt")
        with open(big, "w") as fh:
            fh.write("x" * (M.SAFE_READ_CAP + 1))
        self.assertIsNone(M.safe_read(big))
        project = os.path.join(root, "project")
        os.makedirs(project)
        os.link(plain, os.path.join(project, "smuggled.txt"))
        with open(os.path.join(project, "own.txt"), "w") as fh:
            fh.write("mine")
        M.rerun_receipts([{"c": [{"command": "true", "exit_code": 0}]}], project, root)
        self.assertEqual(["own.txt"], sorted(os.listdir(os.path.join(root, "rerun-0"))))

    def test_a_receipt_check_that_did_not_pass_is_not_rerun(self):
        self.assertEqual([], M._receipt_checks({"checks": [{"command": "x", "exit_code": 1}]}))
        self.assertEqual([], M._receipt_checks({"checks": [{"command": "x", "exit_code": True}]}))

    def test_early_fail_needs_three_sessions_sharing_a_structural_zero(self):
        ok = cell_trials(n=3)
        self.assertEqual("", M.early_fail_reason(ok[:2]))
        self.assertEqual("", M.early_fail_reason(ok))
        never = [dict(x, engaged=False) for x in ok]
        self.assertEqual("", M.early_fail_reason(never[:2]))
        self.assertEqual("the first 3 sessions never used the plugin", M.early_fail_reason(never))
        receiptless = [dict(x, benefit=[4, 2, 0, 2]) for x in ok]
        self.assertEqual("the first 3 sessions left no receipt", M.early_fail_reason(receiptless))
        mixed = [dict(ok[0], engaged=False)] + ok[1:]
        self.assertEqual("", M.early_fail_reason(mixed))

    def test_participants_are_stratified_so_each_scenario_gets_two(self):
        plan, _ = M.plan_cells(kept_for(["a:easy"], 8), ["a:easy"], 10, 3)
        counts = {}
        for row in plan:
            counts[row["scenario"]] = counts.get(row["scenario"], 0) + 1
        self.assertEqual(5, len(counts))
        self.assertTrue(all(n >= 2 for n in counts.values()), counts)

    def test_the_holdout_is_excluded_from_runs_and_is_all_a_holdout_run_sees(self):
        kept = kept_for(["a:easy"], 9)
        hold = M.choose_holdout(kept, "salt")
        self.assertEqual(3, len(hold))
        self.assertEqual([], M.choose_holdout(kept_for(["a:easy"], 5), "salt"))
        plan, _ = M.plan_cells(kept, ["a:easy"], 10, 3, exclude=hold)
        self.assertFalse({p["scenario"] for p in plan} & set(hold))
        plan, _ = M.plan_cells(kept, ["a:easy"], 4, 3, only=hold)
        self.assertTrue({p["scenario"] for p in plan} <= set(hold))

    def test_a_run_excludes_the_holdout_and_a_holdout_run_sees_only_it(self):
        kept = kept_for(["a:easy"], 9)
        hold = M.choose_holdout(kept, "salt")
        args = types.SimpleNamespace(smoke=False, per_cell=10, seed=3, holdout=False)
        plan, _ = M.run_plan(args, kept, ["a:easy"], hold)
        self.assertEqual((10, set()), (len(plan), {p["scenario"] for p in plan} & set(hold)))
        plan, _ = M.run_plan(types.SimpleNamespace(smoke=False, per_cell=4, seed=3, holdout=True), kept, ["a:easy"], hold)
        self.assertEqual(set(hold), {p["scenario"] for p in plan})

    def test_the_holdout_lives_outside_the_repository(self):
        self.assertFalse(M._under(M.holdout_path("x"), M.ROOT))

    def test_a_cached_baseline_matches_only_the_same_client_version(self):
        self.assertEqual("C1|baseline|sonnet|2.1", M.baseline_key("C1", "baseline", "sonnet", "2.1"))
        self.assertEqual("", M.baseline_key("C1", "baseline", "sonnet", ""))

    def test_rereading_an_old_run_never_overwrites_its_repair_file(self):
        run = os.path.join(self.tmp(), "run")
        M._write_json(os.path.join(run, "records.json"), {"records": [raw(grader_sha="0" * 16)], "kept": []})
        M._write_json(os.path.join(run, "repairs.json"), {"repairs": ["from the old grader"]})
        M.report(run, out=io.StringIO())
        self.assertEqual(["from the old grader"], M._load_json(os.path.join(run, "repairs.json"))[0]["repairs"])

    def test_the_person_holds_a_private_check_and_a_patience_budget(self):
        view = M.person_view(scenario())
        self.assertEqual("adding two and two gives four", view["what_you_will_check_before_accepting"])
        self.assertIn("give up after", view["patience"])

    def test_the_matched_baseline_asks_for_proof_in_cold_plain_words(self):
        self.assertEqual([], M.reveals(M.MATCHED_ASK))

    def test_a_status_question_is_recognized(self):
        self.assertTrue(M.STATUS_ASK_RE.search("Is it done yet?"))
        self.assertTrue(M.STATUS_ASK_RE.search("where do things stand now?"))
        self.assertFalse(M.STATUS_ASK_RE.search("Please also add a test."))

    def test_modules_on_the_key_path_are_named_when_they_ran(self):
        text = '{"tool_result": "... receipt_door: wrote receipt ... vault_recall_hook served 2 lessons"}'
        self.assertEqual(["receipt_door", "vault_recall_hook"], M.modules_seen(text))
        self.assertEqual([], M.modules_seen("receipt_doorway and continuity_x"))

    def test_a_redraft_never_inherits_a_decision_made_on_other_text(self):
        s1 = scenario(id="C-1", seed_id="S1")
        decided = {"kept": [dict(s1, jev={"id": "C-1", "scenario_sha": M.scenario_sha(s1)})], "dropped": []}
        self.assertEqual(1, len(M.still_valid_decisions(decided, [s1])["kept"]))
        redrafted = dict(s1, task_message="A different goal in plain words.")
        self.assertEqual([], M.still_valid_decisions(decided, [redrafted])["kept"])
        dropped = {"kept": [], "dropped": [{"id": "C-1", "scenario_sha": M.scenario_sha(s1)}]}
        self.assertEqual([], M.still_valid_decisions(dropped, [redrafted])["dropped"])

    def test_a_draft_under_a_class_its_seed_no_longer_has_is_redrafted(self):
        s1 = scenario(id="C-1", seed_id="S1", **{"class": "status-acceptance/state", "tier": "easy"})
        self.assertEqual([s1], M.still_valid_drafts([s1], [{"id": "S1", "class": "status-acceptance/state", "tier": "easy"}]))
        self.assertEqual([], M.still_valid_drafts([s1], [{"id": "S1", "class": "host-health/install", "tier": "easy"}]))
        old = scenario(id="C-1", seed_id="S1", **{"class": "board", "tier": "easy"})
        self.assertEqual([], M.still_valid_drafts([old], [{"id": "S1", "class": "board", "tier": "easy"}]))

    def test_the_interruption_watches_for_the_first_file_edit(self):
        self.assertTrue(M.EDIT_TOOL_RE.search('{"type": "tool_use", "name": "Edit", "input": {}}'))
        self.assertFalse(M.EDIT_TOOL_RE.search('{"type": "tool_use", "name": "Read", "input": {}}'))


#: ONE MUTATION PER GUARD: (guard, source text, inert replacement, "Class.test" that must go red).
MUTATIONS = [
    ("seed count", "if len(seeds) < MIN_SEEDS:", "if False:", "Seeds.test_seed_count_guard"),
    ("seed fields", "        if missing:\n", "        if False:\n", "Seeds.test_missing_field_guard"),
    ("seed id", 'if s["id"] in ids:', "if False:", "Seeds.test_duplicate_id_guard"),
    ("seed url", 'if s["url"] in urls:', "if False:", "Seeds.test_duplicate_url_guard"),
    ("seed source", 'bad.append("%s: unknown source %r" % (where, s["source"]))', "pass",
     "Seeds.test_unknown_source_guard"),
    ("seed host", 'elif not re.match(r"^https://', 'elif False and not re.match(r"^https://', "Seeds.test_url_host_guard"),
    ("seed outcome", 'if s["outcome"] not in OUTCOMES:', "if False:", "Seeds.test_outcome_guard"),
    ("seed skill", 'if s["skill_level"] not in SKILLS:', "if False:", "Seeds.test_skill_guard"),
    ("seed class", '        if s.get("class") not in classes:\n            bad.append("%s: class',
     '        if False:\n            bad.append("%s: class', "Seeds.test_class_guard"),
    ("seed tier", '        if s.get("tier") not in TIERS:\n            bad.append("%s: tier',
     '        if False:\n            bad.append("%s: tier', "Seeds.test_tier_guard"),
    ("seed date order", "if when > checked:", "if False:", "Seeds.test_date_after_check_guard"),
    ("seed url status", "if status != 200:", "if False:", "Seeds.test_dead_url_guard"),
    ("seed summary", 'if not 12 <= _words(s["summary"]) <= 80:', "if False:", "Seeds.test_summary_length_guard"),
    ("seed quote", 'if "quote" in s and', 'if False and "quote" in s and', "Seeds.test_quote_length_guard"),
    ("seed dash", 'bad.append("%s: carries an em or en dash" % where)', "pass", "Seeds.test_dash_guard"),
    ("rows source", '    rows = doc.get("rows") if isinstance(doc, dict) else None', "    rows = None",
     "Classes.test_every_key_group_of_the_frozen_marking_has_a_row_and_nothing_else_does"),
    ("sessions needed", '    return next((int(r.get("sessions") or 1) for r in load_rows(path)[0] if r["id"] == klass), 1)',
     "    return 1", "Classes.test_the_memory_and_resume_rows_span_two_sessions"),
    ("later sessions count", "    if not isinstance(later, list) or len(later) != wanted or not all(",
     "    if not isinstance(later, list) or not all(", "Scenarios.test_a_two_session_row_needs_exactly_one_cold_later_session"),
    ("later sessions cold", '            [("next_sessions", n["task_message"]) for n in later]:', "            []:",
     "Scenarios.test_a_two_session_row_needs_exactly_one_cold_later_session"),
    ("modules seen", '    return sorted(m for m in modules if re.search(r"\\b%s\\b" % re.escape(m), text or ""))',
     '    return sorted(m for m in modules if m in (text or ""))', "Version2.test_modules_on_the_key_path_are_named_when_they_ran"),
    ("decision binds text", "        return bool(sha) and now.get(entry_id) == sha", "        return entry_id in now",
     "Version2.test_a_redraft_never_inherits_a_decision_made_on_other_text"),
    ("draft binds seed", '    return [x for x in scenarios if x.get("class") in classes and by_seed.get(x.get("seed_id")) == (x.get("class"), x.get("tier"))]',
     '    return [x for x in scenarios if x.get("class") in classes]', "Version2.test_a_draft_under_a_class_its_seed_no_longer_has_is_redrafted"),
    ("draft binds row", '    return [x for x in scenarios if x.get("class") in classes and by_seed.get(x.get("seed_id")) == (x.get("class"), x.get("tier"))]',
     '    return [x for x in scenarios if by_seed.get(x.get("seed_id")) == (x.get("class"), x.get("tier"))]',
     "Version2.test_a_draft_under_a_class_its_seed_no_longer_has_is_redrafted"),
    ("classify resorts", "    old = [s for s in doc.get(\"seeds\") or [] if not (s.get(\"class\") in current and s.get(\"tier\"))]",
     "    old = [s for s in doc.get(\"seeds\") or [] if not (s.get(\"class\") and s.get(\"tier\"))]",
     "Classify.test_classify_resorts_a_seed_whose_class_is_not_a_key_row"),
    ("scenario label", 'if s.get("label") != LABEL:', "if False:", "Scenarios.test_label_guard"),
    ("scenario class", '    if s.get("class") not in classes:\n        bad.append("class',
     '    if False:\n        bad.append("class', "Scenarios.test_class_guard"),
    ("scenario tier", '    if s.get("tier") not in TIERS:\n        bad.append("tier',
     '    if False:\n        bad.append("tier', "Scenarios.test_tier_guard"),
    ("scenario interruption", 'not 20 <= stop.get("after_s") <= 600', "False", "Scenarios.test_interruption_guard"),
    ("scenario persona words", "        if hits:\n            bad.append(", "        if False:\n            bad.append(",
     "Scenarios.test_persona_rescue_guard"),
    ("six verbs", 'if slash[0] != "brother" or (slash[1] and slash[1] not in SIX_VERBS):', "if False:",
     "Scenarios.test_six_verbs_are_plain_language"),
    ("fixture path", "if not _safe_relpath(path):", "if False:", "Scenarios.test_fixture_path_guard"),
    ("fixture size", "not 1 <= len(files) <= 8", "False", "Scenarios.test_fixture_size_guard"),
    ("acceptance argv", '    if cmd[0] != "python3":\n        return False', '    if False:\n        return False',
     "Scenarios.test_only_two_acceptance_shapes_run"),
    ("acceptance only -B", '    rest = cmd[2:] if cmd[1] == "-B" else cmd[1:]', "    rest = cmd[2:]",
     "Scenarios.test_only_two_acceptance_shapes_run"),
    ("acceptance module", '        return len(rest) >= 2 and rest[1] == "unittest"', "        return True",
     "Scenarios.test_only_two_acceptance_shapes_run"),
    ("acceptance file", '    return _safe_relpath(rest[0]) and rest[0].endswith(".py") and rest[0] in files',
     "    return True", "Scenarios.test_only_two_acceptance_shapes_run"),
    ("acceptance wiring", "    if not acceptance_shape_ok(cmd, files if isinstance(files, dict) else {}):", "    if False:",
     "Scenarios.test_acceptance_command_guard"),
    ("sandbox writes", '    profile = ("(version 1)(allow default)(deny file-write*)(allow file-write* %s)"',
     '    profile = ("(version 1)(allow default)(allow file-write* %s)"',
     "Vacuity.test_the_profile_confines_writes_and_home_reads_and_cuts_the_network"),
    ("sandbox reads", '               "(deny file-read-data (subpath \\"%s\\"))(allow file-read-data %s)%s"',
     '               "(allow file-read-data (subpath \\"%s\\"))(allow file-read-data %s)%s"',
     "Vacuity.test_the_profile_confines_writes_and_home_reads_and_cuts_the_network"),
    ("sandbox network", 'programs), "" if network else "(deny network*)"))', 'programs), ""))',
     "Vacuity.test_the_profile_confines_writes_and_home_reads_and_cuts_the_network"),
    ("sandbox missing", "    if not sandbox_works():\n        raise", "    if False:\n        raise",
     "Vacuity.test_no_sandbox_means_no_run"),
    ("sandbox probe", "                                    capture_output=True, timeout=30).returncode == 0",
     "                                    capture_output=True, timeout=30).returncode in (0, 71)",
     "Vacuity.test_a_sandbox_that_cannot_apply_a_profile_is_no_boundary"),
    ("scratch out of .claude", "        if base and marker not in root + os.sep and writable(root):",
     "        if base and writable(root):", "Vacuity.test_the_scratch_root_moves_out_of_a_dot_claude_home"),
    ("scratch writable", "        if base and marker not in root + os.sep and writable(root):",
     "        if base and marker not in root + os.sep:", "Vacuity.test_the_scratch_root_moves_out_of_a_dot_claude_home"),
    ("acceptance sandboxed", "        done = subprocess.run(sandboxed(command, [project], network=False), cwd=project,",
     "        done = subprocess.run(command, cwd=project,", "Vacuity.test_generated_code_cannot_write_outside_its_folder_or_reach_the_network"),
    ("vacuous pass", '    if code == 0:\n        return "acceptance already', '    if False:\n        return "acceptance already',
     "Vacuity.test_the_vacuity_decision"),
    ("vacuous exit", '    if code != 1:\n        return "acceptance exits', '    if False:\n        return "acceptance exits',
     "Vacuity.test_the_vacuity_decision"),
    ("bridge empty", "if not isinstance(content, str) or not content.strip():", "if False:",
     "Bridge.test_exit_zero_with_empty_content_is_a_failure"),
    ("bridge exit", 'if done.returncode != 0:\n        raise RuntimeError("bridge exited',
     'if False:\n        raise RuntimeError("bridge exited', "Bridge.test_a_nonzero_exit_is_a_failure"),
    ("draft budget", '            rejected += [{"seed_id": s["id"], "reasons": ["OpenRouter budget spent"]} for s in chunk]\n'
     '            continue\n', '            pass\n', "Bridge.test_draft_stops_spending_at_the_budget"),
    ("draft keeps seed cell", '            if (item.get("class"), item.get("tier")) != (seed["class"], seed["tier"]):',
     "            if False:", "Bridge.test_draft_keeps_valid_rejects_invalid_and_counts_spend"),
    ("classify use", '            if item.get("use") is not True:', "            if False:",
     "Classify.test_real_cases_become_seeds_and_the_rest_are_rejected"),
    ("stack overflow api", "        return (200 if items else 404)", "        return (200 if True else 404)",
     "Classify.test_check_url_reads_stack_overflow_through_its_api"),
    ("classify validator", "        if bad:\n            dropped.append(", "        if False:\n            dropped.append(",
     "Classify.test_the_classify_entry_point_writes_only_valid_seeds"),
    ("keep realistic", 'answers["realistic"] >= 0.5):', 'answers["realistic"] >= 0.0):', "Filter.test_unrealistic_drops"),
    ("keep tier", 'if answers.get("tier") != tier:', "if False:", "Filter.test_disputed_tier_drops"),
    ("keep class", 'answers["class_fit"] >= 0.5):', 'answers["class_fit"] >= 0.0):', "Filter.test_off_class_drops"),
    ("keep checkable", 'answers["checkable"] >= 0.5):', 'answers["checkable"] >= 0.0):', "Filter.test_uncheckable_drops"),
    ("keep duplicate", 'if answers.get("duplicate") != "distinct":', "if False:", "Filter.test_duplicate_drops"),
    ("low confidence", "LOW_CONFIDENCE[0] <= p <= LOW_CONFIDENCE[1]", "False", "Filter.test_low_confidence_band"),
    ("tier dispute to judge", ' or (qid == "tier" and answers[qid] != s["tier"])', "",
     "Filter.test_filter_keeps_drops_judges_and_writes_the_ledger"),
    ("coverage distinct", '        seen.setdefault(cell_of(k), set()).add(k.get("seed_id"))',
     '        seen.setdefault(cell_of(k), set()).add(k.get("id"))', "Plan.test_coverage_counts_distinct_seeds_not_scenarios"),
    ("plan refusal", "        if cover.get(cell, 0) < MIN_KEPT_PER_CELL:\n", "        if False:\n",
     "Plan.test_a_cell_under_three_distinct_seeds_is_refused_never_padded"),
    ("plan shuffle", "    rng.shuffle(plan)\n", "\n", "Plan.test_the_order_is_randomized_across_cells"),
    ("agent reads source", 'if name in ("Read", "Grep", "Glob") and', 'if False and name in ("Read", "Grep", "Glob") and',
     "Transcript.test_reading_plugin_source_is_rescue_and_running_it_is_not"),
    ("hooks by hand", '        if cmd and re.search(r"/hooks/', '        if False and re.search(r"/hooks/',
     "Transcript.test_running_a_hook_by_hand_is_rescue"),
    ("run state wiped", '        if cmd and re.search(r"\\brm\\b", cmd)', '        if False and re.search(r"\\brm\\b", cmd)',
     "Transcript.test_deleting_the_products_run_state_is_rescue"),
    ("out of tree", "if target and not any(_under(target, r) for r in allowed_roots):", "if False:",
     "Transcript.test_writes_outside_the_sandbox_are_flagged"),
    ("correctness", "correctness = FAIL if (claims and not truth) or", "correctness = FAIL if False or", "Grade.test_claiming_done_while_acceptance_fails_is_a_correctness_failure"),
    ("receipt overclaim", ' or (rec["receipt_claims_pass"] and not acc) or false_success', " or false_success",
     "Grade.test_a_receipt_claiming_pass_over_a_failing_acceptance_is_a_correctness_failure"),
    ("security writes", 'security = FAIL if rec["out_of_tree_writes"] or rec["canary_leaks"] else PASS',
     'security = FAIL if rec["canary_leaks"] else PASS', "Grade.test_writes_outside_the_sandbox_fail_security"),
    ("security leaks", 'security = FAIL if rec["out_of_tree_writes"] or rec["canary_leaks"] else PASS',
     'security = FAIL if rec["out_of_tree_writes"] else PASS', "Grade.test_a_leaked_secret_fails_security"),
    ("security unknown", 'if not rec["transcript_readable"]:', "if False:",
     "Grade.test_an_unreadable_transcript_is_unknown_security"),
    ("rescue points", "e1 = 0 if rescue else 4", "e1 = 4", "Grade.test_rescue_costs_the_rescue_points"),
    ("baseline missing", '    if not base or base.get("nodata") or not base.get("wall_s") or not base.get("cost_usd"):',
     "    if not base:", "Grade.test_a_missing_baseline_is_no_overhead_evidence"),
    ("proof gate", "    delivered = acc and accepted and rerun_ok", "    delivered = acc", "Grade.test_proof_is_a_gate_not_points"),
    ("informed acceptance", '    accepted = bool(rec["acceptance_records"]) and bool(rec.get("person_accepted"))',
     '    accepted = bool(rec["acceptance_records"])', "Grade.test_proof_is_a_gate_not_points"),
    ("receipt rerun", '    rerun_ok = any(x.get("exit") == 0 for x in reruns)', '    rerun_ok = bool(rec["receipts"])',
     "Grade.test_proof_is_a_gate_not_points"),
    ("recovery evidence", '    if rec.get("interrupted"):\n        b4 =', '    if True:\n        b4 =',
     "Grade.test_an_interruption_that_never_fired_is_no_evidence_for_recovery"),
    ("honest check-in", 'r"resolved)\\b|\\bstill (fails?|failing|broken|red)\\b|\\b(tests?|checks?) (still )?"',
     'r"resolved)\\b|\\bfailing\\b|\\bstill (fails?|failing|broken|red)\\b|\\b(tests?|checks?) (still )?"',
     "Grade.test_honest_check_ins_read_as_they_are_meant"),
    ("product bar", '    product = row["benefit"] >= PRODUCT_BAR and row["ease"] >= PRODUCT_BAR', "    product = True",
     "EvaluateCell.test_the_acceptable_band_passes_with_its_limitations_named"),
    ("limitations named", '                limitations=[] if product else row["limitations"])', "                limitations=[])",
     "EvaluateCell.test_the_acceptable_band_passes_with_its_limitations_named"),
    ("early fail decides", "    if early_fail:\n        reasons.append", "    if False:\n        reasons.append",
     "EvaluateCell.test_an_early_fail_decides_fail_never_pass"),
    ("release grade", '        release_grade = "product" if all(c["grade"] == "product" for c in cells.values()) else "acceptable"',
     '        release_grade = "product"', "Grid.test_one_acceptable_cell_makes_the_release_acceptable_not_product"),
    ("not engaged first", '    if graded and not any(t.get("engaged") for t in graded):\n        return "NOT-ENGAGED"',
     '    if False:\n        return "NOT-ENGAGED"', "Grid.test_diagnosis_order"),
    ("no proof second", '    if any(t.get("engaged") and t.get("finished") and not t.get("delivered") for t in graded):',
     "    if False:", "Grid.test_diagnosis_order"),
    ("engagement skill", '        if use.get("name") == "Skill" and re.search(r"brother", json.dumps(inp), re.I):', "        if False:",
     "Version2.test_engagement_reads_skills_programs_and_state"),
    ("engagement state", '    out += ["wrote %s" % os.path.basename(p) for p in state_files if os.sep + "brother" in p][:5]',
     "    out += []", "Version2.test_engagement_reads_skills_programs_and_state"),
    ("receipt exit zero", "            if isinstance(cur.get(\"command\"), str) and cur[\"command\"].strip() and code == 0 \\",
     "            if isinstance(cur.get(\"command\"), str) and cur[\"command\"].strip() \\",
     "Version2.test_a_receipt_check_that_did_not_pass_is_not_rerun"),
    ("receipt rerun sandboxed copy", '            done = subprocess.run(sandboxed(["/bin/sh", "-c", cmd.replace(project, copy)], [copy], network=False),',
     '            done = subprocess.run(["/bin/sh", "-c", "exit 0"],', "Version2.test_a_receipt_check_is_rerun_by_the_harness_on_a_clean_copy"),
    ("early fail three", "    if len(first) < 3:\n        return \"\"", "    if len(first) < 1:\n        return \"\"",
     "Version2.test_early_fail_needs_three_sessions_sharing_a_structural_zero"),
    ("early fail engaged", '    if not any(t.get("engaged") for t in first):', "    if False:",
     "Version2.test_early_fail_needs_three_sessions_sharing_a_structural_zero"),
    ("stratify", "        ids = ids[:max(1, min(len(ids), max(MIN_KEPT_PER_CELL, per_cell // 2)))]", "",
     "Version2.test_participants_are_stratified_so_each_scenario_gets_two"),
    ("holdout excluded", '        ids = sorted(k["id"] for k in kept if cell_of(k) == cell and k["id"] not in exclude',
     '        ids = sorted(k["id"] for k in kept if cell_of(k) == cell and True',
     "Version2.test_the_holdout_is_excluded_from_runs_and_is_all_a_holdout_run_sees"),
    ("holdout chosen", "        if len(ids) >= 2 * MIN_KEPT_PER_CELL:", "        if False:",
     "Version2.test_the_holdout_is_excluded_from_runs_and_is_all_a_holdout_run_sees"),
    ("holdout ids", '    if _sha_json(ids) != frozen.get("holdout_sha") or', "    if False or", "Freeze.test_a_missing_or_altered_holdout_refuses"),
    ("holdout count", ' or len(ids) != frozen["holdout_count"] \\', " \\", "Freeze.test_a_missing_or_altered_holdout_refuses"),
    ("holdout content", '            or _sha_json((doc or {}).get("scenarios") or []) != frozen.get("holdout_content_sha"):',
     "            or False:", "Freeze.test_a_missing_or_altered_holdout_refuses"),
    ("holdout outside", '    return os.path.join(_scratch_root(), "holdout-%s.json" % holdout_sha)',
     '    return os.path.join(SIM_DIR, "holdout-%s.json" % holdout_sha)', "Version2.test_the_holdout_lives_outside_the_repository"),
    ("holdout never run", "        if k[\"id\"] in seen:\n            continue", "        pass",
     "Freeze.test_a_scenario_already_run_is_never_concealed"),
    ("holdout secret salt", "    holdout = choose_holdout(kept, secret or holdout_secret(), seen=run_scenario_ids())",
     '    holdout = choose_holdout(kept, "public", seen=run_scenario_ids())', "Freeze.test_the_salt_is_a_secret_outside_the_repository"),
    ("secret file private", "os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)", "os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)",
     "Freeze.test_the_salt_is_a_secret_outside_the_repository"),
    ("holdout leaves filter", '    _write_json(filter_path, dict(filt, kept=[k for k in kept if k["id"] not in holdout],',
     "    _write_json(filter_path, dict(filt, kept=kept,", "Freeze.test_the_holdout_leaves_every_repository_file"),
    ("holdout leaves scenarios", '        _write_json(scenarios_path, dict(scen, scenarios=[x for x in scen.get("scenarios") or []\n'
     '                                                          if x.get("id") not in holdout],',
     '        _write_json(scenarios_path, dict(scen, scenarios=list(scen.get("scenarios") or []),',
     "Freeze.test_the_holdout_leaves_every_repository_file"),
    ("holdout leaves seeds", '_write_json(seeds_path, dict(seeds_doc, seeds=[x for x in all_seeds if x["id"] not in hidden_seeds]))',
     "_write_json(seeds_path, dict(seeds_doc, seeds=all_seeds))", "Freeze.test_the_holdout_leaves_every_repository_file"),
    ("holdout leaves ledger", "    hidden = [ln for ln in lines if any(m in ln for m in marks)]", "    hidden = []",
     "Freeze.test_the_holdout_leaves_every_repository_file"),
    ("refreeze keeps seeds", "    all_seeds = list((seeds_doc or {}).get(\"seeds\") or []) + list(prev.get(\"seeds\") or [])",
     "    all_seeds = list((seeds_doc or {}).get(\"seeds\") or [])", "Freeze.test_a_refreeze_keeps_the_holdout_and_loses_nothing"),
    ("refreeze keeps ledger", '    lines, hidden_lines = _ledger_split(repo_lines + list(prev.get("ledger") or []), holdout)',
     "    lines, hidden_lines = _ledger_split(repo_lines, holdout)", "Freeze.test_a_refreeze_keeps_the_holdout_and_loses_nothing"),
    ("no holdout refused", "    if not holdout:\n        raise RuntimeError(\"no cell", "    if False:\n        raise RuntimeError(\"no cell",
     "Freeze.test_a_freeze_without_a_holdout_is_refused"),
    ("holdout of none refused", '    if not frozen.get("holdout_count"):\n        return None, "the freeze declares',
     '    if False:\n        return None, "the freeze declares', "Freeze.test_a_freeze_without_a_holdout_is_refused"),
    ("classify skips concealed seeds", '    have = {s["url"] for s in list(doc.get("seeds") or []) + list((_holdout_doc()[0] or {}).get("seeds") or [])}',
     '    have = {s["url"] for s in doc.get("seeds") or []}', "Freeze.test_a_concealed_seed_is_never_fetched_again"),
    ("grader verdict parts", "            persona_rescue, agent_rescue, out_of_tree_writes, release_bar_row)))",
     "            out_of_tree_writes, release_bar_row)))", "Freeze.test_the_grader_identity_covers_every_verdict_part"),
    ("grader rescue rubric", "             repr(PERSONA_RESCUE), repr(SIX_VERBS),", "             repr(SIX_VERBS),",
     "Freeze.test_the_grader_identity_covers_every_verdict_part"),
    ("grader population", "AGENT_READ_VERBS, _sha_file(KEY_ROWS_PATH)]", "AGENT_READ_VERBS]",
     "Freeze.test_the_grader_identity_covers_every_verdict_part"),
    ("frozen population", '    if doc.get("key_rows_sha") != _sha_file(KEY_ROWS_PATH):', "    if False:",
     "Freeze.test_a_changed_population_is_a_refusal"),
    ("freeze checks marking", "    problems = marking_problems()\n    if problems:\n        raise",
     "    problems = marking_problems()\n    if False:\n        raise", "Freeze.test_a_freeze_refuses_rows_that_miss_the_marking"),
    ("run checks marking", "    problems = marking_problems()\n    if problems:\n        return",
     "    problems = marking_problems()\n    if False:\n        return", "Freeze.test_a_drifted_marking_after_the_freeze_refuses_the_run"),
    ("marking hash", '    if (doc.get("source") or {}).get("sha256_16") != _sha_file(marking_path):', "    if False:",
     "Classes.test_a_drifted_marking_is_named"),
    ("marking group", "        if not mine:", "        if False:", "Classes.test_a_missing_group_or_a_row_outside_the_marking_is_named"),
    ("marking operation", '            if not any(op in (r.get("covers") or []) for r in mine):', "            if False:",
     "Classes.test_an_uncovered_operation_is_named"),
    ("marking outside", '        if r.get("group") not in names:', "        if False:",
     "Classes.test_a_missing_group_or_a_row_outside_the_marking_is_named"),
    ("marking covers none", '        if not r.get("covers"):', "        if False:", "Classes.test_an_uncovered_operation_is_named"),
    ("budget guard", "    if spent >= OR_BUDGET_USD:\n        raise", "    if False:\n        raise",
     "Spend.test_or_ask_refuses_a_spent_grant_before_anything_leaves"),
    ("or_ask budget", "    budget_guard()\n    verdict, why = (gate or content_gate)", "    verdict, why = (gate or content_gate)",
     "Spend.test_or_ask_refuses_a_spent_grant_before_anything_leaves"),
    ("filter budget", "        try:\n            budget_guard()\n        except RuntimeError as exc:",
     "        try:\n            pass\n        except RuntimeError as exc:", "Spend.test_the_filter_asks_jev_nothing_once_the_grant_is_spent"),
    ("run budget", '    if spent:\n        out.write("%s run: %s\\n" % (NODATA, spent))',
     '    if False:\n        out.write("%s run: %s\\n" % (NODATA, spent))', "Spend.test_a_run_refuses_a_spent_grant_before_any_session_starts"),
    ("run-one budget", '    if spent:\n        out.write("%s run-one: %s\\n" % (NODATA, spent))',
     '    if False:\n        out.write("%s run-one: %s\\n" % (NODATA, spent))', "Spend.test_a_run_refuses_a_spent_grant_before_any_session_starts"),
    ("person budget only deepseek", '    if person != "deepseek":\n        return ""', '    if True:\n        return ""',
     "Spend.test_a_run_refuses_a_spent_grant_before_any_session_starts"),
    ("run spend own", '    return {"openrouter_usd": round(sum(r.get("person_usd") or 0.0 for r in records), 4),',
     '    return {"openrouter_usd": round(spend_total(), 4),', "ReportAndGate.test_the_report_shows_the_runs_own_spend_never_the_ledger_total"),
    ("old run spend unknown", '              % (spend.get("openrouter_usd") if "openrouter_ledger_total_usd" in spend else NODATA,',
     '              % (spend.get("openrouter_usd"),', "ReportAndGate.test_the_report_shows_the_runs_own_spend_never_the_ledger_total"),
    ("release row label", "        LABEL, counted[PASS], len(result", '        "", counted[PASS], len(result',
     "ReportAndGate.test_the_release_bar_row_and_the_json_report_carry_the_label"),
    ("json report label", "        json.dump(dict(result, label=LABEL,", '        json.dump(dict(result, label="",',
     "ReportAndGate.test_the_release_bar_row_and_the_json_report_carry_the_label"),
    ("run plan holdout", "    return plan_cells(kept, cells, per_cell, args.seed, exclude=() if args.holdout else holdout,",
     "    return plan_cells(kept, cells, per_cell, args.seed, exclude=(),", "Version2.test_a_run_excludes_the_holdout_and_a_holdout_run_sees_only_it"),
    ("acceptable bar", '            for axis in ("benefit", "ease") if sum(trial[axis]) < ACCEPTABLE_BAR]',
     '            for axis in ("benefit", "ease") if False]', "Person.test_one_verdict"),
    ("classify budget", '            rejected += [{"url": c["url"], "reasons": ["OpenRouter budget spent"]} for c in chunk]\n'
     '            continue\n', '            pass\n', "Classify.test_classify_stops_spending_at_the_budget"),
    ("run-one holdout by name", "    if holdout is None or args.scenario in holdout:", "    if holdout is None:",
     "Person.test_run_one_refuses_a_concealed_holdout_scenario"),
    ("baseline key", '    return "|".join((scenario_id, arm, model, version)) if version else ""',
     '    return "|".join((scenario_id, arm, model, version))', "Version2.test_a_cached_baseline_matches_only_the_same_client_version"),
    ("repair file kept", "    if run_dir and current:\n", "    if run_dir:\n",
     "Version2.test_rereading_an_old_run_never_overwrites_its_repair_file"),
    ("run-one holdout", "    if holdout is None or args.scenario in holdout:", "    if holdout is None:",
     "Person.test_run_one_refuses_a_concealed_holdout_scenario"),
    ("link not followed", "                if os.path.islink(path) or not _under(path, real_root):",
     "                if False:", "Version2.test_a_planted_link_is_never_followed_by_the_harness"),
    ("copy keeps links", "            shutil.copytree(project, copy, symlinks=True, ignore=shutil.ignore_patterns(\".git\"),",
     "            shutil.copytree(project, copy, ignore=shutil.ignore_patterns(\".git\"),",
     "Version2.test_a_planted_link_is_never_followed_by_the_harness"),
    ("vetted links", '        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))',
     '        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))', "Version2.test_only_vetted_files_are_read_or_copied"),
    ("vetted hard links", "        if _stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and st.st_size <= SAFE_READ_CAP:",
     "        if _stat.S_ISREG(st.st_mode) and st.st_size <= SAFE_READ_CAP:", "Version2.test_only_vetted_files_are_read_or_copied"),
    ("vetted size", "        if _stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and st.st_size <= SAFE_READ_CAP:",
     "        if _stat.S_ISREG(st.st_mode) and st.st_nlink == 1:", "Version2.test_only_vetted_files_are_read_or_copied"),
    ("vetted copy", "                            copy_function=_safe_copy)", "                            )",
     "Version2.test_only_vetted_files_are_read_or_copied"),
    ("bare judge", '            "--strict-mcp-config", "--mcp-config", BARE_MCP, "--disallowedTools", disallowed]',
     '            "--disallowedTools", disallowed]', "Filter.test_a_judge_is_a_bare_client_with_no_mcp_server"),
    ("private check", '            "what_you_will_check_before_accepting": scenario.get("outcome"),', "",
     "Version2.test_the_person_holds_a_private_check_and_a_patience_budget"),
    ("cell gate refusal", 'for t in graded for g in ("security", "correctness") if t.get(g) == FAIL]',
     'for t in graded for g in ("security", "correctness") if False]', "EvaluateCell.test_a_security_failure_cannot_be_averaged_away"),
    ("cell seeds", "    if distinct_seeds < MIN_KEPT_PER_CELL:\n", "    if False:\n", "EvaluateCell.test_two_distinct_seeds_is_no_data"),
    ("cell participants", "    if len(graded) < MIN_TRIALS_PER_CELL:\n", "    if False:\n",
     "EvaluateCell.test_nine_participants_is_no_data"),
    ("cell no evidence", '    if any(None in t["benefit"] or None in t["ease"] for t in graded):', "    if False:",
     "EvaluateCell.test_a_point_with_no_evidence_is_no_data"),
    ("cell unknown security", '    if any(t.get("security") not in (PASS, FAIL) for t in graded):', "    if False:",
     "EvaluateCell.test_unknown_security_is_no_data"),
    ("cell ungraded", '        if not t.get("graded"):\n            nodata.append', '        if False:\n            nodata.append',
     "EvaluateCell.test_an_ungraded_trial_is_no_data"),
    ("cell bar", "        if row[axis] < ACCEPTABLE_BAR:", "        if False:", "EvaluateCell.test_a_weak_score_fails_the_bar"),
    ("cell completion", "    if done < COMPLETION_SHARE * len(trials):", "    if False:",
     "EvaluateCell.test_failed_participants_stay_in_the_denominator"),
    ("grid all pass", 'verdict = PASS if verdicts == {PASS} else FAIL if FAIL in verdicts else NODATA',
     'verdict = PASS if PASS in verdicts and FAIL not in verdicts else FAIL if FAIL in verdicts else NODATA',
     "Grid.test_one_unrun_cell_keeps_release_no_data_however_good_the_rest"),
    ("grid fail", 'verdict = PASS if verdicts == {PASS} else FAIL if FAIL in verdicts else NODATA',
     'verdict = PASS if verdicts == {PASS} else NODATA', "Grid.test_one_failing_cell_fails_release_and_writes_its_repair_item"),
    ("grid repairs", "            repairs.append({", "            (lambda r: None)({",
     "Grid.test_one_failing_cell_fails_release_and_writes_its_repair_item"),
    ("freeze scenarios", '    if doc.get("filter_sha") != _sha_file(filter_path):', "    if False:",
     "Freeze.test_changed_scenarios_are_a_refusal"),
    ("freeze grader", '    if doc.get("grader_sha") != grader_sha():', "    if False:", "Freeze.test_a_changed_grader_is_a_refusal"),
    ("freeze missing", "    if doc is None:\n        return \"nothing is frozen", "    if False:\n        return \"nothing is frozen",
     "Freeze.test_nothing_frozen_is_a_refusal"),
    ("stale grader", '        if r.get("grader_sha") and r["grader_sha"] != grader_sha():', "        if False:",
     "Freeze.test_a_record_graded_under_another_grader_is_not_rescored"),
    ("run needs freeze", "    stale = frozen_mismatch()\n    if stale:\n", "    stale = frozen_mismatch()\n    if False:\n",
     "ReportAndGate.test_run_refuses_unfrozen_scenarios"),
    ("content gate", '    if verdict != "ALLOW":\n', "    if False:\n", "Bridge.test_the_content_gate_refusal_sends_nothing"),
    ("flow follow ups", '    e4 = 0 if not finished else 2 if rec["follow_ups"] <= 1 else 1',
     '    e4 = 0 if not finished else 2', "Grade.test_follow_ups_cost_flow_points"),
    ("protocol 1 files", "        stray += [os.path.join(b, f) for b, _d, fs in os.walk(home) for f in fs]\n", "\n",
     "Person.test_item_1_a_home_with_any_file_is_refused"),
    ("protocol 1 memory", '                  if n in ("CLAUDE.md", "memory", "MEMORY.md", "notes")]',
     '                  if False]', "Person.test_item_1_a_memory_folder_in_the_client_config_is_refused"),
    ("protocol 1 bundle", '                  if not _under(b, os.path.join(config, "plugins")) for n in ds + fs',
     "                  for n in ds + fs", "Person.test_item_1_the_candidate_bundles_own_files_are_not_strays"),
    ("protocol 1 plugins", "        if installed != sorted(expected):\n", "        if False:\n",
     "Person.test_item_1_another_plugin_is_refused"),
    ("protocol 2", '    if not (re.match(r"^[0-9a-f]{16}$", str(shas.get("scenario")))',
     '    if False and (re.match(r"^[0-9a-f]{16}$", str(shas.get("scenario")))',
     "Person.test_item_2_missing_input_hashes_are_refused"),
    ("protocol 3", '    if not callable(ctx.get("action_filter")):', "    if False:", "Person.test_item_3_no_action_filter_is_refused"),
    ("protocol 4", '    if ctx.get("arm") == "brother" and not (', '    if False and not (',
     "Person.test_item_4_rescue_detection_without_plugin_roots_is_refused"),
    ("protocol 5", '    if ctx.get("person_backend") not in PERSON_BACKENDS or ctx.get("person_linked") is not False:',
     "    if False:", "Person.test_item_5_an_unknown_backend_or_a_linked_person_is_refused"),
    ("protocol 5 after", '    if not rec.get("person_model") or rec.get("person_linked") is not False:', "    if False:",
     "Person.test_item_5_after_the_run_needs_the_answering_model"),
    ("protocol at entry", '        if refused:\n            rec["protocol_refused"]', '        if False:\n            rec["protocol_refused"]',
     "Person.test_the_run_entry_point_refuses_before_any_turn"),
    ("person view", '    return {"you": scenario.get("persona"), "what_you_want": scenario.get("task_message"),',
     '    return {"you": scenario.get("persona"), "what_you_want": scenario.get("task_message"), "f": scenario.get("fixture"),',
     "Person.test_the_person_sees_no_fixture_and_no_acceptance"),
    ("person parse", '    if obj.get("action") == "stop" and obj.get("reason") in ("satisfied", "gave_up"):',
     '    if obj.get("action") == "stop":', "Person.test_parse_person_reads_say_and_stop_and_refuses_the_rest"),
    ("redact email", '    return _HOME_DIR.sub(lambda m: m.group(1) + "[user]", _EMAIL.sub("[email]", str(text or "")))',
     '    return _HOME_DIR.sub(lambda m: m.group(1) + "[user]", str(text or ""))',
     "Person.test_replies_are_scrubbed_of_emails_and_home_folders"),
    ("redact home", '    return _HOME_DIR.sub(lambda m: m.group(1) + "[user]", _EMAIL.sub("[email]", str(text or "")))',
     '    return _EMAIL.sub("[email]", str(text or ""))', "Person.test_replies_are_scrubbed_of_emails_and_home_folders"),
    ("cold patterns", '    return [why for pattern, why in REVEALS if re.search(pattern, str(text or ""), re.I)]',
     "    return []", "Scenarios.test_cold_discovery_refuses_every_kind_of_giveaway"),
    ("cold wiring", "        if told:\n", "        if False:\n", "Scenarios.test_the_check_in_and_interruption_are_cold_too"),
    ("keep reveals", 'answers["reveals"] < 0.5):', 'answers["reveals"] < 2):', "Filter.test_revealing_text_drops"),
    ("false success", " or false_success \\\n", " \\\n",
     "Grade.test_a_person_claiming_success_over_a_failed_check_is_false_success"),
    ("person verbs", "command only if the assistant itself showed it to you.",
     "command only if the assistant itself showed it to you, or /brother start.", "Person.test_the_person_is_never_told_the_tools_verbs"),
    ("one verdict", '    if not trial["completed"] or trial["rescue"]:', "    if False:", "Person.test_one_verdict"),
    ("run-one freeze", "    stale = why or frozen_mismatch()\n    if stale:\n", "    stale = why or frozen_mismatch()\n    if False:\n",
     "Person.test_run_one_refuses_unfrozen_scenarios"),
]



def _mutant(old, new):
    with open(SOURCE, encoding="utf-8") as fh:
        text = fh.read()
    if text.count(old) != 1:
        return None, "the guard text occurs %d time(s), not once" % text.count(old)
    mod = types.ModuleType("sim_trial_mutant")
    mod.__file__ = SOURCE
    exec(compile(text.replace(old, new), SOURCE, "exec"), mod.__dict__)
    return mod, ""


class Mutations(unittest.TestCase):
    def test_every_guard_has_a_test_that_goes_red_under_its_mutation(self):
        global M
        survivors = []
        for name, old, new, target in MUTATIONS:
            mod, why = _mutant(old, new)
            if mod is None:
                survivors.append("%s: NOT APPLIED, %s" % (name, why))
                continue
            cls_name, meth = target.split(".")
            result = unittest.TestResult()
            original, M = M, mod
            try:
                globals()[cls_name](meth).run(result)
            finally:
                M = original
            if result.wasSuccessful():
                survivors.append("%s: SURVIVED, %s stayed green" % (name, target))
        self.assertEqual([], survivors, "\n".join(survivors))

    def test_each_target_is_green_on_the_real_module(self):
        for name, _old, _new, target in MUTATIONS:
            cls_name, meth = target.split(".")
            result = unittest.TestResult()
            globals()[cls_name](meth).run(result)
            self.assertTrue(result.wasSuccessful(), "%s: %s is red on the real module" % (name, target))


# ------------------------------------------------------------------------------------------ SIM1.c: FirstUserBar
FU_DIR = os.path.join(os.path.dirname(HERE), "benchmarks", "sim-users", "first-user")
_FU_RUNS = {}


def fu_set():
    return copy.deepcopy(M.load_first_user()[0])


def fu_solutions():
    with open(os.path.join(FU_DIR, "solutions.json"), encoding="utf-8") as fh:
        return json.load(fh)["solutions"]


def fu_apply(project, files):
    for rel, body in files.items():
        path = os.path.join(project, rel)
        if body is None:
            if os.path.exists(path):
                os.remove(path)
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)


def fu_exit(s, solved):
    """(first exit, second exit) of a scenario's own acceptance, really run in a throwaway project: on its
    untouched fixture, or after the answer key was applied. Twice, because a check must not change the project."""
    key = (s["id"], solved)
    if key not in _FU_RUNS:
        project = tempfile.mkdtemp(prefix="fu-", dir=M._scratch_root())
        try:
            fu_apply(project, s["fixture"]["files"])
            if solved:
                fu_apply(project, fu_solutions()[s["id"]])
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": project, "PYTHONDONTWRITEBYTECODE": "1"}
            cmd = [sys.executable] + s["acceptance"]["command"][1:]
            codes = [subprocess.run(cmd, cwd=project, capture_output=True, text=True, env=env, timeout=60).returncode
                     for _ in range(2)]
            _FU_RUNS[key] = tuple(codes)
        finally:
            shutil.rmtree(project, ignore_errors=True)
    return _FU_RUNS[key]


def fu_raw(participant="P001", row="execution-outcome/deliver", scenario_id="FU-01", **over):
    """A run record for a first-user cell: an ordinary session, nobody cut it off."""
    r = raw(participant, "execution-outcome:easy", scenario_id)
    r.update(cell=row, **{"class": row})
    r.update(interrupted=False, acceptance_after_interruption=None)
    r.update(over)
    return r


LOST = dict(final_acceptance={"exit": 1, "tail": ""}, acceptance_at_check_in=1, check_in_reply="It is not done yet.",
            person_accepted=False, acceptance_records=[], receipts=[], receipt_rerun=[], receipt_claims_pass=False,
            abandoned=True)
NINE = dict(check_in_reply="It is done and the tests pass.")                     # ease 9: no next step named
EIGHT = dict(NINE, follow_ups=2)                                                   # ease 8


def fu_trials(row, spec):
    """Graded first-user trials, `spec` a list of record overrides, one trial each."""
    return [M.grade_first_user(fu_raw("P%03d" % i, row, "FU-%d" % (i % 3), **over)) for i, over in enumerate(spec)]


ROW = "execution-outcome/deliver"
ADMIT = {ROW: "admitted"}


def setattr_all(mod, names, values):
    for n, v in zip(names, values):
        setattr(mod, n, v)


def mutated_copy(doc, fn):
    out = copy.deepcopy(doc)
    fn(out)
    return out


class FirstUserBar(unittest.TestCase):
    """SIM1.c. The first-user bar: its scenario set covers all 22 key rows, its rubric is settled and frozen with
    reasons before any scored run, and a natural success can reach the top of it. EVERY CASE ISOLATES ONE
    CONDITION: it starts from the committed, valid set or rubric and changes one property."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fu-t-", dir=M._scratch_root())
        self.rows = M.load_classes()[0]
        self.seeds = M.load_seed_index()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def problems(self, scenarios):
        return M.first_user_problems(scenarios, self.rows, self.seeds)

    # ---------------------------------------------------------------------------------------------- the set
    def test_the_committed_set_meets_its_contract(self):
        scenarios = fu_set()
        self.assertEqual([], self.problems(scenarios))
        self.assertEqual(22, len(self.rows))
        for row in self.rows:
            self.assertGreaterEqual(len([s for s in scenarios if s["class"] == row]), 3, row)

    def test_a_key_row_with_no_first_user_scenario_is_refused(self):
        scenarios = [s for s in fu_set() if s["class"] != "loop-control/adopt"]
        self.assertEqual(["key row loop-control/adopt has 0 scenario(s) from distinct sources, fewer than 3"],
                         self.problems(scenarios))

    def test_a_row_whose_scenarios_share_one_source_is_refused(self):
        scenarios = fu_set()
        for s in scenarios:
            if s["class"] == "loop-control/adopt":
                s["seed_id"] = s["source"]["seed_id"] = "S04"
        self.assertEqual(["key row loop-control/adopt has 1 scenario(s) from distinct sources, fewer than 3"],
                         self.problems(scenarios))

    def test_the_tier_mix_is_about_45_45_10(self):
        scenarios = fu_set()
        share = {t: sum(1 for s in scenarios if s["tier"] == t) / float(len(scenarios)) for t in M.FIRST_USER_TIERS}
        self.assertAlmostEqual(0.45, share["easy"], delta=0.05)
        self.assertAlmostEqual(0.45, share["medium"], delta=0.05)
        self.assertAlmostEqual(0.10, share["hard"], delta=0.05)
        for s in scenarios:
            if s["tier"] == "medium":
                s["tier"] = "easy"
        said = self.problems(scenarios)
        self.assertEqual(2, len(said), said)
        self.assertTrue(all(p.startswith("tier ") for p in said), said)

    def test_each_required_kind_language_and_persona_is_needed_on_its_own(self):
        scenarios = fu_set()
        for s in scenarios:
            if s["kind"] == "cost-question":
                s["kind"] = "docs"
        self.assertEqual(["no scenario of kind cost-question"], self.problems(scenarios))
        scenarios = fu_set()
        for s in scenarios:
            s["language"] = "python"
        self.assertEqual(["fixtures are in one language only"], self.problems(scenarios))
        scenarios = fu_set()
        for s in scenarios:
            s["persona"]["non_developer"] = False
        self.assertEqual(["no non-developer persona"], self.problems(scenarios))
        scenarios = fu_set()
        for s in scenarios:
            if s["persona"]["skill_level"] == "novice":
                s["persona"]["skill_level"] = "intermediate"
        self.assertEqual(["no novice persona"], self.problems(scenarios))

    def test_scripted_interruptions_stay_the_exception(self):
        scenarios = fu_set()
        self.assertLessEqual(sum(1 for s in scenarios if s["interruption"]), 0.2 * len(scenarios))
        for s in scenarios[:20]:
            s["interruption"] = {"after_s": 60, "message": "Sorry, my laptop slept. Please carry on."}
        self.assertEqual(["more than 20 percent of the scenarios carry a scripted interruption"],
                         self.problems(scenarios))

    def test_one_bad_property_is_named_alone(self):
        deliver = fu_set()[0]
        self.assertEqual([], M.validate_first_user_scenario(deliver, self.rows, self.seeds))
        resume = next(s for s in fu_set() if s["class"] == "execution-outcome/resume")
        cases = [
            ("not a key row", dict(deliver, **{"class": "nope/row"})),
            ("tier", dict(deliver, tier="extra-hard")),
            ("is not a first-user kind", dict(deliver, kind="hostile-note")),
            ("is not a first-user kind", dict(deliver, kind="double-start")),
            ("language", dict(deliver, language="ruby")),
            ("expected_decision", dict(deliver, expected_decision="maybe")),
            ("not labelled", dict(deliver, label="HUMAN")),
            ("non_developer", dict(deliver, persona={"skill_level": "novice", "background": "x"})),
            ("interruption", dict(deliver, interruption={"after_s": 5, "message": "stop"})),
            ("not cold", dict(deliver, task_message="Please fix the thing, then run pip to check it.")),
            ("not plain first-time language", dict(deliver, task_message="Read the hooks under .claude/ first.")),
            ("dash", dict(deliver, outcome="it works " + chr(0x2013) + " mostly")),
            ("next_sessions must hold exactly 1", dict(resume, next_sessions=[])),
            ("source must name", dict(deliver, source={"seed_id": "S99999", "shape_basis": "x"})),
            ("fixture needs 1 to 10 files", dict(deliver, fixture={"files": dict({"f%d.txt" % i: "x" for i in range(10)}, **{"check.py": "x"})},
                                    acceptance={"command": ["python3", "check.py"]})),
            ("acceptance.command", dict(deliver, acceptance={"command": ["python3", "-c", "print(1)"]})),
            ("no public https source", dict(deliver, seed_id="S99999", source={"seed_id": "S99999", "shape_basis": "x"})),
        ]
        for needle, s in cases:
            said = M.validate_first_user_scenario(s, self.rows, self.seeds)
            self.assertEqual(1, len(said), (needle, said))
            self.assertIn(needle, said[0])

    def test_a_pasted_error_may_name_files_but_no_other_kind_may(self):
        text = 'It crashes with: File "totals.py", line 2, in average, ZeroDivisionError. Please make it stop.'
        trace = next(s for s in fu_set() if s["kind"] == "traceback")
        self.assertEqual([], M.validate_first_user_scenario(dict(trace, task_message=text), self.rows, self.seeds))
        other = M.validate_first_user_scenario(dict(trace, kind="docs", task_message=text), self.rows, self.seeds)
        self.assertEqual(1, len(other), other)
        self.assertIn("a file name", other[0])
        tool = M.validate_first_user_scenario(dict(trace, task_message=text + " Use the brother door."), self.rows,
                                              self.seeds)
        self.assertIn("a feature name of the tool", tool[0])

    def test_every_scenario_is_traceable_to_a_public_source(self):
        for s in fu_set():
            seed = self.seeds.get(s["seed_id"])
            self.assertIsNotNone(seed, s["id"])
            self.assertTrue(seed["url"].startswith("https://"), s["id"])
            self.assertEqual(s["seed_id"], s["source"]["seed_id"], s["id"])
            self.assertTrue(s["source"]["shape_basis"].strip(), s["id"])

    def test_every_scenario_has_an_answer_key_and_nothing_else_has(self):
        self.assertEqual(sorted(s["id"] for s in fu_set()), sorted(fu_solutions()))

    def test_every_check_fails_on_the_fixture_and_passes_after_the_answer(self):
        unrun, bad = [], []
        for s in fu_set():
            before, after = fu_exit(s, False), fu_exit(s, True)
            if 3 in before + after:
                unrun.append(s["id"])      # the check reported that its runtime is missing: NO-DATA, not a pass
            elif before != (1, 1) or after != (0, 0):
                bad.append("%s before %s after %s" % (s["id"], before, after))
        self.assertEqual([], bad)
        if unrun:
            self.skipTest("NO-DATA: the runtime for %d scenario(s) is missing (%s)" % (len(unrun), ", ".join(unrun)))

    def test_the_boundary_bar_is_untouched(self):
        with open(os.path.join(os.path.dirname(FU_DIR), "scenarios.json"), encoding="utf-8") as fh:
            self.assertEqual(513, len(json.load(fh)["scenarios"]))
        self.assertEqual(2, M.RUBRIC["version"])
        old = M.grade(raw("P1", "execution-outcome:easy", "C-S01", interrupted=False, acceptance_after_interruption=None),
                      {})
        self.assertEqual([4, 2, 2, None], old["benefit"])     # the boundary grader still leaves recovery open

    # ------------------------------------------------------------------------------------- the frozen rubric
    def rubric(self):
        return M._load_json(os.path.join(FU_DIR, "rubric.json"))[0]

    def test_the_rubric_file_settles_every_validity_problem_with_a_reason(self):
        doc = self.rubric()
        self.assertEqual([], M.rubric_problems(doc))
        ids = [r["id"] for r in doc["rules"]]
        for owner_named in ("uninterrupted-success", "correct-rejection", "cell-aggregation", "rescue", "question"):
            self.assertIn(owner_named, ids)
        self.assertEqual("SIMULATED", doc["label"])

    def test_one_broken_rule_is_named_alone(self):
        doc = self.rubric()
        drop = mutated_copy(doc, lambda d: d["rules"].pop([r["id"] for r in d["rules"]].index("cell-aggregation")))
        self.assertEqual(["rule cell-aggregation is present 0 times, not once"], M.rubric_problems(drop))
        twice = mutated_copy(doc, lambda d: d["rules"].append(dict(d["rules"][0])))
        self.assertEqual(["rule uninterrupted-success is present 2 times, not once"], M.rubric_problems(twice))
        bare = mutated_copy(doc, lambda d: d["rules"][1].update(reason="Because."))
        self.assertEqual(["rule correct-rejection has no reason"], M.rubric_problems(bare))
        terse = mutated_copy(doc, lambda d: d["rules"][1].update(rule="Reject."))
        self.assertEqual(["rule correct-rejection has no rule text"], M.rubric_problems(terse))
        cites = mutated_copy(doc, lambda d: d["rules"][0].update(
            reason=d["rules"][0]["reason"] + " The last run measured the cap in practice."))
        said = M.rubric_problems(cites)
        self.assertEqual(1, len(said), said)
        self.assertIn("cites a result", said[0])
        self.assertEqual(["rubric not labelled SIMULATED"], M.rubric_problems(mutated_copy(doc, lambda d: d.update(label="x"))))

    def test_the_rubric_bars_are_the_codes_bars(self):
        bars = self.rubric()["bars"]
        self.assertEqual((M.PRODUCT_BAR, M.ACCEPTABLE_BAR, M.COMPLETION_SHARE, M.MIN_TRIALS_PER_CELL,
                          M.FIRST_USER_MIN_PER_ROW),
                         (bars["product"], bars["acceptable"], bars["share"], bars["min_attempts_per_row"],
                          bars["min_distinct_sources_per_row"]))

    def freeze_into(self):
        """The committed set and rubric copied into a throwaway folder and frozen there."""
        paths = {k: os.path.join(self.tmp, k) for k in ("scenarios.json", "rubric.json", "frozen.json")}
        for name in ("scenarios.json", "rubric.json"):
            shutil.copy(os.path.join(FU_DIR, name), paths[name])
        runs = os.path.join(self.tmp, "runs")
        doc = M.freeze_first_user(paths["frozen.json"], paths["scenarios.json"], paths["rubric.json"], runs)
        return paths, runs, doc

    def departures(self, paths):
        return M.first_user_freeze_problems(paths["frozen.json"], paths["scenarios.json"], paths["rubric.json"])

    def test_the_committed_bar_is_frozen_and_unchanged(self):
        self.assertEqual([], M.first_user_freeze_problems())

    def test_a_fresh_freeze_holds_until_one_frozen_thing_changes(self):
        paths, _runs, doc = self.freeze_into()
        self.assertEqual("SIMULATED", doc["label"])
        self.assertEqual([], self.departures(paths))

    def test_a_changed_rubric_scenario_or_grader_is_each_a_refusal(self):
        paths, _runs, doc = self.freeze_into()
        rub = M._load_json(paths["rubric.json"])[0]
        rub["rules"][0]["reason"] += " And this is also true."
        M._write_json(paths["rubric.json"], rub)
        self.assertIn("the rubric changed after it was frozen", self.departures(paths))
        shutil.copy(os.path.join(FU_DIR, "rubric.json"), paths["rubric.json"])
        scen = M._load_json(paths["scenarios.json"])[0]
        scen["scenarios"][0]["outcome"] += " Also this."
        M._write_json(paths["scenarios.json"], scen)
        self.assertEqual(["the scenarios changed after they were frozen"], self.departures(paths))
        shutil.copy(os.path.join(FU_DIR, "scenarios.json"), paths["scenarios.json"])
        doc["grader_sha"] = "0" * 16
        M._write_json(paths["frozen.json"], doc)
        self.assertEqual(["the grading functions changed after they were frozen"], self.departures(paths))

    def test_nothing_frozen_is_a_departure(self):
        said = M.first_user_freeze_problems(os.path.join(self.tmp, "none.json"))
        self.assertEqual(1, len(said))
        self.assertTrue(said[0].startswith("the first-user bar is not frozen"))

    def test_a_freeze_is_refused_once_a_scored_run_exists(self):
        paths, runs, _doc = self.freeze_into()
        M._write_json(os.path.join(runs, "20261102T000000", "records.json"), {"records": []})
        with self.assertRaises(RuntimeError) as ctx:
            M.freeze_first_user(paths["frozen.json"], paths["scenarios.json"], paths["rubric.json"], runs)
        self.assertIn("a scored first-user run exists", str(ctx.exception))

    def test_an_invalid_set_or_rubric_cannot_be_frozen(self):
        paths = {k: os.path.join(self.tmp, k) for k in ("scenarios.json", "rubric.json", "frozen.json")}
        shutil.copy(os.path.join(FU_DIR, "rubric.json"), paths["rubric.json"])
        M._write_json(paths["scenarios.json"], {"scenarios": fu_set()[:3]})
        with self.assertRaises(RuntimeError) as ctx:
            M.freeze_first_user(paths["frozen.json"], paths["scenarios.json"], paths["rubric.json"],
                                os.path.join(self.tmp, "runs"))
        self.assertIn("not valid", str(ctx.exception))
        self.assertFalse(os.path.exists(paths["frozen.json"]))

    # ------------------------------------------------------------------------------- entry points: main()
    def run_main(self, *argv):
        sink = io.StringIO()
        old, sys.stdout = sys.stdout, sink
        try:
            code = M.main(list(argv))
        finally:
            sys.stdout = old
        return code, sink.getvalue()

    def patched(self):
        paths = {k: os.path.join(self.tmp, k) for k in ("scenarios.json", "rubric.json", "frozen.json")}
        for name in ("scenarios.json", "rubric.json"):
            shutil.copy(os.path.join(FU_DIR, name), paths[name])
        saved = (M.FIRST_USER_SCENARIOS_PATH, M.FIRST_USER_RUBRIC_PATH, M.FIRST_USER_FROZEN_PATH, M.FIRST_USER_RUNS_DIR)
        M.FIRST_USER_SCENARIOS_PATH, M.FIRST_USER_RUBRIC_PATH = paths["scenarios.json"], paths["rubric.json"]
        M.FIRST_USER_FROZEN_PATH, M.FIRST_USER_RUNS_DIR = paths["frozen.json"], os.path.join(self.tmp, "runs")
        self.addCleanup(lambda: setattr_all(M, ("FIRST_USER_SCENARIOS_PATH", "FIRST_USER_RUBRIC_PATH",
                                                "FIRST_USER_FROZEN_PATH", "FIRST_USER_RUNS_DIR"), saved))
        return paths

    def test_the_command_reads_no_data_before_the_freeze_and_pass_after_it(self):
        self.patched()
        code, said = self.run_main("first-user", "check")
        self.assertEqual(2, code)
        self.assertIn("NO-DATA SIMULATED first-user bar", said)
        self.assertEqual(0, self.run_main("first-user", "freeze")[0])
        code, said = self.run_main("first-user", "check")
        self.assertEqual(0, code)
        self.assertIn("PASS SIMULATED first-user bar: 66 scenarios, 0 problem(s)", said)
        for row in self.rows:
            self.assertIn(row, said)

    def test_the_command_fails_when_the_rubric_is_edited_after_the_freeze(self):
        paths = self.patched()
        self.assertEqual(0, self.run_main("first-user", "freeze")[0])
        rub = M._load_json(paths["rubric.json"])[0]
        rub["rules"][0]["reason"] += " And this is also true."
        M._write_json(paths["rubric.json"], rub)
        code, said = self.run_main("first-user", "check")
        self.assertEqual(1, code)
        self.assertIn("the rubric changed after it was frozen", said)

    def test_the_command_refuses_to_refreeze_after_a_scored_run(self):
        self.patched()
        M._write_json(os.path.join(M.FIRST_USER_RUNS_DIR, "20261102T000000", "records.json"), {"records": []})
        code, said = self.run_main("first-user", "freeze")
        self.assertEqual(2, code)
        self.assertIn("NO-DATA first-user freeze: a scored first-user run exists", said)

    def test_the_command_reads_no_data_for_an_unreadable_set(self):
        self.patched()
        os.remove(M.FIRST_USER_SCENARIOS_PATH)
        code, said = self.run_main("first-user", "check")
        self.assertEqual(2, code)
        self.assertIn("NO-DATA first-user check", said)

    def test_the_command_fails_when_a_row_has_no_scenario(self):
        self.patched()
        M._write_json(M.FIRST_USER_SCENARIOS_PATH, {"scenarios": [s for s in fu_set() if s["class"] != "loop-control/adopt"]})
        code, said = self.run_main("first-user", "check")
        self.assertEqual(1, code)
        self.assertIn("key row loop-control/adopt has 0 scenario(s)", said)

    # ------------------------------------------------------------------- the report: a whole run, graded
    def run_for(self, rows, extra=(), per=10):
        """A run folder as the harness writes one: records carry the old class:tier cell, and nothing says what
        decision a scenario expects."""
        scen = fu_set()
        recs = []
        for row in rows:
            mine = [s for s in scen if s["class"] == row]
            for i in range(per):
                s = mine[i % len(mine)]
                over = dict(cell=row + ":" + s["tier"])
                if s["expected_decision"] == "reject":
                    over.update(person_rejected=True, rejection_records=["r.json"], person_accepted=False,
                                acceptance_records=[], receipts=[], receipt_rerun=[], receipt_claims_pass=False)
                recs.append(fu_raw("P%03d" % i, row, s["id"], **over))
        run = os.path.join(self.tmp, "run1")
        M._write_json(os.path.join(run, "records.json"), {"label": "SIMULATED", "records": recs + list(extra)})
        return run

    def admit(self, states):
        """The admission the report reads, fixed for one test (the door's own view is tested on its own below)."""
        saved = M.load_admission
        M.load_admission = lambda *a, **k: (dict(states), "")
        self.addCleanup(setattr, M, "load_admission", saved)

    def frozen_world(self, states=None):
        self.patched()
        self.assertEqual(0, self.run_main("first-user", "freeze")[0])
        self.admit(states if states is not None else {r: "admitted" for r in self.rows})

    def test_the_report_passes_a_run_that_meets_the_bar_on_every_key_row(self):
        self.frozen_world()
        code, said = self.run_main("first-user", "report", "--runs", self.run_for(self.rows))
        self.assertEqual(0, code, said)
        self.assertIn("PASS SIMULATED first-user release grid (not human evidence): 22 PASS, 0 FAIL, 0 NO-DATA of 22 key rows", said)

    def test_the_report_reads_a_hidden_row_as_no_data_never_a_pass(self):
        states = {r: "admitted" for r in self.rows}
        states["loop-control/adopt"] = "hidden"
        self.frozen_world(states)
        code, said = self.run_main("first-user", "report", "--runs", self.run_for(self.rows))
        self.assertEqual(2, code)
        self.assertIn("21 PASS, 0 FAIL, 1 NO-DATA", said)
        self.assertIn("hidden from consumers", said)

    def test_the_report_fails_the_release_when_one_row_fails(self):
        self.frozen_world()
        run = self.run_for(self.rows)
        doc = M._load_json(os.path.join(run, "records.json"))[0]
        for r in doc["records"]:
            if r["class"] == "host-health/install":
                r.update(LOST)
        M._write_json(os.path.join(run, "records.json"), doc)
        code, said = self.run_main("first-user", "report", "--runs", run)
        self.assertEqual(1, code)
        self.assertIn("21 PASS, 1 FAIL, 0 NO-DATA", said)

    def test_the_report_keeps_an_ungraded_attempt_in_its_row(self):
        self.frozen_world()
        crashed = {"participant": "PX", "scenario_id": "FU-01", "class": ROW, "cell": ROW + ":easy", "arm": "brother",
                   "nodata": "the task crashed: x"}
        code, said = self.run_main("first-user", "report", "--runs", self.run_for(self.rows, extra=[crashed]))
        self.assertEqual(2, code)
        self.assertIn("21 PASS, 0 FAIL, 1 NO-DATA", said)

    def test_the_report_refuses_an_unfrozen_bar(self):
        self.patched()
        self.admit({r: "admitted" for r in self.rows})
        code, said = self.run_main("first-user", "report", "--runs", self.run_for(self.rows))
        self.assertEqual(2, code)
        self.assertIn("the first-user bar is not frozen", said)

    def test_the_report_refuses_a_record_that_names_no_key_row(self):
        self.frozen_world()
        stray = fu_raw("PX", "not/a-row", "FU-01", arm="brother")
        code, said = self.run_main("first-user", "report", "--runs", self.run_for(self.rows, extra=[stray]))
        self.assertEqual(2, code)
        self.assertIn("1 record(s) name no key row", said)

    def test_the_report_reads_an_unreadable_record_as_no_data_in_its_own_row(self):
        self.frozen_world()
        torn = {"participant": "PX", "scenario_id": "FU-01", "class": ROW, "cell": ROW + ":easy", "arm": "brother"}
        code, said = self.run_main("first-user", "report", "--runs", self.run_for(self.rows, extra=[torn, "text"]))
        self.assertEqual(2, code)
        self.assertIn("21 PASS, 0 FAIL, 1 NO-DATA", said)
        self.assertIn("the record could not be graded", said)

    def test_malformed_scenarios_and_rules_are_named_not_crashed_on(self):
        odd = fu_set()
        odd[0]["class"], odd[1]["seed_id"], odd[2]["persona"] = ["x"], ["y"], "novice"
        odd.append("text")
        said = self.problems(odd)
        self.assertTrue(any("not an object" in p for p in said), said)
        self.assertEqual(["rule uninterrupted-success is present 0 times, not once"],
                         [p for p in M.rubric_problems(dict(self.rubric(), rules=[{"id": ["x"]}] + self.rubric()["rules"][1:]))
                          if "uninterrupted" in p])

    def test_any_trace_of_a_run_keeps_the_bar_frozen(self):
        paths, runs, _doc = self.freeze_into()
        os.makedirs(os.path.join(runs, "20261102T000000"))          # a run that died before writing a record
        with self.assertRaises(RuntimeError):
            M.freeze_first_user(paths["frozen.json"], paths["scenarios.json"], paths["rubric.json"], runs)

    def test_checking_and_reporting_write_nothing(self):
        self.frozen_world()
        run = self.run_for(self.rows)

        def tree():
            return sorted(os.path.join(b, f) for b, _d, fs in os.walk(self.tmp) for f in fs)
        before = tree()
        self.run_main("first-user", "check")
        self.run_main("first-user", "report", "--runs", run)
        self.assertEqual(before, tree())

    def test_the_report_reads_a_missing_run_as_no_data(self):
        self.frozen_world()
        code, said = self.run_main("first-user", "report", "--runs", os.path.join(self.tmp, "no-such-run"))
        self.assertEqual(2, code)
        self.assertIn("NO-DATA SIMULATED first-user report", said)

    # --------------------------------------------------------------------------------------------- grading
    def test_an_uninterrupted_success_is_not_capped(self):
        rec = fu_raw()
        old = M.grade(rec, {})
        self.assertEqual(8, sum(v or 0 for v in old["benefit"]))        # the boundary rubric: 8 of 10 at most
        got = M.grade_first_user(rec, {})
        self.assertEqual(([4, 2, 2, None], 8), (got["benefit"], got["open_points"]))
        self.assertEqual((10.0, 10.0, True), (got["benefit_10"], got["ease_10"], got["completed"]))

    def test_a_missing_proof_is_a_real_deduction_not_free_marks(self):
        got = M.grade_first_user(fu_raw(receipts=[], receipt_rerun=[], receipt_claims_pass=False), {})
        self.assertEqual(7.5, got["benefit_10"])                        # 6 of the 8 open points
        got = M.grade_first_user(fu_raw(person_accepted=False, acceptance_records=[]), {})
        self.assertEqual(7.5, got["benefit_10"])
        self.assertEqual(0.0, M.grade_first_user(fu_raw(**LOST), {})["benefit_10"])

    def test_an_interrupted_run_is_scored_on_all_ten_points(self):
        got = M.grade_first_user(fu_raw(interrupted=True, acceptance_after_interruption=0), {})
        self.assertEqual(([4, 2, 2, 2], 10, 10.0), (got["benefit"], got["open_points"], got["benefit_10"]))
        hurt = M.grade_first_user(fu_raw(interrupted=True, acceptance_after_interruption=1, deleted_tracked=["a.py"]), {})
        self.assertEqual(([4, 2, 2, 0], 8.0), (hurt["benefit"], hurt["benefit_10"]))

    def test_every_natural_success_scores_at_the_top_band_and_a_lost_run_does_not(self):
        unrun = []
        for s in fu_set():
            before, after = fu_exit(s, False), fu_exit(s, True)
            if 3 in before + after:
                unrun.append(s["id"])
                continue
            over = dict(final_acceptance={"exit": after[0], "tail": ""}, acceptance_at_check_in=after[0],
                        expected_decision=s["expected_decision"])
            if s["expected_decision"] == "reject":
                over.update(person_rejected=True, rejection_records=["home/deliveries/rejection.json"],
                            person_accepted=False, acceptance_records=[], receipts=[], receipt_rerun=[],
                            receipt_claims_pass=False)
            win = M.grade_first_user(fu_raw("P1", s["class"], s["id"], **over), {})
            self.assertEqual((10.0, 10.0, True), (win["benefit_10"], win["ease_10"], win["completed"]), s["id"])
            lost_over = dict(LOST, final_acceptance={"exit": before[0], "tail": ""}, acceptance_at_check_in=before[0],
                             expected_decision=s["expected_decision"])
            if s["expected_decision"] == "reject":
                lost_over.update(person_rejected=False, rejection_records=[])
            lost = M.grade_first_user(fu_raw("P1", s["class"], s["id"], **lost_over), {})
            self.assertEqual(False, lost["completed"], s["id"])
            self.assertLess(lost["benefit_10"], 5.0, s["id"])
        if unrun:
            self.skipTest("NO-DATA: the runtime for %d scenario(s) is missing (%s)" % (len(unrun), ", ".join(unrun)))

    def reject_rec(self, **over):
        base = dict(expected_decision="reject", person_rejected=True, rejection_records=["home/deliveries/r.json"],
                    person_accepted=False, acceptance_records=[], receipts=[], receipt_rerun=[], receipt_claims_pass=False)
        base.update(over)
        return fu_raw(**base)

    def test_a_correct_rejection_earns_the_acceptance_points(self):
        got = M.grade_first_user(self.reject_rec(), {})
        self.assertEqual(([4, 2, None, None], 10.0, True, "PASS"),
                         (got["benefit"], got["benefit_10"], got["completed"], got["correctness"]))

    def test_a_rejection_without_its_record_earns_nothing(self):
        got = M.grade_first_user(self.reject_rec(rejection_records=[]), {})
        self.assertEqual(([4, 0, None, None], False), (got["benefit"], got["completed"]))
        self.assertEqual(6.67, got["benefit_10"])

    def test_accepting_a_result_that_should_be_rejected_is_a_correctness_failure(self):
        got = M.grade_first_user(self.reject_rec(person_accepted=True, person_rejected=False, rejection_records=[]), {})
        self.assertEqual("FAIL", got["correctness"])
        self.assertEqual("PASS", M.grade_first_user(self.reject_rec(), {})["correctness"])

    def test_an_unrecorded_rejection_decision_is_no_data(self):
        rec = self.reject_rec()
        del rec["person_rejected"]
        got = M.grade_first_user(rec, {})
        self.assertEqual(False, got["graded"])
        self.assertIn("did not record the person's decision", got["nodata"])

    def test_questions_are_counted_by_the_frozen_definition(self):
        yes = ["Which file do you mean?", "I fixed it. Is the title right?", "Done. What name should it have?"]
        no = ["Do you accept this?", "Done. Do you want to accept or reject it?", "It works? Yes, it does.", "", None,
              "I changed it and it is fine."]
        self.assertEqual([True] * 3, [M.is_counted_question(r) for r in yes])
        self.assertEqual([False] * 6, [M.is_counted_question(r) for r in no])
        self.assertEqual(3, M.count_questions(["Which?", "Fine."], denials=2))
        self.assertEqual(2, M.grade_first_user(fu_raw(replies=["Done. Do you accept this?"], questions=9), {})["ease"][1])
        self.assertEqual(0, M.grade_first_user(fu_raw(replies=["A?", "B?", "C?", "D?"]), {})["ease"][1])
        self.assertEqual(1, M.grade_first_user(fu_raw(replies=["A?", "B?"], questions=0), {})["ease"][1])
        self.assertEqual(0, M.grade_first_user(fu_raw(questions=4), {})["ease"][1])      # no texts kept: the count stands

    def test_rescue_is_only_knowledge_of_the_products_insides(self):
        self.assertTrue(M.persona_rescue("please read SKILL.md and then change the hooks"))
        self.assertEqual([], M.persona_rescue("Yes, go ahead with that."))
        plugin = os.path.join(self.tmp, "plugin")
        own = [{"name": "Read", "input": {"file_path": os.path.join(plugin, "hooks", "x.py")}}]
        project = [{"name": "Read", "input": {"file_path": os.path.join(self.tmp, "project", "app.py")}},
                   {"name": "Bash", "input": {"command": "python3 -m unittest discover"}}]
        self.assertTrue(M.agent_rescue(own, [plugin], ""))
        self.assertEqual([], M.agent_rescue(project, [plugin], ""))
        self.assertEqual(6.0, M.grade_first_user(fu_raw(persona_rescue=["x"]), {})["ease_10"])     # 4 of 10 lost

    # ----------------------------------------------------------------------------------------- the cell rule
    def cell(self, spec, sources=3, admission=ADMIT, row=ROW):
        return M.evaluate_first_user_cell(fu_trials(row, spec), sources, row, admission)

    def test_nine_of_ten_at_the_bar_is_product_grade_whatever_the_mean_says(self):
        got = self.cell([NINE] * 9 + [LOST])
        self.assertEqual(("PASS", "product", 0.9), (got["verdict"], got["grade"], got["share_product"]))
        self.assertLess(got["ease"], 9.0)         # a mean of ease would have refused this cell

    def test_eight_of_ten_fails_even_when_the_mean_is_acceptable(self):
        got = self.cell([{}] * 8 + [LOST] * 2)
        self.assertEqual(("FAIL", 0.8), (got["verdict"], got["share_acceptable"]))
        self.assertGreaterEqual(got["benefit"], M.ACCEPTABLE_BAR)    # a mean of benefit would have passed it

    def test_an_acceptable_cell_names_the_points_it_loses(self):
        got = self.cell([EIGHT] * 9 + [{}])
        self.assertEqual(("PASS", "acceptable", 0.1, 1.0), (got["verdict"], got["grade"], got["share_product"],
                                                           got["share_acceptable"]))
        self.assertTrue(any(x.startswith("plain-language flow") for x in got["limitations"]), got["limitations"])

    def test_an_abandoned_attempt_is_never_at_the_bar(self):
        got = self.cell([dict(abandoned=True)] * 10)
        self.assertEqual((10.0, 8.0), (got["benefit"], got["ease"]))     # good scores, yet never a proven delivery
        self.assertEqual(("FAIL", 0.0), (got["verdict"], got["share_acceptable"]))

    def test_a_rescued_attempt_cannot_be_at_either_bar(self):
        got = self.cell([{}] * 8 + [dict(persona_rescue=["x"])] * 2)
        self.assertEqual(("FAIL", 0.8), (got["verdict"], got["share_acceptable"]))

    def test_an_ungraded_attempt_stays_in_the_denominator_and_makes_the_cell_no_data(self):
        trials = fu_trials(ROW, [{}] * 9) + [{"graded": False, "participant": "P9", "scenario_id": "FU-1", "nodata": "x"}]
        got = M.evaluate_first_user_cell(trials, 3, ROW, ADMIT)
        self.assertEqual(("NO-DATA", 0.9), (got["verdict"], got["share_product"]))

    def test_short_evidence_is_no_data(self):
        self.assertEqual("NO-DATA", self.cell([{}] * 9)["verdict"])
        self.assertEqual("NO-DATA", self.cell([{}] * 10, sources=2)["verdict"])
        self.assertEqual("NO-DATA", self.cell([{}] * 9 + [dict(transcript_readable=False)])["verdict"])
        self.assertEqual("PASS", self.cell([{}] * 10)["verdict"])

    def test_a_security_or_correctness_failure_refuses_the_cell_whatever_the_share(self):
        got = self.cell([{}] * 9 + [dict(out_of_tree_writes=["/etc/x"])])
        self.assertEqual("FAIL", got["verdict"])
        self.assertTrue(any(r.startswith("security FAIL") for r in got["reasons"]), got["reasons"])
        got = self.cell([{}] * 9 + [dict(check_in_reply="It is done.", acceptance_at_check_in=1)])
        self.assertTrue(any(r.startswith("correctness FAIL") for r in got["reasons"]), got["reasons"])

    def test_a_hidden_or_unknown_row_is_never_a_pass(self):
        perfect = [{}] * 10
        hidden = self.cell(perfect, admission={ROW: "hidden"})
        self.assertEqual(("NO-DATA", True), (hidden["verdict"], hidden["hidden"]))
        self.assertIn("hidden from consumers", hidden["reasons"][0])
        unknown = self.cell(perfect, admission={})
        self.assertEqual(("NO-DATA", False), (unknown["verdict"], unknown["hidden"]))
        self.assertIn("unknown admission", unknown["reasons"][0])
        self.assertEqual("NO-DATA", self.cell(perfect, admission=None)["verdict"])
        self.assertEqual("NO-DATA", self.cell(perfect, admission={ROW: "yes"})["verdict"])
        self.assertEqual("PASS", self.cell(perfect, admission={ROW: "admitted"})["verdict"])

    def test_a_row_is_admitted_only_when_its_group_is_admitted(self):
        rows = [{"id": "a/x", "group": "a"}, {"id": "b/y", "group": "b"}, {"id": "c/z", "group": "c"},
                {"id": "d/w", "group": None}]
        view = {"queue": [{"id": "b", "cause": "no admission record"}]}
        self.assertEqual({"a/x": "admitted", "b/y": "hidden"}, M.row_admission(rows, view, {"a", "b"}))
        self.assertEqual({}, M.row_admission(rows, {"queue": [{"id": "*"}]}, {"a", "b"}))

    # ------------------------------------------- admission through the door's own view of OUT1.n's record
    def out1n(self, change):
        """A copy of the real OUT1.n record with every group's admission removed (so every group is hidden), then
        `change` applied: the record the door reads for this test."""
        doc = M._load_json(os.path.join(os.path.dirname(HERE), "docs", "plan", "ADMISSION.json"))[0]
        for entry in doc["entries"].values():
            entry.pop("admission", None)
        change(doc["entries"])
        path = os.path.join(self.tmp, "ADMISSION.json")
        M._write_json(path, doc)
        saved = M.OUT1N_ADMISSION_PATH
        M.OUT1N_ADMISSION_PATH = path
        self.addCleanup(setattr, M, "OUT1N_ADMISSION_PATH", saved)

    def admitted_record(self, **over):
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=M.ROOT, capture_output=True, text=True).stdout.strip()
        rec = {"implemented": True, "packaged": True, "supported_hosts": ["claude"], "host": "claude", "candidate": head,
               "installed_verdict": "PASS", "evidence_refs": ["e"], "evidence_current": True, "blocking_defects": [],
               "quality_verdict": "PASS", "benefit_score": 9, "ease_score": 9, "score_evidence_refs": ["s"]}
        rec.update(over)
        return rec

    def loop_rows(self):
        return sorted(r for r in self.rows if r.startswith("loop-control/"))

    def test_an_admitted_group_admits_its_rows_through_the_door(self):
        self.out1n(lambda e: e["loop-control"].update(admission=self.admitted_record()))
        table, why = M.load_admission()
        self.assertEqual("", why)
        self.assertEqual(self.loop_rows(), sorted(r for r, s in table.items() if s == "admitted"))

    def test_a_hidden_groups_row_never_grades(self):
        self.out1n(lambda e: e["loop-control"].update(admission=self.admitted_record(quality_verdict="FAIL")))
        self.patched()
        self.assertEqual(0, self.run_main("first-user", "freeze")[0])
        code, said = self.run_main("first-user", "report", "--runs", self.run_for(self.rows))
        self.assertEqual(2, code, said)
        for row in self.loop_rows():
            line = [x for x in said.splitlines() if x.strip().startswith(row + " ")][0]
            self.assertEqual(("NO-DATA", True), (line.split()[1], "hidden from consumers" in line), line)
        self.assertIn("0 admitted and graded on their evidence, 22 NO-DATA (22 hidden, 0 of unknown admission) of 22", said)

    def test_a_row_whose_group_the_record_does_not_name_reads_no_data(self):
        self.out1n(lambda e: (e.pop("loop-control"), e["execution-outcome"].update(admission=self.admitted_record())))
        table = M.load_admission()[0]
        for row in self.loop_rows():
            self.assertNotIn(row, table)
        got = self.cell([{}] * 10, admission=table, row=self.loop_rows()[0])
        self.assertEqual(M.NODATA, got["verdict"])
        self.assertIn("of unknown admission", got["reasons"][0])

    def test_an_unreadable_record_admits_nothing(self):
        path = os.path.join(self.tmp, "ADMISSION.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{torn")
        table, why = M.load_admission(path)
        self.assertEqual({}, table)
        self.assertIn("no OUT1.n admission record", why)

    def test_the_release_needs_every_key_row(self):
        rows = [ROW, "status-acceptance/state"]
        both = {r: "admitted" for r in rows}
        scen = [dict(s) for s in fu_set()]
        good = fu_trials(rows[0], [{}] * 10) + fu_trials(rows[1], [{}] * 10)
        grid = M.first_user_grid(good, scen, rows, both)
        self.assertEqual(("PASS", "product", "SIMULATED"), (grid["verdict"], grid["grade"], grid["label"]))
        short = M.first_user_grid(fu_trials(rows[0], [{}] * 10), scen, rows, both)
        self.assertEqual("NO-DATA", short["verdict"])
        broken = M.first_user_grid(fu_trials(rows[0], [{}] * 10) + fu_trials(rows[1], [LOST] * 10), scen, rows, both)
        self.assertEqual("FAIL", broken["verdict"])
        shut = M.first_user_grid(good, scen, rows, {rows[0]: "admitted", rows[1]: "hidden"})
        self.assertEqual("NO-DATA", shut["verdict"])

    def test_the_bar_is_selected_and_every_score_says_simulated(self):
        self.assertEqual("first-user", M.RELEASE_BAR)
        self.assertNotEqual(M.BAR_BOUNDARY, M.RELEASE_BAR)
        self.assertEqual(ROW, M.first_user_cell({"class": ROW, "tier": "easy"}))
        got = M.grade_first_user(fu_raw(), {})
        self.assertEqual((True, "first-user"), (got["simulated"], got["bar"]))
        self.assertEqual("SIMULATED", self.cell([{}] * 10)["label"])
        self.assertEqual(ROW, got["cell"])

    # ------------------------------------------------------- the live run: run_task and its entry point, wired
    def drive(self, sid, person_says, first_user=True, first_cut=None, bad_record=False, first_tools=(),
              heard=(), receipts=None):
        """run_task on a real first-user scenario with only the outside world faked: the fresh install, the client
        turn and the person's model. The fixture, git, the sandboxed acceptance check and every record are real.
        The faked turn applies the answer key on the first message, and writes a delivery record the way the
        product does when it is told to accept or reject. first_cut: what the first turn reports as cut, by
        default whether the harness armed a cut after the first edit."""
        scenario = {s["id"]: s for s in fu_set()}[sid]
        calls, prompts = [], []
        sys.path.insert(0, os.path.join(HERE, "journey"))
        import run_journey as real_rj
        fake = types.ModuleType("run_journey")
        fake.CANDIDATE_MARKETPLACE = "brother-candidate"

        def install_fresh(_tree, scratch):
            config, home = os.path.join(scratch, "config"), os.path.join(scratch, "home")
            root = os.path.join(config, "plugins", "cache", "brother-candidate", "brother")
            for d in (root, home, os.path.join(scratch, "candidate-marketplace")):
                os.makedirs(d)
            return {"ok": True, "identity": "cand", "candidate_identity": "cand", "reason": "", "config": config,
                    "home": home, "installed_root": root}
        fake.install_fresh = install_fresh
        fake.stranger_env = real_rj.stranger_env   # the production environment strip, never a stand in

        def turn(message, project, env_base, _login, _model, _resume, _deadline, transcript, cut_after_edit=None):
            calls.append({"message": message, "cut_after_edit": cut_after_edit,
                          "product_env": sorted(k for k in env_base if k.startswith(("BM_", "BROTHER")))})
            first = len(calls) == 1
            if first:
                fu_apply(project, fu_solutions()[sid])
                fu_apply(project, {k: v.replace("<SCRATCH>", os.path.dirname(project)) for k, v in (receipts or {}).items()})
            answers = os.path.join(env_base["HOME"], ".claude", "brother", "docs", "deliveries")
            if message in (scenario["accept_message"], M.FIRST_USER_REJECT_MESSAGE):
                verdict = {"verdict": "rejected"} if message == M.FIRST_USER_REJECT_MESSAGE else {"ref": "r"}
                fu_apply(answers, {"answer.json": json.dumps(verdict)})
                if bad_record:
                    fu_apply(answers, {"torn.json": "{not json"})
            with open(transcript, "a", encoding="utf-8") as fh:
                fh.write("{}\n" + "".join(json.dumps(x) + "\n" for x in (heard if first else ())))
            cut = (cut_after_edit is not None if first_cut is None else first_cut) if first else False
            tools = [t if isinstance(t, dict) else {"name": "Bash", "input": {"command": t % os.path.dirname(project)}}
                     for t in first_tools] if first else []
            return {"session_id": "s1", "result": "Done, the check passes. Next you can review it.",
                    "cut_by_deadline": cut, "tool_uses": tools, "denials": 0, "has_result": True, "unreadable": False,
                    "message": message, "exit": 0, "wall_s": 1.0, "is_error": False, "cost_usd": 0.0, "num_turns": 1}
        said = list(person_says)

        def ask(prompt, timeout=300):
            prompts.append(prompt)
            return json.dumps(said.pop(0) if said else {"action": "stop", "reason": "gave_up"}), "deepseek/v", 0.0
        saved = (sys.modules.get("run_journey"), M.claude_turn, M._scratch_root, M.sandboxed)
        sys.modules["run_journey"], M.claude_turn = fake, turn
        M._scratch_root = lambda: self.tmp
        if not M.sandbox_works():
            #: inside the hermetic check's own sandbox a second one cannot be applied, and run_acceptance then
            #: refuses with -1 by design; the boundary has its own tests (Vacuity, the receipt rerun), so here the
            #: check runs as written, without it, and the run wiring is what these cases drive
            M.sandboxed = lambda argv, *_a, **_k: list(argv)
        try:
            rec = M.run_task(scenario, "P001", "brother", "sonnet", None, os.path.join(self.tmp, "run"),
                             ask=ask, first_user=first_user)
        finally:
            (prior, M.claude_turn, M._scratch_root, M.sandboxed) = saved
            if prior is None:
                sys.modules.pop("run_journey", None)
            else:
                sys.modules["run_journey"] = prior
        return rec, calls, prompts

    SATISFIED = [{"action": "say", "text": "Is it finished?"}, {"action": "stop", "reason": "satisfied"}]

    def test_a_scenario_with_no_interruption_is_never_cut_and_runs_to_its_end(self):
        rec, calls, _p = self.drive("FU-02", self.SATISFIED)
        self.assertNotIn("nodata", rec)
        self.assertIsNone(calls[0]["cut_after_edit"])
        self.assertEqual((False, False, 0), (rec["interrupted"], rec["abandoned"], rec["final_acceptance"]["exit"]))
        got = M.grade_first_user(dict(rec, expected_decision="accept"), {})
        self.assertEqual((True, 8, None), (got["graded"], got["open_points"], got["benefit"][3]), got["observations"])

    def test_a_first_turn_stopped_by_its_deadline_is_abandonment_not_an_interruption(self):
        rec, calls, _p = self.drive("FU-02", self.SATISFIED, first_cut=True)
        self.assertNotIn("nodata", rec)
        self.assertEqual((False, True, True), (rec["interrupted"], rec["first_turn_timed_out"], rec["abandoned"]))

    def test_a_scenario_with_an_interruption_is_still_cut_and_told_its_message(self):
        rec, calls, _p = self.drive("FU-16", self.SATISFIED)
        lo, hi = M.INTERRUPT_AFTER_EDIT_S
        self.assertTrue(lo <= calls[0]["cut_after_edit"] <= hi)
        self.assertTrue(rec["interrupted"])
        self.assertEqual({s["id"]: s for s in fu_set()}["FU-16"]["interruption"]["message"], calls[1]["message"])

    def test_the_person_can_reject_and_the_rejection_is_recorded_apart_from_acceptances(self):
        rec, calls, prompts = self.drive("FU-26", [{"action": "stop", "reason": "rejected"}])
        self.assertIn('"rejected" (you do not accept', prompts[0])
        self.assertEqual(M.FIRST_USER_REJECT_MESSAGE, calls[-1]["message"])
        self.assertEqual((True, False), (rec["person_rejected"], rec["person_accepted"]))
        self.assertEqual(([], 1), (rec["acceptance_records"], len(rec["rejection_records"])))
        got = M.grade_first_user(dict(rec, expected_decision="reject"), {})
        self.assertEqual(2, got["benefit"][1], got["observations"])

    def test_an_acceptance_is_counted_and_an_unreadable_answer_is_neither(self):
        rec, _c, _p = self.drive("FU-02", self.SATISFIED, bad_record=True)
        self.assertEqual((False, True), (rec["person_rejected"], rec["person_accepted"]))
        self.assertEqual([[os.path.join("home", ".claude", "brother", "docs", "deliveries", "answer.json")], []],
                         [rec["acceptance_records"], rec["rejection_records"]])

    def test_the_run_keeps_every_reply_text_and_denial_for_the_question_count(self):
        rec, calls, _p = self.drive("FU-02", self.SATISFIED)
        self.assertEqual(len(calls), len(rec["replies"]))
        self.assertEqual(["Done, the check passes. Next you can review it."] * len(calls), rec["replies"])
        self.assertEqual(0, rec["denials"])

    CATALOGUE_TOOLS = "python3 %s/candidate-marketplace/bundle/runtime/hooks/brothermode/tools/"

    def test_a_hook_run_by_hand_from_the_candidate_catalogue_is_rescue(self):
        hook = self.CATALOGUE_TOOLS + "bm_fence_hook.py session-label --help"
        #: the product's own refusal names the hook, as the fence's did on the cost probe: still rescue
        rec, _c, _p = self.drive("FU-02", self.SATISFIED, first_tools=(hook,),
                                 heard=self.told("bm_fence_hook.py session-label"))
        self.assertEqual(["ran the plugin's hooks by hand to debug them"], rec["agent_rescue"])
        got = M.grade_first_user(dict(rec, expected_decision="accept"), {})
        self.assertEqual(0, got["ease"][0], got["observations"])

    @staticmethod
    def told(text, via="Edit"):
        """Two transcript lines: the agent's tool call, then an error result worded as a hook's refusal naming
        `text`. Via Edit it is the client's own reply; via Bash it is the agent's own output, so it is forgeable."""
        body = "PreToolUse:Edit hook error: refused; run %s first" % text
        return ({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": via, "input": {}}]}},
                {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                                                          "content": body}]}})

    def test_a_plugin_tool_the_product_directs_is_not_rescue_on_the_first_user_bar(self):
        tool = self.CATALOGUE_TOOLS + "bm_store.py init"
        rec, _c, _p = self.drive("FU-02", self.SATISFIED, first_tools=(tool,), heard=self.told("bm_store.py init"))
        self.assertEqual([], rec["agent_rescue"])
        self.assertIn("bm_fence_hook.py", rec["registered_hooks"])
        boundary, _c, _p = self.drive("FU-02", self.SATISFIED, first_user=False, first_tools=(tool,),
                                      heard=self.told("bm_store.py init"))
        self.assertEqual(["ran the plugin's hooks by hand to debug them"], boundary["agent_rescue"])

    def test_a_plugin_tool_nothing_in_the_product_directed_stays_rescue(self):
        tool = self.CATALOGUE_TOOLS + "bm_store.py init"
        rec, _c, _p = self.drive("FU-02", self.SATISFIED, first_tools=(tool,),
                                 heard=self.told("bm_store.py init", via="Bash"))
        self.assertEqual(["ran the plugin's hooks by hand to debug them"], rec["agent_rescue"])

    def test_evasions_of_the_ordinary_use_rule_stay_rescue(self):
        hooks, told = M.hook_scripts(M.ROOT), {"bm_store.py", "BM_FENCE_HOOK.py", "hook_guard.py", "helper.sh"}
        p = "/x/candidate-marketplace/bundle/runtime/hooks/brothermode/tools/"
        for cmd in ("nl %sbm_store.py" % p, "python3 -c \"print(open('%sbm_store.py').read())\"" % p,
                    "cp %sbm_store.py /tmp/x" % p, "python3 %sBM_FENCE_HOOK.py session-label" % p,
                    "python3 %sbm_store.py init && rm -rf s" % p, "python3 %sbm_store.py < %sbm_store.py" % (p, p),
                    "python3 %sbm_vault.py run" % p, "python3 %sbm_view.py && python3 \"$H/brother_hook\".py" % p,
                    "python3 %sbm_view.py $(true)" % p, "python3 %sbrother_hoo?.py" % p, "perl -pe1 %sbm_store.py" % p,
                    "zcat -f %sbm_store.py" % p, "ruby -pe1 %sbm_store.py" % p, "python3 -I -c \"print(1)\"",
                    "python3 -Ic x", "env X=1 python3 %sbm_store.py init" % p, "python3 %sBM_FENCE_HOOK.py" % p):
            with self.subTest(cmd=cmd):
                self.assertFalse(M.first_user_ordinary({"name": "Bash", "input": {"command": cmd}}, hooks, told))
        self.assertTrue(M.first_user_ordinary({"name": "Bash", "input": {"command": "python3 %sbm_store.py init 2>&1" % p}},
                                              hooks, told))
        src = os.path.join(M.ROOT, "bundle", "runtime", "hooks", "brothermode", "tools", "bm_store.py")
        link, folder = os.path.join(self.tmp, "page.md"), os.path.join(self.tmp, "folder.md")
        os.symlink(src, link)
        os.makedirs(folder)
        for target in (link, folder):
            with self.subTest(target=target):
                self.assertFalse(M.first_user_ordinary({"name": "Read", "input": {"file_path": target}}, hooks, told))

    def test_reading_plugin_source_through_the_tree_is_rescue_and_a_skill_page_is_not(self):
        src = os.path.join(M.ROOT, "bundle", "runtime", "hooks", "brothermode", "tools", "bm_store.py")
        rec, _c, _p = self.drive("FU-02", self.SATISFIED, first_tools=({"name": "Read", "input": {"file_path": src}},))
        self.assertEqual(["read plugin source: Read bm_store.py"], rec["agent_rescue"])
        page = os.path.join(M.ROOT, "bundle", "skills", "using-brother", "SKILL.md")
        rec, _c, _p = self.drive("FU-02", self.SATISFIED, first_tools=({"name": "Read", "input": {"file_path": page}},))
        self.assertEqual([], rec["agent_rescue"])

    def test_a_shell_read_of_plugin_source_stays_rescue(self):
        rec, _c, _p = self.drive("FU-02", self.SATISFIED,
                                 first_tools=(self.CATALOGUE_TOOLS.replace("python3", "cat") + "bm_store.py",))
        self.assertIn("read plugin source through the shell", rec["agent_rescue"])

    def test_a_second_command_on_a_new_line_is_never_excused(self):
        hooks, told = M.hook_scripts(M.ROOT), M.product_directions(M.ROOT, "")
        p = "/x/candidate-marketplace/bundle/runtime/hooks/brothermode/tools/"
        first = "python3 %sbm_view.py" % p
        self.assertTrue(M.first_user_ordinary({"name": "Bash", "input": {"command": first}}, hooks, told))
        for tail in ("\ncat %sbm_fence_hook.py" % p, "\npython3 /x/bundle/hooks/brother_hook.py Stop",
                     "\nrm -rf /x/home/.claude/brother", "\r\ncat %sbm_store.py" % p, "\n",
                     " %sbm_fence_hook.py" % p, " --file=%sbrother_hook.py" % p):
            with self.subTest(tail=tail):
                use = {"name": "Bash", "input": {"command": first + tail}}
                self.assertFalse(M.first_user_ordinary(use, hooks, told))

    def test_a_tool_a_shipped_page_names_is_excused_without_any_message(self):
        told = M.product_directions(M.ROOT, "")
        self.assertIn("bm_controller.py", told)
        use = {"name": "Bash", "input": {"command": "python3 /x/runtime/hooks/brothermode/tools/bm_controller.py status"}}
        self.assertTrue(M.first_user_ordinary(use, M.hook_scripts(M.ROOT), told))

    def test_a_hook_message_counts_only_as_the_clients_reply_to_an_edit(self):
        lines = lambda via: "\n".join(json.dumps(x) for x in self.told("zz_tool.py", via=via))
        self.assertIn("zz_tool.py", M.product_directions(M.ROOT, lines("Edit")))
        self.assertNotIn("zz_tool.py", M.product_directions(M.ROOT, lines("Bash")))
        self.assertNotIn("zz_tool.py", M.product_directions(M.ROOT, lines("Edit").replace("PreToolUse:Edit hook", "Stop said")))

    def test_an_unreadable_shipped_page_reads_no_directions(self):
        tree = os.path.join(self.tmp, "tree")
        fu_apply(tree, {"bundle/skills/a/SKILL.md": "run tool_a.py"})
        self.assertEqual({"tool_a.py"}, M.product_directions(tree, ""))
        with open(os.path.join(tree, "bundle", "skills", "a", "B.md"), "wb") as fh:
            fh.write(b"\xff\xfe tool_b.py")
        self.assertIsNone(M.product_directions(tree, ""))

    def test_an_unknown_hook_list_or_direction_set_makes_nothing_ordinary(self):
        self.assertIsNone(M.hook_scripts(self.tmp))
        self.assertIsNone(M.product_directions(self.tmp, ""))
        use = {"name": "Bash", "input": {"command": "python3 /p/hooks/x/bm_store.py init"}}
        self.assertFalse(M.first_user_ordinary(use, None, {"bm_store.py"}))
        self.assertFalse(M.first_user_ordinary(use, {"bm_fence_hook.py"}, None))
        self.assertTrue(M.first_user_ordinary(use, {"bm_fence_hook.py"}, {"bm_store.py"}))
        tree = os.path.join(self.tmp, "tree")
        fu_apply(tree, {"bundle/hooks/hooks.json": '{"command": "a_hook.py"}', "bundle/x/hooks.json": "{torn"})
        self.assertIsNone(M.hook_scripts(tree))

    def test_a_missing_boundary_freeze_refuses_the_first_user_bar(self):
        self.patched()
        self.assertEqual(0, self.run_main("first-user", "freeze")[0])
        saved = M.FROZEN_PATH
        self.addCleanup(setattr, M, "FROZEN_PATH", saved)
        M.FROZEN_PATH = os.path.join(self.tmp, "no-boundary-frozen.json")
        code, said = self.run_main("first-user", "check")
        self.assertEqual(1, code)
        self.assertIn("the boundary grader the first-user grader builds on changed after it was frozen", said)

    def test_the_release_gate_never_passes_a_failing_first_user_run(self):
        self.frozen_world()
        run = os.path.join(M.FIRST_USER_RUNS_DIR, "20261102T000000")
        lost = [fu_raw("X%03d" % i, ROW, "FU-01", final_acceptance={"exit": 1, "tail": "boom"}) for i in range(10)]
        shutil.copytree(self.run_for([r for r in self.rows if r != ROW], extra=lost), run)
        code, said = self.run_main("gate")
        self.assertEqual(1, code, said)
        self.assertTrue(said.startswith("FAIL SIMULATED first-user release grid"), said)

    def test_a_changed_boundary_grader_refuses_the_first_user_bar(self):
        self.patched()
        self.assertEqual(0, self.run_main("first-user", "freeze")[0])
        saved = M.FROZEN_PATH
        self.addCleanup(setattr, M, "FROZEN_PATH", saved)
        M.FROZEN_PATH = os.path.join(self.tmp, "boundary-frozen.json")
        M._write_json(M.FROZEN_PATH, dict(M._load_json(saved)[0], grader_sha="0" * 16))
        code, said = self.run_main("first-user", "check")
        self.assertEqual(1, code)
        self.assertIn("the boundary grader the first-user grader builds on changed after it was frozen", said)

    def test_the_release_gate_reads_the_first_user_bar(self):
        self.frozen_world()
        code, said = self.run_main("gate")
        self.assertEqual((2, True), (code, "no first-user run" in said), said)
        run = os.path.join(M.FIRST_USER_RUNS_DIR, "20261102T000000")
        shutil.copytree(self.run_for(self.rows), run)
        code, said = self.run_main("gate")
        self.assertEqual(0, code, said)
        self.assertIn("PASS SIMULATED first-user release grid (not human evidence): 22 PASS", said)

    def test_the_maintainers_product_settings_never_reach_the_persons_session(self):
        planted = {"BM_FENCE_MODE": "enforced", "BROTHERMODE_VAULT": "/v", "BROTHER_X": "1"}
        saved = {k: os.environ.get(k) for k in planted}
        os.environ.update(planted)
        try:
            rec, calls, _p = self.drive("FU-02", self.SATISFIED)
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.assertEqual([[]], [c["product_env"] for c in calls[:1]])
        self.assertTrue(set(planted) <= set(rec["stripped_env"]), rec["stripped_env"])
        self.assertEqual("plugin default (BM_FENCE_MODE unset)", rec["fence_mode"])

    def test_each_receipt_outcome_is_kept_and_an_unreadable_one_is_no_data(self):
        rec, _c, _p = self.drive("FU-02", self.SATISFIED, receipts={
            "runs/a/receipt/receipt.json": json.dumps({"verdict": "NOT DONE", "status": "NEEDS-REPAIR-ON-NEW-BASE",
                                                       "reason": "lane at <SCRATCH>/project could not open"}),
            "runs/b/receipt/receipt.json": "{torn",
            "runs/c/receipt/receipt.json": "[1, 2]"})
        got = {o["receipt"].split(os.sep)[-3]: o for o in rec["receipt_outcomes"]}
        self.assertEqual(["a", "b", "c"], sorted(got))
        self.assertEqual(("NOT DONE", "NEEDS-REPAIR-ON-NEW-BASE"), (got["a"]["verdict"], got["a"]["status"]))
        self.assertEqual("lane at <scratch>/project could not open", got["a"]["reason"])
        self.assertEqual((M.NODATA, M.NODATA), (got["b"]["verdict"], got["c"]["verdict"]))
        self.assertIn("unreadable receipt", got["b"]["why"])
        self.assertNotIn(self.tmp, json.dumps(rec["receipt_outcomes"]))

    def test_the_boundary_run_is_unchanged(self):
        rec, _c, prompts = self.drive("FU-16", [{"action": "stop", "reason": "rejected"}], first_user=False)
        self.assertNotIn('"rejected"', prompts[0])
        self.assertEqual(M.PERSON_SHA, rec["person_input_sha"]["persona"])
        self.assertEqual((False, "boundary"), (rec["person_accepted"], rec["bar"]))
        for key in ("person_rejected", "replies", "denials"):
            self.assertNotIn(key, rec)

    def run_world(self, fake_task=None, frozen=True, admission=None):
        """The first-user run command through main(), frozen bar in a throwaway folder, every door to the real
        world shut except the task function this test hands in."""
        self.patched()
        if frozen:
            self.assertEqual(0, self.run_main("first-user", "freeze")[0])
        fence = Fenced(self.tmp)
        fence.__enter__()
        self.addCleanup(fence.__exit__)
        saved = (M.client_version, M.load_admission)
        M.client_version = lambda: "test"
        M.load_admission = lambda *a, **k: (dict(admission or {}), "")
        self.addCleanup(setattr_all, M, ("client_version", "load_admission"), saved)
        if fake_task:
            M._login, M.run_task = (lambda: None), fake_task

    def fake_task(self, seen, crash=()):
        def task(scenario, participant, arm, model, login, out_dir, tree, person, ask, first_user):
            seen.append({"id": scenario["id"], "arm": arm, "first_user": first_user, "tree": tree})
            if scenario["id"] in crash:
                raise RuntimeError("boom")
            return fu_raw(participant, scenario["class"], scenario["id"], cell="%s:%s" % (scenario["class"], scenario["tier"]))
        return task

    def test_the_run_refuses_an_unfrozen_bar_before_any_session(self):
        self.run_world(frozen=False)
        code, said = self.run_main("first-user", "run", "--runs-dir", os.path.join(self.tmp, "out"))
        self.assertEqual(2, code)
        self.assertIn("NO-DATA first-user run: the first-user bar is not frozen", said)

    def test_the_run_plans_every_key_row_on_the_brother_arm_and_grades_its_own_records(self):
        seen = []
        self.run_world(self.fake_task(seen))
        out = os.path.join(self.tmp, "out")
        code, said = self.run_main("first-user", "run", "--all-rows", "--per-row", "2", "--jobs", "1", "--runs-dir", out)
        self.assertEqual(2, code, said)   # no admission record in the throwaway world: every row NO-DATA
        self.assertEqual(44, len(seen))
        self.assertEqual({("brother", True, M.ROOT)}, {(x["arm"], x["first_user"], x["tree"]) for x in seen})
        by_row = {}
        for x in seen:
            by_row.setdefault({s["id"]: s for s in fu_set()}[x["id"]]["class"], []).append(x["id"])
        self.assertEqual(sorted(self.rows), sorted(by_row))
        run = os.path.join(out, os.listdir(out)[0])
        doc = M._load_json(os.path.join(run, "records.json"))[0]
        self.assertEqual(("first-user", "SIMULATED", 44), (doc["bar"], doc["label"], len(doc["records"])))
        self.assertIn("NO-DATA SIMULATED first-user release grid (not human evidence): 0 PASS, 0 FAIL, 22 NO-DATA", said)

    def test_a_crashed_task_is_no_data_in_its_own_row_never_the_runs_crash(self):
        seen = []
        self.run_world(self.fake_task(seen, crash=("FU-02",)))
        out = os.path.join(self.tmp, "out")
        code, said = self.run_main("first-user", "run", "--all-rows", "--only", "FU-02,FU-03", "--per-row", "2",
                                   "--jobs", "1", "--runs-dir", out)
        self.assertEqual(2, code, said)
        doc = M._load_json(os.path.join(out, os.listdir(out)[0], "records.json"))[0]
        crashed = [r for r in doc["records"] if r.get("nodata")]
        self.assertEqual((2, 1), (len(doc["records"]), len(crashed)))
        self.assertEqual(("FU-02", "execution-outcome/deliver"), (crashed[0]["scenario_id"], crashed[0]["class"]))

    def test_by_default_no_session_is_spent_on_a_row_that_is_not_admitted(self):
        seen = []
        self.run_world(self.fake_task(seen), admission={ROW: "admitted", "loop-control/probe": "hidden"})
        code, said = self.run_main("first-user", "run", "--per-row", "3", "--jobs", "1", "--runs-dir",
                                   os.path.join(self.tmp, "out"))
        self.assertEqual(2, code, said)
        mine = {s["id"] for s in fu_set() if s["class"] == ROW}
        self.assertEqual((3, True), (len(seen), {x["id"] for x in seen} <= mine))
        self.assertIn("1 admitted and graded on their evidence, 21 NO-DATA (1 hidden, 20 of unknown admission) of 22", said)

    def test_an_unknown_scenario_refuses_the_whole_run(self):
        self.run_world()
        code, said = self.run_main("first-user", "run", "--only", "FU-02,FU-99", "--runs-dir", os.path.join(self.tmp, "o"))
        self.assertEqual(2, code)
        self.assertIn("NO-DATA first-user run: unknown scenario(s) FU-99", said)



def _first_user_mutations():
    """One mutation per guard SIM1.c adds: source text, inert text, and the test that must go red."""
    T = "FirstUserBar."
    shared = "    verdict = FAIL if problems or [p for p in freeze if p not in unfrozen] else NODATA if unfrozen else PASS\n"
    one_bad = T + "test_one_bad_property_is_named_alone"
    kinds = T + "test_each_required_kind_language_and_persona_is_needed_on_its_own"
    edited = T + "test_a_changed_rubric_scenario_or_grader_is_each_a_refusal"
    cmd_edit = T + "test_the_command_fails_when_the_rubric_is_edited_after_the_freeze"
    return [
        ("fu recovery open points", "    avail = sum(w for (_n, w), v in zip(BENEFIT_POINTS, benefit) if v is not None)\n",
         "    avail = sum(w for (_n, w) in BENEFIT_POINTS)\n", T + "test_an_uninterrupted_success_is_not_capped"),
        ("fu rejection earns acceptance", "        benefit[1], benefit[2] = (2 if rejected else 0), None\n",
         "        benefit[1], benefit[2] = 0, None\n", T + "test_a_correct_rejection_earns_the_acceptance_points"),
        ("fu rejection has no receipt", "        benefit[1], benefit[2] = (2 if rejected else 0), None\n",
         "        benefit[1], benefit[2] = (2 if rejected else 0), benefit[2]\n",
         T + "test_a_correct_rejection_earns_the_acceptance_points"),
        ("fu rejection needs its record",
         "        rejected = bool(rec.get(\"rejection_records\")) and bool(rec.get(\"person_rejected\"))\n",
         "        rejected = bool(rec.get(\"person_rejected\"))\n", T + "test_a_rejection_without_its_record_earns_nothing"),
        ("fu accepting a rejection", "        if rec.get(\"person_accepted\"):\n            correctness = FAIL\n",
         "        if False:\n            correctness = FAIL\n",
         T + "test_accepting_a_result_that_should_be_rejected_is_a_correctness_failure"),
        ("fu unrecorded rejection", "        if \"person_rejected\" not in rec:\n", "        if False:\n",
         T + "test_an_unrecorded_rejection_decision_is_no_data"),
        ("fu question closing step", "    return not _ACCEPT_ASK.search(last)\n", "    return True\n",
         T + "test_questions_are_counted_by_the_frozen_definition"),
        ("fu question mark ends the reply", "    if not text.endswith(\"?\"):\n        return False\n",
         "    if False:\n        return False\n", T + "test_questions_are_counted_by_the_frozen_definition"),
        ("fu texts override the count", "    if rec.get(\"replies\") is not None:\n", "    if False:\n",
         T + "test_questions_are_counted_by_the_frozen_definition"),
        ("fu share not mean", "    if out[\"share_product\"] >= COMPLETION_SHARE:\n",
         "    if out[\"benefit\"] >= PRODUCT_BAR and out[\"ease\"] >= PRODUCT_BAR:\n",
         T + "test_nine_of_ten_at_the_bar_is_product_grade_whatever_the_mean_says"),
        ("fu acceptable share not mean", "    if out[\"share_acceptable\"] >= COMPLETION_SHARE:\n",
         "    if out[\"benefit\"] >= ACCEPTABLE_BAR and out[\"ease\"] >= ACCEPTABLE_BAR:\n",
         T + "test_eight_of_ten_fails_even_when_the_mean_is_acceptable"),
        ("fu denominator keeps every attempt", "        return round(at / float(len(trials)), 4) if trials else None\n",
         "        return round(at / float(len(graded)), 4) if graded else None\n",
         T + "test_an_ungraded_attempt_stays_in_the_denominator_and_makes_the_cell_no_data"),
        ("fu abandoned is not at the bar",
         "        at = sum(1 for t in graded if t.get(\"completed\") and t.get(\"benefit_10\") is not None\n",
         "        at = sum(1 for t in graded if t.get(\"benefit_10\") is not None\n",
         T + "test_an_abandoned_attempt_is_never_at_the_bar"),
        ("fu hidden row", "    if state != \"admitted\":\n", "    if state == \"hidden\":\n",
         T + "test_a_hidden_or_unknown_row_is_never_a_pass"),
        ("fu no admission check", "    if state != \"admitted\":\n", "    if False:\n",
         T + "test_a_hidden_or_unknown_row_is_never_a_pass"),
        ("fu cell refusal", "    if reasons:\n        return dict(out, verdict=FAIL, reasons=reasons)\n",
         "    if False:\n        return dict(out, verdict=FAIL, reasons=reasons)\n",
         T + "test_a_security_or_correctness_failure_refuses_the_cell_whatever_the_share"),
        ("fu sources", "    if distinct_sources < FIRST_USER_MIN_PER_ROW:\n", "    if False:\n", T + "test_short_evidence_is_no_data"),
        ("fu attempts", "    if len(trials) < MIN_TRIALS_PER_CELL:\n", "    if False:\n", T + "test_short_evidence_is_no_data"),
        ("fu unknown security",
         "    if any(t.get(\"security\") not in (PASS, FAIL) for t in trials if t.get(\"graded\")):\n", "    if False:\n",
         T + "test_short_evidence_is_no_data"),
        ("fu grid needs every row",
         "    verdict = PASS if verdicts == {PASS} else (FAIL if FAIL in verdicts else NODATA)\n    grade_ = None\n",
         "    verdict = PASS if PASS in verdicts and FAIL not in verdicts else (FAIL if FAIL in verdicts else NODATA)\n"
         "    grade_ = None\n", T + "test_the_release_needs_every_key_row"),
        ("fu row coverage", "        if len({s.get(\"seed_id\") for s in mine}) < FIRST_USER_MIN_PER_ROW:\n", "        if False:\n",
         T + "test_a_key_row_with_no_first_user_scenario_is_refused"),
        ("fu tier mix", "        if abs(have - want) > FIRST_USER_TOLERANCE:\n", "        if False:\n",
         T + "test_the_tier_mix_is_about_45_45_10"),
        ("fu required kinds", "    for kind in FIRST_USER_REQUIRED_KINDS:\n", "    for kind in ():\n", kinds),
        ("fu languages", "    if len({s.get(\"language\") for s in ok}) < 2:\n", "    if False:\n", kinds),
        ("fu non developer", "    if not any((s.get(\"persona\") or {}).get(\"non_developer\") for s in ok):\n",
         "    if False:\n", kinds),
        ("fu novice", "    if not any((s.get(\"persona\") or {}).get(\"skill_level\") == \"novice\" for s in ok):\n",
         "    if False:\n", kinds),
        ("fu interruptions",
         "    if sum(1 for s in ok if s.get(\"interruption\")) > FIRST_USER_MAX_INTERRUPTED * len(ok):\n", "    if False:\n",
         T + "test_scripted_interruptions_stay_the_exception"),
        ("fu excluded kinds",
         "    if s.get(\"kind\") in FIRST_USER_EXCLUDED_KINDS or s.get(\"kind\") not in FIRST_USER_KINDS:\n",
         "    if False:\n", one_bad),
        ("fu cold",
         "        giveaway = _first_user_reveals(text, s.get(\"kind\")) if field not in (\"answer_if_asked\", \"accept_message\") else []\n",
         "        giveaway = []\n", one_bad),
        ("fu pasted error",
         "    return [t for t in told if t not in PASTED_ERROR_OK] if kind == \"traceback\" else told\n", "    return told\n",
         T + "test_a_pasted_error_may_name_files_but_no_other_kind_may"),
        ("fu public source",
         "        if not isinstance(seed, dict) or not str(seed.get(\"url\") or \"\").startswith(\"https://\"):\n",
         "        if False:\n", one_bad),
        ("fu reason cites no result", "        hit = RESULT_CITING.search(reason)\n", "        hit = None\n",
         T + "test_one_broken_rule_is_named_alone"),
        ("fu reason present", "        if len(reason.split()) < 12:\n", "        if False:\n",
         T + "test_one_broken_rule_is_named_alone"),
        ("fu rule present once", "        if len(found) != 1:\n", "        if len(found) > 1:\n",
         T + "test_one_broken_rule_is_named_alone"),
        ("fu freeze rubric", "    if doc.get(\"rubric_sha\") != _sha_file(rubric_path or FIRST_USER_RUBRIC_PATH):\n",
         "    if False:\n", edited),
        ("fu freeze scenarios",
         "    if doc.get(\"scenarios_sha\") != _sha_file(scenarios_path or FIRST_USER_SCENARIOS_PATH):\n",
         "    if False:\n", edited),
        ("fu freeze grader", "    if doc.get(\"grader_sha\") != first_user_grader_sha(rubric_path):\n", "    if False:\n", edited),
        ("fu freeze after a run", "    if first_user_scored_runs(runs_dir):\n", "    if False:\n",
         T + "test_a_freeze_is_refused_once_a_scored_run_exists"),
        ("fu freeze valid only", "    if bad:\n        raise RuntimeError(\"the first-user bar is not valid",
         "    if False:\n        raise RuntimeError(\"the first-user bar is not valid",
         T + "test_an_invalid_set_or_rubric_cannot_be_frozen"),
        ("fu command wiring", "    if args.cmd == \"first-user\":\n        return cmd_first_user(args)\n",
         "    if args.cmd == \"first-user\":\n        return 0\n", cmd_edit),
        ("fu command freeze departure", shared, "    verdict = FAIL if problems else NODATA if unfrozen else PASS\n", cmd_edit),
        ("fu command set problems", shared,
         "    verdict = FAIL if [p for p in freeze if p not in unfrozen] else NODATA if unfrozen else PASS\n",
         T + "test_the_command_fails_when_a_row_has_no_scenario"),
        ("fu command refreeze exit",
         "            out.write(\"%s first-user freeze: %s\\n\" % (NODATA, exc))\n            return 2\n",
         "            out.write(\"%s first-user freeze: %s\\n\" % (NODATA, exc))\n            return 0\n",
         T + "test_the_command_refuses_to_refreeze_after_a_scored_run"),
        ("fu report needs the freeze", "    if stale or lost or not isinstance(doc, dict):\n", "    if lost or not isinstance(doc, dict):\n",
         T + "test_the_report_refuses_an_unfrozen_bar"),
        ("fu report names every record", "    if stale or lost or not isinstance(doc, dict):\n", "    if stale or not isinstance(doc, dict):\n",
         T + "test_the_report_refuses_a_record_that_names_no_key_row"),
        ("fu report reads the run", "    if stale or lost or not isinstance(doc, dict):\n", "    if stale or lost:\n",
         T + "test_the_report_reads_a_missing_run_as_no_data"),
        ("fu report expected decision",
         "        return grade_first_user(dict(rec, expected_decision=expected.get(rec.get(\"scenario_id\"))), {})\n",
         "        return grade_first_user(rec, {})\n",
         T + "test_the_report_passes_a_run_that_meets_the_bar_on_every_key_row"),
        ("fu ungraded keeps its row", "        return dict(base, bar=BAR_FIRST_USER, simulated=True, cell=first_user_cell(rec))\n",
         "        return dict(base, bar=BAR_FIRST_USER, simulated=True)\n",
         T + "test_the_report_keeps_an_ungraded_attempt_in_its_row"),
        ("fu report wiring", "        return EXIT[first_user_report(args.runs or latest_run(FIRST_USER_RUNS_DIR), out)]\n",
         "        return 0\n", T + "test_the_report_fails_the_release_when_one_row_fails"),
        ("fu report keeps unreadable records", "    except (KeyError, TypeError, ValueError, AttributeError) as exc:\n        return {\"participant\"",
         "    except ZeroDivisionError as exc:\n        return {\"participant\"",
         T + "test_the_report_reads_an_unreadable_record_as_no_data_in_its_own_row"),
        ("fu any trace of a run", "    return sorted(glob.glob(os.path.join(runs_dir or FIRST_USER_RUNS_DIR, \"*\")))\n",
         "    return sorted(glob.glob(os.path.join(runs_dir or FIRST_USER_RUNS_DIR, \"*\", \"records.json\")))\n",
         T + "test_any_trace_of_a_run_keeps_the_bar_frozen"),
        ("fu class must be text", "    if not isinstance(s.get(\"class\"), str) or s.get(\"class\") not in rows:\n",
         "    if s.get(\"class\") not in rows:\n", T + "test_malformed_scenarios_and_rules_are_named_not_crashed_on"),
        ("fu command unreadable set",
         "    if not scenarios:\n        out.write(\"%s first-user check: %s\\n\" % (NODATA, why))\n        return 2\n",
         "    if not scenarios:\n        out.write(\"%s first-user check: %s\\n\" % (NODATA, why))\n        return 0\n",
         T + "test_the_command_reads_no_data_for_an_unreadable_set"),
    ]


MUTATIONS.extend(_first_user_mutations())


def _first_user_run_mutations():
    """One mutation per guard the first-user run wiring adds."""
    T = "FirstUserBar."
    return [
        ("fu run cuts only an interruption",
         "cut_after_edit=cut_at if scenario.get(\"interruption\") else None)\n", "cut_after_edit=cut_at)\n",
         T + "test_a_scenario_with_no_interruption_is_never_cut_and_runs_to_its_end"),
        ("fu run deadline is not an interruption",
         "        rec[\"interrupted\"] = first[\"cut_by_deadline\"] and bool(scenario.get(\"interruption\"))\n",
         "        rec[\"interrupted\"] = first[\"cut_by_deadline\"]\n",
         T + "test_a_first_turn_stopped_by_its_deadline_is_abandonment_not_an_interruption"),
        ("fu run deadline is abandonment", "            or rec[\"first_turn_timed_out\"]\n", "            or False\n",
         T + "test_a_first_turn_stopped_by_its_deadline_is_abandonment_not_an_interruption"),
        ("fu run person may reject", "    if obj.get(\"action\") == \"stop\" and obj.get(\"reason\") in also:\n",
         "    if False:\n", T + "test_the_person_can_reject_and_the_rejection_is_recorded_apart_from_acceptances"),
        ("fu run reject brief",
         "            prompt = (FIRST_USER_PERSON_BRIEF if first_user else PERSON_BRIEF) % (",
         "            prompt = (PERSON_BRIEF) % (",
         T + "test_the_person_can_reject_and_the_rejection_is_recorded_apart_from_acceptances"),
        ("fu run boundary brief",
         "            prompt = (FIRST_USER_PERSON_BRIEF if first_user else PERSON_BRIEF) % (",
         "            prompt = (FIRST_USER_PERSON_BRIEF) % (", T + "test_the_boundary_run_is_unchanged"),
        ("fu run rejection decision", "            rec[\"person_rejected\"] = last.get(\"reason\") == \"rejected\"\n",
         "            rec[\"person_rejected\"] = False\n",
         T + "test_the_person_can_reject_and_the_rejection_is_recorded_apart_from_acceptances"),
        ("fu run rejection told", "            if rec[\"person_rejected\"] and time.time() - t0 < TASK_CAP_S:\n",
         "            if False:\n", T + "test_the_person_can_reject_and_the_rejection_is_recorded_apart_from_acceptances"),
        ("fu run rejection is not acceptance", "if verdicts[p] not in (None, \"rejected\")]\n", "if verdicts[p] != 1]\n",
         T + "test_the_person_can_reject_and_the_rejection_is_recorded_apart_from_acceptances"),
        ("fu run unreadable answer", "if verdicts[p] not in (None, \"rejected\")]\n", "if verdicts[p] != \"rejected\"]\n",
         T + "test_an_acceptance_is_counted_and_an_unreadable_answer_is_neither"),
        ("fu run rejection records", "for p in answers if verdicts[p] == \"rejected\"]\n", "for p in answers if False]\n",
         T + "test_the_person_can_reject_and_the_rejection_is_recorded_apart_from_acceptances"),
        ("fu run keeps replies", "            rec[\"replies\"] = [redact((t[\"result\"] or \"\")[-2000:]) for t in turns]\n",
         "            rec[\"replies\"] = []\n", T + "test_the_run_keeps_every_reply_text_and_denial_for_the_question_count"),
        ("fu run first-user record only", "        if first_user:\n            #: the reply texts",
         "        if True:\n            #: the reply texts", T + "test_the_boundary_run_is_unchanged"),
        ("fu run needs the freeze", "    stale = first_user_freeze_problems()\n    if stale:\n        out.write(\"%s first-user run",
         "    stale = first_user_freeze_problems()\n    if False:\n        out.write(\"%s first-user run",
         T + "test_the_run_refuses_an_unfrozen_bar_before_any_session"),
        ("fu run unknown scenario", "    unknown = [x for x in only if x not in by_id]\n", "    unknown = []\n",
         T + "test_an_unknown_scenario_refuses_the_whole_run"),
        ("fu run first-user task", "args.tree or ROOT, args.person, None, True): p for p in plan}",
         "args.tree or ROOT, args.person, None, False): p for p in plan}",
         T + "test_the_run_plans_every_key_row_on_the_brother_arm_and_grades_its_own_records"),
        ("fu run every row", "        plan += [{\"cell\": row, \"scenario\": ids[i % len(ids)]} for i in range(per_row if ids else 0)]\n",
         "        plan += [{\"cell\": row, \"scenario\": ids[i % len(ids)]} for i in range(min(1, per_row) if ids else 0)]\n",
         T + "test_the_run_plans_every_key_row_on_the_brother_arm_and_grades_its_own_records"),
        ("fu run crash is a row", "            except Exception as exc:   # one broken task is that attempt's",
         "            except ZeroDivisionError as exc:   # one broken task is that attempt's",
         T + "test_a_crashed_task_is_no_data_in_its_own_row_never_the_runs_crash"),
        ("run rescue sees the catalogue", "            plugin_roots = [install[\"installed_root\"], config, os.path.join(scratch, \"candidate-marketplace\"),\n",
         "            plugin_roots = [install[\"installed_root\"], config, config,\n",
         T + "test_a_hook_run_by_hand_from_the_candidate_catalogue_is_rescue"),
        ("fu freeze boundary grader", "    if not isinstance(base, dict) or base.get(\"grader_sha\") != grader_sha():\n", "    if False:\n",
         T + "test_a_changed_boundary_grader_refuses_the_first_user_bar"),
        ("fu gate reads the release bar",
         "first_user_release_row() if RELEASE_BAR == BAR_FIRST_USER else release_bar_row()", "release_bar_row()",
         T + "test_the_release_gate_reads_the_first_user_bar"),
        ("fu gate no run", "    if not run_dir:\n        return NODATA, \"%s first-user release grid: no first-user run",
         "    if False:\n        return NODATA, \"%s first-user release grid: no first-user run",
         T + "test_the_release_gate_reads_the_first_user_bar"),
        ("fu rescue hooks stay rescue",
         "    return script in {d.lower() for d in directed} and script not in {h.lower() for h in hooks}\n",
         "    return script in {d.lower() for d in directed}\n", T + "test_a_hook_run_by_hand_from_the_candidate_catalogue_is_rescue"),
        ("fu directed only", "    return script in {d.lower() for d in directed} and script not in {h.lower() for h in hooks}\n",
         "    return script not in {h.lower() for h in hooks}\n", T + "test_a_plugin_tool_nothing_in_the_product_directed_stays_rescue"),
        ("fu directions from hook messages", "                if _FU_HOOK_REPLY.match(text):\n", "                if False:\n",
         T + "test_a_plugin_tool_the_product_directs_is_not_rescue_on_the_first_user_bar"),
        ("fu directions only as the hook's wording", "                if _FU_HOOK_REPLY.match(text):\n", "                if True:\n",
         T + "test_a_hook_message_counts_only_as_the_clients_reply_to_an_edit"),
        ("fu directions not forgeable", "                  and called.get(block.get(\"tool_use_id\")) in _FU_EDIT_TOOLS):\n",
         "                  ):\n", T + "test_a_plugin_tool_nothing_in_the_product_directed_stays_rescue"),
        ("fu pages direct", "                names |= set(_FU_SCRIPT.findall(fh.read()))\n", "                fh.read()\n",
         T + "test_a_tool_a_shipped_page_names_is_excused_without_any_message"),
        ("fu page decode", "        except (OSError, ValueError):\n            return None\n    called = {}",
         "        except (OSError, ValueError):\n            continue\n    called = {}", T + "test_an_unreadable_shipped_page_reads_no_directions"),
        ("fu whole command shape", "(?:[ \\t]+2>&1)?[ \\t]*\\Z\")", "(?:[ \\t]+2>&1)?[ \\t]*\")",
         T + "test_evasions_of_the_ordinary_use_rule_stay_rescue"),
        ("fu one script per command", "    if not run or len(_FU_SCRIPT.findall(cmd)) != 1:\n", "    if not run:\n",
         T + "test_a_second_command_on_a_new_line_is_never_excused"),
        ("fu no newline as a gap", "(?:[ \\t]+[A-Za-z0-9_./=:,@%+-]+)*(?:[ \\t]+2>&1)?",
         "(?:\\s+[A-Za-z0-9_./=:,@%+-]+)*(?:[ \\t]+2>&1)?", T + "test_a_second_command_on_a_new_line_is_never_excused"),
        ("fu md not a link", "        return target.endswith(\".md\") and not os.path.islink(target) and os.path.isfile(target)\n",
         "        return target.endswith(\".md\") and os.path.exists(target)\n", T + "test_evasions_of_the_ordinary_use_rule_stay_rescue"),
        ("fu md a page", "        return target.endswith(\".md\") and not os.path.islink(target) and os.path.isfile(target)\n",
         "        return True\n", T + "test_reading_plugin_source_through_the_tree_is_rescue_and_a_skill_page_is_not"),
        ("fu rescue unknown hooks", "    if hooks is None or directed is None:\n", "    if directed is None:\n",
         T + "test_an_unknown_hook_list_or_direction_set_makes_nothing_ordinary"),
        ("fu unknown directions", "    if hooks is None or directed is None:\n", "    if hooks is None:\n",
         T + "test_an_unknown_hook_list_or_direction_set_makes_nothing_ordinary"),
        ("fu pages unreadable", "    if not pages:\n        return None\n", "    if False:\n        return None\n",
         T + "test_an_unknown_hook_list_or_direction_set_makes_nothing_ordinary"),
        ("fu corrupt hooks list", "        except (OSError, ValueError):\n            return None\n        names |= set(re.findall(",
         "        except (OSError, ValueError):\n            continue\n        names |= set(re.findall(",
         T + "test_an_unknown_hook_list_or_direction_set_makes_nothing_ordinary"),
        ("fu missing boundary freeze", "    if not isinstance(base, dict) or base.get(\"grader_sha\") != grader_sha():\n",
         "    if isinstance(base, dict) and base.get(\"grader_sha\") != grader_sha():\n",
         T + "test_a_missing_boundary_freeze_refuses_the_first_user_bar"),
        ("fu gate keeps the report verdict", "    verdict = first_user_report(run_dir, sink)\n", "    verdict = first_user_report(run_dir, sink) and PASS\n",
         T + "test_the_release_gate_never_passes_a_failing_first_user_run"),
        ("fu rescue first-user only",
         "        counted = [u for u in uses if not first_user_ordinary(u, hooks, directed)] if first_user else uses\n",
         "        counted = [u for u in uses if not first_user_ordinary(u, hook_scripts(tree), product_directions(tree, said))]\n",
         T + "test_a_plugin_tool_the_product_directs_is_not_rescue_on_the_first_user_bar"),
        ("fu rescue applied", "        rec[\"agent_rescue\"] = agent_rescue(counted,", "        rec[\"agent_rescue\"] = agent_rescue(uses,",
         T + "test_a_plugin_tool_the_product_directs_is_not_rescue_on_the_first_user_bar"),
        ("fu rescue tree bundle root", "                            os.path.join(tree, \"bundle\")]\n", "                            config]\n",
         T + "test_reading_plugin_source_through_the_tree_is_rescue_and_a_skill_page_is_not"),
        ("run strips through stranger_env", "        env_base, stripped = rj.stranger_env(CLAUDE_CONFIG_DIR=config, HOME=home)\n",
         "        env_base, stripped = dict(os.environ, CLAUDE_CONFIG_DIR=config, HOME=home), []\n",
         T + "test_the_maintainers_product_settings_never_reach_the_persons_session"),
        ("run keeps an unreadable receipt", "                rec[\"receipt_outcomes\"].append(dict(entry, verdict=NODATA, why=\"unreadable receipt: %s\" % str(exc)[:120]))\n",
         "                pass\n", T + "test_each_receipt_outcome_is_kept_and_an_unreadable_one_is_no_data"),
        ("run receipt reason scrubbed", "                value = redact(str(doc.get(key) or \"\").replace(scratch, \"<scratch>\"))[:300]\n",
         "                value = str(doc.get(key) or \"\")[:300]\n", T + "test_each_receipt_outcome_is_kept_and_an_unreadable_one_is_no_data"),
        ("fu run grades its run", "    return EXIT[first_user_report(run_dir, out)]\n", "    return 0\n",
         T + "test_the_run_plans_every_key_row_on_the_brother_arm_and_grades_its_own_records"),
        ("fu admission hidden group", "            out[r[\"id\"]] = \"hidden\" if g in queued else \"admitted\"\n",
         "            out[r[\"id\"]] = \"admitted\"\n", T + "test_a_hidden_groups_row_never_grades"),
        ("fu admission unknown group", "        if isinstance(g, str) and g in groups:\n", "        if isinstance(g, str):\n",
         T + "test_a_row_whose_group_the_record_does_not_name_reads_no_data"),
        ("fu admission unreadable registry", "    if \"*\" in queued:\n        return {}\n", "    if False:\n        return {}\n",
         T + "test_a_row_is_admitted_only_when_its_group_is_admitted"),
        ("fu admission empty record", "        if not isinstance(entries, dict) or not entries:\n", "        if False:\n",
         T + "test_an_unreadable_record_admits_nothing"),
        ("fu admission reads the door", "        view = door.consumer_admission(registry, cand, host, path)\n",
         "        view = {\"queue\": []}\n", T + "test_a_hidden_groups_row_never_grades"),
        ("fu admission candidate", "        cand = candidate or (head.stdout.strip() if head.returncode == 0 else None)\n",
         "        cand = candidate\n", T + "test_an_admitted_group_admits_its_rows_through_the_door"),
        ("fu run admitted rows only",
         "    rows = rows if args.all_rows else [r for r in rows if admission.get(r) == \"admitted\"]\n", "    rows = rows\n",
         T + "test_by_default_no_session_is_spent_on_a_row_that_is_not_admitted"),
        ("fu admission summary counts", "    n = {s: sum(1 for r in rows if table.get(r) == s) for s in (\"admitted\", \"hidden\")}\n",
         "    n = {s: sum(1 for r in rows if True) for s in (\"admitted\", \"hidden\")}\n",
         T + "test_a_hidden_groups_row_never_grades"),
    ]


MUTATIONS.extend(_first_user_run_mutations())


if __name__ == "__main__":
    unittest.main(verbosity=1)
