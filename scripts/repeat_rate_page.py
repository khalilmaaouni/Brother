#!/usr/bin/env python3
"""repeat_rate_page: writes docs/benchmarks/REPEAT-RATE.md, the public page
publishing the learning loop's repeat rate. Founder ruling 2026-09-05
(row LL-5, the question UI): the loop leads the market by publishing the
number nobody else prints, and it publishes tonight with its controlled
two arm cell reading NO-DATA and dated, rather than waiting for the two
week window to close.

THIS SCRIPT NEVER RE-IMPLEMENTS A MEASUREMENT. Every cell is read verbatim
out of an existing instrument's own stdout:

  - scripts/repeat_control.py (default run): the primary E53.5 replay
    signal, the vault_recall hook outcome line (lessons shown over
    sessions), and the secondary same-signature collision detector block.
  - scripts/repeat_control.py --start 2026-09-05: the controlled two arm
    comparison line, read honestly as NO-DATA until the two week window
    named below closes.
  - products/brothermode/tools/bm_recurrence.py report: lessons applied
    versus shown.
  - scripts/board_status.py --vault-counters: lessons recalled this week.

A cell whose instrument does not print the expected line is never
invented: the whole run refuses (exit 2, NO-DATA) rather than write a page
with a guessed number. Every machine path (anything starting /Users/) is
scrubbed before anything is written, because this page is public.
"""
import datetime
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_PATH = os.path.join(ROOT, "docs", "benchmarks", "REPEAT-RATE.md")

#: The founder's own date for the controlled arm's two week window
#: (row LL-5, 2026-09-05 question UI ruling).
CONTROL_START = "2026-09-05"
CONTROL_CHECK_DATE = "2026-09-18"

#: The three outside-evidence briefs read for the "what this does not
#: measure" paragraph (session-2026-09-05-evening, section 5 of each).
RESEARCH_BRIEFS = (
    "LL-RESEARCH-chinese.md",
    "LL-RESEARCH-frontier.md",
    "LL-RESEARCH-western.md",
)

_PATH_RE = re.compile(r"/Users/[^\s,)]+")


class InstrumentMissing(Exception):
    """Raised when an instrument's own output does not carry a cell this
    page needs. Caught only at the top level: the page is never written
    on a partial read."""


def redact(text):
    """Strip any machine path under /Users/ out of an instrument line.
    This page is public; no session id or client path may reach it."""
    return _PATH_RE.sub("(local path)", text)


