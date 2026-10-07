"""Immutable proof-period accounting, without changing any ledger row.

One parser serves baseline disclosure and new run liability. Callers must pin
sha256(baseline_bytes) in the frozen policy. A baseline is an observation, not
permission to release or reconcile historic reservations. This module has no
provider calls, live defaults, or public launcher.
"""
import base64
import contextlib
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time


class EvidenceError(ValueError):
    pass


class DrainRefused(RuntimeError):
    """A proof call refused at registration because the run is ending, the call would outlive the deadline, or the
    registering module is not the frozen code. Distinct from a failure: nothing was registered, nothing was spent."""


#: OWNER RULING ON Q1 (design of record, section 5), 2026-09-27: option A, his words "ok" to the recommendation and
#: then "I allow it". A paid call that failed after dispatch, whose reservation is BOUND (every attempt priced by the
#: frozen catalog), counts at that bound in the burn guard's headroom, so one such call no longer ends the pair
#: unfunded; an unbounded one still funds nothing. Frozen code: changing it is one reviewed edit, never an environment
#: variable.
BOUNDED_ABANDON_COUNTS = True

#: A proof call is admitted only when its own timeout ends this many seconds before the run's deadline.
DRAIN_MARGIN_S = 60
PROOF_KEYS = ('BROTHER_PROOF_PHASE', 'BROTHER_PROOF_BASELINE', 'BROTHER_PROOF_BASELINE_SHA256')


def run_ledger_paths(env=None):
    """Every plain run's own OpenRouter ledger (<runs root>/run-*/money/openrouter-ledger.jsonl), oldest first. Since
    2026-09-27 each run spends in its own money root, so a reader that totals spend ACROSS runs (reports, per build cost)
    reads the shared ledger plus these; a reader of one run's funding reads that run's root alone."""
    import glob
    env = os.environ if env is None else env
    root = env.get("BROTHER_RUNS_ROOT") or os.path.expanduser("~/.claude/evidence/loop-runs")
    return sorted(glob.glob(os.path.join(root, "run-*", "money", "openrouter-ledger.jsonl")))


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def amount(value):
    # An int past the float range (10**400 is valid JSON) makes math.isfinite raise OverflowError, which no
    # ValueError handler catches; every ledger amount and timestamp enters here, so it is refused here (F3).
    try:
        ok = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0
    except OverflowError:
        ok = False
    if not ok:
        raise EvidenceError('invalid finite nonnegative amount or timestamp')
    return value


def _unique_members(pairs):
    """object_pairs_hook for every proof record: plain json.loads keeps the LAST of repeated members, so a settlement
    carrying "actual_cost": 9 then "actual_cost": 0 read as a measured zero (money audit 2026-09-27, finding 6)."""
    record = {}
    for key, value in pairs:
        if key in record:
            raise EvidenceError('repeated member %r in one record' % (key,))
        record[key] = value
    return record


def loads(raw):
    """json.loads refusing a repeated member. The one parser of every ledger row and proof record read here, and of
    the OpenRouter ledger rows openrouter_ledger reads."""
    return json.loads(raw, object_pairs_hook=_unique_members)


def total(values):
    """A sum of amounts that is itself a finite amount. Finite amounts can overflow to inf and inf minus inf is NaN,
    which makes every cap comparison false (money audit 2026-09-27, finding 8), so a sum that is not finite refuses."""
    result = sum(values)
    try:
        return amount(result)
    except EvidenceError:
        raise EvidenceError('ledger amounts sum to %r, not a finite amount' % (result,)) from None


def identity(row):
    run, attempt = row.get('run_id'), row.get('attempt_id')
    if (not isinstance(run, str) or not run.strip() or len(run) > 256 or '/' in run or '\\' in run
            or not isinstance(attempt, str) or not re.fullmatch(r'[0-9a-f]{32}', attempt)):
        raise EvidenceError('new activity lacks valid run and attempt identity')
    return run, attempt


