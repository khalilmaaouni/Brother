#!/usr/bin/env python3
"""EPIC M1.05 Mobile Ownership Resolver (per docs/plan/MOBILE-EPIC-M1-UNITS-
CANONICAL.md -- read that file before touching this unit's numbering).

Takes a semantic plan (scripts/mobile_plan_compiler.py's
compile_semantic_plan() output, EPIC M1.04) and a REAL target project
directory on disk -- some other repository entirely, e.g. an iOS app repo,
never Brother's own repo -- and resolves each semantic unit to a real file
that unit should own, or says honestly why it cannot:

  resolved         exactly one real, non-generated file matches the unit's
                    artifact_kind under the project's own source roots, at
                    the strongest tier that yields a decisive answer (an
                    exact stem match beats a looser substring match).
  ambiguous         more than one real file plausibly matches -- the full
                    candidate list comes back, no path is chosen. Per the
                    M1.05 brief: "no guessed path when genuine ambiguity
                    exists ... surface it as a decision point."
  generated_only    every real file that matches is marked generated (a
                    "@generated"/"DO NOT EDIT" header, or a path under a
                    known generated directory) -- this resolver never
                    proposes silently editing a marked-generated file, so
                    it refuses to pick one even when there is only one.
  new_file          no real file matches at all -- a repository-relative
                    path is synthesized under the project's own observed
                    source_roots, using the same stem/ext filename
                    convention scripts/mobile_plan_compiler.py's adapters
                    already use, and marked exists=False so a caller can
                    never mistake it for a real file on disk. If that exact
                    synthesized path already exists on disk (a case-
                    insensitive filesystem folding a differently-cased real
                    file onto it, e.g. "view.swift" existing when "View.
                    swift" is proposed), the result comes back "ambiguous"
                    instead -- never a false exists=False that would let a
                    caller overwrite a real, human-owned file.

Every result also carries an "ownership" block naming whether the artifact
is shared across platforms or platform-specific, from two independent,
never-fabricated signals: (1) comparing the (stem, ext) this artifact_kind
resolves to across every adapter actually registered in
mobile_plan_compiler.ADAPTERS, with the actual per-adapter data attached as
evidence -- today there is exactly one adapter (ios-swiftui, EPIC M1.04), so
real cross-platform divergence is NOT YET PROVABLE and this resolver says so
rather than guessing; (2) a real directory segment in ANY resolved/
candidate/proposed path named "shared", "common" or "cross-platform"
(case-insensitive), which is direct, observed evidence independent of the
adapter registry's current size.

Mirrors this codebase's own conventions (scripts/mobile_plan_compiler.py):
a typed exception (OwnershipResolverError) raised only when the WHOLE call
cannot proceed at all (a malformed semantic_plan, not a dict, no "units"
list, or project_dir not a real directory) -- never for a per-unit
resolution gap, since ambiguous/new_file/generated_only are normal results,
not errors, the same "never refuse over a detection gap" contract
mobile_plan_compiler.resolve_adapter() already holds. main() follows the
same NO-DATA(2)/FAIL(1)/PASS(0) exit contract as mobile_plan_compiler.py's
own main().

Built via the founder-directed OpenRouter lane (docs/decisions/
overnight-mobile-swarm-scope-2026-09-15.json, flipped OpenRouter axis):
DeepSeek (deepseek/deepseek-v4.1-flash, effort xhigh) drafted a candidate
first pass; its "raise OwnershipResolverError for a per-unit missing
artifact_kind" behavior was evaluated and rejected (it would abort every
sibling unit's resolution over one bad unit, contradicting this module's
own per-unit-gap contract), but its project_dir precondition check and
structured per-adapter evidence were adopted. Muse (effort high) then ran
an adversarial review of the resulting implementation and found eight real
bugs, all fixed here: a case-insensitive substring tier matching "View"
inside "Review"; an exact-tier-all-generated result hiding a real usable
substring match; the shared-by-path signal reading only candidates[0]
instead of every candidate; new_file synthesizing a path under a listed
but non-existent source root when a later one exists; an absolute or
".."-laden source_root escaping project_dir entirely; a "Pods" (capital P,
the real CocoaPods convention) directory silently pruned from the walk
while lowercase "pods" was correctly flagged generated, so the same vendor
file's status depended on casing; an uncaught OSError on an unwritable
--out path breaking main()'s NO-DATA/FAIL/PASS contract; and a
case-sensitive generated-marker header check missing a lowercase
"do not edit" comment.

Python 3.9, standard library only. No network. Never writes to project_dir.
"""
import argparse
import json
import os
import sys

