#!/usr/bin/env python3
"""bm_commit_msg_hook.py: refuses a commit message this repo's own recorded
policy forbids, BEFORE the commit lands.

WHY THIS EXISTS (RF-1, docs/handover/2026-08-11-morning/03-RULES-AND-PROCESS-
FIXES.md, ratified 2026-08-11 morning decision 12)
  A Claude co-author trailer landed on a public-repo commit whose recorded
  rule forbids it (repos.md, founder decision 2026-08-10: "No Claude
  co-author trailer in this repo, founder rule: only Khalil Maaouni as
  creator"), got pushed by another session, and cost a force rewrite of
  public history. The scan that would have caught it ran at PUSH time, one
  session after the commit already existed. This file moves the same check
  to COMMIT time, as a git commit-msg hook, so a forbidden message never
  becomes a commit in the first place.

WHAT IT REFUSES, each with the offending line number named in the reason
  1. An em dash, U+2014.
  2. An en dash, U+2013.
     (The standing no-em/no-en-dash rule applies to every commit message in
     every repo on this machine; see CLAUDE.md.)
  3. Any line starting with "Co-Authored-By:" (the exact trailer git itself
     emits and the one this repo's recorded policy forbids outright).

HOW IT IS WIRED (not done by this file, on purpose)
  A commit-msg hook must live at .git/hooks/commit-msg, and .git/hooks/ is
  never tracked by git: installing into it here would silently do nothing
  for every other clone. Wiring this file in is the caller's job, one line:
    printf '#!/bin/sh\\nexec python3 scripts/bm_commit_msg_hook.py "$1"\\n' \\
      > .git/hooks/commit-msg && chmod +x .git/hooks/commit-msg

EXIT CODES
  0   the message contains none of the three forbidden shapes.
  1   the message contains one of them; stderr names the line and the reason.
  2   usage error (wrong argument count, or the message file could not be
      read).

Python 3.9, standard library only. No network, no subprocess. Reads only the
one file named on argv[1].

No em or en dashes anywhere in this file, its comments, or its output.
"""

import io
import sys

#: Written as escapes on purpose, not as the literal glyphs: this repo's own
#: no-em/no-en-dash scan runs over every file, and a literal glyph here would
#: fail that scan on the one file whose entire job is detecting them.
EM_DASH = "\u2014"
EN_DASH = "\u2013"
TRAILER_PREFIX = "Co-Authored-By:"


def _warn(text):
    sys.stderr.write(text if text.endswith("\n") else text + "\n")
    sys.stderr.flush()


def check_message(text):
    """Returns None when the message is clean, or a plain-language reason
    naming the offending line number when it is not. Pure function, no I/O,
    so this is what a test drives directly."""
    for lineno, line in enumerate(text.splitlines(), 1):
        if EM_DASH in line:
            return ("line %d contains an em dash (U+2014). This repo's rule: "
                    "no em or en dashes anywhere, including commit messages. "
                    "Use a comma, a colon, or a period instead: %r"
                    % (lineno, line))
        if EN_DASH in line:
            return ("line %d contains an en dash (U+2013). This repo's rule: "
                    "no em or en dashes anywhere, including commit messages. "
                    "Use a comma, a colon, or a period instead: %r"
                    % (lineno, line))
        if line.startswith(TRAILER_PREFIX):
            return ("line %d starts with %s. This repo's recorded policy "
                    "forbids that trailer outright (repos.md, founder "
                    "decision 2026-08-10: only Khalil Maaouni as creator). "
                    "Remove the line and commit again: %r"
                    % (lineno, TRAILER_PREFIX, line))
    return None


def main(argv):
    if len(argv) != 1:
        _warn("bm_commit_msg_hook: usage: bm_commit_msg_hook.py "
              "COMMIT_MSG_FILE (git passes this argument itself; do not "
              "call it by hand without one)")
        return 2
    path = argv[0]
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except (IOError, OSError) as exc:
        _warn("bm_commit_msg_hook: could not read %s: %s" % (path, exc))
        return 2
    reason = check_message(text)
    if reason is None:
        return 0
    _warn("bm_commit_msg_hook: REFUSING this commit message, %s" % reason)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
