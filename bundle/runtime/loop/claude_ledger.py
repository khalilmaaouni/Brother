#!/usr/bin/env python3
"""The one writer and the one reader of the Claude call ledger (findings 14 and 29, review 2026-09-26; U7).

model_call.py registers every Claude call with start() BEFORE provider contact and closes it with exactly one
finish() row (event "done") written in a finally. Both rows carry the same call id, an aware UTC stamp and, in a proof
phase, the run's tags. start() holds the ledger's lock (proof_ledger.ledger_lock, the lock mark_ending also takes)
and, in a proof phase, admits the call there (proof_ledger.admit_locked): a call is registered before the run's
ending marker or refused with nothing written (objection 8). Every reader goes through tally():
  - a call is a START row; a DONE row pairs with its start ONLY through the call id, so an unrelated completion can
    never cancel an uncosted call (counting starts minus dones did exactly that);
  - a START with no terminal row whose expires_at is still ahead is PENDING (owner question Q2, default reading):
    counted apart, never as unknown; past expires_at, or without one (legacy rows), it is uncosted (NO-DATA);
  - a run tally (run_id) counts only rows tagged with that run; an untagged row there is NO-DATA;
  - cost evidence is a finite, non negative number that is not a bool; a null, NaN, negative or text figure is not
    spend, and the call it belongs to stays uncosted;
  - money is the sum of valid DONE costs inside the window, paired or not (a legacy DONE row without an id is still
    money that was reported), while a START with no id cannot be paired and stays uncosted: historical rows are NO-DATA;
  - a missing ledger is a measured empty one; a ledger that cannot be read is NO-DATA; a torn line is counted.
usage: claude_ledger.py tally <ledger path> <since ISO>   prints "USD <x>" and "NOTE <text or empty>", exit 0
       claude_ledger.py --selftest
"""
import collections, datetime, json, math, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proof_ledger  # noqa: E402

#: The seconds model_call's transport waits past the call's own timeout before it kills the child (_run_in).
COMMUNICATE_GRACE_S = 30
#: A START expires this long after its timeout ends: the communicate grace plus the proof drain margin (U7). Admission
#: uses the same sum, so every admitted call expires by the run's deadline.
EXPIRY_S = COMMUNICATE_GRACE_S + proof_ledger.DRAIN_MARGIN_S


def ledger_path(env=None):
    env = os.environ if env is None else env
    return os.path.expanduser(env.get("BROTHER_CLAUDE_CALLS_LEDGER") or "~/.claude/evidence/claude-calls.jsonl")


def _stamp(t):
    return t.astimezone(datetime.timezone.utc).isoformat(timespec="microseconds")


