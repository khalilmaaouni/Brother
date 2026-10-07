"""Tests for the MG1.b half of scripts/merge_gate.py: the ledger row, its writer and its verifier.

NO SUBPROCESS HERE. The safety screen admits no subprocess import in a test, so git and the gate child are a WORLD:
a stand-in runner that answers the few git calls merge_gate makes from two dicts of file contents (hub main, and the
candidate tree), and records the environment the gate child was given. Each fixture isolates one refusal.

Run: python3 -B scripts/test_merge_gate.py
"""
from __future__ import annotations

import inspect
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import merge_gate  # noqa: E402

TREE = "a" * 40
OTHER_TREE = "c" * 40
SUMMARY_OK = "ran checks\n\npass 5   fail 0   no-data 0\n"
GATE_PATH = "scripts/required_fast.sh"


class Proc:
    def __init__(self, code, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


def _real(rel):
    with open(os.path.join(ROOT, rel), "rb") as handle:
        return handle.read()


def main_files():
    files = {name: b"x\n" for name in (
        "scripts/heavy_slot.py", "scripts/git_location_guard.py", "scripts/test_version_truth.py",
        "scripts/unrelated_tool.py", "tests/test_surface.py", "products/brothermode/tools/test_bm_clock_guard.py",
        "products/brothermode/scripts/verify-install.sh", "products/brothersbe/evals/test_no_data_class.py",
        "plugin/runtime/brother/core/test_alpha.py", "plugin/runtime/brother/test_beta.py")}
    files[GATE_PATH] = _real(GATE_PATH)
    files["scripts/plugin_runtime_fast_discover.py"] = _real("scripts/plugin_runtime_fast_discover.py")
    return files


class World:
    """The stand-in runner: git answers from file dicts, `sh` is the stand-in gate."""

    def __init__(self):
        self.main = main_files()
        self.trees = {}
        self.gate_code = 0
        self.gate_out = SUMMARY_OK
        self.gate_env = None
        self.worktree_tree = TREE   # what `git rev-parse HEAD^{tree}` answers in the scratch worktree

    def candidate(self, tree=TREE, **edits):
        files = dict(self.main)
        for path, data in edits.items():
            files[path.replace("__", "/").replace("_dot_", ".")] = data
        self.trees[tree] = files
        return files

    def files_of(self, ref):
        return self.trees.get(ref, self.main)

    def __call__(self, argv, cwd=None, env=None):
        if argv[0] == "sh":
            self.gate_env = dict(env) if env is not None else None
            return Proc(self.gate_code, self.gate_out, "")
        if argv[:3] == ["git", "rev-parse", "HEAD^{tree}"]:
            return Proc(0, self.worktree_tree + "\n")
        if argv[:3] == ["git", "cat-file", "-e"]:
            return Proc(0 if argv[3][:-len("^{tree}")] in self.trees else 1)
        if argv[:2] == ["git", "show"]:
            ref, _, path = argv[2].partition(":")
            data = self.files_of(ref).get(path)
            return Proc(128, "", "missing") if data is None else Proc(0, data.decode("utf-8"))
        if argv[:4] == ["git", "ls-tree", "-r", "--name-only"]:
            return Proc(0, "\n".join(sorted(self.files_of(argv[4]))) + "\n")
        if argv[:3] == ["git", "diff", "--name-only"]:
            left, right = self.files_of(argv[3]), self.files_of(argv[4])
            names = sorted(n for n in set(left) | set(right) if left.get(n) != right.get(n))
            return Proc(0, "\n".join(names) + "\n")
        return Proc(129, "", "unknown call")


class GateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="merge-gate-b-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.clone = os.path.join(self.tmp, "clone")
        os.makedirs(os.path.join(self.clone, ".git"))
        self.world = World()
        self.env = {}

    def log(self, text=SUMMARY_OK):
        path = os.path.join(self.tmp, "run.log")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def record(self, rc=0, text=SUMMARY_OK, tree=TREE, **edits):
        if tree not in self.world.trees or edits:
            self.world.candidate(tree, **edits)
        return merge_gate.record_row(self.clone, tree, rc, self.log(text), "12", "h" * 40, "base-sha",
                                     self.env, runner=self.world)

    def resign(self, row, **over):
        body = {k: v for k, v in row.items() if k != "mac"}
        body.update(over)
        return merge_gate.sign_row(self.clone, body)

    def verify(self, row, offset=0.0, env=None):
        return merge_gate.verify_row(self.clone, row, time.time() + offset,
                                     self.env if env is None else env, runner=self.world)

    def assertRefused(self, verdict, code):
        self.assertFalse(verdict[0], verdict)
        self.assertTrue(verdict[1].startswith(code + ":"), verdict)

    def ledger(self):
        return merge_gate.ledger_path(self.clone, self.env)


class TestRecordRow(GateCase):
    def test_a_recorded_row_carries_every_field_and_passes(self):
        row = self.record()
        for key in merge_gate.ROW_KEYS:
            self.assertIn(key, row)
        self.assertEqual(row["schema"], 1)
        self.assertEqual(row["counts"], {"pass": 5, "fail": 0, "no_data": 0})
        self.assertEqual(row["by"].split("@", 1)[1], os.uname().nodename)
        self.assertEqual(row["checks_changed"], [])
        self.assertTrue(row["finished"][-6] in "+-" and "T" in row["started"])
        self.assertTrue(self.verify(row)[0], self.verify(row))

    def test_the_log_is_copied_beside_the_ledger_and_hashed(self):
        row = self.record()
        copy = os.path.join(os.path.dirname(self.ledger()), row["log"])
        with open(copy, "rb") as handle:
            self.assertEqual(handle.read().decode("utf-8"), SUMMARY_OK)

    def test_the_key_is_0600_and_the_directories_0700(self):
        self.record()
        key = os.path.join(os.path.dirname(self.ledger()), "key")
        self.assertEqual(os.stat(key).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.dirname(self.ledger())).st_mode & 0o077, 0)

    def test_a_caller_cannot_supply_counts(self):
        self.assertNotIn("counts", inspect.signature(merge_gate.record_row).parameters)
        with self.assertRaises(TypeError):
            merge_gate.record_row(self.clone, TREE, 0, self.log(), "1", "h", "b", {}, counts={}, runner=self.world)

    def test_a_row_is_one_line_in_the_ledger(self):
        self.record()
        self.record()
        with open(self.ledger(), "rb") as handle:
            lines = handle.read().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(os.path.exists(self.ledger() + ".lock"))

    def test_parallel_appends_never_tear_a_line(self):
        path = self.ledger()
        os.makedirs(os.path.dirname(path), exist_ok=True)

        def work(n):
            for i in range(20):
                merge_gate._append_line(path, json.dumps({"n": n, "i": i, "pad": "z" * 5000}))
        threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(merge_gate.read_ledger(path)), 120)

    def test_a_log_with_no_summary_records_no_counts(self):
        row = self.record(text="nothing useful\n")
        self.assertNotIn("counts", row)

    def test_hostile_arguments_are_refused(self):
        good = (self.clone, TREE, 0, self.log(), "1", "h", "b")
        for index, bad in ((0, None), (0, 5), (1, None), (1, "short"), (2, True), (2, "0"), (2, None),
                           (3, []), (4, 7), (5, None), (6, b"x")):
            args = list(good)
            args[index] = bad
            with self.assertRaises(merge_gate.MergeGateError, msg=repr((index, bad))):
                merge_gate.record_row(*args, {}, runner=self.world)
        with self.assertRaises(merge_gate.MergeNoData):
            merge_gate.record_row(*good, {})
        with self.assertRaises(merge_gate.MergeGateError):
            merge_gate.record_row(*good, {}, runner="git")


