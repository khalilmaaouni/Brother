"""What intake_measure.py must get right.

Intake V2's closing measure against docs/plan/DECISION-APPROACH... the plan's
three targets: first-request tokens under 30,000, turns to an accepted plan
(3 developer / 6 analyst), and skill-stack bytes at or under half of the
2026-09-06 baseline (docs/plan/intake-benchmark-2026-09-06/). scripts/intake_cost.py,
the tool that produced that baseline, was cut with PR 485; this suite proves
the replacement measures BYTES the same way (so the numbers stay comparable)
and adds the two turn/token figures the cut tool never covered.

Fixtures live under scripts/fixtures/intake_measure/ and are entirely
synthetic (a 100-byte fake SKILL.md, three tiny JSONL transcripts): nothing
here is a copy of a real Claude Code transcript.
"""
import io
import contextlib
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intake_measure as M  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "intake_measure")
BASELINE_PASS = os.path.join(FIXTURES, "baseline_pass.json")
BASELINE_FAIL = os.path.join(FIXTURES, "baseline_fail.json")
TRANSCRIPT_DEV_PASS = os.path.join(FIXTURES, "transcript_dev_pass.jsonl")
TRANSCRIPT_ANALYST_FAIL = os.path.join(FIXTURES, "transcript_analyst_fail.jsonl")
TRANSCRIPT_NO_USAGE = os.path.join(FIXTURES, "transcript_no_usage.jsonl")


