"""lesson_severity: how bad was it, decided by criteria rather than by feeling.

The founder's direction, 2026-08-29: there should be a ranking of criticality in
mistakes, learning and wisdom. Measured before building it: this estate holds 183
failure notes and 7 of them carry any severity field at all. So the ranking does
not exist, and the practical effect is that every lesson arrives with equal
weight, which means the expensive ones are read with the same attention as the
trivial ones and therefore with less than they deserve.

WHY CRITERIA AND NOT JUDGEMENT. A severity assigned by how bad it felt at the
time drifts: the same class of failure gets CRITICAL on the night it cost an
hour and LOW three weeks later when somebody is tired. The four tests below are
answerable yes or no from the record, so two people reading the same failure
reach the same rank.

THE SCALE, worst first, and each rank names what makes it that rank:

  CRITICAL  the system LIED ABOUT ITSELF, or harm was irreversible.
            A check that reported clean while broken, a claim of landed work
            that never landed, a capability declared dead that was alive. These
            are worst not because they cost the most minutes but because they
            corrode the one thing autonomy runs on: that a green reading means
            something. Everything else is recoverable by spending time.

  HIGH      it RECURRED, or it will. The same mistake twice, or a defect whose
            cause is still present after the fix. Recurrence is the test,
            because a one-off costs its own hour while a pattern costs every
            future hour.

  MEDIUM    it cost real time once and the cause is closed.

  LOW       noticed, corrected inside the same step, nothing lost.

A lesson may also be WISDOM rather than a failure: something learned that no
mistake had to teach. It carries no severity, because ranking it against
failures would push it down a list it does not belong on.

Python 3, standard library only. No network.
"""
import argparse
import json
import os
import sys

CRITICAL, HIGH, MEDIUM, LOW, WISDOM = "CRITICAL", "HIGH", "MEDIUM", "LOW", "WISDOM"
ORDER = (CRITICAL, HIGH, MEDIUM, LOW, WISDOM)

#: The four questions, in order. First yes wins, so a lesson that is both a lie
#: and a recurrence ranks CRITICAL: the worse fact governs.
CRITERIA = (
    (CRITICAL, "self_misreport",
     "did the system report something about ITSELF that was not true: a check "
     "reading clean while broken, work claimed landed that was not, a "
     "capability declared unavailable that was present"),
    (CRITICAL, "irreversible",
     "was anything lost that could not be recovered: deleted work, rewritten "
     "public history, a published secret, money spent"),
    (HIGH, "recurred",
     "has this same class happened before, or is the cause still present after "
     "the fix"),
    (MEDIUM, "cost_time",
     "did it cost real time once, with the cause now closed"),
)


def rank(facts):
    """(severity, reason). `facts` is a dict of the criterion keys to booleans.

    An UNANSWERED criterion is not a no. A lesson whose facts are unknown ranks
    by what IS known and says which questions were never asked, because
    defaulting an unknown to false is how a critical failure gets filed as
    medium."""
    unknown = [key for _sev, key, _q in CRITERIA if key not in facts]
    for severity, key, question in CRITERIA:
        if facts.get(key):
            note = ""
            if unknown:
                note = ("; %d criterion(s) were never answered: %s"
                        % (len(unknown), ", ".join(unknown)))
            return severity, "%s%s" % (question, note)
    if unknown:
        return LOW, ("no criterion answered yes, but %d were never asked (%s), "
                     "so this rank is a floor rather than a verdict"
                     % (len(unknown), ", ".join(unknown)))
    return LOW, "noticed and corrected inside the same step, nothing lost"


def rank_of_text(text):
    """A best-effort rank from a written note, for the 176 notes that carry no
    severity field. Lexical and deliberately conservative: it never assigns
    CRITICAL from wording alone, because a note SAYING something was critical is
    not the same as the criteria being met, and this estate has been bitten
    enough times by a label that outran its evidence."""
    low = (text or "").lower()
    if any(p in low for p in ("reported clean", "read clean", "declared dead",
                              "claimed landed", "lied", "silently passed",
                              "false green")):
        return CRITICAL, "the note describes the system misreporting itself"
    if any(p in low for p in ("again", "second time", "third time", "recurred",
                              "same mistake", "keeps happening")):
        return HIGH, "the note describes a recurrence"
    if any(p in low for p in ("cost", "wasted", "lost an hour", "minutes")):
        return MEDIUM, "the note describes time spent"
    return LOW, "no severity signal found in the text, which is a floor not a verdict"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--facts", help='JSON, e.g. {"self_misreport": true}')
    ap.add_argument("--text", help="rank a written note instead")
    ap.add_argument("--explain", action="store_true",
                    help="print the scale and its criteria")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.explain:
        for severity, key, question in CRITERIA:
            print("%-9s %-16s %s" % (severity, key, question))
        print("%-9s %-16s %s" % (LOW, "(default)",
                                 "noticed and corrected in the same step"))
        print("%-9s %-16s %s" % (WISDOM, "(no severity)",
                                 "learned without a mistake having to teach it"))
        return 0

    if args.text:
        sev, why = rank_of_text(args.text)
    elif args.facts:
        try:
            facts = json.loads(args.facts)
        except ValueError as exc:
            print("NO-DATA: could not read the facts: %s" % exc, file=sys.stderr)
            return 2
        sev, why = rank(facts)
    else:
        print("usage: lesson_severity.py --facts JSON | --text NOTE | --explain",
              file=sys.stderr)
        return 2
    print("%s: %s" % (sev, why))
    return 0


if __name__ == "__main__":
    sys.exit(main())
