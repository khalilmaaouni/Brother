#!/usr/bin/env python3
"""How many unit runners the REMAINING OpenRouter money funds until the stop hour, measured from the ledger.
usage: burn_guard.py [--stop-hour 7] [--runners-now N]      burn_guard.py --selftest
Prints one line and the recommended cap; exit 0 normally, 3 when the money is already spent (cap 0).
The owner's night figure is a real ceiling, so "as many lanes as possible" means as many as the money funds, never more.
Every unknown fails toward FEWER runners: an unreadable ledger, a zero rate or a missing clock all yield the floor."""
import datetime, json, os, sys
# THE MONEY RECORD IS DURABLE, 2026-09-21. It lived under /tmp until a reboot at about 11:33 JST
# destroyed it mid night: the ledger and the grant both vanished, the guard correctly refused to fund
# anything it could not measure, and every lane stopped. A spend record is the one file that may never
# be only in a scratch directory. The root is read from the same environment variable the dispatcher
# reads, so the guard and the dispatcher can never disagree about where the money is written.
def state_root(env=None):
    """Where the money is written. A sandbox passes its own root through the environment; everything else
    gets a DURABLE home owned path. Never a scratch directory: a spend record that a reboot can delete is
    a spend record that will be lost exactly once, and then silently spent twice."""
    env = os.environ if env is None else env
    return env.get("BROTHER_OR_STATE_ROOT") or os.path.expanduser("~/.claude/brother-or-dispatch-state")

STATE_ROOT = state_root()
LEDGER = os.path.join(STATE_ROOT, "openrouter-ledger.jsonl")
GRANT = os.path.join(STATE_ROOT, "cap-grant.json")
FLOOR, CEILING = 2, 24          # never fewer than 2 lanes while money remains, never more than the plan can use

def spend(rows):
    """Total reconciled spend. This is the figure that matters, and it is read AFTER the fact, never predicted.
    A row whose cost is not a number is ignored, never guessed at."""
    total = 0.0
    for r in rows:
        if r.get("type") != "RECONCILE": continue
        c = r.get("actual_cost")
        if not isinstance(c, (int, float)) or isinstance(c, bool): continue
        total += float(c)
    return total

def abandoned_liability(rows):
    """Every ABANDONED reservation's estimate (R2, independent review of RS1, 2026-09-26): a dead
    holder's call whose true cost is unknown, which the ledger itself (core/openrouter_ledger.py
    _walk_ledger) still counts against its own daily cap. This reader must agree, or it funds lanes
    past money the ledger has already committed: measured 2026-09-26, a 10 ceiling with 9.5 of
    abandoned liability and 0 measured spend still showed a full 8 lane headroom.
    Never folded into spend() itself: that figure stays pure measured cost, unchanged, so its own
    callers and tests keep meaning exactly what they always meant. A row whose estimate is not a
    number is ignored, never guessed at, same rule as spend()'s own actual_cost."""
    reserved = {r.get("reservation_id"): r.get("estimated_cost") for r in rows if r.get("type") == "RESERVE"}
    total = 0.0
    for r in rows:
        if r.get("type") != "ABANDONED": continue
        c = reserved.get(r.get("reservation_id"))
        if not isinstance(c, (int, float)) or isinstance(c, bool): continue
        total += float(c)
    return total

def headroom(rows, ceiling):
    """Money left to fund lanes with: the ceiling minus measured spend minus every unresolved
    (abandoned) liability, both read from the SAME rows (R2). THIS is the one place the two
    figures are combined; main() calls it rather than inlining the arithmetic, so the combination
    itself is what a test exercises, not a helper beside it. A headroom that only subtracted spend
    funded lanes past money the ledger had already committed to a dead holder's unknown cost."""
    return ceiling - spend(rows) - abandoned_liability(rows)

def hours_left(now, stop_hour, tz=None):
    """Hours until the next stop_hour o'clock. Always positive; a stop hour that already passed means tomorrow."""
    dt = datetime.datetime.fromtimestamp(now, tz)
    # 24 IS MIDNIGHT (2026-09-23 20:3x): the driver exports the deadline hour plus one when the minutes are not zero, so a
    # 23:59 deadline gives 24, the range check below accepts 0 to 24, and replace(hour=24) raised: the driver saw no
    # MONEY line and refused every run ending between 23:01 and 23:59. Midnight is hour 0 of the next day.
    stop = dt.replace(hour=stop_hour % 24, minute=0, second=0, microsecond=0)
    if stop <= dt: stop += datetime.timedelta(days=1)
    return (stop - dt).total_seconds() / 3600.0

# THE COST MODEL, simplified by the owner 2026-09-21: "stop trying to calculate cost per runner and just estimate
# the night, precision is not needed in cost in advance but at the end". He is right, and the old version proved
# it: predicting cost per runner hour failed in BOTH directions within an hour, collapsing to a floor of 2 while
# the machine was idle, then leaping to a ceiling of 24 because fresh runners had reconciled nothing and looked
# free. None of that arithmetic was load bearing, because the REAL control is elsewhere: the dispatcher refuses a
# call that would take spend past the grant, so overspend is impossible whatever this function returns. So this
# now answers one question only, is there money left, and otherwise returns the night's planned width.
PLANNED_LANES = 8       # what a night is planned at; the dispatcher stops the work when the money actually runs out
RESERVE = 1.0           # leave a little headroom so the last lanes can finish their call rather than dying mid flight


def cap(headroom, planned=PLANNED_LANES, reserve=RESERVE):
    """Lanes to run: the planned width while money remains, none once it does not.
    No rate, no per runner estimate, no prediction. The exact figure is read at the END, from the ledger."""
    if not isinstance(headroom, (int, float)) or isinstance(headroom, bool):
        return 0                       # an unreadable headroom is not permission to spend
    if headroom <= reserve:
        return 0
    return max(0, int(planned))

