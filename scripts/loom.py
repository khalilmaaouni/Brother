#!/usr/bin/env python3
"""loom: the interaction layer above the receipt door.

THE COMMITTED SECOND STEP of the door redesign decided on 2026-08-31
(docs/decisions/door-redesign-2026-08-31.json, option A: "the receipt door
now, the loom committed after the filing"). The receipt door already turns a
finished run into facts a person can check: one receipt per piece of work,
the scoping sentence under all of them, and two screens whose every mark came
out of a fixed table. What it does not do is CLOSE THE LOOP. The screens are
posed and nobody's answer is ever written down, and a unit whose own declared
scope names a risk class runs, merges and is reported after the fact, which
makes the release screen a report of something already done rather than a
decision that could still go either way.

THREE THINGS THIS FILE ADDS, and nothing else:

  PARKING, so the release screen is a gate and not an obituary. A unit whose
  own declared words hit one of the six risk classes is held BEFORE it is
  claimed, run or merged. Parking reuses the scheduler's existing founder
  gate (scripts/graph_loop.py's plan() already defers any row whose status is
  "AWAITING FOUNDER" as FOUNDER-GATED and never dispatches it) rather than
  teaching the scheduler a seventh status it would have to learn everywhere.

  THE ANSWER, recorded in the person's own words and never generated. There
  is no auto-accept, no default acceptor and no path from a green check to an
  answer, which is the same line scripts/accept_delivery.py already draws for
  the delivery record. The answer file names who, when, on which screen, and
  the evidence the question was posed on.

  THE DECISION IS NEVER SCORED. The evidence is scored, by
  receipt_door.MARK_TABLE, which is a lookup and not a judgement. The human's
  choice on that evidence carries no mark at all: every answer record says
  scored: false and says why, because a number attached to a person's own
  decision would be an opinion wearing arithmetic's clothes. That sentence is
  the acceptance screen's own; it is held to here rather than restated.

WHAT THIS FILE DELIBERATELY DOES NOT DO. It does not touch the six verb
routing and it does not add a fifth human moment: the charter's four (intent,
forcing condition, release, acceptance) are the whole list, and this only
gives the last two a place to put the answer. It renders through
decide.render (via receipt_door), never decide.main, which stamps a
machine-wide intake sentinel a test fixture must never touch.

Exit codes
  0  the command did what it says
  2  refused or NO-DATA, each one named on stdout: a duplicate answer, an
     unparsable timestamp, an unknown screen or choice, a run directory with
     no Work document in it, a screen this run never posed. NO-DATA is never
     a pass.

origin: two callers, both named here. scripts/brother_run.py imports this
module and calls park_units() before its drain when the caller passed
--park-risky, and calls parked_ids() and park_reason() while building its
report; a human runs this module's own CLI (main(), below) to answer a screen
a run left waiting. Nothing else calls it (verified: grep -rln "import loom"
over scripts and bundle/runtime lists brother_run.py, this file's own test,
and the generated bundle mirror).

PRODUCER: this module is the sole producer of the answer record. The write is
the os.open(..., O_CREAT | O_EXCL | O_WRONLY) plus json.dump inside
record_answer() (below), mirroring scripts/accept_delivery.py so a duplicate
answer is refused by the filesystem rather than by an exists() check this
process could lose a race to. It also updates rows of a Work document that
door.py created and brother_run.py otherwise owns: park_units() and
apply_release() rewrite that document in place, touching only the `status`
and `parked` fields of the rows they name.

Python 3, standard library only. No network.
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import receipt_door  # noqa: E402

NODATA = "NO-DATA"

#: The scheduler's own founder gate, reused rather than reinvented:
#: graph_loop.plan() already refuses to dispatch a row whose status is this
#: and reports it as FOUNDER-GATED with a reason. A new status would have to
#: be taught to the scheduler, the board and every reader of a Work document.
PARKED_STATUS = "AWAITING FOUNDER"
#: What a released unit goes back to, which is work_record.create()'s own
#: default for a fresh row, so a released unit is indistinguishable from one
#: that was never parked.
RELEASED_STATUS = "SCHEDULED"

SCREENS = ("release", "acceptance")
CHOICES = ("accept", "hold")

#: Written into every answer record, because the reason a decision carries no
#: mark is part of the record and not a convention a later reader has to
#: already know.
WHY_NOT_SCORED = (
    "the evidence is scored, by a fixed table that looks facts up rather than "
    "judging them; the person's decision on that evidence is not scored at "
    "all, because a mark on a human's own choice would be an opinion wearing "
    "arithmetic's clothes")


def _rows(record):
    return record.get("rows") or record.get("units") or []


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _save(path, doc):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)


def triggers_by_unit(record):
    """{unit_id: [(risk_class, the words it hit on)]}, straight from
    receipt_door.risk_triggers, which is a pattern match over what each unit
    itself declared. Nothing here decides what is risky."""
    out = {}
    for name, uid, words in receipt_door.risk_triggers(_rows(record)):
        out.setdefault(uid, []).append((name, words))
    return out


def park_body(hits):
    """What a parked unit's sentence says after its subject. It names the
    class and the words, because a gate that cannot say what tripped it is a
    gate nobody trusts twice. Built once and worn two ways: with the unit id
    as the subject on the run's own surface, and with "it" as the subject in
    the delivery report, where the report already supplies the id and every
    other refusal reason beside it starts the same way."""
    return ("parked before it runs: its own declared scope names %s, which "
            "this estate holds for a person to decide. Nothing of it was "
            "claimed, run or merged."
            % "; ".join("%s (on the words: %s)" % (name, words)
                        for name, words in hits))


def park_sentence(uid, hits):
    return "%s is %s" % (uid, park_body(hits))


def park_units(record_path):
    """Hold every unit whose own declared scope names a risk class, before the
    drain claims it. Returns (parked_ids, sentences).

    ONE WAY AND IDEMPOTENT: a row already carrying a `parked` marker is left
    exactly as the last answer left it, so resuming a run whose units a person
    already released does not park them a second time and quietly undo the
    release. A DONE row is never parked either: the decision it would gate has
    already happened, and pretending otherwise would misreport the run."""
    doc = _load(record_path)
    hits = triggers_by_unit(doc)
    parked, sentences, touched = [], [], False
    for row in _rows(doc):
        uid = row.get("id")
        if uid not in hits or row.get("parked") or row.get("status") == "DONE":
            continue
        why = park_sentence(uid, hits[uid])
        row["status"] = PARKED_STATUS
        row["parked"] = {
            "triggers": [{"trigger": name, "words": words}
                         for name, words in hits[uid]],
            "why": why,
            "answered": None,
        }
        parked.append(uid)
        sentences.append(why)
        touched = True
    if touched:
        _save(record_path, doc)
    return parked, sentences


def parked_ids(record):
    """Every unit currently held, in plan order: parked and either unanswered
    or answered `hold`. A released unit is not in this list, which is what
    makes it usable as "what is still waiting on a person"."""
    out = []
    for row in _rows(record):
        parked = row.get("parked")
        if not parked:
            continue
        answered = parked.get("answered") or {}
        if answered.get("choice") != "accept":
            out.append(row.get("id"))
    return out


def park_reason(row):
    """The plain sentence a parked unit contributes to the delivery report, or
    "" for a unit that was never parked. A held unit reports the person's own
    words, because the reason it did not ship is now theirs and not the
    engine's."""
    parked = row.get("parked") or {}
    if not parked:
        return ""
    answered = parked.get("answered") or {}
    if answered.get("choice") == "hold":
        return ("it was parked on a risk trigger and %s held it on %s: %s"
                % (answered.get("by") or "a person",
                   answered.get("at") or NODATA,
                   answered.get("words")
                   or "no words were recorded with the hold"))
    if answered.get("choice") == "accept":
        # Released and still not delivered. The release is not the delivery,
        # and saying so keeps a released but unfinished unit from reading as
        # if a person's decision were the thing that failed.
        return ("it was released by %s on %s and has not finished yet; ask "
                "for the same outcome again to continue it"
                % (answered.get("by") or "a person",
                   answered.get("at") or NODATA))
    hits = [(t.get("trigger"), t.get("words"))
            for t in parked.get("triggers") or []]
    if hits:
        return "it is " + park_body(hits)
    return "it was parked on a risk trigger, and the marker names no class"


