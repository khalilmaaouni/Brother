"""LP-0201: one versioned JSON contract registry for the surfaces this tool
already emits, so the CLI, skills and the future workspace read ONE schema
instead of five private shapes that can drift apart unnoticed.

BAND K added a SECOND generation to the same registry, and it is a different
kind of thing, so read the two halves differently. The first eleven pages of
this file describe five surfaces that already exist and are validated against
what their real producers actually print. The last section describes six
lifecycle objects NOTHING in this repository emits yet (the change passport,
the decision packet, the human decision, the release receipt, the observation
contract, the verified-reality state): bands R and L build the producers, and
their identity rules are settled HERE first, deliberately, because the top
half of this file exists to clean up after five producers that each invented
their own idea of identity, and the cheapest time to not do that again is
before the first one is written. Everything from the `BAND K` banner down is
therefore a promise about documents that do not exist yet, and a fixture for
it is hand built rather than captured, which `tools/test_sbe_contracts.py`
says out loud in the same terms.

WHY THIS EXISTS. Five producers (`brothersbe.tasks`, `brothersbe.status`
twice over, `brothersbe.work`, `brothersbe.handover`) each grew its own
`schemaVersion` field, its own "these are the required keys" list, and its
own refusal wording, independently, over ten release candidates. Nothing
compared them. A consumer written against `sbe status --json` today has no
single place to learn what the surface promises, or what changes when the
tool's own version moves. This module is that place: a name for each
surface, the fields it promises, the `schemaVersion` values it is known to
have written, and one `validate_*` function per surface that checks a
document against that promise without guessing at fields it does not know.

WHAT THIS DOES NOT DO, stated here because a control that oversells itself
is worse than none (the same rule `brothersbe.tasks`'s own module docstring
states):

  - It does not PARSE JSON. Every `validate_*` function takes an already
    loaded Python object (whatever `json.loads`/`json.load` handed back),
    the same way `evidence._check_schema` takes an already loaded receipt.
    A caller with raw text parses it and decides what an unparseable
    document means for itself; this module has nothing useful to add there.
  - It does not validate every nested field. The task registry's nested task
    records are checked against `tasks.RECORD_FIELDS` (and `role`/`status`
    against `tasks.ROLES`/`tasks.STATUSES`) because those are the exact
    depth `tasks.load_registry` itself already enforces; the other four
    surfaces are checked at their TOP LEVEL only (does every promised field
    exist, does `schemaVersion` name a version this registry knows). A
    `scope`, `notes`, `evidence` or `acknowledgment` sub-object one level
    down is not walked. Depth grows in a later wave, named as a gap here
    rather than silently assumed complete.
  - It does not WRITE anything, or change what any of the five producers
    already emit. `sbe status --team --json` (`status.build_team_report`)
    carries no `schemaVersion` field as of 1.0.0-rc.16, and this module does
    not add one; `validate_status_team` accepts its absence, by name, in one
    place (see the comment above `STATUS_TEAM_KNOWN_SCHEMA_VERSIONS`), and
    starts requiring it the day the producer starts writing it.
  - Unknown fields are ALLOWED, on every surface: a document that carries
    more than this registry names is forward compatible, not broken, and is
    never refused for that alone. An unknown `schemaVersion` is the opposite
    case and IS refused, by name, never parsed hopefully: a field read under
    the wrong semantics is worse than one that fails loudly (the same rule
    `evidence._check_schema` and `tasks.load_registry` already state for
    their own one surface each).

WHERE THE KNOWN VERSIONS COME FROM. Every producer that exports its own
schema-version constant is imported here rather than retyped, the same "one
overlap rule, imported from the module that owns it" discipline `tasks.py`
already uses for `paths_overlap`: `tasks.KNOWN_REGISTRY_VERSIONS` for the
task registry, the package-level `brothersbe.SCHEMA_VERSION` for `sbe
status --json` and the handover record (both literally read it), and
`handover.SCHEMA_FIELDS` for the handover record's exact field tuple. Two
producers export no constant at all: `work._brief_document` writes the
literal `"1.0"` inline, and `status.build_report`/`status.build_team_report`
build their return dicts inline with no exported field tuple. Those two
surfaces' known versions and field tuples are typed out by hand below, each
with a comment naming the exact function this registry copies, so a reader
can diff this file against that function rather than trust a claim.

CONTRACTS_SCHEMA_VERSION, an integer, is THIS registry's own version, not
any producer's. Every producer this module knows about is at major version
1 today (`tasks.REGISTRY_VERSION` reads `"1.0"`, the package `SCHEMA_VERSION`
reads `"1.0"`, the work brief and the handover record both write `"1.0"`):
this registry continues that same generation as the integer 1 rather than
starting a second count disconnected from the artifacts it describes, and
moves only when the SHAPE of the registry itself (not a producer's own
version) changes in a way an old consumer of THIS module could misread.

THE HOUSE 3-TUPLE. Every `validate_*` function returns `(verdict, evidence,
problems)`: `verdict` is one of `"PASS"`, `"FAIL"`, `"NO-DATA"` (the same
three-word vocabulary `tools/sbe_checks.py:VERDICTS` and every `sbe doctor`
check already use; never a bare boolean, because "the document was not even
given" and "the document is the wrong shape" are different findings and a
bool cannot carry both); `evidence` is one human-readable summary line, the
same job `detail` does in `cli.py`'s `_doctor_checks()` triples; `problems`
is a tuple of the individual named failures, empty on `"PASS"`, so a caller
can act on the SPECIFIC field that is wrong instead of re-parsing `evidence`.
Three values, not two, mirroring the shape `tools/fixtures/golden-scenario/
build_scenario.py`'s own `run_sbe` docstring names as this project's
standing convention for a verdict-shaped return: `NO-DATA` only ever means
"no document was given to check" (`data is None`), never "the document
exists but does not match" (`FAIL`, the shape `evals/test_no_data_class.py`'s
own `legacy_cases` already fixes for every OTHER check in this project: a
wrong-shaped JSON blob is `FAIL`, not `NO-DATA`, because absence and a
broken claim are different findings).

THE SPINE'S OWN RULES, stated here because they are the reason band K
exists and they are easy to weaken by accident later:

  - IDENTITY IS ANSWERED, NOT MERELY PRESENT. Every lifecycle object carries
    `IDENTITY_SPINE` plus the commit or artifact identity its own kind binds
    to, and every one of those fields goes through `answered()`. `"changeId":
    ""` is present and identifies nothing, which is worse than absent because
    it looks like a binding. The BINDERS hold to the same bar and for a
    sharper reason (`_hollow_problems`): they compare two documents' values,
    and `None == None`, so two documents that each identify nothing once
    matched each other perfectly and were reported as binding.
  - A HUMAN DECISION BINDS THREE THINGS OR IT IS REFUSED: the change, the
    head, and the digest of the exact packet the person was shown. Not two of
    three, and not "warn on mismatch": `decision_binds` returns FAIL, because
    a decision bound to another head or another packet is not a weaker
    approval, it is an approval of something else. The producer-class rule
    sits beside it and is checked in ALL THREE readers of a decision
    (`validate_human_decision`, `decision_binds`, `release_binds`; see
    `_not_human_problems`), because a rule checked in two of three places is
    a rule whose route around it is to call the third.
  - A LATE DECLARATION IS NAMED, NOT ACCEPTED. An observation contract dated
    after the release it claims to watch reads `LATE_DECLARATION`, never
    `PREDECLARED`, so "we were watching for that" cannot be written once the
    answer is already in. That comparison is only as good as the two
    timestamps, so `_instant` refuses a date with no time on it and a time
    with no UTC offset rather than supplying either by assumption.
  - EVERY DECLARED BINDING IS RECOMPUTED SOMEWHERE. A digest field nothing
    ever recomputes is a citation, not a binding: `decision_binds` recomputes
    `packetSha256` and `reality_binds` recomputes
    `observationContractSha256`, both through `_digest_of`, which turns a
    document with no canonical encoding into a named problem rather than an
    exception out of an authorization path.

Readiness and reality are LIFECYCLE STATES, never a fourth check verdict:
`VERDICTS` stays the three words this project already closed on, and
`READINESS_STATES`/`REALITY_STATES`/`DECLARATION_STATES` are disjoint from it
by construction, which `tools/test_sbe_contracts.py` pins as a test rather
than leaving to care.

Python floor is 3.9: no match statements, no `X | Y` annotations. Standard
library only, no `jsonschema` dependency: every check below is an explicit,
named comparison, the same style `evidence.py`'s `_check_schema` and
`_check_required` already use for the one surface they cover.
"""
import hashlib
import json
import re
from datetime import datetime, timezone

from . import SCHEMA_VERSION
from . import handover as handover_mod
from . import tasks as tasks_mod
from ._toolspath import mount

mount()
from sbe_checks import answered  # noqa: E402  (tools/ has to be mounted first)

#: This registry's own schema version. See the module docstring for why it
#: starts at 1 rather than at a number disconnected from the artifacts it
#: describes.
CONTRACTS_SCHEMA_VERSION = 1

#: The vocabulary every validator's `verdict` element is drawn from,
#: byte-identical to `tools/sbe_checks.py:VERDICTS`. Not imported from
#: there: importing it would pull `tools/` onto `sys.path` for three string
#: literals this module can simply hold, and unlike `tasks.RECORD_FIELDS` or
#: `handover.SCHEMA_FIELDS` this tuple is not owned by any one producer to
#: drift away from; every surface in this project already agrees on it.
VERDICTS = ("PASS", "FAIL", "NO-DATA")

#: `sbe status --json` (`status.build_report`'s own return dict, `src/
#: brothersbe/status.py`, the `return {...}` just above `def any_blocking`).
#: No exported field-tuple constant exists there to import; typed out by
#: hand, and this comment names the exact statement to diff against.
#: NOTE: this tuple was already missing "possiblyBehind" (added by PR #85,
#: P5) before this H3 change touched it; that gap is pre-existing and not
#: this change's to close. "deploymentDrift" (H3) is added here because this
#: change is the one that introduced it.
STATUS_FIELDS = ("schemaVersion", "tool", "toolVersion", "generatedAt", "root", "scope",
                 "brokenClaims", "mergeBlockers", "activeConflicts", "missingEvidence",
                 "soundEvidence", "notes", "nextAction", "nextActionDetail", "handover",
                 "deploymentDrift", "possiblyBehind")

#: `sbe status --team --json` (`status.build_team_report`'s own return dict,
#: the `return {"tool": ...}` just above `TEAM_BLOCKING_SEVERITIES`). Also
#: hand-typed for the same reason as `STATUS_FIELDS`. Deliberately carries
#: no `"schemaVersion"` entry: the real producer writes none as of
#: 1.0.0-rc.16, and a required-field list that named one anyway would make
#: every real `sbe status --team --json` call fail THIS validator over a gap
#: that is `status.py`'s to close, not this registry's to paper over.
STATUS_TEAM_FIELDS = ("tool", "root", "headCommit", "changes", "findings", "handover",
                      "planOnly", "completedTasks", "basisLegend")

#: The work brief (`work._brief_document`'s own return dict, `src/
#: brothersbe/work.py`). Matches `CONTRACT_FIELDS` in `tools/
#: test_sbe_work_brief.py`, which already pins this exact set against the
#: real command; not imported from that test module on purpose (a test file
#: is not a dependency other code should reach into), typed out by hand
#: instead, from the same source statement that test itself reads.
WORK_BRIEF_FIELDS = ("schemaVersion", "taskId", "title", "why", "baselineCommit", "planPath",
                     "scope", "mustNotTouch", "dependencies", "acceptance",
                     "verificationCommands", "relevantPointers", "knownConstraints",
                     "stopConditions", "requiredEvidenceKind", "model",
                     "maxAttemptsPerApproach")

#: The one surface with no local `schemaVersion` at all yet. Accepted when
#: absent (see the module docstring); when PRESENT, held to the same known
#: set every OTHER surface at major version 1 already carries, so a future
#: `status.py` change that starts stamping `"schemaVersion": SCHEMA_VERSION`
#: onto the team report (matching every other producer) is already valid
#: against this registry the day it ships, with no edit here required.
STATUS_TEAM_KNOWN_SCHEMA_VERSIONS = (SCHEMA_VERSION,)

#: The work brief's own known versions. `work._brief_document` writes the
#: literal `"1.0"` inline (no exported constant to import, unlike
#: `tasks.REGISTRY_VERSION` or the package `SCHEMA_VERSION`); this tuple is
#: that same literal, named here so a second producer version, when it
#: exists, is added rather than swapped in.
WORK_BRIEF_KNOWN_SCHEMA_VERSIONS = ("1.0",)


def _missing_fields(data, required):
    """Every name in `required` that `data` does not carry, sorted. Presence
    only: a field holding `None` or `""` still counts as present here, the
    same "required means the KEY exists" reading `tasks.load_registry`'s own
    `RECORD_FIELDS` check uses. A surface that additionally wants "answered,
    not just present" is a stricter, later check, not this one."""
    return sorted(f for f in required if f not in data)


def _shape_problem(data, label):
    """None when `data` is a JSON object this module can inspect further, or
    a named problem string when it is not. `data is None` is handled by the
    caller as `NO-DATA` (nothing was given at all, an absence); everything
    that is not a `dict` but IS something is `FAIL` material here, the same
    "wrong shape is a broken claim, not an absence" rule
    `evals/test_no_data_class.py`'s own `legacy_cases` already fixes for
    every check that reads a JSON file in this project. `label` names the
    surface in the problem string; the caller passes it (each already states
    the label in its own docstring) rather than this function reading it off
    shared module state, so two `validate_*` calls in flight at once, on two
    different surfaces, can never make one borrow the other's name in its
    problem text."""
    if not isinstance(data, dict):
        return ("this %s is a JSON %s, not an object; a valid document is a JSON object "
               "at the top level" % (label, type(data).__name__))
    return None


