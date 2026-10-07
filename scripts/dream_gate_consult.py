#!/usr/bin/env python3
'''dream_gate_consult: consult the gate ordering policy, replay recorded
decisions against it, gate the candidate, bound the canary and roll back to
the pin (unit D13.3).

READ ONLY FOR THE GATE. Nothing in this module writes a gate order, edits a
check command or changes an exit code. The one write it makes is the dream
journal intent, through dream_record.record_decision, and that write is the
point: the proposed order is recorded before it is handed back, so a
proposal without a recorded intent is never used.

FAILURE DIRECTION. A hostile argument (None, a str where a list belongs, a
bool where an int belongs, NaN, an unhashable member) is refused with
GateConsultError, itself a ValueError, never with a bare interpreter error.
A missing, empty, non UTF-8 or corrupt file is NO-DATA: consult_policy
returns the current order with state NO-DATA, canary_run blocks, rollback
refuses. Live gain is NO-DATA until a real authorized canary supplies
measured evidence; this unit never fabricates one.
'''
from __future__ import annotations

import json
import math
import os
import sys

from plugin.runtime.brother.core import dream_record
from plugin.runtime.brother.core import dream_replay
from plugin.runtime.brother.core.dream_gate_policy import select_order

NODATA = 'NO-DATA'
PIN_SCHEMA = 'c0.orderpin.1'
POLICY_VERSION = 'd13.3-gate-order-v1'
ORDER_KIND = 'gate.order'
CANARY_MAX_BOUND = 3
GATE_MIN_WORLDS = 3
GATE_MIN_SUPPORTED = 10
HEX_DIGITS = '0123456789abcdef'


class GateConsultError(ValueError):
    '''A deliberate refusal: a hostile argument or a corrupt contract.'''


def _require_str(value, label):
    '''A non-empty string, or a refusal: never a default and never None.'''
    if not isinstance(value, str) or not value.strip():
        raise GateConsultError('%s: %s must be a non-empty string' % (NODATA, label))
    return value


def _require_order(value, label):
    '''A list of unique non-empty check names, or a refusal.

    A str is refused before iteration: a str is a sequence of characters and
    would otherwise pass as one check name per letter.
    '''
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise GateConsultError('%s: %s must be a list of check names' % (NODATA, label))
    names = []
    seen = set()
    for member in value:
        if not isinstance(member, str) or not member:
            raise GateConsultError('%s: %s member must be a non-empty string' % (NODATA, label))
        if member in seen:
            raise GateConsultError('%s: duplicate check name in %s' % (NODATA, label))
        seen.add(member)
        names.append(member)
    if not names:
        raise GateConsultError('%s: %s must not be empty' % (NODATA, label))
    return names


def _read_bytes(path, label):
    '''The bytes of a file, or (None, reason). A directory is never a file.'''
    if os.path.isdir(path):
        return None, '%s path is a directory' % label
    try:
        with open(path, 'rb') as handle:
            return handle.read(), ''
    except OSError:
        return None, '%s file is missing or unreadable' % label


def _read_json_object(path, label):
    '''The json object a file holds, or (None, reason). Bytes first, always.'''
    if not isinstance(path, str) or not path:
        raise GateConsultError('%s: %s path must be a non-empty string' % (NODATA, label))
    body, reason = _read_bytes(path, label)
    if body is None:
        return None, reason
    try:
        text = body.decode('utf-8')
    except UnicodeDecodeError:
        return None, '%s file is not utf-8' % label
    try:
        value = json.loads(text)
    except ValueError:
        return None, '%s file is not json' % label
    if not isinstance(value, dict):
        return None, '%s file is not a json object' % label
    return value, ''