import contract_check as CC
import mobile_plan_compiler as MPC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Pruned entirely from the walk: performance only, for directories that are
# both enormous on a real repo and never plausibly named by an adapter's
# own stem/ext convention (no current or planned adapter emits a
# ".git"/"node_modules" -shaped filename). Deliberately NOT the vendor/
# build directories below (Pods, DerivedData, build, .build, Carthage,
# .gradle, generated) -- those stay walkable so a file living inside one is
# still found and reported as "generated_only" (a real, informative
# result) instead of silently vanishing into a false "new_file" claim
# (Muse adversarial review, EPIC M1.05, finding 6: a real "Pods/" directory
# pruned here while a hypothetical lowercase "pods/" was not produced two
# different, wrong answers for the same kind of vendor file).
# ponytail: node_modules can hold tens of thousands of files on a real JS
# project; pruning it is a deliberate walk-performance cut with a known
# ceiling (a genuinely JS/RN-adapter-relevant file inside it would be
# missed) -- upgrade path if that ever matters: an adapter-aware allowlist
# instead of a blanket prune.
_SKIP_DIRS = {".git", "node_modules"}

# Marker text near the top of a file that means "do not treat this as a
# human-owned source file" -- real conventions used by codegen tools
# (protoc, sourcery, SwiftGen, R.java, etc.), not invented here. Compared
# case-insensitively against the file's head (Muse adversarial review,
# EPIC M1.05, finding 8: a lowercase "do not edit" comment was missed
# because only the uppercase marker constant was checked).
_GENERATED_MARKERS = (
    "@generated", "do not edit", "this file was automatically generated",
    "code generated", "auto-generated file", "<auto-generated>",
)
# A path segment that, on its own, marks a file as generated even with no
# header -- includes the vendor/build directories _SKIP_DIRS above
# deliberately does NOT prune, so a file inside one is flagged rather than
# invisibly skipped or (worse) silently proposed as a real owner.
_GENERATED_PATH_SEGMENTS = {"build", "derived_data", "derivedata", "pods",
                            ".build", "generated", "carthage", ".gradle"}

_SHARED_PATH_SEGMENTS = {"shared", "common", "cross-platform", "crossplatform"}


class OwnershipResolverError(Exception):
    """Raised only when the WHOLE call cannot proceed: semantic_plan is not
    usable (not a dict, no "units" list), or project_dir is not a real
    directory. Never raised for one unit's own resolution gap; those come
    back as an "ambiguous", "new_file", "generated_only" or "invalid_unit"
    result for that unit, same as mobile_plan_compiler.resolve_adapter()
    never refuses over a detection gap."""


def _basename_no_ext(path):
    base = os.path.basename(path)
    stem, _, _ext = base.rpartition(".")
    return stem if stem else base


def _is_generated(rel_path, project_dir):
    """(generated: bool, reason: str_or_None). Checked by path first (cheap,
    no file read needed), then by a header marker in the file's first 2KB,
    both case-insensitive. Never raises: an unreadable file is simply
    not-generated-by-header."""
    normalized = rel_path.replace(os.sep, "/").lower()
    segments = set(normalized.split("/"))
    hit = segments & _GENERATED_PATH_SEGMENTS
    if hit:
        return True, "path segment %r" % sorted(hit)[0]
    full = os.path.join(project_dir, rel_path)
    try:
        with open(full, "r", encoding="utf-8", errors="ignore") as fh:
            head = fh.read(2000)
    except OSError:
        return False, None
    head_lower = head.lower()
    for marker in _GENERATED_MARKERS:
        if marker in head_lower:
            return True, "header marker %r" % marker
    return False, None


