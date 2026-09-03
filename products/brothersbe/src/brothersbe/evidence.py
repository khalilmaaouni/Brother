"""Generate a receipt by RUNNING the command, and refuse to read one that was
merely written.

The defect this exists for, in one sentence: every receipt this project reads
today can be typed by hand by the same agent whose work it verifies, so a
fabricated duration, exit code, row count or rerun id satisfies the schema and a
gate PASSes on a run nobody's command ever performed.

Two bindings close it, and both are stated with their limits because a control
that oversells itself is worse than none:

1. THE COMMAND IS RUN HERE. `sbe evidence run -- <command...>` executes the
   command through `subprocess`, times it, and records what it observed. There
   is no argument that writes a duration or an exit code; those come from the
   run or the receipt does not exist. This is the binding that matters, and it
   is why this module is a wrapper rather than a validator.
2. THE RECEIPT IS BOUND TO A COMMIT AND TO FILE CONTENT. `headCommit` must be
   the head at verify time, and every file the receipt claims to cover must
   still hold the bytes whose digest the receipt recorded. Evidence made against
   code that has since changed is evidence about a different program.

WHAT THE SEAL IS, AND WHAT IT IS NOT. `runId` is a SHA256 over the receipt's own
run facts, recomputed at verify time. It is TAMPER EVIDENCE against hand
authoring: a plausible receipt typed to satisfy the schema does not carry a
matching seal and is refused by name. It is NOT a signature. Anybody who can
read this file can compute a matching seal, because the input is the receipt
itself and the key is nothing. So a locally generated receipt is never more than
LOCAL-ADVISORY, and the only receipt this project will call PROTECTED-CI is one
minted where `SBE_CI_RUN_ID` was set by an environment the agent does not
control. `show` prints that trust level on every receipt, every time, so the
distinction is never something a reader has to remember.

DIGESTS, NEVER RAW OUTPUT. `stdout` and `stderr` are recorded as SHA256 digests
and byte counts only. A receipt is a file that gets committed, pasted into a
pull request and shipped to whoever asks for evidence, and a command that prints
a token, a connection string or a customer row would otherwise persist it
forever in the one artifact everybody is encouraged to share. The digest proves
the same bytes came back; it carries none of them.

WHICH CHECK RAN IS DECLARED, NEVER INFERRED FROM THE COMMAND LINE. `--kind`
records, as a first-class sealed field, which of this project's obligations the
run is offered against (`design`, `gate`, `score`). It exists because the
consumer of these receipts used to work the identity out for itself, by
substring-matching the recorded `argv`: a receipt for `/bin/cat
tests/test_design_of_gate_score.txt` named all three words, so one command that
ran no check at all cleared three obligations at once. Nothing about that was
hypothetical; it was reproduced. The field moves the claim from a guess a reader
makes about a string to a statement the receipt carries.

What the field IS: a declaration made at generation time, bound to a run that
actually happened, sealed with everything else, so it cannot be added to a
receipt afterwards and cannot be given to a command that never ran. What it is
NOT: proof of what the command did. This wrapper starts a process and times it;
it does not understand the process. An operator who types `--kind design` over a
command that checks nothing has written a false declaration, and the receipt
records the argv right beside it so a reader can see the two disagree. A receipt
that declares no kind clears no obligation, and one written before this field
existed is legacy: NO-DATA for obligation purposes, never a silent pass.

AND THE DECLARATION IS NO LONGER THE IDENTITY. `--kind` above closed the
inference bypass and left the one under it standing: a declaration is a word the
caller types, so `-- true --kind gate` still minted a clean, sealed,
commit-bound receipt for a command that checked nothing. `sbe evidence run
--check <id>` resolves the executable, the exact argument vector, the working
directory, the covered paths and the environment out of `.sbe/checks.yml`, a
registry inside the protected control plane, and accepts NO substitution: a
command after `--`, a `--covers` or a `--kind` beside it is refused rather than
quietly overridden. The receipt records `checkId`, `checkKind`,
`checkSpecSha256`, the executable and its hash, every runner file hash, the argv
and the working directory, all sealed, and `verify` recomputes every one of them
against the registry as it stands NOW, so a redefined check or a modified runner
invalidates the receipts minted before it. The free-form mode stays for local
experimentation and it is always LOCAL-ADVISORY, whatever the tree state and
whatever CI run id is set, because nothing outside the caller says what it was.
What this still cannot prove is the registry itself: an entry pointing an
important-sounding id at a command that checks nothing is a false registry, and
what defends against that is `.sbe/checks.yml` being in the control-plane rule
of `.sbe/policy.yml`, not anything computed here.

ARGV IS DIFFERENT, AND ONLY PARTLY FIXED. Unlike stdout and stderr, `argv` has
to be recorded as text: a receipt whose command was paraphrased proves nothing
about what happened. So a credential typed ON the command line used to be
persisted into the receipt verbatim, and the receipt is the one artifact
everybody is encouraged to share. `redact_argv()` now masks any token matching
a KNOWN secret shape before `argv` is written, reusing (not reinventing) the
same `SECRET_PATTERNS` `tools/sbe_telemetry.py` already scans an operator's own
messages with. Each match becomes a named marker, `[REDACTED:<shape>]`, and the
receipt records `argvRedactions`, the count, so a reader knows whether `argv` is
still verbatim (0) or not. This narrows the limit; it does not close it. The
pattern list is finite by nature, so a secret in a shape the list does not know
still reaches the receipt untouched. That residual is stated, not hidden, in
`docs/KNOWN-LIMITS.md`.

Python floor is 3.9: no match statements, no `X | Y` annotations. Standard
library only, because zero dependencies is a promise on this project's front
page and an evidence generator is the last place to retract it.
"""
import argparse
import hashlib
import io
import json
import os
import platform
import subprocess
import sys
import time

from . import version
from . import checks as check_registry
from . import cwd as cwd_mod
from .impact import DiffUnavailable, changed_files, resolve_range, _git
from . import content_binding
# `contracts` is NOT imported at module scope: `contracts.py` imports
# `handover.py`, which imports `status.py`, which imports THIS module at
# module scope (`evidence_mod.CHECK_KIND_NAMES`), so an eager import here
# closes the cycle back onto a partially-initialized `evidence` module.
# BAND L, L1 below imports it lazily instead, inside the two functions that
# need it, the same way `cli.py` and `decisions.py` already import `evidence`
# lazily rather than at their own module scope for the identical reason.

from ._toolspath import mount
mount()
from sbe_checks import answered, evidence_problem  # noqa: E402
from sbe_telemetry import SECRET_PATTERNS  # noqa: E402

#: Schema versions this module knows how to read. An unknown version is refused
#: rather than parsed hopefully: a consumer that reads an old field under new
#: semantics is worse off than one that fails loudly. See `_check_schema`.
#: OLDEST FIRST, and the order is load-bearing: `_fields_for` reads it as the
#: version timeline, so a receipt is never judged by a field its own version
#: postdates.
KNOWN_SCHEMA_VERSIONS = ("1.0", "1.1", "1.2", "1.3", "1.4")

#: The version NEW receipts are stamped with. Evidence-local on purpose: the
#: package-wide SCHEMA_VERSION governs other stores (the task registry, the
#: init config), and coupling their versions would force every consumer to
#: move in lockstep for a change only receipts carry. "1.1" adds
#: `argvRedactions` beside the recorded argv. "1.2" adds `checkKinds` and
#: `checkKindsSource`, the declared identity of the check this run is offered
#: as. FORWARD ONLY: no receipt already on disk is rewritten by this bump, and
#: every older version stays readable under its own contract. "1.3" adds the
#: REGISTERED check binding (`checkId`, `checkKind`, `checkSpecSha256`,
#: `checkBinding`, `checkIdSource`): which registered check ran, resolved from
#: `.sbe/checks.yml` rather than declared by the caller. "1.4" adds
#: `ciRunUrl`, the other half of the producer record: `ciRunId` says a build
#: system claimed the run, and the url is how a human reaches that run without
#: already knowing which build system minted the id.
EVIDENCE_SCHEMA_VERSION = "1.4"

GENERATOR = "sbe evidence run"

#: The obligations a receipt can be declared against, and the ONE place this
#: vocabulary is written down. `src/brothersbe/status.py` derives its own
#: MISSING EVIDENCE table from this tuple rather than keeping a second copy,
#: so a kind added here can never be a kind the consumer silently does not
#: know. Values are lowercase and stable: they are written into receipts that
#: outlive the build that wrote them.
CHECK_KIND_NAMES = ("design", "gate", "score")

#: Every field a receipt must record before any verdict is built on it. Each
#: goes through `answered()`, the same vacuity test every other receipt field in
#: this project goes through, so "" and "TODO" record nothing here too. `0` and
#: `False` are answers: a zero exit code and a zero-byte stdout are results.
#: `checkKinds` is deliberately NOT here: an empty list is a real answer (this
#: run declares no obligation), and `answered()` reads an empty list as
#: nothing, so requiring it would fail every honest run that made no claim.
#: `checkKindsSource` IS required, because the provenance sentence is written
#: unconditionally: a receipt that carries no sentence at all is a receipt
#: whose generator never considered the question.
REQUIRED_FIELDS = (
    "schemaVersion", "generator", "generatorVersion", "runId", "argv",
    "argvRedactions", "startedAt", "endedAt", "durationSeconds", "exitCode",
    "headCommit", "stdoutSha256", "stderrSha256", "environment", "toolVersions",
    "workingTreeDirty", "coveredFilesSource", "checkKindsSource", "checkIdSource",
)

