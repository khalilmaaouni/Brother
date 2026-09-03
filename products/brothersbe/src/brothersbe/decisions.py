"""A decision package written at the moment a decision is taken, quoting the
run that took it, bound to the commit it was taken against.

The defect this exists for, in one sentence: a gate FAIL, a waived control, a
raised tier and a forced close all scroll past in a terminal, so the reason a
change shipped anyway survives only in whoever happened to be watching, and a
week later nobody can say what was decided, on what evidence, or what would
change it.

THE KILL CRITERION THIS MODULE IS BUILT AROUND: this file is a READER and a
WRITER of files, never a second gate runner. Nothing in it recomputes a verdict
over source code and nothing in it starts a gate. It quotes verdict lines the
real tools produced at the moment they were printed, records what it could not
read as NO-DATA naming the store that would fill it, and binds the package to
the head commit it was written against. If a truthful package cannot be
produced without re-running somebody else's check, the section says NO-DATA
instead of running it.

WHAT A PACKAGE MAY AND MAY NOT CARRY, stated because a package is SHARED:

  - It quotes the verdict line it was handed, and nothing else. Every other
    line from the triggering run is COUNTED and discarded, never copied. The
    evidence receipts persist digests rather than raw output for exactly this
    reason (see `evidence.py`), and a new artifact that everybody is
    encouraged to paste into a pull request must not quietly widen that
    policy: an unmatched line may carry a token, a connection string or a
    customer row.
  - Absent evidence renders NO-DATA naming the file or store that would fill
    it. A package never reads as a clean verdict over something nobody
    examined, and a waived control is recorded WAIVED.
  - It carries the DECIDING CODE: the shipped check's own function, excerpted
    verbatim with the file and the first and last line numbers beside it, plus
    a Mermaid flowchart of what that check's registry entry DECLARES about how
    it decides. Both are read from this project's own source under `tools/`,
    which already ships in the open, so the sharing policy above is not
    widened by them: no customer file, no run output and no environment is
    read to build either one. A check no registry declares gets NO-DATA in
    both sections and no invented span, because a span nobody read sends a
    reviewer to the wrong lines, which is worse than printing none.

WHERE A PACKAGE LANDS, and why it says so in its own header:

  dossier         <dossier>/decisions/NNN-<slug>/DECISION.md
  repository      <repo top>/.sbe/decisions/NNN-<slug>/DECISION.md

The dossier location is used only when the checked directory carries a
`00-intake.json`, because that file is what names the project. With no intake
there is no project to name, so nothing invents one and the package goes to
the repository store instead, saying which of the two it is in and why.

`sbe explain` READS THIS STORE. It prints the package a decision id names, or
the newest package for a check that is bound to the current head. With none
bound to this head it REGENERATES one from the shipped registry, allocates the
next id, and writes it through the one writer here. It never edits a package
that already exists: `write_package` refuses to overwrite a `DECISION.md` bound
to a different commit, and supersession is recorded in the NEW package, because
the older one records what somebody decided about a different program and a
rewrite would delete that with no record of the deletion.

NO HELPER IN THIS MODULE RETURNS A BARE TWO-VALUE TUPLE. Every helper returns
one dict with named keys, or a single value. A two-value return reads as a
possible `(verdict, evidence)` pair to the honesty meta-test in
`evals/test_no_data_class.py`, which refuses any such function sitting outside
a check registry. Where a caller wants two things it reads two keys off one
dict. Stated here so the next writer does not reintroduce it.

Nothing here constructs a git merge, rebase, push or deploy. The only git this
module runs is `rev-parse` and, for `sbe lineage`, a read of `git log
--follow`, both through the `impact._git` helper the rest of the package
already uses rather than a second copy of the plumbing. Reading the
deciding code and drawing the flowchart start NO process at all: they import
the shipped registries and read source files with `inspect`, and
`tools/test_sbe_decisions.py::
test_neither_helper_starts_a_subprocess_to_read_the_logic` holds that by
closing `_git`, this module's only door to a child process, and demanding an
answer anyway.

Python floor is 3.9: no match statements, no `X | Y` annotations. Standard
library only. Maturity: INTERNAL-EVAL, exercised on this repository's fixtures
and on no other estate.
"""
import argparse
import contextlib
import inspect
import io
import json
import os
import re
import stat
import sys
import tempfile
import time
from importlib.machinery import SourceFileLoader

try:
    import fcntl
except ImportError:          # every non-POSIX platform (Windows: msvcrt below)
    fcntl = None
try:
    import msvcrt
except ImportError:
    msvcrt = None             # `write_package` refuses to write unlocked rather than
                       # reopen the race the lock exists to close)

# `_git` is the same private helper `status.py` reuses. `_tier_index` is
# imported for the same reason rather than re-spelled: the tier ORDER is
# `impact.py`'s rule, and a second copy of it here would decide "is this a
# raise?" differently from the tool that printed the report.
from .impact import _git, _tier_index  # noqa: E402
from . import cwd as cwd_mod
from . import contracts as contracts_mod
#: The one tier tuple `close_durations_by_tier` buckets by; `policy.py` owns
#: it, and a second copy here could silently drift from `sbe policy`'s own.
from .policy import TIERS  # noqa: E402

#: `_toolspath.mount()`, the one mechanism every converged module shares, is
#: called LAZILY, inside `registries()`, never here: this module is imported
#: by a module under `tools/` (`tools/test_sbe_decisions.py`), so mounting
#: `tools/` at module scope would be a cycle.
from ._toolspath import mount

#: Where packages live under a dossier, and under the repository root when
#: there is no dossier to name. Both spelled once, here, so a writer and a
#: reader of the store cannot drift into two different stores.
DECISIONS_REL_DOSSIER = "decisions"
DECISIONS_REL_ROOT = os.path.join(".sbe", "decisions")

#: The file that names a project. Its ABSENCE is what sends a package to the
#: repository store; see the module docstring.
INTAKE_REL = "00-intake.json"

#: The four moments that write a package. A trigger naming something else is
#: still recorded, with a note saying it is outside this list: refusing to
#: write would lose the decision, and normalizing it in silence would hide
#: that a caller is writing packages for a moment nobody declared.
TRIGGER_KINDS = ("gate", "waiver", "tier", "forced-close")

#: The kind a package REGENERATED by `sbe explain` records. Deliberately outside
#: `TRIGGER_KINDS` above: those four are the moments a decision is taken, and
#: asking for an explanation is not one of them. `build_package` notices that it
#: is outside the four and says so in the package's own notes, which is the
#: honest record: that file was written because somebody asked, not because
#: anything was decided. The sections that would otherwise claim a run are
#: written differently for it, in `render_package` and `_evidence_lines`.
EXPLAIN_KIND = "explain"

PACKAGE_FILENAME = "DECISION.md"

#: The four verdict words this project ships. This module introduces none: it
#: REPORTS the ones the checks themselves printed.
VERDICT_WORDS = ("PASS", "FAIL", "NO-DATA", "WAIVED")

#: The two that are a decision. A PASS decided nothing anybody has to carry,
#: and a NO-DATA decided nothing either: it records that nothing was examined,
#: which is a finding for the gate to print and not a decision to package.
PACKAGE_WORTHY = ("FAIL", "WAIVED")

#: THE VERDICT-LINE GRAMMAR, copied from the printers rather than remembered.
#: The three lines it was copied from, each opened before this was written:
#:
#:   tools/sbe_gate.py:1524   say("  %-9s %-8s %s [severity: %s]"
#:                                % (name, verdict, one_line(ev), check.severity))
#:   tools/sbe_gate.py:1504   say("  %-9s %-8s %s" % (">> " + name, "WAIVED", ...))
#:   tools/sbe_score.py:1380  say("%-*s  %-7s  %s [severity: %s]"
#:                                % (width, n, v, one_line(e), CHECKS[n].severity))
#:
#: Three printers, one shape: an optional indent, a check name optionally
#: prefixed with ">> " for a waiver, whitespace, one of the four verdict words,
#: and the evidence. The padding widths differ between the tools and are not
#: matched here on purpose: matching a column count would make this parser go
#: blind the day somebody renames a check to a longer name, which is the
#: silent-narrowing defect this project keeps finding one level lower.
_VERDICT_LINE_RE = re.compile(
    r"^[ \t]*(?:>>[ \t]+)?(?P<check>\S+)[ \t]+(?P<verdict>%s)(?:[ \t]+(?P<evidence>.*))?$"
    % "|".join(VERDICT_WORDS))

#: The header lines a package writes about itself and a reader reads back off
#: the file. Each prefix is spelled ONCE and used by both `render_package` and
#: the readers below, so the writer and the reader of a store cannot drift into
#: two different spellings and leave `sbe explain` blind to packages this same
#: module wrote.
_BOUND_PREFIX = "- bound to commit: "
_BOUND_RE = re.compile(r"^%s(\S+)\s*$" % re.escape(_BOUND_PREFIX), re.M)
_ID_PREFIX = "- id: "
_ID_RE = re.compile(r"^%s(\S+)[ \t]*$" % re.escape(_ID_PREFIX), re.M)
_CHECK_PREFIX = "- check: "
_CHECK_RE = re.compile(r"^%s(.*)$" % re.escape(_CHECK_PREFIX), re.M)
_SUPERSEDES_PREFIX = "- supersedes: "

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


class DecisionUnwritable(Exception):
    """The package could not be written, so nothing was recorded. Raised
    rather than returning a path that does not exist: a decision nobody can
    open is not a decision that was taken."""