def _shared_path_segment(rel_path):
    for seg in rel_path.replace(os.sep, "/").split("/"):
        if seg.lower() in _SHARED_PATH_SEGMENTS:
            return seg
    return None


def _ownership(artifact_kind, paths, custom_adapter=None):
    """The ownership block: two independent, never-fabricated signals, see
    the module docstring. `paths` is every real/proposed path worth
    checking for the shared-by-path signal (every candidate for an
    ambiguous/generated_only result, or the single resolved/proposed path)
    -- checking only one candidate missed real evidence sitting on another
    one, and the signal must not depend on sort order (Muse adversarial
    review, EPIC M1.05, finding 3). `custom_adapter` is the raw `adapter=`
    argument the caller passed to resolve_unit() when it was a dict:
    mobile_plan_compiler.resolve_adapter() returns such a dict verbatim,
    bypassing MPC.ADAPTERS entirely, so comparing against the registry in
    that case would describe adapters this resolution never used instead
    of the one that actually did (adversarial hardening review, EPIC
    M1.05, finding m2)."""
    seg = None
    for p in paths:
        if not p:
            continue
        hit = _shared_path_segment(p)
        if hit:
            seg = hit
            break
    if isinstance(custom_adapter, dict):
        per_adapter = {}
        pair = custom_adapter.get(artifact_kind)
        if isinstance(pair, (tuple, list)) and len(pair) == 2:
            per_adapter["caller_supplied"] = {"stem": pair[0], "ext": pair[1]}
        registry_shared = None
        registry_basis = ("a caller-supplied adapter dict was used for "
                          "this resolution, not a registered one from "
                          "MPC.ADAPTERS -- cross-platform divergence "
                          "against the registry is not meaningful here")
    else:
        per_adapter = {}
        for adapter_id, mapping in MPC.ADAPTERS.items():
            if isinstance(mapping, dict) and artifact_kind in mapping:
                per_adapter[adapter_id] = {"stem": mapping[artifact_kind][0],
                                           "ext": mapping[artifact_kind][1]}
        if len(per_adapter) < 2:
            registry_shared = None
            registry_basis = ("only %d adapter(s) registered (%s) -- real "
                              "cross-platform divergence is not provable "
                              "until EPIC M1.06 adds more"
                              % (len(MPC.ADAPTERS), ", ".join(sorted(MPC.ADAPTERS))))
        else:
            pairs = {(v["stem"], v["ext"]) for v in per_adapter.values()}
            registry_shared = len(pairs) <= 1
            registry_basis = ("(stem, ext) for %r is %s across the %d "
                              "registered adapters" % (
                                  artifact_kind,
                                  "identical" if registry_shared else "different",
                                  len(per_adapter)))
    return {
        "shared_across_platforms": registry_shared,
        "shared_across_platforms_basis": registry_basis,
        "shared_across_platforms_evidence": per_adapter,
        "shared_by_path": seg is not None,
        "shared_by_path_basis": (
            "path segment %r observed" % seg if seg
            else "no shared/common/cross-platform path segment observed"),
    }


def _is_within_project(project_dir, abs_path):
    """True if abs_path's realpath is project_dir itself or somewhere
    beneath it. The one containment predicate every path leaving this
    module is checked against, so a symlink escape is refused the same way
    whether it appears as a source root (_safe_join) or as a plain file
    entry discovered mid-walk (_candidate_paths) -- a symlinked FILE inside
    `filenames` previously had zero containment check even though a
    symlinked directory used as a source root, or descended into, was
    already refused (adversarial hardening review, EPIC M1.05, finding
    M1)."""
    project_real = os.path.realpath(project_dir)
    candidate_real = os.path.realpath(abs_path)
    return candidate_real == project_real or candidate_real.startswith(
        project_real + os.sep)