def _write_fixture_file(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def _skill_md_path(root, plugin_name, skill_name, version="1.0.0"):
    return os.path.join(root, "plugins", "cache", "brother", plugin_name, version,
                         "skills", skill_name, "SKILL.md")


def _build_fake_claude_dir(root, full):
    """Write the same synthetic skill-stack layout the old committed fixture
    trees held, at test time instead of on disk: a fake claude-dir under
    plugins/cache/brother/<plugin>/<version>/skills/<skill>/SKILL.md.

    full=False: exactly one of the five baseline ENTRY_POINTS present
    (brother/using-brother), 100 bytes, a PARTIAL stack.
    full=True: all five present at 20 bytes each (100 total, matching the
    partial total so the PASS/FAIL fixtures below stay unchanged).
    """
    if full:
        entries = [
            ("brother", "using-brother"),
            ("brothermode", "brotherme"),
            ("brothermode", "start"),
            ("brothersbe", "kickoff"),
            ("brothersbe", "start"),
        ]
        for plugin_name, skill_name in entries:
            _write_fixture_file(_skill_md_path(root, plugin_name, skill_name), b"a" * 20)
    else:
        _write_fixture_file(_skill_md_path(root, "brother", "using-brother"), b"a" * 100)


def _tree_skill_md_path(root, plugin_name, skill_name):
    product_root = {"brother": "bundle", "brothermode": "products/brothermode",
                     "brothersbe": "products/brothersbe"}[plugin_name]
    return os.path.join(root, product_root, "skills", skill_name, "SKILL.md")


def _build_fake_tree_root(root, full, drop_brothersbe=False):
    """Write the same synthetic five-entry-point layout as
    _build_fake_claude_dir, but at the repository TREE paths --tree
    resolves against: ROOT/bundle/skills/<name> for brother, and
    ROOT/products/<plugin>/skills/<name> for brothermode and brothersbe.

    full=False: only brother/using-brother present (100 bytes), a PARTIAL
    stack, same shape as _build_fake_claude_dir(full=False).
    full=True: all five present at 20 bytes each (100 total).
    drop_brothersbe=True (only meaningful with full=True): the two
    brothersbe entries are omitted entirely, so products/brothersbe never
    exists under this root, proving a missing product directory reads as
    NO-DATA for just its own entries rather than the whole stack.
    """
    if full:
        entries = [
            ("brother", "using-brother"),
            ("brothermode", "brotherme"),
            ("brothermode", "start"),
        ]
        if not drop_brothersbe:
            entries += [("brothersbe", "kickoff"), ("brothersbe", "start")]
        for plugin_name, skill_name in entries:
            _write_fixture_file(_tree_skill_md_path(root, plugin_name, skill_name), b"a" * 20)
    else:
        _write_fixture_file(_tree_skill_md_path(root, "brother", "using-brother"), b"a" * 100)


FAKE_CLAUDE_DIR = None
FAKE_CLAUDE_DIR_FULL = None
FAKE_TREE_ROOT_FULL = None
FAKE_TREE_ROOT_NO_SBE = None
_FAKE_CLAUDE_ROOTS = []


def setUpModule():
    global FAKE_CLAUDE_DIR, FAKE_CLAUDE_DIR_FULL, FAKE_TREE_ROOT_FULL, FAKE_TREE_ROOT_NO_SBE
    FAKE_CLAUDE_DIR = tempfile.mkdtemp(prefix="intake_measure_fake_")
    FAKE_CLAUDE_DIR_FULL = tempfile.mkdtemp(prefix="intake_measure_fake_full_")
    FAKE_TREE_ROOT_FULL = tempfile.mkdtemp(prefix="intake_measure_tree_full_")
    FAKE_TREE_ROOT_NO_SBE = tempfile.mkdtemp(prefix="intake_measure_tree_no_sbe_")
    _FAKE_CLAUDE_ROOTS.extend([FAKE_CLAUDE_DIR, FAKE_CLAUDE_DIR_FULL,
                                FAKE_TREE_ROOT_FULL, FAKE_TREE_ROOT_NO_SBE])
    _build_fake_claude_dir(FAKE_CLAUDE_DIR, full=False)
    _build_fake_claude_dir(FAKE_CLAUDE_DIR_FULL, full=True)
    _build_fake_tree_root(FAKE_TREE_ROOT_FULL, full=True)
    _build_fake_tree_root(FAKE_TREE_ROOT_NO_SBE, full=True, drop_brothersbe=True)


def tearDownModule():
    for root in _FAKE_CLAUDE_ROOTS:
        shutil.rmtree(root, ignore_errors=True)


def run_main(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = M.main(argv)
    return code, out.getvalue()


class BytesAgainstBaseline(unittest.TestCase):
    """Same method as the cut intake_cost.py: sum each entry point's
    SKILL.md (+ references) bytes. FAKE_CLAUDE_DIR has exactly one of the
    five ENTRY_POINTS present (brother/using-brother, 100 bytes) and is a
    PARTIAL stack: since the security review of 0e831da6, a partial stack
    must never compute a fraction over the shrunk population, so it is
    NO-DATA, never a PASS or FAIL. FAKE_CLAUDE_DIR_FULL has all five present
    at 20 bytes each (100 total, matching the old partial total so the
    PASS/FAIL fixtures below stay unchanged) and is the only shape that
    reaches the fraction."""

    def test_measured_bytes_is_100_for_the_fixture_stack(self):
        total, rows = M.measure_intake_bytes(FAKE_CLAUDE_DIR)
        self.assertEqual(total, 100)
        present = [r for r in rows if r["key"] == "entry:brother/using-brother"]
        self.assertEqual(len(present), 1)
        self.assertEqual(present[0]["bytes"], 100)

    def test_partial_stack_is_nodata_never_pass_or_fail(self):
        """Exactly one of the five baseline entry points present, four
        missing: 100 bytes measured is comfortably under half of
        BASELINE_PASS's 1000, so a naive fraction would print PASS. It must
        print NO-DATA instead and name the missing count."""
        status, line = M.evaluate_bytes(FAKE_CLAUDE_DIR, BASELINE_PASS)
        self.assertEqual(status, "NO-DATA")
        self.assertIn("BYTES: NO-DATA", line)
        self.assertIn("4 of 5", line)
        self.assertNotIn("PASS", line)
        self.assertNotIn("FAIL", line)

    def test_pass_at_or_under_half_of_baseline(self):
        status, line = M.evaluate_bytes(FAKE_CLAUDE_DIR_FULL, BASELINE_PASS)
        self.assertEqual(status, "PASS")
        self.assertIn("100", line)
        self.assertIn("BYTES", line)

    def test_fail_over_half_of_baseline(self):
        status, line = M.evaluate_bytes(FAKE_CLAUDE_DIR_FULL, BASELINE_FAIL)
        self.assertEqual(status, "FAIL")
        self.assertIn("BYTES", line)

    def test_missing_baseline_file_is_nodata_naming_the_path(self):
        missing = os.path.join(FIXTURES, "does-not-exist.json")
        status, line = M.evaluate_bytes(FAKE_CLAUDE_DIR, missing)
        self.assertEqual(status, "NO-DATA")
        self.assertIn(missing, line)

    def test_empty_claude_dir_is_nodata_not_a_pass_at_zero_bytes(self):
        """A population of all-NO-DATA entry-point rows must never compose
        into a PASS: an empty --claude-dir has every entry point missing,
        current_total is 0, and 0 <= half the baseline is true by
        arithmetic alone. evaluate_bytes must catch that shape and report
        NO-DATA, naming how many rows were NO-DATA, before comparing."""
        empty_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty_dir, ignore_errors=True)
        status, line = M.evaluate_bytes(empty_dir, BASELINE_PASS)
        self.assertEqual(status, "NO-DATA")
        self.assertIn("NO-DATA", line)
        self.assertIn("entry point", line)

    def test_empty_claude_dir_exits_2_through_main_not_0(self):
        empty_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty_dir, ignore_errors=True)
        code, out = run_main([
            "--baseline", BASELINE_PASS,
            "--claude-dir", empty_dir,
            "--transcript", TRANSCRIPT_DEV_PASS,
            "--persona", "developer",
        ])
        self.assertEqual(code, 2)
        self.assertIn("BYTES: NO-DATA", out)


