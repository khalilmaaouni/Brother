"""Calibration for scripts/roadmap_merge.py.

The property under test is the reason the driver exists: two lanes that each
touched their OWN row must merge clean, and a genuine disagreement must still
stop. Everything runs on fixtures built here, never on the live roadmap, so
the suite cannot be turned green by the board happening to be in a good
state, and it stays true after the board moves on.

Both directions are driven for every rule: the auto resolve AND the refusal.
A driver that only ever says yes is not a merge driver, it is a preference.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'scripts'))
import roadmap_merge as rm  # noqa: E402

DRIVER = os.path.join(REPO_ROOT, 'scripts', 'roadmap_merge.py')


def row(rid, status='OPEN', evidence='', **extra):
    r = {'id': rid, 'title': 'row ' + rid, 'status': status,
         'evidence': evidence}
    r.update(extra)
    return r


def doc(rows, **top):
    d = {'schema': 1, 'session': 'fixture'}
    d.update(top)
    d['rows'] = rows
    return d


def run_merge(base, ours, theirs):
    """Run merge() capturing the log lines, returning (doc, lines)."""
    lines = []
    merged = rm.merge(base, ours, theirs, lines.append)
    return merged, lines


def ids(merged):
    return [r['id'] for r in merged['rows']]


class OneSideChanges(unittest.TestCase):
    def test_a_row_added_by_one_side_only_is_kept(self):
        base = doc([row('A'), row('B')])
        ours = doc([row('A'), row('B'), row('C')])
        merged, _ = run_merge(base, ours, doc([row('A'), row('B')]))
        self.assertEqual(ids(merged), ['A', 'B', 'C'])

    def test_rows_added_by_both_sides_are_both_kept(self):
        """THE WHOLE POINT. Two lanes each add their own row: git sees two
        edits to the same lines, this sees two disjoint rows."""
        base = doc([row('A')])
        ours = doc([row('A'), row('OURS')])
        theirs = doc([row('A'), row('THEIRS')])
        merged, _ = run_merge(base, ours, theirs)
        self.assertEqual(ids(merged), ['A', 'OURS', 'THEIRS'])

    def test_a_row_changed_by_one_side_only_takes_that_side(self):
        base = doc([row('A', 'OPEN'), row('B')])
        ours = doc([row('A', 'OPEN'), row('B')])
        theirs = doc([row('A', 'DONE', evidence='ran it'), row('B')])
        merged, lines = run_merge(base, ours, theirs)
        self.assertEqual(merged['rows'][0]['status'], 'DONE')
        self.assertEqual(lines, [], 'a one sided change needs no ruling')

    def test_an_added_row_lands_after_the_last_row_of_its_section(self):
        base = doc([row('A', section='alpha'), row('B'), row('C', section='alpha')])
        ours = doc([row('A', section='alpha'), row('B'), row('C', section='alpha'),
                    row('NEW', section='alpha')])
        merged, _ = run_merge(base, ours, doc(base['rows']))
        self.assertEqual(ids(merged), ['A', 'B', 'C', 'NEW'])

    def test_a_row_in_a_new_section_is_appended_at_the_end(self):
        base = doc([row('A', section='alpha'), row('B')])
        theirs = doc([row('A', section='alpha'), row('B'),
                      row('NEW', section='omega')])
        merged, _ = run_merge(base, doc(base['rows']), theirs)
        self.assertEqual(ids(merged), ['A', 'B', 'NEW'])
        self.assertEqual(merged['rows'][-1]['section'], 'omega')


class BothSidesChangeTheSameRow(unittest.TestCase):
    def test_the_further_status_wins_and_the_choice_is_printed(self):
        base = doc([row('A', 'OPEN')])
        ours = doc([row('A', 'IN-FLIGHT', evidence='started')])
        theirs = doc([row('A', 'DONE', evidence='x')])
        merged, lines = run_merge(base, ours, theirs)
        self.assertEqual(merged['rows'][0]['status'], 'DONE')
        self.assertEqual(len(lines), 1)
        self.assertIn('A', lines[0])
        self.assertIn('DONE over IN-FLIGHT', lines[0])

    def test_the_further_status_wins_when_it_is_ours(self):
        base = doc([row('A', 'OPEN')])
        ours = doc([row('A', 'DONE', evidence='x')])
        theirs = doc([row('A', 'IN-FLIGHT', evidence='started')])
        merged, lines = run_merge(base, ours, theirs)
        self.assertEqual(merged['rows'][0]['status'], 'DONE')
        self.assertIn('took ours', lines[0])

    def test_the_same_status_falls_to_the_longer_evidence(self):
        base = doc([row('A', 'OPEN')])
        ours = doc([row('A', 'DONE', evidence='ran it, output quoted')])
        theirs = doc([row('A', 'DONE', evidence='ran')])
        merged, lines = run_merge(base, ours, theirs)
        self.assertEqual(merged['rows'][0]['evidence'], 'ran it, output quoted')
        self.assertIn('evidence', lines[0])

    def test_the_same_change_on_both_sides_is_not_a_conflict(self):
        base = doc([row('A', 'OPEN')])
        same = doc([row('A', 'DONE', evidence='ran')])
        merged, lines = run_merge(base, same, doc(same['rows']))
        self.assertEqual(merged['rows'][0]['status'], 'DONE')
        self.assertEqual(lines, [])

    def test_a_status_off_the_ladder_is_never_ranked(self):
        """SUPERSEDED is a row set aside, not a row moved forward. Ranking it
        either way would be the guess this driver refuses to make."""
        base = doc([row('A', 'OPEN')])
        ours = doc([row('A', 'SUPERSEDED', evidence='replaced by B')])
        theirs = doc([row('A', 'DONE', evidence='ran')])
        with self.assertRaises(rm.Undecidable) as cm:
            run_merge(base, ours, theirs)
        self.assertIn('off the ladder', str(cm.exception))

    def test_same_status_and_same_evidence_length_is_undecidable(self):
        base = doc([row('A', 'OPEN')])
        ours = doc([row('A', 'DONE', evidence='aaaa', title='ours title')])
        theirs = doc([row('A', 'DONE', evidence='bbbb', title='theirs title')])
        with self.assertRaises(rm.Undecidable) as cm:
            run_merge(base, ours, theirs)
        self.assertIn('A', str(cm.exception))

    def test_the_same_id_added_differently_on_both_sides_is_undecidable(self):
        base = doc([row('A')])
        with self.assertRaises(rm.Undecidable):
            run_merge(base, doc([row('A'), row('NEW', 'OPEN', 'ours')]),
                      doc([row('A'), row('NEW', 'OPEN', 'theirs')]))


class TopLevelFields(unittest.TestCase):
    def test_ours_wins_a_field_both_sides_changed(self):
        base = doc([row('A')], note='base note')
        ours = doc([row('A')], note='our note')
        theirs = doc([row('A')], note='their note')
        merged, _ = run_merge(base, ours, theirs)
        self.assertEqual(merged['note'], 'our note')

    def test_theirs_wins_a_field_only_theirs_changed(self):
        base = doc([row('A')], note='base note')
        ours = doc([row('A')], note='base note')
        theirs = doc([row('A')], note='their note')
        merged, _ = run_merge(base, ours, theirs)
        self.assertEqual(merged['note'], 'their note')

    def test_a_field_added_by_theirs_only_is_kept(self):
        base = doc([row('A')])
        theirs = doc([row('A')], risk_watch=['one thing'])
        merged, _ = run_merge(base, doc([row('A')]), theirs)
        self.assertEqual(merged['risk_watch'], ['one thing'])


class MalformedInput(unittest.TestCase):
    def test_a_document_with_no_rows_array_is_no_data(self):
        with self.assertRaises(rm.NoData) as cm:
            rm.validate({'schema': 1}, 'ours')
        self.assertIn('no rows array', str(cm.exception))

    def test_duplicate_ids_on_one_side_are_no_data(self):
        with self.assertRaises(rm.NoData) as cm:
            rm.validate(doc([row('A'), row('A')]), 'theirs')
        self.assertIn('duplicate row id A', str(cm.exception))

    def test_a_row_with_no_id_is_no_data(self):
        with self.assertRaises(rm.NoData):
            rm.validate({'rows': [{'title': 'nameless'}]}, 'ours')

    def test_a_non_object_document_is_no_data(self):
        with self.assertRaises(rm.NoData):
            rm.validate([1, 2, 3], 'base')


class StylePreserved(unittest.TestCase):
    """The round trip. A merge that changes nothing must reproduce the file
    byte for byte, or every clean merge would show as a whole file rewrite."""

    def sample(self):
        return ('{\n'
                '  "schema": 1,\n'
                '  "note": "keeps its order",\n'
                '  "rows": [\n'
                '   {\n'
                '    "id": "A",\n'
                '    "zzz_last": 1,\n'
                '    "aaa_first": 2,\n'
                '    "status": "OPEN"\n'
                '   }\n'
                '  ]\n'
                ' }\n')

    def test_measure_style_reads_the_indent_off_the_base(self):
        self.assertEqual(rm.measure_style(self.sample())['indent'], 2)
        self.assertEqual(rm.measure_style('{\n "a": 1\n}\n')['indent'], 1)

    def test_a_no_change_merge_reproduces_the_bytes_exactly(self):
        text = json.dumps(json.loads(self.sample()), indent=2) + '\n'
        base = json.loads(text)
        merged, _ = run_merge(base, json.loads(text), json.loads(text))
        self.assertEqual(rm.serialise(merged, rm.measure_style(text)), text)

    def test_key_order_survives_a_merge_that_changed_a_row(self):
        text = json.dumps(json.loads(self.sample()), indent=2) + '\n'
        base = json.loads(text)
        theirs = json.loads(text)
        theirs['rows'][0]['status'] = 'DONE'
        merged, _ = run_merge(base, json.loads(text), theirs)
        self.assertEqual(list(merged['rows'][0]),
                         ['id', 'zzz_last', 'aaa_first', 'status'])
        self.assertEqual(list(merged), ['schema', 'note', 'rows'])

    def test_a_file_with_no_trailing_newline_keeps_none(self):
        style = rm.measure_style('{\n "a": 1\n}')
        self.assertFalse(style['trailing_newline'])
        self.assertFalse(rm.serialise({'a': 1}, style).endswith('\n'))


class CommandLine(unittest.TestCase):
    """The exit codes, driven through the real entry point. A caller (the
    landing pipeline) branches on these three numbers and nothing else."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='roadmap-merge-test-')
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, name, data):
        path = os.path.join(self.dir, name)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(data if isinstance(data, str)
                     else json.dumps(data, indent=1) + '\n')
        return path

    def call(self, argv):
        """Run main() with stdout and stderr captured, returning (code, text)."""
        out, err = io.StringIO(), io.StringIO()
        old = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            code = rm.main(argv)
        finally:
            sys.stdout, sys.stderr = old
        return code, out.getvalue() + err.getvalue()

    def test_a_clean_merge_exits_zero_and_writes_the_file(self):
        b = self.write('b.json', doc([row('A')]))
        o = self.write('o.json', doc([row('A'), row('OURS')]))
        t = self.write('t.json', doc([row('A'), row('THEIRS')]))
        out = os.path.join(self.dir, 'out.json')
        code, text = self.call([b, o, t, '-o', out])
        self.assertEqual(code, 0, text)
        with open(out, encoding='utf-8') as fh:
            self.assertEqual([r['id'] for r in json.load(fh)['rows']],
                             ['A', 'OURS', 'THEIRS'])

    def test_an_undecidable_row_exits_one_and_writes_nothing(self):
        b = self.write('b.json', doc([row('A', 'OPEN')]))
        o = self.write('o.json', doc([row('A', 'DONE', 'aaaa', title='ours')]))
        t = self.write('t.json', doc([row('A', 'DONE', 'bbbb', title='theirs')]))
        out = os.path.join(self.dir, 'out.json')
        code, text = self.call([b, o, t, '-o', out])
        self.assertEqual(code, 1, text)
        self.assertIn('CONFLICT', text)
        self.assertIn('row A', text)
        self.assertFalse(os.path.exists(out), 'exit 1 must write nothing')

    def test_a_malformed_input_exits_two(self):
        b = self.write('b.json', doc([row('A')]))
        o = self.write('o.json', {'schema': 1})
        t = self.write('t.json', doc([row('A')]))
        out = os.path.join(self.dir, 'out.json')
        code, text = self.call([b, o, t, '-o', out])
        self.assertEqual(code, 2, text)
        self.assertIn('NO-DATA', text)
        self.assertFalse(os.path.exists(out))

    def test_unreadable_json_exits_two(self):
        b = self.write('b.json', 'not json at all')
        o = self.write('o.json', doc([row('A')]))
        t = self.write('t.json', doc([row('A')]))
        code, text = self.call([b, o, t, '-o', os.path.join(self.dir, 'x.json')])
        self.assertEqual(code, 2, text)

    def test_a_missing_file_exits_two(self):
        o = self.write('o.json', doc([row('A')]))
        code, text = self.call([os.path.join(self.dir, 'nope.json'), o, o,
                                '-o', os.path.join(self.dir, 'x.json')])
        self.assertEqual(code, 2, text)
        self.assertIn('NO-DATA', text)