def _verdict(problems, pass_evidence):
    """(verdict, evidence, problems) from a (possibly empty) list of named
    problem strings and the evidence line to use when there are none."""
    if not problems:
        return "PASS", pass_evidence, ()
    summary = "%d problem(s): %s" % (len(problems), "; ".join(problems))
    return "FAIL", summary, tuple(problems)


def validate_task_registry(data):
    """The task registry (`.sbe/tasks.json`, `tasks.load_registry`'s own
    file). Checks `schemaVersion` against `tasks.KNOWN_REGISTRY_VERSIONS`,
    the two top-level fields `tasks.load_registry` itself reads
    (`schemaVersion`, `tasks`), and, for every entry in `tasks` that is a
    JSON object, its fields against `tasks.RECORD_FIELDS` and its `role`/
    `status` against `tasks.ROLES`/`tasks.STATUSES`: the exact depth
    `tasks.load_registry` already enforces, imported rather than
    reimplemented so the two cannot silently disagree about what a valid
    record looks like.

    NOT A PURE OBJECT CHECK, despite the promise the rest of this module
    keeps (see the module docstring's "it does not PARSE JSON" section).
    `controllerLeases` is checked through `tasks._validate_lease`, which
    calls `os.path.realpath` on each lease's `worktree` to confirm it is
    already canonical, and `realpath` consults the filesystem: a symlink at
    that path changes what it resolves to, so the SAME in-memory document
    can validate `PASS` before that symlink exists and `FAIL` after, on the
    same machine, with nothing about the document itself having changed.
    That makes this one check machine- and moment-dependent in a way no
    other check in this module is. Named here rather than fixed tonight:
    the honest fix is a lease check that does not touch the filesystem, a
    larger change than this pass, and pretending the dependence does not
    exist would be worse than stating it."""
    if data is None:
        return "NO-DATA", "no task registry document was given to validate", ()
    shape = _shape_problem(data, "task registry")
    if shape:
        return "FAIL", shape, (shape,)

    problems = []
    version = data.get("schemaVersion")
    if "schemaVersion" not in data:
        problems.append("schemaVersion is missing: nothing here knows what the "
                        "remaining fields mean")
    elif version not in tasks_mod.KNOWN_REGISTRY_VERSIONS:
        problems.append("schemaVersion %r is not one this registry knows (%s); refused "
                        "rather than parsed hopefully"
                        % (version, ", ".join(tasks_mod.KNOWN_REGISTRY_VERSIONS)))

    missing = _missing_fields(data, ("schemaVersion", "tasks"))
    if missing:
        problems.append("missing required top-level field(s): %s" % ", ".join(missing))

    tasks_list = data.get("tasks")
    if "tasks" in data:
        if not isinstance(tasks_list, list):
            problems.append("'tasks' is %s, not a list of records"
                            % type(tasks_list).__name__)
        else:
            for i, record in enumerate(tasks_list):
                label = "tasks[%d]" % i
                if not isinstance(record, dict):
                    problems.append("%s is %s, not a task record object"
                                    % (label, type(record).__name__))
                    continue
                rec_id = record.get("id")
                if isinstance(rec_id, str) and rec_id:
                    label = "task %r" % rec_id
                rec_missing = _missing_fields(record, tasks_mod.RECORD_FIELDS)
                if rec_missing:
                    problems.append("%s is missing field(s): %s"
                                    % (label, ", ".join(rec_missing)))
                role = record.get("role")
                if "role" in record and role not in tasks_mod.ROLES:
                    problems.append("%s has role %r, not one of %s"
                                    % (label, role, ", ".join(tasks_mod.ROLES)))
                status = record.get("status")
                if "status" in record and status not in tasks_mod.STATUSES:
                    problems.append("%s has status %r, not one of %s"
                                    % (label, status, ", ".join(tasks_mod.STATUSES)))

    if "controllerLeases" in data:
        leases = data.get("controllerLeases")
        if not isinstance(leases, list):
            problems.append("'controllerLeases' is %s, not a list of records"
                            % type(leases).__name__)
        else:
            for i, lease in enumerate(leases):
                try:
                    tasks_mod._validate_lease(lease, "task registry", i)
                except tasks_mod.RegistryUnusable as exc:
                    problems.append(str(exc))
                except (ValueError, OSError) as exc:
                    # `os.path.realpath` inside `_validate_lease` raises
                    # `ValueError` on a worktree string carrying an embedded
                    # NUL byte (and can raise `OSError` for other paths this
                    # platform's C library refuses), neither of which
                    # `RegistryUnusable` covers: an authorization-adjacent
                    # check leaves through a verdict, never through a raise.
                    problems.append("task registry: controllerLeases[%d] worktree path "
                                    "is not checkable: %s" % (i, exc))

    count = len(tasks_list) if isinstance(tasks_list, list) else 0
    return _verdict(problems, "schemaVersion %r, %d task record(s), every required "
                              "field present" % (version, count))


def validate_status(data):
    """`sbe status --json` (`status.build_report`'s return dict). Checks
    `schemaVersion` against the package's own `brothersbe.SCHEMA_VERSION`
    (the same constant `status.py` stamps the field with) and every field in
    `STATUS_FIELDS`."""
    if data is None:
        return "NO-DATA", "no sbe status document was given to validate", ()
    shape = _shape_problem(data, "sbe status --json document")
    if shape:
        return "FAIL", shape, (shape,)

    problems = []
    version = data.get("schemaVersion")
    if "schemaVersion" not in data:
        problems.append("schemaVersion is missing: nothing here knows what the "
                        "remaining fields mean")
    elif version != SCHEMA_VERSION:
        problems.append("schemaVersion %r is not one this build knows (%s); refused "
                        "rather than parsed hopefully" % (version, SCHEMA_VERSION))

    missing = _missing_fields(data, STATUS_FIELDS)
    if missing:
        problems.append("missing required field(s): %s" % ", ".join(missing))

    return _verdict(problems, "schemaVersion %r, every one of %d required field(s) "
                              "present" % (version, len(STATUS_FIELDS)))


def validate_status_team(data):
    """`sbe status --team --json` (`status.build_team_report`'s return
    dict). Checks every field in `STATUS_TEAM_FIELDS`. `schemaVersion` is
    OPTIONAL on this one surface, by name, per the module docstring: absent
    is accepted (the real producer writes none as of 1.0.0-rc.16); present
    but unrecognized is still refused, against
    `STATUS_TEAM_KNOWN_SCHEMA_VERSIONS`."""
    if data is None:
        return "NO-DATA", "no sbe status --team document was given to validate", ()
    shape = _shape_problem(data, "sbe status --team --json document")
    if shape:
        return "FAIL", shape, (shape,)

    problems = []
    if "schemaVersion" in data:
        version = data.get("schemaVersion")
        if version not in STATUS_TEAM_KNOWN_SCHEMA_VERSIONS:
            problems.append("schemaVersion %r is not one this build knows (%s); refused "
                            "rather than parsed hopefully"
                            % (version, ", ".join(STATUS_TEAM_KNOWN_SCHEMA_VERSIONS)))

    missing = _missing_fields(data, STATUS_TEAM_FIELDS)
    if missing:
        problems.append("missing required field(s): %s" % ", ".join(missing))

    changes = data.get("changes")
    change_count = len(changes) if isinstance(changes, list) else 0
    if "schemaVersion" in data:
        version_clause = "schemaVersion %r" % data.get("schemaVersion")
    else:
        version_clause = ("no schemaVersion field (not yet emitted by this surface, "
                          "accepted by name)")
    note = ("%s, %d change(s) named, every one of %d required field(s) present"
           % (version_clause, change_count, len(STATUS_TEAM_FIELDS)))
    return _verdict(problems, note)


def validate_work_brief(data):
    """The work brief (`sbe work brief --json`, `work._brief_document`'s
    return dict). Checks `schemaVersion` against
    `WORK_BRIEF_KNOWN_SCHEMA_VERSIONS` and every field in
    `WORK_BRIEF_FIELDS`."""
    if data is None:
        return "NO-DATA", "no work brief document was given to validate", ()
    shape = _shape_problem(data, "work brief document")
    if shape:
        return "FAIL", shape, (shape,)

    problems = []
    version = data.get("schemaVersion")
    if "schemaVersion" not in data:
        problems.append("schemaVersion is missing: nothing here knows what the "
                        "remaining fields mean")
    elif version not in WORK_BRIEF_KNOWN_SCHEMA_VERSIONS:
        problems.append("schemaVersion %r is not one this build knows (%s); refused "
                        "rather than parsed hopefully"
                        % (version, ", ".join(WORK_BRIEF_KNOWN_SCHEMA_VERSIONS)))

    missing = _missing_fields(data, WORK_BRIEF_FIELDS)
    if missing:
        problems.append("missing required field(s): %s" % ", ".join(missing))

    return _verdict(problems, "schemaVersion %r, every one of %d required field(s) "
                              "present" % (version, len(WORK_BRIEF_FIELDS)))


def validate_handover(data):
    """The handover record (`12-handover.json`, `handover.SCHEMA_FIELDS`'s
    own document). Checks `schemaVersion` against the package's own
    `brothersbe.SCHEMA_VERSION` (the same constant `handover.py` stamps the
    field with) and every field in `handover.SCHEMA_FIELDS`, imported
    rather than retyped."""
    if data is None:
        return "NO-DATA", "no handover record was given to validate", ()
    shape = _shape_problem(data, "handover record")
    if shape:
        return "FAIL", shape, (shape,)

    problems = []
    version = data.get("schemaVersion")
    if "schemaVersion" not in data:
        problems.append("schemaVersion is missing: nothing here knows what the "
                        "remaining fields mean")
    elif version != SCHEMA_VERSION:
        problems.append("schemaVersion %r is not one this build knows (%s); refused "
                        "rather than parsed hopefully" % (version, SCHEMA_VERSION))

    missing = _missing_fields(data, handover_mod.SCHEMA_FIELDS)
    if missing:
        problems.append("missing required field(s): %s" % ", ".join(missing))

    return _verdict(problems, "schemaVersion %r, every one of %d required field(s) "
                              "present" % (version, len(handover_mod.SCHEMA_FIELDS)))


# ---------------------------------------------------------------------------
# BAND K, THE CONTRACT SPINE. The five lifecycle objects the chain in
# `NORTH-STAR.md` names below the Change Passport seam: the passport a
# producer hands over, the packet a human is asked to decide on, the human
# decision itself, the release receipt, the observation contract, and the
# verified-reality state that closes the loop. Everything above this line
# describes a surface this tool ALREADY emits; everything below describes a
# surface bands R and L will emit, and describes it FIRST, on purpose, so
# the identity rules are settled before five producers grow five private
# ideas of what "the same change" means (which is exactly the drift the top
# half of this module was written to clean up after).
#
# NOTHING BELOW WRITES, REDUCES OR DECIDES ANYTHING. `READINESS_STATES` is
# the vocabulary a readiness state is drawn from, not a reducer (that is
# R1's, in `lifecycle.py`); `REALITY_STATES` is the same for reality (L4);
# `declaration_timing` compares two timestamps two documents already carry
# and never fetches, adapts or observes anything (L2, L3).
# ---------------------------------------------------------------------------

#: Schema versions the five lifecycle objects can declare, OLDEST FIRST: the
#: order is load-bearing, exactly as `evidence.KNOWN_SCHEMA_VERSIONS`'s is,
#: because `_fields_for` below reads it as the version timeline.
LIFECYCLE_KNOWN_SCHEMA_VERSIONS = ("1.0", "1.1")

#: The version a NEW lifecycle object should be stamped with. Lifecycle-local
#: for the same reason `evidence.EVIDENCE_SCHEMA_VERSION` is evidence-local:
#: coupling it to the package-wide `SCHEMA_VERSION` would force every consumer
#: of the task registry and the status document to move in lockstep with a
#: change only these six objects carry.
LIFECYCLE_SCHEMA_VERSION = "1.1"

#: Which lifecycle version INTRODUCED which field, so a record is judged by
#: its OWN version's contract and an existing record stays valid where it
#: stands. Same mechanism, same shape and the same forward-only rule as
#: `evidence.FIELDS_INTRODUCED_IN`; add a row when a version adds a field and
#: nothing else here needs to change.
#:
#: WHICH FIELDS MAY ARRIVE LATE, AND WHICH MAY NOT. `origin` is here because
#: its absence costs a reader one traceability hop and costs no check its
#: answer: it is the `ciRunUrl` case, a field nothing in this module resolves,
#: fetches or compares. The inverse rule is the one that matters and it is
#: absolute: a field an AUTHORIZATION check reads is required from the first
#: version or it is not an authorization check at all. `producerClass` is the
#: worked example. It is in the 1.0 spine and it will never appear in this
#: table, because a version-scoped `producerClass` would mean a human decision
#: declaring "1.0" could omit the one field `validate_human_decision` reads to
#: refuse an agent-authored decision, and a rule with a version-shaped hole in
#: it is a rule an author picks their way around by declaring an older version.
LIFECYCLE_FIELDS_INTRODUCED_IN = (
    ("1.1", ("origin",)),
)

