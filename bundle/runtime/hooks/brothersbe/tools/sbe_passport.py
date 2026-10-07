#!/usr/bin/env python3
"""The change passport, consumer half: what BrotherSBE can read at the seam.

WHAT THE SEAM IS. The change passport is the single crossing between execution
provenance and assurance. BrotherMode produces one, BrotherSBE consumes one, and
it never travels back. If assurance needs something the passport does not carry,
that is a defect in the passport, not permission to reach into execution state.
This tool is the consuming end and the only place BrotherSBE reads passport data,
so either side can be replaced as long as it can still fill in or read the five
fields below.

THE FIVE FIELDS, in the order the chain names them, each with the side that owes
it:

  1. what was done            change identity, commit range, files
  2. who did it               sessions, claims, the accountable person by name
  3. what was run             each check, its result, and whether a build system
                              or a laptop produced it
  4. what was NOT established mandatory, never empty
  5. where it came from       the method, named and not judged

WHY THIS IS NOT A SCHEMA, which is the constraint that shaped the whole file.
Decision N1 in NORTH-STAR.md, taken 2026-08-15, is that the passport is the SEAM
and not a specified object with its own lifecycle and state machine. So this tool
introduces no new required file, no state, no identifier of its own and no
migration. It is a VIEW: it reads the task registry and the receipts that already
exist, and reports each of the five fields as carried or NO-DATA. A producer that
has something to add deposits it at `.sbe/passport.json` and that deposit is read
if present; nothing requires it to exist, and its absence is reported as absence
rather than as a clean field.

WHAT IT REFUSES TO DO. It never reads execution state: no session log, no fence
registry, no claim file, no other repository. A field nothing carries is NO-DATA
naming the side that owes it, because reaching around the seam to fill a field is
the exact failure the seam exists to prevent. Read `.sbe/` under the change root
and the deposit, and nothing else.

THE EMPTY STATE, declared. Field 4 may never be empty. A passport claiming that
nothing is unexamined is the precise lie this chain exists to prevent, so when
nothing computes as unestablished this tool prints what it was actually able to
look at rather than a clean line. A root with no store at all is NO-DATA in every
field and says so, and that page is never mistaken for a passport.

NOT A GATE. This tool reports, it never blocks: exit 0 always, including on a
root carrying nothing, and 1 only when a requested --out path cannot be written.
NO-DATA here is never a pass and never a block, which is this project's own
vocabulary and not a new rule.

Usage: python3 tools/sbe_passport.py [--root DIR] [--json] [--out PATH]

Python floor is 3.9: no match statements, no `X | Y` annotations. Standard
library only, mirroring every other tool in this directory.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sbe_onepager import (command_of, load_json_or_none, load_tasks,
                          one_line, resolve_receipt_path, scan_evidence_receipts,
                          verdict_of)
# F1 (seam review, cross-family, 2026-08-20): field membership routes through
# THIS answered, not sbe_onepager's blank-only one. sbe_onepager's answered
# only tests for blank; it has no vacuity rule at all, so a deposit member
# reading "unknown", "TODO", "-", "n/a" or "TBD" survived it as though it
# were real content. tools/sbe_checks.py owns VACUOUS_VALUES, the one
# vacuity rule in this project, and every sibling that needs it imports it
# under this same name rather than keeping a private copy.
from sbe_checks import answered

# KNOWN, DELIBERATELY DEFERRED (seam review 2026-08-20, founder decision: fix
# G1 through G4 in this pass, record the rest as debt for a later session).
# Each is a real finding; none is fixed here.
#   G5  a docstring elsewhere in the review chain stutters "rather than a
#       string rather than a string"; cosmetic, not touched.
#   G6  the schema's wording for an empty string vs an absent field is
#       imprecise; a documentation fix, not a behavior change.
#   G8  a non-string value hollowed by F1's deposit_field path (see
#       deposit_field above) produces a "non-string" note in field 4, but a
#       non-string value elsewhere in this module (outside deposit_field)
#       can still fail to produce any note at all; not audited end to end.
#   G9  load_module_from_source in tools/test_sbe_passport.py registers
#       sbe_onepager and sbe_checks under their real names in sys.modules
#       for THIS suite's own process; a test runner that combines this
#       suite with another in the same process can end up with two loaded
#       copies of the same module (one from each suite's injection), which
#       is a real hazard for any test relying on identity across suites.
#   G10 pycache files can still appear in the working tree from an
#       ordinary (non-test) invocation of this tool or its siblings; G2's
#       fix only covers this suite's own subprocess environment.
# A later pass should open a task per item rather than fold them into this
# change; each needs its own read of the call sites before a fix is safe.

DEPOSIT_REL = os.path.join(".sbe", "passport.json")
TASKS_REL = os.path.join(".sbe", "tasks.json")

#: The deposit key each field reads, the field's name, and the side that owes it
#: when nothing carries it. Ordered exactly as the chain draws them.
FIELDS = (
    (1, "what was done", "whatWasDone", "assurance, from its own store"),
    (2, "who did it", "whoDidIt", "execution provenance, through the passport"),
    (3, "what was run", "whatWasRun", "assurance, from its own receipts"),
    (4, "what was NOT established", "whatWasNotEstablished", "assurance, from its own store"),
    (5, "where it came from", "whereItCameFrom", "execution provenance, through the passport"),
)

NO_DATA = "NO-DATA"
CARRIED = "CARRIED"


class DepositField(object):
    """One field's content from a producer deposit, split by F1's rule:
    `lines` are members that survived `answered` (tools/sbe_checks.py's
    VACUOUS_VALUES, the one vacuity rule in this project) AS STRINGS, real
    non-hollow content; `degraded` are page lines naming every member that
    did NOT, quoting the reason (hollow token or non-string) rather than
    rendering a Python repr as though it were carried prose. An object and
    not a tuple, same reason Read and ReceiptRef above are: the honesty
    meta-test reads any 2-tuple return as a possible (verdict, evidence)
    pair and refuses a function it cannot prove never returns PASS."""

    def __init__(self, lines, degraded):
        self.lines = lines
        self.degraded = degraded


def deposit_field(deposit, key, name):
    """The producer's entries for `key` (named `name` for a human reading
    the degraded notes), split into what actually carries content and what
    does not. A deposit that is absent, is not an object, or carries an
    explicit null under `key` returns nothing at all: that is reported as
    absence by the caller (NO-DATA), never invented here.

    F1: this is the ONLY place a deposit member becomes a page line, and
    every member is routed through `answered` (imported above from
    tools/sbe_checks.py) AND must be a Python string. Before this fix a
    deposit whose five fields were ["unknown"], ["TODO"], ["-"],
    ["n/a", "?"], ["TBD"] rendered every field CARRIED, because the old
    `answered` only tested for blank; and a non-string member (an int, a
    dict, a NaN) rendered as its Python repr counted as a carried line. Now
    a hollow token survives nothing, a non-string member survives nothing,
    and a field with zero surviving members reports NO-DATA through the
    normal `CARRIED if lines else NO_DATA` rule in build_fields below,
    naming the owing side exactly as an absent field does.
    """
    if not isinstance(deposit, dict):
        return DepositField([], [])
    value = deposit.get(key)
    if value is None:
        return DepositField([], [])
    if isinstance(value, list):
        members = [(None, v) for v in value]
    elif isinstance(value, dict):
        members = sorted(value.items(), key=lambda kv: str(kv[0]))
    else:
        members = [(None, value)]
    lines, degraded = [], []
    for label, raw in members:
        tag = one_line(label) if label is not None else None
        # I1, I3: these are TOOL sentences about the deposit, not the
        # deposit's carried content, so the producer-controlled fragment
        # inside them is capped by the same rule as every other summary
        # line. Carried content itself (the `lines.append` below) is never
        # capped: truncating it would destroy what the seam exists to move.
        if not isinstance(raw, str):
            what = ("%s=%s" % (_capped(tag), _capped(repr(raw)))) if tag else _capped(repr(raw))
            degraded.append(
                "the producer's %s entry (%s) is non-string, so it is not "
                "carried content" % (name, what))
            continue
        answer = answered(raw)
        if answer is None:
            what = ("%s=%r" % (tag, raw)) if tag else repr(raw)
            degraded.append(
                "the producer's %s entry (%s) is a hollow token, so it is "
                "not carried content" % (name, _capped(what)))
            continue
        lines.append(("%s: %s" % (tag, one_line(answer))) if tag else one_line(answer))
    return DepositField(lines, degraded)


def producer_not_established(deposit):
    """Field 4's producer half. F2: `whatWasNotEstablished: []` present in
    the deposit is a CLAIM ("nothing was left unestablished"), never silent
    absence, and it is judged here rather than absorbed into the
    store-derived gaps as though the key had never been written. It is
    honoured only when `details.notEstablished.noneClaimJustification`
    carries an answered string (quoted back to the reader); otherwise it is
    flagged as suspicious vacuity, because a claim of a clean change with no
    reason given is exactly the false confidence field 4 exists to refuse.
    A present, non-empty list goes through the same member test as every
    other field (deposit_field). An absent key is silence: nothing for the
    producer to report here, same as any other missing key."""
    if not isinstance(deposit, dict) or "whatWasNotEstablished" not in deposit:
        return []
    value = deposit.get("whatWasNotEstablished")
    if isinstance(value, list) and len(value) == 0:
        justification = None
        details = deposit.get("details")
        if isinstance(details, dict):
            not_established = details.get("notEstablished")
            if isinstance(not_established, dict):
                raw = not_established.get("noneClaimJustification")
                if isinstance(raw, str):
                    justification = answered(raw)
        if justification is not None:
            # I2: a tool sentence quoting a producer-controlled value, capped
            # like every other summary line.
            return ["the producer claims nothing was left unestablished, "
                    "justified: %s" % _capped(justification)]
        return ["the producer claims nothing was left unestablished, with no "
                "justification at details.notEstablished.noneClaimJustification: "
                "suspicious vacuity, not evidence of a clean change"]
    field = deposit_field(deposit, "whatWasNotEstablished", "what was NOT established")
    return field.lines + field.degraded


def read_deposit(root):
    """(deposit_or_None, note). The note is what a reader needs to judge the
    field lines below it, so a corrupt deposit is named rather than read as an
    absent one: those are different facts and only one of them is somebody's
    mistake."""
    read = load_json_or_none(os.path.join(root, DEPOSIT_REL))
    if read.problem == "absent":
        return (None, "no producer deposit at %s, so every field below is what "
                      "assurance could read from its own store" % DEPOSIT_REL)
    if read.problem is not None:
        return (None, "the producer deposit at %s is %s, so it was NOT read; a "
                      "deposit that will not parse is a defect in the passport, "
                      "not an absent one" % (DEPOSIT_REL, read.problem))
    if not isinstance(read.data, dict):
        return (None, "the producer deposit at %s is not a JSON object, so it "
                      "was NOT read" % DEPOSIT_REL)
    return (read.data, "a producer deposit was read from %s" % DEPOSIT_REL)


#: F6: the one recognised deposit version. Field shapes above (deposit_field,
#: producer_not_established) are defined for THIS schema alone; a deposit
#: claiming anything else is read for its note (F4's claims line still
#: quotes what it says about itself) but never for its field content, so a
#: v2 or garbage shape can never render as a repr'd CARRIED line the way an
#: unversioned reader would let it.
SCHEMA_KEY = "schema"
SCHEMA_V1 = "change-passport/v1"


def schema_problem(deposit):
    """None when `deposit`'s schema marker is exactly SCHEMA_V1 (or there is
    no deposit dict to check at all: absence is read_deposit's own finding,
    not this one). Otherwise the sentence naming what the deposit carried
    instead, because field shapes are defined for v1 alone and reading a v2
    or garbage deposit as though it were v1 is exactly how a hollow member
    ends up rendered as CARRIED. Never raises: a non-string schema value is
    named, not crashed on.

    R2-E: `deposit.get(SCHEMA_KEY)` returns None for BOTH a missing key and a
    key explicitly present with a JSON null, and those are different facts:
    an absent key means the producer never wrote a schema marker at all,
    while a present-but-null one means it wrote the key and answered it with
    nothing. `producer_class` already keeps this same distinction for
    `ciRunId` (its PRODUCER_OFF_BUILD_SYSTEM vs PRODUCER_NOT_ESTABLISHED
    split); this mirrors it rather than collapsing both into "absent"."""
    if not isinstance(deposit, dict):
        return None
    raw = deposit.get(SCHEMA_KEY)
    if not isinstance(raw, str):
        if raw is None:
            shape = ("present and null rather than a string"
                     if SCHEMA_KEY in deposit else "absent")
        else:
            shape = "non-string"
        return ("the deposit's `schema` field is %s rather than a string, so "
                "this passport does not know the deposit's own version, and "
                "field shapes are only defined for %s"
                % (shape, SCHEMA_V1))
    if raw.strip() != SCHEMA_V1:
        # H2: capped like every other unvalidated deposit value that reaches
        # a page line; a 16,000-character schema produced a 16,000-character
        # note before this.
        return ("the deposit's `schema` is %s, not %s: this reader does not "
                "recognise it, and field shapes are only defined for %s"
                % (_capped(raw), SCHEMA_V1, SCHEMA_V1))
    return None


def sensitivity_note(deposit):
    """F7: one line naming how this deposit's own `sensitivity` marker
    should change how a reader handles it. "raw" carries the handling
    warning outright (unredacted store text, handle it as the store
    itself); "redacted" is named quietly, since that is the safe default;
    anything else is named as unrecognised rather than silently ignored.
    None when there is nothing to say (no deposit, or no string value).

    R2-F: the "raw"/"redacted" comparison is case-insensitive after strip,
    so "RAW" or "Raw" still carries the handling warning; only the
    producer's exact original spelling is quoted back in the printed note,
    never the lowercased form used to judge it."""
    if not isinstance(deposit, dict):
        return None
    raw = deposit.get("sensitivity")
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    lowered = value.lower()
    # H2: this value comes from the same UNVALIDATED deposit the claims note
    # caps, and it lands on the same page line, so it is capped by the same
    # rule. Without this a 16,000-character sensitivity produced exactly the
    # page line CLAIM_MAX_LEN's own docstring says a producer cannot produce.
    shown = _capped(value)
    if lowered == "raw":
        return ("sensitivity: %s. This deposit carries UNREDACTED store "
                "text; handle it with the same care as the store itself."
                % shown)
    if lowered == "redacted":
        return "sensitivity: %s." % shown
    return "sensitivity: %s (unrecognised value)" % shown


#: G4: the deposit is UNVALIDATED input, and this note prints one line per
#: claim on a page a human reads. A single claim longer than this is cut,
#: visibly, so a producer that writes a 16,000-character change.repo cannot
#: turn a summary note into a 16,000-character page line.
CLAIM_MAX_LEN = 200


def _capped(text):
    """`text` flattened to one line and cut at CLAIM_MAX_LEN with the cut
    made visible. H2: every note line built from the unvalidated deposit
    goes through this, not only the claims note, because they all print on
    the same page and a cap that covers one call site of three caps
    nothing."""
    flat = one_line(text)
    if len(flat) > CLAIM_MAX_LEN:
        return flat[:CLAIM_MAX_LEN].rstrip() + " (truncated)"
    return flat


def _claim_text(raw, name):
    """One claim value for deposit_claims_note, or None when it records
    nothing. G4: the note is built from an UNVALIDATED deposit (this
    branch runs even for a refused schema version, per R2-D, so field-shape
    validation never gated it), so a claim is rendered only when it passes
    the one vacuity rule (`answered`, tools/sbe_checks.py) as an actual
    STRING. Before this fix a non-string claim (a dict, a list) rendered as
    its Python repr, and a claim of any length printed in full: a
    `change.repo` of `{'nested': ['container', 'here']}` printed that repr
    verbatim, and a 16k-character `change.repo` produced a
    16k-character-plus note line. Now a non-string claim is named unusable
    rather than repr'd, and any surviving string claim is capped at
    CLAIM_MAX_LEN with the cut made visible ("... (truncated)")."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        return "(unusable: the producer's %s is not a string)" % name
    answer = answered(raw)
    if answer is None:
        return None
    return _capped(answer)


def deposit_claims_note(deposit):
    """F4: what the deposit itself claims to be about, so a reader can judge
    whether this passport is talking about the same change as the store:
    generatedAt, change.repo, change.projectId, and the commit range. Each
    is named "not stated" rather than dropped when the deposit does not
    carry it, because a note that silently omits an absent claim reads the
    same as a note that never looked."""
    if not isinstance(deposit, dict):
        return ""
    gen = _claim_text(deposit.get("generatedAt"), "generatedAt")
    change = deposit.get("change")
    repo = project = base = head = None
    if isinstance(change, dict):
        repo = _claim_text(change.get("repo"), "change.repo")
        project = _claim_text(change.get("projectId"), "change.projectId")
        base = _claim_text(change.get("baseCommit"), "change.baseCommit")
        head = _claim_text(change.get("headCommit"), "change.headCommit")
    range_text = ("%s..%s" % (base, head)
                 if (base is not None and head is not None) else "not stated")
    return ("the deposit claims: generatedAt %s, change.repo %s, "
           "change.projectId %s, commit range %s"
           % (gen if gen is not None else "not stated",
              repo if repo is not None else "not stated",
              project if project is not None else "not stated",
              range_text))


def store_commit_set(root, tasks, extra_commits):
    """Every commit id the STORE itself claims: task receipts plus any loose
    (unbound) receipt commits already found by unbound_receipts. Kept apart
    from the sentence-building in commit_lines below so F4's disagreement
    check can compare against the raw set without re-deriving it from
    prose."""
    commits = set(extra_commits)
    for task in tasks:
        data, _why_not = read_receipt(root, task)
        if isinstance(data, dict) and answered(data.get("headCommit")):
            commits.add(one_line(data["headCommit"]))
    return commits


_HEX_COMMITISH = re.compile(r"^[0-9a-f]+$")


def commitish_agrees(a, b):
    """Whether two commitish strings COULD name the same commit.

    Two rules, in order:

    1. EQUALITY ALWAYS AGREES, whatever the length or shape. Two
       byte-identical values (after lowercasing, so a case difference alone
       still agrees) are the same claim, full stop; a value being short is
       never a reason to call two identical strings a disagreement. (G1,
       regression: the prefix arm's 7-character floor below used to gate
       this case too, so two identical 6-character commitish strings, the
       shape this suite's own default fixture uses, printed a false
       DISAGREEMENT.)
    2. PREFIX AGREEMENT is judged only when the two values differ, per the
       producer schema's own shape for `headCommit`: `^[0-9a-fA-F]{7,40}$`,
       short or full hex. A short hash from one side and the full 40-hex of
       the SAME commit from the other are both legal and never equal as
       strings. The shorter must be at least 7 characters (the schema's own
       floor for a short hash) AND both values must match that hex shape
       (`_HEX_COMMITISH`) before a prefix relationship counts as agreement;
       otherwise a short non-hex value (a branch name, say) that happens to
       prefix a longer, unrelated non-hex value would read as the same
       commit. (G1: the docstring here used to say "hex characters" while
       the code only counted characters and never checked the shape, so
       `commitish_agrees('main-branch', 'main-branch-2')` returned True and
       suppressed a real disagreement between two different values that
       merely share a prefix.)

    An empty string on either side never agrees with anything."""
    a, b = a.strip().lower(), b.strip().lower()
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) < 7:
        return False
    if not (_HEX_COMMITISH.match(shorter) and _HEX_COMMITISH.match(longer)):
        return False
    return longer.startswith(shorter)