def parse(raw):
    """Validate the whole history, including rows outside the proof period."""
    if raw and not raw.endswith(b'\n'):
        raise EvidenceError('incomplete ledger tail')
    rows = []
    for line in raw.decode('utf-8').splitlines():
        if not line.strip():
            continue
        row = loads(line)
        if not isinstance(row, dict):
            raise EvidenceError('ledger entry must be an object')
        rid = row.get('reservation_id')
        if not isinstance(rid, str) or not rid:
            raise EvidenceError('invalid reservation identity')
        amount(row.get('at'))
        rows.append(row)
    reservations, dispatches, terminals = {}, {}, {}
    for index, row in enumerate(rows):
        kind, rid = row.get('type'), row['reservation_id']
        if kind == 'RESERVE':
            if rid in reservations:
                raise EvidenceError('duplicate reservation identity')
            amount(row.get('estimated_cost'))
            reservations[rid] = (index, row)
        elif kind == 'DISPATCH_START':
            if rid not in reservations or rid in dispatches or rid in terminals:
                raise EvidenceError('unknown, duplicated or closed dispatch')
            tagged = identity(row)
            reserve = reservations[rid][1]
            if ('run_id' in reserve or 'attempt_id' in reserve) and identity(reserve) != tagged:
                raise EvidenceError('dispatch identity conflicts with reservation')
            dispatches[rid] = (index, row)
        elif kind in ('RECONCILE', 'RELEASE', 'ABANDONED'):
            if rid not in reservations or rid in terminals:
                raise EvidenceError('unknown or duplicate terminal event')
            if kind == 'RELEASE' and rid in dispatches:
                raise EvidenceError('release after possible provider contact')
            if kind == 'RECONCILE':
                amount(row.get('actual_cost'))
            if 'run_id' in row or 'attempt_id' in row:
                tagged = identity(row)
                matched = dispatches[rid][1] if rid in dispatches else reservations[rid][1]
                if ('run_id' in matched or 'attempt_id' in matched) and identity(matched) != tagged:
                    raise EvidenceError('terminal identity conflicts with reservation or dispatch')
            terminals[rid] = (index, row)
        else:
            raise EvidenceError('unknown ledger event')
    return rows, reservations, dispatches, terminals


def historical(raw):
    _, reservations, dispatches, terminals = parse(raw)
    pending = []
    for rid in sorted(reservations):
        terminal = terminals.get(rid, (None, {}))[1]
        kind = terminal.get('type')
        if kind == 'RELEASE':
            continue
        if kind == 'RECONCILE' and rid in dispatches and terminal.get('cost_source') == 'provider_usage':
            continue
        pending.append(rid)
    return dict(unknown_cost_calls=len(pending),
                abandoned_unsettled_calls=sum(terminals.get(rid, (None, {}))[1].get('type') == 'ABANDONED' for rid in pending),
                reserved_liability_usd=sum(reservations[rid][1]['estimated_cost'] for rid in pending),
                unknown_reservation_ids=pending)


def capture(ledger_path, baseline_path):
    """Write a fresh, byte-preserving snapshot under the existing ledger lock.

    Used explicitly by the authorized launcher, never implicitly on admission.
    Failure can leave an incomplete baseline which must be rejected, not reused.
    """
    ledger, baseline = Path(ledger_path).resolve(), Path(baseline_path)
    with ledger.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        raw = ledger.read_bytes()
        rows, _, _, _ = parse(raw)
        record = dict(schema='loop-ledger-baseline-v1', ledger_path=str(ledger),
                      captured_at=datetime.now(timezone.utc).isoformat(),
                      prefix_base64=base64.b64encode(raw).decode('ascii'), prefix_bytes=len(raw),
                      prefix_sha256=sha256(raw), prefix_rows=len(rows), historic=historical(raw))
        encoded = (json.dumps(record, sort_keys=True, allow_nan=False) + '\n').encode('utf-8')
        with baseline.open('xb') as output:
            output.write(encoded); output.flush()
            import os
            os.fsync(output.fileno())
    return sha256(encoded)