def evidence_posed(receipts):
    """What the question was posed ON, carried into the answer record: the id,
    the state, the command and the captured exit code of every piece of work,
    exactly as receipt_door computed them. No prose and no marks: the screen
    carries those, and this is the part a later reader needs to know what was
    in front of the person when they answered."""
    if not receipts:
        return NODATA
    return [{"id": r.get("id"), "state": r.get("state"),
             "command": r.get("command"), "exit_code": r.get("exit_code")}
            for r in receipts]


def answers_dir(run_dir):
    """Beside the screens, deliberately: brother_run finds a run's Work
    document as "the one .json in the run directory that is neither claims nor
    target", so a third json file at the top of a run directory breaks
    --resume. The screens subdirectory is where nothing counts files."""
    return os.path.join(run_dir, "screens")


def answer_path(run_dir, screen):
    return os.path.join(answers_dir(run_dir), "%s-answer.json" % screen)


def parse_iso(value):
    """Raises ValueError on anything that is not a real ISO date or datetime.
    Never guesses and never defaults to now(), the same rule
    scripts/accept_delivery.py holds for an acceptance: the time a person
    answered is their claim or it is nothing."""
    return datetime.datetime.fromisoformat(value)


def record_answer(run_dir, screen, choice, by, at, words="", receipts=None):
    """Write one human answer to one screen. (True, path) or (False, reason).

    RECORDED, NEVER GENERATED: every field below comes from the caller, and
    there is no branch in this function that invents a choice, an acceptor or
    a time. A second answer to the same screen is refused by O_EXCL rather
    than overwriting the first person's record."""
    if screen not in SCREENS:
        return False, ("%r is not a screen this run poses; the screens are %s"
                       % (screen, " and ".join(SCREENS)))
    if choice not in CHOICES:
        return False, ("%r is not an answer; the answers are %s, and neither "
                       "of them is scored" % (choice, " and ".join(CHOICES)))
    if not str(by or "").strip():
        return False, ("an answer needs the name of the person giving it; "
                       "there is no default acceptor")
    try:
        parse_iso(at)
    except (TypeError, ValueError):
        return False, "the time %r is not a valid ISO date or datetime" % at
    entry = {
        "screen": screen,
        "choice": choice,
        "by": str(by).strip(),
        "at": str(at).strip(),
        "words": str(words or "").strip(),
        "scored": False,
        "why_not_scored": WHY_NOT_SCORED,
        "posed_on": evidence_posed(receipts),
    }
    path = answer_path(run_dir, screen)
    try:
        os.makedirs(answers_dir(run_dir), exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False, ("the %s screen of this run was already answered (%s); "
                       "a second answer is refused, not written over the "
                       "first" % (screen, path))
    except OSError as exc:
        return False, "%s: the answer could not be written: %s" % (NODATA, exc)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(entry, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return True, path


def read_answer(run_dir, screen):
    """The recorded answer to one screen, or None. A file that will not parse
    is None as well: an unreadable answer is not an answer."""
    try:
        return _load(answer_path(run_dir, screen))
    except (OSError, ValueError):
        return None


def apply_release(record_path, entry):
    """Apply one release answer to every unit still waiting on it. Returns
    (released_ids, held_ids).

    `accept` puts a parked unit back to SCHEDULED, so the next drain claims it
    like any other unit. `hold` leaves the status exactly where it is and only
    writes the answer down: a hold that moved a status would be the engine
    making a decision about the decision a person just made."""
    doc = _load(record_path)
    stamp = {k: entry.get(k) for k in ("choice", "by", "at", "words")}
    released, held = [], []
    for row in _rows(doc):
        parked = row.get("parked")
        if not parked or (parked.get("answered") or {}).get("choice"):
            continue
        parked["answered"] = stamp
        if entry.get("choice") == "accept":
            row["status"] = RELEASED_STATUS
            released.append(row.get("id"))
        else:
            held.append(row.get("id"))
    if released or held:
        _save(record_path, doc)
    return released, held


def _work_doc(run_dir):
    """The run's Work document, by brother_run's own rule rather than a second
    copy of that rule. Imported inside the function because brother_run
    imports this module, and a function level import is the plain way to let
    two modules know about each other without an import cycle."""
    import brother_run  # noqa: PLC0415
    return brother_run._find_work_doc(run_dir)


def _show(run_dir, out):
    doc_path = _work_doc(run_dir)
    if not doc_path:
        out.append("%s: %s holds no single Work document, so there is nothing "
                   "to show; a run directory holds exactly one"
                   % (NODATA, run_dir))
        return 2
    doc = _load(doc_path)
    waiting = parked_ids(doc)
    rows = {r.get("id"): r for r in _rows(doc)}
    out.append("loom: %r" % doc.get("outcome"))
    if not waiting:
        out.append("  nothing is parked: no piece of this run named a risk "
                   "class, or every parked piece was already released")
    for uid in waiting:
        out.append("  %s: %s" % (uid, park_reason(rows.get(uid) or {})))
    for screen in SCREENS:
        page = os.path.join(answers_dir(run_dir), "%s-screen.html" % screen)
        answer = read_answer(run_dir, screen)
        if answer:
            out.append("  the %s screen was answered %r by %s on %s"
                       % (screen, answer.get("choice"), answer.get("by"),
                          answer.get("at")))
        elif os.path.isfile(page):
            out.append("  the %s screen is waiting for an answer: %s"
                       % (screen, page))
    return 0


def _answer(args, out):
    doc_path = _work_doc(args.run)
    if not doc_path:
        out.append("%s: %s holds no single Work document, so there is no run "
                   "here to answer" % (NODATA, args.run))
        return 2
    page = os.path.join(answers_dir(args.run), "%s-screen.html" % args.screen)
    if not os.path.isfile(page):
        out.append("%s: this run posed no %s screen (%s does not exist), so "
                   "there is no question here to answer"
                   % (NODATA, args.screen, page))
        return 2
    choice = "accept" if args.accept else "hold"
    ok, result = record_answer(args.run, args.screen, choice, args.by,
                               args.at, args.words)
    if not ok:
        out.append("loom: refused: %s" % result)
        return 2
    out.append("loom: your answer is recorded in %s" % result)
    if args.screen == "release":
        released, held = apply_release(doc_path, _load(result))
        if released:
            out.append("loom: %d piece(s) released and back in the queue "
                       "(%s); ask for the same outcome again to finish them"
                       % (len(released), ", ".join(released)))
        if held:
            out.append("loom: %d piece(s) stay held (%s); nothing of them "
                       "will run" % (len(held), ", ".join(held)))
        if not released and not held:
            out.append("%s: nothing in this run was waiting on a release "
                       "answer, so your answer changed no piece of work"
                       % NODATA)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)

    park = sub.add_parser("park", help="hold every unit whose own declared "
                                       "scope names a risk class")
    park.add_argument("--record", required=True,
                      help="the run's Work document")

    show = sub.add_parser("show", help="what this run is waiting on")
    show.add_argument("--run", required=True, help="the run directory")

    answer = sub.add_parser("answer", help="record your answer to a screen")
    answer.add_argument("--run", required=True, help="the run directory")
    answer.add_argument("--screen", required=True, choices=list(SCREENS))
    side = answer.add_mutually_exclusive_group(required=True)
    side.add_argument("--accept", action="store_true")
    side.add_argument("--hold", action="store_true")
    answer.add_argument("--by", required=True,
                        help="your name; there is no default acceptor")
    answer.add_argument("--at", required=True,
                        help="when you answered, ISO date or datetime; never "
                             "defaulted to now")
    answer.add_argument("--words", default="",
                        help="why, in your own words; kept verbatim")

    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    out = []
    try:
        if args.command == "park":
            parked, sentences = park_units(args.record)
            out.extend("loom: " + s for s in sentences)
            if not parked:
                out.append("loom: nothing was parked: no piece of this work "
                           "names one of the six risk classes")
            code = 0
        elif args.command == "show":
            code = _show(args.run, out)
        else:
            code = _answer(args, out)
    except (OSError, ValueError) as exc:
        out.append("%s: %s could not be read or written: %s"
                   % (NODATA, args.command, exc))
        code = 2
    print("\n".join(out))
    return code


if __name__ == "__main__":
    sys.exit(main())