class ToolAuthored(str):
    """A page line THIS TOOL computed, not one a producer deposited.

    H1: the literal-line guard (tools/sbe_onepager.py, one_line's
    guard_header arm) exists to stop untrusted deposit content from reading
    as one of this page's own sentences. Once "DISAGREEMENT:" joined that
    guard's prefixes it also decorated the tool's OWN disagreement finding,
    which was false twice over: the decoration says "reported value", which
    the tool's own conclusion is not, and it left ZERO lines on the page
    starting with DISAGREEMENT:, so a reader scanning for that word found
    nothing on a page that had computed a real conflict.

    The distinction cannot be made by text, because the whole point is that
    a producer can write any text. It is made by TYPE: values parsed out of
    a deposit are always plain `str` (json.loads builds nothing else), so a
    producer cannot forge this class, and the renderer trusts exactly what
    this module constructed itself."""


def _commit_disagreement_parts(store_commits, deposit):
    """A dict with named keys "commits" (sorted store commits) and "head"
    (deposit_head_raw) when the store's own receipts and the producer
    deposit's change.headCommit BOTH assert a commit and cannot possibly
    name the same one, else None. Never a bare 2-tuple: a literal (x, y)
    return reads to the honesty meta-test as an unregistered (verdict,
    evidence) pair it cannot prove is never PASS, and this helper has no
    registry entry to prove it in (see decisions.py's house rule against
    bare two-value tuples). The one place this judgment is made:
    `commit_disagreement_line` (the prose DISAGREEMENT line field 1
    already carried) and `commit_disagreement_json` (F-D's fix: the same
    finding as a machine-readable top-level `disagreement` key, see
    render_json) both call this rather than each re-deriving the answer,
    so the text surface and the JSON surface can never disagree about
    whether a disagreement exists.

    Either side being silent is not a disagreement, that is the gap field 4
    already reports; inventing a mismatch out of one side's silence would be
    exactly the false confidence this seam exists to prevent.

    R2-A: "do not match" is judged by commitish_agrees above, not by exact
    string equality. The producer schema's own commitish shape is short or
    full hex (`^[0-9a-fA-F]{7,40}$`), so a store receipt's short hash and the
    deposit's full 40-hex of the SAME commit are both legal and were never
    equal as strings; that printed a false DISAGREEMENT for every same-commit
    pair that happened to use different lengths or letter case. This fires
    only when no store commit could possibly be the same commit as the
    deposit's claim."""
    if not store_commits or not isinstance(deposit, dict):
        return None
    change = deposit.get("change")
    if not isinstance(change, dict):
        return None
    raw_head = change.get("headCommit")
    if not isinstance(raw_head, str) or answered(raw_head) is None:
        return None
    head = one_line(raw_head)
    if any(commitish_agrees(head, c) for c in store_commits):
        return None
    return {"commits": sorted(store_commits), "head": head}


