"""What the L5 command check must keep true.

Half of this file is about the checker NOT firing. It manufactured two false
positives before it worked, both against correct plan content, and that failure
costs more than a miss: it sends a person to fix something already right, and it
teaches everyone to ignore the tool.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_l5_commands as C  # noqa: E402


ARGPARSE_HELP = "usage: t.py [-h] [--json] [--selftest]\n\noptions:\n  -h, --help  show this\n"
SUBPARSER_TOP = ("usage: t.py [-h] [--db DB] {record,attribute} ...\n\n"
                 "options:\n  -h, --help  show this\n  --db DB  the store\n")
SUB_HELP = ("usage: t.py record [-h] --unit UNIT [--surfaced S]\n\n"
            "options:\n  -h, --help  show this\n")
HANDROLLED = "Usage: t.py do-a-thing\nNo argparse here.\n"


def runner_for(mapping):
    """Answers --help per (subcommand or None) so both levels can be faked."""
    def _run(argv):
        sub = argv[2] if len(argv) > 3 else None
        text = mapping.get(sub, "")

        class P(object):
            stdout, stderr = text, ""
        return P()
    return _run


class ItCatchesTheInventedFlag(unittest.TestCase):
    """The defect it exists for: a REAL script with an UNREAL flag, which passes
    every structural rule and fails only when somebody does the work."""

    def test_a_flag_the_help_does_not_list_is_FAIL(self):
        v, why = C.check_command(
            "python3 scripts/check_l5_commands.py --no-such-flag", set())
        self.assertEqual(v, C.FAIL, why)
        self.assertIn("--no-such-flag", why)

    def test_a_flag_the_help_DOES_list_is_PASS(self):
        v, why = C.check_command(
            "python3 scripts/check_l5_commands.py --verbose", set())
        self.assertEqual(v, C.PASS, why)


class ItRefusesToManufactureAViolation(unittest.TestCase):
    """Both of these were real false positives during development."""

    def test_a_SUBCOMMAND_flag_is_not_reported_as_invented(self):
        """First false positive: reading only the top level help, under which no
        subcommand flag appears."""
        v, why = C.check_command(
            "python3 scripts/check_l5_commands.py record --unit u", set(),
            runner=runner_for({None: SUBPARSER_TOP, "record": SUB_HELP}),
            roots=[C.ROOT],
            help_cache={})
        self.assertNotEqual(v, C.FAIL, why)

    def test_a_GLOBAL_flag_used_BEFORE_a_subcommand_is_not_invented(self):
        """Second false positive: once it read the subcommand help it stopped
        reading the top level, so the global --db became invented. A real
        invocation mixes both, so both are consulted."""
        v, why = C.check_command(
            "python3 scripts/check_l5_commands.py --db PATH record --unit u", set(),
            runner=runner_for({None: SUBPARSER_TOP, "record": SUB_HELP}),
            roots=[C.ROOT],
            help_cache={})
        self.assertNotEqual(v, C.FAIL, why)

    def test_a_hand_rolled_help_is_UNREADABLE_and_never_FAIL(self):
        """--help only lists flags when the script builds it from a parser. A
        script reading sys.argv by hand can accept flags its help never
        mentions, so this declines to rule."""
        v, why = C.check_command(
            "python3 scripts/check_l5_commands.py --whatever", set(),
            runner=runner_for({None: HANDROLLED}),
            roots=[C.ROOT],
            help_cache={})
        self.assertEqual(v, C.UNREADABLE, why)

    def test_a_command_that_is_not_a_python_invocation_is_left_alone(self):
        v, why = C.check_command("grep -rn 'x' docs/ | sort", set())
        self.assertEqual(v, C.PASS, why)


class AMissingScriptIsOnlyAFailureIfNobodyCreatesIt(unittest.TestCase):
    def test_a_planned_file_a_package_declares_is_PLANNED(self):
        v, why = C.check_command("python3 scripts/not_yet.py --x",
                                 {"not_yet.py"})
        self.assertEqual(v, C.PLANNED, why)

    def test_a_missing_file_nobody_declares_is_FAIL(self):
        v, why = C.check_command("python3 scripts/nobody_makes_this.py", set())
        self.assertEqual(v, C.FAIL, why)
        self.assertIn("nothing is going to create it", why)


class OneRepoProductSubtreesResolve(unittest.TestCase):
    """M5 (docs/plan/ONE-REPO-TRANSITION-2026-08-31.md): a roadmap step
    tagged "(BrotherModeUp)" or "(BrotherSBE)" names its script the way
    that product named it standalone (relative to its own OLD root, e.g.
    "tools/bm_playbook.py"), and that path must resolve inside this one
    repo's own products/<name> subtree, not only inside the old standalone
    SIBLING checkout on this machine, since M7 retires that checkout and a
    resolve() that only worked through it would go blind that day."""

    def test_a_brothermode_owned_tool_resolves_inside_products_brothermode(self):
        found = C.resolve("tools/bm_playbook.py")
        self.assertIsNotNone(found, "tools/bm_playbook.py did not resolve anywhere")
        self.assertTrue(
            found.startswith(os.path.join(C.ROOT, "products", "brothermode")),
            "resolved outside the one repo's own product subtree: %s" % found)

    def test_a_brothersbe_owned_tool_resolves_inside_products_brothersbe(self):
        found = C.resolve("tools/sbe_design.py")
        self.assertIsNotNone(found, "tools/sbe_design.py did not resolve anywhere")
        self.assertTrue(
            found.startswith(os.path.join(C.ROOT, "products", "brothersbe")),
            "resolved outside the one repo's own product subtree: %s" % found)

    def test_resolve_finds_a_product_tool_even_with_no_sibling_checkout(self):
        """Isolates the fix from the SIBLING fallback: a script that exists
        ONLY under a fake ROOT's products/ subtree, with no SIBLING
        candidate at all, must still resolve."""
        d = tempfile.mkdtemp()
        product_tools = os.path.join(d, "products", "brothermode", "tools")
        os.makedirs(product_tools)
        target = os.path.join(product_tools, "only_here.py")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("print('ok')\n")
        roots = (d,) + tuple(
            os.path.join(d, "products", name)
            for name in ("brothermode", "brothersbe", "brotherds"))
        self.assertEqual(C.resolve("tools/only_here.py", roots=roots), target)


class TheFourVerdictsStayFour(unittest.TestCase):
    def test_they_are_four_distinct_strings(self):
        self.assertEqual(len({C.PASS, C.PLANNED, C.UNREADABLE, C.FAIL}), 4)

    def test_only_FAIL_sets_the_exit_code(self):
        """PLANNED and UNREADABLE are reported, never fatal: a plan legitimately
        names files it will create, and declining to rule is not a failure."""
        d = tempfile.mkdtemp()
        path = os.path.join(d, "r.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"rows": [{"id": "X", "owns": ["scripts/planned.py"],
                                 "subtasks": [{"id": "X.1", "steps": [
                                     {"id": "X.1.1",
                                      "command": "python3 scripts/planned.py --x"}]}]}],
                       "features": []}, fh)
        self.assertEqual(C.main(["--roadmap", path]), 0)


class TheRealBoardHasNoInventedCommands(unittest.TestCase):
    """The check that keeps the L5 layer honest from here on."""

    def test_the_live_roadmap_passes(self):
        self.assertEqual(C.main([]), 0)


class NoDataIsNeverAPass(unittest.TestCase):
    def test_an_unreadable_roadmap_is_NO_DATA(self):
        self.assertEqual(C.main(["--roadmap", "/no/such/file.json"]), 2)

    def test_a_board_with_no_steps_is_NO_DATA_not_clean(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "r.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"rows": [], "features": []}, fh)
        self.assertEqual(C.main(["--roadmap", path]), 2)


if __name__ == "__main__":
    unittest.main()
