#!/usr/bin/env python3
"""test_codex_smoke: drives scripts/codex_smoke.py's two decision points in
BOTH directions, which is the part of it that can lie.

The smoke's real work (a Codex binary, a plugin install, a hook install, a
model turn) is not simulated here: that is what the script itself does when
run, and its own verdict line is the evidence. What is tested is the two
places a wrong answer would look like a right one:

1. THE NO-DATA GUARD. A machine with no Codex binary must report NO-DATA and
   exit 2, never 0. A guard nobody drove backwards is a claim, so the absent
   case AND the present case are both driven.
2. THE RECEIPT READER. `receipt_lines` decides whether a run produced a
   receipt naming a changed file. A reader that returned lines for a receipt
   with no changed file would turn a refused run into a passing smoke, which
   is exactly the state the C7 lane hit on its first run: the engine wrote a
   receipt, and every unit in it was refused.
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import codex_smoke  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


class TheNoDataGuard(unittest.TestCase):
    def test_an_absent_binary_is_no_data_and_never_a_pass(self):
        self.assertEqual(codex_smoke.main(["--codex-bin",
                                           "/no/such/codex/binary"]), 2)

    def test_a_directory_is_not_an_executable_binary(self):
        # The other half of the same guard: os.path.isfile, not os.path.exists.
        self.assertEqual(codex_smoke.main(["--codex-bin", HERE]), 2)

    def test_the_real_binary_passes_the_guard(self):
        # The POSITIVE control. Without it the guard could refuse everything
        # and still look correct above.
        real = codex_smoke.DEFAULT_CODEX
        if not (os.path.isfile(real) and os.access(real, os.X_OK)):
            self.skipTest("NO-DATA: no Codex binary at %s on this machine, so "
                          "the guard's positive direction is unproven" % real)
        self.assertTrue(os.path.isfile(real) and os.access(real, os.X_OK))


class TheReceiptReader(unittest.TestCase):
    def _run_dir(self, body):
        run_dir = tempfile.mkdtemp(prefix="codex-smoke-receipt-")
        out = os.path.join(run_dir, "receipt")
        os.makedirs(out)
        if body is not None:
            with open(os.path.join(out, "receipt.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(body, fh)
        return run_dir

    def test_no_receipt_at_all_is_refused(self):
        found, why = codex_smoke.receipt_lines(self._run_dir(None))
        self.assertIsNone(found)
        self.assertIn("no receipt at", why)

    def test_a_receipt_naming_no_changed_file_is_refused(self):
        run_dir = self._run_dir({"scope": {"changed": [],
                                           "declared_untouched": []}})
        found, why = codex_smoke.receipt_lines(run_dir)
        self.assertIsNone(found)
        self.assertIn("names no changed file", why)

    def test_a_receipt_naming_a_changed_file_yields_its_check_line(self):
        run_dir = self._run_dir({"scope": {"changed": [
            {"file": "mathlib.py", "unit": "U1", "state": "verified",
             "check_command": "python3 -c 'import mathlib'", "exit_code": 0,
             "reason": ""}]}})
        found, why = codex_smoke.receipt_lines(run_dir)
        self.assertEqual(why, "")
        path, lines = found
        self.assertTrue(path.endswith(os.path.join("receipt", "receipt.json")))
        self.assertEqual(len(lines), 1)
        self.assertIn("mathlib.py", lines[0])
        self.assertIn("verified", lines[0])
        self.assertIn("exited 0", lines[0])

    def test_an_unreadable_receipt_is_refused_rather_than_raising(self):
        run_dir = self._run_dir({"scope": {"changed": []}})
        with open(os.path.join(run_dir, "receipt", "receipt.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{not json")
        found, why = codex_smoke.receipt_lines(run_dir)
        self.assertIsNone(found)
        self.assertIn("unreadable", why)


if __name__ == "__main__":
    unittest.main(verbosity=2)
