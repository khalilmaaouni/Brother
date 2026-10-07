#!/usr/bin/env python3
"""How many unit runners the REMAINING OpenRouter money funds until the stop hour, measured from the ledger.
usage: burn_guard.py [--stop-hour 7] [--runners-now N]      burn_guard.py --selftest
Prints one line and the recommended cap; exit 0 normally, 3 when the money is already spent (cap 0).
The owner's night figure is a real ceiling, so "as many lanes as possible" means as many as the money funds, never more.
Every unknown fails toward FEWER runners: an unreadable ledger, a zero rate or a missing clock all yield the floor."""
import datetime, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proof_ledger  # noqa: E402  the colocated strict parser: a repeated JSON member refuses (money audit finding 6)
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

def _switch_case():
    """The Claude only run (owner 2026-09-30): or_balance.read never fetches, funding() stays OK on a NOT-USED reading
    with any balance, line() names it; with the switch off the same fetch IS reached (control)."""
    import or_balance, tempfile
    root = tempfile.mkdtemp(prefix="burn-switch-")
    calls = []
    fetch = lambda: calls.append(1) or {"credits": {"total_credits": 1, "total_usage": 0.99}, "key": {"limit_remaining": 0.01}}
    saved = os.environ.get("BROTHER_TRANSPORTS"); os.environ["BROTHER_TRANSPORTS"] = "claude"
    try:
        r = or_balance.read(root, 0, fetch=fetch)
        money = {"ceiling": 50.0, "total": 1.0, "settled": 1.0, "inflight": 0.0, "abandoned": 0.0, "source": "run", "until": "x", "run": {}}
        on = (calls == [] and r.get("status") == "NOT-USED" and funding(money, r)[0] == "OK"
              and funding(money, {"status": "OK", "available": 0.01})[0] == "PROVIDER-LOW"   # the same money with a real low reading still defunds
              and "NOT USED" in or_balance.line(r) and not os.path.exists(os.path.join(root, or_balance.CACHE)))
        os.environ["BROTHER_TRANSPORTS"] = "claude,openrouter"   # a word that is not a transport: still not asked
        junk = or_balance.read(root, 0, fetch=fetch)
        on = on and calls == [] and junk.get("status") == "NOT-USED" and "could not be read" in junk.get("why", "")
        os.environ.pop("BROTHER_TRANSPORTS")
        or_balance.read(root, 0, fetch=fetch)
        off = calls == [1]
    finally:
        if saved is not None: os.environ["BROTHER_TRANSPORTS"] = saved
        else: os.environ.pop("BROTHER_TRANSPORTS", None)
    return on and off


def selftest():
    """Answer the question even when a case RAISES. Measured 2026-09-22: eleven selftests in this
    directory exited 1 with a bare traceback and no verdict, so a pipeline reading the exit code and
    a human reading the text described the same run differently. Cases are built EAGERLY, so one
    raising expression takes the whole run with it; this wrapper is what turns that into a readable
    refusal. It does not make a broken module pass: it still returns non zero."""
    try:
        return _selftest_body()
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s" % (type(exc).__name__, str(exc)[:120]))
        return 1


def _selftest_body():
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
        ("under BROTHER_TRANSPORTS=claude the OpenRouter balance is NOT READ: the fetch is never called, the reading says NOT-USED, and a low balance neither defunds a lane nor a network call leaves",
         _switch_case()),
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

