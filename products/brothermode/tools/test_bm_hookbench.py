#!/usr/bin/env python3
"""Regression tests for tools/bm_hookbench.py, the hook cost benchmark, and
for docs/PERFORMANCE.md, the page it generates.
Standard library only. Run: python3 tools/test_bm_hookbench.py

Same discipline as tools/test_bm_fence_hook.py: every test is self contained
under tempfile.TemporaryDirectory(), and nothing here touches this repository's
own store, the founder's home, or the real vault.

WHAT THIS SUITE IS ACTUALLY DEFENDING
  A measurement tool is a strange thing to test, because the numbers it
  produces are supposed to change. So none of these tests assert a latency.
  They assert the things that make a latency believable:

    1. the chain it measures is READ from hooks/hooks.json, so the page cannot
       describe a hook wiring the runtime no longer has;
    2. a program the parser cannot read is a REFUSAL, never a shorter chain
       quietly reported as the whole cost;
    3. the sandbox is proved to make the fence do real work before a number is
       taken, because a benchmark against a fence that silently fails open
       reports the cost of doing nothing and looks like good news;
    4. the page is a PURE FUNCTION of the record printed inside it, so a number
       typed in by hand cannot survive;
    5. every NOT MEASURED entry keeps its reason and gains no estimate;
    6. the tool spawns no process, which is the constraint that shaped the
       whole design and the one a future edit is most likely to break.
"""
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL_PATH = os.path.join(HERE, "bm_hookbench.py")
PAGE_PATH = os.path.join(ROOT, "docs", "PERFORMANCE.md")
HOOKS_PATH = os.path.join(ROOT, "hooks", "hooks.json")

_spec = importlib.util.spec_from_file_location("bm_hookbench", TOOL_PATH)
hb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hb)
sys.modules["bm_hookbench"] = hb


def read(path):
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


def run_cli(*args):
    return subprocess.run([sys.executable, TOOL_PATH] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, cwd=ROOT)


def fake_record():
    """A tiny record with the shape render() consumes, so the page tests do
    not have to run a real measurement to exercise the renderer."""
    timing = {"samples": 3, "median_ms": 10.0, "min_ms": 9.0, "p90_ms": 11.0,
              "max_ms": 12.0, "spread_factor": 1.2}
    return {
        "generator": "tools/bm_hookbench.py",
        "measured_local_date": "2026-01-02",
        "repetitions": 3,
        "warmup_repetitions": 1,
        "reproducibility_factor": 2.0,
        "machine": {"platform": "demo", "machine": "demo64", "cpu_count": 2,
                    "python_implementation": "CPython",
                    "python_version": "3.9.0",
                    "load_average_at_start": [0.1, 0.2, 0.3]},
        "manifest": [{"event": "Stop", "matcher": "", "timeout_seconds": 30,
                      "programs": ["bm_view.py"]}],
        "not_measured": [{"topic": "a topic", "reason": "a reason"}],
        "actions": [{
            "action": "One demo action",
            "events": [{"event": "Stop", "tool_name": None}],
            "program_count": 1,
            "programs": [{"event": "Stop", "program": "bm_view.py",
                          "args": ["alert", "--tick"], "timeout_seconds": 30,
                          "timing": dict(timing)}],
            "chain": dict(timing),
            "sum_of_program_medians_ms": 10.0,
            "shared_timeout_seconds": [30],
            "chain_share_of_smallest_budget_percent": 0.033,
            "calls_counted": 3,
            "non_zero_exits": 0,
            "fail_open_lines": 0,
            "crashes": [],
            "exit_codes_seen": [0],
        }],
        "unrunnable": [],
    }


# ---------------------------------------------------------------------------
# 1. The chain is read, not typed.
# ---------------------------------------------------------------------------

