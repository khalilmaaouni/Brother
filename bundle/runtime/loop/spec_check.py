#!/usr/bin/env python3
"""A sub unit's OWN spec done check: find it, run it the way the delivery is judged.

WHY (A/B/C test 2026-09-23): the loop landed D14.5 on its builder's own 26 tests while the spec's done check,
`test_dream_repair.WiringTest`, did not exist; the blind endpoint called it NOT DELIVERED. A landing that its spec's
check does not pass is not a delivery, so land_batch now runs this before a landing is kept.
The extractor reads the three wordings measured across the plan (101 of 104 open sub units, 2026-09-23):
  a backticked command on the "Done check" line; a fenced block after it (EVERY line runs, joined with &&);
  a bare python3 line after the label. None when none is found: the caller BLOCKS, never guesses.
usage: spec_check.py --selftest"""
import os, re, shlex, shutil, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # the copy beside this file, and only that one
from grade_build import OK_CMD, SECRET_ENV, SandboxRefused, sandboxed, test_verdict
from loop_switches import suite_env


def section(spec_text, sub):
    """The spec section for one sub unit, or '' when the heading is absent."""
    h = re.search(r"^(#{2,4}) %s\b" % re.escape(sub), spec_text or "", flags=re.M)
    if not h: return ""
    m = re.search(r"^#{2,4} %s\b.*?(?=^#{2,%d} |\Z)" % (re.escape(sub), len(h.group(1))), spec_text, flags=re.M | re.S)
    return m.group(0) if m else ""


def done_check(spec_text, sub):
    """The section's done check command, or None."""
    sec = section(spec_text, sub)
    # a backticked COMMAND on the label's line; a backticked path in the label's prose ("Existing test file is `x.py`",
    # D11.e 2026-09-24) is not one and would be executed as a bare file by the shell (exit 126, every landing dropped)
    m = re.search(r"(?i)done[- ]check[^\n`]*`((?:/usr/bin/)?python3 [^`\n]+)`", sec)
    if m: return m.group(1).strip()
    m = re.search(r"(?i)done[- ]check[^\n]*\n+```[a-z]*\n(.*?)\n```", sec, flags=re.S)
    lines = [l.strip() for l in m.group(1).splitlines() if l.strip() and not l.strip().startswith("#")] if m else []
    if lines: return " && ".join(lines)
    m = re.search(r"(?im)done[- ]check[^\n]*\n+\s*((?:/usr/bin/)?python3 [^\n]+)", sec)
    return m.group(1).strip() if m else None


def accepted(cmd):
    """True only for the ONE shape the grader accepts (grade_build.OK_CMD: one python3 test invocation, no chain, no
    shell, no -c). run_both refuses everything else before any runner is called; gate_refusal asks the same question."""
    return isinstance(cmd, str) and bool(OK_CMD.match(cmd.strip()))


def gate_refusal(spec_text, sub):
    """'' when the landing gate would run this section's done check, else the gate's refusal in words. ONE definition of
    an acceptable done check (2026-09-29): spec_score's DONE CHECK point and brief_check's B4 used to accept any python3
    line the grader's regex matched, while this gate runs done_check() (a fenced block joined with &&) through
    accepted(); on 1af4d51be, 33 of 338 sections scored the point on a check this gate refuses, and green builds were dropped."""
    cmd = done_check(spec_text, sub)
    if cmd is None:
        return "the landing gate finds no done check in the section (a backticked command on the Done check line, a fenced block after it, or a bare python3 line after it)"
    if not accepted(cmd):
        return "the landing gate refuses %r: not one test command the grader accepts (a fenced block runs every line, joined with &&)" % cmd[:80]
    return ""