class BytesFromTree(unittest.TestCase):
    """--tree ROOT resolves the same five entry points against a repository
    checkout instead of the installed plugin cache: brother/using-brother
    under ROOT/bundle/skills/using-brother, brothermode/brothersbe entries
    under ROOT/products/<plugin>/skills/<name>. Same summing method, same
    PASS/FAIL/NO-DATA rules, only the source of the bytes moves."""

    def test_tree_total_matches_the_fixture_stack(self):
        total, rows = M.measure_intake_bytes(FAKE_TREE_ROOT_FULL, tree_root=FAKE_TREE_ROOT_FULL)
        self.assertEqual(total, 100)
        present = [r for r in rows if r["key"] == "entry:brothersbe/kickoff"]
        self.assertEqual(len(present), 1)
        self.assertEqual(present[0]["bytes"], 20)
        self.assertEqual(present[0]["version"], "tree")

    def test_tree_pass_carries_a_tree_label_naming_the_root(self):
        status, line = M.evaluate_bytes(None, BASELINE_PASS, tree_root=FAKE_TREE_ROOT_FULL)
        self.assertEqual(status, "PASS")
        self.assertIn("BYTES (tree %s)" % FAKE_TREE_ROOT_FULL, line)

    def test_tree_fail_over_half_of_baseline(self):
        status, line = M.evaluate_bytes(None, BASELINE_FAIL, tree_root=FAKE_TREE_ROOT_FULL)
        self.assertEqual(status, "FAIL")
        self.assertIn("BYTES (tree %s)" % FAKE_TREE_ROOT_FULL, line)

    def test_tree_missing_a_product_directory_is_nodata_for_just_its_entries(self):
        """products/brothersbe never exists under this root: its two
        entries (kickoff, start) must be the NO-DATA rows named, while
        brother and brothermode resolved fine."""
        status, line = M.evaluate_bytes(None, BASELINE_PASS, tree_root=FAKE_TREE_ROOT_NO_SBE)
        self.assertEqual(status, "NO-DATA")
        self.assertIn("BYTES: NO-DATA", line)
        self.assertIn("2 of 5", line)
        self.assertIn("entry:brothersbe/kickoff", line)
        self.assertIn("entry:brothersbe/start", line)

    def test_cache_mode_is_untouched_when_tree_root_is_none(self):
        """Passing tree_root=None (the default) must reproduce the exact
        cache-mode behaviour proven above, unlabeled."""
        status, line = M.evaluate_bytes(FAKE_CLAUDE_DIR_FULL, BASELINE_PASS)
        self.assertEqual(status, "PASS")
        self.assertIn("BYTES (installed", line)

    def test_cli_tree_flag_reaches_evaluate_bytes(self):
        code, out = run_main([
            "--baseline", BASELINE_PASS,
            "--tree", FAKE_TREE_ROOT_FULL,
            "--transcript", TRANSCRIPT_DEV_PASS,
            "--persona", "developer",
        ])
        self.assertEqual(code, 0, out)
        self.assertIn("BYTES (tree %s): PASS" % FAKE_TREE_ROOT_FULL, out)