class TestTheManifestIsRead(unittest.TestCase):

    def test_the_real_manifest_parses_into_every_event_it_declares(self):
        declared = set(json.loads(read(HOOKS_PATH))["hooks"])
        parsed = set(group["event"] for group in hb.read_hook_chains())
        self.assertEqual(parsed, declared,
                         "the parser and hooks/hooks.json disagree about which "
                         "events are wired")

    def test_the_stop_event_chains_four_programs_under_one_shared_timeout(self):
        """The review's specific complaint, checked against the file rather
        than against anybody's memory of it: a Stop event runs several Python
        programs inside ONE shell command, so they share one budget."""
        stop = [g for g in hb.read_hook_chains() if g["event"] == "Stop"]
        self.assertEqual(len(stop), 1,
                         "Stop is wired as %d commands; this test and the page "
                         "both assume one" % len(stop))
        self.assertGreaterEqual(
            len(stop[0]["programs"]), 2,
            "Stop no longer chains multiple programs, so the shared budget "
            "sentence on docs/PERFORMANCE.md needs rewriting")
        self.assertIsNotNone(stop[0]["timeout_seconds"],
                             "the Stop chain declares no timeout at all")

    def test_a_program_added_to_a_manifest_reaches_the_parse(self):
        """Proves the inventory is READ. A typed list would pass every test
        above and fail this one."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "hooks.json")
            with io.open(path, "w", encoding="utf-8") as handle:
                json.dump({"hooks": {"Stop": [{"hooks": [{
                    "type": "command",
                    "command": ("sh -c 'p=$(cat); printf %s \"$p\" | python3 "
                                "\"${CLAUDE_PLUGIN_ROOT}/tools/bm_alpha.py\" "
                                "one; printf %s \"$p\" | python3 "
                                "\"${CLAUDE_PLUGIN_ROOT}/tools/bm_beta.py\" "
                                "two --three'"),
                    "timeout": 7}]}]}}, handle)
            groups = hb.read_hook_chains(path)
        self.assertEqual([g["event"] for g in groups], ["Stop"])
        self.assertEqual([p["program"] for p in groups[0]["programs"]],
                         ["bm_alpha.py", "bm_beta.py"])
        self.assertEqual(groups[0]["programs"][1]["args"], ["two", "--three"])
        self.assertEqual(groups[0]["timeout_seconds"], 7)

    def test_a_command_the_parser_cannot_read_is_refused(self):
        """The failure that matters: a parser that misses one program of four
        publishes a chain cost that is a quarter too cheap and looks fine."""
        with self.assertRaises(hb.BenchError) as caught:
            hb.parse_command("bash -lc 'exec tools/bm_ghost.py --tick'")
        self.assertIn("bm_ghost.py", str(caught.exception))

    def test_a_shell_program_is_named_and_marked_unrunnable(self):
        """No hook is wired this way any more (tools/test_bm_hooks.py
        refuses one), and the parser keeps handling the shape anyway: a
        shell script it could not name at all would be a chain reported
        shorter than the one the runtime runs."""
        programs = hb.parse_command(
            'sh "${CLAUDE_PLUGIN_ROOT}/tools/bm_legacy_shell.sh"')
        self.assertEqual(len(programs), 1)
        self.assertFalse(programs[0]["runnable"])
        self.assertTrue(programs[0]["reason"].strip())

    def test_the_chain_driver_is_expanded_into_the_programs_it_runs(self):
        """The port's load-bearing parser change. A command naming the
        driver must report the driver's PROGRAMS, or every total on
        docs/PERFORMANCE.md silently becomes the cost of one process where
        four run, which is the exact understatement this file exists to
        prevent."""
        programs = hb.parse_command(
            'python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_hookchain.py" stop')
        self.assertEqual([p["program"] for p in programs],
                         [module for module, _args in hb.CHAINS["stop"]])
        self.assertTrue(all(p["runnable"] for p in programs))

    def test_an_undefined_chain_name_is_refused_rather_than_measured(self):
        with self.assertRaises(hb.BenchError) as caught:
            hb.parse_command(
                'python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_hookchain.py" ghost')
        self.assertIn("ghost", str(caught.exception))

    def test_the_matcher_is_a_regex_and_not_a_substring(self):
        """docs/HOOKS.md records that this project was already bitten once by
        the difference: Edit is a substring of NotebookEdit."""
        chains = [
            {"event": "PreToolUse", "matcher": "Edit|Write", "timeout_seconds": 10,
             "programs": []},
            {"event": "PreToolUse", "matcher": "Bash", "timeout_seconds": 10,
             "programs": []},
        ]
        self.assertEqual(len(hb.groups_for(chains, "PreToolUse", "Edit")), 1)
        self.assertEqual(len(hb.groups_for(chains, "PreToolUse", "Bash")), 1)
        self.assertEqual(len(hb.groups_for(chains, "PreToolUse", "Read")), 0)

    def test_a_broken_matcher_is_refused_rather_than_ignored(self):
        chains = [{"event": "PreToolUse", "matcher": "Edit(", "programs": [],
                   "timeout_seconds": 10}]
        with self.assertRaises(hb.BenchError):
            hb.groups_for(chains, "PreToolUse", "Edit")

    def test_a_manifest_with_no_hooks_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "hooks.json")
            with io.open(path, "w", encoding="utf-8") as handle:
                handle.write("{}")
            with self.assertRaises(hb.BenchError):
                hb.read_hook_chains(path)


# ---------------------------------------------------------------------------
# 2. The payloads.
# ---------------------------------------------------------------------------

class TestThePayloads(unittest.TestCase):
    """The shape comes from docs/HOOKS.md, which documents the PreToolUse
    object field by field. A payload missing session_id or tool_input sends
    the fence hook straight down its fail open path, and the benchmark would
    then measure a hook that checked nothing."""

    def _payload(self, event, tool):
        return json.loads(hb.payload_for(event, tool, "sid", "/p", "/t.jsonl",
                                         "/p/src/thing.py"))

    def test_a_pretooluse_payload_carries_the_documented_fields(self):
        payload = self._payload("PreToolUse", "Edit")
        for key in ("session_id", "transcript_path", "cwd", "permission_mode",
                    "hook_event_name", "tool_name", "tool_input",
                    "tool_use_id"):
            self.assertIn(key, payload)
        self.assertIn("file_path", payload["tool_input"])

    def test_a_bash_payload_carries_a_command_not_a_path(self):
        payload = self._payload("PreToolUse", "Bash")
        self.assertIn("command", payload["tool_input"])
        self.assertNotIn("file_path", payload["tool_input"])

    def test_a_stop_payload_names_the_transcript_the_stop_hook_reads(self):
        payload = self._payload("Stop", None)
        self.assertEqual(payload["hook_event_name"], "Stop")
        self.assertTrue(payload["transcript_path"])
        self.assertIn("stop_hook_active", payload)


# ---------------------------------------------------------------------------
# 3. The statistics.
# ---------------------------------------------------------------------------

class TestTheStatistics(unittest.TestCase):

    def test_summarize_reports_a_median_and_a_spread(self):
        got = hb.summarize([0.001, 0.002, 0.003, 0.004, 0.005])
        self.assertEqual(got["samples"], 5)
        self.assertEqual(got["median_ms"], 3.0)
        self.assertEqual(got["min_ms"], 1.0)
        self.assertEqual(got["max_ms"], 5.0)
        self.assertEqual(got["p90_ms"], 5.0)

    def test_percentile_is_nearest_rank(self):
        values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        self.assertEqual(hb.percentile(values, 0.0), 1)
        self.assertEqual(hb.percentile(values, 1.0), 10)
        self.assertEqual(hb.percentile(values, 0.9), 9)

    def test_an_empty_sample_set_refuses_rather_than_returning_zero(self):
        with self.assertRaises(hb.BenchError):
            hb.summarize([])
        with self.assertRaises(hb.BenchError):
            hb.percentile([], 0.5)


# ---------------------------------------------------------------------------
# 4. The runner.
# ---------------------------------------------------------------------------

class TestTheRunner(unittest.TestCase):

    def _script(self, d, body, name="prog.py"):
        path = os.path.join(d, name)
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
        return path

    def test_a_program_is_fed_the_payload_and_its_arguments(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._script(d, "import json, sys\n"
                                   "print(json.load(sys.stdin)['cwd'])\n"
                                   "print(' '.join(sys.argv[1:]))\n")
            got = hb.run_program(path, ["one", "--two"],
                                 json.dumps({"cwd": "/somewhere"}))
        self.assertEqual(got["exit_code"], 0)
        self.assertIn("/somewhere", got["stdout"])
        self.assertIn("one --two", got["stdout"])

    def test_a_non_zero_exit_is_reported_not_swallowed(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._script(d, "import sys\nsys.exit(3)\n")
            got = hb.run_program(path, [], "")
        self.assertEqual(got["exit_code"], 3)

    def test_a_crash_is_a_result_rather_than_an_exception(self):
        """A hook that blows up must be COUNTED, not allowed to abort the run
        and take every other measurement with it."""
        with tempfile.TemporaryDirectory() as d:
            path = self._script(d, "raise RuntimeError('boom')\n")
            got = hb.run_program(path, [], "")
        self.assertIsNone(got["exit_code"])
        self.assertIn("boom", got["crash"])

    def test_stderr_is_captured_separately_from_stdout(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._script(d, "import sys\n"
                                   "sys.stdout.write('out')\n"
                                   "sys.stderr.write('err')\n")
            got = hb.run_program(path, [], "")
        self.assertEqual(got["stdout"], "out")
        self.assertEqual(got["stderr"], "err")

    def test_the_runner_leaves_the_interpreter_as_it_found_it(self):
        before_argv, before_path = list(sys.argv), list(sys.path)
        before_streams = (sys.stdin, sys.stdout, sys.stderr)
        with tempfile.TemporaryDirectory() as d:
            path = self._script(d, "import json, os, sys\nimport sqlite3\n")
            hb.run_program(path, [], "")
        self.assertEqual(sys.argv, before_argv)
        self.assertEqual(sys.path, before_path)
        self.assertEqual((sys.stdin, sys.stdout, sys.stderr), before_streams)

    def test_a_process_that_used_the_runner_exits_cleanly(self):
        """The regression test for the worst failure this file has had.

        Purging sys.modules is how each repetition gets a cold import, and the
        first version of it deallocated C extension modules while live pure
        Python modules still pointed into them. The interpreter then died with
        SIGSEGV during shutdown, AFTER unittest had printed OK, so the suite
        looked green on screen and exited 139. Nothing asserted in process can
        see that, because it happens after the last assertion runs. Only a
        child process's exit code can, which is what this does."""
        with tempfile.TemporaryDirectory() as d:
            program = self._script(d, "import sqlite3, json, re\nvalue = 1\n")
            driver = self._script(d, (
                "import importlib.util, sys\n"
                "spec = importlib.util.spec_from_file_location('hb', %r)\n"
                "hb = importlib.util.module_from_spec(spec)\n"
                "spec.loader.exec_module(hb)\n"
                "for _ in range(3):\n"
                "    hb.run_program(%r, [], '')\n"
                % (TOOL_PATH, program)), name="driver.py")
            result = subprocess.run([sys.executable, driver],
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    universal_newlines=True)
        self.assertEqual(
            result.returncode, 0,
            "a process that used the benchmark runner exited %d. A negative "
            "or 139 style code here means the interpreter crashed on the way "
            "out, which turns this whole suite red for a reason no assertion "
            "reports. stderr: %s" % (result.returncode, result.stderr))

    def test_each_run_pays_a_cold_import(self):
        """The defect this exists for was real and cost the first draft of the
        page a factor of three: with a naive purge the second run of a program
        inherited the first run's modules warm, so the page reported an import
        cost nobody actually pays. A module imported by the program must be
        gone from sys.modules once it returns."""
        with tempfile.TemporaryDirectory() as d:
            path = self._script(d, "import sqlite3\n")
            self.assertNotIn("sqlite3", hb.runner_baseline())
            hb.run_program(path, [], "")
        self.assertNotIn("sqlite3", sys.modules,
                         "the runner left a module the hook program imported "
                         "resident, so the next repetition would get it warm")


