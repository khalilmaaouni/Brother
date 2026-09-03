"""Calibration for scripts/daybook.py (R22.3).

Uses TEMP fixture repos only, never the real BrotherSBE / BrotherModeUp
checkouts, per the brief: a test that reads real siblings could pass today
and fail tomorrow because someone else's decision landed.
"""
import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO_ROOT, 'scripts')
sys.path.insert(0, SCRIPTS)
import daybook  # noqa: E402


def write_json_decision(store_dir, name, title, at=None, choice_name=None):
    os.makedirs(store_dir, exist_ok=True)
    body = {'title': title}
    if at is not None:
        body['decided'] = {'at': at, 'choice_name': choice_name or 'Chosen'}
    with open(os.path.join(store_dir, name + '.json'), 'w', encoding='utf-8') as fh:
        json.dump(body, fh)


def write_sbe_decision(store_dir, entry_id, title, written_at, verdict):
    entry_dir = os.path.join(store_dir, entry_id)
    os.makedirs(entry_dir, exist_ok=True)
    text = (
        '# Decision %s: %s\n\n'
        'INTERNAL-EVAL.\n\n'
        '- id: %s\n'
        '- trigger: forced-close\n'
        '- verdict recorded by the run: %s\n'
        '- written at: %s\n'
    ) % (entry_id, title, entry_id, verdict, written_at)
    with open(os.path.join(entry_dir, 'DECISION.md'), 'w', encoding='utf-8') as fh:
        fh.write(text)


class Collect(unittest.TestCase):
    def test_a_decision_added_to_any_repo_appears_after_regeneration(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_store = os.path.join(tmp, 'brother-decisions')
            sbe_store = os.path.join(tmp, 'sbe-decisions')
            write_json_decision(json_store, 'first', 'First decision', at='2026-08-20')
            write_sbe_decision(sbe_store, '001-old-fail', 'an old one',
                                '2026-08-15T10:00:00Z', 'FAIL')
            sources = [('Brother', json_store, 'json'), ('SBE', sbe_store, 'sbe')]

            entries, nodata = daybook.collect(sources)
            self.assertEqual(nodata, [])
            titles = {e['title'] for e in entries}
            self.assertEqual(titles, {'First decision', 'an old one'})

            # Add a new decision to EACH store and confirm both show up.
            write_json_decision(json_store, 'second', 'Second decision', at='2026-08-29')
            write_sbe_decision(sbe_store, '002-new-waived', 'a new one',
                                '2026-08-29T09:00:00Z', 'WAIVED')

            entries2, nodata2 = daybook.collect(sources)
            self.assertEqual(nodata2, [])
            titles2 = {e['title'] for e in entries2}
            self.assertEqual(titles2,
                              {'First decision', 'an old one', 'Second decision', 'a new one'})

    def test_a_missing_repo_reads_no_data_rather_than_an_empty_feed(self):
        with tempfile.TemporaryDirectory() as tmp:
            present = os.path.join(tmp, 'present-decisions')
            write_json_decision(present, 'only', 'The only decision', at='2026-08-20')
            missing = os.path.join(tmp, 'this-path-does-not-exist')
            sources = [('Present', present, 'json'), ('Missing', missing, 'json')]

            entries, nodata = daybook.collect(sources)
            self.assertEqual(nodata, [('Missing', missing)])
            # The missing repo still yields ONE entry naming it, not silence.
            missing_entries = [e for e in entries if e['source_repo'] == 'Missing']
            self.assertEqual(len(missing_entries), 1)
            self.assertEqual(missing_entries[0]['status'], 'NO-DATA')
            self.assertIn(missing, missing_entries[0]['title'])
            present_entries = [e for e in entries if e['source_repo'] == 'Present']
            self.assertEqual(len(present_entries), 1)

    def test_an_unreadable_repo_argument_is_named_not_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, 'gone')
            entries, nodata = daybook.collect([('OnlySource', missing, 'sbe')])
            self.assertEqual(len(nodata), 1)
            self.assertEqual(nodata[0][0], 'OnlySource')


class Render(unittest.TestCase):
    def test_running_twice_with_no_new_decisions_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_store = os.path.join(tmp, 'brother-decisions')
            write_json_decision(json_store, 'a', 'A decision', at='2026-08-20')
            write_json_decision(json_store, 'b', 'Another decision', at='2026-08-21',
                                 choice_name='Chose B')
            sources = [('Brother', json_store, 'json')]
            out_path = os.path.join(tmp, 'out.html')

            daybook.render(sources, out_path)
            with open(out_path, 'rb') as fh:
                first = fh.read()
            daybook.render(sources, out_path)
            with open(out_path, 'rb') as fh:
                second = fh.read()
            self.assertEqual(first, second)

    def test_the_page_carries_no_em_or_en_dashes(self):
        # Built from chr(), never typed as literal glyphs, so this test file
        # itself carries none of the characters it exists to catch.
        em_dash = chr(0x2014)
        en_dash = chr(0x2013)
        with tempfile.TemporaryDirectory() as tmp:
            json_store = os.path.join(tmp, 'brother-decisions')
            # Title deliberately carries an em dash and an en dash, since the
            # source repositories are not under this script's control.
            title = 'A decision %s with an em dash and 5%s10' % (em_dash, en_dash)
            write_json_decision(json_store, 'a', title, at='2026-08-20')
            sources = [('Brother', json_store, 'json')]
            out_path = os.path.join(tmp, 'out.html')
            daybook.render(sources, out_path)
            with open(out_path, 'rb') as fh:
                data = fh.read().decode('utf-8')
            self.assertNotIn(em_dash, data)
            self.assertNotIn(en_dash, data)

    def test_a_missing_repo_renders_a_no_data_notice_not_silence(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, 'gone')
            out_path = os.path.join(tmp, 'out.html')
            entries, nodata = daybook.render([('Ghost', missing, 'json')], out_path)
            self.assertEqual(nodata, [('Ghost', missing)])
            with open(out_path, 'r', encoding='utf-8') as fh:
                data = fh.read()
            self.assertIn('NO-DATA', data)
            self.assertIn('Ghost', data)

    def test_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_store = os.path.join(tmp, 'brother-decisions')
            write_json_decision(json_store, 'old', 'Old one', at='2026-08-01')
            write_json_decision(json_store, 'new', 'New one', at='2026-08-29')
            entries, _ = daybook.collect([('Brother', json_store, 'json')])
            ordered = daybook.sort_entries(entries)
            self.assertEqual([e['title'] for e in ordered], ['New one', 'Old one'])


class AlertClass(unittest.TestCase):
    def test_exactly_four_buckets_no_fifth(self):
        seen = set()
        for status in ('FAIL', 'WAIVED', 'NO-DATA', 'PARTIAL', 'UNMEASURED',
                       'AWAITING FOUNDER', 'OPEN', 'Remove the rule (Recommended)',
                       'DECIDED', None, ''):
            seen.add(daybook.alert_class(status))
        self.assertEqual(seen, {'st-blocked', 'st-nodata', 'st-flight', 'st-done'})


if __name__ == '__main__':
    unittest.main()
