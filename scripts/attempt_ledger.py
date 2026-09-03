"""attempt_ledger: stop the third attempt at a technique that failed twice.

THE FAILURE THIS EXISTS FOR, and it is the most expensive one in this estate's
record. A visual cue in an app's main room took a week. On 2026-08-17 three
builds were spent making an element MORE VISIBLE before anyone checked whether it
was being drawn at all; it was not. On 2026-08-24 six further attempts were spent
on one class of solution, every one passing its own numeric gate, and the founder
scored the night 0 out of 5, then 1 out of 5, then 0 out of 5.

THE PART THAT MAKES THIS A TOOL RATHER THAN A NOTE: the vault ALREADY HELD the
lesson. A note named 'a gate fitted to a reference refuses the ask' carried the
line "the halo died three times to one measurement". The failure note says
plainly that it "was read and the tuning continued". So this estate's problem was
never storage and it was never recall. It was that a recalled lesson arrives as
PROSE, and prose does not stop anybody. The same week's answer was sitting in the
source file the whole time: the ask said "tooltip", the six copy strings existed,
and six rounds of light were built instead.

A rule that needs somebody to remember it at the exact moment they are frustrated
is not a control. This is the control: a counter that refuses.

WHAT IT REFUSES, precisely. Not the work. The CLASS. Attempt three at a class
that has failed twice is refused, and the refusal names what to do instead:
change the class, or stop guessing and go and find out. That second branch is the
founder's own instruction, given 2026-08-29: research when stuck rather than
looping through the same problem forever.

THE SECOND BRANCH NOW RUNS ITSELF, rather than telling somebody to. A refusal
used to end in prose: go reread the ask, go find out how it was solved
elsewhere. Prose did not stop the room week either. The refusal now calls a
research step and quotes what it found: the ask, reread verbatim, and a
reference it could actually resolve, never an instruction to go get one.

THE CLASS IS DECLARED, NEVER INFERRED. Inferring a technique class from free text
is exactly the silent guess that fails here. Declaring it costs one word and it
forces the naming that the room week never did: nobody ever wrote down that six
attempts were all "light on the painting", which is why nobody noticed.

BORROWED, and each one is doing real work rather than decorating this docstring:

  THE CIRCUIT BREAKER, from distributed systems. After N failures a breaker OPENS
  and stops sending the same call, because a call that failed N times is
  information, not bad luck. The refusal is the feature.

  REFERENCE CLASS FORECASTING, the outside view. Before attempt N plus one, the
  useful question is not "will this work" but "of the last K times this class was
  tried here, how many worked". This file can answer that, because it is written
  down. An estimate made from inside a problem is systematically optimistic.

  DELTA DEBUGGING and the null experiment. What ended the 2026-08-17 half was
  replacing the element with an unconditional solid marker, a test that CANNOT
  fail if the mechanism works. Four markers appeared, so geometry was fine and
  the render condition was false. That is one experiment worth more than three
  tuning rounds, and the ledger's escalation text asks for it by name.

  THE ANDON CORD, from a production line. Anyone may stop the line, and stopping
  is cheap and expected rather than a failure. A refusal here is that cord.

Python 3, standard library only. No network.

PRODUCER: this module is the sole producer of its own record. The write
happens inside record() (lines 114-120): p.parent.mkdir(...) then
p.open("a", encoding="utf-8") plus fh.write(json.dumps(row) + "\n"),
appending one line per attempt to STORE. It is called from this module's
own CLI ("record" subcommand, main(), lines 227-228) and from
scripts/run_evidence.py's ledger.record(...) call (run_evidence.py, line
205, after importing this module at lines 156-157), but the write itself
never happens anywhere except inside this function.
"""
import argparse
import json
import os
import sys
import pathlib

#: Overridable so the ledger can be driven end to end without writing test
#: attempts into the real one. A polluted ledger is not merely untidy: the base
#: rate below is computed from it, and a fake failure lowers a real number
#: somebody will make a decision with.
STORE = pathlib.Path(os.environ.get(
    "ATTEMPT_LEDGER",
    str(pathlib.Path.home() / ".claude" / "attempt-ledger" / "attempts.jsonl")))