# ---------------------------------------------------------------------------
# 5. The throwaway world, and the proof that it makes the fence work.
# ---------------------------------------------------------------------------

class TestTheSandbox(unittest.TestCase):

    def test_it_builds_a_consented_project_with_a_live_claim(self):
        home_before = os.environ.get("HOME")
        cwd_before = os.getcwd()
        with hb.Sandbox() as box:
            self.assertTrue(os.path.isdir(os.path.join(box.project,
                                                       ".brothermode")))
            self.assertTrue(os.path.isfile(box.config))
            self.assertEqual(os.environ["BROTHERMODE_ROOT"], box.project)
            self.assertTrue(box.label.startswith("bm1-"),
                            "the claim was not made under a derived fence "
                            "label, so the fence could never recognize it")
            base = box.base
        self.assertFalse(os.path.exists(base), "the sandbox was not deleted")
        self.assertEqual(os.environ.get("HOME"), home_before)
        self.assertEqual(os.getcwd(), cwd_before)
        self.assertNotIn("BROTHERMODE_ROOT", os.environ)

    def test_calibrated_a_sandbox_whose_fence_checks_nothing_is_refused(self):
        """The failure this guard exists for: if the store is missing the
        fence fails OPEN, every timing becomes the cost of a hook that decided
        nothing, and the page reports a fast, meaningless number. Reproduced
        by removing the store from a working sandbox and re-running the same
        validation the real run performs."""
        with hb.Sandbox() as box:
            store = os.path.join(box.project, ".brothermode", "store.sqlite3")
            self.assertTrue(os.path.isfile(store))
            os.remove(store)
            with self.assertRaises(hb.BenchError) as caught:
                box._validate()
        self.assertIn("did NOT refuse", str(caught.exception))


