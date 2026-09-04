"""Calibration for scripts/intake_score.py: proves the scorer FAILS as well
as passes, per this estate's rule that a check that cannot fail verifies
nothing. Fixtures are built inline as temp files, no external fixture dir.
"""
import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, 'scripts')
SCORER = os.path.join(SCRIPTS_DIR, 'intake_score.py')

sys.path.insert(0, SCRIPTS_DIR)
import intake_score  # noqa: E402  (import after sys.path edit, by necessity)

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


def by_name(results):
    return {r.name: r for r in results}


def write_record(text):
    fh = tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False,
                                     encoding='utf-8', dir=SCRIPTS_DIR)
    try:
        fh.write(text)
    finally:
        fh.close()
    return fh.name


def run_cli(path, persona, extra_args=None):
    args = [sys.executable, SCORER, path, '--persona', persona]
    if extra_args:
        args.extend(extra_args)
    proc = subprocess.run(args, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


ALL_CITED_RECORD = (
    "# Intake Record\n"
    "\n"
    "## Assumptions\n"
    "- The invoice poster lives at `scripts/invoice_poster.py`, UNGROUNDED otherwise.\n"
    "- Retries are idempotent per docs/plan/INTAKE-9.5-DESIGN.md.\n"
    "\n"
    "## Options\n"
    "1. Add an idempotency key column. Cost: one migration. Recommended: this option.\n"
    "2. Wrap the poster in a lock. Cost: throughput.\n"
    "\n"
    "| Criterion | Weight | Option 1: key column | Option 2: lock |\n"
    "|---|---|---|---|\n"
    "| Cost | 40 | 8 | 5 |\n"
    "| Throughput impact | 30 | 7 | 3 |\n"
    "| Simplicity | 30 | 6 | 8 |\n"
    "\n"
    "## Plan\n"
    "Do the migration first, then the poster change.\n"
    "\n"
    "```mermaid\n"
    "graph TD; A-->B;\n"
    "```\n"
    "\n"
    "## Dev View\n"
    "Edit `scripts/invoice_poster.py` and run the check.\n"
    "\n"
    "## BA View\n"
    "Customers will stop seeing duplicate invoices once this ships.\n"
    "\n"
    "## Approval\n"
    "Do you approve this plan?\n"
)

UNCITED_RECORD = ALL_CITED_RECORD.replace(
    "- Retries are idempotent per docs/plan/INTAKE-9.5-DESIGN.md.\n",
    "- Retries are idempotent, trust me.\n",
)

NO_MERMAID_RECORD = ALL_CITED_RECORD.replace(
    "```mermaid\ngraph TD; A-->B;\n```\n\n",
    "",
)

TWO_MERMAID_RECORD = ALL_CITED_RECORD.replace(
    "```mermaid\ngraph TD; A-->B;\n```\n\n",
    "```mermaid\ngraph TD; A-->B;\n```\n\n```mermaid\ngraph TD; C-->D;\n```\n\n",
)

# Approval heading moved ahead of the Plan heading: the sequencing hard 0.
OUT_OF_ORDER_RECORD = (
    "# Intake Record\n"
    "\n"
    "## Assumptions\n"
    "- The invoice poster lives at `scripts/invoice_poster.py`.\n"
    "\n"
    "## Approval\n"
    "Do you approve this plan?\n"
    "\n"
    "## Plan\n"
    "Do the migration first, then the poster change.\n"
)

BA_LEAK_RECORD = ALL_CITED_RECORD.replace(
    "## BA View\nCustomers will stop seeing duplicate invoices once this ships.\n",
    "## BA View\nEdit `scripts/invoice_poster.py` to fix this.\n",
)

NO_RECOMMENDATION_RECORD = ALL_CITED_RECORD.replace(
    "1. Add an idempotency key column. Cost: one migration. Recommended: this option.\n",
    "1. Add an idempotency key column. Cost: one migration.\n",
)

# The Options section with the weight table stripped back out: the numbered
# pros-and-cons list ALL_CITED_RECORD carried before this criterion existed.
# This is the shape the six shipped docs/plan/examples/ records are actually
# in today, per the row's own instruction not to edit them to pass.
UNWEIGHTED_OPTIONS_RECORD = ALL_CITED_RECORD.replace(
    "\n"
    "| Criterion | Weight | Option 1: key column | Option 2: lock |\n"
    "|---|---|---|---|\n"
    "| Cost | 40 | 8 | 5 |\n"
    "| Throughput impact | 30 | 7 | 3 |\n"
    "| Simplicity | 30 | 6 | 8 |\n",
    "",
)

# Weights mentioned in prose, no table: present but not checkable arithmetic.
PROSE_WEIGHTED_OPTIONS_RECORD = ALL_CITED_RECORD.replace(
    "\n"
    "| Criterion | Weight | Option 1: key column | Option 2: lock |\n"
    "|---|---|---|---|\n"
    "| Cost | 40 | 8 | 5 |\n"
    "| Throughput impact | 30 | 7 | 3 |\n"
    "| Simplicity | 30 | 6 | 8 |\n",
    "\n"
    "Cost is weighted more heavily than throughput in this comparison.\n",
)

# A table with a Weight column and option columns, but the option cells are
# words, not numbers: weights present, arithmetic not fully checkable.
NON_NUMERIC_WEIGHTED_OPTIONS_RECORD = ALL_CITED_RECORD.replace(
    "| Cost | 40 | 8 | 5 |\n"
    "| Throughput impact | 30 | 7 | 3 |\n"
    "| Simplicity | 30 | 6 | 8 |\n",
    "| Cost | 40 | high | medium |\n"
    "| Throughput impact | 30 | high | low |\n"
    "| Simplicity | 30 | medium | high |\n",
)

# A table with no column headed Weight at all: not machine-readable as weighted.
NO_WEIGHT_COLUMN_RECORD = ALL_CITED_RECORD.replace(
    "| Criterion | Weight | Option 1: key column | Option 2: lock |\n",
    "| Criterion | Score | Option 1: key column | Option 2: lock |\n",
)


class GroundedAssumptionsTests(unittest.TestCase):
    def test_uncited_assumption_scores_lower_than_all_cited(self):
        cited = by_name(intake_score.score_record(ALL_CITED_RECORD, 'dev', root=REPO_ROOT))['grounded_assumptions']
        uncited = by_name(intake_score.score_record(UNCITED_RECORD, 'dev', root=REPO_ROOT))['grounded_assumptions']
        self.assertIsNotNone(cited.score)
        self.assertIsNotNone(uncited.score)
        self.assertEqual(cited.score, 10.0)
        self.assertLess(uncited.score, cited.score)
        self.assertIn('1', uncited.evidence)  # one uncited line reported


class CitationExistenceTests(unittest.TestCase):
    """Check 1: a backticked path in a citation is only a real citation when
    it resolves to an actual file under the repository root. Before this
    fix has_citation() took no root at all and a nonexistent backticked
    path scored exactly like a real one."""

    def test_a_backticked_path_that_exists_is_a_citation(self):
        line = "- Assumption: the scorer lives at `scripts/intake_score.py`."
        self.assertTrue(intake_score.has_citation(line, REPO_ROOT))

    def test_a_backticked_path_that_does_not_exist_is_not_a_citation(self):
        line = "- Assumption: the scorer lives at `scripts/does_not_exist_xyz.py`."
        self.assertFalse(intake_score.has_citation(line, REPO_ROOT))

    def test_ungrounded_still_counts_with_no_existence_check_needed(self):
        line = "- Assumption: nothing can go wrong. UNGROUNDED"
        self.assertTrue(intake_score.has_citation(line, REPO_ROOT))

    def test_grounded_assumptions_scores_low_when_the_only_backtick_path_is_missing(self):
        text = "# Plan\n\n- Assumption: the poster is idempotent, per `scripts/does_not_exist_xyz.py`.\n"
        result = by_name(intake_score.score_record(text, 'dev', root=REPO_ROOT))['grounded_assumptions']
        self.assertIsNotNone(result.score)
        self.assertLess(result.score, 7.0)

    def test_grounded_assumptions_scores_full_when_the_backtick_path_exists(self):
        text = "# Plan\n\n- Assumption: the poster is idempotent, per `scripts/intake_score.py`.\n"
        result = by_name(intake_score.score_record(text, 'dev', root=REPO_ROOT))['grounded_assumptions']
        self.assertEqual(result.score, 10.0)

    def test_no_resolvable_root_is_no_data_not_a_score(self):
        text = "# Plan\n\n- Assumption: the poster is idempotent, per `scripts/intake_score.py`.\n"
        result = by_name(intake_score.score_record(text, 'dev', root=None))['grounded_assumptions']
        self.assertIsNone(result.score, "no root resolvable must be NO-DATA, never a guessed score")

    def test_find_repo_root_finds_the_nearest_git_ancestor(self):
        self.assertEqual(intake_score.find_repo_root(SCRIPTS_DIR), REPO_ROOT)

    def test_find_repo_root_returns_none_outside_any_repo(self):
        outside = tempfile.mkdtemp()
        try:
            self.assertIsNone(intake_score.find_repo_root(outside))
        finally:
            os.rmdir(outside)

    def test_cli_root_flag_makes_a_missing_path_uncited(self):
        path = write_record(
            "# Plan\n\n- Assumption: the poster is idempotent, per `scripts/does_not_exist_xyz.py`.\n"
            "\n## Options\n1. do it\nRecommendation: do it.\n\n```mermaid\nflowchart LR\n  A --> B\n```\n")
        try:
            proc = subprocess.run(
                [sys.executable, SCORER, path, '--persona', 'dev', '--root', REPO_ROOT],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        finally:
            os.unlink(path)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('uncited', proc.stdout)


class DiagramTests(unittest.TestCase):
    def test_no_mermaid_block_scores_zero(self):
        result = by_name(intake_score.score_record(NO_MERMAID_RECORD, 'dev'))['diagrams_by_default']
        self.assertEqual(result.score, 0.0)

    def test_two_mermaid_blocks_are_capped_below_one_block(self):
        one = by_name(intake_score.score_record(ALL_CITED_RECORD, 'dev'))['diagrams_by_default']
        two = by_name(intake_score.score_record(TWO_MERMAID_RECORD, 'dev'))['diagrams_by_default']
        self.assertEqual(one.score, 10.0)
        self.assertLess(two.score, one.score)

    def test_a_four_space_indented_fence_is_not_a_diagram(self):
        """AUDIT FINDING. CommonMark/GFM treats a fence indented 4+ spaces
        (outside a list item) as an INDENTED CODE BLOCK, rendered as literal
        text, not a diagram. The prior `^\\s*` accepted any indent, so this
        scored as a valid diagram while GitHub rendered zero."""
        text = "# Plan\n\n    ```mermaid\n    graph TD; A-->B;\n    ```\n"
        self.assertEqual(intake_score.count_mermaid_blocks(text), 0)

    def test_a_three_space_indented_fence_still_counts(self):
        """Calibration in the other direction: 3 spaces is CommonMark's own
        cap for a top-level fence, and still a real one."""
        text = "# Plan\n\n   ```mermaid\n   graph TD; A-->B;\n   ```\n"
        self.assertEqual(intake_score.count_mermaid_blocks(text), 1)

    def test_info_string_suffix_after_mermaid_still_counts(self):
        """GFM: the info string's first word names the language; trailing
        content after it (```mermaid TB) is extra metadata, not a different
        language, and still opens a mermaid fence."""
        text = "# Plan\n\n```mermaid TB\ngraph TD; A-->B;\n```\n"
        self.assertEqual(intake_score.count_mermaid_blocks(text), 1)

    def test_mermaid_as_a_prefix_of_a_longer_word_does_not_count(self):
        """The info-string fix must not regress into substring matching:
        ```mermaidTB is one word, not the language mermaid plus a suffix."""
        text = "# Plan\n\n```mermaidTB\ngraph TD; A-->B;\n```\n"
        self.assertEqual(intake_score.count_mermaid_blocks(text), 0)

    def test_closing_fence_with_more_backticks_than_opener_counts(self):
        """CommonMark: the closing fence must carry at least as many
        backticks as the opener; more is valid."""
        text = "# Plan\n\n```mermaid\ngraph TD; A-->B;\n````\n"
        self.assertEqual(intake_score.count_mermaid_blocks(text), 1)

    def test_closing_fence_with_fewer_backticks_than_opener_does_not_close(self):
        """The inverse: fewer backticks than the opener is not a valid
        closer, so the fence never closes and is not counted."""
        text = "````mermaid\ngraph TD; A-->B;\n```\n"
        self.assertEqual(intake_score.count_mermaid_blocks(text), 0)


class Check3MermaidFenceParsing(unittest.TestCase):
    """CHECK 3: an unterminated mermaid fence must never count as a
    diagram. Investigated with a battery of adversarial fixtures (a real
    closed fence followed by an unterminated one, an unterminated fence
    that would otherwise swallow a later real one, EOF with no trailing
    newline, a 2-backtick fake closer) before writing this class: every one
    of them ALREADY passes against the current count_mermaid_blocks(), so
    this class is a regression lock, not a fix -- the prior adversarial
    audits recorded in count_mermaid_blocks()'s own comments already closed
    this exact gap. Kept here so a future edit that reopens it is caught."""

    def test_a_real_closed_fence_followed_by_an_unterminated_one_counts_once(self):
        text = "```mermaid\nA-->B\n```\n\n```mermaid\nC-->D\n"
        self.assertEqual(intake_score.count_mermaid_blocks(text), 1)

    def test_an_unterminated_fence_with_real_looking_content_scores_zero(self):
        """The unterminated fence carries genuine flowchart syntax, not a
        trivial one-liner, so a substring-only check would be fooled."""
        text = ("# Plan\n\n```mermaid\nflowchart LR\n"
                "  A --> B\n  B --> C\n  C --> D\n")
        score, _evidence = intake_score.score_diagrams(text)
        self.assertEqual(score, 0.0)

    def test_no_trailing_newline_at_eof_still_counts_a_real_close(self):
        text = "```mermaid\nA-->B\n```"
        self.assertEqual(intake_score.count_mermaid_blocks(text), 1)

    def test_a_two_backtick_line_is_not_a_valid_closer(self):
        """Fewer than 3 backticks is not a fence marker at all per GFM, so
        it must not terminate an open mermaid fence."""
        text = "```mermaid\nA-->B\n``\nmore text, still open\n"
        self.assertEqual(intake_score.count_mermaid_blocks(text), 0)


class SequencingTests(unittest.TestCase):
    def test_approval_before_plan_is_a_hard_zero(self):
        result = by_name(intake_score.score_record(OUT_OF_ORDER_RECORD, 'dev'))['sequencing']
        self.assertEqual(result.score, 0.0)

    def test_plan_before_approval_scores_full(self):
        result = by_name(intake_score.score_record(ALL_CITED_RECORD, 'dev'))['sequencing']
        self.assertEqual(result.score, 10.0)

    def test_an_ordinary_bullet_mentioning_confirm_is_not_an_approval_prompt(self):
        """CHECK 4: the bare-text fallback (for an approval prompt that is
        not under its own heading) must not fire on ordinary prose that
        happens to contain 'confirm to proceed' as part of a longer
        descriptive sentence about something else entirely -- here, a
        customer checkout flow described in an Assumptions bullet, with no
        real approval prompt or Approval heading anywhere in the record."""
        lines = (
            "# Intake Record\n\n"
            "## Assumptions\n"
            "- Customers must confirm to proceed with checkout before the charge posts.\n\n"
            "## Plan\n"
            "Ship the invoice fix.\n"
        ).splitlines()
        result = intake_score.score_sequencing(lines)
        self.assertIsNone(result[0], "no real approval prompt exists; must be NO-DATA, never a false hard 0")

    def test_a_genuine_bare_prompt_after_the_plan_still_scores_full(self):
        """Calibration in the other direction: a real approval prompt with
        no heading of its own, positioned correctly, must still be found."""
        lines = (
            "# Intake Record\n\n"
            "## Plan\n"
            "Ship the invoice fix.\n\n"
            "Please confirm to proceed.\n"
        ).splitlines()
        result = intake_score.score_sequencing(lines)
        self.assertEqual(result[0], 10.0)

    def test_a_genuine_bare_prompt_before_the_plan_is_still_a_hard_zero(self):
        lines = (
            "# Intake Record\n\n"
            "Please confirm to proceed.\n\n"
            "## Plan\n"
            "Ship the invoice fix.\n"
        ).splitlines()
        result = intake_score.score_sequencing(lines)
        self.assertEqual(result[0], 0.0)


class LevelAdaptationTests(unittest.TestCase):
    def test_ba_record_with_file_path_is_penalized(self):
        clean = by_name(intake_score.score_record(ALL_CITED_RECORD, 'ba'))['level_adaptation']
        leaked = by_name(intake_score.score_record(BA_LEAK_RECORD, 'ba'))['level_adaptation']
        self.assertEqual(clean.score, 10.0)
        self.assertLess(leaked.score, clean.score)


    def test_and_or_is_not_a_leaked_file_path(self):
        """CHECK 4: ordinary English routinely uses a single slash between
        two short words ('and/or', 'him/her', 'w/o'); none of these are a
        leaked file path and must not trip contains_path()."""
        self.assertFalse(intake_score.contains_path("Customers can pay by credit and/or debit."))
        self.assertFalse(intake_score.contains_path("Notify the assignee, him/her, within a day."))
        self.assertFalse(intake_score.contains_path("This works w/o any changes to billing."))

    def test_a_real_path_with_an_extension_still_counts(self):
        """Calibration in the other direction: the fix must not blind the
        check to a genuine leaked path."""
        self.assertTrue(intake_score.contains_path("See docs/plan/INTAKE-9.5-DESIGN.md for details."))
        self.assertTrue(intake_score.contains_path("Edit `scripts/invoice_poster.py` and run the check."))

    def test_a_multi_segment_path_with_no_extension_still_counts(self):
        """Three or more directory segments is a shape ordinary English
        slash idioms never take, so it still counts even with no extension."""
        self.assertTrue(intake_score.contains_path("The config lives under products/brothermode/tools."))

    def test_make_sure_is_not_a_leaked_command(self):
        """CHECK 4: 'make', 'git', 'bash' and 'sh' are common English words
        too. A bare keyword followed by an ordinary English word is not a
        command invocation."""
        self.assertFalse(intake_score.contains_command(
            "We will make sure customers are notified before the charge posts."))
        self.assertFalse(intake_score.contains_command(
            "The plan is to bash out a quick fix this afternoon."))
        self.assertFalse(intake_score.contains_command(
            "The team will git this done by Friday."))

    def test_a_real_command_with_a_flag_or_backtick_still_counts(self):
        """Calibration in the other direction: real command syntax (a
        flag, or backticked code) must still be caught."""
        self.assertTrue(intake_score.contains_command("Run npm install --production before deploy."))
        self.assertTrue(intake_score.contains_command("Run `git commit -m 'fix'` before merging."))
        self.assertTrue(intake_score.contains_command("$ git push origin main"))


class OptionsTests(unittest.TestCase):
    def test_options_without_recommendation_scores_partial(self):
        full = by_name(intake_score.score_record(ALL_CITED_RECORD, 'dev'))['options_with_recommendation']
        partial = by_name(intake_score.score_record(NO_RECOMMENDATION_RECORD, 'dev'))['options_with_recommendation']
        self.assertEqual(full.score, 10.0)
        self.assertGreater(partial.score, 0.0)
        self.assertLess(partial.score, full.score)

    def test_near_duplicate_options_count_as_one_option(self):
        """CHECK 2: two options whose normalised text is the same or nearly
        the same must count as ONE option. Same wording apart from
        'to'/'in' and no numbering: distinct_count collapses to 1, so full
        marks (which need 2-3 DISTINCT options) are out of reach even
        though a recommendation names one of them by ordinal."""
        text = (
            "# Plan\n\n## Options\n"
            "1. Add an idempotency key column to the invoices table.\n"
            "2. Add an idempotency key column in the invoices table.\n"
            "\nRecommendation: go with Option 1.\n"
        )
        result = by_name(intake_score.score_record(text, 'dev'))['options_with_recommendation']
        self.assertEqual(result.score, 7.0)

    def test_exact_duplicate_options_count_as_one_option(self):
        text = (
            "# Plan\n\n## Options\n"
            "1. Wrap the poster in a lock.\n"
            "2. Wrap the poster in a lock.\n"
            "\nRecommendation: go with Option 1.\n"
        )
        result = by_name(intake_score.score_record(text, 'dev'))['options_with_recommendation']
        self.assertEqual(result.score, 7.0)

    def test_a_recommendation_naming_no_option_scores_as_missing(self):
        """CHECK 2: mentioning the word 'recommend' is not enough; the
        sentence must actually NAME one of the options. Two genuinely
        distinct options with a recommendation about something else
        entirely must score exactly like having no recommendation at all."""
        with_unnamed_recommendation = by_name(intake_score.score_record(
            "# Plan\n\n## Options\n"
            "1. Add an idempotency key column to the invoices table.\n"
            "2. Wrap the poster in a lock.\n"
            "\nWe recommend consulting legal before proceeding.\n",
            'dev'))['options_with_recommendation']
        no_recommendation_at_all = by_name(intake_score.score_record(
            NO_RECOMMENDATION_RECORD, 'dev'))['options_with_recommendation']
        self.assertEqual(with_unnamed_recommendation.score, no_recommendation_at_all.score)

    def test_a_recommendation_naming_an_option_by_ordinal_scores_full(self):
        text = (
            "# Plan\n\n## Options\n"
            "1. Add an idempotency key column to the invoices table.\n"
            "2. Wrap the poster in a lock.\n"
            "\nRecommendation: go with option 2.\n"
        )
        result = by_name(intake_score.score_record(text, 'dev'))['options_with_recommendation']
        self.assertEqual(result.score, 10.0)



class WeightedOptionsTests(unittest.TestCase):
    """Calibrates row R6's new criterion in both directions: a bare
    pros-and-cons list must score ZERO (the founder's own framing: an option
    set without weights is a list, not a comparison), a real weight table
    must score full, and the shapes in between must land strictly in
    between, never rounding up to a pass they did not earn."""

    def test_a_bare_pros_and_cons_list_scores_zero(self):
        result = by_name(intake_score.score_record(UNWEIGHTED_OPTIONS_RECORD, 'dev'))['weighted_options']
        self.assertIsNotNone(result.score, "an unweighted Options section must SCORE, never abstain")
        self.assertEqual(result.score, 0.0)

    def test_a_real_weight_table_scores_full(self):
        result = by_name(intake_score.score_record(ALL_CITED_RECORD, 'dev'))['weighted_options']
        self.assertEqual(result.score, 10.0)

    def test_prose_mention_of_weight_scores_above_zero_below_full(self):
        result = by_name(intake_score.score_record(PROSE_WEIGHTED_OPTIONS_RECORD, 'dev'))['weighted_options']
        self.assertGreater(result.score, 0.0)
        self.assertLess(result.score, 10.0)

    def test_non_numeric_score_cells_score_above_zero_below_full(self):
        """Weights are stated (40/30/30) but the option cells ('high',
        'medium') are not numbers, so the arithmetic is not fully checkable."""
        result = by_name(intake_score.score_record(NON_NUMERIC_WEIGHTED_OPTIONS_RECORD, 'dev'))['weighted_options']
        self.assertGreater(result.score, 0.0)
        self.assertLess(result.score, 10.0)

    def test_a_table_with_no_weight_column_scores_above_zero_below_full(self):
        result = by_name(intake_score.score_record(NO_WEIGHT_COLUMN_RECORD, 'dev'))['weighted_options']
        self.assertGreater(result.score, 0.0)
        self.assertLess(result.score, 10.0)

    def test_weighted_options_is_zero_not_no_data_with_no_options_section(self):
        text = "# Plan\n\nSome prose with no Options section at all.\n"
        result = by_name(intake_score.score_record(text, 'dev'))['weighted_options']
        self.assertIsNotNone(result.score)
        self.assertEqual(result.score, 0.0)


class NoDataTests(unittest.TestCase):
    def test_missing_turn_data_yields_no_data_not_silent_zero(self):
        results = intake_score.score_record(ALL_CITED_RECORD, 'dev')
        interaction = by_name(results)['interaction_economy']
        self.assertIsNone(interaction.score)
        total_weight_scored, excluded_weight, weighted_score, floor_rule = intake_score.summarize(results)
        self.assertGreater(excluded_weight, 0)
        self.assertEqual(excluded_weight + total_weight_scored, 100)

    def test_supplying_turns_removes_the_no_data(self):
        results = intake_score.score_record(ALL_CITED_RECORD, 'dev', turns=3)
        interaction = by_name(results)['interaction_economy']
        self.assertEqual(interaction.score, 10.0)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.paths = []

    def tearDown(self):
        for p in self.paths:
            try:
                os.remove(p)
            except OSError:  # sbe: allow-silent temp fixture cleanup, may already be gone
                pass

    def _write(self, text):
        path = write_record(text)
        self.paths.append(path)
        return path

    def test_exit_2_on_missing_file(self):
        code, out, err = run_cli('/no/such/intake/record/exists.md', 'dev')
        self.assertEqual(code, 2)
        self.assertIn('error', err)

    def test_exit_2_without_persona(self):
        proc = subprocess.run([sys.executable, SCORER, '/no/such/file.md'], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)

    def test_final_line_format_and_exit_0(self):
        path = self._write(ALL_CITED_RECORD)
        code, out, err = run_cli(path, 'dev', extra_args=[
            '--turns', '3', '--process-questions', '0',
            '--override-rate', '5', '--repeat-questions', '0',
            '--receipt-verified',
        ])
        self.assertEqual(code, 0)
        last_line = out.strip().splitlines()[-1]
        self.assertRegex(
            last_line,
            r'^intake-score: persona=dev scored=\d+\.\d/10 over \d+ of 100 weight, floor_rule=(HOLDS|BROKEN|NO-DATA)$',
        )
        self.assertIn('floor_rule=HOLDS', last_line)
        self.assertIn('over 100 of 100 weight', last_line)

    def test_out_of_order_record_breaks_the_floor_rule_over_the_cli(self):
        path = self._write(OUT_OF_ORDER_RECORD)
        code, out, err = run_cli(path, 'dev', extra_args=[
            '--turns', '3', '--process-questions', '0',
            '--override-rate', '5', '--repeat-questions', '0',
            '--receipt-verified',
        ])
        self.assertEqual(code, 0)
        last_line = out.strip().splitlines()[-1]
        self.assertIn('floor_rule=BROKEN', last_line)

    def test_selftest_exits_zero(self):
        proc = subprocess.run([sys.executable, SCORER, '--selftest'], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn('PASS', proc.stdout)


class InlineAssumptionRegression(unittest.TestCase):
    """An assumption written as a plain line, not a bullet under an Assumptions
    heading, must still be scored. This was a real defect found by the
    orchestrator's own mutation test: the scorer returned NO-DATA on a criterion
    the design calls mechanically scoreable, because it only counted bullets
    inside a section it recognized. A rubric that abstains on the way intakes
    actually write is measuring the format, not the quality."""

    CITED = "# Plan\n\nAssumption: the poster is idempotent (`scripts/intake_score.py`).\n"
    UNCITED = "# Plan\n\nAssumption: the poster is idempotent.\n"

    def test_an_inline_cited_assumption_is_scored_not_no_data(self):
        result = by_name(intake_score.score_record(self.CITED, 'dev', root=REPO_ROOT))['grounded_assumptions']
        self.assertIsNotNone(
            result.score, "an inline cited assumption must score, never NO-DATA")
        self.assertEqual(result.score, 10.0)

    def test_an_inline_uncited_assumption_scores_below_a_cited_one(self):
        cited = by_name(intake_score.score_record(self.CITED, 'dev', root=REPO_ROOT))['grounded_assumptions']
        uncited = by_name(intake_score.score_record(self.UNCITED, 'dev', root=REPO_ROOT))['grounded_assumptions']
        self.assertIsNotNone(uncited.score)
        self.assertLess(uncited.score, cited.score,
                        "an uncited assumption must score lower than a cited one")


class WrappedAssumptionRegression(unittest.TestCase):
    """An assumption is an ITEM, not a line. Real records wrap, so a citation
    often sits on a continuation line. Reading one line at a time reported a
    correctly cited assumption as uncited: the scorer blaming the document for
    its own blind spot. Found by scoring a hand-written reference record that
    was built to pass, and did not."""

    WRAPPED = (
        "# Plan\n\n"
        "- Assumption: retries come from the queue consumer at-least-once delivery, per\n"
        "  `scripts/intake_score.py`. Likely.\n"
    )
    WRAPPED_UNCITED = (
        "# Plan\n\n"
        "- Assumption: retries come from the queue consumer at-least-once delivery, and\n"
        "  nobody has checked where.\n"
    )

    def test_a_citation_on_a_continuation_line_counts(self):
        result = by_name(intake_score.score_record(self.WRAPPED, 'dev', root=REPO_ROOT))['grounded_assumptions']
        self.assertIsNotNone(result.score)
        self.assertEqual(result.score, 10.0,
                         "a citation on a wrapped continuation line must count")

    def test_a_wrapped_assumption_with_no_citation_still_fails(self):
        result = by_name(intake_score.score_record(self.WRAPPED_UNCITED, 'dev', root=REPO_ROOT))['grounded_assumptions']
        self.assertIsNotNone(result.score)
        self.assertLess(result.score, 7.0,
                        "wrapping must not become a way to smuggle an uncited assumption past the check")


class AdversarialHolesRegression(unittest.TestCase):
    """Holes found by an adversarial attack on the scorer, not by its own suite.
    Both would have taught authors to write for the checker instead of the reader,
    which is the one failure a quality rubric cannot have."""

    EMPTY = "# Plan\n\nSome prose with no assumptions at all.\n"
    FILLER = ("# Plan\n\n- Assumption: the sky is blue. UNGROUNDED\n"
              "- Assumption: nothing can go wrong. UNGROUNDED\n")
    REAL = "# Plan\n\n- Assumption: the poster is idempotent, per `scripts/intake_score.py`. Confident.\n"

    def test_a_record_with_no_assumptions_scores_zero_not_no_data(self):
        """It scored 10.0 with the floor rule HOLDING, because abstaining dropped
        the criterion out of the denominator: writing nothing was the cheapest
        route to a perfect score."""
        r = by_name(intake_score.score_record(self.EMPTY, 'ba', root=REPO_ROOT))['grounded_assumptions']
        self.assertIsNotNone(r.score, "no assumptions must SCORE, never abstain")
        self.assertEqual(r.score, 0.0)

    def test_all_ungrounded_labels_cannot_reach_a_perfect_score(self):
        """Three content-free assumptions each suffixed UNGROUNDED scored 10.0.
        The label flags a gap for someone to close, it is not a way to pass."""
        r = by_name(intake_score.score_record(self.FILLER, 'dev', root=REPO_ROOT))['grounded_assumptions']
        self.assertIsNotNone(r.score)
        self.assertLessEqual(r.score, 6.0)

    def test_a_genuinely_cited_assumption_still_scores_full(self):
        """The fixes must not punish the real thing."""
        r = by_name(intake_score.score_record(self.REAL, 'dev', root=REPO_ROOT))['grounded_assumptions']
        self.assertEqual(r.score, 10.0)


class DiagramGate(unittest.TestCase):
    """Calibrates the diagram gate in BOTH directions, and separately proves it
    distinguishes its two failure modes. The gate was added because
    `grep -n intake scripts/check_all.sh` exited 1: the scorer existed, could
    already return 0.0 for a record with no fence, and nothing in the
    repository ran it. A scorer nobody runs is a preference, not a control."""

    WITH = "# Plan\n\n```mermaid\nflowchart LR\n  A --> B\n```\n"
    WITHOUT = "# Plan\n\n```text\nflowchart LR\n  A --> B\n```\n"

    def gate(self, *paths):
        proc = subprocess.run(
            [sys.executable, SCORER, '--gate'] + list(paths),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        return proc.returncode, proc.stdout + proc.stderr

    def test_a_record_carrying_a_fence_passes(self):
        path = write_record(self.WITH)
        try:
            code, out = self.gate(path)
        finally:
            os.unlink(path)
        self.assertEqual(code, 0, out)

    def test_a_record_with_no_fence_FAILS(self):
        """The direction that matters. Without this the gate is decoration."""
        path = write_record(self.WITHOUT)
        try:
            code, out = self.gate(path)
        finally:
            os.unlink(path)
        self.assertEqual(code, 1, out)
        self.assertIn('FAIL', out)

    def test_the_offender_is_named_not_just_counted(self):
        """A verdict that says '1 failed' without saying which costs a re-run.
        AUDIT FIX: this asserted only that the name appeared, so it would have
        passed had the gate printed FAIL and exited 0. Assert the code too."""
        path = write_record(self.WITHOUT)
        try:
            code, out = self.gate(path)
        finally:
            os.unlink(path)
        self.assertEqual(code, 1, out)
        self.assertIn(os.path.basename(path), out)

    def test_an_unreadable_record_is_NO_DATA_not_FAIL(self):
        """Could-not-measure is not measured-and-bad. Exit 2, its own verdict."""
        code, out = self.gate(os.path.join(REPO_ROOT, 'no-such-record-xyz.md'))
        self.assertEqual(code, 2, out)
        self.assertIn('NO-DATA', out)

    def test_an_unreadable_record_is_never_a_pass(self):
        code, _ = self.gate(os.path.join(REPO_ROOT, 'no-such-record-xyz.md'))
        self.assertNotEqual(code, 0)

    def test_a_real_FAIL_outranks_a_NO_DATA(self):
        path = write_record(self.WITHOUT)
        try:
            code, out = self.gate(path, os.path.join(REPO_ROOT, 'no-such-record-xyz.md'))
        finally:
            os.unlink(path)
        self.assertEqual(code, 1, out)

    def test_the_shipped_population_passes_today(self):
        """Guards the records this repository actually ships. If this ever goes
        red, a record entered the repository without its diagram."""
        code, out = self.gate()
        self.assertEqual(code, 0, out)

    def test_the_default_population_can_actually_go_red(self):
        """AUDIT FIX. The test above is vacuous on its own: it would pass if
        run_gate were replaced by `return 0`. Plant a bare record INSIDE the real
        default population and require the default run to go red, which proves
        the population is genuinely read rather than assumed clean."""
        planted = os.path.join(REPO_ROOT, 'docs', 'plan', 'examples',
                               'ZZ-AUDIT-TEMP-record.md')
        with open(planted, 'w', encoding='utf-8') as fh:
            fh.write(self.WITHOUT)
        try:
            code, out = self.gate()
        finally:
            os.unlink(planted)
        self.assertEqual(code, 1, out)
        self.assertIn('ZZ-AUDIT-TEMP-record.md', out)

    def test_a_mere_mention_of_the_opener_is_not_a_diagram(self):
        """AUDIT FINDING. The first draft matched the substring ```mermaid
        anywhere, so a sentence merely TALKING about a diagram passed."""
        path = write_record('# Plan\n\nWe should add a ```mermaid block here.\n')
        try:
            code, out = self.gate(path)
        finally:
            os.unlink(path)
        self.assertEqual(code, 1, out)

    def test_a_four_space_indented_fence_FAILS_the_gate(self):
        """AUDIT FINDING, at the gate level (not just the unit function): a
        4-space-indented fence renders as literal text on GitHub, so a
        record carrying only one must still fail."""
        path = write_record('# Plan\n\n    ```mermaid\n    graph TD; A-->B;\n    ```\n')
        try:
            code, out = self.gate(path)
        finally:
            os.unlink(path)
        self.assertEqual(code, 1, out)

    def test_an_empty_fence_is_not_a_diagram(self):
        """AUDIT FINDING. An opener immediately followed by a closer passed."""
        path = write_record('# Plan\n\n```mermaid\n```\n')
        try:
            code, out = self.gate(path)
        finally:
            os.unlink(path)
        self.assertEqual(code, 1, out)

    def test_an_unclosed_fence_is_not_a_diagram(self):
        """AUDIT FINDING. An opener with no closing fence passed."""
        path = write_record('# Plan\n\n```mermaid\nflowchart LR\n  A --> B\n')
        try:
            code, out = self.gate(path)
        finally:
            os.unlink(path)
        self.assertEqual(code, 1, out)

    def test_a_real_closed_non_empty_fence_still_passes(self):
        """The three tests above must not have been satisfied by refusing
        everything. This is the calibration in the other direction."""
        path = write_record('# Plan\n\n```mermaid\nflowchart LR\n  A --> B\n```\n')
        try:
            code, out = self.gate(path)
        finally:
            os.unlink(path)
        self.assertEqual(code, 0, out)

    def test_the_population_match_is_case_insensitive(self):
        """AUDIT FINDING. The glob was case-sensitive while the fence match was
        not, so renaming a file to lower case was a one-word bypass."""
        planted = os.path.join(REPO_ROOT, 'docs', 'plan', 'examples',
                               'zz-audit-temp-record.md')
        with open(planted, 'w', encoding='utf-8') as fh:
            fh.write(self.WITHOUT)
        try:
            code, out = self.gate()
        finally:
            os.unlink(planted)
        self.assertEqual(code, 1, out)

    def test_the_default_population_is_repo_anchored_not_cwd_anchored(self):
        """Run from anywhere, gate the same records. A cwd-relative glob would
        silently read nothing and, before the NO-DATA split, report a pass."""
        proc = subprocess.run(
            [sys.executable, SCORER, '--gate'], cwd=tempfile.gettempdir(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        # AUDIT FIX: this read assertNotIn('0 record(s) named'), which is a
        # substring of '10 record(s) named' and would have gone falsely RED the
        # day the population reached ten. Parse the number instead.
        m = re.search(r'(\d+) checked', proc.stdout)
        self.assertIsNotNone(m, proc.stdout)
        self.assertGreater(int(m.group(1)), 0, proc.stdout)

    def test_scoring_still_works_after_the_gate_was_added(self):
        """Regression: `record` became nargs='*' for the gate. One positional
        plus --persona must still score exactly as before."""
        path = write_record(self.WITH)
        try:
            proc = subprocess.run(
                [sys.executable, SCORER, path, '--persona', 'dev'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        finally:
            os.unlink(path)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('intake-score:', proc.stdout)

    def test_scoring_refuses_more_than_one_record(self):
        """nargs='*' would otherwise silently score only the first."""
        a, b = write_record(self.WITH), write_record(self.WITH)
        try:
            proc = subprocess.run(
                [sys.executable, SCORER, a, b, '--persona', 'dev'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        finally:
            os.unlink(a); os.unlink(b)
        self.assertNotEqual(proc.returncode, 0)


class WeightedOptionsGate(unittest.TestCase):
    """Calibrates the OPT-IN --require-weighted-options gate clause, in both
    directions, and separately proves the flag stays opt-in: the same
    population that goes red WITH the flag must stay green WITHOUT it, which
    is what keeps check_all.sh passing today per this row's own instruction
    not to edit the six shipped records to make a new gate pass."""

    WEIGHTED = (
        "# Plan\n\n## Options\n"
        "1. Add an idempotency key column. Recommended: this option.\n"
        "2. Wrap the poster in a lock.\n\n"
        "| Criterion | Weight | Option 1 | Option 2 |\n"
        "|---|---|---|---|\n"
        "| Cost | 40 | 8 | 5 |\n"
        "| Throughput | 30 | 7 | 3 |\n"
        "| Simplicity | 30 | 6 | 8 |\n\n"
        "```mermaid\nflowchart LR\n  A --> B\n```\n"
    )
    UNWEIGHTED = (
        "# Plan\n\n## Options\n"
        "1. Add an idempotency key column. Recommended: this option.\n"
        "2. Wrap the poster in a lock.\n\n"
        "```mermaid\nflowchart LR\n  A --> B\n```\n"
    )

    def gate(self, *paths, require_weighted_options=False):
        args = [sys.executable, SCORER, '--gate']
        if require_weighted_options:
            args.append('--require-weighted-options')
        args.extend(paths)
        proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        return proc.returncode, proc.stdout + proc.stderr

    def test_flag_off_an_unweighted_record_still_passes(self):
        """The flag must be opt-in: without it, weighted_options is not
        checked at all, exactly the pre-existing diagram-only behaviour."""
        path = write_record(self.UNWEIGHTED)
        try:
            code, out = self.gate(path)
        finally:
            os.unlink(path)
        self.assertEqual(code, 0, out)

    def test_flag_on_a_weighted_record_passes(self):
        path = write_record(self.WEIGHTED)
        try:
            code, out = self.gate(path, require_weighted_options=True)
        finally:
            os.unlink(path)
        self.assertEqual(code, 0, out)

    def test_flag_on_an_unweighted_record_FAILS(self):
        """The direction that matters. Without this the clause is decoration."""
        path = write_record(self.UNWEIGHTED)
        try:
            code, out = self.gate(path, require_weighted_options=True)
        finally:
            os.unlink(path)
        self.assertEqual(code, 1, out)
        self.assertIn('FAIL', out)
        self.assertIn(os.path.basename(path), out)

    def test_the_shipped_population_now_PASSES_under_the_flag(self):
        """INVERTED 2026-08-29, and the inversion is the point.

        This test was written to assert the DEBT: all six shipped records
        predated the weighted_options criterion, carried no weights, and had
        to go red, deliberately, because editing them to make the new gate
        pass would have been teaching to the test. Row R16 then paid the
        debt honestly, so the same assertion started failing for the right
        reason and had to be turned around rather than deleted.

        A test that asserts a known bad state is correct only while the state
        is bad. Leaving it inverted after the fix would have blocked the very
        repair it was documenting, and deleting it would have lost the record
        that the debt ever existed. So it is inverted, and the docstring keeps
        the history."""
        code, out = self.gate(require_weighted_options=True)
        self.assertEqual(code, 0, out)

    def test_the_flag_can_still_go_red_on_a_bare_record(self):
        """The property the inverted test above used to carry, now proven by
        MUTATION rather than by the population happening to be broken. Without
        this, the test above would pass over a gate that had stopped checking
        anything at all."""
        path = write_record('# Plan\n\n```mermaid\nflowchart LR\n  A --> B\n```\n\n'
                            '## Options\n\n1. do it\n2. do not\n\nRecommendation: do it.\n')
        try:
            code, out = self.gate(path, require_weighted_options=True)
        finally:
            os.unlink(path)
        self.assertEqual(code, 1, out)

    def test_the_shipped_population_passes_without_the_flag_too(self):
        """The diagram half must not have been broken by the weights half."""
        code, out = self.gate()
        self.assertEqual(code, 0, out)


class ViewsReplacePersonas(unittest.TestCase):
    """Row R8. The founder's own correction: `--persona ba|dev` is an enum
    that labels the PERSON, and intake can come from anyone on the team, so
    the selector must be dynamic (a view the reader controls) instead of a
    rigid identity chosen once. These tests calibrate the invariant this row
    exists to hold in BOTH directions: the real plumbing must agree across
    all four views (positive), and the exact equality check used to prove
    that must actually be capable of catching a design that lets the view
    leak into the scoring math (negative) -- otherwise the check verifies
    nothing."""

    def _snapshot(self, view, persona=None, **metrics):
        results = intake_score.score_for_view(ALL_CITED_RECORD, view, persona=persona, **metrics)
        return intake_score.view_snapshot(results)

    def test_views_agree_on_a_holding_record(self):
        ok, snapshots = intake_score.views_agree(
            ALL_CITED_RECORD, persona='dev', turns=3, process_questions=0)
        self.assertTrue(ok, snapshots)
        baseline = snapshots['balanced']
        for view in intake_score.VIEW_CHOICES:
            self.assertEqual(snapshots[view], baseline, "view=%s diverged from balanced" % view)
        self.assertEqual(baseline[-1], 'HOLDS')

    def test_views_agree_on_a_broken_record_too(self):
        """The invariant is not just tested on the happy path: a record
        whose floor_rule is BROKEN must report BROKEN identically in every
        view. Silently softening a risk in one view's rendering is exactly
        the failure this row forbids."""
        ok, snapshots = intake_score.views_agree(
            OUT_OF_ORDER_RECORD, persona='dev', turns=3, process_questions=0)
        self.assertTrue(ok, snapshots)
        baseline = snapshots['balanced']
        for view in intake_score.VIEW_CHOICES:
            self.assertEqual(snapshots[view], baseline)
        self.assertEqual(baseline[-1], 'BROKEN')

    def test_views_agree_when_persona_is_never_given_at_all(self):
        """A caller that only ever uses --view (no deprecated --persona) must
        still get an identical answer no matter which view it names, using
        the fixed DEFAULT_SCORE_PERSONA fallback."""
        ok, snapshots = intake_score.views_agree(ALL_CITED_RECORD, turns=3, process_questions=0)
        self.assertTrue(ok, snapshots)

    def test_view_never_changes_which_checks_ran_or_their_weight(self):
        _checks, total_weight_scored, _excl, _score, _floor = self._snapshot(
            'balanced', persona='dev', turns=3, process_questions=0)
        for view in intake_score.VIEW_CHOICES:
            checks, weight, _e, _s, _f = self._snapshot(view, persona='dev', turns=3, process_questions=0)
            self.assertEqual(len(checks), len(intake_score.CRITERIA_WEIGHTS))
            self.assertEqual(weight, total_weight_scored)

    def test_the_invariant_check_would_catch_a_view_that_changes_scoring(self):
        """CALIBRATION IN THE OTHER DIRECTION. Simulates the OLD, wrong shape
        this row removes: a view selecting the scoring bucket directly
        (outcome/balanced -> ba, data/code -> dev), exactly the mistake the
        founder flagged. turns=5 is chosen because it is the point where the
        two curves disagree: score_interaction_economy gives dev turns<=3 a
        10.0 and 4..6 a 7.0, while ba stays 10.0 up to turns<=8, so a
        record scored at turns=5 gets 7.0 under one bucket and 10.0 under the
        other. If this broken mapping ever comes back, the same
        snapshot-equality this row uses to prove HOLDS must report it as
        broken, not as another passing view."""
        broken_bucket_by_view = {'outcome': 'ba', 'balanced': 'ba', 'data': 'dev', 'code': 'dev'}
        snapshots = {}
        for view, bucket in broken_bucket_by_view.items():
            results = intake_score.score_record(ALL_CITED_RECORD, bucket, turns=5, process_questions=0)
            snapshots[view] = intake_score.view_snapshot(results)
        baseline = snapshots['balanced']
        all_agree = all(snap == baseline for snap in snapshots.values())
        self.assertFalse(
            all_agree,
            "a view-selects-the-scoring-bucket design must NOT pass the same equality "
            "check this row uses to prove the real plumbing holds")

    def test_persona_to_view_migration_mapping(self):
        self.assertEqual(intake_score.PERSONA_TO_VIEW, {'ba': 'outcome', 'dev': 'code'})

    def test_resolve_view_precedence_flag_beats_env_beats_persona_beats_default(self):
        old_env = os.environ.pop(intake_score.VIEW_ENV_VAR, None)
        try:
            self.assertEqual(intake_score.resolve_view('data', 'ba'), 'data')
            os.environ[intake_score.VIEW_ENV_VAR] = 'code'
            self.assertEqual(intake_score.resolve_view(None, 'ba'), 'code')
            del os.environ[intake_score.VIEW_ENV_VAR]
            self.assertEqual(intake_score.resolve_view(None, 'ba'), 'outcome')
            self.assertEqual(intake_score.resolve_view(None, 'dev'), 'code')
            self.assertEqual(intake_score.resolve_view(None, None), intake_score.DEFAULT_VIEW)
        finally:
            if old_env is None:
                os.environ.pop(intake_score.VIEW_ENV_VAR, None)
            else:
                os.environ[intake_score.VIEW_ENV_VAR] = old_env

    def test_print_report_never_drops_a_check_in_any_view(self):
        """Adaptation must never mean running fewer checks for a view. Every
        one of the four views must print every criterion, always."""
        results = intake_score.score_record(ALL_CITED_RECORD, 'dev', turns=3, process_questions=0)
        expected_names = set(name for name, _w in intake_score.CRITERIA_WEIGHTS)
        for view in intake_score.VIEW_CHOICES:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                intake_score.print_report(results, 'dev', view=view, persona_explicit=True)
            printed_names = set(
                re.match(r'^- (\S+) ', line).group(1)
                for line in buf.getvalue().splitlines() if line.startswith('- '))
            self.assertEqual(printed_names, expected_names, "view=%s dropped a check" % view)

    def test_outcome_view_reorders_but_balanced_does_not_change_the_facts(self):
        """Rendering is proven to actually differ (outcome leads with the
        lowest scoring / NO-DATA checks), while the underlying conclusions
        captured in the snapshot stay identical -- proven above. Here: the
        printed ORDER for outcome differs from balanced's fixed order, on a
        record where at least one check is NO-DATA so there is something to
        reorder toward."""
        results = intake_score.score_record(ALL_CITED_RECORD, 'dev')  # no turns/process-questions: NO-DATA present
        buf_balanced = io.StringIO()
        with contextlib.redirect_stdout(buf_balanced):
            intake_score.print_report(results, 'dev', view='balanced', persona_explicit=True)
        buf_outcome = io.StringIO()
        with contextlib.redirect_stdout(buf_outcome):
            intake_score.print_report(results, 'dev', view='outcome', persona_explicit=True)
        balanced_order = [l for l in buf_balanced.getvalue().splitlines() if l.startswith('- ')]
        outcome_order = [l for l in buf_outcome.getvalue().splitlines() if l.startswith('- ')]
        self.assertNotEqual(balanced_order, outcome_order)
        self.assertEqual(set(balanced_order), set(outcome_order))

    def test_cli_view_flag_alone_scores_without_persona(self):
        path = write_record(ALL_CITED_RECORD)
        try:
            proc = subprocess.run(
                [sys.executable, SCORER, path, '--view', 'data',
                 '--turns', '3', '--process-questions', '0'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        finally:
            os.unlink(path)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('intake-view: view=data', proc.stdout)
        last_line = proc.stdout.strip().splitlines()[-1]
        self.assertRegex(last_line, r'^intake-score: view=data scored=')

    def test_cli_old_persona_still_produces_the_exact_old_final_line(self):
        """The migration promise, over the CLI: --persona dev keeps
        producing byte-identical output to before this row, not merely a
        code path that happens to still run."""
        path = write_record(ALL_CITED_RECORD)
        try:
            proc = subprocess.run(
                [sys.executable, SCORER, path, '--persona', 'dev',
                 '--turns', '3', '--process-questions', '0'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        finally:
            os.unlink(path)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        last_line = proc.stdout.strip().splitlines()[-1]
        self.assertRegex(
            last_line,
            r'^intake-score: persona=dev scored=\d+\.\d/10 over \d+ of 100 weight, floor_rule=(HOLDS|BROKEN|NO-DATA)$')

    def test_cli_persona_ba_starts_on_the_outcome_view(self):
        path = write_record(ALL_CITED_RECORD)
        try:
            proc = subprocess.run(
                [sys.executable, SCORER, path, '--persona', 'ba'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        finally:
            os.unlink(path)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('intake-view: view=outcome', proc.stdout)

    def test_cli_check_views_passes_on_a_real_record(self):
        path = write_record(ALL_CITED_RECORD)
        try:
            proc = subprocess.run(
                [sys.executable, SCORER, path, '--check-views',
                 '--turns', '3', '--process-questions', '0'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        finally:
            os.unlink(path)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('view-invariant: HOLDS', proc.stdout)
        for view in intake_score.VIEW_CHOICES:
            self.assertIn('view=%s' % view, proc.stdout)

    def test_cli_still_exits_2_with_neither_persona_nor_view(self):
        path = write_record(ALL_CITED_RECORD)
        try:
            proc = subprocess.run(
                [sys.executable, SCORER, path],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        finally:
            os.unlink(path)
        self.assertEqual(proc.returncode, 2)


class DataOverlayTests(unittest.TestCase):
    """Calibrates --overlay data (row P15, doc 23.2 DS overlay): four rows
    (cutoff_and_split, leakage, primary_metric_and_baseline, reproducibility)
    scored PASS/FAIL/NO-DATA straight from the record text, printed under the
    universal total, and proven never to move that total or the exit code."""

    ALL_FOUR = (
        "# Intake Record\n\n"
        "## Data Considerations\n"
        "Data cutoff: 2026-06-30. The train/test split is temporal, train before cutoff, test after.\n"
        "Leakage check: features available only at prediction time were used, confirmed no target leakage.\n"
        "The primary metric is F1; the baseline is the existing rule-based classifier at F1 0.62.\n"
        "Reproducibility: random seed=42, pinned data version v3, rerun via `scripts/train.py`.\n"
    )

    # The shipped-shape record from this file's own template: names none of
    # the four DS topics, so every row must abstain rather than guess.
    NONE_NAMED = ALL_CITED_RECORD

    GAPS_NAMED = (
        "# Intake Record\n\n"
        "## Data Considerations\n"
        "No cutoff defined for this dataset; the split has not been specified.\n"
        "Leakage risk not checked before training.\n"
        "No baseline chosen for the primary metric.\n"
        "This result is not reproducible: no seed was set.\n"
    )

    def test_all_four_topics_score_pass(self):
        rows = intake_score.score_data_overlay(self.ALL_FOUR)
        by = {r.name: r for r in rows}
        self.assertEqual(len(rows), 4)
        for name in ('cutoff_and_split', 'leakage', 'primary_metric_and_baseline', 'reproducibility'):
            self.assertEqual(by[name].status, 'PASS', "%s: %s" % (name, by[name].evidence))
            self.assertTrue(by[name].evidence)

    def test_no_topic_named_scores_no_data_not_fail(self):
        rows = intake_score.score_data_overlay(self.NONE_NAMED)
        self.assertEqual(len(rows), 4)
        for r in rows:
            self.assertEqual(r.status, 'NO-DATA', "%s: %s" % (r.name, r.evidence))

    def test_a_stated_gap_scores_fail(self):
        """The direction that matters: without this the row can never fail
        and verifies nothing, per this estate's own standing rule that a
        check which cannot fail proves nothing."""
        rows = intake_score.score_data_overlay(self.GAPS_NAMED)
        for r in rows:
            self.assertEqual(r.status, 'FAIL', "%s: %s" % (r.name, r.evidence))

    def test_no_leakage_found_is_a_pass_not_a_fail(self):
        """The one row where a bare negation word is the GOOD outcome ('no
        target leakage'). A generic 'contains a no/not word' rule would fail
        this; the leakage row uses its own narrower gap language instead."""
        text = "# Plan\n\nLeakage: checked and confirmed no target leakage between train and test.\n"
        row = {r.name: r for r in intake_score.score_data_overlay(text)}['leakage']
        self.assertEqual(row.status, 'PASS', row.evidence)

    def test_cli_overlay_prints_four_rows_under_the_total(self):
        path = write_record(self.ALL_FOUR)
        try:
            code, out, err = run_cli(path, 'dev', extra_args=['--overlay', 'data'])
        finally:
            os.unlink(path)
        self.assertEqual(code, 0, err)
        self.assertIn('intake-overlay: overlay=data', out)
        for name in ('cutoff_and_split', 'leakage', 'primary_metric_and_baseline', 'reproducibility'):
            self.assertIn('- %s: PASS' % name, out)
        total_idx = out.index('intake-score: persona=dev')
        overlay_idx = out.index('intake-overlay: overlay=data')
        self.assertLess(total_idx, overlay_idx, 'the overlay must print under the universal total')

    def test_overlay_never_changes_the_universal_total_or_exit_code(self):
        path = write_record(self.ALL_FOUR)
        try:
            code_with, out_with, _ = run_cli(path, 'dev', extra_args=['--overlay', 'data'])
            code_without, out_without, _ = run_cli(path, 'dev')
        finally:
            os.unlink(path)
        self.assertEqual(code_with, code_without)
        total_with = [ln for ln in out_with.splitlines() if ln.startswith('intake-score:')][0]
        total_without = [ln for ln in out_without.splitlines() if ln.startswith('intake-score:')][0]
        self.assertEqual(total_with, total_without)

    def test_no_topic_record_keeps_total_unchanged_too(self):
        path = write_record(self.NONE_NAMED)
        try:
            code_with, out_with, _ = run_cli(path, 'dev', extra_args=['--overlay', 'data'])
            code_without, out_without, _ = run_cli(path, 'dev')
        finally:
            os.unlink(path)
        self.assertEqual(code_with, code_without)
        total_with = [ln for ln in out_with.splitlines() if ln.startswith('intake-score:')][0]
        total_without = [ln for ln in out_without.splitlines() if ln.startswith('intake-score:')][0]
        self.assertEqual(total_with, total_without)
        for name in ('cutoff_and_split', 'leakage', 'primary_metric_and_baseline', 'reproducibility'):
            self.assertIn('- %s: NO-DATA' % name, out_with)

    def test_unknown_overlay_name_refused_at_exit_2(self):
        path = write_record(self.ALL_FOUR)
        try:
            proc = subprocess.run(
                [sys.executable, SCORER, path, '--persona', 'dev', '--overlay', 'bogus'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        finally:
            os.unlink(path)
        self.assertEqual(proc.returncode, 2)
        self.assertIn('bogus', proc.stderr, 'the unknown overlay must be refused BY NAME')

    def test_without_overlay_flag_no_overlay_output_at_all(self):
        path = write_record(self.ALL_FOUR)
        try:
            code, out, err = run_cli(path, 'dev')
        finally:
            os.unlink(path)
        self.assertEqual(code, 0, err)
        self.assertNotIn('intake-overlay', out)


if __name__ == '__main__':
    unittest.main()
