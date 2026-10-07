#!/usr/bin/env python3
"""The repair advisor: a Claude model reads WHY a round was refused and names the three edits; DeepSeek makes them.
usage as a library:  repair_advisor.advise(lines, build_excerpt, spec_excerpt, runner=None) -> str | None
usage as a check:    python3 -B repair_advisor.py --selftest
Owner 2026-09-22 23:0x: "Add the repair advisor" (after a day where a sub unit burned five rounds of eight builds per
exhaustion and the finisher saved 0 of 12) and "Have the repair advisor on high". Guidance only: its three numbered edits
HEAD the next round's brief (before the evidence, before the rules); it never builds, never grades, never decides. A call that
fails, or an answer without three numbered edits, is None and adds nothing. BROTHER_REPAIR_ADVISOR: on (every round), off, or
half (sub units whose name hashes even, so a run measures the advisor against itself). Effort: high at least, for this call only."""
import hashlib, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)

MODEL = os.environ.get("BROTHER_REPAIR_ADVISOR_MODEL", "sonnet")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
PROMPT = ("You are the repair advisor for one rejected build. Below are the machine's own refusal lines, the specification "
          "excerpt and the build's previous attempt. Answer with EXACTLY three numbered edits (1. 2. 3.), each one line, each naming "
          "the file and the change, smallest first, that make the refusal lines pass. No preamble, no code blocks, nothing after 3.\n\n"
          "REFUSAL LINES:\n%s\n\nSPECIFICATION EXCERPT:\n%s\n\nPREVIOUS BUILD EXCERPT:\n%s\n")
NUMBERED = re.compile(r"^\s*([123])[.)]\s+\S", re.M)


def enabled(sub, env=None):
    """on: always; half: even hash of the sub unit name (the A/B control); anything else, including unset: off."""
    v = str((os.environ if env is None else env).get("BROTHER_REPAIR_ADVISOR", "")).strip().lower()
    if v == "on": return True
    if v == "half": return int(hashlib.sha256(str(sub).encode()).hexdigest(), 16) % 2 == 0
    return False


def effort_for_call(current, env=None):
    """BROTHER_ADVISOR_EFFORT (default medium since the owner's order of 2026-09-23 07:5x; high was 26 million output tokens a
    night), or the current setting when it is already above it."""
    want = str((os.environ if env is None else env).get("BROTHER_ADVISOR_EFFORT") or "medium").strip().lower()
    want = want if want in EFFORTS else "medium"
    c = str(current or "").strip().lower()
    return c if c in EFFORTS and EFFORTS.index(c) > EFFORTS.index(want) else want


def model(env=None):
    """FX-11.5 (REQ-FX11-18): the advisor model, read at CALL time. MODEL above is the import time value and stays for
    readers; no call uses it, because the runner sets BROTHER_REPAIR_ADVISOR_MODEL after importing this file."""
    if env is not None and not isinstance(env, dict):
        raise ValueError("model wants an environment mapping or None")
    value = (os.environ if env is None else env).get("BROTHER_REPAIR_ADVISOR_MODEL")
    if value is None:
        return "sonnet"
    if not isinstance(value, str):
        raise ValueError("BROTHER_REPAIR_ADVISOR_MODEL is not text")
    return value.strip() or "sonnet"


def advise(lines, build_excerpt, spec_excerpt, runner=None, timeout=240, env=None):
    """The advisor's text, headed 'REPAIR ADVISOR (<model>):', or None. Never raises."""
    _eff = effort_for_call(os.environ.get("BROTHER_CLAUDE_EFFORT"))
    try:
        import model_call as M, model_router as R
        prompt = PROMPT % ((lines or "")[:12000], (spec_excerpt or "")[:8000], (build_excerpt or "")[:20000])
        who = model(env)
        if runner is None:
            before = os.environ.get("BROTHER_CLAUDE_EFFORT")
            os.environ["BROTHER_CLAUDE_EFFORT"] = effort_for_call(before)   # this call only; restored below
            try:
                import loop_roles
                on = loop_roles._breaker_on(dict(os.environ) if env is None else env)
                chain = loop_roles.call_chain("repair_advisor", choice=who, env=env)[0] if on else [who]
                if not chain: return None   # a refused roles file makes no call
                a = M.call(prompt, "grade", R.PRIVATE, timeout=timeout, chain=chain) if on else M.call_one(who, prompt, "grade", R.PRIVATE, timeout=timeout)
            finally:
                if before is None: os.environ.pop("BROTHER_CLAUDE_EFFORT", None)
                else: os.environ["BROTHER_CLAUDE_EFFORT"] = before
            text = a.answer if a is not None else None
            if a is not None: who = a.model   # the header names the model that answered
        else:
            text = runner(prompt)
        if not isinstance(text, str) or len(NUMBERED.findall(text)) < 3: return None
        return "REPAIR ADVISOR (%s, effort %s), do these first:\n%s\n" % (who, _eff, text.strip()[:3000])
    except Exception:   # sbe: allow-silent the advice is optional; None means no advice, the next round proceeds on the grader's own reasons
        return None


def selftest():
    good = lambda p: "1. scripts/x.py: move the subprocess call into the allow listed runner\n2. scripts/test_x.py: assert the guard raises on None\n3. scripts/x.py: return NO-DATA on an unreadable file"
    two = lambda p: "1. a\n2. b"
    none = lambda p: None
    raises = lambda p: (_ for _ in ()).throw(RuntimeError("wire"))
    cases = [("three numbered edits are returned, headed with the model name and effort", (advise("FAIL x", "b", "s", runner=good) or "").startswith("REPAIR ADVISOR (") and "effort medium" in advise("FAIL x", "b", "s", runner=good) and "3. scripts/x.py" in advise("FAIL x", "b", "s", runner=good)),
             ("fewer than three numbered edits is None", advise("FAIL x", "b", "s", runner=two) is None),
             ("no answer or a raising call is None, never an exception", advise("FAIL x", "b", "s", runner=none) is None and advise("FAIL x", "b", "s", runner=raises) is None),
             ("enabled: on is always, off or unset is never, half splits by name and is stable", enabled("A.1", {"BROTHER_REPAIR_ADVISOR": "on"}) and not enabled("A.1", {}) and not enabled("A.1", {"BROTHER_REPAIR_ADVISOR": "off"}) and enabled("A.1", {"BROTHER_REPAIR_ADVISOR": "half"}) == enabled("A.1", {"BROTHER_REPAIR_ADVISOR": "half"}) and any(enabled(s, {"BROTHER_REPAIR_ADVISOR": "half"}) for s in ("A.1", "B.2", "C.3", "D.4")) and not all(enabled(s, {"BROTHER_REPAIR_ADVISOR": "half"}) for s in ("A.1", "B.2", "C.3", "D.4"))),
             ("the call runs at the advisor effort (medium by default, BROTHER_ADVISOR_EFFORT otherwise), or higher when already set higher, never lower",
              effort_for_call(None, {}) == "medium" and effort_for_call("low", {}) == "medium" and effort_for_call("xhigh", {}) == "xhigh" and effort_for_call("bogus", {}) == "medium" and effort_for_call(None, {"BROTHER_ADVISOR_EFFORT": "high"}) == "high")]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad))); return 1 if bad else 0


if __name__ == "__main__": sys.exit(selftest() if "--selftest" in sys.argv else 2)