def verified_prefix(ledger_path, baseline_path, expected_sha256, *, raw=None):
    baseline_bytes = Path(baseline_path).read_bytes()
    if not isinstance(expected_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_sha256) or sha256(baseline_bytes) != expected_sha256:
        raise EvidenceError('baseline identity mismatch')
    baseline = loads(baseline_bytes)
    if baseline.get('schema') != 'loop-ledger-baseline-v1' or baseline.get('ledger_path') != str(Path(ledger_path).resolve()):
        raise EvidenceError('baseline describes another ledger or schema')
    prefix = base64.b64decode(baseline['prefix_base64'], validate=True)
    if len(prefix) != baseline['prefix_bytes'] or sha256(prefix) != baseline['prefix_sha256']:
        raise EvidenceError('baseline prefix metadata mismatch')
    old_rows, _, _, _ = parse(prefix)
    if len(old_rows) != baseline['prefix_rows'] or historical(prefix) != baseline['historic']:
        raise EvidenceError('baseline disclosure mismatch')
    if raw is None:
        raw = Path(ledger_path).read_bytes()
    else:
        if not isinstance(raw, (bytes, bytearray)):
            raise EvidenceError('captured ledger bytes must be bytes')
        raw = bytes(raw)
        try:
            parse(raw)
        except EvidenceError:
            raise
        except ValueError as exc:
            raise EvidenceError(str(exc)) from exc
    if not raw.startswith(prefix):
        raise EvidenceError('ledger history changed or was truncated')
    return baseline, raw, len(old_rows)


def analyze(ledger_path, baseline_path, expected_sha256, *, raw=None):
    """All post-baseline liability, irrespective of date or proof phase.

    New dispatch of an old reservation enters the proof scope. Untagged new
    activity cannot be filtered away. Settlements need the dispatch boundary
    and provider billing provenance; unproven numbers remain unknown.
    """
    baseline, raw, cutoff = verified_prefix(ledger_path, baseline_path, expected_sha256, raw=raw)
    _, reservations, dispatches, terminals = parse(raw)
    scoped = sorted(rid for rid, (index, _) in reservations.items()
                    if index >= cutoff or dispatches.get(rid, (-1, None))[0] >= cutoff
                    or terminals.get(rid, (-1, None))[0] >= cutoff)
    runs, attempt_owners = {}, {}
    for rid in sorted(reservations):
        row = reservations[rid][1]
        if 'run_id' in row or 'attempt_id' in row:
            owner_run, owner_attempt = identity(row)
            if owner_attempt in attempt_owners and attempt_owners[owner_attempt] != owner_run:
                raise EvidenceError('attempt reused by another run')
            attempt_owners[owner_attempt] = owner_run
    pending, abandoned = [], []
    for rid in scoped:
        _, reserve = reservations[rid]
        dispatch = dispatches.get(rid)
        run, attempt = identity(dispatch[1] if dispatch else reserve)
        if run in runs and runs[run]['attempt_id'] != attempt:
            raise EvidenceError('run reused with another attempt')
        if attempt in attempt_owners and attempt_owners[attempt] != run:
            raise EvidenceError('attempt reused by another run')
        attempt_owners[attempt] = run
        summary = runs.setdefault(run, dict(attempt_id=attempt, reservation_ids=[],
                                 unknown_reservation_ids=[], measured_spend_usd=0,
                                 inflight_reservation_ids=[], abandoned_bounded_usd=0, abandoned_unbounded=0))
        summary['reservation_ids'].append(rid)
        terminal = terminals.get(rid, (None, {}))[1]
        kind = terminal.get('type')
        if kind == 'RECONCILE' and not dispatch:
            raise EvidenceError('new settlement lacks dispatch observation')
        known = kind == 'RELEASE' or (kind == 'RECONCILE' and terminal.get('cost_source') == 'provider_usage')
        if kind == 'RECONCILE' and known:
            summary['measured_spend_usd'] += terminal['actual_cost']
        if not known:
            pending.append(rid); summary['unknown_reservation_ids'].append(rid)
            if kind == 'ABANDONED':
                abandoned.append(rid)
            # U6: in flight (no terminal yet), abandoned at a bound (only a RESERVE whose bound is exactly True),
            # or unbounded (every other unknown terminal, an unproven settlement included). Never measured spend.
            if kind is None:
                summary['inflight_reservation_ids'].append(rid)
            elif kind == 'ABANDONED' and reserve.get('bound') is True:
                summary['abandoned_bounded_usd'] += reserve['estimated_cost']
            else:
                summary['abandoned_unbounded'] += 1
    for summary in runs.values():
        # bounded abandons are a subset of the liability summed below, so that sum bounds them
        summary['measured_spend_usd'] = total([summary['measured_spend_usd']])
    return dict(schema='loop-ledger-analysis-v1', baseline_sha256=expected_sha256,
                ledger_sha256=sha256(raw), historic=baseline['historic'],
                new_reservation_ids=scoped, unknown_reservation_ids=pending,
                unknown_cost_calls=len(pending), abandoned_unsettled_calls=len(abandoned),
                reserved_liability_usd=total(reservations[rid][1]['estimated_cost'] for rid in pending),
                runs=runs)