LESSONS = pathlib.Path.home() / ".claude" / "repeat-guard" / "lessons.jsonl"

ALLOW, REFUSE, NODATA = "ALLOW", "REFUSE", "NO-DATA"
EXIT_ALLOW, EXIT_REFUSE, EXIT_NODATA = 0, 1, 2

#: Two strikes, from this estate's own written rule: "two failed attempts at one
#: class of solution means change the class, not the constants". So the third is
#: refused. It is a parameter rather than a constant because a cheap class may
#: deserve a third go and an expensive one may not deserve a second, but the
#: DEFAULT is the rule already written down and repeatedly ignored.
DEFAULT_STRIKES = 2


def read(store=STORE):
    """Every recorded attempt, or None when the store cannot be read.

    None is not an empty list. An unreadable store means nothing is known, and
    reporting that as 'no failures yet' is how a breaker silently stops
    breaking."""
    p = pathlib.Path(store)
    if not p.exists():
        return []
    out = []
    try:
        with p.open(encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    # One bad line must not blind the whole ledger, but a
                    # dropped record can hide a real strike and must not
                    # vanish without a trace.
                    print("attempt_ledger: %s:%d is not valid JSON, skipping"
                          % (p, n), file=sys.stderr)
                    continue
    # Explicit sentinel, not a swallow: check() treats None as NO-DATA and every
    # caller of read() already branches on it, so the caller decides what an
    # unreadable store means rather than this function guessing at it here.
    except OSError:  # sbe: allow-silent explicit None sentinel documented above; every caller branches on it
        return None
    return out


def record(problem, klass, outcome, note="", store=STORE):
    p = pathlib.Path(store)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {"problem": problem, "class": klass, "outcome": outcome, "note": note}
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def failures(rows, problem, klass):
    return [r for r in rows
            if r.get("problem") == problem and r.get("class") == klass
            and str(r.get("outcome", "")).lower().startswith("fail")]


def base_rate(rows, klass):
    """(tried, worked, note). The outside view, over every problem.

    Deliberately across problems rather than within one: the point of a
    reference class is that it is wider than the case in front of you."""
    same = [r for r in rows if r.get("class") == klass]
    worked = [r for r in same if str(r.get("outcome", "")).lower().startswith("pass")]
    if not same:
        return 0, 0, ("no attempt of this class has ever been recorded, so there "
                      "is no outside view to take. That is %s, not encouraging"
                      % NODATA)
    return len(same), len(worked), ""


def related_lessons(text, path=LESSONS):
    """Recorded lessons whose trigger appears in the problem or class.

    The refusal is more useful carrying the note somebody already wrote than
    carrying only a count."""
    p = pathlib.Path(path)
    if not p.exists():
        return []
    hits, low = [], (text or "").lower()
    try:
        with p.open(encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                try:
                    rec = json.loads(line)
                except ValueError:
                    print("attempt_ledger: %s:%d is not valid JSON, skipping"
                          % (p, n), file=sys.stderr)
                    continue
                trig = str(rec.get("trigger", "")).lower()
                if trig and trig in low:
                    hits.append(rec)
    except OSError:
        return []
    return hits


def lesson_reference(problem, klass, lessons_path=LESSONS):
    """The one lesson a refusal can actually quote: the first whose `solves`
    field names this exact technique class, or failing that the first whose
    `trigger` matches (related_lessons()). A real citation only: a source
    that will not resolve says found=False and why, never a guess."""
    p = pathlib.Path(lessons_path)
    if not p.exists():
        return {"found": False,
                "reason": "%s: no lessons file at %s" % (NODATA, p)}
    solved = []
    try:
        with p.open(encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    print("attempt_ledger: %s:%d is not valid JSON, skipping"
                          % (p, n), file=sys.stderr)
                    continue
                if str(rec.get("solves", "")).strip().lower() == str(klass).strip().lower():
                    solved.append(rec)
    except OSError:
        return {"found": False, "reason": "%s: %s could not be read" % (NODATA, p)}
    hit = solved or related_lessons("%s %s" % (problem, klass), path=lessons_path)
    if not hit:
        return {"found": False,
                "reason": "%s: no lesson's solves or trigger field matched %r"
                          % (NODATA, klass)}
    rec = hit[0]
    return {"found": True, "solves": rec.get("solves", ""),
            "trigger": rec.get("trigger", ""), "note": str(rec.get("note", ""))[:200]}


def check_output_reference(rows, problem, klass):
    """The failing check's own output, already sitting in the ledger: the
    note carried by the most recent failed attempt of this class, if any
    attempt bothered to record one (run_evidence.py does, by default)."""
    notes = [r.get("note") for r in failures(rows or [], problem, klass) if r.get("note")]
    if not notes:
        return {"found": False,
                "reason": "%s: no failed attempt of this class carried a note" % NODATA}
    return {"found": True, "note": str(notes[-1])[:500]}


def prior_solution_reference(klass, other_ledgers=()):
    """A PASSED attempt of this class in somebody else's own ledger. Never
    crawled on its own: the caller names which ledgers to check, so this
    stays a pure function taking its inputs rather than a silent sweep
    across other projects (this estate's own project-boundary rule)."""
    for store_path in other_ledgers:
        for r in (read(store_path) or []):
            if (r.get("class") == klass
                    and str(r.get("outcome", "")).lower().startswith("pass")):
                return {"found": True, "path": str(store_path), "problem": r.get("problem")}
    return {"found": False,
            "reason": "%s: no passed attempt of class %r found in %d other ledger(s)"
                      % (NODATA, klass, len(other_ledgers))}


def research(rows, problem, klass, lessons_path=LESSONS, other_ledgers=()):
    """The finding a refusal quotes, instead of telling a human to go read.
    Always three parts: the ask itself, reread verbatim; one reference,
    resolved rather than requested; and a prior solution when one is on
    hand. No network. A source that will not resolve says NO-DATA and why,
    never a fabricated citation."""
    lesson = lesson_reference(problem, klass, lessons_path)
    check_out = check_output_reference(rows, problem, klass)
    prior = prior_solution_reference(klass, other_ledgers)
    if lesson["found"]:
        synthesis = "resolved from a vault lesson, no human reading required"
    elif check_out["found"]:
        synthesis = "resolved from the failing check's own output, no human reading required"
    else:
        synthesis = "nothing resolved: %s; %s" % (lesson["reason"], check_out["reason"])
    return {"reread_ask": str(problem), "lesson": lesson, "check_output": check_out,
            "prior_solution": prior, "synthesis": synthesis}


def _reference_text(finding):
    """One line naming what actually resolved, for the refusal to quote."""
    if finding["lesson"]["found"]:
        bit = "a vault lesson already solves class %r: %s" % (
            finding["lesson"].get("solves") or finding["lesson"].get("trigger"),
            finding["lesson"]["note"])
    elif finding["check_output"]["found"]:
        bit = "the failing check's own output: %s" % finding["check_output"]["note"]
    else:
        bit = finding["synthesis"]
    if finding["prior_solution"]["found"]:
        bit += " | already solved elsewhere at %s" % finding["prior_solution"]["path"]
    return bit


def check(rows, problem, klass, strikes=DEFAULT_STRIKES, lessons_path=LESSONS,
          other_ledgers=()):
    """(verdict, reason). Pure, so every branch is testable without a store."""
    if rows is None:
        return NODATA, ("the attempt ledger could not be read, so nothing is "
                        "known about what has already been tried. That is not "
                        "permission to try again")
    prior = failures(rows, problem, klass)
    n = len(prior)
    if n < strikes:
        if n == 0:
            return ALLOW, ("no failed attempt of class %r is recorded against "
                           "%r, so this is attempt 1 and nothing is known about "
                           "it yet" % (klass, problem))
        return ALLOW, ("%d failed attempt(s) of class %r recorded, the limit is "
                       "%d. This is the last one before the class is closed, so "
                       "make it the decisive experiment rather than another "
                       "adjustment" % (n, klass, strikes))
    tried, worked, _note = base_rate(rows, klass)
    finding = research(rows, problem, klass, lessons_path, other_ledgers)
    return REFUSE, (
        "class %r has already failed %d time(s) on %r, which is the limit. "
        "Across the whole ledger this class has been tried %d time(s) and "
        "worked %d. Do not adjust its constants again. Two moves are open and "
        "neither is another attempt at this class: CHANGE THE CLASS, and write "
        "down which one you are abandoning and why; or STOP GUESSING AND GO AND "
        "FIND OUT, which means one decisive experiment that cannot fail if the "
        "mechanism works. Run it yourself: python3 scripts/find_out.py %r. "
        "That research already ran, so here it is instead of a "
        "chore: the literal ask, reread verbatim: %r. %s"
        % (klass, n, problem, tried, worked, problem, finding["reread_ask"], _reference_text(finding)))


def refusal_research(rows, problem, klass, strikes=DEFAULT_STRIKES,
                      lessons_path=LESSONS, other_ledgers=()):
    """The research step a refusal invokes, callable on its own. Driven
    backwards: an attempt that is still allowed (fewer than `strikes` prior
    failures on this class) triggers nothing at all, None, because there is
    nothing yet to refuse and so nothing yet to research."""
    verdict, _reason = check(rows, problem, klass, strikes, lessons_path, other_ledgers)
    if verdict != REFUSE:
        return None
    return research(rows, problem, klass, lessons_path, other_ledgers)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")

    r = sub.add_parser("record", help="write down an attempt and how it went")
    r.add_argument("--problem", required=True)
    r.add_argument("--class", dest="klass", required=True,
                   help="the TECHNIQUE class, declared not inferred")
    r.add_argument("--outcome", required=True, choices=["passed", "failed"])
    r.add_argument("--note", default="")

    c = sub.add_parser("check", help="may this class be tried again")
    c.add_argument("--problem", required=True)
    c.add_argument("--class", dest="klass", required=True)
    c.add_argument("--strikes", type=int, default=DEFAULT_STRIKES)
    c.add_argument("--other-ledger", dest="other_ledger", action="append", default=[],
                   help="path to another project's own attempts.jsonl to check for a "
                        "passed attempt of this class (repeatable)")

    b = sub.add_parser("base-rate", help="the outside view on a class")
    b.add_argument("--class", dest="klass", required=True)

    sub.add_parser("list", help="everything recorded")

    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    if not args.cmd:
        ap.print_help()
        return EXIT_NODATA

    rows = read()

    if args.cmd == "record":
        row = record(args.problem, args.klass, args.outcome, args.note)
        after = read() or []
        n = len(failures(after, args.problem, args.klass))
        print("recorded: %s / %s / %s" % (row["problem"], row["class"], row["outcome"]))
        print("  %d failed attempt(s) of this class now stand against this problem" % n)
        return EXIT_ALLOW

    if args.cmd == "base-rate":
        if rows is None:
            print("%s: the ledger could not be read" % NODATA, file=sys.stderr)
            return EXIT_NODATA
        tried, worked, note = base_rate(rows, args.klass)
        if note:
            print(note, file=sys.stderr)
            return EXIT_NODATA
        print("class %r: tried %d, worked %d (%.0f%%)"
              % (args.klass, tried, worked, 100.0 * worked / tried))
        print("An estimate made from inside a problem is systematically "
              "optimistic. This is the number from outside it.")
        return EXIT_ALLOW

    if args.cmd == "list":
        for row in (rows or []):
            print("%-10s %-26s %-24s %s"
                  % (row.get("outcome", "?"), str(row.get("problem"))[:26],
                     str(row.get("class"))[:24], str(row.get("note", ""))[:60]))
        print("%d attempt(s) recorded" % len(rows or []))
        return EXIT_ALLOW

    verdict, reason = check(rows, args.problem, args.klass, args.strikes,
                            other_ledgers=args.other_ledger)
    stream = sys.stderr if verdict != ALLOW else sys.stdout
    print("%s: %s" % (verdict, reason), file=stream)
    for lesson in related_lessons("%s %s" % (args.problem, args.klass))[:3]:
        print("  recorded lesson: %s" % str(lesson.get("note", ""))[:200], file=stream)
    return {ALLOW: EXIT_ALLOW, REFUSE: EXIT_REFUSE}.get(verdict, EXIT_NODATA)


if __name__ == "__main__":
    sys.exit(main())