class TurnsToAcceptance(unittest.TestCase):
    """A human turn is a transcript line with type user and
    origin.kind == human (verified against a real transcript: tool-result
    and slash-command-expansion 'user' lines carry no such origin). The
    record reaches its accepted state at the first assistant message whose
    text contains the literal marker M.ACCEPTANCE_MARKER. TURNS counts human
    turns seen up to and including that point."""

    def test_developer_three_turns_passes(self):
        turns = M.count_turns_to_acceptance(TRANSCRIPT_DEV_PASS)
        self.assertEqual(turns, 3)
        status, line = M.evaluate_turns(TRANSCRIPT_DEV_PASS, "developer")
        self.assertEqual(status, "PASS")
        self.assertIn("3", line)

    def test_analyst_seven_turns_fails_the_six_turn_bar(self):
        turns = M.count_turns_to_acceptance(TRANSCRIPT_ANALYST_FAIL)
        self.assertEqual(turns, 7)
        status, line = M.evaluate_turns(TRANSCRIPT_ANALYST_FAIL, "analyst")
        self.assertEqual(status, "FAIL")

    def test_same_transcript_passes_as_developer_bar_of_three_is_not_it(self):
        # 7 turns fails BOTH bars; use the 3-turn transcript to prove the
        # analyst bar (6) is looser than the developer bar (3).
        status, line = M.evaluate_turns(TRANSCRIPT_DEV_PASS, "analyst")
        self.assertEqual(status, "PASS")

    def test_no_transcript_is_nodata(self):
        status, line = M.evaluate_turns(None, "developer")
        self.assertEqual(status, "NO-DATA")

    def test_no_persona_is_nodata_even_with_a_transcript(self):
        status, line = M.evaluate_turns(TRANSCRIPT_DEV_PASS, None)
        self.assertEqual(status, "NO-DATA")


class TokensFromFirstUsage(unittest.TestCase):
    def test_first_usage_input_tokens_under_30000_passes(self):
        tokens = M.first_usage_input_tokens(TRANSCRIPT_DEV_PASS)
        self.assertEqual(tokens, 1200)
        status, line = M.evaluate_tokens(TRANSCRIPT_DEV_PASS)
        self.assertEqual(status, "PASS")
        self.assertIn("1200", line)

    def test_no_transcript_is_nodata(self):
        status, line = M.evaluate_tokens(None)
        self.assertEqual(status, "NO-DATA")

    def test_transcript_without_usage_is_nodata_naming_why(self):
        tokens = M.first_usage_input_tokens(TRANSCRIPT_NO_USAGE)
        self.assertIsNone(tokens)
        status, line = M.evaluate_tokens(TRANSCRIPT_NO_USAGE)
        self.assertEqual(status, "NO-DATA")
        self.assertIn("usage", line.lower())