# ---------------------------------------------------------------------------
# 6. A real measurement.
# ---------------------------------------------------------------------------

class TestARealMeasurement(unittest.TestCase):
    """One repetition, so the suite stays fast. Nothing here asserts a
    latency: it asserts the record is complete, honest about its machine, and
    that taking it left this repository alone."""

    #: Bytecode cache directory this suite is responsible for NOT creating.
    PYCACHE = os.path.join(HERE, "__pycache__")

    @classmethod
    def setUpClass(cls):
        cls.before = cls._tree_snapshot()
        cls.pycache_before = cls._pycache_state()
        cls.record = hb.measure(reps=1)
        cls.after = cls._tree_snapshot()
        cls.pycache_after = cls._pycache_state()

    @classmethod
    def _pycache_state(cls):
        """What tools/__pycache__ looks like right now, or None if absent.

        The earlier form of the check asserted the directory did not exist AT
        ALL, which is a claim this suite does not own: in the full gate other
        suites import modules from tools/ first, so on a machine with no cache
        checked out the directory is already there before this suite starts.
        That is somebody else's write, and it made the check fail on
        continuous integration while passing on a developer machine that ran
        this suite alone, twice, which is the shape of a check that tests its
        environment rather than its subject. What this suite owns is whether
        THIS measurement wrote anything, so the state is compared across the
        run instead. It still fails if the measurement creates the directory,
        adds a file to it, or rewrites one.
        """
        if not os.path.isdir(cls.PYCACHE):
            return None
        return sorted(
            (name, os.stat(os.path.join(cls.PYCACHE, name)).st_mtime_ns)
            for name in os.listdir(cls.PYCACHE))

    @staticmethod
    def _tree_snapshot():
        snapshot = {}
        for rel in (".", "tools", "docs", "hooks", "scripts"):
            path = os.path.join(ROOT, rel)
            snapshot[rel] = sorted(os.listdir(path))
        return snapshot

    def test_the_run_wrote_nothing_into_this_repository(self):
        self.assertEqual(self.before, self.after,
                         "a measurement changed the contents of this "
                         "checkout; it is supposed to work entirely inside a "
                         "temporary directory")
        self.assertEqual(
            self.pycache_before, self.pycache_after,
            "the measurement changed tools/__pycache__; the sandbox sets both "
            "sys.pycache_prefix and sys.dont_write_bytecode so that the "
            "measured imports cache into the throwaway directory and nowhere "
            "else. A cache that already existed before the run is another "
            "suite's business, not this one's.")

    def test_every_action_reports_a_chain_and_its_programs(self):
        self.assertTrue(self.record["actions"])
        for action in self.record["actions"]:
            self.assertTrue(action["programs"], action["action"])
            self.assertEqual(action["program_count"], len(action["programs"]))
            self.assertGreater(action["chain"]["median_ms"], 0.0)
            for entry in action["programs"]:
                self.assertGreater(entry["timing"]["median_ms"], 0.0)
                self.assertGreaterEqual(entry["timing"]["max_ms"],
                                        entry["timing"]["median_ms"])
                self.assertLessEqual(entry["timing"]["min_ms"],
                                     entry["timing"]["median_ms"])

    def test_the_hooks_all_succeeded_under_the_benchmark(self):
        """Not a field failure rate, and the page says so. It is still the
        thing that tells a reader the numbers above came from programs that
        ran rather than from programs that fell over."""
        for action in self.record["actions"]:
            self.assertEqual(action["crashes"], [], action["action"])
            self.assertEqual(action["non_zero_exits"], 0, action["action"])

    def test_the_machine_record_is_read_from_this_environment(self):
        import platform
        machine = self.record["machine"]
        self.assertEqual(machine["python_version"], platform.python_version())
        self.assertEqual(machine["python_implementation"],
                         platform.python_implementation())
        self.assertEqual(machine["platform"], platform.platform())

    def test_the_record_carries_no_hostname_and_no_home_directory(self):
        """A latency page is not a reason to publish where the founder lives
        on disk."""
        import platform
        blob = json.dumps(self.record)
        node = platform.node()
        if node:
            self.assertNotIn(node, blob)
        self.assertNotIn(os.path.expanduser("~"), blob)

    def test_the_unmeasurable_hook_is_reported_rather_than_omitted(self):
        """RENAMED and re-pointed 2026-08-17: the session-start hook is a
        Python program now, not a shell script, and it is STILL not
        measurable here, because it drives its sibling tools as real
        processes and this tool spawns none. What must never change is that
        an unmeasurable hook appears on the page as unmeasured WITH ITS
        REASON, rather than quietly missing from a page of totals."""
        names = [entry["program"] for entry in self.record["unrunnable"]]
        self.assertIn("bm_sessionstart.py", names)
        for entry in self.record["unrunnable"]:
            self.assertTrue(entry["reason"].strip())


