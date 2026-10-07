from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from scripts.gate_order import (
    CorruptLogError,
    NoDataError,
    history,
    parse_current_order,
    parse_log,
)

NODATA = 'NO-DATA'
SCHEMA = 'brother-required-fast-run-v1'
KIND = 'dream-policy'
DEFAULT_MAX_AGE_S = 86400
CANARY_MAX_RUNS = 20

class PolicyInputError(Exception):
    pass

@dataclass(frozen=True)
class Declaration:
    names: tuple[str, ...]
    groups: tuple[tuple[str, ...], ...]
    group_of: dict[str, str]

@dataclass(frozen=True)
class CheckObservation:
    name: str
    command: str
    exit_code: int
    elapsed_s: float

@dataclass(frozen=True)
class RunRecord:
    schedule_id: str
    started_at: str
    worktree_key: str
    checks: tuple[CheckObservation, ...]

_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9-]*$')
_GROUP_RE = re.compile(r'^[a-z][a-z0-9-]*$')

def parse_declaration(text: str) -> Declaration:
    if not isinstance(text, str):
        raise PolicyInputError(f'{NODATA}: declaration text must be str')
    names = []
    group_of = {}
    group_members = {}
    group_order = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split('\t')
        if len(parts) != 2:
            raise PolicyInputError(f'{NODATA}: line {lineno} must have two TSV fields')
        name, group = parts[0], parts[1]
        if not _NAME_RE.match(name):
            raise PolicyInputError(f'{NODATA}: bad check name {name!r}')
        if name in group_of:
            raise PolicyInputError(f'{NODATA}: duplicate check name {name!r}')
        if group != '-':
            if not _GROUP_RE.match(group):
                raise PolicyInputError(f'{NODATA}: bad group {group!r}')
        names.append(name)
        group_of[name] = group
        if group != '-':
            group_members.setdefault(group, []).append(name)
            if group not in group_order:
                group_order.append(group)
    if not names:
        raise PolicyInputError(f'{NODATA}: empty declaration')
    for group in group_order:
        if len(group_members[group]) < 2:
            raise PolicyInputError(f'{NODATA}: group {group!r} used fewer than twice')
    groups = tuple(tuple(group_members[g]) for g in group_order)
    return Declaration(names=tuple(names), groups=groups, group_of=group_of)

