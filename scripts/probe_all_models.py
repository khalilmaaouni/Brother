#!/usr/bin/env python3
"""Call EVERY model in the registry through the real router and adapter, and check the answer.

This is deliberately NOT in the battery. It spends real money and real minutes, so it runs when
someone asks, and check_all stays free and fast.

It is also deliberately not the same test as model_router.py --available. That probe talks to each
transport directly. This one goes through model_call.call_one, so it exercises the WIRE GATE, the
empty answer rule, and the argv construction that two Codex probes died on earlier: a trusted
directory and a closed stdin.

The question is arithmetic on purpose. A prose probe cannot be graded, and "the model said
something" is not evidence that it worked. 7 plus 5 is 12 or the model failed.

usage: probe_all_models.py [--kind build] [--only name,name]
"""
import json, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))
import model_router as R
import model_call as C

ASK = "Reply with only the number and nothing else: what is 7 plus 5?"
JEV_ASK = json.dumps({"state": {"fact": "A build reported that 7 plus 5 is 12."},
                      "questions": {"correct": {"type": "noul",
                                                "instructions": "Answer true if 7 plus 5 equals 12, false otherwise."}}})


def judge(name, transport, kind, answer):
    """Did it actually answer? Never 'it returned something'."""
    if kind == "decide":
        try:
            d = json.loads(answer[answer.index("{"):]) if "{" in answer else {}
        except ValueError:
            return False, "answered but the decision was not parseable JSON"
        a = (d.get("answers") or {}).get("correct") or {}
        p = a.get("noul")
        if not isinstance(p, (int, float)):
            return False, "no probability came back"
        return (p > 0.5), "probability %.2f" % p
    return ("12" in answer), ("said 12" if "12" in answer else "wrong answer: " + answer.strip()[:50])


def main():
    def arg(f, d=None):
        return sys.argv[sys.argv.index(f) + 1] if f in sys.argv else d
    only = set((arg("--only") or "").split(",")) - {""}
    reg = R.registry()
    names = [n for n in reg if not only or n in only]
    print("%-9s %-8s %-7s %-6s %8s  %s" % ("model", "transport", "kind", "verdict", "seconds", "detail"))
    bad = []
    for name in sorted(names, key=lambda n: (reg[n]["transport"], n)):
        m = reg[name]
        kind = "decide" if "decide" in m["kinds"] else "build"
        prompt = JEV_ASK if kind == "decide" else ASK
        t0 = time.time()
        try:
            a = C.call_one(name, prompt, kind, R.PUBLIC if m["privacy"] != R.PRIVATE else R.PRIVATE,
                           timeout=240, reg=reg)
        except R.Refused as exc:
            print("%-9s %-8s %-7s %-6s %8s  REFUSED: %s" % (name, m["transport"], kind, "NO", "-", exc))
            bad.append(name)
            continue
        if not a.ok:
            print("%-9s %-8s %-7s %-6s %8.1f  %s" % (name, m["transport"], kind, "NO", a.seconds, a.detail[:60]))
            bad.append(name)
            continue
        right, why = judge(name, m["transport"], kind, a.answer)
        if not right:
            bad.append(name)
        print("%-9s %-8s %-7s %-6s %8.1f  %s" % (name, m["transport"], kind,
                                                 "yes" if right else "NO", a.seconds, why))
    print()
    if bad:
        print("%d of %d model(s) did NOT answer correctly: %s" % (len(bad), len(names), ", ".join(sorted(bad))))
        return 1
    print("all %d model(s) answered correctly through the router and the adapter" % len(names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