def configuration(root, env=None):
    """Resolve a proof policy and actual attempt, or None for a legacy call.

    Any partial proof configuration refuses. Identity comes from the start
    observation, never a caller-selected tag. Grading sandboxes that strip
    the run directory cannot silently spend against a different proof root.
    """
    env = os.environ if env is None else env
    got = _start(env)
    if got is None:
        return None
    run_dir, start, run, attempt = got
    baseline = env.get('BROTHER_PROOF_BASELINE', '')
    expected = env.get('BROTHER_PROOF_BASELINE_SHA256', '')
    if not Path(baseline).is_absolute():
        raise EvidenceError('proof policy and absolute run identity required')
    if os.path.lexists(Path(run_dir) / 'proof' / 'ledger-end.jsonl'):
        # U5 (B5-05): once the run's end snapshot exists, nothing more is funded or registered for it
        raise EvidenceError('the run already took its end snapshot; nothing more is funded for it')
    ledger = str((Path(root) / 'openrouter-ledger.jsonl').resolve())
    if (start.get('ledger_baseline') != baseline
            or start.get('ledger_baseline_sha256') != expected
            or not Path(start.get('ledger_path', '')).is_absolute()
            or str(Path(start.get('ledger_path', '')).resolve()) != ledger):
        raise EvidenceError('proof identity or baseline binding mismatch')
    observed = analyze(ledger, baseline, expected)
    for other_run, row in observed['runs'].items():
        if (other_run == run and row['attempt_id'] != attempt) or (other_run != run and row['attempt_id'] == attempt):
            raise EvidenceError('proof run or attempt identity was reused')
    return dict(run_id=run, attempt_id=attempt, ledger=ledger, run_dir=run_dir,
                baseline=baseline, baseline_sha256=expected, analysis=observed)


def _start(env):
    """(run_dir, start record, run, attempt) for a proof phase, None outside one. Any partial proof configuration,
    a refused reuse, or a start record that does not name this run and phase raises."""
    phase = env.get('BROTHER_PROOF_PHASE', '')
    if not any(env.get(key) for key in PROOF_KEYS):
        return None
    run_dir = env.get('BROTHER_RUN_DIR', '')
    if phase not in ('RB', 'RC') or not Path(run_dir).is_absolute():
        raise EvidenceError('proof policy and absolute run identity required')
    if reuse_refused(run_dir):
        raise EvidenceError('proof attempt was reused')
    start = loads((Path(run_dir) / 'proof' / 'start.json').read_bytes())
    if not isinstance(start, dict) or start.get('schema') != 'loop-proof-start-v1':
        raise EvidenceError('invalid proof start observation')
    run, attempt = identity(start)
    if run != Path(run_dir).name or start.get('phase') != phase:
        raise EvidenceError('proof identity or baseline binding mismatch')
    return run_dir, start, run, attempt


def run_identity(env=None):
    """(run_id, attempt_id) from this run's proof start record, or None outside a proof phase. The one source of the
    tags a paid call's rows carry (objection 13); an unreadable or mismatched start record raises, never untagged."""
    got = _start(os.environ if env is None else env)
    return None if got is None else got[2:]


def ordinary_run_identity(env=None):
    """(run_id, attempt_id) of an ORDINARY run (no proof phase) from the start record its driver wrote at
    <BROTHER_RUN_DIR>/proof/start.json, or None when no run directory is named (a call outside every run). 2026-10-04:
    run_identity answers None outside a proof phase by contract, so every ordinary run's Claude rows were untagged and a
    run's own spend could not be told from anyone else's. Never consulted in a proof phase (run_identity owns that, with
    its admission); it TAGS only and admits nothing. A named run directory whose start record is unreadable, of another
    schema, a proof phase's, or naming another run raises: a corrupt identity is refused, never a silent untagged row.
    So does a stale inherited BROTHER_RUN_DIR naming a folder with no valid start record (a shell or child outliving its
    run): such a call is refused, loudly, rather than tagged to a run it does not belong to. make_run_dir always exports
    an absolute directory."""
    env = os.environ if env is None else env
    if any(env.get(key) for key in PROOF_KEYS):
        return None
    run_dir = env.get('BROTHER_RUN_DIR', '')
    if not run_dir:
        return None
    if not Path(run_dir).is_absolute():
        raise EvidenceError('a run directory must be absolute to name a run identity: %r' % run_dir)
    try:
        start = loads((Path(run_dir) / 'proof' / 'start.json').read_bytes())
    except (OSError, ValueError) as exc:
        raise EvidenceError('the run start record cannot be read: %s' % exc) from exc
    if not isinstance(start, dict) or start.get('schema') != 'loop-proof-start-v1':
        raise EvidenceError('invalid run start record')
    if start.get('phase') in ('RB', 'RC'):
        raise EvidenceError('a proof start record outside a proof phase')
    run, attempt = identity(start)
    if run != Path(run_dir).name:
        raise EvidenceError('the run start record names %s, not this run directory' % run)
    return run, attempt


