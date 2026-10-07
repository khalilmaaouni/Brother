#!/usr/bin/env python3
"""mutation_probe: the suites it runs are sandboxed from money and live state."""
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mutation_probe as mp  # noqa: E402


class ProbeSandbox(unittest.TestCase):
    def test_a_suite_runs_with_a_throwaway_home_and_state_folders(self):
        root = tempfile.mkdtemp(prefix="probe-sandbox-test-")
        os.makedirs(os.path.join(root, "scripts"))
        report = os.path.join(root, "seen.txt")
        with open(os.path.join(root, "scripts", "test_env_probe.py"), "w") as fh:
            fh.write("import os\nopen(%r, 'w').write('|'.join([os.environ.get('HOME',''),"
                     "os.environ.get('BROTHER_OR_STATE_ROOT',''),os.environ.get('BROTHER_JEV_STATE_DIR',''),"
                     "os.environ.get('BROTHER_RUN_DIR','<unset>')]))\n" % report)
        real_home = os.environ.get("HOME", "")
        os.environ["BROTHER_RUN_DIR"] = "/some/live/run"
        try:
            code, stdout, stderr = mp.run_test(root, "scripts/test_env_probe.py", 30)
            self.assertEqual(code, 0)
        finally:
            os.environ.pop("BROTHER_RUN_DIR", None)
        home, or_state, jev_state, run_dir = open(report).read().split("|")
        for value in (home, or_state, jev_state):
            self.assertTrue(value.startswith(root), msg=value)
        self.assertNotEqual(home, real_home)
        self.assertEqual(run_dir, "<unset>")


class TestCopyPlanner(unittest.TestCase):
    def test_default_copy_paths_includes_top_level_roots(self):
        self.assertEqual(mp.default_copy_paths("plugin/a.py", "scripts/b.py"), ("plugin", "scripts"))

    def test_extra_copy_paths_are_added(self):
        paths = mp.resolve_copy_paths("plugin/a.py", "scripts/b.py", ("docs",))
        self.assertIn("docs", paths)

    def test_scratch_tree_copies_directory_and_file(self):
        rel_dir = "scripts"
        rel_file = "scripts/mutation_probe.py"
        root = mp.scratch_tree((rel_dir, rel_file))
        try:
            self.assertTrue(os.path.isdir(os.path.join(root, rel_dir)))
            self.assertTrue(os.path.isfile(os.path.join(root, rel_file)))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_absolute_src_blocks(self):
        with self.assertRaises(ValueError):
            mp.resolve_copy_paths(os.path.abspath("plugin/a.py"), "scripts/b.py", ())
        with self.assertRaises(ValueError):
            mp.scratch_tree((os.path.abspath("plugin/a.py"),))

    def test_traversal_src_blocks(self):
        with self.assertRaises(ValueError):
            mp.resolve_copy_paths("../outside.py", "scripts/b.py", ())
        with self.assertRaises(ValueError):
            mp.scratch_tree(("../outside.py",))

    def test_missing_copy_blocks(self):
        with self.assertRaises(ValueError):
            mp.scratch_tree(("no-such-file-xyz",))

    def test_hostile_inputs_are_refused(self):
        bad = [None, 1, 1.5, True, [], {}, float("nan")]
        for value in bad:
            with self.assertRaises(ValueError):
                mp.default_copy_paths(value, "scripts/test.py")
            with self.assertRaises(ValueError):
                mp.default_copy_paths("plugin/a.py", value)
            with self.assertRaises(ValueError):
                mp.resolve_copy_paths(value, "scripts/test.py", ())
            with self.assertRaises(ValueError):
                mp.resolve_copy_paths("plugin/a.py", value, ())
            with self.assertRaises(ValueError):
                mp.resolve_copy_paths("plugin/a.py", "scripts/test.py", value)
            with self.assertRaises(ValueError):
                mp.scratch_tree(value)
        with self.assertRaises(ValueError):
            mp.resolve_copy_paths("plugin/a.py", "scripts/test.py", ("ok", 1))
        with self.assertRaises(ValueError):
            mp.scratch_tree(("ok", 1))


