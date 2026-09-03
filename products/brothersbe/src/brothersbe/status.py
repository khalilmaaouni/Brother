"""One truthful, blocker-first answer to "where does this change stand", so an
engineer never has to assemble it from six commands by hand. Adoption friction
is an assurance defect: a control nobody runs protects nothing.

THE KILL CRITERION THIS MODULE IS BUILT AROUND, quoted from the wave 8 spec
because it is the whole point: if a truthful summary cannot be produced
without this file running the suites (`tools/test_sbe_*.py`,
`evals/run_evals.py`) or becoming a second gate runner (`sbe_design.py`,
`sbe_gate.py`, `sbe_score.py`), it stops and says so rather than run them.
Nothing in this file starts a subprocess and nothing in it computes a NEW
verdict over source code. It reads state other commands already recorded:
`sbe evidence` receipts, the `sbe task` registry, an intake file, a
disposition file, and the diff itself (read the same way `sbe impact` already
reads it, by importing that module rather than re-typing the git plumbing).
A design, gate or score FAIL surfaced here is read out of an EXISTING,
already-verified evidence receipt someone generated with `sbe evidence run`;
a check this project owns but nobody ran a receipt for is reported under
MISSING EVIDENCE, never invented as a FAIL this module discovered on its own.

WHERE THIS MODULE LOOKS, stated because a store this module does not check is
a store whose absence must never read as clean:

  intake            <root>/00-intake.json, or <dossier>/00-intake.json per
                     discovered dossier when the flat file above is absent
  disposition       <root>/disposition.json, or <dossier>/disposition.json,
                     matching whichever intake was actually read
  evidence store    <root>/.sbe/evidence/  (recursively, every *.json)
  task registry     <root>/.sbe/tasks.json

The evidence store and task registry are always shared and root-level: read
once, the same way, for the whole repository, exactly as `sbe status --team`
already reads them. Intake and disposition are read flat at `<root>` FIRST,
the same single-dossier convention `tools/test_sbe_impact.py`'s own fixtures
write to, and when that flat `00-intake.json` exists this module's output is
unchanged from every earlier version of this file: the flat layout always
wins and dossier discovery never runs. Only when the flat file is ABSENT does
this module walk for dossiers, through the SAME `_design_roots`/
`_team_changes` machinery `build_team_report` below already uses (the
default `design/` root, plus any `.sbe/team-profile.json` `designRoots`
entry that resolves inside the repository; an entry that would escape the
repository is refused and named as a merge blocker, never silently walked).
Each discovered dossier's own `00-intake.json` and `disposition.json` are
then read in the flat file's place, one target per dossier, and every
finding they produce is labeled with the dossier's name so a reader always
knows which change it came from. A repository that writes neither the flat
files nor any dossier still reads NO-DATA here, exactly as before.

ACTIVE CONFLICTS reuses wave 5's overlap scan by calling `tasks.load_registry`,
`tasks.open_tasks` and `tasks.claims_overlap` directly, the same three
functions `sbe task check` itself calls; there is no second copy of the
overlap rule in this file.

HOW A DESIGN/GATE/SCORE OBLIGATION IS CLEARED, and this changed because the
old answer was a bypass. A receipt whose `sbe evidence verify` verdict is PASS
(the receipt itself is trustworthy: sealed, current, every covered file intact)
clears an obligation only when the receipt DECLARES that kind in its own
`checkKinds` field, read through `evidence.declared_kinds`, which is the single
reader of that field. Nothing here looks at the recorded command line any more.
It used to: the kind was inferred by substring-matching the joined argv, so a
receipt recording `/bin/cat tests/test_design_of_gate_score.txt` named three
obligations and cleared all three, for a command that ran no check at all. That
was reproduced, not theorized.

A receipt that declares no kind is NO-DATA for obligation purposes, never a
silent pass, and the three shapes of that (a receipt older than the field, a
field that does not parse as a kind list, and an honest run that declared
nothing) are each named where MISSING EVIDENCE is reported, so a reader is
never told "no evidence exists" when the truth is "evidence exists and says
nothing about which check it was". A receipt whose recorded `exitCode` is
nonzero is a MERGE BLOCKER whether or not it declares a kind: the run was made,
it failed, and evidence of that failure already exists. A receipt whose own
verify() is NO-DATA (advisory: a dirty tree at generation time, or no covered
file) is neither a broken claim nor clean evidence, and is not otherwise pinned
in a section; it is counted in the evidence scope note.

LANE B-004: on the CR-06 discovered-dossier path, "clears an obligation" is
scoped PER CHANGE, not read off one repository-wide set. The evidence store
is still scanned once, shared and root-level, exactly as stated above; what
changed is which dossier a given receipt is allowed to clear. A receipt is
attributable to a dossier when a registry task record belonging to that
dossier's own `08-plan.json` claims it by `evidenceId`, or when every one of
its `coveredFiles` falls inside a path that dossier's plan `owns`. Before
this, a gate receipt generated for change A cleared change B's MISSING
EVIDENCE obligation too, purely because both dossiers consulted the same
global `kindsCovered` set; that was reproduced, not theorized, with a
two-dossier fixture. A receipt attributable to no dossier at all is
UNSCOPED: on the flat single-dossier layout it still clears the obligation
exactly as it always has (this scoping never runs there, so that output
stays byte-identical), but on the discovered-dossier path it clears no
dossier's obligation, and the MISSING EVIDENCE finding says so by name
("a matching receipt exists but is unscoped") rather than staying silent
about a receipt that plainly exists in the store. See
`_dossier_evidence_attribution`.

WHAT A DECLARED KIND IS NOT: a proof that the command performed that check.
`sbe evidence run --kind gate` binds the declaration to a run that actually
happened and seals it, so it cannot be typed into a receipt afterwards, and the
recorded argv sits beside it for a reader to compare. An operator who declares
a kind over a command that checks nothing has written a false statement rather
than exploited an inference, which is the whole of the improvement and is
stated here rather than oversold.

Python floor is 3.9: no match statements, no `X | Y` annotations. Standard
library only. Maturity: INTERNAL-EVAL, exercised on this repository's
fixtures and on no other estate.
"""
import io
import json
import os
import time

from . import SCHEMA_VERSION, version
from . import evidence as evidence_mod
from . import impact as impact_mod
from . import lifecycle
from . import tasks as tasks_mod
from . import work as work_mod
from .impact import DiffUnavailable, _git, _tier_index  # noqa: E402  (the same private
        # helpers evidence.py and decisions.py already reuse: `_git` for the same plumbing,
        # `_tier_index` (Ledger 12's `_declared_tier`) for the same tier-order check
        # `decisions.py` already imports it for, rather than a second copy of TIERS here)

#: How each kind reads in a report, and the command line that would record
#: evidence for it. The NAMES are not defined here: they come from
#: `evidence.CHECK_KIND_NAMES`, the vocabulary receipts are written in, so this
#: module can never recognize a kind the generator cannot write or miss one it
#: can. A name with no row below still appears, under a generic label, because
#: a kind this table has not been taught about is still an obligation and
#: dropping it would be the silent gap this project refuses everywhere else.
CHECK_KIND_DETAIL = {
    "design": ("design completeness check", "bin/sbe design --strict <dossier>"),
    "gate": ("hard gate", "bin/sbe gate <dossier>"),
    "score": ("scored surface", "bin/sbe score --strict <dossier>"),
}

CHECK_KINDS = tuple(
    (kind,) + CHECK_KIND_DETAIL.get(
        kind, ("%s check" % kind, "the command that runs the %s check" % kind))
    for kind in evidence_mod.CHECK_KIND_NAMES)

INTAKE_REL = "00-intake.json"
DISPOSITION_REL = "disposition.json"

SECTION_NAMES = ("BROKEN CLAIMS", "MERGE BLOCKERS", "ACTIVE CONFLICTS", "MISSING EVIDENCE",
                 "COMPLETED EVIDENCE")