# ---------------------------------------------------------------------------
# 7. The page.
# ---------------------------------------------------------------------------

class TestThePage(unittest.TestCase):

    def setUp(self):
        self.assertTrue(os.path.isfile(PAGE_PATH),
                        "docs/PERFORMANCE.md is missing; generate it with "
                        "python3 tools/bm_hookbench.py")
        self.text = read(PAGE_PATH)

    def test_the_page_re_renders_from_its_own_record_byte_for_byte(self):
        """THE gate of this suite. The page carries the record it was made
        from, and the renderer is a pure function of that record, so a number
        edited into the prose by hand cannot survive this check. It is the
        mechanical form of the rule that this page is generated and never
        typed."""
        record = hb.extract_record(self.text)
        self.assertEqual(
            hb.render(record), self.text,
            "docs/PERFORMANCE.md is not what tools/bm_hookbench.py renders "
            "from the record printed inside it. Either a number was typed in "
            "by hand, or the renderer changed and the page was not "
            "regenerated: run python3 tools/bm_hookbench.py")

    def test_a_page_with_a_number_edited_by_hand_is_caught(self):
        """Calibration: the test above has to FAIL on a tampered page, or it
        is proving nothing."""
        tampered = re.sub(r"\| (\d+)\.(\d+) \|", r"| \g<1>9.\g<2> |",
                          self.text, count=1)
        self.assertNotEqual(tampered, self.text, "the fixture changed nothing")
        self.assertNotEqual(hb.render(hb.extract_record(tampered)), tampered)

    def test_the_page_declares_itself_generated_and_names_its_generator(self):
        self.assertIn("GENERATED by `tools/bm_hookbench.py`", self.text)

    def test_the_page_states_the_machine_and_the_interpreter(self):
        record = hb.extract_record(self.text)
        self.assertIn(record["machine"]["platform"], self.text)
        self.assertIn(record["machine"]["python_version"], self.text)
        self.assertIn("vary by machine", self.text)

    def test_the_page_names_every_event_the_manifest_wires(self):
        for event in json.loads(read(HOOKS_PATH))["hooks"]:
            self.assertIn(event, self.text,
                          "docs/PERFORMANCE.md never mentions the %s event, "
                          "which hooks/hooks.json wires" % event)

    def test_every_not_measured_entry_carries_a_reason_and_no_estimate(self):
        record = hb.extract_record(self.text)
        self.assertTrue(record["not_measured"])
        number = re.compile(r"\d+(?:\.\d+)?\s*(?:ms|milliseconds|percent|%)")
        for entry in record["not_measured"]:
            self.assertTrue(entry["topic"].strip())
            self.assertGreater(len(entry["reason"].strip()), 40,
                               "%s carries no real reason" % entry["topic"])
            self.assertIsNone(
                number.search(entry["reason"]),
                "the NOT MEASURED entry %r carries a number, which is exactly "
                "the estimate it is supposed to refuse to make"
                % entry["topic"])
            self.assertIn("NOT MEASURED: %s" % entry["topic"], self.text)

    def test_the_page_refuses_the_field_questions_it_cannot_answer(self):
        """The review asked for these by name. Silence about them would be the
        same defect in a new place, so each has to be present AS A REFUSAL."""
        topics = " ".join(entry["topic"].lower() for entry
                          in hb.extract_record(self.text)["not_measured"])
        for word in ("contention", "false refusal", "failure frequency"):
            self.assertIn(word, topics,
                          "no NOT MEASURED entry covers %r" % word)

    def test_the_totals_are_labelled_as_lower_bounds(self):
        self.assertIn("LOWER BOUND", self.text)

    def test_the_page_states_the_band_it_promises_for_a_re_run(self):
        record = hb.extract_record(self.text)
        self.assertIn("REPRODUCIBILITY BAND", self.text)
        self.assertIn("factor of %.1f" % record["reproducibility_factor"],
                      self.text)

    def test_the_page_says_the_run_uses_a_throwaway_store(self):
        self.assertIn("throwaway", self.text)
        self.assertIn("temporary directory", self.text)

    def test_a_matcher_containing_a_pipe_is_escaped_in_the_table(self):
        """An unescaped alternation splits one table cell into several, so the
        row a reader sees stops being the row the manifest holds."""
        matchers = [g["matcher"] for g in hb.extract_record(self.text)["manifest"]
                    if "|" in g["matcher"]]
        self.assertTrue(matchers, "no matcher in this manifest has a pipe, so "
                                  "this test is no longer exercising anything")
        for matcher in matchers:
            self.assertIn(matcher.replace("|", "\\|"), self.text)

    def test_the_page_carries_no_em_or_en_dash(self):
        offenders = []
        for rel in ("docs/PERFORMANCE.md", "tools/bm_hookbench.py",
                    "tools/test_bm_hookbench.py"):
            for i, line in enumerate(read(os.path.join(ROOT, rel)).split("\n"), 1):
                if "\u2013" in line or "\u2014" in line:
                    offenders.append("%s:%d" % (rel, i))
        self.assertEqual(offenders, [], "em or en dash found at %s"
                         % ", ".join(offenders))

    def test_the_renderer_is_deterministic_for_one_record(self):
        record = fake_record()
        self.assertEqual(hb.render(record), hb.render(record))

    def test_a_page_carrying_no_record_refuses_rather_than_guessing(self):
        with self.assertRaises(hb.BenchError):
            hb.extract_record("# a page with no record at all\n")


