#!/usr/bin/env python3
"""The advisor BEFORE the build: a Claude model turns the spec section and the real files into the plan DeepSeek executes.
usage as a library:  build_plan.plan(spec_excerpt, files_excerpt, runner=None) -> str | None
usage as a check:    python3 -B build_plan.py --selftest
Owner 2026-09-22 23:1x: fix the loop and the repair loop now. Measured today: grader pass 24 to 46 percent at round 0 because
the worker guessed the plan and learned the rubric one refusal at a time. The plan names the files, the edit list, the tests,
the exact done check and the allow list constraints; it heads the round 0 brief. Guidance only; a failed call or an answer
missing a section is None and adds nothing. BROTHER_BUILD_PLAN: on (default), off, or half (even hash of the sub unit name).
Effort high at least for this call only, like the repair advisor."""
import hashlib, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import grade_build  # FX-09: the one reader of BROTHER_BRIEF_SCREEN
MODEL = os.environ.get("BROTHER_BUILD_PLAN_MODEL", "sonnet")
SECTIONS = ("FILES", "EDITS", "TESTS", "DONE_CHECK", "CONSTRAINTS")
_PROMPT_HEAD = ("You are the planner for one build. From the specification excerpt and the real files below, write the plan a junior "
                "engineer executes without guessing. Answer with exactly these five headings, each followed by short lines:\n"
                "FILES: every path created or edited, one per line, with why.\nEDITS: per file, the functions or blocks to add or change, "
                "the guards (unknown, corrupt or missing input refuses, never a crash), and every caller to update.\nTESTS: the test file "
                "beside the module and the named cases, including one per guard and the red-without-the-code case.\nDONE_CHECK: the exact "
                "command, one line.\n")
# FX-09: today's hand typed statement of the grader's screen (BROTHER_BRIEF_SCREEN unset or off), and the sentence that
# replaces it when the brief carries THE GRADER'S SCREEN, generated from grade_build.py (it forbids shutil; the grader does not).
CONSTRAINTS_HAND = ("CONSTRAINTS: no file imports subprocess, urllib, socket, http, requests or shutil and none calls run(), "
                    "getattr() or exec() dynamically, except allow listed command runners: %s; Python 3.9 and 3.13; tests must fail without the code.\n")
CONSTRAINTS_SCREEN = ("CONSTRAINTS: restate, for these files only, the lines of THE GRADER'S SCREEN block in the specification excerpt "
                      "that bind them (allow listed command runners: %s); Python 3.9 and 3.13; tests must fail without the code.\n")
_PROMPT_TAIL = "No preamble, no code blocks.\n\nSPECIFICATION EXCERPT:\n%s\n\nREAL FILES (excerpt):\n%s\n"
PROMPT = _PROMPT_HEAD + CONSTRAINTS_HAND + _PROMPT_TAIL


def enabled(sub, env=None):
    # OFF UNLESS ASKED FOR (plan E step 2f): unset and unknown values are off, like every plan E switch.
    v = str((os.environ if env is None else env).get("BROTHER_BUILD_PLAN", "")).strip().lower()
    if v == "on": return True
    if v == "half": return int(hashlib.sha256(str(sub).encode()).hexdigest(), 16) % 2 == 0
    return False


def complete(text):
    return isinstance(text, str) and all(re.search(r"^\s*%s\s*:" % s, text, re.M) for s in SECTIONS)


def model(env=None):
    """FX-11.5 (REQ-FX11-18): the planner model, read at CALL time. MODEL above is the import time value and stays for
    readers; no call uses it, because the runner sets BROTHER_BUILD_PLAN_MODEL after importing this file."""
    if env is not None and not isinstance(env, dict):
        raise ValueError("model wants an environment mapping or None")
    value = (os.environ if env is None else env).get("BROTHER_BUILD_PLAN_MODEL")
    if value is None:
        return "sonnet"
    if not isinstance(value, str):
        raise ValueError("BROTHER_BUILD_PLAN_MODEL is not text")
    return value.strip() or "sonnet"


