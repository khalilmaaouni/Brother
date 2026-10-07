'''M5.2 per lane pipeline.

R5 grade runs on the first returned build.
R6 probe starts on first PASS grade.
R7 judge uses a PASS adversary, missing adversary is NO-DATA.
R8 corrupt lane input blocks that lane only as BLOCKED.
'''
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from typing import Any

PASS = 'PASS'
FAIL = 'FAIL'
BLOCKED = 'BLOCKED'
NO_DATA = 'NO-DATA'


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    return True


def _is_valid_id(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if value == '':
        return False
    if value in ('.', '..'):
        return False
    if '/' in value or '\\' in value:
        return False
    return True


def _is_callable(value: Any) -> bool:
    return callable(value)


def _malformed_build(build: Any) -> bool:
    if not isinstance(build, dict):
        return True
    if not _is_valid_id(build.get('id')):
        return True
    if 'ok' not in build or not isinstance(build['ok'], bool):
        return True
    if 'seconds' in build and not _is_number(build['seconds']):
        return True
    if 'error' in build and not isinstance(build['error'], str):
        return True
    return False


def grade_lane(builds: list[dict], grade_fn: object) -> dict:
    '''Grade only the first returned build, never wait for later builds.

    Malformed build or grader failure blocks the lane.
    '''
    if not isinstance(builds, list):
        return {'status': BLOCKED, 'reason': 'builds is not a list'}
    if not builds:
        return {'status': BLOCKED, 'reason': 'empty builds'}
    if not _is_callable(grade_fn):
        return {'status': BLOCKED, 'reason': 'grade_fn is not callable'}
    first = builds[0]
    if _malformed_build(first):
        return {
            'status': BLOCKED,
            'reason': 'malformed build record',
            'build_id': first.get('id') if isinstance(first, dict) else None,
        }
    try:
        result = grade_fn(first)
    except Exception as exc:
        return {
            'status': BLOCKED,
            'reason': 'grade_fn error: ' + type(exc).__name__,
            'build_id': first.get('id'),
        }
    if not isinstance(result, dict):
        return {
            'status': BLOCKED,
            'reason': 'grade_fn returned no dict',
            'build_id': first.get('id'),
        }
    status = result.get('status')
    if status not in (PASS, FAIL):
        return {
            'status': BLOCKED,
            'reason': 'grade_fn returned unknown status',
            'build_id': first.get('id'),
        }
    return {'status': status, 'grade': result, 'build_id': first.get('id')}


def probe_lane(grade_pass: dict, probe_fn: object) -> dict:
    '''Probe only when a PASS grade is present.

    Malformed input or probe failure blocks the lane.
    '''
    if not isinstance(grade_pass, dict):
        return {'status': BLOCKED, 'reason': 'grade_pass is not a dict'}
    if grade_pass.get('status') != PASS:
        return {'status': NO_DATA, 'reason': 'no PASS grade'}
    if not _is_callable(probe_fn):
        return {'status': BLOCKED, 'reason': 'probe_fn is not callable'}
    try:
        result = probe_fn(grade_pass)
    except Exception as exc:
        return {'status': BLOCKED, 'reason': 'probe_fn error: ' + type(exc).__name__}
    if not isinstance(result, dict):
        return {'status': NO_DATA, 'reason': 'missing probe answer'}
    if result.get('status') != PASS:
        return {'status': NO_DATA, 'reason': 'probe did not answer PASS', 'probe': result}
    return result


def judge_lane(probes: list[dict], judge_fn: object) -> dict:
    '''Judge only on a PASS probe with a named adversary. Missing adversary is NO-DATA.

    Malformed input or judge failure blocks the lane.
    '''
    if not isinstance(probes, list):
        return {'status': BLOCKED, 'reason': 'probes is not a list'}
    if not _is_callable(judge_fn):
        return {'status': BLOCKED, 'reason': 'judge_fn is not callable'}
    pass_probes = [
        p
        for p in probes
        if isinstance(p, dict)
        and p.get('status') == PASS
        and _is_valid_id(p.get('adversary'))
    ]
    if not pass_probes:
        return {'status': NO_DATA, 'reason': 'no PASS probe'}
    chosen = pass_probes[0]
    try:
        result = judge_fn(chosen)
    except Exception as exc:
        return {'status': BLOCKED, 'reason': 'judge_fn error: ' + type(exc).__name__}
    if not isinstance(result, dict):
        return {'status': NO_DATA, 'reason': 'missing judge answer'}
    if result.get('status') not in (PASS, FAIL):
        return {
            'status': NO_DATA,
            'reason': 'judge returned unknown status',
            'judge': result,
        }
    return {'status': result.get('status'), 'judge': result}


def run_lane(lane: dict, grade_fn: object, probe_fn: object, judge_fn: object) -> dict:
    '''Run one lane independently. Corrupt lane input blocks this lane only.'''
    if not isinstance(lane, dict):
        return {
            'lane_id': None,
            'status': BLOCKED,
            'reason': 'lane is not a dict',
            'judge_called': False,
        }
    lane_id = lane.get('id')
    if not _is_valid_id(lane_id):
        return {
            'lane_id': None,
            'status': BLOCKED,
            'reason': 'lane id missing or invalid',
            'judge_called': False,
        }
    builds = lane.get('builds')
    if not isinstance(builds, list):
        return {
            'lane_id': lane_id,
            'status': BLOCKED,
            'reason': 'builds is not a list',
            'judge_called': False,
        }
    if not builds:
        return {
            'lane_id': lane_id,
            'status': BLOCKED,
            'reason': 'empty lane',
            'judge_called': False,
        }
    grade = grade_lane(builds, grade_fn)
    if grade.get('status') != PASS:
        return {
            'lane_id': lane_id,
            'status': grade.get('status', BLOCKED),
            'grade': grade,
            'judge_called': False,
        }
    probe = probe_lane(grade, probe_fn)
    if probe.get('status') != PASS:
        return {
            'lane_id': lane_id,
            'status': probe.get('status', NO_DATA),
            'grade': grade,
            'probe': probe,
            'judge_called': False,
        }
    if not _is_valid_id(probe.get('adversary')):
        return {
            'lane_id': lane_id,
            'status': NO_DATA,
            'reason': 'missing adversary',
            'grade': grade,
            'probe': probe,
            'judge_called': False,
        }
    judge = judge_lane([probe], judge_fn)
    return {
        'lane_id': lane_id,
        'status': judge.get('status', NO_DATA),
        'grade': grade,
        'probe': probe,
        'judge': judge,
        'judge_called': True,
    }


class VerdictRowError(ValueError):
    '''Deliberate refusal for verdict table operations.'''


def validate_verdict_row(row: dict) -> tuple[bool, str]:
    '''Validate a verdict row before it is written.

    Returns (True, '') when the row is safe to append.
    Returns (False, reason) with NO-DATA named when the row is corrupt.
    '''
    try:
        if not isinstance(row, dict):
            return False, NO_DATA + ': row is not a dict'
        lane_id = row.get('lane_id')
        if not _is_valid_id(lane_id):
            return False, NO_DATA + ': missing or invalid lane_id'
        status = row.get('status')
        if status not in (PASS, FAIL, BLOCKED, NO_DATA):
            return False, NO_DATA + ': missing or invalid status'
        for key in row:
            if not isinstance(key, str):
                return False, NO_DATA + ': non-string key in row'
        json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False)
        return True, ''
    except (TypeError, ValueError) as exc:
        return False, NO_DATA + ': row is not JSON serializable or has unhashable key: ' + type(exc).__name__