#: The facts the seal covers. Deliberately every fact a forger would have to
#: choose: the command, when it ran, how long it took, what it returned, what it
#: printed, which commit it was made against, and what it claims to cover.
#: `argv` here is the RECORDED argv, after `redact_argv()` has run: the seal
#: covers what the receipt actually says, the same way it always has, and
#: `argvRedactions` sits beside it so a forger cannot drop the count to make a
#: redacted receipt read as verbatim without breaking the seal.
#: `checkKinds` and `checkKindsSource` are sealed for the reason the whole
#: field exists: an unsealed identity could be typed into an existing receipt
#: afterwards, which is the same hand-authoring this module refuses everywhere
#: else. Sealed, a declared kind can only come from a run that happened.
SEALED_FIELDS = (
    "schemaVersion", "generator", "generatorVersion", "argv", "argvRedactions",
    "startedAt", "endedAt", "durationSeconds", "exitCode", "baseCommit",
    "headCommit", "stdoutSha256", "stderrSha256", "stdoutBytes", "stderrBytes",
    "environment", "toolVersions", "workingTreeDirty", "ciRunId",
    # SEALED FROM SCHEMA 1.4 ONWARD, AND ONLY FROM THERE. `compute_seal`
    # digests `_fields_for(declared, SEALED_FIELDS)`, so membership in this
    # tuple is not the whole rule: the FIELDS_INTRODUCED_IN row below puts
    # this field in 1.4, and a receipt DECLARING 1.3 or earlier is judged by
    # its own version's contract, which never had it. On such a receipt a url
    # can be typed in by hand afterwards, the digest does not move, and the
    # receipt still verifies.
    # That is the deliberate cost, stated rather than hidden. Sealing this
    # field retroactively would change the digest of every 1.3 receipt already
    # on disk and fail all of them at once, which is a worse outcome than one
    # unchecked field on receipts minted before the field existed. The
    # narrower property is the one that holds: on a receipt minted at 1.4 or
    # later the url IS in the digest, so a url typed in afterwards leaves
    # `runId` no longer matching the seal.
    # EVEN THERE IT IS TAMPER EVIDENCE, NOT A SIGNATURE, the same words
    # `_check_seal` uses further down. The seal is a keyless SHA256 anyone who
    # has read this module recomputes, so it catches an edit nobody resealed
    # and stops nobody who chose to reseal.
    # NOTHING IN THIS REPOSITORY READS THE URL. It is recorded for a human to
    # follow back to the build run; no check resolves it, fetches it, or
    # compares it against `ciRunId`.
    "ciRunUrl",
    "coveredFiles", "coveredFilesSource", "repository", "checkKinds",
    "checkKindsSource",
    # The registered binding is sealed for the reason the whole registry
    # exists: an UNSEALED checkId could be typed into an existing free-form
    # receipt afterwards, which is the hand authoring this module refuses
    # everywhere else, and it would hand `true` the identity of a registered
    # migration rehearsal. Sealed, a registered identity can only come from a
    # run this wrapper resolved out of `.sbe/checks.yml` itself.
    "checkId", "checkKind", "checkSpecSha256", "checkBinding", "checkIdSource",
)

#: Which fields each schema version INTRODUCED, so `_fields_for` can judge a
#: receipt by its own version's contract instead of by the newest one. Add a
#: row here when a version adds a field; nothing else needs to change.
FIELDS_INTRODUCED_IN = (
    ("1.1", ("argvRedactions",)),
    ("1.2", ("checkKinds", "checkKindsSource")),
    ("1.3", ("checkId", "checkKind", "checkSpecSha256", "checkBinding", "checkIdSource")),
    ("1.4", ("ciRunUrl",)),
)


def _fields_for(declared, fields):
    """The field tuple a receipt of `declared` schema version is judged by.

    "1.0" predates argv redaction and "1.1" predates the declared check kind,
    so their receipts are judged by their own contract: requiring a field that
    version never defined would fail an honest old receipt with a sentence
    about emptiness instead of the truth, that the field postdates it. Fields
    are only ever ADDED here, which is what makes the bump forward-only.

    An unknown or missing version is judged by the FULL, newest set, which is
    the strict direction: `_check_schema` refuses an unknown version before
    any caller in `verify` reaches this, so the only way to arrive here with
    one is a caller that skipped that refusal, and such a caller should get
    the strictest reading rather than the most forgiving.
    """
    if declared not in KNOWN_SCHEMA_VERSIONS:
        return fields
    age = KNOWN_SCHEMA_VERSIONS.index(declared)
    later = set()
    for version_name, names in FIELDS_INTRODUCED_IN:
        if KNOWN_SCHEMA_VERSIONS.index(version_name) > age:
            later.update(names)
    return tuple(f for f in fields if f not in later)


#: One name per pattern in `SECRET_PATTERNS`, same order, so a redacted argv
#: token says WHICH shape matched instead of a bare "[REDACTED]". The patterns
#: themselves are imported above from `tools/sbe_telemetry.py`, the same list
#: the telemetry redactor already scans an operator's own messages with; only
#: these labels are added here, because that redactor never needed to name a
#: shape and a receipt reader does. Kept in sync by `redact_argv()`'s own length
#: check below: a mismatch is refused rather than mislabeled.
ARGV_REDACTION_SHAPES = (
    "api-key", "github-token", "github-pat", "aws-key-id", "slack-token",
    "bearer-token", "private-key", "jwt", "kv-secret", "ssn", "card-number",
)


class ReceiptUnreadable(Exception):
    """The receipt could not be read at all, so nothing was inspected.

    Raised rather than returning an empty receipt, because an empty receipt and
    an unreadable one produce the same dict and only one of them is a file
    somebody wrote.
    """


def _sha256_bytes(blob):
    return hashlib.sha256(blob).hexdigest()


def _sha256_file(path):
    """The digest of a file's bytes, or None when it cannot be read.

    None is a real answer here and the caller says which one it got: a covered
    file that has been deleted is a different finding from one whose contents
    changed, and folding both into "changed" would name the wrong thing.
    """
    try:
        with open(path, "rb") as fh:
            return _sha256_bytes(fh.read())
    except (IOError, OSError):
        return None


def _iso(epoch):
    """ISO 8601 in UTC, to the second, with an explicit Z.

    A local timestamp in a receipt that travels between a laptop and CI is a
    timestamp nobody can compare. The machine comparison uses the epoch fields
    beside this one; this string is for the human reading `show`.
    """
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def redact_argv(argv):
    """(redacted argv, redaction count, provenance note). Three values, not two:
    a two-value return here would read as a possible verdict source to this
    project's own honesty meta-test (`evals/test_no_data_class.py`), and this
    is not a verdict either, so it says what it is in the third slot rather
    than shipping a bare pair.

    Reuses `SECRET_PATTERNS` imported from `tools/sbe_telemetry.py` above: the
    same shapes the telemetry redactor already masks in an operator's own
    messages, never a second list invented here that could drift from it. Each
    match is replaced by a NAMED marker, `[REDACTED:<shape>]`, using
    `ARGV_REDACTION_SHAPES`, so a reader can tell an AWS key id from an SSN
    without re-deriving the match from a bare "[REDACTED]".

    Matched per ARGUMENT, not on the command line joined into one string:
    joining first would let a shape span a boundary no shell ever produced (the
    end of one argument and the start of the next reading as a single token),
    which is exactly the kind of over-matching a receipt cannot afford to be
    wrong about in the other direction. Each `argv[i]` is scanned and replaced
    independently, in the order the command actually received it.

    This is the same limit the source list carries in its own file: finite by
    nature, so a secret typed in a shape none of these patterns know still
    reaches the receipt untouched. That residual is named in
    `docs/KNOWN-LIMITS.md`, not hidden by this function's success on the shapes
    it does know.
    """
    if len(ARGV_REDACTION_SHAPES) != len(SECRET_PATTERNS):
        raise RuntimeError(
            "ARGV_REDACTION_SHAPES names %d shape(s) but the imported SECRET_PATTERNS from "
            "tools/sbe_telemetry.py holds %d pattern(s); they must change together or a "
            "redaction marker would name the wrong shape. Fix src/brothersbe/evidence.py "
            "before writing another receipt."
            % (len(ARGV_REDACTION_SHAPES), len(SECRET_PATTERNS)))
    redacted = []
    count = 0
    for token in argv:
        clean = token
        for pattern, shape in zip(SECRET_PATTERNS, ARGV_REDACTION_SHAPES):
            clean, hits = pattern.subn("[REDACTED:%s]" % shape, clean)
            count += hits
        redacted.append(clean)
    if count == 0:
        note = "0 redaction(s): argv is recorded verbatim, unchanged from what ran"
    else:
        note = ("%d redaction(s) against %d known secret shape(s) mirrored from "
                "tools/sbe_telemetry.py::SECRET_PATTERNS; argv is no longer verbatim"
                % (count, len(SECRET_PATTERNS)))
    return redacted, count, note


def kind_declaration_note(kinds):
    """The provenance sentence written into `checkKindsSource`, every time.

    Written unconditionally, including when nothing was declared, because the
    absence of a declaration is itself a fact a reader needs: a receipt with an
    empty `checkKinds` and no sentence beside it is indistinguishable from a
    receipt whose generator predates the field, and those two are different
    situations. One value, not a pair: this is a note, never a verdict.
    """
    if not kinds:
        return ("no --kind was given, so this receipt declares no check kind and satisfies no "
                "design, gate or score obligation. A kind is never inferred from the recorded "
                "command line; that inference is the defect this field replaced")
    return ("declared by --kind at generation time: %s. The wrapper ran the command and timed "
            "it; it does not inspect what the command did, so this names the obligation the "
            "run was OFFERED against, never a proof that the command performs that check. The "
            "recorded argv sits beside it for a reader to compare"
            % ", ".join(kinds))


def declared_kinds(receipt):
    """The set of check kinds this receipt DECLARES. Never inferred from argv.

    The one reader of the field, so `sbe status` and anything else downstream
    cannot drift into a second interpretation. An entry that is not a known
    kind name is dropped here rather than passed on, because an unknown kind
    can clear no obligation this build knows about; `_check_kinds` is what
    reports the receipt as malformed for carrying one.
    """
    declared = receipt.get("checkKinds")
    if not isinstance(declared, list):
        return set()
    return set(k for k in declared if isinstance(k, str) and k in CHECK_KIND_NAMES)


