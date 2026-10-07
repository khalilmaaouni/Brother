import json
import math
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from scripts.perf_audit_capture import CaptureInputError, capture, per_check_seconds
from scripts.perf_audit_load import snapshot
from scripts import gate_order
from scripts import perf_audit_load
from scripts.perf_audit_load import (
    LoadSnapshotInputError,
    count_processes_from_proc,
    parse_loadavg,
    snapshot_full,
)
from scripts.perf_audit_classify import Verdict, classify


class RecordingRunner:
    def __init__(self, exit_code=0, log_line='PASS    exit 0   version-truth         12s  ok\n'):
        self.calls = []
        self.exit_code = exit_code
        self.log_line = log_line

    def __call__(self, argv, log_path):
        self.calls.append((list(argv), Path(log_path)))
        with open(log_path, 'w', encoding='utf-8') as handle:
            handle.write(self.log_line)
        return self.exit_code


class CaptureTests(unittest.TestCase):
    def __init__(self, methodName='runTest'):
        if not isinstance(methodName, str):
            raise ValueError('CaptureTests method name must be str')
        super().__init__(methodName=methodName)

    def test_refuses_non_string_method_name(self):
        for value in [None, 123, b'bytes', [], {}]:
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    CaptureTests(value)

    def test_hostile_parameters_are_refused_before_side_effects(self):
        hostile = [None, 123, b'bytes', [], {}]
        with tempfile.TemporaryDirectory() as base:
            out = Path(base) / 'out'
            gate = Path(base) / 'gate.sh'
            runner = RecordingRunner()
            for value in hostile:
                with self.subTest(param='label', value=repr(value)):
                    with self.assertRaises(CaptureInputError):
                        capture(value, out, gate, runner)
                    self.assertFalse(out.exists())
                with self.subTest(param='out_dir', value=repr(value)):
                    with self.assertRaises(CaptureInputError):
                        capture('ok', value, gate, runner)
                with self.subTest(param='gate_script', value=repr(value)):
                    with self.assertRaises(CaptureInputError):
                        capture('ok', out, value, runner)
                    self.assertFalse(out.exists())
                with self.subTest(param='run_gate', value=repr(value)):
                    with self.assertRaises(CaptureInputError):
                        capture('ok', out, gate, value)
                    self.assertFalse(out.exists())

    def test_label_regex_is_refused(self):
        with tempfile.TemporaryDirectory() as base:
            out = Path(base) / 'out'
            gate = Path(base) / 'gate.sh'
            runner = RecordingRunner()
            for label in ['BAD', 'has_underscore', '', 'a b', 'UPPER', 'a/b']:
                with self.subTest(label=label):
                    with self.assertRaises(CaptureInputError):
                        capture(label, out, gate, runner)
                    self.assertFalse(out.exists())

    def test_capture_refuses_out_dir_that_is_a_file(self):
        with tempfile.TemporaryDirectory() as base:
            out = Path(base) / 'out'
            out.write_text('not a directory', encoding='utf-8')
            gate = Path(base) / 'required_fast.sh'
            runner = RecordingRunner()
            with self.assertRaises(CaptureInputError):
                capture('run-3', out, gate, runner)
            self.assertEqual(runner.calls, [])
            self.assertTrue(out.is_file())

    def test_capture_refuses_when_run_gate_raises(self):
        def boom(argv, log_path):
            raise RuntimeError('boom')
        with tempfile.TemporaryDirectory() as base:
            out = Path(base) / 'out'
            gate = Path(base) / 'required_fast.sh'
            with self.assertRaises(CaptureInputError):
                capture('run-4', out, gate, boom)
            self.assertFalse((out / 'run-4-capture.json').exists())

    def test_per_check_seconds_refuses_non_path_and_propagates_for_real_path(self):
        for value in [None, 123, b'bytes', [], {}]:
            with self.subTest(value=repr(value)):
                with self.assertRaises(CaptureInputError):
                    per_check_seconds(value)
        with tempfile.TemporaryDirectory() as base:
            missing = Path(base) / 'missing.log'
            with self.assertRaises(gate_order.NoDataError):
                per_check_seconds(missing)
            corrupt = Path(base) / 'corrupt.log'
            corrupt.write_text('PASS exit notanumber name 12s boom\n', encoding='utf-8')
            with self.assertRaises(gate_order.CorruptLogError):
                per_check_seconds(corrupt)
            good = Path(base) / 'good.log'
            good.write_text('PASS    exit 0   version-truth         12s  ok\n', encoding='utf-8')
            self.assertEqual(per_check_seconds(good), {'version-truth': 12.0})

    def test_snapshot_fields(self):
        snap = snapshot()
        self.assertEqual(set(snap.keys()), {
            'load_average_1m', 'load_average_5m', 'load_average_15m',
            'cpu_count', 'captured_at',
        })
        for key in ('load_average_1m', 'load_average_5m', 'load_average_15m'):
            self.assertIsInstance(snap[key], float)
        self.assertTrue(snap['cpu_count'] is None or isinstance(snap['cpu_count'], int))
        self.assertIsInstance(snap['captured_at'], str)
        parsed = datetime.fromisoformat(snap['captured_at'])
        self.assertIsNotNone(parsed.tzinfo)

    def test_capture_runs_once_with_expected_argv_and_writes_json(self):
        with tempfile.TemporaryDirectory() as base:
            out = Path(base) / 'out'
            gate = Path(base) / 'required_fast.sh'
            runner = RecordingRunner()
            record = capture('run-1', out, gate, runner)
            self.assertEqual(len(runner.calls), 1)
            argv, log_path = runner.calls[0]
            self.assertEqual(argv, ['sh', str(gate)])
            self.assertEqual(log_path, out / 'run-1-gate.log')
            self.assertTrue(log_path.exists())
            capture_path = out / 'run-1-capture.json'
            self.assertTrue(capture_path.exists())
            with open(capture_path, 'r', encoding='utf-8') as handle:
                loaded = json.load(handle)
            self.assertEqual(loaded['label'], 'run-1')
            self.assertEqual(loaded['gate_exit_code'], 0)
            self.assertEqual(loaded['gate_log_path'], str(log_path))
            self.assertIsInstance(loaded['wall_seconds'], int)
            self.assertIn('load_before', loaded)
            self.assertIn('load_after', loaded)
            self.assertEqual(per_check_seconds(log_path), {'version-truth': 12.0})

    def test_capture_uses_the_supplied_snapshot_fn_for_both_blocks(self):
        calls = []

        def fake_snapshot():
            calls.append(1)
            return {'load_average_1m': 1.5, 'cpu_count': 8, 'claude_processes': 1, 'source': 'uptime+ps'}

        with tempfile.TemporaryDirectory() as base:
            record = capture('run-2', Path(base) / 'out', Path(base) / 'g.sh', RecordingRunner(), fake_snapshot)
        self.assertEqual(len(calls), 2)
        self.assertEqual(record['load_before']['source'], 'uptime+ps')
        self.assertEqual(record['load_after']['claude_processes'], 1)

    def test_capture_refuses_a_non_callable_snapshot_fn_before_side_effects(self):
        with tempfile.TemporaryDirectory() as base:
            out = Path(base) / 'out'
            for bad in (None, 3, 'snapshot', {}):
                with self.subTest(bad=repr(bad)):
                    with self.assertRaises(CaptureInputError):
                        capture('run-3', out, Path(base) / 'g.sh', RecordingRunner(), bad)
            self.assertFalse(out.exists())

    def test_capture_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as base:
            out = Path(base) / 'out'
            out.mkdir()
            gate = Path(base) / 'required_fast.sh'
            capture_path = out / 'run-2-capture.json'
            old = json.dumps({'label': 'old'}) + '\n'
            capture_path.write_text(old, encoding='utf-8')
            runner = RecordingRunner()
            with self.assertRaises(SystemExit) as cm:
                capture('run-2', out, gate, runner)
            self.assertEqual(cm.exception.code, 2)
            self.assertEqual(runner.calls, [])
            self.assertEqual(capture_path.read_text(encoding='utf-8'), old)


