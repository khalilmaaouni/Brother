#!/usr/bin/env python3
"""Tests for scripts/loop/adapter_conformance.py and the receipt gate in scripts/loop/loop_roles.py (FX-31.8).

R-FX-31-2 (C11): not measured is not a genuine zero. R-FX-31-3 (C18): a provider refusal is its own status.
R-FX-31-6: a receipt binds the adapter file, the executable and the registry row by SHA-256; a binary replaced after the
receipt was written makes loop_roles.check refuse. Only the command line cases start a process (this module's own entry
point, offline); nothing here touches a home directory.
"""
import contextlib
import inspect
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import adapters as A  # noqa: E402
import adapter_conformance as AC  # noqa: E402
import loop_roles as LR  # noqa: E402

REG = {"cheap": {"transport": "bridge", "privacy": "public", "kinds": {"build", "grade"}},
       "other": {"transport": "codex", "privacy": "public", "kinds": {"build", "grade"}}}
ROLES = {"worker": {"does": "x", "when": "inside", "kind": "build", "content": "public", "must_be_chosen": False,
                    "default": "cheap"}}
HOSTILE = [None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object()]


class Stub(object):
    """An adapter whose answers the test chooses, to prove each case can go red."""

    def __init__(self, zero=0.0, absent=None, status=A.STATUS_PROVIDER_REFUSED, ok=False):
        self.zero, self.absent, self.status, self.ok = zero, absent, status, ok

    def cost(self, result):
        return self.zero if result.get("cost_usd") == 0 or "billed" in str(result) or "total_cost_usd" in str(result) else self.absent

    def judge(self, model, row, result):
        if "refus" in str(result):
            return A.AdapterResult(self.ok, "", "x", self.status)
        return A.AdapterResult(True, "ok", "answered", A.STATUS_OK)


