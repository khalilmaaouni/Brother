"""load_reservation: admit heavy work from MEASURED resources and an explicit
machine reservation, never from what the command is called.

UNIT ORCH-34. THE DECIDING PROPERTY, from the debate where the orchestrator's
own proposal LOST: heavy work is admitted through an EXPLICIT machine
reservation, never through a name-based exemption. The losing idea was to
always allow commands that LOOK cheap (a commit, a copy, a read): A COMMAND'S
NAME IS NOT ITS COST. A commit can run expensive hooks and must write; a copy
can consume gigabytes; a nominal read can launch subprocesses. Under a
critically low disk, admitting supposedly cheap writes can destroy the
remaining recovery margin, and this estate has a recorded zero-space incident
that lost three database files. So admit() below never inspects a command
string or a `work["name"]` field for its verdict: `name` is carried only for
the audit trail (see record_refusal), and is proven inert by the mutation
test named in this unit's brief (temporarily branch on it, watch the suite
fail, revert).

THE THREE BANDS, reused from resource_gate.py's own measured constants,
never re-derived here:
  - disk_free_gib >= resource_gate.DISK_CLEANUP_GIB (15): ADMIT. Plenty of
    room, no coordination needed.
  - resource_gate.DISK_REFUSE_GIB (8) <= disk < DISK_CLEANUP_GIB (15):
    ADMIT only when a LIVE machine reservation is held; otherwise REFUSE.
    This is the band disk_floor_gate.py used to hand out on a bare name
    check; here it costs an explicit reservation.
  - disk < resource_gate.DISK_REFUSE_GIB (8): REFUSE outright. A reservation
    does not buy heavy work back in below the hard floor.
The one exception to all three bands is the bounded persistence operation,
below.

THE ONE BOUNDED PERSISTENCE OPERATION (`work["kind"] == "persist"`) is
sanctioned even under the refuse floor, because durability needs a path that
is accounted for rather than assumed cheap. It is never exempt from ITS OWN
declared budget: `work["bytes"]` must be given up front, and is refused
outright when it exceeds the bytes actually left. It also never trusts that
it is the only such operation in flight: `work["concurrent_bytes"]` is the
caller's own declared sum of bytes already committed by OTHER bounded
persistence operations it knows about (this module is a pure, stateless
decision function; it has no channel to observe a sibling call by itself, so
tracking concurrent commitments is the caller's job, exactly the same way
machine_reservation.py leaves dead-holder detection to itself rather than
letting a second module guess).

CONTINGENCY AND EDGES, named here because the docstring is where the next
reader looks before trusting this module:

  A RESERVATION HELD BY A DEAD HOLDER. Never reimplemented: this module
  calls machine_reservation._dead_reason() on the raw reservation record
  (the same dict machine_reservation stores under data["holder"]), so a
  dead lease or a dead pid on this host reads as "not held" using
  machine_reservation's OWN expiry and pid-liveness rule, not a second copy
  of it.

  A BOUNDED OPERATION WHOSE BUDGET IS LARGER THAN THE FREE SPACE. Refused:
  budget_bytes > disk_free_gib * GIB fails the check before anything else
  about the persist path matters.

  TWO BOUNDED OPERATIONS AT ONCE. The second call's `concurrent_bytes` adds
  the first one's still-outstanding budget to its own before comparing
  against the same free-space reading, so two individually-plausible
  budgets that would together overrun the disk are refused even though
  each alone would have fit.

  A READING THAT IS PRESENT BUT ZERO. disk_free_gib == 0.0 is a real
  number, not NO-DATA: it falls straight into the refuse-floor band like
  any other low reading, it is never treated as an unreadable field.

  A READING MISSING ONE FIELD. A `reading` dict with no "disk_free_gib" key
  at all is treated exactly like an explicit None: REFUSE, with NO-DATA
  named in the reasons. An unreadable resource reading REFUSES heavy work;
  it never reads as plenty of room. This applies to the persist path too:
  a budget cannot be checked against a free-space number nobody could read.

Python 3, standard library only, plus this repo's own machine_reservation
and resource_gate modules (reused for their bands and their liveness rule,
never re-derived).
"""
import json
import os
import time

import machine_reservation
import resource_gate

NODATA = "NO-DATA"
ADMIT, REFUSE = "ADMIT", "REFUSE"
GIB = resource_gate.GIB
GATE_VERSION = "load_reservation/1"

#: Every refusal is recorded here by default; tests redirect this via the
#: `log_path` argument to a temp file so nothing here ever touches the real
#: home directory.
DEFAULT_REFUSAL_LOG = os.path.expanduser(
    "~/.claude/state/load-reservation-refusals.jsonl")


def _now(clock=None):
    return (clock or time.time)()


def _disk_free_gib(reading):
    """The disk_free_gib number in `reading`, or None for anything that is
    not trustworthy evidence: not a dict, the field missing entirely, or the
    field explicitly None or unparseable. A present-but-zero reading is a
    real number and is returned as 0.0, never folded into this None path."""
    if not isinstance(reading, dict) or "disk_free_gib" not in reading:
        return None
    value = reading.get("disk_free_gib")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _reservation_status(reservation, now):
    """(held, detail). held is True only when machine_reservation's OWN
    liveness rule says this record is still live; a dead holder (expired
    lease, or a dead pid on this host) is reported as not held, using
    _dead_reason so the reason it died is preserved for the audit trail."""
    if not reservation:
        return False, "no reservation is recorded"
    reason = machine_reservation._dead_reason(reservation, now)
    if reason is not None:
        return False, ("reservation held by %s is not live: %s"
                        % (reservation.get("holder", "unknown"), reason))
    return True, "reservation held by %s" % reservation.get("holder", "unknown")