# ---------------------------------------------------------------------------
# 8. The reproducibility band.
# ---------------------------------------------------------------------------

class TestTheBand(unittest.TestCase):

    def _record(self, median):
        record = fake_record()
        record["actions"][0]["chain"]["median_ms"] = median
        record["actions"][0]["programs"][0]["timing"]["median_ms"] = median
        return record

    def test_a_re_run_inside_the_band_passes(self):
        rows = hb.compare(self._record(10.0), self._record(18.0))
        self.assertTrue(rows)
        self.assertTrue(all(row["within"] for row in rows), rows)

    def test_a_re_run_outside_the_band_fails(self):
        rows = hb.compare(self._record(10.0), self._record(30.0))
        self.assertTrue(any(not row["within"] for row in rows))

    def test_a_program_that_appeared_or_vanished_is_a_mismatch(self):
        old = self._record(10.0)
        new = self._record(10.0)
        new["actions"][0]["programs"] = []
        rows = hb.compare(old, new)
        vanished = [row for row in rows if row["new_ms"] is None]
        self.assertTrue(vanished, "a program present in one run and absent "
                                  "from the other was reported as agreement")
        self.assertFalse(vanished[0]["within"])


# ---------------------------------------------------------------------------
# 9. The constraint that shaped the design.
# ---------------------------------------------------------------------------

