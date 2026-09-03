"""Decide which read-only specialist reviewers, if any, should look at a
change, from the diff alone. No model chooses a reviewer here; nothing here
runs a review either. It only routes.

THE DEFECT THIS EXISTS FOR, in one sentence: a review workflow that always
convenes every specialist teaches nobody to trust it (a T0 docs fix waits on
seven agents that read nothing relevant), and a review workflow with no rule
at all lets a migration or a secret slip past because nobody happened to
mention it. This module is the deterministic middle: it looks at the same
detector hits `brothersbe.impact` already computes from a diff, adds a small
number of content-level detectors `impact` does not carry (a migration
hidden in a generic filename, SQL embedded in a host language, a secret-shaped
value outside a file `impact` would flag by name, a documentation sentence
that made a control promise and was removed, a test-only change that weakens
an assertion, evidence that fails its own verification), and turns the result
into at most two reviewer names, a tier, and named reasons.

THE ONE RULE THAT MAKES THIS SAFE: this module never invents a second tier
ladder. The tier it reports is exactly what `brothersbe.impact` already
computes (the human's declared tier when readable, the diff-derived tier as a
floor otherwise); none of the detectors added here feed into that
computation. A reviewer choice is never allowed to raise or lower the tier
that governs how much evidence a change owes -- it only decides who looks,
never how much is owed.

WHAT DETERMINISM MEANS HERE. `route()` reads the diff between two commits,
the intake file if one is discoverable, and the evidence store if one exists,
and nothing else: no clock, no random source, no model call. The same two
commits, on the same tree, produce the same route every time it is run on
this repository's own fixtures.

SELECTION, in the order the rules are tried (the FULL statement, priority
first): migration beats security beats data beats backend beats
infrastructure beats QA beats evidence, which is TRIGGER_RULES below and the
one place this order is written down. At most two reviewers are ever
selected, in priority order, and how many slots exist depends only on the
tier: zero triggers is always zero reviewers regardless of tier (this is what
makes a T0 documentation change mechanical-only, without a special case for
it); one trigger or more at T0/T1 gets one reviewer; T2/T3 gets up to two.
Every trigger that fired but lost its slot to a higher-priority one is named
in `unmeasured`, by trigger and by the reviewer it would have selected,
never silently dropped.

WHAT THIS NEVER CLAIMS. `mechanicalOnly: true` or `primaryReviewer: null`
never means "this change is clean" -- it means no trigger this module knows
how to detect fired. A file type nothing here can read is named in
`unmeasured` by name, never folded into a quiet pass. This module has no
verdict vocabulary of its own: `verdict` in its report is `ROUTED` (a route
was computed) or `NO-DATA` (there was no diff to read at all), and it is
never `PASS`, because a route is not a review and this module runs no review.
"""
import io
import json
import os
import re
import tempfile

from . import impact as impact_mod
from .impact import DiffUnavailable, _git  # noqa: E402 -- the same private import
                                            # evidence.py already makes from this module

from ._toolspath import mount
mount()
from sbe_telemetry import SECRET_PATTERNS  # noqa: E402

SCHEMA_VERSION = "1.0"

#: The seven reviewer agents this router may name, exactly the seven files
#: under `agents/`. Nothing here may name an eighth without that file
#: existing first.
REVIEWER_NAMES = (
    "backend-reviewer", "data-reviewer", "evidence-auditor", "migration-reviewer",
    "principal-architect", "qa-reviewer", "security-reviewer",
)

#: THE routing table. One row per rule, in priority order (lowest number
#: wins when more triggers fire than there are slots). Adding an eighth
#: reviewer is one row here plus fixtures, never an edit spread across the
#: detectors below: each detector only ever names a TRIGGER (the string in
#: column 2), and this table is the one place a trigger becomes a reviewer.
TRIGGER_RULES = (
    (1, "migration", "migration-reviewer",
     "a schema migration, backfill, or destructive data operation"),
    (2, "security", "security-reviewer",
     "a secret, authentication, authorization, payment, partner, or personal-data surface"),
    (3, "data", "data-reviewer",
     "a decision-reaching number, SQL, a dbt model, a warehouse, or a data pipeline"),
    (4, "backend", "backend-reviewer",
     "a service, API, queue, transaction, or other backend surface"),
    (5, "infrastructure", "principal-architect",
     "infrastructure, deployment, availability, or rollback"),
    (6, "qa", "qa-reviewer",
     "a test strategy change, or a significant test-only change"),
    (7, "evidence", "evidence-auditor",
     "stale, malformed, or disputed evidence"),
)
TRIGGER_PRIORITY = tuple(row[1] for row in TRIGGER_RULES)
REVIEWER_OF_TRIGGER = dict((row[1], row[2]) for row in TRIGGER_RULES)
TRIGGER_DESCRIPTION = dict((row[1], row[3]) for row in TRIGGER_RULES)
MAX_SPECIALISTS = 2

#: The two tiers this router treats as low-risk enough for the fast path:
#: small and reversible under `impact.compute_tier`'s own ladder. This is a
#: READING of the tier `impact` already computed, never a second ladder --
#: see the module docstring's "ONE RULE" paragraph.
FAST_PATH_TIERS = ("T0", "T1")

