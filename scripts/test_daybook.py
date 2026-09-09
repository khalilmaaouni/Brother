"""Calibration for scripts/daybook.py (R22.3, rebuilt as Daybook v2 per the
2026-09-08 debate judgment, unit U9).

Every fixture is an outcome-contract-v1 shaped JSON object built fresh in a
temp directory: never the real docs/decisions/ (57-plus pre-v2 decision
cards live there today and none of them are outcome contracts, which this
suite also exercises directly rather than assumes).
"""
import contextlib
import datetime
import io
import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO_ROOT, 'scripts')
sys.path.insert(0, SCRIPTS)
import daybook  # noqa: E402


def contract(question='A question', state='draft', persona='analyst',
             decision=None, receipts=None, history=None, repo_name='brother-hub'):
    """A minimal outcome-contract-v1 record carrying only the fields
    daybook.py actually reads."""
    body = {
        'schema_version': 'outcome-contract-v1',
        'project': {
            'project_id': 'p1',
            'name': 'Project One',
            'repository': {'name': repo_name},
        },
        'question': question,
        'state': state,
        'persona': persona,
        'receipts': receipts if receipts is not None else [],
        'history': history if history is not None else [],
    }
    if decision is not None:
        body['decision'] = decision
    return body


def write_json(store_dir, name, body):
    os.makedirs(store_dir, exist_ok=True)
    path = os.path.join(store_dir, name + '.json')
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(body, fh)
    return path


class DisplayState(unittest.TestCase):
    def test_each_display_column_gets_its_own_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(tmp, 'a-open', contract(state='draft'))
            write_json(tmp, 'b-planned-open', contract(state='planned'))
            write_json(tmp, 'c-close-call', contract(
                state='in-flight', decision={'close_call': True, 'margin': 0.04}))
            write_json(tmp, 'd-decided', contract(state='delivered'))
            write_json(tmp, 'e-superseded', contract(state='superseded'))

            entries, nodata = daybook.collect(tmp)
            self.assertFalse(nodata)
            by_state = {e['filename']: e['display_state'] for e in entries}
            self.assertEqual(by_state['a-open.json'], 'open')
            self.assertEqual(by_state['b-planned-open.json'], 'open')
            self.assertEqual(by_state['c-close-call.json'], 'close-call')
            self.assertEqual(by_state['d-decided.json'], 'decided')
            self.assertEqual(by_state['e-superseded.json'], 'superseded')

    def test_delivered_wins_over_close_call(self):
        # decided/superseded are their own columns regardless of close_call,
        # per the orchestrator's ruling.
        entry = daybook.build_entry(
            'x.json', contract(state='delivered', decision={'close_call': True}))
        self.assertEqual(entry['display_state'], 'decided')

    def test_a_non_contract_file_lands_in_the_fifth_column_never_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(tmp, 'old-decision-card', {'title': 'A pre-v2 decision card'})
            write_json(tmp, 'real-contract', contract())
            entries, nodata = daybook.collect(tmp)
            self.assertFalse(nodata)
            noncontract = [e for e in entries if not e['is_contract']]
            self.assertEqual(len(noncontract), 1)
            self.assertEqual(noncontract[0]['filename'], 'old-decision-card.json')

            out_path = os.path.join(tmp, 'out.html')
            daybook.render(tmp, out_path)
            with open(out_path, 'r', encoding='utf-8') as fh:
                page = fh.read()
            self.assertIn('old-decision-card.json', page)
            self.assertIn('Not a contract', page)

    def test_inflight_subdirectory_is_swept_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(tmp, 'a', contract(question='top level'))
            write_json(os.path.join(tmp, 'inflight'), 'b', contract(question='draft in flight'))
            entries, nodata = daybook.collect(tmp)
            self.assertFalse(nodata)
            titles = {e['title'] for e in entries}
            self.assertEqual(titles, {'top level', 'draft in flight'})


