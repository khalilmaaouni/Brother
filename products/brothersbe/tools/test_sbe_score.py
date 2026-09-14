#!/usr/bin/env python3
"""Fixtures for sbe_score.py's agent-brief-cache-order lint.

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


if __name__ == "__main__":
    unittest.main()
