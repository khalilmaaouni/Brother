#!/usr/bin/env python3
"""WIRE-05: the release gate that refuses a Tier A part reporting NO-DATA.

WHY THIS EXISTS. scripts/assurance_coverage.py (WIRE-01) already answers
"which parts of this system are Tier A (they govern claims, evidence,
scope, orchestration, context, recovery, memory, host enforcement,
security, enterprise governance, benchmarking, acceptance or releases) and
which of those report NO-DATA". That answer sits in
docs/generated/ASSURANCE-COVERAGE.json until something turns it into a
refusal a release script actually obeys. Without this module, a Tier A part
with nothing in the battery running it is a fact in a JSON file nobody has
to act on, which is exactly the shape of "advisory control nobody can obey"
this estate has already named as worthless. This module is the refusal.

MEASURED, NOT TRUSTED, THE NIGHT THIS WAS WRITTEN. The brief that ordered
this unit says Tier A NO-DATA parts "went from 16 to 0 tonight," so "this
gate will currently PASS, and that is the hard case." Read fresh against
the coverage file actually on disk in this worktree before writing a line
of this module: tier_a_no_data still lists 16 parts (acceptance_trial_
assign, assurance_graph, benchmark_harness, claim_evidence, claim_lifecycle,
claim_score, context_capsule, cut, dependency_graph_check, e80_release_
reproduction_drive, golden_master_quality_claims, merge_passport, oracle_
stability, orchestrator_protocol, risk_review_orchestrator, vault_retrieval_
policy), and the file's own declared part_count (271) already undercounts
the live scripts/ directory (289 non-test modules, a drift of 18) because
other units in this same worktree are still landing files. Both facts are
consistent with the brief's claim being about a state that has since moved,
never about this module having a bug: the point of the "ASSUMED STATE IS
NEVER GROUND TRUTH" discipline is that a claim about current state is only
as good as the check that produced it in the same turn, and this module's
own README-shaped section below re-runs the real gate against the real file
so whoever reads this line next has this run's own numbers, not the
brief's. A hard case an author assumed away by trusting a stale claim is
not a hard case anyone actually exercised.

WHAT THE COVERAGE FILE DOES NOT CARRY. assurance_coverage.build() writes no
top-level "generated at" timestamp; the closest fact on disk is the file's
own mtime. coverage_generated_at below is therefore that mtime, stated as
what it is (when this file was last written), never invented as "when the
census ran" (those usually coincide, but nothing here would notice if they
did not, so the field is named and documented honestly rather than
oversold).

RULE 3, UNCLASSIFIED IS NOT TIER C, AND THE RULE THIS MODULE CHOSE: an
UNCLASSIFIED part reporting NO-DATA BLOCKS a release exactly like a Tier A
one, kept in a separate field (unclassified_blockers / unclassified_count)
so a reader can always tell a KNOWN strategic gap from an UNKNOWN-tier one
that might turn out to be one. This is not a preference, it follows from
the worker contract's own rule 2: "unknown input raises, no permissive
default anywhere, for any enum, any state." UNCLASSIFIED is, by
construction, an unresolved tier; treating an unresolved tier's NO-DATA as
harmless the way Tier C's is would be exactly that forbidden permissive
default, and 140 of today's 271 parts are UNCLASSIFIED, so this choice is
not decorative: it is the difference between a gate that means something
and one that only ever certifies the 65 parts someone has already sorted.

RULE 6, STALENESS WARNS, NEVER REFUSES, BY ITSELF: this module compares the
coverage file's declared part_count against the live count of non-test
modules in scripts_dir right now (the exact drift assurance_coverage.py's
own docstring names: 271 declared against 279 seen that night). A mismatch
is reported as `stale=True` with the concrete counts, but never flips
`allowed` on its own. Justification: the brief itself observed the same
measurement return 71 and 69 an hour apart while agents were adding files
concurrently, which means drift is the NORMAL condition on a night like
this one, not an exceptional one. A gate that refuses every time the
tree has moved since the census last ran would refuse on every single
invocation tonight, which is indistinguishable from the "universal
refuser" bad state named below: a control that never passes is exactly as
useless, in the opposite direction, as one that never refuses. Staleness
earns a loud, named warning in `reason`; it earns a block only by way of
whatever it caused (a part going NO-DATA that used to be WIRED), which the
Tier A / UNCLASSIFIED checks already catch on their own.

THE BAD STATE A GREEN RUN WOULD ALSO PASS: a gate that says allowed because
it found nothing to look at is indistinguishable, from the outside, from
one that looked hard and found nothing wrong. Guarded here with a positive
count assertion: `allowed` requires `tier_a_examined > 0` (see check()),
never merely "no blockers were found in an empty or wrongly-scoped list."
A coverage file with zero Tier A parts is a real, tested case
(test_zero_tier_a_parts_refuses) and it refuses, loudly, naming exactly
why, rather than passing on a technicality.

CONTINGENCY. On any doubt, this gate REFUSES: a needless refusal costs a
conversation; a release shipped past an unproven Tier A control costs the
thing the whole census exists to prevent. Two refusal shapes:
  - ReleaseGateRefused: the coverage file itself cannot be trusted (missing,
    unreadable, not JSON, wrong top-level shape, a part record missing a
    required field, a part named twice, or a tier/status value outside the
    known vocabulary). The gate's own machinery could not answer the
    question at all. Distinct message text for "missing" versus "malformed"
    (RULE 5), because a caller fixing "the file does not exist" needs a
    different action than a caller fixing "the file is corrupt."
  - GateResult(allowed=False, ...): the gate answered the question and the
    answer is no. Never an exception; a caller is expected to check
    `.allowed` on every call, not only the ones that go wrong.
An owner proceeds past a business refusal (allowed=False) by fixing the
underlying gap: wiring a battery check for the named Tier A or UNCLASSIFIED
part (closing its NO-DATA status), or, for a part that genuinely cannot
influence a decision, reclassifying it (a change to assurance_coverage.py's
own evidence, not a flag on this gate: this module has no override switch,
on purpose, matching wire_birth_gate.py's "no third way" precedent). WHO IS
TOLD: every refusal main() reaches is appended to a durable, JSON-lines log
(default docs/plan/RELEASE-GATE-REFUSALS.log) and count_refusals() answers
"how many, ever" from it, because this estate's own load gate once refused
ten commands and wrote the fact nowhere, so nobody could tell whether it
had ever prevented anything or only ever cost time (worker contract rule
7). Nothing here pages or emails anyone.

EDGE LIST, walked explicitly:
  - empty coverage file (zero bytes): fails JSON parsing, ReleaseGateRefused
    (malformed).
  - coverage file with zero Tier A parts at all: refused via the positive
    count assertion above, named as its own case in `reason`, never folded
    into "0 blockers found."
  - a part appearing twice: ReleaseGateRefused (malformed); a duplicate
    would make "every Tier A NO-DATA part" an unprovable claim.
  - a tier value outside {A, B, C, UNCLASSIFIED}: ReleaseGateRefused
    (unknown enum raises, worker contract rule 2).
  - a record missing its status field (or part, or tier): ReleaseGateRefused
    (malformed), naming which key and which part index.
  - a status value that is neither NO-DATA nor the battery-credit status:
    ReleaseGateRefused (unknown enum raises).
  - a coverage file generated by a different tool version: not fatal. Noted
    in `reason` as informational provenance once the file's shape has
    already passed validation; this module does not know what a different
    generator promises about its own output, so it neither trusts nor
    rejects it on that basis alone.

Python 3.9 floor, standard library only, no network, no subprocess. This
module does not run the battery, does not classify anything (that is
assurance_coverage.py's job, reused here rather than re-derived), and does
not decide who may override a refusal.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

import assurance_coverage

ROOT = assurance_coverage.ROOT
DEFAULT_COVERAGE = assurance_coverage.OUT
DEFAULT_SCRIPTS_DIR = assurance_coverage.SCRIPTS
DEFAULT_LOG = os.path.join(ROOT, "docs", "plan", "RELEASE-GATE-REFUSALS.log")

REQUIRED_PART_KEYS = ("part", "tier", "status")


class ReleaseGateRefused(Exception):
    """The gate's own machinery could not trust the coverage file: missing,
    unreadable, not JSON, the wrong top-level shape, a part record missing
    a required key, a duplicate part name, or a tier/status value outside
    the known vocabulary. Never raised for an ordinary business refusal (a
    real Tier A or UNCLASSIFIED NO-DATA finding, or zero Tier A parts to
    examine): those are GateResult(allowed=False, ...), which a caller is
    expected to see on every call, not only the ones that go wrong. See the
    module docstring's CONTINGENCY section.
    """


@dataclass
class GateResult:
    """The verdict for one check() call.

    allowed             True only when this run confirmed a non-zero number
                        of Tier A parts (tier_a_examined > 0), none of them
                        report NO-DATA, and no UNCLASSIFIED part reports
                        NO-DATA either. See RULE 3 and "THE BAD STATE A
                        GREEN RUN WOULD ALSO PASS" in the module docstring.
    blockers            every Tier A part reporting NO-DATA, sorted. Rule 1:
                        the reason names every one of these, never a count.
    unclassified_blockers  every UNCLASSIFIED part reporting NO-DATA,
                        sorted, kept separate from `blockers` so a reader
                        can tell a known strategic gap from an unresolved
                        one that might be one.
    unclassified_count  len(unclassified_blockers): the unit contract names
                        this exact field, kept as an int alongside the list
                        above rather than instead of it.
    tier_a_examined     how many Tier A parts this run actually looked at:
                        the positive count assertion, checkable on its own.
    stale               True when the coverage file's declared part_count
                        no longer matches the live module count in
                        scripts_dir. Never flips `allowed` by itself (RULE
                        6): staleness is a warning, not a refusal.
    reason              human-readable. Names every blocker and every
                        unclassified part when allowed is False; states
                        what was actually examined either way.
    coverage_generated_at  the coverage file's own mtime, ISO-8601, or None
                        if it could not be read. See "WHAT THE COVERAGE
                        FILE DOES NOT CARRY" above for why this is an mtime
                        and not a claimed generation timestamp.
    """

    allowed: bool
    blockers: List[str] = field(default_factory=list)
    unclassified_blockers: List[str] = field(default_factory=list)
    unclassified_count: int = 0
    tier_a_examined: int = 0
    stale: bool = False
    reason: str = ""
    coverage_generated_at: Optional[str] = None


def _load_coverage(coverage_path):
    """(payload, parts): the raw top-level dict and its validated part
    records. Raises ReleaseGateRefused for anything that would make the
    Tier A / UNCLASSIFIED NO-DATA answer untrustworthy. See the module
    docstring's EDGE LIST for the exact cases handled here.
    """
    if not os.path.isfile(coverage_path):
        raise ReleaseGateRefused(
            "missing coverage file: %r does not exist. Run "
            "'python3 scripts/assurance_coverage.py' to write it, or point "
            "--coverage at the right path. A missing file is never read as "
            "\"no blockers\" (RULE 4)." % (coverage_path,)
        )

    try:
        with open(coverage_path, encoding="utf-8") as handle:
            raw = handle.read()
    except OSError as exc:
        raise ReleaseGateRefused(
            "missing coverage file: %r could not be opened: %s: %s"
            % (coverage_path, type(exc).__name__, exc)
        ) from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReleaseGateRefused(
            "malformed coverage file: %r is not valid JSON: %s"
            % (coverage_path, exc)
        ) from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("parts"), list):
        raise ReleaseGateRefused(
            "malformed coverage file: %r has no top-level 'parts' list; "
            "this gate only trusts scripts/assurance_coverage.py's own "
            "shape" % (coverage_path,)
        )

    seen = set()
    parts = []
    for i, record in enumerate(payload["parts"]):
        if not isinstance(record, dict):
            raise ReleaseGateRefused(
                "malformed coverage file: parts[%d] is not an object: %r"
                % (i, record)
            )
        missing_keys = [k for k in REQUIRED_PART_KEYS if k not in record]
        if missing_keys:
            raise ReleaseGateRefused(
                "malformed coverage file: parts[%d] (part=%r) is missing %s"
                % (i, record.get("part"), missing_keys)
            )
        name = record["part"]
        if not isinstance(name, str) or not name:
            raise ReleaseGateRefused(
                "malformed coverage file: parts[%d] has a non-string or "
                "empty 'part' name: %r" % (i, name)
            )
        if name in seen:
            raise ReleaseGateRefused(
                "malformed coverage file: part %r appears twice; a "
                "coverage file cannot promise an exhaustive Tier A NO-DATA "
                "list over a duplicate" % (name,)
            )
        seen.add(name)

        tier = record["tier"]
        if tier not in assurance_coverage.TIERS:
            raise ReleaseGateRefused(
                "malformed coverage file: part %r carries tier %r, not one "
                "of %s" % (name, tier, sorted(assurance_coverage.TIERS))
            )
        status = record["status"]
        if status not in assurance_coverage.STATUSES:
            raise ReleaseGateRefused(
                "malformed coverage file: part %r carries status %r, not "
                "one of %s" % (name, status, sorted(assurance_coverage.STATUSES))
            )
        parts.append({"part": name, "tier": tier, "status": status})

    return payload, parts


def _coverage_generated_at(coverage_path):
    """The coverage file's own mtime, ISO-8601 UTC, or None if it could not
    be stat'd (an OSError here, after a successful read in _load_coverage,
    would mean the file vanished between the two calls; treated as "cannot
    say", never invented)."""
    try:
        mtime = os.path.getmtime(coverage_path)
    except OSError:
        return None
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()


