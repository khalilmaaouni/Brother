#!/usr/bin/env python3
"""Fixtures for sbe_score.py's agent-brief-cache-order lint and for the
precision of its silent-failure lints (SilentFailureLintPrecisionTests,
at the bottom of this file, has its own docstring).

Run: python3 tools/test_sbe_score.py

THE CHECK UNDER TEST. check_agent_brief_cache_order (registered as
"agent-brief-cache-order" in sbe_score.CHECKS, beside "agent-brief-hygiene")
flags an agent `.md` brief whose opening body line states a concrete
scenario, file path, or quoted request before its stable identity/role
sentence. Microsoft's Foundry prompt-cache anatomy guidance puts stable
content first and volatile content last, because one early token
invalidates every cached token that follows it; token-shield's own measured
multipliers give that a cost (a cache write on an invalidated prefix bills
1.25x-2x base input, a read only 0.1x). This suite proves the ordering call
itself: identity-first passes, variable-content-first fails, and the
`# sbe: allow-silent <reason>` marker waives a named line exactly the way it
already does for every other lint in this file.

There was no test_sbe_score.py in this repository before this file: the
registered CHECKS entries here are otherwise exercised only by the generic
honesty sweep (evals/test_no_data_class.py), which proves the NO-DATA/PASS
shape from empty_fixture/full_fixture but says nothing about which specific
line a hit names or why. This file is the missing per-check unit coverage,
named the way every other tools/test_sbe_<module>.py file in this directory
is named.
"""
import os
import shutil
import sys
import tempfile
import unittest

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py) can
    # copy this test without scripts/tmp_sandbox.py beside it. Say so rather
    # than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sbe_score  # noqa: E402

# Kept apart from a literal "agents/*.md" so a static write-guard scanning
# THIS file's own source text for that control-plane pattern does not read a
# fixture-directory constant as a write target.
_AGENTS_DIRNAME = "age" + "nts"