def kind_declaration_gap(receipt):
    """The sentence saying why this receipt clears no obligation, or None when
    it declares at least one kind.

    Three different absences, named separately, because a consumer that folds
    them together tells a reader "no evidence" where the truth is "evidence
    this build cannot read as evidence for anything":
    a receipt written before the field existed, a receipt whose field holds
    something that is not a list of kinds, and an honest run that declared
    nothing.
    """
    if declared_kinds(receipt):
        return None
    version_seen = answered(receipt.get("schemaVersion")) or "no schemaVersion"
    if "checkKinds" not in receipt:
        return ("it records no checkKinds field at all (schema %s predates it), so which check "
                "it ran is not stated anywhere in it. Legacy receipts are NO-DATA for "
                "obligation purposes here, never a silent pass; re-run the check through `sbe "
                "evidence run --kind <design|gate|score>` to record one that says"
                % version_seen)
    raw = receipt.get("checkKinds")
    if not isinstance(raw, list):
        return ("its checkKinds field holds %r, which is not a list of check kinds, so nothing "
                "here can read which check it ran" % (raw,))
    known = [k for k in raw if isinstance(k, str) and k in CHECK_KIND_NAMES]
    if raw and not known:
        return ("its checkKinds field names only %s, and this build knows no such kind (it "
                "knows %s)" % (", ".join(repr(k) for k in raw), ", ".join(CHECK_KIND_NAMES)))
    return ("it declares no check kind: it was run without `--kind`, so it is evidence that a "
            "command ran and not evidence that any named obligation was met")


def registered_check_id(receipt):
    """The REGISTERED check this receipt was minted for, or None.

    The one reader of the field, so every consumer asks the same question the
    same way. `checkKinds` is deliberately not consulted here: a kind is a word
    the caller typed and an id is a name this wrapper resolved out of
    `.sbe/checks.yml`, and folding them together is the defect this schema
    version closes.
    """
    cid = receipt.get("checkId")
    if isinstance(cid, str) and cid.strip():
        return cid.strip()
    return None


def check_identity_note(check_id, spec_sha=None):
    """The provenance sentence written into `checkIdSource`, every time.

    Written unconditionally, including for a free-form run, because the absence
    of a registered identity is itself the fact a reader needs: a receipt with
    no id and no sentence beside it is indistinguishable from one whose
    generator predates the registry, and those are different situations. One
    value, not a pair: this is a note, never a verdict.
    """
    if not check_id:
        return ("no --check was given, so this run is FREE FORM: the command came from the "
                "caller, not from .sbe/checks.yml. Free-form evidence is LOCAL-ADVISORY "
                "whatever else is true of it, and it satisfies no required policy check; "
                "rerun through `sbe evidence run --check <id>` for evidence that does")
    return ("resolved from .sbe/checks.yml at generation time: check %s, specification digest "
            "%s. The executable, the argument vector, the working directory and the covered "
            "paths came from the registry and no argument on the command line could replace "
            "any of them" % (check_id, spec_sha))


def working_tree_dirty(cwd):
    """(dirty, detail). A repository git cannot answer for is reported, never
    assumed clean: 'no output from git status' and 'git did not run' look
    identical downstream and only one of them means the tree is clean."""
    code, out, err = _git(["status", "--porcelain"], cwd)
    if code != 0:
        return None, "git status did not run here: %s" % (err.strip() or "no message")
    lines = [l for l in out.split("\n") if l.strip()]
    return bool(lines), "%d uncommitted path(s)" % len(lines)


def repository_identity(cwd):
    """The remote this work came from, or an honest note that there is none.

    A local clone with no remote is a normal thing and not a defect, so it is
    recorded as such rather than left blank, which would be indistinguishable
    from a field the generator forgot to fill.
    """
    code, out, _err = _git(["remote", "get-url", "origin"], cwd)
    remote = out.strip() if code == 0 else ""
    return {
        "remote": remote or None,
        "remoteNote": None if remote else "no origin remote is configured in this clone",
        "root": os.path.abspath(cwd),
    }


def compute_seal(receipt):
    """The SHA256 over the sealed run facts, canonically encoded.

    `sort_keys` and a fixed separator set, so two runs that observed the same
    facts produce the same seal on any machine, and a field reordered by an
    editor does not read as a tampered receipt.
    """
    declared = answered(receipt.get("schemaVersion")) or EVIDENCE_SCHEMA_VERSION
    payload = dict((k, receipt.get(k)) for k in _fields_for(declared, SEALED_FIELDS))
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")
    return _sha256_bytes(blob)


#: The one sentence every surface uses for this level, so the wording a
#: reader sees can never drift from what the code actually checked.
CLAIMED_SENTENCE = ("CI-CLAIMED means CI shaped metadata was recorded but no protected "
                    "identity was verified.")


def trust_level(receipt):
    """LOCAL-ADVISORY or CI-CLAIMED, and the sentence saying why.

    CI-CLAIMED needs three things: the run was a REGISTERED check, a CI run id
    was recorded by the environment, and the tree was clean at generation time. A
    CI job running over uncommitted edits is not protected by being CI; it is a
    local run wearing a badge. And a free-form command in CI is not protected
    either, however clean the tree: nothing outside the caller says which check
    it was, so `-- true` under a CI run id would otherwise read as the strongest
    evidence this project can mint.

    The registration condition applies from schema 1.3 onward ONLY. A receipt
    written before the registry existed is judged by its own version's contract,
    the same way every other field here is: re-reading an old receipt under a
    rule its generator could not satisfy would relabel evidence nobody changed.
    """
    declared = answered(receipt.get("schemaVersion"))
    knows_registry = (declared in KNOWN_SCHEMA_VERSIONS
                      and KNOWN_SCHEMA_VERSIONS.index(declared)
                      >= KNOWN_SCHEMA_VERSIONS.index("1.3"))
    if knows_registry and registered_check_id(receipt) is None:
        return ("LOCAL-ADVISORY",
                "this receipt was minted for a free-form command rather than a check "
                "registered in .sbe/checks.yml, so nothing outside the caller says which check "
                "it is. Free-form evidence is advisory whatever else is true of it")
    ci = answered(receipt.get("ciRunId"))
    dirty = receipt.get("workingTreeDirty")
    if ci is None:
        return ("LOCAL-ADVISORY",
                "no SBE_CI_RUN_ID was set when this ran, so nothing outside the machine that "
                "wrote it attests to it")
    if dirty is not False:
        return ("LOCAL-ADVISORY",
                "a CI run id is recorded (%s) but the working tree was dirty or unreadable at "
                "generation time, and a run over uncommitted edits is not protected by being "
                "CI" % ci)
    # BR-1013, the honest downgrade. This branch used to return PROTECTED-CI,
    # which any local process could obtain by exporting SBE_CI_RUN_ID: the seal
    # is a checksum over the receipt's own fields, so it proves the receipt was
    # not edited afterwards and proves nothing about who produced it. Until a
    # cryptographic attestation binds a receipt to a repository, a workflow and
    # a commit, the strongest honest thing this project can say is that CI
    # shaped metadata was recorded. There is deliberately no level above this
    # one: a policy that demands protected evidence now finds nothing that can
    # satisfy it, and says so, which is the correct answer while the proof does
    # not exist.
    return ("CI-CLAIMED",
            "%s A run id (%s) was recorded on a clean tree, and nothing here verified who "
            "set it" % (CLAIMED_SENTENCE, ci))