class LoadSnapshotTests(unittest.TestCase):
    def __init__(self, methodName='runTest'):
        if not isinstance(methodName, str):
            raise ValueError('LoadSnapshotTests method name must be str')
        super().__init__(methodName=methodName)

    def test_refuses_non_string_method_name(self):
        for value in [None, 123, b'bytes', [], {}]:
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    LoadSnapshotTests(value)

    def test_snapshot_reads_os_load_and_cpu(self):
        with mock.patch.object(perf_audit_load.os, 'getloadavg', return_value=(1.25, 2.5, 3.75)):
            with mock.patch.object(perf_audit_load.os, 'cpu_count', return_value=7):
                snap = snapshot()
        self.assertEqual(set(snap.keys()), {
            'load_average_1m', 'load_average_5m', 'load_average_15m',
            'cpu_count', 'captured_at',
        })
        self.assertEqual(snap['load_average_1m'], 1.25)
        self.assertEqual(snap['load_average_5m'], 2.5)
        self.assertEqual(snap['load_average_15m'], 3.75)
        self.assertEqual(snap['cpu_count'], 7)
        self.assertIsNotNone(datetime.fromisoformat(snap['captured_at']).tzinfo)

    def test_snapshot_does_not_run_ps(self):
        source = Path(perf_audit_load.__file__).read_text(encoding='utf-8')
        for banned in ('import subprocess', 'from subprocess', 'os.system', 'os.popen', "'ps'", '"ps"'):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, source)

    def test_snapshot_does_not_crash_on_this_host(self):
        for _ in range(3):
            snap = snapshot()
            self.assertIsInstance(snap['load_average_1m'], float)
            self.assertIsInstance(snap['captured_at'], str)

    def test_snapshot_full_reads_fixture_loadavg_and_proc(self):
        with tempfile.TemporaryDirectory() as base:
            loadavg = Path(base) / 'loadavg'
            loadavg.write_text('1.25 2.50 3.75 1/100 12345\n', encoding='utf-8')
            proc = Path(base) / 'proc'
            proc.mkdir()
            for pid, comm in [('10', 'claude'), ('11', 'python3'), ('12', 'bash')]:
                child = proc / pid
                child.mkdir()
                (child / 'comm').write_text(comm + '\n', encoding='utf-8')
            with mock.patch.object(perf_audit_load, 'LOADAVG_PATH', str(loadavg)):
                with mock.patch.object(perf_audit_load, 'PROC_ROOT', str(proc)):
                    snap = snapshot_full()
        self.assertEqual(snap['source'], 'proc')
        self.assertEqual(snap['load_avg_1m'], 1.25)
        self.assertEqual(snap['load_avg_5m'], 2.5)
        self.assertEqual(snap['load_avg_15m'], 3.75)
        self.assertEqual(snap['claude_processes'], 1)
        self.assertEqual(snap['python_processes'], 1)
        self.assertEqual(snap['total_processes'], 3)

    def test_snapshot_full_missing_loadavg_is_unread_not_zero(self):
        with tempfile.TemporaryDirectory() as base:
            missing = Path(base) / 'missing-loadavg'
            proc = Path(base) / 'proc'
            proc.mkdir()
            with mock.patch.object(perf_audit_load, 'LOADAVG_PATH', str(missing)):
                with mock.patch.object(perf_audit_load, 'PROC_ROOT', str(proc)):
                    snap = snapshot_full()
        self.assertEqual(snap['source'], 'unread')
        self.assertTrue(math.isnan(snap['load_avg_1m']))
        self.assertTrue(math.isnan(snap['load_avg_5m']))
        self.assertTrue(math.isnan(snap['load_avg_15m']))

    def test_snapshot_full_missing_proc_root_is_unread(self):
        with tempfile.TemporaryDirectory() as base:
            loadavg = Path(base) / 'loadavg'
            loadavg.write_text('1.0 2.0 3.0 1/100 1\n', encoding='utf-8')
            missing_proc = Path(base) / 'missing-proc'
            with mock.patch.object(perf_audit_load, 'LOADAVG_PATH', str(loadavg)):
                with mock.patch.object(perf_audit_load, 'PROC_ROOT', str(missing_proc)):
                    snap = snapshot_full()
        self.assertEqual(snap['source'], 'unread')
        self.assertTrue(math.isnan(snap['load_avg_1m']))

    def test_parse_loadavg_refuses_hostile_input(self):
        hostile = [None, 123, b'bytes', [], {}, '', 'not numbers', 'nan nan nan']
        for value in hostile:
            with self.subTest(value=repr(value)):
                with self.assertRaises(LoadSnapshotInputError):
                    parse_loadavg(value)

    def test_count_processes_from_proc_refuses_hostile_input(self):
        hostile = [None, 123, b'bytes', [], {}]
        for value in hostile:
            with self.subTest(value=repr(value)):
                with self.assertRaises(LoadSnapshotInputError):
                    count_processes_from_proc(value)
        with tempfile.TemporaryDirectory() as base:
            with self.assertRaises(LoadSnapshotInputError):
                count_processes_from_proc(Path(base) / 'missing')