def _safe_join(project_dir, root):
    """project_dir joined with a profile-supplied root, refusing to let the
    result escape project_dir. An absolute root silently discards
    project_dir via os.path.join's own semantics, and a ".."-laden root can
    walk out via traversal; either would break "paths are repository-
    relative to the TARGET project" (Muse adversarial review, EPIC M1.05,
    finding 5). Returns None for an out-of-bounds root instead of ever
    walking, or proposing a path, outside project_dir."""
    if _is_within_project(project_dir, os.path.join(project_dir, root)):
        return os.path.normpath(os.path.join(project_dir, root))
    return None


def _first_usable_root(project_dir, source_roots):
    """The first safe source root (see _safe_join) that already exists on
    disk, for synthesizing a new_file path under a real directory rather
    than one only listed but absent (Muse adversarial review, EPIC M1.05,
    finding 4). Falls back to the first safe root even if it does not
    exist yet, then to "." (always safe once project_dir itself is real)."""
    roots = source_roots if source_roots else ["."]
    safe = [(r, _safe_join(project_dir, r)) for r in roots]
    safe = [(r, j) for r, j in safe if j is not None]
    for root, joined in safe:
        if os.path.isdir(joined):
            return root
    return safe[0][0] if safe else "."


def _candidate_paths(project_dir, source_roots, ext):
    """Every real repository-relative file path under project_dir, scoped to
    source_roots when given (mobile-project-profile-v1's own field), with
    the matching extension, skipping _SKIP_DIRS and any root that would
    escape project_dir (_safe_join). Never raises: a non-existent,
    unreadable or out-of-bounds root is simply empty."""
    roots = source_roots if source_roots else ["."]
    seen = set()
    out = []
    for root in roots:
        root_abs = _safe_join(project_dir, root)
        if root_abs is None or not os.path.isdir(root_abs):
            continue
        for dirpath, dirnames, filenames in os.walk(root_abs):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for name in filenames:
                if not name.lower().endswith("." + ext.lower()):
                    continue
                full = os.path.join(dirpath, name)
                if not _is_within_project(project_dir, full):
                    # A symlinked FILE entry in `filenames` (os.walk does
                    # not resolve these) can point anywhere on disk;
                    # os.walk's own followlinks=False only refuses
                    # descending into a symlinked DIRECTORY, so a file
                    # symlink needs its own containment check here (Muse
                    # adversarial hardening review, EPIC M1.05, finding M1).
                    continue
                rel = os.path.relpath(full, project_dir)
                if rel not in seen:
                    seen.add(rel)
                    out.append(rel)
    return sorted(out)


def _classify(matches, project_dir):
    """matches -> (status, path_or_None, candidate_infos). status is
    "resolved" (exactly one non-generated match), "ambiguous" (more than
    one), "generated_only" (at least one match, all generated), or "none"
    (no matches at all)."""
    infos = []
    for rel in matches:
        generated, reason = _is_generated(rel, project_dir)
        infos.append({"path": rel, "generated": generated,
                     "generated_reason": reason})
    if not infos:
        return "none", None, []
    non_generated = [i for i in infos if not i["generated"]]
    if not non_generated:
        return "generated_only", None, infos
    if len(non_generated) == 1:
        return "resolved", non_generated[0]["path"], infos
    return "ambiguous", None, infos


