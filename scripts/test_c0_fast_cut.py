"""C0.5 tests: shadow equivalence (shadow_run), the promotion gate
(shadow_gate), and the one flag back (--shadow, --fast, --full-chain,
--allow-unsafe, --reason).

Every fixture is built in a temp folder: nothing here reads a live
repository document. Hostile input is refused with this module's own
refusal value (False from the gate) or its own deliberate error (ValueError
from shadow_run), never a raw interpreter exception.
"""
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cut  # noqa: E402


class _GateRegex(object):
    """Stands in for gate_order: the one attribute _shadow_verdicts needs,
    in the documented line shape STATUS exit N NAME SECONDSs SUMMARY, with
    the summary OPTIONAL so the summary-less case can be exercised
    deterministically."""
    RESULT_RE = re.compile(
        r"^(PASS|FAIL|NO-DATA) exit (-?\d+) (\S+) ([\d.]+)s(?: (.*))?$",
        re.MULTILINE)


def _gate_line(status="PASS", exit_code=0, name="gate-a", seconds="0.5",
               summary="all good"):
    if summary is None:
        return "%s exit %d %s %ss" % (status, exit_code, name, seconds)
    return "%s exit %d %s %ss %s" % (status, exit_code, name, seconds,
                                     summary)


class _FakeRun(object):
    """Stands in for subprocess.run: one git answer, one gate answer per
    call, no process ever spawned."""

    def __init__(self, sha="a" * 40, gate_text="", gate_code=0, git_code=0):
        self.sha = sha
        self.gate_text = gate_text
        self.gate_code = gate_code
        self.git_code = git_code
        self.calls = []
        self.gate_calls = 0

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        if cmd and cmd[0] == "git":
            out = self.sha + "\n" if self.git_code == 0 else ""
            return subprocess.CompletedProcess(cmd, self.git_code, out, "")
        self.gate_calls += 1
        text = self.gate_text
        if callable(text):
            text = text(self.gate_calls)
        return subprocess.CompletedProcess(cmd, self.gate_code, text, "")


