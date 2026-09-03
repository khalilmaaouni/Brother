#!/usr/bin/env python3
"""BrotherSBE intake: five objective questions in, one tier out.

The tier decides how much dossier a task gets, which is the mechanism behind
"brief always": a one line fix produces nothing, a new system produces the full
set. The rule is a decision table, not a judgment, so two engineers classifying
the same task land on the same tier.

Every answer is read for MEANING, never for truthiness. This function used to
test `a.get("touches_sensitive")` directly, and the tool's own prompts teach the
answers "y" and "n", so an intake written in the vocabulary this file asks for
computed the wrong tier in both directions at once: five answers of "n" computed
T3, because the string "n" is truthy, and `consumers: "several"` computed T0,
because it matched neither "some" nor "many" and fell through to the lowest tier.
The first blocks honest work at maximum ceremony, which is how a gate gets
switched off; the second silently decides that a change owes no evidence at all,
and the re-derivation in sbe_design.py then agrees with it, because it recomputes
the same wrong answer from the same unread strings.

So the vocabulary is explicit, it is shared (sbe_checks.boolean_answer, beside
VACUOUS_VALUES, imported by everything), and a value outside it is REFUSED by
name rather than guessed at, exactly as sbe_decide.py reports an unrecognized
criterion value instead of ignoring it.
"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sbe_checks import answered, boolean_answer, BOOLEAN_VOCABULARY

QUESTIONS = [
    ("changes_contract", "Does this change a data model, an API contract, or a file interface others depend on? "
                         "(no/additive/breaking; additive means nothing that exists today has to change) "),
    ("crosses_boundary", "Does it cross a service, system, or team boundary? (y/n) "),
    ("reversible_under_hour", "Is it reversible in under an hour? (y/n) "),
    ("touches_sensitive", "Does it touch money, partner data, personal data, or production state? (y/n) "),
    ("consumers", "How many downstream consumers break if it is wrong? (none/some/many) "),
]

TIERS = ("T0", "T1", "T2", "T3")
# The two questions that are not yes/no. Each vocabulary is named once so the
# prompt, the refusal message and the rule all read from the same tuple.
CONSUMERS = "consumers"
CONSUMER_VALUES = ("none", "some", "many")

#: The contract question, widened 2026-08-15 because a yes on it alone forced
#: T2 and six design documents, and on the pilot team's architecture (a field
#: added in the CMS, read by the backend, returned through the API, displayed
#: by the client) that yes is nearly impossible to avoid. The team's report
#: named this the most concrete finding in their review, and re-deriving it
#: here confirmed it: `changes_contract` alone reached T2 with every other
#: answer at its lowest value. Ceremony was following the SHAPE of the answer
#: rather than the blast radius of the change.
#:
#: The split is the smallest thing that fixes that. `breaking` means an
#: existing consumer must change; `additive` means nothing that exists today
#: has to. Additive lands at T1, which owes a purpose brief and a behaviour
#: table, not a seven-document dossier.
#:
#: BACKWARD COMPATIBILITY IS DELIBERATE AND ONE-DIRECTIONAL. A stored `true`
#: or `y` still reads as `breaking`, never as `additive`. Every dossier and
#: fixture written before this split therefore keeps the tier it was judged
#: at: this change can only lower the cost of a NEW answer somebody writes on
#: purpose, and can never silently de-inflate a dossier already on disk. The
#: opposite mapping would have re-tiered live dossiers behind their authors'
#: backs, which is the failure this comment exists to rule out.
CONTRACT = "changes_contract"
CONTRACT_VALUES = ("no", "additive", "breaking")

#: H4: a defect intake with no link to what it fixes is a fix nobody can
#: trace back to the behaviour that failed. `origin` is optional (a blank
#: block defaults to "feature", so every dossier written before this field
#: existed stays valid with no edit) but a `defect` origin owes a `fixes`
#: reference naming the regression row or behaviour it addresses; blank
#: there is refused, not guessed at, the same discipline `normalize_intent`
#: already applies to `desired_outcome`.
ORIGIN_VALUES = ("feature", "defect")


class UnreadableIntake(ValueError):
    """One or more answers the tier rule could not interpret, named one per line.

    Raised rather than defaulted. A tier is the size of the evidence a change
    owes, so guessing at an answer nobody can read is guessing at how much of
    this project's machinery applies, and the two guesses available (treat it as
    a yes, treat it as a no) are the two failures this class exists to prevent.
    """

    def __init__(self, problems):
        self.problems = list(problems)
        ValueError.__init__(self, "; ".join(self.problems))


def read_answers(a):
    """(values, problems): every answer as the meaning it records, or named as unreadable.

    `values` holds only the answers that were read. `problems` holds one sentence
    per answer that was not, naming the field, quoting the value received, and
    listing the vocabulary that would have been accepted, so a typo is
    distinguishable from an omission and from a lie.
    """
    values, problems = {}, []
    for key, _prompt in QUESTIONS:
        raw = (a or {}).get(key)
        if key == CONSUMERS:
            v = " ".join(str(raw).split()).casefold() if isinstance(raw, str) else raw
            if v in CONSUMER_VALUES:
                values[key] = v
            else:
                problems.append("%s=%r is not a recognized value (accepted: %s)"
                                % (key, raw, ", ".join(CONSUMER_VALUES)))
            continue
        if key == CONTRACT:
            v = " ".join(str(raw).split()).casefold() if isinstance(raw, str) else raw
            if v in CONTRACT_VALUES:
                values[key] = v
                continue
            # The pre-split vocabulary, still read, and read CONSERVATIVELY:
            # a yes becomes `breaking`, never `additive`, so no dossier
            # written before the split changes tier without somebody editing
            # it. A boolean that cannot be read falls through to the refusal
            # below carrying BOTH vocabularies, because a person who typed
            # "maybe" needs to see every word that would have been accepted.
            b = boolean_answer(raw)
            if b is not None:
                values[key] = "breaking" if b else "no"
            else:
                problems.append("%s=%r is not a recognized value (accepted: %s, or %s, or a JSON boolean; "
                                "a yes reads as breaking)"
                                % (key, raw, ", ".join(CONTRACT_VALUES), ", ".join(BOOLEAN_VOCABULARY)))
            continue
        b = boolean_answer(raw)
        if b is None:
            problems.append("%s=%r is not a recognized value (accepted: %s, or a JSON boolean)"
                            % (key, raw, ", ".join(BOOLEAN_VOCABULARY)))
        else:
            values[key] = b
    return values, problems


def compute_tier(a):
    """Named inputs, one output. Highest matching rule wins.

    Raises UnreadableIntake if any of the five answers is not in the accepted
    vocabulary. There is no lenient mode: the caller that wants to report the
    problem rather than raise calls read_answers() and prints the sentences.
    """
    v, problems = read_answers(a)
    if problems:
        raise UnreadableIntake(problems)
    if v["touches_sensitive"] or not v["reversible_under_hour"]:
        return "T3"
    if v[CONTRACT] == "breaking" or v[CONSUMERS] == "many":
        return "T2"
    if v[CONTRACT] == "additive" or v["crosses_boundary"] or v[CONSUMERS] == "some":
        return "T1"
    return "T0"


REQUIRED = {"T0": [], "T1": ["01", "08"], "T2": ["01", "02", "03", "05", "06", "07", "08"],
            "T3": ["01", "02", "03", "04", "05", "06", "07", "08"]}

#: The question budget's own three-way bucket, coarser than T0..T3 and named
#: for what a person reading the record cares about, not for the rule that
#: computed it. Stored as `budget_tier` on the record, never as `tier`:
#: `tier` already carries T0..T3 everywhere else this project reads it
#: (evals/run_evals.py, sbe_design.py and every fixture that seeds one), so
#: reusing that key for a different vocabulary would silently break every
#: one of those readers instead of adding a field. The mapping is exact,
#: not approximate: T3 IS "not reversible in under an hour, or touches
#: something sensitive", which is the definition of irreversible; T0 asks
#: no extra ceremony question at all, which is one-line-fix; T1 and T2 both
#: ask the same origin-and-intent questions and differ only in whether a
#: value hypothesis is also required, so both read as routine.
BUDGET_TIERS = ("one-line-fix", "routine", "irreversible")
_BUDGET_TIER_BY_TIER = {"T0": "one-line-fix", "T1": "routine", "T2": "routine", "T3": "irreversible"}


def budget_tier_for(tier):
    """The BUDGET_TIERS bucket a T0..T3 tier reads as. Refused, not
    guessed, for a tier outside TIERS: the same discipline required_artifacts
    already applies to an unrecognized tier."""
    if tier not in TIERS:
        raise ValueError("unknown tier %r (expected one of %s); a tier nothing recognizes "
                         "has no question budget bucket" % (tier, ", ".join(TIERS)))
    return _BUDGET_TIER_BY_TIER[tier]


def record_budget_tier(record):
    """The BUDGET_TIERS bucket a stored intake record reads as, or None if
    neither `budget_tier` nor the legacy `tier` field names one. Reads
    `budget_tier` first (a record written after this feature exists) and
    falls back to deriving it from `tier` (every record written before it),
    the same backward-compatibility discipline `normalize_origin` already
    applies to a blank origin block."""
    bt = record.get("budget_tier")
    if bt in BUDGET_TIERS:
        return bt
    return _BUDGET_TIER_BY_TIER.get(record.get("tier"))


def build_overrides(previous_intent, intent_result, change_reason):
    """Every stated assumption this run's answers overrode: a field
    `previous_intent` named that this run's answer disagrees with.

    Only reachable with a real entry when `change_reason` was given: a
    disagreement with no reason is FAIL in normalize_intent and never
    reaches here, so every entry this returns really was overridden on
    purpose, on record, with a reason -- never silently.
    """
    overrides = []
    previous_intent = previous_intent if isinstance(previous_intent, dict) else None
    if not previous_intent:
        return overrides
    for field in ("desired_outcome", "value_hypothesis"):
        prev = answered(previous_intent.get(field))
        new = intent_result.get(field)
        if prev and new and prev != new:
            overrides.append({"field": field, "previous": prev, "new": new,
                               "reason": change_reason})
    return overrides


def required_artifacts(tier):
    """The artifact numbers this tier owes. An unknown tier is refused, not zero.

    Returning [] for a tier outside TIERS made "this tier requires nothing" the
    default answer for a typo, and the one caller that stands between this
    function and a silent no-requirement is a single `if` in another file.
    """
    if tier not in REQUIRED:
        raise ValueError("unknown tier %r (expected one of %s); a tier nothing recognizes "
                         "requires nothing, which is the wrong default for a rule that "
                         "decides how much evidence a change owes" % (tier, ", ".join(TIERS)))
    return REQUIRED[tier]


#: The intent block's three fields, named once so the CLI prompts, the
#: normalization and the stored JSON all read from the same names.
INTENT_FIELDS = ("desired_outcome", "requested_by", "value_hypothesis")


def normalize_intent(intent, tier, previous=None):
    """The intent block for one intake, checked against `tier`: the desired
    outcome, who wants it, and the value hypothesis.

    Returns a dict carrying the three fields (each read through sbe_checks
    `answered()`, so a placeholder like "TBD" reads the same as blank), a
    `desired_outcome_inferred` flag, and a `verdict` in PASS, NO-DATA or FAIL
    -- the same three words every other check in this project reports, never
    a fourth.

    LOW-RISK WORK (T0) MAY INFER-AND-CONFIRM: at T0 neither field is
    required, and an inferred `desired_outcome` is carried through labeled
    `desired_outcome_inferred=True` rather than read as stated. This is the
    floor staying a floor: T0 owes no ceremony, so nothing here is required
    at T0, and requiring it anyway is exactly the inflation the floor exists
    to refuse.

    MATERIAL WORK (T1 and above) REQUIRES THE OUTCOME EXPLICIT: a blank or
    merely-inferred `desired_outcome` at T1+ is NO-DATA, naming the human on
    `requested_by` as who must supply it -- never a crash, never a silent
    default.

    HIGH-IMPACT WORK (T2, T3) ALSO REQUIRES THE VALUE HYPOTHESIS: a blank
    `value_hypothesis` at T2/T3 is NO-DATA the same way, naming the same
    human.

    A CHANGED REQUIREMENT IS NEVER SILENT: when `previous` (the intent block
    already on record) names a `desired_outcome` or `value_hypothesis` that
    disagrees with this one, and this one carries no `change_reason`, that is
    FAIL, not NO-DATA -- a requirement that moved without anyone saying why
    is a contradiction this tool holds, not an absence it merely lacks, and
    FAIL is the only verdict word that blocks: NO-DATA never blocks on its
    own, and never passes either.
    """
    if tier not in TIERS:
        raise ValueError("unknown tier %r (expected one of %s); intent cannot be checked "
                         "against a tier nothing recognizes" % (tier, ", ".join(TIERS)))
    intent = intent if isinstance(intent, dict) else {}
    previous = previous if isinstance(previous, dict) else None

    desired_outcome = answered(intent.get("desired_outcome"))
    requested_by = answered(intent.get("requested_by"))
    value_hypothesis = answered(intent.get("value_hypothesis"))
    inferred = bool(intent.get("desired_outcome_inferred")) and desired_outcome is not None
    change_reason = answered(intent.get("change_reason"))

    result = {
        "desired_outcome": desired_outcome,
        "desired_outcome_inferred": inferred,
        "requested_by": requested_by,
        "value_hypothesis": value_hypothesis,
    }

    if previous:
        prev_outcome = answered(previous.get("desired_outcome"))
        prev_hypothesis = answered(previous.get("value_hypothesis"))
        moved = ((prev_outcome and desired_outcome and prev_outcome != desired_outcome) or
                (prev_hypothesis and value_hypothesis and prev_hypothesis != value_hypothesis))
        if moved and not change_reason:
            result["verdict"] = "FAIL"
            result["problems"] = [
                "desired_outcome or value_hypothesis changed from the intent already on "
                "record (previous desired_outcome=%r, value_hypothesis=%r; now %r, %r) with "
                "no change_reason explaining the move: a requirement that changed silently "
                "is a contradiction, not an absence, and this tool will not overwrite one "
                "without a reason on record"
                % (prev_outcome, prev_hypothesis, desired_outcome, value_hypothesis)]
            return result

    problems = []
    who = requested_by or "no human was named on requested_by to ask"
    if tier != "T0" and (desired_outcome is None or inferred):
        problems.append(
            "no explicit desired_outcome on record for a %s (material) intake%s; %s must "
            "supply it before this tier's work proceeds"
            % (tier, " (the one on file is only inferred, not stated)" if inferred else "",
               who))
    if tier in ("T2", "T3") and value_hypothesis is None:
        problems.append(
            "no value_hypothesis on record for a %s (high-impact) intake; %s must supply "
            "why this change is expected to be worth it" % (tier, who))

    result["verdict"] = "NO-DATA" if problems else "PASS"
    result["problems"] = problems
    return result


def normalize_origin(origin):
    """The origin block for one intake: is this a feature or a defect, and
    if a defect, what does it fix.

    Returns a dict carrying `type` and `fixes`, and a `verdict` in PASS or
    NO-DATA -- never FAIL, because an origin block has no prior version to
    contradict the way `intent` does.

    A BLANK ORIGIN DEFAULTS TO "feature": every dossier written before this
    field existed carries no origin block at all, and reading that as a
    feature (never as a defect with nothing to fix) keeps every one of them
    valid with no edit, the same backward-compatibility discipline `read_answers`
    already applies to `changes_contract`.

    A DEFECT NAMES WHAT IT FIXES, ALWAYS: `type == "defect"` with a blank or
    merely-placeholder `fixes` is NO-DATA, naming exactly what is missing --
    never a crash, never a silently-accepted defect with no link to the
    behaviour that failed.
    """
    origin = origin if isinstance(origin, dict) else {}
    raw_type = origin.get("type")
    kind = " ".join(str(raw_type).split()).casefold() if isinstance(raw_type, str) else raw_type
    if kind not in ORIGIN_VALUES:
        kind = "feature"
    fixes = answered(origin.get("fixes"))

    result = {"type": kind, "fixes": fixes}
    problems = []
    if kind == "defect" and fixes is None:
        problems.append(
            "origin.type is \"defect\" but origin.fixes names no regression row or behaviour "
            "that failed; a defect intake must name what it fixes before this tier's work "
            "proceeds")
    result["verdict"] = "NO-DATA" if problems else "PASS"
    result["problems"] = problems
    return result


def ask_text(prompt, required=True):
    """One free-text question, re-asked until `answered()` accepts it (when
    required) or accepted blank (when not). Mirrors ask()'s EOF handling: a
    closed stdin is the same named failure path here too, never an unhandled
    traceback."""
    while True:
        try:
            raw = input(prompt)
        except EOFError:
            print("\nsbe_intake: stdin closed before this question was answered; nothing "
                  "was written.")
            sys.exit(1)
        value = answered(raw)
        if value is not None or not required:
            return value
        print("  that reads as no answer at all; say who or what, plainly.")


def ask(key, prompt):
    """One question, re-asked until the answer is in the accepted vocabulary.

    The prompt teaches y/n and the file used to accept anything, reading only
    whether the reply began with the letter y: "nope" was a no and so was
    "yes, but only in staging". A reply this tool cannot read is refused here,
    where the person who typed it is still sitting there, rather than written to
    00-intake.json for a gate to misread three commits later.
    """
    while True:
        try:
            raw = input(prompt)
        except EOFError:
            # Not swallowed: this is the named failure path for a closed stdin,
            # which used to end in an unhandled traceback that read as a broken
            # tool rather than as "nobody answered".
            print("\nsbe_intake: stdin closed before %s was answered; nothing was written." % key)
            sys.exit(1)
        values, problems = read_answers({key: raw})
        if key in values:
            return values[key]
        print("  %s" % next(p for p in problems if p.startswith(key + "=")))


USAGE = """usage: sbe_intake.py [DIRECTORY]
       sbe_intake.py --budget RECORDS_ROOT

