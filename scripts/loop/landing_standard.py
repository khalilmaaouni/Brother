#!/usr/bin/env python3
"""The loop landing standard as four pure rules (LS1.a, docs/plan/specs/LS1.md).

docs/plan/specs/LOOP-LANDING-STANDARD.md was a page; these are the parts of it a machine can decide, so the scorer
(spec_score.py) and the pool (runner_pool.py) refuse the shapes that cost real builds:
  own_test_refusal    the done check runs a test the sub unit does not name (RL3.a, 2026-10-02: three PASS builds
                      dropped at landing because RL3.a's check ran RL3.b's test).
  mirrored_refusal    a LOOP built sub unit names a file mirrored into bundle/ (DR1, 2026-10-11: such a build can
                      never grade green); a session built one may.
  checklist_missing   the S1 to S12 checklist keys a section does not carry.
  shared_file_hold    two units in flight change one file (S3: a build against an old tree stops applying once its
                      sibling lands), so the later one waits.
Each returns its refusal text, or "" when the rule holds; missing input fails closed (NO-DATA), never passes.
"""
import re

CHECKLIST = ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10", "S11", "S12")
_RUN_TEST = re.compile(r"(?:^|[\s;&|(])(?:/usr/bin/)?python3(?:\s+-[A-Za-z]+)*\s+([^\s;&|]+\.py)\b")


def _norm(path):
    path = str(path).strip()
    while path.startswith("./"):
        path = path[2:]
    return path


def own_test_refusal(done_cmd, section_paths):
    """The first test the done check runs that the section does not name, as a refusal; "" when every one is named."""
    if not isinstance(done_cmd, str) or not done_cmd.strip():
        return "OWN TEST: NO-DATA, no done check"
    named = {_norm(p) for p in (section_paths or ())}
    for ran in _RUN_TEST.findall(done_cmd):
        if _norm(ran) not in named:
            return "OWN TEST: the done check runs %s, which this sub unit does not name (S1, S2)" % _norm(ran)
    return ""


def mirrored_refusal(section_paths, mirrored, loop_built):
    """A loop built section naming a file mirrored into bundle/ is refused; a session built one never is."""
    if not loop_built:
        return ""
    mirrored = {_norm(m) for m in (mirrored or ())}
    for path in (section_paths or ()):
        p = _norm(path)
        rel = p[len("scripts/"):] if p.startswith("scripts/") else None
        if p.startswith("bundle/") or (rel is not None and ("bundle/runtime/" + rel) in mirrored):
            return "MIRRORED: %s is mirrored into bundle/ (S4)" % p
    return ""


def checklist_missing(section):
    """The checklist keys absent from the section; a key counts when a line starts with it followed by a space."""
    text = section if isinstance(section, str) else ""
    starts = set()
    for line in text.splitlines():
        for key in CHECKLIST:
            if line.lstrip().startswith(key + " "):
                starts.add(key)
    return [k for k in CHECKLIST if k not in starts]


def shared_file_hold(candidate, candidate_paths, in_flight):
    """The candidate waits when a unit already in flight changes one of its files; a unit never blocks itself."""
    mine = {_norm(p) for p in (candidate_paths or ())}
    for unit, paths in sorted((in_flight or {}).items()):
        if unit == candidate:
            continue
        for p in sorted(_norm(x) for x in (paths or ())):
            if p in mine:
                return "S3: %s shares %s with %s in flight" % (candidate, p, unit)
    return ""
