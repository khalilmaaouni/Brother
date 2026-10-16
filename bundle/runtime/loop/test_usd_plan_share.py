#!/usr/bin/env python3
"""P60: every LANDED line carries the OpenRouter cash plus the Claude plan share.

WHAT WAS WRONG (owner order 2026-10-14 23h3x: "I need to include the claude model cost based on a 20x account at 220 usd
per month"): land_batch.usd_text printed the OpenRouter cash and, for a native build, the Claude calls at API prices,
which is not what Claude costs: the plan is a flat monthly price. The LANDED line now reads
usd TOTAL (openrouter N.NN, claude plan N.NN), the plan part being BROTHER_CLAUDE_PLAN_USD_MONTH (default 220) over the
days of the month, split evenly over that day's landings, the landing being written counted.

THE ENTRY POINT. usd_text is driven directly with fixture runs folders and ledgers in a temp dir and a fixed clock (now=),
as the LANDED line calls it with batch=; one case runs land_batch's main through test_land_batch_landings.Fixture and reads
the LANDED line itself. Hermetic: temp dirs only, the environment passed in, no network. Standard library only.
Run: python3 -B scripts/loop/test_usd_plan_share.py
"""
import datetime, os, re, shutil, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import land_batch as LB                     # noqa: E402
import test_land_batch_h6c as H             # noqa: E402  the provider ledger fixture the OpenRouter part is tested on
import test_land_batch_landings as LL       # noqa: E402

NOW = datetime.datetime(2026, 10, 16, 3, 0, 0)   # October: 31 days
ENV = {}                                         # unset: the default 220


def stamp(*parts):
    """mark_landed's own time format for a fixture landing."""
    return datetime.datetime(*parts).strftime('%Y-%m-%d %H:%M:%S')


