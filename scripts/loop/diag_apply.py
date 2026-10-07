#!/usr/bin/env python3
"""Execute each diagnostician's proving command and, ONLY when the proof holds, write the fact into the unit's note.
usage (repo root): diag_apply.py <diagnose dir>
The model proposes, execution decides. A proving command is run ONLY if it is a plain grep or git grep: no shell, no pipes,
no redirection, 10 second limit. Anything else is REFUSED (never run). UNKNOWN, unparsable or unproven answers write nothing. Nothing here ever starts a runner (plan E step 2b)."""
import glob, json, os, re, shlex, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # THIS file's own directory: the copies installed beside it
from runner_pool import admissible


from diag_notes import write_note  # noqa: E402  the note format lives in one module


def finished(plan, sub):
    """True when the plan already records this sub unit as landed, or its unit as DONE; an unreadable plan reads as
    finished (nothing is restarted on a plan nobody can read)."""
    return bool(admissible(plan, sub))


def allowed(prove):
    """argv when the proving command may run, else None. Only a plain grep or git grep, no shell characters, no path outside the repo."""
    if not isinstance(prove, str) or re.search(r"[|;&<>`$\n]", prove): return None
    try: argv = shlex.split(prove)
    except ValueError: return None
    if not (argv[:1] == ["grep"] or argv[:2] == ["git", "grep"]): return None
    if any(x.startswith(("/", "~")) or ".." in x.split("/") for x in argv[1:] if not x.startswith("-")): return None
    if any(x in ("-f", "--file", "--exclude-from", "-O", "--open-files-in-pager") or x.startswith(("--file=", "--exclude-from=", "-O", "--open-files-in-pager")) for x in argv): return None
    return argv
def selftest():
    """Answer the question even when a case RAISES. Measured 2026-09-22: this selftest lived inline
    under __main__ and built its cases EAGERLY, so one raising expression exited 1 with a bare
    traceback and no verdict, and a pipeline reading the exit code saw an ordinary failure while a
    human saw a stack trace where the verdict belongs. Moving it into a function is what makes the
    wrapper possible at all. It does not make a broken module pass: it still returns non zero."""
    try:
        return _selftest_body()
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s" % (type(exc).__name__, str(exc)[:120]))
        return 1


