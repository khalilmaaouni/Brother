"""Brother Core: OpenRouter reserve/reconcile budget ledger (unit OR-1).

Replaces a read-modify-write budget.json, which would recreate the
exact class of concurrency bug already found and fixed in registry.py
tonight (run_id collision, duplicate-key overwrite, no locking), this
time in the budget layer, where a bug is harder to notice: a silently
overspent session looks identical to a correct one until the bill
surfaces.

Design (Muse's own words, real adversarial review, 2026-09-19): never
read-modify-write JSON. An append-only JSONL ledger plus a real
os.flock, two entries per call: a RESERVE recorded before the call
runs (an estimate), a RECONCILE recorded after (the real cost from the
bridge's own [usage] line). The current spend is always the sum of
reconciled costs plus any reservation that has not yet been
reconciled (a call still in flight, or one that crashed before
reconciling, which must still count against the cap until explicitly
released).
"""

import datetime
import fcntl
import json
import math
import os
import time
import uuid
from dataclasses import dataclass
from typing import Tuple


class BudgetExceeded(Exception):
    """Raised when a reservation would exceed the daily cap."""


class LedgerError(Exception):
    """Raised for a malformed ledger entry or a reconcile with no
    matching reservation."""


class _MissingProofLedger:
    """A proof ledger that refuses every call. A proof ledger that cannot be
    loaded is never headroom and never a crash: every entry point here raises
    this module's own LedgerError, so a caller that reads a refusal never reads
    a fabricated number."""

    class DrainRefused(Exception):
        pass

    def _refuse(self, *args, **kwargs):
        raise LedgerError("the proof ledger module is unavailable")

    loads = _refuse
    parse = _refuse
    configuration = _refuse
    admit_locked = _refuse
    cap_view = _refuse


try:
    proof_ledger = __import__("scripts.loop.proof_ledger", fromlist=["proof_ledger"])
except ImportError:
    proof_ledger = _MissingProofLedger()


#: D2.2 REQ-LEDGER-2 (board unit D2): the only provenances a settlement may name. A RECONCILE row that does not
#: carry one of these is not evidence of a real provider charge, so reconcile() blocks it rather than trusting a
#: number that nothing measured. This is the one place every settlement crosses.
VALID_PROVENANCES = {"bridge.stderr.usage"}


#: A RUN'S OWN MONEY (owner, 2026-09-27: "Each run has its own budget and does not carry over ... Separate their budget
#: ledgers"). The loop driver gives every plain run its own state root, <run dir>/money, holding this file. In such a
#: root the ledger is the run's alone: every row counts, whatever UTC day it was written on, so a run that crosses
#: 09:00 JST never gets its budget back, and nothing another run or day wrote can reach it.
RUN_BUDGET_FILENAME = "run-budget.json"


def write_run_budget(root, run_id, openrouter_usd, until, max_slots=None):
    """Write (or, when the owner changes the budget through the intake, rewrite) a run's budget record, atomically.
    until is an ISO 8601 time WITH an offset: the run's deadline, after which nothing more is admitted. Returns the
    path; raises LedgerError on an input it would refuse to read back, so a bad record is never written."""
    rec = {"schema": "run-budget-v1", "run_id": run_id, "openrouter_usd": openrouter_usd, "until": until}
    if max_slots is not None:
        rec["max_slots"] = max_slots
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, RUN_BUDGET_FILENAME)
    tmp = path + ".tmp-%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1)
    try:
        run_budget_of(tmp)
    except LedgerError:
        os.unlink(tmp)
        raise
    os.replace(tmp, path)
    return path


def run_budget(root):
    """The run's budget record, or None when root is not a run's money root. A record that is present but unreadable
    or malformed RAISES LedgerError: a run root must never fall back to the day scoped rules of the shared root."""
    path = os.path.join(root, RUN_BUDGET_FILENAME)
    if not os.path.lexists(path):
        return None
    return run_budget_of(path)


def run_budget_of(path):
    """Read and validate one run budget file; LedgerError when it is anything but a well formed record."""
    try:
        with open(path, encoding="utf-8") as fh:
            rec = proof_ledger.loads(fh.read())
        if not isinstance(rec, dict) or rec.get("schema") != "run-budget-v1":
            raise ValueError("not a run-budget-v1 record")
        if not isinstance(rec.get("run_id"), str) or not rec["run_id"]:
            raise ValueError("no run_id")
        rec["openrouter_usd"] = _money(rec.get("openrouter_usd"), "run budget openrouter_usd")
        until = rec.get("until")
        if not isinstance(until, str) or not until:
            raise ValueError("no until")
        datetime.datetime.fromisoformat(until)   # a date that does not parse is not a deadline
        if "+" not in until[19:] and "-" not in until[19:] and not until.endswith("Z"):
            raise ValueError("until carries no offset")
        return rec
    except (OSError, ValueError, TypeError) as exc:
        raise LedgerError("run budget %s unreadable: %s" % (path, exc)) from exc


