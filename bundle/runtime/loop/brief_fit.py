#!/usr/bin/env python3
"""Fit a worker brief under the dispatcher's byte cap, cutting the LEAST useful part first.

usage as a library:  text = brief_fit.fit(brief, rules, lessons, note, cap=199000)
usage as a check:    python3 -B brief_fit.py --selftest

WHY THE ORDER MATTERS, and it is the whole reason this module exists. A repair brief is four parts: the unit's
own brief, the standing rules, the estate's failure lessons, and the NOTE carrying the grader's and red team's
actual lines from the round that just failed. The dispatcher refuses past a byte cap, so under pressure
something is cut.

Until 2026-09-21 the thing cut was the note, because it was appended to whatever the static text left over.
That is exactly backwards. The note is the ONLY part that differs between round N and round N+1: the brief, the
rules and the lessons are byte identical every round, and the worker has already been shown them. Cutting the
note turns an informed repair into a plain retry, and error-message-driven repair is measured at 2.24 times
faster than a plain retry (arXiv 2510.18327).

So the note gets a guaranteed floor and the static text yields in order of how much it repeats:
  1. the estate LESSONS go first, being identical across every lane and every round;
  2. then the standing RULES, identical across every round of this lane;
  3. the unit's own BRIEF is never cut, because without it there is no task at all.
Anything dropped says so in the text, so a worker is never silently handed less than it thinks it has."""
import sys

CAP = 199000
NOTE_FLOOR = 40000
LESSONS_CUT = "\n[estate lessons omitted this round so the failure evidence fits]\n"
RULES_CUT = "\n[standing rules omitted this round so the failure evidence fits]\n"


def fit(brief, rules, lessons, note, cap=CAP, note_floor=NOTE_FLOOR):
    """The assembled brief, under `cap` bytes, protecting up to `note_floor` bytes of `note`.

    Returns the text. Never raises on a long input: it cuts. A note longer than the room left is truncated on a
    BYTE boundary and says so, because a half decoded character in a prompt is a worse failure than a short
    note. When even the brief alone exceeds the cap, the brief is returned whole and the caller's own size
    check refuses the lane: silently shipping a truncated task specification would be worse than not shipping."""
    brief, rules, lessons, note = (x or "" for x in (brief, rules, lessons, note))
    parts = [brief, rules, lessons]
    want = min(len(note.encode()), max(0, note_floor))
    room = cap - len("".join(parts).encode())
    for i, replacement in ((2, LESSONS_CUT), (1, RULES_CUT)):
        if room >= want:
            break
        if parts[i]:
            room += len(parts[i].encode())
            parts[i] = replacement
            room -= len(replacement.encode())
    if len(note.encode()) > max(room, 0):
        note = (note.encode()[:max(room - 200, 0)].decode("utf-8", "ignore")
                + "\n[previous build excerpt cut to fit the brief]\n")
    # THE ERROR BEFORE THE RULES (delivery law; owner 2026-09-22 after the same three grader refusals recurred all day): the
    # failure evidence follows the unit's own brief and PRECEDES the standing rules and lessons, which the worker has
    # already been shown every round. Until now it was appended last, under up to 60000 bytes of static text.
    return parts[0] + note + parts[1] + parts[2]


def selftest():
    """Answer the question even when a case RAISES. Measured 2026-09-22: eleven selftests in this
    directory exited 1 with a bare traceback and no verdict, so a pipeline reading the exit code and
    a human reading the text described the same run differently. Cases are built EAGERLY, so one
    raising expression takes the whole run with it; this wrapper is what turns that into a readable
    refusal. It does not make a broken module pass: it still returns non zero."""
    try:
        return _selftest_body()
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s" % (type(exc).__name__, str(exc)[:120]))
        return 1


def _selftest_body():
    big = "L" * 60000
    cases = [("the failure evidence sits after the brief and BEFORE the rules and lessons", (lambda t: t.index("BRIEF") < t.index("NOTE") < t.index("RULES") < t.index("LESSONS"))(fit("BRIEF", "RULES", "LESSONS", "NOTE"))),
             
        ("everything fits, nothing is touched",
         fit("b", "r", "l", "n") == "bnrl"),
        ("a note within the floor survives when the static text is large",
         "EVIDENCE" in fit("b" * 100000, "r" * 40000, "l" * 40000, "EVIDENCE" + "e" * 30000)),
        ("the lessons are dropped before the note is",
         LESSONS_CUT in fit("b" * 150000, "r" * 20000, big, "n" * 30000)),
        ("the rules are dropped only after the lessons",
         RULES_CUT in fit("b" * 170000, "r" * 20000, big, "n" * 30000)
         and LESSONS_CUT in fit("b" * 170000, "r" * 20000, big, "n" * 30000)),
        ("the unit's own brief is never cut",
         fit("UNIQUEBRIEF" + "b" * 180000, "r" * 20000, big, "n" * 30000).startswith("UNIQUEBRIEF")),
        ("the result stays under the cap when it can",
         len(fit("b" * 100000, "r" * 20000, "l" * 20000, "n" * 100000).encode()) <= CAP),
        ("a note longer than the room is cut and says so",
         "cut to fit the brief" in fit("b" * 150000, "r", "l", "n" * 100000)),
        ("a tiny note is not padded or altered",
         "tiny" in fit("b", "r", "l", "tiny") and fit("b", "r", "l", "tiny").startswith("btiny")),
        ("empty inputs produce empty output rather than an error", fit("", "", "", "") == ""),
        ("None inputs are treated as empty, never crash", fit(None, None, None, None) == ""),
        ("a zero floor still protects nothing but does not crash",
         isinstance(fit("b" * 10, "r", "l", "n" * 10, note_floor=0), str)),
        ("byte truncation never leaves a broken character",
         fit("b" * 198900, "", "", "é" * 500).encode().decode("utf-8") is not None),
        ("nothing is dropped when there is room, even with a huge floor",
         fit("b", "r", "l", "n", note_floor=10 ** 6) == "bnrl"),
    ]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else (print(__doc__) or 0))
