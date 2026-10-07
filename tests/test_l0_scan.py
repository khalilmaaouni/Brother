#!/usr/bin/env python3
'''Drive l0_scan: a restricted name in HEAD, in the working tree, or only
in reachable history must block, a missing or malformed token hash list
must block, and the term itself must never reach standard output or the
record. Every fixture is built in a temp folder, so the suite needs no git,
no existing repository and no document outside the code.'''
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts import l0_scan  # noqa: E402

TERM = 'zzqrestrictedcanary'
CANARY = hashlib.sha256(TERM.encode('utf-8')).hexdigest()
OTHER_CANARY = hashlib.sha256(b'zzqunrelatedcanary').hexdigest()
LF = chr(10)


def _write_object(git_dir, kind, payload):
    data = ('%s %d' % (kind, len(payload))).encode('ascii') + bytes([0]) + payload
    digest = hashlib.sha1(data).hexdigest()
    folder = os.path.join(git_dir, 'objects', digest[:2])
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, digest[2:]), 'wb') as handle:
        handle.write(zlib.compress(data))
    return digest


def _tree_body(entries):
    body = b''
    for mode, name, sub in entries:
        body += (mode + ' ').encode('ascii')
        body += name.encode('utf-8') + bytes([0])
        body += bytes.fromhex(sub)
    return body


def _nested(files):
    root = {}
    for path, text in files.items():
        node = root
        parts = path.split('/')
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = text
    return root


def _put_file(root, relative, text):
    full = os.path.join(root, relative.replace('/', os.sep))
    folder = os.path.dirname(full)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(full, 'w', encoding='utf-8') as handle:
        handle.write(text)


class Repo:
    '''A git object store written by hand, so no test needs git or an
    existing repository. Objects are loose, which is what the scanner reads
    first.'''

    def __init__(self, root):
        self.root = root
        self.git = os.path.join(root, '.git')
        os.makedirs(os.path.join(self.git, 'objects'), exist_ok=True)
        os.makedirs(os.path.join(self.git, 'refs', 'heads'), exist_ok=True)
        self._write(os.path.join(self.git, 'HEAD'),
                    'ref: refs/heads/main' + LF)

    @staticmethod
    def _write(path, text):
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text)

    def commit(self, files, parents=(), message='canary commit'):
        tree = self._tree(_nested(files))
        lines = ['tree %s' % tree]
        lines.extend('parent %s' % parent for parent in parents)
        lines.append('author canary <canary@example.invalid> 0 +0000')
        lines.append('committer canary <canary@example.invalid> 0 +0000')
        payload = (LF.join(lines) + LF + LF + message + LF).encode('utf-8')
        sha = _write_object(self.git, 'commit', payload)
        self._write(os.path.join(self.git, 'refs', 'heads', 'main'), sha + LF)
        return sha

    def _tree(self, node):
        entries = []
        for name in sorted(node):
            value = node[name]
            if isinstance(value, dict):
                entries.append(('40000', name, self._tree(value)))
            else:
                blob = _write_object(self.git, 'blob', value.encode('utf-8'))
                entries.append(('100644', name, blob))
        return _write_object(self.git, 'tree', _tree_body(entries))