def _ledger_path(root):
    return os.path.join(root, "openrouter-ledger.jsonl")


def _lock_path(root):
    return os.path.join(root, "openrouter-ledger.lock")


def _money(value, name):
    """The one boundary every amount crosses, written or read. NaN makes
    every later 'spend > cap' comparison false (the cap dies silently), a
    negative cost grants credit, so anything that is not a finite number
    >= 0 BLOCKS as a LedgerError; it never reads as headroom."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or value < 0:
        raise LedgerError("%s must be a finite number >= 0, got %r" % (name, value))
    return value


def _read_entries(root):
    path = _ledger_path(root)
    if not os.path.lexists(path):
        return []
    if not os.path.exists(path):
        # a ledger link whose target is gone is an unreadable ledger, never an empty one: read as empty, reserve then
        # recreated the target and admitted spending (money audit 2026-09-27, finding 11)
        raise LedgerError("the ledger %s is a link to a missing file" % path)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except UnicodeDecodeError as exc:
        # Board unit M4.2: a ledger whose bytes are not valid utf-8 is corrupt
        # input. It is refused with this module's own LedgerError rather than
        # surfacing a raw UnicodeDecodeError from the interpreter, because a
        # ledger that cannot be decoded is not headroom.
        raise LedgerError("ledger %s is not valid utf-8: %s" % (path, exc)) from exc
    except OSError as exc:
        # A directory (or any other unreadable path) where the ledger belongs is
        # corrupt input too, refused here rather than crashing the caller with a
        # raw IsADirectoryError/PermissionError.
        raise LedgerError("ledger %s unreadable: %s" % (path, exc)) from exc
    entries = []
    for line in text.split("\n"):
        line = line.strip()
        if line:
            try:
                entry = proof_ledger.loads(line)   # a repeated member refuses (finding 6)
            except ValueError as exc:
                raise LedgerError("corrupt ledger line %r: %s" % (line[:80], exc))
            if not isinstance(entry, dict) or "type" not in entry:
                raise LedgerError("ledger line is not an entry: %r" % line[:80])
            entries.append(entry)
    return entries


def _append_entry(root, entry):
    with open(_ledger_path(root), "a") as f:
        f.write(json.dumps(entry) + "\n")
        # D2.2: every row is flushed and fsynced, not only a DISPATCH_START marker, so a close that the process
        # crashed just after writing is never lost while this state machine's own readers have already counted it.
        f.flush()
        os.fsync(f.fileno())


def _utc_day(at, name):
    """The UTC calendar day of a timestamp. A missing or non-finite time is
    corruption: an entry we cannot date cannot be counted for any day."""
    if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at) or at < 0:
        raise LedgerError("%s must be a finite timestamp, got %r" % (name, at))
    return int(at // 86400)


@dataclass(frozen=True)
class DailyAccount:
    """D2.3: one UTC day's accounting view of the ledger. Membership is by the
    UTC date of each RESERVE row's own `at`. A settlement that lands on a later
    day does not move membership; outstanding liability survives the boundary."""
    day: str
    actual_spend_usd: float
    outstanding_liability_usd: float
    reservation_ids_outstanding: Tuple[str, ...]
    reservation_ids_settled: Tuple[str, ...]
    reservation_ids_released: Tuple[str, ...]
    unknown_cost_count: int


def _day_number_for(day):
    """The UTC day number (days since 1970-01-01) of a YYYY-MM-DD string, or
    LedgerError. A value that is not exactly YYYY-MM-DD, or that names a day
    that does not exist, is refused here rather than silently read as another
    day; a wrong type is refused the same way and never folded into a guess."""
    if isinstance(day, bool) or not isinstance(day, str):
        raise LedgerError("day must be a YYYY-MM-DD UTC date string, got %r" % (day,))
    if len(day) != 10 or day[4] != "-" or day[7] != "-" \
            or not (day[:4].isdigit() and day[5:7].isdigit() and day[8:10].isdigit()):
        raise LedgerError("day must be a YYYY-MM-DD UTC date string, got %r" % (day,))
    try:
        parsed = datetime.datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError as exc:
        raise LedgerError("day must be a real YYYY-MM-DD UTC date, got %r" % (day,)) from exc
    return (parsed - datetime.date(1970, 1, 1)).days


def daily_accounting(root, day):
    """D2.3: one UTC day's accounting view of the ledger.

    Membership is by the UTC date of each RESERVE row's own `at`, so a
    settlement that lands on a later day does not move its reservation into the
    later day's account. Settled reservations carry a RECONCILE row; released
    carry a RELEASE row; every other reservation whose RESERVE belongs to the
    day is outstanding, and its estimate is summed into
    outstanding_liability_usd and never silently dropped. A RETAIN without a
    RECONCILE is counted once in unknown_cost_count, because its true cost is
    unknown and its liability still stands. Corrupt or unreadable ledger input
    BLOCKS with LedgerError; a wrong type for root or day is refused the same
    way, never a raw TypeError."""
    if isinstance(root, bool) or not isinstance(root, str):
        raise LedgerError("root must be a string, got %r" % (root,))
    day_number = _day_number_for(day)

    entries = _read_entries(root)
    # one validation walk over the whole file: an unknown reference, a double
    # close or an unknown entry type anywhere blocks the read.
    _walk_ledger(entries, None)

    reserve_entry_of = {}
    actual_by_id = {}
    reconciled_ids = set()
    released_seen = set()
    retained_seen = set()
    for entry in entries:
        etype = entry["type"]
        rid = entry.get("reservation_id")
        if etype == "RESERVE":
            reserve_entry_of[rid] = entry
        elif etype == "RECONCILE":
            reconciled_ids.add(rid)
            actual_by_id[rid] = entry.get("actual_cost")
        elif etype == "RELEASE":
            released_seen.add(rid)
        elif etype == "RETAIN":
            retained_seen.add(rid)

    actual_spend = 0.0
    outstanding_liability = 0.0
    unknown_cost_count = 0
    outstanding_out = []
    settled_out = []
    released_out = []

    for rid, reserve_entry in reserve_entry_of.items():
        if _utc_day(reserve_entry.get("at"), "ledger RESERVE at") != day_number:
            continue
        if rid in reconciled_ids:
            actual_spend += _money(actual_by_id[rid], "ledger actual_cost")
            settled_out.append(rid)
        elif rid in released_seen:
            released_out.append(rid)
        else:
            outstanding_liability += _money(reserve_entry.get("estimated_cost"), "ledger estimated_cost")
            outstanding_out.append(rid)
            if rid in retained_seen:
                unknown_cost_count += 1

    return DailyAccount(
        day=day,
        actual_spend_usd=_money(actual_spend, "actual_spend_usd"),
        outstanding_liability_usd=_money(outstanding_liability, "outstanding_liability_usd"),
        reservation_ids_outstanding=tuple(sorted(outstanding_out)),
        reservation_ids_settled=tuple(sorted(settled_out)),
        reservation_ids_released=tuple(sorted(released_out)),
        unknown_cost_count=unknown_cost_count,
    )


def _validate_model(model):
    """A RESERVE row's model tag (board unit M4.1): a non-empty string
    of 1 to 256 characters after strip, or None for a caller that
    does not tag its reservations yet. None is kept as a legal value,
    rather than making the keyword required as the spec's own
    signature shows, because openrouter_dispatch.py's dispatch()
    calls reserve() with no model at all until M4.4 wires it in, and
    that file is owned by another writer today: a required model
    would raise on every existing dispatch() call and take
    test_openrouter_dispatch red. Anything given that is not a real
    model string blocks; it is never silently dropped."""
    if model is None:
        return None
    if isinstance(model, bool) or not isinstance(model, str):
        raise LedgerError("model must be a string, got %r" % (model,))
    stripped = model.strip()
    if not stripped or len(stripped) > 256:
        raise LedgerError("model must be 1 to 256 chars after strip, got %r" % (model,))
    for _ch in stripped:
        # A raw control character (a newline, a NUL, a TAB, a CR, a DEL) in the
        # tag can forge a line in any downstream report or terminal that
        # prints the tag without escaping: CapabilityProfile already escapes
        # for exactly this reason (proven 2026-09-20), so the ledger tag
        # refuses the value here at the one boundary every tag crosses,
        # rather than trusting every future reader to escape. This is the
        # single behaviour mutation M-M4-MODEL-CONTROL removes.
        if ord(_ch) < 0x20 or ord(_ch) == 0x7f:
            raise LedgerError(
                "model must not contain control characters, got %r" % (model,))
    return stripped


def _walk_ledger(entries, today):
    """One walk over the ledger, enforcing every integrity check
    (unknown reference, double close, unknown entry type) exactly
    once, so current_spend, spend_breakdown, actual_costs_for_model
    and open_reservations can never quietly disagree about what the
    ledger contains. Returns (settled, inflight, abandoned, history,
    open_entries):
      settled    reconciled (real, measured) cost dated today
      inflight   open (unresolved) RESERVE estimates dated today
      abandoned  ABANDONED (unknown, but still counted) estimates
                 dated today: the safe direction, since a killed call
                 may already have been billed by the provider
      history    every (model, actual_cost) RECONCILE pair, for any
                 day, in ledger order (history looks back across
                 days by design; only a day's spend total does not)
      open_entries  the raw RESERVE entry dicts not yet closed by any
                 RECONCILE, RELEASE or ABANDONED, in ledger order
    """
    if any(row.get("type") == "DISPATCH_START" for row in entries):
        try:
            proof_ledger.parse((''.join(json.dumps(row) + '\n' for row in entries)).encode())
        except (ValueError, TypeError, KeyError) as exc:
            raise LedgerError(str(exc)) from exc
    reserved, day_of, model_of, entry_of = {}, {}, {}, {}
    closed_ids = set()
    proof_related = set()
    settled = 0.0
    abandoned = 0.0
    history = []
    for entry in entries:
        etype = entry["type"]
        rid = entry.get("reservation_id")
        if etype == "RESERVE":
            if rid in reserved:
                # a second RESERVE of one id replaced the first liability and freed its headroom (money audit
                # 2026-09-27, finding 10); the proof parser already refuses this, a legacy ledger now does too
                raise LedgerError("reservation_id %r is reserved twice; the ledger is corrupt or was hand-edited" % rid)
            reserved[rid] = _money(entry.get("estimated_cost"), "ledger estimated_cost")
            day_of[rid] = _utc_day(entry.get("at"), "ledger RESERVE at")
            entry_of[rid] = entry
            if entry.get("run_id") is not None and entry.get("attempt_id") is not None:
                proof_related.add(rid)
            model = entry.get("model")
            if model is not None:
                model_of[rid] = model
        elif etype == "DISPATCH_START":
            proof_related.add(rid)
            continue  # validated by the shared proof parser above
        elif etype == "RETAIN":
            # D2.2 REQ-LEDGER-6: a RETAIN holds a reservation's estimate in flight, it never frees headroom, so the
            # id is deliberately NOT added to closed_ids. An unknown id, or a RETAIN after a close, is corruption.
            if rid not in reserved:
                raise LedgerError(
                    "RETAIN for unknown reservation_id %r; the ledger is corrupt or was hand-edited" % rid)
            if rid in closed_ids:
                raise LedgerError(
                    "reservation_id %r is closed twice; RETAIN after it was already closed" % rid)
            continue
        elif etype == "RECONCILE":
            if rid not in reserved:
                raise LedgerError(
                    "RECONCILE for unknown reservation_id %r; the "
                    "ledger is corrupt or was hand-edited" % rid
                )
            if rid in closed_ids:
                raise LedgerError(
                    "reservation_id %r is closed twice; a second RECONCILE "
                    "would double count its spend" % rid)
            cost = _money(entry.get("actual_cost"), "ledger actual_cost")
            if today is None or day_of[rid] == today:
                settled += cost
            if rid in model_of:
                history.append((model_of[rid], cost))
            closed_ids.add(rid)
        elif etype == "RELEASE":
            if rid not in reserved or rid in closed_ids:
                raise LedgerError(
                    "RELEASE for an unknown or already closed reservation_id %r; "
                    "the ledger is corrupt or was hand-edited" % rid)
            closed_ids.add(rid)
        elif etype == "ABANDONED":
            if rid not in reserved:
                raise LedgerError(
                    "ABANDONED for unknown reservation_id %r; the "
                    "ledger is corrupt or was hand-edited" % rid)
            if rid in closed_ids:
                raise LedgerError(
                    "reservation_id %r is closed twice; ABANDONED after it "
                    "was already closed" % rid)
            if today is None or day_of[rid] == today or rid in proof_related:
                abandoned += reserved[rid]
            closed_ids.add(rid)
        else:
            # An entry type we do not know is not noise to skip: skipping it
            # is reading an unknown as safe. Proven 2026-09-20.
            raise LedgerError("unknown ledger entry type %r" % (entry.get("type"),))

    inflight = 0.0
    open_entries = []
    for rid, estimated_cost in reserved.items():
        if rid not in closed_ids:
            open_entries.append(entry_of[rid])
            if today is None or day_of[rid] == today or rid in proof_related:
                inflight += estimated_cost

    return settled, inflight, abandoned, history, open_entries


def _scope(root, now=None):
    """The day the money walk counts: None (every row) in a run's own root, else the UTC day of now."""
    return None if run_budget(root) is not None else _utc_day(time.time() if now is None else now, "now")


def _current_spend(entries, now=None, today="unset"):
    """Sum of every reconciled cost, plus every reservation that has
    not yet been reconciled, released or abandoned (a call still in
    flight, one that crashed before reconciling, or one a reaper has
    confirmed dead but whose true cost is unknown): all three still
    count against the cap, or a concurrent caller could believe there
    is room that does not really exist."""
    # THE CAP IS PER DAY. Until 2026-09-20 nothing here knew what a day was:
    # every entry ever written was summed, so the "daily" cap was a lifetime
    # cap and, once reached, refused every call forever. Integrity is still
    # checked over the WHOLE file (a corrupt old entry blocks); only MONEY is
    # counted for the UTC day in which a reservation was made. A reservation
    # abandoned by a crash therefore holds its estimate until that day ends,
    # never beyond it.
    if today == "unset":
        today = _utc_day(time.time() if now is None else now, "now")
    settled, inflight, abandoned, _, _ = _walk_ledger(entries, today)
    return _spend_total(settled, inflight, abandoned)


def _spend_total(settled, inflight, abandoned):
    """The day's total, itself a finite amount: finite parts can overflow to inf, which is not a spend (money audit
    2026-09-27, finding 8)."""
    return _money(settled + inflight + abandoned, "spend total")


class _LockedLedger:
    """A real flock held for the duration of a reserve-then-check
    critical section, so two concurrent processes reading "how much is
    spent" can never both decide there is room for a call that only
    one of them can actually afford."""

    def __init__(self, root):
        self._root = root
        self._fh = None

    def __enter__(self):
        os.makedirs(self._root, exist_ok=True)
        self._fh = open(_lock_path(self._root), "a")
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb):
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        self._fh.close()


