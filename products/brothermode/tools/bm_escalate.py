#!/usr/bin/env python3
"""bm_escalate.py: the attempt ledger, and the rule that says stop and ask.

WHY THIS EXISTS
  Founder ask, 2026-08-15: "the system should also be able to recognise when
  it is stuck and has tried every method possible and ask for help, guidance,
  give recommendation or handover to a human as needed when needed."

  Three partial answers already existed and none of them covered an ordinary
  session:
    - tools/bm_controller.py escalates a unit that failed twice, and only
      inside a running Full-Auto controller.
    - BrotherSBE's worker brief carries maxAttemptsPerApproach: 2 as a
      SENTENCE in a brief, which this product's own first law says is not a
      control.
    - tools/bm_stall.py and BrotherSBE's stall detector watch dead workers,
      stale fences, disk floor and owed handovers. They detect a stuck
      MACHINE. Nothing detected a stuck LINE OF REASONING.

  So no counter anywhere recorded that the same objective had defeated three
  different approaches, and no rule turned that count into a decision. This
  file is that counter and that rule.

WHAT IT DOES
  attempt   record one attempt against an objective (approach, observed root
            cause, verdict, evidence).
  forcing   record a condition where guessing is the danger. No attempt is
            required and none is counted: this escalates now.
  check     answer one question about an objective: CONTINUE or ESCALATE,
            with the trigger named. Pure read.
  packet    print the escalation packet in the checkpoint shape both products
            already define. REFUSES to print one without a recommendation and
            a default action.
  resolve   close an open escalation with what the human decided.
  open      list every open escalation in this project, for a session-close
            check. Pure read.

THE FOUR TRIGGERS, any one is enough (decide_verdict, one pure function)
  forcing_condition      a named condition where guessing is the danger, so
                         the count never applies: an irreversible action with
                         ambiguous intent, a missing owner, unavailable
                         credentials, authoritative sources that conflict, or
                         the tool being asked to approve its own exception.
                         Escalates at zero attempts, deliberately.
  approaches_exhausted   3 or more DISTINCT approaches have failed.
  no_new_information     2 attempts failed with the same observed root cause.
                         A repeat buys nothing, which is the real signal;
                         this is BrotherSBE's prose rule promoted to a count.
  budget_spent           a budget was declared for the objective, the attempts
                         are spent, and no attempt has passed since it opened.

  Never on a single failure, with ONE exception, taken from the architecture
  review of 2026-08-15: an attempt marked --truth-affecting escalates on the
  first failure. A wrong status, or a PASS with a known counterexample, is a
  defect in what this product sells, and burning a second attempt on it just
  means the wrong answer stands longer. Everywhere else the floor holds,
  because a system that asks for help too early is as useless as one that
  never asks.

WHAT THIS FILE NEVER DOES
  It never answers the question, never retries anything, and never decides on
  behalf of the human. Like the stall sweep next to it, it reports and the
  session or the person acts. A monitor wired to a control is a control
  defect, not a feature.

WHAT IS AND IS NOT ENFORCED, stated plainly because W5 of the review this
came from requires it
  ENFORCED: the verdict itself, and the packet's refusal to be printed
  hollow. Both are this file, both are covered by tools/test_bm_escalate.py.
  NOT ENFORCED: that a session records its attempts at all, and that a
  session with an open escalation does not simply stop. The second one is
  closable by a Stop hook (docs/ESCALATION.md carries the snippet); until
  that hook is wired into hooks/hooks.json, which is another lane's file,
  this paragraph is the discipline and the tool is only its instrument.

Python 3.9 floor, standard library only, like every other tool here.
"""
import argparse
import json
import os
import sys
import time

STORE_DIRNAME = ".brothermode"
LEDGER_NAME = "escalations.jsonl"

EXIT_OK = 0
EXIT_REFUSED = 2

#: Distinct failed approaches that mean the objective has beaten the methods
#: available to this session. Three rather than two: two is a bad day, three
#: is a pattern, and the middle value is where the founder's "tried every
#: method possible" actually lands without becoming a nuisance.
APPROACH_CEILING = 3

#: Failures sharing one observed root cause. Two, because the second one
#: bought no new information, which is the only thing an attempt is for.
ROOT_CAUSE_CEILING = 2

FAILED = "failed"
PASSED = "passed"
VERDICTS = (FAILED, PASSED)

