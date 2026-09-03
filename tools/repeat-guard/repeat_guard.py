#!/usr/bin/env python3
"""repeat_guard: stop repeating an approach that is not working, and stop
repeating a mistake already recorded.

FOUNDER ORDER 2026-08-24, in his words: "Do not keep trying more than 3 times
the same approach if it does not work, adjust it to the very least and learn
from your mistakes in a smart way."

WHY A HOOK AND NOT A RULE. The three-attempt rule ALREADY EXISTS in
~/.claude/CLAUDE.md under Debugging ("after 2 failures revert speculative
edits; after a 3rd stop"). It has been there for weeks and nothing enforces it,
which is exactly the condition this estate's own spend-and-autonomy law names:
a rule is not a control unless a file enforces it. This file is that file.

TWO HALVES, because there are two different failures:

  WITHIN a session   the same approach tried again and again after it failed.
                     Counted here, blocked at the 4th.
  ACROSS sessions    a mistake already written down and repeated anyway.
                     Matched here, surfaced at the MOMENT OF ACTION rather
                     than at session start, which is the whole point: the Kay
                     Vault Failures-Index is injected at session start and by
                     the time the mistake is being made it is thousands of
                     tokens upstream and no longer in play.

WHY POSTTOOLUSE IS REQUIRED. A PreToolUse hook cannot know that the previous
attempt failed; it sees only what is about to run. The outcome lives in
PostToolUse's `tool_response.exit_code`. So the guard is one file wired to
both events: PostToolUse records what happened, PreToolUse decides.

THE MODEL WRITES NOTHING HERE. Borrowed from the DeepSeek Harness memory
design (append-only SessionEvent log, model reads, loop writes): the agent's
agency over this history is entirely on the read side, so it cannot talk
itself out of its own record.

EXIT CODES, and this is the part that decides whether the control is real:
PreToolUse BLOCKS ONLY ON EXIT 2. Exit 1 is a non-blocking error and the tool
call proceeds, which looks exactly like enforcement and is not. That is the
same shape as the release gate here that printed FAIL and exited 0 on
2026-08-23, over which eleven tests passed because every one asserted the
string and never the code.

FAILS OPEN. Any malformed payload, unreadable state file, or unexpected
exception exits 0 and lets the work proceed. A guard that breaks a session
because its own state file is corrupt is worse than no guard.
"""
import hashlib
import json
import os
import pathlib
import re
import sys

STATE_DIR = pathlib.Path.home() / ".claude" / "repeat-guard"
LESSONS = STATE_DIR / "lessons.jsonl"

# The founder's number. Three attempts of one approach; the fourth is refused.
MAX_ATTEMPTS = 3

# Volatile substrings that make two runs of the SAME approach look different.
# Without this the counter never fires, because a temp path or a sha differs
# every time and every attempt hashes to a new signature.
VOLATILE = [
    (re.compile(r'/(?:private/)?(?:tmp|var/folders)/[^\s"\']+'), "<TMP>"),
    (re.compile(r'\b[0-9a-f]{7,64}\b'), "<HEX>"),
    (re.compile(r'\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?\b'), "<TS>"),
    (re.compile(r'\b\d{5,}\b'), "<NUM>"),
    (re.compile(r'\s+'), " "),
]


def signature(tool_name, tool_input):
    """A stable fingerprint of the APPROACH, not of the exact bytes."""
    if tool_name == "Bash":
        raw = str(tool_input.get("command", ""))
    elif tool_name in ("Edit", "Write", "NotebookEdit"):
        raw = tool_name + " " + str(tool_input.get("file_path", ""))
    else:
        raw = tool_name + " " + json.dumps(tool_input, sort_keys=True)[:400]
    for pattern, repl in VOLATILE:
        raw = pattern.sub(repl, raw)
    raw = raw.strip().lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16], raw[:200]


def state_path(session_id):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "no-session")[:80]
    return STATE_DIR / f"{safe}.jsonl"


def verdict_of(rec):
    """Re-derive success from the RAW fields, never from the stored `ok`.

    WHY THIS EXISTS, 2026-08-25. The exit_code defect recorded every successful
    Edit, Write and NotebookEdit as a failure, and fixing the classifier did NOT
    release anything: the counters it had already poisoned stayed poisoned, and
    the stuck state is SELF-SEALING because only a recorded SUCCESS resets a
    counter and a refused approach never runs to produce one. 465 of 791 records
    across this machine were affected and five signatures were stuck permanently.

    A data migration would have meant rewriting other live sessions' logs, which
    is destructive on records this code did not author. Deriving the verdict at
    READ time instead means a classifier fix propagates BACKWARD through every
    history automatically, in every session, touching nobody's data.

    The stored `ok` is kept in the file for provenance and is deliberately NOT
    consulted here. `exit_code` is the raw fact; absent, it means the tool never
    reported one, which is the fail-open case.
    """
    code = rec.get("exit_code")
    if rec.get("timed_out"):
        return False
    if code is None:
        return rec.get("success") is not False
    return code == 0


