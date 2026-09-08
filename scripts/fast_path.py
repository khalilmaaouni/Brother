#!/usr/bin/env python3
"""FAST-0 eligibility and escalation contract (steering 8.3, 8.5, 8.6, 8.10).

NOT WIRED INTO brother_run.py: the seam is the run_door call in main(), see
design-P2.md section 6. This module only decides eligible/not eligible and
formats the escalation marker; nothing here calls brother_run, plans a run,
or spawns a worker.

Every eligibility condition reuses an existing owner rather than inventing a
new classifier (design-P2.md section 3):
  - work_record.check_units     : the unit's own declared-scope contract
  - receipt_door.risk_triggers  : auth/security/payment/migration/destructive
                                   wording, and public-API surface wording
  - autonomy_dial.classify      : architecture/design/scope-expansion risk
  - integrate.dirty_paths       : clean git base
Two rules have no existing owner and are genuinely new here: a check that
already passes (nothing to prove), and FAST_FORBIDDEN_PATHS (manifests,
generated surfaces, CI trigger trees).

Any condition this module cannot prove true returns (False, reason); it
never raises and it never guesses eligible by omission.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import integrate      # dirty_paths
import receipt_door    # risk_triggers
import autonomy_dial   # classify
import work_record     # check_units


#: 8.3's write-scope boundary with no existing owner. Each entry is
#: forbidden to FAST-0 for its own reason, named beside it:
FAST_FORBIDDEN_PATHS = (
    # dependency manifests: system_doc.py and bundle_runtime.py never read
    # these, so a fast unread write here changes what every future install
    # or bundle pulls without either generator noticing
    "requirements.txt", "package.json", "pyproject.toml", "Pipfile",
    "go.mod", "Cargo.toml",
    # generated surfaces: scripts/system_doc.py and scripts/bundle_runtime.py
    # own these; a hand write here is either overwritten by the next
    # generator run or leaves the generator's own --check red
    "SYSTEM.md", "bundle/",
    "docs/plan/READINESS-BOARD.html",
)

#: A path segment that marks a CI trigger tree, github_cost_wall.py's own
#: domain (2026-08-16 law), never FAST-0's to touch unread.
FAST_FORBIDDEN_SEGMENT = ".github/workflows"


def _forbidden_hit(owns):
    """The first declared path that falls inside FAST_FORBIDDEN_PATHS or
    FAST_FORBIDDEN_SEGMENT, or None."""
    for path in owns or []:
        p = str(path)
        if FAST_FORBIDDEN_SEGMENT in p:
            return p
        parts = p.split("/")
        for forb in FAST_FORBIDDEN_PATHS:
            if p == forb or p.endswith("/" + forb) or forb in parts:
                return p
    return None


def _check_already_passes(done_check, cwd, timeout=30):
    """True only when done_check demonstrably exits 0 right now, before any
    work. A timeout, a missing shell, or any other subprocess failure reads
    as NOT already passing (the safer default: it goes through the door
    that actually runs the check), never as eligible by omission."""
    try:
        proc = subprocess.run(done_check, shell=True, cwd=cwd,
                               capture_output=True, timeout=timeout)
    except Exception:
        return False
    return proc.returncode == 0


def eligible(outcome, unit, cwd, env=None):
    """(bool, reason). `unit` is {"id", "objective", "done_check", "owns",
    depends_on (optional)}. Every steering 8.3 condition is proven true in
    order, cheapest and most decisive first; the first condition that
    cannot be proven true returns (False, reason naming the classifier that
    refused it). Never raises: a malformed unit, a missing cwd, or any
    internal error is ineligible, not an exception escaping to the caller."""
    try:
        unit = unit if isinstance(unit, dict) else {}
        owns = list(unit.get("owns") or [])
        raw_done_check = unit.get("done_check")
        if not isinstance(raw_done_check, str) or not raw_done_check.strip():
            return False, ("done_check: not a non-empty string (%r)"
                            % (raw_done_check,))
        done_check = raw_done_check.strip()

        problems = work_record.check_units([unit])
        if problems:
            return False, "work_record.check_units: " + problems[0]

        if len(owns) > 2:
            return False, ("more than 2 declared write paths (%d): FAST-0 "
                            "is single-unit, at-most-2-path only" % len(owns))

        if unit.get("depends_on"):
            return False, ("unit declares depends_on: FAST-0 admits no "
                            "dependency between units")

        hits = receipt_door.risk_triggers([unit])
        if hits:
            name, _uid, words = hits[0]
            return False, "receipt_door.risk_triggers: %s (%s)" % (name, words)

        observables = {
            "single_file_or_named_target": bool(owns) and len(owns) <= 2,
            "contract_change": "none",
            "crosses_boundary": False,
            "reversible_under_hour": True,
        }
        klass = autonomy_dial.classify(observables)
        if klass != "A0":
            return False, "autonomy_dial.classify: %s, not A0" % klass

        forb = _forbidden_hit(owns)
        if forb:
            return False, "FAST_FORBIDDEN_PATHS: %s" % forb

        dirty = integrate.dirty_paths(cwd)
        if dirty is None:
            return False, ("integrate.dirty_paths: git status could not run "
                            "(never guessed clean)")
        if dirty:
            return False, ("integrate.dirty_paths: dirty tree (%s)"
                            % ", ".join(dirty[:3]))

        if _check_already_passes(done_check, cwd):
            return False, ("done_check already passes before any work: "
                            "nothing for FAST-0 to prove")

        return True, "every FAST-0 condition (steering 8.3) proven true"
    except Exception as exc:  # never raise: uncertain reads as ineligible
        return False, "eligible() could not decide: %r" % (exc,)


ESCALATION_MARKER = "FAST-PATH-ESCALATION"


def escalation(undeclared, next_command):
    """A line beginning FAST-PATH-ESCALATION naming every path in
    `undeclared`, plus the normal-path `next_command`; None when
    `undeclared` is empty. Pure formatting only: this never widens a unit's
    `owns`, never merges anything, and never decides the next command
    itself, it only reports the one the caller already computed."""
    paths = [str(p) for p in (undeclared or [])]
    if not paths:
        return None
    return "%s: undeclared path(s) %s; next: %s" % (
        ESCALATION_MARKER, ", ".join(paths), next_command)


if __name__ == "__main__":
    # ponytail: non-trivial branching logic gets one runnable check.
    ok, why = eligible("demo", {"id": "D1", "objective": "x",
                                 "done_check": "false", "owns": ["a.txt"]},
                        os.getcwd())
    print("eligible demo ->", ok, why)
    print("escalation demo ->", escalation(["a", "b"], "brother_run.py --resume x"))
