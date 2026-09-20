#!/usr/bin/env python3
"""jev_registry: the one place this estate reads data/jev-registry.json,
the 116-entry catalogue of every place Jev (TypeSafe's typed decision
model) is or could be wired into the Brother toolkit.

WHY THIS EXISTS. jev_decide.py (JEV-01) already refuses to call Jev for a
malformed question, but it has no idea which of the registry's 116 use
cases are even allowed to reach it: a MUST_NOT row exists in the
catalogue precisely so a caller can name "this decision is documented,
considered, and Jev is never the one who answers it" without deleting the
row or re-deriving that refusal from scratch at every call site. A
must_stay_on_machine row carries the same shape of refusal for a
different reason (privacy, not judgment). This module is the one place
that refusal is decided, so no caller of Jev has to re-implement the
MUST_NOT/must_stay_on_machine check, or reformat a registry entry into
the {type, instructions, criteria} shape jev_decide.decide() expects, on
its own.

THE FOUR FUNCTIONS.

  load(path)                 the whole registry (a list of entry dicts),
                              or raises RegistryError naming exactly what
                              is wrong. Never returns a partially parsed
                              list: a file that fails validation on entry
                              80 of 116 raises before entry 1 is handed to
                              any caller.
  get(registry, entry_id)    the one entry with that id, or raises
                              RegistryError (a KeyError-shaped refusal:
                              the id was not found, nothing else is
                              wrong).
  lint(registry)              a list of Finding objects, one per problem
                              found; an empty list means the registry is
                              clean. Never raises for a structurally valid
                              registry (one load() already accepted):
                              lint reports free-text problems, it does not
                              stop the machine the way load() does.
  callable(registry, id)      (True, question_dict) when Jev may be asked
                              this question, else (False, reason). Shadows
                              the builtin `callable` deliberately: this is
                              the name the calling brief specifies, and
                              nothing in this module needs the builtin.
  both_orders(question)       [question] or [question, reversed_copy]:
                              see its own docstring.

ONE SOURCE OF TRUTH FOR "THIS ENTRY IS NOT FIT TO ASK". _entry_findings()
is the single function that decides whether one entry has a structural
problem (bad type, a choice/score with no option named "unknown", too few
options, two options with the same name, a missing wave, a
must_stay_on_machine entry with the wrong role, a duplicate id). lint()
calls it once per entry to build the full report; callable() calls it on
the one entry it resolved and refuses the moment the list is non-empty,
naming every finding in its reason. There is no second copy of these
rules anywhere in this module: a rule added to _entry_findings is
enforced by both functions the same day, never patched into one and
forgotten in the other.

THE MUST_NOT / must_stay_on_machine REFUSAL IS LOCAL AND FREE. callable()
decides an unknown id, a MUST_NOT entry, and a must_stay_on_machine entry
before it even computes _entry_findings, and this whole module never
imports subprocess, urllib, or any network-capable stdlib module: there
is no call left to make by accident, whatever a caller does with the
(False, reason) it gets back.

CONTINGENCY, edge by edge.

  empty registry file (`[]`)       load() succeeds with an empty list;
                                    lint() on it reports no findings (an
                                    empty catalogue has no bad entries);
                                    get()/callable() report "unknown id"
                                    for anything asked of it.
  one malformed entry among many   load() raises before returning
                                    anything: see "never returns a
                                    partially parsed list" above.
  duplicate id IN A FILE            load() refuses the whole file: a
                                    caller can never silently keep the
                                    first or the last of two entries that
                                    claim the same id, the ambiguity is
                                    refused outright.
  duplicate id in an in-memory      lint() reports it on every occurrence
  registry (never built by load())  sharing the id, not only the second;
                                    get()/callable() still resolve the
                                    FIRST match, but callable() then
                                    refuses it anyway, since that entry's
                                    own findings include "duplicate-id".
  id with different case or         never matched: get()/callable() use
  incidental whitespace             exact string equality only, so
                                    "J025 " or "j025" is an unknown id
                                    like any other typo, never silently
                                    normalised to "J025".
  unknown id                       get() raises RegistryError; callable()
                                    returns (False, reason), never raises.
  MUST_NOT entry                   callable() refuses unconditionally,
                                    whatever its question type or privacy
                                    tag says; lint() does not require a
                                    MUST_NOT entry to carry an "unknown"
                                    option, a wave, or a minimum option
                                    count, since it is never actually
                                    asked.
  must_stay_on_machine entry whose lint() flags it (a privacy tag this
  role is not MUST_NOT             strict with no matching refusal is a
                                    registry bug, not a caller's problem
                                    to catch at call time); callable()
                                    still refuses it regardless of what
                                    lint says, since the refusal is by
                                    privacy tag, not by lint's opinion.
  a noul question                  never flagged for a missing "unknown"
                                    option: a noul answer is a
                                    probability read through an abstain
                                    band (0.2-0.8), it has no option list
                                    to abstain into the way choice/score
                                    do.
  an option given as {"Unknown":   matched by its NAME (the dict's own
  "a description that never says   key), never by its description: a
  the word"}                       description mentioning "unknown" does
                                    not make some other option the
                                    catch-all, and a name that says
                                    "Unknown" is the catch-all whatever
                                    its description says.
  a choice question with 0 or 1    lint finding ("too-few-options"): a
  options                          choice needs at least two options to
                                    mean anything; a noul/score question
                                    is not checked here, this rule is
                                    choice-specific per the calling
                                    brief.
  two options with the same name   lint finding ("duplicate-option-names"),
  (case-insensitive) on a          for choice or score: a duplicate name
  choice or score question         would silently collapse in the
                                    criteria dict/list this module builds
                                    for jev_decide, so it is refused
                                    before that can happen.
  both_orders on a score/noul      returns [question] unchanged: only a
  question                         choice question has an option order
                                    for a caller to worry about.

Python 3.9 floor, standard library only, no network, no subprocess.
"""
import argparse
import json
import os
import sys
from dataclasses import dataclass