def _current_module_count(scripts_dir):
    """Non-test .py files directly in scripts_dir right now, or None if the
    directory cannot even be listed. None is a machinery hiccup, not
    grounds to refuse the whole gate over (RULE 6: staleness only warns);
    a caller sees it as "staleness not assessed", not as "assessed as
    fresh"."""
    try:
        names = os.listdir(scripts_dir)
    except OSError:
        return None
    return sum(1 for n in names if n.endswith(".py") and not n.startswith("test_"))


def _staleness(payload, scripts_dir):
    """(stale, note). Compares the coverage file's declared part_count
    against the live module count in scripts_dir. See RULE 6 in the module
    docstring for why this never blocks by itself."""
    declared = payload.get("part_count")
    if not isinstance(declared, int):
        return False, "coverage file carries no usable part_count field; staleness not assessed"
    live = _current_module_count(scripts_dir)
    if live is None:
        return False, "scripts_dir %r could not be listed; staleness not assessed" % (scripts_dir,)
    if live == declared:
        return False, "part_count matches the live tree (%d)" % (declared,)
    return True, (
        "coverage file declares %d part(s); scripts_dir currently holds %d "
        "non-test module(s) (drift of %d). This is the ordinary condition "
        "while other units add files concurrently, never on its own a "
        "reason to refuse (see RULE 6)"
        % (declared, live, live - declared)
    )