def proof_funding(root, now):
    """Use the dispatch authority while reporting only this proof's measured cost."""
    keys = ('BROTHER_PROOF_PHASE', 'BROTHER_PROOF_BASELINE', 'BROTHER_PROOF_BASELINE_SHA256')
    if not any(os.environ.get(key) for key in keys):
        return None
    from pathlib import Path
    if not os.environ.get('BROTHER_OR_STATE_ROOT') or not Path(root).is_absolute():
        raise ValueError('proof funding requires an explicit absolute state root')
    # U3 (B5-04): the plugin runtime comes from the frozen code root the launcher names, never from the cwd's git
    # toplevel, which in a proof is the launch worktree the loop lands work into.
    code_root = os.environ.get('BROTHER_CODE_ROOT', '')
    if not code_root or not os.path.isdir(code_root):
        raise ValueError('proof funding requires BROTHER_CODE_ROOT naming a directory, got %r' % code_root)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    if code_root not in sys.path:
        sys.path.insert(0, code_root)
    from plugin.runtime.brother.core import openrouter_dispatch as dispatch
    from plugin.runtime.brother.core import openrouter_ledger as ledger
    import proof_ledger
    with ledger._LockedLedger(root):
        policy = proof_ledger.configuration(root)
        observed = policy['analysis']
        # A failed paid call has an unknown bill. Today's rule (the switch off, owner question Q1 open): any such
        # call funds nothing more. Switched on, one whose reservation is bound counts at its bound in the headroom
        # below (cap_view already holds every abandoned estimate); an unbounded one still funds nothing.
        runs = observed['runs'].values()
        if proof_ledger.BOUNDED_ABANDON_COUNTS:
            unknown_terminal = sum(r['abandoned_unbounded'] for r in runs)
        else:
            unknown_terminal = sum(len(r['unknown_reservation_ids']) - len(r['inflight_reservation_ids']) for r in runs)
        if unknown_terminal:
            raise ValueError('new terminal cost is unknown')
        limits = dispatch.effective_limits(root, datetime.datetime.fromtimestamp(now, datetime.timezone.utc))
        funding = proof_ledger.cap_view(root, now)
        own = observed['runs'].get(policy['run_id'], {})
        return dict(ceiling=limits.daily_cap_usd, headroom=limits.daily_cap_usd - funding['total'],
                    measured_spend_usd=own.get('measured_spend_usd', 0),
                    liability_usd=funding['inflight'] + funding['abandoned'])


def _finite_amount(value):
    """A money amount must be a finite, nonnegative number. Anything else is corrupt data,
    never a guess: NaN and infinities would otherwise poison every sum and fund lanes.
    F3 (D-22): an int past the float range (10**400 is valid JSON) made math.isfinite raise
    OverflowError, a traceback instead of this refusal. Every file amount enters here, so here it stops."""
    try:
        ok = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0
    except OverflowError:
        ok = False
    if not ok:
        raise ValueError('invalid finite nonnegative amount')
    return float(value)


def _validate_legacy_rows(rows):
    """Strict validation for the non-proof ledger. Reuses the proof_ledger.amount rule
    (finite, nonnegative, not bool) but not proof_ledger.parse, which requires a
    reservation_id and at on every row and would refuse the legacy rows this path owns.
    An ABANDONED row is an unknown terminal cost: the task requires NO-DATA and 0 lanes,
    never a funded plan."""
    reservations = {}
    terminals = set()
    for r in rows:
        if not isinstance(r, dict):
            raise ValueError('ledger entry must be an object')
        kind = r.get('type')
        if kind == 'RESERVE':
            rid = r.get('reservation_id')
            if not isinstance(rid, str) or not rid:
                raise ValueError('invalid reservation identity')
            if rid in reservations:
                raise ValueError('duplicate reservation identity')
            reservations[rid] = _finite_amount(r.get('estimated_cost'))
        elif kind == 'RECONCILE':
            _finite_amount(r.get('actual_cost'))
            rid = r.get('reservation_id')
            if isinstance(rid, str) and rid:
                terminals.add(rid)
        elif kind == 'RELEASE':
            rid = r.get('reservation_id')
            if isinstance(rid, str) and rid:
                terminals.add(rid)
        elif kind == 'ABANDONED':
            # AN UNKNOWN BILL IS LIABILITY, NOT CORRUPTION (2026-09-27 15:16): this line used to raise, so ONE timed out
            # call anywhere in the file's history funded 0 lanes for every later read, a one way latch that stopped a
            # 100 USD run at 0.23. The reservation stays open in the sum below, so its estimate is counted exactly once.
            rid = r.get('reservation_id')
            if not isinstance(rid, str) or rid not in reservations:
                raise ValueError('abandon of an unknown reservation')
        elif kind == 'DISPATCH_START':
            pass
        else:
            raise ValueError('unknown ledger event')
    return reservations, terminals


