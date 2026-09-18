"""DOM-00.01: evidence-first admission for a proposed feature row.

WHY THIS EXISTS. Forty rows were admitted onto a roadmap without checking
whether they already existed, and fourteen of the next fifteen checked were
duplicates of work already in the tree. The fix is not a reviewer catching
this later, it is a function every proposed row must pass through before it
is ever scheduled: a capability cannot be admitted without naming the
evidence that will prove it (a runnable done check and a runnable negative
check that can go red), the gap it closes (measurable outcome and
baseline), how it is expected to work (mechanism), the exact files it
touches, where the proof will be recorded, and how it is rolled back. A row
missing any of these is REFUSED, never admitted by default, and a row that
exactly repeats one already admitted is REFUSED as a duplicate rather than
scheduled twice.

Python 3, standard library only. No filesystem, no network: every fact this
module needs is passed in by the caller.
"""

REQUIRED_STRING_FIELDS = (
    "measurable_outcome",
    "baseline",
    "expected_mechanism",
    "done_check",
    "negative_done_check",
    "evidence_artifact",
    "rollback",
)

REQUIRED_FIELDS = REQUIRED_STRING_FIELDS + ("exact_files",)

#: "id" is accepted but optional: a caller may carry it for its own
#: bookkeeping. Every other key is unrecognized and refuses the record.
ALLOWED_KEYS = frozenset(REQUIRED_FIELDS) | {"id"}

ADMIT = "ADMIT"
REFUSE = "REFUSE"


def _is_non_empty_string(value):
    """True when value is a string with at least one non-space character."""
    return isinstance(value, str) and value.strip() != ""


def _exact_files_problem(value):
    """None when value is a valid exact_files list, else a reason string
    naming exactly what was wrong with it."""
    if not isinstance(value, list):
        return "field exact_files was not a list, got %s" % type(value).__name__
    if len(value) == 0:
        return "field exact_files was an empty list"
    for index, item in enumerate(value):
        if not _is_non_empty_string(item):
            return "field exact_files entry %d was not a non-empty string" % index
    return None


def _is_duplicate(outcome, files, existing):
    """True when existing holds a prior record with the same
    measurable_outcome and the same set of exact_files. A malformed prior
    record (not a mapping, or an exact_files that is not a plain list of
    strings) is skipped rather than raised on: this function only ever
    compares against records that are themselves well formed, and a
    caller's already-admitted history is assumed to have passed admit()
    itself."""
    for prior in existing:
        if not isinstance(prior, dict):
            continue
        if prior.get("measurable_outcome") != outcome:
            continue
        prior_files = prior.get("exact_files")
        if not isinstance(prior_files, list):
            continue
        if not all(isinstance(item, str) for item in prior_files):
            continue
        if set(prior_files) == set(files):
            return True
    return False


def admit(record, existing=None):
    """Decide whether one proposed feature record may be admitted.

    Returns (verdict, reason). verdict is exactly "ADMIT" or "REFUSE",
    never anything else. reason always names the specific field that was
    missing, invalid, or unrecognized, or the duplicate that was found, or
    (on ADMIT) restates the evidence that will prove the feature.

    record must be a mapping carrying every field in REQUIRED_FIELDS as a
    non-empty string (exact_files as a non-empty list of non-empty
    strings), and no key outside ALLOWED_KEYS. Anything else about record
    (not a mapping at all) refuses rather than raising.

    existing is an optional iterable of previously admitted records. None
    means no prior records to check against. A record whose
    measurable_outcome and exact_files exactly match a prior record is
    refused as a duplicate; any other overlap is not by itself a refusal
    reason.
    """
    if not isinstance(record, dict):
        return REFUSE, "record could not be read as a mapping: %r" % (record,)

    for key in record:
        if key not in ALLOWED_KEYS:
            return REFUSE, "unrecognized field: %s" % key

    for field in REQUIRED_FIELDS:
        if field not in record:
            return REFUSE, "missing field: %s" % field

    for field in REQUIRED_STRING_FIELDS:
        if not _is_non_empty_string(record[field]):
            return REFUSE, "field %s was missing or not a non-empty string" % field

    files_problem = _exact_files_problem(record["exact_files"])
    if files_problem is not None:
        return REFUSE, files_problem

    outcome = record["measurable_outcome"]
    files = record["exact_files"]
    if existing is not None and _is_duplicate(outcome, files, existing):
        return REFUSE, "duplicate of existing feature with measurable_outcome: %s" % outcome

    return ADMIT, "admitted with done_check: %s" % record["done_check"]