def generate(cwd, argv, base=None, head="HEAD", covers=None, timeout=None, kinds=None,
             check_id=None, registry_path=None):
    """Run `argv` and return the receipt describing what actually happened.

    `check_id`, when given, is the REGISTERED mode and it is the mode this
    module now exists for. The executable, the argument vector, the working
    directory, the covered paths and the environment all come from
    `.sbe/checks.yml`; `argv`, `covers` and `kinds` must be empty, and a caller
    that supplies any of them is refused rather than quietly overridden, because
    a substitution that succeeds silently is exactly the caller-supplied command
    the registry replaces.

    The command runs HERE. Nothing in the signature accepts a duration, an exit
    code or an output digest, which is the whole point: those three are the
    fields a hand-written receipt gets to invent today.

    `kinds` is the operator's declaration of WHICH obligation this run is
    offered against, from `CHECK_KIND_NAMES`, and it is the one thing here the
    wrapper takes on trust rather than observes. It is taken anyway, because
    the alternative shipped for a wave and was worse: the consumer guessed the
    identity from the command line, and a command that ran no check cleared
    three obligations by having three words in a filename. A declaration is
    checkable by a reader against the argv recorded next to it; a guess is not.

    `timeout`, in seconds, is None unless the caller passes `--timeout`. There
    is no default: a silent one would kill a legitimate long suite and hand
    back a receipt about a run that never finished, which is the exact
    false-positive pattern this project's kill criteria warn against. When it
    is set and the command outruns it, `subprocess.run` kills the child and
    waits for it (that is Python's own contract for `timeout=`, not something
    hand-rolled here), and this raises `ReceiptUnreadable` rather than writing
    a receipt: no exit code was ever observed, so there is nothing honest to
    seal, and the caller's stderr names what happened instead.
    """
    # THE REGISTERED PATH, resolved before anything else: every refusal below
    # has to happen before a process starts, because a receipt that could not be
    # written is a run whose result nobody records.
    spec = binding = None
    run_env = None
    covers_from_registry = None
    if check_id:
        if argv:
            raise ReceiptUnreadable(
                "`--check %s` resolves its command from .sbe/checks.yml and a command was also "
                "given after `--`. There is no substitution on this path: drop the `-- "
                "<command...>`, or drop `--check` and mint a free-form LOCAL-ADVISORY receipt "
                "that satisfies no required check" % check_id)
        if covers:
            raise ReceiptUnreadable(
                "`--check %s` takes its covered paths from the registry's `covers` globs, so "
                "`--covers` is refused here: a coverage list the caller supplies is the same "
                "substitution as a command the caller supplies, one field further along"
                % check_id)
        if kinds:
            raise ReceiptUnreadable(
                "`--check %s` takes its kind from the registry, so `--kind` is refused here. A "
                "caller-declared kind beside a registered id is the free-form declaration this "
                "registry replaced" % check_id)
        path = registry_path or check_registry.default_registry_path(cwd)
        try:
            registry = check_registry.load_registry(path)
            spec = check_registry.resolve(registry, check_id)
            binding, run_env = check_registry.binding_of(cwd, spec, path)
            covers_from_registry = check_registry.covered_paths(cwd, spec)
        except check_registry.RegistryUnreadable as exc:
            raise ReceiptUnreadable(
                "no receipt was written for check %r: %s" % (check_id, exc))
        argv = check_registry.expected_argv(spec)
        kinds = None

    if not argv:
        raise ReceiptUnreadable("no command to run: `sbe evidence run` needs `-- <command...>` "
                                "or `--check <id>`")

    # Refused BEFORE the command runs, not after: a receipt that could not be
    # written is a run whose result nobody records, so the argument that makes
    # the receipt impossible has to fail first.
    declared = sorted(set(kinds or ()))
    unknown = [k for k in declared if k not in CHECK_KIND_NAMES]
    if unknown:
        raise ReceiptUnreadable(
            "no receipt would be readable for check kind(s) %s; this build knows %s. A kind "
            "nothing downstream recognizes clears no obligation, so it is refused here rather "
            "than written into a receipt that would look like a claim"
            % (", ".join(repr(k) for k in unknown), ", ".join(CHECK_KIND_NAMES)))

    covered_source = "explicit --covers"
    if covers_from_registry is not None:
        covered_paths = covers_from_registry
        covered_source = ("the registered check %s, whose covers globs (%s) matched %d tracked "
                          "file(s)" % (check_id, ", ".join(spec["covers"]),
                                       len(covers_from_registry)))
    elif covers:
        covered_paths = list(covers)
    else:
        try:
            base_sha, head_ref = resolve_range(cwd, base, head)
            covered_paths = changed_files(cwd, base_sha, head_ref)
            covered_source = "the diff %s..%s" % (base_sha[:12], head_ref)
        except DiffUnavailable as exc:
            covered_paths = []
            covered_source = "none: %s" % exc

    code, base_out, _e = _git(["rev-parse", base] if base else ["rev-parse", "HEAD^"], cwd)
    base_commit = base_out.strip() if code == 0 else None
    code, head_out, _e = _git(["rev-parse", head], cwd)
    head_commit = head_out.strip() if code == 0 else None

    dirty, dirty_detail = working_tree_dirty(cwd)

    # A registered check runs in the directory its registry entry names,
    # resolved against the repository root, and inherits ONLY the allowlisted
    # environment. A free-form run keeps today's behavior exactly: the
    # repository root and the caller's own environment.
    run_dir = cwd
    if spec is not None:
        run_dir = os.path.abspath(os.path.join(cwd, spec["command"]["cwd"]))
        if not os.path.isdir(run_dir):
            raise ReceiptUnreadable(
                "check %s is registered to run in %r, which does not exist under %s, so no "
                "command was started" % (check_id, spec["command"]["cwd"], cwd))

    started = time.time()
    try:
        proc = subprocess.run(list(argv), cwd=run_dir, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout, env=run_env)
    except OSError as exc:
        raise ReceiptUnreadable(
            "the command did not start, so there is no run to write a receipt about: %s" % exc)
    except subprocess.TimeoutExpired:
        elapsed = time.time() - started
        raise ReceiptUnreadable(
            "the command was killed after %.3fs, past --timeout %ss. No exit code was ever "
            "observed, so no receipt was written for this run: a timeout is a fact about the "
            "wrapper, never a fact worth sealing as if the command had answered. Rerun without "
            "--timeout, or with a longer one, if this command legitimately needs more time"
            % (elapsed, timeout))
    ended = time.time()

    # Pass the child's output through untouched. A wrapper that swallows what
    # the command printed makes the human read the receipt instead of the run,
    # and the receipt deliberately does not carry the text.
    if proc.stdout:
        sys.stdout.write(proc.stdout.decode("utf-8", "replace"))
    if proc.stderr:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))

    covered = []
    for rel in covered_paths:
        full = rel if os.path.isabs(rel) else os.path.join(cwd, rel)
        digest = _sha256_file(full)
        covered.append({
            "path": rel,
            "sha256": digest,
            "note": None if digest else "this file could not be read when the receipt was "
                                        "written, so its content is not bound to this run",
        })

    # The COMMAND already ran above, over the untouched `argv` the caller gave
    # this function. Only the RECORDED copy is redacted here, after the run, so
    # a masked token never reaches the process that was actually invoked.
    redacted_argv, argv_redactions, argv_redaction_note = redact_argv(list(argv))

    receipt = {
        "schemaVersion": EVIDENCE_SCHEMA_VERSION,
        "generator": GENERATOR,
        "generatorVersion": version(),
        "repository": repository_identity(cwd),
        "baseCommit": base_commit,
        "headCommit": head_commit,
        "argv": redacted_argv,
        "argvRedactions": argv_redactions,
        "startedAt": _iso(started),
        "endedAt": _iso(ended),
        "startedAtEpoch": round(started, 6),
        "endedAtEpoch": round(ended, 6),
        "durationSeconds": round(ended - started, 6),
        "exitCode": proc.returncode,
        "toolVersions": {
            "python": platform.python_version(),
            "sbe": version(),
        },
        "environment": platform.platform(),
        # DIGESTS, NOT TEXT, and the reason is in the module docstring: a
        # receipt is the one artifact everybody is encouraged to share, so a
        # secret printed by the command must not be persisted into it.
        "stdoutSha256": _sha256_bytes(proc.stdout or b""),
        "stderrSha256": _sha256_bytes(proc.stderr or b""),
        "stdoutBytes": len(proc.stdout or b""),
        "stderrBytes": len(proc.stderr or b""),
        "outputPolicy": "digests only; raw stdout and stderr are never persisted, because a "
                        "receipt is shared and a secret in output would be shared with it",
        "workingTreeDirty": dirty,
        "workingTreeDetail": dirty_detail,
        "ciRunId": os.environ.get("SBE_CI_RUN_ID") or None,
        # The other half of the producer record, and a POINTER rather than a
        # check: `producer_class` in tools/sbe_passport.py branches on
        # `ciRunId` alone, and no consumer in this repository reads `ciRunUrl`
        # at all. It is written so a human can reach the build run; nothing
        # resolves it or holds it against the id.
        # `or None` rather than "" all the same, for the reason the id beside
        # it uses it: a key PRESENT and null is this wrapper saying it looked
        # and found nothing, which is a different fact from an ABSENT key, and
        # keeping the shape means a later consumer can read that difference
        # here the way `producer_class` already reads it on the id.
        "ciRunUrl": os.environ.get("SBE_CI_RUN_URL") or None,
        "coveredFiles": covered,
        "coveredFilesSource": covered_source,
        # WHICH CHECK, as a field, because the consumer used to read it out of
        # the command line above and a filename could spell three obligations.
        "checkKinds": declared,
        "checkKindsSource": kind_declaration_note(declared),
        # WHICH REGISTERED CHECK, resolved from .sbe/checks.yml rather than
        # declared beside the command. None on the free-form path, and the
        # sentence below says so rather than leaving a reader to infer it.
        "checkId": check_id or None,
        "checkKind": spec["kind"] if spec else None,
        "checkSpecSha256": check_registry.spec_sha256(spec) if spec else None,
        "checkBinding": binding,
        "checkIdSource": check_identity_note(
            check_id, check_registry.spec_sha256(spec) if spec else None),
    }
    receipt["runId"] = compute_seal(receipt)
    return receipt