class TestVerifyRow(GateCase):
    def test_stale_row_is_refused(self):
        row = self.record()
        self.assertTrue(self.verify(row, 47 * 3600)[0])
        self.assertRefused(self.verify(row, 49 * 3600), "STALE")

    def test_the_age_limit_can_be_set(self):
        row = self.record()
        self.assertRefused(self.verify(row, 2 * 3600, {"MERGE_GATE_MAX_AGE_H": "1"}), "STALE")
        self.assertRefused(self.verify(row, 0, {"MERGE_GATE_MAX_AGE_H": "nan"}), "SCHEMA")

    def test_foreign_host_is_refused_and_a_trusted_host_is_not(self):
        row = self.resign(self.record(), by="someone@elsewhere.invalid")
        self.assertRefused(self.verify(row), "FOREIGN")
        self.assertTrue(self.verify(row, 0, {"MERGE_GATE_TRUSTED_HOSTS": "x, elsewhere.invalid"})[0])

    def test_a_tree_absent_from_the_clone_is_refused(self):
        row = self.record()
        del self.world.trees[TREE]
        self.assertRefused(self.verify(row), "TREE-ABSENT")

    def test_a_row_without_counts_is_no_summary(self):
        self.assertRefused(self.verify(self.record(text="no summary here\n")), "NO-SUMMARY")

    def test_rc_zero_with_fail_above_zero_is_a_contradiction(self):
        row = self.record(rc=0, text="pass 3   fail 1   no-data 0\nFAILED: alpha\n")
        self.assertRefused(self.verify(row), "CONTRADICTION")

    def test_rc_one_with_fail_zero_and_no_names_is_a_contradiction(self):
        row = self.record(rc=1, text="pass 3   fail 0   no-data 0\n")
        self.assertRefused(self.verify(row), "CONTRADICTION")

    def test_an_honest_failure_is_a_fail_not_a_pass(self):
        row = self.record(rc=1, text="pass 3   fail 1   no-data 0\nFAILED: alpha\n")
        self.assertEqual(row["failed"], ["alpha"])
        self.assertRefused(self.verify(row), "FAIL")

    def test_counts_that_disagree_with_the_log_are_a_contradiction(self):
        row = self.resign(self.record(), counts={"pass": 9, "fail": 0, "no_data": 0})
        self.assertRefused(self.verify(row), "CONTRADICTION")

    def test_a_missing_log_is_refused(self):
        row = self.record()
        os.unlink(os.path.join(os.path.dirname(self.ledger()), row["log"]))
        self.assertRefused(self.verify(row), "LOG-MISSING")

    def test_an_edited_log_is_refused(self):
        row = self.record()
        with open(os.path.join(os.path.dirname(self.ledger()), row["log"]), "a") as handle:
            handle.write("extra\n")
        self.assertRefused(self.verify(row), "LOG-HASH")

    def test_a_log_path_leaving_the_ledger_directory_is_refused(self):
        self.assertRefused(self.verify(self.resign(self.record(), log="../key")), "SCHEMA")

    def test_schema_refusals(self):
        row = self.record()
        missing = {k: v for k, v in row.items() if k != "pid"}
        for bad in (self.resign(row, schema=2), self.resign(row, schema=True), self.resign(row, pid="1"),
                    self.resign(row, rc=True), self.resign(row, failed="x"), self.resign(row, by="nohost"),
                    self.resign(row, counts={"pass": 1, "fail": 0}), self.resign(row, finished="yesterday"),
                    self.resign(row, tree="short"), self.resign(missing)):
            self.assertRefused(self.verify(bad), "SCHEMA")
        stripped = {k: v for k, v in row.items() if k != "pid"}
        self.assertRefused(self.verify(stripped), "SCHEMA")

    def test_hostile_rows_and_arguments_are_refused_not_crashed(self):
        for bad in (None, 5, "row", [], True, float("nan"), b"x"):
            self.assertRefused(self.verify(bad), "SCHEMA")
        row = self.record()
        for bad in (None, True, float("nan"), "now", [], float("inf")):
            with self.assertRaises(merge_gate.MergeGateError, msg=repr(bad)):
                merge_gate.verify_row(self.clone, row, bad, {}, runner=self.world)
        for bad in (None, 5, []):
            with self.assertRaises(merge_gate.MergeGateError):
                merge_gate.verify_row(bad, row, 1.0, {}, runner=self.world)
        with self.assertRaises(merge_gate.MergeNoData):
            merge_gate.verify_row(self.clone, row, time.time(), {})
        unhashable = dict(row, failed=[["x"]], tree=["a"])
        self.assertRefused(self.verify(unhashable), "SCHEMA")

    def test_a_tampered_row_fails_the_mac(self):
        row = dict(self.record(), pr="999")
        self.assertRefused(self.verify(row), "MAC")

    def test_a_row_with_no_mac_is_refused(self):
        row = {k: v for k, v in self.record().items() if k != "mac"}
        self.assertRefused(self.verify(row), "SCHEMA")
        self.assertRefused(self.verify(dict(row, mac="0" * 64)), "MAC")

    def test_a_key_with_group_permissions_is_refused(self):
        row = self.record()
        os.chmod(os.path.join(os.path.dirname(self.ledger()), "key"), 0o640)
        self.assertRefused(self.verify(row), "MAC")

    def test_a_row_with_no_data_is_refused_with_the_names(self):
        row = self.record(rc=0, text="pass 4   fail 0   no-data 1\nNO-DATA: slow-check  (not a pass, and not a failure)\n")
        self.assertEqual(row["nodata_names"], ["slow-check"])
        verdict = self.verify(row)
        self.assertRefused(verdict, "NO-DATA-ROW")
        self.assertIn("slow-check", verdict[1])

    def test_an_edited_gate_script_is_refused(self):
        row = self.resign(self.record(), gate_sha256="e" * 64)
        self.assertRefused(self.verify(row), "GATE-EDITED")

    def test_an_unreadable_gate_at_main_is_refused_never_waved_through(self):
        row = self.record()
        del self.world.main[GATE_PATH]
        with self.assertRaises(merge_gate.MergeNoData):
            self.verify(row)

    def test_a_legacy_row_is_always_refused(self):
        tsv = os.path.join(self.tmp, "seq.tsv")
        with open(tsv, "w") as handle:
            handle.write("%s\t0\t2026-10-03T22:00:00Z\t12\t%s\t%s\t%s\n" % (TREE, "h" * 40, "1" * 64, "1" * 64))
        self.assertEqual(merge_gate.import_legacy_tsv(self.clone, tsv, self.env), 1)
        row = merge_gate.read_ledger(self.ledger())[0]
        self.assertTrue(row["legacy"])
        self.assertNotIn("counts", row)
        self.assertRefused(self.verify(row), "LEGACY")
        self.assertRefused(self.verify(merge_gate.sign_row(self.clone, row)), "LEGACY")

    def test_a_bad_legacy_file_raises_and_writes_nothing(self):
        tsv = os.path.join(self.tmp, "seq.tsv")
        with open(tsv, "w") as handle:
            handle.write("%s\t0\twhen\t12\thead\nshort\tline\n" % TREE)
        with self.assertRaises(merge_gate.MergeNoData):
            merge_gate.import_legacy_tsv(self.clone, tsv, self.env)
        self.assertFalse(os.path.exists(self.ledger()))
        for bad in (None, 5, "/no/such/file.tsv"):
            with self.assertRaises(merge_gate.MergeGateError):
                merge_gate.import_legacy_tsv(self.clone, bad, self.env)