class TestTheSubprocessBanIsRespected(unittest.TestCase):
    """tools/test_bm.py bans `import subprocess` in every shipping module
    under tools/, with a per file allowlist naming bm_autosave.py and
    bm_controller.py. This file is a shipping module and is not on that list,
    which is why it runs hook programs in process and says so on the page.

    That check catches an `import subprocess` line. It would not catch
    os.system, os.popen, os.fork or os.posix_spawn, all of which spawn a
    process while satisfying the letter of the ban. Passing the letter and
    breaking the promise is worse than failing the check, so this test names
    those routes too."""

    def test_the_tool_imports_no_subprocess_and_spawns_no_process(self):
        source = read(TOOL_PATH)
        banned_import = re.compile(
            r"^\s*(?:import|from)\s+(subprocess|socket|urllib|http|asyncio)\b",
            re.MULTILINE)
        found = banned_import.search(source)
        self.assertIsNone(found, "tools/bm_hookbench.py imports %s; it is a "
                                 "shipping module and tools/test_bm.py's "
                                 "allowlist does not name it"
                          % (found.group(1) if found else ""))
        for route in ("os.system", "os.popen", "os.fork", "os.spawn",
                      "os.posix_spawn", "os.execv", "__import__('subprocess'",
                      '__import__("subprocess"'):
            self.assertNotIn(
                route, source,
                "tools/bm_hookbench.py reaches for %s, which spawns a process "
                "while satisfying the letter of the ban in tools/test_bm.py. "
                "The page's whole account of what it does not measure rests on "
                "this file spawning nothing." % route)

    def test_the_allowlist_in_test_bm_still_does_not_name_this_file(self):
        """If somebody later adds bm_hookbench.py to that allowlist, this test
        fails and whoever did it has to come back here and rewrite the NOT
        MEASURED entry that the ban is the reason for."""
        self.assertNotIn(
            '"bm_hookbench.py"', read(os.path.join(HERE, "test_bm.py")),
            "tools/test_bm.py now names bm_hookbench.py. If the subprocess "
            "exemption was granted, measure the real processes and delete the "
            "NOT MEASURED entry about interpreter startup, rather than leaving "
            "the page claiming a limit that no longer applies.")