#: THE plain-language line for the low-risk fast path: tier T0 or T1 with
#: zero triggers fired (mechanicalOnly True already, by the existing tier
#: table above). Printed only in that one case, never touches tier,
#: verdict, or exit code. It answers a different question than the
#: "never claims a clean review" sentence below it: that sentence says this
#: router does not vouch for the CHANGE; this line says only that this
#: router's OWN rules found nothing that needs a second reader, which is
#: exactly what `mechanicalOnly: true` at T0/T1 has always meant -- this
#: only makes the plain-English reading of that fact visible instead of
#: requiring a reader to infer it from an absence of reasons.
#:
#: THE WORDS SAY ONLY WHAT THIS ROUTER READ, never what it did not: this
#: router never opens `.sbe/checks.yml` (`grep -c 'checks.yml' reviewroute.py`
#: is 0, on purpose -- reading the registry is out of this module's scope),
#: so it has no basis to say existing checks are sufficient, and an earlier
#: wording that said exactly that was a claim about the CHECKS dressed as a
#: claim about the ROUTE. This line names the routing decision only (no
#: trigger fired, so no specialist review is being routed) and names who
#: would actually know about check coverage, rather than answering on their
#: behalf.
FAST_PATH_LINE = (
    "LOW-RISK FAST PATH: tier %s, no reviewer trigger fired. This router is not "
    "routing a specialist review for this change; it did not read the check "
    "registry (.sbe/checks.yml) and takes no position on what it would find there "
    "-- the registry itself is the one place that question is answered.")

#: Every detector id `brothersbe.impact.DETECTORS` can produce, mapped to the
#: trigger it means for THIS module. This is a second reading of the same
#: hits `impact` already computed, never a second regex pass over the diff:
#: `impact.detect()` is called once, and this dict only re-labels what it
#: found. An `impact` detector id this dict does not know about is not
#: dropped: it is surfaced in `unmeasured` by name, because a detector
#: `impact` grows later and this router has no opinion about yet must read as
#: "we do not have a reviewer mapping for this," never as a silent PASS on
#: whatever it found.
IMPACT_DETECTOR_TRIGGER = {
    "openapi": "backend", "asyncapi": "backend", "protobuf": "backend", "avro": "backend",
    "graphql": "backend", "event-schema": "backend", "queue-config": "backend",
    "db-migration": "migration", "destructive-migration": "migration",
    "sql-ddl": "data", "dbt-model": "data", "orm-model": "data",
    "payment-path": "security", "partner-path": "security", "pii-path": "security",
    "pii-field": "security", "auth-path": "security", "secret-material": "security",
    "infrastructure": "infrastructure", "ci-pipeline": "infrastructure",
    "production-config": "infrastructure",
}

#: File extensions a plain host-language file carries, for the detectors below
#: that read CONTENT rather than a path shape `impact` already owns (a path
#: shape belongs in `impact.DETECTORS`, not duplicated here).
_CODE_EXT = re.compile(r"\.(py|rb|js|jsx|ts|tsx|go|java|kt|php|cs)$", re.I)
_CODE_OR_SQL_EXT = re.compile(r"\.(py|rb|js|jsx|ts|tsx|go|java|kt|php|cs|sql)$", re.I)
_DOC_EXT = (".md", ".rst", ".txt", ".adoc")

#: A migration hidden in a file `impact.DETECTORS` would never look inside
#: (its own `db-migration` detector only matches a migrations-shaped PATH,
#: and `destructive-migration` only matches drop/truncate/delete content).
#: This reads for the shape of a schema change in ANY host-language file:
#: raw DDL, an Alembic op call, a Django-style migration class, or an
#: up/down pair. Path-restricted deliberately to code-and-SQL extensions, so
#: a changelog that happens to say "we altered our plans" is not read as a
#: migration.
_MIGRATION_CONTENT = re.compile(
    r"\b(create|alter|drop)\s+table\b"
    r"|\bop\.(add_column|drop_column|create_table|drop_table|alter_column|rename_table)\s*\("
    r"|\bclass\s+Migration\b"
    r"|\bmigrations\.RunPython\b"
    r"|\bdef\s+(upgrade|downgrade)\s*\(", re.I)

#: SQL embedded in a host language, which `impact.DETECTORS` cannot see
#: because its own `sql-ddl` detector requires a `.sql` path. Deliberately
#: DML-shaped (select/insert/update/delete) as well as DDL, because a query
#: reaching a decision is exactly what `data-reviewer` exists to check, DDL
#: or not.
_EMBEDDED_SQL = re.compile(
    r"\bselect\s+[\w*,.\s]+\s+from\s+\w+"
    r"|\binsert\s+into\s+\w+"
    r"|\bupdate\s+\w+\s+set\s+"
    r"|\bdelete\s+from\s+\w+", re.I)

#: A control promise, the kind a documentation file states and a reader
#: relies on ("all requests must be authenticated"). Matched against REMOVED
#: lines only in a doc-shaped file: a promise appearing is not a finding, a
#: promise disappearing is, whatever replaced it. Deliberately narrow: this
#: is not a general "does this doc sound important" scan, only the words a
#: security/compliance sentence tends to carry.
_CONTROL_PROMISE = re.compile(
    r"\b(authenticat\w*|authoriz\w*|encrypt\w*|must\s+(?:be|not|always|never)\b"
    r"|is\s+required\b|shall\s+not\b|guarantee\w*|access\s+control"
    r"|rate[- ]limit\w*|never\s+log\w*|always\s+valid\w*)", re.I)

#: A test file, by path shape: a tests/specs directory, or a filename
#: convention common to Python, JS and TS test runners.
_TEST_FILE = re.compile(
    r"(^|/)(tests?|specs?|__tests__)/"
    r"|(^|/)test_[^/]+\.py$"
    r"|_test\.py$"
    r"|\.test\.[jt]sx?$"
    r"|\.spec\.[jt]sx?$", re.I)

#: An assertion, read broadly on purpose: a bare `assert`, a `self.assertX(`,
#: or a bare `assert_x(`/`assertX(` call, which covers Python's own keyword,
#: unittest's method family, and the common JS/TS `assert(...)`/`expect(...)`
#: shape closely enough for this router's purpose (naming a reviewer, not
#: grading the suite).
_ASSERTION = re.compile(r"\bassert[A-Za-z_]*\s*\(|(?<![\w.])assert\s|\bexpect\s*\(")