class Base(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop("BROTHER_TRANSPORTS", None)
        self.tmp = tempfile.mkdtemp(prefix="adapter-conformance-")
        self.binary = os.path.join(self.tmp, "bridge-bin")
        with open(self.binary, "w") as fh:
            fh.write("#!/bin/sh\nexit 0\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        if self._saved is not None:
            os.environ["BROTHER_TRANSPORTS"] = self._saved

    def receipt(self, model="cheap", **over):
        report = AC.run_conformance(("bridge", "codex", "claude"), offline=True)
        entry = AC.make_receipt_entry(model, REG, self.binary, report)
        entry.update(over)
        return {"schema": AC.SCHEMA, "roles": {"worker": entry}}


class RunConformance(Base):
    def test_all_three_transports_pass_offline(self):
        report = AC.run_conformance(("bridge", "claude", "codex"), offline=True)
        self.assertTrue(report["ok"], report)
        for name in ("bridge", "claude", "codex"):
            self.assertEqual((report["transports"][name]["C11"], report["transports"][name]["C18"]), ("PASS", "PASS"))
            self.assertEqual(report["transports"][name]["adapter_sha256"], AC.adapter_sha256(name))

    def test_not_run_online_reads_no_data_never_pass(self):
        report = AC.run_conformance(("bridge",))
        self.assertFalse(report["ok"])
        self.assertEqual(report["transports"]["bridge"]["C11"], "NO-DATA")
        self.assertEqual(report["transports"]["bridge"]["C18"], "NO-DATA")

    def test_c11_goes_red_when_an_absent_cost_reads_as_a_number(self):
        with mock.patch.object(AC, "_fixtures", lambda t: (Stub(absent=0.0), {}, {"cost_usd": 0}, {}, {"refus": 1})):
            self.assertEqual(AC.run_conformance(("codex",), offline=True)["transports"]["codex"]["C11"], "FAIL")

    def test_c11_goes_red_when_a_genuine_zero_reads_as_not_measured(self):
        with mock.patch.object(AC, "_fixtures", lambda t: (Stub(zero=None), {}, {"cost_usd": 0}, {}, {"refus": 1})):
            self.assertEqual(AC.run_conformance(("codex",), offline=True)["transports"]["codex"]["C11"], "FAIL")

    def test_c18_goes_red_when_a_refusal_is_a_plain_failure_or_an_answer(self):
        for stub in (Stub(status=A.STATUS_FAILED), Stub(status=A.STATUS_OK, ok=True)):
            with mock.patch.object(AC, "_fixtures", lambda t, s=stub: (s, {}, {"cost_usd": 0}, {}, {"refus": 1})):
                self.assertEqual(AC.run_conformance(("codex",), offline=True)["transports"]["codex"]["C18"], "FAIL")

    def test_bad_requests_are_refused(self):
        for bad in ([], ["bridge"], (), ("nope",), ("bridge", "bridge"), (1,), "bridge", None):
            with self.assertRaises(ValueError):
                AC.run_conformance(bad, offline=True)
        for bad in (0, 1, None, "yes"):
            with self.assertRaises(ValueError):
                AC.run_conformance(("bridge",), offline=bad)


class ReceiptBinding(Base):
    def hashes(self):
        return (AC.adapter_sha256("bridge"), AC.file_sha256(self.binary), AC.row_sha256(REG["cheap"]))

    def test_current_receipt_matches_and_each_changed_hash_fails(self):
        entry = self.receipt()["roles"]["worker"]
        a, b, r = self.hashes()
        self.assertTrue(AC.receipt_matches(entry, a, b, r))
        other = "0" * 64
        self.assertFalse(AC.receipt_matches(entry, other, b, r))
        self.assertFalse(AC.receipt_matches(entry, a, other, r))
        self.assertFalse(AC.receipt_matches(entry, a, b, other))

    def test_malformed_receipt_or_hash_never_matches(self):
        entry = self.receipt()["roles"]["worker"]
        a, b, r = self.hashes()
        for bad in HOSTILE:
            self.assertFalse(AC.receipt_matches(bad, a, b, r))
            self.assertFalse(AC.receipt_matches(entry, bad, b, r))
            self.assertFalse(AC.receipt_matches(entry, a, bad, r))
            self.assertFalse(AC.receipt_matches(entry, a, b, bad))
        self.assertFalse(AC.receipt_matches({}, a, b, r))

    def test_check_accepts_a_current_receipt(self):
        code, lines = LR.check({}, ROLES, REG, self.receipt())
        self.assertEqual(code, 0, lines)

    def test_check_without_a_receipt_is_unchanged(self):
        self.assertEqual(LR.check({}, ROLES, REG)[0], 0)

    def test_changed_binary_c18_fixture_makes_check_refuse(self):
        receipt = self.receipt()
        with open(self.binary, "w") as fh:
            fh.write("#!/bin/sh\nexit 7\n")   # the binary was replaced after the receipt
        self.assertFalse(LR.check_role_receipt("worker", receipt, REG))
        code, lines = LR.check({}, ROLES, REG, receipt)
        self.assertEqual(code, 1, lines)
        self.assertTrue(any("conformance receipt" in line for line in lines))

    def test_changed_registry_row_refuses(self):
        receipt = self.receipt()
        reg = {"cheap": dict(REG["cheap"], privacy="internal"), "other": REG["other"]}
        self.assertEqual(LR.check({}, ROLES, reg, receipt)[0], 1)

    def test_failed_conformance_case_refuses(self):
        for case in AC.CASES:
            self.assertFalse(LR.check_role_receipt("worker", self.receipt(**{case: "FAIL"}), REG))

    def test_receipt_for_another_model_refuses(self):
        receipt = self.receipt(model="other")
        self.assertEqual(LR.check({}, ROLES, REG, receipt)[0], 1)

    def test_incomplete_or_malformed_receipt_refuses(self):
        good = self.receipt()
        for key in AC.ENTRY_KEYS:
            broken = {"schema": AC.SCHEMA, "roles": {"worker": {k: v for k, v in good["roles"]["worker"].items() if k != key}}}
            self.assertFalse(LR.check_role_receipt("worker", broken, REG), key)
        for bad in HOSTILE + [{"schema": 2, "roles": good["roles"]}, {"schema": AC.SCHEMA, "roles": {"worker": 5}},
                              {"schema": AC.SCHEMA, "roles": {}}, {"schema": AC.SCHEMA, "roles": {"worker": dict(good["roles"]["worker"], binary_path=5)}},
                              {"schema": AC.SCHEMA, "roles": {"worker": dict(good["roles"]["worker"], model=[])}}]:
            self.assertFalse(LR.check_role_receipt("worker", bad, REG))
            if bad is not None:
                self.assertEqual(LR.check({}, ROLES, REG, bad)[0], 1)
        for bad in HOSTILE:
            self.assertFalse(LR.check_role_receipt(bad, good, REG))
            self.assertFalse(LR.check_role_receipt("worker", good, bad))

    def test_missing_binary_refuses(self):
        receipt = self.receipt(binary_path=os.path.join(self.tmp, "gone"))
        self.assertFalse(LR.check_role_receipt("worker", receipt, REG))


class Hostile(Base):
    def test_every_public_function_returns_or_refuses(self):
        funcs = [f for n, f in inspect.getmembers(AC, inspect.isfunction) if not n.startswith("_") and f.__module__ == AC.__name__]
        self.assertGreaterEqual(len(funcs), 6)
        for fn in funcs:
            count = len(inspect.signature(fn).parameters)
            for value in HOSTILE:
                try:
                    fn(*([value] * count))
                except (ValueError, LookupError, SystemExit):
                    pass


class SameStemModule(Base):
    """The landing fuzz names a scripts/ file by its bare stem and scripts/ comes first on its path, so it reaches
    scripts/adapter_conformance.py under the name adapter_conformance. Its public functions take the same refusals."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        path = os.path.join(os.path.dirname(HERE), "adapter_conformance.py")
        spec = importlib.util.spec_from_file_location("scripts_adapter_conformance_under_test", path)
        cls.old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.old)

    def test_every_public_function_returns_or_refuses(self):
        funcs = [f for n, f in inspect.getmembers(self.old, inspect.isfunction)
                 if not n.startswith("_") and f.__module__ == self.old.__name__]
        self.assertGreaterEqual(len(funcs), 14)
        for fn in funcs:
            count = len(inspect.signature(fn).parameters)
            for value in HOSTILE:
                with mock.patch.object(self.old, "DEFAULT_EVIDENCE_ROOT", tempfile.mkdtemp()):
                    try:
                        fn(*([value] * count))
                    except (ValueError, LookupError, SystemExit):
                        pass

    def test_main_refuses_a_non_list_argv_with_exit_2(self):
        for value in HOSTILE:
            if value is None or value == ["x"] or value == []:
                continue
            with self.assertRaises(SystemExit) as ctx:
                self.old.main(value)
            self.assertEqual(ctx.exception.code, 2)


class CommandLine(Base):
    """The entry point FX-31's unit done check names: `adapter_conformance.py --transport bridge,claude,codex --offline`.
    MEASURED 2026-10-05: the file had no entry point, so that command imported it and exited 0 with every C11 forced to
    FAIL. One condition per case; the process cases run the file the way the done check does and read its exit code."""

    STEP = ["--transport", "bridge,claude,codex", "--offline"]

    def process(self, args):
        try:
            return subprocess.run([sys.executable, "-B", os.path.join(HERE, "adapter_conformance.py")] + args,
                                  capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            self.fail("the command line could not run (%s: %s)" % (type(exc).__name__, exc))

    def said(self, args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = AC.main(args)
        return code, out.getvalue().strip().splitlines()

    # the result lines the four done check commands before this one print when they pass (their shapes, measured
    # 2026-10-05): what the closer has already collected by the time the fifth command speaks
    FOUR_GREEN = ("selftest: 19 cases, OK\nselftest: 84 cases, OK\nRan 57 tests in 6.292s\n\nOK\n"
                  "Ran 39 tests in 0.485s\n\nOK\n")
    FAILED_LINE = "FAIL: conformance: 6 cases over bridge,claude,codex, FAILED: codex C11"

    def broken(self, args):
        """The real file run AS THE PROGRAM (runpy, run_name __main__, so its own entry block decides the exit code) in a
        child whose Codex adapter reads an absent cost as 0.0: exactly one case, codex C11, fails."""
        path = os.path.join(HERE, "adapter_conformance.py")
        code = ("import runpy, sys\nsys.path.insert(0, %r)\nimport adapters.codex as C\n"
                "C.CodexAdapter.cost = lambda self, result, usage=None: 0.0\nsys.argv = [%r] + %r\n"
                "runpy.run_path(%r, run_name='__main__')\n" % (HERE, path, list(args), path))
        try:
            return subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            self.fail("the command line could not run (%s: %s)" % (type(exc).__name__, exc))

    def closer_quotes(self, proc):
        """(ok, quoted) from the loop's own closer, scripts/close_unit.py verdict(), over the four green commands' lines
        followed by this process's real output. Loaded by path: scripts/ is never put on this process's sys.path."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("close_unit_under_test", os.path.join(os.path.dirname(HERE), "close_unit.py"))
        closer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(closer)
        return closer.verdict(proc.returncode, self.FOUR_GREEN + (proc.stdout or "") + (proc.stderr or ""))

    def test_the_done_check_command_runs_every_case_and_exits_zero(self):
        r = self.process(self.STEP)
        lines = r.stdout.strip().splitlines()
        self.assertEqual(r.returncode, 0, (lines[-1:] or ["no output"], r.stderr[-300:]))
        self.assertEqual(len([l for l in lines if l.startswith("PASS ")]), 6, lines)
        self.assertEqual(lines[-1], "PASS: conformance: 6 cases over bridge,claude,codex, OK")

    def test_the_process_exit_code_is_the_verdict_not_always_zero(self):
        """Nothing runs without --offline, so the PROCESS answers 2: the code is main's return, never a bare 0."""
        r = self.process(["--transport", "bridge"])
        self.assertEqual(r.returncode, 2, r.stdout[-300:] + r.stderr[-300:])

    def test_one_failed_case_makes_the_process_exit_one(self):
        """The PROCESS, with one case really failing: exit 1, never 0 and never the NO-DATA 2."""
        r = self.broken(self.STEP)
        lines = r.stdout.strip().splitlines()
        self.assertEqual(r.returncode, 1, (lines[-1:] or ["no output"], r.stderr[-300:]))
        self.assertEqual(lines[-1], self.FAILED_LINE)

    def test_the_closer_quotes_the_failed_line_after_four_green_commands(self):
        """MEASURED 2026-10-06 (review): the verdict line had a shape close_unit.verdict does not know, so a red close
        quoted 'Ran 57 tests ... OK Ran 39 tests ... OK': the four commands that passed, and not the one that failed."""
        ok, quoted = self.closer_quotes(self.broken(self.STEP))
        self.assertFalse(ok)
        self.assertTrue(quoted.endswith(self.FAILED_LINE), quoted)

    def test_the_closer_quotes_a_green_run_by_this_commands_own_line(self):
        ok, quoted = self.closer_quotes(self.process(self.STEP))
        self.assertTrue(ok)
        self.assertTrue(quoted.endswith("PASS: conformance: 6 cases over bridge,claude,codex, OK"), quoted)

    def test_the_closer_quotes_a_run_that_proved_nothing_by_its_reason_alone(self):
        ok, quoted = self.closer_quotes(self.process(["--transport", "bridge"]))
        self.assertFalse(ok)
        self.assertEqual(quoted, "NO-DATA: conformance: 2 cases over bridge, NO-DATA")

    def test_a_failed_case_exits_one_and_is_named(self):
        with mock.patch.object(AC, "_fixtures", lambda t: (Stub(absent=0.0), {}, {"cost_usd": 0}, {}, {"refus": 1})):
            code, lines = self.said(["--transport", "codex", "--offline"])
        self.assertEqual(code, 1, lines)
        self.assertTrue(lines[0].startswith("FAIL    codex C11: "), lines)
        self.assertEqual(lines[-1], "FAIL: conformance: 2 cases over codex, FAILED: codex C11")

    def test_cases_that_did_not_run_are_no_data_exit_two_never_a_pass(self):
        code, lines = self.said(["--transport", "bridge"])
        self.assertEqual(code, 2, lines)
        self.assertEqual(lines[-1], "NO-DATA: conformance: 2 cases over bridge, NO-DATA")

    def test_a_request_that_names_no_known_transport_runs_nothing_and_exits_two(self):
        code, lines = self.said(["--transport", "smoke-signal", "--offline"])
        self.assertEqual(code, 2, lines)
        self.assertTrue(lines[-1].startswith("NO-DATA: conformance: NO-DATA, nothing ran: "), lines)

    def test_an_argv_that_is_not_a_list_of_str_refuses_with_exit_2(self):
        for value in HOSTILE:
            if value is None or value == ["x"] or value == []:
                continue
            with self.assertRaises(SystemExit) as ctx:
                AC.main(value)
            self.assertEqual(ctx.exception.code, 2, value)


if __name__ == "__main__":
    unittest.main()