def _stats_reason(stats):
    '''Empty when the gate stat contract C1 holds, else the reason it does not.'''
    if not isinstance(stats, dict) or not stats:
        return 'stats must be a non-empty json object'
    for name, entry in stats.items():
        if not isinstance(name, str) or not name:
            return 'every stats key must be a non-empty check name'
        if not isinstance(entry, dict):
            return 'the stats entry for %s must be a json object' % name
        runs = entry.get('runs')
        fails = entry.get('fails')
        mean = entry.get('mean_seconds', 0.0)
        if isinstance(runs, bool) or not isinstance(runs, int) or runs < 0:
            return 'stats runs for %s must be a non-negative int' % name
        if isinstance(fails, bool) or not isinstance(fails, int) or fails < 0 or fails > runs:
            return 'stats fails for %s must be an int between 0 and runs' % name
        if mean is None:
            mean = 0.0
        if isinstance(mean, bool) or not isinstance(mean, (int, float)):
            return 'stats mean_seconds for %s must be a number' % name
        if not math.isfinite(float(mean)) or float(mean) < 0.0:
            return 'stats mean_seconds for %s must be finite and non-negative' % name
    return ''


def _groups_reason(groups):
    '''Empty when the group map contract C2 holds, else the reason it does not.'''
    if not isinstance(groups, dict) or not groups:
        return 'groups must be a non-empty json object'
    for name, group_id in groups.items():
        if not isinstance(name, str) or not name:
            return 'every groups key must be a non-empty check name'
        if not isinstance(group_id, str) or not group_id:
            return 'every group id must be a non-empty string'
    return ''


def _pin_order(pin):
    '''The pinned order list, or (None, reason) when the pin breaks C4.

    The gate sha and the policy sha are checked for shape only: this unit
    holds no gate file, so it cannot recompute them and refuses to pretend
    that it did.
    '''
    if not isinstance(pin, dict):
        return None, 'pin must be a json object'
    if pin.get('schema') != PIN_SCHEMA:
        return None, 'pin schema must be %s' % PIN_SCHEMA
    order = pin.get('order')
    if isinstance(order, (str, bytes)) or not isinstance(order, (list, tuple)) or not order:
        return None, 'pin order must be a non-empty list'
    names = []
    seen = set()
    for member in order:
        if not isinstance(member, str) or not member:
            return None, 'every pin order member must be a non-empty string'
        if member in seen:
            return None, 'pin order must not repeat a check'
        seen.add(member)
        names.append(member)
    mandatory = pin.get('mandatory')
    if isinstance(mandatory, (str, bytes)) or not isinstance(mandatory, (list, tuple)) or not mandatory:
        return None, 'pin mandatory must be a non-empty list'
    for member in mandatory:
        if not isinstance(member, str) or not member:
            return None, 'every pin mandatory member must be a non-empty string'
    if set(mandatory) != set(names):
        return None, 'pin mandatory set must equal the pin order set'
    for key in ('gate_sha', 'policy_sha'):
        digest = pin.get(key)
        if not isinstance(digest, str) or len(digest) != 64:
            return None, 'pin %s must be 64 lowercase hex characters' % key
        for char in digest:
            if char not in HEX_DIGITS:
                return None, 'pin %s must be 64 lowercase hex characters' % key
    return names, ''