class TestScan(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = holder.name
        self.work = os.path.join(self.tmp, 'work')
        os.makedirs(self.work)
        self.repo = Repo(self.work)
        self.record = os.path.join(self.tmp, 'record.json')
        self.hashes = os.path.join(self.tmp, 'token-hashes.txt')

    def _digests(self, values):
        with open(self.hashes, 'w', encoding='utf-8') as handle:
            for value in values:
                handle.write(value + LF)

    def _run(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = l0_scan.check_quarantine(self.work, self.record,
                                            self.hashes)
        return code, out.getvalue()

    def _record(self):
        with open(self.record, encoding='utf-8') as handle:
            return handle.read()

    def test_history_token_blocks(self):
        first = self.repo.commit({
            'README.md': 'readme',
            'docs/zzqrestrictedcanary-handover.md': 'pack',
        }, message='carry the pack')
        self.repo.commit({'README.md': 'readme'}, parents=[first],
                         message='drop the pack again')
        self._digests([CANARY])
        code, out = self._run()
        self.assertEqual(code, 2, out)
        self.assertNotIn(TERM, out)
        record = json.loads(self._record())
        self.assertTrue(record['blocked'])
        self.assertNotIn(TERM, self._record())

    def test_head_token_blocks(self):
        self.repo.commit({'docs/zzqrestrictedcanary-handover.md': 'pack'})
        self._digests([CANARY])
        code, out = self._run()
        self.assertEqual(code, 2, out)
        self.assertNotIn(TERM, out)

    def test_worktree_token_blocks(self):
        self.repo.commit({'README.md': 'readme'})
        _put_file(self.work, 'handover/zzqrestrictedcanary-pack.md', 'pack')
        self._digests([CANARY])
        code, out = self._run()
        self.assertEqual(code, 2, out)
        self.assertTrue(os.path.isfile(os.path.join(
            self.work, 'handover', 'zzqrestrictedcanary-pack.md')))

    def test_missing_hash_blocks(self):
        self.repo.commit({'README.md': 'readme'})
        self.assertFalse(os.path.exists(self.hashes))
        code, out = self._run()
        self.assertEqual(code, 2, out)

    def test_empty_hash_blocks(self):
        self.repo.commit({'README.md': 'readme'})
        with open(self.hashes, 'w', encoding='utf-8') as handle:
            handle.write('')
        code, out = self._run()
        self.assertEqual(code, 2, out)

    def test_malformed_hash_blocks(self):
        self.repo.commit({'README.md': 'readme'})
        self._digests([CANARY, 'not-a-digest'])
        code, out = self._run()
        self.assertEqual(code, 2, out)

    def test_clean_tree_clears(self):
        self.repo.commit({'README.md': 'readme', 'docs/plan/notes.md': 'n'})
        _put_file(self.work, 'src/app.py', 'print(1)')
        self._digests([CANARY])
        code, out = self._run()
        self.assertEqual(code, 0, out)
        self.assertIn('CLEAR', out)

    def test_worktree_without_git_blocks(self):
        self._digests([CANARY])
        empty = os.path.join(self.tmp, 'empty')
        os.makedirs(empty)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = l0_scan.check_quarantine(empty, self.record, self.hashes)
        self.assertEqual(code, 2, out.getvalue())

    def test_canonical_candidates_cover_the_name(self):
        names = l0_scan.canonical_candidates(
            'docs/zzqrestrictedcanary-handover.md')
        self.assertIn(TERM, names)
        self.assertNotIn('', names)
        self.assertEqual(l0_scan.canonical_candidates(''), [])

    def test_scan_names_finds_a_basename_candidate(self):
        hits = l0_scan.scan_names(['docs/zzqrestrictedcanary-handover.md'],
                                  [CANARY])
        self.assertEqual(hits, ['docs/zzqrestrictedcanary-handover.md'])

    def test_scan_names_refuses_an_empty_token_list(self):
        with self.assertRaises(ValueError):
            l0_scan.scan_names(['README.md'], [])

    def test_load_token_hash_list_reads_and_refuses(self):
        self._digests([CANARY, OTHER_CANARY])
        self.assertEqual(l0_scan.load_token_hash_list(self.hashes),
                         [CANARY, OTHER_CANARY])
        self.assertEqual(l0_scan.load_token_hash_list(
            os.path.join(self.tmp, 'absent.txt')), [])
        broken = os.path.join(self.tmp, 'broken.txt')
        with open(broken, 'wb') as handle:
            handle.write(bytes([0xff, 0xfe, 0x20, 0x61]))
        self.assertEqual(l0_scan.load_token_hash_list(broken), [])
        self.assertEqual(l0_scan.load_token_hash_list(self.tmp), [])

    def test_hostile_input_is_refused(self):
        self._digests([CANARY])
        self.assertEqual(l0_scan.load_token_hash_list(None), [])
        self.assertEqual(l0_scan.load_token_hash_list(7), [])
        self.assertEqual(l0_scan.load_token_hash_list(b'bytes'), [])
        with self.assertRaises(ValueError):
            l0_scan.canonical_candidates(None)
        with self.assertRaises(ValueError):
            l0_scan.canonical_candidates(42)
        with self.assertRaises(ValueError):
            l0_scan.canonical_candidates(b'bytes')
        with self.assertRaises(ValueError):
            l0_scan.canonical_candidates(float('nan'))
        with self.assertRaises(ValueError):
            l0_scan.scan_names('README.md', [CANARY])
        with self.assertRaises(ValueError):
            l0_scan.scan_names(['README.md'], 'canary')
        with self.assertRaises(ValueError):
            l0_scan.scan_names([None], [CANARY])
        with self.assertRaises(ValueError):
            l0_scan.scan_names([True], [CANARY])
        with self.assertRaises(ValueError):
            l0_scan.scan_names([float('nan')], [CANARY])
        with self.assertRaises(ValueError):
            l0_scan.scan_names(['README.md'], [None])
        with self.assertRaises(ValueError):
            l0_scan.scan_history(None, [CANARY], '', 'HEAD')
        with self.assertRaises(ValueError):
            l0_scan.scan_history(self.work, [CANARY], '', None)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = l0_scan.check_quarantine(None, self.record, self.hashes)
        self.assertEqual(code, 2)

    def test_record_carries_digests_only(self):
        self.repo.commit({'docs/zzqrestrictedcanary-handover.md': 'pack'})
        self._digests([CANARY])
        self._run()
        text = self._record()
        record = json.loads(text)
        self.assertTrue(record['blocked'])
        self.assertTrue(record['hits'])
        for digest in record['hits']:
            self.assertEqual(len(digest), 64)
            self.assertTrue(all(one in '0123456789abcdef'
                                for one in digest))
        self.assertNotIn(TERM, text)


if __name__ == '__main__':
    unittest.main()
