#!/usr/bin/env python3
"""Execute a council seat's own proof commands and keep only the blockers that survive.
usage (repo root): python3 scripts/council_verify.py <unit ...> [--json out.json]      council_verify.py --selftest
WHY. Measured 2026-09-20: three seats raised 466 blockers across 44 units and not one carried a proof, so every
blocker cost a human or an agent reading code to triage, and most were never triaged at all. A seat that must ship
`proof` (a plain grep) and `expect` (the text it must print) turns that pile into a list execution can settle: the
model proposes, the command decides, and the orchestrator reads only what survived.
A blocker with no proof is UNPROVEN, never dropped and never trusted: it stays for a human, it just stops blocking.
Only a plain grep or git grep inside the repository is ever run, reusing diag_apply's own screen, so a seat cannot
smuggle a shell command into the verifier."""
import json, os, re, shlex, subprocess, sys

def allowed(prove):
    """argv when a model-proposed proof command may run, else None. Only a plain grep or git grep, no shell
    characters, no path outside the repository, no option that reads or writes another file. The model proposes
    the command; this decides whether it is safe to run at all."""
    if not isinstance(prove, str) or re.search(r"[|;&<>`$\n]", prove): return None
    try: argv = shlex.split(prove)
    except ValueError: return None
    if not (argv[:1] == ["grep"] or argv[:2] == ["git", "grep"]): return None
    if any(x.startswith(("/", "~")) or ".." in x.split("/") for x in argv[1:] if not x.startswith("-")): return None
    if any(x in ("-f", "--file", "--exclude-from", "-O", "--open-files-in-pager") or
           x.startswith(("--file=", "--exclude-from=", "-O", "--open-files-in-pager")) for x in argv): return None
    return argv

def classify(b, run):
    """('PROVEN'|'REFUTED'|'UNPROVEN'|'REFUSED', detail) for one blocker dict. Unknown shapes are UNPROVEN, never PROVEN."""
    if not isinstance(b, dict): return "UNPROVEN", "not an object"
    proof, expect = b.get("proof"), b.get("expect")
    if not isinstance(proof, str) or not proof.strip(): return "UNPROVEN", "no proof command"
    if not isinstance(expect, str) or not expect.strip(): return "UNPROVEN", "no expected text"
    argv = allowed(proof)
    if argv is None: return "REFUSED", "proof is not a plain grep inside the repo"
    out = run(argv)
    if out is None: return "UNPROVEN", "proof command did not run"
    return ("PROVEN", "expected text found") if expect in out else ("REFUTED", "expected text absent")

def summarise(rows):
    """counts by verdict, and the proven ones first, which is the only list worth a human's attention."""
    by = {}
    for r in rows: by.setdefault(r["verdict"], []).append(r)
    order = ("PROVEN", "REFUTED", "UNPROVEN", "REFUSED")
    return [(k, by.get(k, [])) for k in order]


def jev_batch(unproven, decide, cascade=None, limit=40):
    """ONE typed call over every UNPROVEN blocker, as a shadow second opinion on a verdict already reached.
    All four Jev tests hold: council_verify is the deterministic first pass and keeps authority; the judgement
    recurs per unit and per attack; being wrong only changes which blocker a human reads first; and batching the
    whole residue into one call makes the ask cheap. noul only, the one type the estate's capability profile allows.
    Returns {qid: probability}. Anything unreadable yields {}, so a failed call silently changes no verdict."""
    rows = unproven[:limit]
    if not rows: return {}
    qs = {}
    for i, r in enumerate(rows):
        qs["b%d" % i] = {"type": "noul", "instructions":
                         "This is a real defect in unit %s that should hold work until it is fixed: %s"
                         % (r.get("unit", "?"), (r.get("text") or "")[:400])}
    try:
        ans = decide("Blockers raised by an adversarial spec review whose own proof command did not settle them. "
                     "A deterministic verifier already ran every proof it could; these are the residue.",
                     qs, "council-blocker-residue")
    except Exception:  # a shadow opinion must never be able to fail the verifier  # sbe: allow-silent shadow only
        return {}
    if not isinstance(ans, dict): return {}
    out = {}
    for i, r in enumerate(rows):
        a = ans.get("b%d" % i)
        p = a.get("probability") if isinstance(a, dict) else None
        if isinstance(p, (int, float)) and not isinstance(p, bool): out[i] = float(p)
    return out


def _captured_questions():
    seen = {}
    jev_batch([{"unit": "D1", "text": "a"}], lambda s, q, f: seen.update(q) or {})
    return list(seen.values())