#: A marker that turns a live assertion into one that never runs, added with
#: no accompanying assertion: the shape a weakened test takes far more often
#: than a deleted assert line does.
_WEAKENING_MARKER = re.compile(
    r"\bpytest\.mark\.skip\w*|\bunittest\.skip\w*|@skip\b|\.skip\(|\bxfail\b|\bSkipTest\b", re.I)

#: How many test-only changed lines (added plus removed) count as
#: "significant" under rule 6, absent any weakening signal. Chosen, not
#: measured: below this a test-only diff is ordinary coverage growth this
#: router stays quiet about, exactly as it stays quiet about a small doc
#: edit; the weakening detector below fires at ANY size, because a removed
#: assertion is a finding at one line just as much as at fifty.
QA_SIGNIFICANT_LINES = 20


#: The only seven fields a fresh-context review is ever fed (plan section
#: 6.4). This is the ONE place the legal field set is written down;
#: `build_fresh_context_review_input` below reads it only to compose its
#: error message, never to decide what it accepts -- what it accepts is
#: fixed by its own named parameters, not by this tuple.
FRESH_CONTEXT_REVIEW_FIELDS = (
    "intent", "behaviorContract", "businessImpact", "risk", "diff",
    "requiredProof", "evidenceSummaries",
)

#: The exactly-three fixed questions a fresh-context review asks, verbatim
#: from plan section 6.4. Never extended, never reworded per change: a
#: reviewer that gets to pick its own questions is the eight-personas
#: problem this section exists to refuse.
FRESH_CONTEXT_QUESTIONS = (
    "Does the implementation satisfy the declared behavior?",
    "What important failure mode has no proof?",
    "Which assurance claim is stronger than its evidence?",
)


def _normalize_key_name(name):
    """`name` lower-cased with underscores, hyphens and spaces stripped, so
    `chat_log`, `chatLog`, `chat-log` and `chat log` all normalize to the
    same string (`"chatlog"`) for the blocklist match below.

    R2-2's fix: the round-one match was a bare `.lower()`, so `chat_log`
    (in the list) and `chatLog` (a plain camelCase spelling, NOT in the
    list because it normalizes to `chatlog`, a name the round-one list
    never carried) were caught inconsistently -- the refusal message's own
    example advertised `chatLog` as a caught name while the code that ran
    could not catch it. Stripping separators before matching, and holding
    the blocklist itself to the same normalized form, means every spelling
    of one name reduces to one entry to check against, once."""
    flat = str(name).lower()
    for ch in ("_", "-", " "):
        flat = flat.replace(ch, "")
    return flat


#: Conversation-shaped key NAMES the recursive scan below refuses wherever
#: they appear NESTED inside one of the seven permitted fields (a dict key
#: at any depth, including inside a list or a tuple of dicts), matched
#: after both sides run through `_normalize_key_name` so `chatHistory`,
#: `chat_log`, `chat-log`, `chat log` and `CONVERSATION` are all the same
#: refusal. This is A NORMALIZED NAME BLOCKLIST OVER NESTED DICT/LIST/TUPLE
#: KEYS, NOTHING MORE: it catches the implementation conversation riding
#: through a permitted field's own structure
#: (`intent={"goal": "x", "conversation": TRANSCRIPT}`,
#: `evidenceSummaries=[{"transcript": TRANSCRIPT}]`,
#: `evidenceSummaries=({"chatLog": TRANSCRIPT},)`). It is deliberately NOT a
#: general conversation-shape detector and never tries to be one -- see the
#: HONESTY paragraph on `ReviewInputRejected` and
#: `build_fresh_context_review_input` below for what a key-name blocklist
#: cannot and does not claim to catch: free text, and any name this tuple
#: does not list.
_CONVERSATION_KEY_NAMES = frozenset(
    _normalize_key_name(name) for name in (
        "conversation", "transcript", "transcripts", "messages", "chat_log", "chatlog",
        "dialogue", "dialog", "chatHistory", "convo", "conversationlog", "messagehistory",
        "sessiontranscript", "implementationchat",
    ))


def _find_conversation_key(value, path):
    """The first dotted/bracketed path inside `value` whose own dict key
    normalizes (via `_normalize_key_name`) to a name in
    `_CONVERSATION_KEY_NAMES`, or None. Recurses through dicts, lists AND
    tuples only -- a string leaf is a value, never something this scan
    parses for keys of its own, because the seven fields are legitimately
    fed strings and structured summaries, not a place to hunt inside free
    text (that is exactly the honesty boundary this module states plainly
    rather than papering over).
    """
    if isinstance(value, dict):
        for key, sub in value.items():
            key_path = "%s.%s" % (path, key)
            if _normalize_key_name(key) in _CONVERSATION_KEY_NAMES:
                return key_path
            found = _find_conversation_key(sub, key_path)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for index, sub in enumerate(value):
            found = _find_conversation_key(sub, "%s[%d]" % (path, index))
            if found:
                return found
    return None


class ReviewInputRejected(ValueError):
    """Raised by `build_fresh_context_review_input` in the two situations it
    can actually catch, named separately because they are different kinds
    of check:

    1. STRUCTURAL CLOSURE: a caller passes a field beyond the seven named in
       `FRESH_CONTEXT_REVIEW_FIELDS`. The builder has no parameter for
       `conversation=`, `transcript=`, or any other extra name, so it lands
       in `**smuggled` and is refused here instead of riding along in the
       returned dict.
    2. NESTED KEY-NAME SCAN: a caller passes one of the seven permitted
       fields whose OWN value nests a key in `_CONVERSATION_KEY_NAMES`
       (matched after both the found key and every listed name run through
       `_normalize_key_name`: lower-cased with underscores, hyphens and
       spaces stripped, so `chat_log`, `chatLog`, `chat-log` and `chat log`
       are the same refusal) inside a dict, a list, or a tuple, at any
       depth. This catches the material riding INSIDE a permitted field,
       not just beside it.

    WHAT THIS IS, STATED PLAINLY SO NOTHING WIDER IS EVER READ INTO IT: a
    NORMALIZED NAME BLOCKLIST over nested dict/list/tuple keys, nothing
    more. It is not a semantic detector of conversation-shaped content and
    never attempts to be one.

    WHAT THIS DOES NOT AND CANNOT CATCH, stated here rather than left to be
    inferred from an absence of a check: free text in a permitted field's
    own string value (`intent="<the whole chat transcript>"`, no nested key
    at all) is mechanically indistinguishable from a legitimate one-sentence
    intent, and this class does not pretend otherwise. A nested key whose
    name is not in `_CONVERSATION_KEY_NAMES` (spelled however it likes) is
    also not caught, by the same logic: this is a fixed list, not a
    heuristic that grows on its own. Both remain the CALLER'S CONTRACT, not
    this module's enforcement -- the seven fields exist to be fed a short,
    human-written summary, and no name blocklist can verify a string's
    content matches that intent, or guess every synonym nobody listed.
    """