def _open_reservation(entries, reservation_id, holder_id=None):
    """The RESERVE entry for reservation_id, or LedgerError when it does not
    exist, is already closed, or (when holder_id is given) belongs to someone
    else. One guard for reconcile and release: a reservation is closed once,
    by its owner. Proven 2026-09-20: release took any id and freed another
    holder's in-flight spend; a second reconcile doubled the spend."""
    reserve_entry, closed = None, False
    for entry in entries:
        if entry.get("reservation_id") != reservation_id:
            continue
        if entry["type"] == "RESERVE":
            reserve_entry = entry
        elif entry["type"] in ("RECONCILE", "RELEASE", "ABANDONED"):
            closed = True
    if reserve_entry is None:
        raise LedgerError("unknown reservation_id %r" % reservation_id)
    if closed:
        raise LedgerError("reservation_id %r is already closed" % reservation_id)
    if holder_id is not None and reserve_entry.get("holder_id") != holder_id:
        raise LedgerError("reservation_id %r belongs to %r, not to %r"
                          % (reservation_id, reserve_entry.get("holder_id"), holder_id))
    return reserve_entry


def reserve(root, daily_cap, estimated_cost, holder_id, model=None, now=None, bound=None, timeout_seconds=None):
    """Reserve estimated_cost against the daily cap. Raises
    BudgetExceeded if current spend plus this reservation would exceed
    daily_cap. Returns a reservation_id to pass to reconcile() or
    release() afterward.

    In a proof phase this is where a call is ADMITTED (objection 8):
    proof_ledger.admit_locked runs inside the held lock that
    proof_ledger.mark_ending also takes, with the call's own
    timeout_seconds, so the call is registered before the run's ending
    marker or refused (DrainRefused) with nothing appended.

    bound (objection 11) is written into the RESERVE row as given: True
    only when estimated_cost is the catalog priced worst case of every
    attempt the bridge can make, False otherwise, absent when None. Only
    a real bool is accepted.

    model (board unit M4.1) tags the RESERVE row with the model this
    estimate is for, so actual_costs_for_model() can build per model
    history later. Optional (see _validate_model for why); when given
    it must be a real model string or the call blocks with nothing
    appended.

    The check-then-append happens inside one held flock, so two
    concurrent callers racing for the last bit of headroom can never
    both succeed: the second one to acquire the lock sees the first
    one's reservation already in the ledger.

    daily_cap may be a callable of the registration time (epoch seconds)
    returning the cap: it is then read inside the held lock, at the moment
    of admission, so a cap that changed while the caller waited (an owner
    grant expiring during the slot or lock wait) is never admitted against
    its stale value (money audit 2026-09-27, finding 9).
    """
    if not callable(daily_cap):
        _money(daily_cap, "daily_cap")
    _money(estimated_cost, "estimated_cost")
    model = _validate_model(model)
    if bound is not None and not isinstance(bound, bool):
        raise LedgerError("bound must be True, False or None, got %r" % (bound,))
    explicit_now = now
    with _LockedLedger(root):
        # The clock is read after the lock is held: a registration that waited for the lock is admitted against the
        # time it actually registers, never the time it started waiting (Codex check-in 2, finding 5).
        now = explicit_now if explicit_now is not None else time.time()
        entries = _read_entries(root)
        policy = proof_ledger.configuration(root)
        if policy is not None:
            proof_ledger.admit_locked(policy["run_dir"], timeout_seconds, __file__, now=now)
        view = proof_ledger.cap_view(root, now) if policy is not None else None
        current = view["total"] if view is not None else _current_spend(entries, now, _scope(root, now))
        cap = _money(daily_cap(now) if callable(daily_cap) else daily_cap, "daily_cap")
        if current + estimated_cost > cap:
            raise BudgetExceeded(
                "reserving $%.6f would bring spend to $%.6f, over the "
                "$%.6f daily cap (current spend $%.6f)"
                % (estimated_cost, current + estimated_cost, cap, current)
            )
        # uuid4: holder + millisecond + pid collided when two threads reserved
        # in the same millisecond (seen live 2026-09-20), so one id named two
        # reservations and one close could hit both.
        # D2.2 REQ-LEDGER-1: holder + millisecond + pid + a FULL uuid4 hex. The old [:12] truncation could still
        # collide for two reservations in the same millisecond, and one id naming two reservations lets one close
        # hit both.
        reservation_id = "%s-%d-%d-%s" % (holder_id, int(now * 1000), os.getpid(),
                                          uuid.uuid4().hex)
        entry = {
            "type": "RESERVE",
            "reservation_id": reservation_id,
            "holder_id": holder_id,
            "estimated_cost": estimated_cost,
            "at": now,
        }
        if policy is not None:
            # the timeout this call was admitted with, so the dispatch marker admits it again against the deadline
            # after its own lock wait (Codex check-in 3, finding 1)
            entry.update(run_id=policy["run_id"], attempt_id=policy["attempt_id"], timeout_seconds=timeout_seconds)
        if model is not None:
            entry["model"] = model
        if bound is not None:
            entry["bound"] = bound
        _append_entry(root, entry)
    return reservation_id