def _iso(epoch):
    """ISO 8601 in UTC, to the second, with an explicit Z: the same spelling
    the evidence receipts and the task registry use."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _slug(text, fallback):
    """A directory-safe slug, lowercased, with every run of other characters
    collapsed to one dash. Empty input yields the caller's fallback rather
    than an empty directory name nobody can read."""
    slug = _SLUG_STRIP_RE.sub("-", str(text or "").lower()).strip("-")
    return slug or fallback


# ---------------------------------------------------------------------------
# BAND R: the plan section 9.2 plain-language ask, and the one function that
# binds a human's answer to it. Neither computes a verdict of its own: the
# readiness state comes from `lifecycle.reduce_readiness`, and the binding
# comes from `contracts.decision_binds`, exactly the "never a second gate
# runner" rule the module docstring states for the packages above. This
# section only composes and renders what those two already decided.
# ---------------------------------------------------------------------------

#: The plan section 9.2 labels, in the order they are rendered. Named here
#: once so `render_decision_packet` and its own test can both read the exact
#: spelling rather than one of them guessing at the other's wording.
DECISION_PACKET_LABELS = ("Decision needed:", "Why now:", "What Brother established:",
                          "What remains unknown:", "Options:", "Recommendation:",
                          "Safe default if no answer:", "Needed person:")


def _blank(value):
    """True when `value` records nothing this renderer can print: not a
    string at all, or a string `tools/sbe_checks.py`'s own `answered()` reads
    as recording no answer.

    Round three of one defect. Rounds one and two each held a private rule
    (`isinstance(str) and not strip()`, then a locally spelled token list,
    `_VACUOUS_TOKENS`) that this comment used to claim was "held in
    agreement with `tools/sbe_checks.py`'s own list by test only" -- a test
    that was never written, so the two lists drifted: `sbe_checks.py`'s own
    `VACUOUS_VALUES` carries 34 tokens plus a no-alphanumeric rule (a value
    with no letter or digit anywhere, whatever the punctuation spells,
    records nothing; "#" is refused the same way "-", "--", "?" and "???"
    already were), and the local copy here carried 10 and had no such rule,
    so `assertedCause: "#"`, `"??"`, `"tba"`, `"tbc"`, `"fixme"`, `"xxx"`,
    `"nil"`, `"foo"`, `"bar"`, `"ditto"`, `"see above"`, `"not decided"` or
    `"unclear"` all rendered here as if they were real answers.

    Rather than spell a second list that can drift again, this calls the
    one function every other vacuity test in this project already answers
    to, imported the same lazy way `registries()` below already imports
    `sbe_checks.Check` and `Pruner`: `_toolspath.mount()` cannot run at this
    module's own import time (see the module-scope comment on `from
    ._toolspath import mount`), so it runs here, at call time, instead.

    Every identity value this project binds on is held to "answered, not
    merely present" for the same reason `contracts.py`'s round-two
    typed-identity rule holds a head commit or a human name to being an
    actual string (`_identity_type_problem`: a numeric or boolean value
    would answer a question it never meant to by nothing more than Python's
    own cross-type equality); this renderer holds every printed field to the
    same bar. Non-string values (`[]`, `{}`, `0`, `False`) are blank here
    directly, before `answered()` is even reached: it is a text renderer,
    and a value that never was text answers nothing it can print."""
    if not isinstance(value, str):
        return True
    mount()
    from sbe_checks import answered  # noqa: E402
    return answered(value) is None


def _readiness_sentence(readiness):
    """One plain sentence stating the readiness state `reduce_readiness`
    (or an equivalent caller) derived, for the reader who opens this packet
    without already knowing the code. Never a verdict word by itself:
    `contracts.READINESS_STATES` is a lifecycle state, not a check verdict
    (see `contracts.py`'s own note on that distinction), so this sentence
    says it in words instead of printing the constant."""
    state = readiness.get("readinessState")
    reasons = [str(r) for r in (readiness.get("reasons") or ()) if str(r).strip()]
    joined = "; ".join(reasons)
    if state == "READY_FOR_HUMAN_DECISION":
        return "Readiness: this change is ready for a human decision."
    if state == "READY_WITH_KNOWN_RISK":
        return ("Readiness: this change is ready, with known risk%s."
                % (" (%s)" % joined if joined else ""))
    if state == "NOT_READY":
        return ("Readiness: this change is not ready yet%s."
                % (" (%s)" % joined if joined else ""))
    return "Readiness: %r, a state this renderer does not recognize." % (state,)


def render_decision_packet(packet, readiness):
    """The plan section 9.2 plain-language ask, as one block of text a human
    can read without opening any other file.

    `packet` carries the `contracts` decision-packet fields (`question`,
    `knownRisks`, `notEstablished`; `readinessState` is read from `readiness`
    below, not from the packet's own copy, so a caller that only has the
    freshly DERIVED state -- the whole point of `lifecycle.reduce_readiness`
    -- never has to fake a packet field to get a sentence out of this
    function) plus the fields this renderer alone reads: `whyNow`,
    `established` (a list), `options` (a list of `{"label": ..., "text":
    ...}` dicts), `recommendation`, `safeDefault` and `neededPerson`.
    `readiness` is the dict `lifecycle.reduce_readiness` returns.

    REFUSES to print a packet with a hollow spot in it, raising `ValueError`
    naming the field, rather than rendering a page with a blank where an
    answer belongs: `question`, `recommendation`, `safeDefault` and
    `neededPerson` must each answer something, and `notEstablished` must be
    a non-empty list of non-hollow items. `notEstablished` is mandatory and
    never empty for the same reason `validate_passport`'s `whatWasNotEstablished`
    is: a packet claiming nothing is unexamined is the lie this chain exists
    to prevent, and a list holding a hollow entry (`""`, `None`, a stray
    non-string) is that same lie one level down: it PRINTS as an unexamined
    item while recording nothing, so it is refused rather than silently
    dropped (dropping it could empty the one list that must never be empty).

    ALSO REFUSES when `packet["readinessState"]` does not name the same state
    as `readiness["readinessState"]`: `readiness` is the freshly DERIVED
    state (`lifecycle.reduce_readiness`'s own return), and a packet stamped
    with a different state describes a program `reduce_readiness` was never
    asked about, or was asked about and answered differently. Rendering the
    packet's own stale state as if it agreed with the derived one is the same
    class of lie a HOLD laundered as a RELEASE is: two answers to the same
    question, silently let disagree.

    Plain words only: no jargon, and no em dash or en dash anywhere in the
    rendered text.

    Prints EXACTLY the eight `DECISION_PACKET_LABELS` above, in that order,
    each starting its own line, so a caller (or a test) can find every
    block by its label text.
    """
    packet_state = packet.get("readinessState")
    derived_state = readiness.get("readinessState")
    if packet_state != derived_state:
        raise ValueError(
            "render_decision_packet refuses to print: the packet's own readinessState "
            "(%r) does not match the state reduce_readiness just derived (%r); a packet "
            "stamped one state must never render over a reducer result that says "
            "another" % (packet_state, derived_state))
    for field in ("question", "recommendation", "safeDefault", "neededPerson"):
        if _blank(packet.get(field)):
            raise ValueError("render_decision_packet refuses to print with %r empty or "
                             "missing: a decision ask with a blank where an answer belongs "
                             "is not an ask a human can act on" % (field,))
    not_established = packet.get("notEstablished")
    if not isinstance(not_established, (list, tuple)) or not not_established:
        raise ValueError("render_decision_packet refuses to print with notEstablished "
                         "empty or missing: a packet claiming nothing is unexamined is "
                         "the lie this chain exists to prevent")
    for index, item in enumerate(not_established):
        if _blank(item):
            raise ValueError("render_decision_packet refuses to print: notEstablished[%d] "
                             "records nothing (%r); a hollow item is filtered silently "
                             "nowhere in this project, because silent filtering could "
                             "empty the one list that must never be empty"
                             % (index, item))

    lines = [_readiness_sentence(readiness), ""]
    lines.append("Decision needed:")
    lines.append("  %s" % packet["question"])
    lines.append("")
    lines.append("Why now:")
    why_now = packet.get("whyNow")
    lines.append("  %s" % (why_now if not _blank(why_now) else "(not stated)"))
    lines.append("")
    lines.append("What Brother established:")
    established = [str(item) for item in (packet.get("established") or ())
                   if str(item).strip()]
    if established:
        lines.extend("  - %s" % item for item in established)
    else:
        lines.append("  (nothing recorded)")
    lines.append("")
    lines.append("What remains unknown:")
    lines.extend("  - %s" % item for item in not_established)
    known_risks = [str(item) for item in (packet.get("knownRisks") or ())
                   if str(item).strip()]
    lines.extend("  - known risk: %s" % item for item in known_risks)
    lines.append("")
    lines.append("Options:")
    options = list(packet.get("options") or ())
    if options:
        for option in options:
            label = option.get("label") or "(unlabeled option)"
            text = option.get("text") or "(no detail recorded)"
            lines.append("  - %s: %s" % (label, text))
    else:
        lines.append("  (no options recorded)")
    lines.append("")
    lines.append("Recommendation:")
    lines.append("  %s" % packet["recommendation"])
    lines.append("")
    lines.append("Safe default if no answer:")
    lines.append("  %s" % packet["safeDefault"])
    lines.append("")
    lines.append("Needed person:")
    lines.append("  %s" % packet["neededPerson"])
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# BAND F, F4: the reproduce-first defect record. A defect record is a THIRD
# thing this module renders, beside a decision package and the section 9.2
# packet above: it is not bound to a commit the way `build_package` is, and
# it carries no `readinessState` the way the packet does. What it shares with
# both is the one rule this module holds every rendered field to:
# `render_decision_packet` refuses to print a blank where an answer belongs
# (`_blank`, already defined above) rather than let a reader mistake an
# unanswered field for one nobody asked. This section holds a SHARPER version
# of that same rule for one field: `assertedCause` is never printed as if it
# were established, because it is a claim, not a fact, until something
# reproduces the symptom.
#
# THE DEFECT THIS EXISTS FOR: a bug report that asserts a cause reads,
# to anyone skimming it later, exactly like a bug report whose cause was
# CONFIRMED, because English does not mark the difference and neither did
# any record this project wrote before this one. `proofplan.py`'s own module
# docstring names the identical shape one layer up, over checks rather than
# over bugs: a claim that could not be recomputed against the world as it
# stands now is refused, never read hopefully. Here, "recomputed against the
# world" means "reproduced": REPRODUCTION COMES FIRST, literally, in the
# rendered order below, and the Verdict line's cause reads `CAUSE: NO-DATA`
# whenever `reproduction` is blank, WHATEVER `assertedCause` says.
# ---------------------------------------------------------------------------

#: The defect record's own fixed labels, one per rendered line, mirroring
#: `DECISION_PACKET_LABELS` above: named once so `render_defect_record` and
#: its own test read the exact spelling rather than one of them guessing at
#: the other's wording.
DEFECT_RECORD_LABELS = ("Symptom:", "Reproduction:", "Asserted cause:", "Verdict:")


def _render_field_block(value):
    """`value`, stripped, split on every line boundary `str.splitlines()`
    recognizes (`\\n`, `\\r`, `\\r\\n`, `\\v`/`\\x0b`, `\\f`/`\\x0c`, `\\x1c`,
    `\\x1d`, `\\x1e`, `\\x85`, `\\u2028`, `\\u2029`), and every line indented
    two spaces so no continuation line can ever sit at the column this
    renderer's own labels print at (column 0, `DEFECT_RECORD_LABELS`
    appended to `lines` verbatim, unindented). Any line that would otherwise
    OPEN with one of those four label spellings has that label's own colon
    broken (a space inserted before it, "Verdict:" -> "Verdict :"). The
    guarantee is exactly this: at most one line per label sits at column 0
    opening with the label spelling, so a reader that matches labels at
    LINE STARTS (splitlines then startswith) can never be fooled. A label
    substring in the MIDDLE of a field's line ("see the Verdict: ...") is
    not de-labeled, so a naive whole-text `text.split("Verdict:")` can
    still be fooled by it; readers must anchor on line starts, and
    round three of the evidence audit (R3-1) is why this sentence says so.

    F-4a's fix, round two: a multi-line `assertedCause` (equally, `symptom`
    or `reproduction`) containing its own "Verdict:\\n  CAUSE: ..." lines
    used to render a forged Verdict block INSIDE the Asserted cause section,
    ahead of the real one. Round one's fix indented every line but split
    only on the literal `"\\n"` character (`text.split("\\n")`); an attack
    string built on any OTHER line terminator Python itself recognizes as a
    line break -- a bare `\\r`, `\\x0b`, `\\x0c`, `\\x85`, `\\u2028`,
    `\\u2029` and the rest of the boundary list above -- was never split at
    all, so the embedded "Verdict:" text stayed on what `split("\\n")` saw
    as a single unsplit line, sitting at column 0 the instant a `\\r` in the
    middle of it moved a real terminal's cursor back to the start of the
    line: the same forgery round one believed it had closed. Round one's own
    docstring called that "impossible by construction" while splitting on
    one literal character out of the eleven boundaries the interpreter
    recognizes; that claim was false as written. Splitting with
    `str.splitlines()` instead of `split("\\n")` closes the actual gap,
    because it is the same function Python itself uses to decide what
    counts as a line: nothing this renderer treats as one line can straddle
    a boundary `str.splitlines()` would also treat as a break, so the
    forgery is unbypassable by any input that is more than one line by
    Python's own definition, which is the guarantee this function actually
    makes, no wider and no narrower."""
    text = str(value).strip()
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        for label in DEFECT_RECORD_LABELS:
            if stripped.startswith(label):
                colon = label.index(":")
                stripped = "%s %s%s" % (label[:colon], label[colon:], stripped[len(label):])
                break
        out.append("  %s" % stripped if stripped else "")
    return "\n".join(out)


def render_defect_record(defect):
    """A defect record where the reproduction comes first, literally: this
    renderer will not print a cause as established without first printing
    whether anyone reproduced the symptom, and the two lines sit right next
    to each other so a reader sees both at once.

    `defect` carries `symptom` (required, answered: the same `_blank` bar
    `render_decision_packet` already holds its own required fields to),
    `reproduction` (the exact command or steps that reproduce the symptom,
    or blank/absent when nobody has reproduced it yet) and `assertedCause`
    (the human's belief about what is wrong; optional, and NEVER treated as
    a fact by itself).

    THE RULE: the rendered Verdict line reads `CAUSE: <assertedCause>` only
    when BOTH `reproduction` answers something AND `assertedCause` answers
    something. Whenever `reproduction` is blank, the Verdict line reads
    `CAUSE: NO-DATA`, and the asserted cause, if there is one, is still
    printed on its own labeled line above the verdict so a reader can see
    the claim and see that it was never confirmed, side by side, rather than
    have it silently dropped. This is the one behavior this section exists
    to guarantee: a defect carrying an asserted cause but no reproduction
    never has that cause echoed as fact in its rendered verdict.

    Refuses (`ValueError`, naming the field, the same refusal shape
    `render_decision_packet` already uses) to render with `symptom` blank: a
    defect record about nothing is not a record.

    Plain words only: no jargon, and no em dash or en dash anywhere in the
    rendered text.
    """
    symptom = defect.get("symptom")
    if _blank(symptom):
        raise ValueError(
            "render_defect_record refuses to print with 'symptom' empty or missing: a "
            "defect record about nothing is not a record")

    reproduction = defect.get("reproduction")
    asserted_cause = defect.get("assertedCause")
    has_reproduction = not _blank(reproduction)
    has_asserted_cause = not _blank(asserted_cause)

    lines = [DEFECT_RECORD_LABELS[0], _render_field_block(symptom), ""]

    lines.append(DEFECT_RECORD_LABELS[1])
    if has_reproduction:
        lines.append(_render_field_block(reproduction))
    else:
        lines.append("  NO-DATA: nobody has reproduced this defect yet")
    lines.append("")

    lines.append(DEFECT_RECORD_LABELS[2])
    if has_asserted_cause:
        lines.append(_render_field_block(asserted_cause))
    else:
        lines.append("  NO-DATA: no cause has been asserted")
    lines.append("")

    lines.append(DEFECT_RECORD_LABELS[3])
    if has_reproduction and has_asserted_cause:
        cause_block = _render_field_block(asserted_cause)
        first_line, _, rest = cause_block.partition("\n")
        lines.append("  CAUSE: %s" % first_line.strip())
        if rest:
            lines.append(rest)
    elif not has_reproduction:
        lines.append(
            "  CAUSE: NO-DATA (reproduction is absent, so the asserted cause is never echoed "
            "as fact here, whatever it claims)")
    else:
        lines.append("  CAUSE: NO-DATA (no cause has been asserted)")
    return "\n".join(lines) + "\n"


class _EvidenceText(str):
    """An evidence line that also carries, as one named attribute past its own
    text, whether the decision it describes authorizes release.

    A `str` subclass rather than a fourth tuple slot: every reader of
    `bind_human_decision`'s result, this project's own reviewer fixtures
    included, unpacks it as `verdict, evidence, problems = ...` or reads
    `result[0]`, exactly the house 3-tuple `contracts.validate`/`contracts.
    decision_binds` already return, and a 4-tuple would break every one of
    them. `os.stat_result` and `time.struct_time` carry the same shape of
    fix: stay the sequence old callers already unpack, add the new fact as a
    named attribute past it. A caller that only reads `evidence` as text (the
    old field) sees the same string either way; one that wants the boolean
    reads `evidence.authorizes` instead of re-parsing the sentence for
    "does NOT authorize".
    """

    def __new__(cls, text, authorizes):
        self = str.__new__(cls, text)
        self.authorizes = authorizes
        return self


def bind_human_decision(decision, packet, head_commit):
    """Does this human decision actually authorize release, over THIS packet
    at THIS head? The one place a caller asks both questions at once rather
    than calling `contracts.validate` and `contracts.decision_binds`
    separately and forgetting one.

    THE TWO QUESTIONS ARE DIFFERENT AND BOTH MUST BE LEGIBLE. Binding is "did
    a real human, over this exact packet, at this exact head, answer at
    all?" A HOLD binds exactly as cleanly as a RELEASE does: refusing a
    well-bound HOLD would be its own defect (a human who said wait would find
    their answer rejected as though they said nothing). Authorization is a
    second, separate question this function used to never ask: whether the
    word the human wrote is `contracts.DECISION_AUTHORIZING_RELEASE`. Round
    one returned the bare binding verdict and called it done, so a HOLD and a
    RELEASE that both bind read as byte-identical evidence, and a caller that
    trusted the verdict word alone read a HOLD as clearance. Now the evidence
    sentence NAMES the decision word and its consequence, and the returned
    evidence also carries an `authorizes` attribute (see `_EvidenceText`) so
    a caller never has to re-derive it or re-parse the sentence: `True` only
    when the decision reads exactly `contracts.DECISION_AUTHORIZING_RELEASE`,
    `False` on a well-bound HOLD and on every refusal, because a decision
    that does not even bind authorizes nothing.

    REQUIRES `head_commit`. `contracts.decision_binds` accepts `head_commit=
    None` and silently skips comparing the decision against the change's
    CURRENT head when it is (that function's own docstring: "when it is None
    only the two documents are compared against each other, which is the
    weaker question"), because that parameter is optional there for callers
    that genuinely only have the two documents. This surface is not one of
    those callers: it is asked "does this decision authorize release right
    now", which always has a current head to compare against, and a caller
    that cannot name one has no business asking the question at all. So a
    hollow `head_commit` (`None`, or a string that is empty or whitespace) is
    refused HERE, before either composed call runs, naming the rule, rather
    than silently passed through to the weaker question `decision_binds`
    would otherwise answer without saying it was asked the weaker one.

    Composes exactly two calls after that and adds no logic of its own beyond
    the authorization read above: `contracts.validate("human-decision",
    decision)` first (catching a document that is not even a valid human
    decision on its own face: wrong producer class, missing identity, an
    invented `decision` word), and, only when that PASSES, `contracts.
    decision_binds(decision, packet, head_commit)` (does THIS decision answer
    THIS packet at THIS head). A refusal from either is returned with its
    evidence text UNCHANGED (only wrapped in `_EvidenceText` with `authorizes
    =False`, since nothing that failed to bind authorizes anything), so a
    refusal from either reads exactly as that function's own refusal reads,
    with no third wording invented here for the same case.

    Never computes a digest and never recomputes a binding rule itself: see
    the module docstring's "never a second gate runner" law, and `contracts.
    canonical_digest`'s own note that this project holds one place that
    digests a document so two callers cannot silently disagree about the
    bytes.
    """
    if head_commit is None or not isinstance(head_commit, str) or not head_commit.strip():
        return ("FAIL",
                _EvidenceText(
                    "bind_human_decision refuses: the current head is required; a "
                    "binding that skips the head check must say so, and this surface "
                    "never skips it (head_commit=%r)" % (head_commit,), False),
                ("head_commit is required and must name something; None or an empty "
                 "string is what a failed `git rev-parse` hands a caller, and this "
                 "surface never passes either one through to the weaker two-document "
                 "question `contracts.decision_binds` answers when head_commit is "
                 "omitted",))
    verdict, evidence, problems = contracts_mod.validate("human-decision", decision)
    if verdict != "PASS":
        return verdict, _EvidenceText(evidence, False), problems
    verdict, evidence, problems = contracts_mod.decision_binds(decision, packet, head_commit)
    if verdict != "PASS":
        return verdict, _EvidenceText(evidence, False), problems

    answer = decision.get("decision")
    authorizes = answer == contracts_mod.DECISION_AUTHORIZING_RELEASE
    if authorizes:
        evidence = "%s; decision %s by %r authorizes release" % (
            evidence, answer, decision.get("producer"))
    else:
        evidence = "%s; decision %s by %r binds and does NOT authorize release" % (
            evidence, answer, decision.get("producer"))
    return verdict, _EvidenceText(evidence, authorizes), problems


def repo_top_of(root):
    """The repository top for `root`, as a dict with keys `top` and `note`.

    A directory git cannot answer for is NOT fatal here: the package is
    written under the directory the caller named, and `note` carries the
    NO-DATA sentence the package prints, so a reader can see the package was
    filed relative to a directory rather than to a repository.
    """
    absolute = os.path.abspath(root)
    try:
        code, out, err = _git(["rev-parse", "--show-toplevel"], absolute)
    except OSError as exc:
        return {"top": absolute,
                "note": "NO-DATA: git could not be run in %s (%s), so this package was "
                        "filed under the directory the caller named rather than the "
                        "repository top" % (absolute, exc)}
    if code != 0 or not out.strip():
        return {"top": absolute,
                "note": "NO-DATA: git cannot answer for %s (%s), so this package was "
                        "filed under the directory the caller named rather than the "
                        "repository top" % (absolute, err.strip() or "no toplevel")}
    return {"top": out.strip(), "note": ""}


def head_commit_of(root):
    """The head commit `root` sits on, as a dict with keys `commit` and `note`.

    `commit` is None when git cannot answer, and `note` says so in NO-DATA
    words. None is a real answer here, never guessed at: a package bound to a
    commit nobody resolved would claim to describe a program it never saw.
    """
    absolute = os.path.abspath(root)
    try:
        code, out, err = _git(["rev-parse", "HEAD"], absolute)
    except OSError as exc:
        return {"commit": None,
                "note": "NO-DATA: git could not be run in %s (%s), so this package is "
                        "bound to no commit and says nothing about which program was "
                        "checked" % (absolute, exc)}
    if code != 0 or not out.strip():
        return {"commit": None,
                "note": "NO-DATA: no head commit resolved in %s (%s), so this package is "
                        "bound to no commit and says nothing about which program was "
                        "checked" % (absolute, err.strip() or "no HEAD")}
    return {"commit": out.strip(), "note": ""}


def package_location(top, dossier):
    """Where a package goes, as a dict with keys `dir`, `location` and `reason`.

    The reason is printed in the package's own header, because a reader who
    finds a package in the repository store needs to know it landed there
    because no project could be named, not because somebody filed it wrong.
    """
    if dossier:
        intake = os.path.join(os.path.abspath(dossier), INTAKE_REL)
        if os.path.isfile(intake):
            return {"dir": os.path.join(os.path.abspath(dossier), DECISIONS_REL_DOSSIER),
                    "location": "dossier",
                    "reason": "%s names the project this decision belongs to" % intake}
        return {"dir": os.path.join(top, DECISIONS_REL_ROOT),
                "location": "repository store",
                "reason": "the checked directory %s carries no %s, so there is no project "
                          "to name and nothing invents one"
                          % (os.path.abspath(dossier), INTAKE_REL)}
    return {"dir": os.path.join(top, DECISIONS_REL_ROOT),
            "location": "repository store",
            "reason": "the run that triggered this package named no dossier, so there is "
                      "no project to name and nothing invents one"}


#: The lock sidecar's own suffix. A SIBLING of the store directory, never a
#: file placed INSIDE it: `list_packages` treats every entry of a decisions
#: directory that is not a `NNN-<slug>` directory as a NO-DATA problem to
#: report, so a lock file living inside the store would show up as one on
#: every browse. `decisions_dir + _LOCK_SUFFIX` sits beside it instead.
_LOCK_SUFFIX = ".lock"

#: How long `_store_lock` waits for the exclusive lock before giving up.
#: Bounded rather than infinite so a stuck holder cannot wedge every later
#: writer forever.
_LOCK_TIMEOUT_S = 30.0


@contextlib.contextmanager
def _store_lock(decisions_dir):
    """Exclusive advisory lock serializing every writer of ONE decision
    store, held for a whole id-allocate-and-write critical section rather
    than only the final write.

    This mirrors `tools/sbe_telemetry.py`'s `_writer_lock`: an `fcntl.flock`
    taken on a sidecar file beside the thing being protected, spun for up to
    `_LOCK_TIMEOUT_S` before giving up. It is not the SAME function, because
    the two callers want opposite failure behavior. Telemetry's writer must
    never drop a row, so a timeout or a missing `fcntl` yields an UNLOCKED
    pass and the caller proceeds anyway. This lock exists so two writers can
    no longer claim the same decision id, and proceeding unlocked would
    reopen exactly that race, so a timeout or a missing `fcntl` here raises
    `DecisionUnwritable` instead of yielding: nothing is recorded rather than
    recorded unprotected.
    """
    if fcntl is None and msvcrt is None:
        raise DecisionUnwritable(
            "this platform has neither fcntl nor msvcrt, so the decision store at %s "
            "cannot be locked for a safe write; nothing was recorded rather than "
            "writing unprotected against a concurrent writer" % decisions_dir)
    try:
        os.makedirs(decisions_dir, exist_ok=True)
    except OSError as exc:
        raise DecisionUnwritable("%s cannot be created (%s); nothing was recorded"
                                 % (decisions_dir, exc))
    lock_path = decisions_dir.rstrip(os.sep) + _LOCK_SUFFIX
    try:
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o644)
    except OSError as exc:
        raise DecisionUnwritable(
            "the lock sidecar %s could not be opened (%s); nothing was recorded rather "
            "than writing unprotected against a concurrent writer" % (lock_path, exc))
    try:
        held = False
        deadline = time.time() + _LOCK_TIMEOUT_S
        while True:
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    # Same byte-range shape as tools/sbe_telemetry.py's writer
                    # lock (pad to one byte, anchor at offset 0, lock [0, 1)):
                    # msvcrt regions anchor at the CURRENT position, so every
                    # opener must target the same range or two writers never
                    # contend. The refusal-on-timeout policy above is this
                    # store's own and unchanged.
                    if os.fstat(fd).st_size < 1:
                        os.write(fd, b"0")
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                held = True
                break
            except OSError:
                if time.time() >= deadline:
                    break
                time.sleep(0.02)
        if not held:
            raise DecisionUnwritable(
                "the decision store at %s stayed locked by another writer past %.0fs; "
                "nothing was recorded rather than writing unprotected against it"
                % (decisions_dir, _LOCK_TIMEOUT_S))
        yield
    finally:
        # No explicit `flock(fd, LOCK_UN)`: POSIX releases every lock held
        # through a descriptor the instant that descriptor is closed (`man 2
        # flock`), this fd is never reused past this point, and an explicit
        # unlock call here would need its own failure handling for no
        # behavioral gain. One syscall does the whole job.
        os.close(fd)


