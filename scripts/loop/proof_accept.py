#!/usr/bin/env python3
"""Decide RB and RC from LR23 receipts plus run-bound proof observations.

Evidence is read only. This program never launches a loop, removes HOLD,
changes runtime files, reconciles a live ledger, or manufactures observations.
Exit 0 PASS, 1 FAIL, 2 NO-DATA. Any unknown row takes precedence over failure.

usage: proof_accept.py --pair <pair dir> <RB dir> <RC dir>
Every figure is rederived from the bytes each run retained at its end (the OpenRouter and
Claude ledger snapshots, the landing record and its history, the intervention history) and
compared with the receipt; the launcher's pair record (pair.json and one result per driver)
is required, so an older consistent pair can never stand for a relaunch that failed (D-12).
"""
import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import sys

# The loop's own loop_receipt and proof_launch, never a same-named copy elsewhere on the path: the
# deploy parity check refuses an import that does not say which copy it means (F47).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proof_launch

# THE ONE DEFINITION of the proof scope: the release plan's loop_scope_recorded.regex and PROOF-ACCEPTANCE.md carry
# copies that test_release_plan and test_proof_accept pin to this string. Every id is exact and anchored (owner ruling
# 2026-10-04, "A: Widen to held 1.1.0 units"): the 2026-09-26 prefixes let L3 admit L3b (DEFERRED) and D\d admit any
# future D unit. The widening adds D2, FX-31, PR1 and OP1, the parents of the sub units held as proof supply (D2.7,
# FX-31.8, PR1.c to PR1.f, OP1.d); the pool admits at unit grain and starts each unit's first unlanded sub unit. D2.7
# left the supply 2026-10-05 (plan_store.SUPPLY, hand-routed) and D2 stays here: an empty supply holds all of D2, and
# the intake scope must equal this string (check intake_scope), so dropping D2 would refuse the pair's own intake. No
# 1.1.1 unit (SO, FB1, U8) and no DEFERRED unit (L3b, M4 stays out as built before the freeze) is admitted. MG1 joined
# the same evening for MG1.e and MG1.f (decision proof-supply-mg1.json: no 1.1.1 sub unit was loop buildable).
# MG1.f left the supply 2026-10-06 (plan_store.SUPPLY: landed by the loop 2026-10-05, refused by the cut condition
# review, removed, moved to 1.1.1) and MG1 stays here for MG1.e, as D2 does: the intake scope must equal this string.
# PX1 to PX9 are the practice workload (owner ruling 2026-10-05, proof-runs-dry-2.json B): they exist only on branch
# practice/proof-2026-10-06, so on the release line they match no unit and admit nothing.
SCOPE = ('^(C0|D0|D1|D2|D3|D4|D5|D6|D7|D8|D9|D10|D11|D12|D13|D14|D15|FX-31|H1|H2|H3|H4|H5|H6|H7'
         '|L1|L1b|L2|L2b|L3|L5a|L6b|M1|M2|M3|MG1|OP1|PR1|PX1|PX2|PX3|PX4|PX5|PX6|PX7|PX8|PX9|R1|R2|R3|R4)$')
HEX = re.compile(r'^[0-9a-f]{64}$')
MIN_SECONDS = 8 * 60 * 60
# B4 allocation requires a bounded handoff. Thirty seconds is setup only,
# never credited toward either eight-hour work interval.
MAX_GAP_SECONDS = 30
MAX_BOUNDARY_SECONDS = 60


class NoData(ValueError):
    pass


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise NoData('number unavailable')
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise NoData('number unavailable')
    if not result.is_finite() or result < 0:
        raise NoData('nonfinite or negative number')
    return result


def count(value):
    n = number(value)
    if n != n.to_integral_value():
        raise NoData('count is fractional')
    return int(n)


