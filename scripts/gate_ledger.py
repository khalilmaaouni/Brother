#!/usr/bin/env python3
"""Gate ledger append and chain verification for C0.3."""

import fcntl
import hashlib
import json
import math
import os
import re
import sys

HEX32 = re.compile(r'^[0-9a-f]{32}$')
HEX40 = re.compile(r'^[0-9a-f]{40}$')
HEX64 = re.compile(r'^[0-9a-f]{64}$')
GENESIS = '0' * 64
VALID_STATUS = {'PASS', 'FAIL', 'NO-DATA'}
VALID_VERDICT_SOURCE = {'full', 'cache', 'shadow'}


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def _line_hash(record):
    return hashlib.sha256(_canonical(record).encode('utf-8')).hexdigest()


def _is_hex(value, length):
    if not isinstance(value, str):
        return False
    if len(value) != length:
        return False
    return all(c in '0123456789abcdef' for c in value)


def _validate(record):
    if not isinstance(record, dict):
        raise ValueError("record must be a dict")
    if record.get('schema') != 1 or type(record.get('schema')) is not int:
        raise ValueError("schema must be integer 1")
    if not _is_hex(record.get('cut_id'), 32):
        raise ValueError("cut_id must be 32 hex")
    if not isinstance(record.get('cut_version'), str) or not record['cut_version']:
        raise ValueError("cut_version must be non-empty string")
    if not isinstance(record.get('gate_name'), str) or not record['gate_name']:
        raise ValueError("gate_name must be non-empty string")
    if not isinstance(record.get('gate_argv'), list) or not all(isinstance(x, str) for x in record['gate_argv']):
        raise ValueError("gate_argv must be list of strings")
    if record.get('status') not in VALID_STATUS:
        raise ValueError("status must be PASS, FAIL or NO-DATA")
    if type(record.get('exit_code')) is not int:
        raise ValueError("exit_code must be integer")
    for key in ('started_unix', 'ended_unix', 'duration_s'):
        val = record.get(key)
        if type(val) is bool or not isinstance(val, (int, float)):
            raise ValueError(f"{key} must be numeric")
        if math.isnan(val) or math.isinf(val):
            raise ValueError(f"{key} must be finite")
    if record['duration_s'] < 0:
        raise ValueError("duration_s must be >= 0")
    if record['ended_unix'] < record['started_unix']:
        raise ValueError("ended_unix must be >= started_unix")
    if not isinstance(record.get('worktree_key'), str) or not record['worktree_key']:
        raise ValueError("worktree_key must be non-empty string")
    tree_sha = record.get('tree_sha')
    if tree_sha != 'UNKNOWN' and not _is_hex(tree_sha, 40):
        raise ValueError("tree_sha must be UNKNOWN or 40 hex")
    inputs_hash = record.get('inputs_hash')
    if inputs_hash is not None and not _is_hex(inputs_hash, 64):
        raise ValueError("inputs_hash must be null or 64 hex")
    if not _is_hex(record.get('code_hash'), 64):
        raise ValueError("code_hash must be 64 hex")
    if not isinstance(record.get('tool_versions'), dict):
        raise ValueError("tool_versions must be a dict")
    if record.get('verdict_source') not in VALID_VERDICT_SOURCE:
        raise ValueError("verdict_source must be full, cache or shadow")
    cache_key = record.get('cache_key')
    if cache_key is not None and not _is_hex(cache_key, 64):
        raise ValueError("cache_key must be null or 64 hex")
    deadline = record.get('deadline_unix')
    if deadline is not None:
        if type(deadline) is bool or not isinstance(deadline, (int, float)):
            raise ValueError("deadline_unix must be null or numeric")
        if math.isnan(deadline) or math.isinf(deadline):
            raise ValueError("deadline_unix must be finite")
    prev = record.get('prev_hash')
    if not _is_hex(prev, 64):
        raise ValueError("prev_hash must be 64 hex")


def _read_last_line(path):
    # Only a ledger that does not exist is an empty chain. Any other read error
    # raises: an unreadable ledger read as empty would fork the chain at GENESIS.
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except FileNotFoundError:
        return None  # sbe: allow-silent only an ABSENT ledger is an empty chain; every other read error raises
    if not lines:
        return None
    return lines[-1].rstrip('\n')


def _require_path(path):
    """The one place a ledger path is judged: text, and not empty. Every entry point routes through here."""
    if isinstance(path, bool) or not isinstance(path, str) or not path:
        raise ValueError("ledger path must be a non-empty string, got %r" % (path,))
    return path


def append_ledger(path, record):
    """Append one validated record, chaining prev_hash from the last line.

    Refuses (raises ValueError) on invalid or stale input.
    """
    _require_path(path)
    if not isinstance(record, dict):
        raise ValueError("record must be a dict")
    record = dict(record)
    prev = record.get('prev_hash')
    # One writer at a time from reading the last line to writing the next:
    # the fast gate runs checks side by side and each appends here, and two
    # unlocked writers would both chain to the same last line (measured
    # 2026-09-26: 8 writers of 25 records forked the chain on 3 of 3 runs).
    with open(path, 'a', encoding='utf-8') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        last_line = _read_last_line(path)
        expected_prev = GENESIS if last_line is None else _line_hash(json.loads(last_line))
        if prev is None:
            record['prev_hash'] = expected_prev
        elif prev != expected_prev:
            raise ValueError("prev_hash mismatch")
        _validate(record)
        line = _canonical(record)
        f.write(line + '\n')
        f.flush()


def verify_chain(path):
    """Return True only if every line validates and prev_hash chains exactly.

    A non-str path is refused with ValueError.  A missing or empty file is a
    refusal (False), never True.
    """
    _require_path(path)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except OSError:
        return False
    prev = GENESIS
    for raw in lines:
        line = raw.rstrip('\n')
        if not line:
            return False
        try:
            record = json.loads(line)
        except Exception:
            return False
        try:
            _validate(record)
        except ValueError:
            return False
        if record.get('prev_hash') != prev:
            return False
        prev = _line_hash(record)
    return True


def _main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Append one gate ledger record from JSON on stdin or --json.")
    parser.add_argument('--path', default=os.environ.get('GATE_LEDGER_PATH', 'logs/gate-ledger.jsonl'))
    parser.add_argument('--json', help='JSON record text')
    args = parser.parse_args(argv)
    if args.json:
        record = json.loads(args.json)
    else:
        record = json.load(sys.stdin)
    append_ledger(args.path, record)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