def build_fresh_context_review_input(intent, behaviorContract, businessImpact, risk, diff,
                                     requiredProof, evidenceSummaries, **smuggled):
    """The whole and only input a fresh-context review is fed, plus the
    three fixed questions it asks.

    WHAT IS ENFORCED, mechanically, by this function: (1) structural closure
    -- the seven fields above are the only named parameters this function
    has, so a caller cannot pass the implementation conversation through a
    field that does not exist; any extra keyword lands in `**smuggled` and
    is refused by name via `ReviewInputRejected`, never silently dropped.
    (2) a recursive scan of every one of the seven fields' own VALUES
    (dicts, lists, AND tuples, at any depth) for a nested key that
    normalizes (`_normalize_key_name`: lower-cased, underscores, hyphens and
    spaces stripped) to a name in `_CONVERSATION_KEY_NAMES`, refused the
    same way, naming the offending nested path. THIS IS A NORMALIZED NAME
    BLOCKLIST OVER NESTED DICT/LIST/TUPLE KEYS, NOTHING MORE -- see
    `ReviewInputRejected` for the same rule stated against the exception it
    raises.

    WHAT IS NOT AND CANNOT BE ENFORCED here, stated plainly rather than
    implied by a docstring that only lists what passes: free text inside a
    permitted field (`intent="<the whole chat transcript>"`, no nested key
    at all) is mechanically indistinguishable from legitimate content, and a
    nested key whose name is not in `_CONVERSATION_KEY_NAMES` is not caught
    either, however conversation-shaped it reads to a human. This function
    does not scan field VALUES for conversation-shaped prose, does not grow
    the blocklist by guessing synonyms, and does not claim to do either.
    That remains the CALLER'S CONTRACT: feed this function a short,
    human-written summary in each field, not a transcript with the
    smuggled keys stripped out.
    """
    if smuggled:
        raise ReviewInputRejected(
            "fresh-context review input refuses field(s) %s: a fresh-context review is fed "
            "only %s, never the implementation conversation"
            % (", ".join(sorted(smuggled)), ", ".join(FRESH_CONTEXT_REVIEW_FIELDS)))
    fields = (
        ("intent", intent), ("behaviorContract", behaviorContract),
        ("businessImpact", businessImpact), ("risk", risk), ("diff", diff),
        ("requiredProof", requiredProof), ("evidenceSummaries", evidenceSummaries),
    )
    for field_name, field_value in fields:
        offender = _find_conversation_key(field_value, field_name)
        if offender:
            raise ReviewInputRejected(
                "fresh-context review input refuses field %r: nested key at %r reads as "
                "conversation-class material (matched against %s), refused wherever it rides "
                "inside a permitted field, never only at the top level"
                % (field_name, offender, ", ".join(sorted(_CONVERSATION_KEY_NAMES))))
    return {
        "intent": intent,
        "behaviorContract": behaviorContract,
        "businessImpact": businessImpact,
        "risk": risk,
        "diff": diff,
        "requiredProof": requiredProof,
        "evidenceSummaries": evidenceSummaries,
        "questions": list(FRESH_CONTEXT_QUESTIONS),
    }