def load_run_records(run_dirs: Sequence[str], *, now_epoch: float, max_age_s: int = DEFAULT_MAX_AGE_S) -> tuple[RunRecord, ...]:
    if not isinstance(run_dirs, (list, tuple)):
        raise PolicyInputError(f'{NODATA}: run_dirs must be sequence')
    if isinstance(now_epoch, bool) or not isinstance(now_epoch, (int, float)):
        raise PolicyInputError(f'{NODATA}: now_epoch must be number')
    if not math.isfinite(float(now_epoch)):
        raise PolicyInputError(f'{NODATA}: now_epoch must be finite')
    if isinstance(max_age_s, bool) or not isinstance(max_age_s, int) or max_age_s < 0:
        raise PolicyInputError(f'{NODATA}: max_age_s must be non-negative int')
    records = []
    for d in run_dirs:
        if not isinstance(d, str):
            raise PolicyInputError(f'{NODATA}: run_dir must be str')
        if not os.path.isdir(d):
            raise PolicyInputError(f'{NODATA}: missing run dir {d}')
        files = []
        try:
            entries = sorted(os.listdir(d))
        except OSError as exc:
            raise PolicyInputError(f'{NODATA}: cannot list {d}') from exc
        for entry in entries:
            if entry.endswith('.json'):
                files.append(os.path.join(d, entry))
        if not files:
            raise PolicyInputError(f'{NODATA}: no run records in {d}')
        for path in files:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception as exc:
                raise PolicyInputError(f'{NODATA}: cannot read {path}') from exc
            if not isinstance(data, dict):
                raise PolicyInputError(f'{NODATA}: run record must be object')
            if data.get('schema') != SCHEMA:
                raise PolicyInputError(f'{NODATA}: bad schema in {path}')
            schedule_id = data.get('schedule_id')
            started_at = data.get('started_at')
            worktree_key = data.get('worktree_key')
            mandatory_set_sha256 = data.get('mandatory_set_sha256')
            checks_raw = data.get('checks')
            if not isinstance(schedule_id, str) or not isinstance(started_at, str) or not isinstance(worktree_key, str):
                raise PolicyInputError(f'{NODATA}: bad run identity in {path}')
            if not isinstance(mandatory_set_sha256, str) or len(mandatory_set_sha256) != 64 or not re.match(r'^[0-9a-f]{64}$', mandatory_set_sha256):
                raise PolicyInputError(f'{NODATA}: bad mandatory_set_sha256 in {path}')
            if not isinstance(checks_raw, list):
                raise PolicyInputError(f'{NODATA}: checks must be list in {path}')
            checks = []
            for c in checks_raw:
                if not isinstance(c, dict):
                    raise PolicyInputError(f'{NODATA}: check must be object in {path}')
                name = c.get('name')
                command = c.get('command')
                exit_code = c.get('exit_code')
                elapsed_s = c.get('elapsed_s')
                if not isinstance(name, str) or not isinstance(command, str):
                    raise PolicyInputError(f'{NODATA}: bad check name or command in {path}')
                if isinstance(exit_code, bool) or not isinstance(exit_code, int):
                    raise PolicyInputError(f'{NODATA}: bad exit_code in {path}')
                if isinstance(elapsed_s, bool) or not isinstance(elapsed_s, (int, float)):
                    raise PolicyInputError(f'{NODATA}: bad elapsed_s in {path}')
                elapsed_s = float(elapsed_s)
                if not math.isfinite(elapsed_s) or elapsed_s < 0.0:
                    raise PolicyInputError(f'{NODATA}: bad elapsed_s in {path}')
                checks.append(CheckObservation(name=name, command=command, exit_code=exit_code, elapsed_s=elapsed_s))
            try:
                s = started_at.replace('Z', '+00:00')
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    raise ValueError('missing tz')
                started_epoch = dt.timestamp()
            except Exception as exc:
                raise PolicyInputError(f'{NODATA}: bad started_at in {path}') from exc
            if now_epoch - started_epoch > max_age_s:
                continue
            records.append(RunRecord(schedule_id=schedule_id, started_at=started_at, worktree_key=worktree_key, checks=tuple(checks)))
    if not records:
        raise PolicyInputError(f'{NODATA}: no run records within max age')
    return tuple(records)

def score(fail_rate: float, mean_seconds: float) -> float:
    if isinstance(fail_rate, bool) or not isinstance(fail_rate, (int, float)):
        raise PolicyInputError(f'{NODATA}: fail_rate must be number')
    if isinstance(mean_seconds, bool) or not isinstance(mean_seconds, (int, float)):
        raise PolicyInputError(f'{NODATA}: mean_seconds must be number')
    fail_rate = float(fail_rate)
    mean_seconds = float(mean_seconds)
    if not math.isfinite(fail_rate) or not math.isfinite(mean_seconds):
        raise PolicyInputError(f'{NODATA}: score inputs must be finite')
    if fail_rate < 0.0 or mean_seconds < 0.0:
        raise PolicyInputError(f'{NODATA}: score inputs must be non-negative')
    if fail_rate > 1.0:
        raise PolicyInputError(f'{NODATA}: fail_rate must be at most 1')
    if mean_seconds > 0.0:
        return fail_rate / mean_seconds
    # a zero mean is no timing evidence: it never auto-wins its group (D13 test_zero_mean_does_not_auto_win)
    return 0.0