Asks the five intake questions and writes 00-intake.json into DIRECTORY
(default: the current directory).

Give it the dossier directory. This tool used to take no argument at all: it
accepted one, ignored it, and wrote to wherever it was run from, and the README
shows it being run from a repository root. A stray 00-intake.json in a root is
its own dossier, and design/ below it is another, so the file lands where it
does not belong and the reader is told it worked.

--budget RECORDS_ROOT reports the question budget across every intake
record under RECORDS_ROOT instead of asking anything; see BUDGET_USAGE
below, printed by --budget itself when it is given the wrong number of
arguments."""


BUDGET_USAGE = """usage: sbe_intake.py --budget RECORDS_ROOT

Reads every 00-intake.json record found anywhere under RECORDS_ROOT and
prints, one line per budget tier (one-line-fix, routine, irreversible), how
many intakes landed there, how many ceremony questions they drew in total
(the origin and intent questions, the part of the intake that scales with
tier; the five fixed tier questions are not counted, because they never
vary), and how many stated assumptions their answers overrode. Then it
prints one FLAG line for every routine intake that drew more than one
question, naming the record.

A records root that is not a directory, or a directory with no
00-intake.json anywhere under it, prints NO-DATA and the reason instead of
a row of zeros: an empty report and an absent estate must never read the
same on screen."""


def run_budget_report(root):
    """Reads every 00-intake.json under `root` and prints the question
    budget report described in BUDGET_USAGE. Returns the process exit code:
    0 for a produced report, 3 for NO-DATA (the same code
    sbe_decision_record.py uses for the same word, for an absent or empty
    root)."""
    if not os.path.isdir(root):
        print("NO-DATA: %r is not a directory; no intake records to report on" % root)
        return 3
    paths = []
    for dirpath, _dirnames, filenames in os.walk(root):
        if "00-intake.json" in filenames:
            paths.append(os.path.join(dirpath, "00-intake.json"))
    paths.sort()
    if not paths:
        print("NO-DATA: no 00-intake.json records found under %r" % root)
        return 3

    totals = dict((t, {"intakes": 0, "questions": 0, "overrides": 0}) for t in BUDGET_TIERS)
    flags = []
    for path in paths:
        try:
            with open(path) as f:
                record = json.load(f) or {}
        except (ValueError, OSError) as exc:
            print("sbe_intake: %s could not be read (%s), skipped" % (path, exc))
            continue
        bt = record_budget_tier(record)
        if bt is None:
            print("sbe_intake: %s has no readable tier, skipped" % path)
            continue
        n_questions = len(record.get("questions_asked") or [])
        n_overrides = len(record.get("overrides") or [])
        totals[bt]["intakes"] += 1
        totals[bt]["questions"] += n_questions
        totals[bt]["overrides"] += n_overrides
        if bt == "routine" and n_questions > 1:
            flags.append((path, n_questions))

    for t in BUDGET_TIERS:
        s = totals[t]
        print("%s: %d intakes, %d questions, %d overrides"
              % (t, s["intakes"], s["questions"], s["overrides"]))
    for path, n in flags:
        print("FLAG: routine intake %s drew %d questions (more than one)" % (path, n))
    return 0


def main():
    argv = sys.argv[1:]
    if any(a in ("-h", "--help") for a in argv):
        print(USAGE)
        sys.exit(0)
    if argv and argv[0] == "--budget":
        rest = argv[1:]
        if len(rest) != 1:
            print("sbe_intake: --budget takes exactly one records root, got %d (%s).\n\n%s"
                  % (len(rest), ", ".join(rest), BUDGET_USAGE))
            sys.exit(2)
        sys.exit(run_budget_report(rest[0]))
    flags = [a for a in argv if a.startswith("-")]
    if flags:
        # A mistyped flag is a usage error, exit 2 to match the CLI's
        # documented table (0 ran, 1 a control failed, 2 usage).
        print("sbe_intake: %s is not an option.\n\n%s" % (", ".join(flags), USAGE))
        sys.exit(2)
    if len(argv) > 1:
        print("sbe_intake: one directory at a time, got %d (%s).\n\n%s"
              % (len(argv), ", ".join(argv), USAGE))
        sys.exit(1)
    where = argv[0] if argv else "."
    if not os.path.isdir(where):
        print("sbe_intake: %r is not a directory, so nothing was written. Create the dossier "
              "directory first, then run this in it.\n\n%s" % (where, USAGE))
        sys.exit(1)
    answers = {}
    for key, prompt in QUESTIONS:
        answers[key] = ask(key, prompt)
    tier = compute_tier(answers)

    # H4: feature-or-defect, asked beside the five tier questions rather than
    # gated by tier, because origin does not decide how much ceremony a
    # change owes (that stays the five questions' job); it decides whether a
    # fix carries a link to the behaviour that failed. A defect answer is
    # re-asked for `fixes` until answered, the same loop discipline `ask()`
    # and `ask_text()` already apply, so the CLI can only ever write a
    # defect with something named on `fixes`.
    # questions_asked is this run's own question budget: the ceremony that
    # scales with tier and with feature-vs-defect, never the five fixed tier
    # questions above (those are paid every time, so counting them would
    # not measure a budget, just the record count). Each entry is the exact
    # prompt text, plus, when this answer replaced a stated assumption
    # already on record, that assumption -- see build_overrides below.
    questions_asked = []
    origin_type = None
    while origin_type is None:
        raw_origin = ask_text("Is this a feature or a defect? (feature/defect) ")
        candidate = " ".join(raw_origin.split()).casefold()
        if candidate in ORIGIN_VALUES:
            origin_type = candidate
        else:
            print("  origin=%r is not a recognized value (accepted: %s)"
                  % (raw_origin, ", ".join(ORIGIN_VALUES)))
    questions_asked.append({"question": "Is this a feature or a defect? (feature/defect)",
                            "assumption_overridden": None})
    origin_answers = {"type": origin_type}
    if origin_type == "defect":
        origin_answers["fixes"] = ask_text(
            "What regression or behaviour row does this fix? ")
        questions_asked.append({"question": "What regression or behaviour row does this fix?",
                                "assumption_overridden": None})
    origin = normalize_origin(origin_answers)
    if origin["verdict"] == "NO-DATA":
        print("sbe_intake: %s\n\nNothing was written." % "; ".join(origin["problems"]))
        sys.exit(1)

    # The intent block reads whatever is already on record so a changed
    # desired_outcome or value_hypothesis is caught (normalize_intent's
    # FAIL, case 2) rather than silently overwritten. A prior file that does
    # not parse is reported and treated as no prior record, never a crash:
    # this run is about to replace it anyway.
    path = os.path.join(where, "00-intake.json")
    previous_intent = None
    previous_opened_at = None
    if os.path.exists(path):
        try:
            with open(path) as f:
                previous = json.load(f) or {}
            previous_intent = previous.get("intent")
            previous_opened_at = previous.get("openedAt")
        except ValueError as exc:
            print("sbe_intake: the existing %s does not parse (%s); this run has no prior "
                  "intent to compare against." % (path, exc))
            previous_intent = None

    # LOW-RISK (T0) STAYS LIGHTWEIGHT: zero extra prompts. Material tiers ask
    # who wants it and what outcome is desired; high-impact tiers also ask
    # why it is expected to be worth it. Every asked field is required
    # (ask_text loops until answered), so normalize_intent can only report
    # NO-DATA here for the one case CLI never reaches: a caller
    # supplying intent programmatically, not through this loop.
    intent_answers = {}
    if tier != "T0":
        intent_answers["requested_by"] = ask_text("Who wants this? (a named human) ")
        questions_asked.append({"question": "Who wants this? (a named human)",
                                "assumption_overridden": None})
        intent_answers["desired_outcome"] = ask_text("What outcome is desired? ")
        questions_asked.append({"question": "What outcome is desired?",
                                "assumption_overridden": None})
        if tier in ("T2", "T3"):
            intent_answers["value_hypothesis"] = ask_text(
                "What is the value hypothesis (why is this worth doing)? ")
            questions_asked.append({"question": "What is the value hypothesis "
                                    "(why is this worth doing)?",
                                    "assumption_overridden": None})
    intent = normalize_intent(intent_answers, tier, previous_intent)
    if intent["verdict"] == "FAIL":
        print("sbe_intake: %s\n\nNothing was written." % "; ".join(intent["problems"]))
        sys.exit(1)

    # By this point a moved field carries a change_reason on record (a move
    # with none is the FAIL above, and it already exited), so every entry
    # build_overrides returns here really was overridden on purpose. Fold
    # each one back into the matching question above, naming the assumption
    # it replaced, so a reader of questions_asked alone can see why that
    # question was not a rubber stamp of what was already on file.
    overrides = build_overrides(previous_intent, intent, intent_answers.get("change_reason"))
    overridden_by_field = dict((o["field"], o["previous"]) for o in overrides)
    for entry in questions_asked:
        if entry["question"] == "What outcome is desired?":
            entry["assumption_overridden"] = overridden_by_field.get("desired_outcome")
        elif entry["question"].startswith("What is the value hypothesis"):
            entry["assumption_overridden"] = overridden_by_field.get("value_hypothesis")

    # Both override fields are null, and null is the honest value here rather
    # than a placeholder: this writes the tier its own answers COMPUTE, so no
    # tier was moved, and a non-null override would claim a control nobody
    # exercised. Overriding is an edit to this file, and the design check FAILs
    # an override that sets one field and not the other, so the closing line
    # below says which two fields it takes rather than leaving it to be found.
    # H8: the earliest honest "started" moment for this dossier's median-
    # duration-per-tier report (brothersbe.decisions.close_durations_by_tier).
    # Stamped once and PRESERVED across a re-run: re-running this tool to
    # correct an answer must not move when the change was actually declared,
    # the same "do not silently overwrite" reasoning `previous_intent` above
    # already applies to the intent block.
    opened_at = previous_opened_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out = {"answers": answers, "tier": tier, "override": None, "override_reason": None,
           "openedAt": opened_at,
           "intent": {"desired_outcome": intent["desired_outcome"],
                      "desired_outcome_inferred": intent["desired_outcome_inferred"],
                      "requested_by": intent["requested_by"],
                      "value_hypothesis": intent["value_hypothesis"]},
           "origin": {"type": origin["type"], "fixes": origin["fixes"]},
           # The question budget (see run_budget_report / --budget): every
           # ceremony question this run actually asked, every stated
           # assumption a re-run's answers overrode, and the three-way
           # bucket (BUDGET_TIERS) `tier` reads as for that report.
           "questions_asked": questions_asked, "overrides": overrides,
           "budget_tier": budget_tier_for(tier)}
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print("tier %s (artifacts required: %s) written to %s"
          % (tier, ", ".join(required_artifacts(tier)) or "none", path))
    # All THREE edits, named. This instruction used to say "set BOTH override
    # and override_reason", and a reader who did exactly that FAILed the
    # design check with "the override field and the tier field disagree",
    # because the contract also requires moving the tier field itself, an
    # edit the instruction never mentioned: the tool's own printed teaching
    # led straight to a refusal.
    print("To override this tier, edit that file and set all three fields: \"tier\" (the tier you "
          "are moving to), \"override\" (the same tier, declaring the move), and \"override_reason\" "
          "(at least 3 words and 12 characters). A move with any of the three missing or "
          "disagreeing FAILs the design check as an edit rather than an override.")
    sys.exit(0)


if __name__ == "__main__":
    main()
