"""Calibration for scripts/gen_readiness_board.py.

Proves the validator FAILS as well as passes. A board that renders whatever it
is given is not a control, and this estate has shipped three checks this week
that could only go green.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO_ROOT, 'scripts')
SCRIPT = os.path.join(SCRIPTS, 'gen_readiness_board.py')
sys.path.insert(0, SCRIPTS)
import gen_readiness_board as board  # noqa: E402


def doc(rows=None, gates=None):
    return {
        'gates': gates if gates is not None else [
            {'id': 'G1', 'title': 'g', 'size': 's', 'status': 'OPEN', 'blocker': 'b'}],
        'rows': rows if rows is not None else [
            {'id': 'R1', 'gate': 'G1', 'wave': 1, 'title': 't', 'detail': 'd',
             'depends_on': [], 'owner': 'o', 'status': 'DONE',
             'done_check': 'c', 'watchdog_verify': 'v', 'owns': [],
             # The row contract, added 2026-08-29. Every fixture below carries
             # it, because a fixture that fails the contract would make every
             # OTHER assertion in this file fail for the wrong reason.
             'ships': 's', 'role': 'r', 'why_now': 'w', 'effect': 'e',
             'visible_when': 'when', 'persona': 'P1-BA', 'their_moment': 'm',
             'what_they_see': 'sees'}],
    }


class Validator(unittest.TestCase):
    def test_a_coherent_roadmap_has_no_problems(self):
        self.assertEqual(board.validate(doc()), [])

    def test_a_dangling_dependency_is_caught(self):
        d = doc()
        d['rows'][0]['depends_on'] = ['R99']
        self.assertTrue(any('R99' in p for p in board.validate(d)))

    def test_a_row_naming_a_gate_that_does_not_exist_is_caught(self):
        d = doc()
        d['rows'][0]['gate'] = 'G99'
        self.assertTrue(any('G99' in p for p in board.validate(d)))

    def test_a_row_with_no_done_check_is_caught(self):
        """A row that cannot close honestly must not render as if it could."""
        d = doc()
        d['rows'][0]['done_check'] = ''
        self.assertTrue(any('done_check' in p for p in board.validate(d)))

    def test_an_unknown_status_is_caught_not_silently_defaulted(self):
        d = doc()
        d['rows'][0]['status'] = 'NEARLY DONE'
        self.assertTrue(any('NEARLY DONE' in p for p in board.validate(d)))


class ReadySet(unittest.TestCase):
    def test_a_row_with_no_dependencies_is_ready(self):
        d = doc(rows=[{'id': 'R1', 'gate': 'G1', 'wave': 1, 'title': 't', 'detail': '',
                       'depends_on': [], 'owner': 'o', 'status': 'SCHEDULED',
                       'done_check': 'c', 'watchdog_verify': 'v', 'owns': []}])
        self.assertEqual(board.ready_rows(d), ['R1'])

    def test_a_row_whose_dependency_is_open_is_NOT_ready(self):
        """The direction that matters. A wave arriving is not readiness."""
        d = doc(rows=[
            {'id': 'R1', 'gate': 'G1', 'wave': 1, 'title': 't', 'detail': '', 'depends_on': [],
             'owner': 'o', 'status': 'SCHEDULED', 'done_check': 'c', 'watchdog_verify': 'v', 'owns': [],
             'ships': 's', 'role': 'r', 'why_now': 'w', 'effect': 'e', 'visible_when': 'when',
             'persona': 'P1-BA', 'their_moment': 'm', 'what_they_see': 'sees'},
            {'id': 'R2', 'gate': 'G1', 'wave': 2, 'title': 't', 'detail': '', 'depends_on': ['R1'],
             'owner': 'o', 'status': 'SCHEDULED', 'done_check': 'c', 'watchdog_verify': 'v', 'owns': [],
             'ships': 's', 'role': 'r', 'why_now': 'w', 'effect': 'e', 'visible_when': 'when',
             'persona': 'P1-BA', 'their_moment': 'm', 'what_they_see': 'sees'}])
        self.assertNotIn('R2', board.ready_rows(d))

    def test_a_row_becomes_ready_when_its_dependency_closes(self):
        d = doc(rows=[
            {'id': 'R1', 'gate': 'G1', 'wave': 1, 'title': 't', 'detail': '', 'depends_on': [],
             'owner': 'o', 'status': 'DONE', 'done_check': 'c', 'watchdog_verify': 'v', 'owns': [],
             'ships': 's', 'role': 'r', 'why_now': 'w', 'effect': 'e', 'visible_when': 'when',
             'persona': 'P1-BA', 'their_moment': 'm', 'what_they_see': 'sees'},
            {'id': 'R2', 'gate': 'G1', 'wave': 2, 'title': 't', 'detail': '', 'depends_on': ['R1'],
             'owner': 'o', 'status': 'SCHEDULED', 'done_check': 'c', 'watchdog_verify': 'v', 'owns': [],
             'ships': 's', 'role': 'r', 'why_now': 'w', 'effect': 'e', 'visible_when': 'when',
             'persona': 'P1-BA', 'their_moment': 'm', 'what_they_see': 'sees'}])
        self.assertIn('R2', board.ready_rows(d))

    def test_an_in_flight_row_is_not_offered_as_ready(self):
        """Somebody is already on it; offering it again invites two writers."""
        d = doc(rows=[{'id': 'R1', 'gate': 'G1', 'wave': 1, 'title': 't', 'detail': '',
                       'depends_on': [], 'owner': 'o', 'status': 'IN-FLIGHT',
                       'done_check': 'c', 'watchdog_verify': 'v', 'owns': [],
                       'ships': 's', 'role': 'r', 'why_now': 'w', 'effect': 'e',
                       'visible_when': 'when', 'persona': 'P1-BA', 'their_moment': 'm',
                       'what_they_see': 'sees'}])
        self.assertEqual(board.ready_rows(d), [])


class EndToEnd(unittest.TestCase):
    def run_script(self, *args):
        p = subprocess.run([sys.executable, SCRIPT] + list(args),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True)
        return p.returncode, p.stdout + p.stderr

    def test_the_real_roadmap_is_coherent(self):
        code, out = self.run_script('--check')
        self.assertEqual(code, 0, out)

    def test_an_unreadable_source_is_NO_DATA_not_a_pass(self):
        """Exit 2, its own verdict. Could-not-read is never measured-and-fine."""
        saved = board.SOURCE
        try:
            board.SOURCE = os.path.join(tempfile.gettempdir(), 'no-such-roadmap-xyz.json')
            self.assertEqual(board.main(['--check']), 2)
        finally:
            board.SOURCE = saved

    def test_malformed_json_is_NO_DATA_not_a_crash(self):
        saved = board.SOURCE
        fh = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        fh.write('{not json')
        fh.close()
        try:
            board.SOURCE = fh.name
            self.assertEqual(board.main(['--check']), 2)
        finally:
            board.SOURCE = saved
            os.unlink(fh.name)

    def test_an_incoherent_roadmap_FAILS_rather_than_rendering(self):
        saved = board.SOURCE
        d = doc()
        d['rows'][0]['depends_on'] = ['R99']
        fh = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        json.dump(d, fh)
        fh.close()
        try:
            board.SOURCE = fh.name
            self.assertEqual(board.main(['--check']), 1)
        finally:
            board.SOURCE = saved
            os.unlink(fh.name)

    def test_every_row_in_the_real_roadmap_carries_a_done_check(self):
        """The founder's 0 of 5 came from a row closed against something other
        than its own done_check. A row with no done_check cannot even try."""
        d = board.load()
        missing = [r['id'] for r in d['rows'] if not r.get('done_check')]
        self.assertEqual(missing, [], 'rows with no done_check: %s' % missing)

    def test_the_rendered_board_contains_no_em_or_en_dash(self):
        code, _ = self.run_script()
        self.assertEqual(code, 0)
        with open(board.OUTPUT, encoding='utf-8') as fh:
            text = fh.read()
        # Written as escapes, never as literals. The first draft of this test
        # embedded the two characters directly and cleanse.sh, which scans the
        # whole tree, refused the commit: the dash checker contained the dashes
        # it checks for.
        self.assertNotIn('\u2014', text)   # em dash
        self.assertNotIn('\u2013', text)   # en dash

class RowContract(unittest.TestCase):
    """Founder rule 2026-08-29: a row must say what SHIPS, its role, why it is
    prioritised here, the effect somebody will observe, and when that effect
    appears. Enforced rather than encouraged, because this estate has watched a
    rule with no file behind it degrade to optional under time pressure more than
    once in a single day."""

    def contracted(self, **over):
        r = {'id': 'R1', 'gate': 'G1', 'wave': 1, 'title': 't', 'detail': 'd',
             'depends_on': [], 'owner': 'o', 'status': 'DONE', 'done_check': 'c',
             'watchdog_verify': 'v', 'owns': [],
             'ships': 's', 'role': 'r', 'why_now': 'w', 'effect': 'e',
             'visible_when': 'when', 'persona': 'P1-BA', 'their_moment': 'm',
             'what_they_see': 'sees'}
        r.update(over)
        return {'gates': [{'id': 'G1', 'title': 'g', 'size': 's',
                           'status': 'OPEN', 'blocker': 'b'}], 'rows': [r]}

    def test_a_fully_contracted_row_passes(self):
        self.assertEqual(board.validate(self.contracted()), [])

    def test_each_missing_field_is_caught_by_name(self):
        for field in board.ROW_CONTRACT_FIELDS:
            probs = board.validate(self.contracted(**{field: ''}))
            self.assertTrue(any(repr(field) in p for p in probs),
                            '%s was not caught' % field)

    def test_a_whitespace_only_field_does_not_satisfy_the_contract(self):
        """A space is not an explanation, and this is the cheapest way to
        satisfy a required field without saying anything."""
        probs = board.validate(self.contracted(ships='   '))
        self.assertTrue(any('ships' in p for p in probs))

    def test_the_board_REFUSES_to_render_an_abstract_row(self):
        d = self.contracted(effect='')
        fh = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        json.dump(d, fh)
        fh.close()
        saved = board.SOURCE
        try:
            board.SOURCE = fh.name
            self.assertEqual(board.main(['--check']), 1)
        finally:
            board.SOURCE = saved
            os.unlink(fh.name)

    def test_every_row_in_the_REAL_roadmap_honours_the_contract(self):
        doc = board.load()
        bad = [(r['id'], f) for r in doc['rows'] for f in board.ROW_CONTRACT_FIELDS
               if not str(r.get(f) or '').strip()]
        self.assertEqual(bad, [], 'rows missing contract fields: %s' % bad)

    def test_visible_when_is_a_separate_field_from_delivered_at(self):
        """A control's effect often appears later than its delivery. Conflating
        the two reports a capability as live when only its code is."""
        self.assertIn('visible_when', board.ROW_CONTRACT_FIELDS)
        self.assertNotIn('delivered_at', board.ROW_CONTRACT_FIELDS)

class DesignDoctrine(unittest.TestCase):
    """Founder doctrine 2026-08-29: simplicity by adding just enough, art is
    about removing, success is saying no to most things, and the weight of each
    decision matters. Enforced, because a doctrine nothing checks is a poster."""

    def test_a_feature_missing_what_it_removes_is_refused(self):
        d = board.load()
        d['features'][0]['removes'] = ''
        self.assertTrue(any('removes' in p for p in board.validate(d)))

    def test_a_feature_with_no_real_world_grounding_is_refused(self):
        """A feature justified only by internal elegance has no physics."""
        d = board.load()
        d['features'][0]['grounded_in'] = ''
        self.assertTrue(any('grounded_in' in p for p in board.validate(d)))

    def test_a_feature_stating_no_weight_is_refused(self):
        """A cheap reversible bet and a one way door must not look the same."""
        d = board.load()
        d['features'][0]['weight'] = ''
        self.assertTrue(any('weight' in p for p in board.validate(d)))

    def test_a_feature_that_names_no_borrow_is_refused(self):
        d = board.load()
        d['features'][0]['borrowed_from'] = ''
        self.assertTrue(any('borrowed_from' in p for p in board.validate(d)))

    def test_a_refusal_with_no_flip_condition_is_refused(self):
        """A refusal with no flip condition is a grudge, not a decision."""
        d = board.load()
        d['refused'][0]['flip_condition'] = ''
        self.assertTrue(any('flip' in p for p in board.validate(d)))

    def test_the_no_list_is_not_empty(self):
        """Success is saying no to most things. A board with an empty NO list
        has not made a decision, it has made a wish list."""
        d = board.load()
        self.assertGreater(len(d.get('refused', [])), 0)

    def test_every_real_feature_honours_the_full_doctrine(self):
        d = board.load()
        bad = [(f['id'], k) for f in d['features']
               for k in board.FEATURE_CONTRACT_FIELDS
               if not str(f.get(k) or '').strip()]
        self.assertEqual(bad, [], 'features missing doctrine fields: %s' % bad)

    def test_every_refusal_names_what_it_would_take_to_reopen(self):
        d = board.load()
        bad = [x['id'] for x in d.get('refused', [])
               if not str(x.get('flip_condition') or '').strip()]
        self.assertEqual(bad, [])


class TheBoardShowsItsCompass(unittest.TestCase):
    """The progress page law requires a north star callout as its own section,
    and this board rendered NONE for its whole life. That is not cosmetic: an
    evening of reprioritisation ordered engineering work beautifully while a
    second board carried the true measure, which says no engineering moves it.
    A board whose compass is implicit can be perfectly ordered and pointed at
    the wrong pile."""

    def test_the_board_data_carries_a_north_star(self):
        doc = board.load()
        self.assertIn("north_star", doc, "the board has no compass at all")
        self.assertTrue(str(doc["north_star"].get("metric", "")).strip())

    def test_the_rendered_page_shows_it(self):
        html = board.render(board.load())
        self.assertIn("The north star", html)
        self.assertIn("northstar", html)

    def test_the_current_value_is_stated_even_when_it_is_zero(self):
        """Especially when it is zero. A compass that only appears once it reads
        well is decoration."""
        doc = board.load()
        self.assertTrue(str(doc["north_star"].get("current_value", "")).strip())

    def test_it_says_why_engineering_does_not_move_it(self):
        """The honest constraint, kept visible on purpose: the metric needs a
        person to accept a result, and no engineering closes that."""
        doc = board.load()
        why = doc["north_star"].get("why_engineering_does_not_move_it", "")
        self.assertTrue(str(why).strip())

    def test_a_board_with_no_north_star_still_renders_rather_than_crashing(self):
        """A missing compass must be a visible absence, never a broken page: a
        renderer that dies takes the whole board away over one missing key."""
        doc = board.load()
        doc.pop("north_star", None)
        html = board.render(doc)
        # ASSERT ON THE SECTION, NOT ON WORDS THAT CAN APPEAR ANYWHERE. This
        # read `assertNotIn("The north star", html)` and went red the moment a
        # ROW's own prose said the words "The north star reads zero external
        # deliveries per week", which is a true sentence in a row and nothing to
        # do with whether the compass section rendered. A test that fails on
        # correct content is a test that gets edited around. The heading is the
        # thing this cares about, so the heading is what it looks for.
        self.assertNotIn("<h2>The north star</h2>", html)
        self.assertIn("The ledger", html)


class TheLearningSectionHoldsItsOwnLine(unittest.TestCase):
    """Added 2026-08-29 on founder direction. A section ABOUT learning is the
    last place an unverified claim or a dead link may sit, so it is guarded the
    same way the decision screens are."""

    def ll(self):
        return board.load().get('learning_loop') or {}

    def test_the_section_exists_and_renders(self):
        self.assertTrue(self.ll(), 'no learning_loop section in the board data')
        self.assertIn('The three failures, which are not one failure',
                      board.render(board.load()))

    def test_a_board_without_it_still_renders(self):
        """A missing section must be a visible absence, never a broken page."""
        doc = board.load()
        doc.pop('learning_loop', None)
        html = board.render(doc)
        self.assertNotIn('The three failures, which are not one failure', html)
        self.assertIn('The ledger', html)

    def test_every_borrowed_source_was_actually_checked(self):
        """The guard against a plausible URL written from memory. A page about
        learning that cites a dead link has taught the wrong thing twice."""
        bad = []
        for b in self.ll().get('borrowed') or []:
            if not str(b.get('url', '')).startswith('https://'):
                bad.append('not https: %r' % b.get('discipline'))
            if not b.get('checked'):
                bad.append('unchecked: %r' % b.get('discipline'))
        self.assertEqual(bad, [], 'borrowed sources not verified: %s' % bad)

    def test_every_borrowed_discipline_says_how_it_applies_HERE(self):
        """A discipline listed without a use is decoration, and decoration on a
        page about learning is exactly the thing being corrected."""
        for b in self.ll().get('borrowed') or []:
            self.assertTrue(str(b.get('how_it_applies', '')).strip(),
                            b.get('discipline'))

    def test_every_priority_item_carries_a_done_check_or_says_NOT_BUILT(self):
        for it in self.ll().get('priority') or []:
            dc = str(it.get('done_check', ''))
            self.assertTrue(dc.strip(), it.get('item'))
            if it.get('state') == 'DONE':
                self.assertNotIn('NOT BUILT', dc, it.get('item'))

    def test_an_open_item_is_not_dressed_as_finished(self):
        """The failure this estate keeps making: an intention written as though
        it were a control. An OPEN item must say NOT BUILT in its own check."""
        for it in self.ll().get('priority') or []:
            if str(it.get('state', '')).startswith('OPEN'):
                self.assertIn('NOT BUILT', str(it.get('done_check', '')),
                              it.get('item'))

    def test_every_failure_mode_names_its_state_and_its_evidence(self):
        modes = self.ll().get('modes') or []
        self.assertTrue(modes)
        for m in modes:
            self.assertTrue(str(m.get('state', '')).strip(), m.get('id'))
            self.assertTrue(str(m.get('evidence', '')).strip(), m.get('id'))


class EverySeriesInTheDataReachesThePage(unittest.TestCase):
    """THE EXACT FAILURE THIS GUARDS, 2026-08-29. The founder asked whether his
    team's requests were on the board. Three series sat in the board's own data
    with a renderer that never referenced the key, and a fourth had never been
    connected at all, so none of the twenty eight was visible. He found it by
    asking.

    A record that is right while the page is silent is the failure mode this
    estate keeps repeating, and the only durable fix is a test that fails when
    data exists and does not render."""

    def test_the_team_asks_section_renders(self):
        html = board.render(board.load())
        self.assertIn('What the team actually asked for', html)

    def test_every_reviewer_problem_reaches_the_page(self):
        doc = board.load()
        P = ((doc.get('team_complaints') or {})
             .get('P_series_verified_2026_08_29') or {})
        self.assertTrue(P, 'the reviewer series vanished from the data')
        html = board.render(doc)
        missing = [k for k in P if ('>%s<' % k) not in html]
        self.assertEqual(missing, [], 'raised but not shown: %s' % missing)

    def test_every_hole_reaches_the_page(self):
        doc = board.load()
        H = ((doc.get('team_complaints') or {})
             .get('H_series_the_holes_nobody_complained_about') or {}).get('holes') or {}
        self.assertTrue(H, 'the nine holes vanished from the data')
        html = board.render(doc)
        missing = [k for k in H if ('>%s<' % k) not in html]
        self.assertEqual(missing, [], 'raised but not shown: %s' % missing)

    def test_the_rollup_total_matches_the_series_it_counts(self):
        """A headline number that drifts from the rows under it is worse than
        no headline number."""
        tc = board.load().get('team_complaints') or {}
        counted = (tc.get('rollup_2026_08_29') or {}).get('counted') or {}
        self.assertEqual(counted['P_reviewer_problems']['total'],
                         len(tc.get('P_series_verified_2026_08_29') or {}))
        self.assertEqual(
            counted['H_holes']['total'],
            len((tc.get('H_series_the_holes_nobody_complained_about') or {})
                .get('holes') or {}))

    def test_each_series_total_equals_its_own_breakdown(self):
        counted = ((board.load().get('team_complaints') or {})
                   .get('rollup_2026_08_29') or {}).get('counted') or {}
        for name, c in counted.items():
            self.assertEqual(
                c['addressed'] + c['partial'] + c['not_addressed'], c['total'], name)

    def test_a_verdict_is_shown_for_every_reviewer_problem(self):
        """Listing an ask without saying where it stands is the shape of a
        board that looks responsive and answers nothing."""
        doc = board.load()
        P = ((doc.get('team_complaints') or {})
             .get('P_series_verified_2026_08_29') or {})
        for k, v in P.items():
            self.assertIn(str(v.get('verdict', '')).upper(),
                          ('ADDRESSED', 'PARTIAL', 'NOT-ADDRESSED', 'NO-DATA'), k)


class TheVaultStrip(unittest.TestCase):
    """WBS V12: 'lessons recalled this week, receipts bound, notes written',
    read from board_status.vault_counters() and rendered with the exact
    command that produced each number. board.BS is the same board_status
    module gen_readiness_board.py imports to render the page, so patching
    its vault_counters is patching the one function render() calls."""

    def test_the_real_board_carries_the_vault_strip(self):
        html = board.render(board.load())
        self.assertIn('vault-strip', html)
        self.assertIn('lessons recalled this week', html)
        self.assertIn('receipts bound', html)
        self.assertIn('notes written this week', html)

    def _vault_section(self, html):
        start = html.index('class="strip vault-strip"')
        end = html.index('northstar', start)
        return html[start:end]

    def test_every_vault_counter_is_shown_beside_its_own_command(self):
        """Not typed: a count with no command next to it is exactly the
        theatre this row exists to refuse."""
        html = board.render(board.load())
        section = self._vault_section(html)
        self.assertEqual(section.count('<code>'), 3)

    def test_a_missing_source_renders_NO_DATA_never_a_typed_zero(self):
        fake = [{'label': 'lessons recalled this week', 'count': None,
                'command': 'python3 fake --since x',
                'error': 'no access audit at /no/such/path'}]
        with unittest.mock.patch.object(board.BS, 'vault_counters', return_value=fake):
            html = board.render(board.load())
        section = self._vault_section(html)
        self.assertIn('NO-DATA', section)
        self.assertIn('python3 fake --since x', section)

    def test_a_seeded_count_is_rendered_verbatim(self):
        fake = [{'label': 'receipts bound', 'count': 42,
                'command': 'python3 scripts/board_status.py --vault-counters',
                'error': None}]
        with unittest.mock.patch.object(board.BS, 'vault_counters', return_value=fake):
            html = board.render(board.load())
        section = self._vault_section(html)
        self.assertIn('<span class="v">42</span>', section)
        self.assertNotIn('NO-DATA', section)


if __name__ == '__main__':
    unittest.main()
