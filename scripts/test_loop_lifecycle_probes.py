"""Hostile inputs fired at the brother.loop lifecycle AS EXECUTED CODE.

Written 2026-09-21 after the owner asked for the lifecycle to be probed for edge cases. It found
four real defects in code written the same hour, which is the point: a mechanism reasoned about is
not a mechanism tested, and all four survived their own selftests.

  1. a heartbeat stamped in the FUTURE read PRODUCING, and would forever, because every staleness
     test is `age > limit` and a negative age passes it (clock skew, a hand edit, a wrong zone);
  2. a loop that wrote STARTED and was SIGKILLed read PRODUCING for the full staleness window,
     which is the silent death the heartbeat exists to catch;
  3. a non numeric passes_since_landing raised ValueError out of classify() and took the only
     tool that can say the loop is sick down with it;
  4. one unparseable actual_cost killed the entire report, including the blockers section.

Each probe returns (ok, note) and a raised exception is a FINDING, never an error: a mechanism
that crashes on hostile input has failed the probe, whatever its own tests say.

Run: python3 scripts/test_loop_lifecycle_probes.py    exit 1 on any finding
"""
import datetime, io, json, os, sys, tempfile, importlib.util

def load(name, path):
    s = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m); return m

# LOAD THE VERSIONED COPY, NOT THE ONE IN THIS HOME. This used to read ~/.claude/bin, which fails outright in
# an EXPORT tree with an empty HOME (FileNotFoundError), the single most expensive failure class this estate
# records. It was also testing whatever happened to be sitting in that home folder rather than the code that
# actually ships, so a fix present here and missing there, or the reverse, would never have been caught.
_HERE = os.path.dirname(os.path.abspath(__file__))
H = load("hb", os.path.join(_HERE, "loop", "loop_heartbeat.py"))
R = load("lr", os.path.join(_HERE, "loop", "loop_report.py"))
D = tempfile.mkdtemp()
findings = []

def probe(name, fn):
    try:
        ok, note = fn()
    except Exception as exc:
        findings.append((name, "CRASH", "%s: %s" % (type(exc).__name__, exc)))
        print("PROBE %-38s CRASH   %s: %s" % (name, type(exc).__name__, exc)); return
    print("PROBE %-38s %-7s %s" % (name, "ok" if ok else "FINDING", note))
    if not ok: findings.append((name, "WRONG", note))

now = datetime.datetime(2026, 9, 21, 20, 0, 0)
fresh = now.strftime("%Y-%m-%d %H:%M:%S")

