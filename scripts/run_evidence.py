"""run_evidence: never destroy the evidence you are about to need.

THE FAILURE THIS EXISTS FOR, and it happened here, not in theory. A ten minute
test battery was captured with `tail -4`. Four lines survived. The summary line
said six suites failed and the four surviving lines named two of them, so the
other four were unknowable, the run could not be compared against the baseline,
and a merge that was ready to happen stopped. Re-running cost another ten
minutes. The evidence was not lost by a crash or a timeout: it was thrown away
at the moment of capture, deliberately, to keep the output short.

That is the whole class. A long command is run, its output is trimmed to fit a
terminal, and the trimmed part turns out to be the part that decided something.
The trim always looks reasonable while you are typing it, because at that moment
you do not yet know which line you will need.

THE RULE THIS ENFORCES: capture everything, read a slice. The full output goes to
a durable file EVERY time, and what comes back to the reader is a view. A view
can be as short as you like, because the original is still there. Nothing this
runs can lose its own record.

WHY A TOOL RATHER THAN A HABIT. This estate already knew better: its own rules
say compressed output never stands as evidence and to re-run raw when exact text
matters. Knowing it did not prevent it, and knowing it will not prevent it next
time either, because the trim is applied by the person under time pressure who
is sure this particular command is boring. A tool that cannot trim the original
removes the choice.

WHAT IT WARNS ABOUT, loudly and in colour when the terminal supports it, because
a warning nobody sees is a comment:

  * output that was NEVER going to fit, so the reader knows a view is a view
  * a command whose exit code disagrees with its own printed verdict, which is
    the shape that let a gate print FAIL and exit 0 here once
  * a run that produced NOTHING at all, which reads identically to a clean pass
    and is the most dangerous silence there is

Python 3, standard library only. No network.

origin: a human, or a session acting for one, running this script's own CLI
directly, wrapping whatever command they are about to trust: `python3
scripts/run_evidence.py -- <command>`. This estate's own founder-level rule
(the "never destroy your own evidence" law) names it that way: "~/Brother/
scripts/run_evidence.py runs a command, writes the whole output to
~/.claude/evidence, returns the command's own exit code". Confirmed by grep:
scripts/check_all.sh and scripts/test_reporting_adversarial.py both invoke it
directly by path (check_all.sh:147 runs scripts/test_run_evidence.py, its own
self-test, not capture() itself); nothing in scripts or bundle/runtime imports
run_evidence and calls capture() programmatically (verified: grep -rn "import
run_evidence" scripts bundle/runtime finds no hit outside this file's own
tests), so every real capture is a direct, deliberate CLI invocation.

PRODUCER: this module is the sole producer of its own evidence files. The
write happens inside capture(), above, at the `with open(path, "w",
encoding="utf-8") as fh: fh.write(body)` call (lines 94-95 of this file),
which always writes the FULL captured stdout and stderr, never a trimmed
view; view() and findings() only read the dict capture() already returned.
"""
import argparse
import json
import os
import subprocess
import sys
import time

#: Where full captures live. Durable and outside any repository, because an
#: evidence file that lands in a working tree turns into a diff, gets deleted as
#: clutter, and is gone the next time somebody needs it.
DEFAULT_STORE = os.path.expanduser("~/.claude/evidence")

#: Beyond this, a reader is definitely seeing a view rather than the output, and
#: is told so. Not a limit on what is captured: everything is always captured.
VIEW_WARN_LINES = 40

#: Words a command prints that claim a verdict. When one of these appears and
#: the exit code says the opposite, that disagreement is the finding, because a
#: gate that printed FAIL and exited 0 is how eleven tests once passed over a
#: broken check in this estate.
VERDICT_WORDS = ("FAILED", "FAIL:", "ERROR", "REFUSED", "NO-DATA")

RED, YELLOW, GREEN, DIM, OFF = "\033[31m", "\033[33m", "\033[32m", "\033[2m", "\033[0m"


def _colour(enabled):
    if enabled:
        return RED, YELLOW, GREEN, DIM, OFF
    return "", "", "", "", ""


def capture(argv, store=None, cwd=None, env=None, runner=None, clock=None):
    """Run it, keep ALL of it, return where it went and what it said.

    Returns a dict: exit_code, path, lines, bytes, stdout, stderr, seconds.
    `stdout` here is the WHOLE thing; slicing is the caller's business and the
    file outlives the caller either way."""
    store = store or DEFAULT_STORE
    os.makedirs(store, exist_ok=True)
    clock = clock or time.time
    started = clock()
    runner = runner or (lambda a, **kw: subprocess.run(a, **kw))
    try:
        proc = runner(argv, cwd=cwd, env=env, capture_output=True, text=True)
        out, err, code = proc.stdout or "", proc.stderr or "", proc.returncode
    except Exception as exc:  # noqa: BLE001
        # sbe: allow-silent the failure IS the captured evidence, written below
        out, err, code = "", "run_evidence: could not start %r: %s" % (argv, exc), 127
    seconds = clock() - started

    stamp = "%d-%d" % (int(started), os.getpid())
    name = "".join(c if c.isalnum() else "-" for c in " ".join(argv))[:60].strip("-")
    path = os.path.join(store, "%s-%s.txt" % (stamp, name or "command"))
    body = ("$ %s\n[exit %s after %.1fs]\n\n--- stdout ---\n%s\n--- stderr ---\n%s"
            % (" ".join(argv), code, seconds, out, err))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return {"exit_code": code, "path": path, "stdout": out, "stderr": err,
            "seconds": seconds, "lines": body.count("\n") + 1, "bytes": len(body)}


