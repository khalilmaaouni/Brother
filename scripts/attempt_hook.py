#!/usr/bin/env python3
"""attempt_hook: a PostToolUse hook on Bash that writes to scripts/attempt_ledger.py
without anybody typing its "record" subcommand.

learning_loop priority item n=2 in docs/plan/READINESS-ROADMAP-2026-08-29.json: "A
ledger only helps whoever writes to it, and the person least likely to write to it
is the one on their sixth attempt." scripts/attempt_ledger.py is the breaker; this
file is the mechanical write it has never had.

THE CONTRACT THIS FILE READS, taken from products/brothermode/tools/bm_bash_audit.py
(the estate's own PostToolUse Bash hook, its docstring and _run_post) and
tools/repeat-guard/repeat_guard.py (the estate's own PreToolUse/PostToolUse pair that
already fingerprints an approach and counts its failures): one JSON object on stdin,
with tool_name, tool_input.command and, on PostToolUse, tool_response holding
exit_code, stdout, stderr and timed_out for a Bash call. A PostToolUse hook cannot
deny the call (it already ran); its only channel back to the model, mirrored from
products/brothermode/tools/vault_recall_hook.py's PreToolUse use of the same
envelope, is a JSON object on stdout: {"hookSpecificOutput": {"hookEventName": ...,
"additionalContext": <text>}}. This file always exits 0.

THE FINGERPRINT, klass(). First token plus a normalized form: the home directory
collapsed to '~', numbers and hex-looking runs (shas, pids, timestamps) replaced by
one placeholder, whitespace collapsed. Two retries of the same technique against a
different sha or a different path fingerprint to the identical class string. `klass`
is used as BOTH `problem` and `class` when calling attempt_ledger's own record/check:
attempt_ledger.failures() filters on both fields together, so an automatically
inferred class can only accumulate strikes against itself if problem and class are
the same string for every call this file makes.

THE STORE. Reuses attempt_ledger.py's own ATTEMPT_LEDGER environment variable and
its STORE default (~/.claude/attempt-ledger/attempts.jsonl); this file names no
second variable of its own. A test sets ATTEMPT_LEDGER before invoking this script
as a subprocess and gets an isolated store, because attempt_ledger.STORE is read
from the environment at import time, once per process.

WHAT COUNTS AS FAILED, _classify(). exit_code is the FIRST signal, kept exactly as
before, when tool_response carries one: `(exit_code == 0) and not timed_out`.
scripts/run_evidence.py's own _ledger()/main() comment (dated 2026-08-29, lines
192-193) measured that this is not enough on its own: across 21,596 real Bash
attempts on this machine, exit_code was None 20,466 times, 0 the rest, and NEVER
non-zero, so "a hook cannot count failures however carefully it is written." So
this is the SECOND signal, read only when exit_code is None, the way
tools/repeat-guard/repeat_guard.py reads output rather than a field the harness
does not reliably fill: the last 20 non-blank lines of tool_response's stdout and
stderr are scanned, most recent first, for a failure signature (a line starting
with "Traceback", or containing "command not found", "No such file or directory",
"fatal:", "FAILED", "Error:", "error:", "ModuleNotFoundError", "SyntaxError",
"AssertionError", or the word "exit" followed by a number 1 to 255 at the end of
the line). A hit is a failure INFERRED FROM OUTPUT, noted as such
("inferred: <the matched line>"). No hit, with exit_code still None, is an UNKNOWN
outcome and writes nothing: never guessed as a failure, never guessed as a pass,
per the same "no false negative, no false positive" posture attempt_ledger.py's
own read() docstring already states for an unreadable store.

WHEN THIS FILE WRITES. A failed call (by either signal) is always recorded
(outcome "failed", one call, note per the signal that fired). Before recording
it, this file checks the class against the rows already on disk (the PRIOR
failures, not counting the one just seen) through attempt_ledger.check(); when
that check already says REFUSE, this is the attempt past the limit and the
breaker's own verdict-and-reason line ("%s: %s" % (verdict, reason), the
identical format attempt_ledger.py's own CLI main() prints) is sent back to the
model as additionalContext. A successful call (exit_code == 0 only; a None
exit_code never yields "passed", only "failed" or unknown) whose class carries no
recorded failure writes nothing at all -- the ledger must not fill with noise. A
successful call whose class DOES carry a recorded failure is written as outcome
"passed" (attempt_ledger.py's own CLI vocabulary, so base-rate's "worked" count
sees it), so the ledger keeps what finally worked next to what did not.

FAIL OPEN. Any unreadable stdin, non-JSON stdin, non-dict payload, non-Bash tool,
or unexpected exception prints exactly one line to stderr and exits 0. A broken
hook that blocks every Bash call is worse than no hook (same posture
bm_bash_audit.py and repeat_guard.py both state for themselves).

THE RESEARCH BRANCH NOW RUNS ITSELF, priority item n=4's SHIP. The refusal
text names "python3 scripts/find_out.py <the problem>" for a person to run;
this file runs it before it ever prints, so the same additionalContext
carries the top hits under the refusal rather than an instruction nobody
follows. scripts/find_out.py is imported as a module (_find_out_hits below)
and called with klass PLUS the just-recorded failure's own note (the last
output line), never klass alone: tried both, on the real vault, against four
realistic failing-command scenarios, and klass+note matched as many or more
of the four sources every single time (one scenario went 2/4 to 3/4, another
3/4 to 4/4; never worse). Top _FIND_OUT_TOP hits per source, one
"find_out: <source>: <score> <path> <title>" line each, closed by
"find_out: <n> of 4 source(s) answered"; when every source comes back
NO-DATA/empty, when find_out could not be imported, or when it raises,
_find_out_hits returns exactly one "find_out: NO-DATA (<reason>)" line
instead, so a research failure never costs the refusal its own text.

BOUNDED, because this runs inside a PostToolUse hook's own 10 second budget.
find_out.py itself is out of this file's scope (this brief's declared write
paths are attempt_hook.py, its test, and one roadmap sentence), so the bound
is an outer wall-clock check between the four source calls
(_FIND_OUT_BUDGET_SECONDS), not a new argument inside find_out.py: on the
real vault (243 failure notes, 1.1M under 40-Failures) all four sources
together take about a tenth of a second, so the budget is deliberately loose
relative to the 10 second hook timeout while still being a real, checked
number rather than an unbounded walk.

FIND_OUT_VAULT, FIND_OUT_PATTERNS, FIND_OUT_MEMORY: overridable the same way
ATTEMPT_LEDGER already is (read from the environment once, at import time),
so a test can point every source at a temp fixture instead of the real
vault, pattern store, and memory index. Unset, they default to find_out.py's
own VAULT and MEMORY constants.

Python 3, standard library only. No network. No em or en dashes anywhere in this
file, its comments, or its output.
"""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import attempt_ledger as ledger  # noqa: E402
try:
    import find_out  # noqa: E402
    _FIND_OUT_IMPORT_ERROR = None
