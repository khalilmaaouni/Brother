#!/usr/bin/env python3
"""User route tests for scripts/wiring_audit.py (unit WR1.b).

The --user-routes mode reads the generated verb skills, extracts every
runtime command path they name and refuses one that does not resolve under
bundle/runtime. This suite drives skill_routes() and resolve_route()
directly, user_routes() and main() over temporary fixture trees, and the
required_fast.sh row that makes the mode mandatory.

Every fixture lives in a temporary folder; the real tree is read only by the
one case that drives the shipped skills, and that case is skipped in a tree
that carries no bundle/skills.
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wiring_audit  # noqa: E402


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED_FAST = os.path.join(REPO_ROOT, "scripts", "required_fast.sh")
REAL_SKILLS = os.path.join(REPO_ROOT, "bundle", "skills")


def _write(path, body):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    mode = "wb" if isinstance(body, bytes) else "w"
    with open(path, mode) as handle:
        handle.write(body)


def _skill(root, name, text):
    _write(os.path.join(root, "bundle", "skills", "brother-" + name,
                        "SKILL.md"), text)


def _run_main(argv):
    """Run wiring_audit.main() in process; return (code, stdout, stderr)."""
    saved = sys.argv
    sys.argv = ["wiring_audit.py"] + list(argv)
    out = io.StringIO()
    err = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = wiring_audit.main(list(argv))
    except SystemExit as exc:
        code = exc.code
    finally:
        sys.argv = saved
    return code, out.getvalue(), err.getvalue()


class TempTreeTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="wr1b-")
        self.addCleanup(shutil.rmtree, self.root, True)


class ExtractionTests(unittest.TestCase):
    def test_extraction_handles_both_forms_punctuation_and_non_runtime_paths(self):
        text = (
            "Run ${BROTHER_PLUGIN_ROOT}/runtime/loop/start_run.sh.\n"
            "Then runtime/x.py and runtime/x.py, too.\n"
            "Ignore runtime/x.py.bak and skills/using-brother/readme.md here.\n"
            "Also ${OTHER}/runtime/loop/stop_loop.sh) is a route.\n"
        )
        self.assertEqual(
            wiring_audit.skill_routes(text),
            ["runtime/loop/start_run.sh", "runtime/loop/stop_loop.sh",
             "runtime/x.py"])

    def test_extraction_is_distinct_and_sorted(self):
        text = "runtime/b.py runtime/a.py runtime/b.py\n"
        self.assertEqual(wiring_audit.skill_routes(text),
                         ["runtime/a.py", "runtime/b.py"])

    def test_extraction_of_a_plain_sentence_is_empty(self):
        self.assertEqual(wiring_audit.skill_routes("nothing here\n"), [])


class ResolveRouteTests(TempTreeTestCase):
    def test_a_live_file_resolves(self):
        _write(os.path.join(self.root, "bundle", "runtime", "loop",
                            "start_run.sh"), "#!/bin/sh\n")
        self.assertEqual(
            wiring_audit.resolve_route(self.root,
                                       "runtime/loop/start_run.sh"), "")

    def test_a_missing_file_is_named_missing(self):
        os.makedirs(os.path.join(self.root, "bundle", "runtime"))
        self.assertEqual(
            wiring_audit.resolve_route(self.root, "runtime/nope.py"),
            "missing")

    def test_a_traversal_or_symlink_route_is_refused(self):
        outside = os.path.join(self.root, "outside.py")
        _write(outside, "x = 1\n")
        self.assertEqual(
            wiring_audit.resolve_route(self.root,
                                       "runtime/../../outside.py"),
            "escapes bundle/runtime")
        link = os.path.join(self.root, "bundle", "runtime", "linked.py")
        os.makedirs(os.path.dirname(link))
        os.symlink(outside, link)
        self.assertEqual(
            wiring_audit.resolve_route(self.root, "runtime/linked.py"),
            "escapes bundle/runtime")

    def test_a_directory_or_empty_file_in_place_of_a_script_is_refused(self):
        os.makedirs(os.path.join(self.root, "bundle", "runtime", "loopdir"))
        self.assertEqual(
            wiring_audit.resolve_route(self.root, "runtime/loopdir"),
            "not a regular file")
        _write(os.path.join(self.root, "bundle", "runtime", "empty.py"), "")
        self.assertEqual(
            wiring_audit.resolve_route(self.root, "runtime/empty.py"),
            "empty")
        _write(os.path.join(self.root, "bundle", "runtime", "good.py"),
               "x = 1\n")
        self.assertEqual(
            wiring_audit.resolve_route(self.root, "runtime/good.py"), "")


class UserRoutesTests(TempTreeTestCase):
    def test_no_skill_found_is_no_data_exit_2(self):
        code, lines = wiring_audit.user_routes(self.root)
        self.assertEqual(code, 2, lines)
        self.assertTrue(lines, lines)
        self.assertTrue(lines[0].startswith("NO-DATA:"), lines)
        self.assertIn(os.path.join(self.root, "bundle", "skills"), lines[0])

    def test_skills_naming_no_route_are_no_data_exit_2(self):
        _skill(self.root, "help", "This skill names no command at all.\n")
        code, lines = wiring_audit.user_routes(self.root)
        self.assertEqual(code, 2, lines)
        self.assertTrue(lines, lines)
        self.assertTrue(lines[0].startswith("NO-DATA:"), lines)

    def test_a_dead_route_is_refused_naming_skill_path_and_reason(self):
        _skill(self.root, "start",
               "Run ${BROTHER_PLUGIN_ROOT}/runtime/loop/start_run.sh now.\n")
        code, lines = wiring_audit.user_routes(self.root)
        self.assertEqual(code, 1, lines)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("FAIL brother-start:", lines[0])
        self.assertIn("runtime/loop/start_run.sh", lines[0])
        self.assertIn("missing", lines[0])

    def test_a_live_route_passes_with_a_count_line(self):
        _skill(self.root, "start", "Run runtime/loop/start_run.sh now.\n")
        _skill(self.root, "help", "Also runtime/loop/stop_loop.sh here.\n")
        _write(os.path.join(self.root, "bundle", "runtime", "loop",
                            "start_run.sh"), "#!/bin/sh\n")
        _write(os.path.join(self.root, "bundle", "runtime", "loop",
                            "stop_loop.sh"), "#!/bin/sh\n")
        code, lines = wiring_audit.user_routes(self.root)
        self.assertEqual(code, 0, lines)
        self.assertEqual(len(lines), 1, lines)
        self.assertEqual(
            lines[0],
            "PASS: 2 distinct command path(s) in 2 verb skill(s) resolve "
            "under bundle/runtime")

    def test_an_unreadable_skill_is_a_fail_line(self):
        _skill(self.root, "start", "runtime/loop/start_run.sh\n")
        _write(os.path.join(self.root, "bundle", "runtime", "loop",
                            "start_run.sh"), "#!/bin/sh\n")
        skill = os.path.join(self.root, "bundle", "skills", "brother-start",
                             "SKILL.md")
        os.remove(skill)
        os.makedirs(skill)
        code, lines = wiring_audit.user_routes(self.root)
        self.assertEqual(code, 1, lines)
        self.assertTrue(lines, lines)
        self.assertIn("FAIL brother-start:", lines[0])

    @unittest.skipUnless(os.path.isdir(REAL_SKILLS),
                         "this tree carries no bundle/skills")
    def test_every_route_the_real_skills_name_resolves(self):
        code, lines = wiring_audit.user_routes(REPO_ROOT)
        self.assertEqual(code, 0, lines)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("PASS:"), lines)


class MainModeTests(TempTreeTestCase):
    def test_user_routes_refuses_to_combine_with_other_modes(self):
        for extra in ("--json", "--tiers", "--selftest"):
            code, out, err = _run_main(["--user-routes", extra,
                                        "--root", self.root])
            self.assertEqual(code, 1, (extra, out, err))
            self.assertIn("--user-routes", err)
            self.assertEqual(out, "", (extra, out))

    def test_user_routes_mode_prints_lines_and_exits_with_the_code(self):
        _skill(self.root, "start",
               "Run ${BROTHER_PLUGIN_ROOT}/runtime/loop/start_run.sh now.\n")
        code, out, err = _run_main(["--user-routes", "--root", self.root])
        self.assertEqual(code, 1, (out, err))
        self.assertIn("FAIL brother-start:", out)
        self.assertIn("runtime/loop/start_run.sh", out)
        self.assertIn("missing", out)

    def test_user_routes_mode_is_no_data_without_skills(self):
        code, out, err = _run_main(["--user-routes", "--root", self.root])
        self.assertEqual(code, 2, (out, err))
        self.assertIn("NO-DATA:", out)

    def test_a_non_list_argv_refuses_with_exit_2(self):
        for bad in (0, True, -1, float("nan"), "", "x", b"x", {},
                    {"a": 1}, (), {1, 2}, object()):
            with self.assertRaises(SystemExit) as caught:
                wiring_audit.main(bad)
            self.assertEqual(caught.exception.code, 2, bad)


class RequiredFastRowTests(unittest.TestCase):
    @unittest.skipUnless(os.path.isfile(REQUIRED_FAST),
                         "this tree carries no scripts/required_fast.sh")
    def test_required_fast_carries_the_user_routes_row_once(self):
        with open(REQUIRED_FAST, "rb") as handle:
            text = handle.read().decode("utf-8", "replace")
        self.assertEqual(text.count("wiring-user-routes"), 1)
        self.assertIn("run_check \"wiring-user-routes\"", text)
        self.assertIn("python3 scripts/wiring_audit.py --user-routes", text)
        self.assertIn("--root \"$(pwd)\"", text)


class HostileInputTests(unittest.TestCase):
    VALUES = (None, 0, 1, -1, True, False, 1.5, float("nan"), "", "x", b"x",
              [], ["x"], {}, {"a": 1}, (), {1, 2}, object())

    def _drive(self, call):
        try:
            call()
        except (ValueError, SystemExit):
            return
        except Exception as exc:
            self.fail("call raised %s: %s" % (type(exc).__name__, exc))

    def test_every_public_route_function_returns_or_refuses(self):
        for value in self.VALUES:
            self._drive(lambda v=value: wiring_audit.skill_routes(v))
            self._drive(lambda v=value: wiring_audit.resolve_route(v, v))
            self._drive(lambda v=value: wiring_audit.user_routes(v))

    def test_a_wrong_typed_root_or_route_is_refused_with_value_error(self):
        for bad in (0, True, -1, float("nan"), b"x", [], {}, (), {1, 2},
                    object()):
            with self.assertRaises(ValueError):
                wiring_audit.resolve_route(bad, "runtime/x.py")
            with self.assertRaises(ValueError):
                wiring_audit.resolve_route("/tmp", bad)
        with self.assertRaises(ValueError):
            wiring_audit.skill_routes(b"runtime/x.py")
        with self.assertRaises(ValueError):
            wiring_audit.user_routes(b"x")


if __name__ == "__main__":
    unittest.main()
