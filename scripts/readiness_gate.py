"""readiness_gate: the review's enterprise readiness gate as a checkable surface.

WHY THIS EXISTS (docs/plan/VAULT-WBS-V2-2026-08-29.json, row VB3-12): the
review named six gate items (reproducible benchmark, tenancy leakage zero,
fail-closed policy, Japanese threshold, restore drill, reproducible release
artifact) that match this estate's evidence discipline. They belong on a
checkable surface, never in prose, and mirror scripts/parity_gate.py's rule:
a verdict is granted by NAMED evidence, never assertion. No evidence is
NO-DATA and NO-DATA is never a pass.

FIFTEEN-QUESTION PR BAR, a known gap named rather than hidden: the row's own
text refers to "the review's ... fifteen definition-of-done questions", but
no file in this repository (docs/plan/, docs/plan/research/, or git history)
enumerates those fifteen questions. Searched: every *.md and *.json under
docs/plan and docs/plan/research for "fifteen", "definition-of-done",
"readiness gate", "restore drill", "tenancy leakage", "fail-closed policy",
"Japanese threshold", "reproducible release artifact", "PR bar", plus
`git log --all -S"fifteen question"` (one hit: the commit that wrote this
very row, ff836e0, which added no other file). The reference page this row
also asks for (docs/plan/FIFTEEN-QUESTION-PR-BAR.md) says so plainly instead
of inventing fifteen questions nobody wrote.

Python 3, standard library only. No network.
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WBS_SOURCE = os.path.join("docs", "plan", "VAULT-WBS-V2-2026-08-29.json")
NODATA = "NO-DATA"
PASS = "PASS"
FAIL = "FAIL"

#: Each item mirrors parity_gate's cells: a verdict is granted by the named
#: evidence at `path`, relative to --root, never by assertion.
#:   kind "suite":  `path` is a script run read-only (python3 <path>); its own
#:                  exit code is the evidence. Missing file is NO-DATA.
#:   kind "record": `path` is a JSON file recording a run, read for a boolean
#:                  "passed" field. Missing file or field is NO-DATA.
ITEMS = [
    {"id": "reproducible-benchmark", "title": "Reproducible benchmark",
     "critical": True, "kind": "suite",
     "path": os.path.join("scripts", "test_make_benchmark_bundle.py"),
     "blocker": None},
    {"id": "tenancy-leakage-zero", "title": "Tenancy leakage zero",
     "critical": True, "kind": "suite",
     "path": os.path.join("scripts", "test_tenancy_isolation.py"),
     "blocker": "VB3-03 landed in BrotherModeUp (PR 159); the Brother-side evidence suite that proves it from this repository is queued"},
    {"id": "fail-closed-policy", "title": "Fail-closed policy",
     "critical": True, "kind": "suite",
     "path": os.path.join("scripts", "test_policy_fail_closed.py"),
     "blocker": "VB3-04 landed in BrotherModeUp (PR 160); the Brother-side evidence suite that proves it from this repository is queued"},
    # V6, flipped critical at M4 exactly as pinned in the vault hardening
    # scope: pre-M4 a missing BrotherModeUp checkout made this row NO-DATA,
    # and critical-NO-DATA would have punished a missing checkout rather than
    # missing proof. Since the M4 subtree landing the tool and fixture are
    # in-tree, the NO-DATA case cannot exist on any clone, so the row now
    # carries the criticality the benchmark deserves.
    {"id": "japanese-threshold", "title": "Japanese threshold",
     "critical": True, "kind": "suite",
     "path": os.path.join("scripts", "test_japanese_threshold.py"),
     "blocker": "VB2-03 (Japanese-first retrieval benchmark) landed in BrotherModeUp "
                "PR 176 (tools/bm_vault_jbench.py, tools/fixtures/japanese-benchmark.json, "
                "245 cases, six classes, per-class floors); this Brother-side wrapper runs "
                "that tool as a black box to prove it from this repository"},
    {"id": "restore-drill", "title": "Restore drill",
     "critical": True, "kind": "record",
     "path": os.path.join("docs", "plan", "RESTORE-DRILL-ENTERPRISE-RESULT.json"),
     "blocker": "no enterprise restore drill has proven the governed stores "
                "(populated multi-tenant backup, destroy, restore, validate)"},
    {"id": "reproducible-release-artifact", "title": "Reproducible release artifact",
     "critical": False, "kind": "suite",
     "path": os.path.join("scripts", "release_invariant.py"),
     "blocker": "no release invariant tool exists in this estate",
     # The battery-exceptions check name this item may be excepted under: the
     # invariant goes red BY DESIGN when runtime content lands between
     # releases, and the estate's declared-exception mechanism is how that
     # state is carried honestly until the next crossing. Only a VALID
     # (declared, unexpired) entry converts the FAIL, it prints as EXCEPTED
     # rather than PASS, and critical items never take an exception.
     "exception_key": "release-invariant"},
]


def _check_suite(root, relpath):
    """(verdict, evidence). Runs the script read-only; its exit code is the evidence."""
    path = os.path.join(root, relpath)
    if not os.path.isfile(path):
        return NODATA, "%s does not exist" % relpath
    proc = subprocess.run([sys.executable, path],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.returncode == 0:
        return PASS, "%s exit 0" % relpath
    # Exit 2 is this estate's own NO-DATA convention (see check_all.sh's
    # run_check: "0) pass; 2) nodata; *) fail"). A suite that cannot reach a
    # verdict -- an evidence boundary it could not read -- is unproven, not
    # broken, and reporting it as FAIL would hide that distinction from
    # evaluate()'s blocker-string append below.
    if proc.returncode == 2:
        return NODATA, "%s exit 2" % relpath
    return FAIL, "%s exit %d" % (relpath, proc.returncode)


def _check_record(root, relpath, key="passed"):
    """(verdict, evidence). Reads a recorded run's boolean field."""
    path = os.path.join(root, relpath)
    if not os.path.isfile(path):
        return NODATA, "%s does not exist" % relpath
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        return NODATA, "%s unreadable: %s" % (relpath, exc)
    val = doc.get(key)
    if val is True:
        return PASS, "%s: %s=true" % (relpath, key)
    if val is False:
        return FAIL, "%s: %s=false" % (relpath, key)
    return NODATA, "%s carries no boolean %r field" % (relpath, key)