def _legacy_money(rows):
    """Measured spend plus every open reservation's estimate. Both figures are read from
    the same validated rows: an open reservation is money the ledger has already committed,
    exactly like the proof path's inflight liability."""
    reservations, terminals = _validate_legacy_rows(rows)
    total = 0.0
    for r in rows:
        if r.get('type') == 'RECONCILE':
            total += _finite_amount(r.get('actual_cost'))
    liability = sum(cost for rid, cost in reservations.items() if rid not in terminals)
    return total, liability


def _read_grant(path):
    """A grant with a non-finite cap or a non-timezone-aware expiry never lifts the cap.
    The dispatcher compares aware datetimes; this reader must agree rather than crashing
    or silently accepting an expired night."""
    with open(path, encoding="utf-8") as f:
        grant = proof_ledger.loads(f.read())
    if not isinstance(grant, dict):
        raise ValueError('cap grant must be an object')
    ceiling = _finite_amount(grant.get("daily_cap"))
    until = grant.get("until")
    if until is not None:
        try:
            expiry = datetime.datetime.fromisoformat(str(until))
        except (TypeError, ValueError) as exc:
            raise ValueError('invalid grant until') from exc
        if expiry.tzinfo is None or expiry < datetime.datetime.now().astimezone():
            ceiling = _configured_cap(os.path.dirname(path))
            print("GRANT   expired at %s: the dispatcher's configured cap %.2f applies" % (until, ceiling))
    return ceiling


DEFAULT_DAILY_CAP = 20.0   # openrouter_dispatch.DEFAULT_DAILY_CAP: the cap when no deployment limit is configured


def _configured_cap(root):
    """The baseline an expired grant returns to, read the way openrouter_dispatch.effective_limits reads it:
    dispatch-limits.json's daily_cap, or the 20 default when the file or the key is absent (D-22). A hardcoded 20
    lifted a configured LOWER limit. Stricter than the dispatcher on purpose: a present but corrupt limit
    refuses here where it falls back there, because an unknown baseline never reads as the higher default."""
    try:
        with open(os.path.join(root, "dispatch-limits.json"), encoding="utf-8") as f:
            data = proof_ledger.loads(f.read())
        if not isinstance(data, dict):
            raise ValueError("not an object")
        return _finite_amount(data["daily_cap"]) if "daily_cap" in data else DEFAULT_DAILY_CAP
    except FileNotFoundError:
        return DEFAULT_DAILY_CAP
    except (OSError, ValueError) as exc:
        raise ValueError("deployment limits unreadable (%s)" % exc) from exc


RUN_BUDGET_FILE = "run-budget.json"   # openrouter_ledger.RUN_BUDGET_FILENAME: a run's own money root


def _core():
    """openrouter_ledger and openrouter_dispatch from the tree the loop's code runs from (model_router.code_root), else
    the frozen copy beside this file, else the checkout this file sits in: the SAME modules the dispatcher admits with."""
    roots = []
    try:
        import model_router
        roots.append(model_router.code_root())
    except Exception:   # sbe: allow-silent the next candidate roots are tried; none found raises ImportError below
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    roots += [os.path.join(here, "candidate"), os.path.dirname(os.path.dirname(here))]
    for r in roots:
        if r and os.path.isfile(os.path.join(r, "plugin", "runtime", "brother", "core", "openrouter_ledger.py")):
            if r not in sys.path:
                sys.path.insert(0, r)
            from plugin.runtime.brother.core import openrouter_ledger, openrouter_dispatch
            return openrouter_ledger, openrouter_dispatch
    raise ImportError("no code root holds plugin/runtime/brother/core")