def consult_policy(current, stats_path, groups_path, pin_path):
    '''The proposed order and its status, or the current order on NO-DATA.

    R10: missing, empty, non UTF-8, corrupt or contract breaking input keeps
    the current order and reports state NO-DATA. R11: a valid proposal is
    recorded through dream_record.record_decision before it is returned, and
    a proposal whose intent was not written is never handed back.
    '''
    current_order = _require_order(current, 'current')
    _require_str(stats_path, 'stats_path')
    _require_str(groups_path, 'groups_path')
    _require_str(pin_path, 'pin_path')

    def keep(reason):
        return list(current_order), {'state': NODATA, 'reason': reason}

    stats, reason = _read_json_object(stats_path, 'stats')
    if stats is None:
        return keep(reason)
    reason = _stats_reason(stats)
    if reason:
        return keep(reason)
    groups, reason = _read_json_object(groups_path, 'groups')
    if groups is None:
        return keep(reason)
    reason = _groups_reason(groups)
    if reason:
        return keep(reason)
    pin, reason = _read_json_object(pin_path, 'pin')
    if pin is None:
        return keep(reason)
    pin_order, reason = _pin_order(pin)
    if pin_order is None:
        return keep(reason)
    if set(pin_order) != set(current_order):
        return keep('the pinned order set differs from the current order')
    try:
        proposed = select_order(list(current_order), stats, groups)
    except (TypeError, ValueError) as exc:
        return keep('the ordering policy refused the candidate: %s' % exc)
    if not isinstance(proposed, list) or set(proposed) != set(current_order):
        return keep('the ordering policy returned something other than a permutation')
    run_dir = dream_record.run_dir_from_env()
    if not run_dir:
        sys.stderr.write('dream_gate_consult: no run directory, so the %s intent cannot be '
                         'recorded; keeping the current order\n' % ORDER_KIND)
        return keep('no run directory for the %s intent' % ORDER_KIND)
    options = [json.dumps(list(current_order)), json.dumps(list(proposed))]
    observed = {'stage': 'consult', 'stats_path': stats_path,
                'groups_path': groups_path, 'pin_path': pin_path}
    try:
        event_id = dream_record.record_decision(run_dir, ORDER_KIND, observed, options,
                                                options[1], POLICY_VERSION, cost=None)
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        sys.stderr.write('dream_gate_consult: could not record the %s intent (%s); keeping '
                         'the current order\n' % (ORDER_KIND, exc))
        return keep('the %s intent was refused' % ORDER_KIND)
    if event_id is None:
        sys.stderr.write('dream_gate_consult: the %s intent was not written; keeping the '
                         'current order\n' % ORDER_KIND)
        return keep('the %s intent was not written' % ORDER_KIND)
    return list(proposed), {'state': 'OK',
                            'reason': 'recorded intent accepted the proposed order',
                            'decision_event_id': event_id}


def _policy_for(order):
    '''A replay policy that picks the recorded option holding this exact order.

    When the order is not among the recorded options the policy answers None,
    which replay counts as UNSUPPORTED: a decision that never offered this
    order is a coverage gap and never a free PASS.
    '''
    encoded = json.dumps(list(order))

    def policy(kind, observed, options):
        if isinstance(options, (str, bytes)) or not isinstance(options, (list, tuple)) or not options:
            return None
        if encoded in options:
            return encoded
        for option in options:
            if isinstance(option, str):
                try:
                    if json.loads(option) == list(order):
                        return option
                except ValueError:
                    continue
        return None

    return policy


def replay_orders(worlds, current, candidate):
    '''Replay the current and the candidate orders over recorded worlds.

    R14: the recorded outcome is exposed only through dream_replay.replay,
    which reveals it only when the replayed choice matches the recorded one.
    R16: a candidate that changes the mandatory set is refused outright.
    '''
    current_order = _require_order(current, 'current')
    candidate_order = _require_order(candidate, 'candidate')
    if set(current_order) != set(candidate_order):
        raise GateConsultError('%s: candidate set differs from the current order' % NODATA)
    if worlds is None or isinstance(worlds, (str, bytes)) or not isinstance(worlds, (list, tuple)):
        raise GateConsultError('%s: worlds must be a list or tuple of worlds' % NODATA)
    try:
        verdict = dream_replay.compare(list(worlds), _policy_for(current_order),
                                       _policy_for(candidate_order), 0, 0)
    except TypeError as exc:
        raise GateConsultError('%s: worlds must be an iterable of worlds' % NODATA) from exc
    candidate_overall = verdict.get('candidate') or {}
    incumbent_overall = verdict.get('incumbent') or {}
    return {
        'verdict': str(verdict.get('verdict', NODATA)),
        'reason': str(verdict.get('reason', '')),
        'worlds': str(verdict.get('worlds', 0)),
        'supported': str(candidate_overall.get('supported', 0)),
        'incumbent_supported': str(incumbent_overall.get('supported', 0)),
    }


