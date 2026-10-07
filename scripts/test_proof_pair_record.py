#!/usr/bin/env python3
"""R-2 (D-12, objection 7): the pair record the launcher keeps outside both run directories, and acceptance over it.

pair-<id>/pair.json names the two run directories the launcher expects, the frozen manifest and the coverage end; it is
created once (O_EXCL). pair-<id>/RB.result.json and RC.result.json record each driver's exit code and its receipt's
digest, once. Acceptance with --pair reads them: missing is NO-DATA, a refused or failed launch is FAIL. The D-12
regression is built for real: a prior pair with valid evidence, then a relaunch whose proof folder and launch registry
cannot be written, so no refusal can be recorded inside or beside the run. Only the new pair's result can say so, and
acceptance over that pair never answers PASS.

Every case drives the entry points the launcher and the owner use (proof_launch's CLI and helpers, proof_accept.main)
over the acceptance fixture of test_proof_accept. Nothing here asserts from source text.
Run: python3 -B scripts/test_proof_pair_record.py
"""
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / 'loop'))   # named so the deploy parity check can read it (F47)
import proof_accept as A
import proof_launch as L
import proof_ledger as P
import loop_receipt as R
import test_proof_accept as fixture

TOOL = HERE / 'loop' / 'proof_launch.py'


def quiet(fn, *args):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*args)


class PairRecord(fixture.Proof):
    def accept(self, pair=None):
        self.save()
        return quiet(A.main, ['--pair', str(pair or self.pair)] + [str(d) for d in self.dirs])

    def tool(self, *argv):
        return subprocess.run([sys.executable, '-B', str(TOOL)] + [str(a) for a in argv], capture_output=True, text=True)

    def new_pair(self, name):
        p = self.root / name
        p.mkdir()
        L.write_pair(p, self.dirs[0], self.dirs[1], 'e' * 64, self.manifest_sha, '2026-09-27T00:00:00+00:00')
        return p

    def test_the_honest_pair_passes(self):
        self.assertEqual(self.accept(), 0)

    def test_d12_relaunch_over_old_valid_evidence_is_never_pass(self):
        self.assertEqual(self.accept(), 0)
        rb = self.dirs[0]
        registry = P.launch_dir(rb).parent
        for folder in (rb / 'proof', registry):
            folder.chmod(stat.S_IRUSR | stat.S_IXUSR)
            self.addCleanup(folder.chmod, stat.S_IRWXU)
        with mock.patch.dict(os.environ, BROTHER_PROOF_PHASE='RB'):
            code = quiet(R.proof_cli, ['proof-start', '--run-dir', str(rb), '--deadline-epoch', '1'])
        self.assertEqual(code, 2)
        again = self.new_pair('pair-relaunch')
        L.record_result(again, 'RB', code, None)
        for folder in (rb / 'proof', registry):
            folder.chmod(stat.S_IRWXU)
        self.assertIn(quiet(A.main, ['--pair', str(again)] + [str(d) for d in self.dirs]), (1, 2))

    def test_a_missing_pair_record_is_no_data(self):
        empty = self.root / 'pair-empty'
        empty.mkdir()
        self.assertEqual(self.accept(empty), 2)

    def test_an_rb_exit_1_with_a_deadline_receipt_fails(self):
        for rec, _ in self.records:
            rec['end_state'] = 'DEADLINE'
        self.save()
        self.record_results(codes=(1, 0))
        self.assertEqual(quiet(A.main, ['--pair', str(self.pair)] + [str(d) for d in self.dirs]), 1)

    def test_a_missing_rc_result_is_no_data(self):
        self.save()
        (self.pair / 'RC.result.json').unlink()
        self.assertEqual(quiet(A.main, ['--pair', str(self.pair)] + [str(d) for d in self.dirs]), 2)

    def test_the_pair_record_is_written_once(self):
        with self.assertRaises(FileExistsError):
            L.write_pair(self.pair, self.dirs[0], self.dirs[1], 'e' * 64, self.manifest_sha, 'x')

    def test_cli_pair_write_without_a_manifest_refuses(self):
        p = self.root / 'pair-partial'
        p.mkdir()
        r = self.tool('pair-write', '--pair-dir', p, '--rb', self.dirs[0], '--rc', self.dirs[1], '--env-sha256', 'e' * 64,
                      '--pair-until', 'x')
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((p / 'pair.json').exists())

    def test_a_result_is_written_once(self):
        self.save()
        with self.assertRaises(FileExistsError):
            L.record_result(self.pair, 'RB', 0, self.dirs[0] / 'receipt/receipt.json')

    def test_a_result_for_an_unknown_phase_refuses(self):
        with self.assertRaises(ValueError):
            L.record_result(self.pair, 'RX', 0, None)

    def test_a_result_records_the_receipt_digest(self):
        self.save()
        got = json.loads((self.pair / 'RB.result.json').read_text())
        self.assertEqual(got['receipt_sha256'], fixture.sha((self.dirs[0] / 'receipt/receipt.json').read_bytes()))
        self.assertEqual((got['exit_code'], got['run_dir']), (0, str(self.dirs[0].resolve())))

    def test_cli_pair_write_then_result(self):
        p = self.root / 'pair-cli'
        p.mkdir()
        r = self.tool('pair-write', '--pair-dir', p, '--rb', self.dirs[0], '--rc', self.dirs[1], '--env-sha256', 'e' * 64,
                      '--manifest-sha256', self.manifest_sha, '--pair-until', '2026-09-27T00:00:00+00:00')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.save()
        r = self.tool('result', '--pair-dir', p, '--phase', 'RB', '--exit', '0', '--receipt', self.dirs[0] / 'receipt/receipt.json')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads((p / 'RB.result.json').read_text())['exit_code'], 0)
        r = self.tool('pair-write', '--pair-dir', p, '--rb', self.dirs[0], '--rc', self.dirs[1], '--env-sha256', 'e' * 64,
                      '--manifest-sha256', self.manifest_sha, '--pair-until', 'x')
        self.assertNotEqual(r.returncode, 0)