def findings(result, colour=False):
    """Everything worth saying out loud about this run, worst first.

    Returns a list of (severity, message). Severity is one of ALERT, WARN, NOTE,
    so a caller can decide how loud to be without re-deriving the judgement."""
    red, yellow, green, dim, off = _colour(colour)
    out = []
    code, so, se = result["exit_code"], result["stdout"], result["stderr"]
    both = so + se

    if not both.strip():
        out.append(("ALERT", "%sthe command printed NOTHING at all%s. That reads "
                             "exactly like a clean pass and is never the same thing: "
                             "check the command ran what you think it ran"
                    % (red, off)))
    said = [w for w in VERDICT_WORDS if w in both]
    if said and code == 0:
        out.append(("ALERT", "%sit printed %s and still exited 0%s. Trust the exit "
                             "code, and find out why the two disagree: a gate that "
                             "prints a verdict it does not enforce is worse than no "
                             "gate" % (red, ", ".join(said), off)))
    if code != 0 and not said:
        out.append(("WARN", "%sexited %s but printed no verdict word%s, so the "
                            "reason is somewhere in the full capture rather than in "
                            "any summary line" % (yellow, code, off)))
    if result["lines"] > VIEW_WARN_LINES:
        out.append(("NOTE", "%s%d lines captured: anything shown here is a VIEW%s. "
                            "The whole thing is at %s"
                    % (dim, result["lines"], off, result["path"])))
    if not out:
        out.append(("NOTE", "%sexit %s, %d line(s), full capture at %s%s"
                    % (green, code, result["lines"], result["path"], off)))
    return out


def view(result, mode="tail", n=20, grep=None):
    """A slice for a reader. The original is untouched and always on disk."""
    text = result["stdout"] + ("\n" + result["stderr"] if result["stderr"] else "")
    lines = text.splitlines()
    if grep:
        lines = [line for line in lines if grep in line]
    if mode == "head":
        return "\n".join(lines[:n])
    if mode == "all":
        return "\n".join(lines)
    return "\n".join(lines[-n:])


def _ledger():
    """The attempt ledger, or None. Optional on purpose: this runner must keep
    working in a checkout that does not carry the ledger, and a missing
    dependency is not a reason to refuse to run somebody's command."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import attempt_ledger
        return attempt_ledger
    except Exception:  # noqa: BLE001  # sbe: allow-silent the feature is optional
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--view", choices=("head", "tail", "all"), default="tail")
    ap.add_argument("-n", type=int, default=20)
    ap.add_argument("--grep")
    ap.add_argument("--store")
    ap.add_argument("--json", action="store_true")
    # THE ATTEMPT LEDGER'S TRIGGER. Declared, never inferred, and placed HERE
    # rather than in a hook because of a fact measured 2026-08-29: the harness
    # does not tell a PostToolUse hook whether a Bash command failed. A Bash
    # tool result carries interrupted, isImage, noOutputExpected, stderr and
    # stdout, and no exit code at all. Across 21,596 recorded attempts on this
    # machine, exit_code was None 20,466 times, 0 the rest, and NEVER non-zero.
    # So a hook cannot count failures however carefully it is written.
    #
    # This runner can, because it runs the command itself and reads
    # proc.returncode. The only place that knows an attempt failed is the place
    # that made it fail.
    ap.add_argument("--problem", help="what you are trying to fix, in your words")
    ap.add_argument("--class", dest="klass",
                    help="the TECHNIQUE class being tried, declared not inferred")
    ap.add_argument("command", nargs=argparse.REMAINDER)
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    cmd = [c for c in args.command if c != "--"]
    if not cmd:
        print("usage: run_evidence.py [--view head|tail|all] [-n N] [--grep S] "
              "-- <command>", file=sys.stderr)
        return 2

    ledger = _ledger()
    if args.problem and args.klass and ledger is not None:
        verdict, why = ledger.check(ledger.read(), args.problem, args.klass)
        if verdict == ledger.REFUSE:
            # REFUSED BEFORE RUNNING, which is the whole point. Running it and
            # then reporting would be a report, and a report is what this
            # estate already had for a week while six attempts went by.
            print("%s: %s" % (verdict, why), file=sys.stderr)
            return 1

    result = capture(cmd, store=args.store)

    if args.problem and args.klass and ledger is not None:
        ledger.record(args.problem, args.klass,
                      "passed" if result["exit_code"] == 0 else "failed",
                      " ".join(cmd)[:200])
    if args.json:
        print(json.dumps({k: v for k, v in result.items()
                          if k not in ("stdout", "stderr")}, sort_keys=True))
    else:
        print(view(result, args.view, args.n, args.grep))
        print("")
        for severity, message in findings(result, colour=sys.stderr.isatty()):
            print("%-5s %s" % (severity, message), file=sys.stderr)
    # THE EXIT CODE IS THE COMMAND'S OWN, never this tool's opinion of it.
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