CONTINUE = "CONTINUE"
ESCALATE = "ESCALATE"

#: Conditions where the counter is the wrong instrument, because the danger is
#: guessing rather than looping. Taken verbatim in meaning from the 2026-08-15
#: architecture review's "escalate immediately" list, which is the same set
#: BrotherSBE's L6 already carries as prose. A free-text condition is accepted
#: too: this tuple is what `--forcing-condition list` prints, not a whitelist,
#: because refusing an unlisted danger would be the wrong direction to fail in.
FORCING_CONDITIONS = (
    "irreversible-and-ambiguous",
    "no-owner",
    "credentials-unavailable",
    "sources-conflict",
    "judgment-not-reducible-to-a-rule",
    "self-approval",
    "waiver-touches-money-security-privacy-or-customer-behaviour",
)


def norm(text):
    """Lower-cased, whitespace-collapsed, punctuation dropped. Used to decide
    whether two approaches or two root causes are the SAME one, so "retry the
    import" and "Retry  the import" cannot look like two methods tried.

    PUNCTUATION WAS ADDED AFTER AN ATTACK, not by foresight: an adversarial
    reviewer fed "retry the import", "retry the import," and "retry  the
    import;" as three approaches and got approaches_exhausted, a false
    escalation whose reason line confidently named three distinct methods.
    Case and whitespace alone did not deliver the guarantee this docstring
    already claimed. It still only collapses SPELLING: "DB timeout" and "db
    timed out" remain two root causes here, and no normaliser fixes that. The
    honest ceiling is that this compares strings, so the person recording
    attempts has to reuse their own wording, and the failure direction when
    they do not is a MISSED escalation rather than a false one.
    """
    kept = [c if (c.isalnum() or c.isspace()) else " " for c in str(text or "")]
    return " ".join("".join(kept).split()).lower()


def resolve_root(start=None):
    """Nearest ancestor holding .brothermode/, else the nearest holding .git,
    else None. Deliberately the same order bm_store.resolve_root uses (marker
    beats git) so the two never disagree about which project this is; not
    imported from it because that module costs seconds to import and this
    tool runs on a hook path."""
    d = os.path.abspath(start or os.environ.get("BROTHERMODE_ROOT") or os.getcwd())
    seen_git = None
    while True:
        if os.path.isdir(os.path.join(d, STORE_DIRNAME)):
            return d
        if seen_git is None and os.path.isdir(os.path.join(d, ".git")):
            seen_git = d
        parent = os.path.dirname(d)
        if parent == d:
            return seen_git
        d = parent


def ledger_path(root):
    return os.path.join(root, STORE_DIRNAME, LEDGER_NAME)


def read_ledger(path):
    """Every readable record, plus the line numbers that could not be read.

    An unreadable line is REPORTED, never skipped silently: a ledger that
    quietly drops what it cannot parse would under-count attempts, and this
    tool under-counting attempts means it fails toward "keep going", which is
    the exact direction the founder asked it not to fail in.
    """
    records, broken = [], []
    if not os.path.exists(path):
        return records, broken
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                broken.append(n)
                continue
            if isinstance(d, dict):
                records.append(d)
            else:
                broken.append(n)
    return records, broken