#: The six facts EVERY lifecycle object carries, whatever else it carries:
#: which contract generation it was written under, which change it is about,
#: when it came into being, who or what produced it, which CLASS that producer
#: belongs to (the machine-checkable half of `producer`: a free-text name
#: cannot be compared against `PRODUCER_CLASSES`, and the human-decision rule
#: needs a comparison, not a hopeful read of a string), and where it came
#: from. The commit or artifact identity that applies is NOT here: it is named
#: per object below, because "the identity that applies" is a different fact
#: for a decision packet (the head it was built over) than for a release
#: receipt (the commit that actually went out), and one shared field name
#: holding both would be the exact ambiguity this spine exists to remove.
IDENTITY_SPINE = ("schemaVersion", "changeId", "createdAt", "producer",
                  "producerClass", "origin")

#: The one producer class allowed to author a human decision.
PRODUCER_CLASS_HUMAN = "human"

#: Every producer class a lifecycle object may declare. Closed: an unknown
#: class is refused rather than read as "probably not a human", because the
#: whole point of the field is that the answer is stated rather than inferred.
PRODUCER_CLASSES = ("human", "agent", "pipeline", "tool")

#: The classes a human decision may NEVER be authored by, named rather than
#: derived as "everything except `PRODUCER_CLASS_HUMAN`", so a class added to
#: `PRODUCER_CLASSES` later has to be placed here deliberately.
NON_HUMAN_PRODUCER_CLASSES = ("agent", "pipeline", "tool")

#: The change passport's five fields, `tools/sbe_passport.py:FIELDS`'s own
#: `key` column, in that tuple's own order. Typed out by hand rather than
#: imported for the reason the module docstring gives for `VERDICTS`:
#: importing would mount `tools/` for five string literals. Note what that
#: tool is and is not: `sbe_passport` REPORTS on the seam (which of the five
#: fields anything at this seam can fill, `render_json`'s `root`/`fields`/
#: `carried` document), and no producer in this repository emits a filled
#: passport yet. This tuple is the shape of the object being reported ON, the
#: one a consumer below the seam receives.
PASSPORT_FIELDS = ("whatWasDone", "whoDidIt", "whatWasRun",
                   "whatWasNotEstablished", "whereItCameFrom")

#: The commit identity each object binds to, beside `changeId`. Every name
#: here is held to the same "present AND answered" bar as the spine: a record
#: whose binding field is `""` binds nothing while looking like it binds
#: something, which is worse than one that says nothing at all.
PASSPORT_BINDING = ("headCommit",)
DECISION_PACKET_BINDING = ("headCommit",)
HUMAN_DECISION_BINDING = ("headCommit", "packetSha256")
RELEASE_RECEIPT_BINDING = ("releasedCommit", "artifactSha256", "runId")
OBSERVATION_CONTRACT_BINDING = ("headCommit",)
VERIFIED_REALITY_BINDING = ("releasedCommit", "observationContractSha256")

#: The one identity field a version table is allowed to introduce late, named
#: here so the exemption is a decision on the record rather than the accident
#: of nobody checking. `origin` is the `ciRunUrl` case `LIFECYCLE_FIELDS_
#: INTRODUCED_IN` already argues: nothing in this module resolves, fetches or
#: compares it, so its absence costs a reader one traceability hop and costs
#: no check its answer.
VERSION_SCOPABLE_IDENTITY_FIELDS = ("origin",)

#: Every identity or binding name `_fields_for` may NEVER drop, whatever a
#: future version table says. The table's own comment states the rule ("a
#: field an AUTHORIZATION check reads is required from the first version or
#: it is not an authorization check at all") and until this tuple existed
#: nothing enforced it: a later generation could add `packetSha256` to the
#: table, and a decision declaring `"schemaVersion": "1.0"` would then be
#: judged by a contract with the binding cut out of it, which is the
#: version-shaped hole an author picks their way around by declaring an older
#: version. Derived from the tuples themselves rather than typed out, so a
#: binding field added below is protected the moment it is named.
NEVER_VERSION_SCOPED = tuple(sorted(
    (set(IDENTITY_SPINE) | set(PASSPORT_BINDING) | set(DECISION_PACKET_BINDING)
     | set(HUMAN_DECISION_BINDING) | set(RELEASE_RECEIPT_BINDING)
     | set(OBSERVATION_CONTRACT_BINDING) | set(VERIFIED_REALITY_BINDING))
    - set(VERSION_SCOPABLE_IDENTITY_FIELDS)))

#: `changeId` plus every BINDING field named above, plus `producer`: the
#: identity values this module actually compares for equality somewhere,
#: either directly (`decision.get("changeId") != packet.get("changeId")`) or
#: through `_digest_of`. Python's own cross-type equality (`0 == False ==
#: 0.0`, `1 == True`) means a numeric or boolean value here would bind to an
#: unrelated document by nothing more than that coercion, not by naming the
#: same thing twice, so every field in this set is held to "a non-hollow
#: STRING", not merely `answered()`'s "recording something": `answered()`
#: alone reads `False` and `0` as real answers, correctly for a row count
#: and wrongly for an identity.
#:
#: `producer` IS HERE EVEN THOUGH NOTHING COMPARES IT FOR EQUALITY, unlike
#: every other member of this set. It used to sit beside `schemaVersion`,
#: `createdAt`, `producerClass` and `origin` in the exempt list this comment
#: named, on the stated argument that the type-collision risk `_identity_
#: type_problem` exists for only applies to a field something else binds
#: against. That argument is still true of `createdAt` and `origin`. It
#: never was true of `producer`: the field names WHO OR WHAT claims
#: authorship of this record, the same class of claim `changeId` and every
#: binding field make, and `"producer": 0` or `"producer": false` validated
#: PASS on every surface, an authorship claim that answers nothing typed as
#: though it had, refused nowhere. Held to the same bar for that reason,
#: not because anything compares it. Deliberately narrower than
#: `NEVER_VERSION_SCOPED` still: `schemaVersion`, `createdAt`, `producerClass`
#: and `origin` remain exempt.
IDENTITY_VALUE_FIELDS = tuple(sorted(
    {"changeId", "producer"} | set(PASSPORT_BINDING) | set(DECISION_PACKET_BINDING)
    | set(HUMAN_DECISION_BINDING) | set(RELEASE_RECEIPT_BINDING)
    | set(OBSERVATION_CONTRACT_BINDING) | set(VERIFIED_REALITY_BINDING)))

#: The rest of each object, presence-checked only by `_lifecycle_problems`
#: (the spine and the binding carry the identity; these carry the content).
#: ONE NAMED EXCEPTION: `validate_decision_packet` additionally routes its
#: OWN `DECISION_PACKET_FIELDS` through `answered()`, the central hollow
#: rule, so an empty `knownRisks` is refused there specifically, unlike on
#: every other surface below, where an empty container in the body remains a
#: real answer (an observation contract's `observes: []` is honestly "we
#: watched nothing", not a hollow claim the way a blank packet question is).
DECISION_PACKET_FIELDS = ("readinessState", "question", "knownRisks", "notEstablished")
HUMAN_DECISION_FIELDS = ("decision",)
RELEASE_RECEIPT_FIELDS = ("releasedAt",)
OBSERVATION_CONTRACT_FIELDS = ("declaredAt", "observes", "window")
VERIFIED_REALITY_FIELDS = ("realityState", "observedAt", "basis")

#: Readiness is a LIFECYCLE STATE, never a fourth check verdict. The
#: distinction is ratified in `design/lifecycle-blockers/03-adr.md` and it is
#: mechanical here, not stylistic: `VERDICTS` answers "did this check pass",
#: these answer "where is this change in its life", and the two vocabularies
#: are disjoint by construction so no consumer can read one as the other.
READINESS_STATES = ("READY_FOR_HUMAN_DECISION", "READY_WITH_KNOWN_RISK", "NOT_READY")

#: The same, for the far end of the chain. `REALITY_NOT_OBSERVED` is the
#: honest default and the common one: released, nothing has come back yet.
REALITY_STATES = ("REALITY_NOT_OBSERVED", "VERIFIED_IN_REALITY",
                  "CONTRADICTED_BY_REALITY")

#: The two states that assert a CONCLUSION was reached, as against
#: `REALITY_NOT_OBSERVED`'s honest "nothing has come back yet." The
#: distinction this pair exists for: "evidence absent" (`REALITY_NOT_
#: OBSERVED`, or `basis` simply missing, already its own `FAIL` via
#: `_missing_fields`) and "a value nobody produced" (one of THESE two states
#: declared with an empty `basis`) are different findings and read
#: differently, never the same sentence and never the same verdict arm. An
#: empty `basis` under `REALITY_NOT_OBSERVED` is honest: nothing was watched,
#: so there is nothing to cite. An empty `basis` under either state here is a
#: conclusion with no evidence behind it, which used to validate `PASS`, the
#: happy path, identically to a real observation: absence read as invention's
#: disguise.
DEFINITIVE_REALITY_STATES = ("VERIFIED_IN_REALITY", "CONTRADICTED_BY_REALITY")

#: The next link past `REALITY_STATES`: a release can be `VERIFIED_IN_REALITY`
#: and still have missed the business value it was released for, which is a
#: different question with its own closed vocabulary. Moved HERE as the
#: single source (contract vocabulary law: shared state words live in this
#: module, consumers mirror BY NAME); `lifecycle.py` restates it locally as
#: `VALUE_REALIZATION_STATES` for the same reason `_REALITY_STATE_WORDS`
#: there is spelled locally rather than imported, held in agreement by
#: `tools/test_sbe_reality.py`.
VALUE_REALIZATION_STATES = ("VALUE_REALIZED", "VALUE_NOT_REALIZED",
                            "VALUE_NOT_YET_MEASURABLE", "VALUE_TARGET_MOVED")

#: `facts["measureMaturity"]`'s closed vocabulary for
#: `lifecycle.reduce_value_realization`: has the business measure had enough
#: time or volume behind it to mean anything yet. Same move as
#: `VALUE_REALIZATION_STATES` above; `lifecycle.py` restates it locally as
#: `_MEASURE_MATURITY_STATES`, held in agreement by the same test file.
MEASURE_MATURITY_STATES = ("MATURE", "NOT_YET_MATURE")

#: What `declaration_timing` can say about an observation contract's date
#: relative to the release it claims to watch. `LATE_DECLARATION` is the whole
#: reason this exists: a contract written after the release is a description
#: of what already happened, and reading it as a predeclaration would let a
#: change be "watched" by a promise made once the answer was already in.
#: `UNDETERMINED` covers a missing or unparseable timestamp on either side:
#: nothing was compared, so nothing is claimed.
DECLARATION_STATES = ("PREDECLARED", "LATE_DECLARATION", "UNDETERMINED")

#: The two answers a human decision can record at this node.
DECISION_ANSWERS = ("RELEASE", "HOLD")

#: The ONE of them that authorizes a release, named the way
#: `PRODUCER_CLASS_HUMAN` is: `release_binds` compares against this rather
#: than against "not HOLD", so a third answer added to `DECISION_ANSWERS`
#: later has to be placed here deliberately before it can let anything out.
DECISION_AUTHORIZING_RELEASE = "RELEASE"