class PlanShare(H.Base):
    def setUp(self):
        super().setUp()
        self.add_run()                           # D1.1-000001 with one graded build
        self.add_ledger(self.good_pair())        # its OpenRouter cash: 0.03
        self.claude = os.path.join(self.root, 'no-claude.jsonl')

    def landed(self, name, at):
        H.write(os.path.join(self.runs, name, 'STATUS'), 'LANDED %s/round0/out/x-build.json at %s commit abc123\n' % (name, at))

    def text(self, sub='D1.1', batch=('D1.1',), env=ENV, now=NOW, runs=None):
        return LB.usd_text(sub, runs or self.runs, self.ledger, claude_path=self.claude, batch=list(batch), now=now, env=env)

    # --- the share ------------------------------------------------------------------------------------------------------
    def test_two_landings_that_day_each_read_3_55(self):
        self.landed('X1.a-010000', stamp(2026, 10, 16, 1))   # the day's earlier landing
        self.assertEqual(self.text(), 'usd 3.58 (openrouter 0.03, claude plan 3.55)')

    def test_two_sub_units_landing_together_each_read_3_55(self):
        H.write(os.path.join(self.runs, 'D1.2-000001', 'STATUS'), 'READY x (round 0)\n')   # not landed yet: not counted
        for sub in ('D1.1', 'D1.2'):
            self.assertIn('claude plan 3.55)', self.text(sub, batch=('D1.1', 'D1.2')))

    def test_the_first_landing_of_a_day_carries_the_whole_daily_share(self):
        self.landed('X1.a-230000', stamp(2026, 10, 15, 23))   # yesterday's: not this day's
        self.assertEqual(self.text(), 'usd 7.13 (openrouter 0.03, claude plan 7.10)')

    def test_the_month_and_the_price_are_read_not_assumed(self):
        self.assertIn('claude plan 7.86)', self.text(now=datetime.datetime(2026, 2, 10, 12)))   # 220 / 28
        self.assertIn('claude plan 9.68)', self.text(env={'BROTHER_CLAUDE_PLAN_USD_MONTH': '300'}))   # 300 / 31

    # --- NO-DATA, never a default or a guess ----------------------------------------------------------------------------
    def test_a_price_that_is_not_a_positive_finite_number_is_no_data(self):
        for raw in ('abc', '', '0', '-220', 'nan', 'inf'):
            with self.subTest(raw=raw):
                out = self.text(env={'BROTHER_CLAUDE_PLAN_USD_MONTH': raw})
                self.assertTrue(out.startswith('usd NO-DATA (openrouter 0.03, claude plan NO-DATA: '), out)
                self.assertNotIn('7.10', out)

    def test_an_unreadable_landing_ledger_is_no_data(self):
        os.makedirs(os.path.join(self.runs, 'X1.a-010000', 'STATUS'))   # a STATUS that cannot be read as a file
        self.assertTrue(self.text().startswith('usd NO-DATA (openrouter 0.03, claude plan NO-DATA: STATUS of X1.a-010000'), self.text())

    def test_a_landed_line_with_no_readable_date_is_no_data(self):
        H.write(os.path.join(self.runs, 'X1.a-010000', 'STATUS'), 'LANDED x at yesterday commit abc\n')
        self.assertIn('claude plan NO-DATA: STATUS of X1.a-010000 says LANDED with no readable date', self.text())

    def test_a_missing_runs_folder_is_no_data_on_both_parts(self):
        self.assertEqual(self.text(runs=os.path.join(self.root, 'gone')),
                         'usd NO-DATA (openrouter NO-DATA, claude plan NO-DATA: the runs folder could not be read (FileNotFoundError))')

    def test_landings_are_counted_in_the_one_runs_folder_the_statuses_name(self):
        st = os.path.join(self.runs, 'D1.1-000001', 'STATUS')
        self.assertEqual(LB.landings_root([st]), os.path.abspath(self.runs))
        other = os.path.join(self.root, 'elsewhere', 'D1.2-000001', 'STATUS')
        for bad in ([st, other], [], None):
            with self.subTest(bad=bad):
                self.assertEqual(LB.landings_root(bad), '')
        out = LB.usd_text('D1.1', self.runs, self.ledger, claude_path=self.claude, batch=['D1.1'], now=NOW, env=ENV,
                          landings=LB.landings_root([st, other]))
        self.assertTrue(out.startswith('usd NO-DATA (openrouter 0.03, claude plan NO-DATA: the runs folder could not be read'), out)

    def test_a_sub_unit_outside_its_batch_has_no_share(self):
        self.assertIn('claude plan NO-DATA: no landing batch names', self.text(batch=('D9.9',)))

    # --- the OpenRouter part, exactly as before -------------------------------------------------------------------------
    def test_the_openrouter_part_is_todays_figure(self):
        before = LB.usd_text('D1.1', self.runs, self.ledger, claude_path=self.claude)
        self.assertEqual(before, 'usd 0.03')
        self.assertIn('(openrouter %s, ' % before[4:], self.text())
        missing = os.path.join(self.root, 'missing.jsonl')
        self.assertEqual(LB.usd_text('D1.1', self.runs, missing, claude_path=self.claude), 'usd NO-DATA')
        self.assertEqual(LB.usd_text('D1.1', self.runs, missing, claude_path=self.claude, batch=['D1.1'], now=NOW, env=ENV),
                         'usd NO-DATA (openrouter NO-DATA, claude plan 7.10)')

    def test_corrupt_provider_rows_are_still_named(self):
        self.add_ledger(['not json'] + self.good_pair())
        self.assertTrue(self.text().endswith(' (1 corrupt row(s) skipped)'), self.text())


class LandedLine(unittest.TestCase):
    def test_the_landed_line_main_prints_carries_both_parts(self):
        root = tempfile.mkdtemp(prefix='p60-landed-')
        self.addCleanup(shutil.rmtree, root, True)
        f = LL.Fixture(os.path.join(root, 'f'), extra_env={'BROTHER_CLAUDE_PLAN_USD_MONTH': '220'})
        f.edits({'edits': [{'path': 'docs/landed-u1a.txt', 'new_file_content': 'landed\n'}], 'tests': []})
        rc, out = f.land()
        self.assertEqual(rc, 0, out)
        line = [l for l in out.splitlines() if l.startswith('LANDED  ')]
        self.assertEqual(len(line), 1, out)
        m = re.search(r'%s usd \S+ \(openrouter \S+, claude plan (\d+\.\d\d)\)' % re.escape(LL.SUB), line[0])
        self.assertIsNotNone(m, line[0])
        self.assertIn(m.group(1), ['%.2f' % (220.0 / d) for d in (28, 29, 30, 31)], 'the only landing of its day: the whole share')


if __name__ == '__main__':
    unittest.main()
