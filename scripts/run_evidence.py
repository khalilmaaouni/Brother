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
write happens inside capture(), which opens the file BEFORE the command runs,
writes the header with line two reading `[exit unfinished]`, then streams
every output line (stdout and stderr merged, in arrival order) to the file and
flushes it before the next line is read, so a run that is killed still leaves
everything it printed. When the command ends, capture() writes the end marker
and overwrites line two in place with the real exit code. It always writes the
FULL output, never a trimmed view; view() and findings() only read the dict
capture() already returned.
"""
import argparse
import json
import math
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


#: Line two of a capture is padded to this width, before and after the run, so
#: the real exit code can overwrite `[exit unfinished]` in place.
STATUS_WIDTH = 64

#: The line that closes a finished capture. A file without it did not finish.
END_MARK = "--- end ---"

OUTPUT_MARK = "--- output (stdout and stderr in arrival order) ---"


class CaptureRefused(Exception):
    """The capture file could not be opened, so the command was NOT run:
    running without a record is the one thing this module exists to prevent."""


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_str_list(value):
    return (isinstance(value, list) and bool(value)
            and all(isinstance(item, str) for item in value))


def _status_text(code, seconds):
    if code is None:
        return "[exit unfinished]"
    return "[exit %d after %.1fs]" % (code, seconds)


def _reading(clock):
    value = clock()
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value):
        raise ValueError("the clock returned %r, not a finite number" % (value,))
    return value


def status_line(code, seconds):
    """Line two of a capture, always STATUS_WIDTH characters wide.

    None is a run still in progress; an int is the command's exit code."""
    if code is not None and not _is_int(code):
        raise ValueError("code must be None or an int, got %s" % type(code).__name__)
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise ValueError("seconds must be a number, got %s" % type(seconds).__name__)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("seconds must be finite and not negative, got %r" % seconds)
    text = _status_text(code, seconds)
    if len(text) > STATUS_WIDTH:
        raise ValueError("status %r is wider than %d" % (text, STATUS_WIDTH))
    return text.ljust(STATUS_WIDTH)


def open_capture(path):
    """The capture file, opened for binary read and write. A seam for tests."""
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non empty string")
    try:
        return open(path, "w+b")
    except OSError as exc:
        raise CaptureRefused("%s: %s" % (path, exc)) from exc


def stream_runner(argv, cwd, env, emit):
    """Run argv with stderr merged into stdout and hand each line to emit as it
    arrives. Returns the child's exit code."""
    if not _is_str_list(argv):
        raise ValueError("argv must be a non empty list of strings")
    if cwd is not None and not isinstance(cwd, str):
        raise ValueError("cwd must be None or a string")
    if env is not None and not (isinstance(env, dict) and all(
            isinstance(k, str) and isinstance(v, str) for k, v in env.items())):
        raise ValueError("env must be None or a dict of strings")
    if not callable(emit):
        raise ValueError("emit must be callable")
    # N12.b: a copy, never the process environment itself; a Python child
    # flushes each print as it happens unless the caller chose otherwise.
    child_env = dict(os.environ if env is None else env)
    if "PYTHONUNBUFFERED" not in child_env:
        child_env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(argv, cwd=cwd, env=child_env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, universal_newlines=True,
                            encoding="utf-8", errors="replace")
    try:
        for line in iter(proc.stdout.readline, ""):
            emit(line)
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        proc.stdout.close()
    return proc.wait()


def _no_dashes(text):
    """text with every dash character turned into a space."""
    return "".join(" " if c in "-\u2010\u2011\u2012\u2013\u2014\u2015\u2212" else c
                   for c in text)


def capture(argv, store=None, cwd=None, env=None, runner=None, clock=None):
    """Run it, keep ALL of it, return where it went and what it said.

    Returns a dict: exit_code, path, lines, bytes, stdout, stderr, seconds,
    write_error (the first failed write after the header, or None).
    `stdout` here is the WHOLE stream, stdout and stderr merged in arrival
    order, and `stderr` is empty; slicing is the caller's business and the
    file outlives the caller either way. The file is written while the command
    runs: a run that is stopped leaves every line it printed and a line two
    that still reads `[exit unfinished]`."""
    if not _is_str_list(argv):
        raise ValueError("argv must be a non empty list of strings")
    for label, value in (("store", store), ("cwd", cwd)):
        if value is not None and not isinstance(value, str):
            raise ValueError("%s must be None or a string" % label)
    if env is not None and not isinstance(env, dict):
        raise ValueError("env must be None or a dict")
    for label, value in (("runner", runner), ("clock", clock)):
        if value is not None and not callable(value):
            raise ValueError("%s must be None or callable" % label)
    store = store or DEFAULT_STORE
    clock = clock or time.time
    started = _reading(clock)
    runner = runner or stream_runner

    stamp = "%d-%d" % (int(started), os.getpid())
    name = "".join(c if c.isalnum() else "-" for c in " ".join(argv))[:60].strip("-")
    path = os.path.join(store, "%s-%s.txt" % (stamp, name or "command"))
    first = ("$ %s\n" % " ".join(argv)).encode("utf-8", "replace")
    header = first + ("%s\n\n%s\n" % (status_line(None, 0.0), OUTPUT_MARK)).encode("utf-8")
    fh = None
    try:
        os.makedirs(store, exist_ok=True)
        fh = open_capture(path)
        fh.write(header)
        fh.flush()  # the header is on disk before the command starts
    except OSError as exc:
        if fh is not None:
            fh.close()
        raise CaptureRefused("%s (%s)" % (path, exc)) from exc

    out = []
    count = {"newlines": header.count(b"\n"), "bytes": len(header)}
    failed = []

    def guarded(step, *args):
        # N12.b: a write that fails (a full disk, a closed handle) never stops
        # the run; the first message is kept and the output stays in memory.
        try:
            step(*args)
        except (OSError, ValueError) as exc:
            failed.append(_no_dashes("%s" % (exc,)))

    def emit(line):
        data = line.encode("utf-8", "replace")
        out.append(line)
        guarded(fh.write, data)
        guarded(fh.flush)  # on disk before the next line is read
        count["newlines"] += data.count(b"\n")
        count["bytes"] += len(data)

    try:
        code = runner(argv, cwd=cwd, env=env, emit=emit)
        if not _is_int(code):
            raise ValueError("the runner returned %r, not an exit code" % (code,))
    except Exception as exc:  # noqa: BLE001
        # sbe: allow-silent the failure IS the captured evidence, written below
        emit("run_evidence: could not start %r: %s\n" % (argv, exc))
        code = 127
    seconds = max(_reading(clock) - started, 0.0)

    if out and not out[-1].endswith("\n"):
        emit("\n")
    stdout = "".join(out)
    trailer = ("%s\n%s\n" % (END_MARK, _status_text(code, seconds))).encode("utf-8")
    guarded(fh.write, trailer)
    count["newlines"] += trailer.count(b"\n")
    count["bytes"] += len(trailer)
    guarded(fh.seek, len(first))
    guarded(fh.write, status_line(code, seconds).encode("utf-8"))
    guarded(fh.close)
    return {"exit_code": code, "path": path, "stdout": stdout, "stderr": "",
            "seconds": seconds, "lines": count["newlines"] + 1, "bytes": count["bytes"],
            "write_error": failed[0] if failed else None}


