#!/usr/bin/env python3
"""A producer and its consumer are exercised on REAL data, never on a fixture either side authored.

usage (repo root):
  python3 -B scripts/contract_roundtrip.py            run every registered contract
  python3 -B scripts/contract_roundtrip.py --selftest
Exit 0 when every contract round trips, 1 when one breaks, 2 when a contract cannot be exercised (NO-DATA).

WHY THIS EXISTS, and it is the most expensive failure class measured on 2026-09-21. THREE separate defects,
each invisible for a long time, each with GREEN TESTS ON BOTH SIDES, because each side tested against its OWN
idea of the contract:

  the intake gate   decide.py wrote ONE GLOBAL stamp; the gate read a PER SESSION stamp. Every founder question
                    was refused from 2026-09-15 until it was found, and the refusal told the session to render
                    another screen, which also could not satisfy it.
  bind_ledger       the ledger writes holder_id on RESERVE only and records cost as actual_cost; the reader
                    required holder_id on RECONCILE and read estimated_cost. It returned "" for EVERY real pair
                    in the estate. Its suite passed throughout, against a fixture carrying a field the real
                    writer never writes. Measured: 616 RESERVE rows carry holder_id, 567 RECONCILE rows carry
                    none.
  a log watcher     emitted every matching line where its consumer expected transitions, so one stale event
                    repeated at the owner.

A UNIT TEST ON EITHER SIDE CANNOT CATCH THIS. Only running the real writer into the real reader can. So a
contract here is a pair of callables plus a sample of REAL data, and the assertion is that what the producer
writes, the consumer reads back.

FAIL DIRECTION: a contract whose real data cannot be found is NO-DATA and exits 2, never a pass. An absent
sample is exactly how this class hides."""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LEDGER = os.path.expanduser("~/.claude/brother-or-dispatch-state/openrouter-ledger.jsonl")


def contract_ledger_roundtrip():
    """WRITE a reservation with the real writer, READ it back with the real reader, on the live ledger."""
    sys.path.insert(0, ROOT)
    try:
        from plugin.runtime.brother.core import openrouter_ledger as L
    except Exception as exc:                      # noqa: BLE001 - any import failure is NO-DATA, not a pass
        return "NO-DATA", "the ledger module could not be imported (%s)" % exc
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("l6b3", os.path.join(HERE, "jev_catalogue_l6b3.py"))
        reader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reader)
    except Exception as exc:                      # noqa: BLE001
        return "NO-DATA", "the reader could not be imported (%s)" % exc
    root = os.path.dirname(LEDGER)
    if not os.path.isfile(LEDGER):
        return "NO-DATA", "no live ledger at %s, so the real shape cannot be exercised" % LEDGER
    holder = "contract-roundtrip-probe-%d" % os.getpid()
    try:
        rid = L.reserve(root, daily_cap=10_000.0, estimated_cost=0.001, holder_id=holder)
        L.reconcile(root, rid, actual_cost=0.001)
    except Exception as exc:                      # noqa: BLE001
        return "FAIL", "the real WRITER refused a normal reservation (%s)" % exc
    text = open(LEDGER, encoding="utf-8").read()
    got = reader.bind_ledger(holder, text)
    if got != rid:
        return "FAIL", ("the real READER could not read back what the real WRITER just wrote: expected %r, got "
                        "%r. This is the bind_ledger class: both sides green, contract broken." % (rid, got))
    return "PASS", "wrote a reservation with the real writer and read it back with the real reader"


def contract_decide_stamp():
    """The intake gate's stamp: where decide.py WRITES it, the gate must READ it."""
    renderer = os.path.join(HERE, "decide.py")
    gate = os.path.expanduser("~/.claude/hooks/intake_gate.py")
    if not (os.path.isfile(renderer) and os.path.isfile(gate)):
        return "NO-DATA", "renderer or gate not found, so the contract cannot be exercised"
    r = open(renderer, encoding="utf-8").read()
    g = open(gate, encoding="utf-8").read()
    writes_per_session = "decision-screens" in r
    if not writes_per_session and "stamp_decision_screen" in r:
        # decide.py stamps through brother_paths.stamp_decision_screen, so the
        # path is the resolver's, not a literal here: ask where it lands.
        sys.path.insert(0, HERE)
        try:
            import brother_paths
            probe = dict(os.environ, CLAUDE_CODE_SESSION_ID="probe")
            probe.pop("BROTHER_DECISION_SENTINEL", None)
            writes_per_session = "decision-screens" in brother_paths.decision_sentinel_path(probe)
        except Exception as exc:                  # noqa: BLE001 - any import failure is NO-DATA, not a pass
            return "NO-DATA", "decide.py stamps through brother_paths, which could not be read (%s)" % exc
    reads_per_session = "decision-screens" in g
    if reads_per_session and not writes_per_session:
        return "FAIL", ("the gate reads ~/.claude/decision-screens/<session>.json and the renderer never writes "
                        "there, so NO screen can satisfy it. This is the intake gate class.")
    if writes_per_session and not reads_per_session:
        return "FAIL", "the renderer writes a per session stamp the gate does not read"
    return "PASS", "renderer and gate name the same stamp location"


CONTRACTS = [("openrouter ledger write to read", contract_ledger_roundtrip),
             ("decide.py stamp to intake gate", contract_decide_stamp)]


def selftest():
    cases = [
        ("every contract is a (name, callable) pair",
         all(isinstance(n, str) and callable(f) for n, f in CONTRACTS)),
        ("a contract returns one of three verdicts",
         all(f()[0] in ("PASS", "FAIL", "NO-DATA") for _, f in CONTRACTS)),
        ("a contract always explains itself", all(str(f()[1]).strip() for _, f in CONTRACTS)),
        ("there is at least one contract registered", len(CONTRACTS) >= 1),
    ]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    worst = 0
    for name, fn in CONTRACTS:
        try:
            verdict, detail = fn()
        except Exception as exc:                  # noqa: BLE001 - a crashing contract is NO-DATA, never a pass
            verdict, detail = "NO-DATA", "the contract itself raised (%s)" % exc
        print("%-8s %-34s %s" % (verdict, name, detail))
        if verdict == "FAIL":
            worst = max(worst, 1)
        elif verdict == "NO-DATA":
            worst = max(worst, 2)
    return worst


if __name__ == "__main__":
    sys.exit(main())
