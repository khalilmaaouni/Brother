'''L5d.d root cause classifier for the L5d performance audit.

Standard library only. Reads the capture record shape used by L5d.a and
reuses the gate log wrapper from the same module. A capture that cannot be
read is NO_DATA, never a crash and never a safe PASS.
'''

import enum
from pathlib import Path
from typing import Dict, Mapping, Optional, TypedDict

from scripts.gate_order import CorruptLogError, NoDataError
from scripts.perf_audit_capture import (
    CaptureInputError,
    CaptureRecord,
    per_check_seconds,
)


class Verdict(enum.Enum):
    MACHINE_LOAD = 'MACHINE_LOAD'
    CODE_REGRESSION = 'CODE_REGRESSION'
    STALE_TARGET = 'STALE_TARGET'
    INCONCLUSIVE = 'INCONCLUSIVE'
    NO_DATA = 'NO_DATA'


class Classification(TypedDict):
    verdict: Verdict
    quiet_wall_seconds: int
    loaded_wall_seconds: int
    loaded_load_avg_1m: float
    loaded_cpu_count: int
    top_hog_quiet: str
    top_hog_loaded: str
    target_seconds: int
    reasoning: str


SOURCE_UPTIME_PS = 'uptime+ps'
# The ONE quiet bar (H2, attack 2026-09-30): one core's worth of queue on an 8 core machine. A quiet record
# taken above it measures the neighbours, so no cause may be named from it; scripts/donecheck_l5d.py reads
# the same constant, and scripts/perf_audit_run.py waits for it before capturing.
QUIET_LOAD = 8.0


def _no_data(reasoning: str, target_seconds: int) -> Classification:
    '''Return the NO_DATA verdict with an explicit reason.'''
    return {
        'verdict': Verdict.NO_DATA,
        'quiet_wall_seconds': 0,
        'loaded_wall_seconds': 0,
        'loaded_load_avg_1m': 0.0,
        'loaded_cpu_count': 0,
        'top_hog_quiet': '',
        'top_hog_loaded': '',
        'target_seconds': target_seconds,
        'reasoning': reasoning,
    }


def _read_record(record) -> Optional[Dict[str, object]]:
    '''Read the wanted fields from one capture record, or None if unreadable.'''
    if not isinstance(record, dict):
        return None
    wall = record.get('wall_seconds')
    if type(wall) is not int or wall < 0:
        return None
    log_path = record.get('gate_log_path')
    if not isinstance(log_path, (str, Path)):
        return None
    load = record.get('load_before')
    if not isinstance(load, dict):
        return None
    if load.get('source') != SOURCE_UPTIME_PS:
        return None
    avg = load.get('load_average_1m')
    if type(avg) is bool or type(avg) not in (int, float):
        return None
    try:
        load_avg = float(avg)
    except (TypeError, ValueError, OverflowError):
        return None
    if load_avg != load_avg:
        return None
    cpu_count = load.get('cpu_count')
    if type(cpu_count) is not int or cpu_count <= 0:
        return None
    return {
        'wall_seconds': wall,
        'gate_log_path': log_path,
        'load_avg': load_avg,
        'cpu_count': cpu_count,
    }


def _per_check(log_path) -> Optional[Dict[str, float]]:
    '''Return per check seconds, or None when the gate log is unreadable.'''
    try:
        return per_check_seconds(log_path)
    except (CaptureInputError, NoDataError, CorruptLogError, OSError):
        return None


def _top_hog(per_check: Mapping[str, float]) -> str:
    '''Name of the slowest check, or the empty string when there are none.'''
    best_name = ''
    best_seconds = None
    for name in per_check:
        seconds = per_check[name]
        if best_seconds is None or seconds > best_seconds:
            best_seconds = seconds
            best_name = name
    return best_name


def classify(quiet: CaptureRecord, loaded: CaptureRecord,
             target_seconds: int = 90) -> Classification:
    '''Classify a quiet and a loaded capture against the L5d decision rule.

    Never raises on shape. Returns NO_DATA when either gate log is unreadable
    or corrupt, when either load_before.source is not uptime+ps, or when
    target_seconds is not an int.
    '''
    if type(target_seconds) is not int:
        return _no_data('target_seconds must be int', 0)

    quiet_read = _read_record(quiet)
    if quiet_read is None:
        return _no_data('quiet capture is missing or corrupt', target_seconds)

    loaded_read = _read_record(loaded)
    if loaded_read is None:
        return _no_data('loaded capture is missing or corrupt', target_seconds)

    if quiet_read['load_avg'] > QUIET_LOAD:
        return _no_data('quiet capture was taken at load %.2f, above the quiet bar of %.1f, so it measures '
                        'the neighbours and names no cause' % (quiet_read['load_avg'], QUIET_LOAD),
                        target_seconds)

    quiet_checks = _per_check(quiet_read['gate_log_path'])
    if quiet_checks is None:
        return _no_data('quiet gate log is unreadable or corrupt', target_seconds)

    loaded_checks = _per_check(loaded_read['gate_log_path'])
    if loaded_checks is None:
        return _no_data('loaded gate log is unreadable or corrupt', target_seconds)

    top_hog_quiet = _top_hog(quiet_checks)
    top_hog_loaded = _top_hog(loaded_checks)

    q_wall = quiet_read['wall_seconds']
    l_wall = loaded_read['wall_seconds']
    l_load = loaded_read['load_avg']
    l_cpu = loaded_read['cpu_count']

    if q_wall <= 150 and l_wall >= 3 * q_wall and l_load >= 2 * l_cpu:
        verdict = Verdict.MACHINE_LOAD
        reasoning = ('quiet wall is at or under 150s, the loaded wall is at least 3 times the '
                     'quiet wall, and the loaded 1m load is at least twice the loaded cpu count')
    elif q_wall > 300 and top_hog_quiet == top_hog_loaded:
        verdict = Verdict.CODE_REGRESSION
        reasoning = ('quiet wall is over 300s and the same slowest check leads both captures, '
                     'so the cost follows the code and not the machine')
    elif q_wall <= 150 and top_hog_quiet != top_hog_loaded:
        quiet_values = list(quiet_checks.values())
        all_trivial = bool(quiet_values) and all(value in (0, 1) for value in quiet_values)
        if all_trivial:
            verdict = Verdict.INCONCLUSIVE
            reasoning = ('quiet per check means are all 0 or 1, so precision is UNKNOWN and no '
                         'verdict can be trusted from these captures')
        else:
            verdict = Verdict.STALE_TARGET
            reasoning = ('quiet wall is at or under 150s and the slowest check differs between '
                         'the two captures while the quiet per check means are not all 0 or 1')
    else:
        verdict = Verdict.INCONCLUSIVE
        reasoning = 'no decision rule matched for these two captures'

    return {
        'verdict': verdict,
        'quiet_wall_seconds': q_wall,
        'loaded_wall_seconds': l_wall,
        'loaded_load_avg_1m': l_load,
        'loaded_cpu_count': l_cpu,
        'top_hog_quiet': top_hog_quiet,
        'top_hog_loaded': top_hog_loaded,
        'target_seconds': target_seconds,
        'reasoning': reasoning,
    }