def gate_candidate(verdict, min_worlds, min_supported):
    '''The gated verdict, or NO-DATA below the thresholds.

    R15: a candidate is NO-DATA until there are at least three worlds and at
    least ten supported decisions. The caller's thresholds may raise that
    floor, never lower it: the floor is a control, not a report.
    '''
    if not isinstance(verdict, dict):
        raise GateConsultError('%s: verdict must be a dict of counts' % NODATA)
    for value, label in ((min_worlds, 'min_worlds'), (min_supported, 'min_supported')):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise GateConsultError('%s: %s must be a non-negative int' % (NODATA, label))
    worlds = verdict.get('worlds')
    supported = verdict.get('supported')
    for value, label in ((worlds, 'worlds'), (supported, 'supported')):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise GateConsultError('%s: verdict %s must be a non-negative int' % (NODATA, label))
    needed_worlds = min_worlds if min_worlds > GATE_MIN_WORLDS else GATE_MIN_WORLDS
    needed_supported = min_supported if min_supported > GATE_MIN_SUPPORTED else GATE_MIN_SUPPORTED
    if worlds < needed_worlds or supported < needed_supported:
        return NODATA
    chosen = verdict.get('verdict')
    if chosen in ('CANDIDATE', 'INCUMBENT'):
        return chosen
    return NODATA


def _blocked(reason):
    '''A canary or order answer that carries no evidence, and says so.'''
    return {'state': NODATA, 'verdict': NODATA, 'reason': reason}


def canary_run(order, bound_n, auth_path):
    '''NO-DATA live gain, or a block when authorization or the bound is absent.

    R18: an authorized canary needs the auth file to exist and hold at least
    one byte, and bound_n in 1..3; anything else blocks. R20: live gain stays
    NO-DATA even then, because this unit runs no canary and measures nothing.
    '''
    _require_order(order, 'order')
    if isinstance(bound_n, bool) or not isinstance(bound_n, int):
        raise GateConsultError('%s: bound_n must be an int, never a bool or a float' % NODATA)
    if bound_n < 1 or bound_n > CANARY_MAX_BOUND:
        return _blocked('bound_n %d is outside 1..%d, so no canary runs'
                        % (bound_n, CANARY_MAX_BOUND))
    _require_str(auth_path, 'auth_path')
    body, reason = _read_bytes(auth_path, 'auth')
    if body is None:
        return _blocked(reason)
    if not body:
        return _blocked('auth file is empty')
    try:
        body.decode('utf-8')
    except UnicodeDecodeError:
        return _blocked('auth file is not utf-8')
    return {'state': 'OK', 'verdict': NODATA,
            'reason': 'an authorized canary of at most %d runs is admitted; live gain stays '
                      'NO-DATA until a measured canary supplies evidence' % bound_n}


def rollback(pin_path):
    '''The pinned order, byte identical, or a refusal.

    R19: the list returned is exactly the pin's order, in the pin's order,
    with nothing sorted and nothing added. A missing or corrupt pin refuses
    rather than returning an empty order, because an empty order written to a
    gate would drop every mandatory check.
    '''
    _require_str(pin_path, 'pin_path')
    pin, reason = _read_json_object(pin_path, 'pin')
    if pin is None:
        raise GateConsultError('%s: cannot roll back, the pin %s' % (NODATA, reason))
    pin_order, reason = _pin_order(pin)
    if pin_order is None:
        raise GateConsultError('%s: cannot roll back, the pin %s' % (NODATA, reason))
    return list(pin_order)


__all__ = ['GateConsultError', 'consult_policy', 'replay_orders', 'gate_candidate',
           'canary_run', 'rollback']