def commit_disagreement_line(store_commits, deposit):
    """F4: the prose DISAGREEMENT sentence, printed by name in field 1 rather
    than silently rendering two unrelated provenance claims as though they
    agreed. See `_commit_disagreement_parts` for the shared judgment both
    this and `commit_disagreement_json` read."""
    found = _commit_disagreement_parts(store_commits, deposit)
    if found is None:
        return None
    commits, head = found["commits"], found["head"]
    # I4: the comparison (inside _commit_disagreement_parts) uses the full
    # value, but what PRINTS inside this tool-authored line is capped. The
    # line renders unguarded (H1), so a producer-controlled value inside it
    # is exactly where a length cap earns its keep.
    return ToolAuthored(
        "DISAGREEMENT: the store receipt(s) claim commit(s) %s, the "
        "producer deposit's change.headCommit claims %s; these do not "
        "match, and this passport does not decide which side is right"
        % (", ".join(commits), _capped(head)))


def commit_disagreement_json(store_commits, deposit):
    """F-D fix: the SAME disagreement `commit_disagreement_line` prints as
    prose, as structured data a machine consumer can branch on without
    parsing a sentence. Before this fix `render_json`'s only trace of a
    head-commit conflict was the prose DISAGREEMENT string riding inside
    field 1's `lines`, so a wrong-head deposit still reported `"carried": N,
    "total": N"` with nothing to detect on except reading English out of a
    list of strings -- the exact attack the disagreement line exists to
    catch was invisible to a JSON consumer.

    Returns `{"store": [commit, ...], "producerDeposit": commit}`, naming
    which side asserted which commit by key rather than by prose, or None
    when there is no disagreement (either side silent, or the two sides
    could name the same commit). `None` here is what `render_json` writes
    as a JSON `null`: absence of conflict, not absence of the field."""
    found = _commit_disagreement_parts(store_commits, deposit)
    if found is None:
        return None
    commits, head = found["commits"], found["head"]
    return {"store": commits, "producerDeposit": head}