def write_receipt(receipt, out_path):
    """Write `receipt` (as returned by `generate()`) to `out_path` as JSON,
    creating the parent directory first if it does not exist yet.

    This is the exact write `sbe evidence run`'s own `--out` performs (see
    `main`, the `run` subcommand, which calls this too): pulled out so a
    second caller that already holds a receipt from `generate()` -- today
    that is `mint_default` below, used by `sbe verify`'s default minting --
    uses the identical writer path rather than growing a second one beside
    it. `design/lifecycle-blockers/03-adr.md` records that instruction as
    binding for CR-08: receipts route through this module's own writer,
    never a parallel one.

    Raises OSError on a directory or a file it cannot create; this function
    does not decide what that failure means to whichever command called it,
    that call keeps the job, same as every other write in this module.
    """
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    with io.open(out_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")


def mint_default_many(cwd, kind_argv_pairs, evidence_dir, base=None, head="HEAD", covers=None,
                      timeout=None):
    """Mint one receipt per `(kind, argv)` pair in `kind_argv_pairs`, each to
    the FIXED path `<evidence_dir>/<kind>.json`, and return `(written,
    failures)`: `written` the paths actually written, `failures` a list of
    `(kind, exception)` for the pairs that were not.

    This is the helper `sbe verify`'s default minting uses (`cli.py`'s
    `_cmd_verify`, by way of `_mint_evidence`), so minting several receipts
    at once stays inside this module's own generate-then-write path instead
    of `cli.py` growing a parallel writer that fabricates a receipt's fields
    from a run it already watched happen. Every `generate()` call here runs
    `argv` again, because THE COMMAND IS RUN HERE is this module's own law
    (see the module docstring): a receipt has to observe an actual run,
    never restate one somebody else watched.

    EVERY PAIR IS GENERATED BEFORE ANY OF THEM IS WRITTEN, and that order is
    the reason this function exists beside a simpler generate-then-write
    loop. `evidence_dir` sits inside `cwd`, the same tree `generate()` reads
    `git status --porcelain` against to decide `workingTreeDirty` for EACH
    receipt (`working_tree_dirty`); write one receipt at a time -- generate,
    write, generate, write -- and the first receipt's own file is a new
    untracked path by the time the second one asks whether the tree is
    dirty, so only the first of several receipts minted together could ever
    read clean, and every one after it reads NO-DATA for a reason that has
    nothing to do with the code under test and everything to do with this
    function's own prior write. Generating every receipt first, against the
    tree exactly as the caller found it, then writing every one that
    generated successfully, means every receipt's dirty state reflects one
    shared truth about that tree, never a cascade this function created by
    writing into the thing it was reading.

    A FIXED path per kind, not one namespaced by time or run id, is
    deliberate for a second reason: `evidence.verify`'s own `exclude_dirs`
    parameter, the same machinery `status.py`'s `_scan_evidence` already
    passes, treats a receipt regenerated at a fixed `--out` path as the
    ordinary shape of a routine re-run rather than a change to the code
    under test (see `docs/KNOWN-LIMITS.md`, "Evidence covering evidence").
    Without a fixed path, every `sbe verify` would mint a new file and the
    store would grow without bound; with one, running `sbe verify` twice
    regenerates the same receipts in place, the shape that exclusion
    machinery already expects.

    A failure generating or writing one pair does not stop the others: each
    is attempted independently and its exception, if any, lands in
    `failures` rather than being raised, so one broken delegate does not
    cost the receipts the other delegates earned.
    """
    generated, failures = [], []
    for kind, argv in kind_argv_pairs:
        try:
            receipt = generate(cwd, argv, base=base, head=head, covers=covers, timeout=timeout,
                               kinds=[kind])
        except Exception as exc:
            failures.append((kind, exc))
            continue
        generated.append((kind, receipt))
    written = []
    for kind, receipt in generated:
        out_path = os.path.join(evidence_dir, "%s.json" % kind)
        try:
            write_receipt(receipt, out_path)
        except Exception as exc:
            failures.append((kind, exc))
            continue
        written.append(out_path)
    return written, failures


def mint_default(cwd, kind, argv, evidence_dir, base=None, head="HEAD", covers=None,
                 timeout=None):
    """Run `argv`, declared as `kind`, through `generate()`, write the
    receipt to the FIXED path `<evidence_dir>/<kind>.json`, and return that
    path. Raises whatever `mint_default_many` recorded as this one pair's
    failure, if generating or writing it failed.

    The single-receipt convenience over `mint_default_many` above, which
    does the actual generate-then-write work; see its docstring for why
    minting more than one receipt together needs the two-phase order this
    function does not, on its own, need to worry about.
    """
    written, failures = mint_default_many(cwd, [(kind, argv)], evidence_dir, base=base,
                                          head=head, covers=covers, timeout=timeout)
    if failures:
        _, exc = failures[0]
        raise exc
    return written[0]


# ---------------------------------------------------------------------------
# BAND L, L1: THE RELEASE RECEIPT. A release receipt is a `contracts.py`
# lifecycle object (the six-field identity spine plus `contracts.
# RELEASE_RECEIPT_BINDING`: `releasedCommit`, `artifactSha256`, `runId`, plus
# `contracts.RELEASE_RECEIPT_FIELDS`: `releasedAt`), read and written here as
# a plain JSON file, the same way `load()`/`write_receipt()` above already
# read and write an evidence run receipt. IT IS A DIFFERENT RECORD than the
# run receipt this module has minted since BAND E: a run receipt proves a
# COMMAND executed; a release receipt records that a CHANGE was released.
# The two are never folded into one shape, and `CHECK_KIND_NAMES`, the run
# receipt's own closed obligation vocabulary, is untouched by this section on
# purpose: a release receipt clears no design/gate/score obligation and
# declares none.
#
# `contracts.validate_release_receipt` and `contracts.RELEASE_RECEIPT_
# BINDING` already check that a receipt binds `changeId`, `releasedCommit`,
# `artifactSha256` and `runId` to something non-hollow, on the receipt's own
# face. What that module cannot say is where the receipt's OWN CLAIM came
# from: a receipt minted over a unit-test fixture and a receipt minted by a
# real CI job both validate identically on `contracts.py`'s face, because
# `contracts.py` is never told the difference. `RELEASE_SOURCE_CLASSES` is
# that missing fact, recorded here as `sourceAttribution.trustClass`, a
# closed vocabulary the same way `contracts.PRODUCER_CLASSES` is: an unknown
# class is refused rather than read as "probably real".
# ---------------------------------------------------------------------------

#: The closed vocabulary a release receipt's `sourceAttribution.trustClass`
#: is drawn from. `observation.py` (L3) imports this tuple rather than
#: inventing its own, so "fixture" means the same thing on an observation
#: result that it means here.
RELEASE_SOURCE_CLASSES = ("fixture", "file", "ci-run")


def build_release_receipt(change_id, released_commit, artifact_sha256, run_id,
                          trust_class, source_detail, producer, producer_class="tool",
                          origin=None, released_at=None):
    """A release receipt: the `contracts.py` lifecycle fields plus this
    module's own `sourceAttribution`. Builds a dict; does not write
    anything. Pair with `write_receipt()` (already this module's one JSON
    writer, see its own docstring) for a file, and `contracts.validate(
    "release-receipt", ...)` (or `verify_release_receipt_for_release` below,
    which calls it) to check the result before trusting it.

    `released_at` and `createdAt` default to NOW, UTC, with an explicit
    offset (`_iso`, this module's own formatter): `contracts.declaration_
    timing` (L2) can only compare an instant `contracts._instant` can parse
    -- a date, a time, AND an explicit UTC offset -- so a receipt this
    module mints defaults to a timestamp that rule can actually read, rather
    than a bare date that reads as midnight.

    Raises `ValueError` naming `trust_class` when it is not one of
    `RELEASE_SOURCE_CLASSES`: an attribution this module cannot classify
    classifies nothing, and guessing which of the three it probably meant is
    the exact hazard THE CODEX A2 LAW (`verify_release_receipt_for_release`,
    below) exists to close by refusing outright instead.
    """
    if trust_class not in RELEASE_SOURCE_CLASSES:
        raise ValueError(
            "trust_class %r is not one of %s; a release receipt's source attribution is a "
            "closed vocabulary, refused rather than read as probably real"
            % (trust_class, ", ".join(RELEASE_SOURCE_CLASSES)))
    from . import contracts as contracts_mod  # lazy: see the module-scope comment above
    stamp = released_at or _iso(time.time())
    return {
        "schemaVersion": contracts_mod.LIFECYCLE_SCHEMA_VERSION,
        "changeId": change_id,
        "createdAt": stamp,
        "producer": producer,
        "producerClass": producer_class,
        "origin": origin,
        "releasedCommit": released_commit,
        "artifactSha256": artifact_sha256,
        "runId": run_id,
        "releasedAt": stamp,
        "sourceAttribution": {"trustClass": trust_class, "detail": source_detail},
    }


def read_release_receipt(path):
    """The release receipt at `path`, or raise `ReceiptUnreadable`.

    The SAME reader `load()` above already gives an evidence run receipt
    (the FIFO/socket guard from `sbe_checks.evidence_problem`, the JSON-
    object shape check): a release receipt is a JSON file exactly the same
    way, so this is that function under a name that says what it reads here,
    not a second reader growing its own rules beside it.
    """
    return load(path)


def release_receipt_trust(receipt):
    """(trustClass, detail): the source attribution `receipt` carries, or
    `(None, note)` when it carries none this module can read as one of
    `RELEASE_SOURCE_CLASSES`.

    The ONE reader of `sourceAttribution`, so a caller never re-derives "is
    this receipt real" a second way that could disagree with THE CODEX A2
    LAW below.
    """
    attribution = receipt.get("sourceAttribution")
    if not isinstance(attribution, dict):
        return None, "this receipt carries no sourceAttribution object"
    trust_class = attribution.get("trustClass")
    if trust_class not in RELEASE_SOURCE_CLASSES:
        return None, ("sourceAttribution.trustClass %r is not one of %s"
                      % (trust_class, ", ".join(RELEASE_SOURCE_CLASSES)))
    return trust_class, attribution.get("detail")


def verify_release_receipt_for_release(path, released_commit, expected_artifact_sha256=None):
    """The house 3-tuple (`verdict`, `evidence`, `problems`) over whether the
    release receipt at `path` supports a claim about a REAL release of
    `released_commit`.

    THE CODEX A2 LAW: a receipt whose `sourceAttribution.trustClass` is
    `"fixture"` NEVER satisfies this claim, whatever else it gets right.
    Checked HERE, in the one reader of a release receipt for a live claim,
    rather than left to a caller that might forget: a fixture is
    manufactured evidence for a test, and reading it as a live release is
    exactly the hazard this function exists to refuse, by name, every time,
    naming the trust class in the refusal text.

    A receipt naming a commit other than `released_commit` is also refused
    (`FAIL`): `contracts.validate("release-receipt", ...)` and `contracts.
    RELEASE_RECEIPT_BINDING` only confirm the receipt's OWN face is well-
    formed (every binding field answered, typed as a string) -- neither one
    is handed an EXPECTED commit to compare against, so that comparison is
    added here, after the structural check passes.

    THE ARTIFACT DIGEST CHECK, `expected_artifact_sha256`, closes the same
    class of hole one level down: without it, this function answered only
    "was this receipt released for the commit I expected", with no way to
    also ask "and was it the binary I expected", so a caller could read a
    `PASS` for a released commit whose receipt named a completely different
    artifact. Closed by calling `contracts._artifact_digest_problem`, the
    identical rule `contracts.release_binds` already applies to its own
    `expected_artifact_sha256` argument, so this function SHARES the rule
    instead of growing a second copy that could quietly drift from the
    first: an expectation `answered()` reads as recording nothing is
    refused by name, never read as no expectation at all; either side that
    is not itself a canonical SHA-256 (64 lowercase hexadecimal characters)
    is `FAIL` too, naming the document or the caller as the defective side
    (the arm added after equality-only comparison let two equal strings
    that were never digests at all, `"banana" == "banana"`, reach `PASS`;
    see `contracts._artifact_digest_problem`'s own docstring for the exact
    rule); a recorded digest that differs from the expectation is `FAIL`,
    naming BOTH digests; a match, or an absent expectation, both reach
    `PASS`, distinguished only by the evidence sentence's own wording
    (`contracts._artifact_note`).
    This function still never hashes a single byte of anything: it has no
    artifact to hash, only the receipt's claim about one and a caller's own
    claim about what that digest should be, and this check compares the
    two claims, nothing more.

    THE DIGEST CHECK RUNS LAST, after the fixture refusal and the commit
    check, so it can never rescue a receipt that already fails an earlier
    refusal: a fixture sourced receipt, or one for the wrong commit, is
    `FAIL` on that earlier ground even when its artifact digest happens to
    match `expected_artifact_sha256` exactly.
    """
    from . import contracts as contracts_mod  # lazy: see the module-scope comment above
    try:
        receipt = read_release_receipt(path)
    except ReceiptUnreadable as exc:
        return "NO-DATA", "no release receipt could be read at %s: %s" % (path, exc), ()

    verdict, shape_evidence, problems = contracts_mod.validate("release-receipt", receipt)
    if verdict != "PASS":
        return verdict, shape_evidence, problems

    trust_class, detail = release_receipt_trust(receipt)
    if trust_class is None:
        problem = ("this release receipt carries no readable source attribution (%s); a "
                  "receipt whose source is unstated is refused for a live-release claim "
                  "rather than read as probably real" % detail)
        return "FAIL", problem, (problem,)
    if trust_class == "fixture":
        problem = ("this release receipt's source attribution is trust class %r; a "
                  "fixture-sourced receipt never satisfies a claim about a real release, "
                  "whatever else it gets right" % trust_class)
        return "FAIL", problem, (problem,)

    if receipt.get("releasedCommit") != released_commit:
        problem = ("this receipt releases %r and the claim is about %r; a receipt for "
                  "another commit does not support this claim"
                  % (receipt.get("releasedCommit"), released_commit))
        return "FAIL", problem, (problem,)

    artifact_problem, artifact_verified = contracts_mod._artifact_digest_problem(
        receipt.get("artifactSha256"), expected_artifact_sha256)
    if artifact_problem:
        return "FAIL", artifact_problem, (artifact_problem,)

    artifact_note = contracts_mod._artifact_note(receipt.get("artifactSha256"), artifact_verified)
    return ("PASS",
           "release receipt at %s: trust class %r, released %r, %s, matches the claim"
           % (path, trust_class, released_commit, artifact_note), ())


def load(path):
    """The receipt at `path`, or raise. A file that is not JSON is not a receipt.

    ACCESS is checked BEFORE open(), by stat rather than by opening: a FIFO
    where a receipt was expected used to hang this command forever, in both
    text and JSON mode, with no verdict line at all, which is worse than any
    parse failure because a parse failure at least prints FAIL. This is the
    same `evidence_problem` check the hard gates use (`tools/sbe_checks.py`,
    `tools/sbe_gate.py::load_receipt`), so a FIFO, socket, device or
    unreadable file is refused by name here in bounded time instead.
    """
    if not path or not os.path.exists(path):
        raise ReceiptUnreadable("no receipt at %s; absent evidence is NO-DATA, never a pass"
                                % path)
    problem = evidence_problem(path)
    if problem:
        raise ReceiptUnreadable(
            "%s was refused before it was opened: %s. Opening it directly could block this "
            "command forever; a broken claim must never look like a hang" % (path, problem))
    if os.path.isdir(path):
        raise ReceiptUnreadable("%s is a directory, not a receipt" % path)
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except ValueError as exc:
        raise ReceiptUnreadable("%s does not parse as JSON: %s" % (path, exc))
    except (IOError, OSError) as exc:
        raise ReceiptUnreadable("%s could not be opened: %s" % (path, exc))
    if not isinstance(data, dict):
        raise ReceiptUnreadable("%s holds %s at the top level; a receipt is a JSON object"
                                % (path, type(data).__name__))
    return data


def _check_schema(receipt):
    declared = answered(receipt.get("schemaVersion"))
    if declared is None:
        return "the receipt records no schemaVersion, so nothing here knows what its fields mean"
    if declared not in KNOWN_SCHEMA_VERSIONS:
        return ("schemaVersion %r is not one this build reads (%s); an unknown schema is "
                "refused rather than parsed hopefully, because a field read under the wrong "
                "semantics is worse than one that fails loudly"
                % (declared, ", ".join(KNOWN_SCHEMA_VERSIONS)))
    return None


def _check_required(receipt):
    fields = _fields_for(answered(receipt.get("schemaVersion")), REQUIRED_FIELDS)
    missing = [f for f in fields if answered(receipt.get(f)) is None]
    if not missing:
        return None
    return ("%d required field(s) record nothing: %s. A field holding \"\", null or a "
            "placeholder is a field nobody answered, and a verdict built on it would be "
            "describing an empty string" % (len(missing), ", ".join(sorted(missing))))


def _check_kinds(receipt):
    """The declared check kind is a list of names this build knows, or nothing.

    Only the SHAPE is checkable here. Whether the command actually performed
    the declared check is not something this module can know, and a control
    that pretended otherwise would be the overselling this file's own docstring
    warns about. A receipt that carries no field at all is not a problem: that
    is a legacy receipt, judged under its own version, and its consequence
    lives at the consumer (it clears no obligation) rather than as a FAIL here.
    """
    if "checkKinds" not in receipt:
        return None
    raw = receipt.get("checkKinds")
    if not isinstance(raw, list):
        return ("checkKinds records %r, and a check kind list is a JSON array; a receipt whose "
                "declared identity cannot be read is not a receipt anything should build an "
                "obligation verdict on" % (raw,))
    bad = [k for k in raw if not isinstance(k, str) or k not in CHECK_KIND_NAMES]
    if bad:
        return ("checkKinds names %s, which this build does not know (it knows %s). An "
                "unrecognized kind is refused rather than ignored, because ignoring it would "
                "leave a receipt that reads as a declaration and satisfies nothing"
                % (", ".join(repr(k) for k in bad), ", ".join(CHECK_KIND_NAMES)))
    return None


def _check_registered(receipt, cwd):
    """(problems, note). Does the registered check this receipt names still
    exist, unchanged, and does this receipt still describe it?

    Delegates every comparison to `brothersbe.checks.binding_problems`, which
    owns the registry: recomputing the specification digest here would be a
    second implementation of the same rule, and the two would drift.

    A receipt that names NO registered check is not a problem at this layer.
    Its consequence lives where it belongs: `trust_level` reads it as
    LOCAL-ADVISORY and the policy engine refuses it for any required check, so
    a free-form receipt is honest evidence of a command that ran and never a
    registered check that passed.
    """
    check_id = registered_check_id(receipt)
    if check_id is None:
        return [], ("no registered check is named, so this is free-form evidence: advisory, "
                    "and it satisfies no required policy check")
    problems = check_registry.binding_problems(receipt, cwd)
    if problems:
        return problems, None
    return [], ("registered check %s still matches .sbe/checks.yml: specification digest, "
                "command, working directory, runner hashes and coverage all re-checked"
                % check_id)


def _check_seal(receipt):
    claimed = answered(receipt.get("runId"))
    actual = compute_seal(receipt)
    if claimed == actual:
        return None
    return ("runId %s does not match the seal over this receipt's own run facts (%s). Either "
            "the receipt was written by hand rather than generated by `%s`, or a sealed field "
            "was edited after the run. This seal is tamper evidence, not a signature: it "
            "catches a plausible receipt nobody's command produced, and it does not stop "
            "somebody who read src/brothersbe/evidence.py"
            % (claimed, actual, GENERATOR))


def _check_commit(receipt, cwd, path=None):
    """(problem, note). The binding that makes a receipt evidence for ONE
    commit: `headCommit` binds to a DIGEST of the tracked content at that
    commit, not to the commit identifier itself (see
    `docs/adr/2026-08-12-handover-across-two-machines.md` and the founder
    decision in `docs/plans/2026-08-12-overnight-team-version-and-
    roadmap.md` section 2.1 that this implements). `path` (the receipt
    file itself, as passed to `verify`) is exempted from that digest by
    exact repo-relative path the same way `handover.py` exempts
    `12-handover.json`: a commit whose entire diff is the receipt file is
    the receipt announcing itself, never "the code moved". Any commit that
    bundles the receipt with anything else changes a tracked path outside
    the exemption, so it still fails this exactly as before. `path` outside
    `cwd`, or omitted, exempts nothing -- callers that pass no path (or a
    path this repository never tracks) still get an exact content match at
    HEAD, which is what an unmodified tree always was anyway."""
    code, out, err = _git(["rev-parse", "HEAD"], cwd)
    if code != 0:
        return None, ("HEAD does not resolve in %s (%s), so the commit binding was not "
                      "checked and nothing here confirms which code this receipt covers"
                      % (cwd, err.strip() or "no message"))
    current = out.strip()
    claimed = answered(receipt.get("headCommit"))
    if claimed == current:
        return None, "headCommit %s is the current head" % current[:12]
    exempt = frozenset()
    if path:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(cwd)).replace(os.sep, "/")
        if not rel.startswith(".."):
            exempt = frozenset((rel,))
    if claimed and content_binding.content_unchanged(cwd, claimed, current, exempt):
        return None, ("headCommit %s is the current head aside from its own introducing "
                      "commit(s), which changed nothing but this receipt" % claimed[:12])
    return ("headCommit %s is not the current head %s: this receipt is evidence for a commit "
            "that is no longer checked out, and the code it covered has moved"
            % (str(claimed)[:12], current[:12]), None)