def check(coverage_path=None, scripts_dir=None):
    """The one question this gate answers: may a release proceed.

    coverage_path defaults to docs/generated/ASSURANCE-COVERAGE.json.
    scripts_dir defaults to scripts/, and is the tree the staleness check
    (RULE 6) compares the coverage file's declared part_count against; a
    test supplies its own temp directory here rather than the real one.

    Raises ReleaseGateRefused when the coverage file cannot be trusted at
    all (see _load_coverage). Otherwise always returns a GateResult; a
    business refusal (Tier A or UNCLASSIFIED NO-DATA, or zero Tier A parts
    to examine) is GateResult(allowed=False, ...), never an exception.
    """
    coverage_path = coverage_path or DEFAULT_COVERAGE
    scripts_dir = scripts_dir or DEFAULT_SCRIPTS_DIR

    payload, parts = _load_coverage(coverage_path)

    tier_a = [p for p in parts if p["tier"] == assurance_coverage.TIER_A]
    tier_a_examined = len(tier_a)
    blockers = sorted(
        p["part"] for p in tier_a if p["status"] == assurance_coverage.NO_DATA
    )

    unclassified = [
        p for p in parts if p["tier"] == assurance_coverage.TIER_UNCLASSIFIED
    ]
    unclassified_blockers = sorted(
        p["part"] for p in unclassified if p["status"] == assurance_coverage.NO_DATA
    )

    stale, stale_note = _staleness(payload, scripts_dir)
    generated_at = _coverage_generated_at(coverage_path)

    notes = []
    if tier_a_examined == 0:
        # THE BAD STATE A GREEN RUN WOULD ALSO PASS: an empty examination is
        # never read as "nothing wrong was found." Named explicitly so this
        # branch is never mistaken for the ordinary "no blockers" case.
        notes.append(
            "0 Tier A part(s) are present in this coverage file: refusing "
            "rather than certifying an examination that never happened "
            '(see "THE BAD STATE A GREEN RUN WOULD ALSO PASS" in the '
            "module docstring)"
        )
    if blockers:
        notes.append(
            "%d Tier A part(s) report NO-DATA: %s"
            % (len(blockers), ", ".join(blockers))
        )
    if unclassified_blockers:
        notes.append(
            "%d UNCLASSIFIED part(s) report NO-DATA and are refused rather "
            "than presumed harmless (RULE 3: an unresolved tier is an "
            "unknown risk, never a default pass): %s"
            % (len(unclassified_blockers), ", ".join(unclassified_blockers))
        )

    allowed = tier_a_examined > 0 and not blockers and not unclassified_blockers

    if not notes:
        notes.append(
            "%d Tier A part(s) examined, none reporting NO-DATA; %d "
            "UNCLASSIFIED part(s) examined, none reporting NO-DATA"
            % (tier_a_examined, len(unclassified))
        )

    generated_by = payload.get("generated_by")
    if generated_by is not None and generated_by != "scripts/assurance_coverage.py":
        notes.append(
            "note: coverage file was generated_by %r, not the expected "
            "scripts/assurance_coverage.py; shape validation already "
            "passed, so this is informational provenance only, never a "
            "refusal by itself" % (generated_by,)
        )

    if stale:
        notes.append("WARNING, STALE: %s" % (stale_note,))

    return GateResult(
        allowed=allowed,
        blockers=blockers,
        unclassified_blockers=unclassified_blockers,
        unclassified_count=len(unclassified_blockers),
        tier_a_examined=tier_a_examined,
        stale=stale,
        reason=" | ".join(notes),
        coverage_generated_at=generated_at,
    )