class TestChecksEdited(GateCase):
    def edited(self, path):
        row = self.record(**{path.replace("/", "__").replace(".", "_dot_"): b"changed\n"})
        return row

    def test_an_edited_check_script_is_refused(self):
        row = self.edited("scripts/test_version_truth.py")
        self.assertEqual(row["checks_changed"], ["scripts/test_version_truth.py"])
        self.assertRefused(self.verify(row), "CHECKS-EDITED")

    def test_an_edit_outside_the_closure_is_a_pass(self):
        row = self.edited("scripts/unrelated_tool.py")
        self.assertEqual(row["checks_changed"], [])
        self.assertTrue(self.verify(row)[0], self.verify(row))

    def test_a_check_outside_scripts_is_in_the_closure(self):
        row = self.edited("tests/test_surface.py")
        self.assertEqual(row["checks_changed"], ["tests/test_surface.py"])
        self.assertRefused(self.verify(row), "CHECKS-EDITED")

    def test_a_script_the_gate_runs_outside_run_check_is_in_the_closure(self):
        row = self.edited("scripts/heavy_slot.py")
        self.assertEqual(row["checks_changed"], ["scripts/heavy_slot.py"])
        self.assertRefused(self.verify(row), "CHECKS-EDITED")

    def test_the_git_location_guard_is_in_the_closure(self):
        row = self.edited("scripts/git_location_guard.py")
        self.assertEqual(row["checks_changed"], ["scripts/git_location_guard.py"])
        self.assertRefused(self.verify(row), "CHECKS-EDITED")

    def test_a_discovered_test_is_in_the_closure(self):
        row = self.edited("plugin/runtime/brother/core/test_alpha.py")
        self.assertEqual(row["checks_changed"], ["plugin/runtime/brother/core/test_alpha.py"])
        self.assertRefused(self.verify(row), "CHECKS-EDITED")

    def test_a_dash_m_module_is_in_the_closure(self):
        row = self.edited("products/brothermode/tools/test_bm_clock_guard.py")
        self.assertEqual(row["checks_changed"], ["products/brothermode/tools/test_bm_clock_guard.py"])
        self.assertRefused(self.verify(row), "CHECKS-EDITED")

    def test_a_script_named_relative_to_a_cd_is_in_the_closure(self):
        row = self.edited("products/brothermode/scripts/verify-install.sh")
        self.assertEqual(row["checks_changed"], ["products/brothermode/scripts/verify-install.sh"])
        self.assertRefused(self.verify(row), "CHECKS-EDITED")
        row = self.edited("products/brothersbe/evals/test_no_data_class.py")
        self.assertRefused(self.verify(row), "CHECKS-EDITED")

    def test_the_closure_does_not_contain_the_gate_itself(self):
        closure = merge_gate.gate_closure(self.clone, "hub/main", self.world)
        self.assertNotIn(GATE_PATH, closure)
        self.assertIn("scripts/plugin_runtime_fast_discover.py", closure)

    def test_gate_closure_refuses_hostile_input(self):
        for bad in (None, 5, [], True):
            with self.assertRaises(merge_gate.MergeGateError):
                merge_gate.gate_closure(bad, "hub/main", self.world)
            with self.assertRaises(merge_gate.MergeGateError):
                merge_gate.gate_closure(self.clone, bad, self.world)
        with self.assertRaises(merge_gate.MergeNoData):
            merge_gate.gate_closure(self.clone, "hub/main")