def read_attempts(path, sig):
    """Every recorded outcome for this signature, oldest first."""
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                continue  # a torn line is not a reason to break the session
            if rec.get("sig") == sig:
                out.append(rec)
    return out


def record(path, rec):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def matching_lessons(text):
    """Recorded mistakes whose trigger appears in what is about to run.

    Lexical matching on purpose, the choice the DeepSeek harness atlas reports
    keeps working well enough in coding agents, and one nobody has to trust a
    model to have made correctly."""
    if not LESSONS.exists():
        return []
    hits = []
    low = text.lower()
    with LESSONS.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                continue
            trig = str(rec.get("trigger", "")).lower()
            if trig and trig in low:
                hits.append(rec)
    return hits


def pre(payload):
    sig, shown = signature(payload.get("tool_name", ""), payload.get("tool_input") or {})
    path = state_path(payload.get("session_id"))
    # CONSECUTIVE failures since the last success, not failures ever. An
    # approach that worked once works; blocking it because it failed three
    # times before that would refuse a fix rather than a repetition.
    attempts = read_attempts(path, sig)
    fails = []
    for a in attempts:
        # verdict_of, never a.get("ok"): the stored verdict may have been
        # computed by the defective classifier, and re-deriving it here is what
        # releases a signature that fix alone could not.
        if verdict_of(a):
            fails = []
        else:
            fails.append(a)

    if len(fails) >= MAX_ATTEMPTS:
        last = fails[-1]
        sys.stderr.write(
            "REPEAT GUARD: this approach has already failed "
            f"{len(fails)} times in this session and is refused a "
            f"{len(fails) + 1}th time.\n\n"
            f"  approach: {shown}\n"
            f"  last exit code: {last.get('exit_code')}\n"
            f"  last error: {str(last.get('err', ''))[:300]}\n\n"
            "The founder's standing order is to adjust rather than repeat: "
            "change the approach at least in some material way, or stop and "
            "report the attempts, the hypothesis, and one or two options. "
            "Re-running the same command to see if it fails differently is "
            "the thing this guard exists to refuse.\n"
        )
        return 2

    lessons = matching_lessons(shown)
    if lessons:
        # Surfaced, never blocked. A recorded lesson is a warning at the moment
        # of action, and the moment of action is the only moment it can change
        # anything. Blocking here would refuse work on a substring match, which
        # is how three false positives happened on this machine in one day.
        notes = "; ".join(str(l.get("note", ""))[:200] for l in lessons[:3])
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext":
                    "REPEAT GUARD, recorded lesson matching what you are about "
                    "to run: " + notes,
            }
        }))
    return 0


def post(payload):
    sig, shown = signature(payload.get("tool_name", ""), payload.get("tool_input") or {})
    resp = payload.get("tool_response")
    if not isinstance(resp, dict):
        return 0  # nothing to learn from a shape we do not recognise
    code = resp.get("exit_code")
    # FAIL OPEN WHEN THERE IS NO EXIT CODE. `exit_code` is a Bash concept: the
    # Edit, Write and NotebookEdit tools this hook is ALSO registered on return
    # no such field, so `(None == 0)` scored every one of their SUCCESSES as a
    # failure and the count only ever climbed. Four successful edits to one file
    # then tripped a control whose entire purpose is refusing an approach that
    # FAILED three times. Found 2026-08-24 by being refused by it, reproduced by
    # two sessions independently, and fixed on the founder's authorisation.
    #
    # This restores the posture this file's own header already promises: an
    # unrecognised shape is allowed, never counted against the caller. A tool
    # that reports failure some other way is handled by the explicit checks
    # below rather than by an absent field meaning "broken".
    if code is None:
        ok = not resp.get("timed_out") and resp.get("success") is not False
    else:
        ok = (code == 0) and not resp.get("timed_out")
    record(state_path(payload.get("session_id")), {
        "sig": sig,
        "approach": shown,
        "ok": bool(ok),
        "exit_code": code,
        "err": (resp.get("stderr") or "")[:500],
    })
    return 0


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
        event = payload.get("hook_event_name")
        if event == "PreToolUse":
            return pre(payload)
        if event == "PostToolUse":
            return post(payload)
        return 0
    except Exception:  # noqa: BLE001 - failing open is the deliberate posture
        return 0


if __name__ == "__main__":
    sys.exit(main())