class ClassifyTests(unittest.TestCase):
    def __init__(self, methodName='runTest'):
        if not isinstance(methodName, str):
            raise ValueError('ClassifyTests method name must be str')
        super().__init__(methodName=methodName)

    def test_refuses_non_string_method_name(self):
        for value in [None, 123, b'bytes', [], {}]:
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    ClassifyTests(value)

    def _write_log(self, folder, filename, checks):
        path = Path(folder) / filename
        lines = []
        for name, seconds in checks:
            lines.append('PASS    exit 0   {:<21}{:>3}s  ok\n'.format(name, seconds))
        path.write_text(''.join(lines), encoding='utf-8')
        return path

    def _load_block(self, load_avg, cpu_count, source):
        return {
            'load_average_1m': load_avg,
            'load_average_5m': load_avg,
            'load_average_15m': load_avg,
            'cpu_count': cpu_count,
            'captured_at': '2026-01-01T00:00:00+00:00',
            'source': source,
        }

    def _record(self, wall, log_path, load_avg, cpu_count, source='uptime+ps'):
        return {
            'label': 'sample',
            'started_at': '2026-01-01T00:00:00+00:00',
            'ended_at': '2026-01-01T00:00:00+00:00',
            'wall_seconds': wall,
            'gate_exit_code': 0,
            'gate_log_path': str(log_path),
            'load_before': self._load_block(load_avg, cpu_count, source),
            'load_after': self._load_block(load_avg, cpu_count, source),
        }

    def test_no_data_when_quiet_source_is_not_uptime_ps(self):
        with tempfile.TemporaryDirectory() as base:
            log = self._write_log(base, 'quiet.log', [('gate-a', 12)])
            quiet = self._record(100, log, 1.0, 4, source='unread')
            loaded = self._record(400, log, 16.0, 4)
            result = classify(quiet, loaded)
        self.assertEqual(result['verdict'], Verdict.NO_DATA)

    def test_a_quiet_record_above_the_load_bar_names_no_cause(self):
        """H2 (attack 2026-09-30): the classifier read no load from the QUIET record, so a loaded capture
        labelled quiet could classify CODE_REGRESSION and write a false cause. A quiet record whose 1 minute
        load is above the quiet bar is NO_DATA, whatever the walls say."""
        from scripts.perf_audit_classify import QUIET_LOAD
        with tempfile.TemporaryDirectory() as base:
            log = self._write_log(base, 'quiet.log', [('gate-a', 200)])
            quiet = self._record(400, log, QUIET_LOAD + 0.1, 8)
            loaded = self._record(1200, log, 40.0, 8)
            result = classify(quiet, loaded)
            self.assertEqual(result['verdict'], Verdict.NO_DATA)
            self.assertIn('quiet bar', result['reasoning'])
            quiet_ok = self._record(400, log, QUIET_LOAD, 8)
            self.assertEqual(classify(quiet_ok, loaded)['verdict'], Verdict.CODE_REGRESSION)

    def test_the_quiet_bar_is_the_done_checks_bar(self):
        from scripts.perf_audit_classify import QUIET_LOAD
        from scripts import donecheck_l5d
        self.assertIs(donecheck_l5d.QUIET_LOAD, QUIET_LOAD)

    def test_no_data_when_loaded_source_is_not_uptime_ps(self):
        with tempfile.TemporaryDirectory() as base:
            log = self._write_log(base, 'quiet.log', [('gate-a', 12)])
            quiet = self._record(100, log, 1.0, 4)
            loaded = self._record(400, log, 16.0, 4, source='proc')
            result = classify(quiet, loaded)
        self.assertEqual(result['verdict'], Verdict.NO_DATA)

    def test_no_data_when_gate_log_missing(self):
        with tempfile.TemporaryDirectory() as base:
            missing = Path(base) / 'missing.log'
            quiet = self._record(100, missing, 1.0, 4)
            loaded = self._record(400, missing, 16.0, 4)
            result = classify(quiet, loaded)
        self.assertEqual(result['verdict'], Verdict.NO_DATA)

    def test_no_data_when_gate_log_is_corrupt(self):
        with tempfile.TemporaryDirectory() as base:
            corrupt = Path(base) / 'corrupt.log'
            corrupt.write_text('PASS exit notanumber name 12s boom\n', encoding='utf-8')
            quiet = self._record(100, corrupt, 1.0, 4)
            loaded = self._record(400, corrupt, 16.0, 4)
            result = classify(quiet, loaded)
        self.assertEqual(result['verdict'], Verdict.NO_DATA)

    def test_machine_load_verdict(self):
        with tempfile.TemporaryDirectory() as base:
            qlog = self._write_log(base, 'quiet.log', [('gate-a', 12)])
            llog = self._write_log(base, 'loaded.log', [('gate-a', 40)])
            quiet = self._record(100, qlog, 1.0, 4)
            loaded = self._record(400, llog, 16.0, 4)
            result = classify(quiet, loaded)
        self.assertEqual(result['verdict'], Verdict.MACHINE_LOAD)
        self.assertEqual(result['quiet_wall_seconds'], 100)
        self.assertEqual(result['loaded_wall_seconds'], 400)
        self.assertEqual(result['loaded_load_avg_1m'], 16.0)
        self.assertEqual(result['loaded_cpu_count'], 4)

    def test_code_regression_verdict(self):
        with tempfile.TemporaryDirectory() as base:
            qlog = self._write_log(base, 'quiet.log', [('gate-a', 200), ('gate-b', 120)])
            llog = self._write_log(base, 'loaded.log', [('gate-a', 205), ('gate-b', 125)])
            quiet = self._record(330, qlog, 1.0, 4)
            loaded = self._record(450, llog, 1.0, 4)
            result = classify(quiet, loaded)
        self.assertEqual(result['verdict'], Verdict.CODE_REGRESSION)
        self.assertEqual(result['top_hog_quiet'], 'gate-a')
        self.assertEqual(result['top_hog_loaded'], 'gate-a')

    def test_stale_target_verdict(self):
        with tempfile.TemporaryDirectory() as base:
            qlog = self._write_log(base, 'quiet.log', [('gate-a', 12), ('gate-b', 5)])
            llog = self._write_log(base, 'loaded.log', [('gate-b', 11), ('gate-a', 4)])
            quiet = self._record(100, qlog, 1.0, 4)
            loaded = self._record(100, llog, 1.0, 4)
            result = classify(quiet, loaded)
        self.assertEqual(result['verdict'], Verdict.STALE_TARGET)
        self.assertEqual(result['top_hog_quiet'], 'gate-a')
        self.assertEqual(result['top_hog_loaded'], 'gate-b')

    def test_inconclusive_when_all_quiet_means_are_zero_or_one(self):
        with tempfile.TemporaryDirectory() as base:
            qlog = self._write_log(base, 'quiet.log', [('gate-a', 1), ('gate-b', 0)])
            llog = self._write_log(base, 'loaded.log', [('gate-b', 1), ('gate-a', 0)])
            quiet = self._record(100, qlog, 1.0, 4)
            loaded = self._record(100, llog, 1.0, 4)
            result = classify(quiet, loaded)
        self.assertEqual(result['verdict'], Verdict.INCONCLUSIVE)
        self.assertIn('UNKNOWN', result['reasoning'])

    def test_inconclusive_when_no_rule_matches(self):
        with tempfile.TemporaryDirectory() as base:
            qlog = self._write_log(base, 'quiet.log', [('gate-a', 200), ('gate-b', 100)])
            llog = self._write_log(base, 'loaded.log', [('gate-a', 210), ('gate-b', 110)])
            quiet = self._record(250, qlog, 1.0, 4)
            loaded = self._record(260, llog, 1.0, 4)
            result = classify(quiet, loaded)
        self.assertEqual(result['verdict'], Verdict.INCONCLUSIVE)

    def test_default_target_seconds_is_ninety(self):
        with tempfile.TemporaryDirectory() as base:
            qlog = self._write_log(base, 'quiet.log', [('gate-a', 12)])
            llog = self._write_log(base, 'loaded.log', [('gate-a', 40)])
            quiet = self._record(100, qlog, 1.0, 4)
            loaded = self._record(400, llog, 16.0, 4)
            result = classify(quiet, loaded)
        self.assertEqual(result['target_seconds'], 90)

    def test_classification_shape(self):
        with tempfile.TemporaryDirectory() as base:
            log = self._write_log(base, 'quiet.log', [('gate-a', 12)])
            quiet = self._record(100, log, 1.0, 4)
            loaded = self._record(400, log, 16.0, 4)
            result = classify(quiet, loaded)
        self.assertEqual(set(result.keys()), {
            'verdict', 'quiet_wall_seconds', 'loaded_wall_seconds',
            'loaded_load_avg_1m', 'loaded_cpu_count', 'top_hog_quiet',
            'top_hog_loaded', 'target_seconds', 'reasoning',
        })

    def test_hostile_records_are_refused_not_crash(self):
        hostile = [None, 123, 'str', b'bytes', [], {}, True, float('nan')]
        for value in hostile:
            with self.subTest(value=repr(value)):
                result = classify(value, value)
                self.assertEqual(result['verdict'], Verdict.NO_DATA)
        with tempfile.TemporaryDirectory() as base:
            log = self._write_log(base, 'quiet.log', [('gate-a', 12)])
            quiet = self._record(100, log, 1.0, 4)
            loaded = self._record(400, log, 16.0, 4)
            for value in hostile:
                with self.subTest(quiet=repr(value)):
                    self.assertEqual(classify(value, loaded)['verdict'], Verdict.NO_DATA)
                with self.subTest(loaded=repr(value)):
                    self.assertEqual(classify(quiet, value)['verdict'], Verdict.NO_DATA)

    def test_hostile_target_seconds_is_refused(self):
        with tempfile.TemporaryDirectory() as base:
            log = self._write_log(base, 'quiet.log', [('gate-a', 12)])
            quiet = self._record(100, log, 1.0, 4)
            loaded = self._record(400, log, 16.0, 4)
            for value in [None, 'str', b'bytes', [], {}, True, float('nan'), 12.5]:
                with self.subTest(target=repr(value)):
                    self.assertEqual(classify(quiet, loaded, value)['verdict'], Verdict.NO_DATA)

    def test_bool_wall_seconds_is_refused(self):
        with tempfile.TemporaryDirectory() as base:
            log = self._write_log(base, 'quiet.log', [('gate-a', 12)])
            quiet = self._record(True, log, 1.0, 4)
            loaded = self._record(400, log, 16.0, 4)
            result = classify(quiet, loaded)
        self.assertEqual(result['verdict'], Verdict.NO_DATA)

    def test_nan_load_average_is_refused(self):
        with tempfile.TemporaryDirectory() as base:
            log = self._write_log(base, 'quiet.log', [('gate-a', 12)])
            quiet = self._record(100, log, float('nan'), 4)
            loaded = self._record(400, log, 16.0, 4)
            result = classify(quiet, loaded)
        self.assertEqual(result['verdict'], Verdict.NO_DATA)