class TestClassifier(unittest.TestCase):
    def test_survived_on_zero_exit(self):
        self.assertEqual(mp.classify_run(0, "", "", "t"), "SURVIVED")

    def test_killed_on_expected_assertion_failure(self):
        self.assertEqual(mp.classify_run(1, "FAIL: t\nAssertionError", "", "t"), "KILLED")

    def test_import_error_is_not_killed(self):
        self.assertEqual(mp.classify_run(1, "", "ImportError", "t"), "INFRA")

    def test_missing_file_is_not_killed(self):
        self.assertEqual(mp.classify_run(1, "", "FileNotFoundError", "t"), "INFRA")

    def test_timeout_is_not_killed(self):
        self.assertEqual(mp.classify_run("timeout", "", "", "t"), "TIMEOUT")

    def test_wrong_test_failure_is_infra(self):
        self.assertEqual(mp.classify_run(1, "FAIL: other\nAssertionError", "", "t"), "INFRA")

    def test_non_assertion_error_is_infra(self):
        self.assertEqual(mp.classify_run(1, "", "ValueError", "t"), "INFRA")

    def test_baseline_zero_tests_blocks(self):
        self.assertFalse(mp.baseline_ok(0, "Ran 0 tests", ""))

    def test_baseline_green_passes(self):
        self.assertTrue(mp.baseline_ok(0, "Ran 2 tests OK", ""))

    def test_baseline_one_test_suite_passes(self):
        self.assertTrue(mp.baseline_ok(0, "Ran 1 test in 0.000s\n\nOK\n", ""))
        self.assertFalse(mp.baseline_ok(0, "Ran 0 test OK", ""))

    def test_baseline_requires_ok(self):
        self.assertFalse(mp.baseline_ok(0, "Ran 2 tests", ""))

    def test_baseline_requires_nonzero_tests(self):
        self.assertFalse(mp.baseline_ok(0, "Ran 0 tests OK", ""))

    def test_classify_run_hostile_inputs(self):
        bad = [None, 1.5, True, [], {}, float("nan")]
        for value in bad:
            self.assertEqual(mp.classify_run(value, "", "", "t"), "INFRA")
            self.assertEqual(mp.classify_run(1, value, "", "t"), "INFRA")
            self.assertEqual(mp.classify_run(1, "", value, "t"), "INFRA")
            self.assertEqual(mp.classify_run(1, "", "", value), "INFRA")

    def test_baseline_ok_hostile_inputs(self):
        bad = [None, 1.5, True, [], {}, float("nan")]
        for value in bad:
            self.assertFalse(mp.baseline_ok(value, "", ""))
            self.assertFalse(mp.baseline_ok(0, value, ""))
            self.assertFalse(mp.baseline_ok(0, "", value))

class TestRootReachedThroughASymlink(unittest.TestCase):
    """Found by the hermetic gate: the export tree lives under a symlinked temp folder, and _safe_rel compared a RESOLVED
    path with an UNRESOLVED root, so every honest relative path read as outside the root."""

    def test_an_honest_path_is_accepted_when_the_root_is_a_symlink_and_an_escape_is_still_refused(self):
        import tempfile, shutil
        base = tempfile.mkdtemp(prefix="mp-symlink-")
        try:
            real = os.path.join(base, "real"); os.makedirs(os.path.join(real, "scripts"))
            link = os.path.join(base, "link"); os.symlink(real, link)
            old = mp.ROOT; mp.ROOT = link
            try:
                self.assertEqual(mp._safe_rel("scripts/b.py"), os.path.join(os.path.realpath(real), "scripts", "b.py"))
                os.symlink(base, os.path.join(real, "scripts", "out"))  # a link inside the tree that points OUT of it
                with self.assertRaises(ValueError):
                    mp._safe_rel("scripts/out/elsewhere.py")
            finally:
                mp.ROOT = old
        finally:
            shutil.rmtree(base, ignore_errors=True)