def next_id(decisions_dir):
    """The next NNN, allocated by READING the directory. An unreadable
    directory raises rather than restarting at 001: restarting would write a
    second 001 over somebody's first one.

    A plain, unlocked read: safe to call from anywhere, including from inside
    `write_package`'s own `_store_lock`, where it is the AUTHORITATIVE read
    (nothing else can be writing to `decisions_dir` while that lock is held).
    Called unlocked, from `build_package`, it is a best-effort guess instead;
    `write_package` is what makes the eventual write safe regardless.
    """
    if not os.path.isdir(decisions_dir):
        return "001"
    try:
        entries = os.listdir(decisions_dir)
    except OSError as exc:
        raise DecisionUnwritable("%s cannot be listed (%s); refusing to allocate an id "
                                 "that may already be taken" % (decisions_dir, exc))
    highest = 0
    for name in entries:
        head = name[:3]
        if head.isdigit():
            highest = max(highest, int(head))
    return "%03d" % (highest + 1)


# ---------------------------------------------------------------------------
# The deciding code: which shipped check decided, where it lives, and what its
# own registry declares about how it decides. Everything below READS. Nothing
# below invokes a check, and the fixture
# `test_neither_helper_starts_a_subprocess_to_read_the_logic` holds that by
# closing this module's only door to a child process and demanding an answer
# anyway.
# ---------------------------------------------------------------------------

#: One process reads the registries once. A list rather than a bare global so
#: the "filled or not" question is `if _REGISTRY_CACHE` and not a sentinel
#: nobody can tell from a legitimately empty result.
_REGISTRY_CACHE = []

#: Characters a Mermaid label may carry. A whitelist rather than a blacklist,
#: because the failure mode is a check name or an evidence path that closes a
#: label early and turns the rest of the sentence into diagram syntax. `<` and
#: `>` are absent on purpose: without them no label can spell an edge arrow.
_LABEL_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                  "0123456789 .,:;/_-?=+%")

#: The three verdict words this project ships, spelled here so a flowchart can
#: say which of them a registry did NOT declare an example for. Imported from
#: `sbe_checks` inside `registries()` would be better; it is not, because this
#: constant is read by `logic_flowchart` on the NO-DATA path where the tools
#: tree may be exactly what could not be read.
_VERDICT_WORDS = ("PASS", "FAIL", "NO-DATA")


def registries():
    """Every shipped check registry, as ONE dict with keys `declarations`,
    `modules` and `problems`.

    `declarations` maps a check name to a LIST of dicts, each carrying
    `source` (the path under `tools/`), `path` (the absolute file), `registry`
    (the name of the module-level dict) and `check` (the `Check` object). A
    list, not a single entry, because two shipped registries genuinely declare
    the name `migration` today, and resolving one of them in silence would
    print a line span out of a file the reader was not thinking about.

    THE DISCOVERY RULE HERE IS NOT A SECOND RULE. It is the one
    `evals/test_no_data_class.py` states and applies (`tool_sources`,
    `load_tool_modules`, `discover_registries`): every `.py` anywhere under
    `tools/`, pruned by the shared `sbe_checks.Pruner`, never one inside a
    `__pycache__` directory, never one that is not a regular file, imported,
    with `sbe_checks.py` itself excluded because reloading it would rebind
    `Check` to a second unequal class; and every module-level non-underscore
    dict holding at least one `Check` is a registry.

    That eval module is not IMPORTED here, and the reason is mechanical rather
    than a matter of taste: it imports every module under `tools/`, which
    includes `tools/test_sbe_decisions.py`, which imports THIS module, so
    importing it from here is a cycle that would break the honesty meta-test
    itself. The two are held in agreement by a fixture instead
    (`tools/test_sbe_decisions.py::
    test_the_discovery_rule_here_and_in_the_honesty_meta_test_agree`), which
    asks both for their registries and fails on any disagreement, so a change
    to the rule there goes red here rather than drifting quietly.

    Residual, stated because it is real: following that rule imports every
    module under `tools/`, test modules included, on the first call in a
    process. It is fast and it starts no subprocess, and it is what makes a
    registry added next year visible on the day it is added rather than on the
    day somebody remembers to list it here.

    A module that cannot be imported, a path that cannot be stat-ed, a pruned
    directory holding Python and a directory the walk was refused all land in
    `problems` as NO-DATA sentences and are printed by the callers below. None
    of them is swallowed: a registry this module could not read is a check it
    may resolve to nothing, and the reader has to be told which.
    """
    if _REGISTRY_CACHE:
        return _REGISTRY_CACHE[0]
    tools = mount()
    from sbe_checks import Check, Pruner  # noqa: E402
    declarations, modules, problems = {}, [], []
    pruner = Pruner()
    for dirpath, dirnames, filenames in os.walk(tools, onerror=pruner.onerror):
        dirnames[:] = pruner(dirpath, dirnames)
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            full = os.path.join(dirpath, filename)
            rel = os.path.relpath(full, tools)
            if "__pycache__" in rel.split(os.sep):
                problems.append("NO-DATA: tools/%s sits in a bytecode cache directory, so it is "
                                "named here and never imported; any check declared there is "
                                "invisible to this module" % rel)
                continue
            try:
                regular = not os.path.islink(full) and stat.S_ISREG(os.lstat(full).st_mode)
            except OSError as exc:
                problems.append("NO-DATA: tools/%s could not be stat-ed (%s), so whether it is "
                                "source at all is unknown and it was not imported" % (rel, exc))
                continue
            if not regular:
                problems.append("NO-DATA: tools/%s is not a regular file, so it is named here "
                                "and never imported; any check declared through it is invisible "
                                "to this module" % rel)
                continue
            if filename == "sbe_checks.py":
                # The one named exclusion, and the same one the honesty
                # meta-test makes: re-loading this file would bind a SECOND,
                # unequal `Check` class, and every isinstance test below would
                # then be false against registries that imported the first.
                # It declares no registry of its own, so nothing is lost.
                continue
            try:
                module = SourceFileLoader(rel[:-3].replace(os.sep, "."), full).load_module()
            except Exception as exc:
                problems.append("NO-DATA: tools/%s could not be imported (%s: %s), so any check "
                                "registry in it went unread and a check declared there resolves "
                                "to nothing here" % (rel, type(exc).__name__, exc))
                continue
            modules.append("tools/%s" % rel)
            for attr, value in sorted(vars(module).items()):
                if attr.startswith("_") or not isinstance(value, dict) or not value:
                    continue
                if not any(isinstance(item, Check) for item in value.values()):
                    continue
                for name, item in sorted(value.items()):
                    if not isinstance(item, Check):
                        problems.append("NO-DATA: tools/%s::%s registers %r as a bare value "
                                        "rather than a Check declaring its evidence, so this "
                                        "module can read no logic for it" % (rel, attr, name))
                        continue
                    declarations.setdefault(name, []).append(
                        {"source": "tools/%s" % rel, "path": full, "registry": attr,
                         "check": item})
    hidden, uninspected = pruner.hidden(lambda f: f.endswith(".py"))
    for tree in list(hidden) + list(uninspected) + list(pruner.denied):
        problems.append("NO-DATA: %s under tools/ was pruned or could not be entered, so a "
                        "registry inside it was never read" % tree)
    for entries in declarations.values():
        entries.sort(key=lambda entry: (entry["source"], entry["registry"]))
    result = {"declarations": declarations, "modules": sorted(modules),
              "problems": problems}
    _REGISTRY_CACHE.append(result)
    return result


def _unresolved_note(name, found):
    """The NO-DATA sentence for a check name no shipped registry declares."""
    known = sorted(found["declarations"])
    note = ("NO-DATA: no shipped check registry under tools/ declares %r, so no deciding code "
            "was read and no line span is printed here. %d registry module(s) were read and "
            "they declare %d check(s)%s. Naming a file this module never opened would send a "
            "reviewer to lines nobody looked at."
            % (name or "(no check name was recorded on this decision)",
               len(found["modules"]), len(known),
               "" if not known else " (%s)" % ", ".join(known)))
    if found["problems"]:
        note += " Read with these gaps: %s" % " ".join(found["problems"])
    return note


def _declaration_note(name, entries, found):
    """The sentence naming WHERE this check is declared, including every other
    registry that declares the same name.

    A name two registries declare is resolved to the first by path, and the
    others are named in the same sentence rather than dropped: a silent pick
    would print a span out of a file the reader was not thinking about.
    """
    where = ", ".join("%s::%s" % (entry["source"], entry["registry"]) for entry in entries)
    if len(entries) == 1:
        note = "the check %r is declared in %s, and the span above is that declaration." % (
            name, where)
    else:
        note = ("the check %r is declared in %d registries (%s). The span above is the first "
                "of them by path; the others are named here rather than dropped, because a "
                "silent pick would send a reader to a file they were not thinking about."
                % (name, len(entries), where))
    if found["problems"]:
        note += " Read with these gaps: %s" % " ".join(found["problems"])
    return note


