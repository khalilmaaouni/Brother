#!/usr/bin/env python3
"""ORCH-26: an advisory linter over a dispatch brief, demoted on purpose.

WHY DEMOTED. The first draft of this row blocked dispatch on any brief that
failed it. Two independent adversaries rejected that design with the same
objection: every field in a brief is mechanically generatable, so a
presence-only check (does this key exist, is it a string) proves nothing
about whether the brief is any good, and a blocking gate that can be
satisfied by boilerplate becomes paperwork nobody reads. This module is
deliberately advisory: `main()` always exits 0 when it could read the plan,
whatever it finds. It prints findings, it never blocks a dispatch.

WHAT KEEPS IT FROM BEING WORTHLESS PAPERWORK ANYWAY: most of its checks are
SEMANTIC, comparing one field against another or one unit against its
siblings, not just checking a key is present.

  - owns emptiness: an empty or missing "owns" is flagged, because nothing
    then declares what the unit may write.
  - done_check relevance: the done_check string must mention at least one
    owned path (or its basename). A done_check can be present, non-empty
    and syntactically fine while being copy-pasted from a different unit
    and proving nothing about the unit's own owned files; only comparing
    the two fields against each other catches that, presence of either
    field alone cannot.
  - cross-brief scope collision: two units in the same plan owning the same
    path is a conflict that is invisible to any check that looks at one
    brief at a time; it only appears when the whole plan is compared.
  - enum membership: task_class and evidence_obligation are checked against
    the vocabulary in scripts/orchestrator_invariants.py, imported rather
    than retyped, per that module's own rule against a second copy of a
    list it already holds.

Python 3.9, standard library only. No network.

Usage:
  python3 scripts/brief_lint.py --plan docs/plan/ORCH-1020-WBS.json
Exit 0 whenever the plan could be read (advisory: findings are printed, not
enforced). Exit 2 only when the plan file could not be read or parsed: a
"could not check" state is not the same as "no findings", and is never
silently read as a pass.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchestrator_invariants as INV  # noqa: E402


class Unreadable(Exception):
    """The plan could not be read or parsed, so nothing may be concluded."""


def _basename(path):
    return os.path.basename(path.rstrip("/"))


def lint_brief(brief, allowed_task_classes=None, allowed_evidence_obligations=None):
    """Findings for one brief dict. Empty list means nothing was flagged.

    allowed_task_classes / allowed_evidence_obligations are caller-supplied
    sets rather than hardcoded here, so this module never carries its own
    copy of a vocabulary scripts/orchestrator_invariants.py already owns.
    """
    findings = []

    owns = brief.get("owns")
    if not isinstance(owns, list) or not owns:
        findings.append(
            "owns is empty or missing: nothing declares what files this unit may write"
        )
        owns = []

    if owns:
        done_check = brief.get("done_check")
        if not isinstance(done_check, str) or not done_check.strip():
            findings.append(
                "done_check is empty or missing: no command proves the unit is finished"
            )
        else:
            matched = any(
                isinstance(path, str)
                and (path in done_check or (_basename(path) and _basename(path) in done_check))
                for path in owns
            )
            if not matched:
                findings.append(
                    "done_check mentions none of owns %r: it may be copy-pasted from "
                    "another unit and proves nothing about this unit's own files" % owns
                )

    if allowed_task_classes is not None:
        task_class = brief.get("task_class")
        if task_class not in allowed_task_classes:
            findings.append(
                "task_class %r is not one of the allowed values %r"
                % (task_class, sorted(allowed_task_classes))
            )

    if allowed_evidence_obligations is not None:
        evidence = brief.get("evidence_obligation")
        if evidence not in allowed_evidence_obligations:
            findings.append(
                "evidence_obligation %r is not one of the allowed values %r"
                % (evidence, sorted(allowed_evidence_obligations))
            )

    return findings


def lint_wbs(wbs, allowed_task_classes=None, allowed_evidence_obligations=None):
    """{unit_id: [findings]} for every unit in a WBS-shaped dict, including
    the cross-brief scope collision check that a per-brief lint cannot see."""
    if not isinstance(wbs, dict):
        return {"<wbs>": ["plan is not a JSON object"]}
    units = wbs.get("units")
    if not isinstance(units, list):
        return {"<wbs>": ['plan has no "units" list']}

    results = {}

    def uid_of(unit, i):
        uid = unit.get("id") if isinstance(unit, dict) else None
        return uid if isinstance(uid, str) and uid else "<unit-%d>" % i

    for i, unit in enumerate(units):
        if not isinstance(unit, dict):
            results.setdefault("<unit-%d>" % i, []).append("unit is not a JSON object")
            continue
        uid = uid_of(unit, i)
        findings = lint_brief(unit, allowed_task_classes, allowed_evidence_obligations)
        if findings:
            results.setdefault(uid, []).extend(findings)

    owners = {}
    for i, unit in enumerate(units):
        if not isinstance(unit, dict):
            continue
        uid = uid_of(unit, i)
        owns = unit.get("owns")
        if not isinstance(owns, list):
            continue
        for path in owns:
            if isinstance(path, str):
                owners.setdefault(path, set()).add(uid)

    for path, uids in owners.items():
        if len(uids) > 1:
            uids_sorted = sorted(uids)
            for uid in uids_sorted:
                others = [u for u in uids_sorted if u != uid]
                results.setdefault(uid, []).append(
                    "scope collision: path %r is also owned by %r" % (path, others)
                )

    return results


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise Unreadable("%s could not be read: %s" % (path, exc))
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise Unreadable("%s is not valid JSON: %s" % (path, exc))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Advisory brief linter. Prints findings, never blocks: "
                    "exit 0 whenever the plan could be read, exit 2 only "
                    "when it could not be read or parsed."
    )
    ap.add_argument("--plan", required=True)
    a = ap.parse_args(argv)

    try:
        wbs = _read_json(a.plan)
    except Unreadable as exc:
        print("brief-lint: NO-DATA, could not check: %s" % exc)
        return 2

    findings_by_unit = lint_wbs(
        wbs,
        allowed_task_classes=INV.TASK_CLASSES,
        allowed_evidence_obligations=INV.EVIDENCE_OBLIGATIONS,
    )

    if not findings_by_unit:
        print("brief-lint: advisory, no findings in %s" % a.plan)
        return 0

    total = 0
    for uid in sorted(findings_by_unit):
        findings = findings_by_unit[uid]
        total += len(findings)
        print("unit %s:" % uid)
        for finding in findings:
            print("  - %s" % finding)
    print("brief-lint: advisory, %d finding(s) across %d unit(s) in %s. "
          "Nothing here blocks dispatch." % (total, len(findings_by_unit), a.plan))
    return 0


if __name__ == "__main__":
    sys.exit(main())