class QuietGateTests(unittest.TestCase):
    """H2 (attack 2026-09-30): quiet is LOAD based at the source (1 minute load at or under the quiet bar), and
    the process counts are a secondary signal covering every worker kind by executable basename."""

    def _block(self, load, claude=0, workers=0, source='uptime+ps'):
        return {'captured_at': 't', 'load_average_1m': load, 'cpu_count': 8, 'claude_processes': claude,
                'worker_processes': workers, 'source': source}

    def test_quiet_needs_the_load_at_or_under_the_bar(self):
        from scripts import perf_audit_run as R
        self.assertTrue(R.is_quiet(self._block(R.QUIET_LOAD, claude=1)))
        self.assertFalse(R.is_quiet(self._block(R.QUIET_LOAD + 0.01, claude=1)))
        self.assertFalse(R.is_quiet(self._block(27.0, claude=1)))
        self.assertFalse(R.is_quiet(self._block(float('nan'), claude=0)))

    def test_quiet_still_needs_at_most_one_claude_process(self):
        from scripts import perf_audit_run as R
        self.assertFalse(R.is_quiet(self._block(1.0, claude=2)))
        self.assertFalse(R.is_quiet(self._block(1.0, claude=0, source='unread')))

    def test_workers_are_counted_by_basename_across_every_kind(self):
        from scripts import perf_audit_run as R
        ps = ('/x/claude\n/Applications/Claude.app/Contents/MacOS/Claude\n/x/Claude Helper\n'
              '/usr/local/bin/codex\n/x/bin/node\n/x/bin/npm\n/usr/bin/python3\n/x/Python\n/x/python3.13\n'
              'bash\n/x/nodejs-docs\n')
        counts = R.count_processes(ps)
        self.assertEqual(counts['claude_processes'], 1)
        self.assertEqual(counts['python_processes'], 3)
        self.assertEqual(counts['worker_processes'], 7)
        self.assertEqual(counts['total_processes'], 11)

    def test_a_reading_line_names_the_load_and_the_workers(self):
        from scripts import perf_audit_run as R
        line = R.reading_line(self._block(2.5, claude=1, workers=4))
        self.assertIn('load_1m=2.50', line)
        self.assertIn('workers=4', line)