def run_both(cmd, cwd, timeout=900, runner=None):
    """(True, detail) when cmd exits 0 under python3 AND /usr/bin/python3 with an EMPTY HOME and the unittest runner's
    own summary shows at least one test executed on each, else (False, why). A timeout is a failure. An exit 0 that
    ran no test is a failure too (Codex audit F1, 2026-09-27: an EMPTY test script was accepted as the spec's check)."""
    # The command comes from spec markdown that models draft (spec_wave), so it is data: only the ONE shape the
    # grader accepts (grade_build.OK_CMD: one python3 test invocation, no chain, no shell, no -c) may run, and it
    # runs as an argv list, never through a shell. Found 2026-09-24 by the security review: `python3 -c 1; echo X`
    # from a section reached shell=True in the landing tree with the full environment.
    if not accepted(cmd):
        return False, "refused: not one test command the grader accepts (a chained, shell or non test line never reaches a shell)"
    argv = shlex.split(cmd.strip())
    home = tempfile.mkdtemp(prefix="spec-check-home-")
    os.makedirs(os.path.join(home, "tmp"), exist_ok=True)
    # THE SPEC'S CHECK IS BUILD CODE AND RUNS WHERE BUILD CODE RUNS (review 2026-10-02, reproduced: this gate ran the
    # build's own test on the open host, network up, writes anywhere, credential shaped names kept, while every other
    # landing step ran it under grade_build.sandboxed with loop_switches.suite_env). Same profile, same environment, and
    # the grader's own rule for credential shaped names (grade_build.run); a host without the sandbox refuses, never bare.
    env = suite_env(os.environ, home)
    for k in [k for k in env if SECRET_ENV.search(k)]:
        env.pop(k)
    try:
        for py in ("python3", "/usr/bin/python3"):
            try:
                c = sandboxed([py] + argv[1:], cwd, home)
            except SandboxRefused as exc:
                return False, "%s: SANDBOX REFUSED: %s" % (py, exc)
            try:
                r = (runner or subprocess.run)(c, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
            except subprocess.TimeoutExpired:
                return False, "%s: timed out after %d s" % (py, timeout)
            if r.returncode != 0:
                last = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
                return False, "%s exit %d: %s" % (py, r.returncode, (last[-1] if last else "no output")[:120])
            kind, detail = test_verdict(r.returncode, (r.stderr or "") + (r.stdout or ""))
            if kind != "PASSED":
                return False, "%s exit 0 but ran no test: %s" % (py, detail[:120])
        return True, "green on python3 and /usr/bin/python3"
    finally:
        shutil.rmtree(home, ignore_errors=True)


def selftest():
    S = ("## X.1 first\nDone check: `python3 -B scripts/test_a.py`\n## X.2 second\nDone-check:\n```\npython3 a.py\n# note\npython3 b.py\n```\n"
         "## X.3 third\nDone check is a single runnable command:\npython3 -B scripts/test_c.py\n## X.4 none\nNo check here.\n")
    calls = []
    ran = "----------------------------------------------------------------------\nRan 1 test in 0.001s\n\nOK\n"
    ok = lambda code, err=ran: (lambda c, **k: (calls.append(c), type("R", (), {"returncode": code, "stdout": "last line", "stderr": err})())[1])
    S += ("## X.5 prose path then fence\nDone check: runnable from repository root. Existing test file is `plugin/t.py` existing.\n\n"
          "```sh\npython3 -B -m unittest plugin.t.T -v\n```\n")
    cases = [("a backticked command", done_check(S, "X.1") == "python3 -B scripts/test_a.py"),
             ("a backticked path in the label's prose is not the command; the fenced block is (D11.e 2026-09-24)",
              done_check(S, "X.5") == "python3 -B -m unittest plugin.t.T -v"),
             ("a fenced block runs every line, comments skipped", done_check(S, "X.2") == "python3 a.py && python3 b.py"),
             ("a bare line after the label", done_check(S, "X.3") == "python3 -B scripts/test_c.py"),
             ("no check is None, never a guess", done_check(S, "X.4") is None and done_check(S, "X.9") is None and done_check(None, "X.1") is None),
             ("a section ends at the next heading of its level", "X.2" not in section(S, "X.1")),
             ("gate_refusal: one accepted command is ''", gate_refusal(S, "X.1") == "" and gate_refusal(S, "X.5") == ""),
             ("gate_refusal: a fenced block the gate chains is refused by name", "refuses" in gate_refusal(S, "X.2")),
             ("gate_refusal: no check at all is refused, never ''", gate_refusal(S, "X.4") != "" and gate_refusal(None, "X.1") != "")]
    good, _ = run_both("python3 -B scripts/test_a.py", ".", runner=ok(0))
    cases += [("green needs both Pythons, the interpreter swapped, argv a list and no shell",
               good and calls[-1][-3:] == ["/usr/bin/python3", "-B", "scripts/test_a.py"] and calls[-2][-3] == "python3"),
              ("each run is wrapped by the grader's sandbox (sandbox-exec leads the argv)",
               good and calls[-1][0] == "sandbox-exec" and calls[-2][0] == "sandbox-exec")]
    n_before = len(calls)
    inj, why_inj = run_both("python3 -c 1; echo INJECTED", ".", runner=ok(0))
    chain, _ = run_both("python3 -B scripts/test_a.py && python3 -B scripts/test_b.py", ".", runner=ok(0))
    cases += [("a shell form or a chained command is refused before any runner is called",
               inj is False and "refused" in why_inj and chain is False and len(calls) == n_before)]
    bad, why = run_both("python3 -B scripts/test_a.py", ".", runner=ok(1))
    cases += [("a red run is False and names the interpreter", not bad and why.startswith("python3 exit 1"))]
    for label, err in (("an exit 0 with no unittest summary (an empty script)", ""),
                       ("an exit 0 whose summary executed zero tests", ran.replace("Ran 1 test", "Ran 0 tests")),
                       ("an exit 0 whose only test was skipped", ran.replace("OK", "OK (skipped=1)")),
                       ("two summaries in one run (one forged)", ran + ran)):
        cases += [(label + " is not green", run_both("python3 -B scripts/test_a.py", ".", runner=ok(0, err))[0] is False)]
    def boom(c, **k): raise subprocess.TimeoutExpired(c, 1)
    cases += [("a timeout is a failure", run_both("python3 -B scripts/test_a.py", ".", runner=boom)[0] is False)]
    home_seen = []
    run_both("python3 -B scripts/test_a.py", ".", runner=lambda c, **k: (home_seen.append(k["env"]["HOME"]), type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())[1])
    cases += [("the check runs with an empty HOME, never this machine's", home_seen and home_seen[0] != os.path.expanduser("~"))]
    env_seen = []
    os.environ["SPEC_CHECK_SELFTEST_TOKEN"] = "planted"   # the NAME is the test; the value is never printed
    try:
        run_both("python3 -B scripts/test_a.py", ".", runner=lambda c, **k: (env_seen.append(set(k["env"])), type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())[1])
    finally:
        os.environ.pop("SPEC_CHECK_SELFTEST_TOKEN", None)
    cases += [("a credential shaped name is dropped from the check's environment (grade_build.SECRET_ENV)",
               env_seen and "SPEC_CHECK_SELFTEST_TOKEN" not in env_seen[0] and "PATH" in env_seen[0])]
    os.environ["GIT_DIR"] = os.path.join(tempfile.gettempdir(), "spec-check-selftest-not-a-repo")
    try:
        run_both("python3 -B scripts/test_a.py", ".", runner=lambda c, **k: (env_seen.append(set(k["env"])), type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())[1])
    finally:
        os.environ.pop("GIT_DIR", None)
    cases += [("a git location variable is dropped, the landing's suite environment (loop_switches.suite_env)",
               len(env_seen) == 2 and "GIT_DIR" not in env_seen[1] and "PYTHONDONTWRITEBYTECODE" in env_seen[1])]
    bad_names = [n for n, v in cases if not v]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad_names else "FAILED: " + ", ".join(bad_names)))
    return 1 if bad_names else 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else 2)
