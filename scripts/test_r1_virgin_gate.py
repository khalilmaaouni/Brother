"""R1.3: the virgin gate, proved where the public runner runs it.

check_virgin_gate is this slice's control point for RQ-003 (the export shaped
tree is built by the exporter's own function) and for RQ-008 (a corrupt or
missing summary is NO-DATA, never OK). The tests below prove the three things
this slice names: the tree, the HOME conditions and the verdict parse. Hostile
API input is refused with ValueError by every late gate entry point this slice
owns, never a TypeError from inside and never a silent accept.

The module under test sits beside this file, so it is loaded from that path by
file, and this test file holds no import statement for it. That keeps the test
reading the module that ships in this folder and never a same named module
that may sit earlier on sys.path.
"""
import importlib.util
import os
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_sibling():
    """Load the module that sits beside this test file, by path."""
    path = os.path.join(HERE, "cut_preflight.py")
    spec = importlib.util.spec_from_file_location("cut_preflight_r13_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


P = _load_sibling()

#: None, wrong types, a bool, bytes, NaN, an unhashable value and an empty
#: string: every one of these must be refused by a late gate entry point.
BAD_TEXT = (None, 123, 0.5, float("nan"), True, False, b"x", ["."],
            {"root": "."}, "")


class _Proc(object):
    """The shape subprocess.run returns, without importing subprocess here."""

    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class Runner(object):
    """A canned stand in for subprocess.run. It answers by the script or the
    git verb in the command and records every call, so a test can assert the
    conditions the gate ran under."""

    def __init__(self, answers=None):
        self.answers = dict(answers or {})
        self.calls = []

    def __call__(self, cmd, **kw):
        key = os.path.basename(cmd[1]) if cmd[0] == "sh" else " ".join(cmd[:2])
        self.calls.append((key, list(cmd), kw))
        code, out = self.answers.get(key, (0, ""))
        return _Proc(code, out, "")


def _gate(answers, build=None):
    runner = Runner(answers)
    result = P.check_virgin_gate(".", runner, build=build or (lambda dest: None))
    return result, runner


class TheSummaryParseIsStrict(unittest.TestCase):
    """RQ-008 at the virgin gate: the summary line decides, and a missing or
    count free summary is NO-DATA, never OK."""

    def test_a_green_export_tree_is_ok(self):
        (verdict, name, _), _ = _gate(
            {"required_fast.sh": (0, "pass 34   fail 0   no-data 3\n")})
        self.assertEqual((verdict, name), (P.OK, "virgin-gate"))

    def test_a_red_export_tree_refuses_and_names_the_suite(self):
        (verdict, _, detail), _ = _gate(
            {"required_fast.sh": (1, "pass 30   fail 2   no-data 5\n"
                                     "FAILED: receipt-door export-public\n")})
        self.assertEqual(verdict, P.REFUSED)
        self.assertIn("fail 2", detail)
        self.assertIn("receipt-door export-public", detail)

    def test_no_summary_line_is_no_data_never_ok(self):
        (verdict, _, _), _ = _gate(
            {"required_fast.sh": (0, "nothing useful here\n")})
        self.assertEqual(verdict, P.NODATA)

    def test_a_summary_without_counts_is_no_data(self):
        (verdict, _, _), _ = _gate(
            {"required_fast.sh": (0, "pass fail no-data\n")})
        self.assertEqual(verdict, P.NODATA)

    def test_a_non_zero_exit_with_a_clean_summary_is_still_refused(self):
        (verdict, _, _), _ = _gate(
            {"required_fast.sh": (1, "pass 3   fail 0   no-data 0\n")})
        self.assertEqual(verdict, P.REFUSED)


class TheTreeAndTheHomeAreThePublicRunners(unittest.TestCase):
    """RQ-003 and RQ-002 at the control point: the gate runs the marker script
    inside the tree the builder made, under a fresh HOME with no git variable
    exported, and it leaves neither the tree nor the HOME behind."""

    def test_the_export_tree_marker_is_the_path_the_plan_names(self):
        self.assertEqual(P.EXPORT_TREE_MARKER.replace(os.sep, "/"),
                         "scripts/required_fast.sh")

    def test_the_gate_runs_in_the_built_tree_and_not_in_the_author_tree(self):
        seen = []

        def build(dest):
            seen.append(dest)
            self.assertTrue(os.path.isdir(dest),
                            "the builder is given a real directory")

        (verdict, _, _), runner = _gate(
            {"required_fast.sh": (0, "pass 1   fail 0   no-data 0\n")},
            build=build)
        self.assertEqual(verdict, P.OK)
        _, cmd, kw = runner.calls[0]
        self.assertEqual(cmd[0], "sh")
        self.assertEqual(cmd[1], os.path.join(seen[0], P.EXPORT_TREE_MARKER))
        self.assertEqual(kw["cwd"], seen[0])
        self.assertNotEqual(os.path.abspath(kw["cwd"]), os.path.abspath("."))

    def test_the_tree_and_the_home_are_gone_after_the_run(self):
        seen = []
        (verdict, _, _), runner = _gate(
            {"required_fast.sh": (0, "pass 1   fail 0   no-data 0\n")},
            build=lambda dest: seen.append(dest))
        self.assertEqual(verdict, P.OK)
        home = runner.calls[0][2]["env"]["HOME"]
        self.assertFalse(os.path.exists(seen[0]), "the built tree was left behind")
        self.assertFalse(os.path.exists(home), "the HOME was left behind")

    def test_each_run_gets_its_own_tree_and_its_own_home(self):
        trees, homes = [], []
        for _ in range(2):
            (_, _, _), runner = _gate(
                {"required_fast.sh": (0, "pass 1   fail 0   no-data 0\n")},
                build=lambda dest: trees.append(dest))
            homes.append(runner.calls[0][2]["env"]["HOME"])
        self.assertEqual(len(set(trees)), 2)
        self.assertEqual(len(set(homes)), 2)
        for path in trees + homes:
            self.assertFalse(os.path.exists(path), "%s was left behind" % path)

    def test_the_home_is_fresh_and_never_the_operator_home(self):
        (_, _, _), runner = _gate(
            {"required_fast.sh": (0, "pass 1   fail 0   no-data 0\n")})
        home = runner.calls[0][2]["env"]["HOME"]
        self.assertNotEqual(home, os.path.expanduser("~"))
        self.assertIn("cut-preflight-home-", home)

    def test_no_git_variable_reaches_the_gate(self):
        with mock.patch.dict(os.environ, {"GIT_DIR": "/real/.git",
                                          "GIT_AUTHOR_NAME": "x",
                                          "GIT_INDEX_FILE": "/real/index"}):
            (verdict, _, _), runner = _gate(
                {"required_fast.sh": (0, "pass 1   fail 0   no-data 0\n")})
        self.assertEqual(verdict, P.OK)
        leaked = [k for k in runner.calls[0][2]["env"] if k.startswith("GIT_")]
        self.assertEqual(leaked, [])


class ARefusalIsNeverCachedAndNeverLeavesATree(unittest.TestCase):
    def test_a_refused_gate_removes_its_tree(self):
        seen = []
        (verdict, _, _), _ = _gate(
            {"required_fast.sh": (1, "pass 1   fail 2   no-data 0\n")},
            build=lambda dest: seen.append(dest))
        self.assertEqual(verdict, P.REFUSED)
        self.assertFalse(os.path.exists(seen[0]), "a refused run left its tree")

    def test_a_tree_that_cannot_be_built_is_no_data_never_ok(self):
        seen = []

        def boom(dest):
            seen.append(dest)
            raise RuntimeError("allowlist unreadable")

        (verdict, _, detail), _ = _gate({}, build=boom)
        self.assertEqual(verdict, P.NODATA)
        self.assertIn("could not be built", detail)
        self.assertFalse(os.path.exists(seen[0]), "a failed build left its tree")

    def test_the_verdict_is_not_cached_from_a_prior_version(self):
        (first, _, _), _ = _gate(
            {"required_fast.sh": (0, "pass 1   fail 0   no-data 0\n")})
        (second, _, _), _ = _gate(
            {"required_fast.sh": (1, "pass 1   fail 2   no-data 0\n")})
        self.assertEqual((first, second), (P.OK, P.REFUSED))


class TheLateGatePeersReadTheirInputsHarshly(unittest.TestCase):
    """The other late gates this slice owns, and the guard each of them
    routes its text arguments through."""

    def test_a_leftover_release_branch_refuses_and_names_it(self):
        runner = Runner({"git ls-remote": (0, "cd2a18\trefs/heads/release/1.0.21\n")})
        verdict, name, detail = P.check_public_remote(".", "1.0.21", "https://x",
                                                      runner)
        self.assertEqual((verdict, name), (P.REFUSED, "public-remote"))
        self.assertIn("refs/heads/release/1.0.21", detail)

    def test_a_clean_remote_is_ok_and_both_refs_are_asked(self):
        runner = Runner()
        self.assertEqual(P.check_public_remote(".", "1.0.21", "https://x",
                                               runner)[0], P.OK)
        cmd = runner.calls[0][1]
        self.assertIn("refs/heads/release/1.0.21", cmd)
        self.assertIn("refs/tags/v1.0.21", cmd)

    def test_an_unreachable_remote_is_no_data(self):
        runner = Runner({"git ls-remote": (128, "fatal: unable to access\n")})
        self.assertEqual(P.check_public_remote(".", "1.0.21", "https://x",
                                               runner)[0], P.NODATA)


class HostileInputIsRefusedNotCrashed(unittest.TestCase):
    """RQ-008: None, a wrong type, a bool, bytes, NaN or an empty string
    where a path, a commit, a version or a remote belongs is refused with
    ValueError by every late gate entry point, never a TypeError from inside
    and never an accepted value."""

    def test_check_virgin_gate_refuses_a_hostile_root(self):
        for bad in BAD_TEXT:
            with self.assertRaises(ValueError):
                P.check_virgin_gate(bad, Runner(), build=lambda dest: None)

    def test_check_virgin_gate_refuses_a_non_callable_runner(self):
        for bad in ("not callable", 7, ["n"], True):
            with self.assertRaises(ValueError):
                P.check_virgin_gate(".", bad, build=lambda dest: None)

    def test_check_virgin_gate_refuses_a_non_callable_build(self):
        for bad in ("not callable", 7, ["n"], True):
            with self.assertRaises(ValueError):
                P.check_virgin_gate(".", Runner(), build=bad)

    def test_previous_cut_commit_refuses_a_hostile_root(self):
        for bad in BAD_TEXT:
            with self.assertRaises(ValueError):
                P.previous_cut_commit(bad)

    def test_check_changelog_text_refuses_a_hostile_root(self):
        for bad in BAD_TEXT:
            with self.assertRaises(ValueError):
                P.check_changelog_text(bad, "9674ed89", Runner())

    def test_check_changelog_text_refuses_a_hostile_since(self):
        for bad in (123, 0.5, float("nan"), True, False, b"x", ["s"], {}, ""):
            with self.assertRaises(ValueError):
                P.check_changelog_text(".", bad, Runner())

    def test_check_public_remote_refuses_each_hostile_argument(self):
        for bad in BAD_TEXT:
            with self.assertRaises(ValueError):
                P.check_public_remote(bad, "1.0.21", "https://x", Runner())
            with self.assertRaises(ValueError):
                P.check_public_remote(".", bad, "https://x", Runner())
            with self.assertRaises(ValueError):
                P.check_public_remote(".", "1.0.21", bad, Runner())


if __name__ == "__main__":
    unittest.main()