def timestamp(value):
    if not isinstance(value, str):
        raise NoData('timestamp absent')
    try:
        result = datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:
        raise NoData('timestamp invalid')
    if result.tzinfo is None:
        raise NoData('timestamp lacks timezone')
    return result


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise NoData('duplicate JSON object key')
        result[key] = value
    return result


def sandbox_valid(directory, record, attempt):
    if not isinstance(record, dict) or record.get('enforced') is not True:
        raise NoData('sandbox enforcement unavailable')
    if not isinstance(record.get('evidence_sha256'), str) or not HEX.fullmatch(record['evidence_sha256']):
        raise NoData('sandbox evidence digest absent')
    attempted, blocked = count(record['escape_attempts']), count(record['blocked_attempts'])
    if attempted == 0:
        raise NoData('sandbox enforcement was not observed')
    artifact = Path(record['evidence_path'])
    if artifact.resolve() != (Path(directory) / 'proof' / 'sandbox.json').resolve():
        return False
    raw = artifact.read_bytes()
    if hashlib.sha256(raw).hexdigest() != record['evidence_sha256']:
        return False
    observed = json.loads(raw, object_pairs_hook=unique_object)
    control = observed['observation']
    return (blocked == attempted and observed['schema'] == 'loop-sandbox-observation-v1'
            and observed['run_id'] == Path(directory).name and observed.get('attempt_id') == attempt
            and type(observed['exit_code']) is int and observed['exit_code'] == 0
            and control['control'] is True and count(control['escape_attempts']) == attempted
            and count(control['blocked_attempts']) == blocked)


REVERTS = re.compile(rb'This reverts commit ([0-9a-fA-F]{7,64})')
COMMIT = re.compile(r'^[0-9a-f]{40}([0-9a-f]{24})?$')
OBSERVED = ('LANDED', 'FAST-FORWARD')
REHEARSAL_KNOB = 'BROTHER_PROOF_MIN_WINDOW_S'


def history_end(commit, horizon):
    """The last line of every retained landing history (land_batch.write_history writes it after the log bytes): the
    range the log covers. It is LAST, and git never puts a NUL in a message, so a history cut short anywhere has lost it
    (X1 finding 1, 2026-09-27: a write that failed part way kept the newest entry, which named the horizon, and lost an
    older revert below it, and naming the horizon was read as full coverage)."""
    return b'\x00range %s..%s complete\n' % (commit.encode('utf-8'), horizon.encode('utf-8'))


def reaches_horizon(commit, raw, horizon):
    """True when a landing's retained history runs up to the last fetch: the landing IS that remote, or the history is
    the whole of `git log --format=%H%x00%B <commit>..<horizon>`, which its last line says (history_end). A history that
    stops short of the horizon, or was cut short, is UNKNOWN. A horizon that is not text is reached by nothing, so every
    landing is UNKNOWN rather than a crash."""
    if not isinstance(horizon, str):
        return False
    return commit == horizon or raw.endswith(history_end(commit, horizon))