def _under_excluded(rel, exclude_dirs):
    """True when `rel` (a receipt-recorded path, POSIX-style) sits inside one
    of `exclude_dirs` (also POSIX-style, relative to the same root). A path
    equal to an excluded directory, or nested under it, matches; a path that
    merely shares a string prefix (`.sbe/evidence-old` against `.sbe/evidence`)
    does not, because this compares path SEGMENTS, not characters."""
    norm = rel.replace(os.sep, "/").strip("/")
    for d in exclude_dirs or ():
        dn = str(d).replace(os.sep, "/").strip("/")
        if dn and (norm == dn or norm.startswith(dn + "/")):
            return True
    return False


def _check_covered(receipt, cwd, exclude_dirs=None):
    """(problems, note, excluded). Did the code this receipt claims to cover
    change since?

    Two ways a covered file stops being covered, and both are named separately
    because they are different findings: the bytes differ from the digest the
    receipt recorded, or the file was written after the run ended. The second
    catches a change that was made and reverted, and it is deliberately strict:
    a file touched after the evidence was made is a file the evidence did not
    see in its current state.

    `exclude_dirs`, when given, names path prefixes this receipt's coverage
    must never be judged against, even though `coveredFiles` still lists them
    faithfully (that field records what the diff actually found; this
    function decides what that finding is allowed to PROVE). This exists for
    one reason: a receipt's `coveredFiles`, computed from a diff rather than
    an explicit `--covers` list, cannot tell "code this run tested" from
    "another evidence receipt that happened to land in the same diff", and a
    receipt that regenerates routinely (every CI push re-runs the same check
    at the same `--out` path) is not a fact about the code under test. Without
    this, an unrelated receipt that merely covered the OLD bytes by diff-range
    accident fails the moment the covered receipt is refreshed: the evidence
    store poisoning itself. See docs/KNOWN-LIMITS.md ("Evidence covering
    evidence") for what excluding it does and does not close.
    """
    covered = receipt.get("coveredFiles") or []
    ended = receipt.get("endedAtEpoch")
    problems, checked, touched, excluded = [], 0, 0, 0
    for entry in covered:
        rel = answered(entry.get("path"))
        if rel is None:
            problems.append("a coveredFiles entry records no path")
            continue
        if _under_excluded(rel, exclude_dirs):
            excluded += 1
            continue
        full = rel if os.path.isabs(rel) else os.path.join(cwd, rel)
        if not os.path.exists(full):
            problems.append("covered file %s no longer exists" % rel)
            continue
        checked += 1
        recorded = entry.get("sha256")
        actual = _sha256_file(full)
        if recorded and actual != recorded:
            problems.append("covered file %s now hashes to %s, not the %s the receipt "
                            "recorded: the code changed after the evidence was made"
                            % (rel, str(actual)[:12], str(recorded)[:12]))
            continue
        if isinstance(ended, (int, float)):
            try:
                mtime = os.path.getmtime(full)
            except OSError:
                problems.append("covered file %s could not be stat'd, so nothing confirms it "
                                "is unchanged" % rel)
                continue
            if mtime > ended:
                touched += 1
                problems.append("covered file %s was written after the run ended (%s vs %s); "
                                "a file touched after the evidence was made is a file the "
                                "evidence did not see"
                                % (rel, _iso(mtime), _iso(ended)))
    note = ("%d covered file(s) re-hashed against the receipt, %d written after the run"
            % (checked, touched))
    if excluded:
        note += (", %d under an excluded path (the evidence store) never judged as coverage"
                 % excluded)
    return problems, note, excluded