#: The wire format's three question types (mirrors jev_decide.QUESTION_TYPES;
#: kept as this module's own tuple rather than importing jev_decide, since
#: this module has no other reason to depend on it).
QUESTION_TYPES = ("noul", "choice", "score")

#: The one role value that means "Jev is documented here and never called".
MUST_NOT = "MUST_NOT"

#: The one privacy value that means "never leaves this machine".
MUST_STAY_ON_MACHINE = "must_stay_on_machine"

#: The complete set of privacy values a registry entry may carry. A typo
#: (e.g. "publc_or_own_text") or a missing "privacy" field is exactly as
#: dangerous as no refusal at all: callable()'s must_stay_on_machine gate
#: only fires on an EXACT match of MUST_STAY_ON_MACHINE, so any other
#: spelling of that value would silently be treated as callable. lint()
#: refuses every value outside this set, and callable() gates on lint, so
#: an unrecognised or absent privacy tag is never callable either.
ALLOWED_PRIVACY_VALUES = frozenset(["needs_content_gate", "public_or_own_text", MUST_STAY_ON_MACHINE])

#: CLI exit codes: 0 clean, 1 findings, 2 the file could not even be read.
EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_UNREADABLE = 2


class RegistryError(Exception):
    """A registry file could not be loaded, or an id does not name an
    entry in it. One class for both: both are "this reference is no
    good", the caller does not need to distinguish them to know a
    registry lookup did not produce an entry."""


@dataclass(frozen=True)
class Finding:
    """One lint problem: which entry, which rule, and a human sentence.
    Frozen and comparable so a test can assert one is `in` a findings list
    without formatting it first."""
    entry_id: str
    rule: str
    message: str

    def __str__(self):
        return "%s [%s]: %s" % (self.entry_id, self.rule, self.message)