def surviving_landings(rows, logs, start, end):
    """THE SURVIVAL RULE (U10, Q3 default), over retained bytes only. One landing is one landing commit this run
    recorded (land_batch, loop-landing-v1) at a time inside [start, end]. It survives when the lander observed the
    remote holding it (verdict LANDED or FAST-FORWARD, with the remote sha and the fetch time) and its history was
    retained, and no history this run retained carries `This reverts commit <it>`. A landing whose remote was never
    observed or whose history is gone is of UNKNOWN survival. A revert pushed after the run's last fetch is not seen:
    the claim is what was observed, and detail['observed_at'] names each fetch. A revert of a revert still removes the
    first landing (fewer landings, never more). Returns (surviving, unknown, detail); KeyError for an unreadable row.
    THE LAST FETCH IS THE HORIZON (finding B2, 2026-09-27): a retained history proves survival only when it reaches the
    remote the run's last observed fetch saw (reaches_horizon). The lander rewrites every earlier history at each fetch; a
    rewrite that failed left the earlier bytes in place, silent about a revert that landed after them, and they counted.
    A HISTORY IS WHOLE OR UNKNOWN (X1 finding 1, 2026-09-27): only a history ending in its own range line (history_end)
    proves survival, so bytes cut short by a failed write read UNKNOWN, never surviving."""
    lo, hi = timestamp(start), timestamp(end)
    named = {m.decode('ascii').lower() for raw in logs.values() for m in REVERTS.findall(raw)}
    status, observed, outside, inside = {}, {}, set(), []
    for row in rows:
        if (not isinstance(row, dict) or row.get('schema') != 'loop-landing-v1'
                or not isinstance(row.get('commit'), str) or not COMMIT.match(row['commit'])):
            raise KeyError('a landing row is not a loop-landing-v1 row')
        commit = row['commit']
        try:
            at = timestamp(row.get('at'))
        except NoData:
            raise KeyError('landing row %s carries no valid time' % commit[:12])
        if not lo <= at <= hi:
            outside.add(commit)
            continue
        inside.append(row)
    fetched = [row['remote_sha'] for row in inside
               if row.get('verdict') in OBSERVED and bool(row.get('remote_sha')) and bool(row.get('fetched_at'))]
    horizon = fetched[-1] if fetched else None   # the file is append only: the last observed row is the last fetch
    for row in inside:
        commit = row['commit']
        seen = (row.get('verdict') in OBSERVED and bool(row.get('remote_sha')) and bool(row.get('fetched_at'))
                and logs.get(commit) is not None and reaches_horizon(commit, logs[commit], horizon))
        if seen:
            observed[commit] = row['fetched_at']
        if status.get(commit) != 'survived':
            status[commit] = 'survived' if seen else 'unknown'
    for commit in status:
        if any(commit.startswith(n) for n in named):
            status[commit] = 'reverted'
    pick = lambda s: sorted(c for c, v in status.items() if v == s)
    detail = dict(surviving=pick('survived'), unknown=pick('unknown'), reverted=pick('reverted'),
                  subs={row['commit']: [s for s in (row.get('subs') or []) if isinstance(s, str)] for row in inside},
                  outside_window=sorted(outside - set(status)), observed_at=observed)
    return len(detail['surviving']), len(detail['unknown']), detail