class ReadWhenClassification(unittest.TestCase):
    """docs/decisions/intake-byte-floor-2026-09-09.json option A: teach
    _read_stack_files (shared by _skill_stack and _skill_stack_tree) to
    tell an unconditional citation of a references/... file from one
    written behind the convention two other lanes are moving branch-only
    text behind: a line starting 'Read when <condition>: references/...'.
    A cited-both-ways file counts as unconditional; a 'Read when' line with
    no references/ token adds nothing."""

    def test_plain_and_read_when_citations_split_into_two_buckets(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        skill_dir = os.path.join(root, "skills", "demo")
        _write_fixture_file(os.path.join(skill_dir, "references", "plain.md"), b"p" * 30)
        _write_fixture_file(os.path.join(skill_dir, "references", "whenonly.md"), b"w" * 40)
        skill_md = os.path.join(skill_dir, "SKILL.md")
        _write_fixture_file(skill_md, (
            "See references/plain.md for the base flow.\n"
            "Read when debugging a stall: references/whenonly.md\n"
        ).encode("utf-8"))

        used, read_when = M._read_stack_files(skill_md, skill_dir, root)

        used_names = {os.path.basename(p) for p in used}
        read_when_names = {os.path.basename(p) for p in read_when}
        self.assertEqual(used_names, {"SKILL.md", "plain.md"})
        self.assertEqual(read_when_names, {"whenonly.md"})
        self.assertEqual(sum(read_when.values()), 40)

    def test_reference_cited_both_ways_counts_as_unconditional(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        skill_dir = os.path.join(root, "skills", "demo2")
        _write_fixture_file(os.path.join(skill_dir, "references", "both.md"), b"b" * 10)
        skill_md = os.path.join(skill_dir, "SKILL.md")
        _write_fixture_file(skill_md, (
            "See references/both.md always.\n"
            "Read when rare: references/both.md\n"
        ).encode("utf-8"))

        used, read_when = M._read_stack_files(skill_md, skill_dir, root)

        both_path = os.path.abspath(os.path.join(skill_dir, "references", "both.md"))
        self.assertIn(both_path, used)
        self.assertEqual(read_when, {})

    def test_read_when_line_with_no_reference_token_adds_nothing(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        skill_dir = os.path.join(root, "skills", "demo3")
        os.makedirs(skill_dir, exist_ok=True)
        skill_md = os.path.join(skill_dir, "SKILL.md")
        _write_fixture_file(skill_md, b"Read when tired: take a break.\n")

        used, read_when = M._read_stack_files(skill_md, skill_dir, root)

        self.assertEqual(sorted(used.keys()), [os.path.abspath(skill_md)])
        self.assertEqual(read_when, {})

    def test_existing_full_stack_fixture_has_no_read_when_bytes(self):
        """The old fixtures carry no 'Read when' lines; their figures must
        be unchanged by this feature."""
        total, rows = M.measure_intake_bytes(FAKE_CLAUDE_DIR_FULL)
        self.assertEqual(total, 100)
        read_when_total = sum(r.get("read_when_bytes", 0) for r in rows)
        self.assertEqual(read_when_total, 0)

    def test_bytes_line_names_the_read_when_total(self):
        status, line = M.evaluate_bytes(FAKE_CLAUDE_DIR_FULL, BASELINE_PASS)
        self.assertEqual(status, "PASS")
        self.assertIn("unconditional", line)
        self.assertIn("read-when 0 bytes not counted", line)


class CliExitCodes(unittest.TestCase):
    """main()'s own return value, not the printed text, is the verdict a
    battery run reads (repeat-guard lesson: assert the exit code)."""

    def test_all_pass_exits_0(self):
        code, out = run_main([
            "--claude-dir", FAKE_CLAUDE_DIR_FULL,
            "--baseline", BASELINE_PASS,
            "--transcript", TRANSCRIPT_DEV_PASS,
            "--persona", "developer",
        ])
        self.assertEqual(code, 0, out)
        self.assertIn("BYTES", out)
        self.assertIn("TURNS", out)
        self.assertIn("TOKENS", out)
        self.assertIn("PASS", out)
        self.assertNotIn("FAIL", out)

    def test_any_fail_exits_1(self):
        code, out = run_main([
            "--claude-dir", FAKE_CLAUDE_DIR_FULL,
            "--baseline", BASELINE_FAIL,
            "--transcript", TRANSCRIPT_DEV_PASS,
            "--persona", "developer",
        ])
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL", out)

    def test_analyst_turns_fail_exits_1(self):
        code, out = run_main([
            "--claude-dir", FAKE_CLAUDE_DIR_FULL,
            "--baseline", BASELINE_PASS,
            "--transcript", TRANSCRIPT_ANALYST_FAIL,
            "--persona", "analyst",
        ])
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL", out)

    def test_no_transcript_gives_two_nodata_and_exits_2(self):
        code, out = run_main([
            "--claude-dir", FAKE_CLAUDE_DIR_FULL,
            "--baseline", BASELINE_PASS,
        ])
        self.assertEqual(code, 2, out)
        self.assertEqual(out.count("NO-DATA"), 2)
        self.assertNotIn("FAIL", out)

    def test_missing_baseline_exits_2_with_named_path(self):
        missing = os.path.join(FIXTURES, "does-not-exist.json")
        code, out = run_main(["--claude-dir", FAKE_CLAUDE_DIR, "--baseline", missing])
        self.assertEqual(code, 2, out)
        self.assertIn(missing, out)

    def test_json_mode_is_valid_json_with_the_three_keys(self):
        import json
        code, out = run_main([
            "--claude-dir", FAKE_CLAUDE_DIR_FULL,
            "--baseline", BASELINE_PASS,
            "--transcript", TRANSCRIPT_DEV_PASS,
            "--persona", "developer",
            "--json",
        ])
        self.assertEqual(code, 0, out)
        payload = json.loads(out)
        self.assertIn("bytes", payload)
        self.assertIn("turns", payload)
        self.assertIn("tokens", payload)


if __name__ == "__main__":
    unittest.main()
