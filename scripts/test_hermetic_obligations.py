"""The hermetic gate honours the estate's own declared NO-DATA, and only that.

hermetic_test_check.check() runs a changed test on the export tree the public
runner sees and used to refuse every exit but 0. A check that cannot run its
input (the input is not shipped) must say NO-DATA, exit 2, and
scripts/gate_obligations.json declares that expectation (expected_absent_input,
read by required_fast.sh through evidence_obligation.py). The gate now accepts an
exit 2 when, and only when, that declaration holds on the export tree:

  (a) a required_fast.sh check whose whole command is this test file declares an input,
  (b) the input stays inside the tree and is absent from it,
  (c) the test's output names it.

Everything else is refused exactly as before. Each case below isolates ONE
condition, driven through the real entry points (check(), and main() for the
printed line), on a canned runner so nothing executes. Hermetic by design: a
private temp root, no HOME, no ps, no network.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hermetic_test_check as H  # noqa: E402

TEST = "scripts/test_door.py"
INPUT = "docs/plan/PLAN.json"
CHECK = "door-check"
DECL = {"schema": "brother.gate-obligations/v1", "default": "REQUIRED_FOR_MERGE",
        "checks": {CHECK: {"obligation": "REQUIRED_FOR_MERGE", "expected_absent_input": INPUT,
                           "reason": "the plan is not shipped in the public edition."}}}
SAYS = "SKIPPED: %s is not in this tree\n" % INPUT


class Runner(object):
    """A canned subprocess.run: one fixed answer for the test, (code, stdout, stderr)."""

    def __init__(self, code=2, out=SAYS, err=""):
        self.answer = (code, out, err)
        self.calls = 0

    def __call__(self, cmd, **kw):
        self.calls += 1
        code, out, err = self.answer
        return subprocess.CompletedProcess(cmd, code, out, err)


def rf(*lines):
    return "\n".join(("#!/bin/sh",) + lines) + "\n"


GOOD_RF = rf('run_check "%s"   python3 %s' % (CHECK, TEST))


def tree_builder(required_fast=GOOD_RF, obligations=DECL, present=False, symlink=False, extra=()):
    """build(dest) for check(): the export tree a case ships. `obligations` is a dict (written as JSON), a str (written
    raw, to make it corrupt), or None (not shipped); `required_fast` is a str or None (not shipped)."""
    def build(dest):
        os.makedirs(os.path.join(dest, "scripts"))
        open(os.path.join(dest, TEST), "w").close()
        if required_fast is not None:
            with open(os.path.join(dest, "scripts", "required_fast.sh"), "w") as fh:
                fh.write(required_fast)
        if obligations is not None:
            with open(os.path.join(dest, "scripts", "gate_obligations.json"), "w") as fh:
                fh.write(obligations if isinstance(obligations, str) else json.dumps(obligations))
        if present:
            os.makedirs(os.path.join(dest, os.path.dirname(INPUT)))
            open(os.path.join(dest, INPUT), "w").close()
        if symlink:
            os.makedirs(os.path.join(dest, os.path.dirname(INPUT)))
            os.symlink(os.path.join(dest, "nowhere"), os.path.join(dest, INPUT))
        for name in extra:
            os.makedirs(os.path.dirname(os.path.join(dest, name)), exist_ok=True)
            open(os.path.join(dest, name), "w").close()
    return build


def decl(**entry):
    """DECL with the door check's entry replaced by `entry` (empty dict: an entry declaring nothing)."""
    out = json.loads(json.dumps(DECL))
    out["checks"][CHECK] = dict({"obligation": "REQUIRED_FOR_MERGE", "reason": "r"}, **entry)
    return out