def _iso(epoch):
    """ISO 8601 in UTC, to the second, with an explicit Z: the same spelling
    the evidence receipts and the task registry use."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _git_head(root):
    """The current HEAD sha in `root`, or None when git cannot answer for it.

    None is a real answer, never guessed at: every caller that needs a head
    commit to compare against treats None as NO-DATA for that comparison
    rather than skipping the comparison in silence.
    """
    try:
        code, out, _err = _git(["rev-parse", "HEAD"], root)
    except OSError:
        return None  # sbe: allow-silent every caller renders None as NO-DATA with a reason, per this docstring; a machine with no git gets an honest absence, not a crash
    return out.strip() if code == 0 and out.strip() else None


#: The dossier's own record files that name the commit they were produced
#: against (`headSha`), most recent obligation first: P5, `sbe status`'s
#: POSSIBLY BEHIND note reads exactly these two, the same two files
#: `_approval_ladder_candidate` and `_review_ladder_check` already read for
#: sha-equality staleness, only here compared BY DATE instead.
_DATED_RECORD_RELS = ("10-approval.json", "11-review.json")


def _commit_epoch(root, sha):
    """The committer-date (Unix epoch seconds) of `sha` in `root`, or None
    when git cannot resolve it (unknown sha, not a git repository, git
    missing). None is a real answer, read as "cannot compare" by every
    caller, never guessed at."""
    try:
        code, out, _err = _git(["log", "-1", "--format=%ct", sha], root)
    except OSError:
        return None  # sbe: allow-silent every caller renders None as "cannot compare", per this docstring; a machine with no git gets an honest absence, not a crash
    out = out.strip()
    if code != 0 or not out.isdigit():
        return None
    return int(out)


def _newest_record_binding(root, doss):
    """(sha, relPath) for whichever of `_DATED_RECORD_RELS` at `doss` names
    a `headSha` that resolves to the MOST RECENT commit, or (None, None)
    when none of them exist, parse, or name a commit git can date. A record
    bound to an unresolvable sha (never fetched, or hand-typed in a test) is
    silently excluded rather than treated as either the newest or the
    oldest: a date this function cannot read is not a fact it can compare.
    """
    dated = []
    for rel in _DATED_RECORD_RELS:
        payload = _read_json_or_none(os.path.join(doss, rel))
        if not isinstance(payload, dict):
            continue
        sha = payload.get("headSha")
        if not isinstance(sha, str) or not sha.strip():
            continue
        sha = sha.strip()
        epoch = _commit_epoch(root, sha)
        if epoch is not None:
            dated.append((epoch, sha, rel))
    if not dated:
        return None, None
    dated.sort(key=lambda item: item[0])
    _epoch, sha, rel = dated[-1]
    return sha, rel


def _possibly_behind_note(root, doss, head, prefix=""):
    """P5: `lifecycle.reduce_next_action` has no fact anywhere that compares
    the newest DATE a dossier's own records were produced against the
    newest date HEAD actually moved to; the only existing analog is
    `bbstatus._stale_sentence`, and that one compares two shas for a status
    POST, never a date, and never names what moved. This is the one place
    `sbe status` names it, for a HUMAN reading a report rather than a CI
    build key.

    None when there is nothing to compare (`head` unresolved, no dated
    record at `doss`) or when the newest dated record already IS head (the
    fresh case: nothing has landed since). Otherwise a plain sentence naming
    which record is behind, the commit it is bound to, and the commit range
    that landed since it -- NEVER a bare boolean, so a reader is told WHICH
    commits moved rather than merely that something did.

    `sha..head` reads as empty (not "no commits") when `sha` is not an
    ancestor of `head` (a rewritten history, a detached fixture) rather than
    raising: `git log` answers that shape with an ordinary empty result, not
    an error, so this reads it as nothing new to name rather than crashing
    the whole report over one unreachable record.
    """
    if not head:
        return None
    sha, rel = _newest_record_binding(root, doss)
    if not sha or sha == head:
        return None
    try:
        code, out, _err = _git(["log", "--format=%h %s", "%s..%s" % (sha, head)], root)
    except OSError:
        return None  # sbe: allow-silent the caller reads None as "nothing to compare", per this docstring; a machine with no git gets an honest absence, not a crash
    if code != 0:
        return None
    commits = [line for line in out.splitlines() if line.strip()]
    if not commits:
        return None
    shown = commits[:5]
    more = "" if len(commits) <= 5 else " (+%d more)" % (len(commits) - 5)
    return ("%spossibly behind: %s is bound to %s, but %d commit(s) have landed on HEAD "
           "since: %s%s" % (prefix, rel, sha[:12], len(commits), "; ".join(shown), more))


#: H3: evidence binds to a commit, never to what is RUNNING. `bbstatus.
#: _stale_sentence` and `read_readiness` below both compare a recorded head
#: to the CURRENT checkout's head; neither says anything about a deployed
#: environment, which can sit on an older commit than either. This is the
#: one place a deployed ref is named and compared, deliberately beside those
#: two rather than a third copy of the comparison.
DEPLOYED_REF_REL = os.path.join(".sbe", "deployed-ref")

#: The env override, same shape as SBE_DOSSIER_ROOT (sbe_design.py) and
#: SBE_PROFILE (sbe_profile.py): a CI runner or a deploy hook names its own
#: environment without writing into the checkout. Wins over the file when
#: both are present, since an environment naming it is doing so for THIS
#: run, deliberately, while the file is a standing record.
DEPLOYED_REF_ENV = "SBE_DEPLOYED_REF"


def _read_deployed_ref(root):
    """(ref, source, warning) for the deployed ref named at `root`.

    `ref` is the stripped commit this estate says is actually running, or
    None when nothing usable is named. `source` names where it came from
    (the environment variable, or the file path), for the DRIFT/CLEAN
    sentence and the NO-DATA detail alike. `warning` is a plain sentence
    naming why an existing file could not be read as one ref, e.g. more
    than one non-blank line: DEPLOYED_REF_REL is a ONE LINE record by
    convention, and a file that is not that shape is reported by name
    rather than guessed at (first line wins, or silently ignored).

    An absent file, an unreadable file, or a file with no non-blank line
    are all the same "nothing named" case as no file at all -- never a
    warning, since there is nothing there to have gotten wrong.
    """
    env = os.environ.get(DEPLOYED_REF_ENV)
    if env is not None and env.strip():
        return env.strip(), "the %s environment variable" % DEPLOYED_REF_ENV, None

    path = os.path.join(root, DEPLOYED_REF_REL)
    if not os.path.isfile(path):
        return None, None, None
    try:
        with io.open(path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError as exc:
        return None, None, "deployed ref file at %s did not read: %s" % (path, exc)
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return None, None, None
    if len(lines) > 1:
        return None, None, ("deployed ref file at %s carries %d lines; it must name exactly "
                            "one ref, so nothing was read" % (path, len(lines)))
    return lines[0], path, None


def write_deployed_ref(root, ref):
    """Write `ref` as the single line at DEPLOYED_REF_REL under `root`,
    creating `.sbe/` when absent. (ok, detail). `ok` is False only when
    `ref` is blank or the write itself failed (permissions, an
    unmakeable parent); this is a plain recording, never a second gate, so
    it never refuses a ref merely because it looks unfamiliar or does not
    resolve in this checkout -- a deployed environment can be ahead of, or
    on a fork of, the commit graph this clone can see.
    """
    ref = (ref or "").strip()
    if not ref:
        return False, "no ref given; nothing written"
    path = os.path.join(root, DEPLOYED_REF_REL)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(ref + "\n")
    except OSError as exc:
        return False, "could not write %s: %s" % (path, exc)
    return True, "deployed ref %s written to %s" % (ref, path)


def deployment_drift(root, head):
    """The H3 comparison. Returns {"verdict": "DRIFT"|"CLEAN"|"NO-DATA",
    "detail": <sentence>}, the same two-key shape `read_readiness` returns,
    never a bare boolean: evidence proves a COMMIT is sound; nothing else in
    this repository says whether that commit is what is actually running
    anywhere, and this is the one place that gap is named.

    "NO-DATA" covers every case this cannot compare, each named in the
    detail rather than folded together: no deployed ref is named at all
    (deployment tracking is not configured here, which is a fact about this
    estate, never a failure), a malformed deployed-ref file (the warning
    from `_read_deployed_ref`), or a `head` this run could not resolve.
    "DRIFT" and "CLEAN" both name BOTH refs, so a reader never has to take
    the verdict word on faith.
    """
    ref, source, warning = _read_deployed_ref(root)
    if warning:
        return {"verdict": "NO-DATA", "detail": warning}
    if not ref:
        return {"verdict": "NO-DATA",
                "detail": ("no deployed ref is named for %s (set %s, or write one line to "
                          "%s); deployment tracking is not configured here"
                          % (root, DEPLOYED_REF_ENV, os.path.join(root, DEPLOYED_REF_REL)))}
    if not head:
        return {"verdict": "NO-DATA",
                "detail": ("a deployed ref (%s, from %s) is named, but HEAD could not be "
                          "resolved to compare it against" % (ref, source))}
    if ref == head:
        return {"verdict": "CLEAN",
                "detail": "the deployed ref from %s (%s) matches HEAD (%s)" % (source, ref, head)}
    return {"verdict": "DRIFT",
            "detail": ("DRIFT: the deployed ref named at %s is %s, but the evidence in this "
                      "report binds to HEAD %s; what is running is not what was verified"
                      % (source, ref, head))}


def read_readiness(path, current_head=None):
    """Read a CI readiness artifact `scripts/local-gates.sh` wrote (see its
    `emit_readiness_artifact`, `.sbe/readiness/<headCommit>.json`) and return
    a verdict dict, NEVER a silent PASS: this function only reads and
    validates the artifact, it is not a second gate runner and does not
    grade `batteryState`/`requiredProof` itself -- a caller feeds those
    straight into `lifecycle.reduce_readiness` alongside its own
    `dossierHeadCommit`/`accountableHuman`/`noDataPermitted`, unchanged.

    Returns {"verdict": "NO-DATA"|"OK", "detail": <sentence>, "data": <dict
    or None>}. `verdict` is "NO-DATA" for every one of: the file is absent,
    the file did not read or did not parse as JSON, the parsed document
    carries no `headCommit`, or the artifact's `headCommit` is STALE (does
    not match the current HEAD) -- the last of these prints exactly the same
    `STALE: status head <artifact-head> is not the current head <real-head>;
    not posted` sentence `bbstatus._stale_sentence` prints on its own
    posting path, so a reader sees one wording for "this status describes a
    commit that is no longer HEAD" on either host.

    `current_head` is the real HEAD to compare the artifact against;
    defaults to `_git_head(os.getcwd())` so an ordinary caller need not
    compute it, and a test can inject a fixed value without a real git
    checkout. A `current_head` this function cannot resolve (None, same as
    `_git_head`'s own "cannot answer" case) SKIPS the staleness check rather
    than refusing a comparison it has no second half for.
    """
    if not path or not os.path.isfile(path):
        return {"verdict": "NO-DATA",
                "detail": "no readiness artifact at %s" % path, "data": None}
    try:
        with io.open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return {"verdict": "NO-DATA",
                "detail": "readiness artifact at %s did not read as JSON: %s" % (path, exc),
                "data": None}
    if not isinstance(data, dict) or not data.get("headCommit"):
        return {"verdict": "NO-DATA",
                "detail": "readiness artifact at %s has no headCommit" % path, "data": None}

    real_head = current_head if current_head is not None else _git_head(os.getcwd())
    artifact_head = data.get("headCommit")
    if real_head and artifact_head != real_head:
        return {"verdict": "NO-DATA",
                "detail": ("STALE: status head %s is not the current head %s; not posted"
                          % (artifact_head, real_head)),
                "data": None}
    return {"verdict": "OK",
            "detail": "readiness artifact at %s matches HEAD %s" % (path, artifact_head),
            "data": data}


def _receipt_kinds(receipt):
    """Which of design/gate/score this receipt DECLARES, read from its own
    `checkKinds` field and from nothing else.

    One line of delegation on purpose: `evidence.declared_kinds` is the single
    reader of that field, so this module cannot drift into a second
    interpretation of it. What this function no longer does is the point. It
    used to substring-match the joined argv, which meant a receipt for
    `/bin/cat tests/test_design_of_gate_score.txt` cleared the design, gate and
    score obligations at once, on a command that ran no check. A receipt that
    declares nothing now clears nothing, and `_scan_evidence` records WHY so
    the absence is reported rather than assumed.
    """
    return evidence_mod.declared_kinds(receipt)


def _declared_tier(raw_intake, computed_tier):
    """Ledger 12: the tier `tools/sbe_design.py`'s own override check would
    treat as authoritative for this intake, read from the SAME two fields
    it reads (`tier`, `override`, beside `override_reason`), rather than
    the always-recomputed value `impact.read_intake` returns.

    `impact.read_intake` re-derives the tier from `answers` on purpose (L15:
    a hand-edited `tier` field is an edit, not an override, and must never
    silently pass), and this module keeps trusting that recomputed floor
    everywhere else. A DOCUMENTED override changes what the human declared:
    `sbe design --strict` already accepts a `tier` field that differs from
    the computed one once `override` names that SAME tier and
    `override_reason` carries an actual reason, and prints the OVERRIDDEN
    tier from then on, never the computed one. Before this function, status
    kept printing (and gating MISSING EVIDENCE on) the computed tier even
    after a documented, valid override, so a change `sbe design` already
    treats as T2 could read here as the T0 its raw answers alone compute.

    Returns `computed_tier` unchanged unless `raw_intake["tier"]` and
    `raw_intake["override"]` agree with each other, both are non-None, and
    `override_reason` is a non-empty string: exactly the shape
    `sbe design`'s own override branch requires before IT stops printing
    the computed tier. This function does not replicate sbe_design's own
    reviewability threshold on the reason's word/character count (that
    finer-grained gate is a control this module does not own, by the same
    "never a second gate runner" law its own module docstring states); a
    reason too short for `sbe design --strict` to accept still reads as
    documented here, and `sbe design` remains the one place that refuses
    it. A missing, mismatched, or blank-reasoned override reads as no
    override at all and falls back to `computed_tier`, never blocking on it
    the way `sbe design` does.
    """
    if not isinstance(raw_intake, dict):
        return computed_tier
    declared = raw_intake.get("tier")
    override = raw_intake.get("override")
    reason = raw_intake.get("override_reason")
    if declared is None or override is None or declared != override:
        return computed_tier
    try:
        _tier_index(declared)
    except ValueError:
        return computed_tier
    if not isinstance(reason, str) or not reason.strip():
        return computed_tier
    return declared


def _scan_evidence(root, evidence_dir):
    """Every *.json under the evidence store, verified and classified.

    Returns a dict: `broken` (BROKEN CLAIMS items), `clean` (the receipts
    that verify PASS with a zero exit code), `failing` (MERGE BLOCKERS items,
    a verified receipt recording a nonzero exit code), `kindsCovered` (the
    set of design/gate/score kinds ANY verified receipt, passing or failing,
    DECLARES), `kindless` (one sentence per verified receipt that declares no
    kind, so a reader is told that evidence exists and says nothing about
    which check it was, rather than left to read the absence as no evidence at
    all), `count`, `inspected` (whether the store existed to look at),
    `note` (the scope sentence every caller prints, either way), and
    `receipts` (LANE B-004: one `{"path", "runId", "kinds", "coveredFiles"}`
    dict per verified, non-NO-DATA receipt -- clean and failing alike, the
    same population `kindsCovered` is built from -- so a caller that needs
    PER-CHANGE coverage, not this global set, can decide attribution for
    itself without re-reading every receipt off disk a second time; see
    `_dossier_evidence_attribution`).

    Every receipt verifies with the evidence store ITSELF excluded from its
    covered files: `evidence_mod.verify` is called with `exclude_dirs` set to
    `evidence_dir`'s path relative to `root`, the same spelling `coveredFiles`
    entries use. A receipt's `coveredFiles` normally comes from a diff, not a
    hand-picked list, and that diff cannot distinguish "code this run tested" from
    "another receipt that happened to land in the same base..HEAD range". A
    receipt regenerated at a fixed `--out` path (the ordinary shape of a CI
    re-run) is not a change to the code under test, and without this
    exclusion an unrelated receipt that merely covered its OLD bytes by
    diff-range accident would FAIL the moment it refreshed: the evidence
    store poisoning itself. See docs/KNOWN-LIMITS.md ("Evidence covering
    evidence") for exactly what this closes and does not.

    A receipt CLAIMED BY A TASK RECORD verifies against that task's own tree:
    when a registry record's `evidenceId` equals the receipt's `runId` and the
    record still declares a worktree directory that exists, `verify` runs with
    that worktree as its cwd, because the receipt is evidence for the task
    branch's head, and reading it against the root checkout would misreport
    every finished task's own evidence as a broken claim (the gap the golden
    scenario disclosed through rc.10). The resolution is disclosed on the
    clean entry (`verifiedIn`, `task`, and the finding sentence itself). A
    claimed receipt whose worktree is gone, an unclaimed receipt, and an
    unreadable registry all verify against root exactly as before: linkage
    that cannot be read never upgrades a verdict.
    """
    broken, clean, failing, kindless, receipts = [], [], [], [], []
    kinds_covered = set()
    if not os.path.isdir(evidence_dir):
        return {"broken": broken, "clean": clean, "failing": failing,
                "kindless": kindless,
                "kindsCovered": kinds_covered, "count": 0, "inspected": False,
                "note": "no evidence store found at %s" % evidence_dir,
                "receipts": receipts}
    exclude_rel = os.path.relpath(evidence_dir, root)
    try:
        registry_records = tasks_mod.load_registry(root).get("tasks", [])
    except tasks_mod.RegistryUnusable:
        registry_records = []
    claimed_by_run_id = {}
    for record in registry_records:
        record_run_id = record.get("evidenceId")
        record_worktree = record.get("worktree")
        if record_run_id and record_worktree and os.path.isdir(record_worktree):
            claimed_by_run_id[record_run_id] = (record_worktree, record.get("id"))
    paths = []
    for dirpath, _dirnames, filenames in os.walk(evidence_dir):
        for name in filenames:
            if name.endswith(".json"):
                paths.append(os.path.join(dirpath, name))
    paths.sort()
    for full in paths:
        # SERIALIZED RELATIVE PATHS ARE POSIX-SPELLED, ON EVERY PLATFORM.
        # `os.path.relpath` answers in the host's spelling, so on Windows this
        # read `.sbe\evidence\design.json` and the same receipt therefore had two
        # different names depending on which machine printed the JSON. A relative
        # path inside a machine-readable contract is an identifier, not a
        # filesystem instruction: consumers compare it, store it, and send it
        # between machines, so it has to mean one thing everywhere. This project
        # already spells its own canonical relative constants that way
        # (`tasks.DEFAULT_EVIDENCE_DIR` is ".sbe/evidence"). Absolute paths in
        # this same envelope stay host-spelled on purpose, because those ARE
        # filesystem instructions for the machine reading them.
        rel = os.path.relpath(full, root).replace(os.sep, "/")
        try:
            receipt_run_id = evidence_mod.load(full).get("runId")
        except evidence_mod.ReceiptUnreadable:
            receipt_run_id = None
        verify_cwd, claimed_task = (None, None)
        if receipt_run_id and receipt_run_id in claimed_by_run_id:
            verify_cwd, claimed_task = claimed_by_run_id[receipt_run_id]
        result = evidence_mod.verify(full, cwd=verify_cwd or root,
                                     exclude_dirs=(exclude_rel,))
        verdict = result["verdict"]
        if verdict == "FAIL":
            broken.append({
                "finding": "receipt %s fails verify: %s" % (rel, "; ".join(result["reasons"])),
                "remedy": "the receipt no longer proves anything; re-run the command through "
                          "`sbe evidence run` to produce a fresh one",
            })
            continue
        if verdict == "NO-DATA":
            # Advisory (a dirty tree at generation, or no covered file): not a
            # broken claim, and not clean evidence either. Counted in the note
            # only, per the module docstring, never silently dropped.
            continue
        receipt = result["receipt"] or {}
        kinds = _receipt_kinds(receipt)
        kinds_covered |= kinds
        gap = evidence_mod.kind_declaration_gap(receipt)
        if gap:
            kindless.append("receipt %s declares no check kind: %s" % (rel, gap))
        covered_paths = [cf.get("path") for cf in (receipt.get("coveredFiles") or [])
                        if isinstance(cf, dict) and cf.get("path")]
        receipts.append({"path": rel, "runId": receipt.get("runId"), "kinds": kinds,
                         "coveredFiles": covered_paths})
        trust = result["trust"]
        exit_code = receipt.get("exitCode")
        argv_text = " ".join(str(a) for a in (receipt.get("argv") or []))
        kinds_text = (", ".join(sorted(kinds)) if kinds
                      else "none declared, so it clears no obligation")
        if exit_code == 0:
            head = _git_head(verify_cwd or root)
            resolved = (", verified in task %s's declared worktree" % claimed_task
                        if verify_cwd else "")
            entry = {
                "finding": "receipt %s verifies as sound evidence, trust %s (declared check "
                          "kind(s): %s; command: %s; scope: this receipt's covered files "
                          "against HEAD %s, nothing beyond them%s)"
                          % (rel, trust, kinds_text, argv_text or "not recorded",
                             head[:7] if head else "unknown", resolved),
                "remedy": "no action; this receipt is sound evidence",
                "path": rel, "trust": trust,
            }
            if verify_cwd:
                entry["verifiedIn"] = verify_cwd
                entry["task"] = claimed_task
            clean.append(entry)
        else:
            failing.append({
                "finding": "receipt %s verifies as trustworthy but records exit code %s for "
                          "`%s`, trust %s, declared check kind(s) %s"
                          % (rel, exit_code, argv_text or "(argv not recorded)", trust,
                             kinds_text),
                "remedy": "fix the underlying failure and re-run to produce a new passing "
                         "receipt; see %s" % rel,
                "path": rel,
                "coveredFiles": covered_paths,
            })
    note = (("%d receipt(s) found under %s" % (len(paths), evidence_dir)) if paths
           else "evidence store %s exists and holds no receipt" % evidence_dir)
    if kindless:
        note += ("; %d verified receipt(s) declare no check kind and clear no obligation"
                 % len(kindless))
    return {"broken": broken, "clean": clean, "failing": failing, "kindless": kindless,
            "kindsCovered": kinds_covered, "count": len(paths), "inspected": True, "note": note,
            "receipts": receipts}


def _plan_owns(doss):
    """(owns, taskIds): every path a change's own `08-plan.json` declares a
    task OWNS, and the task ids that plan defines. A missing or unreadable
    plan reads as (empty set, empty set), the honest state before `sbe plan
    --write` has ever run for this change: it owns nothing yet and no
    registry record can be its claim. Mirrors the identical read
    `build_team_report` already does inline for its own scope-conflict
    attribution (`plan_owns_by_change`), kept as a separate function here
    rather than a shared one so this change touches nothing in that
    function or its own pinned tests."""
    plan = _read_json_or_none(os.path.join(doss, "08-plan.json"))
    owns, task_ids = set(), set()
    if plan:
        for task in plan.get("tasks", []):
            tid = task.get("id")
            if tid:
                task_ids.add(tid)
            for p in task.get("owns") or []:
                if isinstance(p, str) and p.strip():
                    owns.add(p)
    return owns, task_ids


def _dossier_evidence_attribution(root, dossiers, ev):
    """LANE B-004: per-change coverage over `ev["receipts"]`, for CR-06's
    discovered-dossier path only.

    Reproduced before this function existed: `_scan_evidence`'s own
    `kindsCovered` is a single GLOBAL set, computed once over every receipt
    in the store with no notion of which change it belongs to. A gate
    receipt generated for change A therefore cleared change B's MISSING
    EVIDENCE obligation too, the moment both dossiers were discovered in the
    same run, purely because the same global set was consulted for both.

    A receipt is attributable to a dossier when EITHER: a registry task
    record claims the receipt by `evidenceId` (the receipt's `runId`
    matches) AND that record is genuinely THIS dossier's own task, OR every
    one of the receipt's `coveredFiles` falls inside a path that dossier's
    plan `owns` (`sbe_fence_hook.paths_overlap`, via `tasks.paths_overlap`,
    the same containment rule task ownership is judged by everywhere else in
    this project; a receipt that also covers a file outside the dossier's
    declared ownership is not a clean claim of that dossier's obligation and
    is not attributed to it on that path). A receipt attributable to no
    dossier at all stays UNSCOPED: it is left out of every dossier's
    coverage below, on purpose, rather than guessed at.

    "Genuinely this dossier's own task" is deliberately NOT just "the
    record's `id` is one this dossier's plan also names": every derived plan
    starts fresh at T01 (`build_team_report`'s own scope-conflict code,
    right below this function, states the identical fact for the identical
    reason), so two sibling dossiers routinely each declare a task of their
    own also called "T01", and an id match alone would let a registry record
    that truly belongs to dossier A get read as dossier B's claim too,
    reopening the exact class of cross-dossier bug this function exists to
    close. A record only counts as a dossier's claim when, IN ADDITION to
    the id match, the record's own declared `ownedPaths` overlap a path that
    dossier's plan `owns`: the same containment check the coveredFiles path
    above already uses, applied to the registry's own claim instead of the
    receipt's.

    Returns (`coveredByName`: {dossier name: set(kind) actually attributed
    to it}, `unscopedPaths`: {kind: [receipt path, ...]} for receipts
    declaring that kind but attributable to no dossier, `attributedNames`:
    {kind: {dossier name: [receipt path, ...]}} for receipts declaring that
    kind and attributed to some dossier), so a caller reporting a kind a
    dossier still owes can say WHY a matching receipt did not clear it
    (unscoped, or landed on a different dossier) instead of staying silent
    about a receipt that plainly exists in the store.
    """
    try:
        registry_records = tasks_mod.load_registry(root).get("tasks", [])
    except tasks_mod.RegistryUnusable:
        registry_records = []
    plan_facts = dict((name, _plan_owns(doss)) for name, doss in dossiers)
    claimed_run_ids = {}
    for name, (owns, task_ids) in plan_facts.items():
        claimed = set()
        for r in registry_records:
            evidence_id = r.get("evidenceId")
            if not evidence_id or r.get("id") not in task_ids:
                continue
            record_owns = r.get("ownedPaths") or []
            if any(tasks_mod.paths_overlap(p, claim, root)
                  for p in record_owns for claim in owns):
                claimed.add(evidence_id)
        claimed_run_ids[name] = claimed

    covered_by_name = dict((name, set()) for name, _doss in dossiers)
    unscoped_paths, attributed_names = {}, {}
    for rcpt in ev["receipts"]:
        run_id = rcpt.get("runId")
        covered_files = rcpt.get("coveredFiles") or []
        kinds = rcpt.get("kinds") or set()
        attributed_to = []
        for name, _doss in dossiers:
            owns, _task_ids = plan_facts[name]
            by_claim = bool(run_id) and run_id in claimed_run_ids.get(name, set())
            by_owned_paths = bool(covered_files) and all(
                any(tasks_mod.paths_overlap(cf, claim, root) for claim in owns)
                for cf in covered_files)
            if by_claim or by_owned_paths:
                attributed_to.append(name)
        if attributed_to:
            for name in attributed_to:
                covered_by_name[name] |= kinds
                for kind in kinds:
                    attributed_names.setdefault(kind, {}).setdefault(name, []).append(
                        rcpt.get("path"))
        else:
            for kind in kinds:
                unscoped_paths.setdefault(kind, []).append(rcpt.get("path"))
    return covered_by_name, unscoped_paths, attributed_names


def _scan_tasks(root, reg_path):
    """The task registry read for ACTIVE CONFLICTS and FORCED closes.

    The overlap scan is `tasks.claims_overlap` over `tasks.open_tasks`, the
    exact functions `sbe task check` runs; nothing here re-derives the
    overlap rule. A registry that exists and cannot be read is named as such,
    never read as zero conflicts, because an unreadable registry still
    records somebody's open fences (see `tasks.RegistryUnusable`).
    """
    conflicts, forced = [], []
    if not os.path.exists(reg_path):
        return {"conflicts": conflicts, "forced": forced, "inspected": False,
                "note": "no task registry found at %s" % reg_path, "openCount": 0}
    try:
        data = tasks_mod.load_registry(root)
    except tasks_mod.RegistryUnusable as exc:
        return {"conflicts": conflicts, "forced": forced, "inspected": False,
                "note": "task registry at %s could not be read: %s" % (reg_path, exc),
                "openCount": None}
    live = tasks_mod.open_tasks(data)
    seen_pairs = set()
    for i, a in enumerate(live):
        for b in live[i + 1:]:
            for pa in a.get("ownedPaths") or []:
                for pb in b.get("ownedPaths") or []:
                    if tasks_mod.claims_overlap(pa, pb, root):
                        key = (a["id"], pa, b["id"], pb)
                        if key in seen_pairs:
                            continue
                        seen_pairs.add(key)
                        conflicts.append({
                            "finding": "task %s owns %r and task %s owns %r; two open "
                                      "writers overlap" % (a["id"], pa, b["id"], pb),
                            "remedy": "queue the writers: task %s and task %s both claim "
                                     "overlapping scope, one must close or narrow its owned "
                                     "paths before both proceed" % (a["id"], b["id"]),
                        })
    for t in data.get("tasks", []):
        if t.get("status") == "closed" and "forced" in t:
            f = t.get("forced") or {}
            forced.append({
                "finding": "task %s was closed FORCED by %s (%s)"
                          % (t.get("id"), f.get("who"), f.get("why")),
                "remedy": "review the forced disposition on task %s before merging, or "
                         "address the named violations: %s"
                         % (t.get("id"), ", ".join(f.get("violations") or []) or "(none)"),
            })
    note = ("%d open task(s) among %d total, read from %s"
           % (len(live), len(data.get("tasks", [])), reg_path))
    return {"conflicts": conflicts, "forced": forced, "inspected": True, "note": note,
            "openCount": len(live)}


def _scope_sentence(scope):
    """The sentence every positive or empty line carries, naming exactly
    which stores this run read. Every positive statement names its inspected
    scope; this is the one sentence that names it.

    The `dossiers` clause only appears when CR-06 discovery actually ran
    (the flat layout was absent and this run went looking under the design
    roots instead): `storesInspected["dossiers"]` stays `None` whenever the
    flat layout is present, so a flat repository's scope sentence is exactly
    the five-part sentence this file has always printed.
    """
    si = scope.get("storesInspected", {})
    parts = [
        "intake %s" % (si.get("intake") or "absent"),
        "disposition %s" % (si.get("disposition") or "absent"),
        "evidence store %s" % (si.get("evidenceDir") or "absent"),
        "task registry %s" % (si.get("taskRegistry") or "absent"),
    ]
    dossiers = si.get("dossiers")
    if dossiers is not None:
        parts.append("dossiers discovered: %s" % ", ".join(dossiers))
    parts.append(
        "diff %s" % (scope.get("diffRange") or ("NO-DATA: %s" % scope.get("diffProblem")
                                                if scope.get("diffProblem") else "NO-DATA")))
    return "scope: " + "; ".join(parts)


def _intake_summary_text(summaries):
    """The MERGE BLOCKERS empty-scope note's `intake ... (tier ...)` clause.

    One flat target (the ordinary layout, `summaries[0]["label"] is None`)
    keeps the exact sentence this file has always printed. One or more
    discovered dossiers are joined instead, each labeled by name, so a
    reader knows which change's intake was actually read.
    """
    if len(summaries) == 1 and summaries[0]["label"] is None:
        s = summaries[0]
        return "intake %s (tier %s)" % (s["intakePath"] if s["intakeExists"] else "absent",
                                        s["tier"] or "unknown")
    if not summaries:
        return "intake absent (tier unknown)"
    return "; ".join(
        "dossier %s intake %s (tier %s)"
        % (s["label"], s["intakePath"] if s["intakeExists"] else "absent",
           s["tier"] or "unknown")
        for s in summaries)


def _missing_evidence_summary_text(summaries):
    """The MISSING EVIDENCE empty-scope note's `declared tier ... from ...`
    clause. Same singular-versus-discovered split as `_intake_summary_text`,
    kept as a separate function because the two notes have always used
    different wording for the same underlying facts."""
    if len(summaries) == 1 and summaries[0]["label"] is None:
        s = summaries[0]
        return "declared tier %s from %s" % (
            s["tier"] or "unknown", s["intakePath"] if s["intakeExists"] else "no intake file")
    if not summaries:
        return "declared tier unknown from no intake file"
    return "; ".join(
        "dossier %s declared tier %s from %s"
        % (s["label"], s["tier"] or "unknown",
           s["intakePath"] if s["intakeExists"] else "no intake file")
        for s in summaries)


def _section_line(items, inspected, detail):
    """The empty-state line for one section: NO-DATA when nothing was there
    to inspect, a clean-scope line when something was inspected and found
    nothing to report."""
    if items:
        return None
    return ("NO-DATA. scope: %s" % detail) if not inspected else ("clean. scope: %s" % detail)


def _next_action(sections, scope, ladder_candidates):
    """The whole report's one next action, LANE C1 (B-003): picked by
    `lifecycle.reduce_next_action` over every candidate this run can name,
    rather than this function owning its own priority order a second time.

    Candidates, most urgent first: the first item of each of the four
    blocking sections (broken claims, merge blockers, active conflicts,
    missing evidence -- unchanged from every earlier version of this
    function, same order, same "(SECTION NAME)" wording), every task-
    active / task-ready / review-not-cleared candidate `ladder_candidates`
    carries in from `_change_ladder_candidates` (new: these used to have no
    representation here at all, which was the reproduced bug -- a dossier
    whose only outstanding obligation was review, or an unstarted ready
    task, read as clean because nothing here had ever looked), and a
    fallback that guarantees this list is never empty: the sound-evidence
    remedy when `sound_evidence` is the only thing this run found, or the
    plain "nothing blocking" sentence when nothing was found at all. Both
    fallback wordings, and the "(SECTION NAME)" suffix on sections 1-4, are
    unchanged from every earlier version of this function, so a repository
    with no plan anywhere (nothing for `ladder_candidates` to ever carry)
    gets byte-identical output to before this lane.

    `scope` is unused by the pick itself now (kept as a parameter so this
    function's signature still names what a caller must have on hand); the
    scope sentence is appended by `build_report` once, after this function
    returns, exactly where it has always been appended.

    Returns the reducer's own `{"actionId", "label", "reason", "basis"}`
    dict, never a formatted string: the caller composes the rendered
    `nextAction` text from `reason` plus its own scope sentence.
    """
    broken, merge, conflict, missing_evidence, sound = sections
    candidates = []
    if broken:
        candidates.append(lifecycle.candidate(
            lifecycle.RUNG_BROKEN_CLAIM, "%s (BROKEN CLAIMS)" % broken[0]["remedy"]))
    if merge:
        candidates.append(lifecycle.candidate(
            lifecycle.RUNG_MERGE_BLOCKER, "%s (MERGE BLOCKERS)" % merge[0]["remedy"]))
    if conflict:
        candidates.append(lifecycle.candidate(
            lifecycle.RUNG_ACTIVE_CONFLICT, "%s (ACTIVE CONFLICTS)" % conflict[0]["remedy"]))
    if missing_evidence:
        candidates.append(lifecycle.candidate(
            lifecycle.RUNG_MISSING_EVIDENCE,
            "%s (MISSING EVIDENCE)" % missing_evidence[0]["remedy"]))
    candidates.extend(ladder_candidates)
    if sound:
        fallback_reason = "%s (COMPLETED EVIDENCE)" % sound[0]["remedy"]
    else:
        fallback_reason = "nothing blocking here that this tool can see."
    candidates.append(lifecycle.candidate(lifecycle.RUNG_FINISH, fallback_reason))
    return lifecycle.reduce_next_action(candidates)


def _review_ladder_check(root, doss, head):
    """Whether `11-review.json` at `doss` clears the review obligation, for
    `_change_ladder_candidates` below (never for `build_team_report`'s own
    severity-11 finding, which keeps its own richer branch-by-branch
    `detail` text -- self-review identity, staleness, structured findings,
    LANE B-006 history -- unchanged and untouched by this lane). Reuses the
    SAME primitives that block already reads (`_read_review_record`,
    `_commit_author`, `_same_identity`), so the underlying RULE -- missing,
    malformed, stale, unresolvable author, self-reviewed, not approved, or
    approved -- is read once, never re-derived a second time; only the
    assembly into ONE recommended sentence is separate here.

    Returns (cleared, nextAction): `nextAction` is `None` exactly when
    `cleared` is True.
    """
    path = os.path.join(doss, "11-review.json")
    state, payload = _read_review_record(path)
    if state == "missing":
        return False, (
            "run sbe review %s --write --reviewer <name> --reviewer-type "
            "human|model|independent-model --result "
            "approved|changes-required|unverifiable to record one" % doss)
    if state == "malformed":
        return False, "regenerate 11-review.json with sbe review %s --write" % doss
    review = payload
    bound = review.get("headSha")
    if head and bound and bound != head:
        return False, "re-run sbe review against the current head and --write again"
    try:
        author_name, author_email = _commit_author(root, bound)
    except OSError:
        return False, ("install or repair git on this machine so the reviewed commit's "
                       "author can be checked")
    if author_name is None:
        return False, ("fetch the reviewed commit so its author can be checked, or "
                       "re-run sbe review once it is reachable")
    if _same_identity(review.get("reviewer"), author_name, author_email):
        return False, ("get an independent reviewer to review %s and record a fresh "
                       "11-review.json" % doss)
    if review.get("result") != "approved":
        return False, "resolve what the review flagged, then record a fresh 11-review.json"
    return True, None


def _ladder_finding(rung, severity, verdict, evidence, commit, owner, reason, basis,
                    detail):
    """A2 ROUND 2: one task/approval/plan-readiness candidate, shaped so
    BOTH of `_task_and_approval_candidates`'s callers can read it without
    either one re-shaping what the other already computed. `_change_ladder_
    candidates` (`build_report`'s own ladder) reads only `rung` and
    `reason`, the same two keys every other candidate in that function's
    list already carries via `lifecycle.candidate`. `build_team_report`'s
    severity-10 derivation reads the full finding shape (`severity`,
    `verdict`, `evidence`, `commit`, `owner`, `nextAction`) to build its own
    recorded entry, the SAME shape `_finding` produces for every other
    finding in that report. `change` is set to `None`: neither caller ever
    reads it back off a candidate (team's own severity-10 entry is built
    from the LOOP's own `name`, never from a winning candidate's `change`
    field), so inventing one here would be a fact nobody consumes.
    """
    entry = _finding(None, severity, verdict, evidence, commit, owner, reason, basis,
                     detail)
    entry["rung"] = rung
    entry["reason"] = reason
    return entry


def _approval_ladder_candidate(doss, head, prefix):
    """BLOCKER A2 / A2 ROUND 2: the missing-approval candidate for the
    change whose dossier is `doss`, in `lifecycle`'s own rung vocabulary,
    for `_task_and_approval_candidates` to include once every task the
    plan declares is closed clean (this function is only ever called from
    there, after that check, for the identical "nothing left to approve
    until nothing is left to do" reason -- see that function's own
    docstring for why the gate moved from "a plan exists" to "the plan's
    own work is finished").

    Mirrors the IDENTICAL `10-approval.json` judgement `build_team_report`'s
    own severity-5 finding already makes below (missing, stale, or a saved
    `final` other than PASS), read here a second time for a second surface
    rather than re-derived, so a second `sbe status --json` reading over the
    SAME commit never falls through to `finish` ("nothing blocking here")
    while `sbe status --team` and `sbe map` (built entirely from the team
    report) both name this change's own `resolve-missing-approval`.
    Reproduced, not theorized: `tools/test_sbe_status_team.py::
    TestCanonicalNextAction.test_missing_approval_is_the_same_action_id_on_both_surfaces`.

    A stale approval (bound to a commit that is no longer head) is
    deliberately `RUNG_STALE_RECORD`, not this rung, mirroring team's own
    severity-4 split for the identical fact: staleness is a different
    finding from absence or failure, and this function keeps them apart the
    same way team's own `_finding` calls do.
    """
    approval_path = os.path.join(doss, "10-approval.json")
    approval = _read_json_or_none(approval_path)
    if approval is None:
        return [_ladder_finding(
            lifecycle.RUNG_MISSING_APPROVAL, 5, "NO-DATA", approval_path, head, None,
            "%srun sbe pr verify and save its --json output as 10-approval.json "
            "(MISSING APPROVAL)" % prefix, "observed",
            "no approval report is saved for this change; absence is a fact, not an "
            "accusation")]
    bound = approval.get("headSha")
    if head and bound and bound != head:
        return [_ladder_finding(
            lifecycle.RUNG_STALE_RECORD, 4, "FAIL", approval_path, bound, None,
            "%sre-run sbe pr verify against the current head (MISSING APPROVAL)" % prefix,
            "derived",
            "the approval report binds to %s but the repository head is %s: stale "
            "approval" % (bound[:12], head[:12]))]
    if approval.get("final") != "PASS":
        return [_ladder_finding(
            lifecycle.RUNG_MISSING_APPROVAL, 5, str(approval.get("final")), approval_path,
            bound, None,
            "%sresolve the failing controls on the pull request (MISSING APPROVAL)"
            % prefix, "observed",
            "the saved approval report's FINAL is %s" % approval.get("final"))]
    return []


def _task_and_approval_candidates(doss, head, prefix, plan, records, registry_data):
    """A2 ROUND 2: the ONE shared plan-readiness / task-readiness /
    approval candidate list, read IDENTICALLY by `_change_ladder_
    candidates` (`build_report`'s own ladder) and `build_team_report`'s
    severity-10 derivation, so the two surfaces cannot diverge by
    construction the way round 1's BLOCKER A2 fix let them.

    Reproduced, not theorized: round 1 added `_approval_ladder_candidate`
    to this function's predecessor UNCONDITIONALLY, gated only on
    `08-plan.json` existing. Since `RUNG_MISSING_APPROVAL` (40) outranks
    `RUNG_ACTIVE_TASK` (60) and `RUNG_READY_TASK` (70), a fresh plan whose
    tasks had not been started yet recommended chasing a pull-request
    approval on BOTH surfaces before anyone had started the work -- the
    original "review pending" journey-2 disease (see `TestCanonicalNext
    Action`'s own module docstring), unified across both surfaces instead
    of cured. `build_team_report`'s own severity 5 (missing approval), 6
    (missing convergence) and 12 (missing evidence) NO-DATA findings share
    the IDENTICAL defect for the SAME reason (none of the three is gated on
    task completion either), which is why this function's own approval
    candidate, and `build_team_report`'s own exclusion of those three
    findings from its candidate pool below, are both gated on the SAME
    fact: whether every task this plan declares is closed clean. A FAIL
    (something already ran and failed) at severity 5, 6 or 12 is a
    different fact from "not attempted yet" and is never gated this way,
    on either surface.

    `plan`: the parsed `08-plan.json`, or `None` when no plan exists yet for
    this change -- in which case a SINGLE "derive the plan" candidate is
    returned (mirroring `build_team_report`'s own severity-8 "no plan"
    finding's text exactly, the fix for the SAME divergence one rung
    further back: plain `sbe status` used to answer "nothing blocking here"
    for a dossier team already named "start-ready-task" for, over the exact
    same fresh-intake-only repository), and `records`/`registry_data` are
    never read. `records`: this change's own task-registry rows (already
    filtered to the plan's own declared task ids by both callers).
    `registry_data`: the loaded registry; `None` means the registry could
    not be read, in which case this function returns `[]` (nothing can be
    safely judged ready or done without it), the same conservative answer
    `_change_ladder_candidates` always gave on a registry read failure.

    Approval is only ever a candidate once every task the plan declares is
    CLOSED CLEAN (`_closed_clean`, the identical predicate `build_team_
    report`'s own severity 9 "nothing left to do; open a PR" finding
    already uses): there is nothing to approve until there is nothing left
    to do, the same moment a plan stops needing an active or ready task
    candidate at all.
    """
    plan_path = os.path.join(doss, "08-plan.json")
    if plan is None:
        return [_ladder_finding(
            lifecycle.RUNG_READY_TASK, 8, "NO-DATA", plan_path, head, None,
            "%srun sbe plan %s --write to derive the task graph" % (prefix, doss),
            "observed",
            "no plan exists for this change yet, so there are no tasks to ready: a "
            "starting state to move past, not a ready task and not an error")]
    if registry_data is None:
        return []

    records_by_id = {}
    for r in records:
        records_by_id.setdefault(r.get("id"), []).append(r)

    candidates = []
    for r in records:
        if r.get("status") == "open":
            candidates.append(_ladder_finding(
                lifecycle.RUNG_ACTIVE_TASK, 7, "NO-DATA", "task %s" % r.get("id"), head,
                r.get("agent"),
                "%sfinish or check task %s with sbe work (ACTIVE TASK)"
                % (prefix, r.get("id")), "observed",
                "task %s is open, held by %s" % (r.get("id"), r.get("agent"))))

    plan_tasks = plan.get("tasks", [])
    if not candidates:
        for task in plan_tasks:
            tid = task.get("id")
            if records_by_id.get(tid):
                continue  # already started or done: not a "ready" candidate
            deps = [d for d in (task.get("dependsOn") or []) if isinstance(d, str)]
            blockers = [b for b in
                       (work_mod._dependency_problem(registry_data, d) for d in deps) if b]
            if not blockers:
                candidates.append(_ladder_finding(
                    lifecycle.RUNG_READY_TASK, 8, "NO-DATA", "task %s" % tid, head, None,
                    "%srun sbe work start %s --plan %s to begin it (READY TASK)"
                    % (prefix, tid, plan_path), "derived",
                    "task %s's dependencies are all closed clean and it carries no "
                    "registry record yet: ready to start" % tid))
                break  # one is enough to name; the same "first item wins"
                       # convention every other section here already uses

    all_done = bool(plan_tasks) and all(
        _closed_clean(records_by_id.get(t.get("id"), [])) for t in plan_tasks)
    if all_done:
        candidates.extend(_approval_ladder_candidate(doss, head, prefix))

    return candidates


def _change_ladder_candidates(root, doss, head, prefix):
    """LANE C1 (B-003) / A2 ROUND 2: the plan-readiness / task-active /
    task-ready / missing-approval candidates for the change whose dossier
    is `doss`, from the SAME `_task_and_approval_candidates` `build_team_
    report`'s severity-10 derivation reads, plus the review-not-cleared
    candidate (kept here rather than folded into that shared function:
    team's own severity 11 already carries several distinct sub-findings --
    self-review, staleness, structured findings, LANE B-006 history -- that
    `_review_ladder_check`'s single cleared/not-cleared answer intentionally
    does not re-derive; see that function's own docstring).

    `08-plan.json` not existing yet at `doss` no longer ALWAYS short-
    circuits to `[]`: when `00-intake.json` exists at `doss` (a real,
    discovered change with nothing planned yet), `_task_and_approval_
    candidates` now returns the single "derive the plan" candidate for
    that state, the fix for the sibling divergence one rung further back
    from BLOCKER A2 (plain `sbe status` used to answer "nothing blocking
    here" for a fresh-intake-only repository that `sbe status --team`
    already named "start-ready-task" for). When NEITHER `00-intake.json`
    nor `08-plan.json` exists at `doss`, this still returns `[]`: nothing
    has been declared yet to plan, the same "not a discovered change at
    all" state `build_team_report`'s own `_team_changes` walker already
    requires an intake OR a validated plan to cross (MAJOR A6), so a truly
    empty repository (the flat single-target convention's own placeholder
    `doss`, which need not exist on disk at all) keeps recommending nothing
    rather than "derive a plan" for a change nobody has started. A registry
    that cannot be read still reads as `[]` here (the existing ACTIVE
    CONFLICTS note already names that failure elsewhere in this report;
    this function does not invent a second, competing finding for it),
    preserved exactly as before this round.
    """
    plan_path = os.path.join(doss, "08-plan.json")
    plan = _read_json_or_none(plan_path)
    if plan is None:
        if not os.path.isfile(os.path.join(doss, INTAKE_REL)):
            return []
        return _task_and_approval_candidates(doss, head, prefix, None, [], None)
    try:
        registry_data = tasks_mod.load_registry(root)
    except tasks_mod.RegistryUnusable:
        return []
    plan_ids = set(t.get("id") for t in plan.get("tasks", []))
    records = [r for r in registry_data.get("tasks", []) if r.get("id") in plan_ids]
    candidates = _task_and_approval_candidates(
        doss, head, prefix, plan, records, registry_data)

    cleared, review_next = _review_ladder_check(root, doss, head)
    if not cleared:
        candidates.append(lifecycle.candidate(
            lifecycle.RUNG_REVIEW_NOT_CLEARED, "%s%s (REVIEW)" % (prefix, review_next)))
    return candidates


# ---------------------------------------------------------------------------
# LT-302.B: the smallest possible read path for `12-handover.json` (LT-301's
# explicit-human-handover artifact), so `sbe status` can say whether
# ownership of a change is moving to someone else, and to whom, without
# becoming a second copy of the engine that writes it.
#
# This module never imports `brothersbe.handover`: that module already
# imports THIS one (`handover.cmd_prepare` derives its summary from the same
# task-registry and evidence readers this file owns), so a reverse import
# would be a cycle. What is read here is deliberately smaller than
# `handover.read_handover`'s own fourteen-field check: only `status` (to
# tell prepared/acknowledged/rejected apart) and `headSha` (for the same
# bound-commit staleness compare convergence, approval and review are
# already held to, in `build_team_report` below). `_read_handover_record`
# mirrors `_read_review_record`'s three-state law exactly: a missing record
# is NO-DATA (nobody is handing this change to anyone, a fact, never a
# block, per LT-302.B's own instruction); a record present but unparseable,
# not an object, or carrying a `status` this installation has never
# written, is a broken claim, worse than silence.
#
# The result is intentionally NOT folded into `build_team_report`'s
# severity 1..11 findings list. That range is already pinned twice: this
# project's own contract test (`test_every_finding_carries_the_full_
# contract` in tools/test_sbe_status_team.py) hard-asserts `1 <= severity
# <= 11`, and the LT-202 structured-findings note a few hundred lines below
# states the same law in prose ("never invents a severity outside 1..11").
# A handover is explicitly never a merge blocker of its own ("do not make
# handover absence a blocker when ownership is not changing" -- LT-302.B),
# so it does not need a severity slot to be visible: both `build_report` and
# `build_team_report` return a new, purely additive `handover` list, one
# entry per change, and a caller reads it by name, exactly the way LT-302's
# skill is told to read every other status field: JSON, never prose
# interpretation.
# ---------------------------------------------------------------------------

HANDOVER_REL = "12-handover.json"
_HANDOVER_STATUSES = ("prepared", "acknowledged", "rejected")


def _read_handover_record(path):
    """("missing", None) | ("malformed", <why, as text>) | ("ok", <dict>).

    See the module note above for why this mirrors `_read_review_record`
    rather than `handover.read_handover`: same three states, a smaller
    field check.
    """
    if not os.path.exists(path):
        return "missing", None
    try:
        data = json.loads(io.open(path, encoding="utf-8").read())
    except (ValueError, OSError) as exc:
        return "malformed", "does not parse: %s" % exc
    if not isinstance(data, dict):
        return "malformed", "the top-level JSON value is not an object"
    if data.get("status") not in _HANDOVER_STATUSES:
        return "malformed", ("status %r is not one of %s"
                             % (data.get("status"), ", ".join(_HANDOVER_STATUSES)))
    return "ok", data


def _handover_state(dossier, head):
    """One dict describing what `12-handover.json` says about ownership of
    the change at `dossier`, or the honest absence of one.

    `status` is "none" (no handover exists for this change: LT-302.B's "no
    handover needed"), "malformed" (a record exists and cannot be trusted),
    or the artifact's own recorded "prepared" ("handover prepared", and,
    when it is not stale, the detail sentence below also says "awaiting
    receiver", LT-302.B's third state, in exactly those words),
    "acknowledged" or "rejected". `stale` is True when a record's own bound
    `headSha` no longer matches `head` (mirroring the exact bound-commit
    compare convergence, approval and review already use below), False when
    it matches, and None when staleness does not apply (no record, or a
    broken one). `change` is left for the caller to fill in: this function
    is called once per dossier by both `build_report` and
    `build_team_report`, and only the caller knows which of the two
    vocabularies (a discovered dossier's name, or `None` for the flat
    single-dossier layout) applies.
    """
    path = os.path.join(dossier, HANDOVER_REL)
    kind, payload = _read_handover_record(path)
    if kind == "missing":
        return {"change": None, "status": "none", "path": path, "boundHead": None,
                "stale": None, "outgoingOwner": None, "intendedReceiver": None,
                "detail": "no handover (12-handover.json) is recorded for this change; "
                          "absence is a fact, not an accusation, and never a block when "
                          "ownership is not changing"}
    if kind == "malformed":
        return {"change": None, "status": "malformed", "path": path, "boundHead": None,
                "stale": None, "outgoingOwner": None, "intendedReceiver": None,
                "detail": "%s cannot be trusted: %s" % (path, payload)}
    status_val = payload.get("status")
    bound = payload.get("headSha")
    outgoing = payload.get("outgoingOwner")
    receiver = payload.get("intendedReceiver")
    stale = bool(head and bound and bound != head)
    if status_val == "prepared":
        if stale:
            detail = ("the handover from %s to %s binds to %s but the repository head is "
                      "%s: stale, and must be re-prepared before %s can acknowledge or "
                      "reject it" % (outgoing, receiver, str(bound)[:12],
                                    (head or "?")[:12], receiver))
        else:
            detail = ("a handover from %s to %s is prepared and awaiting receiver: "
                      "ownership remains with %s until %s acknowledges"
                      % (outgoing, receiver, outgoing, receiver))
    elif status_val == "acknowledged":
        ack = payload.get("acknowledgment") or {}
        who = ack.get("receiver") or receiver
        detail = ("the handover from %s was acknowledged by %s at %s; ownership has "
                  "moved to %s" % (outgoing, who, ack.get("at"), who))
        if stale:
            detail += (" (the code has moved on since: bound to %s, head is now %s)"
                      % (str(bound)[:12], (head or "?")[:12]))
    else:  # "rejected", the only value _read_handover_record's "ok" state admits here
        ack = payload.get("acknowledgment") or {}
        who = ack.get("receiver") or receiver
        detail = ("the handover to %s was rejected by %s (%s); ownership stays with %s"
                  % (receiver, who, ack.get("reason") or "no reason recorded", outgoing))
        if stale:
            detail += (" (the code has moved on since: bound to %s, head is now %s)"
                      % (str(bound)[:12], (head or "?")[:12]))
    return {"change": None, "status": status_val, "path": path, "boundHead": bound,
            "stale": stale, "outgoingOwner": outgoing, "intendedReceiver": receiver,
            "detail": detail}


def build_report(path, base=None, now=None):
    """The whole `sbe status` verdict: six sections, blocker-first, plus the
    scope this run actually read. Raises nothing; every failure to read a
    store becomes a NO-DATA note in that store's section instead.

    CR-06: intake and disposition are read flat at `<root>` first, exactly as
    every earlier version of this function read them. Only when that flat
    `00-intake.json` is absent does this function discover dossiers through
    the SAME `_design_roots`/`_team_changes` walker `build_team_report`
    already uses, read each discovered dossier's own intake and disposition
    in the flat file's place, and label every finding they produce with the
    dossier's name. See the module docstring for the full statement.
    """
    root = os.path.abspath(path)
    now = time.time() if now is None else now

    broken_claims, merge_blockers, active_conflicts = [], [], []
    missing_evidence, sound_evidence = [], []
    handover_entries = []
    # LANE C1 (B-003): the task-ladder/review candidates `_change_ladder_
    # candidates` finds per target, fed into `_next_action`'s reducer call
    # below alongside sections 1-4.
    ladder_candidates = []
    # P5: one POSSIBLY BEHIND sentence per target whose newest dated record
    # names a commit older than HEAD; never gates `nextAction` (a caller who
    # wants a resume-time warning reads this list, not the reducer), see
    # `_possibly_behind_note`.
    possibly_behind = []

    intake_path = os.path.join(root, INTAKE_REL)
    disposition_path = os.path.join(root, DISPOSITION_REL)
    evidence_dir = tasks_mod.evidence_dir(root)
    reg_path = tasks_mod.registry_path(root)

    # ---- CR-06: dossier discovery, only when the flat layout is absent.
    # The flat file always wins when it exists: discovery never even runs
    # then, which is how a flat repository's output stays byte-identical to
    # every earlier version of this function. ----
    flat_present = os.path.exists(intake_path)
    dossiers, dossier_refusals = [], []
    if not flat_present:
        dossiers, dossier_refusals = _team_changes(root)
    if flat_present or not dossiers:
        targets = [(None, intake_path, disposition_path)]
    else:
        targets = [(name, os.path.join(doss, INTAKE_REL), os.path.join(doss, DISPOSITION_REL))
                  for name, doss in dossiers]

    head_sha = _git_head(root)
    scope = {
        "root": root,
        "base": base,
        "headCommit": head_sha,
        "storesInspected": {
            "intake": intake_path if flat_present else None,
            "disposition": disposition_path if os.path.exists(disposition_path) else None,
            "evidenceDir": evidence_dir if os.path.isdir(evidence_dir) else None,
            "taskRegistry": reg_path if os.path.exists(reg_path) else None,
            # None when the flat layout is present (discovery never ran) or
            # when discovery ran and found nothing; the same absence-reads-
            # as-absence shape every other store above already uses. Only
            # non-empty when at least one dossier was actually found.
            "dossiers": (sorted(name for name, _d in dossiers)
                        if (not flat_present and dossiers) else None),
        },
        "diffRange": None,
        "diffProblem": None,
    }

    # ---- Evidence store: BROKEN CLAIMS, the sound receipts, and the
    # exit-code-failing receipts that belong under MERGE BLOCKERS. Shared and
    # root-level for every target below, exactly as `sbe status --team`
    # already reads it: a receipt is never scoped to one dossier. ----
    ev = _scan_evidence(root, evidence_dir)
    broken_claims.extend(ev["broken"])
    sound_evidence.extend(ev["clean"])
    merge_blockers.extend(ev["failing"])

    # ---- LANE B-004: per-change evidence scoping, for the MISSING EVIDENCE
    # obligation check below only. Computed ONLY on the CR-06
    # discovered-dossier path (`dossiers` non-empty and the flat layout
    # absent): the flat single-dossier layout keeps consulting `ev`'s own
    # global `kindsCovered` exactly as it always has, so a flat repository's
    # output stays byte-identical to every earlier version of this function
    # (the CR-06 law stated at the top of this file). `dossier_kinds_covered`
    # stays `{}` in that case, and every read below treats an empty dict as
    # "scoping does not apply here" rather than "every dossier covers
    # nothing". See `_dossier_evidence_attribution` for what "attributable"
    # means. ----
    dossier_kinds_covered, unscoped_kind_paths, attributed_kind_names = {}, {}, {}
    if not flat_present and dossiers:
        dossier_kinds_covered, unscoped_kind_paths, attributed_kind_names = (
            _dossier_evidence_attribution(root, dossiers, ev))

    # ---- Task registry: ACTIVE CONFLICTS and FORCED closes. Shared and
    # root-level, same reasoning as the evidence store above. ----
    tk = _scan_tasks(root, reg_path)
    active_conflicts.extend(tk["conflicts"])
    merge_blockers.extend(tk["forced"])

    # ---- CR-06 containment: a designRoots entry that would escape the
    # repository is refused by `_design_roots` and never walked; it is
    # surfaced here as a merge blocker, never a silent skip, the same
    # containment law `build_team_report` already applies to it. ----
    for entry in dossier_refusals:
        merge_blockers.append({
            "finding": "designRoots entry %r in .sbe/team-profile.json resolves outside "
                      "this repository root and was REFUSED: it is not walked for "
                      "dossiers, and no dossier under it is discovered" % entry,
            "remedy": "point designRoots entry %r at a directory inside this repository, "
                     "or remove the entry" % entry,
        })

    # ---- Disposition staleness, intake/diff reconciliation and MISSING
    # EVIDENCE, run once per target above: the one flat root target in the
    # ordinary layout (unchanged from every earlier version of this
    # function), or once per discovered dossier when the flat layout was
    # absent instead. ----
    summaries = []
    for label, t_intake_path, t_disposition_path in targets:
        prefix = ("dossier %s: " % label) if label else ""

        if os.path.exists(t_disposition_path) and head_sha:
            _live, disp_note = impact_mod.read_disposition(t_disposition_path, head_sha)
            if disp_note:
                broken_claims.append({
                    "finding": "%sdisposition file %s: %s"
                              % (prefix, t_disposition_path, disp_note),
                    "remedy": "record a fresh disposition against head %s naming who decided "
                             "and why" % head_sha[:12],
                })

        # Intake vs diff reconciliation, read exactly as `sbe impact` reads
        # it: this is analysis of a diff and two small JSON files, never a
        # subprocess and never a new gate run.
        human_tier, _answers, intake_problem = impact_mod.read_intake(t_intake_path)
        if human_tier is not None:
            # Ledger 12: the same overridden-tier field sbe design reads,
            # read a second time here rather than re-derived (see
            # `_declared_tier`). Reassigned in place so every use of
            # `human_tier` below (the disagreement text, the summaries
            # entry, and the MISSING EVIDENCE obligation gate) stays in
            # sync with the ONE effective tier, exactly the way `sbe
            # design` treats a documented override as live everywhere it
            # prints, not only in one note.
            human_tier = _declared_tier(_read_json_or_none(t_intake_path), human_tier)
        idata = None
        try:
            idata = impact_mod.report(
                root, base=base, head="HEAD",
                intake_path=t_intake_path if os.path.exists(t_intake_path) else None,
                disposition_path=(t_disposition_path
                                  if os.path.exists(t_disposition_path) else None))
            if scope["diffRange"] is None:
                scope["diffRange"] = idata["scope"]
        except DiffUnavailable as exc:
            if scope["diffProblem"] is None:
                scope["diffProblem"] = str(exc)

        if intake_problem and human_tier is None and os.path.exists(t_intake_path):
            merge_blockers.append({
                "finding": "%sintake at %s cannot be read: %s"
                          % (prefix, t_intake_path, intake_problem),
                "remedy": "fix 00-intake.json so its five answers parse in the accepted "
                         "vocabulary, then re-run status",
            })
        if idata is not None:
            for d in idata["disagreements"]:
                if d["disposition"] != "missing":
                    continue
                merge_blockers.append({
                    "finding": "%sintake declared %s but the diff shows %s (detector %s on "
                              "%s, no disposition)"
                              % (prefix, human_tier, idata["proposedTier"], d["detector"],
                                 d["file"]),
                    "remedy": "record a disposition for %s naming who decided and why, "
                             "against head %s, or revise the declared tier"
                             % (d["detector"],
                                (head_sha or idata.get("headCommit") or "?")[:12]),
                })

        # ---- MISSING EVIDENCE: only when a tier is known and owes
        # something. ----
        #
        # An obligation is cleared by a receipt that DECLARES its kind, never
        # by one whose command line happens to spell it. Receipts that
        # declare nothing are named in the same finding rather than left out
        # of it: a reader told "no receipt records a hard gate run" while
        # four receipts sit in the store would reasonably conclude the tool
        # cannot see them.
        #
        # LANE B-004: which set clears an obligation depends on whether CR-06
        # dossier discovery ran. `dossier_kinds_covered` is non-empty ONLY on
        # that path, and on it a kind counts as covered for THIS target only
        # when a receipt is actually attributable to THIS dossier (see
        # `_dossier_evidence_attribution`); the flat single-dossier layout
        # (that dict stays `{}`) keeps consulting `ev`'s own global set
        # exactly as it always has, unchanged.
        if human_tier not in (None, "T0"):
            kindless_note = ""
            if ev["kindless"]:
                kindless_note = ("; %d receipt(s) in the store declare no check kind and "
                                 "clear no obligation: %s"
                                 % (len(ev["kindless"]), "; ".join(ev["kindless"])))
            if dossier_kinds_covered:
                covered_here = dossier_kinds_covered.get(label, set())
            else:
                covered_here = ev["kindsCovered"]
            for kind, klabel, cmdline in CHECK_KINDS:
                if kind in covered_here:
                    continue
                scope_note = ""
                # A kind covered somewhere in the store, but not attributed to
                # THIS dossier: never silently cleared, and never silently
                # dropped either -- name the receipt and say why it did not
                # clear this obligation, unscoped or landed elsewhere.
                if dossier_kinds_covered and kind in ev["kindsCovered"]:
                    parts = []
                    unscoped_here = sorted(unscoped_kind_paths.get(kind) or [])
                    if unscoped_here:
                        parts.append("a matching receipt exists but is unscoped (%s)"
                                     % ", ".join(unscoped_here))
                    elsewhere = sorted(n for n in attributed_kind_names.get(kind, {})
                                       if n != label)
                    if elsewhere:
                        parts.append(
                            "a matching receipt exists but is attributed to dossier %s, "
                            "not this one" % ", ".join(elsewhere))
                    if parts:
                        scope_note = "; " + "; ".join(parts)
                missing_evidence.append({
                    "finding": "%sno evidence receipt declares a %s run, and declared "
                              "tier %s owes one%s%s"
                              % (prefix, klabel, human_tier, kindless_note, scope_note),
                    "remedy": "run `%s` through `sbe evidence run --kind %s` to record it"
                             % (cmdline, kind),
                })

        summaries.append({
            "label": label,
            "intakePath": t_intake_path,
            "intakeExists": os.path.exists(t_intake_path),
            "dispositionExists": os.path.exists(t_disposition_path),
            "tier": human_tier,
        })

        # LT-302.B: one handover entry per target, purely additive (see the
        # note above `_handover_state`). The dossier for this target is
        # wherever its own intake lives: `root` itself for the flat layout,
        # or the discovered dossier directory otherwise, exactly the same
        # split `prefix` above already reads off `label`.
        target_doss = os.path.dirname(t_intake_path)
        h_entry = _handover_state(target_doss, head_sha)
        h_entry["change"] = label
        handover_entries.append(h_entry)

        # P5: named here, per target, so a label already knows which
        # dossier's record is behind rather than a single unlabeled note.
        behind_note = _possibly_behind_note(root, target_doss, head_sha, prefix)
        if behind_note:
            possibly_behind.append(behind_note)

        # LANE C1 (B-003): the same task-active / task-ready / review-not-
        # cleared candidates `build_team_report` derives from severities 7,
        # 8 and 11, read here for the SAME dossier and fed into the SAME
        # reducer `_next_action` below now uses, so a target with nothing in
        # sections 1-4 but a task still open, ready, or a review still
        # pending is never reported as "nothing blocking here". Empty when
        # no `08-plan.json` exists yet at this target: a plain repository
        # with no plan anywhere keeps producing exactly the next-action text
        # it always has (see `_change_ladder_candidates`).
        ladder_candidates.extend(_change_ladder_candidates(root, target_doss, head_sha, prefix))

    sections = (broken_claims, merge_blockers, active_conflicts, missing_evidence,
               sound_evidence)
    next_action_detail = _next_action(sections, scope, ladder_candidates)
    next_action = "%s %s" % (next_action_detail["reason"], _scope_sentence(scope))

    notes = {
        "brokenClaims": _section_line(
            broken_claims,
            ev["inspected"] or any(s["dispositionExists"] for s in summaries),
            "%s; disposition %s"
            % (ev["note"], "present" if any(s["dispositionExists"] for s in summaries)
               else "absent")),
        "mergeBlockers": _section_line(
            merge_blockers,
            any(s["intakeExists"] for s in summaries) or tk["inspected"] or ev["count"] > 0
            or scope["diffRange"] is not None,
            "%s; %s; %s"
            % (_intake_summary_text(summaries), tk["note"],
               scope["diffRange"] or ("diff NO-DATA: %s" % scope["diffProblem"]))),
        "activeConflicts": _section_line(active_conflicts, tk["inspected"], tk["note"]),
        "missingEvidence": _section_line(
            missing_evidence,
            any(s["intakeExists"] and s["tier"] is not None for s in summaries),
            _missing_evidence_summary_text(summaries)),
        "soundEvidence": _section_line(sound_evidence, ev["inspected"], ev["note"]),
    }

    return {
        "schemaVersion": SCHEMA_VERSION,
        "tool": "sbe status",
        "toolVersion": version(),
        "generatedAt": _iso(now),
        "root": root,
        "scope": scope,
        "brokenClaims": broken_claims,
        "mergeBlockers": merge_blockers,
        "activeConflicts": active_conflicts,
        "missingEvidence": missing_evidence,
        "soundEvidence": sound_evidence,
        "notes": notes,
        "nextAction": next_action,
        # LANE C1 (B-003): the SAME reducer output the rendered `nextAction`
        # string above was composed from (`reason` plus the scope
        # sentence), carried through structured so a caller can match on
        # `actionId` instead of parsing prose. See `lifecycle.
        # reduce_next_action`.
        "nextActionDetail": {k: next_action_detail[k]
                             for k in ("actionId", "label", "reason", "basis")},
        "handover": handover_entries,
        # P5: a list of plain sentences, one per target whose newest dated
        # record (10-approval.json or 11-review.json) is bound to a commit
        # older than HEAD, each naming the commit range that landed since.
        # Empty, never a bare boolean, when every target's records are
        # fresh or nothing is dated at all -- see `_possibly_behind_note`.
        "possiblyBehind": possibly_behind,
        # H3: {"verdict", "detail"}, never a bare boolean -- see
        # `deployment_drift`. Root-level and computed once, like the
        # evidence store above, since a deployed ref names what is running
        # for the WHOLE checkout, not one dossier.
        "deploymentDrift": deployment_drift(root, head_sha),
    }


def any_blocking(data):
    """True when any of sections 1-4 (never section 5) carries an item; the
    exit-code rule, in one place so the CLI and any future caller agree."""
    return bool(data["brokenClaims"] or data["mergeBlockers"] or data["activeConflicts"]
               or data["missingEvidence"])


def render_text(data):
    out = ["sbe status: %s" % data["root"]]
    section_pairs = (
        ("BROKEN CLAIMS", data["brokenClaims"], data["notes"]["brokenClaims"]),
        ("MERGE BLOCKERS", data["mergeBlockers"], data["notes"]["mergeBlockers"]),
        ("ACTIVE CONFLICTS", data["activeConflicts"], data["notes"]["activeConflicts"]),
        ("MISSING EVIDENCE", data["missingEvidence"], data["notes"]["missingEvidence"]),
        ("COMPLETED EVIDENCE", data["soundEvidence"], data["notes"]["soundEvidence"]),
    )
    for name, items, empty_note in section_pairs:
        out.append("")
        out.append("%s:" % name)
        if items:
            for item in items:
                out.append("  - %s" % item["finding"])
        else:
            out.append("  %s" % empty_note)
    if data.get("possiblyBehind"):
        out.append("")
        out.append("POSSIBLY BEHIND:")
        for note in data["possiblyBehind"]:
            out.append("  - %s" % note)
    drift = data.get("deploymentDrift")
    if drift and drift.get("verdict") != "NO-DATA":
        out.append("")
        out.append("DEPLOYMENT: %s" % drift["detail"])
    out.append("")
    out.append("NEXT ACTION: %s" % data["nextAction"])
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# The team view: every active change in one blocker-first report.
# Spec: docs/specs/2026-07-30-sbe-status-team.md. Zero network by design:
# approval facts come only from a saved 10-approval.json, review facts only
# from a saved 11-review.json, and their staleness against the current head
# is DERIVED and labeled so. Findings carry a `basis` honesty field: observed
# (read this run), derived (computed from observed values), unavailable (a
# source that could not be read, which keeps its severity slot visible
# instead of vanishing).
# ---------------------------------------------------------------------------

TEAM_SEVERITIES = {
    1: "broken claims", 2: "merge blockers", 3: "scope conflicts",
    4: "stale evidence", 5: "missing approvals", 6: "convergence failures",
    7: "active tasks", 8: "ready tasks", 9: "completed changes",
    10: "next action",
    # 11, review record: deliberately OUTSIDE 1..6, so `team_blocking` below
    # never blocks a merge on this slot. A missing review is what every one
    # of this repository's nine merged pull requests would show today (human
    # review has never run here), and the repository's own law is that
    # absence is NO-DATA, never a pass and never a block (see cli.py's
    # `_record_review`): making a brand new, never-yet-produced kind of
    # record retroactively MERGE-BLOCKING the day it is introduced would
    # turn a NO-DATA fact into a block by construction, which is exactly the
    # confusion that law exists to forbid. A record that DOES exist and is
    # stale still blocks: that finding is filed at severity 4, the same
    # "stale evidence" slot approval and convergence staleness already use,
    # because staleness is not absence, it is a record disagreeing with the
    # commit in front of it, and this project already treats that as
    # blocking for the other two stores.
    11: "review record",
    # MAJOR A4: missing evidence (the plan's own verification commands were
    # never run under `sbe evidence run`) used to be crammed into severity
    # 5 alongside "no approval report is saved", an unrelated fact that
    # happened to share the same slot. The plain-status side already keeps
    # a separate MISSING EVIDENCE section, apart from its MERGE BLOCKERS;
    # this mirrors that separation for team. Appended at 12 rather than
    # inserted earlier: `lifecycle.TEAM_SEVERITY_TO_RUNG` (owned by
    # lifecycle.py, not this file) maps every OTHER severity number here to
    # its own priority rung already, and renumbering any of 1-11 would move
    # every one of those existing mappings out from under itself. See the
    # registration right after this dict for the one new entry that table
    # needs for THIS number, and the docstring there for why it lives here
    # instead of in lifecycle.py.
    12: "missing evidence",
}

# MAJOR A4 (continued): `lifecycle.TEAM_SEVERITY_TO_RUNG` is the one table
# `build_team_report`'s own severity-10 "next action" finding (and
# `build_report`'s next-action reducer) both read every OTHER entry of
# `TEAM_SEVERITIES` through, so severity 12 needs a matching entry there
# too or `lifecycle.reduce_next_action`'s own lookup raises `KeyError` the
# first time a severity-12 finding is ever computed for a change. That
# table is lifecycle.py's own, by that module's explicit design ("This
# module owns the ONE priority ladder now"), and belongs there; it is
# registered from here, once, at import time, only because this severity
# number is new in THIS release and the file that owns the table is not
# one this change touches. `setdefault` rather than a plain assignment, so
# re-importing this module twice in one process (as several of this
# project's own fixtures do) can never raise on a second identical
# registration.
lifecycle.TEAM_SEVERITY_TO_RUNG.setdefault(12, lifecycle.RUNG_MISSING_EVIDENCE)


def _finding(change, severity, verdict, evidence, commit, owner, next_action, basis,
             detail):
    return {"change": change, "severity": severity, "verdict": verdict,
            "evidence": evidence, "commit": commit, "owner": owner,
            "nextAction": next_action, "basis": basis, "detail": detail}


def _design_roots(root):
    """(roots, refusals). `roots` is every directory (relative to `root`) safe
    to walk for dossiers: the default "design" plus any designRoots entry from
    .sbe/team-profile.json that resolves INSIDE the repository root. An entry
    that would resolve outside the root (a ".." escape, or an absolute path
    elsewhere on disk) is never added to `roots` and never walked; it comes
    back in `refusals`, by its own literal spelling, so the caller can surface
    a visible refusal instead of a silent skip or a directory traversal."""
    roots = ["design"]
    refusals = []
    profile = os.path.join(root, ".sbe", "team-profile.json")
    if os.path.isfile(profile):
        try:
            extra = json.loads(io.open(profile, encoding="utf-8").read())
        except (ValueError, OSError):
            extra = None  # sbe: allow-silent  (not silent: the profile is optional
            #        config; a broken one leaves the default root and discovery
            #        still runs, and is not itself a containment problem)
        root_abs = os.path.abspath(root)
        for entry in ((extra.get("designRoots", []) or []) if extra else []):
            if not isinstance(entry, str) or not entry.strip():
                continue
            candidate = os.path.abspath(os.path.join(root, entry))
            if candidate == root_abs or candidate.startswith(root_abs + os.sep):
                if entry not in roots:
                    roots.append(entry)
            else:
                refusals.append(entry)
    return roots, refusals


def _has_validated_plan(doss):
    """True when `doss/08-plan.json` parses as an object carrying a
    non-empty `tasks` list, every entry itself an object naming an `id`:
    MAJOR A6's own bar for "validated" (the same minimal shape every other
    reader of a plan in this file, `_plan_owns` and
    `_change_ladder_candidates` among them, already assumes without
    checking it explicitly). A plan this loose or this broken is not a
    dossier worth discovering on its own; it stays invisible exactly as it
    always has, and is not what this function exists to surface."""
    plan = _read_json_or_none(os.path.join(doss, "08-plan.json"))
    if not isinstance(plan, dict):
        return False
    tasks = plan.get("tasks")
    return bool(tasks) and isinstance(tasks, list) and all(
        isinstance(t, dict) and t.get("id") for t in tasks)


def _team_changes(root):
    """([(name, dossier path)], refusals): every dossier discovered under a
    safe root, plus any designRoots entry `_design_roots` refused.

    MAJOR A6: a directory is discovered when it carries `00-intake.json`
    OR a validated `08-plan.json` (see `_has_validated_plan`). Before this,
    discovery keyed on the intake file alone, so a dossier whose intake was
    deleted, or that a derived plan was written into without one ever being
    recorded, was invisible to `sbe status --team`, to `sbe status`'s own
    CR-06 dossier discovery (both call this function), and to `sbe map`
    (built entirely from the team report): a plan and its tasks plainly
    existed on disk and this function reported the directory as though it
    did not."""
    changes = []
    roots, refusals = _design_roots(root)
    for rel in roots:
        base_dir = os.path.join(root, rel)
        if not os.path.isdir(base_dir):
            continue
        for name in sorted(os.listdir(base_dir)):
            doss = os.path.join(base_dir, name)
            if (os.path.isfile(os.path.join(doss, "00-intake.json"))
                    or _has_validated_plan(doss)):
                changes.append((name, doss))
    return changes, refusals


def _read_json_or_none(path):
    try:
        return json.loads(io.open(path, encoding="utf-8").read())
    except (ValueError, OSError):
        return None


#: The fields a review record must carry for this run to trust anything it
#: says. Named once, here, so the write side (cli.py's `_record_review`) and
#: this read side cannot drift into two different ideas of "complete".
_REVIEW_REQUIRED_FIELDS = ("headSha", "reviewer", "reviewerType", "result")


def _read_review_record(path):
    """("missing", None), ("malformed", <why, as text>) or ("ok", <dict>).

    Deliberately NOT `_read_json_or_none`, which folds "the file does not
    exist" and "the file exists and will not parse" into the same None: that
    conflation is exactly right for convergence and approval above, where an
    absent report and an unreadable one are both already read as NO-DATA and
    nobody has had reason to tell them apart. A review record is held to a
    stricter, three-state law instead (see the call site): a missing record
    is NO-DATA, and a record that is present but broken, whether unparseable
    JSON or missing one of its own required fields, is FAIL, because that is
    proof somebody tried and left something nobody can trust, which is a
    worse fact than nobody having reviewed anything yet.
    """
    if not os.path.exists(path):
        return "missing", None
    try:
        data = json.loads(io.open(path, encoding="utf-8").read())
    except (ValueError, OSError) as exc:
        return "malformed", "does not parse: %s" % exc
    if not isinstance(data, dict):
        return "malformed", "the top-level JSON value is not an object"
    missing = [k for k in _REVIEW_REQUIRED_FIELDS if not data.get(k)]
    if missing:
        return "malformed", "missing required field(s): %s" % ", ".join(missing)
    return "ok", data


#: LT-202: normalized findings inside `11-review.json`. The fields a
#: `structuredFindings` entry must carry for THIS entry, specifically, to be
#: trusted, named once here so the write side (cli.py's
#: `_merge_finding_group`) and this read side cannot drift into two
#: different ideas of "complete". `fingerprint` is included even though
#: nothing here recomputes it: an entry missing its own fingerprint is
#: exactly as untrustworthy as one missing its category, because a fresh
#: `sbe review --write --findings-json` run always writes one.
_STRUCTURED_FINDING_REQUIRED_FIELDS = (
    "fingerprint", "reviewer", "category", "severity", "confidence",
    "introducedByChange", "location", "failure", "status")
_STRUCTURED_FINDING_CONFIDENCE = ("high", "medium", "low")
_STRUCTURED_FINDING_INTRODUCED = ("yes", "no", "unknown")
#: The `findingsSchemaVersion` values this installation can read. LT-202's
#: own migration path: a record naming a version outside this tuple is
#: MALFORMED rather than silently parsed as today's shape, exactly the way
#: an unrecognized `schemaVersion` would be treated anywhere else in this
#: package, and a future shape change grows this tuple rather than replacing
#: it, so an old record naming "1.0" never stops being readable.
_STRUCTURED_FINDINGS_SCHEMA_VERSIONS = ("1.0",)


def _malformed_structured_finding(index, item):
    """Every reason `item` (the `index`-th entry of a `structuredFindings`
    array already known to be a list) cannot be trusted, or `[]` when it
    can. A malformed entry is reported by its own index, the same
    specificity `_read_review_record` already holds a malformed record's
    parse error to, so "some finding is broken" never has to stand in for
    "finding 3 is broken"."""
    if not isinstance(item, dict):
        return ["structuredFindings[%d] is not an object" % index]
    reasons = []
    for field in _STRUCTURED_FINDING_REQUIRED_FIELDS:
        if not item.get(field):
            reasons.append("structuredFindings[%d] missing %r" % (index, field))
    confidence = item.get("confidence")
    if confidence is not None and confidence not in _STRUCTURED_FINDING_CONFIDENCE:
        reasons.append("structuredFindings[%d] confidence %r is not one of %s"
                       % (index, confidence, _STRUCTURED_FINDING_CONFIDENCE))
    introduced = item.get("introducedByChange")
    if introduced is not None and introduced not in _STRUCTURED_FINDING_INTRODUCED:
        reasons.append("structuredFindings[%d] introducedByChange %r is not one of %s"
                       % (index, introduced, _STRUCTURED_FINDING_INTRODUCED))
    return reasons


def _read_structured_findings(review):
    """("absent", None), ("malformed", <why, as text>) or ("ok", <list>) for
    the `structuredFindings` LT-202 can add to a review record `review` that
    has ALREADY parsed as a trustworthy dict (the caller only reaches this
    once `_read_review_record` has already returned "ok"; a record broken at
    the top level is reported through that path instead, and this one is
    never called for it, the same way `_same_identity` below is only ever
    called once an author is already known).

    "absent" is the honest, unremarkable case for every review record
    LT-202 predates, and for one an LT-202-era `sbe review --write` wrote
    without `--findings-json`: no structured findings were ever recorded,
    which is a fact, not an accusation, exactly the law
    `_read_review_record`'s own docstring already states for a missing
    11-review.json altogether; nothing here invents a finding to fill the
    silence. "malformed" covers every way a PRESENT value cannot be
    trusted: `structuredFindings` given without its own
    `findingsSchemaVersion` (the explicit migration path this sub-schema
    requires, so a reader is never left guessing which shape it is looking
    at), a `findingsSchemaVersion` this installation does not recognize, a
    `structuredFindings` value that is not a list, or any one entry failing
    `_malformed_structured_finding`. A malformed finding is FAIL here, never
    silently dropped from the list as though it were merely absent.
    """
    if "structuredFindings" not in review:
        return "absent", None
    version = review.get("findingsSchemaVersion")
    if not version:
        return "malformed", ("structuredFindings is present without "
                             "findingsSchemaVersion: a record must name which shape "
                             "its findings are in to be trusted")
    if version not in _STRUCTURED_FINDINGS_SCHEMA_VERSIONS:
        return "malformed", ("findingsSchemaVersion %r is not one this installation "
                             "recognizes (knows: %s)"
                             % (version, ", ".join(_STRUCTURED_FINDINGS_SCHEMA_VERSIONS)))
    payload = review.get("structuredFindings")
    if not isinstance(payload, list):
        return "malformed", "structuredFindings is present but is not a list"
    reasons = []
    for i, item in enumerate(payload):
        reasons.extend(_malformed_structured_finding(i, item))
    if reasons:
        return "malformed", "; ".join(reasons)
    return "ok", payload


#: LANE B-006: every review record `_record_review` (cli.py) ever superseded
#: with a later `--write` is archived, verbatim, as one JSON object per line
#: into this append-only file beside `11-review.json`, before the new record
#: replaces it (see cli.py's `_archive_prior_review`). Named once, here, so
#: the write side and this read side cannot drift into two different ideas
#: of where history lives.
_REVIEW_HISTORY_FILENAME = "11-review-history.jsonl"


def _read_review_history(path):
    """Every historical review record ever archived to the append-only
    `path` (see `_REVIEW_HISTORY_FILENAME` above), as a list of dicts,
    oldest first.

    This backs a DISCLOSURE, not a pass/fail gate the way
    `_read_review_record` is: a missing file is the ordinary case (nothing
    has ever been overwritten yet) and reads as `[]`, not an error, and an
    unreadable file or a single line that will not parse as a JSON object is
    skipped rather than escalated, so a broken line here can never make the
    OTHER, readable lines invisible to the one check that reads this file.
    The record this line was archived FROM already went through
    `_record_review`'s own well-formed write path, so a line failing to
    parse here is itself already a fact worth trusting less, never a reason
    to hide every other line beside it.
    """
    if not os.path.exists(path):
        return []
    try:
        text = io.open(path, encoding="utf-8").read()
    except OSError:
        return []
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:  # sbe: allow-silent this file backs a disclosure, not a
            # gate: one broken line must not hide every other, readable line beside
            # it, and `_archive_prior_review` (cli.py) already wraps a source record
            # that will not parse as JSON in a `{"corrupt": true, ...}` marker before
            # it ever reaches this file, so a line failing here is a SECOND, unrelated
            # corruption this function has no way to name more specifically than skipping it.
            continue
        if isinstance(item, dict):
            entries.append(item)
    return entries


def _commit_author(root, sha):
    """(name, email) that authored `sha` in `root`.

    Raises `OSError` when git itself could not be run at all (no `git` on
    PATH, a permission problem): that is a DIFFERENT fact from "this sha does
    not resolve", and collapsing the two into the same silent None would make
    "the tool that checks self-review is broken here" read identically to
    "this commit is not in the history", which is exactly the confusion this
    project's own law forbids. It is not caught here; the caller (this
    module's `build_team_report`) catches it once, at the one place that
    turns either failure into its own explicit, differently-worded NO-DATA
    finding, never a pass either way.

    Returns (None, None), not an exception, when git DID run and simply could
    not resolve `sha` to a commit (a wrong sha, a shallow clone, history that
    has since been rewritten): that is the ordinary "not found" case, and
    `None` is a real answer for it, not a guess.
    """
    if not sha:
        return None, None
    code, out, _err = _git(["log", "-1", "--format=%an\x1f%ae", sha], root)
    if code != 0 or not out.strip():
        return None, None
    parts = out.strip().splitlines()[0].split("\x1f")
    if len(parts) != 2 or not parts[0].strip():
        return None, None
    return parts[0].strip(), parts[1].strip()


def _same_identity(reviewer, author_name, author_email):
    """True when `reviewer`, as recorded in a review record, reads as the
    same person as a commit's (author_name, author_email), folding case and
    surrounding space the way a git identity naturally varies: a bare name, a
    bare email, or "Name <email>" typed as one string. Mirrors the judgement
    `prverify.py` already makes for GitHub approvals (an approval whose login
    equals the pull request's author login is "approving their own change");
    this is the same check made from local git history instead of a GitHub
    API, because status makes no network call, by construction.

    Called only once an author IS known (the caller reads an unknown author
    as NO-DATA and never calls this to guess through it), so an empty
    `reviewer` here can only mean a malformed record, which the caller has
    already turned away before this runs.
    """
    reviewer_folded = (reviewer or "").strip().lower()
    if not reviewer_folded:
        return False
    name_folded = (author_name or "").strip().lower()
    email_folded = (author_email or "").strip().lower()
    return (reviewer_folded == name_folded or reviewer_folded == email_folded
           or (bool(email_folded) and email_folded in reviewer_folded))


def _closed_clean(records):
    """True when at least one record for a task id is closed, and not FORCED.
    A FORCED close records a disposition, never a completion; see
    `work._dependency_problem`, whose rule this mirrors rather than retypes."""
    return any(r.get("status") == "closed" and not r.get("forced") for r in records)


def _superseded_by_shared_ladder(finding, all_done):
    """A2 ROUND 2: True when `finding` (one of a change's own recorded
    findings, inside `build_team_report`) should not compete for that
    change's severity-10 next action, because `_task_and_approval_
    candidates` -- the SAME shared function `_change_ladder_candidates`
    (`build_report`'s own ladder) calls -- derives the identical fact a
    second, correctly-gated time. See that function's own docstring for why
    the gate on severities 5, 6 and 12 is `not all_done` rather than always.

    Severity 8 (both its "no plan" and "ready task" flavors) and the "open
    task" flavor of severity 7 (verdict NO-DATA; its "forced close" FAIL
    flavor is untouched, since nothing else derives that candidate) are
    ALWAYS superseded. Severities 5 (missing approval), 6 (missing
    convergence) and 12 (missing evidence) are superseded ONLY in their
    NO-DATA ("not attempted yet") flavor, and ONLY while `not all_done`: a
    FAIL at any of the three (something already ran and failed) is a
    different fact from "not attempted yet" and stays eligible regardless
    of task state, unchanged from before this round.

    `finding` stays recorded in `build_team_report`'s own `findings` list
    exactly as before either way: this only removes its candidacy for the
    severity-10 reduction, never its disclosure.
    """
    severity = finding["severity"]
    if severity == 8:
        return True
    if severity == 7 and finding["verdict"] == "NO-DATA":
        return True
    if severity in (5, 6, 12) and finding["verdict"] == "NO-DATA" and not all_done:
        return True
    return False


def build_team_report(path):
    """{"root", "headCommit", "changes": [names], "findings": [...]}, findings
    sorted most severe first, deterministically."""
    root = os.path.abspath(path)
    head = _git_head(root)
    findings = []
    changes, root_refusals = _team_changes(root)

    for entry in root_refusals:
        findings.append(_finding(
            "(team-profile.json)", 3, "FAIL", entry, head, None,
            "point designRoots entry %r in .sbe/team-profile.json at a directory "
            "inside this repository, or remove the entry" % entry,
            "unavailable",
            "designRoots entry %r resolves outside the repository root and was "
            "REFUSED: it is not walked for dossiers, and no dossier under it is "
            "discovered" % entry))

    registry_tasks, registry_problem, registry_data = [], None, None
    try:
        registry_data = tasks_mod.load_registry(root)
        registry_tasks = registry_data.get("tasks", [])
    except tasks_mod.RegistryUnusable as exc:
        registry_problem = str(exc)

    evidence_dir = tasks_mod.evidence_dir(root)
    ev = _scan_evidence(root, evidence_dir)

    open_by_change = {}
    plan_owns_by_change = {}
    handover_entries = []
    plan_only_changes = []
    for name, doss in changes:
        change_start = len(findings)

        # MAJOR A6: purely additive, mirroring the handover list right
        # below: a change discovered ONLY because it carries a validated
        # 08-plan.json (see `_has_validated_plan` / `_team_changes`), with
        # no `00-intake.json` recorded for it at all, is named here by
        # itself rather than silently read the same as an intake-bearing
        # change everywhere else in this report.
        if not os.path.isfile(os.path.join(doss, INTAKE_REL)):
            plan_only_changes.append({
                "change": name,
                "detail": "%s carries a validated 08-plan.json but no %s: exists, "
                         "no intake recorded yet" % (doss, INTAKE_REL),
            })

        # LT-302.B: one handover entry per change, purely additive (see the
        # note above `_handover_state`, right before `build_report`): never
        # folded into the severity 1..11 findings this loop builds below,
        # so it can never brush against `team_blocking`'s 1..6 range or this
        # project's own hard-pinned 1..11 findings-contract test.
        h_entry = _handover_state(doss, head)
        h_entry["change"] = name
        handover_entries.append(h_entry)

        plan = _read_json_or_none(os.path.join(doss, "08-plan.json"))
        plan_ids = set()
        plan_owns = set()
        commands = 0
        if plan:
            for task in plan.get("tasks", []):
                plan_ids.add(task.get("id"))
                commands += len(task.get("verificationCommands", []))
                for p in task.get("owns") or []:
                    if isinstance(p, str) and p.strip():
                        plan_owns.add(p)
        plan_owns_by_change[name] = plan_owns

        # A2 ROUND 2: whether every task this plan declares is closed
        # clean, the SAME predicate the severity 9 "completed changes"
        # finding below already computes (and `_closed_clean` itself
        # already applies task by task) -- read once here into a variable
        # that survives past the `if not registry_problem:` block that
        # computes it, so the severity-10 derivation further down (which
        # runs whether or not a plan or a readable registry exists) always
        # has a defined answer rather than risking a `NameError` on a
        # `plan_tasks`/`records_by_id` that a registry failure never built.
        # Stays `False` (never gated open) exactly when severity 8/9 also
        # stay silent: no plan, or a registry this run could not read.
        all_done = False

        if registry_problem:
            findings.append(_finding(
                name, 3, "NO-DATA", tasks_mod.registry_path(root), head, None,
                "restore the registry file; its contents still record open fences",
                "unavailable",
                "the task registry could not be read (%s), so scope conflicts for this "
                "change are unknowable and this slot stays visible" % registry_problem))
            records = []
        else:
            records = [r for r in registry_tasks if r.get("id") in plan_ids] if plan else []
        open_records = [r for r in records if r.get("status") == "open"]
        open_by_change[name] = open_records

        for rec in records:
            if "forced" in str(rec.get("status", "")) or rec.get("forced"):
                findings.append(_finding(
                    name, 7, "FAIL", "task %s" % rec.get("id"), head,
                    rec.get("agent"),
                    "a FORCED close never satisfies a dependent; redo the task cleanly "
                    "or record why it stands",
                    "observed",
                    "task %s was closed FORCED by %s and stays loud everywhere it "
                    "appears" % (rec.get("id"), rec.get("agent"))))

        for rec in open_records:
            findings.append(_finding(
                name, 7, "NO-DATA", "task %s" % rec.get("id"), head, rec.get("agent"),
                "finish or check the task with sbe work", "observed",
                "task %s is open, held by %s" % (rec.get("id"), rec.get("agent"))))

            # Severity 2: a receipt-free MERGE BLOCKER this run can actually
            # see for itself, an open task whose tree already violates its own
            # declaration, read by the SAME postcondition `sbe task close`
            # would refuse against; no second copy of that rule lives here.
            base_commit = rec.get("baseCommit")
            if not base_commit:
                findings.append(_finding(
                    name, 2, "NO-DATA", "task %s" % rec.get("id"), head, rec.get("agent"),
                    "record a baseCommit on task %s so its declared scope can be "
                    "checked against what actually changed" % rec.get("id"),
                    "unavailable",
                    "task %s carries no baseCommit, so its postcondition against "
                    "declared ownership cannot be computed" % rec.get("id")))
            else:
                try:
                    post = tasks_mod.postcondition(root, rec, evidence_dir)
                except tasks_mod.DiffUnavailable as exc:
                    findings.append(_finding(
                        name, 2, "NO-DATA", "task %s" % rec.get("id"), head,
                        rec.get("agent"),
                        "restore the declared worktree or base commit for task %s so "
                        "its scope can be checked" % rec.get("id"),
                        "unavailable",
                        "the postcondition for task %s could not be computed: %s"
                        % (rec.get("id"), exc)))
                else:
                    bad = list(post["violations"]) + list(post.get("receiptViolations")
                                                          or [])
                    if bad:
                        findings.append(_finding(
                            name, 2, "FAIL", "task %s" % rec.get("id"), head,
                            rec.get("agent"),
                            "task %s changed %s outside its declared ownership; narrow "
                            "the change or widen the declaration before closing"
                            % (rec.get("id"), ", ".join(bad)),
                            "observed",
                            "task %s's tree carries change(s) outside its declared "
                            "ownedPaths: %s" % (rec.get("id"), ", ".join(bad))))

        if not plan:
            findings.append(_finding(
                name, 8, "NO-DATA", os.path.join(doss, "08-plan.json"), head, None,
                "run sbe plan %s --write to derive the task graph" % doss,
                "observed",
                "no plan exists for this change yet, so there are no tasks to ready: "
                "a starting state to move past, not a ready task and not an error"))
        else:
            conv = _read_json_or_none(os.path.join(doss, "09-convergence.json"))
            if conv is None:
                findings.append(_finding(
                    name, 6, "NO-DATA", os.path.join(doss, "09-convergence.json"),
                    head, None,
                    "run sbe converge %s --base <sha> --head <sha>" % doss,
                    "observed",
                    "a plan exists and no convergence report does; unexamined is not "
                    "PASS"))
            else:
                final = conv.get("final")
                bound = conv.get("head")
                if head and bound and bound != head:
                    findings.append(_finding(
                        name, 4, "FAIL", "09-convergence.json", bound, None,
                        "re-run sbe converge against the current head", "derived",
                        "the convergence report binds to %s but the repository head is "
                        "%s: stale" % (bound[:12], head[:12])))
                elif final in ("FAIL", "REVIEW-REQUIRED"):
                    findings.append(_finding(
                        name, 6, final, "09-convergence.json", bound, None,
                        "amend the dossier or the implementation, then regenerate plan, "
                        "evidence and convergence", "observed",
                        "convergence recorded %s" % final))

            # A2 ROUND 2 / TARGET 3: reuse `_approval_ladder_candidate`'s own
            # approval judgement instead of re-deriving it here. The two used
            # to disagree (this site's remedy text dropped the "(MISSING
            # APPROVAL)" suffix, and its stale/FAIL branches recorded the
            # bare filename "10-approval.json" instead of the full dossier
            # path), which is exactly the kind of drift a second copy of the
            # same judgement invites. `_approval_ladder_candidate` returns
            # `_finding`-shaped entries with `change` set to `None` (see its
            # own docstring); `change` is filled in here from this loop's own
            # `name`, the same way every other finding in this loop already
            # is.
            for cand in _approval_ladder_candidate(doss, head, ""):
                entry = {k: v for k, v in cand.items() if k not in ("rung", "reason")}
                entry["change"] = name
                findings.append(entry)

            # A review record, `11-review.json`, written by `sbe review
            # --write` (see cli.py). Held to a stricter three-state law than
            # convergence and approval above: those two fold "absent" and
            # "present but will not parse" into the same NO-DATA, because
            # nobody has ever had reason to tell the two apart here. A review
            # record is: absence is NO-DATA (nobody has reviewed yet, which is
            # a fact, not an accusation); a record present but unparseable, or
            # missing one of its own required fields, is FAIL (somebody tried
            # and left a record nobody can trust, which is a worse fact than
            # silence); and a record that parses is judged from what it says,
            # never from what its own "result" field claims about itself, the
            # same way a stale approval is judged from the head sha it is
            # bound to rather than from its own FINAL.
            review_path = os.path.join(doss, "11-review.json")
            review_state, review_payload = _read_review_record(review_path)
            if review_state == "missing":
                findings.append(_finding(
                    name, 11, "NO-DATA", review_path, head, None,
                    "run sbe review %s --write --reviewer <name> --reviewer-type "
                    "human|model|independent-model --result "
                    "approved|changes-required|unverifiable to record one" % doss,
                    "observed",
                    "no review record (11-review.json) is saved for this change; "
                    "absence is a fact, not an accusation"))
            elif review_state == "malformed":
                findings.append(_finding(
                    name, 11, "FAIL", review_path, head, None,
                    "regenerate 11-review.json with sbe review %s --write" % doss,
                    "observed",
                    "%s cannot be trusted (%s): a record nobody can read is not a clean "
                    "pass" % (review_path, review_payload)))
            else:
                review = review_payload
                bound = review.get("headSha")
                # Hoisted out of the "not stale" branch below (LANE B-006):
                # the history-disclosure block after the structured-findings
                # block needs `result` whether or not this record is stale,
                # the same way it already needs `bound`, and a value read
                # once here is the same value the "not stale" branch below
                # would otherwise re-read from the same dict a second time.
                result = review.get("result")
                if head and bound and bound != head:
                    findings.append(_finding(
                        name, 4, "FAIL", "11-review.json", bound, review.get("reviewer"),
                        "re-run sbe review against the current head and --write again",
                        "derived",
                        "the review record binds to %s but the repository head is %s: "
                        "stale review" % (bound[:12], head[:12])))
                else:
                    reviewer = review.get("reviewer")
                    finding_list = review.get("findings")
                    finding_count = len(finding_list) if isinstance(finding_list, list) else 0
                    risk_list = review.get("acceptedRisks")
                    risk_count = len(risk_list) if isinstance(risk_list, list) else 0
                    # Two different NO-DATA reasons, kept apart rather than
                    # collapsed into one: `git_error` is set only when git
                    # itself could not be run at all (no `git` on PATH, a
                    # permission problem), which is a different fact from
                    # "git ran and this sha does not resolve" (`author_name`
                    # staying None below). Neither is ever a pass, and each
                    # names what actually happened rather than sharing a
                    # single silent "could not check" sentence.
                    git_error = None
                    try:
                        author_name, author_email = _commit_author(root, bound)
                    except OSError as exc:
                        author_name = author_email = None
                        git_error = exc
                    if git_error is not None:
                        findings.append(_finding(
                            name, 11, "NO-DATA", review_path, bound, reviewer,
                            "install or repair git on this machine so the reviewed "
                            "commit's author can be checked", "unavailable",
                            "git could not be run to check the author of the reviewed "
                            "commit %s (%s): a self-review check this run could not "
                            "even attempt is never a pass" % ((bound or "?")[:12], git_error)))
                    elif author_name is None:
                        findings.append(_finding(
                            name, 11, "NO-DATA", review_path, bound, reviewer,
                            "fetch the reviewed commit so its author can be checked, or "
                            "re-run sbe review once it is reachable", "unavailable",
                            "git ran but could not resolve the reviewed commit %s, so "
                            "its author is unknown and this review cannot be checked "
                            "for self-review: an undetermined author is never a pass"
                            % (bound or "?")[:12]))
                    elif _same_identity(reviewer, author_name, author_email):
                        findings.append(_finding(
                            name, 11, "FAIL", review_path, bound, reviewer,
                            "get an independent reviewer to review %s and record a "
                            "fresh 11-review.json" % doss, "derived",
                            "the review record names %s as reviewer, and %s is the "
                            "author of the reviewed commit %s: an approval naming only "
                            "the author is not an approval"
                            % (reviewer, author_name, (bound or "?")[:12])))
                    elif result != "approved":
                        findings.append(_finding(
                            name, 11, str(result), review_path, bound, reviewer,
                            "resolve what the review flagged, then record a fresh "
                            "11-review.json", "observed",
                            "the saved review record's result is %s (%d finding(s), %d "
                            "accepted risk(s))" % (result, finding_count, risk_count)))
                    else:
                        findings.append(_finding(
                            name, 11, "PASS", review_path, bound, reviewer,
                            "nothing outstanding from review", "observed",
                            "the saved review record is approved by %s (%s), "
                            "independent of the commit author, with %d finding(s) and "
                            "%d accepted risk(s) recorded"
                            % (reviewer, review.get("reviewerType"), finding_count,
                               risk_count)))

                # LT-202: the normalized findings, if this record carries
                # them, read INDEPENDENTLY of the pass/fail/stale judgement
                # just above, whether or not this record is stale: a stale
                # binding is already its own severity-4 finding, and
                # whatever this record's findings actually say is still an
                # observed fact about the record on disk either way. This
                # never invents a severity outside 1..11: it stays inside
                # slot 11 (review record), the same slot the rest of this
                # block already uses, as one further finding beside it
                # rather than a new numbered section.
                # `reviewer` (the loop-scoped variable) is only ever assigned
                # inside the inner "not stale" branch above, so a STALE
                # record would leave it unbound here: this reads
                # `review.get("reviewer")` directly instead, the same way
                # the stale finding itself does at its own append call, and
                # for the same reason.
                struct_reviewer = review.get("reviewer")
                struct_state, struct_payload = _read_structured_findings(review)
                if struct_state == "absent":
                    findings.append(_finding(
                        name, 11, "NO-DATA", review_path, bound, struct_reviewer,
                        "record structured findings with sbe review %s --write "
                        "--findings-json <path> to carry fingerprinted, deduplicated "
                        "findings" % doss, "observed",
                        "this review record carries no structuredFindings: either it "
                        "predates LT-202 or --findings-json was never passed; absence "
                        "is a fact, not an accusation"))
                elif struct_state == "malformed":
                    findings.append(_finding(
                        name, 11, "FAIL", review_path, bound, struct_reviewer,
                        "regenerate 11-review.json with a valid --findings-json file",
                        "observed",
                        "structuredFindings on %s cannot be trusted (%s): a finding "
                        "nobody can read is not a clean pass"
                        % (review_path, struct_payload)))
                else:
                    blocking = [f for f in struct_payload if f.get("blocking")]
                    pre_existing = [f for f in struct_payload
                                    if f.get("introducedByChange") != "yes"]
                    arbitration = [f for f in struct_payload
                                  if f.get("status") == "arbitration"]
                    if blocking:
                        struct_verdict = "FAIL"
                        struct_next = ("resolve the blocking structured finding(s) in "
                                      "%s" % review_path)
                    elif arbitration:
                        struct_verdict = "NO-DATA"
                        struct_next = ("adjudicate the contradicting finding(s) in %s "
                                      "(see docs/CLI.md's adjudication protocol shape)"
                                      % review_path)
                    else:
                        struct_verdict = "PASS"
                        struct_next = "nothing blocking among the structured findings"
                    findings.append(_finding(
                        name, 11, struct_verdict, review_path, bound, struct_reviewer,
                        struct_next, "observed",
                        "%d structured finding(s) recorded: %d blocking, %d "
                        "pre-existing, %d pending arbitration"
                        % (len(struct_payload), len(blocking), len(pre_existing),
                           len(arbitration))))

                # LANE B-006: a second `sbe review --write` at the SAME head
                # used to overwrite `11-review.json` in place, with nothing
                # anywhere recording that a DIFFERENT verdict had ever been
                # recorded at that exact commit; an `approved` write could
                # erase a `changes-required` write undetectably. `cli.py`'s
                # `_record_review` now archives every superseded record into
                # `11-review-history.jsonl` before it writes a new one (see
                # `_archive_prior_review` there), so nothing is lost, but
                # THE RECORD NEVER JUDGES ITSELF (this file's own module
                # note): whether an archived, same-head, different-result
                # entry is worth a reader's attention is worked out here, at
                # read time, the same way staleness and self-review already
                # are, not by the write path. This reads INDEPENDENTLY of
                # every judgement above it, whether or not the current
                # record is stale, for the same reason the structured-
                # findings block just above does: staleness already has its
                # own severity-4 finding, and a same-head verdict flip is a
                # separate, observable fact about the history file either
                # way. `result` is the hoisted `review.get("result")` above
                # (read once whether or not this record is stale), the same
                # variable the "not stale" branch's own PASS/FAIL judgement
                # already used.
                history_path = os.path.join(doss, _REVIEW_HISTORY_FILENAME)
                history_entries = _read_review_history(history_path)
                differing = [e for e in history_entries
                            if e.get("headSha") == bound and e.get("result")
                            and e.get("result") != result]
                if differing:
                    prior_results = sorted(set(e.get("result") for e in differing))
                    findings.append(_finding(
                        name, 11, "FAIL", history_path, bound, struct_reviewer,
                        "read the superseded verdict(s) in %s before trusting the "
                        "current result" % history_path, "derived",
                        "the current review record's result is %r, but %s carries "
                        "%d prior verdict(s) bound to this SAME head %s that said "
                        "otherwise (%s): a later write replaced an earlier, "
                        "different verdict without the commit changing, disclosed "
                        "here rather than silently lost"
                        % (result, history_path, len(differing), (bound or "?")[:12],
                           ", ".join(prior_results))))

            if commands and not ev["clean"] and not ev["broken"]:
                # MAJOR A4: its own severity (12, "missing evidence"), never
                # severity 5 ("missing approvals"): the two are unrelated
                # facts that used to share a slot, and a fresh plan owes
                # both at once.
                findings.append(_finding(
                    name, 12, "NO-DATA", evidence_dir, head, None,
                    "run the plan's verification commands under sbe evidence run",
                    "observed",
                    "%d verification command(s) planned and no receipt exists yet"
                    % commands))

            # Severities 8 and 9 need the registry to be readable to mean
            # anything; when it is not, the severity-3 unavailable finding
            # above already says so, and neither "ready" nor "completed" is
            # guessed at from data this run could not read.
            if not registry_problem:
                plan_tasks = plan.get("tasks", [])
                records_by_id = {}
                for r in records:
                    records_by_id.setdefault(r.get("id"), []).append(r)

                for task in plan_tasks:
                    tid = task.get("id")
                    task_records = records_by_id.get(tid, [])
                    if task_records:
                        continue  # already started or done: not a "ready" candidate
                    deps = [d for d in (task.get("dependsOn") or []) if isinstance(d, str)]
                    blockers = [work_mod._dependency_problem(registry_data, d)
                               for d in deps]
                    blockers = [b for b in blockers if b]
                    if not blockers:
                        findings.append(_finding(
                            name, 8, "NO-DATA", "task %s" % tid, head, None,
                            "run sbe work start %s --plan %s to begin it"
                            % (tid, os.path.join(doss, "08-plan.json")),
                            "derived",
                            "task %s's dependencies are all closed clean and it carries "
                            "no registry record yet: ready to start" % tid))

                all_done = bool(plan_tasks) and all(
                    _closed_clean(records_by_id.get(t.get("id"), [])) for t in plan_tasks)
                if all_done:
                    findings.append(_finding(
                        name, 9, "PASS", os.path.join(doss, "08-plan.json"), head, None,
                        "nothing left to do for this change; open a PR and run sbe pr "
                        "verify",
                        "derived",
                        "every task in the plan is closed clean in the registry"))

        # Severity 10: exactly one next action per change, always, derived
        # from that change's own highest-severity finding recorded above. A
        # change with nothing recorded above still gets one, so the rule
        # never has an exception.
        #
        # LANE C1 (B-003): "highest-severity" is no longer a raw comparison
        # of `severity` numbers -- picked instead by `lifecycle.
        # reduce_next_action`, the SAME reducer `build_report`'s own plain
        # `nextAction` is derived through, over candidates translated from
        # team severity to `lifecycle`'s own rung via `TEAM_SEVERITY_TO_
        # RUNG`. That table is why this changed at all: severity 9
        # ("completed changes") used to sort below severity 11 ("review
        # record") as a bare integer, so a change whose tasks were all
        # closed clean but had never been reviewed recommended "nothing
        # left to do, open a pull request" instead of "run review" --
        # reproduced, not theorized, by `tools/test_sbe_status_team.py`'s
        # own `TestCanonicalNextAction`. Candidates are still handed to the
        # reducer pre-sorted by `(rung, evidence, detail)`, the exact
        # tie-break this file used before this lane, so two findings tied
        # at the same rung resolve exactly as they always have.
        #
        # A2 ROUND 2: `pool` below excludes exactly the findings `_task_
        # and_approval_candidates` derives a second, correctly-gated time --
        # the SAME shared function `_change_ladder_candidates` (`build_
        # report`'s own ladder) calls -- so the two surfaces cannot diverge
        # by construction the way round 1's BLOCKER A2 fix let them.
        # Severity 8 (both its "no plan" and "ready task" flavors) and the
        # "open task" flavor of severity 7 (verdict NO-DATA; its "forced
        # close" FAIL flavor is untouched) are always superseded. Severities
        # 5 (missing approval), 6 (missing convergence) and 12 (missing
        # evidence) are superseded ONLY in their NO-DATA ("not attempted
        # yet") flavor, and ONLY while `not all_done`: a FAIL at any of the
        # three (something already ran and failed) is a different fact from
        # "not attempted yet" and stays eligible regardless of task state,
        # unchanged from before this round. Every excluded finding stays
        # recorded above exactly as before: only its candidacy for THIS
        # reduction is removed.
        this_change = findings[change_start:]
        if this_change:
            pool = [f for f in this_change
                   if not _superseded_by_shared_ladder(f, all_done)]
            ordered = sorted(
                pool,
                key=lambda f: (lifecycle.TEAM_SEVERITY_TO_RUNG[f["severity"]],
                               f["evidence"] or "", f["detail"]))
            candidates = [dict(f, rung=lifecycle.TEAM_SEVERITY_TO_RUNG[f["severity"]])
                         for f in ordered]
            candidates.extend(_task_and_approval_candidates(
                doss, head, "", plan, records, registry_data))
            top = lifecycle.reduce_next_action(candidates)
            entry = _finding(
                name, 10, top["verdict"], top["evidence"], top["commit"], top["owner"],
                top["nextAction"], "derived",
                "next action for %s, derived from its highest-severity finding "
                "(severity %d, %s): %s"
                % (name, top["severity"], TEAM_SEVERITIES[top["severity"]],
                   top["nextAction"]))
            entry["actionId"] = top["actionId"]
            entry["label"] = top["label"]
            findings.append(entry)
        else:
            findings.append(_finding(
                name, 10, "NO-DATA", None, head, None,
                "nothing is recorded for this change yet", "derived",
                "no finding was recorded for %s at any severity, so there is nothing "
                "to derive a next action from" % name))

    for item in ev["broken"]:
        findings.append(_finding(
            "(shared evidence store)", 1, "FAIL", item.get("finding", "receipt"),
            head, None, item.get("remedy", "regenerate the receipt"), "observed",
            item.get("finding", "a receipt failed verification")))

    for item in ev["failing"]:
        covered = set(item.get("coveredFiles") or [])
        attributed = None
        for name, _doss in changes:
            if covered & plan_owns_by_change.get(name, set()):
                attributed = name
                break
        findings.append(_finding(
            attributed or "(shared evidence store)", 2, "FAIL",
            item.get("path", "receipt"), head, None,
            item.get("remedy", "regenerate the receipt"), "observed",
            item.get("finding", "a receipt recorded a nonzero exit code")))

    names = [n for n, _d in changes]
    # Scope conflicts are computed over ALL open registry records, pairwise,
    # because the registry is one global fence table while plan task ids are
    # per-change (every derived plan starts at T01): attributing first and
    # comparing after would let two changes' fences collide invisibly.
    open_all = [] if registry_problem else [r for r in registry_tasks
                                            if r.get("status") == "open"]
    for i, ra in enumerate(open_all):
        for rb in open_all[i + 1:]:
            shared = sorted(set(ra.get("ownedPaths", []))
                            & set(rb.get("ownedPaths", [])))
            holders = {n for n, recs in open_by_change.items()
                       for r in recs if r is ra or r is rb}
            change_label = ", ".join(sorted(holders)) or "(registry)"
            for pth in shared:
                findings.append(_finding(
                    change_label, 3, "FAIL", pth, head,
                    "%s and %s" % (ra.get("agent"), rb.get("agent")),
                    "serialize the two tasks or split the path", "observed",
                    "open task %s (%s) and open task %s (%s) both own %s"
                    % (ra.get("id"), ra.get("agent"), rb.get("id"),
                       rb.get("agent"), pth)))

    findings.sort(key=lambda f: (f["severity"], f["change"], f["evidence"] or "",
                                 f["detail"]))

    # MAJOR A7: task completion, stated plainly, sourced from the SAME
    # closed (non-FORCED) registry records `_closed_clean` already judges
    # per task above; a global, sorted list here rather than a second scan.
    # `[]` when the registry could not be read at all: the existing
    # severity-3 unavailable finding above already names that failure, and
    # a completion list built from data this run could not read would be a
    # guess, never a fact.
    completed_tasks = []
    if not registry_problem:
        completed_tasks = sorted(
            r.get("id") for r in registry_tasks
            if r.get("status") == "closed" and not r.get("forced") and r.get("id"))

    return {"tool": "sbe status --team", "root": root, "headCommit": head,
            "changes": names, "findings": findings, "handover": handover_entries,
            "planOnly": plan_only_changes, "completedTasks": completed_tasks,
            "basisLegend": "observed: read this run; derived: computed from observed "
                           "values; unavailable: a source that could not be read and "
                           "stays visible"}


#: Ledger 16: the exit-code rule, named explicitly here rather than left to
#: be reconstructed from a bare numeric range at the one call site
#: (`team_blocking`, right below). The severity numbers `team_blocking`
#: treats as actionable, i.e. worth a nonzero exit code: 1 through 6
#: (broken claims, merge blockers, scope conflicts, stale evidence, missing
#: approvals, convergence failures) plus 12 (missing evidence, MAJOR A4's
#: own rung, mirroring MISSING EVIDENCE's place in `any_blocking`'s
#: identical rule for plain `sbe status`).
#: Severities 7 through 11 (active/ready/completed tasks, next action,
#: review record) are deliberately excluded and never move the exit code on
#: their own, by the SAME "absence is a fact, not an accusation" law
#: `TEAM_SEVERITIES`'s own comment states for severity 11: an in-progress
#: task, a ready one, or a review nobody has run yet is a state to report,
#: not a reason to fail a build. Named as an explicit set, not a numeric
#: range, because severity 12 sits outside the contiguous 1-6 span; a
#: caller reading this constant sees the exact boundary without having to
#: reconstruct it from a range plus an exception.
TEAM_BLOCKING_SEVERITIES = frozenset((1, 2, 3, 4, 5, 6, 12))


def team_blocking(data):
    """The one rule this exit code follows: nonzero exactly when at least
    one finding sits at a severity named in `TEAM_BLOCKING_SEVERITIES`.
    This function owns that boundary in exactly one place, so a change to
    which severities block never has to be re-derived at every call site."""
    return any(f["severity"] in TEAM_BLOCKING_SEVERITIES for f in data["findings"])


def render_team(data):
    out = ["sbe status --team: %s" % data["root"],
           "head %s, %d change(s): %s"
           % ((data["headCommit"] or "unresolved")[:12], len(data["changes"]),
              ", ".join(data["changes"]) or "none discovered under design/")]
    # MAJOR A7: task completion, stated plainly, right in the header block
    # rather than left for a reader to infer from the absence of a
    # severity-8 "ready" finding.
    completed = data.get("completedTasks") or []
    if completed:
        out.append("completed tasks: %d closed clean (%s)"
                   % (len(completed), ", ".join(completed)))
    else:
        out.append("completed tasks: none closed clean yet")
    current = None
    for f in data["findings"]:
        if f["severity"] != current:
            current = f["severity"]
            out.append("")
            out.append("%d. %s" % (current, TEAM_SEVERITIES[current].upper()))
        owner = (" [%s]" % f["owner"]) if f["owner"] else ""
        out.append("  %-10s %-16s %s%s" % (f["change"][:10], f["verdict"], f["detail"],
                                           owner))
        out.append("             next action: %s" % f["nextAction"])
    if not data["findings"]:
        out.append("")
        out.append("no findings: no dossier under design/ has anything recorded to read")
    # BLOCKER A3: the JSON `handover` array already carried `status`,
    # `outgoingOwner` and `intendedReceiver` (LT-302.B), but this plain-text
    # renderer never read it at all, so a human running `sbe status --team`
    # with no --json never saw who owned a change after a handover was
    # acknowledged. One line per change whose handover is not "none": the
    # same `detail` sentence `_handover_state` already composed (it already
    # says "ownership has moved to X" for an acknowledged record), read
    # here rather than re-derived a second time.
    handover_lines = [h for h in (data.get("handover") or [])
                      if h.get("status") not in (None, "none")]
    if handover_lines:
        out.append("")
        out.append("OWNERSHIP HANDOVER")
        for h in handover_lines:
            out.append("  %s: %s" % (h.get("change") or "(unnamed)", h.get("detail")))
    # MAJOR A6: a change discovered only because it carries a validated
    # 08-plan.json, with no 00-intake.json recorded for it, is named here
    # by itself, the same purely-additive pattern the handover block above
    # already uses, rather than folded silently into the ordinary change
    # list with nothing to tell it apart.
    plan_only = data.get("planOnly") or []
    if plan_only:
        out.append("")
        out.append("PLAN-ONLY (no intake recorded)")
        for p in plan_only:
            out.append("  %s: %s" % (p.get("change") or "(unnamed)", p.get("detail")))
    return "\n".join(out) + "\n"