def _resolve_tiers(all_matches, project_dir, stem):
    """Two-tier candidate resolution over filenames that already agree on
    extension: an exact-stem match (case-sensitive: adapter stems are
    fixed PascalCase strings, and a differently-cased file is a weaker
    signal, not the same claim) is authoritative ONLY when it actually
    yields a decisive answer (resolved or ambiguous). If the exact tier's
    only matches are all generated, or there are none, the looser
    substring tier is still consulted -- an all-generated exact match must
    never hide a real, usable substring match (Muse adversarial review,
    EPIC M1.05, finding 2). The substring tier is also case-sensitive
    (Muse finding 1: a case-insensitive "view" in "review" would falsely
    resolve a unit named "view" to an unrelated "Review.swift").

    Returns (status, path_or_None, candidates, match_basis); status "none"
    means the caller should fall through to new_file."""
    exact_matches = [m for m in all_matches if _basename_no_ext(m) == stem]
    exact_status, exact_path, exact_candidates = (
        _classify(exact_matches, project_dir) if exact_matches
        else ("none", None, []))
    if exact_status in ("resolved", "ambiguous"):
        return exact_status, exact_path, exact_candidates, "exact_stem"

    loose_matches = [m for m in all_matches
                     if m not in exact_matches and stem in _basename_no_ext(m)]
    loose_status, loose_path, loose_candidates = (
        _classify(loose_matches, project_dir) if loose_matches
        else ("none", None, []))
    if loose_status in ("resolved", "ambiguous"):
        return loose_status, loose_path, loose_candidates, "substring"

    if exact_status == "generated_only" or loose_status == "generated_only":
        basis = "exact_stem" if exact_status == "generated_only" else "substring"
        return "generated_only", None, exact_candidates + loose_candidates, basis
    return "none", None, [], "none"


def _is_usable_component_pair(stem, ext):
    """True when this adapter stem/ext pair is a usable non-empty string
    pair, i.e. one _candidate_paths() can join without raising an uncaught
    AttributeError at ext.lower() (EPIC M1.05 finding m1). USABILITY ONLY:
    a "../../../../etc/evil" stem is perfectly usable by this predicate and
    is refused elsewhere, by mobile_plan_compiler's own safety validator.
    The two are kept apart on purpose -- resolve_unit() reports an unusable
    pair as a per-unit invalid_unit status but lets an UNSAFE one refuse
    the call outright."""
    return isinstance(stem, str) and bool(stem) and isinstance(ext, str) and bool(ext)