def _selftest_body():
        cases = [("plain grep runs", allowed("grep -n foo scripts/a.py") is not None), ("git grep runs", allowed("git grep -n foo -- scripts") is not None),
                 ("pipe refused", allowed("grep foo a.py | sh") is None), ("semicolon refused", allowed("grep foo a.py; rm -rf x") is None),
                 ("substitution refused", allowed("grep $(id) a.py") is None), ("other program refused", allowed("python3 -c 'print(1)'") is None),
                 ("absolute path refused", allowed("grep -R foo /etc") is None), ("home path refused", allowed("grep foo ~/.ssh/id") is None),
                 ("parent escape refused", allowed("grep foo a/../../b") is None), ("pattern file refused", allowed("grep -f /etc/passwd a.py") is None),
                 ("pager escape refused", allowed("git grep -Osh foo") is None), ("non str refused", allowed(None) is None), ("newline refused", allowed("grep a b\nrm x") is None),
                 ("unbalanced quote refused", allowed("grep 'foo a.py") is None),
                 ("finished: a DONE unit's sub unit is finished", finished({"units": [{"id": "X", "state": "DONE", "sub_units": ["X.1"], "evidence": ""}]}, "X.1")),
                 ("finished: a landed sub unit of an open unit is finished", finished({"units": [{"id": "X", "state": "PARTIAL", "sub_units": ["X.1", "X.2"], "evidence": "X.1 landed 2026-09-21"}]}, "X.1")),
                 ("finished: an unlanded sub unit of an open unit is not", not finished({"units": [{"id": "X", "state": "PARTIAL", "sub_units": ["X.1", "X.2"], "evidence": "X.1 landed 2026-09-21"}]}, "X.2")),
                 ("finished: a sub unit no unit owns, or an unreadable plan, is finished (never restarted)", finished({"units": []}, "Z.9") and finished(None, "Z.9") and finished({"units": "x"}, "Z.9"))]
        bad = [n for n, good in cases if not good]
        print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
        return 1 if bad else 0


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if "--selftest" in args:
        return selftest()
    D = os.path.expanduser(args[0])
    with open("docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json", encoding="utf-8") as fh:
        plan = json.load(fh)
    n = {"PROVEN": 0, "UNPROVEN": 0, "REFUSED": 0, "NO-DATA": 0}
    for f in sorted(glob.glob(D + "/out/*.json")):
        sub = os.path.basename(f)[:-5]
        try:
            with open(f, encoding="utf-8") as fh:
                t = fh.read()
            # AN ANSWER IS APPLIED ONCE (review 2026-09-27: one unchanged answer restarted a runner twice): it is consumed
            # the moment it is read, so the next round sees only answers to the questions it sent.
            os.replace(f, f + ".applied")
            a = json.loads(t[t.find("{"):t.rfind("}") + 1])
            fact, prove, expect, hint = (str(a.get(k) or "") for k in ("fact", "prove", "expect", "hint"))
        except (OSError, ValueError, AttributeError): n["NO-DATA"] += 1; print("NO-DATA  %-8s answer unreadable" % sub); continue
        if not fact or "UNKNOWN" in fact.upper() or not expect: n["NO-DATA"] += 1; print("NO-DATA  %-8s model said UNKNOWN or gave no expectation" % sub); continue
        argv = allowed(prove)
        if argv is None:
            n["REFUSED"] += 1; print("REFUSED  %-8s proof is not a plain grep inside the repo: %s" % (sub, prove[:90])); continue
        try: r = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired): n["UNPROVEN"] += 1; print("UNPROVEN %-8s proof command did not run" % sub); continue
        if r.returncode != 0 or expect not in r.stdout: n["UNPROVEN"] += 1; print("UNPROVEN %-8s expected text absent: %s" % (sub, fact[:100])); continue
        n["PROVEN"] += 1; u = next((x for x in plan["units"] if sub in (x.get("sub_units") or [])), None)
        refusal = admissible(plan, sub)
        if refusal:
            # NEVER RESTART LANDED WORK. Measured 2026-09-22 run 5: D4.e, D6.6 and L1.1, all landed in DONE units, were
            # rebuilt from their old EXHAUSTED folders every round because only "a runner is alive" was checked; their
            # READY builds could never land (the digest rightly ignores landed sub units) and the pulse warned READY-NOT-LANDING.
            print("SKIP     %-8s %s" % (sub, refusal)); n["SKIPPED"] = n.get("SKIPPED", 0) + 1; continue
        # A PROVEN FACT IS BRIEF INPUT, NEVER A RESTART (plan E step 2b, 2026-10-01). It used to start a runner with the fact
        # in RUNNER_HINT, which also reset that sub unit's round history: FX-13.4 was "PROVEN, RESTARTED with hint" three
        # times and refused at its brief each time, because a sentence cannot change what blocked it. Now the fact is
        # written to the unit's note; the next runner the pool starts reads it into its brief, and the pool re-admits a
        # parked unit only when the note's CONTENT changed (an identical fact rewrites nothing, so it re-admits nothing).
        if not u:
            print("PROVEN   %-8s %s | no unit in the plan, no note" % (sub, fact[:110])); continue
        said = write_note(u["id"], sub, "VERIFIED FACT: %s %s" % (fact, hint))
        print("PROVEN   %-8s %s | note %s for the next brief" % (sub, fact[:110], said))

    print(" | ".join("%s %d" % kv for kv in n.items()))

    return 0


if __name__ == "__main__":
    sys.exit(main())
