#!/usr/bin/env python3
"""Shared, dependency-free constants and helpers for the two BrotherSBE
PreToolUse write guards: `tools/sbe_authority_hook.py` (structured Edit/Write
targets) and `tools/sbe_bash_write_guard.py` (Bash command text).

WHY THIS FILE EXISTS
  Before this file, each guard reached the other's file for a small piece of
  logic it needed: `sbe_authority_hook.py` did a full sibling-path load of
  `sbe_bash_write_guard.py` (1700+ lines) solely to reach `CONTROL_PLANE_
  PATTERNS`, and `sbe_bash_write_guard.py` did a full sibling-path load of
  `sbe_authority_hook.py` (750+ lines) solely to reach `confirmed_surface`.
  Measured: each load cost 40-45ms of the guard's 45-50ms total wall time per
  PreToolUse call, which fires on every Edit, Write and Bash call in a
  brothersbe-managed project. Neither guard needed anything ELSE from the
  other's file. This file holds exactly the four symbols both sides actually
  use (`CONTROL_PLANE_PATTERNS`, `confirmed_surface`, `_case_fold_candidate`,
  `_known_segments`), has no dependency on either guard, and is small enough
  that loading it costs microseconds instead of tens of milliseconds.

  A hostile refuter of `sbe_bash_write_guard.py` proved why the two guards
  must share exactly one `CONTROL_PLANE_PATTERNS` list rather than two lists
  that happen to agree today: it found a gap where an agent could grant
  itself CLAUDE.md through a tasks.json rewrite the structured-write guard
  allowed, because that guard's own copy of the control-plane list had
  drifted from the Bash guard's. One list, in one file neither guard owns
  more than the other, closes that class of drift for good.

  This module's own file name matches `tools/sbe_*guard*.py`, one of the
  patterns inside `CONTROL_PLANE_PATTERNS` below, so it is itself a protected
  control-plane file exactly like its two callers, with no special casing
  needed to make that true.

Python 3.9, standard library only, cross-platform, no network, no subprocess,
and no import of anything outside this file (that is the whole point: adding
a dependency here would put the cost this file exists to remove right back).
No em or en dashes anywhere in this file, its comments, or its output.
"""

# ---------------------------------------------------------------------------
# The control plane.
#
# `.sbe/tasks.json` and `STATE.md` are here beyond the spec's literal list
# because spec fixture 5 is "a command that deletes .sbe/tasks.json is
# caught", and because 1.4 says in as many words: do not let the agent delete
# the baseline, task registry, fence registry or policy and then treat the
# missing control as permission. A registry that can be removed is not a
# control.
#
# `.sbe/policy.yml`, `.sbe/checks.yml` and `src/brothersbe/policy.py` do not
# exist in this repository yet (they land with blockers 3 and 4). They are
# listed anyway: a pattern that protects a file from the moment it is created
# is the point, and a glob over an absent file matches nothing and costs
# nothing.
# ---------------------------------------------------------------------------

CONTROL_PLANE_PATTERNS = (
    ".sbe/policy.yml",
    ".sbe/checks.yml",
    ".sbe/tasks.json",
    # The grant files. Found by a hostile refuter of this guard: break-glass is
    # the one record that clears any protected change, and it was protected by
    # nothing, so an agent could write itself the exemption through the very
    # channel this file exists to close. Approvals, waivers and decisions grant
    # the same way and belong here for the same reason.
    ".sbe/break-glass.json",
    ".sbe/approvals.json",
    ".sbe/waivers.json",
    ".sbe/decisions/*",
    "src/brothersbe/evidence.py",
    "src/brothersbe/policy.py",
    "tools/sbe_*guard*.py",
    "tools/sbe_*hook*.py",
    "STATE.md",
)


# ---------------------------------------------------------------------------
# Case-insensitive filesystem hazard for the authority-surface match.
#
# `_matched_surface` (in `tools/sbe_instruction_surface.py`) compares exact
# spellings on purpose (CLAUDE.md is not claude.md; that is the surface
# list's own design, not a bug). But a write aimed at "claude.md" on a
# case-insensitive volume (the macOS default) can land on the SAME on-disk
# entry as an existing "CLAUDE.md" regardless of what string the tool call
# spelled, and a check that only ever compares strings would miss it: exactly
# the hazard `tools/sbe_fence_hook.py`'s own `paths_overlap` already handles
# for scope overlap, and the technique is reused here rather than re-derived
# (`_same_entry_case_insensitive`, which confirms two spellings name one
# entry by inode where both exist, or by probing the volume itself when they
# do not, and never trusts a case-folded STRING match alone).
# ---------------------------------------------------------------------------

_KNOWN_SEGMENTS = None


def _known_segments(surface_mod):
    """{lowercased path segment: its canonical spelling}, built once from the
    literal tuples `_matched_surface` itself is built from, plus the fixed
    literals its docstring names (CLAUDE.md, agents, skills, SKILL.md,
    .github, workflows) that do not live in a named tuple. Covers exactly the
    segments the nine detected families are spelled with; a case-folded
    hazard in a segment none of the nine families ever name is out of scope
    by the same logic `_matched_surface` itself uses to decide what counts."""
    global _KNOWN_SEGMENTS
    if _KNOWN_SEGMENTS is not None:
        return _KNOWN_SEGMENTS
    seg = {}
    for d in surface_mod.AUTHORITY_TOP_DIRS:
        seg[d.lower()] = d
    for f in surface_mod.AUTHORITY_ROOT_FILES:
        seg[f.lower()] = f
    for p in surface_mod.AUTHORITY_CODEOWNERS_PATHS:
        for part in p.split("/"):
            seg[part.lower()] = part
    seg["claude.md"] = "CLAUDE.md"
    seg["agents"] = "agents"
    seg["skills"] = "skills"
    seg["skill.md"] = "SKILL.md"
    seg[".github"] = ".github"
    seg["workflows"] = "workflows"
    _KNOWN_SEGMENTS = seg
    return seg


def _case_fold_candidate(surface_mod, rel):
    """`rel` with every path segment that case-insensitively matches a known
    authority-surface segment replaced by that segment's canonical spelling,
    or "" when no segment needed replacing (nothing to fold)."""
    seg = _known_segments(surface_mod)
    parts = rel.split("/")
    changed = False
    fixed = []
    for p in parts:
        canon = seg.get(p.lower())
        if canon is not None and canon != p:
            changed = True
            fixed.append(canon)
        else:
            fixed.append(p)
    if not changed:
        return ""
    return "/".join(fixed)


def confirmed_surface(fence_mod, surface_mod, root, rel):
    """The authority surface `rel` matches, exact spelling first. Failing
    that, the case-folded candidate spelling IF the filesystem confirms the
    two name one entry, never on the string fold alone. Returns "" when
    neither is an authority surface.

    `fence_mod` and `surface_mod` are the caller's own already-loaded
    `sbe_fence_hook` and `sbe_instruction_surface` modules, passed in rather
    than loaded here: this module holds no dependency on either sibling, on
    purpose, so it can never become the next file something else has to pay
    a full exec to reach."""
    surface = surface_mod._matched_surface(rel)
    if surface:
        return surface
    candidate = _case_fold_candidate(surface_mod, rel)
    if not candidate or candidate == rel:
        return ""
    candidate_surface = surface_mod._matched_surface(candidate)
    if not candidate_surface:
        return ""
    if fence_mod._same_entry_case_insensitive(root, rel, candidate):
        return candidate_surface
    return ""