def resolve_unit(unit, project_dir, project_profile=None, adapter=None):
    """Resolve ONE semantic unit dict (compile_semantic_plan()'s shape) to a
    real file, an ambiguous candidate set, a new-file candidate, or
    generated_only. Never raises for a per-unit gap; a malformed unit
    (missing "artifact_kind"/"id") comes back as its own "invalid_unit"
    status instead, so one bad unit in a plan never aborts resolve_plan()
    for every sibling unit. Raises OwnershipResolverError only for a bad
    project_dir -- a whole-call precondition, never a per-unit gap."""
    if not os.path.isdir(project_dir):
        # Silently returning "new_file" for every unit would mask a
        # caller's typo as "nothing exists here yet" instead of surfacing
        # the real mistake (DeepSeek draft review, EPIC M1.05). main()
        # already checks this before ever calling resolve_plan()/
        # resolve_unit(); this guard covers a direct library caller.
        raise OwnershipResolverError(
            "project_dir is not a real directory: %s" % project_dir)
    unit_id = unit.get("id") if isinstance(unit, dict) else None
    artifact_kind = unit.get("artifact_kind") if isinstance(unit, dict) else None
    if not isinstance(unit, dict) or not unit_id or not artifact_kind:
        return {
            "unit_id": unit_id, "artifact_kind": artifact_kind,
            "status": "invalid_unit", "path": None, "exists": None,
            "match_basis": None, "candidates": [], "ownership": None,
            "reason": "unit is missing a usable 'id' or 'artifact_kind' -- "
                     "not a compile_semantic_plan() unit",
        }

    # resolve_adapter() returns (adapter_dict, matched) since PR #705's
    # Major-1 fix. This call site reads the dict only: an unmatched
    # platform profile is already surfaced to the operator by
    # mobile_plan_compiler.main()'s own NO-DATA line, and resolve_unit()
    # answers a per-unit ownership question, not a detection-gap one.
    #
    # It also now RAISES PlanCompilerError on an unsafe OR merely unusable
    # stem/ext, because the m1-04 entry validator the m1 guard below was
    # written against ("not yet an ancestor of this branch") became one
    # when these branches were brought together. Those two cases keep
    # different shapes here, deliberately:
    #   unsafe  (a traversal stem, a shell metacharacter): the refusal
    #           propagates, loud, exactly as the PR #717 follow-up test
    #           test_malicious_adapter_stem_cannot_escape_project_dir_via_
    #           new_file requires. A caller must never see a security
    #           refusal downgraded to a per-unit status it can skip past.
    #   unusable (a non-string or empty stem/ext, e.g. ext=None): a
    #           per-unit gap like every other one in this module, so it
    #           comes back as invalid_unit (EPIC M1.05 finding m1).
    # Which one it was is decided by re-applying the m1 guard's own
    # predicate below, never by matching on the exception's message.
    try:
        resolved_adapter, _matched = MPC.resolve_adapter(
            project_profile=project_profile, adapter=adapter)
    except MPC.PlanCompilerError as exc:
        pair = adapter.get(artifact_kind) if isinstance(adapter, dict) else None
        unusable = (not isinstance(pair, (tuple, list)) or len(pair) != 2
                    or not _is_usable_component_pair(pair[0], pair[1]))
        if not unusable:
            raise
        return {
            "unit_id": unit_id, "artifact_kind": artifact_kind,
            "status": "invalid_unit", "path": None, "exists": None,
            "match_basis": None, "candidates": [], "ownership": None,
            "reason": "adapter stem/ext for artifact_kind %r was refused "
                     "as unusable by mobile_plan_compiler.resolve_adapter"
                     "(): %s" % (artifact_kind, exc),
        }
    # mobile_plan_compiler.resolve_adapter() returns a caller-supplied
    # dict verbatim, bypassing MPC.ADAPTERS -- kept so _ownership() can
    # describe the adapter this resolution actually used instead of the
    # unrelated registry (finding m2).
    custom_adapter = adapter if isinstance(adapter, dict) else None
    default_adapter = MPC.ADAPTERS[MPC.DEFAULT_ADAPTER_ID]
    if artifact_kind in resolved_adapter:
        stem, ext = resolved_adapter[artifact_kind]
    elif artifact_kind in default_adapter:
        stem, ext = default_adapter[artifact_kind]
    else:
        return {
            "unit_id": unit_id, "artifact_kind": artifact_kind,
            "status": "invalid_unit", "path": None, "exists": None,
            "match_basis": None, "candidates": [], "ownership": None,
            "reason": "artifact_kind %r is not known to any registered "
                     "adapter" % artifact_kind,
        }
    # A caller-supplied adapter dict (the `adapter=` argument, or a
    # project_profile naming one) is untrusted: a non-string or empty
    # stem/ext (e.g. ext=None) would otherwise reach ext.lower() inside
    # _candidate_paths() and raise an uncaught AttributeError, breaking
    # this module's own "never raises for a per-unit gap" contract. Same
    # per-unit-gap convention as the artifact_kind check just above, not a
    # whole-call OwnershipResolverError (hardening review, EPIC M1.05,
    # finding m1).
    if not _is_usable_component_pair(stem, ext):
        return {
            "unit_id": unit_id, "artifact_kind": artifact_kind,
            "status": "invalid_unit", "path": None, "exists": None,
            "match_basis": None, "candidates": [], "ownership": None,
            "reason": "adapter stem/ext for artifact_kind %r is not a "
                     "usable non-empty string pair: stem=%r ext=%r"
                     % (artifact_kind, stem, ext),
        }

    raw_source_roots = (project_profile.get("source_roots")
                        if isinstance(project_profile, dict) else None)
    # A malformed profile ("source_roots" not a list, or an entry that is
    # not a plain path string) is the same kind of detection gap
    # mobile_plan_compiler.resolve_adapter() already tolerates for
    # "platforms" (Muse adversarial review, EPIC M1.04) -- never a reason
    # to crash here either; unusable entries are dropped, not raised on.
    if isinstance(raw_source_roots, list):
        source_roots = [r for r in raw_source_roots if isinstance(r, str)] or None
    else:
        source_roots = None

    all_matches = _candidate_paths(project_dir, source_roots, ext)
    status, path, candidates, match_basis = _resolve_tiers(
        all_matches, project_dir, stem)

    if status == "none":
        new_root = _first_usable_root(project_dir, source_roots or ["."])
        # stem/ext are already refused if unsafe (a traversal segment or a
        # shell metacharacter): both come from resolved_adapter above,
        # which is either MPC.ADAPTERS' own trusted static data or a
        # caller-supplied dict MPC.resolve_adapter() already validated
        # before returning it (mobile_plan_compiler.py's
        # _validate_safe_component(), PR #717 follow-up, Muse adversarial
        # review 2026-09-15). new_root is separately contained under
        # project_dir by _safe_join()/_first_usable_root() above, so this
        # join cannot escape either input.
        new_path = os.path.normpath(os.path.join(new_root, "%s.%s" % (stem, ext)))
        full = os.path.join(project_dir, new_path)
        # os.path.exists() alone is not the whole check: a symlink could
        # squat at exactly this synthesized path and point outside
        # project_dir, the same containment risk _is_within_project already
        # refuses for a discovered file (finding M1) -- only a hit that is
        # actually inside project_dir counts as a real collision; anything
        # escaping containment is invisible to this module, same as any
        # other out-of-bounds entry, and falls through to an ordinary
        # new_file result below.
        # ponytail: a symlink planted at exactly this stem+ext path is not
        # specially flagged beyond falling through to new_file; upgrade
        # path if that narrow case ever matters: an explicit
        # os.path.islink(full) check reported in the new_file result too.
        if os.path.exists(full) and _is_within_project(project_dir, full):
            # No case-sensitive tier above matched, yet the proposed path
            # already exists on disk -- the normal way this happens is a
            # case-insensitive filesystem (macOS default) folding a
            # differently-cased real file (e.g. "view.swift") onto this
            # proposed "View.swift". open(full, "w") would silently
            # overwrite that real, human-owned file; asserting exists=False
            # here would be a lie. This is a decision point, not a proposal
            # -- surfaced the same "never guess" way `ambiguous` already
            # surfaces multiple real candidates, never a new status shape
            # (adversarial hardening review, EPIC M1.05, finding C1).
            generated, reason = _is_generated(new_path, project_dir)
            candidates = [{"path": new_path, "generated": generated,
                          "generated_reason": reason}]
            return {
                "unit_id": unit_id, "artifact_kind": artifact_kind,
                "status": "ambiguous", "path": None, "exists": None,
                "match_basis": "case_insensitive_collision",
                "candidates": candidates,
                "ownership": _ownership(artifact_kind, [new_path], custom_adapter=custom_adapter),
                "reason": "no case-sensitive match for stem %r ext %r, but "
                         "%r already exists on disk (likely a case-"
                         "insensitive filesystem collision) -- refusing to "
                         "propose it as a new file" % (stem, ext, new_path),
            }
        return {
            "unit_id": unit_id, "artifact_kind": artifact_kind,
            "status": "new_file", "path": new_path, "exists": False,
            "match_basis": "none", "candidates": [],
            "ownership": _ownership(artifact_kind, [new_path], custom_adapter=custom_adapter),
            "reason": "no real file under the project's source_roots "
                     "matches stem %r ext %r" % (stem, ext),
        }

    reasons = {
        "resolved": "exactly one real, non-generated match",
        "ambiguous": "%d real files plausibly match -- none chosen"
                    % len([c for c in candidates if not c["generated"]] or candidates),
        "generated_only": "every real match is generated -- refusing to "
                          "propose editing it",
    }
    ownership_paths = [path] if path else [c["path"] for c in candidates]
    # candidates is the structured "ambiguous, candidates: [...]" decision
    # point the brief asks for -- kept populated for generated_only too
    # (visibility into which real, unusable files were found), empty for a
    # clean "resolved" result since path already names the one answer.
    return {
        "unit_id": unit_id, "artifact_kind": artifact_kind,
        "status": status, "path": path,
        "exists": True if status == "resolved" else None,
        "match_basis": match_basis,
        "candidates": [] if status == "resolved" else candidates,
        "ownership": _ownership(artifact_kind, ownership_paths, custom_adapter=custom_adapter),
        "reason": reasons[status],
    }