def _read_raw_intent(intake_path):
    """The raw `intent` value from the intake file, in whatever shape a
    dossier author gave it (a string, a dict, anything JSON allows), or the
    literal string `"NO-DATA"` when there is no intake file, it does not
    parse, or it carries no `intent` key at all.

    Read directly here, separately from `impact.read_intent` (which already
    narrows to one trimmed `value_hypothesis` string): the nested-key
    smuggling scan in `build_fresh_context_review_input` needs this field's
    real shape, not a pre-narrowed one, or F-3b's own reachability claim
    (a hostile intake file's `intent` block is refused by the SAME route()
    a real command runs) would be untrue for anything but the narrowed case.
    """
    if not intake_path or not os.path.exists(intake_path):
        return "NO-DATA"
    try:
        with io.open(intake_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except ValueError:
        # sbe: allow-silent a malformed intake is already reported as a tier
        # problem by impact.read_intake, called on the same path; this
        # function only ever adds a field and must not invent a second
        # error message for one file.
        return "NO-DATA"
    if not isinstance(data, dict) or "intent" not in data:
        return "NO-DATA"
    return data["intent"]


def _fresh_context_review(primary, intent, risk, diff, required_proof, evidence_summaries):
    """(required, combinedWith, questions, reviewInput): plan section 6.4's
    routing rule in full -- one independent fresh-context review by default
    (`required` is always True: this router never proposes zero reviews of
    any kind), and when risk routing already selected a specialist as
    `primaryReviewer`, the fresh-context review COMBINES with that reviewer
    (`combinedWith` names it) rather than this router proposing a second,
    separate reviewer for the same change. With no specialist selected,
    `combinedWith` is None: the fresh-context review runs standalone as the
    one default reviewer. Either way this never adds a name to
    `REVIEWER_NAMES`, never occupies `secondaryReviewer`, and never raises
    the tier -- see the module docstring's "ONE RULE" paragraph.

    `reviewInput` is the F-3b fix: the ACTUAL payload a fresh-context review
    would be fed, built by calling `build_fresh_context_review_input` with
    material `route()` already holds (intent read from the intake file,
    `risk` as the tier already computed, `diff` as the real diff text,
    `requiredProof` as the unmeasured list, `evidenceSummaries` as the
    evidence-fail hits already computed), never invented. A field this
    caller genuinely has nothing for is passed as the string `"NO-DATA"`,
    never a guessed value. Before F-3b, `build_fresh_context_review_input`
    had no caller outside its own tests; this call is the one production
    path, so a hostile intake file that smuggles a conversation-shaped key
    into its `intent` block is refused HERE, by the same `route()` a real
    command runs, not only inside a unit test that called the builder
    directly.

    R2-1's fix: "refused" used to mean `build_fresh_context_review_input`
    RAISING `ReviewInputRejected` straight out of `route()`, and `route()`'s
    own contract (this module's docstring: `verdict` is `ROUTED` or
    `NO-DATA`, never anything else) has no room for an uncaught exception --
    a route IS computable here (the diff, the tier, the reviewer selection
    all stand on their own) even when the fresh-context payload cannot be
    built, so raising past `route()` threw away a real, honest routing
    result over one refused field, and `sbe review-route`'s own caller
    (`cli.py`'s `_cmd_review_route`) catches only `DiffUnavailable`, so the
    refusal reached the terminal as a raw traceback with no route printed
    at all. Routing itself still completes here: this function catches
    `ReviewInputRejected` and turns it into an EXPLICIT refusal record
    (`{"verdict": "NO-DATA", "refused": <the rejection message, which
    already names the offending nested path>}`) so the refusal is VISIBLE
    in `reviewInput` rather than fatal to the whole command. `route()`'s
    other keys (`primaryReviewer`, `tier`, `reasons`, ...) are computed and
    returned exactly as before; only `reviewInput` itself changes shape,
    from the real payload to the refusal record, and only in this one case.
    """
    try:
        review_input = build_fresh_context_review_input(
            intent=intent, behaviorContract="NO-DATA", businessImpact="NO-DATA", risk=risk,
            diff=diff, requiredProof=required_proof, evidenceSummaries=evidence_summaries)
    except ReviewInputRejected as exc:
        review_input = {"verdict": "NO-DATA", "refused": str(exc)}
    return {
        "required": True,
        "combinedWith": primary,
        "questions": list(FRESH_CONTEXT_QUESTIONS),
        "reviewInput": review_input,
    }


def _is_low_risk_fast_path(tier, selected):
    """True only when the tier is T0/T1 AND zero triggers were selected.

    Gating on TIER as well as `selected` matters: an intake can declare T2 or
    T3 while the diff itself raises no trigger this router knows how to
    detect (a human-declared risk `impact` still honours as a floor), and
    that case must never read as the fast path -- a declared T2/T3 is a
    human saying the change is NOT low-risk, whatever the diff-only detectors
    found or missed.
    """
    return tier in FAST_PATH_TIERS and not selected


class RouteUnavailable(DiffUnavailable):
    """Re-exported under this module's own name for a caller that wants to
    catch only what this module raises, without reaching into `impact`."""


def _is_test_file(path):
    return bool(_TEST_FILE.search(path.lower()))


def _diff_lines(cwd, base, head, path):
    """(added, removed): the ADDED and REMOVED lines for one file, each a
    plain list of text with the diff marker stripped.

    `impact.added_lines` only ever returns the added half, by that module's
    own law (a deleted line must never raise a tier). This router is not
    bound by that law for its OWN detectors, because none of them feed a
    tier: a weakened test or a removed control promise is exactly a
    REMOVAL-shaped finding, and reading only the added half would make both
    undetectable by construction.
    """
    code, out, _err = _git(["diff", "-U0", "%s..%s" % (base, head), "--", path], cwd)
    if code != 0:
        return [], []
    added, removed = [], []
    for line in out.split("\n"):
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return added, removed


def _hit(detector, file_, why, trigger):
    return {"detector": detector, "file": file_, "why": why, "trigger": trigger}


def _migration_content_hits(cwd, base, head, files):
    hits = []
    for f in files:
        if not _CODE_OR_SQL_EXT.search(f.lower()):
            continue
        added = impact_mod.added_lines(cwd, base, head, f)
        m = _MIGRATION_CONTENT.search(added)
        if m:
            hits.append(_hit(
                "migration-content", f,
                "an added line matches a schema-migration shape (%r) in a file no path-based "
                "migration detector would have looked inside" % m.group(0)[:40],
                "migration"))
    return hits


def _embedded_sql_hits(cwd, base, head, files):
    hits = []
    for f in files:
        low = f.lower()
        if not _CODE_EXT.search(low):
            continue
        added = impact_mod.added_lines(cwd, base, head, f)
        m = _EMBEDDED_SQL.search(added)
        if m:
            hits.append(_hit(
                "embedded-sql", f,
                "an added line embeds a SQL statement (%r) in a host-language file, which the "
                "path-only `sql-ddl` detector (it requires a .sql path) would miss"
                % m.group(0)[:40],
                "data"))
    return hits


def _secret_like_hits(cwd, base, head, files):
    """A secret-SHAPED value in an added line, in ANY file, reusing the SAME
    `SECRET_PATTERNS` `evidence.redact_argv` and `sbe_telemetry.redact`
    already scan with. `impact.DETECTORS`' own `secret-material` detector
    only matches a FILENAME naming "secret" or "credential"; a fixture file
    named `defaults.py` carrying a hardcoded API key is exactly what that
    naming rule cannot see, and what `security-reviewer`'s own brief warns
    against relying on filename patterns for.
    """
    hits = []
    for f in files:
        added = impact_mod.added_lines(cwd, base, head, f)
        for line in added.splitlines():
            if any(p.search(line) for p in SECRET_PATTERNS):
                hits.append(_hit(
                    "secret-like-value", f,
                    "an added line matches a known secret-shaped pattern (the same list "
                    "sbe_telemetry.py and sbe evidence redact with), regardless of the "
                    "filename it lives in",
                    "security"))
                break
    return hits


def _doc_control_promise_hits(cwd, base, head, files):
    """A control-promise sentence REMOVED from a documentation file.
    `impact` treats every doc extension as QUIET (no detector, not even
    unmeasured); this router does not inherit that blindness for content a
    reader will actually rely on, because a promise disappearing from a
    README is exactly the class of change a reviewer should see even when
    no code changed at all.
    """
    hits = []
    for f in files:
        if os.path.splitext(f.lower())[1] not in _DOC_EXT:
            continue
        _added, removed = _diff_lines(cwd, base, head, f)
        for line in removed:
            m = _CONTROL_PROMISE.search(line)
            if m:
                hits.append(_hit(
                    "docs-control-promise", f,
                    "a removed line reads a control promise (matched %r); verify the "
                    "control itself did not weaken to match the new wording"
                    % line.strip()[:80],
                    "security"))
                break
    return hits


def _qa_hits(cwd, base, head, files):
    """Rule 6: fires only over a TEST-ONLY diff (every changed file matches
    `_TEST_FILE`), and only two ways: a weakening signal at ANY size (an
    assertion removed with none added back, or a skip/xfail marker added
    with no matching assertion), or a plain significant size with no
    weakening signal at all, so an ordinary large coverage addition still
    gets looked at once, not because it is suspicious but because a
    significant test-only change is what rule 6 names either way.
    """
    if not files or not all(_is_test_file(f) for f in files):
        return []
    total_changed = 0
    weakened = []
    for f in files:
        added, removed = _diff_lines(cwd, base, head, f)
        total_changed += len(added) + len(removed)
        added_asserts = sum(1 for l in added if _ASSERTION.search(l))
        removed_asserts = sum(1 for l in removed if _ASSERTION.search(l))
        weakening_marker = any(_WEAKENING_MARKER.search(l) for l in added)
        if removed_asserts > added_asserts or weakening_marker:
            weakened.append(f)
    if weakened:
        return [_hit(
            "test-weakened-assertion", f,
            "an assertion appears removed with none added back, or a skip/xfail marker was "
            "added with no replacement assertion", "qa")
            for f in weakened]
    if total_changed >= QA_SIGNIFICANT_LINES:
        return [_hit(
            "test-only-significant", None,
            "%d test-only line(s) changed across %d file(s), which is enough for a coverage "
            "and traceability pass" % (total_changed, len(files)), "qa")]
    return []


def _evidence_hits(cwd):
    """Rule 7: fires only when a receipt already on disk FAILS its own
    `sbe evidence verify` (stale head, malformed schema, a covered file that
    changed, a broken seal). This never runs the checks that would MAKE
    evidence; it only reads what is already there, exactly as
    `evidence-auditor`'s own brief requires ("must never generate the
    evidence it audits"). An absent evidence store is not a trigger: rule 7
    is explicitly "run it when evidence itself is material or disputed,"
    never on every change.
    """
    try:
        from . import evidence as evidence_mod
        from . import tasks as tasks_mod
    except ImportError:
        return []
    evidence_dir = tasks_mod.evidence_dir(cwd)
    if not os.path.isdir(evidence_dir):
        return []
    hits = []
    for kind in evidence_mod.CHECK_KIND_NAMES:
        path = os.path.join(evidence_dir, "%s.json" % kind)
        if not os.path.exists(path):
            continue
        result = evidence_mod.verify(path, cwd=cwd)
        if result["verdict"] == "FAIL":
            hits.append(_hit(
                "evidence-fail", os.path.relpath(path, cwd),
                "the %s receipt fails sbe evidence verify: %s"
                % (kind, "; ".join(result["reasons"])[:220]),
                "evidence"))
    return hits


def _resolve_target(path):
    """(repo_cwd, intake_path or None, dossier_dir or None).

    `path` directly holding `00-intake.json` is read as a dossier, the same
    single-dossier convention `sbe impact` and `sbe status` already read
    flat: in the common case that convention IS the git repository root
    (every fixture in this project's own test suite is shaped that way), so
    `repo_cwd` is found by walking upward from `path` for the nearest `.git`,
    falling back to `path` itself when none is found (in which case the git
    calls this makes will report why, honestly, rather than this function
    guessing).

    `path` with no `00-intake.json` directly inside it is read as the
    repository itself, exactly the way `sbe impact <path>` reads its own
    positional argument: no dossier, so the diff-derived tier stands alone
    as the floor `impact.py` always documents it as.

    This does NOT walk `design/` for a team of dossiers the way
    `status.build_team_report` does: `sbe review-route` routes ONE diff at a
    time, and multi-dossier discovery belongs to a command that already
    aggregates across changes, not to this one.
    """
    abs_path = os.path.abspath(path)
    intake_path = os.path.join(abs_path, "00-intake.json")
    if not os.path.exists(intake_path):
        return abs_path, None, None
    cur = abs_path
    while True:
        if os.path.isdir(os.path.join(cur, ".git")) or os.path.isfile(os.path.join(cur, ".git")):
            return cur, intake_path, abs_path
        parent = os.path.dirname(cur)
        if parent == cur:
            return abs_path, intake_path, abs_path
        cur = parent


def _tier_and_source(cwd, base_sha, head_ref, hits, intake_path):
    """(tier, source sentence, intake problem or None). Exactly the ladder
    `brothersbe.impact` already computes, read through that module's own
    functions so a tier here and a tier `sbe impact` reports can never
    drift apart; see the module docstring's "ONE RULE" paragraph."""
    derived = impact_mod.answers_from_hits(hits)
    proposed = impact_mod.compute_tier(derived)
    human_tier, _answers, problem = impact_mod.read_intake(intake_path) if intake_path else \
        (None, None, None)
    if human_tier:
        return human_tier, "declared at %s" % intake_path, problem
    source = ("derived from the diff (a floor, never a ceiling: no intake was found or none "
             "was readable)")
    # A stranger's walk found the silent half of this sentence: an intake CAN
    # exist on disk while this path derives a different tier from the diff,
    # because no dossier was named on the command line. Saying which files
    # were skipped is the difference between a floor and a quiet override.
    unread = []
    design_dir = os.path.join(cwd, "design")
    if os.path.isdir(design_dir):
        for name in sorted(os.listdir(design_dir)):
            candidate = os.path.join(design_dir, name, "00-intake.json")
            if os.path.exists(candidate):
                unread.append(os.path.relpath(candidate, cwd))
    if unread:
        shown = ", ".join(unread[:3])
        more = "" if len(unread) <= 3 else " and %d more" % (len(unread) - 3)
        source += ("; NOTE: %d intake file(s) exist and were NOT read (%s%s): "
                   "name the dossier on the command line to honour a declared "
                   "tier" % (len(unread), shown, more))
    return proposed, source, problem


#: The per-repo ledger of how many times each reviewer has been routed,
#: living beside `.sbe/tasks.json` (the other small JSON registry this
#: project keeps at a repo's own `.sbe/` directory) rather than in a new
#: top-level location.
ROUTE_LEDGER_NAME = "review-route-counts.json"


def _route_ledger_path(cwd):
    return os.path.join(cwd, ".sbe", ROUTE_LEDGER_NAME)


def routed_counts(cwd):
    """The ledger as a dict of reviewer name -> total times routed, read
    fresh off disk. Absent, empty or corrupt reads as no history yet (an
    empty dict), never a crash: this ledger is a count for a human to read,
    not a control a missing file should fail."""
    try:
        with io.open(_route_ledger_path(cwd), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}

def _record_routed(cwd, reviewer_names):
    """Increment the ledger by one for every name in `reviewer_names` (a
    reviewer selected twice in one call, which cannot happen today since
    primary and secondary are always distinct, would still only count once
    per call because `route()` passes a set). Atomic rewrite: a temp file in
    the same `.sbe` directory, then `os.replace`, the same pattern
    `tasks.save_registry` already uses for its own registry, so a crash
    mid-write never leaves a half-written ledger.

    Returns the ledger AFTER the increment. A no-op (nothing selected) still
    returns the current ledger, unmodified and unwritten, so a mechanical-only
    route never creates a `.sbe` directory it had no reason to touch."""
    if not reviewer_names:
        return routed_counts(cwd)
    directory = os.path.join(cwd, ".sbe")
    if not os.path.isdir(directory):
        os.makedirs(directory)
    data = routed_counts(cwd)
    for name in reviewer_names:
        data[name] = int(data.get(name, 0)) + 1
    fd, tmp = tempfile.mkstemp(prefix="review-route-counts.", suffix=".tmp", dir=directory)
    try:
        with io.open(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, _route_ledger_path(cwd))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return data


def route(target, base=None, head="HEAD", work_profile=None):
    """The whole route, as a dict ready to be printed or serialized.

    Raises `DiffUnavailable` exactly where `impact.resolve_range` would: no
    git repository, no default branch to diff against, no parent commit.
    The caller decides what that means for an exit code; this function only
    computes.
    """
    cwd, intake_path, _dossier = _resolve_target(target)
    base_sha, head_ref = impact_mod.resolve_range(cwd, base, head)
    code, head_sha, _e = _git(["rev-parse", head_ref], cwd)
    head_sha = head_sha.strip() if code == 0 else head_ref

    files = impact_mod.changed_files(cwd, base_sha, head_ref)
    impact_hits, impact_unmeasured = impact_mod.detect(cwd, base_sha, head_ref, files)

    mapped_impact_hits = []
    unknown_detector_notes = []
    for h in impact_hits:
        trigger = IMPACT_DETECTOR_TRIGGER.get(h["detector"])
        if trigger is None:
            unknown_detector_notes.append({
                "file": h["file"],
                "reason": "impact detector %r fired here but review-route has no reviewer "
                          "mapping for it yet; add a row to IMPACT_DETECTOR_TRIGGER in "
                          "src/brothersbe/reviewroute.py rather than let this pass quietly"
                          % h["detector"]})
            continue
        mapped_impact_hits.append(_hit(h["detector"], h["file"], h["why"], trigger))

    evidence_hits = _evidence_hits(cwd)
    local_hits = (
        _migration_content_hits(cwd, base_sha, head_ref, files)
        + _embedded_sql_hits(cwd, base_sha, head_ref, files)
        + _secret_like_hits(cwd, base_sha, head_ref, files)
        + _doc_control_promise_hits(cwd, base_sha, head_ref, files)
        + _qa_hits(cwd, base_sha, head_ref, files)
        + evidence_hits
    )
    all_hits = mapped_impact_hits + local_hits

    tier, tier_source, intake_problem = _tier_and_source(cwd, base_sha, head_ref, impact_hits,
                                                          intake_path)

    triggers = {}
    for h in all_hits:
        triggers.setdefault(h["trigger"], []).append(h)

    ordered = [t for t in TRIGGER_PRIORITY if t in triggers]
    max_reviewers = 1 if tier in ("T0", "T1") else MAX_SPECIALISTS
    selected = ordered[:max_reviewers]
    overflow = ordered[max_reviewers:]

    primary = REVIEWER_OF_TRIGGER[selected[0]] if len(selected) > 0 else None
    secondary = REVIEWER_OF_TRIGGER[selected[1]] if len(selected) > 1 else None

    # Recorded as a side effect only, never folded into the dict this function
    # returns: `--json` on the identical diff must stay byte-identical run to
    # run (TestDeterminismAndRegistry.test_the_same_diff_routes_byte_
    # identical_twice), and a monotonic count in that payload would break it
    # on its second call. A caller that wants the count reads it back with
    # `routed_counts(cwd)`, exactly as `_cmd_review_route` does for text output.
    _record_routed(cwd, sorted(set(n for n in (primary, secondary) if n)))

    reasons = ["tier %s, %s" % (tier, tier_source)]
    if intake_problem:
        reasons.append("intake: %s" % intake_problem)
    if work_profile:
        reasons.append(
            "task workProfile %r was supplied; it is recorded for provenance and does not "
            "change which reviewer is selected, because TRIGGER_RULES already orders every "
            "trigger this diff raised" % work_profile)
    for trig in selected:
        evidence = triggers[trig][:3]
        files_named = ", ".join(sorted(set(e["file"] for e in evidence if e["file"])) or
                                ["no single file (a whole-diff signal)"])
        reasons.append(
            "%s (rule %d) -> %s: %s. Evidence: %s"
            % (trig, TRIGGER_PRIORITY.index(trig) + 1, REVIEWER_OF_TRIGGER[trig],
               TRIGGER_DESCRIPTION[trig], files_named))
    if not selected:
        reasons.append(
            "no trigger fired: mechanical checks only, zero specialist reviewer. This is a "
            "legal result and is never read as a clean review")

    unmeasured = list(impact_unmeasured)
    covered_files = set(h["file"] for h in all_hits if h.get("file"))
    unmeasured = [u for u in unmeasured if u["file"] not in covered_files]
    unmeasured.extend(unknown_detector_notes)
    for trig in overflow:
        unmeasured.append({
            "file": None,
            "reason": ("%s (rule %d, would have selected %s) fired but lost its slot: more "
                      "than %d trigger(s) fired at tier %s and this trigger ranked lower by "
                      "silent-failure potential and blast radius than the selected one(s)"
                      % (trig, TRIGGER_PRIORITY.index(trig) + 1, REVIEWER_OF_TRIGGER[trig],
                         max_reviewers, tier))})

    diff_code, diff_text, _diff_err = _git(["diff", "%s..%s" % (base_sha, head_ref)], cwd)
    diff_value = diff_text if diff_code == 0 and diff_text.strip() else "NO-DATA"
    intent_value = _read_raw_intent(intake_path)
    required_proof_value = unmeasured if unmeasured else "NO-DATA"
    evidence_summaries_value = evidence_hits if evidence_hits else "NO-DATA"

    return {
        "schemaVersion": SCHEMA_VERSION,
        "scope": "git diff %s..%s over %d changed file(s)" % (base_sha[:12], head_ref,
                                                              len(files)),
        "baseCommit": base_sha,
        "headCommit": head_sha,
        "tier": tier,
        "primaryReviewer": primary,
        "secondaryReviewer": secondary,
        "reasons": reasons,
        "mechanicalOnly": not selected,
        "unmeasured": unmeasured,
        "verdict": "ROUTED",
        "lowRiskFastPath": _is_low_risk_fast_path(tier, selected),
        "freshContextReview": _fresh_context_review(
            primary, intent_value, tier, diff_value, required_proof_value,
            evidence_summaries_value),
    }


def no_data_report(target, exc):
    return {
        "schemaVersion": SCHEMA_VERSION,
        "scope": None,
        "baseCommit": None,
        "headCommit": None,
        "tier": None,
        "primaryReviewer": None,
        "secondaryReviewer": None,
        "reasons": ["no diff was available to route: %s" % exc],
        "mechanicalOnly": True,
        "unmeasured": [{"file": None, "reason": str(exc)}],
        "verdict": "NO-DATA",
        "lowRiskFastPath": False,
        "freshContextReview": {
            "required": False,
            "combinedWith": None,
            "questions": list(FRESH_CONTEXT_QUESTIONS),
        },
    }


def render(data, counts=None):
    """`counts`, when given, is a `routed_counts(cwd)` snapshot (reviewer name
    -> total times routed, ledger read AFTER this call's own increment): each
    selected reviewer's line then names its running total. Absent (None) or a
    reviewer missing from it prints the reviewer name alone, so a caller that
    never reads the ledger (or a ledger this run could not write) degrades to
    the old line rather than crashing on a missing key."""
    counts = counts or {}
    out = []
    if data["verdict"] == "NO-DATA":
        out.append("NO-DATA: %s" % data["reasons"][0])
        return "\n".join(out) + "\n"
    out.append(data["scope"])
    out.append("tier %s" % data["tier"])

    def _reviewer_line(label, name):
        if not name:
            return "%s %s" % (label, "none")
        if name in counts:
            return "%s %s (routed %d time(s) total)" % (label, name, counts[name])
        return "%s %s" % (label, name)

    out.append(_reviewer_line("primary reviewer  ", data["primaryReviewer"]))
    out.append(_reviewer_line("secondary reviewer", data["secondaryReviewer"]))
    out.append("mechanical only    %s" % data["mechanicalOnly"])
    for reason in data["reasons"]:
        out.append("  REASON %s" % reason)
    for u in data["unmeasured"]:
        out.append("  UNMEASURED %s%s" % ((u["file"] + ": ") if u["file"] else "", u["reason"]))
    out.append("review-route never claims a clean review: it names who should look, never "
               "that nobody needs to")
    if data.get("lowRiskFastPath"):
        out.append(FAST_PATH_LINE % data["tier"])
    fcr = data.get("freshContextReview")
    if fcr and fcr.get("required"):
        if fcr.get("combinedWith"):
            out.append("fresh-context review  combined with %s (never a duplicate reviewer)"
                       % fcr["combinedWith"])
        else:
            out.append("fresh-context review  standalone, one independent review by default")
    return "\n".join(out) + "\n"