# ---------------------------------------------------------------------------
# 10. The command line.
# ---------------------------------------------------------------------------

class TestTheCommandLine(unittest.TestCase):

    def test_json_mode_prints_a_record_and_writes_no_page(self):
        with tempfile.TemporaryDirectory() as d:
            marker = os.path.join(d, "should-not-appear.md")
            result = run_cli("--json", "--reps", "1", "--out", marker)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertIn("actions", record)
        self.assertFalse(os.path.exists(marker))

    def test_the_documented_invocation_writes_the_page_where_told(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "PERFORMANCE.md")
            result = run_cli("--reps", "1", "--out", out)
            self.assertEqual(result.returncode, 0, result.stderr)
            text = read(out)
        self.assertEqual(hb.render(hb.extract_record(text)), text)

    def test_compare_reports_the_band_and_exits_zero_when_inside_it(self):
        with tempfile.TemporaryDirectory() as d:
            first = os.path.join(d, "first.json")
            result = run_cli("--json", "--reps", "1")
            self.assertEqual(result.returncode, 0, result.stderr)
            with io.open(first, "w", encoding="utf-8") as handle:
                handle.write(result.stdout)
            again = run_cli("--compare", first, "--reps", "1")
        self.assertIn("inside the stated band", again.stdout)
        self.assertIn(again.returncode, (0, 1),
                      "compare returned %d, which is neither a verdict nor a "
                      "refusal" % again.returncode)

    def test_an_unrecognized_argument_is_refused(self):
        result = run_cli("--turbo")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized", result.stderr)

    def test_help_states_the_documented_invocation(self):
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("python3 tools/bm_hookbench.py", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=1)
