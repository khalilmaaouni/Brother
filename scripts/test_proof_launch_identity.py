#!/usr/bin/env python3
"""A proof run is judged against what it was LAUNCHED as, recorded outside its own directory.

F44 (finding g, Codex decision D-12): a failed reuse whose proof folder could not be written left
no reuse marker, so an older consistent proof stayed acceptable. F45 (s4 A3 to A5, D-17): a
coordinated rewrite of proof/start.json and the environment, a phase rewrite with the phase
variable unset, or a deleted reuse marker was admitted, because every value the run was checked
against lived in files the run writes or variables it reads.

Every case builds the hostile state for real (a read-only folder, a rewritten record carrying its
own hash, a deleted marker, a reused directory) and drives the real launcher, work clock, spend
identity or acceptance entry point. Nothing here asserts from source text.

Run: python3 -B scripts/test_proof_launch_identity.py
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / 'loop'))   # named so the deploy parity check can read it (F47)
import loop_receipt as R
import proof_accept as A
import proof_ledger as P
import test_proof_accept as acceptance
import test_proof_accounting_admission as admission

REGISTRY = '.proof-launches'


def quiet_cli(argv):
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        return R.proof_cli(argv)


def read_only(case, folder):
    folder.chmod(stat.S_IRUSR | stat.S_IXUSR)
    case.addCleanup(folder.chmod, stat.S_IRWXU)


class Launcher(admission.Admission):
    """The launcher side: proof-start, proof-work-start and the spend identity, on a real ledger."""

    def registry(self):
        return self.root / REGISTRY / self.run.name

    def refusals(self):
        where = self.registry()
        return sorted(p.name for p in where.glob('refused-*')) if where.is_dir() else []

    def test_valid_launch_admits_work(self):
        self.start(); self.assertEqual(self.work(), self.now)

    def test_real_writers_produce_an_accepted_launch(self):
        # The acceptance fixture is synthetic; this is the same row over what the real writers record.
        import proof_launch
        self.start(); self.work(); R.proof_finish(str(self.run), '2026-09-26T08:00:00+00:00')
        self.assertIs(proof_launch.accepted(str(self.run), 'RB'), True)

    def test_launch_is_recorded_outside_the_run_directory(self):
        started = self.start()
        record = self.registry() / 'launch.json'
        self.assertTrue(record.is_file(), 'no launch record at %s' % record)
        self.assertNotIn(str(self.run), str(record.resolve()))
        launched = json.loads(record.read_text())
        for key in ('run_id', 'attempt_id', 'phase', 'ledger_path', 'ledger_baseline', 'ledger_baseline_sha256'):
            self.assertEqual(launched[key], started[key], key)
        self.assertEqual(launched['run_dir'], str(self.run.resolve()))

    def test_admitted_work_is_recorded_once_outside(self):
        started = self.start(); work = self.work()
        admitted = json.loads((self.registry() / 'work-start.json').read_text())
        self.assertEqual((admitted['attempt_id'], admitted['work_start']), (started['attempt_id'], work))

    def test_reused_launch_directory_is_refused_even_without_its_proof_folder(self):
        self.start(); shutil.rmtree(self.run / 'proof')
        self.assertEqual(self.cli(), 2)
        self.assertTrue(self.refusals())

    def test_reused_launch_directory_is_refused_by_the_launcher_itself(self):
        self.start(); shutil.rmtree(self.run / 'proof')
        with self.assertRaises(ValueError):
            self.start()
        self.assertTrue(self.refusals())

    def test_reused_launch_directory_is_voided_before_the_deadline_check(self):
        self.start(); shutil.rmtree(self.run / 'proof')
        self.assertEqual(quiet_cli(['proof-start', '--run-dir', str(self.run)]), 2)
        self.assertTrue(self.refusals())

    def test_dangling_proof_link_is_voided_before_the_deadline_check(self):
        self.run.rmdir(); self.run.mkdir(); (self.run / 'proof').symlink_to('missing')
        self.assertEqual(quiet_cli(['proof-start', '--run-dir', str(self.run)]), 2)
        self.assertTrue(self.refusals())

    def test_work_clock_cannot_be_admitted_twice(self):
        # Dropping work_start from start.json must not let the clock start again later.
        self.start(); self.work()
        p = self.run / 'proof/start.json'; r = json.loads(p.read_text()); r.pop('work_start'); p.write_text(json.dumps(r))
        self.assert_work_refuses()

    def test_refusal_recorded_nowhere_is_reported(self):
        self.start()
        shutil.rmtree(self.root / REGISTRY); (self.root / REGISTRY).write_text('not a directory')
        read_only(self, self.run / 'proof')
        with self.assertRaises(OSError):
            R.refuse_reuse(str(self.run))

    def test_read_only_proof_folder_reuse_is_recorded_outside(self):
        self.start(); before = (self.run / 'proof/start.json').read_bytes()
        read_only(self, self.run / 'proof')
        self.assertEqual(self.cli(), 2)
        self.assertEqual((self.run / 'proof/start.json').read_bytes(), before)
        self.assertTrue(self.refusals(), 'the refusal was recorded nowhere')

    def test_unwritable_registry_refuses_the_launch(self):
        (self.root / REGISTRY).write_text('not a directory')
        self.assertEqual(self.cli(), 2)

    def test_coordinated_start_and_env_baseline_swap_refuses_work(self):
        # s4 A3: the baseline, its digest and the ledger are replaced in start.json AND in the
        # environment together, so the start record and the policy still agree with each other.
        self.start()
        other = self.root / 'stateB'; other.mkdir(); ledger = other / 'openrouter-ledger.jsonl'
        ledger.write_text(json.dumps(dict(type='RESERVE', reservation_id='b', at=1, estimated_cost=1.0)) + '\n'
                          + json.dumps(dict(type='RELEASE', reservation_id='b', at=2)) + '\n')
        base = self.root / 'baselineB.json'; digest = P.capture(ledger, base)
        for key, value in (('ledger_path', str(ledger.resolve())), ('ledger_baseline', str(base)), ('ledger_baseline_sha256', digest)):
            self.change_start(key, value)
        with mock.patch.dict(os.environ, BROTHER_OR_STATE_ROOT=str(other), BROTHER_PROOF_BASELINE=str(base),
                             BROTHER_PROOF_BASELINE_SHA256=digest):
            self.assert_work_refuses()

    def test_start_phase_rewrite_with_phase_unset_refuses_work(self):
        # s4 A4: a non-proof phase and no phase variable used to skip every admission re-check.
        self.start(); self.change_start('phase', 'not-proof')
        with mock.patch.dict(os.environ):
            os.environ.pop('BROTHER_PROOF_PHASE', None)
            self.assert_work_refuses()

    def test_deleted_reuse_marker_cannot_readmit_the_old_start(self):
        # s4 A5: the in-folder marker was the only record of the refused reuse.
        self.start(); self.assertEqual(self.cli(), 2)
        (self.run / 'proof/reuse-refused.json').unlink()
        self.assert_work_refuses()

    def test_proof_phase_without_its_launch_record_refuses_work(self):
        # Only launch.json goes: removing the whole registry would also make the admission write
        # fail, and that second refusal would mask this one.
        self.start()
        launch = self.registry() / 'launch.json'
        if launch.exists():
            launch.unlink()
        self.assert_work_refuses()

    def test_malformed_launch_record_refuses_work_cleanly(self):
        self.start(); (self.registry() / 'launch.json').write_text('[]')
        self.assertEqual(quiet_cli(['proof-work-start', '--run-dir', str(self.run), '--deadline-epoch', self.deadline]), 2)
        self.assertNotIn('work_start', json.loads((self.run / 'proof/start.json').read_text()))

    def test_sandbox_artifact_rewritten_with_its_digest_refuses_work(self):
        # start.json carries the observation's own digest; both are rewritten together.
        self.start(); artifact = self.run / 'proof/sandbox.json'
        artifact.write_text(artifact.read_text().replace('"exit_code": 0', '"exit_code": 0, "note": "forged"'))
        p = self.run / 'proof/start.json'; r = json.loads(p.read_text())
        r['sandbox']['evidence_sha256'] = hashlib.sha256(artifact.read_bytes()).hexdigest(); p.write_text(json.dumps(r))
        self.assert_work_refuses()

    def test_attempt_rewritten_in_start_refuses_work(self):
        self.start(); self.change_start('attempt_id', 'c' * 32)
        self.assert_work_refuses()

    def spend_env(self):
        return dict(self.env, BROTHER_RUN_DIR=str(self.run))

    def test_spend_identity_control(self):
        self.start()
        self.assertEqual(P.configuration(str(self.state), self.spend_env())['run_id'], 'RB')

    def test_spend_identity_refuses_a_refusal_recorded_only_outside(self):
        self.start(); self.assertEqual(self.cli(), 2)
        (self.run / 'proof/reuse-refused.json').unlink()
        with self.assertRaises(ValueError):
            P.configuration(str(self.state), self.spend_env())

    def test_spend_identity_refuses_a_dangling_reuse_marker(self):
        self.start(); (self.run / 'proof/reuse-refused.json').symlink_to('missing')
        with self.assertRaises(ValueError):
            P.configuration(str(self.state), self.spend_env())


class Acceptance(acceptance.Proof):
    """The acceptance side: the pair is judged against the launch registry, in process."""

    def verdict(self):
        # the launcher's pair record is required (D-12); its results follow the receipts as they now are, so only the
        # case's own hostile edit can move the verdict
        self.record_results()
        return A.accept(*self.dirs, pair=self.pair)

    def row(self, name='launch_identity', run=0):
        rows = self.verdict()['runs'][run]['checks']
        return next((x['verdict'] for x in rows if x['check'] == name), 'ABSENT')

    def edit(self, path, **changes):
        value = json.loads(path.read_text()); value.update(changes); path.write_text(json.dumps(value))

    def relaunch(self, d):
        with mock.patch.dict(os.environ, BROTHER_PROOF_PHASE='RB'):
            return quiet_cli(['proof-start', '--run-dir', str(d)])

    def test_valid_pair_passes(self):
        self.assertEqual(self.verdict()['verdict'], 'PASS')
        self.assertEqual(self.row(), 'PASS')

    def test_failed_reuse_with_read_only_proof_folder_voids_the_old_run(self):
        # Finding g: the in-folder marker cannot be written, so it must not be the only record.
        d = self.dirs[0]; before = (d / 'proof/start.json').read_bytes()
        read_only(self, d / 'proof')
        self.assertEqual(self.relaunch(d), 2)
        d.joinpath('proof').chmod(stat.S_IRWXU)
        self.assertFalse((d / 'proof/reuse-refused.json').exists())
        self.assertEqual((d / 'proof/start.json').read_bytes(), before)
        self.assertNotEqual(self.verdict()['verdict'], 'PASS')
        self.assertEqual(self.row(), 'FAIL')

    def test_deleted_reuse_marker_still_voids_the_old_run(self):
        d = self.dirs[0]
        self.assertEqual(self.relaunch(d), 2)
        (d / 'proof/reuse-refused.json').unlink()
        self.assertNotEqual(self.verdict()['verdict'], 'PASS')
        self.assertEqual(self.row(), 'FAIL')

    def test_attempt_rewritten_throughout_the_run_directory_fails(self):
        # Every in-directory record agrees on the forged attempt, the sandbox artifact's own digest included.
        d = self.dirs[0]; forged = 'c' * 32
        self.edit(d / 'proof/sandbox.json', attempt_id=forged)
        digest = hashlib.sha256((d / 'proof/sandbox.json').read_bytes()).hexdigest()
        evidence = json.loads((d / 'proof/evidence.json').read_text())
        evidence['attempt_id'] = forged; evidence['sandbox']['evidence_sha256'] = digest
        (d / 'proof/evidence.json').write_text(json.dumps(evidence))
        self.edit(d / 'proof/start.json', attempt_id=forged)
        self.edit(d / 'receipt/receipt.json', proof_attempt_id=forged)
        self.assertEqual(self.row('identity'), 'PASS')
        self.assertEqual(self.row('sandbox'), 'PASS')
        self.assertNotEqual(self.verdict()['verdict'], 'PASS')
        self.assertEqual(self.row(), 'FAIL')

    def forge_sandbox(self, *records):
        d = self.dirs[0]; artifact = d / 'proof/sandbox.json'
        self.edit(artifact, note='forged'); digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        for name in records:
            path = d / 'proof' / name; value = json.loads(path.read_text())
            value['sandbox']['evidence_sha256'] = digest; path.write_text(json.dumps(value))

    def test_sandbox_rewritten_in_artifact_and_evidence_fails(self):
        # The artifact and the digest acceptance reads (evidence.json) agree on the forgery.
        self.forge_sandbox('evidence.json')
        self.assertEqual(self.row('sandbox'), 'PASS')
        self.assertNotEqual(self.verdict()['verdict'], 'PASS')
        self.assertEqual(self.row(), 'FAIL')

    def test_sandbox_digest_rewritten_in_start_fails(self):
        self.forge_sandbox('start.json')
        self.assertEqual(self.row(), 'FAIL')

    def test_work_clock_rewritten_throughout_the_run_directory_fails(self):
        d = self.dirs[0]; earlier = '2026-09-25T23:59:00+00:00'
        evidence = json.loads((d / 'proof/evidence.json').read_text())
        evidence['work_start'] = earlier; evidence['freeze']['start']['observed_at'] = earlier
        (d / 'proof/evidence.json').write_text(json.dumps(evidence))
        self.edit(d / 'proof/start.json', work_start=earlier)
        self.edit(d / 'receipt/receipt.json', start=earlier)
        self.assertEqual(self.row('identity'), 'PASS')
        self.assertNotEqual(self.verdict()['verdict'], 'PASS')
        self.assertEqual(self.row(), 'FAIL')

    def test_start_baseline_rewrite_fails(self):
        self.edit(self.dirs[1] / 'proof/start.json', ledger_baseline_sha256='f' * 64)
        self.assertNotEqual(self.verdict()['verdict'], 'PASS')
        self.assertEqual(self.row(run=1), 'FAIL')

    def test_missing_launch_expectation_is_not_pass(self):
        shutil.rmtree(self.root / REGISTRY, ignore_errors=True)
        self.assertEqual(self.verdict()['verdict'], 'NO-DATA')
        self.assertEqual(self.row(), 'NO-DATA')

    def test_missing_work_admission_fails(self):
        (self.root / REGISTRY / 'RB' / 'work-start.json').unlink()
        self.assertEqual(self.row(), 'FAIL')

    def test_corrupt_or_duplicate_key_launch_record_is_not_pass(self):
        record = self.root / REGISTRY / 'RB' / 'launch.json'
        raw = record.read_text(); value = json.loads(raw)
        for text in ('{broken', raw.replace('"phase": "RB"', '"phase": "RC", "phase": "RB"'), '[]',
                     json.dumps(dict(value, schema='other')), json.dumps(dict(value, attempt_id='bad'))):
            record.write_text(text)
            self.assertEqual(self.row(), 'NO-DATA', text[:60])
            self.assertNotEqual(self.verdict()['verdict'], 'PASS')

    def test_launch_row_refuses_another_phase(self):
        # RC's own records agree with each other; judged as RB, only the recorded phase refuses.
        rows = A.check_run(self.dirs[1], 'RB')['checks']
        self.assertEqual(next(x['verdict'] for x in rows if x['check'] == 'launch_identity'), 'FAIL')

    def test_foreign_admission_record_fails(self):
        record = self.root / REGISTRY / 'RB' / 'work-start.json'; value = json.loads(record.read_text())
        for key, forged in (('attempt_id', 'c' * 32), ('schema', 'other')):
            record.write_text(json.dumps(dict(value, **{key: forged})))
            self.assertEqual(self.row(), 'FAIL', key)

    def test_launch_record_of_another_directory_is_not_pass(self):
        # A run directory copied elsewhere cannot carry its launch record with it.
        copy = self.root / 'copy'; copy.mkdir(); moved = copy / 'RB'
        shutil.copytree(self.dirs[0], moved)
        shutil.copytree(self.root / REGISTRY / 'RB', copy / REGISTRY / 'RB')
        row = next(x['verdict'] for x in A.check_run(moved, 'RB')['checks'] if x['check'] == 'launch_identity')
        self.assertEqual(row, 'NO-DATA')


if __name__ == '__main__':
    # Only this file's cases: the inherited admission and acceptance suites run on their own.
    suite = unittest.TestSuite(cls(n) for cls in (Launcher, Acceptance) for n in cls.__dict__ if n.startswith('test_'))
    result = unittest.TextTestRunner().run(suite)
    sys.exit(not result.wasSuccessful())