class GitMode(unittest.TestCase):
    """--git reads the three stages out of a real conflicted index. Built on a
    throwaway repository so the suite never touches this checkout."""

    def setUp(self):
        if not shutil.which('git'):
            self.skipTest('NO-DATA: no git binary on this machine')
        self.dir = tempfile.mkdtemp(prefix='roadmap-merge-git-')
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'test')

    def git(self, *args):
        return subprocess.run(('git',) + args, cwd=self.dir,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def commit(self, rows, message):
        with open(os.path.join(self.dir, 'board.json'), 'w',
                  encoding='utf-8') as fh:
            fh.write(json.dumps(doc(rows), indent=1) + '\n')
        self.git('add', 'board.json')
        self.git('commit', '-q', '-m', message)

    def conflicted_index(self, our_rows, their_rows):
        self.commit([row('A')], 'base')
        self.git('checkout', '-q', '-b', 'theirs')
        self.commit(their_rows, 'theirs')
        self.git('checkout', '-q', 'main')
        self.commit(our_rows, 'ours')
        merged = self.git('merge', 'theirs')
        self.assertNotEqual(merged.returncode, 0,
                            'the fixture must actually conflict')

    def drive(self):
        return subprocess.run([sys.executable, DRIVER, '--git', 'board.json'],
                              cwd=self.dir, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT)

    def test_two_disjoint_added_rows_resolve_and_the_index_can_be_staged(self):
        self.conflicted_index([row('A'), row('OURS')],
                              [row('A'), row('THEIRS')])
        proc = self.drive()
        self.assertEqual(proc.returncode, 0, proc.stdout.decode())
        with open(os.path.join(self.dir, 'board.json'), encoding='utf-8') as fh:
            self.assertEqual([r['id'] for r in json.load(fh)['rows']],
                             ['A', 'OURS', 'THEIRS'])
        self.assertEqual(self.git('add', 'board.json').returncode, 0)
        self.assertEqual(self.git('commit', '-q', '-m', 'merged').returncode, 0)

    def test_an_undecidable_conflict_leaves_the_index_conflicted(self):
        self.conflicted_index([row('A', 'DONE', 'aaaa', title='ours')],
                              [row('A', 'DONE', 'bbbb', title='theirs')])
        proc = self.drive()
        self.assertEqual(proc.returncode, 1, proc.stdout.decode())
        self.assertIn(b'CONFLICT', proc.stdout)
        unmerged = self.git('diff', '--name-only', '--diff-filter=U')
        self.assertIn(b'board.json', unmerged.stdout)

    def test_a_path_with_no_conflict_stages_is_no_data(self):
        self.commit([row('A')], 'only commit')
        proc = subprocess.run([sys.executable, DRIVER, '--git', 'board.json'],
                              cwd=self.dir, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT)
        self.assertEqual(proc.returncode, 2, proc.stdout.decode())
        self.assertIn(b'NO-DATA', proc.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