#: The states a receipt's `ciRunId` field can be in, as TOKENS rather than
#: prose. Two consumers now branch on this field (this module's `origin_of`,
#: which describes it to a human, and `gate_ran` in `tools/sbe_gate.py`, which
#: decides a verdict on it), and a consumer that re-derived the reading from
#: `origin_of`'s SENTENCE would be a substring matcher over prose: the exact
#: shape that made two mirrored `parse_exemption` copies drift apart and cost
#: this project three review defects in one change. One reading, four names.
PRODUCER_BUILD_SYSTEM = "build-system"
PRODUCER_OFF_BUILD_SYSTEM = "off-build-system"
PRODUCER_NOT_ESTABLISHED = "not-established"
PRODUCER_UNREADABLE = "unreadable"


def producer_class(receipt):
    """Which producer state this receipt is in, as one of the four tokens
    above and nothing else.

    THE THREE REAL STATES ARE THREE, not two, and collapsing them is the
    defect this function exists to prevent. A truthy `ciRunId` means something
    outside the machine claimed the run. A key that is PRESENT and null is the
    wrapper saying it ran and no build system was there, which is a laptop.
    An ABSENT key is silence: the receipt predates the field or was typed by
    somebody who never knew it existed, and reporting that as a laptop asserts
    more than the receipt holds.

    THE CEILING, and it is the whole mechanism's ceiling rather than this
    function's: `SBE_CI_RUN_ID` is an environment variable any local shell can
    export, and `src/brothersbe/evidence.py` already states that the seal
    "proves nothing about who produced it". So BUILD_SYSTEM here means THE
    RECEIPT CLAIMS a build system, never that one produced it. A forged id
    reaches this token exactly as easily as a real one.
    """
    if not isinstance(receipt, dict):
        return PRODUCER_UNREADABLE
    if answered(receipt.get("ciRunId")):
        return PRODUCER_BUILD_SYSTEM
    if "ciRunId" in receipt:
        return PRODUCER_OFF_BUILD_SYSTEM
    return PRODUCER_NOT_ESTABLISHED