def _fields_for(declared, fields):
    """`(field tuple, problems)` for a lifecycle object of `declared` version.

    A straight mirror of `evidence._fields_for` over this module's own version
    timeline: a record is never failed for missing a field its own version
    postdates, and an unknown or missing version is judged by the FULL, newest
    set, which is the strict direction (the caller has already recorded the
    unknown version as its own problem).

    A row in `LIFECYCLE_FIELDS_INTRODUCED_IN` that names a version this
    module does not know is itself a named problem, not a raise: this
    function's own comment used to promise "a bad row costs a refused
    document rather than an unimportable module" while doing the opposite,
    `.index()`-ing a version name straight into an uncaught `ValueError` the
    moment a table swapped in at runtime (a fixture, a future generation)
    named one. Checked by membership first so the promise is kept.

    A ROW ITSELF CAN BE MALFORMED, not just the version name inside it: a row
    of the wrong arity used to raise `ValueError` straight out of `for
    version_name, names in ...` unpacking, and a `names` value that is not a
    list of field names (`None`, a bare string that would silently iterate as
    individual characters instead of a field name, an int) used to either
    raise `TypeError` out of `set.update` or corrupt `later` with characters
    that never match a real field name, a silent wrong answer rather than a
    crash. Both are checked here, by shape, before either half of the row is
    trusted, for the same reason the unknown-version check exists: a bad row
    refuses the document rather than crashing the module or silently
    mismeasuring it.

    THE TABLE IS MATERIALIZED EXACTLY ONCE, THE FIRST TIME IT IS EVER READ.
    A generator (or any other one-shot iterable) swapped in for
    `LIFECYCLE_FIELDS_INTRODUCED_IN` used to validate the SAME document
    `PASS` then `FAIL` on consecutive calls: `tuple(LIFECYCLE_FIELDS_
    INTRODUCED_IN)` materialized it into this call's own `rows`, but the
    module attribute still named the now-exhausted generator, so the next
    call read nothing from it, `later` stayed empty, and every field a real
    later version introduces (never scoped out of `required` because
    nothing scoped it) suddenly counted as missing. Writing the materialized
    tuple straight back onto the module attribute, below, closes that: a
    one-shot iterable is consumed once, ever, and every call after (this one
    included) reads the same stable tuple a real constant already was.
    """
    global LIFECYCLE_FIELDS_INTRODUCED_IN
    if declared not in LIFECYCLE_KNOWN_SCHEMA_VERSIONS:
        return fields, []
    age = LIFECYCLE_KNOWN_SCHEMA_VERSIONS.index(declared)
    later = set()
    problems = []
    # THE TABLE ITSELF CAN BE MALFORMED, not just a row inside it: `for row in
    # LIFECYCLE_FIELDS_INTRODUCED_IN` used to raise `TypeError` straight out
    # of this loop the moment the whole table stopped being iterable (`None`,
    # swapped in at runtime by a fixture or a future generation), which broke
    # this docstring's own promise below the same way a malformed ROW does,
    # and for the same reason: a bad table refuses the document rather than
    # crashing the module.
    try:
        rows = tuple(LIFECYCLE_FIELDS_INTRODUCED_IN)
    except TypeError:
        problems.append("the lifecycle field-introduction table itself is not iterable "
                        "(%r); a bad table refuses the document rather than crashing the "
                        "module" % (LIFECYCLE_FIELDS_INTRODUCED_IN,))
        rows = ()
    else:
        # Materialized once; see the docstring above. A plain assignment,
        # not a conditional one: writing back the SAME tuple an ordinary
        # constant already held is a harmless no-op, and writing back the
        # materialized contents of a one-shot iterable is the fix.
        LIFECYCLE_FIELDS_INTRODUCED_IN = rows
    for row in rows:
        if not isinstance(row, (tuple, list)) or len(row) != 2:
            problems.append("the lifecycle field-introduction table carries a row %r "
                            "that is not a (version, field-names) pair; a bad row "
                            "refuses the document rather than crashing the module"
                            % (row,))
            continue
        version_name, names = row
        if version_name not in LIFECYCLE_KNOWN_SCHEMA_VERSIONS:
            problems.append("the lifecycle field-introduction table names version %r, "
                            "which this registry does not know (%s); a bad row refuses "
                            "the document rather than crashing the module"
                            % (version_name, ", ".join(LIFECYCLE_KNOWN_SCHEMA_VERSIONS)))
            continue
        if not isinstance(names, (tuple, list, set, frozenset)):
            problems.append("the lifecycle field-introduction table's row for version "
                            "%r names %r as its field list, which is not a list of "
                            "field names; a bad row refuses the document rather than "
                            "crashing the module" % (version_name, names))
            continue
        if LIFECYCLE_KNOWN_SCHEMA_VERSIONS.index(version_name) > age:
            later.update(names)
    # A version table may never make an identity or binding field optional,
    # whatever it claims: see `NEVER_VERSION_SCOPED`. Enforced here, in the
    # ONE place the table is read, rather than asserted at import, so the
    # rule holds for a table swapped in at runtime too and so a bad row costs
    # a refused document rather than an unimportable module.
    later.difference_update(NEVER_VERSION_SCOPED)
    return tuple(f for f in fields if f not in later), problems


def _unanswered_fields(data, required):
    """Every name in `required` that `data` carries but that records nothing,
    sorted. `answered()` is `tools/sbe_checks.py`'s own vacuity test, the same
    one every receipt field in `evidence.py` goes through, so `""`, `"   "`,
    `"TODO"` and `"n/a"` record nothing here too, and `0` and `False` are
    answers. `schemaVersion` is exempt: it has its own, stricter check against
    the known-version tuple, and reporting it twice would name one problem in
    two voices."""
    return sorted(f for f in required
                  if f != "schemaVersion" and f in data and answered(data.get(f)) is None)


def _lifecycle_problems(data, binding, body):
    """(problems, declared version) for one lifecycle object: the identity
    spine, the object's own commit/artifact binding, and its body fields.

    Missing fields are reported ONCE, over the union, so a reader gets one
    list to act on rather than three overlapping ones. The identity half
    (spine plus binding) is additionally held to "answered, not merely
    present"; the body is presence-only, per `_missing_fields`' own docstring.

    BODY FIELDS ARE VERSION-SCOPED TOO, exactly as identity and binding
    fields are, and through the SAME `_fields_for` call rather than a second
    one: a body field is passed through `_fields_for` alongside identity and
    binding by naming it in `LIFECYCLE_FIELDS_INTRODUCED_IN` beside them, one
    combined call so a bad table row is reported once, not twice. Before
    this, `_fields_for` was only ever asked about `IDENTITY_SPINE + binding`,
    so a table row naming a BODY field had no effect whatsoever: `required`
    unconditionally included every name in `body`, version or no version,
    which broke this module's own forward-only promise ("a record is never
    failed for missing a field its own version postdates") for exactly the
    fields the promise exists to cover, and made `LIFECYCLE_FIELDS_
    INTRODUCED_IN`'s own comment ("add a row when a version adds a field and
    nothing else here needs to change") false for a body field the moment
    anyone tried it. `NEVER_VERSION_SCOPED` still only lists identity and
    binding names, so no body field is protected from being scoped out by a
    bad or malicious row; that is an accepted, narrower gap than the one this
    fixes (body fields carry no authorization weight the way an identity or
    binding field does), named here rather than silently widened.
    """
    problems = []
    version = data.get("schemaVersion")
    if "schemaVersion" not in data:
        problems.append("schemaVersion is missing: nothing here knows what the "
                        "remaining fields mean")
    elif version not in LIFECYCLE_KNOWN_SCHEMA_VERSIONS:
        problems.append("schemaVersion %r is not one this registry knows (%s); refused "
                        "rather than parsed hopefully"
                        % (version, ", ".join(LIFECYCLE_KNOWN_SCHEMA_VERSIONS)))

    identity_names = IDENTITY_SPINE + tuple(binding)
    scoped, table_problems = _fields_for(version, identity_names + tuple(body))
    problems.extend(table_problems)
    identity = tuple(f for f in identity_names if f in scoped)
    required = scoped
    missing = _missing_fields(data, required)
    if missing:
        problems.append("missing required field(s): %s" % ", ".join(missing))

    empty = _unanswered_fields(data, identity)
    if empty:
        problems.append("identity field(s) present but recording nothing: %s; a field "
                        "that identifies nothing binds nothing" % ", ".join(empty))

    value_fields = [f for f in identity if f in IDENTITY_VALUE_FIELDS]
    problems.extend(_wrong_type_identity_problems(data, value_fields))

    producer_class = data.get("producerClass")
    if "producerClass" in data and producer_class not in PRODUCER_CLASSES:
        problems.append("producerClass %r is not one of %s"
                        % (producer_class, ", ".join(PRODUCER_CLASSES)))
    return problems, version


def _vocabulary_problem(data, field, allowed, label):
    """One named problem when `data[field]` is present but outside `allowed`,
    or None. `label` says what the vocabulary IS in the problem text, so a
    reader is told "readiness state" rather than left to infer it from the
    field name."""
    if field not in data:
        return None
    value = data.get(field)
    if value in allowed:
        return None
    return ("%s %r is not one of the %s this registry knows (%s)"
            % (field, value, label, ", ".join(allowed)))


#: The most decimal digits an integer anywhere in a lifecycle document may
#: carry: this module's OWN bound, not whatever interpreter happens to be
#: running it, and not whatever str-digit limit that interpreter happens to
#: be CONFIGURED with. `json.dumps` converts every int to its decimal
#: string, and CPython enforces a limit on that conversion from 3.11 onward
#: (`sys.set_int_max_str_digits`, default 4300); the 3.9 floor enforces none
#: at all. The SAME huge-integer document therefore used to digest cleanly
#: on 3.9 and raise `ValueError` out of `json.dumps` on 3.13, uncaught at the
#: point it happened: `decision_binds` answered `PASS` on one interpreter and
#: `FAIL` (once the raise reached `_digest_of`'s `except`) on the other, for
#: identical input.
#:
#: Setting this at Python's own DEFAULT ceiling (4300) only closed that gap
#: for an UNCONFIGURED interpreter. The limit is legally configurable, lower
#: as well as higher, through `sys.set_int_max_str_digits()` or the
#: `PYTHONINTMAXSTRDIGITS` environment variable, and CPython refuses to
#: configure it below 640 (`sys.int_info.str_digits_check_threshold`; asking
#: for anything from 1 through 639 raises `ValueError`) but freely accepts
#: 640 itself, or anything above. A 1001-digit integer under a configured
#: limit of 1000 reproduces the identical divergence this bound exists to
#: close, just relocated to a different digit count: `json.dumps` raises on
#: that CONFIGURED 3.13 process while a 4300-digit bound still waved the
#: document through, and an unconfigured 3.9 floor (which has no mechanism to
#: read `PYTHONINTMAXSTRDIGITS`, or any int-to-string limit, at all) digests
#: it too, so `decision_binds` again answers one verdict on one process and
#: the other verdict on the other, for identical input.
#:
#: Set here instead at 600, BELOW 640, the LOWEST limit any legal
#: configuration can set: every integer this module ever hands to
#: `json.dumps` is therefore at most 600 digits, which is under every
#: configured or default `sys.set_int_max_str_digits` ceiling on every
#: supported interpreter, so no configuration, present or future, can
#: reintroduce this divergence. An ordinary document's integers are nowhere
#: near this size, so nothing ordinary is refused, and the refusal is
#: enforced BEFORE `json.dumps` is ever reached, so it is this module's own
#: verdict on every interpreter AND every configuration alike, not a race
#: against whichever one happens to be running.
MAX_INTEGER_DIGITS = 600


#: `abs(n) >= _INTEGER_BOUND` is true exactly when `n` prints as more than
#: `MAX_INTEGER_DIGITS` decimal digits. An integer comparison is exact where a
#: bit-length estimate over-refused at the boundary (round-6 refuter
#: residual: the digit count exactly AT the bound, and one over it, were
#: both refused by a sentence claiming "more than" the bound), and it never
#: converts anything to a decimal string, so the check cannot itself trip
#: CPython's version- or configuration-dependent int-to-string limit.
_INTEGER_BOUND = 10 ** MAX_INTEGER_DIGITS


def _decimal_digit_count(value):
    """`MAX_INTEGER_DIGITS + 1` when `abs(value)` prints as more than
    `MAX_INTEGER_DIGITS` base-10 digits, else the exact digit count.

    The over-bound arm decides by integer comparison against
    `_INTEGER_BOUND`, never by converting `value` to a decimal string:
    converting an arbitrarily large int is itself the slow, unbounded
    operation `json.dumps` (and CPython's own conversion limit) exists to
    guard against. Only a value already proven inside the bound is ever
    handed to `str()`, and that conversion is at most `MAX_INTEGER_DIGITS`
    digits, legal on every supported interpreter.
    """
    value = abs(value)
    if value == 0:
        return 1
    if value >= _INTEGER_BOUND:
        return MAX_INTEGER_DIGITS + 1  # over the bound; the exact count adds nothing
    return len(str(value))