def deciding_code(check_name):
    """The code that decided, as ONE dict with keys `file`, `firstLine`,
    `lastLine`, `excerpt` and `note`.

    The excerpt is the function's own lines, unmodified, and `firstLine` and
    `lastLine` address exactly those lines in that file, so a reader can open
    the file at the span and see the same text. `tools/test_sbe_decisions.py::
    test_the_excerpt_is_the_functions_own_lines_at_the_span_it_names` holds
    that: a span that does not address its own excerpt sends a reviewer to the
    wrong lines, which is worse than printing no span at all.

    A check no shipped registry declares returns this same dict with `file`,
    `firstLine` and `lastLine` all None, `excerpt` empty and `note` carrying
    the NO-DATA sentence. Nothing is guessed.

    Read with `inspect`, which reads the FILE. It does not call the check, and
    calling it is the one thing this module may never do.
    """
    name = (check_name or "").strip()
    found = registries()
    entries = found["declarations"].get(name) or []
    if not entries:
        return {"file": None, "firstLine": None, "lastLine": None, "excerpt": "",
                "note": _unresolved_note(name, found)}
    chosen = entries[0]
    try:
        source_file = inspect.getsourcefile(chosen["check"].fn)
        lines, first = inspect.getsourcelines(chosen["check"].fn)
    except (OSError, TypeError) as exc:
        return {"file": None, "firstLine": None, "lastLine": None, "excerpt": "",
                "note": "NO-DATA: %r is declared in %s::%s and its source could not be read "
                        "(%s: %s), so no span is printed. %s"
                        % (name, chosen["source"], chosen["registry"],
                           type(exc).__name__, exc, _declaration_note(name, entries, found))}
    if not source_file:
        return {"file": None, "firstLine": None, "lastLine": None, "excerpt": "",
                "note": "NO-DATA: %r is declared in %s::%s and Python could name no source file "
                        "for the function behind it, so no span is printed. %s"
                        % (name, chosen["source"], chosen["registry"],
                           _declaration_note(name, entries, found))}
    return {"file": os.path.abspath(source_file),
            "firstLine": first,
            "lastLine": first + len(lines) - 1,
            "excerpt": "".join(lines),
            "note": _declaration_note(name, entries, found)}


def _label(text):
    """One Mermaid label: whitespace collapsed, unsafe characters dropped.

    A whitelist, because the shape that breaks a diagram is a path or a check
    name carrying a quote or an angle bracket and closing the label early, and
    the remainder of the sentence then reads as diagram syntax rather than as
    text. `<` and `>` are outside the safe set, so no label can spell an edge.
    """
    flat = " ".join(str(text or "").split())
    kept = "".join(ch for ch in flat if ch in _LABEL_SAFE)
    return kept or "unreadable label"


def logic_flowchart(check_name):
    """The check's declared logic as a Mermaid `flowchart`, or ONE NO-DATA line
    naming why nothing was drawn.

    Every box comes from a DECLARATION in the shipped registry: what the check
    says it reads, the verdict its own registry entry declares for evidence
    that declares nothing, the verdict its own worked example declares, and the
    severity it declared at write time. A verdict word the registry declares no
    example for becomes a NO-DATA box naming the missing declaration, rather
    than a confident box drawn from somebody's memory of how the check behaves.

    Nothing here reads the function body and nothing here runs it: a picture
    derived from a run would be a second gate, and this module is not one.
    """
    name = (check_name or "").strip()
    found = registries()
    entries = found["declarations"].get(name) or []
    if not entries:
        return _unresolved_note(name, found) + (
            " No flowchart was drawn: a diagram of logic nobody read is a drawing.")
    chosen = entries[0]
    check = chosen["check"]
    code = deciding_code(name)
    out = ["flowchart TD\n"]
    out.append('    run["%s"]\n' % _label("a run of the check %s" % name))
    out.append('    run --> declared["%s"]\n'
               % _label("declared in %s::%s, evidence kind %s"
                        % (chosen["source"], chosen["registry"], check.kind)))
    out.append('    code["%s"]\n' % _label(
        "the deciding code: %s lines %s to %s"
        % (chosen["source"], code["firstLine"], code["lastLine"])
        if code["firstLine"] else
        "NO-DATA: the deciding code behind this check could not be read, so this chart names "
        "no line span"))
    out.append('    empty["%s"]\n'
               % _label("%s: the verdict this entry declares for evidence that declares "
                        "nothing%s"
                        % (check.empty_expect,
                           (", because %s" % check.empty_note) if check.empty_note else "")))
    reads = [str(r) for r in (check.reads or ()) if str(r).strip()]
    if not reads:
        out.append('    declared --> reads0["%s"]\n'
                   % _label("NO-DATA: this registry entry declares no evidence to read, so "
                            "what it opens was not read from a declaration"))
        out.append('    reads0 --> code\n')
    for index, source in enumerate(reads):
        out.append('    declared --> reads%d["%s"]\n'
                   % (index, _label("reads %s" % source)))
        out.append('    reads%d --> present%d{"%s"}\n'
                   % (index, index, _label("is %s present and answered?" % source)))
        out.append('    present%d -- "no" --> empty\n' % index)
        out.append('    present%d -- "yes" --> code\n' % index)
    declared_words = [check.empty_expect]
    if check.full_fixture is not None:
        out.append('    code --> worked["%s"]\n'
                   % _label("%s: the verdict this entry declares over its own worked example%s"
                            % (check.full_expect,
                               (", because %s" % check.full_expect_reason)
                               if check.full_expect_reason else "")))
        declared_words.append(check.full_expect)
    else:
        out.append('    code --> worked["%s"]\n'
                   % _label("NO-DATA: this entry declares no worked positive example (%s), so "
                            "the verdict a complete piece of evidence reaches was not read from "
                            "a declaration" % (check.no_full_fixture or "no reason declared")))
    for word in _VERDICT_WORDS:
        if word in declared_words:
            continue
        out.append('    code --> undeclared%s["%s"]\n'
                   % (word.replace("-", ""),
                      _label("NO-DATA: this registry entry declares no worked example reaching "
                             "%s, so when this check reaches that verdict was not read from a "
                             "declaration" % word)))
    out.append('    code --> severity["%s"]\n'
               % _label("declared severity %s: %s"
                        % (check.severity,
                           "a FAIL blocks a strict run"
                           if check.severity == "gate" else
                           "a FAIL is graded and blocks only under the opt-in strict-soft run")))
    if len(entries) > 1:
        out.append('    run --> alsoDeclared["%s"]\n'
                   % _label("the same check name is also declared in %s, and this chart draws "
                            "only the first by path"
                            % ", ".join("%s::%s" % (e["source"], e["registry"])
                                        for e in entries[1:])))
    if found["problems"]:
        out.append('    run --> gaps["%s"]\n'
                   % _label("NO-DATA: %d registry gap(s) were hit while reading this chart: %s"
                            % (len(found["problems"]), " ".join(found["problems"]))))
    return "".join(out)


def _fenced(body, language):
    """A fenced block whose fence is longer than any run of backticks inside it.

    An excerpt of real source can carry a fence of its own, and a three-tick
    fence around it would end the block early and spill the rest of the
    function into the document as prose.
    """
    longest, run = 0, 0
    for ch in body:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    fence = "`" * max(3, longest + 1)
    tail = "" if body.endswith("\n") else "\n"
    return "%s%s\n%s%s%s\n" % (fence, language, body, tail, fence)


def _verdict_section(trigger):
    """The verdict line this package quotes, as a dict with keys `line` and
    `note`.

    An empty or unreadable verdict line is NO-DATA naming what would fill it,
    and the same sentence goes into the package's notes. It is never a package
    that reads clean: a decision with no quoted verdict says nothing about
    what was decided.
    """
    line = trigger.get("verdictLine")
    if not isinstance(line, str) or not line.strip():
        return {"line": "NO-DATA: the run that triggered this package printed no verdict "
                        "line this module could quote. One line matching the verdict "
                        "grammar the checks themselves print, captured at the moment the "
                        "run made it, would fill this. An absence is never a clean "
                        "verdict.",
                "note": "NO-DATA: no verdict line was quoted into this package, so it "
                        "records that a decision happened and not what was decided."}
    return {"line": line.strip(), "note": ""}


def _evidence_lines(trigger):
    """What a reader can open to check this decision, as a list of lines.

    The verdict line itself is evidence of what was printed. A receipt id is
    evidence that the run happened at all, and its ABSENCE is named rather
    than left out, because a package quoting a line nobody can trace back to a
    run is a quotation, not evidence.
    """
    if trigger.get("kind") == EXPLAIN_KIND:
        # A regenerated package quotes nothing, because nothing ran. Repeating
        # the sentence below over it would claim a run as evidence of itself.
        lines = ["NO-DATA: no verdict line was quoted into this package. It was regenerated on "
                 "request by `sbe explain`, so what is below is the shipped check's own "
                 "declared logic and its deciding code, read from this project's source under "
                 "tools/, and not the result of any run."]
    else:
        lines = ["the verdict line quoted above, as printed by the run that triggered this "
                 "package"]
    receipt = trigger.get("evidenceId")
    if isinstance(receipt, str) and receipt.strip():
        lines.append("evidence receipt %s, under .sbe/evidence/, minted by `sbe evidence "
                     "run`" % receipt.strip())
    else:
        lines.append("NO-DATA: no evidence receipt id was handed to this package. A "
                     "receipt minted by `bin/sbe evidence run -- <the command that made "
                     "this verdict>`, stored under .sbe/evidence/, would bind this "
                     "decision to a run nobody typed by hand.")
    return lines


def _detail_lines(trigger):
    """The detail lines the CALLER composed, as a list, appended to the inputs
    section of the package.

    WHAT MAY COME THROUGH HERE, stated because this is the one door into a
    package that does not go through `parse_verdict_lines`: lines composed from
    a STRUCTURED RECORD THIS PROJECT ITSELF WROTE, and nothing else.
    `record_tier_decision` composes them from the named fields of an impact
    report and `record_forced_close` from the named fields of a task registry
    record. Both are files this project writes and a reader already has.

    IT IS NOT A DOOR FOR CAPTURED OUTPUT. Text captured from a run goes through
    `parse_verdict_lines`, which counts and discards every line outside the
    verdict grammar for the reason that function states at length. Routing
    captured lines through here instead would undo that whole policy in one
    line, so a caller that holds terminal text uses `record_from_run`.
    """
    details = trigger.get("details")
    if not isinstance(details, (list, tuple)):
        return []
    return [" ".join(str(item).split()) for item in details if str(item).strip()]


def _input_lines(trigger, root, location, head):
    """Everything this package read, named so a reader can re-read it."""
    lines = ["the trigger kind %r, handed to this module by the command that wrote this "
             "package" % (trigger.get("kind") or "(unnamed)"),
             "the check name %r" % (trigger.get("check") or "(unnamed)"),
             "the directory checked: %s" % (os.path.abspath(trigger["dossier"])
                                            if trigger.get("dossier")
                                            else "none named; %s" % os.path.abspath(root)),
             "the package store: %s (%s)" % (location["dir"], location["location"])]
    if head["commit"]:
        lines.append("the head commit this decision was taken against: %s"
                     % head["commit"])
    else:
        lines.append(head["note"])
    lines.extend(_detail_lines(trigger))
    return lines


def _risk_lines(verdict):
    """What is at stake, derived from the verdict word the run printed and
    from nothing else. A risk this module cannot derive is NO-DATA naming the
    command that measures it, never an invented blast radius."""
    lines = []
    if verdict == "FAIL":
        lines.append("the claim this check tested is broken at this commit, so shipping "
                     "over this decision ships the defect the verdict line names")
    elif verdict == "WAIVED":
        lines.append("the control did not run here, so nothing was examined and nothing "
                     "was found; a waiver records who is carrying that, never that the "
                     "directory was checked and found clean")
    else:
        lines.append("the verdict word recorded for this decision is %r; what it puts at "
                     "stake is whatever that verdict means in the check that printed it"
                     % (verdict or "(none)"))
    lines.append("NO-DATA: the blast radius of this decision was not measured here. "
                 "`bin/sbe impact` computes the tier and names the detectors that raised "
                 "it, and a disposition file beside the dossier records what a human did "
                 "about them.")
    return lines


def _flip_lines(verdict, check, code):
    """What would change this decision, stated as an action somebody can take
    and a run that would show it, rather than as an opinion.

    `code` is the `deciding_code` dict. The last line here used to be a flat
    NO-DATA sentence saying the deciding code was not excerpted; now that it is
    excerpted, that sentence would be a false absence, which is the same defect
    as a false PASS pointing the other way. It says what was actually read, and
    keeps the NO-DATA wording only where nothing was.
    """
    name = check or "the check named above"
    lines = []
    if verdict == "FAIL":
        lines.append("a re-run of %s over this same commit returning a verdict other "
                     "than FAIL, with a receipt under .sbe/evidence/ showing the run "
                     "happened" % name)
        lines.append("a correction to the artifact the verdict line names, followed by "
                     "that same re-run")
    elif verdict == "WAIVED":
        lines.append("removing the `.sbe-exempt` entry that covers %s in the directory "
                     "the verdict line names, then a run that records what the check "
                     "actually found there" % name)
    else:
        lines.append("a re-run of %s over this same commit, recorded with a receipt, "
                     "printing a different verdict line" % name)
    if code["firstLine"]:
        lines.append("reading the deciding code excerpted below, at %s lines %d to %d, and "
                     "changing what it examines; a check that decides differently is a change "
                     "to that function and to the fixtures that hold it"
                     % (code["file"], code["firstLine"], code["lastLine"]))
    else:
        lines.append(code["note"])
    return lines


def _checklist_lines(head, unquoted):
    """The review checklist: questions a reviewer ANSWERS, each of which can be
    answered from a file rather than from memory."""
    where = head["commit"][:12] if head["commit"] else "(no commit resolved)"
    return [
        "Does the verdict line quoted above come from the run you are reviewing, at "
        "commit %s?" % where,
        "Was the check run against this commit, or against an earlier one whose result "
        "no longer describes this tree?",
        "%d line(s) from that run were counted and NOT copied into this package. Do you "
        "need the run's own output to review this decision, and can you still get it?"
        % unquoted,
        "Is there an evidence receipt under .sbe/evidence/ for the run this package "
        "quotes, or is the quotation all there is?",
        "If this decision waived or forced something, who is named as carrying it, and "
        "until when?",
    ]