def _append(path, row):
    """One line, flushed and fsynced. Raises OSError: a row that cannot be made durable is never reported written."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _read(path):
    """The ledger's bytes; b"" for a missing ledger (a measured empty one). Raises OSError for one that cannot be read,
    a dangling link included, so no writer recreates it as a clean, empty ledger (finding 4, 2026-09-27)."""
    if not os.path.lexists(path):
        return b""
    with open(path, "rb") as f:
        return f.read()


def _call_id(row):
    c = row.get("call")
    return c if isinstance(c, str) and c else None


def _is_terminal(row):
    return row.get("event") == "done" or "cost_usd" in row


def _census(raw):
    """(rows, unreadable, starts, terminals) over the WHOLE ledger: each readable row with its instant, the count of lines
    that are not a JSON object with a readable `at`, and how many START and terminal rows carry each call id."""
    rows, unreadable, starts, terminals = [], 0, collections.Counter(), collections.Counter()
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        try:
            r = proof_ledger.loads(line)   # a repeated member (a cost of 9 then 0) is an unreadable row, never a zero
        except ValueError:
            unreadable += 1
            continue
        at = instant(r.get("at")) if isinstance(r, dict) and isinstance(r.get("at"), str) else None
        if at is None:
            unreadable += 1
            continue
        rows.append((r, at))
        cid = _call_id(r)
        if cid:
            (terminals if _is_terminal(r) else starts)[cid] += 1
    return rows, unreadable, starts, terminals


def _identity_faults(starts, terminals):
    """A call id names ONE call (finding 5, 2026-09-27): ids carried by more than one START, ids carried by more than one
    terminal, and ids with a terminal but no START. Any of them means no row can be counted exactly once."""
    return dict(duplicate_starts=sum(1 for n in starts.values() if n > 1),
                duplicate_done=sum(1 for n in terminals.values() if n > 1),
                orphan_done=sum(1 for c in terminals if c not in starts))


def start(path, row, timeout_seconds, env=None, now=None):
    """Register one Claude call before provider contact; returns the START row written. The row gets an aware UTC
    `at`, `expires_at` = at + timeout + EXPIRY_S and, in a proof phase, `run` and `attempt`. Under the ledger lock it
    first reads the WHOLE ledger (finding 4, 2026-09-27): a ledger that already reads as corrupt admits nothing. Raises,
    writing nothing: proof_ledger.EvidenceError (a ValueError) for a row with no call id, a timeout that is not a finite
    non negative number, an unreadable proof start or launch record, or a ledger with a line that is not a row, an
    incomplete last row or an identity fault; FileExistsError when the ledger already holds this call id;
    proof_ledger.DrainRefused when the run is ending, the call would outlive its deadline, or this module is outside
    BROTHER_CODE_ROOT; OSError when the ledger cannot be read or the row cannot be made durable. Unknown money (a
    pending, uncosted or legacy id-less call) is not corruption: the driver judges it (U7)."""
    env = os.environ if env is None else env
    cid = _call_id(row)
    if cid is None:
        raise proof_ledger.EvidenceError("a Claude START needs a call id, or no terminal row can ever close it")
    timeout = proof_ledger.amount(timeout_seconds)
    ident = proof_ledger.run_identity(env)
    # AN ORDINARY RUN'S ROWS CARRY ITS IDENTITY TOO (2026-10-04): tags only; proof admission below stays proof only
    plain = proof_ledger.ordinary_run_identity(env) if ident is None else None
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with proof_ledger.ledger_lock(path):
        raw = _read(path)
        _, unreadable, starts, terminals = _census(raw)
        faults = dict(_identity_faults(starts, terminals), unreadable_rows=unreadable,
                      incomplete_last_row=int(bool(raw) and not raw.endswith(b"\n")))
        if any(faults.values()):
            raise proof_ledger.EvidenceError("the Claude call ledger reads as corrupt (%s); no new call is registered"
                                             % ", ".join("%s=%d" % kv for kv in sorted(faults.items()) if kv[1]))
        if cid in starts:   # an id with only a terminal is an orphan, refused just above
            raise FileExistsError("the Claude call ledger already holds call id %s; no second START is written" % cid)
        at = datetime.datetime.now(datetime.timezone.utc) if now is None else now
        if ident is not None:
            proof_ledger.admit_locked(env.get("BROTHER_RUN_DIR", ""), timeout + COMMUNICATE_GRACE_S, __file__,
                                      env=env, now=at.timestamp())
        rec = dict(row, at=_stamp(at), expires_at=_stamp(at + datetime.timedelta(seconds=timeout + EXPIRY_S)))
        if ident is not None or plain is not None:
            rec.update(run=(ident or plain)[0], attempt=(ident or plain)[1])
        unit_run = env.get("BROTHER_UNIT_RUN")
        if isinstance(unit_run, str) and UNIT_RUN.fullmatch(unit_run):
            rec["unit_run"] = unit_run   # the unit runner's own folder: every call under it, native or helper (2026-10-04)
        _append(path, rec)
    return rec


def finish(path, row, now=None):
    """Append the terminal row of a call under the ledger lock, stamped aware UTC. Never refused for the run's ending
    nor for a damaged ledger: a call admitted before either must still be closed. Raises, writing nothing:
    proof_ledger.EvidenceError (a ValueError) for a row with no call id; FileExistsError when this call already has
    its terminal row (finding 5, 2026-09-27: a second one doubled the cost); OSError when the ledger cannot be read or
    the row cannot be made durable."""
    cid = _call_id(row)
    if cid is None:
        raise proof_ledger.EvidenceError("a Claude terminal row needs a call id, or it closes no START")
    at = datetime.datetime.now(datetime.timezone.utc) if now is None else now
    rec = dict(row, at=_stamp(at))
    rec.setdefault("event", "done")
    with proof_ledger.ledger_lock(path):
        rows, _, _, terminals = _census(_read(path))
        if terminals[cid]:
            raise FileExistsError("Claude call %s already has its terminal row; no second one is written" % cid)
        # THE TERMINAL ROW CARRIES ITS START'S unit_run (2026-10-04), copied here, the one place every caller closes a
        # call, so a unit tally never depends on each caller remembering to copy it; a caller's own value is kept
        tag = next((r.get("unit_run") for r, _ in rows if _call_id(r) == cid and not _is_terminal(r)), None)
        if isinstance(tag, str) and "unit_run" not in rec:
            rec["unit_run"] = tag
        _append(path, rec)
    return rec


#: a unit runner's tag, <sub>-HHMMSS@<claim epoch> (unit_runner, beside claim_run_dir): the only value start() writes
UNIT_RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}-\d{6}@\d{9,11}")


def valid_cost(c):
    """A finite, non negative number that is not a bool. An integer too large for a float (10**1000 is valid JSON)
    is not evidence either: it answers False, never an OverflowError (review 2026-09-26)."""
    if isinstance(c, bool) or not isinstance(c, (int, float)):
        return False
    try:
        f = float(c)
    except OverflowError:
        return False
    return math.isfinite(f) and f >= 0


def instant(stamp):
    """An aware datetime for a ledger or window stamp, or None (D-22, 2026-09-26). The window start is aware UTC and
    model_call writes local time with no offset, so comparing the text counted hours of earlier spend. A naive stamp
    is the writer's local time, the policy abc/claude_window.py already reads it by; a local time the zone shows
    twice (clocks falling back) or never (clocks springing forward) cannot be placed once and is None."""
    try:
        t = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None
    if t.tzinfo is not None:
        return t
    try:
        a, b = t.replace(fold=0).astimezone(), t.replace(fold=1).astimezone()
    except (ValueError, OverflowError, OSError):
        return None
    if a.utcoffset() != b.utcoffset() or a.replace(tzinfo=None) != t:
        return None
    return a


def _moment(value):
    """An aware datetime from a datetime, an ISO stamp (see instant()) or epoch seconds; None when it is none of them."""
    if isinstance(value, datetime.datetime):
        return value if value.tzinfo is not None else instant(value.isoformat())
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.datetime.fromtimestamp(value, datetime.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return instant(value) if isinstance(value, str) else None


def tally(path, run_id=None, since=None, until=None, now=None, unit_run=None):
    """dict(calls, usd, uncosted, inflight, no_id, untagged, unreadable_rows, invalid_done, duplicate_starts,
    duplicate_done, orphan_done, error). A row is inside the window when its instant is at or after `since` and at or
    before `until` (either None: unbounded). With run_id, only rows tagged with that run count and an untagged row is
    NO-DATA. A START with no terminal row is inflight while its expires_at is after `now` (default: the clock), else
    uncosted. With unit_run, only rows tagged with that unit run folder count (start() writes the tag from
    BROTHER_UNIT_RUN, finish() copies it from the START): a call with no tag is never guessed into a unit, by time or
    otherwise. A bound that is not a time is NO-DATA. The identity faults are counted over the WHOLE ledger, whatever
    the window or run: a duplicate or orphan anywhere means no row can be counted exactly once (finding 5)."""
    out = dict(calls=0, usd=0.0, uncosted=0, inflight=0, no_id=0, untagged=0, unreadable_rows=0, invalid_done=0,
               duplicate_starts=0, duplicate_done=0, orphan_done=0, error=None)
    bounds = {}
    for name, value in (("window start", since), ("window end", until), ("now", now)):
        if value is not None:
            bounds[name] = _moment(value)
            if bounds[name] is None:
                out["error"] = "%s is not a time: %r" % (name, value)
                return out
    start_at, end_at = bounds.get("window start"), bounds.get("window end")
    now_at = bounds.get("now") or datetime.datetime.now(datetime.timezone.utc)
    starts, costed, closed = [], set(), set()
    try:
        raw = _read(path)
    except OSError as exc:
        out["error"] = "%s: %s" % (type(exc).__name__, path)
        return out
    rows, out["unreadable_rows"], ids_started, ids_ended = _census(raw)
    out.update(_identity_faults(ids_started, ids_ended))
    for r, at in rows:
        if (start_at is not None and at < start_at) or (end_at is not None and at > end_at):
            continue
        cid = _call_id(r)
        done = _is_terminal(r)
        if unit_run is not None and r.get("unit_run") != unit_run:
            continue   # a unit tally counts only the calls its runner registered: no window, no guess (2026-10-04)
        if run_id is not None and r.get("run") != run_id:
            if not isinstance(r.get("run"), str) or not r.get("run"):
                # AN UNTAGGED ROW IN A RUN TALLY is nobody's money for this run and cannot be ruled out of it: NO-DATA,
                # and an untagged START is a call of unknown cost (objection 13)
                out["untagged"] += 1
                if not done:
                    out["calls"] += 1; out["uncosted"] += 1
            continue
        # A ROW CARRYING A COST IS A COMPLETION, whatever its event field: known money never drops out of the budget,
        # and an unusable cost (null included) is unknown money even when its start row is missing (review 2026-09-26).
        if done:
            if cid:
                closed.add(cid)
            if valid_cost(r.get("cost_usd")):
                out["usd"] += float(r["cost_usd"])
                if cid:
                    costed.add(cid)
            else:
                out["invalid_done"] += 1
        else:
            starts.append((cid, instant(r.get("expires_at")) if isinstance(r.get("expires_at"), str) else None))
    out["calls"] += len(starts)
    if not math.isfinite(out["usd"]):
        out["usd"] = None      # each figure was finite, the total is not: no measurement, said in the note
    for cid, expires in starts:
        if cid is None:
            out["uncosted"] += 1; out["no_id"] += 1
        elif cid in costed:
            continue
        elif cid not in closed and expires is not None and expires > now_at:
            out["inflight"] += 1   # PENDING: still inside its own timeout, so its cost is not known yet, not unknown
        else:
            out["uncosted"] += 1
    return out


def note(t):
    """The words the driver prints beside its Claude figure; empty when every call in the window is costed."""
    if t.get("error"):
        return "NO-DATA: the Claude call ledger could not be read (%s)" % t["error"]
    parts = []
    if t["usd"] is None:
        parts.append("the Claude cost total is not a finite number")
    if t["invalid_done"]:
        parts.append("%d completion row(s) with no usable cost" % t["invalid_done"])
    if t["uncosted"]:
        why = " (%d with no call id, so they cannot be paired)" % t["no_id"] if t["no_id"] else ""
        parts.append("%d Claude call(s) without a known cost%s" % (t["uncosted"], why))
    if t["unreadable_rows"]:
        parts.append("%d unreadable ledger row(s)" % t["unreadable_rows"])
    if t.get("duplicate_starts"):
        parts.append("%d call id(s) on more than one START row" % t["duplicate_starts"])
    if t.get("duplicate_done"):
        parts.append("%d call id(s) on more than one terminal row" % t["duplicate_done"])
    if t.get("orphan_done"):
        parts.append("%d call id(s) with a terminal row and no START" % t["orphan_done"])
    if t.get("untagged"):
        parts.append("%d row(s) carry no run tag" % t["untagged"])
    pending = "+ %d pending" % t["inflight"] if t.get("inflight") else ""
    if parts:
        return "NO-DATA: " + "; ".join(parts + ([pending] if pending else []))
    return pending


def selftest():
    import tempfile
    d = tempfile.mkdtemp(prefix="cl-self-")
    p = os.path.join(d, "l.jsonl")
    with open(p, "w") as f:
        f.write(json.dumps({"at": "2099-01-01T00:00:00", "call": "a"}) + "\n")
        f.write(json.dumps({"at": "2099-01-01T00:00:01", "event": "done", "call": "a", "cost_usd": 0.25}) + "\n")
        f.write(json.dumps({"at": "2099-01-01T00:00:02", "call": "b"}) + "\n")
    t = tally(p, since="2000-01-01T00:00:00")
    cases = [("a paired call is costed and an unpaired one is not", (t["calls"], t["usd"], t["uncosted"]) == (2, 0.25, 1)),
             ("the note says NO-DATA for the unpaired call", note(t).startswith("NO-DATA") and "1 Claude call" in note(t)),
             ("bool and NaN are not cost evidence", not valid_cost(True) and not valid_cost(float("nan")) and valid_cost(0))]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


def main(argv):
    if "--selftest" in argv:
        return selftest()
    if len(argv) != 4 or argv[1] != "tally":
        print("usage: claude_ledger.py tally <ledger path> <since ISO>"); return 2
    t = tally(argv[2], since=argv[3])
    print("USD %s" % ("NO-DATA" if t["usd"] is None else "%.4f" % t["usd"]))
    print("NOTE %s" % note(t))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
