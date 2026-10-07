"""The launch registry: what an RB or RC proof run was launched as, recorded outside the run.

Codex decisions D-12 (finding g) and D-17 (s4 A3 to A5). Every value a proof run was judged by
lived in files the run itself writes (proof/start.json, the reuse marker) or in variables it
reads, so a coordinated rewrite of start.json and the environment left nothing to compare
against, and a reuse refusal that could not be written into a read-only proof folder left no
trace at all. The expectation now lives in proof_ledger.launch_dir(run_dir), beside the run
directories, written by proof-start before start.json exists:

  launch.json      the launch identity and baseline binding (FIELDS), created once, never rewritten
  work-start.json  the admitted work clock, created once by proof-work-start after its checks pass
  refused-*.json   one record per refused reuse of the run directory

Each record is created with O_EXCL and never replaced. proof-work-start and acceptance compare
proof/start.json against launch.json field by field; nothing is compared against a digest the
run carries about itself.

THE PAIR RECORD (U12, objection 7), beside the two run directories in the launcher's pair-<id>/:

  pair.json        the run directories RB and RC must use, the frozen manifest's digest, the coverage end
  RB.result.json   RB's driver exit code and its receipt's digest, then RC.result.json the same for RC

each created once. The launcher runs `proof_launch.py pair-write`, `result` after each driver and `handoff`
before RC; acceptance (proof_accept --pair) reads them, so an older consistent pair can never stand for a
relaunch that failed (D-12).

WHAT THIS DOES NOT PROVE. The registry is written by the same user as the loop. The grading
sandbox cannot reach it (scripts/loop/sandbox.sb allows writes to the slot root and its temp
only), but an unsandboxed process running as the owner can rewrite it exactly as it can rewrite
start.json. No privilege boundary separates the launcher from the loop's unsandboxed children;
tamper resistance against such a writer needs a separate user or a root-owned store.

Test: python3 -B scripts/test_proof_launch_identity.py
"""
from datetime import datetime, timezone
import json
import os
import sys
from pathlib import Path
import uuid

# The loop's own proof_ledger, never a same-named copy elsewhere on the path (deploy parity, F47).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proof_ledger

# sandbox carries the observation's own digest: bound here, a rewrite of the artifact AND that
# digest together no longer agrees with anything the run cannot also rewrite. deadline_epoch is what
# admission reads (proof_ledger.admit_locked, Codex check-in 2 #1): without it every proof call refuses.
# volatile_env is {name: sha256(value)} for the names the shared freeze leaves out (U1).
FIELDS = ('run_id', 'attempt_id', 'phase', 'scope', 'manifest', 'ledger_path', 'ledger_baseline',
          'ledger_baseline_sha256', 'accounting_admission', 'sandbox', 'deadline_epoch', 'volatile_env',
          'claude_ledger_path')
PAIR_SCHEMA, RESULT_SCHEMA = 'loop-proof-pair-v1', 'loop-proof-result-v1'
PHASE_KEY = {'RB': 'rb_dir', 'RC': 'rc_dir'}


def _write_once(path, value):
    raw = (json.dumps(value, sort_keys=True, allow_nan=False) + '\n').encode('utf-8')
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, 'wb') as fh:
        fh.write(raw); fh.flush(); os.fsync(fh.fileno())