def run_money(root, now, core=None):
    """A run's money, read with the dispatcher's own walk and cap (one reader, never a second opinion): the whole run's
    settled, in flight and abandoned amounts, and the cap in force (the run budget until its deadline, then 0)."""
    L, D = core or _core()
    view = L.spend_breakdown(root, now)
    limits = D.effective_limits(root, datetime.datetime.fromtimestamp(now, datetime.timezone.utc))
    return dict(settled=view["settled"], inflight=view["inflight"], abandoned=view["abandoned"], total=view["total"],
                ceiling=limits.daily_cap_usd, source=limits.source, until=limits.grant_until, run=L.run_budget(root))


def funding(money, balance, reserve=RESERVE):
    """(status, headroom, reason): OK, SPENT, EXPIRED, PROVIDER-LOW or NO-DATA. Each names its own cause, so a reader
    never has to infer "the money is spent" from a zero. An unreadable provider balance is not a refusal: the run's own
    ledger still bounds every call (the dispatcher refuses past it); a provider that says it cannot pay is one."""
    head = money["ceiling"] - money["total"]
    if money["source"] == "run-unreadable":
        return "NO-DATA", head, "the run's budget record cannot be read"
    if money["source"] == "run-expired":
        return "EXPIRED", head, "the run's budget ended at its deadline %s" % money["until"]
    if head <= reserve:
        return "SPENT", head, "%.2f of %.2f USD committed (%.2f measured, %.2f in flight, %.2f abandoned at estimate)" % (
            money["total"], money["ceiling"], money["settled"], money["inflight"], money["abandoned"])
    if balance.get("status") == "OK" and balance["available"] <= reserve:
        return "PROVIDER-LOW", head, "OpenRouter has %.2f USD available, at or below the %.2f USD reserve" % (balance["available"], reserve)
    return "OK", head, "%.2f USD left of the run's %.2f" % (head, money["ceiling"])


def run_money_main(root, stop_hour, planned):
    now = datetime.datetime.now().timestamp()
    try:
        money = run_money(root, now)
    except Exception as exc:   # sbe: allow-silent any failure is printed as NO-DATA with its reason and funds nothing
        print("FUNDING NO-DATA the run's money cannot be read (%s: %s)" % (type(exc).__name__, str(exc)[:160]))
        print("NO-DATA: ledger unreadable (%s); no lane is funded until it can be read" % str(exc)[:160]); print(0); return 3
    import or_balance
    balance = or_balance.read(root, now)
    status, head, why = funding(money, balance)
    n = cap(head, planned) if status == "OK" else 0
    run = money["run"] or {}
    print("MONEY   spent %.2f + %.2f unresolved (%.2f in flight, %.2f abandoned at estimate) of %.2f USD | headroom %.2f | %.1f h to %02d:00 | run %s until %s"
          % (money["settled"], money["inflight"] + money["abandoned"], money["inflight"], money["abandoned"], money["ceiling"], head,
             hours_left(now, stop_hour), stop_hour, run.get("run_id", "?"), money["until"]))
    print(or_balance.line(balance))
    print("FUNDING %s %s" % (status, why))
    print("LANES   %d" % n)
    print(n)
    return 3 if n == 0 else 0


def _cli(argv):
    """The ONE place command line numbers enter (F4, D-22). The old lambda cast sys.argv in place, outside every
    refusal, so `--planned foo` or a bare `--stop-hour` was a traceback and `--planned 10**400` funded a 401
    digit lane count. Each flag at most once, with a whole number value in its range, or ValueError naming it."""
    got = {}
    for flag, top in (("--stop-hour", 24), ("--planned", CEILING)):
        if argv.count(flag) > 1:
            raise ValueError("%s given more than once" % flag)
        if flag not in argv:
            continue
        i = argv.index(flag) + 1
        if i >= len(argv):
            raise ValueError("%s needs a value" % flag)
        v = argv[i]
        try:
            n = int(v)
        except ValueError:
            n = None
        if n is None or not 0 <= n <= top:
            raise ValueError("%s %r is not a whole number from 0 to %d" % (flag, v if len(v) <= 32 else v[:32] + "...", top))
        got[flag] = n
    return got.get("--stop-hour"), got.get("--planned", PLANNED_LANES)