def _write_bytes_atomic(path: str, data: bytes) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or '.'
    fd, tmp_path = tempfile.mkstemp(prefix='.verdict-', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _acquire_lock(lock_path: str, timeout: float = 10.0) -> int:
    deadline = time.monotonic() + timeout
    while True:
        try:
            return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise VerdictRowError(BLOCKED + ': lock timeout: ' + lock_path)
            time.sleep(0.01)
        except OSError as exc:
            raise VerdictRowError(
                BLOCKED + ': cannot lock: ' + lock_path + ': ' + exc.strerror
            ) from exc


def _release_lock(lock_fd: int, lock_path: str) -> None:
    try:
        os.close(lock_fd)
    except OSError:
        pass
    try:
        os.unlink(lock_path)
    except OSError:
        pass


def append_verdict_row(table_path: str, row: dict) -> None:
    '''Append one finished lane row atomically and incrementally.

    Invalid row is refused as NO-DATA. Missing dir is created.
    Unwritable dir blocks with the path named.
    '''
    if not isinstance(table_path, str) or table_path == '':
        raise VerdictRowError(BLOCKED + ': table_path missing or invalid')
    ok, reason = validate_verdict_row(row)
    if not ok:
        raise VerdictRowError(reason)
    directory = os.path.dirname(os.path.abspath(table_path)) or '.'
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        raise VerdictRowError(
            BLOCKED + ': cannot create table dir for ' + table_path + ': ' + directory + ': ' + exc.strerror
        ) from exc
    lock_path = table_path + '.lock'
    lock_fd = None
    try:
        lock_fd = _acquire_lock(lock_path)
        rows = read_verdict_table(table_path)
        rows.append(row)
        lines = [
            json.dumps(item, sort_keys=True, ensure_ascii=False, allow_nan=False)
            for item in rows
        ]
        data = (chr(10).join(lines) + chr(10)).encode('utf-8') if lines else b''
        try:
            _write_bytes_atomic(table_path, data)
        except OSError as exc:
            raise VerdictRowError(
                BLOCKED + ': cannot write table: ' + table_path + ': ' + exc.strerror
            ) from exc
    finally:
        if lock_fd is not None:
            _release_lock(lock_fd, lock_path)


def read_verdict_table(table_path: str) -> list[dict]:
    '''Read verdict rows written so far, in order.

    Missing or unreadable table reads as empty. Corrupt lines are skipped.
    '''
    if not isinstance(table_path, str) or table_path == '':
        raise VerdictRowError(BLOCKED + ': table_path missing or invalid')
    try:
        with open(table_path, 'rb') as handle:
            raw = handle.read()
    except OSError:
        return []
    rows = []
    for raw_line in raw.split(bytes([10])):
        if not raw_line.strip():
            continue
        try:
            line = raw_line.decode('utf-8')
        except UnicodeDecodeError:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        ok, _reason = validate_verdict_row(item)
        if ok:
            rows.append(item)
    return rows