except Exception as _find_out_exc:  # sbe: allow-silent defensive fallback; find_out.py ships beside this file, but a broken import must never crash a hook that must always exit 0
    find_out = None
    _FIND_OUT_IMPORT_ERROR = "%s: %s" % (type(_find_out_exc).__name__, _find_out_exc)

#: See the module docstring's FIND_OUT_VAULT/FIND_OUT_PATTERNS/FIND_OUT_MEMORY
#: section: read once at import time so a subprocess test gets an isolated
#: set of stores, exactly like ATTEMPT_LEDGER above.
_FIND_OUT_VAULT = os.environ.get("FIND_OUT_VAULT", find_out.VAULT if find_out else "")
_FIND_OUT_PATTERNS = os.environ.get("FIND_OUT_PATTERNS", find_out.VAULT if find_out else "")
_FIND_OUT_MEMORY = os.environ.get("FIND_OUT_MEMORY", find_out.MEMORY if find_out else "")
#: See the module docstring's BOUNDED section.
_FIND_OUT_BUDGET_SECONDS = 2.0
_FIND_OUT_TOP = 2

_HOME = os.path.expanduser("~")
# Hex-looking runs (shas, pids in hex logs) and plain numbers (pids, ports,
# timestamps) collapse to the same placeholder: the fingerprint only needs to
# know "a number/hash was here", never which one.
_NUM_HASH_RE = re.compile(r"\b[0-9a-fA-F]{6,64}\b|\b\d+\b")
_WS_RE = re.compile(r"\s+")