def origin_of(receipt):
    """Whether a build system or a laptop produced this evidence, which is the
    half of field 3 a commit binding does not answer. `ciRunId` is written by the
    receipt generator when a build system ran the command, so its presence is the
    signal, and a receipt that never carried the field at all is reported as not
    established rather than as a laptop asserted with more confidence than the
    receipt holds.

    The classification is `producer_class` above; this function only puts words
    to it, so the gate and this passport can never disagree about what a
    receipt says while printing sentences that both sound right.
    """
    state = producer_class(receipt)
    if state == PRODUCER_UNREADABLE:
        return "origin unknown: the receipt could not be read as an object"
    if state == PRODUCER_BUILD_SYSTEM:
        return "produced by a build system, run %s" % one_line(receipt.get("ciRunId"))
    if state == PRODUCER_OFF_BUILD_SYSTEM:
        return ("produced OFF a build system: this receipt records no ciRunId, "
                "so a laptop or an unidentified runner produced it")
    return ("origin NOT established: this receipt carries no ciRunId field, so "
            "nothing in it says whether a build system or a laptop ran the command")


def read_receipt(root, task):
    """(data_or_None, why_not). Every refusal path is named, because a receipt
    that cannot be read and a receipt that says nothing are different findings
    and only the second one is the author's fault."""
    resolved = resolve_receipt_path(root, task)
    if resolved.problem is not None:
        return (None, "the receipt binding is ambiguous: %s" % resolved.problem)
    read = load_json_or_none(resolved.path)
    rel = os.path.relpath(resolved.path, root)
    if read.problem == "absent":
        return (None, "no receipt at %s" % rel)
    if read.problem is not None:
        return (None, "the receipt at %s is %s" % (rel, read.problem))
    if not isinstance(read.data, dict):
        return (None, "the receipt at %s is not a JSON object" % rel)
    return (read.data, None)


