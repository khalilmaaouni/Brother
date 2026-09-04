"""Calibration for scripts/coverage_check.py (task 0 of docs/plan/UNIFIED-WBS.md).

The live plan is exercised once, informationally: it is a human-authored
narrative and is not expected to be green (many BMU/DS/SBE ids are only
covered through replan row numbers the checker cannot resolve back to an
id, which is a real gap, not a bug). The pass/fail calibration itself runs
against a minimal, fully-consistent SYNTHETIC fixture built in this file,
per the task brief's own fallback instruction.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

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

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKER = os.path.join(REPO_ROOT, 'scripts', 'coverage_check.py')


def run_checker(root):
    proc = subprocess.run(
        [sys.executable, CHECKER, '--root', root],
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


BMU_FIXTURE = {
    "items": [
        {"id": "X1", "state": "queued", "stage": "intent"},
        {"id": "X2", "state": "done", "stage": "method"},
        {"id": "X3", "state": "blocked", "stage": "human-decision"},
    ]
}

DS_FIXTURE = [
    {"id": "Y1", "state": "queued", "stage": "provenance"},
    {"id": "Y2", "state": "done", "stage": "release"},
]

# Third snapshot, mirroring the real repo's docs/plan/QUEUE.json (a plain
# list, not a {"items": [...]} wrapper -- load_json() accepts both shapes).
QUEUE_FIXTURE = [
    {"id": "W1", "state": "queued", "stage": "required-proof"},
]

APPENDIX_FIXTURE = """# Appendix: synthetic fixture

## BrotherModeUp docs/plan/QUEUE.json: 3 items, 2 open
- queued / intent: X1
- blocked / human-decision: X3

## BrotherDS docs/plan/QUEUE.json: 2 items, 1 open
- queued / provenance: Y1

## BrotherSBE (no machine queue; sources are documents)
- ids: Z1 to Z2
"""

WBS_FIXTURE = """# Synthetic plan

## The phases

### P0. Setup (stage: intent)
- X1 handled here. W1 handled here too.

### P1. Work (stage: method, then human-decision)
- X3 handled here. Z1 and Z2 covered here too.

## Parked, with reasons and flip conditions
- Y1 parked for reasons; flips when ready.

## Nothing forgotten: the coverage mechanism
- SBE document sources: Z1 to Z2.
"""


def write_fixture(root):
    sources = os.path.join(root, 'docs', 'plan', 'sources')
    os.makedirs(sources, exist_ok=True)
    with open(os.path.join(sources, 'bmu-queue-2026-08-22.json'), 'w') as f:
        json.dump(BMU_FIXTURE, f)
    with open(os.path.join(sources, 'ds-queue-2026-08-22.json'), 'w') as f:
        json.dump(DS_FIXTURE, f)
    with open(os.path.join(root, 'docs', 'plan', 'QUEUE.json'), 'w') as f:
        json.dump(QUEUE_FIXTURE, f)
    with open(os.path.join(sources, 'open-items-appendix.md'), 'w') as f:
        f.write(APPENDIX_FIXTURE)
    with open(os.path.join(root, 'docs', 'plan', 'UNIFIED-WBS.md'), 'w') as f:
        f.write(WBS_FIXTURE)


class LivePlanInformational(unittest.TestCase):
    """Not a pass/fail gate: reports the live plan's real state."""

    def test_live_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(os.path.join(REPO_ROOT, 'docs'), os.path.join(tmp, 'docs'))
            code, out, err = run_checker(tmp)
            self.assertIn(code, (0, 1), msg=f'unexpected NO-DATA/crash on the live plan: {err}')
            if code == 0:
                self.assertEqual(code, 0)
            else:
                # Real, expected finding: print it so a verbose run surfaces
                # it, but do not fail calibration on the live document's own
                # narrative gaps.
                print('\n--- live plan is not green (expected, informational) ---')
                print(out)