def selftest():
    rows = [{"type": "RECONCILE", "actual_cost": 1.0, "at": 1.0},
            {"type": "RECONCILE", "actual_cost": 2.0, "at": 2.0},
            {"type": "RESERVE", "actual_cost": 9.0, "at": 3.0},
            {"type": "RECONCILE", "actual_cost": None, "at": 4.0},
            {"type": "RECONCILE", "actual_cost": True, "at": 5.0}]
    cases = [
        ("spend counts every reconciled cost", abs(spend(rows) - 3.0) < 1e-9),
        ("a reservation is not spend", abs(spend([rows[2]])) < 1e-9),
        ("a non numeric cost is skipped, never guessed", abs(spend(rows) - 3.0) < 1e-9),
        ("money left runs the planned width", cap(40.0) == PLANNED_LANES),
        ("a planned width can be set", cap(40.0, planned=3) == 3),
        ("no money left runs nothing", cap(0.0) == 0),
        ("negative headroom runs nothing", cap(-5.0) == 0),
        ("the reserve is respected", cap(0.5, reserve=1.0) == 0),
        ("just above the reserve still runs", cap(1.5, reserve=1.0) == PLANNED_LANES),
        ("an unreadable headroom is not permission to spend", cap(None) == 0),
        ("a boolean is not a headroom", cap(True) == 0),
        ("the money record is never one fixed shared scratch path",
         state_root({}) != "/tmp/brother-or-dispatch-state"),
        ("a sandbox's own root still wins",
         state_root({"BROTHER_OR_STATE_ROOT": "/sandbox/or-state"}) == "/sandbox/or-state"),
        ("the default root follows the home of whoever runs it",
         state_root({}) == os.path.expanduser("~/.claude/brother-or-dispatch-state")),
        ("stop hour 24 is midnight, never a crash", abs(hours_left(datetime.datetime(2026, 9, 23, 22, 0).timestamp(), 24) - 2.0) < 1e-9),
        ("a passed stop hour means tomorrow", 23 < hours_left(datetime.datetime(2026, 9, 21, 7, 30).timestamp(), 7) < 24),
        ("hours left before the stop hour", abs(hours_left(datetime.datetime(2026, 9, 20, 22, 0).timestamp(), 7) - 9.0) < 0.01),
    ]
    # R2 (independent review of RS1, 2026-09-26): headroom was computed from settled spend alone,
    # so a dead holder's abandoned estimate funded lanes as if that money were still free. Measured:
    # a 10 ceiling with 9.5 abandoned and 0 measured spend still showed 8 full lanes of headroom.
    liab_rows = [{"type": "RESERVE", "reservation_id": "r9", "estimated_cost": 9.5},
                 {"type": "ABANDONED", "reservation_id": "r9"}]
    cases += [
        ("an abandoned hold's estimate is real liability", abs(abandoned_liability(liab_rows) - 9.5) < 1e-9),
        ("abandoned liability is never counted as spend", abs(spend(liab_rows)) < 1e-9),
        ("a reservation with no matching abandon is not liability", abs(abandoned_liability([liab_rows[0]])) < 1e-9),
        ("an abandon with no matching reserve is not guessed at",
         abs(abandoned_liability([{"type": "ABANDONED", "reservation_id": "ghost"}])) < 1e-9),
        ("a non numeric estimate is skipped, never guessed",
         abs(abandoned_liability([{"type": "RESERVE", "reservation_id": "z", "estimated_cost": "banana"},
                                   {"type": "ABANDONED", "reservation_id": "z"}])) < 1e-9),
        ("measured spend headroom alone would still fund lanes the ledger has already committed",
         cap(10.0 - spend(liab_rows)) > 0),
        ("headroom() is the entry point main() actually calls, and it refuses to fund past what the ledger already committed",
         cap(headroom(liab_rows, 10.0)) == 0),
        ("with no liability headroom() agrees with the plain ceiling minus spend",
         abs(headroom(rows, 100.0) - (100.0 - spend(rows))) < 1e-9),
    ]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0

def main():
    if "--selftest" in sys.argv: return selftest()
    arg = lambda f, d: (type(d)(sys.argv[sys.argv.index(f) + 1]) if f in sys.argv else d)
    stop_hour = arg("--stop-hour", 7)
    planned = arg("--planned", PLANNED_LANES)
    try:
        with open(LEDGER, encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
    except (OSError, ValueError) as exc:
        print("NO-DATA: ledger unreadable (%s); no lane is funded until it can be read" % exc); print(0); return 3
    try:
        with open(GRANT, encoding="utf-8") as f: ceiling = float(json.load(f)["daily_cap"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("NO-DATA: cap grant unreadable (%s); no lane is funded until it can be read" % exc); print(0); return 3
    now = datetime.datetime.now().timestamp()
    total = spend(rows)
    liability = abandoned_liability(rows)
    head = headroom(rows, ceiling)
    hours = hours_left(now, stop_hour)
    n = cap(head, planned)
    print("MONEY   spent %.2f + %.2f unresolved (abandoned) of %.2f USD | headroom %.2f | %.1f h to %02d:00 | the exact figure is read at the end, not predicted"
          % (total, liability, ceiling, head, hours, stop_hour))
    print("LANES   %d%s" % (n, " (STOP: the night's money is spent)" if n == 0 else " (the night's planned width)"))
    print(n)
    return 3 if n == 0 else 0
if __name__ == "__main__": sys.exit(main())
