#!/usr/bin/env python3
"""Was a fan out round refused for money, or by a draining proof run, rather than failed on merit?
usage as a library:  fanout_verdict.all_refused_by_budget(results_json_path) -> True | False | None
                     fanout_verdict.all_refused_by_drain(results_json_path) -> True | False | None
usage as a check:    python3 -B fanout_verdict.py --selftest
Measured 2026-09-22 18:49 to 18:59: the dispatcher's daily cap was spent, every worker call returned BudgetExceeded, five
rounds "ran" in seconds, ten sub units were marked EXHAUSTED and the pool parked them until a human fact. A refused round is
not a failed round: the runner ends UNFUNDED instead, which the pool re-seats once money is back. Unknown, unreadable or
partial results are None (never "refused"), so a real failure is never excused as a money outage."""
import json, sys

REFUSAL = "BudgetExceeded"
DRAIN = "DrainRefused"
CONFIG = "CONFIG_WAIT"   # model_call.CONFIG_WAIT: the program does not know the model
RUN_REFUSALS = (REFUSAL, DRAIN, "Stopped")   # the run refused the call before sending it (worker_mix.RUN_REFUSALS)   # proof_ledger.DrainRefused: the run is ending or the call would outlive its deadline


def _all_refused(path, word):
    try:
        with open(path, encoding="utf-8") as f: rows = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(rows, list) or not rows: return None
    for r in rows:
        if not isinstance(r, dict): return None
        if r.get("ok") is True: return False
        if word not in str(r.get("error") or ""): return False
    return True


def all_refused_by_budget(path):
    """True when every record in the fan out results failed with the dispatcher's budget refusal; False when at least one
    record succeeded or failed for another reason; None when the file is missing, unreadable, empty or not a list."""
    return _all_refused(path, REFUSAL)


def all_refused_by_drain(path):
    """True when every record was refused at registration because the proof run is draining (objection 10): nothing
    was registered or spent, so the runner answers DRAINED without consuming a round. False and None as above."""
    return _all_refused(path, DRAIN)


def all_config_wait(path):
    """True when every record in the fan out results is a CONFIG_WAIT: the program or endpoint does not know the model
    (or the configuration breaker already holds it), so nothing was built and nothing was learned about the brief. The
    runner ends CONFIG_WAIT, spending no round, and the pool re-seats it once the resolved program changes. A record is
    CONFIG by its config flag or by its error's first word; False and None as above."""
    try:
        with open(path, encoding="utf-8") as f: rows = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(rows, list) or not rows: return None
    config = False
    for r in rows:
        if not isinstance(r, dict): return None
        if r.get("ok") is True: return False
        if r.get("config") is True or str(r.get("error") or "").startswith(CONFIG):
            config = True
        elif not any(w in str(r.get("error") or "") for w in RUN_REFUSALS):
            return False   # a real failure beside the CONFIG ones: the round is graded, never excused
    return config   # CONFIG mixed with the run's own refusals (drain, budget, stop) spends nothing either (attack R1 F7)


def all_claude_errors(path):
    """True when every record is a native Claude session that ended in a Claude error result (native_worker sets
    claude_error): a usage limit or an outage, so nothing was built and nothing was learned about the brief. The runner
    then ends UNFUNDED, spending no attempt, and the pool re-seats it after its cooldown (2026-10-02: a Claude plan user
    WILL hit a limit, and burning rounds on it parked sub units). False when any record is anything else; None when the
    file is missing, unreadable, empty or not a list."""
    try:
        with open(path, encoding="utf-8") as f: rows = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(rows, list) or not rows: return None
    for r in rows:
        if not isinstance(r, dict): return None
        if r.get("ok") is True or r.get("claude_error") is not True: return False
    return True


def all_unseated(path):
    """True when no native session ran in the round: every record waited for a seat in vain (seat_wait) or ended in a
    Claude error, and at least one waited (all Claude errors is all_claude_errors). Review 2026-10-02, reproduced: a
    runner that lost the seat race was graded NO-PASS with no session run, its attempt spent and its unit parked. The
    runner re-seats such a round; False when any session ran; None for a missing, unreadable, empty or non list file."""
    try:
        with open(path, encoding="utf-8") as f: rows = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(rows, list) or not rows: return None
    waited = False
    for r in rows:
        if not isinstance(r, dict): return None
        if r.get("ok") is True: return False
        if r.get("seat_wait") is True: waited = True
        elif r.get("claude_error") is not True: return False
    return waited


def selftest():
    import os, tempfile
    d = tempfile.mkdtemp(prefix="fanout-verdict-")
    def w(name, obj):
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as f:
            if isinstance(obj, str): f.write(obj)
            else: json.dump(obj, f)
        return p
    refused = [{"id": "a", "ok": False, "error": "BudgetExceeded: reserving $0.02 would bring spend over the cap"}] * 3
    mixed = refused + [{"id": "b", "ok": False, "error": "TimeoutExpired"}]
    one_ok = refused + [{"id": "c", "ok": True, "error": None}]
    cases = [("every record refused for budget is True", all_refused_by_budget(w("r.json", refused)) is True),
             ("one failure for another reason is False", all_refused_by_budget(w("m.json", mixed)) is False),
             ("one success is False", all_refused_by_budget(w("o.json", one_ok)) is False),
             ("a missing file is None", all_refused_by_budget(os.path.join(d, "none.json")) is None),
             ("unreadable, empty or non list results are None", all_refused_by_budget(w("j.json", "not json")) is None and all_refused_by_budget(w("e.json", [])) is None and all_refused_by_budget(w("d.json", {"a": 1})) is None),
             ("a non dict record is None", all_refused_by_budget(w("x.json", ["x"])) is None)]
    # M1-7 (objection 10): a round refused because the run is draining is neither a failure nor a money outage
    drained = [{"id": "a", "ok": False, "error": "DrainRefused: a call of 300 s would outlive the deadline"}] * 2
    cases += [("every record refused by the drain is True", all_refused_by_drain(w("dr.json", drained)) is True),
              ("a drain refusal mixed with a timeout is False",
               all_refused_by_drain(w("dm.json", drained + [{"id": "t", "ok": False, "error": "TimeoutExpired"}])) is False),
              ("a missing file is None for the drain too", all_refused_by_drain(os.path.join(d, "none.json")) is None),
              ("a budget refusal is not a drain refusal", all_refused_by_drain(w("rb.json", refused)) is False),
              ("a drain refusal is not a budget refusal", all_refused_by_budget(w("db.json", drained)) is False)]
    cfg = [{"id": "a", "ok": False, "config": True, "error": "CONFIG_WAIT: the program does not know this model"}] * 2
    cases += [("every record CONFIG_WAIT is True", all_config_wait(w("c.json", cfg)) is True),
              ("CONFIG_WAIT mixed with a model failure is False (a real loss is never excused)",
               all_config_wait(w("cm.json", cfg + [{"id": "t", "ok": False, "error": "exit 1: boom"}])) is False),
              ("CONFIG_WAIT with one build is False: the build is graded", all_config_wait(w("co.json", cfg + [{"id": "o", "ok": True}])) is False),
              ("a missing file is None for CONFIG too", all_config_wait(os.path.join(d, "none.json")) is None),
              ("CONFIG mixed with drain and budget refusals is CONFIG_WAIT", all_config_wait(w("cr.json", cfg + drained + refused[:1])) is True),
              ("drain and budget refusals alone are not CONFIG_WAIT", all_config_wait(w("dr2.json", drained + refused[:1])) is False)]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad))); return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else 2)