def propose_order(decl: Declaration, records: tuple[RunRecord, ...]) -> tuple[str, ...]:
    if not isinstance(decl, Declaration):
        raise PolicyInputError(f'{NODATA}: decl must be Declaration')
    if not isinstance(records, tuple):
        raise PolicyInputError(f'{NODATA}: records must be tuple')
    names = decl.names
    if not isinstance(names, tuple):
        raise PolicyInputError(f'{NODATA}: names must be tuple')
    if not names:
        raise PolicyInputError(f'{NODATA}: empty names')
    if len(set(names)) != len(names):
        raise PolicyInputError(f'{NODATA}: duplicate names')
    if not isinstance(decl.groups, tuple):
        raise PolicyInputError(f'{NODATA}: groups must be tuple')
    if not isinstance(decl.group_of, dict):
        raise PolicyInputError(f'{NODATA}: group_of must be dict')
    if not records:
        raise PolicyInputError(f'{NODATA}: no records')
    agg = {name: {'runs': 0, 'fails': 0, 'secs': 0.0} for name in names}
    for rec in records:
        if not isinstance(rec, RunRecord):
            raise PolicyInputError(f'{NODATA}: record must be RunRecord')
        if not isinstance(rec.checks, tuple):
            raise PolicyInputError(f'{NODATA}: checks must be tuple')
        seen = set()
        for chk in rec.checks:
            if not isinstance(chk, CheckObservation):
                raise PolicyInputError(f'{NODATA}: check must be CheckObservation')
            if chk.name not in agg:
                raise PolicyInputError(f'{NODATA}: unknown check {chk.name}')
            if chk.name in seen:
                raise PolicyInputError(f'{NODATA}: duplicate check {chk.name}')
            seen.add(chk.name)
            if isinstance(chk.exit_code, bool) or not isinstance(chk.exit_code, int):
                raise PolicyInputError(f'{NODATA}: bad exit_code')
            if isinstance(chk.elapsed_s, bool) or not isinstance(chk.elapsed_s, (int, float)):
                raise PolicyInputError(f'{NODATA}: bad elapsed_s')
            e = float(chk.elapsed_s)
            if not math.isfinite(e) or e < 0.0:
                raise PolicyInputError(f'{NODATA}: bad elapsed_s')
            agg[chk.name]['runs'] += 1
            if chk.exit_code != 0:
                agg[chk.name]['fails'] += 1
            agg[chk.name]['secs'] += e
        if seen != set(names):
            raise PolicyInputError(f'{NODATA}: set change across records')
    canonical_index = {name: i for i, name in enumerate(names)}
    scores = {}
    for name in names:
        runs = agg[name]['runs']
        if runs == 0:
            raise PolicyInputError(f'{NODATA}: no runs for {name}')
        rate = agg[name]['fails'] / runs
        mean = agg[name]['secs'] / runs
        scores[name] = score(rate, mean)
    order = list(names)
    seen_group_members = set()
    for group in decl.groups:
        if not isinstance(group, tuple):
            raise PolicyInputError(f'{NODATA}: group must be tuple')
        for m in group:
            if m not in canonical_index:
                raise PolicyInputError(f'{NODATA}: unknown group member {m}')
            if m in seen_group_members:
                raise PolicyInputError(f'{NODATA}: overlapping groups')
            seen_group_members.add(m)
        slots = [i for i, name in enumerate(order) if name in group]
        sorted_members = sorted(group, key=lambda n: (-scores[n], canonical_index[n]))
        for slot, member in zip(slots, sorted_members):
            order[slot] = member
    if set(order) != set(names) or len(order) != len(names):
        raise PolicyInputError(f'{NODATA}: not a permutation')
    return tuple(order)