class Fixture(object):
    """Shared set up and helpers, mixed in below; every test class names unittest.TestCase itself, which the plan lint reads."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hermobl_hermetic_obl_")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "scripts"))
        open(os.path.join(self.root, TEST), "w").close()
        # The canned runner never executes what it is handed: pin the sandbox wrap present, as test_hermetic_test_check.
        for name, value in (("_sandbox_present", True), ("_sandbox_applies", "")):
            patcher = mock.patch.object(H.G, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(os.environ, {"BROTHER_SCRATCH": os.path.join(self.root, "scratch")})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("BROTHER_SANDBOX", None)
        # A refusal reruns the test in the checkout's HEAD only to choose its words; nothing to rerun here, which the
        # refusal text says ("could not run"), so no git repository is needed and the verdict is what is asserted.
        patcher = mock.patch.object(H, "_checkout_rerun", return_value=(None, "no rerun in this fixture"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_gate(self, code=2, out=SAYS, err="", **tree):
        runner = Runner(code, out, err)
        res = H.check(self.root, [TEST], runner, build=tree_builder(**tree))
        self.assertEqual(runner.calls, 1)
        self.assertEqual(len(res), 1)
        return res[0]

    def assertRefused(self, res):
        self.assertEqual(res[0], H.REFUSED, res)
        self.assertIn("FAILS on the export tree with an empty HOME", res[2])

    def assertDeclaredNoData(self, res, inputs=INPUT):
        self.assertEqual(res[0], H.OK, res)
        self.assertEqual(res[2], "NO-DATA on the export tree as declared (%s absent)" % inputs)


class DeclaredAbsentAndNamedIsAcceptedDistinctly(Fixture, unittest.TestCase):
    def test_the_accepted_line_is_ok_and_says_no_data_never_a_plain_pass(self):
        res = self.run_gate()
        self.assertDeclaredNoData(res)
        self.assertEqual(res[1], TEST)
        self.assertNotIn("passes on the export tree", res[2])
        self.assertEqual(H.P.exit_code([res]), 0)

    def test_the_name_may_be_in_stderr_alone(self):
        self.assertDeclaredNoData(self.run_gate(out="", err=SAYS))

    def test_the_name_may_be_in_stdout_alone(self):
        self.assertDeclaredNoData(self.run_gate(out=SAYS, err=""))

    def test_the_dash_b_and_absolute_interpreter_spellings_of_the_command_map(self):
        for line in ('run_check "%s" python3 -B %s' % (CHECK, TEST),
                     'run_check "%s" /usr/bin/python3 -B %s  # a trailing note' % (CHECK, TEST),
                     'run_check "%s" %s' % (CHECK, TEST)):
            self.assertDeclaredNoData(self.run_gate(required_fast=rf(line)))

    def test_the_check_may_sit_among_other_lines(self):
        text = rf('run_check "other" python3 scripts/test_other.py', "# a comment", "echo hi", GOOD_RF.splitlines()[1])
        self.assertDeclaredNoData(self.run_gate(required_fast=text))

    def test_main_prints_the_distinct_line_and_exits_zero(self):
        runner = Runner()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(H, "ROOT", self.root))
            stack.enter_context(mock.patch.object(H.P, "export_tree_builder", return_value=tree_builder()))
            stack.enter_context(mock.patch.object(H.P, "_run", lambda cmd, cwd, r=None, env=None, timeout=None:
                                                  runner(cmd)))
            out = io.StringIO()
            stack.enter_context(contextlib.redirect_stdout(out))
            code = H.main([TEST])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().strip().split(None, 1)[0], "OK")
        self.assertIn("%s  NO-DATA on the export tree as declared (%s absent)" % (TEST, INPUT), out.getvalue())


class EveryOtherShapeStaysRefused(Fixture, unittest.TestCase):
    def test_declared_but_the_input_is_present_in_the_tree(self):
        self.assertRefused(self.run_gate(present=True))

    def test_declared_but_a_dangling_link_stands_where_the_input_would_be(self):
        self.assertRefused(self.run_gate(symlink=True))

    def test_the_check_has_no_entry_at_all(self):
        self.assertRefused(self.run_gate(obligations={"schema": "s", "default": "REQUIRED_FOR_MERGE", "checks": {}}))

    def test_the_entry_declares_no_absent_input(self):
        self.assertRefused(self.run_gate(obligations=decl()))

    def test_the_entry_declares_an_empty_absent_input(self):
        self.assertRefused(self.run_gate(obligations=decl(expected_absent_input="")))

    def test_the_declaration_belongs_to_a_different_check(self):
        other = json.loads(json.dumps(DECL))
        other["checks"] = {"another-check": other["checks"][CHECK]}
        self.assertRefused(self.run_gate(obligations=other))

    def test_the_output_does_not_name_the_input(self):
        self.assertRefused(self.run_gate(out="SKIPPED: something is missing\n"))

    def test_the_output_names_only_a_prefix_of_the_input(self):
        self.assertRefused(self.run_gate(out="SKIPPED: docs/plan/ is missing\n"))

    def test_exit_one_with_a_full_declaration(self):
        self.assertRefused(self.run_gate(code=1))

    def test_exit_three_with_a_full_declaration(self):
        self.assertRefused(self.run_gate(code=3))

    def test_an_input_that_escapes_the_tree_is_not_a_declaration(self):
        outside = "../outside-plan.json"
        self.assertRefused(self.run_gate(out="SKIPPED: %s\n" % outside, obligations=decl(expected_absent_input=outside)))

    def test_an_absolute_input_is_not_a_declaration(self):
        outside = os.path.join(self.root, "nowhere.json")
        self.assertRefused(self.run_gate(out="SKIPPED: %s\n" % outside, obligations=decl(expected_absent_input=outside)))

    def test_a_non_string_input_is_not_a_declaration(self):
        self.assertRefused(self.run_gate(obligations=decl(expected_absent_input=7)))

    def test_no_check_runs_this_file(self):
        self.assertRefused(self.run_gate(required_fast=rf('run_check "x" python3 scripts/test_other.py')))

    def test_required_fast_is_not_shipped(self):
        self.assertRefused(self.run_gate(required_fast=None))

    def test_a_commented_out_check_is_not_a_check(self):
        self.assertRefused(self.run_gate(required_fast=rf('# run_check "%s" python3 %s' % (CHECK, TEST))))

    def test_a_line_that_is_not_run_check_is_not_a_check(self):
        self.assertRefused(self.run_gate(required_fast=rf('echo "%s" python3 %s' % (CHECK, TEST))))

    def test_a_command_with_extra_arguments_is_not_this_file_run_alone(self):
        self.assertRefused(self.run_gate(required_fast=rf('run_check "%s" python3 %s --max 4' % (CHECK, TEST))))

    def test_a_command_that_only_mentions_the_file_is_not_a_run_of_it(self):
        self.assertRefused(self.run_gate(required_fast=rf('run_check "%s" python3 other.py %s' % (CHECK, TEST))))

    def test_a_check_line_with_no_command_is_not_a_run(self):
        self.assertRefused(self.run_gate(required_fast=rf('run_check "%s"' % CHECK)))

    def test_an_unbalanced_quote_in_required_fast_never_crashes_the_gate(self):
        self.assertRefused(self.run_gate(required_fast=rf("echo 'unclosed", 'run_check "x" python3 other.py')))

    def test_two_checks_run_the_file_and_one_declares_nothing(self):
        both = rf('run_check "%s" python3 %s' % (CHECK, TEST), 'run_check "second" python3 -B %s' % TEST)
        self.assertRefused(self.run_gate(required_fast=both))

    def test_two_checks_run_the_file_and_one_input_is_present(self):
        both = rf('run_check "%s" python3 %s' % (CHECK, TEST), 'run_check "second" python3 -B %s' % TEST)
        obligations = json.loads(json.dumps(DECL))
        obligations["checks"]["second"] = dict(obligations["checks"][CHECK], expected_absent_input="docs/there.json")
        self.assertRefused(self.run_gate(required_fast=both, obligations=obligations, extra=["docs/there.json"],
                                         out=SAYS + "docs/there.json\n"))

    def test_two_checks_run_the_file_and_the_output_names_only_one_input(self):
        both = rf('run_check "%s" python3 %s' % (CHECK, TEST), 'run_check "second" python3 -B %s' % TEST)
        obligations = json.loads(json.dumps(DECL))
        obligations["checks"]["second"] = dict(obligations["checks"][CHECK], expected_absent_input="docs/other.json")
        self.assertRefused(self.run_gate(required_fast=both, obligations=obligations))

    def test_two_checks_with_two_absent_inputs_both_named_are_accepted_with_both_listed(self):
        both = rf('run_check "%s" python3 %s' % (CHECK, TEST), 'run_check "second" python3 -B %s' % TEST)
        obligations = json.loads(json.dumps(DECL))
        obligations["checks"]["second"] = dict(obligations["checks"][CHECK], expected_absent_input="docs/other.json")
        res = self.run_gate(required_fast=both, obligations=obligations, out=SAYS + "docs/other.json\n")
        self.assertDeclaredNoData(res, "%s, docs/other.json" % INPUT)


class AnUnreadableDeclarationFailsClosed(Fixture, unittest.TestCase):
    def test_gate_obligations_is_not_shipped(self):
        self.assertRefused(self.run_gate(obligations=None))

    def test_gate_obligations_is_truncated_json(self):
        self.assertRefused(self.run_gate(obligations=json.dumps(DECL)[:60]))

    def test_gate_obligations_is_empty(self):
        self.assertRefused(self.run_gate(obligations=""))

    def test_gate_obligations_has_the_wrong_top_level(self):
        self.assertRefused(self.run_gate(obligations="[1, 2]"))

    def test_gate_obligations_has_an_unknown_default_level(self):
        bad = json.loads(json.dumps(DECL))
        bad["default"] = "WHENEVER"
        self.assertRefused(self.run_gate(obligations=bad))

    def test_the_directory_reads_as_the_file(self):
        def build(dest):
            tree_builder(obligations=None)(dest)
            os.makedirs(os.path.join(dest, "scripts", "gate_obligations.json"))
        res = H.check(self.root, [TEST], Runner(), build=build)[0]
        self.assertRefused(res)


class ExitZeroAndTheOtherExitsAreUnchanged(Fixture, unittest.TestCase):
    def test_exit_zero_is_still_a_plain_pass_even_with_a_declaration_on_file(self):
        res = self.run_gate(code=0, out="OK\n")
        self.assertEqual(res[0], H.OK)
        self.assertEqual(res[2], "passes on the export tree with an empty HOME: OK")

    def test_exit_zero_with_the_input_present_is_still_a_pass(self):
        self.assertEqual(self.run_gate(code=0, out="OK\n", present=True)[0], H.OK)

    def test_a_timeout_and_a_missing_interpreter_are_still_no_data(self):
        for code in (124, 127):
            res = self.run_gate(code=code, out="boom\n")
            self.assertEqual(res[0], H.NODATA)
            self.assertIn("could not be run", res[2])

    def test_a_test_not_shipped_is_still_ok_and_the_runner_is_never_called(self):
        runner = Runner()

        def build(dest):
            os.makedirs(os.path.join(dest, "scripts"))
        res = H.check(self.root, [TEST], runner, build=build)[0]
        self.assertEqual((res[0], runner.calls), (H.OK, 0))

    def test_the_refusal_text_is_the_one_it_always_was(self):
        res = self.run_gate(code=2, out="boom\n")
        self.assertRefused(res)
        self.assertIn("(exit 2: boom)", res[2])
        self.assertEqual(H.P.exit_code([res]), H.P.EXIT_REFUSED)


class TheDecisionFunctionOnItsOwn(Fixture, unittest.TestCase):
    """declared_no_data is the rule; check() only calls it. Asked directly for the reason a case is refused."""

    def ask(self, code=2, out=SAYS, **tree):
        dest = os.path.join(tempfile.mkdtemp(dir=self.root), "tree")
        tree_builder(**tree)(dest)
        return H.declared_no_data(dest, TEST, subprocess.CompletedProcess([], code, out, ""))

    def test_accepts_and_lists_the_input(self):
        self.assertEqual(self.ask(), ([INPUT], ""))

    def test_each_refusal_gives_its_own_reason(self):
        self.assertIn("not NO-DATA", self.ask(code=1)[1])
        self.assertIn("no required_fast.sh check", self.ask(required_fast=rf("echo"))[1])
        self.assertIn("gate_obligations.json", self.ask(obligations="{")[1])
        self.assertIn("no usable expected_absent_input", self.ask(obligations=decl())[1])
        self.assertIn("present", self.ask(present=True)[1])
        self.assertIn("never names", self.ask(out="x")[1])
        for answer in (self.ask(code=1), self.ask(obligations="{"), self.ask(present=True)):
            self.assertIsNone(answer[0])

    def test_an_empty_stdout_and_stderr_are_none_safe(self):
        dest = os.path.join(self.root, "tree")
        tree_builder()(dest)
        proc = subprocess.CompletedProcess([], 2, None, None)
        self.assertIn("never names", H.declared_no_data(dest, TEST, proc)[1])

    def test_agrees_with_the_estates_own_obligation_step(self):
        """The same declaration, asked of evidence_obligation.py transition: an exit 2 with the input absent is ALLOWED
        there, present is BLOCKED there, and this gate follows the same split."""
        dest = os.path.join(self.root, "agree")
        tree_builder()(dest)
        here = os.path.join(HERE, "evidence_obligation.py")
        for present in (False, True):
            if present:
                os.makedirs(os.path.join(dest, os.path.dirname(INPUT)))
                open(os.path.join(dest, INPUT), "w").close()
            proc = subprocess.run([sys.executable, "-B", here, "transition", "--stage", "merge", "--repo", dest],
                                  input="%s\t2\n" % CHECK, capture_output=True, text=True, timeout=60)
            allowed = proc.returncode == 0
            mine = H.declared_no_data(dest, TEST, subprocess.CompletedProcess([], 2, SAYS, ""))[0] is not None
            self.assertEqual((allowed, mine), (not present, not present))


if __name__ == "__main__":
    unittest.main()
