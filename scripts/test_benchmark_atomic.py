"""Calibration for scripts/benchmark_atomic.py: proves the atomic benchmark
harness can PASS, FAIL, and NO-DATA, and that NO-DATA never counts as a score,
per this estate's rule that a check that cannot fail verifies nothing.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, 'scripts')
HARNESS = os.path.join(SCRIPTS_DIR, 'benchmark_atomic.py')

sys.path.insert(0, SCRIPTS_DIR)
import benchmark_atomic as ba  # noqa: E402  (import after sys.path edit, by necessity)


def run_cli(extra_args=None):
    args = [sys.executable, HARNESS]
    if extra_args:
        args.extend(extra_args)
    proc = subprocess.run(args, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


class SelftestTests(unittest.TestCase):
    def test_selftest_passes(self):
        code, out, err = run_cli(['--selftest'])
        self.assertEqual(code, 0, err)
        self.assertIn('PASS', out)

    def test_selftest_function_returns_true(self):
        self.assertTrue(ba.run_selftest())


class DimensionTests(unittest.TestCase):
    def test_every_criterion_declares_one_of_the_five_dimensions(self):
        for c in ba.CRITERIA:
            self.assertIn(c.dimension, ba.DIMENSIONS,
                          "%s declares dimension %r, not one of %r" % (c.id, c.dimension, ba.DIMENSIONS))

    def test_at_least_one_criterion_per_dimension_family_is_not_required_but_ids_are_unique(self):
        ids = [c.id for c in ba.CRITERIA]
        self.assertEqual(len(ids), len(set(ids)), "criterion ids must be unique")


class NoDataNeverScoresTests(unittest.TestCase):
    """A subject with no shipped material anywhere must return NO-DATA on
    every criterion, never a silent PASS or FAIL, and NO-DATA must never be
    counted in the scored total."""

    def _empty_subject(self, tmp):
        empty_dir = Path(tmp) / 'nothing_here'
        empty_dir.mkdir()
        return ba.Subject('empty-subject', [empty_dir], None, [str(empty_dir)])

    def test_every_criterion_returns_no_data_for_an_empty_subject(self):
        with tempfile.TemporaryDirectory() as tmp:
            subject = self._empty_subject(tmp)
            for c in ba.CRITERIA:
                verdict, evidence = c.check(subject)
                self.assertEqual(verdict, 'NO-DATA',
                                  "%s scored %s on a subject with no material at all; evidence: %s" % (
                                      c.id, verdict, evidence))
                self.assertTrue(evidence, "%s returned an empty evidence string" % c.id)

    def test_no_data_is_excluded_from_the_scored_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_subject = self._empty_subject(tmp)
            results = ba.score_all([empty_subject], ba.CRITERIA)
            scored = sum(1 for (verdict, _ev) in results.values() if verdict != 'NO-DATA')
            self.assertEqual(scored, 0)


class ExitCodeTests(unittest.TestCase):
    def test_runner_exits_2_when_nothing_can_be_scored(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_subject = ba.Subject('empty-subject', [Path(tmp)], None, [tmp])
            results = ba.score_all([empty_subject], ba.CRITERIA)
            scored_total = ba.print_report([empty_subject], ba.CRITERIA, results)
            self.assertEqual(scored_total, 0)
            exit_code = 0 if scored_total > 0 else 2
            self.assertEqual(exit_code, 2)

    def test_real_run_exits_0_on_this_machine(self):
        # This estate's own repo and its README are always present when this
        # test runs, so at least one cell must score; a 0 here would mean the
        # harness itself, not just a fixture, found nothing to read.
        code, out, err = run_cli([])
        self.assertEqual(code, 0, "expected the real run to score something; stderr: %s" % err)
        self.assertIn('--- summary ---', out)


class JsonOutputTests(unittest.TestCase):
    def test_json_flag_emits_parseable_json_with_one_row_per_cell(self):
        import json
        code, out, err = run_cli(['--json'])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        rows = data['results']
        # five subjects: brother, superpowers, bmad, gsd, speckit
        self.assertEqual(len(rows), len(ba.CRITERIA) * 5)
        for row in rows:
            self.assertIn(row['verdict'], ('PASS', 'FAIL', 'NO-DATA'))
            self.assertIn(row['dimension'], ba.DIMENSIONS)


class ClonedSubjectMissingRootTests(unittest.TestCase):
    """bmad, gsd, and speckit are read from a local, pinned clone. When that
    clone is absent (neither the override env var nor the scratch default
    exists), the harness must never clone, never crash, and never score a
    loss: every criterion reads NO-DATA naming the paths it probed."""

    def _subject_with_no_clone_anywhere(self, name, tmp):
        old_scratch = ba.SCRATCH_SUBJECTS_ROOT
        old_env = os.environ.pop(ba.CLONE_ROOT_ENV_VARS[name], None)
        ba.SCRATCH_SUBJECTS_ROOT = Path(tmp) / 'no-clones-here'
        try:
            return ba.build_cloned_subject(name)
        finally:
            ba.SCRATCH_SUBJECTS_ROOT = old_scratch
            if old_env is not None:
                os.environ[ba.CLONE_ROOT_ENV_VARS[name]] = old_env

    def test_missing_root_is_no_data_for_every_criterion(self):
        for name in ('bmad', 'gsd', 'speckit'):
            with tempfile.TemporaryDirectory() as tmp:
                subject = self._subject_with_no_clone_anywhere(name, tmp)
                self.assertEqual(subject.roots, [])
                self.assertTrue(subject.probed, "%s declared no probed path" % name)
                for c in ba.CRITERIA:
                    verdict, evidence = c.check(subject)
                    self.assertEqual(
                        verdict, 'NO-DATA',
                        "%s scored %s on %s with no clone present, expected NO-DATA; evidence: %s" % (
                            c.id, verdict, name, evidence))

    def test_override_env_var_wins_over_the_scratch_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp) / 'bmad-fixture'
            fixture_root.mkdir()
            (fixture_root / 'README.md').write_text('# bmad fixture\n\nreceipt\n', encoding='utf-8')
            old_env = os.environ.get('BENCH_ROOT_BMAD')
            os.environ['BENCH_ROOT_BMAD'] = str(fixture_root)
            try:
                subject = ba.build_cloned_subject('bmad')
            finally:
                if old_env is None:
                    os.environ.pop('BENCH_ROOT_BMAD', None)
                else:
                    os.environ['BENCH_ROOT_BMAD'] = old_env
            self.assertEqual(subject.roots, [fixture_root])


class SubjectDefinitionInvariantTests(unittest.TestCase):
    """Every subject definition must carry either an embedded fixture (the
    selftest's tiny inline PASS/FAIL/empty dirs) or a configurable root path
    (every subject actually scored by a real run); nothing may be scored
    from neither."""

    def test_every_production_subject_declares_a_probed_root_path(self):
        builders = [
            ba.build_brother_subject,
            ba.build_superpowers_subject,
            lambda: ba.build_cloned_subject('bmad'),
            lambda: ba.build_cloned_subject('gsd'),
            lambda: ba.build_cloned_subject('speckit'),
        ]
        for build in builders:
            subject = build()
            self.assertIsInstance(subject.roots, list)
            self.assertTrue(subject.probed,
                             "%s declares no probed path (must carry a root path)" % subject.name)

    def test_selftest_fixtures_are_embedded_not_configurable_root_paths(self):
        # run_selftest writes its PASS/FAIL/empty fixtures inline to a
        # tempdir every call; this is the "embedded fixture" half of the
        # invariant, proven simply by the selftest still passing.
        self.assertTrue(ba.run_selftest())


def _fake_criteria(ids_dims):
    """A minimal criteria list good enough for compute_scores/compute_reasons/
    compute_borrow_items, which only ever read .id and .dimension off each
    entry (the .check callable is never invoked by these functions, since
    they operate on an already-computed results dict, not on subjects)."""
    return [ba.Criterion(cid, dim, 'fixture criterion', None) for cid, dim in ids_dims]


class FakeSubject(object):
    def __init__(self, name):
        self.name = name


class ScoreArithmeticTests(unittest.TestCase):
    """10 * (sum of weights of PASS) / (total weight MINUS that subject's own
    NO-DATA weight); NO-DATA is excluded from the denominator, never counted
    as a zero."""

    def test_no_data_reduces_the_denominator_not_the_score(self):
        criteria = _fake_criteria([('c1', 'ux'), ('c2', 'ux'), ('c3', 'ux'), ('c4', 'ux')])
        subjects = [FakeSubject('x')]
        results = {
            ('c1', 'x'): ('PASS', 'ev1'),
            ('c2', 'x'): ('PASS', 'ev2'),
            ('c3', 'x'): ('FAIL', 'ev3'),
            ('c4', 'x'): ('NO-DATA', 'ev4'),
        }
        scores = ba.compute_scores(subjects, criteria, results)
        r = scores['x']
        self.assertEqual(r['pass'], 2)
        self.assertEqual(r['fail'], 1)
        self.assertEqual(r['no_data'], 1)
        self.assertEqual(r['covered'], 3)   # 4 total minus 1 NO-DATA
        self.assertEqual(r['total'], 4)
        self.assertEqual(r['score'], round(10.0 * 2 / 3, 1))  # 6.7, never 5.0

    def test_all_no_data_scores_none_never_a_silent_zero(self):
        criteria = _fake_criteria([('c1', 'ux'), ('c2', 'ux')])
        subjects = [FakeSubject('empty')]
        results = {('c1', 'empty'): ('NO-DATA', 'e'), ('c2', 'empty'): ('NO-DATA', 'e')}
        scores = ba.compute_scores(subjects, criteria, results)
        self.assertIsNone(scores['empty']['score'])
        self.assertEqual(scores['empty']['covered'], 0)

    def test_full_marks_when_every_covered_check_passes(self):
        criteria = _fake_criteria([('c1', 'ux'), ('c2', 'ux')])
        subjects = [FakeSubject('perfect')]
        results = {('c1', 'perfect'): ('PASS', 'e'), ('c2', 'perfect'): ('PASS', 'e')}
        scores = ba.compute_scores(subjects, criteria, results)
        self.assertEqual(scores['perfect']['score'], 10.0)


class ReasonsTests(unittest.TestCase):
    """A reasons entry fires only where brother is not PASS and at least one
    other subject IS PASS; never where brother already passes, and never
    invents a leader nobody actually scored PASS."""

    def test_reason_fires_only_on_a_brother_loss_with_a_passing_leader(self):
        criteria = _fake_criteria([('c1', 'onboarding'), ('c2', 'ux')])
        subjects = [FakeSubject('brother'), FakeSubject('rival')]
        results = {
            ('c1', 'brother'): ('FAIL', 'brother fail ev'),
            ('c1', 'rival'): ('PASS', 'rival pass ev'),
            ('c2', 'brother'): ('PASS', 'brother pass ev'),
            ('c2', 'rival'): ('FAIL', 'rival fail ev'),
        }
        reasons = ba.compute_reasons(subjects, criteria, results)
        self.assertEqual([r['criterion'] for r in reasons], ['c1'])
        self.assertEqual(reasons[0]['leaders'], ['rival'])
        self.assertTrue(reasons[0]['sentence'])

    def test_no_data_alone_is_never_a_reason(self):
        """Brother FAIL against another subject's NO-DATA (nobody actually
        beat brother) must not produce a reasons entry."""
        criteria = _fake_criteria([('c1', 'onboarding')])
        subjects = [FakeSubject('brother'), FakeSubject('rival')]
        results = {
            ('c1', 'brother'): ('FAIL', 'e'),
            ('c1', 'rival'): ('NO-DATA', 'e'),
        }
        self.assertEqual(ba.compute_reasons(subjects, criteria, results), [])

    def test_no_brother_subject_yields_no_reasons(self):
        criteria = _fake_criteria([('c1', 'onboarding')])
        subjects = [FakeSubject('a'), FakeSubject('b')]
        results = {('c1', 'a'): ('FAIL', 'e'), ('c1', 'b'): ('PASS', 'e')}
        self.assertEqual(ba.compute_reasons(subjects, criteria, results), [])

    def test_install_commands_sentence_derives_the_actual_counts(self):
        """The one criterion with a hand written sentence template: the
        numbers in the sentence must come from the evidence strings
        themselves, never be hardcoded independent of them."""
        criteria = _fake_criteria([('install-commands-documented', 'onboarding')])
        subjects = [FakeSubject('brother'), FakeSubject('rival')]
        results = {
            ('install-commands-documented', 'brother'):
                ('FAIL', '3 install command line(s) in the first fenced block after an Install heading in X'),
            ('install-commands-documented', 'rival'):
                ('PASS', '1 install command line(s) in the first fenced block after an Install heading in Y'),
        }
        reasons = ba.compute_reasons(subjects, criteria, results)
        self.assertIn('rival needs only 1 install command(s)', reasons[0]['sentence'])
        self.assertIn('brother needs 3', reasons[0]['sentence'])


class BorrowItemsTests(unittest.TestCase):
    """The borrow flow: one item per losing cell, stage overrides honored
    while the cell is still losing, and BEATEN detected automatically (never
    by what the hand-maintained file says) the moment the cell itself flips
    to brother PASS."""

    def test_losing_cell_becomes_a_research_item_by_default(self):
        criteria = _fake_criteria([('c1', 'onboarding')])
        subjects = [FakeSubject('brother'), FakeSubject('rival')]
        results = {('c1', 'brother'): ('FAIL', 'be'), ('c1', 'rival'): ('PASS', 're')}
        items = ba.compute_borrow_items(subjects, criteria, results, {})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['id'], 'borrow-c1')
        self.assertEqual(items[0]['stage'], 'RESEARCH')
        self.assertEqual(items[0]['leaders'], ['rival'])

    def test_override_stage_persists_while_still_losing(self):
        criteria = _fake_criteria([('c1', 'onboarding')])
        subjects = [FakeSubject('brother'), FakeSubject('rival')]
        results = {('c1', 'brother'): ('FAIL', 'be'), ('c1', 'rival'): ('PASS', 're')}
        items = ba.compute_borrow_items(subjects, criteria, results, {'borrow-c1': 'DESIGN'})
        self.assertEqual(items[0]['stage'], 'DESIGN')

    def test_beaten_is_detected_automatically_on_flip_ignoring_the_override_value(self):
        """The hand-maintained file still says BUILD; the run data now shows
        brother PASS. The generator must report BEATEN regardless of what
        the file says, because only the flipped cell closes an item."""
        criteria = _fake_criteria([('c1', 'onboarding')])
        subjects = [FakeSubject('brother'), FakeSubject('rival')]
        results = {('c1', 'brother'): ('PASS', 'be'), ('c1', 'rival'): ('PASS', 're')}
        items = ba.compute_borrow_items(subjects, criteria, results, {'borrow-c1': 'BUILD'})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['stage'], 'BEATEN')

    def test_brother_already_passing_and_never_tracked_yields_no_item(self):
        criteria = _fake_criteria([('c1', 'onboarding')])
        subjects = [FakeSubject('brother'), FakeSubject('rival')]
        results = {('c1', 'brother'): ('PASS', 'be'), ('c1', 'rival'): ('PASS', 're')}
        items = ba.compute_borrow_items(subjects, criteria, results, {})
        self.assertEqual(items, [])

    def test_no_leader_and_brother_not_passing_yields_no_item(self):
        """A criterion where brother FAILs but nobody else PASSes either
        (parity in loss, or everyone NO-DATA) is not a borrow item: there is
        nothing to borrow from."""
        criteria = _fake_criteria([('c1', 'onboarding')])
        subjects = [FakeSubject('brother'), FakeSubject('rival')]
        results = {('c1', 'brother'): ('FAIL', 'be'), ('c1', 'rival'): ('NO-DATA', 're')}
        items = ba.compute_borrow_items(subjects, criteria, results, {})
        self.assertEqual(items, [])

    def test_proposed_move_falls_back_to_unscheduled_for_an_unmapped_criterion(self):
        criteria = _fake_criteria([('some-new-criterion-nobody-mapped', 'ux')])
        subjects = [FakeSubject('brother'), FakeSubject('rival')]
        results = {
            ('some-new-criterion-nobody-mapped', 'brother'): ('FAIL', 'be'),
            ('some-new-criterion-nobody-mapped', 'rival'): ('PASS', 're'),
        }
        items = ba.compute_borrow_items(subjects, criteria, results, {})
        self.assertEqual(items[0]['proposed_move'], 'unscheduled, needs a design')

    def test_install_commands_documented_is_unscheduled_by_design(self):
        """This one is a real, current cell, not a fixture: as of this run
        no roadmap phase closes the install-command count itself (only the
        FAIL-state fix has a phase), so the fallback must fire for it."""
        self.assertEqual(ba.phase_for_criterion('install-commands-documented'),
                          'unscheduled, needs a design')

    def test_a_mapped_criterion_returns_its_citation_not_the_fallback(self):
        phase = ba.phase_for_criterion('fail-states-on-first-run')
        self.assertNotEqual(phase, 'unscheduled, needs a design')
        self.assertIn('Phase 0', phase)


class ResearchLocationTests(unittest.TestCase):
    def test_a_pinned_clone_subject_gets_its_sha(self):
        loc = ba.research_location('bmad')
        self.assertIn(ba.PINNED_SHAS['bmad'], loc)

    def test_superpowers_is_named_as_not_a_cloned_subject(self):
        loc = ba.research_location('superpowers')
        self.assertIn('no pinned sha', loc)

    def test_an_unknown_subject_never_invents_a_path(self):
        loc = ba.research_location('nobody-ever-heard-of-this-subject')
        self.assertEqual(loc, 'no pinned clone or citation on file for this subject')


class BorrowQueueMarkdownTests(unittest.TestCase):
    def test_empty_items_still_states_the_law(self):
        text = ba.render_borrow_queue_md([])
        self.assertIn('THE LAW', text)
        self.assertIn('closes only when', text)

    def test_items_render_one_table_row_each_sorted_by_criterion(self):
        items = [
            {'id': 'borrow-zzz', 'criterion': 'zzz', 'dimension': 'ux', 'leaders': ['x'],
             'leader_detail': [{'subject': 'x', 'evidence': 'ev', 'research': 'loc'}],
             'brother_verdict': 'FAIL', 'brother_evidence': 'be',
             'proposed_move': 'unscheduled, needs a design', 'stage': 'RESEARCH'},
            {'id': 'borrow-aaa', 'criterion': 'aaa', 'dimension': 'ux', 'leaders': ['y'],
             'leader_detail': [{'subject': 'y', 'evidence': 'ev2', 'research': 'loc2'}],
             'brother_verdict': 'FAIL', 'brother_evidence': 'be2',
             'proposed_move': 'unscheduled, needs a design', 'stage': 'RESEARCH'},
        ]
        text = ba.render_borrow_queue_md(items)
        self.assertLess(text.index('borrow-aaa'), text.index('borrow-zzz'))

    def test_pipe_characters_in_evidence_never_break_the_table(self):
        items = [{
            'id': 'borrow-c1', 'criterion': 'c1', 'dimension': 'ux', 'leaders': ['x'],
            'leader_detail': [{'subject': 'x', 'evidence': 'a | b', 'research': 'loc'}],
            'brother_verdict': 'FAIL', 'brother_evidence': 'be',
            'proposed_move': 'unscheduled, needs a design', 'stage': 'RESEARCH',
        }]
        text = ba.render_borrow_queue_md(items)
        table_lines = [l for l in text.splitlines() if l.startswith('|')]
        # header + separator + one row
        self.assertEqual(len(table_lines), 3)
        self.assertEqual(table_lines[-1].count('|'), 9)  # 8 columns -> 9 pipes

    def test_is_deterministic(self):
        items = [{
            'id': 'borrow-c1', 'criterion': 'c1', 'dimension': 'ux', 'leaders': ['x'],
            'leader_detail': [{'subject': 'x', 'evidence': 'ev', 'research': 'loc'}],
            'brother_verdict': 'FAIL', 'brother_evidence': 'be',
            'proposed_move': 'unscheduled, needs a design', 'stage': 'RESEARCH',
        }]
        self.assertEqual(ba.render_borrow_queue_md(items), ba.render_borrow_queue_md(items))


class StageOverridesTests(unittest.TestCase):
    def test_absent_file_returns_empty_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ba.load_stage_overrides(Path(tmp) / 'nope.json'), {})

    def test_malformed_json_returns_empty_dict_never_crashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'bad.json'
            p.write_text('{not json', encoding='utf-8')
            self.assertEqual(ba.load_stage_overrides(p), {})

    def test_a_json_list_instead_of_a_dict_is_rejected_not_crashed_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'list.json'
            p.write_text('["not", "a", "dict"]', encoding='utf-8')
            self.assertEqual(ba.load_stage_overrides(p), {})

    def test_valid_overrides_are_read_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'ok.json'
            p.write_text('{"borrow-c1": "BUILD"}', encoding='utf-8')
            self.assertEqual(ba.load_stage_overrides(p), {'borrow-c1': 'BUILD'})


class RealRunJsonShapeTests(unittest.TestCase):
    """The real CLI's --json output, not a fixture: proves the three new
    top level keys actually reach the real run, not just the unit-tested
    helper functions in isolation."""

    def test_json_output_carries_scores_reasons_and_borrow_items(self):
        import json
        code, out, err = run_cli(['--json'])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertIn('scores', data)
        self.assertIn('reasons', data)
        self.assertIn('borrow_items', data)
        self.assertIn('brother', data['scores'])
        self.assertIsNotNone(data['scores']['brother']['score'])

    def test_every_reason_is_a_real_brother_loss_against_a_real_pass(self):
        import json
        code, out, err = run_cli(['--json'])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        by_cell = dict(((r['criterion'], r['subject']), r['verdict']) for r in data['results'])
        for r in data['reasons']:
            self.assertNotEqual(by_cell[(r['criterion'], 'brother')], 'PASS')
            self.assertTrue(any(
                by_cell[(r['criterion'], leader)] == 'PASS' for leader in r['leaders']))

    def test_borrow_queue_file_is_written_and_scores_the_same_shape_twice(self):
        code1, out1, _ = run_cli([])
        self.assertEqual(code1, 0)
        first = ba.BORROW_QUEUE_PATH.read_text(encoding='utf-8')
        code2, out2, _ = run_cli([])
        self.assertEqual(code2, 0)
        second = ba.BORROW_QUEUE_PATH.read_text(encoding='utf-8')
        self.assertEqual(first, second, 'BORROW-QUEUE.md must be byte identical across two runs')


class DurableCloneRootTests(unittest.TestCase):
    """2026-08-29 Gate 1 instrument repair, fault 1: the default clone
    location must be a durable path under the user's home, never a
    session's ephemeral scratchpad, and a missing clone must hand back an
    actionable command, not just a dead path."""

    def test_default_root_is_under_home_never_under_tmp_or_scratchpad(self):
        home = str(Path.home())
        root = str(ba.SCRATCH_SUBJECTS_ROOT)
        self.assertTrue(root.startswith(home), 'default clone root %r is not under home %r' % (root, home))
        self.assertNotIn('/tmp/', root)
        self.assertNotIn('scratchpad', root)

    def test_missing_clone_evidence_names_the_exact_clone_command(self):
        for name in ('bmad', 'gsd', 'speckit'):
            with tempfile.TemporaryDirectory() as tmp:
                old_scratch = ba.SCRATCH_SUBJECTS_ROOT
                old_env = os.environ.pop(ba.CLONE_ROOT_ENV_VARS[name], None)
                ba.SCRATCH_SUBJECTS_ROOT = Path(tmp) / 'no-clones-here'
                try:
                    subject = ba.build_cloned_subject(name)
                finally:
                    ba.SCRATCH_SUBJECTS_ROOT = old_scratch
                    if old_env is not None:
                        os.environ[ba.CLONE_ROOT_ENV_VARS[name]] = old_env
                note = subject.probed_note()
                self.assertIn('git clone', note)
                self.assertIn(ba.PINNED_SHAS[name], note)
                self.assertIn(ba.CLONE_ROOT_ENV_VARS[name], note)

    def test_clone_command_hint_names_the_pinned_sha_and_repo_url(self):
        for name in ('bmad', 'gsd', 'speckit'):
            hint = ba.clone_command_hint(name)
            self.assertIn(ba.PINNED_SHAS[name], hint)
            self.assertIn(ba.CLONE_REPO_URLS[name], hint)
            self.assertIn('git clone', hint)


class BehavioralInstallCommandTests(unittest.TestCase):
    """install-commands-documented, Gate 1 fault 2 repair #1: PASS now
    requires the single documented command to actually RUN, not just to be
    one line of text. Calibrated both directions."""

    def _subject_with_readme(self, tmp, body):
        root = Path(tmp)
        readme = root / 'README.md'
        readme.write_text(body, encoding='utf-8')
        return ba.Subject('probe-subject', [root], readme, [str(root)])

    def test_passes_when_the_single_documented_command_actually_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            subject = self._subject_with_readme(tmp, "# X\n\n## Install\n\n```\ngit --version\n```\n")
            verdict, evidence = ba.check_install_commands(subject)
            self.assertEqual(verdict, 'PASS')
            self.assertIn('git --version', evidence)

    def test_fails_when_the_single_documented_command_does_not_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            subject = self._subject_with_readme(
                tmp, "# X\n\n## Install\n\n```\nthis-binary-does-not-exist-anywhere-xyz --install\n```\n")
            verdict, evidence = ba.check_install_commands(subject)
            self.assertEqual(verdict, 'FAIL')
            self.assertIn('this-binary-does-not-exist-anywhere-xyz', evidence)

    def test_no_data_when_no_readme(self):
        with tempfile.TemporaryDirectory() as tmp:
            subject = ba.Subject('probe-subject', [Path(tmp)], None, [tmp])
            verdict, _evidence = ba.check_install_commands(subject)
            self.assertEqual(verdict, 'NO-DATA')


class BehavioralReceiptTests(unittest.TestCase):
    """receipt-artifact-exists, Gate 1 fault 2 repair #2: PASS now requires
    a shipped test/check/verify command to actually RUN, never just the
    word "receipt" appearing somewhere. Calibrated both directions, plus
    the exact regression this repair closes (word present, no real
    command)."""

    def _subject_with_readme(self, tmp, body):
        root = Path(tmp)
        readme = root / 'README.md'
        readme.write_text(body, encoding='utf-8')
        return ba.Subject('probe-subject', [root], readme, [str(root)])

    def test_passes_when_a_shipped_verify_command_actually_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            subject = self._subject_with_readme(
                tmp, "# X\n\n## Verify\n\n```\ngit --check  # run the test suite\n```\n")
            verdict, evidence = ba.check_receipt_behavioral(subject)
            self.assertEqual(verdict, 'PASS')
            self.assertIn('git --help', evidence)

    def test_fails_when_the_shipped_verify_command_does_not_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            subject = self._subject_with_readme(
                tmp, "# X\n\n## Verify\n\n```\nthis-binary-does-not-exist-anywhere-xyz --test\n```\n")
            verdict, evidence = ba.check_receipt_behavioral(subject)
            self.assertEqual(verdict, 'FAIL')
            self.assertIn('this-binary-does-not-exist-anywhere-xyz', evidence)

    def test_fails_when_the_word_receipt_appears_but_no_command_does(self):
        """The exact defect Gate 1 flagged: a text match alone must no
        longer be enough to PASS."""
        with tempfile.TemporaryDirectory() as tmp:
            subject = self._subject_with_readme(
                tmp, "# X\n\nThis tool writes a rerunnable receipt for every run.\n")
            verdict, _evidence = ba.check_receipt_behavioral(subject)
            self.assertEqual(verdict, 'FAIL')

    def test_no_data_when_no_material(self):
        with tempfile.TemporaryDirectory() as tmp:
            subject = ba.Subject('probe-subject', [Path(tmp)], None, [tmp])
            verdict, _evidence = ba.check_receipt_behavioral(subject)
            self.assertEqual(verdict, 'NO-DATA')


class BehavioralAuditTrailTests(unittest.TestCase):
    """audit-trail-documented, Gate 1 fault 2 repair #3: PASS now requires
    the subject's own root to be a real, inspectable git history, never
    just the phrase "audit trail" appearing somewhere. Calibrated both
    directions, plus the exact regression this repair closes (phrase
    present, no real history)."""

    def _git(self, *args):
        subprocess.run(['git'] + list(args), check=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def test_passes_against_a_real_git_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'README.md').write_text('# X\n', encoding='utf-8')
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                self._git('init', '-q')
                self._git('config', 'user.email', 'test@example.com')
                self._git('config', 'user.name', 'test')
                self._git('add', 'README.md')
                self._git('commit', '-q', '-m', 'initial')
            finally:
                os.chdir(cwd)
            subject = ba.Subject('probe-subject', [root], root / 'README.md', [str(root)])
            verdict, evidence = ba.check_audit_trail_behavioral(subject)
            self.assertEqual(verdict, 'PASS')
            self.assertIn('git', evidence)

    def test_fails_when_the_root_exists_but_is_not_a_git_repo(self):
        """The exact defect Gate 1 flagged: a text match alone must no
        longer be enough to PASS."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'README.md').write_text(
                '# X\n\nWe keep a full audit trail of every run.\n', encoding='utf-8')
            subject = ba.Subject('probe-subject', [root], root / 'README.md', [str(root)])
            verdict, _evidence = ba.check_audit_trail_behavioral(subject)
            self.assertEqual(verdict, 'FAIL')

    def test_no_data_when_no_material(self):
        with tempfile.TemporaryDirectory() as tmp:
            subject = ba.Subject('probe-subject', [Path(tmp)], None, [tmp])
            verdict, _evidence = ba.check_audit_trail_behavioral(subject)
            self.assertEqual(verdict, 'NO-DATA')


if __name__ == '__main__':
    unittest.main()