def build_package(root, trigger):
    """One decision package, as ONE dict. Never a pair, and never a verdict
    this module computed: every verdict word in it came from the run that was
    handed to it.

    `trigger` is a dict carrying `kind`, `check`, `verdict`, `verdictLine`,
    `otherLines` (the lines the run printed that did NOT match the verdict
    grammar, which are COUNTED here and never copied) and `dossier`.

    Every section with no source renders as a NO-DATA line naming the file or
    the store that would fill it, so an absence in a shared artifact reads as
    an absence rather than as a clean bill.
    """
    kind = trigger.get("kind") or "(unnamed)"
    check = trigger.get("check") or ""
    verdict = trigger.get("verdict") or ""
    top_result = repo_top_of(root)
    head = head_commit_of(root)
    location = package_location(top_result["top"], trigger.get("dossier"))
    verdict_section = _verdict_section(trigger)

    other = trigger.get("otherLines")
    counted = trigger.get("unquotedLineCount")
    notes = ["This package QUOTES the run that triggered it. Nothing in it re-ran a "
             "check, and nothing in it computed a verdict over source code."]
    if isinstance(counted, int) and not isinstance(counted, bool):
        # The stronger of the two shapes, and the one `record_from_run` uses:
        # the caller counted the unmatched lines and DISCARDED them before
        # calling here, so their text never reaches this module at all. A
        # caller that hands `otherLines` instead is still holding them.
        unquoted = counted
    elif isinstance(other, (list, tuple)):
        unquoted = len(other)
    else:
        unquoted = 0
        notes.append("NO-DATA: the trigger carried no readable list of other lines, so "
                     "the count of lines left unquoted is 0 by absence rather than by "
                     "measurement.")
    notes.append("%d line(s) printed by that run fell outside the verdict grammar. They "
                 "were counted and discarded, never copied: a decision package is shared, "
                 "and an unmatched line may carry a secret." % unquoted)
    if kind not in TRIGGER_KINDS:
        notes.append("The trigger kind %r is outside the four this project declares (%s). "
                     "It is recorded rather than dropped, and named here rather than "
                     "normalized in silence." % (kind, ", ".join(TRIGGER_KINDS)))
    for extra in (verdict_section["note"], top_result["note"], head["note"]):
        if extra:
            notes.append(extra)

    code = deciding_code(check)
    chart = logic_flowchart(check)
    if not code["firstLine"]:
        notes.append(code["note"])

    identifier = next_id(location["dir"])
    slug = _slug("%s-%s-%s" % (kind, check, verdict), "decision")
    superseded = supersedes_for(location["dir"], slug, head["commit"])
    if superseded["note"]:
        notes.append(superseded["note"])
    return {
        "id": identifier,
        "slug": slug,
        "dir": os.path.join(location["dir"], "%s-%s" % (identifier, slug)),
        "verdictLine": verdict_section["line"],
        "evidence": _evidence_lines(trigger),
        "inputs": _input_lines(trigger, root, location, head),
        "risks": _risk_lines(verdict),
        "whatWouldFlipIt": _flip_lines(verdict, check, code),
        "checklist": _checklist_lines(head, unquoted),
        "decidingCode": code,
        "logicFlowchart": chart,
        "boundCommit": head["commit"],
        "supersedes": superseded,
        "location": location["location"],
        "locationReason": location["reason"],
        "unquotedLineCount": unquoted,
        "notes": notes,
        "trigger": {"kind": kind, "check": check, "verdict": verdict},
        "writtenAt": _iso(time.time()),
    }


def _bullets(lines):
    return "".join("- %s\n" % line for line in lines)


def render_package(package):
    """The package as the Markdown a human opens. One section per named key,
    in the order a reader needs them: what was decided, what backs it, what it
    read, what is at stake, what would change it, what to check, and what this
    file does not know."""
    bound = package["boundCommit"] or ("NO-DATA (no commit resolved; see the notes at the "
                                       "end of this file)")
    trigger = package.get("trigger") or {}
    out = []
    out.append("# Decision %s: %s\n\n" % (package["id"], package["slug"]))
    if trigger.get("kind") == EXPLAIN_KIND:
        # The banner is a claim about how this file came to exist, so it cannot
        # be one fixed sentence: a regenerated package was written because
        # somebody asked what a check does, and no decision was taken and no run
        # was quoted. Printing the sentence below over it would be the first
        # false line in a document about honesty.
        out.append("INTERNAL-EVAL. REGENERATED by `sbe explain` on request, not written at a "
                   "decision. No run is quoted below and nothing was re-run to build it: the "
                   "logic and the deciding code were read from this project's own source.\n\n")
    else:
        out.append("INTERNAL-EVAL. Written by BrotherSBE at the moment this decision was "
                   "taken, quoting the run that took it. This file re-ran nothing.\n\n")
    out.append("%s%s\n" % (_ID_PREFIX, package["id"]))
    out.append("- trigger: %s\n" % (trigger.get("kind") or "(unnamed)"))
    out.append("%s%s\n" % (_CHECK_PREFIX, trigger.get("check") or "(unnamed)"))
    out.append("- verdict recorded by the run: %s\n"
               % (trigger.get("verdict") or "(none recorded)"))
    out.append("%s%s\n" % (_BOUND_PREFIX, bound))
    superseded = package.get("supersedes") or {}
    if superseded.get("id"):
        out.append("%s%s, the package for this same decision at commit %s. That file is left "
                   "exactly as it was: packages are append-only, and it records what somebody "
                   "decided about a different program.\n"
                   % (_SUPERSEDES_PREFIX, superseded["id"], superseded["commit"]))
    out.append("- written at: %s\n" % package.get("writtenAt", "(unrecorded)"))
    out.append("- filed in the %s, because %s\n"
               % (package["location"], package["locationReason"]))
    out.append("- lines from that run counted and not copied: %d\n"
               % package["unquotedLineCount"])
    out.append("\n## The verdict, quoted\n\n")
    out.append("    %s\n" % package["verdictLine"])
    out.append("\n## Evidence\n\n")
    out.append(_bullets(package["evidence"]))
    out.append("\n## What this package read\n\n")
    out.append(_bullets(package["inputs"]))
    out.append("\n## Risks\n\n")
    out.append(_bullets(package["risks"]))
    out.append("\n## What would flip it\n\n")
    out.append(_bullets(package["whatWouldFlipIt"]))
    code = package.get("decidingCode") or {}
    out.append("\n## The code that decided\n\n")
    if code.get("firstLine"):
        out.append(_bullets(["file: %s" % code["file"],
                             "lines %d to %d, which address exactly the excerpt below"
                             % (code["firstLine"], code["lastLine"]),
                             code["note"]]))
        out.append("\n")
        out.append(_fenced(code["excerpt"], "python"))
    else:
        out.append(_bullets([code.get("note") or
                             "NO-DATA: no deciding code was read for this decision, and the "
                             "shipped check registries under tools/ are what would fill it."]))
    chart = package.get("logicFlowchart") or ""
    out.append("\n## How that check decides, as its registry declares it\n\n")
    if chart.lstrip().startswith("flowchart"):
        out.append(_fenced(chart, "mermaid"))
    else:
        out.append(_bullets([chart or
                             "NO-DATA: no flowchart was drawn, and no reason was recorded for "
                             "it, which is itself a gap in this package."]))
    out.append("\n## Review checklist\n\n")
    out.append(_bullets(package["checklist"]))
    out.append("\n## Notes and limits\n\n")
    out.append(_bullets(package["notes"]))
    return "".join(out)


def bound_commit_in(path):
    """What an EXISTING package on disk is bound to, as a dict with keys
    `exists`, `commit` and `note`.

    A file that exists and carries no readable binding line returns
    `commit` None with a note saying so, and callers treat that as a binding
    they could not read rather than as a binding that matches theirs.
    """
    if not os.path.exists(path):
        return {"exists": False, "commit": None, "note": ""}
    try:
        with io.open(path, encoding="utf-8") as fh:
            body = fh.read()
    except (OSError, ValueError) as exc:
        raise DecisionUnwritable("%s exists and cannot be read (%s); refusing to write "
                                 "over a package whose binding nobody could check"
                                 % (path, exc))
    match = _BOUND_RE.search(body)
    if match is None:
        return {"exists": True, "commit": None,
                "note": "NO-DATA: %s carries no readable %r line, so which commit it was "
                        "written against is unknown" % (path, _BOUND_PREFIX.strip())}
    return {"exists": True, "commit": match.group(1), "note": ""}


def package_header(path):
    """What a package on disk says ABOUT ITSELF, as ONE dict with keys `exists`,
    `id`, `check`, `commit` and `note`.

    Read off the header lines `render_package` wrote, through the same prefix
    constants it wrote them with, so a rename of a header line moves both sides
    at once. A field the file does not carry comes back None with the NO-DATA
    sentence in `note`; nothing is inferred from the directory name, because a
    directory can be renamed by hand and the file cannot lie about itself
    without somebody editing it.

    An existing file that cannot be READ raises `DecisionUnwritable`, exactly as
    `bound_commit_in` does and for the same reason: treating a package nobody
    could open as a package that says nothing would let a browse or a
    supersession silently skip somebody's record.
    """
    if not os.path.exists(path):
        return {"exists": False, "id": None, "check": None, "commit": None, "note": ""}
    try:
        with io.open(path, encoding="utf-8") as fh:
            body = fh.read()
    except (OSError, ValueError) as exc:
        raise DecisionUnwritable("%s exists and cannot be read (%s); it is named here rather "
                                 "than skipped, because a package nobody could open is not a "
                                 "package that says nothing" % (path, exc))
    identity = _ID_RE.search(body)
    check = _CHECK_RE.search(body)
    commit = _BOUND_RE.search(body)
    missing = []
    if identity is None:
        missing.append(_ID_PREFIX.strip())
    if check is None:
        missing.append(_CHECK_PREFIX.strip())
    if commit is None:
        missing.append(_BOUND_PREFIX.strip())
    return {
        "exists": True,
        "id": identity.group(1) if identity else None,
        "check": check.group(1).strip() if check else None,
        "commit": commit.group(1) if commit else None,
        "note": "" if not missing else
                "NO-DATA: %s carries no readable %s line, so that field is unknown here and "
                "is not guessed at from the directory name"
                % (path, " and no readable ".join(repr(m) for m in missing)),
    }


def list_packages(decisions_dir):
    """Every package in ONE store, as ONE dict with keys `packages` and
    `problems`.

    Each package is a dict carrying `id`, `slug`, `dirName`, `dir`, `path`,
    `check`, `commit` and `note`, sorted by directory name, which is by id.

    A store that DOES NOT EXIST is not a problem and is not reported as one: it
    is an empty store, which is what an empty `packages` list already says, and
    `next_id` reads a missing directory the same way. Everything that stops this
    function from reading something that IS there lands in `problems` as a
    NO-DATA sentence: a directory it cannot list, a package file it cannot open,
    a numbered directory holding no `DECISION.md`, and an entry in the store
    that is not a package at all. None of those is swallowed, because a browse
    that quietly skipped a package would answer "no package names that check"
    over a store that holds one.
    """
    result = {"packages": [], "problems": []}
    if not os.path.isdir(decisions_dir):
        return result
    try:
        entries = sorted(os.listdir(decisions_dir))
    except OSError as exc:
        result["problems"].append(
            "NO-DATA: %s cannot be listed (%s), so whether it holds packages is unknown. "
            "Nothing here says this store is empty." % (decisions_dir, exc))
        return result
    for name in entries:
        directory = os.path.join(decisions_dir, name)
        if not (name[:3].isdigit() and os.path.isdir(directory)):
            result["problems"].append(
                "NO-DATA: %s sits in the decision store and is not a NNN-<slug> directory, so "
                "it was named here and never read as a package" % directory)
            continue
        path = os.path.join(directory, PACKAGE_FILENAME)
        try:
            header = package_header(path)
        except DecisionUnwritable as exc:
            result["problems"].append("NO-DATA: %s" % exc)
            continue
        if not header["exists"]:
            result["problems"].append(
                "NO-DATA: %s holds no %s, so what was decided there could not be read"
                % (directory, PACKAGE_FILENAME))
            continue
        if header["note"]:
            result["problems"].append(header["note"])
        result["packages"].append({
            "id": header["id"] or name[:3],
            "slug": name.split("-", 1)[1] if "-" in name else "",
            "dirName": name,
            "dir": directory,
            "path": path,
            "check": header["check"],
            "commit": header["commit"],
            "note": header["note"],
        })
    return result


def supersedes_for(decisions_dir, slug, bound_commit):
    """The package a new one SUPERSEDES, as ONE dict with keys `id`, `path`,
    `commit` and `note`. `id` is None when there is nothing to supersede.

    Supersession is recorded in the NEW package and never by editing the old
    one: packages are append-only, and a rewrite would delete what somebody
    decided about a different program with no record of the deletion.

    A package under the same slug whose own binding could not be read is NOT
    claimed as superseded: whether it describes this commit is unknown, and a
    claim over an unknown is the shape of dishonesty this whole module refuses.
    It lands in `note` instead, and the caller carries that note into the new
    package.

    Only the same-slug NOTE is forwarded, not every problem `list_packages`
    found. A browse prints those where a reader is looking at the store; a
    package that recited the whole store's condition in its own notes would
    bury the decision it exists to record.
    """
    empty = {"id": None, "path": None, "commit": None, "note": ""}
    if not bound_commit:
        return dict(empty, note="NO-DATA: this package is bound to no commit, so whether it "
                                "supersedes an earlier package for the same decision was not "
                                "compared. A comparison nobody could make is not one that "
                                "came out equal.")
    same_slug = [p for p in list_packages(decisions_dir)["packages"] if p["slug"] == slug]
    unreadable = [p for p in same_slug if not p["commit"]]
    older = [p for p in same_slug if p["commit"] and p["commit"] != bound_commit]
    note = ""
    if unreadable:
        note = ("NO-DATA: %d earlier package(s) under this same slug (%s) carry no readable "
                "commit binding, so whether this package supersedes them is unknown and is "
                "not claimed here."
                % (len(unreadable), ", ".join(p["dirName"] for p in unreadable)))
    if not older:
        return dict(empty, note=note)
    newest = max(older, key=lambda p: p["dirName"])
    return {"id": newest["id"], "path": newest["path"], "commit": newest["commit"],
            "note": note}


