"""BAND L, L2 AND L3: an observation is a claim about the world, so its
record is predeclared or it is late, never silently either.

L2, THE OBSERVATION CONTRACT. What will be watched in production, and for
how long, is a `contracts.py` lifecycle object (the six-field identity spine
plus `contracts.OBSERVATION_CONTRACT_BINDING`: `headCommit`, plus `contracts.
OBSERVATION_CONTRACT_FIELDS`: `declaredAt`, `observes`, `window`). This
module builds one, validates one through `contracts.validate("observation-
contract", ...)` (the one reader, never a second shape check invented here),
and judges its TIMING against a release receipt through `contracts.
declaration_timing(contract, receipt)`, the one function in `contracts.py`
that already knows PREDECLARED from LATE_DECLARATION from UNDETERMINED.
`judge_observation_timing` below passes that word through UNCHANGED and adds
one explicit field, `presentedAsPredeclared`: a late declaration may still
record real observations (an observer that shows up after the fact still
saw something), but it must never be shown to a reader as though its
criteria were fixed before the release it watches, which is exactly the lie
`declaration_timing`'s own docstring names ("a description of what already
happened"). That field is ALWAYS present and is never a silent default: a
caller that forgets to read `declarationTiming` still gets the honest
boolean rather than the module assuming PREDECLARED because nobody asked.

L3, THE OBSERVATION RESULT ADAPTER. `read_observation_result` reads ONE
signal from a FILE, and ONLY a file: no network call, no vendor SDK, no
subprocess. `OBSERVATION_TRUST_CLASSES` is a subset of `evidence.
RELEASE_SOURCE_CLASSES` (L1's own closed vocabulary, IMPORTED rather than
reinvented here, so "fixture" means the same thing on an observation result
that it means on a release receipt): "file" and "fixture" only, because
this adapter never makes the "ci-run" claim -- nothing in it calls out to a
CI system, and declaring that trust class over a plain file read would be a
receipt for a network call this module never performs. An unavailable or
unreadable source is never silently treated as a real reading: the result's
own `status` is `"NO-DATA"`, naming the path and a remedy, and the ONLY
other status is `"OBSERVED"`, never `"PASS"`, because a bare file read is
not a check.

L1's OWN VOCABULARY IS IMPORTED, NEVER REINVENTED: `OBSERVATION_TRUST_CLASSES`
below is the subset of `evidence.RELEASE_SOURCE_CLASSES` this file/fixture
adapter can honestly claim, computed FROM that tuple at import time rather
than typed out a second time, so "fixture" can never drift into meaning two
different things on a release receipt and on an observation result.
`observation.py` sits outside the `evidence` / `contracts` / `handover` /
`status` import cycle (nothing in that cycle imports THIS module), so this
import is safe at module scope even though `evidence.py` itself has to
import `contracts` lazily to avoid closing that cycle on itself.

`contracts.py` is FROZEN for this lane: read and consumed, never edited or
reimplemented. Python floor is 3.9: no match statements, no `X | Y`
annotations. Standard library only.
"""
import io
import json
import os
import time

from . import contracts as contracts_mod
from . import evidence as evidence_mod


def _iso(epoch):
    """ISO 8601 in UTC, to the second, with an explicit `Z`.

    A copy of `evidence._iso`'s own three lines, not an import of it: that
    name is module-private (a leading underscore, the same convention this
    function's own name uses), and reaching into another module's private
    helper is the same coupling the leading underscore exists to warn a
    reader off. `contracts._instant` needs a date, a time AND an explicit
    UTC offset to read a string as an instant at all (see its own
    docstring); this format satisfies all three the same way `evidence.py`'s
    copy already does, so both modules' timestamps stay comparable.
    """
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


# ---------------------------------------------------------------------------
# L2: THE OBSERVATION CONTRACT
# ---------------------------------------------------------------------------

def build_observation_contract(change_id, head_commit, observes, window, producer,
                               producer_class="tool", origin=None, declared_at=None):
    """An observation contract: the `contracts.py` lifecycle fields this
    surface carries (`contracts.OBSERVATION_CONTRACT_BINDING`, `contracts.
    OBSERVATION_CONTRACT_FIELDS`). Builds a dict; writes nothing.

    `observes` names what will be watched (a signal name, or a list of
    them -- this module does not constrain its shape beyond what `contracts.
    validate_observation_contract` requires, which is presence alone).
    `window` is the observation window, in whatever shape the caller and
    `read_observation_result` below agree on (this module reads it back only
    to echo it onto a result, never to parse it as a date range itself).

    `declared_at` defaults to NOW (`_iso`, this module's own formatter,
    matching `evidence.py`'s): the field `contracts.declaration_timing`
    compares against a release receipt's `releasedAt` to tell PREDECLARED
    from LATE_DECLARATION, so a contract built here without an explicit
    timestamp still carries one that rule can parse.
    """
    return {
        "schemaVersion": contracts_mod.LIFECYCLE_SCHEMA_VERSION,
        "changeId": change_id,
        "createdAt": declared_at or _iso(time.time()),
        "producer": producer,
        "producerClass": producer_class,
        "origin": origin,
        "headCommit": head_commit,
        "declaredAt": declared_at or _iso(time.time()),
        "observes": observes,
        "window": window,
    }


def validate_observation_contract(contract):
    """The house 3-tuple over `contract`, via `contracts.validate(
    "observation-contract", ...)`: the ONE reader, so this module never
    re-derives the same shape check a second way that could drift from it.
    """
    return contracts_mod.validate("observation-contract", contract)