def mark_dispatched(root, reservation_id, holder_id=None, now=None, stopped=None):
    """Durable boundary before provider contact, serialized with admission. `stopped`, when given, is asked inside
    the held lock: a caller told to stop while it waited here gets DrainRefused before any marker is written.

    D2.2 REQ-LEDGER-4: appends one DISPATCH_START when the RESERVE exists and there is no prior DISPATCH_START for
    that id; a second call for the same id is a no op rather than a second marker, so a retry cannot forge a second
    boundary. An unknown id raises LedgerError. holder_id, when given, is checked exactly as before; when None the
    reservation is only required to exist."""
    with _LockedLedger(root):
        now = time.time() if now is None else now   # stamped inside the lock, in the order rows are appended
        entries = _read_entries(root)
        reserve_entry = _open_reservation(entries, reservation_id, holder_id)
        if any(row.get('type') == 'DISPATCH_START' and row.get('reservation_id') == reservation_id for row in entries):
            return  # D2.2 REQ-LEDGER-4: a second call is a no op, never a second marker
        policy = proof_ledger.configuration(root)
        if policy is None:
            _append_entry(root, dict(type='DISPATCH_START', reservation_id=reservation_id, at=now))
            return
        if os.path.lexists(os.path.join(policy['run_dir'], 'proof', 'ending.json')):
            # the run ended between this call's registration and its provider contact: no contact now, and the
            # caller releases the reservation, a known zero, rather than leave settle an open call
            raise proof_ledger.DrainRefused('the run is ending; %r is not dispatched' % reservation_id)
        if stopped is not None and stopped():
            raise proof_ledger.DrainRefused('this process was told to stop; %r is not dispatched' % reservation_id)
        if 'run_id' in reserve_entry:
            # a proof reservation is admitted again here, against the time the marker is written: the lock wait
            # between reservation and marker can eat the margin admission granted (Codex check-in 3, finding 1)
            if reserve_entry.get('timeout_seconds') is None:
                raise proof_ledger.DrainRefused('%r records no admitted timeout; it is not dispatched' % reservation_id)
            proof_ledger.admit_locked(policy['run_dir'], reserve_entry['timeout_seconds'], __file__, now=now)
            if (reserve_entry['run_id'], reserve_entry.get('attempt_id')) != (policy['run_id'], policy['attempt_id']):
                raise LedgerError('dispatch attempt differs from reservation')
        _append_entry(root, dict(type='DISPATCH_START', reservation_id=reservation_id, at=now,
                                run_id=policy['run_id'], attempt_id=policy['attempt_id']))