# The output failure signatures, checked only when exit_code is None (see the
# module docstring's WHAT COUNTS AS FAILED section). Plain substrings, one
# regex for the "exit <N>" shape (N held to 1-255, a real exit status range,
# so "exit 0" or a random paragraph ending in a big number never matches).
_OUTPUT_SIGNATURES = (
    "command not found", "No such file or directory", "fatal:", "FAILED",
    "Error:", "error:", "ModuleNotFoundError", "SyntaxError", "AssertionError",
)
_EXIT_N_RE = re.compile(r"\bexit\b.*\b(\d{1,3})\s*$")
_TAIL_LINES = 20


def fingerprint(command):
    """(class_string): first token, a pipe, then the normalized command text.
    See the module docstring's FINGERPRINT section for what "normalized" does
    and why."""
    text = str(command or "")
    stripped = text.strip()
    first = stripped.split()[0] if stripped else ""
    norm = text
    if _HOME and _HOME not in ("~", "/", ""):
        norm = norm.replace(_HOME, "~")
    norm = _NUM_HASH_RE.sub("<N>", norm)
    norm = _WS_RE.sub(" ", norm).strip()
    return "%s|%s" % (first, norm)


def _last_line(resp):
    """The last non-blank line of stderr, or stdout when stderr is empty: the
    one line a recorded attempt's note can usefully quote."""
    text = str((resp or {}).get("stderr") or (resp or {}).get("stdout") or "")
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()[:500]
    return ""


def _inferred_failure_line(resp):
    """The most recent line, among the last _TAIL_LINES non-blank lines of
    stdout+stderr, that matches an output failure signature; None when no
    line does. Scanned most-recent-first so the line closest to where the
    command actually stopped is the one quoted."""
    text = str(resp.get("stdout") or "") + "\n" + str(resp.get("stderr") or "")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for line in reversed(lines[-_TAIL_LINES:]):
        if line.startswith("Traceback"):
            return line[:500]
        if any(sig in line for sig in _OUTPUT_SIGNATURES):
            return line[:500]
        m = _EXIT_N_RE.search(line)
        if m and 1 <= int(m.group(1)) <= 255:
            return line[:500]
    return None


def _classify(resp):
    """(outcome, note): outcome is "failed", "passed", or None (unknown,
    write nothing). See the module docstring's WHAT COUNTS AS FAILED section
    for exactly what each signal is and why the second one exists."""
    if not isinstance(resp, dict):
        return None, ""
    code = resp.get("exit_code")
    if code is not None:
        ok = (code == 0) and not resp.get("timed_out")
        return ("passed" if ok else "failed"), _last_line(resp)
    line = _inferred_failure_line(resp)
    if line is not None:
        return "failed", "inferred: %s" % line
    return None, ""


def _read_stdin_json():
    try:
        raw = sys.stdin.read()
    except Exception as e:
        return None, "stdin could not be read (%s: %s)" % (type(e).__name__, e)
    if not raw or not raw.strip():
        return None, "stdin was empty"
    try:
        return json.loads(raw), None
    except Exception as e:
        return None, "stdin was not valid JSON (%s: %s)" % (type(e).__name__, e)