class TestLedger(GateCase):
    def write(self, text):
        os.makedirs(os.path.dirname(self.ledger()), exist_ok=True)
        with open(self.ledger(), "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_a_torn_line_raises(self):
        self.write('{"a": 1}\n{"b": \n')
        with self.assertRaises(merge_gate.LedgerCorrupt):
            merge_gate.read_ledger(self.ledger())

    def test_a_non_object_line_raises(self):
        self.write('{"a": 1}\n[1, 2]\n')
        with self.assertRaises(merge_gate.LedgerCorrupt):
            merge_gate.read_ledger(self.ledger())

    def test_a_good_ledger_reads_in_order_and_a_missing_one_is_no_data(self):
        self.write('{"a": 1}\n\n{"a": 2}\n')
        self.assertEqual([r["a"] for r in merge_gate.read_ledger(self.ledger())], [1, 2])
        with self.assertRaises(merge_gate.MergeNoData):
            merge_gate.read_ledger(os.path.join(self.tmp, "absent"))
        for bad in (None, 5, []):
            with self.assertRaises(merge_gate.MergeGateError):
                merge_gate.read_ledger(bad)

    def test_a_corrupt_ledger_blocks_the_tree(self):
        self.record()
        with open(self.ledger(), "a") as handle:
            handle.write("{torn\n")
        ok, why = merge_gate.verify_newest(self.clone, TREE, time.time(), self.env, self.world)
        self.assertFalse(ok)
        self.assertTrue(why.startswith("NO-DATA:"), why)

    def test_the_newest_row_decides_a_later_fail(self):
        self.record(rc=0)
        self.record(rc=1, text="pass 3   fail 1   no-data 0\nFAILED: alpha\n")
        ok, why = merge_gate.verify_newest(self.clone, TREE, time.time(), self.env, self.world)
        self.assertFalse(ok, why)
        self.assertTrue(why.startswith("FAIL:"), why)

    def test_the_newest_row_decides_a_later_pass_and_names_the_flake(self):
        self.record(rc=1, text="pass 3   fail 1   no-data 0\nFAILED: alpha\n")
        self.record(rc=0)
        ok, why = merge_gate.verify_newest(self.clone, TREE, time.time(), self.env, self.world)
        self.assertTrue(ok, why)
        self.assertIn("flaky: earlier FAIL", why)

    def test_no_row_for_the_tree_is_no_data(self):
        self.record()
        ok, why = merge_gate.verify_newest(self.clone, OTHER_TREE, time.time(), self.env, self.world)
        self.assertFalse(ok)
        self.assertTrue(why.startswith("NO-DATA:"), why)


class TestVerifyTree(GateCase):
    """B2, review 2026-10-05: verify_tree, the verdict the `verify` command, the precompute skip and wait_ready read,
    took the FIRST signed rc 0 row and never applied verify_row. Each case isolates ONE row rule through verify_tree."""

    def append(self, row):
        with open(self.ledger(), "a") as handle:
            handle.write(json.dumps(row) + "\n")

    def verdict(self, runner="world"):
        return merge_gate.verify_tree(self.clone, TREE, self.env, self.world if runner == "world" else runner)

    def test_a_clean_row_passes(self):
        self.record()
        self.assertEqual(self.verdict()[0], "PASS")

    def test_changed_checks_refuse_automatic_acceptance(self):
        self.append(self.resign(self.record(), checks_changed=["scripts/test_merge_gate.py"]))
        verdict, why = self.verdict()
        self.assertEqual(verdict, "FAIL", why)
        self.assertTrue(why.startswith("CHECKS-EDITED:"), why)

    def test_an_edited_gate_script_refuses(self):
        self.append(self.resign(self.record(), gate_sha256="0" * 64))
        verdict, why = self.verdict()
        self.assertEqual(verdict, "FAIL", why)
        self.assertTrue(why.startswith("GATE-EDITED:"), why)

    def test_a_foreign_row_never_passes(self):
        self.append(self.resign(self.record(), by="someone@elsewhere.example"))
        verdict, why = self.verdict()
        self.assertEqual(verdict, "NO-DATA", why)
        self.assertTrue(why.startswith("FOREIGN:"), why)

    def test_a_stale_row_never_passes(self):
        self.append(self.resign(self.record(), finished="2020-01-01T00:00:00+00:00"))
        verdict, why = self.verdict()
        self.assertEqual(verdict, "NO-DATA", why)
        self.assertTrue(why.startswith("STALE:"), why)

    def test_a_newer_fail_beats_an_earlier_pass(self):
        self.record(rc=0)
        self.record(rc=1, text="pass 3   fail 1   no-data 0\nFAILED: alpha\n")
        verdict, why = self.verdict()
        self.assertEqual(verdict, "FAIL", why)

    def test_a_newer_pass_beats_an_earlier_fail_and_names_it(self):
        self.record(rc=1, text="pass 3   fail 1   no-data 0\nFAILED: alpha\n")
        self.record(rc=0)
        verdict, why = self.verdict()
        self.assertEqual(verdict, "PASS", why)
        self.assertIn("flaky: earlier FAIL", why)

    def test_the_gate_script_is_read_from_the_remote_tracking_ref(self):
        # M3 sibling: the short name hub/main resolves a tag refs/tags/hub/main first; here that name holds another
        # gate script, so reading it would refuse a clean row as GATE-EDITED
        self.record()
        self.world.trees["hub/main"] = dict(self.world.main, **{GATE_PATH: b"#!/bin/sh\nexit 0\n"})
        self.assertEqual(self.verdict()[0], "PASS", self.verdict())

    def test_no_runner_is_no_data(self):
        self.record()
        self.assertEqual(self.verdict(None)[0], "NO-DATA")

    def test_the_verify_command_applies_the_row_rules(self):
        self.append(self.resign(self.record(), checks_changed=["scripts/test_merge_gate.py"]))
        self.assertEqual(merge_gate.main(["verify", self.clone, TREE], runner=self.world), 1)

    def test_the_verify_command_passes_a_clean_row(self):
        self.record()
        self.assertEqual(merge_gate.main(["verify", self.clone, TREE], runner=self.world), 0)


class TestRunGate(GateCase):
    def setUp(self):
        super().setUp()
        self.wt = os.path.join(self.tmp, "wt")
        os.makedirs(os.path.join(self.wt, "scripts"))
        with open(os.path.join(self.wt, GATE_PATH), "wb") as handle:
            handle.write(self.world.main[GATE_PATH])
        self.world.candidate(TREE)

    def gate(self, env):
        return merge_gate.run_gate(self.clone, self.wt, TREE, "12", "h" * 40, "base-sha", env, runner=self.world)

    def test_a_clean_gate_records_a_passing_row(self):
        row = self.gate(self.env)
        self.assertEqual(row["rc"], 0)
        self.assertTrue(self.verify(row)[0], self.verify(row))
        self.assertEqual(len(merge_gate.read_ledger(self.ledger())), 1)

    def test_the_child_gets_no_merge_gate_variable(self):
        seam = os.path.join(self.tmp, "seam", "ledger.jsonl")
        self.gate({"PATH": "/usr/bin", "MERGE_GATE_LEDGER": seam, "MERGE_GATE_TEST": "1"})
        self.assertFalse([n for n in self.world.gate_env if n.startswith("MERGE_GATE_")])

    def test_the_child_environment_is_the_fixed_allowlist(self):
        self.gate({"PATH": "/usr/bin", "HOME": "/h", "REQUIRED_FAST_JOBS": "9", "BROTHER_RUN_DIR": "/r",
                   "GIT_DIR": "/g", "CUSTOM": "1", "LANG": "C"})
        self.assertEqual(self.world.gate_env, {"PATH": "/usr/bin", "HOME": "/h", "LANG": "C"})
        self.assertTrue(set(self.world.gate_env) <= set(merge_gate.CHILD_ENV_ALLOW))

    def test_the_row_carries_the_exit_code_the_child_gave(self):
        self.world.gate_code = 1
        self.world.gate_out = "pass 3   fail 0   no-data 0\n"
        row = self.gate(self.env)
        self.assertEqual(row["rc"], 1)
        self.assertRefused(self.verify(row), "CONTRADICTION")

    def test_there_is_no_record_subcommand_and_rc_is_never_typed(self):
        self.assertFalse(hasattr(merge_gate, "record"))
        self.assertEqual(merge_gate.main(["record", self.clone, TREE, "0"]), 2)
        self.assertFalse(os.path.exists(self.ledger()))

    def test_the_gate_subcommand_runs_the_gate_through_a_runner_and_refuses_without_one(self):
        argv = ["gate", self.clone, self.wt, TREE, "12", "h" * 40, "base-sha"]
        self.assertEqual(merge_gate.main(argv), 2)
        self.assertFalse(os.path.exists(self.ledger()))
        self.assertEqual(merge_gate.main(argv, runner=self.world), 0)
        self.assertEqual(len(merge_gate.read_ledger(self.ledger())), 1)

    def test_a_worktree_whose_tree_is_not_the_one_to_record_runs_nothing_and_writes_nothing(self):
        # review 2026-10-04 (H1): a worktree at a PR's own head tested the head alone while the row named the merged
        # tree; the gate itself refuses before running, so no caller can record a row for a tree it did not test
        self.world.worktree_tree = OTHER_TREE
        with self.assertRaises(merge_gate.MergeNoData) as caught:
            self.gate(self.env)
        self.assertIn("not the tree", str(caught.exception))
        self.assertIsNone(self.world.gate_env, "the gate ran in a worktree holding another tree")
        self.assertFalse(os.path.exists(self.ledger()), "a row was written for a tree the gate never tested")

    def test_the_gate_script_hash_is_the_one_that_ran(self):
        with open(os.path.join(self.wt, GATE_PATH), "ab") as handle:
            handle.write(b"# edited\n")
        self.assertRefused(self.verify(self.gate(self.env)), "GATE-EDITED")

    def test_hostile_arguments_are_refused(self):
        for bad in (None, 5, [], True):
            with self.assertRaises(merge_gate.MergeGateError):
                merge_gate.run_gate(bad, self.wt, TREE, "1", "h", "b", {}, runner=self.world)
            with self.assertRaises(merge_gate.MergeGateError):
                merge_gate.run_gate(self.clone, bad, TREE, "1", "h", "b", {}, runner=self.world)
        for bad in ("env", 5, [("A", "b")], {1: "x"}):
            with self.assertRaises(merge_gate.MergeGateError):
                self.gate(bad)
        with self.assertRaises(merge_gate.MergeNoData):
            merge_gate.run_gate(self.clone, self.wt, TREE, "1", "h", "b", {})
        with self.assertRaises(merge_gate.MergeNoData):
            merge_gate.run_gate(self.clone, os.path.join(self.tmp, "nowhere"), TREE, "1", "h", "b", {},
                                runner=self.world)


if __name__ == "__main__":
    unittest.main()