def reconcile(root, reservation_id, actual_cost, provenance=None, now=None, cost_source=None, holder_id=None):
    """Record measured settlement, retaining the reservation's proof identity.

    D2.2 REQ-LEDGER-2: provenance, when given, must be a member of VALID_PROVENANCES, and a bad amount or a bad
    provenance blocks with nothing appended. A RECONCILE already on file for this id with the identical
    (actual_cost, provenance) is a no op with zero appends; one with a different amount or provenance raises
    LedgerError, because a second close would double count the spend. A RETAIN or RELEASE for the id blocks a late
    RECONCILE. provenance stays optional so the pre D2 callers that pass neither keep working unchanged, and their
    double close still raises exactly as it always did."""
    _money(actual_cost, "actual_cost")
    if provenance is not None:
        if isinstance(provenance, bool) or not isinstance(provenance, str):
            raise LedgerError("provenance must be a string, got %r" % (provenance,))
        if provenance not in VALID_PROVENANCES:
            raise LedgerError("unknown provenance %r; expected one of %s"
                              % (provenance, sorted(VALID_PROVENANCES)))
    with _LockedLedger(root):
        now = time.time() if now is None else now   # stamped inside the lock, in the order rows are appended
        entries = _read_entries(root)
        first = None
        for scan in entries:
            if scan.get("reservation_id") != reservation_id:
                continue
            if scan.get("type") == "RECONCILE":
                if first is None:
                    first = scan
            elif scan.get("type") == "RETAIN":
                raise LedgerError("reservation_id %r is retained; a late RECONCILE is refused" % reservation_id)
            elif scan.get("type") == "RELEASE":
                raise LedgerError("reservation_id %r is released; a late RECONCILE is refused" % reservation_id)
        if first is not None:
            if provenance is not None and first.get("actual_cost") == actual_cost and first.get("provenance") == provenance:
                return  # identical D2.2 reconcile: no op, zero appends
            raise LedgerError(
                "reservation_id %r is already reconciled; a second RECONCILE with a different amount or provenance "
                "would double count its spend" % reservation_id)
        reserve_entry = _open_reservation(entries, reservation_id, holder_id)
        markers = [row for row in entries if row.get('type') == 'DISPATCH_START' and row.get('reservation_id') == reservation_id]
        row = dict(type='RECONCILE', reservation_id=reservation_id, actual_cost=actual_cost, at=now)
        if provenance is not None:
            row["provenance"] = provenance
        if 'run_id' in reserve_entry or markers:
            if len(markers) != 1 or cost_source != 'provider_usage':
                raise LedgerError('proof settlement requires dispatch and provider cost')
            row.update(run_id=markers[0]['run_id'], attempt_id=markers[0]['attempt_id'], cost_source=cost_source)
        _append_entry(root, row)