def write_package(root, package):
    """Write the package and return the absolute path of the file written.

    Atomic the way `tasks.save_registry` is atomic: a temp file in the same
    directory, then `os.replace`, so the file is never half written. The
    whole id-check-and-write sequence below now runs inside `_store_lock`,
    held from before the append-only check through the final `os.replace`,
    so two writers racing for the same store can no longer both decide the
    next id is 005 and both write it: two writers cannot claim one id.

    A `DECISION.md` already sitting there and bound to a DIFFERENT commit is
    never overwritten. Packages are append-only, because the older package
    records what somebody decided about a different program, and a rewrite
    would delete that with no record of the deletion. The refusal raises
    `DecisionUnwritable` naming both commits.

    A `DECISION.md` already sitting there and bound to the SAME commit is now
    ALSO never overwritten, which is the one behavior this lock changes: id
    allocation used to be a plain, unlocked directory scan (`next_id`, called
    by `build_package`), so a caller that built two packages before writing
    either read the same store twice, allocated the same id twice, and the
    second write landed on top of the first, silently, because "same commit"
    used to read as "this is the same decision, rewrite it in place". Held
    under this lock, that collision is instead caught before a single byte of
    the second write lands: the id is reallocated fresh, from the store this
    lock guarantees is not moving under it, and the second package is filed
    under its own new id rather than erasing the first. Build and write need
    no longer happen one at a time; the lock is what makes that safe now.
    """
    directory = package["dir"]
    decisions_dir = os.path.dirname(directory)
    with _store_lock(decisions_dir):
        target = os.path.join(directory, PACKAGE_FILENAME)
        existing = bound_commit_in(target)
        if existing["exists"] and existing["commit"] != package["boundCommit"]:
            raise DecisionUnwritable(
                "%s is already bound to commit %s and this package is bound to %s; "
                "refusing to overwrite it. A package records what was decided about "
                "the program at its own commit, and packages are append-only: "
                "allocate the next id instead."
                % (target, existing["commit"] or "(unreadable)",
                   package["boundCommit"] or "(none resolved)"))
        if existing["exists"]:
            # Same commit, same slot: another writer reached this exact id
            # before this write did, most likely a concurrent one, since the
            # store `build_package` read to compute `package["dir"]` is stale
            # the instant a second writer starts. Reallocating here is race
            # free: nothing else can write to `decisions_dir` while this lock
            # is held, so a fresh `next_id` read is authoritative.
            old_name = os.path.basename(directory)
            stale_id = package["id"]
            # Split on the first "-", not on `len(stale_id)`: an identifier
            # is always digits-only (`next_id` mints it with "%03d"), so this
            # reads the real slug off the directory name itself and does not
            # depend on `package["id"]` actually matching it.
            slug = old_name.split("-", 1)[1] if "-" in old_name else old_name
            fresh_id = next_id(decisions_dir)
            directory = os.path.join(decisions_dir, "%s-%s" % (fresh_id, slug))
            target = os.path.join(directory, PACKAGE_FILENAME)
            still = bound_commit_in(target)
            if still["exists"]:
                raise DecisionUnwritable(
                    "%s was freshly allocated under the store lock and already exists "
                    "(%s); refusing to guess a further id. Nothing was recorded."
                    % (target, still["commit"] or "(unreadable)"))
            package = dict(package, id=fresh_id)
            package["notes"] = list(package["notes"]) + [
                "This package was reallocated from id %s to id %s at write time: %s "
                "already held a package bound to this same commit, written by another "
                "writer between this package being built and this one being written. "
                "Never overwritten; filed under its own id instead."
                % (stale_id, fresh_id, old_name)]
        try:
            if not os.path.isdir(directory):
                os.makedirs(directory)
        except OSError as exc:
            raise DecisionUnwritable("%s cannot be created (%s); nothing was recorded"
                                     % (directory, exc))
        body = render_package(package)
        try:
            fd, tmp = tempfile.mkstemp(prefix="DECISION.", suffix=".tmp", dir=directory)
        except OSError as exc:
            raise DecisionUnwritable("no temp file could be made in %s (%s); nothing was "
                                     "recorded" % (directory, exc))
        try:
            with io.open(fd, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.replace(tmp, target)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return os.path.abspath(target)


def parse_verdict_lines(text):
    """Every verdict line in a run's output, as ONE dict with keys `verdicts`
    and `unquotedLineCount`. Never a pair.

    `verdicts` is a list of dicts, each carrying `check`, `verdict` and `line`.
    `line` is the matched line with its indentation stripped, and it is the
    ONLY text this module ever carries out of a run.

    EVERY LINE THAT DOES NOT MATCH IS COUNTED AND THROWN AWAY, HERE, before any
    caller can hold it. That is the whole reason the counting happens in this
    function rather than in the writer: a decision package is written to be
    pasted into a pull request, and an unmatched line from a real run may carry
    a token, a connection string, a customer row or a file path nobody meant to
    publish. The evidence receipts persist digests rather than raw output for
    exactly that reason (see `evidence.py`), and a second artifact that widened
    the policy by accident would undo it. A reader who needs the run's own
    output goes and reads the run.

    A verdict word this module does not know is not a verdict line: it is
    counted with the rest. Nothing here invents a fifth verdict.
    """
    verdicts = []
    unquoted = 0
    for raw in str(text or "").splitlines():
        match = _VERDICT_LINE_RE.match(raw)
        if match is None:
            unquoted += 1
            continue
        verdicts.append({"check": match.group("check"),
                         "verdict": match.group("verdict"),
                         "line": raw.strip()})
    return {"verdicts": verdicts, "unquotedLineCount": unquoted}


def record_from_run(root, text, dossier):
    """One decision package per FAIL and per WAIVED line the run printed, as a
    list of the absolute paths written, oldest first.

    A PASS writes nothing, and so does a NO-DATA: neither is a decision
    somebody has to carry. An empty list is the honest answer for "this run
    decided nothing", and the caller says that in words rather than printing an
    empty package.

    Packages are built and written ONE AT A TIME, because ids are allocated by
    reading the store: building two before writing either would read the same
    store twice and allocate the same id twice.

    A write that fails raises `DecisionUnwritable` carrying what had already
    been written and what is now recorded nowhere. It is raised rather than
    returned, so a caller cannot mistake a partial run for a complete one, and
    the CLI that calls it catches it, prints it, and leaves the exit code of
    the gate it was watching exactly where the gate put it.
    """
    parsed = parse_verdict_lines(text)
    worthy = [v for v in parsed["verdicts"] if v["verdict"] in PACKAGE_WORTHY]
    written = []
    for index, item in enumerate(worthy):
        trigger = {
            "kind": "waiver" if item["verdict"] == "WAIVED" else "gate",
            "check": item["check"],
            "verdict": item["verdict"],
            "verdictLine": item["line"],
            "unquotedLineCount": parsed["unquotedLineCount"],
            "dossier": dossier,
        }
        try:
            written.append(write_package(root, build_package(root, trigger)))
        except DecisionUnwritable as exc:
            raise DecisionUnwritable(
                "%s. %d package(s) for this run were written before this failure (%s); "
                "the remaining %d verdict line(s) are recorded nowhere."
                % (exc, len(written), ", ".join(written) or "none",
                   len(worthy) - index))
    return written


# ---------------------------------------------------------------------------
# The other two triggers: a tier raised or disposed, and a forced task close.
#
# Neither of these is handed TERMINAL TEXT the way `record_from_run` is. Each is
# handed a STRUCTURED RECORD this project itself wrote: `sbe impact`'s report
# dict, and the task registry's own task record. So neither one quotes a
# captured line, and each says so in the package rather than letting a section
# headed "quoted" carry something nobody quoted. Both route through
# `build_package` and `write_package` above. There is no second package format.
# ---------------------------------------------------------------------------

#: How a composed verdict line is described in the package that carries it, so a
#: reader can tell a line CAPTURED from a run apart from a line COMPOSED from a
#: report's named fields. Two different things, and a package that blurred them
#: would be claiming a provenance it does not have.
_COMPOSED_FROM_IMPACT = ("NOTE ON THE LINE UNDER 'The verdict, quoted': it was composed from "
                         "the impact report's own fields (verdict, proposedTier, humanTier), "
                         "not captured from a terminal. `sbe impact` hands this module a "
                         "report rather than text, so no line of that run's output was read, "
                         "quoted or counted here.")

_COMPOSED_FROM_REGISTRY = ("NOTE ON THE LINE UNDER 'The verdict, quoted': it was composed "
                           "from the task registry record's own fields (id, forced.who, "
                           "forced.why, forced.verdict), not captured from a terminal. `sbe "
                           "task close --force` hands this module a record rather than text, "
                           "so no line of that run's output was read, quoted or counted here.")


def _raise_note(proposed, human):
    """Whether the report proposes a HIGHER tier than the human declared, as ONE
    dict with keys `raised` and `note`.

    A tier word on either side that this project does not ship raises nothing
    and claims no raise: `raised` is False and `note` carries the NO-DATA
    sentence, because a comparison nobody could make is not a comparison that
    came out equal. The ORDER itself is `impact._tier_index`, imported rather
    than re-spelled, so this cannot decide "is this a raise?" differently from
    the tool that printed the report.
    """
    try:
        raised = _tier_index(str(proposed)) > _tier_index(str(human))
    except ValueError:
        return {"raised": False,
                "note": "NO-DATA: the report's proposed tier %r and declared tier %r could not "
                        "both be ordered, so whether this run RAISED a tier was not compared. "
                        "A comparison nobody could make is not a comparison that came out "
                        "equal." % (proposed, human)}
    return {"raised": raised, "note": ""}


def record_tier_decision(root, impact_data, dossier):
    """ONE decision package for a tier raised or disposed, as the absolute path
    written, or None when the report shows neither.

    None is the honest answer for "nothing was decided here", and the caller
    prints nothing rather than an empty package: a package records a decision
    somebody has to carry, and a run whose proposed tier matched the declared
    one and disposed of nothing produced none.

    IT READS `impact_data` AND NOTHING ELSE. It never re-runs `sbe impact`, it
    never reads the diff a second time, and it starts no process beyond the
    `rev-parse` every package makes to bind itself to a commit. A second reading
    of the diff here could disagree with the report the user is looking at,
    which is the failure this whole module is built to avoid.

    WHICH VERDICT WORD THE PACKAGE CARRIES, and why it is never PASS:

      - The report's own word, when that word is FAIL, NO-DATA or
        REVIEW-REQUIRED. Those are what the run printed and this module reports
        them rather than inventing a fifth.
      - WAIVED, when every disagreement carries a recorded disposition. `sbe
        impact` prints PASS in that case, and it prints PASS BECAUSE a human
        suppressed the raise. The decision this package records is that
        suppression, and a suppression is WAIVED (I7). Copying the report's
        PASS onto a package about a switched-off control is exactly the
        clean-looking record this project exists to stop.
      - NO-DATA otherwise, with the report's own word named in the notes, so a
        word this module did not expect is visible rather than normalized in
        silence.
    """
    data = impact_data if isinstance(impact_data, dict) else {}
    reported = str(data.get("verdict") or "").strip()
    raw_disagreements = data.get("disagreements")
    disagreements = [d for d in raw_disagreements if isinstance(d, dict)] \
        if isinstance(raw_disagreements, (list, tuple)) else []
    disposed = [d for d in disagreements if d.get("disposition") == "recorded"]
    unresolved = [d for d in disagreements if d.get("disposition") != "recorded"]
    proposed = data.get("proposedTier")
    human = data.get("humanTier")
    raise_result = _raise_note(proposed, human)

    if not (reported == "REVIEW-REQUIRED" or disagreements or raise_result["raised"]):
        return None

    notes = []
    if reported in ("FAIL", "NO-DATA", "REVIEW-REQUIRED"):
        verdict = reported
    elif disposed and not unresolved:
        verdict = "WAIVED"
        notes.append("This run's report carries the word %r, and it carries it BECAUSE %d "
                     "disagreement(s) were disposed of by a recorded human decision. What "
                     "this package records is that suppression, so it is WAIVED and never "
                     "%r: a control somebody switched off did not pass."
                     % (reported or "(none)", len(disposed), reported or "(none)"))
    else:
        verdict = "NO-DATA"
        notes.append("NO-DATA: this run's report carries the verdict word %r, which is not "
                     "one this module can record as a decision, so the package carries "
                     "NO-DATA and names the word rather than normalizing it in silence."
                     % (reported or "(none)"))
    if raise_result["note"]:
        notes.append(raise_result["note"])

    line = ("verdict: %s. proposed tier %s (a floor, not a ceiling), declared tier %s"
            % (reported or "(none recorded)", proposed or "(none recorded)",
               human or "none read"))
    details = [_COMPOSED_FROM_IMPACT,
               "the tier the diff proposes: %s (a floor, never a ceiling: two of the five "
               "intake answers cannot be derived from a diff)" % (proposed or "(none "
                                                                  "recorded)"),
               "the tier the human declared at intake: %s" % (human or "none read")]
    for item in disagreements:
        details.append("DISAGREEMENT %s %s [disposition: %s]"
                       % (item.get("detector") or "(unnamed detector)",
                          item.get("file") or "(no file named)",
                          item.get("disposition") or "(none recorded)"))
    if not disagreements:
        details.append("NO-DATA: this report names no disagreement, so which detector raised "
                       "the tier was not read from it. The DETECTED lines `sbe impact` prints "
                       "over this same range are what would fill it.")
    head_sha = data.get("headCommit")
    if isinstance(head_sha, str) and head_sha.strip():
        details.append("the head commit the report was computed against: %s" % head_sha.strip())
    else:
        details.append("NO-DATA: the report named no head commit, so which tree it measured "
                       "was not read from it. `sbe impact --json` records it as headCommit.")
    problem = data.get("intakeProblem")
    if isinstance(problem, str) and problem.strip():
        details.append("the intake this report could not use: %s" % problem.strip())

    package = build_package(root, {
        "kind": "tier",
        # No shipped check registry decides a tier: the detectors live in
        # `impact.py`. Naming one here would send a reader to a check that never
        # ran, so the deciding-code and flowchart sections say NO-DATA instead.
        "check": "",
        "verdict": verdict,
        "verdictLine": line,
        "unquotedLineCount": 0,
        "details": details,
        "dossier": dossier,
    })
    package["notes"].extend(notes)
    return write_package(root, package)


def record_forced_close(root, task_record):
    """ONE decision package for a forced task close, as the absolute path
    written.

    The package carries who forced it, why, the violation list, and the verdict
    the diff postcondition ACTUALLY reached. That verdict is FAIL or NO-DATA and
    never PASS, and the reason is mechanical rather than a matter of taste:
    `tasks.cmd_close` returns on PASS before its forced branch is reached, so a
    record arriving here claiming PASS came from a caller that did not run the
    postcondition. Such a word is recorded as NO-DATA and NAMED in the notes,
    never copied into the header, because a FORCED close headed PASS is a clean
    verdict over a diff nobody cleared.

    A record with no `forced` block at all is written too, as NO-DATA naming
    `sbe task close --force --who --why` as what writes that block. Refusing
    would lose the decision entirely, and writing it as though somebody had been
    named would be worse than either.
    """
    record = task_record if isinstance(task_record, dict) else {}
    forced = record.get("forced")
    forced = forced if isinstance(forced, dict) else None
    task_id = str(record.get("id") or "").strip() or "(no task id recorded)"
    notes = []

    if forced is None:
        verdict = "NO-DATA"
        who = "NO-DATA: nobody is recorded as forcing this close"
        why = "NO-DATA: no reason is recorded for it"
        violations = []
        notes.append("NO-DATA: this task record carries no `forced` block, so who forced the "
                     "close, why, and what the diff postcondition reached are all unknown "
                     "here. `sbe task close --force --who --why` is what writes that block, "
                     "and only a close that ran through it records a disposition.")
    else:
        claimed = str(forced.get("verdict") or "").strip()
        if claimed in ("FAIL", "NO-DATA"):
            verdict = claimed
        else:
            verdict = "NO-DATA"
            notes.append("NO-DATA: this record claims the postcondition reached %r. `sbe task "
                         "close` returns before its forced branch on that word, so a record "
                         "reaching here with it did not run the postcondition. The package "
                         "carries NO-DATA and names the claim rather than printing a clean "
                         "verdict over a diff nobody cleared." % (claimed or "(nothing)"))
        who = str(forced.get("who") or "").strip() or \
            "NO-DATA: the record names nobody as forcing this close"
        why = str(forced.get("why") or "").strip() or \
            "NO-DATA: the record carries no reason for forcing this close"
        raw = forced.get("violations")
        violations = [str(v) for v in raw] if isinstance(raw, (list, tuple)) else []

    line = ("sbe task close %s: FORCED by %s (%s). The diff postcondition reached %s."
            % (task_id, who, why, verdict))
    details = [_COMPOSED_FROM_REGISTRY,
               "the task id: %s" % task_id,
               "the agent on the record: %s" % (str(record.get("agent") or "").strip()
                                                or "NO-DATA: none recorded"),
               "the role on the record: %s" % (str(record.get("role") or "").strip()
                                               or "NO-DATA: none recorded"),
               "who forced it: %s" % who,
               "why: %s" % why]
    if forced is not None:
        details.append("when: %s" % (str(forced.get("at") or "").strip()
                                     or "NO-DATA: the record carries no timestamp"))
    for path in violations:
        details.append("VIOLATION %s (changed outside the declaration, closed over anyway)"
                       % path)
    if not violations:
        details.append("NO-DATA: this record lists no violation path. Either the "
                       "postcondition could not be computed at all, in which case nothing "
                       "was compared, or the list was not carried; `.sbe/tasks.json` holds "
                       "the record either way.")

    package = build_package(root, {
        "kind": "forced-close",
        # A forced close is decided by a human, not by a shipped check, so there
        # is no deciding code to excerpt and nothing invents one.
        "check": "",
        "verdict": verdict,
        "verdictLine": line,
        "unquotedLineCount": 0,
        "details": details,
        "dossier": None,
    })
    package["notes"].extend(notes)
    return write_package(root, package)


# ---------------------------------------------------------------------------
# H8: how long a tiered change actually takes, start to finish, in one number
# per tier. `sbe intake` now stamps `openedAt` the moment a dossier's tier is
# declared (its earliest honest "started" moment); `tasks.cmd_close` already
# stamps `closedAt` on both a clean close and a forced one (see
# `record_forced_close` above, whose own `forced.at` is a DIFFERENT, narrower
# timestamp: who forced a close and when, not when the change itself
# finished). This section reads those two existing stamps back; it invents
# neither.
# ---------------------------------------------------------------------------

def _dossier_intake(root, change):
    """The `openedAt`/`tier` a task's `change` field's own dossier declared,
    as one dict with keys `openedAt` and `tier`, or None when `change` is
    empty, the dossier's `00-intake.json` cannot be read, or it carries no
    `tier`.

    Only `design/<change>/00-intake.json` is read: the same single-dossier,
    directory-name-is-the-change-id convention `work.cmd_start` writes
    (`change_id = os.path.basename(dossier)`) and `reviewroute._resolve_target`
    already documents as the common case. A dossier living somewhere else on
    disk is not found here, and a task whose tier cannot be resolved this way
    is left out of every bucket below rather than guessed into one."""
    change = (change or "").strip()
    if not change:
        return None
    path = os.path.join(root, "design", change, INTAKE_REL)
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    tier = data.get("tier")
    if tier not in TIERS:
        return None
    return {"openedAt": data.get("openedAt"), "tier": tier}


def close_durations_by_tier(root):
    """{tier: [seconds, ...]} across every entry in `policy.TIERS`, one
    sample per CLOSED task in `.sbe/tasks.json` whose `change` resolves to a
    dossier carrying both a readable `tier` and an `openedAt` intake stamp.
    The duration measured is intake `openedAt` to that task's own
    `closedAt`: when the change was declared to when the work closing it
    finished, not the narrower open-to-close span of one task's own worktree.

    A task missing `closedAt`, a change with no resolvable tier, or a
    dossier with no `openedAt` (an intake written before this stamp existed)
    contributes no sample and is never guessed at."""
    from . import tasks as tasks_mod
    buckets = {tier: [] for tier in TIERS}
    try:
        data = tasks_mod.load_registry(root)
    except tasks_mod.RegistryUnusable:
        return buckets
    for task in data.get("tasks", []) or []:
        if task.get("status") != "closed" or not task.get("closedAt"):
            continue
        intake = _dossier_intake(root, task.get("change"))
        if intake is None or not intake["openedAt"]:
            continue
        try:
            opened_epoch = tasks_mod._iso_epoch(intake["openedAt"])
            closed_epoch = tasks_mod._iso_epoch(task["closedAt"])
        except ValueError as exc:
            # The renderer prints "median over N samples", so a pair dropped here
            # shrinks N with no sign that anything was dropped. This file's own
            # ethos is NO-DATA over silent exclusion, and a reader cannot tell an
            # honest twelve from a twelve that was fifteen. Named on stderr,
            # mirroring the sbe lineage and sbe explain sites below.
            sys.stderr.write(
                "sbe close-durations: skipping task %r, its timestamps did not "
                "parse (%s). The median below is over the remaining samples.\n"
                % (task.get("id", "?"), exc))
            continue
        duration = closed_epoch - opened_epoch
        if duration >= 0:
            buckets[intake["tier"]].append(duration)
    return buckets


def render_close_durations_by_tier(root):
    """One line per tier in `policy.TIERS`: the median close duration in
    seconds over every sample `close_durations_by_tier` found, or NO-DATA
    naming that no closed, tiered, intake-stamped task exists for it yet."""
    import statistics
    buckets = close_durations_by_tier(root)
    lines = []
    for tier in TIERS:
        samples = buckets[tier]
        if not samples:
            lines.append(
                "%s: NO-DATA (no closed task both stamped with an intake openedAt and "
                "resolved to this tier)" % tier)
        else:
            lines.append("%s: median %.0fs over %d closed task(s)"
                         % (tier, statistics.median(samples), len(samples)))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# `sbe explain`: browse a package that exists, or regenerate one that does not.
#
# THIS SURFACE READS. The one file it may create is a NEW package under the next
# id, written through `write_package` above, which is the single writer and the
# single place the append-only refusal lives. Nothing here opens a file for
# writing, nothing here edits a package that already exists, and nothing here
# runs a check: a regenerated package carries the deciding code, the declared
# logic and the checklist, and its verdict section says NO-DATA, because no run
# has been made and this module may not make one.
# ---------------------------------------------------------------------------

def _no_run_verdict_line(name):
    """The verdict section of a REGENERATED package, which never reads clean."""
    return ("NO-DATA: no run of %r is recorded against this commit in this decision store, so "
            "there is no verdict line to quote. A run of the gate that owns this check, over "
            "this tree, writes one: `bin/sbe gate <directory>` records a package per FAIL and "
            "per WAIVED line it prints. What is below is the check's shipped logic, not a "
            "result." % name)


def _store_lines(store, location, listing):
    """The provenance header a browse prints before the package itself: which
    store was read, why that store, and everything in it that could not be."""
    lines = ["- store read: %s (%s, because %s)"
             % (store, location["location"], location["reason"]),
             "- %d package(s) read out of that store" % len(listing["packages"])]
    for problem in listing["problems"]:
        lines.append("- %s" % problem)
    return lines


def _unresolved(store, listing, sentence):
    """The refusal, as the same dict shape a resolved browse returns, so a
    caller never has to tell two shapes apart."""
    known = ", ".join("%s (%s)" % (p["dirName"], p["check"] or "no check recorded")
                      for p in listing["packages"])
    return {"resolved": False, "regenerated": False, "text": "", "path": None,
            "store": store,
            "problem": "%s This store holds %d package(s)%s."
                       % (sentence, len(listing["packages"]),
                          "" if not known else ": %s" % known)}


def explain(root, target):
    """Browse or regenerate ONE decision package, as ONE dict with keys
    `resolved`, `regenerated`, `text`, `path`, `store` and `problem`.

    Never a pair, and never a bare string: a caller needs to know whether the
    name resolved (it decides the exit code), what to print, and where the file
    it is looking at lives.

    WHICH STORE IS READ, and why it is not a second rule: `package_location`,
    the same function the writers use, decides it from the same input. A
    directory carrying `00-intake.json` names a project, so its packages live in
    that dossier's store; a directory that does not carries no project to name,
    so they live in the repository store. Reading them with a second rule is how
    `sbe explain` would go blind to packages this same module wrote.

    HOW A NAME RESOLVES:

      - A target beginning with three digits is a DECISION ID. It matches the
        package whose own recorded id or directory name is that, and nothing
        else. An id the store does not hold is refused by name, never answered
        with a neighbouring package.
      - Any other target is a gate or check name. The newest package for it
        BOUND TO THE CURRENT HEAD is printed as it stands. With none bound to
        this head, one is REGENERATED from the shipped registry, allocated the
        next id, and written; the older package for the same decision is named
        as superseded and left byte for byte where it is.
      - A name no package and no shipped registry knows is refused, naming the
        name. An empty package for a check nobody ships would be a document
        about nothing.
    """
    name = " ".join(str(target or "").split())
    top = repo_top_of(root)
    head = head_commit_of(root)
    location = package_location(top["top"], root)
    store = location["dir"]
    listing = list_packages(store)
    if not name:
        return _unresolved(store, listing,
                           "sbe explain: no decision id and no check name was given, so "
                           "nothing was looked up.")

    if name[:3].isdigit():
        wanted = [p for p in listing["packages"]
                  if name in (p["id"], p["dirName"]) or p["id"] == name.zfill(3)]
        if not wanted:
            return _unresolved(store, listing,
                               "sbe explain: no decision package in this store carries the id "
                               "%r." % name)
        chosen = max(wanted, key=lambda p: p["dirName"])
        try:
            with io.open(chosen["path"], encoding="utf-8") as fh:
                body = fh.read()
        except (OSError, ValueError) as exc:
            return _unresolved(store, listing,
                               "sbe explain: %s exists and could not be read (%s), so the "
                               "package it holds was not printed."
                               % (chosen["path"], exc))
        header = ["sbe explain: decision %s, as it was written" % chosen["id"]]
        header.extend(_store_lines(store, location, listing))
        header.append("- read from: %s" % chosen["path"])
        header.append("- this browse wrote nothing.")
        return {"resolved": True, "regenerated": False, "path": chosen["path"],
                "store": store, "problem": "",
                "text": "%s\n\n%s" % ("\n".join(header), body)}

    at_head = [p for p in listing["packages"]
               if p["check"] == name and head["commit"] and p["commit"] == head["commit"]]
    if at_head:
        chosen = max(at_head, key=lambda p: p["dirName"])
        try:
            with io.open(chosen["path"], encoding="utf-8") as fh:
                body = fh.read()
        except (OSError, ValueError) as exc:
            return _unresolved(store, listing,
                               "sbe explain: %s exists and could not be read (%s), so the "
                               "package it holds was not printed."
                               % (chosen["path"], exc))
        header = ["sbe explain: %s, the newest package bound to this head" % name]
        header.extend(_store_lines(store, location, listing))
        header.append("- read from: %s" % chosen["path"])
        header.append("- this browse wrote nothing. Regenerating happens only when no package "
                      "for this name is bound to the current head.")
        return {"resolved": True, "regenerated": False, "path": chosen["path"],
                "store": store, "problem": "",
                "text": "%s\n\n%s" % ("\n".join(header), body)}

    named_before = [p for p in listing["packages"] if p["check"] == name]
    found = registries()
    if not named_before and name not in found["declarations"]:
        return _unresolved(
            store, listing,
            "sbe explain: %r is neither a decision id in this store nor a check any shipped "
            "registry under tools/ declares, so there is nothing to explain and no package "
            "was written. %d registry module(s) were read and they declare %d check(s)%s."
            % (name, len(found["modules"]), len(found["declarations"]),
               "" if not found["declarations"]
               else " (%s)" % ", ".join(sorted(found["declarations"]))))

    package = build_package(root, {
        "kind": EXPLAIN_KIND,
        "check": name,
        "verdict": "NO-DATA",
        "verdictLine": _no_run_verdict_line(name),
        "unquotedLineCount": 0,
        "dossier": root,
    })
    package["notes"].append(
        "NO-DATA: this package was REGENERATED on request by `sbe explain %s`, not written at "
        "a decision. No check was run to build it: the sections below are the shipped check's "
        "own declared logic and its deciding code, read from this project's source, and the "
        "verdict section says NO-DATA because there is no run to quote." % name)
    header = ["sbe explain: %s, regenerated from the shipped check registry" % name]
    header.extend(_store_lines(store, location, listing))
    header.append("- %d package(s) in this store name this check, and none is bound to the "
                  "current head (%s), so one was regenerated under the next id."
                  % (len(named_before), head["commit"] or "no head resolved"))
    try:
        written = write_package(root, package)
    except DecisionUnwritable as exc:
        # Named, never swallowed, and it does not stop the answer: the reader
        # asked what the check does, and the regenerated package below answers
        # that whether or not it could also be filed.
        header.append("- NO-DATA: this regenerated package could not be written (%s), so it is "
                      "printed here and recorded nowhere." % exc)
        written = None
    else:
        header.append("- written to: %s" % written)
    return {"resolved": True, "regenerated": True, "path": written, "store": store,
            "problem": "", "text": "%s\n\n%s" % ("\n".join(header), render_package(package))}


# ---------------------------------------------------------------------------
# `sbe lineage <artifact>`: the chain for one artifact, oldest to newest, one
# line per hop, an evidence pointer on every hop.
#
# THIS SURFACE ONLY READS. It consults, in this order: the task registry for
# the binding that claimed the path, the evidence store for every receipt whose
# coveredFiles names the artifact, the decision store for packages naming it,
# the notes store, and `git log --follow` for commits and authors. Every store
# it cannot read, and every store that is absent, renders ONE hop of kind
# NO-DATA naming the store and what would fill it: an absent store is never a
# silently shorter chain. Nothing here re-derives a rule the owning module
# already states: `tasks.load_registry` reads the registry, `evidence.load` and
# `evidence.verify` read and judge the receipts, `list_packages` reads the
# decision store, and `impact._git` runs the one git read.
# ---------------------------------------------------------------------------

#: Where notes on an artifact will live once `notes.py` ships in Loop 4. Spelled
#: here so the NO-DATA hop for the store names the same place that module will
#: read, rather than a path remembered differently on each side.
NOTES_REL = os.path.join(".sbe", "notes")

#: The written-at header line `render_package` writes, read back for a decision
#: hop's timestamp. The same writer-and-reader pairing as the prefixes above.
_WRITTEN_AT_RE = re.compile(r"^- written at: (\S+)", re.M)


def _hop(when, kind, summary, evidence):
    """ONE lineage hop, as ONE dict with keys `when`, `kind`, `summary` and
    `evidence`. Four named keys, never a tuple. `evidence` is the file, receipt
    path, commit sha or decision package a reader can open, and no caller may
    hand an empty one: a hop with no pointer proves nothing."""
    return {"when": when, "kind": kind, "summary": summary, "evidence": evidence}


def _same_artifact(recorded, artifact):
    """Whether a recorded path and the asked-for artifact name the same file,
    by normalized exact equality. Deliberately narrow: a substring match here
    would claim receipts about `seed.txt.bak` as evidence about `seed.txt`."""
    return os.path.normpath(str(recorded or "")) == os.path.normpath(str(artifact or ""))


def _lineage_binding(top, artifact):
    """The task-registry hops: which binding claimed this path, as ONE dict
    with keys `present` and `hops`. The overlap question is answered by
    `tasks.claims_overlap`, the same rule `sbe task check` runs, never by a
    second spelling of it here."""
    from . import tasks as tasks_mod
    registry = tasks_mod.registry_path(top)
    rel = os.path.relpath(registry, top)
    if not os.path.exists(registry):
        return {"present": False, "hops": [_hop(
            "", "NO-DATA",
            "NO-DATA: no task registry exists at %s, so which binding claimed %s was never "
            "recorded. `sbe task open --owns %s` is what writes that record."
            % (rel, artifact, artifact), registry)]}
    try:
        data = tasks_mod.load_registry(top)
    except tasks_mod.RegistryUnusable as exc:
        return {"present": True, "hops": [_hop(
            "", "NO-DATA",
            "NO-DATA: the task registry at %s could not be read (%s), so which binding "
            "claimed %s is unknown here. Nothing in this chain says nobody held it."
            % (rel, exc, artifact), registry)]}
    hops = []
    records = data.get("tasks") or []
    for record in records:
        owned = [str(p) for p in (record.get("ownedPaths") or [])
                 if tasks_mod.claims_overlap(str(p), artifact, top)]
        if not owned:
            continue
        hops.append(_hop(
            str(record.get("openedAt") or ""), "task-binding",
            "task %s (role %s, status %s) claimed %s under its declared path(s): %s"
            % (record.get("id") or "(no id)", record.get("role") or "(none)",
               record.get("status") or "(none)", artifact, ", ".join(owned)),
            "%s, task record %s" % (rel, record.get("id") or "(no id)")))
    if not hops:
        hops.append(_hop(
            "", "NO-DATA",
            "NO-DATA: %s holds %d task record(s) and none claims %s, so no binding ever "
            "fenced this artifact. `sbe task open --owns %s` would."
            % (rel, len(records), artifact, artifact), registry))
    return {"present": True, "hops": hops}


def _claimed_worktrees(top):
    """{runId: (worktree, taskId)} for every registry record that claims a
    receipt by `evidenceId` and still declares a worktree that exists on
    disk. THE SAME LOOKUP `status._scan_evidence` already builds (see that
    function's own docstring: "A receipt CLAIMED BY A TASK RECORD verifies
    against that task's own tree"), read here a second time for a second
    surface rather than re-derived: a receipt sealed inside a finished
    task's own worktree is evidence for that branch's head, and verifying
    it against the repository top instead misreports every finished task's
    own sound evidence as a broken claim (the rc.10 gap `status.py`
    disclosed and fixed for `sbe status`; `sbe lineage` carried the
    identical gap, reproduced by `tools/test_sbe_decisions.py::
    TestLineageWorktreeResolution`, because its own receipt scan never made
    this lookup at all). An unreadable registry returns `{}`: lineage
    already renders that failure as its own NO-DATA hop in
    `_lineage_binding`, and this function does not invent a second,
    competing finding for it."""
    from . import tasks as tasks_mod
    try:
        records = tasks_mod.load_registry(top).get("tasks", [])
    except tasks_mod.RegistryUnusable:
        return {}
    claimed = {}
    for record in records:
        run_id = record.get("evidenceId")
        worktree = record.get("worktree")
        if run_id and worktree and os.path.isdir(worktree):
            claimed[run_id] = (worktree, record.get("id"))
    return claimed


def _lineage_receipts(top, artifact):
    """The evidence-store hops, as ONE dict with keys `present` and `hops`.

    Every receipt whose coveredFiles names the artifact becomes one hop. A
    receipt that fails `evidence.verify` is a hop of kind `broken-receipt`
    with FAIL in its summary and it STAYS VISIBLE: a broken claim is a
    finding, never a gap. A receipt this run could not read at all is a
    NO-DATA hop naming the path, because whether it covers the artifact is
    exactly what could not be read.

    A receipt CLAIMED BY A TASK RECORD (see `_claimed_worktrees`) verifies
    against that task's own declared worktree, not against `top`: the same
    resolution `status._scan_evidence` already makes, read here so `sbe
    lineage` and `sbe status` can never disagree about the same receipt
    again over the same commit."""
    from . import evidence as evidence_mod
    from . import tasks as tasks_mod
    store = tasks_mod.evidence_dir(top)
    rel_store = os.path.relpath(store, top)
    if not os.path.isdir(store):
        return {"present": False, "hops": [_hop(
            "", "NO-DATA",
            "NO-DATA: no evidence store exists at %s, so no receipt names %s. `bin/sbe "
            "evidence run -- <command>` writes one per run." % (rel_store, artifact),
            store)]}
    claimed_by_run_id = _claimed_worktrees(top)
    paths = []
    for dirpath, _dirnames, filenames in os.walk(store):
        for filename in filenames:
            if filename.endswith(".json"):
                paths.append(os.path.join(dirpath, filename))
    paths.sort()
    hops = []
    named = 0
    for full in paths:
        rel = os.path.relpath(full, top)
        try:
            receipt = evidence_mod.load(full)
        except evidence_mod.ReceiptUnreadable as exc:
            hops.append(_hop(
                "", "NO-DATA",
                "NO-DATA: receipt %s could not be read (%s), so whether it covers %s is "
                "unknown and this chain may be missing a run." % (rel, exc, artifact),
                full))
            continue
        covered = [entry.get("path") for entry in (receipt.get("coveredFiles") or [])
                   if isinstance(entry, dict) and entry.get("path")]
        if not any(_same_artifact(path, artifact) for path in covered):
            continue
        named += 1
        run_id = receipt.get("runId")
        verify_cwd, claimed_task = (None, None)
        if run_id and run_id in claimed_by_run_id:
            verify_cwd, claimed_task = claimed_by_run_id[run_id]
        judged = evidence_mod.verify(full, cwd=verify_cwd or top)
        resolved = (", verified in task %s's declared worktree" % claimed_task
                    if verify_cwd else "")
        when = str(receipt.get("endedAt") or receipt.get("startedAt") or "")
        argv_text = " ".join(str(a) for a in (receipt.get("argv") or []))
        if judged["verdict"] == "FAIL":
            hops.append(_hop(
                when, "broken-receipt",
                "receipt %s covers %s and verifies FAIL (%s)%s; it stays visible, because "
                "a broken claim is a finding and never a gap"
                % (rel, artifact, "; ".join(judged["reasons"]), resolved), full))
        else:
            hops.append(_hop(
                when, "receipt",
                "receipt %s covers %s: verify says %s over the run `%s`, exit code %s%s"
                % (rel, artifact, judged["verdict"], argv_text or "(argv not recorded)",
                   receipt.get("exitCode"), resolved), full))
    if named == 0:
        hops.append(_hop(
            "", "NO-DATA",
            "NO-DATA: %d receipt(s) under %s and none names %s in its coveredFiles, so no "
            "recorded run is bound to this artifact. `bin/sbe evidence run --covers %s -- "
            "<command>` would bind one." % (len(paths), rel_store, artifact, artifact),
            store))
    return {"present": True, "hops": hops}


def _lineage_decisions(root, top, artifact):
    """The decision-store hops, as ONE dict with keys `present` and `hops`.

    The store is the one `package_location` names for this root, the same rule
    the writers and `sbe explain` use. A package names the artifact when its
    own body carries the path, which is stated as the substring test it is:
    a package quotes one verdict line and composed detail lines, and the
    artifact a decision was about appears in those or not at all. Every store
    problem `list_packages` found is forwarded as a NO-DATA hop rather than
    dropped, because a package nobody could read may be exactly the one that
    names this artifact."""
    location = package_location(top, root)
    store = location["dir"]
    if not os.path.isdir(store):
        return {"present": False, "hops": [_hop(
            "", "NO-DATA",
            "NO-DATA: no decision store exists at %s, so no decision package names %s. A "
            "gate FAIL, a WAIVED check, a tier decision or a forced close is what writes "
            "one." % (store, artifact), store)]}
    listing = list_packages(store)
    hops = [_hop("", "NO-DATA", problem, store) for problem in listing["problems"]]
    named = 0
    for package in listing["packages"]:
        try:
            with io.open(package["path"], encoding="utf-8") as fh:
                body = fh.read()
        except (OSError, ValueError) as exc:
            hops.append(_hop(
                "", "NO-DATA",
                "NO-DATA: decision package %s could not be read (%s), so whether it names "
                "%s is unknown." % (package["path"], exc, artifact), package["path"]))
            continue
        if artifact not in body:
            continue
        named += 1
        stamp = _WRITTEN_AT_RE.search(body)
        hops.append(_hop(
            stamp.group(1) if stamp else "", "decision",
            "decision %s (%s) names %s; check %s, bound to commit %s"
            % (package["id"], package["dirName"], artifact,
               package["check"] or "(unnamed)", package["commit"] or "(unresolved)"),
            package["path"]))
    if named == 0:
        hops.append(_hop(
            "", "NO-DATA",
            "NO-DATA: %d decision package(s) under %s and none names %s, so no recorded "
            "decision is bound to this artifact."
            % (len(listing["packages"]), store, artifact), store))
    return {"present": True, "hops": hops}


def _lineage_notes(top, artifact):
    """The notes hop, as ONE dict with keys `present` and `hops`. Exactly one
    hop, and in this loop it is always NO-DATA: the notes store is absent by
    design, `notes.py` ships in Loop 4, and a named absence is never a dropped
    hop and never silence."""
    key = _slug(artifact, "artifact")
    store = os.path.join(top, NOTES_REL, key)
    rel_store = ".sbe/notes/%s/" % key
    present = os.path.isdir(store)
    if present:
        summary = ("NO-DATA: %s exists on disk and no notes reader ships in this loop, so "
                   "whatever it holds was not read. The notes store is absent from this "
                   "loop by design: notes.py ships in Loop 4 and is what would fill this "
                   "hop." % rel_store)
    else:
        summary = ("NO-DATA: the notes store %s is absent in this loop by design (notes.py "
                   "ships in Loop 4), so no note on %s was read. That store is what would "
                   "fill this hop." % (rel_store, artifact))
    return {"present": present, "hops": [_hop("", "notes-NO-DATA", summary, store)]}


def _lineage_commits(top, artifact):
    """The commit hops, read through the existing `_git` helper, as ONE dict
    with keys `present` and `hops`. A repository git cannot answer for
    produces one NO-DATA hop naming that, never a silently shorter chain, and
    so does a history in which no commit ever touched the artifact."""
    try:
        code, out, err = _git(
            ["log", "--follow", "--format=%H%x1f%at%x1f%an", "--", artifact], top)
    except OSError as exc:
        return {"present": False, "hops": [_hop(
            "", "NO-DATA",
            "NO-DATA: git could not be run in %s (%s), so no commit history for %s was "
            "read. Nothing here says the chain is short." % (top, exc, artifact), top)]}
    if code != 0:
        return {"present": False, "hops": [_hop(
            "", "NO-DATA",
            "NO-DATA: git log --follow cannot answer for %s in %s (%s), so the commit "
            "chain was not read. Nothing here says the chain is short."
            % (artifact, top, err.strip() or "no error text"), top)]}
    hops = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 3:
            hops.append(_hop(
                "", "NO-DATA",
                "NO-DATA: one line of git log output did not parse into sha, date and "
                "author, so one commit in this chain is unreadable here.", top))
            continue
        sha, epoch, author = parts
        try:
            when = _iso(int(epoch))
        except ValueError:
            when = ""
        hops.append(_hop(when, "commit",
                         "commit %s by %s touched %s" % (sha[:12], author, artifact), sha))
    if not hops:
        hops.append(_hop(
            "", "NO-DATA",
            "NO-DATA: git log --follow names no commit touching %s in this repository, so "
            "either it was never committed here or it lives under another name. A commit "
            "that adds it is what would fill this hop." % artifact, top))
    return {"present": True, "hops": hops}


def lineage(root, artifact):
    """The chain for one artifact, as ONE dict with keys `artifact`, `hops`
    and `notes`. Never a pair.

    Each hop carries `when`, `kind`, `summary` and `evidence` (the file,
    receipt path, commit sha or decision package a reader can open), sorted
    oldest to newest; hops nothing dated sort after every dated one rather
    than being guessed a place in time. The last note states how many stores
    were consulted, read and absent, and the renderer prints it as the final
    line, so a chain read over a half-missing estate says so on its face.
    """
    root = os.path.abspath(root)
    name = " ".join(str(artifact or "").split())
    top_result = repo_top_of(root)
    top = top_result["top"]
    notes = []
    if top_result["note"]:
        notes.append(top_result["note"])
    results = [
        _lineage_binding(top, name),
        _lineage_receipts(top, name),
        _lineage_decisions(root, top, name),
        _lineage_notes(top, name),
        _lineage_commits(top, name),
    ]
    hops = []
    read = 0
    for result in results:
        hops.extend(result["hops"])
        if result["present"]:
            read += 1
    # "~" sorts after every ISO timestamp, so undated hops land at the end in
    # scan order rather than being guessed a place in time.
    hops.sort(key=lambda hop: hop["when"] or "~")
    notes.append("%d store(s) were consulted for this chain: %d read and %d absent. An "
                 "absent store is a NO-DATA hop above, never a shorter chain."
                 % (len(results), read, len(results) - read))
    return {"artifact": name, "hops": hops, "notes": notes}


def render_lineage(data):
    """The chain as the text a human reads: one line per hop, oldest to
    newest, the evidence pointer on the same line, and the store count last."""
    out = ["sbe lineage: %s, oldest to newest, one line per hop\n"
           % (data["artifact"] or "(no artifact named)")]
    for hop in data["hops"]:
        out.append("%s  %-14s %s [evidence: %s]\n"
                   % (hop["when"] or "(no timestamp)   ", hop["kind"], hop["summary"],
                      hop["evidence"]))
    for note in data["notes"]:
        out.append("%s\n" % note)
    return "".join(out)


def _lineage_parser():
    parser = argparse.ArgumentParser(
        prog="sbe lineage",
        description="Walk the chain for one artifact, oldest to newest: the binding that "
                    "claimed it, every receipt covering it, every decision package naming "
                    "it, notes, and the commits that touched it, with an evidence pointer "
                    "on every hop. A store that is absent is a NO-DATA hop, never a "
                    "shorter chain.")
    parser.add_argument("artifact",
                        help="the artifact to walk, as a path relative to the repository "
                             "top (for example src/module.py)")
    parser.add_argument("--path", default=None,
                        help="the directory to walk from (default: the current one, or "
                             "--cwd when given); the stores are read at that directory's "
                             "repository top")
    cwd_mod.add_cwd_argument(parser)
    return parser


def _lineage_main(rest, exit_ok, exit_usage):
    """The `sbe lineage` surface, routed here by `main` below. Exit 0 when a
    chain was printed, whatever the hops in it say: a printed chain is a
    printed chain, and a chain of NO-DATA hops is a true answer."""
    rest = list(rest)
    if rest and rest[0] in ("-h", "--help"):
        _lineage_parser().print_help(sys.stdout)
        return exit_ok
    try:
        args = _lineage_parser().parse_args(rest)
    except SystemExit as exc:
        return exit_ok if exc.code in (0, None) else exit_usage
    root = os.path.abspath(cwd_mod.resolve(args.path, args.cwd))
    if not os.path.isdir(root):
        sys.stderr.write("sbe lineage: '%s' is not a directory, so no store was read. A "
                         "mistyped path must not read as an empty chain.\n" % root)
        return exit_usage
    sys.stdout.write(render_lineage(lineage(root, args.artifact)))
    return exit_ok


def _explain_parser():
    parser = argparse.ArgumentParser(
        prog="sbe explain",
        description="Print the decision package for a decision id, or for a gate or check "
                    "name. With no package bound to the current head, one is regenerated from "
                    "the shipped check registry and its verdict section says NO-DATA, because "
                    "no run was made. An existing package is never overwritten.")
    parser.add_argument("target",
                        help="a decision id (NNN or NNN-slug), or the name of a gate or check")
    parser.add_argument("--path", default=None,
                        help="the directory the decision was taken over (default: the current "
                             "one, or --cwd when given). A directory carrying 00-intake.json "
                             "names a project, so its packages are read from that dossier's "
                             "decisions/ store; otherwise they are read from .sbe/decisions/ "
                             "at the repository top")
    cwd_mod.add_cwd_argument(parser)
    return parser


def main(rest, exit_ok=0, exit_failed=1, exit_usage=2, surface="explain"):
    """The `sbe explain` and `sbe lineage` surfaces. Exit codes come from the
    caller so this module never disagrees with the CLI's documented table: 0
    when a package or a chain was printed, 2 for an id or a name nothing
    knows, and for a path that is not a directory.

    `surface` names which of the two the CLI dispatched, defaulting to
    `explain` so every existing caller keeps its meaning. `exit_failed` is
    accepted and not used, because nothing here reaches a verdict: a printed
    package is a printed package whatever the run it quotes decided. It is in
    the signature so this module keeps the one shape `tasks.main`,
    `evidence.main` and `prverify.main` already have, and so a later surface
    in this module that DOES fail has the code to return.
    """
    if surface == "lineage":
        return _lineage_main(rest, exit_ok, exit_usage)
    rest = list(rest)
    if rest and rest[0] in ("-h", "--help"):
        # Help is not an error: it prints this surface's own usage and exits 0,
        # rather than being refused as a missing target.
        _explain_parser().print_help(sys.stdout)
        return exit_ok
    try:
        args = _explain_parser().parse_args(rest)
    except SystemExit as exc:
        # argparse already answered: -h/--help printed the usage that was asked
        # for and raised 0. Anything nonzero is a refused flag or argument.
        return exit_ok if exc.code in (0, None) else exit_usage
    root = os.path.abspath(cwd_mod.resolve(args.path, args.cwd))
    if not os.path.isdir(root):
        sys.stderr.write("sbe explain: '%s' is not a directory, so no decision store was read. "
                         "A mistyped path must not read as a store holding nothing.\n" % root)
        return exit_usage
    result = explain(root, args.target)
    if not result["resolved"]:
        sys.stderr.write("%s\n" % result["problem"])
        return exit_usage
    sys.stdout.write(result["text"])
    if not result["text"].endswith("\n"):
        sys.stdout.write("\n")
    return exit_ok