def walk_store(root, tasks):
    """One pass over the task registry, returning (ran, not_established, files).

    Done in one pass on purpose: fields 3 and 4 are the same walk read two ways,
    and computing them separately is how a check ends up reporting a run it also
    reports as missing."""
    ran, gaps, files = [], [], []
    for task in tasks:
        task_id = one_line(task.get("id")) if answered(task.get("id")) else "(unnamed task)"
        owned = task.get("ownedPaths")
        if answered(owned) and isinstance(owned, list):
            files.append("%s changed %s"
                         % (task_id, ", ".join(sorted(one_line(p) for p in owned))))
        else:
            files.append("%s declared no owned files" % task_id)
        data, why_not = read_receipt(root, task)
        if data is None:
            gaps.append("%s was NOT checked: %s" % (task_id, why_not))
            continue
        command, verdict = command_of(data), verdict_of(data)
        if command is None or verdict is None:
            missing = " and ".join([m for m in (
                None if command is not None else "a command",
                None if verdict is not None else "a verdict") if m])
            gaps.append("%s was NOT checked: its receipt is missing %s" % (task_id, missing))
            continue
        if verdict.strip().upper() == NO_DATA:
            gaps.append("%s was NOT checked: its receipt records NO-DATA for `%s`, "
                        "so nothing was examined" % (task_id, one_line(command)))
            continue
        ran.append("%s: `%s` returned %s, %s"
                   % (task_id, one_line(command), one_line(verdict), origin_of(data)))
    return (ran, gaps, files)


def unbound_receipts(root, tasks):
    """(ran_lines, commits) for receipts in the store that no task names.

    Found by a smoke test against a real dossier: three receipts sat under
    `.sbe/evidence/` while field 3 read "nothing was run", because the walk above
    can only reach a receipt some task points at. Reporting "nothing was run" over
    a directory holding evidence is the failure this project punishes hardest, so
    the store is read as well as the registry. They are marked UNBOUND rather than
    folded in silently: a receipt no task claims satisfies no required check, which
    the receipts themselves already say in their own checkIdSource field."""
    named = set()
    for task in tasks:
        resolved = resolve_receipt_path(root, task)
        if resolved.problem is None:
            named.add(os.path.abspath(resolved.path))
    ran, commits = [], []
    for path, data in scan_evidence_receipts(root):
        if os.path.abspath(path) in named:
            continue
        command, verdict = command_of(data), verdict_of(data)
        rel = os.path.relpath(path, root)
        if command is None or verdict is None:
            continue
        if verdict.strip().upper() == NO_DATA:
            continue
        ran.append("UNBOUND receipt %s: `%s` returned %s, %s. No task in this "
                   "store claims it, so it answers no required check."
                   % (rel, one_line(command), one_line(verdict), origin_of(data)))
        if answered(data.get("headCommit")):
            commits.append(one_line(data["headCommit"]))
    return (ran, commits)