def main():
    if "--selftest" in sys.argv: return selftest()
    try:
        return _main(sys.argv[1:])
    except Exception as exc:
        # THE LAST REFUSAL (F3/F4, D-22): anything no handler below names still funds nothing. The driver reads the
        # LAST line as the lane count, and a traceback's last line is not one.
        print("FUNDING NO-DATA see the next line")
        print("NO-DATA: the burn guard could not finish (%s: %s); no lane is funded" % (type(exc).__name__, str(exc)[:200]))
        print(0)
        return 3


def _main(argv):
    try:
        cli_hour, planned = _cli(argv)
    except ValueError as exc:
        print("FUNDING NO-DATA see the next line")
        print("NO-DATA: bad command line (%s); no lane is funded until it is corrected" % exc); print(0); return 3
    # THE STOP HOUR IS THE RUN'S DEADLINE, not the night's 07:00 (owner 2026-09-22: "use more lanes if optimum"). The
    # driver exports BROTHER_STOP_HOUR from the intake deadline; a one hour run then funds every lane the money allows
    # instead of spreading the money to next morning. An unreadable value keeps the night default (fewer lanes).
    try:
        env_hour = int(os.environ.get("BROTHER_STOP_HOUR", ""))
    except ValueError:
        env_hour = 7
    stop_hour = cli_hour if cli_hour is not None else (env_hour if 0 <= env_hour <= 24 else 7)
    try:
        root = state_root()
        funding = proof_funding(root, datetime.datetime.now().timestamp())
    except (OSError, ValueError, TypeError, KeyError, ImportError, OverflowError) as exc:   # proof_ledger.amount: F3
        print("FUNDING NO-DATA see the next line")
        print('NO-DATA: proof funding unreadable (%s)' % exc)
        print(0)
        return 3
    if funding is not None:
        n = cap(funding['headroom'], planned)
        print('MONEY   spent %.8f + %.8f reserved of %.8f USD | headroom %.8f'
              % (funding['measured_spend_usd'], funding['liability_usd'],
                 funding['ceiling'], funding['headroom']))
        print('FUNDING %s' % ("OK proof funding" if n else "SPENT the proof headroom %.8f funds no lane" % funding['headroom']))
        print('LANES   %d' % n)
        print(n)
        return 3 if n == 0 else 0
    if os.path.lexists(os.path.join(root, RUN_BUDGET_FILE)):
        return run_money_main(root, stop_hour, planned)
    ledger_path = os.path.join(root, "openrouter-ledger.jsonl")
    grant_path = os.path.join(root, "cap-grant.json")
    try:
        with open(ledger_path, encoding="utf-8") as f:
            rows = [proof_ledger.loads(l) for l in f if l.strip()]
        total, liability = _legacy_money(rows)
    except (OSError, ValueError, TypeError) as exc:
        print("FUNDING NO-DATA see the next line")
        print("NO-DATA: ledger unreadable (%s); no lane is funded until it can be read" % exc); print(0); return 3
    try:
        ceiling = _read_grant(grant_path)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("FUNDING NO-DATA see the next line")
        print("NO-DATA: cap grant unreadable (%s); no lane is funded until it can be read" % exc); print(0); return 3
    now = datetime.datetime.now().timestamp()
    head = ceiling - total - liability
    hours = hours_left(now, stop_hour)
    n = cap(head, planned)
    print("MONEY   spent %.2f + %.2f unresolved (abandoned) of %.2f USD | headroom %.2f | %.1f h to %02d:00 | the exact figure is read at the end, not predicted"
          % (total, liability, ceiling, head, hours, stop_hour))
    print("FUNDING %s" % ("OK shared root" if n else "SPENT headroom %.2f funds no lane" % head))
    print("LANES   %d%s" % (n, " (STOP: the night's money is spent)" if n == 0 else " (the night's planned width)"))
    print(n)
    return 3 if n == 0 else 0
if __name__ == "__main__": sys.exit(main())