def _deadline(run_dir):
    """The run's deadline_epoch from its launch record, outside the run directory. Anything else raises."""
    try:
        record = loads((launch_dir(run_dir) / 'launch.json').read_bytes())
    except (OSError, ValueError) as exc:
        raise EvidenceError('launch record unreadable: %s' % exc) from exc
    if not isinstance(record, dict) or record.get('run_dir') != str(Path(run_dir).resolve()):
        raise EvidenceError('launch record describes another run directory')
    try:
        return amount(record.get('deadline_epoch'))
    except EvidenceError:
        raise EvidenceError('launch record carries no valid deadline_epoch') from None


def admit_locked(run_dir, timeout_seconds, module_file, env=None, now=None):
    """Admission of one proof call at REGISTRATION, called while the caller holds its ledger's lock, which
    mark_ending also takes: the call is either registered before the ending marker exists (settle then sees it) or
    refused here with nothing written (objection 8).

    DrainRefused when the run is ending, when the registering module is outside BROTHER_CODE_ROOT (U3: a site still
    running launch worktree code cannot spend), or when now + timeout + DRAIN_MARGIN_S passes the launch deadline.
    EvidenceError when the code root, the timeout or the launch record cannot be read: unknown never admits."""
    env = os.environ if env is None else env
    now = time.time() if now is None else amount(now)
    ending = Path(run_dir) / 'proof' / 'ending.json'
    if os.path.lexists(ending):
        raise DrainRefused('the run is ending (%s exists); no new call is registered' % ending)
    root = env.get('BROTHER_CODE_ROOT', '')
    if not root or not os.path.isdir(root):
        raise EvidenceError('proof admission requires BROTHER_CODE_ROOT naming a directory, got %r' % root)
    real_root, real_module = os.path.realpath(root), os.path.realpath(module_file)
    # The deploy stages the code root as <bin>/candidate and the flat tools the driver runs sit directly in <bin>;
    # both are frozen. So a module directly in the staged candidate's own bin is inside too, and nothing else is
    # (a bin subdirectory, or the sibling of a code root that is not the staged candidate: Codex check-in 4, #1).
    flat_tool = (os.path.basename(real_root) == 'candidate'
                 and os.path.dirname(real_module) == os.path.dirname(real_root))
    if os.path.commonpath([real_root, real_module]) != real_root and not flat_tool:
        raise DrainRefused('%s is outside the frozen code root %s' % (real_module, real_root))
    try:
        timeout = amount(timeout_seconds)
    except EvidenceError:
        raise EvidenceError('proof admission needs the call timeout, got %r' % (timeout_seconds,)) from None
    deadline = _deadline(run_dir)
    if now + timeout + DRAIN_MARGIN_S > deadline:
        raise DrainRefused('a call of %ss would outlive the deadline (%.0fs left, %ss margin)'
                           % (timeout, deadline - now, DRAIN_MARGIN_S))


def draining(env=None, now=None, min_timeout=300):
    """True when a proof run should start nothing new: it is ending, or even the shortest call (min_timeout, the
    dispatcher's floor) would outlive the deadline. An unreadable launch record reads as draining (start nothing).
    False outside a proof phase."""
    env = os.environ if env is None else env
    if not any(env.get(key) for key in PROOF_KEYS):
        return False
    run_dir = env.get('BROTHER_RUN_DIR', '')
    try:
        if os.path.lexists(Path(run_dir) / 'proof' / 'ending.json'):
            return True
        deadline = _deadline(run_dir)
        now = time.time() if now is None else amount(now)
        return now + amount(min_timeout) + DRAIN_MARGIN_S > deadline
    except (OSError, ValueError):
        return True