def _select_order_declaration(decl: Declaration, proposed: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(decl, Declaration):
        raise PolicyInputError(f'{NODATA}: decl must be Declaration')
    if not isinstance(proposed, (list, tuple)):
        raise PolicyInputError(f'{NODATA}: proposed must be sequence')
    names = decl.names
    if not isinstance(names, tuple):
        raise PolicyInputError(f'{NODATA}: names must be tuple')
    proposed = tuple(proposed)
    for member in proposed:
        if not isinstance(member, str):
            raise PolicyInputError(f'{NODATA}: proposed member must be str')
    if len(proposed) != len(names):
        raise PolicyInputError(f'{NODATA}: wrong length')
    if set(proposed) != set(names):
        raise PolicyInputError(f'{NODATA}: unknown or missing check name')
    if len(set(proposed)) != len(proposed):
        raise PolicyInputError(f'{NODATA}: duplicate check name')
    # REQ-POLICY-PERMUTE-ONLY: a singleton keeps its canonical index, a group keeps its canonical slot set
    group_of = decl.group_of
    if not isinstance(group_of, dict) or set(group_of) != set(names):
        raise PolicyInputError(f'{NODATA}: declaration groups do not cover names')
    for canonical, member in zip(names, proposed):
        if group_of[canonical] != group_of[member] or (group_of[canonical] == '-' and canonical != member):
            raise PolicyInputError(f'{NODATA}: {member} moved outside its group')
    return proposed


def score_of(name: str, stats: dict[str, dict[str, float]]) -> float:
    '''Return the ordering score for one declared check.

    R8: a zero or missing mean_seconds never scores an infinite auto win and
    uses the fallback constant 30.0 instead.  R9: an unknown check name, or a
    stat block that is not a dict, blocks with ValueError.
    '''
    if not isinstance(name, str):
        raise ValueError(f'{NODATA}: name must be str')
    if not isinstance(stats, dict):
        raise ValueError(f'{NODATA}: stats must be dict')
    if name not in stats:
        raise ValueError(f'{NODATA}: unknown check {name!r}')
    entry = stats[name]
    if not isinstance(entry, dict):
        raise ValueError(f'{NODATA}: stats entry must be dict')
    runs = entry.get('runs')
    fails = entry.get('fails')
    mean = entry.get('mean_seconds', 0.0)
    if mean is None:
        mean = 0.0
    if isinstance(runs, bool) or not isinstance(runs, int) or runs < 0:
        raise ValueError(f'{NODATA}: runs must be a non-negative int')
    if isinstance(fails, bool) or not isinstance(fails, int) or fails < 0 or fails > runs:
        raise ValueError(f'{NODATA}: fails must be an int between 0 and runs')
    if isinstance(mean, bool) or not isinstance(mean, (int, float)):
        raise ValueError(f'{NODATA}: mean_seconds must be a number')
    mean = float(mean)
    if not math.isfinite(mean) or mean < 0.0:
        raise ValueError(f'{NODATA}: mean_seconds must be finite and non-negative')
    rate = fails / runs if runs > 0 else 0.0
    if mean <= 0.0:
        mean = 30.0
    return rate / mean


def validate_permutes_only(current: list[str], proposed: list[str], groups: dict[str, str]) -> None:
    '''Refuse anything that is not a permutation inside equal group ids.

    R4: the set of names is preserved.  R5: a name moves only inside its own
    group id.  R6: a singleton group, including the own-group marker, never
    moves.  R7: an unhashable member raises TypeError as corrupt input.
    R9: a name with no group entry blocks with ValueError.
    '''
    if not isinstance(current, list):
        raise ValueError(f'{NODATA}: current must be a list of str')
    if not isinstance(proposed, list):
        raise ValueError(f'{NODATA}: proposed must be a list of str')
    if not isinstance(groups, dict):
        raise ValueError(f'{NODATA}: groups must be a dict')
    for seq, label in ((current, 'current'), (proposed, 'proposed')):
        for member in seq:
            if not isinstance(member, str):
                raise TypeError(f'{NODATA}: {label} member must be str, not {type(member).__name__}')
            try:
                hash(member)
            except TypeError as exc:
                raise TypeError(f'{NODATA}: {label} member must be hashable') from exc
    if not current:
        raise ValueError(f'{NODATA}: empty current order')
    if len(current) != len(proposed):
        raise ValueError(f'{NODATA}: set change between current and proposed')
    if set(current) != set(proposed):
        raise ValueError(f'{NODATA}: set change between current and proposed')
    if len(set(current)) != len(current):
        raise ValueError(f'{NODATA}: duplicate name in current')
    if len(set(proposed)) != len(proposed):
        raise ValueError(f'{NODATA}: duplicate name in proposed')
    for name in current:
        if name not in groups:
            raise ValueError(f'{NODATA}: unknown check in groups {name!r}')
        if not isinstance(groups[name], str) or not groups[name]:
            raise ValueError(f'{NODATA}: group id must be a non-empty str')
    for name in proposed:
        if name not in groups:
            raise ValueError(f'{NODATA}: unknown check in groups {name!r}')
    for canonical, member in zip(current, proposed):
        if groups[canonical] != groups[member]:
            raise ValueError(f'{NODATA}: {member} moved outside its group')
        if groups[canonical] == '-' and canonical != member:
            raise ValueError(f'{NODATA}: singleton {member} moved')
    return None


def _select_order_policy(current: list[str], stats: dict[str, dict[str, float]], groups: dict[str, str]) -> list[str]:
    '''Return the current order permuted only inside equal group ids.

    The mandatory set is preserved exactly, names move only inside their own
    group id, singleton groups never move, an unhashable member raises
    TypeError before any sort, and an unknown check blocks with ValueError.
    '''
    if not isinstance(current, list):
        raise ValueError(f'{NODATA}: current must be a list of str')
    if not isinstance(stats, dict):
        raise ValueError(f'{NODATA}: stats must be a dict')
    if not isinstance(groups, dict):
        raise ValueError(f'{NODATA}: groups must be a dict')
    for member in current:
        if not isinstance(member, str):
            raise TypeError(f'{NODATA}: current member must be str, not {type(member).__name__}')
    if not current:
        raise ValueError(f'{NODATA}: empty current order')
    if len(set(current)) != len(current):
        raise ValueError(f'{NODATA}: duplicate name in current')
    if set(current) != set(stats.keys()):
        raise ValueError(f'{NODATA}: stats check set mismatch')
    if set(current) != set(groups.keys()):
        raise ValueError(f'{NODATA}: groups check set mismatch')
    for name in current:
        if not isinstance(groups[name], str) or not groups[name]:
            raise ValueError(f'{NODATA}: group id must be a non-empty str')
    scores = {name: score_of(name, stats) for name in current}
    position = {name: i for i, name in enumerate(current)}
    members = {}
    for name in current:
        members.setdefault(groups[name], []).append(name)
    proposed = list(current)
    for gid, group_members in members.items():
        if gid == '-' or len(group_members) < 2:
            continue
        slots = [i for i, name in enumerate(current) if groups[name] == gid]
        ordered = sorted(group_members, key=lambda n: (-scores[n], position[n]))
        for slot, name in zip(slots, ordered):
            proposed[slot] = name
    validate_permutes_only(current, proposed, groups)
    return proposed


def select_order(*args):
    '''Apply the pure D13.2 ordering policy on top of the landed 2-arg shape.

    Called with (current, stats, groups) it returns the current order with
    names permuted only inside their own group id.  Called with the landed
    (declaration, proposed) pair it keeps the D13.1 behaviour byte identical.
    '''
    if len(args) == 2:
        return _select_order_declaration(args[0], args[1])
    if len(args) == 3:
        return _select_order_policy(args[0], args[1], args[2])
    raise PolicyInputError(f'{NODATA}: select_order expects 2 or 3 arguments')

def load_current_order(gate_path: str) -> list[str]:
    '''Load the current check order from the gate script.

    Delegates to scripts.gate_order.parse_current_order.  Missing file or
    empty order raises NoDataError and never returns a default order.
    '''
    if not isinstance(gate_path, str):
        raise NoDataError(f'{NODATA}: gate_path must be str: {gate_path!r}')
    return parse_current_order(gate_path)


def aggregate_history(log_paths: list[str]) -> dict[str, dict[str, float]]:
    '''Aggregate gate log history across the given log paths.

    Delegates to scripts.gate_order.history, which parses each log with
    parse_log.  An empty log raises NoDataError.  A torn result line raises
    CorruptLogError.
    '''
    if not isinstance(log_paths, list):
        raise NoDataError(f'{NODATA}: log_paths must be a list of strings: {log_paths!r}')
    for path in log_paths:
        if not isinstance(path, str):
            raise NoDataError(f'{NODATA}: log path must be str: {path!r}')
    return history(log_paths)


def hash_mapping(payload: dict[str, str]) -> str:
    '''Return a SHA-256 hex digest over a UTF-8 mapping with sorted keys.

    Only stdlib hashlib is used.  The result is 64 lowercase hex characters.
    Non-string keys or values are refused.
    '''
    if not isinstance(payload, dict):
        raise PolicyInputError(f'{NODATA}: payload must be a dict')
    for key, value in payload.items():
        if not isinstance(key, str):
            raise PolicyInputError(f'{NODATA}: payload keys must be str')
        if not isinstance(value, str):
            raise PolicyInputError(f'{NODATA}: payload values must be str')
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def main(argv: Optional[Sequence[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv or argv[0] != 'order':
        print(f'{NODATA}: expected order subcommand', file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(prog=f'{KIND} order')
    parser.add_argument('--declaration', required=True)
    parser.add_argument('--recordings', action='append', required=True)
    parser.add_argument('--now-epoch', type=int, default=None)
    parser.add_argument('--max-age-s', type=int, default=DEFAULT_MAX_AGE_S)
    try:
        args = parser.parse_args(argv[1:])
    except SystemExit:
        return 2
    try:
        with open(args.declaration, 'r', encoding='utf-8') as f:
            text = f.read()
    except OSError:
        print(f'{NODATA}: cannot read declaration {args.declaration}', file=sys.stderr)
        return 2
    try:
        decl = parse_declaration(text)
        now_epoch = args.now_epoch if args.now_epoch is not None else datetime.now().timestamp()
        records = load_run_records(args.recordings, now_epoch=now_epoch, max_age_s=args.max_age_s)
        proposed = propose_order(decl, records)
        order = select_order(decl, proposed)
    except PolicyInputError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    sys.stdout.write('\n'.join(order) + '\n')
    return 0

if __name__ == '__main__':
    sys.exit(main())