def _option_name(option):
    """The matchable, lookup-able name of one option entry: the string
    itself for the registry's normal shape (a bare option name), or the
    single key of a {name: description} mapping. Never the description
    text of a dict option: a description that happens to mention a word
    is not that word being the option's name."""
    if isinstance(option, str):
        return option
    if isinstance(option, dict) and option:
        return next(iter(option))
    return str(option)


def _option_description(option):
    """The text jev_decide.decide() should show for one option: a dict
    option's own mapped description when it is a non-empty string, else
    the option's own name doubles as its description (there is nothing
    else to draw from)."""
    name = _option_name(option)
    if isinstance(option, dict):
        described = option.get(name)
        if isinstance(described, str) and described.strip():
            return described
    return name


def load(path):
    """The registry at `path` as a list of entry dicts, or raises
    RegistryError naming exactly what is wrong. Validates every entry
    before returning any of them: a file that is valid JSON but not a
    list, whose Nth entry has no usable id or question, or that names the
    same id twice, is refused whole, never handed back with the bad
    entries silently included and never resolved by keeping the first or
    the last of a duplicate pair."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw_text = handle.read()
    except OSError as exc:
        raise RegistryError("could not read %s: %s" % (path, exc))

    try:
        data = json.loads(raw_text)
    except ValueError as exc:
        raise RegistryError("%s is not valid JSON: %s" % (path, exc))

    if not isinstance(data, list):
        raise RegistryError(
            "%s must contain a JSON array of entries, got %s"
            % (path, type(data).__name__)
        )

    seen_ids = set()
    for index, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise RegistryError("entry %d in %s is not an object" % (index, path))
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id.strip():
            raise RegistryError("entry %d in %s has no non-empty 'id'" % (index, path))
        if entry_id in seen_ids:
            raise RegistryError(
                "%s has the id %r more than once; entries can never share "
                "an id, load() keeps neither the first nor the last"
                % (path, entry_id)
            )
        seen_ids.add(entry_id)
        question = entry.get("question")
        if not isinstance(question, dict) or not question.get("type"):
            raise RegistryError(
                "entry %r in %s has no usable 'question' object" % (entry_id, path)
            )

    return data


def get(registry, entry_id):
    """The first entry in `registry` whose id is `entry_id`, or raises
    RegistryError (KeyError-shaped: the id just was not found)."""
    for entry in registry:
        if entry.get("id") == entry_id:
            return entry
    raise RegistryError("unknown registry entry id: %r" % (entry_id,))


def _entry_findings(entry, registry):
    """Every problem with exactly one entry: the single source of truth
    both lint() and callable() read from, so a rule lives here once,
    never copied into a second check inside callable(). `registry` is the
    whole list the entry came from; it is needed only to tell whether
    this entry's own id is duplicated elsewhere in it (an entry cannot
    know that by looking at itself alone). Never raises.

    Checked for every entry, regardless of role:
      - a duplicate id (flagged on EVERY entry that shares it, not just
        the second one: whichever of them a caller happens to resolve is
        equally unsafe to trust)
      - a must_stay_on_machine entry whose role is not MUST_NOT
      - a privacy value outside ALLOWED_PRIVACY_VALUES, including a
        missing "privacy" field (M2: a typo or an absent tag on a
        must_stay_on_machine-intended entry must never read as callable)
      - a "call_site_reads_answer" field that is present but not a
        Python bool (BLOCK review, round 6): a data typo, named as a
        type error, distinct from the advise-act-never-read cross-check
        lint() runs separately against a seams config

    Checked only when role is not MUST_NOT (a MUST_NOT entry is never
    actually asked, so these are not this function's problem for it):
      - a question type outside noul/choice/score
      - a choice/score question with no option NAME (case-insensitive;
        never an option's description) containing "unknown"
      - a choice question with fewer than two options
      - a choice/score question with two options sharing the same name
        (case-insensitive)
      - no wave (missing key, null, or blank)
    """
    findings = []
    entry_id = entry.get("id", "<no id>")
    role = entry.get("role")
    question = entry.get("question") or {}
    qtype = question.get("type")

    if sum(1 for other in registry if other.get("id") == entry_id) > 1:
        findings.append(Finding(
            entry_id, "duplicate-id",
            "this id appears more than once in the registry",
        ))

    if role != MUST_NOT:
        if qtype not in QUESTION_TYPES:
            findings.append(Finding(
                entry_id, "bad-question-type",
                "question type %r is outside noul/choice/score" % (qtype,),
            ))

        if qtype in ("choice", "score"):
            options = question.get("options") or []
            names = [_option_name(opt).strip().lower() for opt in options]
            if not any("unknown" in name for name in names):
                findings.append(Finding(
                    entry_id, "missing-unknown-option",
                    "%s question has no option NAME containing 'unknown'" % (qtype,),
                ))
            if len(names) != len(set(names)):
                findings.append(Finding(
                    entry_id, "duplicate-option-names",
                    "%s question has two or more options with the same name" % (qtype,),
                ))

        if qtype == "choice":
            option_count = len(question.get("options") or [])
            if option_count < 2:
                findings.append(Finding(
                    entry_id, "too-few-options",
                    "choice question has %d option(s), needs at least 2" % (option_count,),
                ))

        if not entry.get("wave"):
            findings.append(Finding(
                entry_id, "missing-wave",
                "non-MUST_NOT entry has no wave",
            ))

    if entry.get("privacy") == MUST_STAY_ON_MACHINE and role != MUST_NOT:
        findings.append(Finding(
            entry_id, "machine-only-not-must-not",
            "must_stay_on_machine entry has role %r, must be MUST_NOT" % (role,),
        ))

    privacy = entry.get("privacy")
    if privacy not in ALLOWED_PRIVACY_VALUES:
        if privacy is None:
            findings.append(Finding(
                entry_id, "missing-privacy",
                "entry has no 'privacy' field",
            ))
        else:
            findings.append(Finding(
                entry_id, "bad-privacy-value",
                "privacy value %r is outside %s" % (privacy, sorted(ALLOWED_PRIVACY_VALUES)),
            ))

    # BLOCK review, round 6: a "call_site_reads_answer" field that IS
    # PRESENT but is not a Python bool (a string "false", a 0, a
    # null-as-string) is a data typo, not a deliberate declaration
    # either way, and must be named for exactly that -- a type error --
    # rather than silently reinterpreted as either "reads" or "does not
    # read" by whatever comparison happens to look at it next. This is
    # a distinct, unconditional finding (no seams_config needed, same as
    # every other check in this function): it fires whether or not the
    # entry is ever configured to advise or act. A MISSING field is not
    # this finding, and is not the advise-act-never-read finding below
    # either -- every entry except the eight G1 call sites omits the
    # field entirely, and omission has never been a problem either check
    # in this module (or jev_seam's matching runtime guard) has an
    # opinion about; only a field that is PRESENT gets scrutinized.
    if "call_site_reads_answer" in entry:
        reads_answer = entry["call_site_reads_answer"]
        if not isinstance(reads_answer, bool):
            findings.append(Finding(
                entry_id, "call-site-reads-answer-not-bool",
                "call_site_reads_answer is %r (a %s), not a bool: this field "
                "only ever means anything as the literal True or False, any "
                "other value is a data typo that a loose comparison could "
                "silently misread as either" % (reads_answer, type(reads_answer).__name__),
            ))

    return findings


#: The seams config mode values under which a call site's return value
#: actually changes what happens (item 7, Muse G1 point 5 / A0.8 round
#: 6): "shadow" only ever records a second opinion for calibration, the
#: caller's own answer is used regardless; "advise" and "act" are the
#: two modes where a caller reading (or, for act, the cascade routing on)
#: the answer is the whole point of the mode. An entry whose call site
#: is present but not PROVEN to read the answer (missing is unaffected;
#: see ADVISE_ACT_REQUIRES_READ below)
#: cannot mean anything different in advise or act than it already does
#: in shadow, so configuring either is not enforcement, it just looks
#: like it.
ADVISE_ACT_REQUIRES_READ = ("advise", "act")


def lint(registry, seams_config=None):
    """A list of Finding objects, one per problem in `registry`: every
    entry's own _entry_findings(), concatenated in registry order, plus
    (when `seams_config` is given) one more check across registry and
    config together. Never raises on a structurally valid registry; an
    empty return means clean.

    `seams_config` (item 7, Muse G1 point 5 / A0.8 round 6, hardened in
    the BLOCK review of round 6): the parsed data/jev-seams.json dict
    (or an equivalent {"modes": {...}} mapping), or None to skip this
    check entirely -- a registry can always be linted on its own, with
    no opinion about what any seams file currently says. When given,
    any entry whose "call_site_reads_answer" is PRESENT but is anything
    other than the literal Python bool True -- an explicit False, or a
    non-bool value such as the string "false" or 0 -- whose configured
    mode is "advise" or "act" is a finding: lint() is the one place a
    seams file and the registry it configures are checked against each
    other, so an operator cannot flip a mode to advise or act and
    believe it is now enforcement when the call site that owns it was
    never proven to read the answer at all. The check is
    "present and is not True" rather than "is False" so a one-character
    data typo (a string "false", a stray 0) cannot make this check
    silently pass; a present-but-non-bool value also gets its own
    distinct "call-site-reads-answer-not-bool" finding from
    _entry_findings(), named for what it actually is (a type error)
    rather than folded into this one. The field being MISSING entirely
    stays exactly as it always was -- every entry except the eight G1
    ones omits it, and this check has never had an opinion about that
    (widening it to cover absence too was tried and reverted: it made
    every other advise/act entry in this codebase's own test suite
    uncallable, since none of them ever set this field either). A
    missing or non-dict "modes" mapping is read as every entry off, the
    same safe default _resolve_mode() itself falls back to -- never a
    finding of its own here, lint() is not the tool that validates the
    seams file's own shape."""
    findings = []
    for entry in registry:
        findings.extend(_entry_findings(entry, registry))
    if isinstance(seams_config, dict):
        modes = seams_config.get("modes")
        modes = modes if isinstance(modes, dict) else {}
        for entry in registry:
            entry_id = entry.get("id", "<no id>")
            mode = modes.get(entry_id)
            reads_answer_present = "call_site_reads_answer" in entry
            reads_answer = entry.get("call_site_reads_answer")
            if reads_answer_present and reads_answer is not True and mode in ADVISE_ACT_REQUIRES_READ:
                findings.append(Finding(
                    entry_id, "advise-act-never-read",
                    "configured mode %r cannot be enforcement: this entry's call site "
                    "is not proven to read consult()'s return value "
                    "(call_site_reads_answer is %r, not the bool True), so advise/act "
                    "behaves exactly like shadow while looking like something "
                    "stronger" % (mode, reads_answer),
                ))
    return findings


def callable(registry, entry_id):  # noqa: A001 (shadows builtin by the calling brief's own contract)
    """(True, question_dict) when `entry_id` names an entry Jev may be
    asked, else (False, reason). Never raises.

    Refuses, WITHOUT any network or subprocess call (this module imports
    neither subprocess nor urllib, so there is nothing to call even by
    accident): an unknown id, a MUST_NOT entry, a must_stay_on_machine
    entry, and ANY entry with a lint finding at all (see
    _entry_findings(), the one function both lint() and this share), in
    that order, before anything else is inspected. A clean-looking entry
    with, say, a duplicate id or two options sharing a name is refused
    here exactly as it would be flagged by lint(): there is no second,
    looser copy of these rules for callable() to fall through to.

    On success, `question_dict` is exactly the shape jev_decide.decide()
    expects: {"type", "instructions", "criteria"}. criteria is a dict
    option-name -> description for choice (a dict-shaped option's own
    description when it has one, else the option's own name, since a
    bare-string option carries no separate per-option text), the ordered
    list of option names for score, and {"true": instructions, "false":
    instructions} for noul (a noul question has no per-option text to
    draw from either; both keys carry the same instructions, which is
    what the abstain-band framing already asks the model to weigh for and
    against).
    """
    try:
        entry = get(registry, entry_id)
    except RegistryError as exc:
        return False, str(exc)

    role = entry.get("role")
    if role == MUST_NOT:
        return False, "entry %r is MUST_NOT: Jev is never called for it" % (entry_id,)

    if entry.get("privacy") == MUST_STAY_ON_MACHINE:
        return False, (
            "entry %r must stay on machine: no outside call is permitted" % (entry_id,)
        )

    findings = _entry_findings(entry, registry)
    if findings:
        return False, (
            "entry %r has lint findings, Jev is never called for a dirty "
            "entry: %s" % (entry_id, "; ".join(str(finding) for finding in findings))
        )

    question = entry.get("question") or {}
    qtype = question.get("type")
    instructions = question.get("instructions")
    # qtype is already guaranteed to be in QUESTION_TYPES by the findings
    # gate above for any non-MUST_NOT entry (a bad type is a finding);
    # this is a defensive second check, not the primary control.
    if qtype not in QUESTION_TYPES:
        return False, "entry %r has unrecognised question type %r" % (entry_id, qtype)
    if not isinstance(instructions, str) or not instructions.strip():
        return False, "entry %r has no usable instructions" % (entry_id,)

    options = question.get("options") or []
    if qtype == "choice":
        criteria = {_option_name(opt): _option_description(opt) for opt in options}
    elif qtype == "score":
        criteria = [_option_name(opt) for opt in options]
    else:  # noul
        criteria = {"true": instructions, "false": instructions}

    return True, {"type": qtype, "instructions": instructions, "criteria": criteria}


def both_orders(question):
    """[question] for a noul or score question (no option order to flip);
    [question, reversed_copy] for a choice question, where reversed_copy
    carries the same options with their dict order reversed, so a caller
    near a threshold can ask both orders rather than trust whichever order
    the registry happened to list first. Never mutates `question`."""
    if question.get("type") != "choice":
        return [question]

    criteria = question.get("criteria") or {}
    reversed_criteria = dict(reversed(list(criteria.items())))
    reversed_question = dict(question)
    reversed_question["criteria"] = reversed_criteria
    return [question, reversed_question]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Load and lint the Jev use-case registry."
    )
    parser.add_argument("command", choices=["lint"])
    parser.add_argument("path", help="path to jev-registry.json")
    args = parser.parse_args(argv)

    try:
        registry = load(args.path)
    except RegistryError as exc:
        print("jev-registry: NO-DATA, %s" % exc, file=sys.stderr)
        return EXIT_UNREADABLE

    # item 7 (Muse G1 point 5 / A0.8 round 6): the advise/act-vs-
    # call_site_reads_answer check needs the seams config too, but the
    # CLI's own pinned invocation (`lint <path-to-registry>`) takes no
    # second path argument. The seams file is a conventional SIBLING of
    # the registry file (both live in data/, exactly as
    # DEFAULT_SEAMS_CONFIG_PATH/DEFAULT_REGISTRY_PATH do in jev_seam.py),
    # so it is found the same way rather than adding a flag. Missing or
    # unreadable is never a hard failure here: lint() already reads a
    # None/non-dict seams_config as "skip this one check", the same safe
    # default every other Jev config read in this estate falls back to.
    seams_path = os.path.join(os.path.dirname(os.path.abspath(args.path)), "jev-seams.json")
    seams_config = None
    if os.path.isfile(seams_path):
        try:
            with open(seams_path, "r", encoding="utf-8") as fh:
                seams_config = json.load(fh)
        except (OSError, ValueError):
            seams_config = None

    findings = lint(registry, seams_config=seams_config)
    for finding in findings:
        print(finding)
    return EXIT_FINDINGS if findings else EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())