class PersonaBadge(unittest.TestCase):
    def test_a_known_persona_is_kept(self):
        entry = daybook.build_entry('x.json', contract(persona='developer'))
        self.assertEqual(entry['persona'], 'developer')

    def test_a_missing_or_unknown_persona_reads_no_data(self):
        c = contract()
        del c['persona']
        entry = daybook.build_entry('x.json', c)
        self.assertEqual(entry['persona'], 'NO-DATA')

        entry2 = daybook.build_entry('y.json', contract(persona='engineer'))
        self.assertEqual(entry2['persona'], 'NO-DATA')


class CloseCallBadge(unittest.TestCase):
    def test_close_call_badge_shows_the_margin_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(tmp, 'a', contract(
                state='planned', decision={'close_call': True, 'margin': '0.03'}))
            out_path = os.path.join(tmp, 'out.html')
            daybook.render(tmp, out_path)
            with open(out_path, 'r', encoding='utf-8') as fh:
                page = fh.read()
            self.assertIn('CLOSE CALL', page)
            self.assertIn('0.03', page)

    def test_close_call_false_shows_no_badge(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(tmp, 'a', contract(state='planned', decision={'close_call': False}))
            out_path = os.path.join(tmp, 'out.html')
            daybook.render(tmp, out_path)
            with open(out_path, 'r', encoding='utf-8') as fh:
                page = fh.read()
            self.assertNotIn('CLOSE CALL', page)


class Receipts(unittest.TestCase):
    def test_a_file_ref_in_range_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence = os.path.join(tmp, 'evidence.txt')
            with open(evidence, 'w', encoding='utf-8') as fh:
                fh.write('one line\n')
            r = daybook.resolve_receipt({'id': 'r1', 'ref': 'file:%s:1-1' % evidence})
            self.assertEqual(r['status'], 'RESOLVED')

    def test_a_file_ref_out_of_range_is_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence = os.path.join(tmp, 'evidence.txt')
            with open(evidence, 'w', encoding='utf-8') as fh:
                fh.write('one line\n')
            r = daybook.resolve_receipt({'id': 'r1', 'ref': 'file:%s:5-9' % evidence})
            self.assertEqual(r['status'], 'UNVERIFIED')

    def test_a_missing_file_ref_is_unverified(self):
        r = daybook.resolve_receipt({'id': 'r1', 'ref': 'file:/no/such/path:1-1'})
        self.assertEqual(r['status'], 'UNVERIFIED')

    def test_a_url_ref_is_unverified_with_no_captured_copy(self):
        r = daybook.resolve_receipt({'id': 'r1', 'ref': 'url:https://example.com/x'})
        self.assertEqual(r['status'], 'UNVERIFIED')

    def test_the_pre_amendment_bare_path_shape_is_tolerated(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence = os.path.join(tmp, 'e.txt')
            with open(evidence, 'w', encoding='utf-8') as fh:
                fh.write('x')
            self.assertEqual(
                daybook.resolve_receipt({'id': 'r1', 'path': evidence})['status'], 'RESOLVED')
            self.assertEqual(
                daybook.resolve_receipt({'id': 'r1', 'path': '/no/such'})['status'], 'UNVERIFIED')

    def test_the_count_line_totals_resolved_and_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence = os.path.join(tmp, 'e.txt')
            with open(evidence, 'w', encoding='utf-8') as fh:
                fh.write('line\n')
            write_json(tmp, 'a', contract(receipts=[
                {'id': 'r1', 'ref': 'file:%s:1-1' % evidence},
                {'id': 'r2', 'ref': 'url:https://nowhere.example/y'},
            ]))
            out_path = os.path.join(tmp, 'out.html')
            daybook.render(tmp, out_path)
            with open(out_path, 'r', encoding='utf-8') as fh:
                page = fh.read()
            self.assertIn('1 resolved, 1 unverified.', page)


class QueryFlags(unittest.TestCase):
    def _iso(self, days_ago):
        stamp = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
        return stamp.isoformat()

    def test_since_filters_by_newest_history_stamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(tmp, 'recent', contract(
                question='recent', history=[{'at': self._iso(2), 'by': 'ask', 'note': 'x'}]))
            write_json(tmp, 'stale', contract(
                question='stale', history=[{'at': self._iso(90), 'by': 'ask', 'note': 'x'}]))
            write_json(tmp, 'undated', contract(question='undated', history=[]))
            entries, _ = daybook.collect(tmp)
            filtered = daybook.apply_filters(entries, since_days=30)
            titles = {e['title'] for e in filtered}
            self.assertEqual(titles, {'recent'})

    def test_state_filters_to_the_display_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(tmp, 'a', contract(state='draft', question='a'))
            write_json(tmp, 'b', contract(state='delivered', question='b'))
            entries, _ = daybook.collect(tmp)
            filtered = daybook.apply_filters(entries, state='decided')
            self.assertEqual([e['title'] for e in filtered], ['b'])

    def test_repo_filters_to_project_repository_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(tmp, 'a', contract(repo_name='brother-hub', question='a'))
            write_json(tmp, 'b', contract(repo_name='other-repo', question='b'))
            entries, _ = daybook.collect(tmp)
            filtered = daybook.apply_filters(entries, repo='other-repo')
            self.assertEqual([e['title'] for e in filtered], ['b'])

    def test_persona_filters_to_the_enum_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(tmp, 'a', contract(persona='analyst', question='a'))
            write_json(tmp, 'b', contract(persona='lead', question='b'))
            entries, _ = daybook.collect(tmp)
            filtered = daybook.apply_filters(entries, persona='lead')
            self.assertEqual([e['title'] for e in filtered], ['b'])

    def test_a_non_contract_entry_is_never_hidden_by_a_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(tmp, 'card', {'title': 'not a contract'})
            entries, _ = daybook.collect(tmp)
            filtered = daybook.apply_filters(entries, state='decided', repo='x', persona='lead')
            self.assertEqual(len(filtered), 1)
            self.assertFalse(filtered[0]['is_contract'])

    def test_parse_since_rejects_a_malformed_value(self):
        with self.assertRaises(ValueError):
            daybook.parse_since('30days')


class NoData(unittest.TestCase):
    def test_collect_reports_nodata_for_a_missing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, 'does-not-exist')
            entries, nodata = daybook.collect(missing)
            self.assertEqual(entries, [])
            self.assertTrue(nodata)

    def test_collect_reports_nodata_for_an_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries, nodata = daybook.collect(tmp)
            self.assertEqual(entries, [])
            self.assertTrue(nodata)

    def test_main_exits_2_on_nodata(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, 'does-not-exist')
            out_path = os.path.join(tmp, 'out.html')
            code = daybook.main(['--records', missing, '-o', out_path])
            self.assertEqual(code, 2)

    def test_main_exits_0_when_records_are_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(tmp, 'a', contract())
            out_path = os.path.join(tmp, 'out.html')
            code = daybook.main(['--records', tmp, '-o', out_path])
            self.assertEqual(code, 0)


class RenderSafety(unittest.TestCase):
    def test_the_page_carries_no_em_or_en_dashes(self):
        em_dash = chr(0x2014)
        en_dash = chr(0x2013)
        with tempfile.TemporaryDirectory() as tmp:
            title = 'A question %s with an em dash and 5%s10' % (em_dash, en_dash)
            write_json(tmp, 'a', contract(question=title))
            out_path = os.path.join(tmp, 'out.html')
            daybook.render(tmp, out_path)
            with open(out_path, 'r', encoding='utf-8') as fh:
                page = fh.read()
            self.assertNotIn(em_dash, page)
            self.assertNotIn(en_dash, page)

    def test_no_external_resource_is_loaded_a_url_receipt_is_text_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(tmp, 'a', contract(receipts=[
                {'id': 'r1', 'ref': 'url:https://example.com/evidence'},
            ]))
            out_path = os.path.join(tmp, 'out.html')
            daybook.render(tmp, out_path)
            with open(out_path, 'r', encoding='utf-8') as fh:
                page = fh.read()
            self.assertNotIn('src="http', page)
            self.assertNotIn('href="http', page)
            # the url still appears, but as plain escaped text, not a link.
            self.assertIn('example.com', page)

    def test_running_twice_with_no_changes_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(tmp, 'a', contract())
            out_path = os.path.join(tmp, 'out.html')
            daybook.render(tmp, out_path)
            with open(out_path, 'rb') as fh:
                first = fh.read()
            daybook.render(tmp, out_path)
            with open(out_path, 'rb') as fh:
                second = fh.read()
            self.assertEqual(first, second)


class HonestyBadges(unittest.TestCase):
    def test_a_delivered_record_with_no_decision_and_no_receipts_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(tmp, 'a', contract(state='delivered', decision=None, receipts=[]))
            out_path = os.path.join(tmp, 'out.html')
            daybook.render(tmp, out_path)
            with open(out_path, 'r', encoding='utf-8') as fh:
                page = fh.read()
            self.assertIn('NO DECISION', page)
            self.assertIn('NO RECEIPTS', page)

    def test_a_delivered_record_with_a_decision_and_a_receipt_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(tmp, 'a', contract(
                state='delivered', decision={'close_call': False},
                receipts=[{'ref': 'x.md#L1-L2', 'label': 'x'}]))
            out_path = os.path.join(tmp, 'out.html')
            daybook.render(tmp, out_path)
            with open(out_path, 'r', encoding='utf-8') as fh:
                page = fh.read()
            self.assertNotIn('NO DECISION', page)
            self.assertNotIn('NO RECEIPTS', page)


class MachinePath(unittest.TestCase):
    def test_the_stamp_carries_no_absolute_users_path(self):
        # nested under daybook.ROOT (this checkout's own path), so on a
        # machine where the repo lives under /Users/ (as it does here) the
        # unfixed stamp actually reproduces the bug rather than a temp dir
        # elsewhere that never contained /Users/ in the first place.
        with tempfile.TemporaryDirectory(dir=daybook.ROOT) as tmp:
            write_json(tmp, 'a', contract())
            out_path = os.path.join(tmp, 'out.html')
            daybook.render(tmp, out_path)
            with open(out_path, 'r', encoding='utf-8') as fh:
                page = fh.read()
            self.assertNotIn('/Users/', page)


class CollectFlag(unittest.TestCase):
    """R22.1.2/R22.1.3: --collect prints the collected records as JSON on
    stdout instead of rendering the HTML board, honouring --records and
    the existing query filters."""

    def test_collect_prints_json_with_its_state_and_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(tmp, 'a', contract(state='draft'))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = daybook.main(['--records', tmp, '--collect'])
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]['state'], 'draft')

    def test_collect_over_an_empty_directory_exits_2_with_nodata_on_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = daybook.main(['--records', tmp, '--collect'])
            self.assertEqual(code, 2)
            self.assertIn('NO-DATA', stderr.getvalue())

    def test_collect_over_a_missing_records_dir_names_it_and_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, 'does-not-exist')
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = daybook.main(['--records', missing, '--collect'])
            self.assertEqual(code, 2)
            self.assertIn('NO-DATA', stderr.getvalue())
            self.assertIn(missing, stderr.getvalue())

    def test_collect_honours_the_state_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(tmp, 'a', contract(state='draft', question='a'))
            write_json(tmp, 'b', contract(state='delivered', question='b'))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = daybook.main(['--records', tmp, '--collect', '--state', 'decided'])
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual([p['title'] for p in payload], ['b'])

    def test_collect_does_not_write_the_html_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(tmp, 'a', contract())
            out_path = os.path.join(tmp, 'out.html')
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                daybook.main(['--records', tmp, '--collect', '-o', out_path])
            self.assertFalse(os.path.exists(out_path))


if __name__ == '__main__':
    unittest.main()