def findings(result, colour=False):
    """Everything worth saying out loud about this run, worst first.

    Returns a list of (severity, message). Severity is one of ALERT, WARN, NOTE,
    so a caller can decide how loud to be without re-deriving the judgement."""
    red, yellow, green, dim, off = _colour(colour)
    out = []
    code, so, se = result["exit_code"], result["stdout"], result["stderr"]
    both = so + se

    if result.get("write_error") is not None:
        out.append(("ALERT", "%sthe capture file stopped growing (%s): the record on "
                             "disk is INCOMPLETE; the whole output is in this result "
                             "only%s" % (red, _no_dashes("%s" % (result["write_error"],)),
                                         off)))
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


# M3.6 R28-R34: the evidence checked log reducer. A summary's quotations must
# exist verbatim in the full text; quote free or empty input is refused.
ACCEPT = "ACCEPT"
BLOCK = "BLOCK"


def _require_text(value, name):
    if not isinstance(value, str):
        raise ValueError("%s must be a string, got %s"
                         % (name, type(value).__name__))
    return value


def quoted_spans(summary: str) -> list[str]:
    """Extract double quoted and single quoted spans from a summary.

    Quote free text returns an empty list. An unclosed quote is corrupt input
    and is refused rather than guessed at. The quote characters themselves are
    delimiters, not part of the span."""
    _require_text(summary, "summary")
    spans = []
    i = 0
    n = len(summary)
    while i < n:
        ch = summary[i]
        if ch == '"' or ch == "'":
            quote = ch
            j = i + 1
            buf = []
            while j < n:
                if summary[j] == "\\" and j + 1 < n:
                    buf.append(summary[j + 1])
                    j += 2
                    continue
                if summary[j] == quote:
                    break
                buf.append(summary[j])
                j += 1
            if j >= n:
                raise ValueError("summary has an unclosed %s quote" % quote)
            if buf:
                spans.append("".join(buf))
            i = j + 1
        else:
            i += 1
    return spans


def verbatim_hits(full_text: str, spans: list[str]) -> tuple[bool, list[str]]:
    """True only when every span occurs exactly in full_text.

    Returns (all_found, missing). A missing span is listed, never dropped, so
    the caller can say which quotation did not survive."""
    _require_text(full_text, "full_text")
    if not isinstance(spans, list):
        raise ValueError("spans must be a list, got %s" % type(spans).__name__)
    missing = []
    for span in spans:
        if not isinstance(span, str):
            raise ValueError("every span must be a string, got %s"
                             % type(span).__name__)
        if span not in full_text:
            missing.append(span)
    return (not missing, missing)


def reduce_log(full_text: str, summary: str) -> dict:
    """ACCEPT only when every quoted span of summary is verbatim in full_text.

    Empty, non string, or quote free input is refused with ValueError, never
    accepted. A quote that does not match yields BLOCK. No remote model call is
    made here; if one is added later it must sit behind the same private terms
    gate as every OpenRouter lane, and an unreadable private names file must
    block the call."""
    _require_text(full_text, "full_text")
    _require_text(summary, "summary")
    if not full_text or not summary:
        raise ValueError("reduce_log refuses empty input")
    spans = quoted_spans(summary)
    if not spans:
        raise ValueError("reduce_log refuses a quote free summary")
    ok, missing = verbatim_hits(full_text, spans)
    verdict = ACCEPT if ok else BLOCK
    return {"verdict": verdict, "spans": spans, "missing": missing}


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
    if argv is not None and not (isinstance(argv, list)
                                 and all(isinstance(a, str) for a in argv)):
        print("run_evidence: argv must be a list of strings", file=sys.stderr)
        raise SystemExit(2)
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

    try:
        result = capture(cmd, store=args.store)
    except CaptureRefused as exc:
        print("REFUSED: cannot open a capture file (%s); the command was NOT run"
              % exc, file=sys.stderr)
        return 1

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