def release(root, reservation_id, holder_id=None, now=None, reason=None):
    """Release a reservation that will never be reconciled, freeing its estimated cost from the running total
    without recording a spend.

    D2.2 REQ-LEDGER-3: with reason given, it must be a non empty string, the RESERVE must exist, and there must be
    no DISPATCH_START, no RECONCILE and no RETAIN for the id, else LedgerError. That is the prevention control: a
    dispatched or settled reservation must never be freed into fake headroom. The pre D2 holder check (holder_id,
    third positional) is preserved when reason is not given, so every existing caller that names its holder keeps
    exactly its old behaviour and exit code."""
    if reason is not None:
        if isinstance(reason, bool) or not isinstance(reason, str) or not reason.strip():
            raise LedgerError("reason must be a non-empty string, got %r" % (reason,))
        with _LockedLedger(root):
            now = time.time() if now is None else now
            entries = _read_entries(root)
            _open_reservation(entries, reservation_id)
            for row in entries:
                if row.get("reservation_id") != reservation_id:
                    continue
                if row.get('type') in ('DISPATCH_START', 'RECONCILE', 'RETAIN'):
                    raise LedgerError(
                        "reservation_id %r was dispatched, reconciled or retained; it cannot be released"
                        % reservation_id)
            _append_entry(root, {
                "type": "RELEASE",
                "reservation_id": reservation_id,
                "reason": reason.strip(),
                "at": now,
            })
        return
    if holder_id is None:
        raise LedgerError("release requires a reason")
    with _LockedLedger(root):
        now = time.time() if now is None else now   # stamped inside the lock, in the order rows are appended
        entries = _read_entries(root)
        _open_reservation(entries, reservation_id, holder_id)
        if any(row.get("type") == "DISPATCH_START" and row.get("reservation_id") == reservation_id for row in entries):
            raise LedgerError("cannot release after dispatch")
        _append_entry(root, {
            "type": "RELEASE",
            "reservation_id": reservation_id,
            "at": now,
        })