class TestShadow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="c05-shadow-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.specs = os.path.join(self.tmp, "docs", "plan", "specs")
        os.makedirs(self.specs)

    # ---- helpers -------------------------------------------------------
    def _patch_gate(self, module=_GateRegex):
        original = cut.GO
        cut.GO = module
        self.addCleanup(setattr, cut, "GO", original)

    def _case(self, name):
        path = os.path.join(self.tmp, name)
        os.makedirs(path)
        return path

    def _record(self, name="C0-shadow-1.json", directory=None, **overrides):
        data = {
            "schema_version": "c0.shadow.1",
            "version": "1.0.0",
            "tree_sha": "a" * 40,
            "fast_verdicts": {"gate-a": "PASS"},
            "full_verdicts": {"gate-a": "PASS"},
            "equal": True,
            "fast_seconds": 1.0,
            "full_seconds": 2.0,
            "pin_sha256": "b" * 64,
            "recorded_at": "2020-01-01T00:00:00Z",
        }
        data.update(overrides)
        path = os.path.join(directory or self.specs, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        return path

    def _raw(self, directory, name, text):
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    # ---- shadow_gate ---------------------------------------------------
    def test_diverge_blocks_fast(self):
        self._record("C0-shadow-1.json", recorded_at="2020-01-01T00:00:00Z")
        self._record("C0-shadow-2.json", equal=False,
                     recorded_at="2020-01-02T00:00:00Z")
        self.assertFalse(cut.shadow_gate(self.specs))

    def test_latest_record_false_blocks(self):
        self._record("C0-shadow-1.json", recorded_at="2020-01-01T00:00:00Z")
        self._record("C0-shadow-2.json", recorded_at="2020-01-02T00:00:00Z")
        self._record("C0-shadow-3.json", equal=False,
                     recorded_at="2020-01-03T00:00:00Z")
        self.assertFalse(cut.shadow_gate(self.specs))

    def test_consecutive_true_allows(self):
        self._record("C0-shadow-1.json", recorded_at="2020-01-01T00:00:00Z")
        self._record("C0-shadow-2.json", recorded_at="2020-01-02T00:00:00Z")
        self.assertTrue(cut.shadow_gate(self.specs))

    def test_missing_records_refuse(self):
        self.assertFalse(cut.shadow_gate(self.specs))
        self.assertFalse(cut.shadow_gate(os.path.join(self.tmp, "absent")))

    def test_hostile_records_dir_refused(self):
        for value in (None, 12, 1.5, b"specs", [], {}, True):
            with self.subTest(value=repr(value)):
                self.assertFalse(cut.shadow_gate(value))

    def test_hostile_min_consecutive_refused(self):
        self._record("C0-shadow-1.json", recorded_at="2020-01-01T00:00:00Z")
        self._record("C0-shadow-2.json", recorded_at="2020-01-02T00:00:00Z")
        for value in (None, True, 0, -3, "2", 1.5):
            with self.subTest(value=repr(value)):
                self.assertFalse(cut.shadow_gate(self.specs, value))
        self.assertFalse(cut.shadow_gate(self.specs, 3))

    def test_equal_true_with_mismatched_maps_refused(self):
        self._record("C0-shadow-1.json", fast_verdicts={"gate-a": "PASS"},
                     full_verdicts={"gate-a": "FAIL"},
                     recorded_at="2020-01-01T00:00:00Z")
        self._record("C0-shadow-2.json", recorded_at="2020-01-02T00:00:00Z")
        self.assertFalse(cut.shadow_gate(self.specs))

    def test_equal_field_that_is_not_a_bool_refused(self):
        for index, value in enumerate(("yes", 1, 0, None, [])):
            case = self._case("eq%d" % index)
            self._record("C0-shadow-1.json", directory=case, equal=value)
            self._record("C0-shadow-2.json", directory=case,
                         recorded_at="2020-01-02T00:00:00Z")
            with self.subTest(value=repr(value)):
                self.assertFalse(cut.shadow_gate(case))

    def test_duplicate_version_different_tree_refused(self):
        self._record("C0-shadow-1.json", recorded_at="2020-01-01T00:00:00Z")
        self._record("C0-shadow-2.json", tree_sha="c" * 40,
                     recorded_at="2020-01-02T00:00:00Z")
        self.assertFalse(cut.shadow_gate(self.specs))

    def test_same_version_different_pin_refused(self):
        self._record("C0-shadow-1.json", recorded_at="2020-01-01T00:00:00Z")
        self._record("C0-shadow-2.json", pin_sha256="d" * 64,
                     recorded_at="2020-01-02T00:00:00Z")
        self.assertFalse(cut.shadow_gate(self.specs))

    def test_hostile_digests_refused(self):
        for index, override in enumerate((
                {"tree_sha": "aaa"}, {"tree_sha": None}, {"tree_sha": 7},
                {"tree_sha": "A" * 40}, {"pin_sha256": "bbb"},
                {"pin_sha256": None}, {"pin_sha256": "B" * 64})):
            case = self._case("digest%d" % index)
            self._record("C0-shadow-1.json", directory=case, **override)
            self._record("C0-shadow-2.json", directory=case,
                         recorded_at="2020-01-02T00:00:00Z")
            with self.subTest(override=override):
                self.assertFalse(cut.shadow_gate(case))

    def test_hostile_wall_times_refused(self):
        for index, override in enumerate((
                {"fast_seconds": -1.0}, {"fast_seconds": True},
                {"fast_seconds": "1.0"}, {"full_seconds": float("nan")},
                {"full_seconds": float("inf")}, {"full_seconds": None})):
            case = self._case("clock%d" % index)
            self._record("C0-shadow-1.json", directory=case, **override)
            self._record("C0-shadow-2.json", directory=case,
                         recorded_at="2020-01-02T00:00:00Z")
            with self.subTest(override=override):
                self.assertFalse(cut.shadow_gate(case))

    def test_hostile_verdict_maps_refused(self):
        for index, override in enumerate((
                {"fast_verdicts": None}, {"fast_verdicts": "PASS"},
                {"fast_verdicts": {}}, {"fast_verdicts": {"gate-a": "MAYBE"}},
                {"fast_verdicts": {"": "PASS"}},
                {"full_verdicts": []}, {"full_verdicts": {"gate-a": 1}})):
            case = self._case("maps%d" % index)
            self._record("C0-shadow-1.json", directory=case, **override)
            self._record("C0-shadow-2.json", directory=case,
                         recorded_at="2020-01-02T00:00:00Z")
            with self.subTest(override=override):
                self.assertFalse(cut.shadow_gate(case))

    def test_hostile_version_field_refused(self):
        for index, override in enumerate((
                {"version": None}, {"version": 7}, {"version": ""},
                {"version": "../x"}, {"version": "a/b"},
                {"version": ".x"})):
            case = self._case("version%d" % index)
            self._record("C0-shadow-1.json", directory=case, **override)
            self._record("C0-shadow-2.json", directory=case,
                         recorded_at="2020-01-02T00:00:00Z")
            with self.subTest(override=override):
                self.assertFalse(cut.shadow_gate(case))

    def test_missing_field_refused(self):
        base = {
            "schema_version": "c0.shadow.1",
            "version": "1.0.0",
            "tree_sha": "a" * 40,
            "fast_verdicts": {"gate-a": "PASS"},
            "full_verdicts": {"gate-a": "PASS"},
            "equal": True,
            "fast_seconds": 1.0,
            "full_seconds": 2.0,
            "pin_sha256": "b" * 64,
            "recorded_at": "2020-01-01T00:00:00Z",
        }
        for index, field in enumerate(sorted(base)):
            case = self._case("missing%d" % index)
            partial = dict(base)
            del partial[field]
            self._raw(case, "C0-shadow-1.json", json.dumps(partial))
            self._raw(case, "C0-shadow-2.json", json.dumps(base))
            with self.subTest(field=field):
                self.assertFalse(cut.shadow_gate(case))

    def test_non_utf8_record_refused(self):
        path = os.path.join(self.specs, "C0-shadow-1.json")
        with open(path, "wb") as handle:
            handle.write(b"\xff\xfe\x00{")
        self._record("C0-shadow-2.json")
        self.assertFalse(cut.shadow_gate(self.specs))

    def test_directory_named_like_a_record_refused(self):
        os.makedirs(os.path.join(self.specs, "C0-shadow-1.json"))
        self.assertFalse(cut.shadow_gate(self.specs))

    def test_unrelated_files_are_ignored(self):
        self._raw(self.specs, "notes.json", "not json at all")
        self._record("C0-shadow-1.json", recorded_at="2020-01-01T00:00:00Z")
        self._record("C0-shadow-2.json", recorded_at="2020-01-02T00:00:00Z")
        self.assertTrue(cut.shadow_gate(self.specs))

    # ---- shadow_run ----------------------------------------------------
    def test_shadow_run_refuses_hostile_arguments(self):
        cases = (
            {"root": None}, {"root": ""}, {"root": 7},
            {"root": self.tmp, "version": None},
            {"root": self.tmp, "version": 7},
            {"root": self.tmp, "version": ""},
            {"root": self.tmp, "version": ".."},
            {"root": self.tmp, "version": "../x"},
            {"root": self.tmp, "version": "1.0.0/../x"},
            {"root": self.tmp, "version": ".hidden"},
            {"root": self.tmp, "version": "1.0.0", "pin_path": 7},
            {"root": self.tmp, "version": "1.0.0", "pin_path": ""},
            {"root": self.tmp, "version": "1.0.0", "runner": "nope"},
        )
        for case in cases:
            args = {"root": self.tmp, "version": "1.0.0"}
            args.update(case)
            with self.subTest(case=case):
                with self.assertRaises(ValueError):
                    cut.shadow_run(**args)

    def test_shadow_run_refuses_traversal_without_writing(self):
        with self.assertRaises(ValueError):
            cut.shadow_run(self.tmp, "../x", None, _FakeRun())
        found = []
        for _base, _dirs, names in os.walk(self.tmp):
            found.extend(n for n in names if n.startswith("C0-shadow-"))
        self.assertEqual(found, [])

    def test_shadow_run_equal_true_writes_the_record(self):
        self._patch_gate()
        runner = _FakeRun(gate_text=_gate_line() + "\n")
        record = cut.shadow_run(self.tmp, "1.0.0", None, runner)
        self.assertTrue(record["equal"])
        self.assertEqual(record["tree_sha"], "a" * 40)
        self.assertEqual(record["fast_verdicts"], {"gate-a": "PASS"})
        self.assertEqual(record["full_verdicts"], {"gate-a": "PASS"})
        self.assertGreaterEqual(record["fast_seconds"], 0.0)
        self.assertGreaterEqual(record["full_seconds"], 0.0)
        self.assertEqual(record["pin_sha256"],
                         hashlib.sha256(b"").hexdigest())
        path = os.path.join(self.specs, "C0-shadow-1.0.0.json")
        self.assertTrue(os.path.isfile(path))
        with open(path, "rb") as handle:
            on_disk = json.loads(handle.read().decode("utf-8"))
        self.assertEqual(on_disk, record)
        self.assertTrue(cut.shadow_gate(self.specs, 1))

    def test_shadow_run_divergence_is_not_equal(self):
        self._patch_gate()
        runner = _FakeRun(gate_text=lambda n: _gate_line(
            status="PASS" if n % 2 else "FAIL") + "\n")
        record = cut.shadow_run(self.tmp, "1.0.0", None, runner)
        self.assertFalse(record["equal"])
        self.assertEqual(len(runner.calls), 3)

    def test_shadow_run_summary_less_lines_are_not_evidence(self):
        self._patch_gate()
        runner = _FakeRun(gate_text=_gate_line(summary=None) + "\n")
        record = cut.shadow_run(self.tmp, "1.0.0", None, runner)
        self.assertFalse(record["equal"])

    def test_shadow_run_unknown_tree_is_not_equal(self):
        self._patch_gate()
        runner = _FakeRun(git_code=1, gate_text=_gate_line() + "\n")
        record = cut.shadow_run(self.tmp, "1.0.0", None, runner)
        self.assertFalse(record["equal"])
        self.assertEqual(record["tree_sha"], "UNKNOWN")

    def test_shadow_run_unreadable_pin_is_not_equal(self):
        self._patch_gate()
        directory = self._case("pin-dir")
        runner = _FakeRun(gate_text=_gate_line() + "\n")
        record = cut.shadow_run(self.tmp, "1.0.0", directory, runner)
        self.assertFalse(record["equal"])

    def test_missing_parser_refuses(self):
        original = cut.GO
        cut.GO = None
        self.addCleanup(setattr, cut, "GO", original)
        with self.assertRaises(ValueError):
            cut._shadow_verdicts(_gate_line() + "\n")

    def test_missing_parser_makes_the_run_not_equal(self):
        original = cut.GO
        cut.GO = None
        self.addCleanup(setattr, cut, "GO", original)
        runner = _FakeRun(gate_text=_gate_line() + "\n")
        record = cut.shadow_run(self.tmp, "1.0.0", None, runner)
        self.assertFalse(record["equal"])

    # ---- main ----------------------------------------------------------
    def test_main_help_names_the_new_flags(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cut.main(["--help"])
        self.assertEqual(code, cut.EXIT_OK)
        for flag in ("--shadow", "--fast", "--full-chain",
                     "--allow-unsafe", "--reason"):
            self.assertIn(flag, out.getvalue())

    def test_main_refuses_a_non_list_argv(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cut.main("--help"), cut.EXIT_REFUSED)
            self.assertEqual(cut.main(7), cut.EXIT_REFUSED)

    def test_main_shadow_writes_and_reports(self):
        self._patch_gate()
        runner = _FakeRun(gate_text=_gate_line() + "\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cut.main(["--shadow", "--version", "1.0.0"],
                            root=self.tmp, runner=runner)
        self.assertEqual(code, cut.EXIT_OK)
        self.assertIn("equal=True", out.getvalue())
        self.assertTrue(os.path.isfile(
            os.path.join(self.specs, "C0-shadow-1.0.0.json")))

    def test_main_shadow_refuses_a_divergence(self):
        self._patch_gate()
        runner = _FakeRun(gate_text=lambda n: _gate_line(
            status="PASS" if n % 2 else "FAIL") + "\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cut.main(["--shadow", "--version", "1.0.0"],
                            root=self.tmp, runner=runner)
        self.assertEqual(code, cut.EXIT_REFUSED)

    def test_main_fast_refuses_without_a_true_gate(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cut.main(["--fast", "--check", "--version", "1.0.0"],
                            root=self.tmp, runner=_FakeRun())
        self.assertEqual(code, cut.EXIT_REFUSED)
        self.assertIn("shadow gate", out.getvalue())

    def test_main_fast_unsafe_needs_a_reason(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cut.main(["--fast", "--allow-unsafe", "--check",
                             "--version", "1.0.0"],
                            root=self.tmp, runner=_FakeRun())
        self.assertEqual(code, cut.EXIT_REFUSED)
        self.assertIn("REFUSED", out.getvalue())

    def test_main_fast_unsafe_with_reason_passes_the_gate(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cut.main(["--fast", "--allow-unsafe", "--reason", "measured",
                      "--check", "--version", "1.0.0"],
                     root=self.tmp, runner=_FakeRun())
        self.assertIn("allowed before the shadow gate", out.getvalue())

    def test_main_full_chain_overrides_fast(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cut.main(["--fast", "--full-chain", "--check",
                      "--version", "1.0.0"],
                     root=self.tmp, runner=_FakeRun())
        self.assertIn("full serial chain runs", out.getvalue())
        self.assertNotIn("REFUSED: --fast", out.getvalue())


if __name__ == "__main__":
    unittest.main()