def evaluate(root=ROOT):
    """List of row dicts, one per gate item, each verdict backed by named evidence."""
    rows = []
    for spec in ITEMS:
        if spec["kind"] == "suite":
            verdict, evidence = _check_suite(root, spec["path"])
        else:
            verdict, evidence = _check_record(root, spec["path"])
        if verdict == NODATA and spec["blocker"]:
            evidence = "%s -- %s" % (evidence, spec["blocker"])
        rows.append({"id": spec["id"], "title": spec["title"],
                     "critical": spec["critical"], "verdict": verdict,
                     "evidence": evidence})
    return rows


def blocking(rows):
    """Critical rows that are not PASS. NO-DATA blocks exactly like FAIL: unproven
    is not permission to open the gate."""
    return [r for r in rows if r["critical"] and r["verdict"] != PASS]


def noncritical_fails(rows):
    """Non-critical rows that FAILED. A definite FAIL is a proven break and is
    worse than a NO-DATA, so it must stop the top line reading READY even off
    the critical path; a NO-DATA on a non-critical item stays a non-blocking
    honest unknown. This closes the gap six independent acceptance reviewers
    all caught: the gate exited 0 (read as READY) while a non-critical check
    was FAILing on the same tree, so exit 0 was not the truth."""
    return [r for r in rows if not r["critical"] and r["verdict"] == FAIL]