def retain(root, reservation_id, reason, now=None):
    """D2.2 REQ-LEDGER-5: keep a reservation's liability when its outcome is unknown, without freeing headroom.

    A non empty reason is required and the RESERVE must exist. A second RETAIN for the same id is idempotent. A
    RECONCILE or RELEASE for the id makes retain raise, because a retained reservation must never quietly settle
    later. The estimate stays counted by _current_spend (see _walk_ledger), which is the entire point: an unknown
    outcome is a liability, never a zero."""
    if isinstance(reason, bool) or not isinstance(reason, str) or not reason.strip():
        raise LedgerError("reason must be a non-empty string, got %r" % (reason,))
    with _LockedLedger(root):
        now = time.time() if now is None else now
        entries = _read_entries(root)
        _open_reservation(entries, reservation_id)
        already = False
        for row in entries:
            if row.get("reservation_id") != reservation_id:
                continue
            if row.get("type") == "RETAIN":
                already = True
            elif row.get("type") in ("RECONCILE", "RELEASE"):
                raise LedgerError(
                    "reservation_id %r is already reconciled or released; it cannot be retained"
                    % reservation_id)
        if already:
            return
        _append_entry(root, {
            "type": "RETAIN",
            "reservation_id": reservation_id,
            "reason": reason.strip(),
            "at": now,
        })