class TestMutantLoading(unittest.TestCase):
    def _valid(self, **overrides):
        m = {"id": "M-X", "why": "because", "old": "a", "new": "b", "expect_test": "test_x"}
        m.update(overrides)
        return m

    def test_valid_mutant_requires_expect_test(self):
        m = self._valid()
        del m["expect_test"]
        ok, problem = mp.validate_mutant(m)
        self.assertFalse(ok)
        self.assertIn("expect_test", problem)

    def test_valid_mutant_requires_non_empty_id(self):
        m = self._valid(id="")
        ok, problem = mp.validate_mutant(m)
        self.assertFalse(ok)
        self.assertIn("id", problem)

    def test_valid_mutant_requires_non_empty_why(self):
        m = self._valid(why="")
        ok, problem = mp.validate_mutant(m)
        self.assertFalse(ok)
        self.assertIn("why", problem)

    def test_valid_mutant_requires_non_empty_old(self):
        m = self._valid(old="")
        ok, problem = mp.validate_mutant(m)
        self.assertFalse(ok)
        self.assertIn("old", problem)

    def test_valid_mutant_accepts_empty_new(self):
        m = self._valid(new="")
        ok, problem = mp.validate_mutant(m)
        self.assertTrue(ok, problem)

    def test_valid_mutant_rejects_hostile_outer(self):
        for bad in [None, 1, 1.5, True, [], float("nan"), b"bytes"]:
            with self.assertRaises(ValueError):
                mp.validate_mutant(bad)

    def test_valid_mutant_rejects_hostile_id(self):
        for bad in [None, 1, 1.5, True, [], {}, float("nan"), b"bytes"]:
            m = self._valid(id=bad)
            with self.assertRaises(ValueError):
                mp.validate_mutant(m)

    def test_valid_mutant_rejects_hostile_why(self):
        for bad in [None, 1, 1.5, True, [], {}, float("nan"), b"bytes"]:
            m = self._valid(why=bad)
            with self.assertRaises(ValueError):
                mp.validate_mutant(m)

    def test_valid_mutant_rejects_hostile_old(self):
        for bad in [None, 1, 1.5, True, [], {}, float("nan"), b"bytes"]:
            m = self._valid(old=bad)
            with self.assertRaises(ValueError):
                mp.validate_mutant(m)

    def test_valid_mutant_rejects_hostile_new(self):
        for bad in [None, 1, 1.5, True, [], {}, float("nan"), b"bytes"]:
            m = self._valid(new=bad)
            with self.assertRaises(ValueError):
                mp.validate_mutant(m)

    def test_valid_mutant_rejects_hostile_expect_test(self):
        for bad in [None, 1, 1.5, True, [], {}, float("nan"), b"bytes"]:
            m = self._valid(expect_test=bad)
            with self.assertRaises(ValueError):
                mp.validate_mutant(m)

    def test_valid_mutant_rejects_hostile_file(self):
        for bad in [1, 1.5, True, [], {}, float("nan"), b"bytes"]:
            m = self._valid(file=bad)
            with self.assertRaises(ValueError):
                mp.validate_mutant(m)

    def test_apply_mutant_rejects_non_unique_old(self):
        original = "a\na\n"
        m = self._valid(old="a")
        patched, problem = mp.apply_mutant(original, "x.py", m)
        self.assertIsNone(patched)
        self.assertIn("unique", problem.lower())

    def test_apply_mutant_rejects_syntax_error(self):
        original = "x = 1\n"
        m = self._valid(old="x = 1", new="x = (")
        patched, problem = mp.apply_mutant(original, "x.py", m)
        self.assertIsNone(patched)
        self.assertIn("syntax", problem.lower())

    def test_apply_mutant_rejects_empty_old(self):
        m = self._valid(old="")
        patched, problem = mp.apply_mutant("x = 1\n", "x.py", m)
        self.assertIsNone(patched)
        self.assertIn("old", problem.lower())

    def test_apply_mutant_rejects_missing_old(self):
        m = self._valid()
        del m["old"]
        patched, problem = mp.apply_mutant("x = 1\n", "x.py", m)
        self.assertIsNone(patched)
        self.assertIn("old", problem.lower())

    def test_apply_mutant_rejects_missing_new(self):
        m = self._valid()
        del m["new"]
        patched, problem = mp.apply_mutant("x = 1\n", "x.py", m)
        self.assertIsNone(patched)
        self.assertIn("new", problem.lower())

    def test_apply_mutant_returns_patched_text_on_success(self):
        original = "x = 1\n"
        m = self._valid(old="x = 1", new="x = 2")
        patched, problem = mp.apply_mutant(original, "x.py", m)
        self.assertEqual(problem, "")
        self.assertEqual(patched, "x = 2\n")

    def test_apply_mutant_rejects_hostile_original(self):
        for bad in [None, 1, 1.5, True, [], {}, float("nan"), b"bytes"]:
            with self.assertRaises(ValueError):
                mp.apply_mutant(bad, "x.py", self._valid())

    def test_apply_mutant_rejects_hostile_src_rel(self):
        for bad in [None, 1, 1.5, True, [], {}, float("nan"), b"bytes", ""]:
            with self.assertRaises(ValueError):
                mp.apply_mutant("x = 1\n", bad, self._valid())

    def test_apply_mutant_rejects_hostile_m(self):
        for bad in [None, 1, 1.5, True, [], float("nan"), b"bytes"]:
            with self.assertRaises(ValueError):
                mp.apply_mutant("x = 1\n", "x.py", bad)

    def test_apply_mutant_rejects_hostile_old(self):
        for bad in [None, 1, 1.5, True, [], {}, float("nan"), b"bytes"]:
            m = self._valid(old=bad)
            with self.assertRaises(ValueError):
                mp.apply_mutant("x = 1\n", "x.py", m)

    def test_apply_mutant_rejects_hostile_new(self):
        for bad in [None, 1, 1.5, True, [], {}, float("nan"), b"bytes"]:
            m = self._valid(new=bad)
            with self.assertRaises(ValueError):
                mp.apply_mutant("x = 1\n", "x.py", m)

    def test_load_mutants_rejects_corrupt_json(self):
        root = tempfile.mkdtemp(prefix="mutants-corrupt-")
        try:
            path = os.path.join(root, "bad.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"not": "a list"}')
            with self.assertRaises(ValueError):
                mp.load_mutants(path)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[1, 2]")
            with self.assertRaises(ValueError):
                mp.load_mutants(path)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("not json")
            with self.assertRaises(ValueError):
                mp.load_mutants(path)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_load_mutants_missing_file_raises_value_error(self):
        with self.assertRaises(ValueError):
            mp.load_mutants("no-such-mutants-file-xyz.json")

    def test_load_mutants_rejects_non_utf8(self):
        root = tempfile.mkdtemp(prefix="mutants-bytes-")
        try:
            path = os.path.join(root, "bad.json")
            with open(path, "wb") as fh:
                fh.write(b"\xff\xfe\x00\x00")
            with self.assertRaises(ValueError):
                mp.load_mutants(path)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_load_mutants_reads_array_and_strips_fences(self):
        root = tempfile.mkdtemp(prefix="mutants-good-")
        try:
            path = os.path.join(root, "good.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("```json\n[{\"id\": \"x\"}]\n```")
            self.assertEqual(mp.load_mutants(path), [{"id": "x"}])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_load_mutants_rejects_duplicate_ids(self):
        root = tempfile.mkdtemp(prefix="mutants-dup-")
        try:
            path = os.path.join(root, "dup.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('[{"id": "same", "why": "a", "old": "x", "new": "y", "expect_test": "t"},'
                         ' {"id": "same", "why": "b", "old": "a", "new": "b", "expect_test": "t"}]')
            with self.assertRaises(ValueError) as ctx:
                mp.load_mutants(path)
            self.assertIn("duplicate", str(ctx.exception).lower())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_load_mutants_rejects_non_list(self):
        root = tempfile.mkdtemp(prefix="mutants-nonlist-")
        try:
            path = os.path.join(root, "nonlist.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"id": "x"}')
            with self.assertRaises(ValueError):
                mp.load_mutants(path)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_load_mutants_rejects_non_dict_items(self):
        root = tempfile.mkdtemp(prefix="mutants-nondict-")
        try:
            path = os.path.join(root, "nondict.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('[1, 2]')
            with self.assertRaises(ValueError):
                mp.load_mutants(path)
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TestProbeRunner(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="probe-runner-")
        os.makedirs(os.path.join(self.root, "pkg"))
        with open(os.path.join(self.root, "pkg", "__init__.py"), "w", encoding="utf-8") as fh:
            fh.write("")
        with open(os.path.join(self.root, "pkg", "src.py"), "w", encoding="utf-8") as fh:
            fh.write("def f():\n    return 1\n")
        with open(os.path.join(self.root, "pkg", "test_src.py"), "w", encoding="utf-8") as fh:
            fh.write("import unittest\n"
                     "from pkg import src\n"
                     "class T(unittest.TestCase):\n"
                     "    def test_f(self):\n"
                     "        self.assertEqual(src.f(), 1)\n"
                     "    def test_g(self):\n"
                     "        self.assertTrue(True)\n")
        self.old_root = mp.ROOT
        mp.ROOT = self.root

    def tearDown(self):
        mp.ROOT = self.old_root
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_mutants(self, data):
        import json
        path = os.path.join(self.root, "mutants.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path

    def _args(self, mutants_path, extra=None):
        args = ["--src", "pkg/src.py", "--test", "pkg/test_src.py",
                "--mutants", mutants_path]
        if extra:
            args.extend(extra)
        return args

    def test_empty_mutants_returns_no_data(self):
        path = self._write_mutants([])
        self.assertEqual(mp.main(self._args(path)), 2)

    def test_one_valid_killed_returns_zero(self):
        m = [{"id": "M-1", "why": "why", "old": "return 1", "new": "return 2", "expect_test": "test_f"}]
        path = self._write_mutants(m)
        self.assertEqual(mp.main(self._args(path)), 0)

    def test_many_mutants_restore_each(self):
        m = [
            {"id": "M-1", "why": "why", "old": "return 1", "new": "return 2", "expect_test": "test_f"},
            {"id": "M-2", "why": "why", "old": "return 1", "new": "return 3", "expect_test": "test_f"},
        ]
        summary = mp.probe("pkg/src.py", "pkg/test_src.py", m)
        self.assertEqual(summary["valid"], 2)
        self.assertEqual(summary["killed"], 2)
        self.assertEqual(summary["invalid"], 0)

    def test_unknown_id_is_allowed(self):
        m = [{"id": "whatever", "why": "why", "old": "return 1", "new": "return 2", "expect_test": "test_f"}]
        summary = mp.probe("pkg/src.py", "pkg/test_src.py", m)
        self.assertEqual(summary["valid"], 1)
        self.assertEqual(summary["killed"], 1)

    def test_load_mutants_rejects_corrupt_json(self):
        path = os.path.join(self.root, "bad.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{bad")
        self.assertEqual(mp.main(self._args(path)), 2)

    def test_valid_mutant_requires_expect_test(self):
        m = [{"id": "M-1", "why": "why", "old": "return 1", "new": "return 2"}]
        path = self._write_mutants(m)
        self.assertEqual(mp.main(self._args(path)), 2)

    def test_stale_old_is_invalid(self):
        m = [{"id": "M-1", "why": "why", "old": "return 99", "new": "return 2", "expect_test": "test_f"}]
        path = self._write_mutants(m)
        self.assertEqual(mp.main(self._args(path)), 2)

    def test_absolute_src_blocks(self):
        m = [{"id": "M-1", "why": "why", "old": "return 1", "new": "return 2", "expect_test": "test_f"}]
        path = self._write_mutants(m)
        abs_src = os.path.join(self.root, "pkg", "src.py")
        args = ["--src", abs_src, "--test", "pkg/test_src.py", "--mutants", path]
        self.assertEqual(mp.main(args), 2)

    def test_traversal_src_blocks(self):
        m = [{"id": "M-1", "why": "why", "old": "return 1", "new": "return 2", "expect_test": "test_f"}]
        path = self._write_mutants(m)
        args = ["--src", "../outside.py", "--test", "pkg/test_src.py", "--mutants", path]
        self.assertEqual(mp.main(args), 2)

    def test_missing_copy_blocks(self):
        m = [{"id": "M-1", "why": "why", "old": "return 1", "new": "return 2", "expect_test": "test_f"}]
        path = self._write_mutants(m)
        args = self._args(path, ["--copy", "no-such-file-xyz"])
        self.assertEqual(mp.main(args), 2)

    def test_restart_has_no_partial_state(self):
        m = [{"id": "M-1", "why": "why", "old": "return 1", "new": "return 2", "expect_test": "test_f"}]
        summary1 = mp.probe("pkg/src.py", "pkg/test_src.py", m)
        summary2 = mp.probe("pkg/src.py", "pkg/test_src.py", m)
        self.assertEqual(summary1["killed"], 1)
        self.assertEqual(summary2["killed"], 1)

    def test_infra_mutant_not_counted_as_killed(self):
        m = [{"id": "M-INFRA", "why": "why", "old": "return 1", "new": "raise ImportError('boom')", "expect_test": "test_f"}]
        summary = mp.probe("pkg/src.py", "pkg/test_src.py", m)
        self.assertEqual(summary["valid"], 1)
        self.assertEqual(summary["killed"], 0)
        self.assertEqual(summary["infra"], 1)

    def test_all_valid_killed_returns_zero(self):
        m = [{"id": "M-1", "why": "why", "old": "return 1", "new": "return 2", "expect_test": "test_f"}]
        path = self._write_mutants(m)
        self.assertEqual(mp.main(self._args(path)), 0)

    def test_survivor_returns_one(self):
        m = [{"id": "M-1", "why": "why", "old": "return 1", "new": "return 1  # same", "expect_test": "test_f"}]
        path = self._write_mutants(m)
        self.assertEqual(mp.main(self._args(path)), 1)

    def test_invalid_mutant_not_counted_valid(self):
        m = [{"id": "M-1", "why": "why", "old": "return 1", "new": "return 2"}]
        summary = mp.probe("pkg/src.py", "pkg/test_src.py", m)
        self.assertEqual(summary["valid"], 0)
        self.assertEqual(summary["invalid"], 1)

    def test_baseline_failure_returns_no_data(self):
        with open(os.path.join(self.root, "pkg", "test_src.py"), "w", encoding="utf-8") as fh:
            fh.write("import unittest\n"
                     "class T(unittest.TestCase):\n"
                     "    def test_f(self):\n"
                     "        self.fail('red')\n"
                     "    def test_g(self):\n"
                     "        self.assertTrue(True)\n")
        m = [{"id": "M-1", "why": "why", "old": "return 1", "new": "return 2", "expect_test": "test_f"}]
        path = self._write_mutants(m)
        self.assertEqual(mp.main(self._args(path)), 2)

    def test_baseline_zero_tests_blocks(self):
        with open(os.path.join(self.root, "pkg", "test_src.py"), "w", encoding="utf-8") as fh:
            fh.write("import unittest\n"
                     "class T(unittest.TestCase):\n"
                     "    pass\n")
        m = [{"id": "M-1", "why": "why", "old": "return 1", "new": "return 2", "expect_test": "test_f"}]
        path = self._write_mutants(m)
        self.assertEqual(mp.main(self._args(path)), 2)

    def test_hostile_inputs_are_refused(self):
        with self.assertRaises(ValueError):
            mp.probe(None, "pkg/test_src.py", [])
        with self.assertRaises(ValueError):
            mp.probe("pkg/src.py", None, [])
        with self.assertRaises(ValueError):
            mp.probe("pkg/src.py", "pkg/test_src.py", None)
        with self.assertRaises(ValueError):
            mp.probe("pkg/src.py", "pkg/test_src.py", [], copy_paths=None)
        with self.assertRaises(ValueError):
            mp.probe("pkg/src.py", "pkg/test_src.py", [], timeout=True)

    def test_main_argv_bytes_refused(self):
        self.assertEqual(mp.main(b"--src"), 2)

    def test_main_argv_int_refused(self):
        self.assertEqual(mp.main(1), 2)

    def test_main_argv_wrong_type_int_refused(self):
        self.assertEqual(mp.main([1, 2]), 2)

    def test_main_argv_dict_element_refused(self):
        self.assertEqual(mp.main([{"src": "x"}]), 2)

    def test_main_timeout_bool_refused(self):
        self.assertEqual(mp.main(["--timeout", True]), 2)


class StaleScratchSweep(unittest.TestCase):
    """One condition per fixture, so no guard can mask another.

    The leak these cover cost 16.3 GB on 2026-09-21: a killed process never
    reaches the finally block that deletes its scratch tree, so the sweep at
    creation time is the only thing that reclaims it.
    """

    def setUp(self):
        self.parent = tempfile.mkdtemp(prefix="sweep-parent-")
        self.addCleanup(shutil.rmtree, self.parent, True)
        real = tempfile.gettempdir
        tempfile.gettempdir = lambda: self.parent
        self.addCleanup(setattr, tempfile, "gettempdir", real)

    def _tree(self, name, age_seconds):
        path = os.path.join(self.parent, name)
        os.makedirs(os.path.join(path, "plugin"))
        with open(os.path.join(path, "plugin", "f.txt"), "w") as fh:
            fh.write("leaked")
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
        return path

    def test_only_condition_is_age_past_the_window(self):
        old = self._tree("mutation-probe-old", mp.STALE_SECONDS + 60)
        self.assertEqual(mp.sweep_stale_scratch(), 1)
        self.assertFalse(os.path.isdir(old))

    def test_only_condition_is_age_inside_the_window(self):
        live = self._tree("mutation-probe-live", 5)
        self.assertEqual(mp.sweep_stale_scratch(), 0)
        self.assertTrue(os.path.isdir(live))

    def test_only_condition_is_a_foreign_prefix(self):
        other = self._tree("someone-elses-work", mp.STALE_SECONDS + 60)
        self.assertEqual(mp.sweep_stale_scratch(), 0)
        self.assertTrue(os.path.isdir(other))

    def test_only_condition_is_many_stale_trees(self):
        for i in range(5):
            self._tree("mutation-probe-%d" % i, mp.STALE_SECONDS + 60)
        self.assertEqual(mp.sweep_stale_scratch(), 5)

    def test_only_condition_is_a_file_not_a_directory(self):
        f = os.path.join(self.parent, "mutation-probe-notadir")
        with open(f, "w") as fh:
            fh.write("x")
        os.utime(f, (0, 0))
        self.assertEqual(mp.sweep_stale_scratch(), 0)
        self.assertTrue(os.path.isfile(f))

    def test_an_unreadable_parent_keeps_rather_than_deletes(self):
        tempfile.gettempdir = lambda: os.path.join(self.parent, "no-such-dir")
        self.assertEqual(mp.sweep_stale_scratch(), 0)

    def test_max_age_zero_refused(self):
        with self.assertRaises(ValueError):
            mp.sweep_stale_scratch(max_age=0)

    def test_max_age_negative_refused(self):
        with self.assertRaises(ValueError):
            mp.sweep_stale_scratch(max_age=-1)

    def test_max_age_wrong_type_refused(self):
        with self.assertRaises(ValueError):
            mp.sweep_stale_scratch(max_age="6h")

    def test_max_age_bool_refused(self):
        with self.assertRaises(ValueError):
            mp.sweep_stale_scratch(max_age=True)

    def test_now_wrong_type_refused(self):
        with self.assertRaises(ValueError):
            mp.sweep_stale_scratch(now="yesterday")

    def test_now_bool_refused(self):
        with self.assertRaises(ValueError):
            mp.sweep_stale_scratch(now=True)

    def test_the_entry_point_itself_sweeps_not_only_the_helper(self):
        """scratch_tree is where every run routes through, so the guard is
        proven there: a helper that works while its caller never calls it
        is the shape this estate has shipped green before."""
        leaked = self._tree("mutation-probe-entrypoint", mp.STALE_SECONDS + 60)
        made = mp.scratch_tree()
        self.addCleanup(shutil.rmtree, made, True)
        self.assertFalse(os.path.isdir(leaked))
        self.assertTrue(os.path.isdir(made))
        self.assertTrue(os.path.basename(made).startswith(mp.SCRATCH_PREFIX))


if __name__ == "__main__":
    unittest.main()