def selftest():
    runs = {("grep", "-n", "foo", "a.py"): "a.py:1:foo bar", ("git", "grep", "-n", "zzz"): ""}
    run = lambda argv: runs.get(tuple(argv))
    cases = [
        ("jev shadows only the unproven residue", jev_batch([{"unit": "D1", "text": "x"}], lambda s, q, f: {"b0": {"probability": 0.95}}) == {0: 0.95}),
        ("an empty residue asks nothing", jev_batch([], lambda s, q, f: 1 / 0) == {}),
        ("a failed jev call changes no verdict", jev_batch([{"unit": "D1", "text": "x"}], lambda s, q, f: (_ for _ in ()).throw(OSError())) == {}),
        ("a non numeric probability is discarded", jev_batch([{"unit": "D1", "text": "x"}], lambda s, q, f: {"b0": {"probability": "high"}}) == {}),
        ("the batch is one call for many blockers", jev_batch([{"unit": "D1", "text": "a"}, {"unit": "D2", "text": "b"}], lambda s, q, f: {k: {"probability": 0.5} for k in q}) == {0: 0.5, 1: 0.5}),
        ("every question is noul, the one allowed type", all(v["type"] == "noul" for v in _captured_questions()) ),
        ("the screen allows a plain grep", allowed("grep -n foo scripts/a.py") is not None),
        ("the screen refuses a pipe", allowed("grep foo a.py | sh") is None),
        ("the screen refuses another program", allowed("python3 -c 'print(1)'") is None),
        ("the screen refuses an absolute path", allowed("grep -R foo /etc") is None),
        ("the screen refuses a parent escape", allowed("grep foo a/../../b") is None),
        ("the screen refuses a pattern file", allowed("grep -f /etc/passwd a.py") is None),
        ("a proof whose text is found is PROVEN", classify({"proof": "grep -n foo a.py", "expect": "foo bar"}, run)[0] == "PROVEN"),
        ("a proof whose text is absent is REFUTED", classify({"proof": "grep -n foo a.py", "expect": "not here"}, run)[0] == "REFUTED"),
        ("an empty result refutes rather than proves", classify({"proof": "git grep -n zzz", "expect": "x"}, run)[0] == "REFUTED"),
        ("no proof is UNPROVEN, never dropped", classify({"claim": "something"}, run)[0] == "UNPROVEN"),
        ("no expectation is UNPROVEN", classify({"proof": "grep -n foo a.py"}, run)[0] == "UNPROVEN"),
        ("a blank proof is UNPROVEN", classify({"proof": "   ", "expect": "x"}, run)[0] == "UNPROVEN"),
        ("a shell command is REFUSED, not run", classify({"proof": "grep foo a.py | sh", "expect": "x"}, run)[0] == "REFUSED"),
        ("another program is REFUSED", classify({"proof": "python3 -c 'print(1)'", "expect": "1"}, run)[0] == "REFUSED"),
        ("a path outside the repo is REFUSED", classify({"proof": "grep -R foo /etc", "expect": "x"}, run)[0] == "REFUSED"),
        ("a command that cannot run is UNPROVEN", classify({"proof": "grep -n other z.py", "expect": "x"}, run)[0] == "UNPROVEN"),
        ("a plain string blocker is UNPROVEN", classify("just prose", run)[0] == "UNPROVEN"),
        ("proven come first in the summary", summarise([{"verdict": "UNPROVEN"}, {"verdict": "PROVEN"}])[0][0] == "PROVEN"),
        ("every verdict bucket is present", [k for k, _ in summarise([])] == ["PROVEN", "REFUTED", "UNPROVEN", "REFUSED"]),
    ]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0

def main():
    if "--selftest" in sys.argv: return selftest()
    units = [a for i, a in enumerate(sys.argv[1:]) if not a.startswith("--") and sys.argv[i] != "--json"]
    out = sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else None
    try:
        with open(os.path.expanduser("~/.claude/evidence/spec-council.json"), encoding="utf-8") as f: council = json.load(f)
    except (OSError, ValueError) as exc:
        print("NO-DATA: council file unreadable (%s)" % exc); return 2
    def run(argv):
        try: return subprocess.run(argv, capture_output=True, text=True, timeout=15).stdout
        except (OSError, subprocess.SubprocessError): return None
    rows, all_rows = {}, []
    for u in (units or sorted(council)):
        bs = (council.get(u) or {}).get("blockers") or []
        rs = []
        for b in bs:
            v, why = classify(b, run)
            rs.append({"unit": u, "verdict": v, "why": why,
                       "text": (b.get("claim") or b.get("blocker") or "")[:160] if isinstance(b, dict) else str(b)[:160]})
        rows[u] = rs; all_rows += rs
        c = {k: len(v) for k, v in summarise(rs) if v}
        print("%-6s %d blocker(s): %s" % (u, len(rs), c or "none"))
    print("\nTOTAL %d blocker(s)" % len(all_rows))
    for k, v in summarise(all_rows):
        if v: print("  %-9s %d" % (k, len(v)))
    proven = [r for r in all_rows if r["verdict"] == "PROVEN"]
    if proven:
        print("\nPROVEN, the only ones that should hold work:")
        for r in proven[:20]: print("  %-6s %s" % (r["unit"], r["text"][:120]))
    if out:
        with open(out, "w", encoding="utf-8") as f: json.dump(rows, f, indent=1)
        print("wrote " + out)
    return 0
if __name__ == "__main__": sys.exit(main())