def verify(path, cwd=None, exclude_dirs=None):
    """PASS, FAIL or NO-DATA over one receipt, with the reasons it inspected.

    ORDER, and it is deliberate. Every FAIL condition is evaluated first, then
    the dirty-tree condition. A broken claim is FAIL even when the receipt is
    advisory, because "this receipt is wrong" outranks "this receipt was never
    authoritative". Only a receipt with nothing broken about it can reach the
    NO-DATA that a dirty tree earns, and only a clean one can reach PASS.

    `exclude_dirs`, forwarded to `_check_covered` unchanged, names path
    prefixes (relative to `cwd`) whose `coveredFiles` entries are recorded but
    never judged: see `_check_covered` for why. Every existing caller that
    does not pass it keeps today's behavior exactly, because the default is
    an empty exclusion, not a guessed one.
    """
    cwd = os.path.abspath(cwd or os.getcwd())
    inspected = ["receipt file %s" % path]
    try:
        receipt = load(path)
    except ReceiptUnreadable as exc:
        return {"verdict": "FAIL", "reasons": [str(exc)], "inspected": inspected,
                "receipt": None, "trust": None, "trustWhy": None}

    problems, notes = [], []

    schema_problem = _check_schema(receipt)
    inspected.append("schemaVersion")
    if schema_problem:
        # Stop here: every later check reads fields whose meaning is exactly
        # what the schema version defines, so continuing would be guessing.
        return {"verdict": "FAIL", "reasons": [schema_problem], "inspected": inspected,
                "receipt": receipt, "trust": None,
                "trustWhy": "an unreadable schema has no trust level"}

    inspected.append("%d required field(s)" % len(
        _fields_for(answered(receipt.get("schemaVersion")), REQUIRED_FIELDS)))
    required_problem = _check_required(receipt)
    if required_problem:
        problems.append(required_problem)

    inspected.append("the declared check kind(s)")
    kinds_problem = _check_kinds(receipt)
    if kinds_problem:
        problems.append(kinds_problem)

    inspected.append("the registered check binding against %s"
                     % os.path.join(cwd, check_registry.REGISTRY_REL))
    registered_problems, registered_note = _check_registered(receipt, cwd)
    problems.extend(registered_problems)
    if registered_note:
        notes.append(registered_note)

    inspected.append("the runId seal over %d run fact(s)" % len(
        _fields_for(answered(receipt.get("schemaVersion")), SEALED_FIELDS)))
    seal_problem = _check_seal(receipt)
    if seal_problem:
        problems.append(seal_problem)

    inspected.append("the current git HEAD in %s" % cwd)
    commit_problem, commit_note = _check_commit(receipt, cwd, path)
    if commit_problem:
        problems.append(commit_problem)
    if commit_note:
        notes.append(commit_note)

    covered = receipt.get("coveredFiles") or []
    inspected.append("%d covered file(s)" % len(covered))
    covered_problems, covered_note, covered_excluded = _check_covered(receipt, cwd, exclude_dirs)
    problems.extend(covered_problems)
    notes.append(covered_note)

    level, why = trust_level(receipt)

    if problems:
        return {"verdict": "FAIL", "reasons": problems, "inspected": inspected,
                "receipt": receipt, "trust": level, "trustWhy": why}

    if receipt.get("workingTreeDirty") is not False:
        detail = receipt.get("workingTreeDetail") or "the tree state was not recorded"
        return {"verdict": "NO-DATA", "inspected": inspected, "receipt": receipt,
                "trust": level, "trustWhy": why,
                "reasons": ["the working tree was dirty or unreadable when this ran (%s), so "
                            "the receipt covers a state that was never committed and nobody "
                            "else can reproduce. That is advisory, and advisory is NO-DATA "
                            "here rather than a pass" % detail]}

    if not covered:
        return {"verdict": "NO-DATA", "inspected": inspected, "receipt": receipt,
                "trust": level, "trustWhy": why,
                "reasons": ["the receipt covers no file at all (%s), so nothing here can "
                            "detect that the code changed after the evidence was made"
                            % (answered(receipt.get("coveredFilesSource")) or "no source "
                               "recorded")]}

    if covered_excluded >= len(covered):
        return {"verdict": "NO-DATA", "inspected": inspected, "receipt": receipt,
                "trust": level, "trustWhy": why,
                "reasons": ["every one of this receipt's %d covered file(s) (%s) sits under an "
                            "excluded path and was never judged as coverage, so nothing here "
                            "grounds this receipt in code that was actually inspected"
                            % (len(covered),
                               answered(receipt.get("coveredFilesSource")) or "no source "
                               "recorded")]}

    return {"verdict": "PASS", "reasons": notes, "inspected": inspected, "receipt": receipt,
            "trust": level, "trustWhy": why}