def real_run(cmd_key, argv):
    """Default runner: shells out to the named script, cwd=ROOT. Returns
    (returncode, stdout, stderr). `cmd_key` is unused here; it exists so a
    test can swap this function out for one keyed by name instead of by
    the exact argv (which embeds sys.executable and an absolute path)."""
    proc = subprocess.run(
        [sys.executable] + argv, cwd=ROOT, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def first_line_starting(text, prefix):
    for line in text.splitlines():
        if line.startswith(prefix):
            return line
    return None


def block_starting(text, prefix, length):
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            return lines[i:i + length]
    return None


def gather(run=real_run):
    """Runs every instrument once, returns a dict of the redacted cell
    lines this page needs. Raises InstrumentMissing (never invents a
    cell) if any instrument fails to run or does not print an expected
    line."""
    cells = {}

    # scripts/repeat_control.py, default arms (the mechanism, not a
    # calendar window): the primary replay signal, the hook outcome line
    # (shown lessons over sessions), and the secondary collision block.
    rc, out, err = run("repeat_control", ["scripts/repeat_control.py"])
    if rc != 0:
        raise InstrumentMissing(
            "scripts/repeat_control.py exited %d: %s" % (rc, err.strip()))
    primary = first_line_starting(out, "primary repeat signal")
    if primary is None:
        raise InstrumentMissing(
            "scripts/repeat_control.py printed no primary repeat signal line")
    cells["primary_line"] = redact(primary)
    cells["primary_cmd"] = "python3 scripts/repeat_control.py"

    hook_line = first_line_starting(out, "hook outcome: vault_recall shown")
    if hook_line is None:
        raise InstrumentMissing(
            "scripts/repeat_control.py printed no vault_recall hook outcome line")
    m = re.search(r"shown (\d+) lesson\(s\) over (\d+) session\(s\)", hook_line)
    if not m:
        raise InstrumentMissing(
            "the vault_recall hook outcome line did not carry a lesson/session count: %r"
            % hook_line)
    shown, sessions = int(m.group(1)), int(m.group(2))
    if sessions <= 0:
        raise InstrumentMissing("the vault_recall hook outcome line reported 0 sessions")
    cells["hook_line"] = redact(hook_line)
    cells["per_session"] = shown / sessions

    secondary_block = block_starting(out, "secondary repeat signal", 4)
    if secondary_block is None:
        raise InstrumentMissing(
            "scripts/repeat_control.py printed no secondary repeat signal block")
    cells["secondary_block"] = [redact(l) for l in secondary_block]
    cells["secondary_cmd"] = "python3 scripts/repeat_control.py"

    # scripts/repeat_control.py --start 2026-09-05: the controlled two arm
    # comparison, honest as NO-DATA until the window closes.
    rc, out, err = run(
        "repeat_control_controlled",
        ["scripts/repeat_control.py", "--start", CONTROL_START])
    if rc != 0:
        raise InstrumentMissing(
            "the controlled repeat_control.py run exited %d: %s" % (rc, err.strip()))
    control_line = first_line_starting(out, "comparison:")
    if control_line is None:
        raise InstrumentMissing(
            "the controlled repeat_control.py run printed no comparison line")
    cells["control_line"] = redact(control_line)
    cells["control_cmd"] = (
        "python3 scripts/repeat_control.py --start %s" % CONTROL_START)

    # bm_recurrence.py report: lessons applied versus shown.
    rc, out, err = run(
        "bm_recurrence",
        ["products/brothermode/tools/bm_recurrence.py", "report"])
    if rc != 0 or not out.strip():
        raise InstrumentMissing(
            "bm_recurrence.py report produced no usable output (exit %d): %s"
            % (rc, err.strip()))
    cells["recurrence_line"] = redact(out.strip().splitlines()[0])
    cells["recurrence_cmd"] = (
        "python3 products/brothermode/tools/bm_recurrence.py report")

    # board_status.py --vault-counters: lessons recalled this week.
    rc, out, err = run(
        "board_status", ["scripts/board_status.py", "--vault-counters"])
    if rc != 0:
        raise InstrumentMissing(
            "scripts/board_status.py --vault-counters exited %d: %s"
            % (rc, err.strip()))
    weekly_line = first_line_starting(out, "Lessons recalled this week:")
    if weekly_line is None:
        raise InstrumentMissing(
            "scripts/board_status.py --vault-counters printed no weekly lesson line")
    cells["weekly_line"] = redact(weekly_line)
    cells["weekly_cmd"] = "python3 scripts/board_status.py --vault-counters"

    return cells


def render(cells, today):
    briefs = ", ".join(RESEARCH_BRIEFS)
    return """# The repeat rate

Status: first edition, published %(today)s (row LL-5, the founder's own
question UI ruling the same evening: the learning loop leads by publishing
the number nobody else prints, so this page ships now with its controlled
arm cell honest as NO-DATA rather than waiting for the two week window to
close). Every cell below is read verbatim from an existing instrument's own
stdout, never re-derived; the command that produced it sits beside it, and
today's date is `%(today)s`.

## The replay signal (primary)

Command: `%(primary_cmd)s`

```
%(primary_line)s
```

## Lessons shown, over sessions

Command: `%(primary_cmd)s`

```
%(hook_line)s
```

Per session: %(per_session)s lesson(s) shown per session (shown divided by
sessions, from the line above).

## Lessons shown this week

Command: `%(weekly_cmd)s`

```
%(weekly_line)s
```

## Lessons applied versus shown

Command: `%(recurrence_cmd)s`

```
%(recurrence_line)s
```

NO-DATA here means what it means everywhere on this page: the denominator
(applicable work units recorded) is under 5, so no rate is printed rather
than one built on too few cases.

## The controlled two arm comparison

Command: `%(control_cmd)s`

```
%(control_line)s
```

NO-DATA until %(control_date)s. The day-parity design (row E53) began
%(control_start)s; two weeks of real sessions have to accumulate in each
arm before a rate per hundred attempts per arm is honest to print. Check
again on or after %(control_date)s by rerunning the command above.

## The secondary signal (same-signature collision detector)

Command: `%(secondary_cmd)s`

```
%(secondary_block)s
```

## What this measures and what it does not

A shown lesson is not an applied one: the "lessons shown" cells above count
a recall firing, not a mistake actually avoided, which is exactly why the
"applied versus shown" cell above is kept separate and is its own NO-DATA
until there are enough recorded work units. The replay signal is n of 4: a
small, real, honestly labeled sample, not a population estimate. The
controlled arm is two weeks of real sessions, day-parity by the actual
recall mechanism rather than the calendar, and it is not backfilled with a
guess before that window closes. No vendor publishes an equivalent figure:
three outside-evidence briefs read for this page (%(briefs)s, session
2026-09-05 evening, section 5 of each) found that the benchmarks the field
actually cites, LoCoMo, LongMemEval and MemoryAgentBench, measure recall
accuracy, never whether a recalled lesson stopped a mistake from repeating;
the western brief's own gap list states this plainly: no vendor or paper it
read counts whether a recalled lesson changed the actual work produced,
only whether recall happened.
""" % {
        "today": today,
        "primary_cmd": cells["primary_cmd"],
        "primary_line": cells["primary_line"],
        "hook_line": cells["hook_line"],
        "per_session": ("%.1f" % cells["per_session"]),
        "weekly_cmd": cells["weekly_cmd"],
        "weekly_line": cells["weekly_line"],
        "recurrence_cmd": cells["recurrence_cmd"],
        "recurrence_line": cells["recurrence_line"],
        "control_cmd": cells["control_cmd"],
        "control_line": cells["control_line"],
        "control_date": CONTROL_CHECK_DATE,
        "control_start": CONTROL_START,
        "secondary_cmd": cells["secondary_cmd"],
        "secondary_block": "\n".join(cells["secondary_block"]),
        "briefs": briefs,
    }


def build_page(run=real_run, today=None):
    """Returns the full page text, or raises InstrumentMissing. Kept
    separate from main() so a test can pass a canned `run` and check the
    text without touching the filesystem or a real subprocess."""
    if today is None:
        today = datetime.date.today().isoformat()
    cells = gather(run=run)
    return render(cells, today)


def main(argv=None, run=None):
    """`run` is injectable (kept a parameter, never a rebound module
    default: a reassigned default argument does not follow a later
    change) so a test can exercise the real write-or-refuse path without
    a real subprocess."""
    effective_run = run if run is not None else real_run
    try:
        text = build_page(run=effective_run)
    except InstrumentMissing as exc:
        sys.stderr.write("NO-DATA: %s\n" % exc)
        return 2
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(text)
    print("wrote %s" % os.path.relpath(OUT_PATH, ROOT))
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