def plan(spec_excerpt, files_excerpt, runners="none for this unit", runner=None, timeout=300, env=None):
    """The plan headed 'BUILD PLAN (<model>, effort high):', or None. Never raises."""
    try:
        try:
            screen = grade_build.brief_screen_mode() == "on"
        except ValueError:   # unreadable switch: today's sentence; build_brief has already refused the round
            screen = False
        template = PROMPT if not screen else _PROMPT_HEAD + CONSTRAINTS_SCREEN + _PROMPT_TAIL
        prompt = template % (runners, (spec_excerpt or "")[:30000], (files_excerpt or "")[:80000])
        _eff = str(os.environ.get("BROTHER_PLANNER_EFFORT") or "medium").strip().lower()   # owner 2026-09-23 07:5x: medium, high was 26 million output tokens a night
        _eff = _eff if _eff in ("low", "medium", "high", "xhigh", "max") else "medium"
        who = model(env)
        if runner is None:
            import model_call as M, model_router as R
            before = os.environ.get("BROTHER_CLAUDE_EFFORT"); cur = str(before or "").lower()
            os.environ["BROTHER_CLAUDE_EFFORT"] = cur if cur in ("high", "xhigh", "max") else _eff
            try:
                import loop_roles
                on = loop_roles._breaker_on(dict(os.environ) if env is None else env)
                chain = loop_roles.call_chain("planner", choice=who, env=env)[0] if on else [who]
                if not chain: return None   # a refused roles file makes no call
                a = M.call(prompt, "grade", R.PRIVATE, timeout=timeout, chain=chain) if on else M.call_one(who, prompt, "grade", R.PRIVATE, timeout=timeout)
                text = a.answer if a is not None else None
                if a is not None: who = a.model   # the header names the model that answered
            finally:
                if before is None: os.environ.pop("BROTHER_CLAUDE_EFFORT", None)
                else: os.environ["BROTHER_CLAUDE_EFFORT"] = before
        else:
            text = runner(prompt)
        if not complete(text): return None
        return "BUILD PLAN (%s, effort %s), execute this plan, do not redesign it:\n%s\n" % (who, _eff, text.strip()[:12000])
    except Exception:   # sbe: allow-silent the plan is advice; None means no plan, the build proceeds on the spec alone and the runner prints that no plan was added
        return None


def selftest():
    good = lambda p: "FILES: scripts/x.py\nEDITS: add f\nTESTS: scripts/test_x.py\nDONE_CHECK: python3 -B scripts/test_x.py\nCONSTRAINTS: none"
    partial = lambda p: "FILES: a\nEDITS: b\nTESTS: c"
    raises = lambda p: (_ for _ in ()).throw(RuntimeError("wire"))
    cases = [("a complete five section plan is returned with its header", (plan("s", "f", runner=good) or "").startswith("BUILD PLAN (") and "DONE_CHECK:" in plan("s", "f", runner=good)),
             ("a plan missing a section is None", plan("s", "f", runner=partial) is None),
             ("no answer or a raising call is None, never an exception", plan("s", "f", runner=lambda p: None) is None and plan("s", "f", runner=raises) is None),
             ("the prompt carries the allow list it was given", "scripts/git_location_guard.py" in (lambda: (lambda cap: (plan("s", "f", runners="scripts/git_location_guard.py", runner=lambda p: cap.append(p) or good(p)), cap)[1][0])([]))()),
             ("enabled: default on, off is never, half splits by name", enabled("A.1", {}) and not enabled("A.1", {"BROTHER_BUILD_PLAN": "off"}) and any(enabled(s, {"BROTHER_BUILD_PLAN": "half"}) for s in ("A.1", "B.2", "C.3", "D.4")) and not all(enabled(s, {"BROTHER_BUILD_PLAN": "half"}) for s in ("A.1", "B.2", "C.3", "D.4")))]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad))); return 1 if bad else 0


if __name__ == "__main__": sys.exit(selftest() if "--selftest" in sys.argv else 2)