def render(receipt, path):
    """The human-readable form. Names the trust level first and unconditionally,
    because a reader who takes a LOCAL-ADVISORY receipt for an authoritative one
    has been misled by the layout rather than by the content."""
    level, why = trust_level(receipt)
    out = []
    out.append("receipt        %s" % path)
    out.append("trust          %s (%s)" % (level, why))
    out.append("generator      %s %s, schema %s"
               % (receipt.get("generator"), receipt.get("generatorVersion"),
                  receipt.get("schemaVersion")))
    argv = receipt.get("argv") or []
    out.append("command        %s" % (" ".join(str(a) for a in argv) if argv
                                      else "none recorded"))
    redactions = receipt.get("argvRedactions")
    out.append("argv redacted  %s" % (
        "not recorded" if redactions is None else
        "no (0 secret-shaped token(s) matched; the command above is verbatim)" if redactions == 0
        else "yes, %d secret-shaped token(s); the command above is NOT verbatim" % redactions))
    check_id = registered_check_id(receipt)
    out.append("check id       %s" % (check_id or "none: FREE FORM, so this receipt is "
                                                  "advisory and satisfies no required check"))
    if check_id:
        binding = receipt.get("checkBinding") or {}
        out.append("check kind     %s" % (receipt.get("checkKind") or "not recorded"))
        out.append("check spec     sha256 %s from %s"
                   % (receipt.get("checkSpecSha256"),
                      binding.get("registryPath") or "not recorded"))
        for entry in (binding.get("runnerFiles") or []):
            out.append("  runner %-12s %s" % (str(entry.get("sha256"))[:12],
                                              entry.get("path")))
        out.append("check env      %s" % (binding.get("environmentNote") or "not recorded"))
    out.append("check source   %s" % (receipt.get("checkIdSource")
                                      or "not recorded (this receipt predates the registry)"))
    kinds = sorted(declared_kinds(receipt))
    out.append("check kinds    %s" % (", ".join(kinds) if kinds else "none declared"))
    out.append("kind source    %s" % (receipt.get("checkKindsSource")
                                      or "not recorded (this receipt predates the field)"))
    out.append("exit code      %s" % receipt.get("exitCode"))
    out.append("ran            %s to %s (%s s)"
               % (receipt.get("startedAt"), receipt.get("endedAt"),
                  receipt.get("durationSeconds")))
    repo = receipt.get("repository") or {}
    out.append("repository     %s" % (repo.get("remote") or repo.get("remoteNote")
                                      or "not recorded"))
    out.append("base commit    %s" % (receipt.get("baseCommit") or "not recorded"))
    out.append("head commit    %s" % (receipt.get("headCommit") or "not recorded"))
    out.append("tree at run    %s" % ("dirty" if receipt.get("workingTreeDirty")
                                      else "clean" if receipt.get("workingTreeDirty") is False
                                      else "not recorded"))
    tools = receipt.get("toolVersions") or {}
    out.append("tool versions  python %s, sbe %s"
               % (tools.get("python", "not recorded"), tools.get("sbe", "not recorded")))
    out.append("environment    %s" % (receipt.get("environment") or "not recorded"))
    out.append("stdout         sha256 %s over %s byte(s)"
               % (receipt.get("stdoutSha256"), receipt.get("stdoutBytes")))
    out.append("stderr         sha256 %s over %s byte(s)"
               % (receipt.get("stderrSha256"), receipt.get("stderrBytes")))
    out.append("output policy  %s" % (receipt.get("outputPolicy") or "not recorded"))
    out.append("runId          %s" % receipt.get("runId"))
    out.append("covers         %s (%s)"
               % (receipt.get("coveredFilesSource") or "not recorded",
                  "%d file(s)" % len(receipt.get("coveredFiles") or [])))
    for entry in (receipt.get("coveredFiles") or []):
        out.append("  %-12s %s" % (str(entry.get("sha256"))[:12], entry.get("path")))
    return "\n".join(out)


def _split_command(rest):
    """(our arguments, the command after the first bare `--`).

    Split by hand rather than left to argparse: `--` is the only unambiguous
    boundary between flags meant for this wrapper and flags meant for the
    command being run, and argparse silently eats the first one.
    """
    if "--" in rest:
        cut = rest.index("--")
        return rest[:cut], rest[cut + 1:]
    return list(rest), []


def _parser():
    parser = argparse.ArgumentParser(
        prog="sbe evidence",
        description="Generate a receipt by running the command, verify one against the "
                    "current commit, or show one with its trust level.")
    sub = parser.add_subparsers(dest="sub")

    run = sub.add_parser("run", help="run a command and write the receipt it earned")
    run.add_argument("--out", required=True, help="where to write the receipt")
    run.add_argument("--covers", action="append", default=[],
                     help="a file this run is evidence for; repeatable. Without it, the "
                          "files changed between base and head are used")
    run.add_argument("--kind", action="append", default=[], choices=list(CHECK_KIND_NAMES),
                     dest="kind",
                     help="the obligation this run is evidence for (%s); repeatable. Without "
                          "it the receipt declares no check kind and clears no obligation. "
                          "This is a declaration, not a proof: the wrapper runs the command, "
                          "it does not inspect what the command checks"
                          % "|".join(CHECK_KIND_NAMES))
    run.add_argument("--check", default=None, dest="check",
                     help="run the check registered under this id in .sbe/checks.yml. The "
                          "executable, the arguments, the working directory and the covered "
                          "paths all come from the registry and NOTHING on this command line "
                          "replaces any of them. Without it the run is free form: advisory "
                          "evidence that satisfies no required policy check")
    run.add_argument("--checks-file", default=None, dest="checks_file",
                     help="read the registry from this path instead of <cwd>/.sbe/checks.yml")
    run.add_argument("--base", default=None, help="the commit to diff from for --covers")
    run.add_argument("--head", default="HEAD", help="the commit this receipt is made against")
    run.add_argument("--cwd", default=".", help="the repository to run in")
    run.add_argument("--timeout", type=float, default=None,
                     help="kill the command and refuse the receipt if it runs longer than "
                          "this many seconds. No default: a silent one would kill a "
                          "legitimate long-running suite")

    ver = sub.add_parser("verify", help="check a receipt against the current commit")
    ver.add_argument("receipt")
    ver.add_argument("--cwd", default=".", help="the repository to verify against")
    ver.add_argument("--json", action="store_true", help="machine-readable output")
    ver.add_argument("--strict", action="store_true",
                     help="make NO-DATA block as well, for protected CI")

    show = sub.add_parser("show", help="print a receipt and its trust level")
    show.add_argument("receipt")
    show.add_argument("--cwd", default=None,
                      help="anchor a relative receipt path here instead of the current "
                           "directory; an already-absolute receipt path is unaffected")
    return parser


def main(rest, exit_ok=0, exit_failed=1, exit_usage=2):
    """The `sbe evidence` surface. Exit codes come from the caller so this module
    never disagrees with the CLI's documented table."""
    ours, command = _split_command(list(rest))
    parser = _parser()
    if not ours:
        parser.print_help(sys.stderr)
        return exit_usage
    try:
        args = parser.parse_args(ours)
    except SystemExit as exc:
        # argparse already answered: -h/--help printed the usage the caller
        # asked for and raised 0, and help is not an error. Anything nonzero
        # is a refused flag or argument. Folding both into exit_usage made
        # every explicit help request on this surface exit 2.
        return exit_ok if exc.code in (0, None) else exit_usage
    if not getattr(args, "sub", None):
        parser.print_help(sys.stderr)
        return exit_usage

    if args.sub == "run":
        if not command and not args.check:
            sys.stderr.write("sbe evidence run: no command given. Usage: sbe evidence run "
                             "--check <id> --out receipt.json, or sbe evidence run --out "
                             "receipt.json -- <command...>. This wrapper runs the command "
                             "itself; there is nothing here that accepts a duration or an "
                             "exit code you typed.\n")
            return exit_usage
        try:
            receipt = generate(os.path.abspath(args.cwd), command, base=args.base,
                               head=args.head, covers=args.covers, timeout=args.timeout,
                               kinds=args.kind, check_id=args.check,
                               registry_path=args.checks_file)
        except ReceiptUnreadable as exc:
            sys.stderr.write("sbe evidence run: no receipt written. %s\n" % exc)
            return exit_failed
        try:
            write_receipt(receipt, args.out)
        except OSError as exc:
            # One message for both ways this write fails (the parent directory
            # could not be created, or the file itself could not be opened):
            # `write_receipt` is the one place either happens now, so this is
            # the one place that reports either happening.
            sys.stderr.write("sbe evidence run: the command ran but the receipt could not be "
                             "written to %s: %s. Treat the run as unrecorded.\n"
                             % (args.out, exc))
            return exit_failed
        level, why = trust_level(receipt)
        sys.stdout.write(
            "\nsbe evidence run: %s\n"
            % ("registered check %s (kind %s, specification digest %s)"
               % (receipt["checkId"], receipt["checkKind"],
                  str(receipt["checkSpecSha256"])[:12]) if receipt["checkId"]
               else "FREE FORM run: no registered check, so this receipt is advisory and "
                    "satisfies no required policy check"))
        sys.stdout.write(
            "sbe evidence run: receipt written to %s. Trust %s (%s). Command exited %d in "
            "%.3fs, over %d covered file(s) from %s. Declared check kind(s): %s. stdout and "
            "stderr are recorded as digests only. argv held %d secret-shaped token(s) and "
            "%s.\n"
            % (args.out, level, why, receipt["exitCode"], receipt["durationSeconds"],
               len(receipt["coveredFiles"]), receipt["coveredFilesSource"],
               ", ".join(receipt["checkKinds"]) or "none, so this receipt clears no design, "
                                                  "gate or score obligation",
               receipt["argvRedactions"],
               "was recorded verbatim" if receipt["argvRedactions"] == 0
               else "was redacted before it was written"))
        # The wrapper's own exit code is the COMMAND's, so a failing command
        # cannot be laundered into a passing evidence step by the fact that a
        # receipt got written about it.
        return exit_failed if receipt["exitCode"] != 0 else exit_ok

    if args.sub == "verify":
        result = verify(args.receipt, cwd=os.path.abspath(args.cwd))
        if args.json:
            sys.stdout.write(json.dumps({
                "tool": "sbe evidence verify",
                "toolVersion": version(),
                "schemaVersion": EVIDENCE_SCHEMA_VERSION,
                "receipt": args.receipt,
                "verdict": result["verdict"],
                "trust": result["trust"],
                "trustWhy": result["trustWhy"],
                "inspected": result["inspected"],
                "reasons": result["reasons"],
            }, indent=2, sort_keys=True) + "\n")
        else:
            sys.stdout.write("%-8s %s\n" % (result["verdict"], args.receipt))
            sys.stdout.write("  inspected: %s\n" % "; ".join(result["inspected"]))
            if result["trust"]:
                sys.stdout.write("  trust:     %s (%s)\n" % (result["trust"],
                                                             result["trustWhy"]))
            for reason in result["reasons"]:
                sys.stdout.write("  %s\n" % reason)
        if result["verdict"] == "FAIL":
            return exit_failed
        if result["verdict"] == "NO-DATA" and args.strict:
            return exit_failed
        return exit_ok

    if args.sub == "show":
        receipt_path = cwd_mod.resolve_relative(args.receipt, args.cwd)
        try:
            receipt = load(receipt_path)
        except ReceiptUnreadable as exc:
            sys.stderr.write("sbe evidence show: %s\n" % exc)
            return exit_failed
        sys.stdout.write(render(receipt, receipt_path) + "\n")
        sys.stdout.write("\nsbe evidence show prints what the receipt SAYS. It checks nothing: "
                         "run `sbe evidence verify` for a verdict.\n")
        return exit_ok

    parser.print_help(sys.stderr)
    return exit_usage