def _read(path):
    from proof_accept import unique_object
    with open(path, encoding='utf-8') as fh:
        value = json.load(fh, object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError('launch registry record is not an object: %s' % path)
    return value


def _registry(run_dir):
    where = proof_ledger.launch_dir(run_dir)
    where.mkdir(parents=True, exist_ok=True)
    return where


def recorded(run_dir):
    """True when this run directory was ever launched or refused: launching it again is a reuse."""
    return os.path.lexists(proof_ledger.launch_dir(run_dir))


def refuse(run_dir, reason):
    """Record a refused reuse OUTSIDE the refused directory. None, or the OSError that stopped it."""
    try:
        _write_once(_registry(run_dir) / ('refused-%s.json' % uuid.uuid4().hex),
                    dict(schema='loop-proof-launch-refusal-v1', run_dir=str(Path(run_dir).resolve()),
                         reason=reason, observed_at=datetime.now(timezone.utc).isoformat()))
    except OSError as exc:
        return exc
    return None


def record(run_dir, start):
    """Create launch.json from the start record, once. FileExistsError on a second launch."""
    value = {key: start.get(key) for key in FIELDS}
    value.update(schema='loop-proof-launch-v1', run_dir=str(Path(run_dir).resolve()),
                 launched_at=datetime.now(timezone.utc).isoformat())
    _write_once(_registry(run_dir) / 'launch.json', value)
    return value


def launch(run_dir):
    """The recorded launch, or None when none was recorded. A record that cannot be read raises."""
    path = proof_ledger.launch_dir(run_dir) / 'launch.json'
    if not os.path.lexists(path):
        return None
    value = _read(path)
    if value.get('schema') != 'loop-proof-launch-v1' or value.get('run_dir') != str(Path(run_dir).resolve()):
        raise ValueError('launch record describes another run directory or schema')
    proof_ledger.identity(value)
    return value


def _drift(start, value):
    return [key for key in FIELDS if start.get(key) != value.get(key)]


def bound(run_dir, start):
    """The launch start.json must still match, or None for a run no proof launch recorded.

    Raises when a reuse was refused or when start.json disagrees with the recorded launch in any
    field, whatever the environment now says (s4 A3, A4, A5).
    """
    if proof_ledger.reuse_refused(run_dir):
        raise ValueError('a reuse of this run directory was refused')
    value = launch(run_dir)
    if value is not None and _drift(start, value):
        raise ValueError('start record disagrees with the recorded launch: ' + ', '.join(_drift(start, value)))
    return value


def admit(run_dir, attempt_id, work_start):
    """Record the admitted work clock, once. FileExistsError when work was already admitted."""
    _write_once(proof_ledger.launch_dir(run_dir) / 'work-start.json',
                dict(schema='loop-proof-work-admission-v1', attempt_id=attempt_id, work_start=work_start))


def accepted(run_dir, phase):
    """Acceptance's launch row. True, False on a contradiction, or raises when there is no evidence."""
    if proof_ledger.reuse_refused(run_dir):
        return False
    value = launch(run_dir)
    if value is None:
        raise ValueError('no launch expectation was recorded outside the run directory')
    start = _read(Path(run_dir) / 'proof' / 'start.json')
    if value.get('phase') != phase or _drift(start, value):
        return False
    if _read(Path(run_dir) / 'proof' / 'evidence.json').get('sandbox') != value.get('sandbox'):
        return False
    path = proof_ledger.launch_dir(run_dir) / 'work-start.json'
    if not os.path.lexists(path):
        return False
    admitted = _read(path)
    return (admitted.get('schema') == 'loop-proof-work-admission-v1'
            and admitted.get('attempt_id') == value['attempt_id']
            and isinstance(start.get('work_start'), str) and admitted.get('work_start') == start['work_start'])


# THE PAIR RECORD (U12, objection 7, D-12). The launcher (scripts/loop/proof_pair.sh) creates pair-<id>/ with a plain
# mkdir, then these helpers write inside it, each file once (O_EXCL): pair.json before RB starts, then one result per
# driver as it exits. Acceptance (proof_accept --pair) reads them; the launcher's handoff reads RB's before RC starts.

def _sha256(path):
    import hashlib
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def write_pair(pair_dir, rb_dir, rc_dir, env_sha256, manifest_sha256, pair_until):
    """Create pair-<id>/pair.json once, naming the run directories RB and RC must use. FileExistsError on a second."""
    value = dict(schema=PAIR_SCHEMA, pair_id=Path(pair_dir).name, rb_dir=str(Path(rb_dir).resolve()),
                 rc_dir=str(Path(rc_dir).resolve()), env_sha256=env_sha256, manifest_sha256=manifest_sha256,
                 pair_until=pair_until, created_at=datetime.now(timezone.utc).isoformat())
    _write_once(Path(pair_dir) / 'pair.json', value)
    return value


def read_pair(pair_dir):
    value = _read(Path(pair_dir) / 'pair.json')
    if value.get('schema') != PAIR_SCHEMA:
        raise ValueError('pair record schema is not %s' % PAIR_SCHEMA)
    return value


def record_result(pair_dir, phase, exit_code, receipt_path=None):
    """Create pair-<id>/<phase>.result.json once: the driver's exit code and its receipt's digest (None when there is
    no readable receipt, a refused launch included). ValueError for a phase other than RB or RC."""
    if phase not in PHASE_KEY:
        raise ValueError('a result is recorded for RB or RC, not %r' % (phase,))
    pair = read_pair(pair_dir)
    try:
        digest = _sha256(receipt_path) if receipt_path else None
    except OSError:
        digest = None
    value = dict(schema=RESULT_SCHEMA, phase=phase, run_dir=pair[PHASE_KEY[phase]], exit_code=int(exit_code),
                 receipt_path=str(receipt_path) if receipt_path else None, receipt_sha256=digest,
                 recorded_at=datetime.now(timezone.utc).isoformat())
    _write_once(Path(pair_dir) / ('%s.result.json' % phase), value)
    return value


def result_ok(pair_dir, phase):
    """True when the recorded driver exited 0 and its receipt is still the bytes it recorded; False on a refusal, a
    failure or a changed receipt. Raises (OSError, ValueError) when the pair record or the result is missing."""
    pair = read_pair(pair_dir)
    value = _read(Path(pair_dir) / ('%s.result.json' % phase))
    receipt = Path(pair[PHASE_KEY[phase]]) / 'receipt' / 'receipt.json'
    return (value.get('schema') == RESULT_SCHEMA and value.get('phase') == phase
            and value.get('run_dir') == pair[PHASE_KEY[phase]]
            and type(value.get('exit_code')) is int and value['exit_code'] == 0
            and isinstance(value.get('receipt_sha256'), str) and value['receipt_sha256'] == _sha256(receipt))


def handoff(pair_dir):
    """(True, '') when RC may start (S11): RB's driver exited 0, its receipt is unchanged since that exit, ended on
    DEADLINE and names RB's own directory, and RB's evidence exists. Otherwise (False, why). Never raises."""
    try:
        pair = read_pair(pair_dir)
        if not result_ok(pair_dir, 'RB'):
            return False, 'RB did not exit 0 with the receipt it recorded'
        rb = Path(pair['rb_dir'])
        receipt = _read(rb / 'receipt' / 'receipt.json')
        if receipt.get('end_state') != 'DEADLINE':
            return False, 'RB ended %r, not DEADLINE' % (receipt.get('end_state'),)
        if receipt.get('run_id') != rb.name:
            return False, 'RB receipt names run %r, not %r' % (receipt.get('run_id'), rb.name)
        if not (rb / 'proof' / 'evidence.json').is_file():
            return False, 'RB has no proof/evidence.json'
        return True, ''
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return False, 'NO-DATA: %s' % exc


def main(argv=None):
    """pair-write | result | handoff, for the launcher. Exit 0 done or handoff allowed, 1 handoff refused, 2 error."""
    import argparse
    p = argparse.ArgumentParser(prog='proof_launch.py')
    p.add_argument('verb', choices=('pair-write', 'result', 'handoff'))
    p.add_argument('--pair-dir', required=True)
    for flag in ('--rb', '--rc', '--env-sha256', '--manifest-sha256', '--pair-until', '--phase', '--exit', '--receipt'):
        p.add_argument(flag)
    a = p.parse_args(argv)
    try:
        if a.verb == 'pair-write':
            if not all((a.rb, a.rc, a.env_sha256, a.manifest_sha256, a.pair_until)):
                raise ValueError('pair-write needs --rb --rc --env-sha256 --manifest-sha256 --pair-until')
            write_pair(a.pair_dir, a.rb, a.rc, a.env_sha256, a.manifest_sha256, a.pair_until)
        elif a.verb == 'result':
            record_result(a.pair_dir, a.phase, int(a.exit), a.receipt or None)
        else:
            ok, why = handoff(a.pair_dir)
            print('HANDOFF' if ok else 'NO HANDOFF: ' + why)
            return 0 if ok else 1
    except (OSError, ValueError, TypeError) as exc:
        print('REFUSED: %s' % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
