'''L5d.a capture harness: one serial gate run with load snapshots.

Standard library only. No subprocess. The caller supplies run_gate.
'''

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, TypedDict, Union

from scripts.perf_audit_load import LoadSnapshot, snapshot
from scripts.gate_order import parse_log


class CaptureInputError(ValueError):
    '''Refuse invalid capture or per-check input in this module.'''


class CaptureRecord(TypedDict):
    label: str
    started_at: str
    ended_at: str
    wall_seconds: int
    gate_exit_code: int
    gate_log_path: str
    load_before: LoadSnapshot
    load_after: LoadSnapshot


_LABEL_RE = re.compile(r'[a-z0-9-]+')


def per_check_seconds(gate_log: Union[str, Path]) -> Dict[str, float]:
    '''Validate gate_log before calling gate_order.parse_log.

    A gate_log that is not a str or pathlib.Path raises CaptureInputError.
    NoDataError and CorruptLogError from gate_order.parse_log propagate
    unchanged only for a real path.
    '''
    if not isinstance(gate_log, (str, Path)):
        raise CaptureInputError('gate_log must be str or pathlib.Path')
    results = parse_log(gate_log)
    return {row['name']: float(row['seconds']) for row in results}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def capture(label: str, out_dir: Union[str, Path], gate_script: Union[str, Path],
            run_gate: Callable[[List[str], Path], int],
            snapshot_fn: Callable[[], Dict[str, object]] = snapshot) -> CaptureRecord:
    '''Run the gate once serially and return a capture record.

    Every positional parameter is validated before any side effect.
    snapshot_fn supplies load_before and load_after; the default reads the OS
    load only, and the classifier accepts a record only when the block carries
    source uptime+ps, which scripts/perf_audit_run.py supplies.
    '''
    if not isinstance(label, str):
        raise CaptureInputError('label must be str')
    if not _LABEL_RE.fullmatch(label):
        raise CaptureInputError('label must match [a-z0-9-]+')
    if not isinstance(out_dir, (str, Path)):
        raise CaptureInputError('out_dir must be str or pathlib.Path')
    if not isinstance(gate_script, (str, Path)):
        raise CaptureInputError('gate_script must be str or pathlib.Path')
    if not callable(run_gate):
        raise CaptureInputError('run_gate must be callable')
    if not callable(snapshot_fn):
        raise CaptureInputError('snapshot_fn must be callable')

    out_path = Path(out_dir)
    log_path = out_path / (label + '-gate.log')
    capture_path = out_path / (label + '-capture.json')

    if capture_path.exists():
        raise SystemExit(2)

    if out_path.exists() and not out_path.is_dir():
        raise CaptureInputError('out_dir exists and is not a directory')

    out_path.mkdir(parents=True, exist_ok=True)

    load_before = snapshot_fn()
    started_at = _iso_now()
    start = time.monotonic()
    try:
        gate_exit_code = run_gate(['sh', str(gate_script)], log_path)
    except Exception as exc:
        raise CaptureInputError('run_gate raised ' + type(exc).__name__) from exc
    end = time.monotonic()
    ended_at = _iso_now()
    load_after = snapshot_fn()

    if type(gate_exit_code) is not int:
        raise CaptureInputError('run_gate must return int')

    record: CaptureRecord = {
        'label': label,
        'started_at': started_at,
        'ended_at': ended_at,
        'wall_seconds': int(round(end - start)),
        'gate_exit_code': gate_exit_code,
        'gate_log_path': str(log_path),
        'load_before': load_before,
        'load_after': load_after,
    }

    tmp_path = capture_path.with_name(capture_path.name + '.tmp')
    with open(tmp_path, 'w', encoding='utf-8') as handle:
        json.dump(record, handle, sort_keys=True, indent=2)
        handle.write('\n')
    os.replace(str(tmp_path), str(capture_path))
    return record