@contextlib.contextmanager
def ledger_lock(ledger_path):
    """The exclusive flock every writer of ledger_path holds: <ledger>.lock beside it (openrouter-ledger.lock for
    the OpenRouter ledger, which openrouter_ledger._LockedLedger and capture() take). The Claude ledger's writer
    takes the same shape, so mark_ending's barrier covers both."""
    with open(str(Path(ledger_path).with_suffix('.lock')), 'a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def mark_ending(run_dir, state_root, claude_ledger_path, state):
    """Create proof/ending.json once (O_EXCL) while holding the OpenRouter ledger lock, then the Claude ledger lock
    (always in that order). From its creation every registration for this run refuses, whatever the end state
    (objection 8, U8). A second call raises FileExistsError and leaves the first record as it was."""
    if not isinstance(state, str) or not state.strip():
        raise EvidenceError('an ending needs its end state')
    path = Path(run_dir) / 'proof' / 'ending.json'
    with ledger_lock(Path(state_root) / 'openrouter-ledger.jsonl'), ledger_lock(claude_ledger_path):
        record = dict(schema='loop-proof-ending-v1', run_dir=str(Path(run_dir).resolve()), state=state.strip(),
                      at=datetime.now(timezone.utc).isoformat())
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, 'wb') as out:
            out.write((json.dumps(record, sort_keys=True) + '\n').encode('utf-8')); out.flush(); os.fsync(out.fileno())
    return record


def cap_view(root, now, env=None):
    """Daily settled spend plus every new unresolved liability across dates.

    Owner-approved baseline history stays separately disclosed. The daily
    settled attribution retains the legacy reservation-day rule. New unknown
    liabilities never expire at midnight or become catalog-measured spend.
    """
    policy = configuration(root, env)
    if policy is None:
        return None
    amount(now)
    _, reservations, _, terminals = parse(Path(policy['ledger']).read_bytes())
    analysis = policy['analysis']; scoped = set(analysis['new_reservation_ids'])
    pending = set(analysis['unknown_reservation_ids'])
    settled = sum(row['actual_cost'] for rid, (_, row) in terminals.items()
                  if row['type'] == 'RECONCILE' and int(reservations[rid][1]['at'] // 86400) == int(now // 86400)
                  and (rid not in scoped or row.get('cost_source') == 'provider_usage'))
    # Each unknown is summed on its own side, never as the liability minus the abandoned part: inf minus inf is NaN,
    # and a NaN total admitted a reservation under a zero cap (money audit 2026-09-27, finding 8).
    is_abandoned = {rid: terminals.get(rid, (None, {}))[1].get('type') == 'ABANDONED' for rid in pending}
    abandoned = sum(reservations[rid][1]['estimated_cost'] for rid in pending if is_abandoned[rid])
    inflight = sum(reservations[rid][1]['estimated_cost'] for rid in pending if not is_abandoned[rid])
    return dict(settled=settled, inflight=inflight, abandoned=abandoned,
                total=total((settled, inflight, abandoned)))


LAUNCH_REGISTRY = '.proof-launches'


def launch_dir(run_dir):
    """Where a run's launch expectation lives: beside the run directories, never inside one.

    <runs root>/.proof-launches/<run>/ holds launch.json, work-start.json and one refused-*.json
    per refused reuse (scripts/loop/proof_launch.py writes them). Nothing in the run directory or
    the environment names this place, so rewriting either cannot move it.
    """
    real = Path(run_dir).resolve()
    return real.parent / LAUNCH_REGISTRY / real.name


def reuse_refused(run_dir):
    """True when a reuse of run_dir was refused, inside the directory or outside it.

    The one reader of both refusal records (finding g, D-12; S2, D-17). The in-folder marker
    counts when it is lexically present, a dangling link included; the launch registry's
    refusals count whatever the proof folder allowed to be written. An unreadable registry
    raises: an unknown refusal state is never read as the fresh case.
    """
    import os
    if os.path.lexists(Path(run_dir) / 'proof' / 'reuse-refused.json'):
        return True
    where = launch_dir(run_dir)
    if not os.path.lexists(where):
        return False
    return any(name.startswith('refused-') for name in os.listdir(where))