def resolve_plan(semantic_plan, project_dir, project_profile=None, adapter=None):
    """resolve_unit() over every unit in a compile_semantic_plan() result.
    Raises OwnershipResolverError only when semantic_plan itself is not
    usable (not a dict, no "units" list) or project_dir is not a real
    directory -- never for a per-unit gap."""
    if not isinstance(semantic_plan, dict) or not isinstance(
            semantic_plan.get("units"), list):
        raise OwnershipResolverError(
            "semantic_plan must be a dict with a 'units' list (see "
            "mobile_plan_compiler.compile_semantic_plan()) -- got %r"
            % (type(semantic_plan).__name__,))
    if not os.path.isdir(project_dir):
        raise OwnershipResolverError(
            "project_dir is not a real directory: %s" % project_dir)
    jid = semantic_plan.get("journey_id", "unknown")
    resolutions = [
        resolve_unit(u, project_dir, project_profile=project_profile,
                    adapter=adapter)
        for u in semantic_plan["units"]
    ]
    return {"journey_id": jid, "resolutions": resolutions}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("semantic_plan", help="path to a compile_semantic_plan() "
                    "JSON record")
    ap.add_argument("project_dir", help="path to the TARGET project's real "
                    "repository root (never Brother's own repo)")
    ap.add_argument("--profile", help="path to a mobile-project-profile-v1 "
                    "JSON record (scripts/mobile_project_profile.py)")
    ap.add_argument("--adapter", choices=sorted(MPC.ADAPTERS),
                    help="resolve filenames for this adapter id instead of "
                    "one picked from --profile")
    ap.add_argument("--out", help="write the result as JSON to this path "
                    "instead of stdout")
    args = ap.parse_args(argv)
    try:
        semantic_plan = CC.load_json(args.semantic_plan, "semantic plan record")
        profile = (CC.load_json(args.profile, "project profile record")
                  if args.profile else None)
    except CC.NoData as exc:
        print("NO-DATA: %s" % exc)
        return 2
    if not os.path.isdir(args.project_dir):
        print("NO-DATA: project_dir is not a real directory: %s"
             % args.project_dir)
        return 2
    try:
        result = resolve_plan(semantic_plan, args.project_dir,
                              project_profile=profile, adapter=args.adapter)
    except OwnershipResolverError as exc:
        print("FAIL: %s" % exc)
        return 1
    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.out:
        # An unwritable --out (missing parent dir, a directory instead of
        # a file, no permission) must still answer NO-DATA/FAIL/PASS, never
        # an uncaught traceback (Muse adversarial review, EPIC M1.05,
        # finding 7).
        try:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(payload + "\n")
        except OSError as exc:
            print("NO-DATA: could not write --out path %r: %s"
                 % (args.out, exc))
            return 2
        print("PASS: wrote %d resolution(s) to %s"
             % (len(result["resolutions"]), args.out))
    else:
        print(payload)
        by_status = {}
        for r in result["resolutions"]:
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        print("PASS: %d resolution(s) (%s)"
             % (len(result["resolutions"]),
                ", ".join("%s=%d" % kv for kv in sorted(by_status.items()))),
             file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
