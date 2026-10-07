#!/usr/bin/env python3
"""FX-06.1 tests for scripts/loop/run_ledger.py, the run ledger writer.

Every case drives run_ledger.main as the entry point, or the function a verb is
named after, in process, with its own run directory and its own BROTHER_RUNFLOW
and BROTHER_RUNS_ROOT. The module is imported by its bare stem from this file's
own directory.

Run: python3 -B scripts/loop/test_run_ledger.py
"""
import contextlib
import fcntl
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import run_ledger


class WriterCase(unittest.TestCase):
    """One condition per case; every tree is a fresh temp directory."""

    def setUp(self):
        run_ledger._NOTE_EMITTED = False
        self.tmp = tempfile.TemporaryDirectory(prefix="run-ledger-fx061-")
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.runs_root = os.path.join(self.root, "runs")
        os.makedirs(self.runs_root)
        self.run_dir = os.path.join(self.root, "run-20260928-000001-1")
        os.makedirs(self.run_dir)
        patcher = mock.patch.dict(os.environ, {
            "BROTHER_RUNFLOW": "shadow",
            "BROTHER_RUNS_ROOT": self.runs_root,
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def cli(self, argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}, clear=False):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = run_ledger.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def ledger(self):
        return os.path.join(self.run_dir, "ledger")

    def read_json(self, path):
        with open(path, "rb") as fh:
            return json.loads(fh.read().decode("utf-8"))

    def begin_ok(self, stage="03", pid=4321, extra=()):
        argv = ["begin", "--stage", stage, "--run-dir", self.run_dir, "--pid", str(pid)]
        argv.extend(extra)
        code, out, err = self.cli(argv)
        self.assertEqual(code, 0, (out, err))

    def end_ok(self, stage="03", state="DONE", extra=()):
        argv = ["end", "--stage", stage, "--run-dir", self.run_dir, "--state", state]
        argv.extend(extra)
        code, out, err = self.cli(argv)
        self.assertEqual(code, 0, (out, err))

    def test_mode_verb_prints_the_word_first_and_the_note_second(self):
        code, out, err = self.cli(["mode"], env={"BROTHER_RUNFLOW": "banana"})
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0], "off")
        self.assertTrue(lines[1].startswith("NOTE BROTHER_RUNFLOW=banana"), lines)
        self.assertEqual(err, "")

    def test_unknown_mode_reads_off_and_says_so_once(self):
        first, first_out, _first_err = self.cli(["mode"], env={"BROTHER_RUNFLOW": "banana"})
        self.assertEqual(first, 0)
        self.assertEqual(first_out.splitlines()[0], "off")
        self.assertEqual(len(first_out.splitlines()), 2)
        second, second_out, _second_err = self.cli(["mode"], env={"BROTHER_RUNFLOW": "banana"})
        self.assertEqual(second, 0)
        self.assertEqual(second_out.splitlines(), ["off"])
        third, _third_out, _third_err = self.cli(
            ["begin", "--stage", "03", "--run-dir", self.run_dir, "--pid", "9"],
            env={"BROTHER_RUNFLOW": "banana"})
        self.assertEqual(third, 0)
        self.assertFalse(os.path.exists(self.ledger()))

    def test_begin_then_end_reads_done(self):
        self.begin_ok("03", pid=5150, extra=[
            "--pid-start", "Mon Sep 28 15:55:00 2026",
            "--command", "bash loop_until.sh",
            "--tool", "loop_until.sh",
        ])
        self.end_ok("03", "DONE", extra=["--exit-code", "0"])
        record = self.read_json(os.path.join(self.ledger(), "03-run.json"))
        self.assertEqual(record["state"], "DONE")
        self.assertEqual(record["schema"], "run-ledger-stage-v1")
        self.assertEqual(record["stage"], "03")
        self.assertEqual(record["name"], "run")
        self.assertEqual(record["attempt"], 1)
        self.assertEqual(record["pid"], 5150)
        self.assertEqual(record["pid_start"], "Mon Sep 28 15:55:00 2026")
        self.assertEqual(record["command"], "bash loop_until.sh")
        self.assertEqual(record["exit_code"], 0)
        self.assertEqual(record["points_at"], "receipt/receipt.json")
        self.assertEqual(record["run_id"], os.path.basename(self.run_dir))

    def test_every_receipt_key_is_present_and_unknowns_say_no_data(self):
        self.begin_ok("03")
        record = self.read_json(os.path.join(self.ledger(), "03-run.json"))
        keys = ("schema", "run_id", "stage", "name", "attempt", "state", "started_at",
                "ended_at", "pid", "pid_start", "command", "tool", "argv", "exit_code",
                "log_path", "log_sha256", "points_at", "reason", "deadline_at", "mode",
                "next")
        for key in keys:
            self.assertIn(key, record)
        self.assertEqual(len(record), len(keys))
        self.assertTrue(str(record["ended_at"]).startswith("NO-DATA"))
        self.assertTrue(str(record["exit_code"]).startswith("NO-DATA"))
        self.assertTrue(str(record["log_sha256"]).startswith("NO-DATA"))
        self.assertTrue(str(record["deadline_at"]).startswith("NO-DATA"))
        self.assertEqual(record["mode"], "shadow")

    def test_begin_records_no_data_for_a_start_not_supplied(self):
        self.begin_ok("03")
        record = self.read_json(os.path.join(self.ledger(), "03-run.json"))
        self.assertEqual(record["pid_start"], "NO-DATA: not supplied by the caller")
        self.assertEqual(record["command"], "NO-DATA: not supplied by the caller")

    def test_state_missing_is_no_data(self):
        word = run_ledger.state(self.run_dir)
        self.assertTrue(word.startswith("NO-DATA"), word)

    def test_state_unknown_word_is_no_data(self):
        os.makedirs(self.ledger())
        with open(os.path.join(self.ledger(), "STATE"), "wb") as fh:
            fh.write(b"BANANA\n")
        self.assertTrue(run_ledger.state(self.run_dir).startswith("NO-DATA"))

    def test_state_empty_or_two_words_is_no_data(self):
        os.makedirs(self.ledger())
        path = os.path.join(self.ledger(), "STATE")
        for text in (b"", b"\n", b"   \n", b"RUNNING ENDED\n", b"RUNNING\nENDED\n"):
            with open(path, "wb") as fh:
                fh.write(text)
            word = run_ledger.state(self.run_dir)
            self.assertTrue(word.startswith("NO-DATA"), (text, word))

    def test_state_is_one_word_after_each_transition(self):
        self.begin_ok("03")
        self.assertEqual(run_ledger.state(self.run_dir), "RUNNING")
        self.assertEqual(len(run_ledger.state(self.run_dir).split()), 1)
        self.end_ok("03", "DONE")
        self.assertEqual(run_ledger.state(self.run_dir), "RUNNING")
        self.begin_ok("05", pid=5151)
        self.end_ok("05", "DONE")
        self.assertEqual(run_ledger.state(self.run_dir), "ENDED")

    def test_begin_from_empty_creates_ledger(self):
        self.assertFalse(os.path.exists(self.ledger()))
        self.begin_ok("03")
        self.assertTrue(os.path.isdir(self.ledger()))

    def test_begin_for_stage_03_writes_last(self):
        self.begin_ok("03")
        with open(os.path.join(self.runs_root, "LAST"), "rb") as fh:
            self.assertEqual(fh.read().decode("utf-8").strip(), self.run_dir)

    def test_empty_run_dir_refuses_and_cwd_is_untouched(self):
        before = os.getcwd()
        code, _out, _err = self.cli(["begin", "--stage", "03", "--run-dir", "", "--pid", "1"])
        self.assertEqual(code, 2)
        self.assertEqual(os.getcwd(), before)
        self.assertFalse(os.path.exists(self.ledger()))

    def test_relative_run_dir_refuses(self):
        code, _out, _err = self.cli(["begin", "--stage", "03", "--run-dir", "relative-run", "--pid", "1"])
        self.assertEqual(code, 2)

    def test_missing_run_dir_refuses(self):
        missing = os.path.join(self.root, "not-there")
        code, _out, _err = self.cli(["begin", "--stage", "03", "--run-dir", missing, "--pid", "1"])
        self.assertEqual(code, 2)

    def test_unknown_stage_refuses(self):
        code, _out, _err = self.cli(["begin", "--stage", "99", "--run-dir", self.run_dir, "--pid", "1"])
        self.assertEqual(code, 2)
        code2, _out2, _err2 = self.cli(["end", "--stage", "99", "--run-dir", self.run_dir, "--state", "DONE"])
        self.assertEqual(code2, 2)
        self.assertFalse(os.path.exists(self.ledger()))

    def test_unknown_state_value_refuses(self):
        self.begin_ok("03")
        code, _out, _err = self.cli(["end", "--stage", "03", "--run-dir", self.run_dir, "--state", "BANANA"])
        self.assertEqual(code, 2)
        record = self.read_json(os.path.join(self.ledger(), "03-run.json"))
        self.assertEqual(record["state"], "STARTED")

    def test_abandon_without_words_refuses_and_writes_nothing(self):
        code, _out, _err = self.cli(["abandon", "--run-dir", self.run_dir, "--words", ""])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(self.ledger()))

    def test_abandon_whitespace_words_refuses(self):
        code, _out, _err = self.cli(["abandon", "--run-dir", self.run_dir, "--words", "   "])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(self.ledger()))

    def test_second_abandon_writes_a_second_file(self):
        first, _out, _err = self.cli(["abandon", "--run-dir", self.run_dir, "--words", "stopping for tonight"])
        self.assertEqual(first, 0)
        path = os.path.join(self.ledger(), "ABANDONED.json")
        with open(path, "rb") as fh:
            before = fh.read()
        second, _out2, _err2 = self.cli(["abandon", "--run-dir", self.run_dir, "--words", "stopping again"])
        self.assertEqual(second, 0)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertTrue(os.path.exists(os.path.join(self.ledger(), "ABANDONED.2.json")))
        self.assertEqual(run_ledger.state(self.run_dir), "ABANDONED")

    def test_begin_refuses_when_the_predecessor_is_not_done(self):
        code, _out, _err = self.cli(["begin", "--stage", "05", "--run-dir", self.run_dir, "--pid", "1"])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(os.path.join(self.ledger(), "05-end.json")))
        self.begin_ok("03")
        self.end_ok("03", "DONE")
        self.begin_ok("05", pid=7002)
        self.assertTrue(os.path.exists(os.path.join(self.ledger(), "05-end.json")))

    def test_begin_after_done_refuses(self):
        self.begin_ok("03")
        self.end_ok("03", "DONE")
        code, _out, _err = self.cli(["begin", "--stage", "03", "--run-dir", self.run_dir, "--pid", "2"])
        self.assertEqual(code, 1)

    def test_begin_after_failed_writes_attempt_two(self):
        self.begin_ok("03", pid=1001)
        self.end_ok("03", "FAILED")
        self.begin_ok("03", pid=1002)
        record = self.read_json(os.path.join(self.ledger(), "03-run.2.json"))
        self.assertEqual(record["attempt"], 2)
        self.assertEqual(record["pid"], 1002)

    def test_a_third_begin_after_two_failed_is_held(self):
        self.begin_ok("03", pid=1001)
        self.end_ok("03", "FAILED")
        self.begin_ok("03", pid=1002)
        self.end_ok("03", "FAILED")
        code, _out, _err = self.cli(["begin", "--stage", "03", "--run-dir", self.run_dir, "--pid", "1003"])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(os.path.join(self.ledger(), "03-run.3.json")))

    def test_begin_refuses_a_started_attempt_whatever_its_liveness(self):
        self.begin_ok("03", extra=["--pid-start", "Mon Sep 28 15:55:00 2026",
                                   "--command", "bash loop_until.sh"])
        code, _out, _err = self.cli(["begin", "--stage", "03", "--run-dir", self.run_dir, "--pid", "2002"])
        self.assertEqual(code, 1)

    def test_rule_refusals_return_1_and_leave_the_ledger_unchanged(self):
        self.begin_ok("03")
        listing = sorted(os.listdir(self.ledger()))
        path = os.path.join(self.ledger(), "03-run.json")
        with open(path, "rb") as fh:
            before = fh.read()
        code, _out, _err = self.cli(["begin", "--stage", "03", "--run-dir", self.run_dir, "--pid", "2"])
        self.assertEqual(code, 1)
        self.assertEqual(sorted(os.listdir(self.ledger())), listing)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_end_on_a_terminal_receipt_refuses_and_leaves_it_unchanged(self):
        self.begin_ok("03")
        self.end_ok("03", "DONE")
        path = os.path.join(self.ledger(), "03-run.json")
        with open(path, "rb") as fh:
            before = fh.read()
        code, _out, _err = self.cli(["end", "--stage", "03", "--run-dir", self.run_dir, "--state", "DONE"])
        self.assertEqual(code, 1)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_end_with_no_begin_records_attempt_one(self):
        self.end_ok("05", "DONE", extra=["--started-at", "2026-09-28T06:55:00+00:00"])
        record = self.read_json(os.path.join(self.ledger(), "05-end.json"))
        self.assertEqual(record["attempt"], 1)
        self.assertEqual(record["state"], "DONE")
        self.assertEqual(record["started_at"], "2026-09-28T06:55:00+00:00")
        self.assertEqual(record["points_at"], "receipt/receipt.json")
        self.assertEqual(run_ledger.state(self.run_dir), "ENDED")

    def test_end_hashes_a_log_file(self):
        log_path = os.path.join(self.root, "driver.log")
        with open(log_path, "wb") as fh:
            fh.write(b"RUN START\nRUN END\n")
        self.begin_ok("03")
        self.end_ok("03", "DONE", extra=["--log", log_path])
        record = self.read_json(os.path.join(self.ledger(), "03-run.json"))
        self.assertEqual(record["log_path"], log_path)
        self.assertEqual(len(record["log_sha256"]), 64)
        self.begin_ok("05", pid=7003)
        self.end_ok("05", "DONE", extra=["--log", os.path.join(self.root, "no-such.log")])
        record5 = self.read_json(os.path.join(self.ledger(), "05-end.json"))
        self.assertTrue(str(record5["log_sha256"]).startswith("NO-DATA"))

    def test_event_appends_and_cuts_long_detail(self):
        detail = "x" * 2100
        code, _out, _err = self.cli(["event", "--run-dir", self.run_dir, "--kind", "note", "--detail", detail])
        self.assertEqual(code, 0)
        with open(os.path.join(self.ledger(), "events.jsonl"), "rb") as fh:
            lines = fh.read().decode("utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record["kind"], "note")
        self.assertTrue(record["detail"].endswith("[cut 100 chars]"), record["detail"][-40:])
        self.assertEqual(len(record["detail"]), 2000 + len(" [cut 100 chars]"))

    def test_many_events_keep_order(self):
        for index in range(40):
            code, _out, _err = self.cli(["event", "--run-dir", self.run_dir, "--kind", "e%02d" % index])
            self.assertEqual(code, 0)
        with open(os.path.join(self.ledger(), "events.jsonl"), "rb") as fh:
            lines = fh.read().decode("utf-8").splitlines()
        self.assertEqual(len(lines), 40)
        kinds = [json.loads(line)["kind"] for line in lines]
        self.assertEqual(kinds, ["e%02d" % index for index in range(40)])

    def test_event_json_option_must_be_an_object(self):
        code, _out, err = self.cli(["event", "--run-dir", self.run_dir, "--kind", "k", "--json", "[1, 2]"])
        self.assertEqual(code, 2)
        self.assertIn("object", err)
        code2, _out2, _err2 = self.cli(["event", "--run-dir", self.run_dir, "--kind", "k", "--json", "{oops"])
        self.assertEqual(code2, 2)
        self.assertFalse(os.path.exists(self.ledger()))
        code3, _out3, _err3 = self.cli(["event", "--run-dir", self.run_dir, "--kind", "k", "--json", '{"a": 1}'])
        self.assertEqual(code3, 0)

    def test_event_non_str_key_raises_value_error_and_writes_nothing(self):
        with self.assertRaises(ValueError):
            run_ledger.event(self.run_dir, "k", json_obj={1: "x"})
        with self.assertRaises(ValueError):
            run_ledger.event(self.run_dir, "k", json_obj={("a",): "x"})
        self.assertFalse(os.path.exists(self.ledger()))

    def test_every_writer_raises_value_error_on_a_wrong_type_before_any_write(self):
        with self.assertRaises(ValueError):
            run_ledger.begin(None, self.run_dir, 1)
        with self.assertRaises(ValueError):
            run_ledger.begin("03", None, 1)
        with self.assertRaises(ValueError):
            run_ledger.begin("03", self.run_dir, "1")
        with self.assertRaises(ValueError):
            run_ledger.begin("03", self.run_dir, True)
        with self.assertRaises(ValueError):
            run_ledger.begin("03", self.run_dir, 1, deadline_epoch=True)
        with self.assertRaises(ValueError):
            run_ledger.begin("03", self.run_dir, 1, log=b"x")
        with self.assertRaises(ValueError):
            run_ledger.begin("03", self.run_dir, 1, argv="x")
        with self.assertRaises(ValueError):
            run_ledger.begin("03", self.run_dir, 1, pid_start=7)
        with self.assertRaises(ValueError):
            run_ledger.end("03", self.run_dir, None)
        with self.assertRaises(ValueError):
            run_ledger.end("03", self.run_dir, "DONE", exit_code=True)
        with self.assertRaises(ValueError):
            run_ledger.event(self.run_dir, None)
        with self.assertRaises(ValueError):
            run_ledger.state(None)
        with self.assertRaises(ValueError):
            run_ledger.abandon(self.run_dir, None)
        self.assertFalse(os.path.exists(self.ledger()))

    def test_main_raises_value_error_on_a_non_list_argv(self):
        for bad in (0, True, b"x", "x", {}, {1, 2}, object()):
            with self.assertRaises(ValueError):
                run_ledger.main(bad)

    def test_main_usage_error_returns_2_without_exiting(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            first = run_ledger.main(["nope"])
            second = run_ledger.main(["begin", "--stage", "03", "--run-dir", self.run_dir, "--pid", "abc"])
            third = run_ledger.main([])
        self.assertEqual((first, second, third), (2, 2, 2))

    def test_liveness_without_an_observation_reads_no_data(self):
        self.assertEqual(run_ledger.liveness(123, "start", "cmd"), "NO-DATA")
        self.assertEqual(run_ledger.liveness(None, "start", "cmd"), "NO-DATA")
        self.assertEqual(run_ledger.liveness(True, "start", "cmd"), "NO-DATA")
        self.assertEqual(run_ledger.liveness("9", "start", "cmd"), "NO-DATA")
        self.assertEqual(run_ledger.liveness(123, None, "cmd"), "NO-DATA")
        self.assertEqual(run_ledger.liveness(123, "NO-DATA: not supplied by the caller", "cmd"), "NO-DATA")
        self.assertEqual(run_ledger.liveness(123, "start", "cmd", "", ""), "DEAD")
        self.assertEqual(run_ledger.liveness(123, "start", "cmd", "", "cmd"), "NO-DATA")
        self.assertEqual(run_ledger.liveness(123, "start", "cmd", None, None), "NO-DATA")

    def test_recycled_pid_with_other_start_time_reads_dead(self):
        self.assertEqual(run_ledger.liveness(4242, "Mon Sep 28 15:55:00 2026", "bash loop_until.sh",
                                             "Mon Sep 28 16:55:00 2026", "bash loop_until.sh"), "DEAD")
        self.assertEqual(run_ledger.liveness(4242, "Mon Sep 28 15:55:00 2026", "bash loop_until.sh",
                                             "Mon Sep 28 15:55:00 2026", "bash loop_until.sh"), "LIVE")

    def test_same_pid_other_command_reads_dead(self):
        self.assertEqual(run_ledger.liveness(4242, "start", "bash loop_until.sh", "start", "python3 other.py"),
                         "DEAD")

    def test_canonical_start_spelling_matches_loop_guard(self):
        self.assertEqual(run_ledger.liveness(77, "Mon  Sep 28   15:55:00 2026", "bash x",
                                             "Mon Sep 28 15:55:00 2026", "bash x"), "LIVE")
        self.assertEqual(run_ledger.liveness(77, "Mon Sep 28 15:55:00 2026", "  bash x  ",
                                             "Mon Sep 28 15:55:00 2026", "bash x"), "LIVE")

    def test_off_writing_verbs_write_nothing(self):
        off = {"BROTHER_RUNFLOW": "off"}
        first, _out, _err = self.cli(["begin", "--stage", "03", "--run-dir", self.run_dir, "--pid", "1"], env=off)
        self.assertEqual(first, 0)
        second, _out2, _err2 = self.cli(["event", "--run-dir", self.run_dir, "--kind", "k"], env=off)
        self.assertEqual(second, 0)
        third, _out3, _err3 = self.cli(["end", "--stage", "03", "--run-dir", self.run_dir, "--state", "DONE"], env=off)
        self.assertEqual(third, 0)
        self.assertFalse(os.path.exists(self.ledger()))
        self.assertFalse(os.path.exists(os.path.join(self.runs_root, "LAST")))

    def test_off_abandon_still_writes(self):
        code, _out, _err = self.cli(
            ["abandon", "--run-dir", self.run_dir, "--words", "the owner stopped it"],
            env={"BROTHER_RUNFLOW": "off"})
        self.assertEqual(code, 0)
        record = self.read_json(os.path.join(self.ledger(), "ABANDONED.json"))
        self.assertEqual(record["words"], "the owner stopped it")
        self.assertEqual(record["pending"], "03-run")
        self.assertEqual(run_ledger.state(self.run_dir), "ABANDONED")

    def test_atomic_write_leaves_no_temp_file(self):
        self.begin_ok("03")
        self.end_ok("03", "DONE")
        for name in os.listdir(self.ledger()):
            self.assertFalse(name.startswith(".tmp-"), name)

    def test_lock_held_elsewhere_exits_4_within_the_bound(self):
        os.makedirs(self.ledger())
        handle = os.open(os.path.join(self.ledger(), ".lock"), os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        saved = run_ledger.LOCK_WAIT_S
        run_ledger.LOCK_WAIT_S = 0.2
        try:
            code, _out, err = self.cli(["begin", "--stage", "03", "--run-dir", self.run_dir, "--pid", "1"])
        finally:
            run_ledger.LOCK_WAIT_S = saved
            fcntl.flock(handle, fcntl.LOCK_UN)
            os.close(handle)
        self.assertEqual(code, 4)
        self.assertEqual(len([line for line in err.splitlines() if "WRITE FAILED" in line]), 1)

    def test_unwritable_ledger_dir_exits_4_with_one_stderr_line(self):
        with open(self.ledger(), "wb") as fh:
            fh.write(b"a plain file where the directory belongs\n")
        code, _out, err = self.cli(["begin", "--stage", "03", "--run-dir", self.run_dir, "--pid", "1"])
        self.assertEqual(code, 4)
        self.assertEqual(len([line for line in err.splitlines() if "WRITE FAILED" in line]), 1)

    def test_concurrent_events_leave_every_line_valid(self):
        failures = []
        guard = threading.Lock()

        def writer(number):
            for index in range(200):
                code = run_ledger.event(self.run_dir, "w%d" % number, detail=str(index))
                if code != 0:
                    with guard:
                        failures.append(code)

        threads = [threading.Thread(target=writer, args=(number,)) for number in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(failures, [])
        with open(os.path.join(self.ledger(), "events.jsonl"), "rb") as fh:
            lines = fh.read().decode("utf-8").splitlines()
        self.assertEqual(len(lines), 400)
        for line in lines:
            self.assertIsInstance(json.loads(line), dict)

    # --- FX-06.2 reader verbs (check, status, reconstruct) ---

    def write_stage(self, stage, state, attempt=1, **fields):
        os.makedirs(self.ledger(), exist_ok=True)
        name = run_ledger.STAGE_NAMES[stage]
        if attempt == 1:
            fname = "%s-%s.json" % (stage, name)
        else:
            fname = "%s-%s.%d.json" % (stage, name, attempt)
        record = {key: "NO-DATA: not supplied by the caller" for key in run_ledger._RECEIPT_KEYS}
        record.update({
            "schema": "run-ledger-stage-v1",
            "run_id": "run-test",
            "stage": stage,
            "name": name,
            "attempt": attempt,
            "state": state,
            "points_at": "receipt/receipt.json",
        })
        record.update(fields)
        path = os.path.join(self.ledger(), fname)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        return path

    def write_receipt(self, **fields):
        folder = os.path.join(self.run_dir, "receipt")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "receipt.json")
        record = {"end_state": "DONE", "driver_log_sha256": "0" * 64}
        record.update(fields)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        return path

    def test_check_no_ledger_is_no_data(self):
        code, out, _err = self.cli(["check", "--run-dir", self.run_dir])
        self.assertEqual(code, 3)
        self.assertTrue(out.startswith("NO-DATA"), out)

    def test_status_no_ledger_prints_no_data_per_stage(self):
        code, out, _err = self.cli(["status", "--run-dir", self.run_dir])
        self.assertEqual(code, 3)
        lines = out.splitlines()
        self.assertEqual(len(lines), len(run_ledger.STAGE_NAMES))
        for line in lines:
            self.assertIn("NO-DATA: no ledger", line)

    def test_check_all_required_done_reads_closed(self):
        for stage in ("03", "05", "06", "07", "08"):
            self.write_stage(stage, "DONE")
        code, out, _err = self.cli(["check", "--run-dir", self.run_dir])
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("CLOSED"), out)
        with open(os.path.join(self.ledger(), "STATE"), "rb") as fh:
            self.assertEqual(fh.read().decode("utf-8").strip(), "CLOSED")

    def test_check_missing_06_is_blocked_and_prints_finish_then_abandon(self):
        self.write_stage("03", "DONE")
        self.write_stage("05", "DONE")
        code, out, _err = self.cli(["check", "--run-dir", self.run_dir])
        self.assertEqual(code, 1)
        self.assertIn("BLOCKED: 06", out)
        finish_at = out.find("finish with:")
        abandon_at = out.find("abandon with:")
        self.assertNotEqual(finish_at, -1)
        self.assertNotEqual(abandon_at, -1)
        self.assertLess(finish_at, abandon_at)

    def test_check_started_with_dead_pid_reads_failed(self):
        self.write_stage("03", "STARTED", pid=99999,
                         pid_start="Mon Jan  1 00:00:00 2001",
                         command="bash loop_until.sh")
        self.write_stage("05", "DONE")
        with mock.patch.object(run_ledger, "_ps_read",
                               return_value=("ok", "Tue Jan  2 00:00:00 2002", "other cmd")):
            code, out, _err = self.cli(["check", "--run-dir", self.run_dir])
        self.assertEqual(code, 1, out)
        self.assertIn("BLOCKED: 03", out)
        self.assertIn("died without an end", out)

    def test_check_live_pid_past_deadline_reads_hung(self):
        for stage in ("03", "05", "06", "07", "08"):
            self.write_stage(stage, "DONE")
        self.write_stage("03", "STARTED", pid=99999,
                         pid_start="Mon Sep 28 15:55:00 2026",
                         command="bash loop_until.sh",
                         deadline_at="2000-01-01T00:00:00+00:00")
        with mock.patch.object(run_ledger, "_ps_read",
                               return_value=("ok", "Mon Sep 28 15:55:00 2026", "bash loop_until.sh")):
            code, out, _err = self.cli(["check", "--run-dir", self.run_dir])
        self.assertEqual(code, 1, out)
        self.assertIn("HUNG: 03", out)
        self.assertIn("stop_loop.sh", out)

    def test_check_uses_the_newest_deadline_change(self):
        for stage in ("03", "05", "06", "07", "08"):
            self.write_stage(stage, "DONE")
        self.write_stage("03", "STARTED", pid=99999,
                         pid_start="Mon Sep 28 15:55:00 2026",
                         command="bash loop_until.sh",
                         deadline_at="2000-01-01T00:00:00+00:00")
        with open(os.path.join(self.ledger(), "events.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": "2026-09-28T00:00:00+00:00",
                                 "kind": "deadline-change",
                                 "deadline_epoch": 4102444800}) + "\n")
        with mock.patch.object(run_ledger, "_ps_read",
                               return_value=("ok", "Mon Sep 28 15:55:00 2026", "bash loop_until.sh")):
            code, out, _err = self.cli(["check", "--run-dir", self.run_dir])
        self.assertEqual(code, 1, out)
        self.assertIn("BLOCKED: 03", out)
        self.assertNotIn("HUNG", out)

    def test_check_unknown_liveness_is_no_data_not_failed(self):
        self.write_stage("03", "STARTED", pid=99999,
                         pid_start="Mon Sep 28 15:55:00 2026",
                         command="bash loop_until.sh")
        self.write_stage("05", "DONE")
        with mock.patch.object(run_ledger, "_ps_read",
                               return_value=("unknown", None, None)):
            code, out, _err = self.cli(["check", "--run-dir", self.run_dir])
        self.assertEqual(code, 1, out)
        self.assertIn("BLOCKED: 03", out)
        self.assertNotIn("died without an end", out)
        self.assertIn("unknown", out)

    def test_check_missing_03_05_rows_with_receipt_reconstructs_done(self):
        self.write_receipt(end_state="DONE")
        for stage in ("06", "07", "08"):
            self.write_stage(stage, "DONE")
        code, out, _err = self.cli(["check", "--run-dir", self.run_dir])
        self.assertEqual(code, 0, out)
        self.assertIn("CLOSED", out)

    def test_check_corrupt_receipt_refuses_naming_the_file(self):
        folder = os.path.join(self.run_dir, "receipt")
        os.makedirs(folder)
        with open(os.path.join(folder, "receipt.json"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        code, out, _err = self.cli(["check", "--run-dir", self.run_dir])
        self.assertEqual(code, 2, out)
        self.assertIn("receipt.json", out)

    def test_check_truncated_stage_receipt_refuses_naming_the_file(self):
        os.makedirs(self.ledger())
        with open(os.path.join(self.ledger(), "03-run.json"), "w", encoding="utf-8") as fh:
            fh.write("{truncated")
        code, out, _err = self.cli(["check", "--run-dir", self.run_dir])
        self.assertEqual(code, 2, out)
        self.assertIn("03-run.json", out)

    def test_check_last_missing_or_dangling_is_no_data(self):
        code, _out, _err = self.cli(["check", "--last"])
        self.assertEqual(code, 3)
        with open(os.path.join(self.runs_root, "LAST"), "w", encoding="utf-8") as fh:
            fh.write(os.path.join(self.root, "not-there") + "\n")
        code2, _out2, _err2 = self.cli(["check", "--last"])
        self.assertEqual(code2, 3)
        with open(os.path.join(self.runs_root, "LAST"), "w", encoding="utf-8") as fh:
            fh.write(self.run_dir + "\n")
        code3, _out3, _err3 = self.cli(["check", "--last"])
        self.assertEqual(code3, 3)

    def test_check_abandoned_older_than_a_restarted_stage_does_not_close(self):
        self.write_stage("03", "STARTED", started_at="2026-09-29T00:00:00+00:00",
                         pid=99999, pid_start="Mon Sep 28 15:55:00 2026",
                         command="bash loop_until.sh")
        self.write_stage("05", "DONE")
        with open(os.path.join(self.ledger(), "ABANDONED.json"), "w", encoding="utf-8") as fh:
            json.dump({"words": "stopping", "at": "2026-09-28T00:00:00+00:00",
                       "pending": "03-run"}, fh)
        with mock.patch.object(run_ledger, "_ps_read",
                               return_value=("ok", "Mon Sep 28 15:55:00 2026", "bash loop_until.sh")):
            code, out, _err = self.cli(["check", "--run-dir", self.run_dir])
        self.assertEqual(code, 1, out)
        self.assertIn("BLOCKED", out)

    def test_check_abandoned_newer_than_started_closes_by_abandon(self):
        for stage in ("03", "05", "06", "07", "08"):
            self.write_stage(stage, "DONE")
        self.write_stage("03", "STARTED", started_at="2026-09-28T00:00:00+00:00",
                         pid=99999, pid_start="Mon Sep 28 15:55:00 2026",
                         command="bash loop_until.sh")
        with open(os.path.join(self.ledger(), "ABANDONED.json"), "w", encoding="utf-8") as fh:
            json.dump({"words": "owner stopped it", "at": "2026-09-29T00:00:00+00:00",
                       "pending": "03-run"}, fh)
        code, out, _err = self.cli(["check", "--run-dir", self.run_dir])
        self.assertEqual(code, 0, out)
        self.assertIn("CLOSED-BY-ABANDON", out)

    def test_check_under_off_prints_the_verdict_and_writes_nothing(self):
        for stage in ("03", "05", "06", "07", "08"):
            self.write_stage(stage, "DONE")
        state_path = os.path.join(self.ledger(), "STATE")
        if os.path.exists(state_path):
            os.remove(state_path)
        code, out, _err = self.cli(["check", "--run-dir", self.run_dir],
                                   env={"BROTHER_RUNFLOW": "off"})
        self.assertEqual(code, 0, out)
        self.assertIn("CLOSED", out)
        self.assertFalse(os.path.exists(state_path))

    def test_reconstruct_under_off_writes_nothing(self):
        log_path = os.path.join(self.root, "driver.log")
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write("RUN START\nRUN END\n")
        code, out, _err = self.cli(["reconstruct", "--log", log_path, "--run-dir", self.run_dir],
                                   env={"BROTHER_RUNFLOW": "off"})
        self.assertEqual(code, 0)
        self.assertIn("OFF: nothing recorded", out)
        self.assertFalse(os.path.exists(self.ledger()))

    def test_reconstruct_two_run_starts_is_no_data_and_writes_nothing(self):
        log_path = os.path.join(self.root, "driver.log")
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write("RUN START a\nRUN START b\nRUN END\n")
        code, out, _err = self.cli(["reconstruct", "--log", log_path, "--run-dir", self.run_dir])
        self.assertEqual(code, 3, out)
        self.assertFalse(os.path.exists(self.ledger()))

    def test_reconstruct_never_overwrites_existing_rows(self):
        self.write_stage("03", "DONE", reason="kept")
        self.write_receipt(end_state="DONE")
        log_path = os.path.join(self.root, "driver.log")
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write("2026-09-28T00:00:00+00:00 RUN START\n2026-09-28T00:01:00+00:00 RUN END\n")
        with open(os.path.join(self.ledger(), "03-run.json"), "rb") as fh:
            before = fh.read()
        code, out, _err = self.cli(["reconstruct", "--log", log_path, "--run-dir", self.run_dir])
        self.assertEqual(code, 0, out)
        with open(os.path.join(self.ledger(), "03-run.json"), "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertIn("kept", out)
        self.assertTrue(os.path.exists(os.path.join(self.ledger(), "05-end.json")))

    def test_reconstruct_missing_05_row_writes_failed_with_no_receipt(self):
        log_path = os.path.join(self.root, "driver.log")
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write("RUN START\nRUN END\n")
        code, out, _err = self.cli(["reconstruct", "--log", log_path, "--run-dir", self.run_dir])
        self.assertEqual(code, 0, out)
        record = self.read_json(os.path.join(self.ledger(), "05-end.json"))
        self.assertEqual(record["state"], "FAILED")
        self.assertIn("no receipt", record["reason"])

    def test_reconstruct_corrupt_receipt_refuses_naming_the_file(self):
        folder = os.path.join(self.run_dir, "receipt")
        os.makedirs(folder)
        with open(os.path.join(folder, "receipt.json"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        log_path = os.path.join(self.root, "driver.log")
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write("2026-09-28T00:00:00+00:00 RUN START\n2026-09-28T00:01:00+00:00 RUN END\n")
        code, out, _err = self.cli(["reconstruct", "--log", log_path, "--run-dir", self.run_dir])
        self.assertEqual(code, 2, out)
        self.assertIn("receipt.json", out)
        self.assertFalse(os.path.exists(self.ledger()))

    def test_readers_raise_value_error_on_a_wrong_type(self):
        with self.assertRaises(ValueError):
            run_ledger.check(run_dir=b"x")
        with self.assertRaises(ValueError):
            run_ledger.check(use_last=1)
        with self.assertRaises(ValueError):
            run_ledger.status(run_dir=7)
        with self.assertRaises(ValueError):
            run_ledger.status(use_last=None)
        with self.assertRaises(ValueError):
            run_ledger.reconstruct(log=None, run_dir=self.run_dir)
        with self.assertRaises(ValueError):
            run_ledger.reconstruct(log="/tmp/x", run_dir=None)


if __name__ == "__main__":
    unittest.main()