# ---- heartbeat edges
def future_stamp():
    ahead = (now + datetime.timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
    st, why = H.classify({"state": "PASS", "stamp": ahead}, ref=now)
    return st == "ALARM", "stamp 6h in the FUTURE classified %s (%s)" % (st, why[:60])

def started_then_killed():
    # age 0 is NOT the test: a loop that started one second ago is genuinely fine and no instrument
    # can know otherwise. The real question is what happens once the grace window has passed.
    old = (now - datetime.timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S")
    st, why = H.classify({"state": "STARTED", "stamp": old}, ref=now)
    fresh_st, _ = H.classify({"state": "STARTED", "stamp": fresh}, ref=now)
    return st == "ALARM" and fresh_st == "PRODUCING", (
        "STARTED 20 min ago with no pass reads %s, STARTED just now reads %s "
        "(blind window is the %d minute grace, and only a SIGKILL reaches it)"
        % (st, fresh_st, H.START_GRACE_MINUTES))

def garbage_counter():
    st, why = H.classify({"state": "PASS", "stamp": fresh, "passes_since_landing": "banana"}, ref=now)
    return st == "ALARM", "non numeric passes_since_landing classified %s" % st

def empty_stamp():
    st, _ = H.classify({"state": "PASS", "stamp": ""}, ref=now)
    return st == "ALARM", "empty stamp classified %s" % st

def rec_is_list():
    p = os.path.join(D, "list.json"); open(p, "w").write("[1,2,3]")
    st, _ = H.classify(H.read(p), ref=now)
    return st == "ALARM", "a JSON list instead of an object classified %s" % st

def truncated_write():
    p = os.path.join(D, "trunc.json"); open(p, "w").write('{"state": "PASS", "stam')
    st, _ = H.classify(H.read(p), ref=now)
    return st == "ALARM", "half written file classified %s" % st

def unwritable_dir():
    p = "/proc/nope/hb.json"
    try:
        H.write(p, "PASS", "x"); return False, "write to an unwritable path did NOT raise"
    except OSError:
        return True, "write to an unwritable path raises OSError (caller must swallow it)"

def state_unknown():
    st, _ = H.classify({"state": "WAT", "stamp": fresh}, ref=now)
    return st in ("PRODUCING", "QUIET"), "an unknown state classified %s (it is not terminal, so fresh means alive)" % st

# ---- report edges
def money_corrupt_cost():
    return None

def money_bad_number():
    rows = [{"type": "RECONCILE", "reservation_id": "a", "actual_cost": "banana"}]
    m = R.money(rows)
    return m is not None, "a non numeric actual_cost gave %s" % (m,)

def rows_empty_file():
    p = os.path.join(D, "empty.jsonl"); open(p, "w").write("")
    r, bad = R.rows(p)
    return r == [] and bad == 0, "an empty ledger returned %r (empty list is right, None would be wrong)" % (r,)

def stage_leave_without_enter():
    y = R.stage_yield([{"at": 100, "stage": "grade", "event": "leave", "sub": "A", "pid": 1, "ok": True}])
    return y["grade"]["ran"] == 1 and y["grade"]["secs"] == 0, "a leave with no enter gave ran=%d secs=%.0f" % (y["grade"]["ran"], y["grade"]["secs"])

def stage_enter_never_left():
    y = R.stage_yield([{"at": 100, "stage": "probe", "event": "enter", "sub": "A", "pid": 1}])
    return "probe" not in y or y["probe"]["ran"] == 0, "a stage still running is invisible to the report: %s" % (dict(y),)

def report_negative_hours():
    buf = io.StringIO(); R.report(since_hours=-5, plan_path=os.path.join(D, "no.json"), out=buf)
    return "NO-DATA" in buf.getvalue(), "a negative window produced %d bytes" % len(buf.getvalue())

def report_survives_corrupt_ledger():
    bad = os.path.join(D, "bad-ledger.jsonl")
    open(bad, "w").write('{"type":"RECONCILE","reservation_id":"a","actual_cost":"banana"}\n')
    old = R.LEDGER; R.LEDGER = bad
    try:
        buf = io.StringIO(); R.report(since_hours=99999, plan_path=os.path.join(D, "no.json"), out=buf)
        return True, "report survived a corrupt money row"
    finally:
        R.LEDGER = old

def report_all_missing():
    olds = (R.STAGES, R.FAILURES, R.LEDGER)
    R.STAGES = R.FAILURES = R.LEDGER = os.path.join(D, "gone.jsonl")
    try:
        buf = io.StringIO(); R.report(since_hours=24, plan_path=os.path.join(D, "no.json"), out=buf)
        t = buf.getvalue()
        return t.count("NO-DATA") >= 4, "every ledger missing gave %d NO-DATA line(s)" % t.count("NO-DATA")
    finally:
        R.STAGES, R.FAILURES, R.LEDGER = olds

for n, f in [("heartbeat: stamp in the future", future_stamp),
             ("heartbeat: STARTED then instant death", started_then_killed),
             ("heartbeat: non numeric counter", garbage_counter),
             ("heartbeat: empty stamp", empty_stamp),
             ("heartbeat: JSON list not object", rec_is_list),
             ("heartbeat: truncated mid write", truncated_write),
             ("heartbeat: unwritable path", unwritable_dir),
             ("heartbeat: unknown state word", state_unknown),
             ("report: non numeric actual_cost", money_bad_number),
             ("report: empty ledger is not missing", rows_empty_file),
             ("report: leave without enter", stage_leave_without_enter),
             ("report: stage still running", stage_enter_never_left),
             ("report: negative window", report_negative_hours),
             ("report: corrupt money row", report_survives_corrupt_ledger),
             ("report: every ledger missing", report_all_missing)]:
    probe(n, f)

print("\n%d finding(s)" % len(findings))
for n, kind, note in findings: print("  %-8s %-38s %s" % (kind, n, note))
sys.exit(1 if findings else 0)