def _verdict(kind, ok, reasons, disk, reservation_detail):
    return {
        "verdict": ADMIT if ok else REFUSE,
        "kind": kind,
        "reasons": list(reasons),
        "disk_free_gib": disk,
        "reservation": reservation_detail,
        "gate": GATE_VERSION,
    }


def admit(work, reading, reservation, clock=None, log_path=None):
    """(dict) the ADMIT/REFUSE verdict for `work`.

    `work` is a caller-supplied classification dict, never a command string
    this module parses: {"kind": "persist", "bytes": N, "concurrent_bytes":
    M} for the bounded persistence operation, or {"kind": <anything else>}
    (default "heavy") for everything that must clear the three disk bands.
    An optional `work["name"]` is carried into the refusal record for the
    audit trail only; it is never read by the decision itself.

    `reading` is a dict shaped like resource_gate.read()'s output (at least
    "disk_free_gib"). `reservation` is the raw record machine_reservation
    stores under data["holder"], or None/{} when nothing is reserved.

    Every REFUSE is recorded via record_refusal() before it is returned, so
    a refusal is data, never only a return value nobody wrote down.
    """
    now = _now(clock)
    work = work if isinstance(work, dict) else {}
    kind = work.get("kind", "heavy")
    disk = _disk_free_gib(reading)
    held, res_detail = _reservation_status(reservation, now)

    if disk is None:
        result = _verdict(kind, False,
                           ["disk_free_gib is %s: an unreadable reading "
                            "never reads as plenty of room" % NODATA],
                           None, res_detail)
        record_refusal(result, work, log_path)
        return result

    if kind == "persist":
        result = _admit_persist(work, disk, res_detail)
        if result["verdict"] == REFUSE:
            record_refusal(result, work, log_path)
        return result

    # Every other kind of work, WHATEVER ITS NAME, follows the same three
    # bands. No command text or work["name"] is consulted here: that is the
    # exact property the losing "cheap-by-name" idea failed on.
    if disk < resource_gate.DISK_REFUSE_GIB:
        result = _verdict(kind, False,
                           ["disk_free_gib %.2f is under the refuse floor of "
                            "%.0f GiB; no reservation admits heavy work here"
                            % (disk, resource_gate.DISK_REFUSE_GIB)],
                           disk, res_detail)
        record_refusal(result, work, log_path)
        return result

    if disk < resource_gate.DISK_CLEANUP_GIB:
        if not held:
            result = _verdict(kind, False,
                               ["disk_free_gib %.2f is under the cleanup "
                                "band of %.0f GiB and %s"
                                % (disk, resource_gate.DISK_CLEANUP_GIB, res_detail)],
                               disk, res_detail)
            record_refusal(result, work, log_path)
            return result
        return _verdict(kind, True,
                         ["disk_free_gib %.2f is under the cleanup band of "
                          "%.0f GiB but %s"
                          % (disk, resource_gate.DISK_CLEANUP_GIB, res_detail)],
                         disk, res_detail)

    return _verdict(kind, True,
                     ["disk_free_gib %.2f clears the cleanup band of %.0f GiB"
                      % (disk, resource_gate.DISK_CLEANUP_GIB)],
                     disk, res_detail)


def _admit_persist(work, disk_free_gib, res_detail):
    """The bounded persistence carve-out: never gated by the reservation or
    the disk bands, gated only by its own declared budget against the bytes
    actually left, counting whatever other in-flight budget the caller
    declares via concurrent_bytes."""
    try:
        budget = float(work.get("bytes"))
    except (TypeError, ValueError):
        return _verdict("persist", False,
                         ["a bounded persistence operation must declare a "
                          "byte budget up front; got %r" % (work.get("bytes"),)],
                         disk_free_gib, res_detail)
    if budget < 0:
        return _verdict("persist", False,
                         ["a byte budget must not be negative; got %r" % budget],
                         disk_free_gib, res_detail)
    try:
        concurrent = float(work.get("concurrent_bytes", 0) or 0)
    except (TypeError, ValueError):
        concurrent = 0.0
    free_bytes = disk_free_gib * GIB
    total = budget + concurrent
    if total > free_bytes:
        return _verdict("persist", False,
                         ["byte budget %.0f (plus %.0f already committed to "
                          "other in-flight bounded persistence) exceeds the "
                          "%.0f bytes left (%.2f GiB free)"
                          % (budget, concurrent, free_bytes, disk_free_gib)],
                         disk_free_gib, res_detail)
    return _verdict("persist", True,
                     ["byte budget %.0f (plus %.0f already committed) fits "
                      "in %.0f bytes left" % (budget, concurrent, free_bytes)],
                     disk_free_gib, res_detail)


def record_refusal(result, work, log_path=None):
    """One JSON line per refusal: the command classification (`work`,
    including its optional name), the readings that caused it (already on
    `result`), and the gate version, because this estate refused about
    twenty commands in one night once and wrote none of them down. Fails
    silent: logging must never change the verdict."""
    path = log_path or DEFAULT_REFUSAL_LOG
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rec = dict(result)
        rec["at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        rec["work"] = work
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except (OSError, TypeError, ValueError):
        pass