def abandon(root, reservation_id, reason, now=None):
    """Close a reservation whose holder process is confirmed gone
    (audit issue F14b: SIGKILL skips release(), so a dead holder's
    estimate would otherwise hold forever). Records the row as
    ABANDONED, never as a RELEASE (which means nothing was spent) and
    never as a RECONCILE of 0 (which means exactly zero was spent): a
    killed call may already have been billed by the provider, so its
    cost is unknown, not zero, and it keeps counting against the cap
    (see _walk_ledger) until reconciled by some later, honest number.

    Uses the same closed-once-ever guard as reconcile()/release() (no
    holder_id check here, since the reaper calling this is not the
    holder: the holder is presumed gone, that is the whole premise)."""
    if isinstance(reason, bool) or not isinstance(reason, str) or not reason.strip():
        raise LedgerError("reason must be a non-empty string, got %r" % (reason,))
    with _LockedLedger(root):
        now = time.time() if now is None else now   # stamped inside the lock, in the order rows are appended
        _open_reservation(_read_entries(root), reservation_id)
        _append_entry(root, {
            "type": "ABANDONED",
            "reservation_id": reservation_id,
            "reason": reason.strip(),
            "at": now,
        })


def actual_costs_for_model(root, model, limit=1000):
    """The last `limit` real (RECONCILE) costs billed against `model`,
    in ledger order, for board unit M4.2's estimator to build a
    percentile off of. A RESERVE with no model tag (an old row, or one
    from a caller that has not started tagging yet) is not evidence
    about any specific model and is ignored for history. Never
    invents a default: an unknown model, or one with no RECONCILE yet,
    returns an empty list. History looks back across every day (only
    a day's spend total is day scoped), and a corrupt or otherwise
    invalid ledger blocks exactly as it does for current_spend."""
    model = _validate_model(model)
    if model is None:
        raise LedgerError("model is required to look up history, got None")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise LedgerError("limit must be a positive integer, got %r" % (limit,))
    entries = _read_entries(root)
    today = _utc_day(time.time(), "now")
    _, _, _, history, _ = _walk_ledger(entries, today)
    costs = [cost for hmodel, cost in history if hmodel == model]
    return costs[-limit:]


def spend_breakdown(root, now=None):
    """Today's spend split three ways, never mixed into one number: -
    settled: reconciled, a real measured cost - inflight: an open
    reservation still running, or one that crashed before
    reconciling: an estimate - abandoned: a reservation a reaper
    confirmed dead (F14b): an estimate of unknown truth that still
    counts, the safe direction, kept visibly separate from settled so
    a report is never read as more real spend than it is total is
    settled + inflight + abandoned, and always equals current_spend()
    for the same `now`: the two are built from the same walk and can
    never quietly disagree."""
    view = proof_ledger.cap_view(root, time.time() if now is None else now)
    if view is not None:
        return view
    entries = _read_entries(root)
    settled, inflight, abandoned, _, _ = _walk_ledger(entries, _scope(root, now))
    return {
        "settled": settled,
        "inflight": inflight,
        "abandoned": abandoned,
        "total": _spend_total(settled, inflight, abandoned),
    }


def open_reservations(root):
    """Every RESERVE row not yet closed by RECONCILE, RELEASE or
    ABANDONED, as raw entry dicts (reservation_id, holder_id,
    estimated_cost, at, and model when tagged), in ledger order. A
    plain read like current_spend, needing no lock. Used by the F14b
    reaper to find a dead holder's abandoned reservation."""
    entries = _read_entries(root)
    _, _, _, _, open_entries = _walk_ledger(entries, _scope(root))
    return open_entries


def current_spend(root, now=None):
    """The current total spend, read fresh from the ledger, for
    reporting or a pre-flight check outside the reserve/reconcile
    critical section (a plain read needs no lock: it observes a
    consistent snapshot even mid-write, since JSONL appends are
    atomic at the line level and a reader only ever sees complete
    lines already flushed)."""
    view = proof_ledger.cap_view(root, time.time() if now is None else now)
    return view["total"] if view is not None else _current_spend(_read_entries(root), now, _scope(root, now))