def _refuse_oversized_integers(value, _path="the document"):
    """Raise `ValueError` on the first int (never a `bool`: `isinstance(True,
    int)` is `True`, and a bool is never a size hazard) anywhere in `value`
    carrying more than `MAX_INTEGER_DIGITS` decimal digits, or return None.

    Walks the same shapes `_refuse_non_string_keys` does, for the same
    reason: a huge int can hide at any depth, not just the top level. See
    `MAX_INTEGER_DIGITS` for why this exists: without it, the SAME document
    digested on one supported interpreter and raised, uncaught, on another.
    The raise here leaves through `ValueError`, which is what `_digest_of`
    already turns into a named problem and a verdict at every authorization
    call site, so no binder exits through this exception either.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        if _decimal_digit_count(value) > MAX_INTEGER_DIGITS:
            raise ValueError(
                "%s carries an integer of more than %d decimal digits; a document this "
                "large digests without complaint on some interpreters and raises out of "
                "json.dumps on others (Python's own int-to-string conversion limit "
                "differs by version), so this module refuses it itself, identically on "
                "every interpreter, before either one gets a chance to disagree"
                % (_path, MAX_INTEGER_DIGITS))
    if isinstance(value, dict):
        for key, item in value.items():
            _refuse_oversized_integers(item, "%s[%r]" % (_path, key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _refuse_oversized_integers(item, "%s[%d]" % (_path, index))


def canonical_digest(document):
    """The SHA256 over `document`, canonically encoded: the digest a decision
    records as `packetSha256`, and the one `decision_binds` recomputes.

    `sort_keys` and a fixed separator set, exactly as `evidence.compute_seal`
    does it, so two machines that hold the same packet compute the same digest
    and a key reordered by an editor does not read as a different packet.

    THE WHOLE DOCUMENT, not a field subset. `compute_seal` digests a named
    subset because a receipt legitimately carries fields written after the run
    it seals; a decision packet is finished when it is handed to a person, so
    every field in it is part of what they were shown, including one added by
    a later schema version. That is also what makes the binding survive schema
    growth: an old decision's digest still matches the old packet it answered,
    because that packet document is not rewritten by anything.

    TAMPER EVIDENCE, NOT A SIGNATURE, the same words `evidence._check_seal`
    uses: this is a keyless digest anyone who has read this module can
    recompute, so it catches a packet edited after the fact and stops nobody
    who chose to re-mint both halves.
    """
    _refuse_non_string_keys(document)
    _refuse_oversized_integers(document)
    blob = json.dumps(document, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _refuse_non_string_keys(value, _path="the document"):
    """Raise `TypeError` on the first mapping key that is not a string, or
    return None.

    JSON has string keys only, so `json.dumps` COERCES every other key to its
    string form: `{1: "x"}` and `{"1": "x"}` encode to the same bytes and
    therefore digest the same, as do `{True: "x"}` and `{"true": "x"}`. Two
    different documents with one digest is the one property this function
    exists to keep, so the coercion is refused rather than performed. The
    refusal leaves through `TypeError`, which is what `_digest_of` already
    turns into a named problem and a verdict at every authorization call
    site, so no binder exits through this exception.

    Not reachable through `json.loads`, which can only build string keys.
    Reachable from any caller that hands this module a dict it built in
    Python, which is what every producer band R and L will write does.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("%s carries the object key %r, a %s and not a string; "
                                "JSON would encode it as its string form, so a second "
                                "document carrying that string would digest the same"
                                % (_path, key, type(key).__name__))
            _refuse_non_string_keys(item, "%s[%r]" % (_path, key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _refuse_non_string_keys(item, "%s[%d]" % (_path, index))


def _digest_of(document, label):
    """`(digest, problem)` for `document`: the digest and None, or None and
    one named problem string.

    THE ONE PLACE any authorization path digests anything, so the escape
    hatches are closed once rather than per caller. `json.loads` accepts
    `NaN` and `Infinity` by default and `canonical_digest` refuses to encode
    them (`allow_nan=False`, so two machines can never disagree about the
    bytes); a document built in Python can carry a non-string key
    (`_refuse_non_string_keys`) or an integer too large for `json.dumps` to
    convert identically on every interpreter this module supports
    (`_refuse_oversized_integers`, `MAX_INTEGER_DIGITS`); and a deeply
    nested document raises `RecursionError` out of `json.dumps` itself.
    `RecursionError` is a `RuntimeError` subclass, so it slips past an
    `except (TypeError,
    ValueError)` and an UNCAUGHT one would leave an authorization path
    exiting through an exception instead of a verdict, where a caller that
    wraps this call in its own `try` could read the crash as "unknown, carry
    on". Named as a problem instead: no digest exists, so nothing binds.

    THE PROBLEM TEXT NAMES THE EXCEPTION'S CLASS, NEVER ITS OWN MESSAGE. The
    class name (`type(exc).__name__`) is the same word on every interpreter
    this module supports; the message is not: `json.dumps(..., allow_nan=
    False)`'s `ValueError` over a `NaN` reads `"Out of range float values
    are not JSON compliant: nan"` on 3.13 and drops the trailing `": nan"`
    on the 3.9 floor, so two machines fed the identical hostile document
    used to print two different evidence lines over the same finding. One
    stable sentence of this module's own words, naming only the class,
    closes that the same way the rest of this file already refuses to let
    an interpreter's own wording become part of a verdict.
    """
    try:
        return canonical_digest(document), None
    except (TypeError, ValueError, RecursionError) as exc:
        return None, ("this %s cannot be canonically encoded, so no digest of it exists "
                      "to compare against the one recorded here (a %s while encoding it); "
                      "nothing binds" % (label, type(exc).__name__))


#: A timestamp this module will compare has to carry a TIME, not just a
#: date. Anchored at the front so an offset, fractional seconds or a trailing
#: `Z` are all still allowed after it; the offset itself is checked on the
#: parsed value, not here.
_HAS_TIME_COMPONENT = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")

#: A fractional-seconds group after the decimal point, so it can be padded or
#: truncated to exactly six digits before either interpreter ever sees it.
#: `datetime.fromisoformat` accepts EXACTLY three or six fractional digits on
#: the Python 3.9 floor and refuses every other count; on 3.13 it accepts one
#: through nine, right-padded to nine and then read as microseconds (so a
#: one-digit fraction reads as tenths, and a seven-to-nine-digit,
#: nanosecond-precision fraction such as a Go producer's default RFC3339
#: output is silently truncated to six). The SAME two documents therefore
#: used to read as two different verdicts depending on which interpreter
#: ran: 3.9 refused the timestamp outright (not an instant, `UNDETERMINED`
#: or a named refusal) while 3.13 parsed it and moved on to compare it,
#: sometimes landing on `LATE_DECLARATION`.
_FRACTIONAL_SECONDS = re.compile(r"\.(\d+)")

#: A basic (colon-less) numeric UTC offset at the very end of the string,
#: e.g. `+0900`. RFC3339 allows this form and `datetime.fromisoformat`
#: accepts it on 3.13 but refuses it on the 3.9 floor, which parses only the
#: colon-separated `+09:00` form. Anchored to the end (`$`) so it matches the
#: trailing offset and nothing else in a string that legitimately carries
#: other `-` characters (the date's own hyphens). Exactly four digits after
#: the sign, so it never fires on the six-digit basic offset with seconds
#: (`+090000`, see `_INSTANT_GRAMMAR` below, which refuses that form outright
#: rather than guessing whether the last two digits are minutes or seconds).
_BASIC_OFFSET = re.compile(r"([+-]\d{2})(\d{2})$")

#: RFC 3339 names the comma as legal for the decimal point (`time-secfrac`),
#: and 3.13's `fromisoformat` reads one; the 3.9 floor only reads a dot.
#: Normalized to a dot, once, before `_FRACTIONAL_SECONDS` ever runs, so the
#: rest of this pipeline never has to know two separators exist.
_COMMA_DECIMAL = re.compile(r",(\d+)")

#: An hour-only numeric offset (`+09`, `-05`) at the very end of the string.
#: RFC 3339's `time-numoffset` does not require minutes and 3.13's parser
#: reads this form; the 3.9 floor refuses it outright. Anchored to the end
#: and to exactly two digits after the sign so it never matches the date's
#: own hyphens or an offset that already carries minutes.
_HOUR_ONLY_OFFSET = re.compile(r"([+-]\d{2})$")

#: A trailing `Z` this module will read as `+00:00`, ONLY when the character
#: right before it is a digit. That lookbehind is the whole fix for `ZZ` and
#: `" Z"`: `text.endswith("Z")` used to fire on both (the string legitimately
#: ends in the letter `Z` either way), turning `...00ZZ` into `...00Z+00:00`
#: and `...00 Z` into `...00 +00:00`, two strings neither this regex nor
#: `datetime.fromisoformat` was ever asked to define, and the 3.9 floor and
#: 3.13 guessed differently on what to do with the leftover (3.9 read
#: `...00ZZ` as a valid instant; 3.13 refused it). Requiring a digit right
#: before the `Z` means a stray second `Z` or a space ahead of it leaves the
#: text untouched, so `_INSTANT_GRAMMAR` below refuses the whole string
#: identically on both interpreters instead of either one guessing.
_TRAILING_Z = re.compile(r"(?<=[0-9])Z$")

#: THE closed grammar. Every lightweight substitution above teaches the 3.9
#: floor one of 3.13's own lenient readings; this is the gate that replaces
#: asking either interpreter's `fromisoformat` what ELSE it is willing to
#: guess. A string that does not match this, after every substitution above
#: has run, returns None before `fromisoformat` is ever called, on both
#: interpreters, for the same reason: nothing here is deferred to whichever
#: parser happens to be more forgiving. `HH:MM:SS` and a `HH:MM` offset
#: (colon, four digits, no seconds) are the only shapes accepted past this
#: point; a colon offset carrying its own seconds (`+09:00:00`) is refused
#: here rather than guessed at, the same choice `_BASIC_OFFSET` already made
#: for its six-digit, colon-less twin (`+090000`): a nonzero offset second is
#: real information, and truncating it silently would make the same instant
#: compare differently than what the producer actually wrote, which is worse
#: than refusing it identically everywhere. THE OFFSET MINUTES ARE `[0-5]
#: [0-9]`, NOT `\d{2}`: an offset minute is a clock minute, 00 through 59,
#: and `\d{2}` used to admit 60 through 99 as well (`+09:75`), a grammar
#: that over-admits with no verdict consequence today only because both
#: interpreters this module supports happen to read the excess the same
#: way; tightened here so the grammar itself states the real bound rather
#: than relying on that coincidence to hold.
_INSTANT_GRAMMAR = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d{6})?[+-]\d{2}:[0-5][0-9]$")


def _normalize_for_floor_parity(text):
    """`text` with its decimal separator, fractional seconds, and numeric
    offset all folded to the one shape `_INSTANT_GRAMMAR` accepts, so
    `datetime.fromisoformat` parses it identically on the Python 3.9 floor
    and on 3.13 rather than refusing on one and succeeding on the other (see
    `_COMMA_DECIMAL`, `_FRACTIONAL_SECONDS`, `_HOUR_ONLY_OFFSET` and
    `_BASIC_OFFSET`). Every substitution mirrors what 3.13's OWN more
    lenient parser already does to the same text (read a comma as a dot, pad
    a short fraction with trailing zeros, truncate a long one to six digits,
    read an hour-only offset as `:00` minutes), so this never changes what
    3.13 itself would have read; it only teaches the floor interpreter to
    read the same thing. What it does NOT do is guess: a shape neither
    substitution recognizes is left as it stands and refused next, by
    `_INSTANT_GRAMMAR`, identically on both interpreters.
    """
    text = _COMMA_DECIMAL.sub(lambda m: "." + m.group(1), text, count=1)
    text = _FRACTIONAL_SECONDS.sub(
        lambda m: "." + (m.group(1) + "000000")[:6], text, count=1)
    match = _BASIC_OFFSET.search(text)
    if match:
        text = text[:match.start()] + match.group(1) + ":" + match.group(2)
    else:
        match = _HOUR_ONLY_OFFSET.search(text)
        if match:
            text = text[:match.start()] + match.group(1) + ":00"
    return text


def _instant(value):
    """The UTC datetime `value` records, or None when it records no instant
    this module can compare.

    AN INSTANT NEEDS THREE THINGS and this function refuses anything short of
    all three: an ISO-8601 date, a TIME on that date, and an EXPLICIT UTC
    offset. None is not a guess and not a failure; `declaration_timing` turns
    it into `UNDETERMINED` and the two validators that read a timestamp turn
    it into a named refusal.

    WHY THE LAST TWO ARE REQUIRED, since an earlier version of this function
    supplied both by assumption. `2026-08-19` parses to that date at MIDNIGHT,
    so a contract declared late on release day reads as declared before a
    release that happened at 11:00, and the lateness this module exists to
    name is erased by the parser. `2026-08-19T10:45:00` with no offset was
    read as UTC on the argument that every producer in this repository writes
    the trailing `Z`: true of the five surfaces above, and worth nothing here,
    because NO producer of these six objects exists yet and the first one
    written against a local clock would hand this module a timestamp whose
    real instant is hours from the one it is read as (`10:45` at `-05:00` is
    `15:45Z`, after the release, and reads as before it). A timestamp missing
    either is not an instant, and this module says so instead of inventing
    the missing half.

    ONE GRAMMAR, NOT TWO PARSERS. Everything from `text.strip()` down to
    `_INSTANT_GRAMMAR.match` runs in pure Python, the same on the 3.9 floor
    and on 3.13, so by the time `datetime.fromisoformat` is ever called this
    function has already decided, itself, whether `text` is one of the forms
    it accepts. `fromisoformat` is then asked to parse a shape both
    interpreters already read identically, rather than being handed the raw
    producer text and trusted to agree with itself across two Python
    versions, which is what let 73 of a 192-form ISO-8601 corpus disagree
    before this grammar existed (see `tools/test_sbe_contracts.py`'s own
    interpreter-agreement sweep).
    """
    text = answered(value)
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not _HAS_TIME_COMPONENT.match(text):
        return None
    text = _TRAILING_Z.sub("+00:00", text, count=1)
    text = _normalize_for_floor_parity(text)
    if not _INSTANT_GRAMMAR.match(text):
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:  # sbe: allow-silent None IS this function's refusal verdict: every caller treats None as "not a usable instant" and refuses onward, so nothing is dropped and no error is hidden
        return None
    if parsed.tzinfo is None:
        return None
    try:
        # A min-year date with a positive offset (or a max-year date with a
        # negative one) shifts past what `datetime` can represent once
        # normalized to UTC: `0001-01-01T00:00:00+09:00` is nine hours
        # before year 1, and `astimezone` raises `OverflowError` rather than
        # return that. Go's zero time, rendered in any location east of UTC,
        # is exactly this timestamp, so this is not a synthetic edge case.
        # `OSError` sits beside it because some platform C libraries raise it
        # instead for the same out-of-range case; either way this is a
        # timestamp that records no instant this module can compare, the
        # same finding an unparseable string already reads as.
        return parsed.astimezone(timezone.utc)
    except (OverflowError, OSError):
        return None


def _identity_type_problem(field, value):
    """None when `value` is a string this module can safely compare as an
    identity, or a named problem naming `field` and the type it actually
    got.

    Called only on a value `answered()` already reads as recording
    something; a hollow value (missing, `None`, `""`) is the OTHER
    function's problem (`_unanswered_fields`, `_hollow_problems`'s own
    empty branch). Every identity value this spine binds on is compared for
    EQUALITY somewhere (`decision_binds`, `release_binds`, `reality_binds`,
    or a direct `data.get(field) != ...` in a validator), and Python's own
    cross-type equality (`0 == False == 0.0`, `1 == True`) means a numeric
    or boolean identity would bind to an unrelated document's identity by
    nothing more than that coercion, not by naming the same thing twice.
    """
    if isinstance(value, str):
        return None
    return ("%s is %s %r, not a string; an identity value has to be a string here, or "
            "Python's own cross-type equality (0 == False == 0.0, 1 == True) could "
            "bind it to an unrelated document's identity" % (field, type(value).__name__,
                                                              value))


def _wrong_type_identity_problems(data, fields):
    """One named problem per name in `fields` that IS present in `data`, IS
    answered by `answered()`'s own bar, and is not a string. See
    `_identity_type_problem`. Used by `_lifecycle_problems` on each surface's
    own identity fields; `_hollow_problems` below runs the same check for the
    binders, over the same `IDENTITY_VALUE_FIELDS` names in every call site
    that reaches it, so the two paths refuse identically rather than each
    growing its own idea of what "the wrong type" means."""
    problems = []
    for f in sorted(fields):
        if f not in data or answered(data.get(f)) is None:
            continue
        problem = _identity_type_problem(f, data.get(f))
        if problem:
            problems.append(problem)
    return problems


def _hollow_problems(label, document, fields):
    """Named problem(s) when `document` does not carry a USABLE identity on
    one or more of `fields`, or an empty list. Two ways to fail here, and
    both are the same underlying finding: `document` binds nothing on that
    field.

    HOLLOW: `document` ANSWERS none of `fields`. Missing and present-but-
    empty are the same finding (the `.get` returns None either way).

    WHY THE BINDERS NEED THIS AND THE VALIDATORS' OWN CHECK IS NOT ENOUGH. A
    binder compares two documents' raw values, and `None == None` is True, as
    is `"" == ""`. Two documents that each identify nothing therefore agreed
    with each other perfectly and were reported as binding, with an evidence
    line naming the very Nones it was calling a match. The validators hold the
    same fields to `answered()` on each document's own face, but a binder that
    trusts a caller to have run them is a check that is off by default, which
    is the same argument `_not_human_problems` makes about the producer-class
    rule one function below.

    WRONG TYPE: `document` answers a field with something that is not a
    string (see `_identity_type_problem`). Every field this function is ever
    called with is one of `IDENTITY_VALUE_FIELDS`, so the same "non-hollow
    string, not merely answered" bar the validators hold identity values to
    applies here too: without it, `decision.get("changeId") != packet.get(
    "changeId")` reads `False != 0` as `False`, and a decision identified by
    `False` binds a packet identified by `0` by nothing more than Python's
    own numeric tower.
    """
    empty = [f for f in fields if answered(document.get(f)) is None]
    problems = []
    if empty:
        problems.append("this %s records nothing on %s, so there is nothing here to "
                        "bind; two documents that each identify nothing are not a "
                        "match" % (label, ", ".join(empty)))
    for problem in _wrong_type_identity_problems(document, [f for f in fields
                                                             if f not in empty]):
        problems.append("this %s's %s" % (label, problem))
    return problems


def _not_human_problems(decision):
    """The named problem(s) with WHO authored this decision, or an empty list.

    One function, called from ALL THREE readers of a human decision:
    `validate_human_decision` (where the document is judged on its own face),
    `decision_binds` (where it is judged against the packet it answers) and
    `release_binds` (where it is judged against what actually shipped),
    because a rule enforced in some of those places and not the rest is a
    rule with a route around it. `release_binds` was that route: it compared
    `changeId` and `releasedCommit` only, so a decision this function refuses
    twice over still bound a release, and the shortest path to a forged
    approval was to skip the two readers that check and call the one that
    did not.
    """
    producer_class = decision.get("producerClass")
    if producer_class == PRODUCER_CLASS_HUMAN:
        return []
    if producer_class in NON_HUMAN_PRODUCER_CLASSES:
        return ["producerClass %r cannot author a human decision; this node is the one "
                "place in the chain a machine never signs, so a %s-authored decision is "
                "refused rather than recorded with a caveat"
                % (producer_class, producer_class)]
    return ["producerClass %r does not say a human authored this decision; unstated is "
            "refused here, never read as probably fine" % (producer_class,)]


def validate_passport(data):
    """The change passport a producer hands across the seam (`NORTH-STAR.md`'s
    own node: "everything above it produces one; everything below it consumes
    one"). Checks the identity spine, `headCommit`, and all five fields
    `tools/sbe_passport.py` names, with field 4 held to a stricter bar than
    presence: `whatWasNotEstablished` is "mandatory, never empty" in that
    tool's own docstring, and a passport that records nothing there is
    claiming everything was established, which is the one claim this whole
    chain exists to stop being made by accident."""
    if data is None:
        return "NO-DATA", "no change passport was given to validate", ()
    shape = _shape_problem(data, "change passport")
    if shape:
        return "FAIL", shape, (shape,)

    problems, version = _lifecycle_problems(data, PASSPORT_BINDING, PASSPORT_FIELDS)
    if "whatWasNotEstablished" in data and answered(data.get("whatWasNotEstablished")) is None:
        problems.append("whatWasNotEstablished records nothing; field 4 of the passport "
                        "is mandatory and never empty, and an empty one reads as "
                        "'everything was established'")
    return _verdict(problems, "schemaVersion %r, change %r, all %d passport field(s) "
                              "carried" % (version, data.get("changeId"),
                                           len(PASSPORT_FIELDS)))


def validate_decision_packet(data):
    """The packet a human is asked to decide on. Checks the identity spine,
    the `headCommit` it was built over, its body fields, and that
    `readinessState` names one of `READINESS_STATES`. The packet carries NO
    digest of itself: the digest is what the decision records about it (see
    `canonical_digest`), and a self-referential field would have to be
    excluded from its own digest, which is one more rule for a forger to
    stand in.

    THE BODY ROUTES THROUGH THE SAME HOLLOW RULE AS THE IDENTITY, on this one
    surface, unlike the presence-only bar `_lifecycle_problems` gives every
    OTHER surface's body: `"question": "   "`, `"question": "TODO"` and
    `"knownRisks": []` used to validate PASS here, present and recording
    nothing, exactly the class `answered()` (`tools/sbe_checks.py`'s one
    hollow rule, named here BY NAME rather than reimplemented) already
    refuses everywhere else in this project. The packet a human is asked to
    decide on is the one surface where that gap actually matters: a blank
    question is not a real question, and an empty `knownRisks` on a document
    whose whole job is naming the risk is the same silent, hollow "yes" this
    module's own rules exist to catch elsewhere in the chain."""
    if data is None:
        return "NO-DATA", "no decision packet was given to validate", ()
    shape = _shape_problem(data, "decision packet")
    if shape:
        return "FAIL", shape, (shape,)

    problems, version = _lifecycle_problems(data, DECISION_PACKET_BINDING,
                                            DECISION_PACKET_FIELDS)
    bad_state = _vocabulary_problem(data, "readinessState", READINESS_STATES,
                                    "readiness states")
    if bad_state:
        problems.append(bad_state)
    hollow_body = _unanswered_fields(data, DECISION_PACKET_FIELDS)
    if hollow_body:
        problems.append("body field(s) present but recording nothing: %s; the ONE "
                        "hollow rule this registry uses everywhere else (`answered()`) "
                        "applies to the packet's own content too, never a looser "
                        "presence-only bar of its own for the document a human is "
                        "asked to decide on" % ", ".join(hollow_body))
    return _verdict(problems, "schemaVersion %r, change %r, readiness %r, every "
                              "required field present"
                              % (version, data.get("changeId"),
                                 data.get("readinessState")))


def validate_human_decision(data):
    """The human decision itself: the node that "is a node, not a courtesy"
    (`NORTH-STAR.md`), sitting between all the proof and the release.

    Checks the identity spine, the two identities it binds (`headCommit` and
    `packetSha256`), `decision` against `DECISION_ANSWERS`, and the one rule
    that makes this object mean anything at all: `producerClass` must be
    `human`. An agent or a pipeline authoring a human decision is refused
    HERE, on the document's own face, with no context needed, because a rule
    that only fires when a caller remembers to pass the right context is a
    rule that is off by default.

    WHAT THIS CANNOT DO, said plainly rather than implied: nothing in a JSON
    document proves a person typed it. `producerClass` is a DECLARATION, and
    this check refuses a document that declares itself machine-authored or
    declines to say. An agent that writes `"producerClass": "human"` into a
    file passes this check and is lying, and no schema catches that; catching
    it needs a signature or a channel a machine does not hold, which is
    `sbe decide`'s problem to own when it exists, not this registry's to
    pretend it solved.
    """
    if data is None:
        return "NO-DATA", "no human decision was given to validate", ()
    shape = _shape_problem(data, "human decision")
    if shape:
        return "FAIL", shape, (shape,)

    problems, version = _lifecycle_problems(data, HUMAN_DECISION_BINDING,
                                            HUMAN_DECISION_FIELDS)
    bad_answer = _vocabulary_problem(data, "decision", DECISION_ANSWERS,
                                     "decision answers")
    if bad_answer:
        problems.append(bad_answer)
    problems.extend(_not_human_problems(data))
    if "createdAt" in data and _instant(data.get("createdAt")) is None:
        problems.append("createdAt %r is not an instant this registry can compare; "
                        "`release_binds` asks whether THIS decision was recorded before "
                        "the release it claims to authorize, and a decision that cannot "
                        "be dated makes that question unanswerable rather than answered "
                        "yes" % (data.get("createdAt"),))
    return _verdict(problems, "schemaVersion %r, change %r, %r, decided by %r"
                              % (version, data.get("changeId"), data.get("decision"),
                                 data.get("producer")))


def validate_release_receipt(data):
    """The record that a release happened. Checks the identity spine and the
    three identities the plan names beside `changeId`: the commit that was
    released, the digest of the artifact that went out, and the run id of the
    thing that shipped it. All three are held to "answered", not merely
    present: a receipt naming an empty artifact digest has recorded a release
    of nothing in particular."""
    if data is None:
        return "NO-DATA", "no release receipt was given to validate", ()
    shape = _shape_problem(data, "release receipt")
    if shape:
        return "FAIL", shape, (shape,)

    problems, version = _lifecycle_problems(data, RELEASE_RECEIPT_BINDING,
                                            RELEASE_RECEIPT_FIELDS)
    if "releasedAt" in data and _instant(data.get("releasedAt")) is None:
        problems.append("releasedAt %r is not an instant this registry can compare; the "
                        "release date is the one every observation contract is judged "
                        "late or early AGAINST, so a receipt that cannot be dated makes "
                        "every declaration about it UNDETERMINED rather than late"
                        % (data.get("releasedAt"),))
    return _verdict(problems, "schemaVersion %r, change %r, released %r, run %r"
                              % (version, data.get("changeId"),
                                 data.get("releasedCommit"), data.get("runId")))


def validate_observation_contract(data):
    """What will be watched in production, and for how long, declared BEFORE
    the release it watches. This validator checks the document alone (spine,
    `headCommit`, `declaredAt`, `observes`, `window`); whether the declaration
    actually preceded the release is a two-document question and lives in
    `declaration_timing`, because a contract is a valid document on its own
    and only its RELATIONSHIP to a receipt can be late."""
    if data is None:
        return "NO-DATA", "no observation contract was given to validate", ()
    shape = _shape_problem(data, "observation contract")
    if shape:
        return "FAIL", shape, (shape,)

    problems, version = _lifecycle_problems(data, OBSERVATION_CONTRACT_BINDING,
                                            OBSERVATION_CONTRACT_FIELDS)
    if "declaredAt" in data and _instant(data.get("declaredAt")) is None:
        problems.append("declaredAt %r is not an instant this registry can compare; a "
                        "predeclaration nothing can date is not a predeclaration"
                        % (data.get("declaredAt"),))
    return _verdict(problems, "schemaVersion %r, change %r, declared %r, window %r"
                              % (version, data.get("changeId"), data.get("declaredAt"),
                                 data.get("window")))


def validate_verified_reality(data):
    """The far end of the chain: what the release actually did in reality.
    Checks the identity spine, the released commit it is about, the digest of
    the observation contract it answers, and `realityState` against
    `REALITY_STATES`.

    The contract digest is a BINDING field, not a courtesy reference: a
    reality state that cites no contract is an after-the-fact story about a
    release, and one that cites a contract minted after the receipt is the
    same story with a date on it (which is what `declaration_timing` is for,
    on the contract this digest names).

    A DEFINITIVE STATE WITH NO EVIDENCE IS INVENTED, NOT VERIFIED. `basis` is
    presence-only like every other body field (see `DECISION_PACKET_FIELDS`'s
    own comment for the one surface that differs), so an empty `basis` under
    `REALITY_NOT_OBSERVED` is honest and stays accepted: nothing was watched,
    so there is nothing to cite. Under `VERIFIED_IN_REALITY` or `CONTRADICTED_
    BY_REALITY` (`DEFINITIVE_REALITY_STATES`) it is a different finding: a
    conclusion recorded with nothing behind it, which used to validate PASS,
    the same happy path a real observation earns, with "evidence absent" and
    "a value nobody produced" reading as one undifferentiated thing. Named
    here, DISTINCTLY from a `basis` that is simply missing (already its own
    `FAIL` via `_missing_fields`, "missing required field(s): basis"), so the
    two never share one sentence.

    THIS RULE, UPDATED: `basis` under a `DEFINITIVE_REALITY_STATE` is now
    NEVER version-scoped away, whatever a `LIFECYCLE_FIELDS_INTRODUCED_IN`
    row claims. It used to be checked only against `"basis" in data`, so a
    row that scoped `basis` out of `required` AND the document simply
    omitted the key (rather than answering it hollow, `""` or `[]`) left
    BOTH the "missing required field" check and this one silent, and a
    `VERIFIED_IN_REALITY` with nothing behind it validated PASS. `basis` is
    the one body field `DEFINITIVE_REALITY_STATES` exists to hold evidence
    for; `NEVER_VERSION_SCOPED` already protects identity and binding fields
    from exactly this kind of table-row bypass on the argument that "a field
    an AUTHORIZATION check reads is required from the first version or it is
    not an authorization check at all" (that tuple's own comment), and a
    definitive reality claim with nothing behind it is the class that
    argument was written for, so the same protection now applies here, by
    checking `answered()` directly rather than presence-in-`data` first."""
    if data is None:
        return "NO-DATA", "no verified-reality state was given to validate", ()
    shape = _shape_problem(data, "verified-reality state")
    if shape:
        return "FAIL", shape, (shape,)

    problems, version = _lifecycle_problems(data, VERIFIED_REALITY_BINDING,
                                            VERIFIED_REALITY_FIELDS)
    bad_state = _vocabulary_problem(data, "realityState", REALITY_STATES,
                                    "reality states")
    if bad_state:
        problems.append(bad_state)
    state = data.get("realityState")
    if state in DEFINITIVE_REALITY_STATES and answered(data.get("basis")) is None:
        already_missing = any(p.startswith("missing required field(s)") and "basis" in p
                              for p in problems)
        if not already_missing:
            problems.append("realityState %r is a definitive conclusion and basis records "
                            "nothing to reach it from; a conclusion no evidence produced is "
                            "invented, not verified, a different finding than evidence "
                            "simply being absent, and never one a version table is allowed "
                            "to scope away" % (state,))
    if "observedAt" in data and _instant(data.get("observedAt")) is None:
        problems.append("observedAt %r is not an instant this registry can compare; a "
                        "reality state that cannot be dated cannot be checked against the "
                        "contract's own declared window" % (data.get("observedAt"),))
    elif state in DEFINITIVE_REALITY_STATES and _instant(data.get("observedAt")) is None:
        # SAME SHAPE AS THE `basis` FIX ABOVE, for the same reason: a
        # version-scoping row that drops `observedAt` out of `required` used
        # to leave a `VERIFIED_IN_REALITY`/`CONTRADICTED_BY_REALITY` claim
        # entirely undated with nothing here to say so, because the `elif`'s
        # sibling branch above only ever fires when the key is PRESENT. A
        # definitive conclusion with no instant to date it is unauthenticated
        # the same way one with no `basis` is invented, and it is never a
        # version table's to scope away either. Read directly off `data`
        # rather than off `required`, exactly as the `basis` check does, so
        # this fires whether the field is simply missing or scoped out.
        already_missing = any(p.startswith("missing required field(s)")
                              and "observedAt" in p for p in problems)
        if not already_missing:
            problems.append("realityState %r is a definitive conclusion and observedAt "
                            "records no instant to date it from; a conclusion that cannot "
                            "be dated cannot be checked against the contract's own "
                            "declared window, and never one a version table is allowed to "
                            "scope away" % (state,))
    return _verdict(problems, "schemaVersion %r, change %r, released %r, reality %r"
                              % (version, data.get("changeId"),
                                 data.get("releasedCommit"), data.get("realityState")))


def decision_binds(decision, packet, head_commit=None, skip_head_check=False):
    """Does this human decision actually answer THIS packet, at THIS head?

    Returns the house 3-tuple. `NO-DATA` only when a document was not given at
    all; every mismatch is `FAIL`, never a `PASS` carrying a warning, because
    a decision that binds the wrong head or the wrong packet is not a weaker
    approval, it is an approval of something else.

    THE HEAD CHECK IS ON BY DEFAULT. `head_commit` is the head the change is
    actually at now, and BOTH documents must name it. A caller with no
    current head to compare has to say so explicitly, `skip_head_check=True`:
    omitting `head_commit` used to read, silently, as "compare the two
    documents only," and a decision that agreed with an old packet but not
    with where the change stood NOW validated PASS with nothing in the
    evidence naming that the freshness question was never asked, despite this
    docstring's own promise that it was "answered as such." Skipping the
    check is still allowed; it is now a choice the caller states, and it is
    named in the evidence either way, never assumed.

    `packet` is refused FIRST against `validate_decision_packet` and
    `decision` against `validate_human_decision`, each its own surface's
    validator: an observation contract or any other lifecycle object happens
    to carry `changeId` and `headCommit` too, and comparing only identity
    fields against a document that was never checked as its own surface let
    either half bind to whatever else was handed here, as long as its
    identity fields happened to line up.
    """
    if decision is None or packet is None:
        return "NO-DATA", "no decision, or no packet, was given to bind", ()
    for label, document in (("human decision", decision), ("decision packet", packet)):
        shape = _shape_problem(document, label)
        if shape:
            return "FAIL", shape, (shape,)

    packet_verdict, packet_evidence, _packet_problems = validate_decision_packet(packet)
    problems = []
    if packet_verdict == "FAIL":
        problems.append("the packet given here does not validate as a decision packet "
                        "on its own face (%s); a decision cannot bind to a document "
                        "that is not the surface it claims to answer" % packet_evidence)
    decision_verdict, decision_evidence, _decision_problems = validate_human_decision(decision)
    if decision_verdict == "FAIL":
        problems.append("the decision given here does not validate as a human decision "
                        "on its own face (%s); a binder cannot bind a document that is "
                        "not the surface it claims to be" % decision_evidence)
    problems.extend(_not_human_problems(decision))
    problems.extend(_hollow_problems("human decision", decision,
                                     ("changeId", "headCommit", "packetSha256")))
    problems.extend(_hollow_problems("decision packet", packet,
                                     ("changeId", "headCommit")))
    if head_commit is None and not skip_head_check:
        problems.append("no head commit was given to check this decision's freshness "
                        "against, and the check was not explicitly skipped; the head "
                        "check is on by default, so a caller with no current head to "
                        "compare has to say so (skip_head_check=True), never omit it "
                        "silently")
    elif head_commit is not None and answered(head_commit) is None:
        problems.append("the head to compare against records nothing (%r); an empty "
                        "string is what a failed `git rev-parse` hands a caller, and a "
                        "decision compared against nothing binds to nothing"
                        % (head_commit,))
    if decision.get("changeId") != packet.get("changeId"):
        problems.append("this decision names change %r and the packet names %r; a "
                        "decision for another change is not this change's decision"
                        % (decision.get("changeId"), packet.get("changeId")))

    decided_head = decision.get("headCommit")
    if decided_head != packet.get("headCommit"):
        problems.append("this decision names head %r and the packet it claims to answer "
                        "was built over %r; the person decided over other code"
                        % (decided_head, packet.get("headCommit")))
    if head_commit is not None and decided_head != head_commit:
        problems.append("this decision names head %r and the change is at %r now; a "
                        "decision does not carry forward onto code it never saw"
                        % (decided_head, head_commit))

    expected, digest_problem = _digest_of(packet, "decision packet")
    if digest_problem:
        problems.append(digest_problem)
    if expected is not None and decision.get("packetSha256") != expected:
        problems.append("this decision records packet digest %r and the packet given "
                        "here digests to %r; the packet changed after it was decided "
                        "on, or this decision answers a different packet"
                        % (decision.get("packetSha256"), expected))

    skip_note = (" (head-freshness check explicitly skipped)"
                if head_commit is None and skip_head_check else "")
    return _verdict(problems, "decision by %r binds change %r, head %r and packet digest "
                              "%r, all three matching%s"
                              % (decision.get("producer"), decision.get("changeId"),
                                 decision.get("headCommit"),
                                 decision.get("packetSha256"), skip_note))


_CANONICAL_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _is_canonical_sha256(value):
    """True only for a string of exactly 64 lowercase hexadecimal digits,
    the one shape `hashlib.sha256(...).hexdigest()` (this module's own
    `canonical_digest`, above) ever produces. Case sensitive on purpose:
    see `_artifact_digest_problem`'s docstring for why uppercase hex is
    refused here rather than folded to lowercase."""
    return isinstance(value, str) and _CANONICAL_SHA256.match(value) is not None


def _artifact_digest_problem(recorded_sha256, expected_sha256):
    """`(problem, verified)` for one artifact digest comparison:
    `recorded_sha256` is a document's own claim about what shipped,
    `expected_sha256` is a caller's own knowledge of what should have
    shipped. `problem` is `None` when there is nothing to refuse;
    `verified` is `True` only on an actual match.

    THE FOUR ARMS, factored out of `release_binds` (where this rule was
    first written inline) so `evidence.verify_release_receipt_for_release`
    can call the SAME rule rather than grow a second copy of it that could
    drift out of step: that function answers a different question (does a
    receipt on disk support a claim about a real release of one commit) and
    is never given a human decision document, so it cannot call `release_
    binds` itself, only share the one comparison both questions need.

      - `expected_sha256 is None`: no expectation was given, nothing to
        check against, `(None, False)`, never a problem;
      - given, but `answered()` reads it as recording nothing (empty
        string, whitespace, a TODO-class token): refused BY NAME, never
        silently read as no expectation at all;
      - given and answered, but either side fails `_is_canonical_sha256`
        (exactly 64 lowercase hexadecimal characters; uppercase hex is
        refused, not folded, because no producer in this codebase ever
        writes anything but lowercase, so accepting it would widen this
        rule past every shape its own hashers can emit): refused, and the
        two sides are told apart by name, never folded into one message
        vague enough to cover both. `recorded_sha256` is checked FIRST: a
        document that cannot state a real digest is a defective DOCUMENT,
        worth naming before anything is said about what the caller
        expected. Only once the document's own claim is a real digest does
        a malformed `expected_sha256` get refused, and that is a defective
        CALLER, never the document's fault. THIS is the arm that closes the
        hole equality-only comparison left open: two equal strings that
        were never digests at all (`"banana" == "banana"`, the exact case
        an adversarial review produced) used to fall straight through to
        the match arm below and report a digest "checked ... and matching"
        when neither side was ever a digest to begin with;
      - both sides canonical, but `recorded_sha256 != expected_sha256`: a
        problem naming BOTH digests, so a reader never has to guess which
        one this registry expected and which one the document actually
        carried;
      - both sides canonical and equal: `(None, True)`.
    """
    if expected_sha256 is None:
        return None, False
    if answered(expected_sha256) is None:
        return ("the expected artifact digest given here records nothing "
                "(%r); an empty expectation checks nothing, so it is refused "
                "rather than silently read as no expectation at all"
                % (expected_sha256,)), False
    if not _is_canonical_sha256(recorded_sha256):
        return ("this receipt records artifact digest %r, which is not a "
                "canonical SHA-256 (64 lowercase hexadecimal characters); a "
                "value the document itself carries in the wrong shape is a "
                "defective document, refused before it is weighed against "
                "the expected digest %r"
                % (recorded_sha256, expected_sha256)), False
    if not _is_canonical_sha256(expected_sha256):
        return ("the expected artifact digest given here (%r) is not a "
                "canonical SHA-256 (64 lowercase hexadecimal characters); a "
                "caller handing this rule something that was never a digest "
                "is a defective caller, not the document, which records %r"
                % (expected_sha256, recorded_sha256)), False
    if recorded_sha256 != expected_sha256:
        return ("this receipt records artifact digest %r and the artifact "
                "expected here digests to %r; a release naming a different "
                "binary than the one this caller expected is not the release "
                "that was decided on, even when the commit and the decision "
                "both match"
                % (recorded_sha256, expected_sha256)), False
    return None, True


def _artifact_note(recorded_sha256, verified):
    """The evidence sentence fragment describing an artifact digest:
    'checked against the expected digest and matching' when `verified`,
    or 'recorded, not verified (no expected digest was given to check it
    against)' otherwise. Shared by `release_binds` and `evidence.verify_
    release_receipt_for_release` so a PASS from either caller reads the
    identical sentence about the identical question, rather than one
    caller's wording quietly drifting from the other's."""
    return (("artifact %r checked against the expected digest and matching"
            % (recorded_sha256,))
           if verified else
           ("artifact %r recorded, not verified (no expected digest was "
            "given to check it against)" % (recorded_sha256,)))


def release_binds(receipt, decision, expected_artifact_sha256=None):
    """Was what was released what was decided on, and was it decided at all?

    Returns the house 3-tuple. SIX things have to hold, and the last four
    were missing while this function was the shortest path in the module to
    a release nobody authorized:

      - the receipt validates as a release receipt, and the decision as a
        human decision, EACH on its own face (`validate_release_receipt`,
        `validate_human_decision`): this function used to check neither
        document against its own surface, so a receipt or a decision with an
        unknown `schemaVersion`, a hollow identity field, or any other
        single-document defect the OTHER validators already catch still
        bound a release here, as long as the two documents agreed with each
        other;
      - the receipt and the decision are about the same change;
      - the released commit is the head the human decided over (a receipt for
        a different commit is `FAIL`: the decision it claims to carry was
        taken over code that is not what shipped, which is the reach-through
        this node exists to close);
      - a HUMAN authored the decision (`_not_human_problems`, the same
        refusal `validate_human_decision` and `decision_binds` apply). This
        function checked neither the producer class nor anything else about
        the decision's own face, so an `agent`-authored decision that BOTH
        other readers refuse used to bind a release here;
      - the decision actually says `RELEASE`. A `HOLD`, an invented answer or
        no `decision` key at all used to bind a release just as happily,
        because nothing here read the field: the two identities matched, and
        matching identities on a decision that says no is a receipt for a
        release the person refused;
      - the receipt's `decisionSha256` recomputes against THIS decision: a
        digest field nothing recomputes is a citation, not a binding, the
        same rule `packetSha256` and `observationContractSha256` already
        hold the other two binders to. `decisionSha256` is checked HERE,
        inside this binder, rather than added to `RELEASE_RECEIPT_BINDING`
        and `validate_release_receipt`'s own required set: those are a
        cross-module contract other producers and consumers already read
        (band L's own release-receipt fixtures, for one), and a single
        document validating on its OWN face does not yet have a decision to
        digest against; the binding this field closes is exactly the
        two-document question this function answers, so it lives where the
        second document actually arrives;
      - the decision was not recorded AFTER the release it claims to
        authorize: a decision timestamped later than `releasedAt` is a
        rubber stamp on an already-shipped change, not the authorization
        this chain requires before shipping, and it used to read identically
        to an honest, timely `RELEASE`. AND, closing the route around that
        rule rather than through it: that question has to be ASKABLE at
        all. `decision.get("createdAt")` or `receipt.get("releasedAt")`
        parsing to no instant used to leave `decided_at > released_at`
        unevaluated with nothing said about it, so a decision timestamped
        `"whenever, mate"` bound a release exactly as an honest, timely one
        would. Named now instead, the same way `decision_binds` names its
        own skipped head-freshness check rather than silently omitting it.

    WHAT IT STILL DOES NOT DO. It does not re-check the decision against the
    packet: that is `decision_binds`, it needs the packet document, and a
    caller releasing anything runs BOTH. This function is not a substitute
    for that call and does not pretend the packet was seen.

    It also does not, by itself, recompute `artifactSha256`: this binder
    never sees the released bytes, only the receipt's claim about them, so
    there is nothing here to hash and compare. A caller that HAS the digest
    the release was supposed to carry passes it as `expected_artifact_sha256`,
    and a mismatch is `FAIL`, naming both digests, the same way a wrong
    `releasedCommit` already is. A caller with no expected digest to check
    against gets a `PASS` whose evidence says the artifact digest was
    RECORDED, not verified, rather than a sentence that reads as though this
    function checked bytes it never received.
    """
    if receipt is None or decision is None:
        return "NO-DATA", "no release receipt, or no decision, was given to bind", ()
    for label, document in (("release receipt", receipt), ("human decision", decision)):
        shape = _shape_problem(document, label)
        if shape:
            return "FAIL", shape, (shape,)

    problems = _not_human_problems(decision)
    receipt_verdict, receipt_evidence, _receipt_problems = validate_release_receipt(receipt)
    if receipt_verdict == "FAIL":
        problems.append("the receipt given here does not validate as a release receipt "
                        "on its own face (%s); a binder cannot bind a document that is "
                        "not the surface it claims to be" % receipt_evidence)
    decision_verdict, decision_evidence, _decision_problems = validate_human_decision(decision)
    if decision_verdict == "FAIL":
        problems.append("the decision given here does not validate as a human decision "
                        "on its own face (%s); a binder cannot bind a document that is "
                        "not the surface it claims to be" % decision_evidence)
    answer = decision.get("decision")
    if answer != DECISION_AUTHORIZING_RELEASE:
        problems.append("this decision records %r and only %r authorizes a release; a "
                        "receipt bound to anything else is a release the person did not "
                        "say yes to" % (answer, DECISION_AUTHORIZING_RELEASE))
    problems.extend(_hollow_problems("release receipt", receipt,
                                     ("changeId", "releasedCommit", "artifactSha256",
                                      "runId", "decisionSha256")))
    problems.extend(_hollow_problems("human decision", decision,
                                     ("changeId", "headCommit")))
    if receipt.get("changeId") != decision.get("changeId"):
        problems.append("this receipt names change %r and the decision names %r"
                        % (receipt.get("changeId"), decision.get("changeId")))
    if receipt.get("releasedCommit") != decision.get("headCommit"):
        problems.append("this receipt released %r and the decision was taken over %r; "
                        "what shipped is not what was decided on"
                        % (receipt.get("releasedCommit"), decision.get("headCommit")))

    artifact_problem, artifact_verified = _artifact_digest_problem(
        receipt.get("artifactSha256"), expected_artifact_sha256)
    if artifact_problem:
        problems.append(artifact_problem)

    expected_decision_digest, decision_digest_problem = _digest_of(decision, "human decision")
    if decision_digest_problem:
        problems.append(decision_digest_problem)
    if (expected_decision_digest is not None
            and receipt.get("decisionSha256") != expected_decision_digest):
        problems.append("this receipt records decision digest %r and the decision given "
                        "here digests to %r; the decision changed after the release was "
                        "minted, or this receipt authorizes a different decision"
                        % (receipt.get("decisionSha256"), expected_decision_digest))

    decided_at = _instant(decision.get("createdAt"))
    released_at = _instant(receipt.get("releasedAt"))
    if decided_at is not None and released_at is not None:
        if decided_at > released_at:
            problems.append("this decision was recorded at %r, after the release at %r; a "
                            "decision timestamped after what it claims to authorize is a "
                            "rubber stamp on an already-shipped change, not an "
                            "authorization of it" % (decision.get("createdAt"),
                                                     receipt.get("releasedAt")))
    else:
        # THE TIMING QUESTION IS UNASKABLE, NOT SILENTLY SKIPPED. Before this,
        # a `createdAt` or `releasedAt` neither instant could parse simply
        # left `decided_at` or `released_at` as `None`, and the `and`
        # above short-circuited the whole comparison without leaving a
        # trace: a decision timestamped `"whenever, mate"` bound a release
        # exactly as an honest, timely one would, with the evidence line
        # saying nothing about the question that was never actually asked.
        # Named here by the same rule `decision_binds` already applies to
        # its own head-freshness check ("no head commit was given ... and
        # the check was not explicitly skipped"): a question this module
        # cannot answer is refused, never read as probably fine.
        unaskable = []
        if decided_at is None:
            unaskable.append("this decision's createdAt %r" % (decision.get("createdAt"),))
        if released_at is None:
            unaskable.append("this receipt's releasedAt %r" % (receipt.get("releasedAt"),))
        problems.append("whether this decision was recorded before the release it claims "
                        "to authorize cannot be asked: %s is not an instant this registry "
                        "can compare; an unanswerable timing question is refused rather "
                        "than read as a silent PASS" % " and ".join(unaskable))

    artifact_note = _artifact_note(receipt.get("artifactSha256"), artifact_verified)
    return _verdict(problems, "release of %r for change %r matches the decided head, "
                              "%s, run %r"
                              % (receipt.get("releasedCommit"), receipt.get("changeId"),
                                 artifact_note, receipt.get("runId")))


def reality_binds(reality, contract):
    """Does this verified-reality state actually answer THIS observation
    contract?

    Returns the house 3-tuple, and it is `decision_binds` for the far end of
    the chain: same shape, same refusals, one recomputed digest.
    `observationContractSha256` is declared a BINDING field by
    `VERIFIED_REALITY_BINDING` and by `validate_verified_reality`'s own
    docstring ("a BINDING field, not a courtesy reference"), and until this
    function existed NOTHING in this module ever recomputed it: a reality
    state naming a digest of the right length, or the digest of a different
    contract, or sixty-four characters of nonsense, validated. A binding
    nothing recomputes is a citation, and a citation is what an after-the-fact
    story about a release carries.

    The released commit must be the head the contract was declared over, for
    the reason `release_binds` gives about its own pair: a reality state about
    one commit citing a contract written over another is watching other code.

    WHAT THIS DOES NOT ANSWER: whether the contract was declared before the
    release. That is `declaration_timing`, it needs the RECEIPT rather than
    the reality state, and a caller closing the loop runs both.

    `contract` is refused FIRST against `validate_observation_contract`, and
    `reality` against `validate_verified_reality`, each its own surface's
    validator, for the same reason `decision_binds` checks `packet` against
    `validate_decision_packet`: a decision packet or any other lifecycle
    object happens to carry `changeId` and `headCommit` too, and comparing
    only identity fields against a document that was never checked as its
    own surface let either half bind to whatever else was handed here. This
    is also how the absence-vs-invention rule on `basis`
    (`DEFINITIVE_REALITY_STATES`, `validate_verified_reality`'s own
    docstring) reaches this binder: a reality state that declares
    `VERIFIED_IN_REALITY` with nothing in `basis` used to bind here exactly
    as a real observation would, the two readings sharing one PASS.
    """
    if reality is None or contract is None:
        return "NO-DATA", ("no verified-reality state, or no observation contract, was "
                           "given to bind"), ()
    for label, document in (("verified-reality state", reality),
                            ("observation contract", contract)):
        shape = _shape_problem(document, label)
        if shape:
            return "FAIL", shape, (shape,)

    contract_verdict, contract_evidence, _contract_problems = (
        validate_observation_contract(contract))
    problems = []
    if contract_verdict == "FAIL":
        problems.append("the contract given here does not validate as an observation "
                        "contract on its own face (%s); a reality state cannot bind to "
                        "a document that is not the surface it claims to answer"
                        % contract_evidence)
    reality_verdict, reality_evidence, _reality_problems = validate_verified_reality(reality)
    if reality_verdict == "FAIL":
        problems.append("the reality state given here does not validate as a "
                        "verified-reality state on its own face (%s); a binder cannot "
                        "bind a document that is not the surface it claims to be"
                        % reality_evidence)
    bad_state = _vocabulary_problem(reality, "realityState", REALITY_STATES,
                                    "reality states")
    if bad_state:
        problems.append(bad_state)
    problems.extend(_hollow_problems("verified-reality state", reality,
                                     ("changeId", "releasedCommit",
                                      "observationContractSha256")))
    problems.extend(_hollow_problems("observation contract", contract,
                                     ("changeId", "headCommit")))
    if reality.get("changeId") != contract.get("changeId"):
        problems.append("this reality state names change %r and the contract names %r; "
                        "an observation of another change is not this change's reality"
                        % (reality.get("changeId"), contract.get("changeId")))
    if reality.get("releasedCommit") != contract.get("headCommit"):
        problems.append("this reality state is about released commit %r and the contract "
                        "it cites was declared over %r; what was watched is not what "
                        "shipped" % (reality.get("releasedCommit"),
                                     contract.get("headCommit")))

    expected, digest_problem = _digest_of(contract, "observation contract")
    if digest_problem:
        problems.append(digest_problem)
    if expected is not None and reality.get("observationContractSha256") != expected:
        problems.append("this reality state cites contract digest %r and the contract "
                        "given here digests to %r; the contract changed after the fact, "
                        "or this state answers a different contract"
                        % (reality.get("observationContractSha256"), expected))

    return _verdict(problems, "reality %r for change %r binds released commit %r and "
                              "contract digest %r"
                              % (reality.get("realityState"), reality.get("changeId"),
                                 reality.get("releasedCommit"),
                                 reality.get("observationContractSha256")))


def declaration_timing(contract, receipt):
    """One of `DECLARATION_STATES` for this observation contract against this
    release receipt.

    `LATE_DECLARATION` when the contract was declared strictly after the
    release it claims to watch: it is then a description of what already
    happened and this module says so by name rather than accepting it
    silently. A contract declared at the same instant is `PREDECLARED`, not
    late: "after" means after. `UNDETERMINED` whenever either side carries no
    instant this module can compare, including a document that is not a
    document at all: nothing was compared, so nothing is claimed, and a caller
    that turns this into a verdict has `NO-DATA` for exactly this case.

    Deliberately NOT a verdict function: `PREDECLARED` is not `PASS`. A
    predeclared contract can still be watching the wrong change, which is the
    caller's own comparison to make on `changeId`.
    """
    if not isinstance(contract, dict) or not isinstance(receipt, dict):
        return "UNDETERMINED"
    declared = _instant(contract.get("declaredAt"))
    released = _instant(receipt.get("releasedAt"))
    if declared is None or released is None:
        return "UNDETERMINED"
    if declared > released:
        return "LATE_DECLARATION"
    return "PREDECLARED"


#: One registry, so a caller can look up a surface by name instead of
#: importing five function names by hand. Not the only way to call this
#: module (every `validate_*` function above is public on its own), but the
#: shape LP-0201 asked for: "a versioned schema registry", not five loose
#: functions with no list naming them all.
VALIDATORS = {
    "task-registry": validate_task_registry,
    "status": validate_status,
    "status-team": validate_status_team,
    "work-brief": validate_work_brief,
    "handover": validate_handover,
    "passport": validate_passport,
    "decision-packet": validate_decision_packet,
    "human-decision": validate_human_decision,
    "release-receipt": validate_release_receipt,
    "observation-contract": validate_observation_contract,
    "verified-reality": validate_verified_reality,
}

#: Every surface name this registry knows, for a caller that wants to list
#: them (a help string, a test that walks all five) without importing
#: `VALIDATORS` and reading its keys.
SURFACES = tuple(sorted(VALIDATORS))


def validate(surface, data):
    """Dispatch to the `validate_*` function named by `surface`, one of
    `SURFACES`. An unknown `surface` is a caller error, not a document
    finding: it is refused with `ValueError`, naming the surfaces this
    registry actually knows, the same way `sbe_checks.Check.__init__`
    refuses an unknown `kind` at construction time rather than at the first
    call. Returns the house 3-tuple `(verdict, evidence, problems)`, exactly
    what the surface's own `validate_*` function returns."""
    fn = VALIDATORS.get(surface)
    if fn is None:
        raise ValueError("unknown surface %r; this registry knows %s"
                         % (surface, ", ".join(SURFACES)))
    return fn(data)