def _find_out_sources(problem_text):
    """The four (name, thunk) pairs _find_out_hits walks, in find_out.py's
    own order. A thunk, not a called value, so a budget check can run
    between sources without paying for one it will not use."""
    words = find_out._words(problem_text)
    return (
        ("vault failures", lambda: find_out.vault_failures(words, _FIND_OUT_VAULT)),
        ("vault learned", lambda: find_out.vault_learned(words, _FIND_OUT_VAULT)),
        ("patterns", lambda: find_out.patterns(problem_text, _FIND_OUT_PATTERNS)),
        ("memory index", lambda: find_out.memory_index(words, _FIND_OUT_MEMORY)),
    )


def _find_out_hits(problem_text):
    """The "find_out: ..." lines for problem_text. See the module docstring's
    THE RESEARCH BRANCH NOW RUNS ITSELF section for the shape of what this
    returns and why. Never raises."""
    if find_out is None:
        return ["find_out: NO-DATA (find_out module could not be imported: %s)"
                % _FIND_OUT_IMPORT_ERROR]
    start = time.monotonic()
    lines = []
    answered = 0
    checked = 0
    try:
        for name, call in _find_out_sources(problem_text):
            if time.monotonic() - start > _FIND_OUT_BUDGET_SECONDS:
                break  # see BOUNDED in the module docstring
            checked += 1
            hits = call()
            if not hits:
                continue  # None (store absent) or [] (no match): neither answered
            answered += 1
            for score, path, title in hits[:_FIND_OUT_TOP]:
                lines.append("find_out: %s: %s  %s  %s" % (name, score, path, title))
    except Exception as e:
        return ["find_out: NO-DATA (%s: %s)" % (type(e).__name__, e)]
    if answered == 0:
        return ["find_out: NO-DATA (no source matched within its time budget; "
                "%d of 4 checked)" % checked]
    lines.append("find_out: %d of 4 source(s) answered" % answered)
    return lines


def _emit_refusal(reason_line, find_out_lines):
    text = "ATTEMPT LEDGER: " + reason_line
    if find_out_lines:
        text += "\n" + "\n".join(find_out_lines)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": text,
        }
    }))


def _run(payload):
    if not isinstance(payload, dict):
        sys.stderr.write("attempt_hook: hook payload was not a JSON object\n")
        return
    if payload.get("tool_name") != "Bash":
        return  # the matcher already restricts this; defensive re-check only
    tool_input = payload.get("tool_input")
    command = (tool_input or {}).get("command") if isinstance(tool_input, dict) else None
    if not command or not str(command).strip():
        return

    resp = payload.get("tool_response")
    resp = resp if isinstance(resp, dict) else {}
    outcome, note = _classify(resp)
    if outcome is None:
        return  # unknown: no exit code and no output failure signature

    klass = fingerprint(command)
    rows_before = ledger.read()

    if outcome == "passed":
        prior_failures = ledger.failures(rows_before or [], klass, klass)
        if not prior_failures:
            return  # ordinary success, nothing recorded before it: no noise
        ledger.record(klass, klass, "passed", note)
        return

    # Check against the rows already on disk BEFORE this attempt is added to
    # them: attempt_ledger.check()'s REFUSE means "this attempt, the one that
    # just happened, was one past the limit", which is exactly the case that
    # must print. Checking against rows that already include this failure
    # would move the refusal one run earlier than the brief's own worked
    # example (third run refused, not second).
    verdict, reason = ledger.check(rows_before, klass, klass)
    ledger.record(klass, klass, "failed", note)
    if verdict == ledger.REFUSE:
        # klass plus the just-recorded note: see the module docstring's THE
        # RESEARCH BRANCH NOW RUNS ITSELF section for why this pairing was
        # chosen over klass alone.
        problem_text = ("%s %s" % (klass, note)).strip()
        _emit_refusal("%s: %s" % (verdict, reason), _find_out_hits(problem_text))


def main():
    payload, err = _read_stdin_json()
    if err is not None:
        sys.stderr.write("attempt_hook: %s\n" % err)
        return 0
    try:
        _run(payload)
    except Exception as e:
        sys.stderr.write("attempt_hook: failing open after an unexpected "
                         "error (%s: %s)\n" % (type(e).__name__, e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