def apply_valid_exceptions(rows, root=ROOT, today=None):
    """Convert a non-critical FAILing row into EXCEPTED when its item declares
    an exception_key with a VALID (declared, unexpired) entry in
    BATTERY-EXPECTATIONS.json. The gate's own closing sentence has always
    claimed it honors declared exceptions and polices only their expiry;
    until 2026-09-01 it policed expiry but hard-failed the excepted item
    itself, which contradicted the mechanism the expectations file exists
    for. EXCEPTED is printed as EXCEPTED, never laundered into PASS, and a
    critical item never takes an exception."""
    keyed = {spec["id"]: spec.get("exception_key")
             for spec in ITEMS if spec.get("exception_key")}
    if not keyed:
        return rows
    path = os.path.join(root, "docs", "plan", "BATTERY-EXPECTATIONS.json")
    if not os.path.isfile(path):
        return rows
    try:
        import battery_verdict as BV
        checks = BV.load_expectations(path)
    except (OSError, ValueError, ImportError):
        return rows
    if today is None:
        from datetime import date
        today = date.today().isoformat()
    for r in rows:
        key = keyed.get(r["id"])
        if (key and not r["critical"] and r["verdict"] == FAIL
                and key in checks and not BV._expired(checks[key], today)):
            r["verdict"] = "EXCEPTED"
            r["evidence"] += (" -- declared exception %r stands until %s: %s"
                              % (key, checks[key].get("review_by", "?"),
                                 (checks[key].get("reason") or "")[:160]))
    return rows


def expired_exceptions(root=ROOT, today=None):
    """Declared battery exceptions whose review_by date has passed. The
    expectations file's own rule (red-team item 6): an exception past its
    review_by turns blocking, because a failure cemetery is not allowed. This
    surfaces the same expiry the consolidated battery verdict enforces, so the
    gate and battery_verdict agree end to end. It reuses battery_verdict's own
    date logic rather than re-deriving it. Returns [] when nothing is declared
    (file absent) or the helper cannot be loaded, never raising: an absent
    expectations file is 'no declared exceptions', not a gate failure."""
    path = os.path.join(root, "docs", "plan", "BATTERY-EXPECTATIONS.json")
    if not os.path.isfile(path):
        return []
    try:
        import battery_verdict as BV
        checks = BV.load_expectations(path)
    except (OSError, ValueError, ImportError):
        return []
    if today is None:
        from datetime import date
        today = date.today().isoformat()
    return sorted(name for name, entry in checks.items()
                  if BV._expired(entry, today))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--today", default=None,
                    help="ISO date to compare declared-exception review_by "
                         "against (default: today); mirrors battery_verdict")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    rows = apply_valid_exceptions(evaluate(args.root), args.root, args.today)
    block = blocking(rows)
    nc_fails = noncritical_fails(rows)
    expired = expired_exceptions(args.root, args.today)

    if args.json:
        print(json.dumps({"rows": rows, "blocking": [r["id"] for r in block],
                          "noncritical_failures": [r["id"] for r in nc_fails],
                          "expired_exceptions": expired},
                          indent=2, sort_keys=True))
        return 1 if (block or nc_fails or expired) else 0

    print("ENTERPRISE READINESS GATE")
    print("source of the six items: %s, row VB3-12" % WBS_SOURCE)
    print("")
    for r in rows:
        print("  %-30s %-8s%s" % (r["title"], r["verdict"],
                                   "  CRITICAL" if r["critical"] else ""))
        print("        %s" % r["evidence"])
    print("")
    print("fifteen-question PR bar: docs/plan/FIFTEEN-QUESTION-PR-BAR.md "
          "(source not found in this repository; see that file for the search log)")
    print("")
    if block or nc_fails or expired:
        print("GATE: NOT READY.")
        if block:
            print("  %d critical item(s) unproven:" % len(block))
            for b in block:
                print("  - %s (%s)" % (b["title"], b["verdict"]))
        if nc_fails:
            print("  %d non-critical item(s) FAILING (a proven break blocks "
                  "READY even off the critical path):" % len(nc_fails))
            for b in nc_fails:
                print("  - %s (%s)" % (b["title"], b["verdict"]))
        if expired:
            print("  %d declared battery exception(s) past review_by (a failure "
                  "cemetery is not allowed):" % len(expired))
            for name in expired:
                print("  - %s" % name)
        return 1
    print("GATE: every critical item is proven, no item is FAILING, and no "
          "declared exception is past its review date. A non-critical item may "
          "still read NO-DATA without blocking.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