def read_object(path):
    with open(path, encoding='utf-8') as fh:
        value = json.load(fh, object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise NoData('expected object: %s' % path)
    return value


def check_run(directory, phase):
    rows = []; result = {'run':phase, 'cost_per_landing_usd':None, 'liability':None}
    def check(name, operation):
        try:
            okay = operation()
            rows.append({'check':name, 'verdict':'PASS' if okay else 'FAIL'})
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
            rows.append({'check':name, 'verdict':'NO-DATA', 'reason':str(exc)})
    try:
        receipt = read_object(Path(directory)/'receipt/receipt.json')
        evidence = read_object(Path(directory)/'proof/evidence.json')
    except (OSError, ValueError, TypeError) as exc:
        result['checks']=[{'check':'records','verdict':'NO-DATA','reason':str(exc)}]
        return result
    def identity():
        proof = Path(directory) / 'proof'
        if os.path.lexists(proof / 'reuse-refused.json'):
            return False
        start = read_object(proof / 'start.json')
        attempt = start.get('attempt_id')
        if not isinstance(attempt, str) or not re.fullmatch(r'[0-9a-f]{32}', attempt):
            raise NoData('proof attempt identity absent')
        return (receipt['run_id'] == Path(directory).name == evidence['run_id'] == start['run_id']
                and evidence['phase'] == phase == start['phase']
                and evidence['schema'] == 'loop-proof-v1' and start['schema'] == 'loop-proof-start-v1'
                and attempt == receipt['proof_attempt_id'] == evidence['attempt_id']
                and timestamp(start['work_start']) == timestamp(evidence['work_start']) == timestamp(receipt['start'])
                and timestamp(evidence['end']) == timestamp(receipt['end']))
    check('identity', identity)
    # The run against what it was LAUNCHED as, recorded outside its directory (D-12, s4 A3 to A5).
    check('launch_identity', lambda: proof_launch.accepted(directory, phase))
    check('duration', lambda: (timestamp(receipt['end']) - timestamp(receipt['start'])).total_seconds() >= MIN_SECONDS)
    check('terminal_state', lambda: receipt['end_state'] in ('COMPLETE', 'DEADLINE', 'FINISHED'))
    def unattended():
        # U11, objection 14: rederived from the retained history between its two markers
        if os.path.lexists(Path(directory) / 'proof-events-failed'):
            raise NoData('an intervention could not be appended during the run (proof-events-failed)')
        if not isinstance(evidence['unattended'], bool) or not isinstance(evidence['interventions'], list):
            raise NoData('unattended observations malformed')
        raw = (Path(directory) / 'proof' / 'events.jsonl').read_bytes()
        if hashlib.sha256(raw).hexdigest() != evidence['history_sha256']:
            return False
        rows = [json.loads(line, object_pairs_hook=unique_object) for line in raw.decode('utf-8').splitlines() if line.strip()]
        kinds = [row.get('kind') if isinstance(row, dict) else None for row in rows]
        if len(kinds) < 2 or kinds[0] != 'start-marker' or kinds[-1] != 'end-marker' or {'start-marker', 'end-marker'} & set(kinds[1:-1]):
            raise NoData('the history is not bounded by its start and end markers')
        if rows[1:-1] != evidence['interventions']:
            return False   # the evidence and the retained history disagree about what happened
        return evidence['unattended'] and not rows[1:-1]
    check('unattended', unattended)
    def no_rehearsal_knob():
        # objection 18: a run frozen with the rehearsal window knob is never production evidence
        manifest = read_object(Path(directory) / 'proof' / 'start.json')['manifest']
        raw = Path(manifest).read_bytes()
        if hashlib.sha256(raw).hexdigest() != evidence['freeze']['start']['manifest_sha256']:
            return False
        environment = json.loads(raw, object_pairs_hook=unique_object)['environment']
        return isinstance(environment, dict) and REHEARSAL_KNOB not in environment
    check('no_rehearsal_knob', no_rehearsal_knob)
    check('intake_scope', lambda: evidence['scope'] == SCOPE)
    def log_matches():
        expected = receipt['driver_log_sha256']
        if not isinstance(expected,str) or not HEX.fullmatch(expected):
            raise NoData('driver log digest absent')
        return hashlib.sha256(Path(receipt['driver_log_path']).read_bytes()).hexdigest() == expected
    check('driver_log', log_matches)
    def cost():
        landings = count(receipt['landings'])
        # CASH ONLY (owner 2026-10-05: "the cost for Claude is not calculated by API cost but based on 20x plan which
        # costs 220 USD per month"): the bar counts OpenRouter cash; Claude usage is the owner's flat plan, reported
        # beside it at API-equivalent prices and never added (claude_snapshot still checks that figure is real)
        spend = number(receipt['openrouter_spend_usd'])
        result['measured_spend_usd'] = str(spend); result['surviving_landings'] = landings
        try:
            result['claude_plan_usage_api_equivalent_usd'] = str(number(receipt['claude_spend_usd']))
        except NoData:
            result['claude_plan_usage_api_equivalent_usd'] = None
        if landings == 0:
            return False
        ratio = spend / Decimal(landings)
        result['cost_per_landing_usd'] = float(ratio)
        return ratio < Decimal('2')
    check('cost_per_landing', cost)
    def landings():
        # U10: the receipt's count against the retained landing record and history, by the survival rule
        import loop_receipt
        try:
            surviving, unknown, detail = loop_receipt.landings_end(directory, evidence, receipt['start'], receipt['end'])
        except ValueError:
            return False
        result['landings_detail'] = detail
        if unknown:
            raise NoData('%d landing(s) of unknown survival: %s' % (unknown, ', '.join(detail['unknown'])))
        return count(receipt['landings']) == surviving
    check('landings', landings)
    def supply():
        # owner 2026-10-04: a proof lands only the sub units named as its supply (plan_store.SUPPLY, the one definition
        # the pool and the lander read); a surviving landing of any other sub unit of a widened unit fails the run
        import plan_store
        detail = result.get('landings_detail')
        if not isinstance(detail, dict) or not isinstance(detail.get('subs'), dict):
            raise NoData('the sub units of the surviving landings are unknown')
        held = sorted(s for c in detail['surviving'] for s in detail['subs'].get(c, []) if plan_store.supply_hold(s))
        result['held_landed'] = held
        return not held
    check('supply', supply)
    def claude_snapshot():
        # U9: the receipt's Claude figure against the retained Claude end bytes, tallied by this run's tag
        import loop_receipt
        try:
            usd, note = loop_receipt.claude_end_spend(directory, evidence, receipt['end'])
        except ValueError:
            return False
        claimed = receipt['claude_spend_usd']
        if isinstance(claimed, str) and claimed.startswith('NO-DATA'):
            raise NoData('the receipt says %s' % claimed)
        if note is not None:
            return False   # a number where the retained bytes hold a pending or uncosted call
        return number(claimed) == number(usd)
    check('claude_snapshot', claude_snapshot)
    def liability():
        li = evidence['liability']
        unknown = count(li['unknown_cost_calls']); abandoned = count(li['abandoned_unsettled_calls'])
        reserved = number(li['reserved_liability_usd'])
        result['liability']={'unknown_cost_calls':unknown, 'abandoned_unsettled_calls':abandoned, 'reserved_liability_usd':str(reserved)}
        if unknown or abandoned or reserved:
            raise NoData('unsettled liability is separate from measured spend and cannot count as zero')
        return True
    check('cost_liability', liability)
    def accounting_snapshot():
        # Rederive from the retained end bytes; the receipt and evidence totals are claims.
        import loop_receipt
        snapshot = evidence['ledger_snapshot']
        if not isinstance(snapshot, dict):
            raise NoData('end ledger snapshot: %s' % (snapshot,))
        result['baseline_identity'] = [snapshot.get('ledger_path'), snapshot.get('baseline_sha256')]
        try:
            derived = loop_receipt.proof_end_accounting(directory, evidence)
        except ValueError:
            return False
        return (receipt['proof_baseline_sha256'] == derived['baseline_sha256']
                and number(receipt['openrouter_spend_usd']) == number(derived['measured_spend_usd'])
                and all(number(evidence['liability'][key]) == number(derived[key])
                        for key in ('unknown_cost_calls', 'abandoned_unsettled_calls', 'reserved_liability_usd')))
    check('accounting_snapshot', accounting_snapshot)
    def sandbox():
        start = read_object(Path(directory) / 'proof' / 'start.json')
        return (start['attempt_id'] == evidence['attempt_id'] == receipt['proof_attempt_id']
                and sandbox_valid(directory, evidence['sandbox'], start['attempt_id']))
    check('sandbox', sandbox)
    manifests = []
    for boundary in ('start','end'):
        def frozen(boundary=boundary):
            row = evidence['freeze'][boundary]
            if not isinstance(row.get('manifest_sha256'),str) or not HEX.fullmatch(row['manifest_sha256']):
                raise NoData('manifest digest missing')
            manifests.append(row['manifest_sha256'])
            if row['verdict'] == 'NO-DATA':
                raise NoData('freeze boundary unknown')
            observed = timestamp(row['observed_at']); edge = timestamp(receipt[boundary])
            offset = (edge - observed).total_seconds() if boundary == 'start' else (observed - edge).total_seconds()
            return (row['schema'] == 'loop-freeze-check-v1' and row['run_id'] == receipt['run_id']
                    and row['phase'] == boundary and row['verdict'] == 'PASS'
                    and count(row['checked_files']) > 0 and 0 <= offset <= MAX_BOUNDARY_SECONDS)
        check('freeze_'+boundary, frozen)
    result.update(checks=rows, manifests=manifests, start=receipt.get('start'), end=receipt.get('end'), run_id=receipt.get('run_id'), attempt_id=receipt.get('proof_attempt_id'))
    return result


def pair_checks(pair, rb, rc, manifests):
    """The launcher's pair record (objection 7, D-12). No record, or no result, is NO-DATA; a record naming other run
    directories or another frozen candidate, a driver that did not exit 0, or a receipt changed since its exit, FAIL."""
    if not pair:
        return [{'check': 'pair_record', 'verdict': 'NO-DATA', 'reason': 'no pair record supplied (--pair)'}]
    rows = []
    def row(name, operation):
        try:
            rows.append({'check': name, 'verdict': 'PASS' if operation() else 'FAIL'})
        except (OSError, ValueError, TypeError, KeyError) as exc:
            rows.append({'check': name, 'verdict': 'NO-DATA', 'reason': str(exc)})
    def record():
        value = proof_launch.read_pair(pair)
        if not manifests:
            raise NoData('the runs name no frozen candidate to compare the pair record with')
        return (value['rb_dir'] == str(Path(rb).resolve()) and value['rc_dir'] == str(Path(rc).resolve())
                and all(m == value['manifest_sha256'] for m in manifests))
    row('pair_record', record)
    for phase in ('RB', 'RC'):
        row('pair_result_' + phase, lambda phase=phase: proof_launch.result_ok(pair, phase))
    return rows


def rb_closed_before_rc(rb, rc):
    """U5 (B5-05): RB's run as RC's retained end ledger shows it must equal RB's run as RB's own end ledger shows it.
    A reservation, a settlement or a spend of RB's appearing after RB's snapshot is FAIL; unreadable is NO-DATA."""
    import proof_ledger
    start = read_object(Path(rb) / 'proof' / 'start.json')
    try:
        seen = [proof_ledger.analyze(start['ledger_path'], start['ledger_baseline'], start['ledger_baseline_sha256'],
                                     raw=(Path(d) / 'proof' / 'ledger-end.jsonl').read_bytes())['runs'].get(start['run_id'])
                for d in (rb, rc)]
    except proof_ledger.EvidenceError as exc:
        raise NoData('end ledger snapshot unreadable: %s' % exc)
    return json.dumps(seen[0], sort_keys=True) == json.dumps(seen[1], sort_keys=True)


def claude_pair(rb, rc, rb_receipt):
    """U9, objection 13: RC's retained Claude end bytes extend RB's, carry only RB and RC rows, no RB row after RB's
    end, and RB's figure tallied from them equals RB's receipt."""
    import claude_ledger, proof_ledger
    rb_start = read_object(Path(rb) / 'proof' / 'start.json')
    rc_run = read_object(Path(rc) / 'proof' / 'start.json')['run_id']
    rb_raw, rc_raw = [(Path(d) / 'proof' / 'claude-end.jsonl').read_bytes() for d in (rb, rc)]
    if not rc_raw.startswith(rb_raw):
        return False
    rb_end = timestamp(rb_receipt['end'])
    for line in rc_raw.decode('utf-8').splitlines():
        if not line.strip():
            continue
        row = proof_ledger.loads(line)   # a repeated member refuses: NO-DATA, like any unreadable row
        at = claude_ledger.instant(row.get('at')) if isinstance(row, dict) and isinstance(row.get('at'), str) else None
        if at is None:
            raise NoData('an unreadable Claude ledger row')
        if row.get('run') not in (rb_start['run_id'], rc_run):
            return False
        if row.get('run') == rb_start['run_id'] and at > rb_end:
            return False
    claimed = rb_receipt['claude_spend_usd']
    if isinstance(claimed, str) and claimed.startswith('NO-DATA'):
        raise NoData('RB receipt says %s' % claimed)
    t = claude_ledger.tally(str(Path(rc) / 'proof' / 'claude-end.jsonl'), run_id=rb_start['run_id'], now=rb_receipt['end'])
    if claude_ledger.note(t):
        return False
    return number(claimed) == number(t['usd'])


def accept(rb, rc, pair=None):
    runs = [check_run(rb,'RB'), check_run(rc,'RC')]
    rows = []
    try:
        gap = (timestamp(runs[1]['start']) - timestamp(runs[0]['end'])).total_seconds()
        rows.append({'check':'consecutive_windows','verdict':'PASS' if 0 <= gap <= MAX_GAP_SECONDS else 'FAIL','gap_seconds':gap})
    except (ValueError, TypeError, KeyError) as exc:
        rows.append({'check':'consecutive_windows','verdict':'NO-DATA','reason':str(exc)})
    attempts = [run.get('attempt_id') for run in runs]
    valid_attempts = all(isinstance(a, str) and re.fullmatch(r'[0-9a-f]{32}', a) for a in attempts)
    rows.append({'check':'distinct_attempts', 'verdict':'NO-DATA' if not valid_attempts else 'PASS' if len(set(attempts)) == 2 else 'FAIL'})
    baselines = [run.get('baseline_identity') for run in runs]
    rows.append({'check':'same_accounting_baseline', 'verdict':'NO-DATA' if None in baselines
                 else 'PASS' if baselines[0] == baselines[1] else 'FAIL'})
    # The ledger is append-only across the pair, so RB's retained end bytes must be a prefix of RC's: an RC snapshot
    # that lost RB's rows validates on its own and would forget RB's spend. Each run's own row checks its snapshot's
    # identity and digest; this row only compares the two retained files.
    try:
        ends = [(Path(d) / 'proof' / 'ledger-end.jsonl').read_bytes() for d in (rb, rc)]
        rows.append({'check':'rc_extends_rb', 'verdict':'PASS' if ends[1].startswith(ends[0]) else 'FAIL'})
    except OSError as exc:
        rows.append({'check':'rc_extends_rb', 'verdict':'NO-DATA', 'reason':'end ledger snapshot unreadable: %s' % type(exc).__name__})
    manifests = [m for run in runs for m in run.get('manifests',[])]
    rows.append({'check':'same_frozen_candidate','verdict':'NO-DATA' if len(manifests)!=4 else 'PASS' if len(set(manifests))==1 else 'FAIL'})
    for name, operation in (('rb_closed_before_rc', lambda: rb_closed_before_rc(rb, rc)),
                            ('claude_pair', lambda: claude_pair(rb, rc, read_object(Path(rb) / 'receipt' / 'receipt.json')))):
        try:
            rows.append({'check': name, 'verdict': 'PASS' if operation() else 'FAIL'})
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            rows.append({'check': name, 'verdict': 'NO-DATA', 'reason': str(exc)})
    rows.extend(pair_checks(pair, rb, rc, manifests))
    verdicts = [row['verdict'] for run in runs for row in run['checks']] + [r['verdict'] for r in rows]
    verdict = 'NO-DATA' if 'NO-DATA' in verdicts else 'FAIL' if 'FAIL' in verdicts else 'PASS'
    return {'schema':'loop-proof-verdict-v1','verdict':verdict,'runs':runs,'pair_checks':rows}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pair');parser.add_argument('rb');parser.add_argument('rc');args=parser.parse_args(argv)
    result=accept(args.rb,args.rc,pair=args.pair)
    print(json.dumps(result,sort_keys=True,indent=2,allow_nan=False))
    return {'PASS':0,'FAIL':1,'NO-DATA':2}[result['verdict']]


if __name__ == '__main__':
    sys.exit(main())