class AuditDocTests(unittest.TestCase):
    DOC_PATH = os.path.join(REPO_ROOT, 'docs', 'architecture', 'L5D-PERFORMANCE-AUDIT.md')
    VERDICT_RE = re.compile(
        r'^verdict=(MACHINE_LOAD|CODE_REGRESSION|STALE_TARGET|INCONCLUSIVE|NO_DATA)$'
    )
    WALL_RE = re.compile(r'^wall_seconds=([0-9]+)$')

    def __init__(self, methodName='runTest'):
        if not isinstance(methodName, str):
            raise ValueError('AuditDocTests method name must be str')
        super().__init__(methodName=methodName)

    def test_refuses_non_string_method_name(self):
        for value in [None, 123, b'bytes', [], {}]:
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    AuditDocTests(value)

    def _read_doc(self):
        path = Path(self.DOC_PATH)
        if not path.is_file():
            self.fail('audit doc missing at ' + self.DOC_PATH)
        return path.read_text(encoding='utf-8')

    def test_doc_carries_one_verdict_and_one_wall_seconds_line(self) -> None:
        text = self._read_doc()
        lines = text.splitlines()
        verdict_hits = [line for line in lines if self.VERDICT_RE.match(line)]
        wall_hits = [line for line in lines if self.WALL_RE.match(line)]
        self.assertEqual(len(verdict_hits), 1, verdict_hits)
        self.assertEqual(len(wall_hits), 1, wall_hits)

    def test_every_path_the_doc_names_exists(self) -> None:
        text = self._read_doc()
        names = []
        inside = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped == 'PATHLIST-BEGIN':
                inside = True
                continue
            if stripped == 'PATHLIST-END':
                inside = False
                continue
            if inside and stripped and not stripped.startswith('#'):
                names.append(stripped)
        self.assertTrue(names, 'audit doc names no paths')
        for name in names:
            target = os.path.join(REPO_ROOT, name)
            self.assertTrue(os.path.isfile(target), name + ' does not exist')


class PinnedBars(unittest.TestCase):
    """Second attack 2026-09-30: X5 (the quiet bar's value) and 1a (a case sensitive SUBSTRING count) survived."""

    def test_the_quiet_bar_is_the_spec_value(self):
        from scripts.perf_audit_classify import QUIET_LOAD
        self.assertEqual(QUIET_LOAD, 8.0)   # the L5d plan record's bar, "load at or under 8" on this 8 core machine

    def test_a_name_that_only_contains_claude_is_not_counted(self):
        from scripts import perf_audit_run as R
        counts = R.count_processes('/x/claude\n/x/claude-helper\n/x/myclaude\n')
        self.assertEqual(counts['claude_processes'], 1)


if __name__ == '__main__':
    unittest.main()