def judge_observation_timing(contract, receipt):
    """One dict judging `contract`'s declaration timing against `receipt`
    (a release receipt, L1's own shape): the module's central L2 job.

    `declarationTiming`: one of `contracts.DECLARATION_STATES`
    (`"PREDECLARED"`, `"LATE_DECLARATION"`, `"UNDETERMINED"`), read straight
    from `contracts.declaration_timing(contract, receipt)` and passed
    through UNCHANGED -- this function never re-derives PREDECLARED or LATE
    a second way.

    `mayRecordObservations`: always `True`. A late declaration is still a
    declaration, and a real observer who shows up after the release still
    saw something real; refusing to record it over a timing technicality
    would throw away a true observation, which is a different failure from
    the one this module exists to prevent.

    `presentedAsPredeclared`: `True` ONLY when `declarationTiming` is
    exactly `"PREDECLARED"`; `False` for both `"LATE_DECLARATION"` and
    `"UNDETERMINED"`. AN EXPLICIT FIELD, NEVER A SILENT DEFAULT: this is the
    module docstring's own law in code. A caller that renders an
    observation contract's criteria to a reader must consult this field
    rather than assume predeclaration because a contract document merely
    exists, because `contract` is refused first here on the SAME timing
    question no consumer of it should silently skip.
    """
    timing = contracts_mod.declaration_timing(contract, receipt)
    return {
        "declarationTiming": timing,
        "mayRecordObservations": True,
        "presentedAsPredeclared": timing == "PREDECLARED",
    }


# ---------------------------------------------------------------------------
# L3: THE OBSERVATION RESULT ADAPTER
# ---------------------------------------------------------------------------

#: The subset of L1's `evidence.RELEASE_SOURCE_CLASSES` this file/fixture
#: adapter can honestly claim, IMPORTED from there rather than typed out a
#: second time (see the module docstring). "ci-run" is excluded ON PURPOSE:
#: nothing in `read_observation_result` calls a CI system, a network
#: endpoint or a vendor SDK, so declaring that trust class here would be a
#: receipt for a call this adapter never makes.
OBSERVATION_TRUST_CLASSES = tuple(
    c for c in evidence_mod.RELEASE_SOURCE_CLASSES if c != "ci-run")

#: The closed vocabulary `read_observation_result`'s own `status` is drawn
#: from. `"OBSERVED"` is the only success state, never `"PASS"`: a bare file
#: read is not a check, and reusing a check-verdict word for it would read
#: as this adapter having verified something it only read.
OBSERVATION_RESULT_STATUSES = ("OBSERVED", "NO-DATA")


def read_observation_result(path, release_id, signal, window, trust_class="file"):
    """One observation result read from a FILE at `path` (JSON), or a
    NO-DATA-shaped result when the path is absent or unreadable. NEVER
    RAISES on an unavailable source, and NEVER returns `"PASS"` (see
    `OBSERVATION_RESULT_STATUSES` above).

    Every result binds `releaseId`, `signal`, `window` (echoed back from the
    caller, the same window the observation contract declared) and `source`
    (`path` plus `trustClass`, imported from L1 rather than reinvented, see
    the module docstring). `freshnessAt` is the source file's own mtime,
    formatted the same way `_iso` formats every other timestamp in this
    module, and is `None` whenever `status` is `"NO-DATA"`: a source nothing
    could read has no freshness to report either.

    Raises `ValueError` naming `trust_class` when it is not one of
    `OBSERVATION_TRUST_CLASSES`: a caller error (an unknown class, or the
    excluded `"ci-run"`), refused before any file is even touched, the same
    way `evidence.build_release_receipt` refuses an unknown trust class
    before building anything.

    `status` is `"NO-DATA"`, naming `path` and a `remedy`, in TWO cases,
    named separately because they are different findings:

      - `path` is absent (falsy, or the file does not exist): the remedy
        names the file that should exist and points at `signal`.
      - `path` exists but does not parse as JSON, or cannot be opened: the
        remedy says the file is broken, never guesses at its content.

    `status` is `"OBSERVED"` only when the file was read AND parsed; `value`
    then carries the parsed JSON content and `remedy` is `None`.
    """
    if trust_class not in OBSERVATION_TRUST_CLASSES:
        raise ValueError(
            "trust_class %r is not one of %s for a file/fixture observation adapter; "
            "\"ci-run\" belongs to a producer that actually calls one, which this adapter "
            "never does" % (trust_class, ", ".join(OBSERVATION_TRUST_CLASSES)))
    source = {"path": path, "trustClass": trust_class}
    result = {"releaseId": release_id, "signal": signal, "window": window, "source": source}

    if not path or not os.path.exists(path):
        result.update({
            "status": "NO-DATA", "value": None, "freshnessAt": None,
            "remedy": "no file exists at %s; record the %r observation there, or point this "
                     "adapter at the file that actually holds it" % (path, signal),
        })
        return result

    try:
        with io.open(path, encoding="utf-8") as fh:
            value = json.load(fh)
    except (IOError, OSError, ValueError) as exc:
        result.update({
            "status": "NO-DATA", "value": None, "freshnessAt": None,
            "remedy": "%s could not be read as an observation (%s: %s); fix or regenerate "
                     "it, never guess its content" % (path, type(exc).__name__, exc),
        })
        return result

    try:
        freshness = _iso(os.path.getmtime(path))
    except OSError:
        freshness = None

    result.update({"status": "OBSERVED", "value": value, "freshnessAt": freshness,
                  "remedy": None})
    return result