class Handoff(fixture.Proof):
    """S11: RC may start only on RB exit 0, a DEADLINE receipt naming RB's directory, its evidence and its result."""

    def setUp(self):
        super().setUp()
        for rec, _ in self.records:
            rec['end_state'] = 'DEADLINE'
        self.save()

    def ok(self):
        return L.handoff(self.pair)

    def test_a_clean_rb_hands_off(self):
        self.assertEqual(self.ok(), (True, ''))
        r = subprocess.run([sys.executable, '-B', str(TOOL), 'handoff', '--pair-dir', str(self.pair)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_an_rb_exit_other_than_0_does_not(self):
        self.record_results(codes=(1, 0))
        self.assertFalse(self.ok()[0])
        r = subprocess.run([sys.executable, '-B', str(TOOL), 'handoff', '--pair-dir', str(self.pair)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_an_unfunded_end_does_not(self):
        self.records[0][0]['end_state'] = 'UNFUNDED'
        self.save()
        self.assertFalse(self.ok()[0])

    def test_a_receipt_naming_another_run_does_not(self):
        self.records[0][0]['run_id'] = 'RC'
        self.save()
        self.assertFalse(self.ok()[0])

    def test_a_receipt_changed_after_its_result_does_not(self):
        p = self.dirs[0] / 'receipt/receipt.json'
        p.write_text(p.read_text() + ' ')
        self.assertFalse(self.ok()[0])

    def test_rb_without_its_evidence_does_not(self):
        (self.dirs[0] / 'proof/evidence.json').unlink()
        self.assertFalse(self.ok()[0])

    def test_a_missing_rb_result_does_not(self):
        (self.pair / 'RB.result.json').unlink()
        self.assertFalse(self.ok()[0])


# Run only the cases defined here: the inherited acceptance cases run in their own suite.
if __name__ == '__main__':
    suite = unittest.TestSuite(cls(n) for cls in (PairRecord, Handoff) for n in cls.__dict__ if n.startswith('test_'))
    sys.exit(not unittest.TextTestRunner().run(suite).wasSuccessful())