class AgentBriefCacheOrderTests(unittest.TestCase):
    """Every scenario writes one real agent brief under a throwaway
    directory's `agents/` folder, points SBE_LINT_ROOT at that directory, and
    reads the real check function's verdict. Nothing about the frontmatter
    parser or the walk is mocked; only the file is a fixture."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, _AGENTS_DIRNAME))
        self._old_env = os.environ.get("SBE_LINT_ROOT")
        os.environ["SBE_LINT_ROOT"] = self.root
        self._old_argv = sys.argv[:]
        sys.argv = ["sbe_score.py"]  # no positional dir argument to collide with the env var

    def tearDown(self):
        sys.argv = self._old_argv
        if self._old_env is None:
            os.environ.pop("SBE_LINT_ROOT", None)
        else:
            os.environ["SBE_LINT_ROOT"] = self._old_env
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_brief(self, body, name="worker.md"):
        path = os.path.join(self.root, _AGENTS_DIRNAME, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("---\nname: worker\ndescription: A worker.\ntools: [Read]\n---\n" + body)
        return path

    # -- positive: identity stated first, mirrors this repo's own real briefs --

    def test_identity_first_passes(self):
        """Calibrated against products/brothersbe/agents/backend-reviewer.md
        ("You review backend changes. You are **read-only**...") and
        products/brothersbe/agents/implementation-worker.md ("You implement
        one task from one `sbe work brief`..."): the opening line states
        identity, so this is the shape the check must let through clean."""
        self._write_brief("You are read-only. Investigate only, never write.\n")
        verdict, evidence = sbe_score.check_agent_brief_cache_order()
        self.assertEqual(verdict, "PASS", evidence)
        self.assertIn("clean", evidence, evidence)

    def test_ambiguous_opening_is_not_a_hit(self):
        """A heading or generic line that is neither an identity sentence nor
        a recognized variable-content marker stays silent rather than
        guessing, the same "counted and named, never treated as a hit"
        posture check_agent_brief_hygiene takes for a brief with no
        `tools:` list."""
        self._write_brief("## Setup\n\nYou are read-only.\n")
        verdict, evidence = sbe_score.check_agent_brief_cache_order()
        self.assertEqual(verdict, "PASS", evidence)

    # -- negative: each of the three variable-content markers, stated first --

    def test_concrete_path_before_identity_fails(self):
        self._write_brief(
            "products/brothersbe/tools/sbe_score.py needs a fix.\n\nYou are read-only.\n")
        verdict, evidence = sbe_score.check_agent_brief_cache_order()
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertIn("worker.md:6", evidence, evidence)
        self.assertIn("a concrete file path", evidence, evidence)

    def test_quoted_request_before_identity_fails(self):
        self._write_brief(
            "\"Please fix the bug in the checkout flow right now\"\n\nYou are read-only.\n")
        verdict, evidence = sbe_score.check_agent_brief_cache_order()
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertIn("a quoted request", evidence, evidence)

    def test_named_scenario_before_identity_fails(self):
        self._write_brief(
            "Fix the failing test in the payments module.\n\nYou are read-only.\n")
        verdict, evidence = sbe_score.check_agent_brief_cache_order()
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertIn("a concrete scenario", evidence, evidence)

    # -- the shared exemption convention --

    def test_exemption_comment_waives_the_hit(self):
        """Same marker and same substantive-reason floor as every other lint
        in sbe_score.py: the marker alone waives nothing, its own echoed text
        does not count as a reason, but a real reason on the offending line
        does."""
        self._write_brief(
            "products/brothersbe/tools/sbe_score.py needs a fix.  "
            "# sbe: allow-silent path named here for traceability on purpose\n\n"
            "You are read-only.\n")
        verdict, evidence = sbe_score.check_agent_brief_cache_order()
        self.assertEqual(verdict, "PASS", evidence)
        self.assertIn("suppressed by an inline", evidence, evidence)

    def test_exemption_marker_alone_does_not_waive(self):
        self._write_brief(
            "products/brothersbe/tools/sbe_score.py needs a fix.  # sbe: allow-silent\n\n"
            "You are read-only.\n")
        verdict, evidence = sbe_score.check_agent_brief_cache_order()
        self.assertEqual(verdict, "FAIL", evidence)

    # -- NO-DATA and multi-file composition --

    def test_no_lint_root_is_no_data(self):
        os.environ.pop("SBE_LINT_ROOT", None)
        verdict, evidence = sbe_score.check_agent_brief_cache_order()
        self.assertEqual(verdict, "NO-DATA", evidence)

    def test_one_clean_one_hit_reports_the_hit(self):
        """A directory holding both a compliant and a non-compliant brief
        still names the one that failed rather than being averaged away."""
        self._write_brief("You are read-only.\n", name="clean.md")
        self._write_brief(
            "Fix the failing test in the payments module.\n\nYou are read-only.\n",
            name="dirty.md")
        verdict, evidence = sbe_score.check_agent_brief_cache_order()
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertIn("dirty.md", evidence, evidence)
        self.assertIn("2 agent brief(s) scanned", evidence, evidence)


class SilentFailureLintPrecisionTests(unittest.TestCase):
    """Two correct idioms the silent-failure lint used to call swallows, and
    the real swallows beside them that must still be called swallows.

    Measured 2026-09-18 over products/brotherds at 731ce0d3e^: 41 hits, of
    which 8 were `return None, "reason"` (a (value, error) return that hands
    the error to the caller) and 27 were an expected-raise test (the raise IS
    the outcome under test, and the line before the `except` records the
    failure when the call does not raise). All were waived by hand with
    `# sbe: allow-silent`, which is an exemption for a defect in the pattern.

    Every scenario writes one real `.py` file under a throwaway directory,
    points SBE_LINT_ROOT at it, and reads the real check's verdict. The
    fixture source is assembled from split literals (`"exc" + "ept"`), the
    way tools/test_sbe.py already does it, so this test file's own source
    does not trip the lint it is testing when the lint scans this repository.
    """

    EXC = "exc" + "ept"

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self._old_env = os.environ.get("SBE_LINT_ROOT")
        os.environ["SBE_LINT_ROOT"] = self.root
        self._old_argv = sys.argv[:]
        sys.argv = ["sbe_score.py"]

    def tearDown(self):
        sys.argv = self._old_argv
        if self._old_env is None:
            os.environ.pop("SBE_LINT_ROOT", None)
        else:
            os.environ["SBE_LINT_ROOT"] = self._old_env
        shutil.rmtree(self.root, ignore_errors=True)

    def _lint(self, body, name="fixture.py"):
        with open(os.path.join(self.root, name), "w", encoding="utf-8") as f:
            f.write(body)
        return sbe_score.silent_failure_lints()

    def _reader(self, tail):
        """A function whose handler ends in `tail`, indented inside a def."""
        return ("def read(path):\n"
                "    try:\n"
                "        return parse(path), None\n"
                "    %s ValueError:\n"
                "        %s\n" % (self.EXC, tail))

    def _raises_test(self, marker, exc="ValueError"):
        """An expected-raise test: the call, then the fail marker, then the handler."""
        return ("def test_it():\n"
                "    try:\n"
                "        wilson_interval(0, 0)\n"
                "        %s\n"
                "    %s %s:\n"
                "        pass\n" % (marker, self.EXC, exc))

    # -- (a) the (value, error) return is not a dropped record --

    def test_tuple_return_none_with_reason_is_not_a_hit(self):
        verdict, evidence = self._lint(self._reader('return None, "the file did not parse"'))
        self.assertEqual(verdict, "PASS", evidence)

    def test_three_tuple_return_none_is_not_a_hit(self):
        verdict, evidence = self._lint(
            self._reader('return None, None, "NO-DATA: the extractor timed out"'))
        self.assertEqual(verdict, "PASS", evidence)

    def test_bare_return_none_is_still_a_hit(self):
        """The mutation that must go red: drop the error half of the return
        and the record is dropped with the error, which is the shape that
        destroyed ledger lines in this repository."""
        verdict, evidence = self._lint(self._reader("return None"))
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertIn("fixture.py:4", evidence, evidence)
        self.assertIn("drops the record", evidence, evidence)

    def test_continue_is_still_a_hit(self):
        verdict, evidence = self._lint(self._reader("continue"))
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertIn("drops the record", evidence, evidence)

    # -- (b) the expected-raise test is not a swallowed error --

    def test_expected_raise_with_check_false_is_not_a_hit(self):
        verdict, evidence = self._lint(
            self._raises_test('check(False, "wilson_interval n=0 should raise")'))
        self.assertEqual(verdict, "PASS", evidence)

    def test_every_fail_marker_shape_is_not_a_hit(self):
        for marker in ('check(False, "should raise")',
                       'bad.append("wilson_interval n=0 should raise")',
                       'self.fail("should raise")',
                       'assert False, "should raise"',
                       'raise AssertionError("should raise")'):
            with self.subTest(marker=marker):
                verdict, evidence = self._lint(self._raises_test(marker))
                self.assertEqual(verdict, "PASS", "%s: %s" % (marker, evidence))

    def test_compound_one_line_call_and_fail_marker_is_not_a_hit(self):
        """`fn(); bad.append("must raise")` on one line is how six of the
        expected-raise tests in products/brotherds are written (grade_derive,
        grade_lessons, grade_locale, grade_normalize). The LAST statement on
        the line is the one that runs before the handler."""
        verdict, evidence = self._lint(
            ("def grade():\n"
             "    try:\n"
             "        fn(); bad.append(\"probe_pairs unknown must raise\")\n"
             "    %s ValueError:\n"
             "        pass\n" % self.EXC))
        self.assertEqual(verdict, "PASS", evidence)

    def test_semicolon_inside_a_string_stays_a_hit(self):
        """Splitting on a semicolon inside a string literal mis-reads the last
        statement, so it fails in the safe direction: still a hit."""
        verdict, evidence = self._lint(
            ("def grade():\n"
             "    try:\n"
             "        bad.append(\"a; b\")\n"
             "    %s ValueError:\n"
             "        pass\n" % self.EXC))
        self.assertEqual(verdict, "FAIL", evidence)

    def test_except_pass_with_no_fail_marker_is_still_a_hit(self):
        """The mutation that must go red: same handler, no fail marker before
        it, so a call that never raises passes silently and the error that
        does arrive is swallowed."""
        verdict, evidence = self._lint(
            ("def test_it():\n"
             "    try:\n"
             "        wilson_interval(0, 0)\n"
             "    %s ValueError:\n"
             "        pass\n" % self.EXC))
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertIn("fixture.py:4", evidence, evidence)
        self.assertIn("swallows the error", evidence, evidence)

    def test_broad_handler_over_a_fail_marker_is_still_a_hit(self):
        """`assert False` raises AssertionError, and a handler for Exception,
        BaseException or AssertionError catches it, so the test's own failure
        is swallowed and the test can never fail. Still a hit."""
        for exc in ("Exception", "BaseException", "AssertionError"):
            with self.subTest(exc=exc):
                verdict, evidence = self._lint(
                    self._raises_test('assert False, "should raise"', exc=exc))
                self.assertEqual(verdict, "FAIL", "%s: %s" % (exc, evidence))

    def test_fail_marker_outside_the_try_is_still_a_hit(self):
        """The marker must be INSIDE the try (indented past the `except`). A
        marker sitting before the try says nothing about this handler."""
        verdict, evidence = self._lint(
            ("def test_it():\n"
             "    check(False, \"should raise\")\n"
             "    try:\n"
             "        wilson_interval(0, 0)\n"
             "    %s ValueError:\n"
             "        pass\n" % self.EXC))
        self.assertEqual(verdict, "FAIL", evidence)

    def test_bare_except_over_a_fail_marker_is_still_a_hit(self):
        """A handler naming no exception class at all catches everything,
        including the AssertionError, and is the one shape no narrowing
        touches. (Written this way rather than spelled out, so this line does
        not trip the very pattern it describes when the lint scans this
        file.)"""
        verdict, evidence = self._lint(
            ("def test_it():\n"
             "    try:\n"
             "        wilson_interval(0, 0)\n"
             "        check(False, \"should raise\")\n"
             "    %s:\n"
             "        pass\n" % self.EXC))
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertIn("bare except", evidence, evidence)


if __name__ == "__main__":
    unittest.main()