def unbound_gaps(root, tasks):
    """The other half of the same scan: receipts present but unreadable as
    evidence. A receipt that exists and says nothing is a gap, and it belongs in
    field 4 rather than being dropped by the filter above."""
    named = set()
    for task in tasks:
        resolved = resolve_receipt_path(root, task)
        if resolved.problem is None:
            named.add(os.path.abspath(resolved.path))
    gaps = []
    for path, data in scan_evidence_receipts(root):
        if os.path.abspath(path) in named:
            continue
        command, verdict = command_of(data), verdict_of(data)
        rel = os.path.relpath(path, root)
        if command is None or verdict is None:
            gaps.append("the receipt %s establishes nothing: it is missing %s"
                        % (rel, "a command" if command is None else "a verdict"))
        elif verdict.strip().upper() == NO_DATA:
            gaps.append("the receipt %s records NO-DATA for `%s`, so nothing was "
                        "examined by it" % (rel, one_line(command)))
    return gaps


def commit_lines(commits):
    """Field 1's change-identity sentences, from the STORE's own claimed
    commit set (store_commit_set above). Reading git here would answer a
    question about the host, not about the passport, and a passport whose
    identity comes from somewhere the producer never wrote is not the
    producer's claim."""
    if not commits:
        return ["change identity NOT established: no receipt in this store binds "
                "to a commit, so what this passport is about cannot be pinned"]
    return ["bound to commit %s" % c for c in sorted(commits)]


def build_fields(root):
    """(fields, note, disagreement_struct). The five fields, each (state,
    lines), the deposit note, and F-D's structured head-commit disagreement
    (`commit_disagreement_json`'s return: a dict naming both claimed commits
    by side, or None when there is no conflict). Assembled here and
    rendered elsewhere so the JSON form and the text form can never
    disagree about what was found."""
    deposit, note = read_deposit(root)
    # F6: field shapes below are defined for change-passport/v1 alone. A
    # deposit that parsed as JSON but claims a different, absent, or
    # non-string version is read for its note only, never for its field
    # content, so a v2 or garbage shape can never render as a CARRIED line.
    version_problem = schema_problem(deposit)
    usable_deposit = deposit if version_problem is None else None
    if deposit is not None:
        # R2-D: the deposit's own claims and its sensitivity handling marker
        # are read from `deposit` itself, not `usable_deposit`, and printed
        # on BOTH branches below. Neither deposit_claims_note nor
        # sensitivity_note reads the schema version; they only read
        # generatedAt/change/sensitivity, which a refused-version deposit
        # still carries honestly. A v2 deposit marked sensitivity: raw is
        # still unredacted store text, and losing the handling warning just
        # because its schema went unrecognised would be exactly the false
        # safety this note exists to prevent. Only the FIELD content (via
        # usable_deposit, above) stays gated on the recognised schema.
        note = "%s %s" % (note, deposit_claims_note(deposit))
        sensitivity = sensitivity_note(deposit)
        if sensitivity is not None:
            note = "%s %s" % (note, sensitivity)
        if version_problem is not None:
            note = "%s %s" % (note, version_problem)

    tasks = load_tasks(root).tasks
    ran, gaps, files = walk_store(root, tasks) if tasks else ([], [], [])
    loose_ran, loose_commits = unbound_receipts(root, tasks)
    gaps = gaps + unbound_gaps(root, tasks)

    store_commits = store_commit_set(root, tasks, loose_commits)
    identity = commit_lines(store_commits)
    disagreement = commit_disagreement_line(store_commits, usable_deposit)
    # F-D: the same finding, as structured data render_json can carry
    # verbatim. Computed from the same store_commits/usable_deposit inputs
    # as the prose line above (through the shared _commit_disagreement_parts)
    # so the two surfaces can never disagree about whether one exists.
    disagreement_struct = commit_disagreement_json(store_commits, usable_deposit)

    deposit_notes = []  # F1: degraded (hollow/non-string) members, folded into field 4

    def dfield(key, name):
        f = deposit_field(usable_deposit, key, name)
        deposit_notes.extend(f.degraded)
        return f.lines

    # F4: every field-1 line names which side asserted it, store receipt or
    # producer deposit, so a passport for another repo or another commit
    # cannot read as though it were this root's own provenance.
    store_field1 = (["store receipt: %s" % l for l in (identity + files)]
                   if (tasks or loose_commits) else [])
    producer_whatwasdone = dfield("whatWasDone", "what was done")
    producer_field1 = ["producer deposit: %s" % l for l in producer_whatwasdone]
    field1 = store_field1 + producer_field1
    if disagreement is not None:
        field1 = field1 + [disagreement]

    deposit_who_did_it = dfield("whoDidIt", "who did it")
    deposit_what_was_run = dfield("whatWasRun", "what was run")
    deposit_where_it_came_from = dfield("whereItCameFrom", "where it came from")

    out = {
        1: field1,
        2: deposit_who_did_it,
        3: ran + loose_ran + deposit_what_was_run,
        5: deposit_where_it_came_from,
    }

    # F5: a deposit that carries content of its own must be acknowledged by
    # the store-arm sentences below rather than denied by them. The false
    # sentence this finding named, "nothing at all was established here",
    # printed even when a green whatWasRun sat four lines above it on the
    # same page.
    deposit_has_content = bool(producer_field1 or deposit_who_did_it
                               or deposit_what_was_run or deposit_where_it_came_from)

    # Field 4 is assembled last and deliberately widened: everything the walk
    # found unchecked, everything the producer declared unestablished, the
    # producer's own degraded (hollow/non-string) deposit members, AND the
    # provenance fields nothing carries. A field this tool could not fill is
    # itself something that was not established, and reporting that only against
    # field 2 while field 4 reads clean is the shape of the lie.
    not_established = (list(gaps) + deposit_notes
                       + producer_not_established(usable_deposit))
    if version_problem is not None:
        not_established.append(
            "the producer deposit's version is not recognised, so none of "
            "its fields were read: %s" % version_problem)
    for number, name, _key, owner in FIELDS:
        if number in (2, 5) and not out.get(number):
            not_established.append(
                "field %d, %s, is not established: nothing at this seam carries "
                "it, and it is owed by %s" % (number, name, owner))
    if not tasks and not loose_ran:
        if deposit_has_content:
            not_established.append(
                "the store here records nothing: %s carries no task to check "
                "and the evidence store holds no readable receipt; the "
                "deposit above is the only carrier of what this change was"
                % TASKS_REL)
        else:
            not_established.append(
                "nothing at all was established here: %s carries no task to check and "
                "the evidence store holds no readable receipt, so this is an empty "
                "store rather than a clean change" % TASKS_REL)
    elif not tasks:
        if deposit_has_content:
            not_established.append(
                "no task was ever opened here: %s carries no task, so the %d "
                "receipt(s) above answer no required check; the deposit "
                "above carries its own claim about this change, but the "
                "store itself proves nothing" % (TASKS_REL, len(loose_ran)))
        else:
            not_established.append(
                "no task was ever opened here: %s carries no task, so the %d receipt(s) "
                "above answer no required check and nothing states what this change was "
                "supposed to prove" % (TASKS_REL, len(loose_ran)))
    elif not gaps:
        not_established.append(
            "no gap was found among the %d task(s) recorded here, and that is the "
            "whole of what was examined: this tool reads the task registry, its "
            "receipts and the deposit, so anything nobody opened a task for is "
            "outside what this line can speak about" % len(tasks))
    out[4] = not_established

    fields = {}
    for number, _name, _key, _owner in FIELDS:
        lines = out.get(number) or []
        fields[number] = (CARRIED if lines else NO_DATA, lines)
    return (fields, note, disagreement_struct)


