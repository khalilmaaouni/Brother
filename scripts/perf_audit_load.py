'''Load and CPU snapshot for the L5d performance audit.

Standard library only. No ps. No subprocess.
'''

import os
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple, TypedDict, Union


class LoadSnapshot(TypedDict):
    load_average_1m: float
    load_average_5m: float
    load_average_15m: float
    cpu_count: Optional[int]
    captured_at: str


def snapshot() -> LoadSnapshot:
    '''Return one load snapshot. Raises OSError if the OS has no load data.'''
    one, five, fifteen = os.getloadavg()
    return {
        'load_average_1m': float(one),
        'load_average_5m': float(five),
        'load_average_15m': float(fifteen),
        'cpu_count': os.cpu_count(),
        'captured_at': datetime.now(timezone.utc).isoformat(),
    }


LOADAVG_PATH = '/proc/loadavg'
PROC_ROOT = '/proc'


class LoadSnapshotInputError(ValueError):
    '''Refuse invalid load snapshot input in this module.'''


class LoadSnapshotFull(TypedDict):
    captured_at: str
    load_avg_1m: float
    load_avg_5m: float
    load_avg_15m: float
    cpu_count: int
    claude_processes: int
    python_processes: int
    total_processes: int
    source: str


def parse_loadavg(text: str) -> Tuple[float, float, float]:
    '''Parse three load averages from /proc/loadavg text.

    A value that is not a str raises LoadSnapshotInputError.
    Corrupt, empty or NaN fields raise LoadSnapshotInputError.
    '''
    if not isinstance(text, str):
        raise LoadSnapshotInputError('loadavg text must be str')
    parts = text.split()
    if len(parts) < 3:
        raise LoadSnapshotInputError('loadavg text needs three fields')
    try:
        one = float(parts[0])
        five = float(parts[1])
        fifteen = float(parts[2])
    except (TypeError, ValueError) as exc:
        raise LoadSnapshotInputError('loadavg fields must be numeric') from exc
    for value in (one, five, fifteen):
        if value != value:
            raise LoadSnapshotInputError('loadavg fields must not be NaN')
    return one, five, fifteen


def count_processes_from_proc(proc_root: Union[str, os.PathLike]) -> Dict[str, int]:
    '''Count claude and python process comm entries under proc_root.

    A proc_root that is not a str or os.PathLike raises
    LoadSnapshotInputError. A missing or unreadable proc_root also raises
    LoadSnapshotInputError rather than returning a safe zero.
    '''
    if not isinstance(proc_root, (str, os.PathLike)):
        raise LoadSnapshotInputError('proc_root must be str or os.PathLike')
    root = os.fspath(proc_root)
    if not os.path.isdir(root):
        raise LoadSnapshotInputError('proc_root must be a directory')
    try:
        names = os.listdir(root)
    except OSError as exc:
        raise LoadSnapshotInputError('proc_root unreadable') from exc
    total = 0
    claude = 0
    python = 0
    for name in names:
        if not name.isdigit():
            continue
        comm_path = os.path.join(root, name, 'comm')
        try:
            with open(comm_path, 'r', encoding='utf-8', errors='replace') as handle:
                comm = handle.readline().strip()
        except OSError:
            continue
        total += 1
        lowered = comm.lower()
        if 'claude' in lowered:
            claude += 1
        if 'python' in lowered:
            python += 1
    return {
        'claude_processes': claude,
        'python_processes': python,
        'total_processes': total,
    }


def _unread_snapshot(captured_at: str, cpu_count: int) -> LoadSnapshotFull:
    return {
        'captured_at': captured_at,
        'load_avg_1m': float('nan'),
        'load_avg_5m': float('nan'),
        'load_avg_15m': float('nan'),
        'cpu_count': cpu_count,
        'claude_processes': 0,
        'python_processes': 0,
        'total_processes': 0,
        'source': 'unread',
    }


def snapshot_full() -> LoadSnapshotFull:
    '''Return a full load snapshot. Never raises.

    A missing, corrupt or unreadable /proc input yields source 'unread'
    with nan load averages, never a safe zero.
    '''
    captured_at = datetime.now(timezone.utc).isoformat()
    cpu_count = os.cpu_count()
    if not isinstance(cpu_count, int) or cpu_count <= 0:
        return _unread_snapshot(captured_at, 0)
    try:
        with open(LOADAVG_PATH, 'r', encoding='utf-8') as handle:
            text = handle.read()
        one, five, fifteen = parse_loadavg(text)
    except (OSError, UnicodeDecodeError, LoadSnapshotInputError):
        return _unread_snapshot(captured_at, cpu_count)
    try:
        counts = count_processes_from_proc(PROC_ROOT)
    except LoadSnapshotInputError:
        return _unread_snapshot(captured_at, cpu_count)
    return {
        'captured_at': captured_at,
        'load_avg_1m': one,
        'load_avg_5m': five,
        'load_avg_15m': fifteen,
        'cpu_count': cpu_count,
        'claude_processes': counts['claude_processes'],
        'python_processes': counts['python_processes'],
        'total_processes': counts['total_processes'],
        'source': 'proc',
    }