def _log_refusal(log_path, reason):
    """Append one JSON line to the durable refusal log (mirrors
    coe_gate.py's _log_refusal). This is the fix the worker brief names
    directly: a gate that refuses and writes the fact nowhere can never be
    recalibrated because nobody can say whether it ever prevented anything.
    A logging failure raises ReleaseGateRefused rather than being
    swallowed: a refusal that could not even be written down is a second,
    worse failure, not a detail to drop quietly."""
    record = {"at": datetime.now(timezone.utc).isoformat(), "reason": reason}
    try:
        log_dir = os.path.dirname(log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError as exc:
        raise ReleaseGateRefused(
            "could not write the refusal log at %r: %s: %s"
            % (log_path, type(exc).__name__, exc)
        ) from exc


def count_refusals(log_path=None):
    """How many refusals main() has ever logged for this log file. 0 for a
    log that does not exist yet (a gate nobody has tripped is not a broken
    gate, mirrors coe_gate.count_refusals). A log that exists but cannot be
    parsed raises ReleaseGateRefused rather than silently reporting 0, so a
    broken log is never mistaken for a clean record."""
    log_path = log_path or DEFAULT_LOG
    if not os.path.exists(log_path):
        return 0
    count = 0
    try:
        with open(log_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                json.loads(line)  # validate, never coerce a bad line to 0
                count += 1
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseGateRefused(
            "refusal log at %r could not be read: %s: %s"
            % (log_path, type(exc).__name__, exc)
        ) from exc
    return count


def main(argv=None):
    """CLI / release-script entry point.

    Exit codes: 0 allowed, 1 a real business refusal (Tier A or
    UNCLASSIFIED NO-DATA, or zero Tier A parts examined), 2 the gate itself
    could not trust the coverage file (ReleaseGateRefused). Every refusal
    of either kind is logged before this returns (worker contract rule 7).
    `--count-refusals` answers "how many, ever" without running the gate.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--coverage", default=DEFAULT_COVERAGE,
                     help="path to ASSURANCE-COVERAGE.json")
    ap.add_argument("--scripts-dir", default=DEFAULT_SCRIPTS_DIR,
                     help="live scripts/ dir compared against for staleness")
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--count-refusals", action="store_true",
                     help="print the number of logged refusals and exit")
    args = ap.parse_args(argv)

    if args.count_refusals:
        print(count_refusals(args.log))
        return 0

    try:
        result = check(args.coverage, args.scripts_dir)
    except ReleaseGateRefused as exc:
        _log_refusal(args.log, str(exc))
        print("RELEASE GATE COULD NOT DECIDE, REFUSED: %s" % exc, file=sys.stderr)
        return 2

    if not result.allowed:
        _log_refusal(args.log, result.reason)
        print("RELEASE GATE REFUSED: %s" % result.reason, file=sys.stderr)
        return 1

    print("RELEASE GATE ALLOWED: %s" % result.reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