def render_text(root, fields, note):
    lines = ["CHANGE PASSPORT (consumer half) for %s" % root, "", note, ""]
    for number, name, _key, owner in FIELDS:
        state, field_lines = fields[number]
        lines.append("%d. %s [%s]" % (number, name.upper(), state))
        if field_lines:
            # F9: content is untrusted, so it is guarded against reading as
            # a forged page header ("5. WHERE IT CAME FROM [CARRIED]") once
            # it lands beside this tool's own real headers on a flat text
            # page. The JSON surface below (render_json) uses the same
            # field_lines unguarded: JSON is structured, so a value that
            # looks like a header there is unambiguous data, not a forgery.
            # H1: a line this module computed itself (ToolAuthored) is not
            # untrusted content and is never defanged; guarding it printed a
            # false "reported value" label on the tool's own finding and hid
            # the real DISAGREEMENT line from any reader scanning for it.
            lines.extend(one_line(l, guard_header=not isinstance(l, ToolAuthored))
                         for l in field_lines)
        else:
            lines.append("NO-DATA: nothing at this seam carries this field. It is "
                         "owed by %s. NO-DATA is not a pass and not a block." % owner)
        lines.append("")
    carried = sum(1 for number, _n, _k, _o in FIELDS if fields[number][0] == CARRIED)
    lines.append("SEAM STATE: %d of %d fields carried, %d NO-DATA."
                 % (carried, len(FIELDS), len(FIELDS) - carried))
    return [one_line(l) if l else "" for l in lines]


def render_json(root, fields, note, disagreement=None):
    """F-D fix: `disagreement` (from build_fields, `commit_disagreement_json`'s
    return) is carried as a top-level `disagreement` key, `null` when there
    is none. Before this fix the ONLY trace of a head-commit conflict was
    the prose DISAGREEMENT string riding inside field 1's `lines`, so a
    machine consumer reading `carried`/`total`/`fields` alone had no way to
    detect the exact attack the disagreement line exists to catch without
    parsing English out of a string list. `carried` and `total` are
    unchanged (they still count fields with content, the same question they
    always answered); this key answers the DIFFERENT question a
    disagreement is about, without a consumer needing to infer it from
    silence on the first two."""
    return json.dumps({
        "root": root,
        "depositNote": note,
        "fields": [{"number": number, "name": name, "key": key, "owedBy": owner,
                    "state": fields[number][0], "lines": fields[number][1]}
                   for number, name, key, owner in FIELDS],
        "carried": sum(1 for number, _n, _k, _o in FIELDS if fields[number][0] == CARRIED),
        "total": len(FIELDS),
        "disagreement": disagreement,
    }, indent=2, sort_keys=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=os.getcwd(),
                    help="the change root holding .sbe/ (default: the current directory)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--out", default=None, help="write here instead of stdout")
    ns = ap.parse_args(argv)
    root = os.path.abspath(ns.root)
    fields, note, disagreement = build_fields(root)
    text = (render_json(root, fields, note, disagreement) if ns.json
            else "\n".join(render_text(root, fields, note))) + "\n"
    if not ns.out:
        sys.stdout.write(text)
        return 0
    try:
        parent = os.path.dirname(os.path.abspath(ns.out))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(ns.out, "w") as fh:
            fh.write(text)
    except OSError as e:
        sys.stderr.write("sbe-passport: could not write %s (%s)\n" % (ns.out, e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