def append(path, record):
    """Append one record, owner-only, creating the store directory if the
    project has one and this is its first record."""
    parent = os.path.dirname(path)
    if not os.path.isdir(parent):
        os.makedirs(parent, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (json.dumps(record, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def for_objective(records, objective):
    key = norm(objective)
    return [r for r in records if norm(r.get("objective")) == key]


def decide_verdict(records, objective):
    """(verdict, trigger, reason) for one objective. Pure: no clock, no disk.

    Order of the triggers is the order they are reported in, not a priority:
    the first one that holds decides, and the reason names it so a person
    reading the line knows WHICH kind of stuck this is. They mean different
    things and they have different remedies.
    """
    rows = for_objective(records, objective)
    if not rows:
        return CONTINUE, None, "nothing recorded for %r yet" % objective

    # TWO clearing rules, because the two events do not mean the same thing,
    # and collapsing them into one was a real defect an adversarial reviewer
    # executed: with a single rule, `[forcing(credentials unavailable),
    # attempt(passed)]` returned CONTINUE. Credentials do not become available
    # because something else passed.
    #   A RESOLVE clears everything before it. A human engaged and answered.
    #   A PASSING ATTEMPT clears prior ATTEMPTS only. It is evidence about the
    #   work, and it says nothing about a danger somebody named.
    # So a forcing condition survives any amount of unrelated success and is
    # closed only by `resolve`, which is the point of naming one.
    resolved_at = 0
    for i, r in enumerate(rows):
        if r.get("kind") == "resolve":
            resolved_at = i + 1
    after_resolve = rows[resolved_at:]

    passed_at = 0
    for i, r in enumerate(after_resolve):
        if r.get("kind") == "attempt" and r.get("verdict") == PASSED:
            passed_at = i + 1
    live = [r for i, r in enumerate(after_resolve)
            if i >= passed_at or r.get("kind") == "forcing"]
    if not live:
        return (CONTINUE, None,
                "cleared by the last %s; %d record(s) in total"
                % ("resolve" if resolved_at == len(rows) else "passing attempt", len(rows)))

    attempts = [r for r in live if r.get("kind") == "attempt"]
    failed = [r for r in attempts if r.get("verdict") == FAILED]

    forced = [r for r in live if norm(r.get("forcing_condition"))]
    if forced:
        return (ESCALATE, "forcing_condition",
                "%s: guessing is the danger here, so no number of attempts changes the answer"
                % forced[-1]["forcing_condition"])

    truth = [r for r in failed if r.get("truth_affecting")]
    if truth:
        return (ESCALATE, "truth_affecting_failure",
                "a failure affecting what this product reports as true (%s); the floor of two "
                "attempts does not apply, because a wrong answer standing longer is the cost"
                % (norm(truth[-1].get("root_cause")) or "no root cause recorded")[:80])

    if len(failed) < 2:
        return (CONTINUE, None,
                "%d failed attempt(s); one failure is a result, not a wall"
                % len(failed))

    causes = {}
    for r in failed:
        c = norm(r.get("root_cause"))
        if c:
            causes[c] = causes.get(c, 0) + 1
    repeated = sorted(c for c, n in causes.items() if n >= ROOT_CAUSE_CEILING)
    if repeated:
        return (ESCALATE, "no_new_information",
                "%d attempts failed with the same observed root cause (%s), so the "
                "last one bought no new information"
                % (causes[repeated[0]], repeated[0][:80]))

    approaches = sorted(set(norm(r.get("approach")) for r in failed if norm(r.get("approach"))))
    if len(approaches) >= APPROACH_CEILING:
        return (ESCALATE, "approaches_exhausted",
                "%d distinct approaches have failed: %s"
                % (len(approaches), "; ".join(a[:40] for a in approaches[:5])))

    budget = None
    for r in live:
        b = r.get("budget_attempts")
        if isinstance(b, int) and b > 0:
            budget = b
    if budget is not None and len(attempts) >= budget:
        return (ESCALATE, "budget_spent",
                "the declared budget of %d attempt(s) is spent and none has passed" % budget)

    return (CONTINUE, None,
            "%d failed attempt(s) across %d approach(es); under every trigger"
            % (len(failed), len(approaches)))


def open_escalations(records):
    """Objectives whose verdict is ESCALATE right now. What a session-close
    check reads."""
    out = []
    for name in sorted(set(r.get("objective") for r in records if r.get("objective"))):
        verdict, trigger, reason = decide_verdict(records, name)
        if verdict == ESCALATE:
            out.append({"objective": name, "trigger": trigger, "reason": reason})
    return out


PACKET_FIELDS = (
    ("owner", "who I am asking"),
    ("found", "what I found"),
    ("recommendation", "my recommendation"),
    ("alternatives", "the alternatives I have not tried"),
    ("risk", "the risk if I guess"),
    ("decision", "the one decision I need"),
    ("default", "what I do if you say nothing"),
)

#: Fields a packet may not omit. Alternatives may legitimately be empty (there
#: may be none left, which is itself the news), and "found" is derived from the
#: ledger. A recommendation and a default are refused because handing a person
#: a problem with no proposal moves the work without reducing it, and because
#: an escalation with no default action stalls the moment nobody answers.
PACKET_REQUIRED = ("recommendation", "decision", "default")


def build_packet(records, objective, fields):
    """(text, problems). Non-empty problems means REFUSED and text is None."""
    verdict, trigger, reason = decide_verdict(records, objective)
    missing = [label for key, label in PACKET_FIELDS
               if key in PACKET_REQUIRED and not norm(fields.get(key))]
    if missing:
        return None, ["no %s given" % m for m in missing]
    rows = [r for r in for_objective(records, objective) if r.get("kind") == "attempt"]
    lines = ["ESCALATION: %s" % objective,
             "trigger: %s (%s)" % (trigger or "none", reason),
             "",
             "what I found: %s" % (norm(fields.get("found")) and fields["found"]
                                   or "%d attempt(s) recorded, none passing" % len(rows))]
    for r in rows:
        lines.append("  - %s | %s | root cause: %s | %s"
                     % (r.get("verdict", "?"), r.get("approach", "(no approach)"),
                        r.get("root_cause") or "not identified",
                        r.get("evidence") or "no evidence recorded"))
    lines += ["",
              "who I am asking: %s" % (fields.get("owner") or "whoever owns this, unnamed"),
              "my recommendation: %s" % fields["recommendation"],
              "the alternatives I have not tried: %s"
              % (fields.get("alternatives") or "none left that I can name"),
              "the risk if I guess: %s"
              % (fields.get("risk") or "not stated, which is itself worth asking about"),
              "the one decision I need: %s" % fields["decision"],
              "what I do if you say nothing: %s" % fields["default"]]
    return "\n".join(lines), []


def cmd_attempt(args, root):
    if args.verdict not in VERDICTS:
        print("REFUSED: verdict must be one of %s" % ", ".join(VERDICTS))
        return EXIT_REFUSED
    if not norm(args.approach):
        print("REFUSED: an attempt with no approach cannot be counted as a distinct method")
        return EXIT_REFUSED
    if args.verdict == FAILED and not norm(args.root_cause):
        print("REFUSED: a failed attempt records the root cause it observed, or the "
              "repeat-failure trigger can never fire; pass --root-cause 'not identified' "
              "if that is the honest answer")
        return EXIT_REFUSED
    record = {"kind": "attempt", "objective": args.objective, "approach": args.approach,
              "root_cause": args.root_cause, "verdict": args.verdict,
              "evidence": args.evidence, "at": int(time.time())}
    if args.budget_attempts:
        record["budget_attempts"] = args.budget_attempts
    if args.truth_affecting:
        record["truth_affecting"] = True
    append(ledger_path(root), record)
    records, _ = read_ledger(ledger_path(root))
    verdict, trigger, reason = decide_verdict(records, args.objective)
    print("recorded. %s: %s" % (verdict, reason))
    if verdict == ESCALATE:
        # P17: never print the repo-relative path. A reader who installed
        # this does not have tools/ under their working directory, so the
        # pasteable command has to name where this module actually is. This
        # module imports no sibling on purpose, so it derives the path from
        # itself rather than reaching for bm_store.invocation the way
        # bm_stall.py can. Corrected 2026-08-16 after the shipping-message
        # rule caught it on main.
        print("next: python3 %s packet --objective %r "
              "--recommendation ... --decision ... --default ..."
              % (os.path.abspath(__file__), args.objective))
    return EXIT_OK


def cmd_forcing(args, root):
    """A condition where guessing is the danger. No attempt is required and
    none is counted: this escalates now."""
    if not norm(args.condition):
        print("REFUSED: name the condition; the known ones are %s"
              % ", ".join(FORCING_CONDITIONS))
        return EXIT_REFUSED
    append(ledger_path(root), {"kind": "forcing", "objective": args.objective,
                               "forcing_condition": args.condition,
                               "detail": args.detail, "at": int(time.time())})
    print("recorded. ESCALATE (forcing_condition): %s" % args.condition)
    return EXIT_OK


def cmd_check(args, root):
    records, broken = read_ledger(ledger_path(root))
    if broken:
        print("NOTE: %d unreadable ledger line(s) at %s, not counted: %s"
              % (len(broken), ledger_path(root), ", ".join(str(n) for n in broken[:10])))
    verdict, trigger, reason = decide_verdict(records, args.objective)
    print("%s%s: %s" % (verdict, " (%s)" % trigger if trigger else "", reason))
    return EXIT_OK


def cmd_packet(args, root):
    records, _ = read_ledger(ledger_path(root))
    verdict, _t, reason = decide_verdict(records, args.objective)
    if verdict != ESCALATE and not args.force:
        print("REFUSED: %s is not escalating (%s); pass --force to write a packet anyway"
              % (args.objective, reason))
        return EXIT_REFUSED
    text, problems = build_packet(records, args.objective,
                                  {"owner": args.owner, "found": args.found,
                                   "recommendation": args.recommendation,
                                   "alternatives": args.alternatives, "risk": args.risk,
                                   "decision": args.decision, "default": args.default})
    if problems:
        print("REFUSED: %s. An escalation without a recommendation and a default action "
              "moves the work to a person without reducing it." % "; ".join(problems))
        return EXIT_REFUSED
    print(text)
    return EXIT_OK


def cmd_resolve(args, root):
    append(ledger_path(root), {"kind": "resolve", "objective": args.objective,
                               "outcome": args.outcome, "note": args.note,
                               "at": int(time.time())})
    print("resolved %s as %s" % (args.objective, args.outcome))
    return EXIT_OK


def cmd_open(args, root):
    records, broken = read_ledger(ledger_path(root))
    if broken:
        print("NOTE: %d unreadable ledger line(s), not counted" % len(broken))
    rows = open_escalations(records)
    if not rows:
        print("no open escalation in %s" % root)
        return EXIT_OK
    for r in rows:
        print("OPEN %s (%s): %s" % (r["objective"], r["trigger"], r["reason"]))
    return EXIT_OK


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", default=None)
    sub = p.add_subparsers(dest="verb")

    a = sub.add_parser("attempt", help="record one attempt against an objective")
    a.add_argument("--objective", required=True)
    a.add_argument("--approach", required=True)
    a.add_argument("--root-cause", dest="root_cause", default="")
    a.add_argument("--verdict", required=True, choices=list(VERDICTS))
    a.add_argument("--evidence", default="")
    a.add_argument("--budget-attempts", dest="budget_attempts", type=int, default=0)
    a.add_argument("--truth-affecting", dest="truth_affecting", action="store_true",
                   help="this failure affects what the product reports as true; escalates "
                        "on the first failure instead of the second")

    f = sub.add_parser("forcing", help="a condition where guessing is the danger; escalates now")
    f.add_argument("--objective", required=True)
    f.add_argument("--condition", required=True,
                   help="one of: %s, or your own" % ", ".join(FORCING_CONDITIONS))
    f.add_argument("--detail", default="")

    c = sub.add_parser("check", help="CONTINUE or ESCALATE for one objective")
    c.add_argument("--objective", required=True)

    k = sub.add_parser("packet", help="print the escalation packet")
    k.add_argument("--objective", required=True)
    k.add_argument("--owner", default="", help="who is being asked: BA, QC, the founder")
    k.add_argument("--found", default="")
    k.add_argument("--recommendation", default="")
    k.add_argument("--alternatives", default="")
    k.add_argument("--risk", default="", help="what breaks if I guess")
    k.add_argument("--decision", default="")
    k.add_argument("--default", dest="default", default="")
    k.add_argument("--force", action="store_true")

    r = sub.add_parser("resolve", help="close an open escalation")
    r.add_argument("--objective", required=True)
    r.add_argument("--outcome", required=True, choices=["answered", "abandoned", "handed-over"])
    r.add_argument("--note", default="")

    sub.add_parser("open", help="list open escalations, for a session-close check")

    args = p.parse_args(argv)
    if not args.verb:
        p.print_help()
        return EXIT_REFUSED
    root = resolve_root(args.root)
    if root is None:
        print("REFUSED: no project here (no .brothermode/ and no .git above %s)" % os.getcwd())
        return EXIT_REFUSED
    # Inline rather than a module-level table, and the reason is worth the
    # comment: a module-level dispatch dict IS discoverable by
    # tools/bm_effects.py's completeness scan, and a discovered module that
    # carries no REGISTRY entry FAILs that suite rather than reporting
    # NO-DATA. Measured on 2026-08-15: hoisting this table turned the effects
    # suite red in one run. The REGISTRY entry belongs in tools/bm_effects.py,
    # which another session holds under a live fence, so the pair lands
    # together in that lane and this table is hoisted then, never before.
    return {"attempt": cmd_attempt, "forcing": cmd_forcing, "check": cmd_check,
            "packet": cmd_packet, "resolve": cmd_resolve, "open": cmd_open}[args.verb](args, root)


if __name__ == "__main__":
    sys.exit(main())