class SyntheticFixtureCalibration(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        write_fixture(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def wbs_path(self):
        return os.path.join(self.tmp, 'docs', 'plan', 'UNIFIED-WBS.md')

    def appendix_path(self):
        return os.path.join(self.tmp, 'docs', 'plan', 'sources', 'open-items-appendix.md')

    def bmu_path(self):
        return os.path.join(self.tmp, 'docs', 'plan', 'sources', 'bmu-queue-2026-08-22.json')

    def test_baseline_is_green(self):
        code, out, err = run_checker(self.tmp)
        self.assertEqual(code, 0, msg=out + err)
        self.assertIn('exit 0', out)

    def test_removed_open_id_is_orphan(self):
        with open(self.wbs_path()) as f:
            text = f.read()
        text = text.replace('- X3 handled here. Z1 and Z2 covered here too.',
                             '- Z1 and Z2 covered here too.')
        with open(self.wbs_path(), 'w') as f:
            f.write(text)
        code, out, err = run_checker(self.tmp)
        self.assertEqual(code, 1, msg=out + err)
        self.assertIn('X3', out)
        self.assertIn('FAIL:', out)

    def test_duplicated_id_fails(self):
        with open(self.wbs_path()) as f:
            text = f.read()
        text = text.replace('- X1 handled here.', '- X1 handled here. X1 again.')
        text = text.replace('- X3 handled here.', '- X3 handled here. X1 here too.')
        with open(self.wbs_path(), 'w') as f:
            f.write(text)
        code, out, err = run_checker(self.tmp)
        self.assertEqual(code, 1, msg=out + err)
        self.assertIn('X1', out)
        self.assertIn('duplicate', out)

    def test_missing_snapshot_is_no_data(self):
        os.remove(self.bmu_path())
        code, out, err = run_checker(self.tmp)
        self.assertEqual(code, 2, msg=out + err)
        self.assertIn('NO-DATA:', out)

    def test_appendix_count_drift_fails(self):
        with open(self.appendix_path()) as f:
            text = f.read()
        text = text.replace('3 items, 2 open', '3 items, 3 open')
        with open(self.appendix_path(), 'w') as f:
            f.write(text)
        code, out, err = run_checker(self.tmp)
        self.assertEqual(code, 1, msg=out + err)
        self.assertIn('drift', out)

    def test_table_row_parked_without_parked_entry_fails(self):
        # The coverage table is the ledger: a row naming PARKED must still
        # have a real "## Parked" line with a flip condition. X3 already has
        # a valid P1 prose mapping in the baseline fixture; giving it a
        # table row instead (with no matching Parked entry) must override
        # that prose mapping and fail, per the table-is-the-ledger rule.
        with open(self.wbs_path()) as f:
            text = f.read()
        table = (
            '## Coverage table: every open id, one row\n\n'
            '| id | source | title | phase | stage | note |\n'
            '|---|---|---|---|---|---|\n'
            '| X3 | bmu-queue | blocked item | PARKED | human-decision |  |\n\n'
        )
        text = text.replace('## Parked, with reasons and flip conditions',
                             table + '## Parked, with reasons and flip conditions')
        with open(self.wbs_path(), 'w') as f:
            f.write(text)
        code, out, err = run_checker(self.tmp)
        self.assertEqual(code, 1, msg=out + err)
        self.assertIn('X3', out)
        self.assertIn('parked without a flip condition', out)

    def test_unknown_table_row_is_extra_not_fail(self):
        # An id with no snapshot at all (e.g. a handover's own id, never fed
        # into bmu/ds/QUEUE.json) is not governed by the coverage rule: its
        # table row is counted as 'extra' and the run still exits 0.
        with open(self.wbs_path()) as f:
            text = f.read()
        table = (
            '## Coverage table: every open id, one row\n\n'
            '| id | source | title | phase | stage | note |\n'
            '|---|---|---|---|---|---|\n'
            '| SBEH-Z9 | handover | an id no snapshot knows about | P0 | method |  |\n\n'
        )
        text = text.replace('## Parked, with reasons and flip conditions',
                             table + '## Parked, with reasons and flip conditions')
        with open(self.wbs_path(), 'w') as f:
            f.write(text)
        code, out, err = run_checker(self.tmp)
        self.assertEqual(code, 0, msg=out + err)
        self.assertNotIn('SBEH-Z9', ''.join(ln for ln in out.splitlines() if ln.startswith('FAIL:')))
        self.assertIn('1 extra', out)

    def test_unaliased_table_stage_fails(self):
        # A coverage-table stage that is neither on the chain nor resolved
        # by the "Stage aliases" line is a FAIL, replacing the old
        # accept-as-is STAGE-NOTE behaviour.
        with open(self.wbs_path()) as f:
            text = f.read()
        table = (
            '## Coverage table: every open id, one row\n\n'
            '| id | source | title | phase | stage | note |\n'
            '|---|---|---|---|---|---|\n'
            '| X1 | bmu-queue | handled here | P0 | made-up-stage |  |\n\n'
        )
        text = text.replace('## Parked, with reasons and flip conditions',
                             table + '## Parked, with reasons and flip conditions')
        with open(self.wbs_path(), 'w') as f:
            f.write(text)
        code, out, err = run_checker(self.tmp)
        self.assertEqual(code, 1, msg=out + err)
        self.assertIn('made-up-stage', out)
        self.assertIn('not on chain and not aliased', out)


if __name__ == '__main__':
    unittest.main()
