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
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WBS_SOURCE = os.path.join("docs", "plan", "VAULT-WBS-V2-2026-08-29.json")
NODATA = "NO-DATA"
PASS = "PASS"
FAIL = "FAIL"

#: The restore-drill record's freshness bar, chosen from this estate's own
#: release cadence (docs/releases/, read 2026-09-04): cuts have landed
#: roughly daily to every two days (0.9.6 2026-08-30; 0.9.7 through 0.9.10
#: all 2026-08-31; 0.9.11, 1.0.0 and 1.0.1 all 2026-09-02). Seven days is a
#: full week past the fastest observed gap between cuts -- long enough that
#: a drill run yesterday, or spanning one weekend, still reads PASS, short
#: enough that a drill several release cycles stale reads NO-DATA instead of
#: certifying a tag its own evidence never ran against (evidence auditor,
#: 2026-09-03: "the tag it certifies is three days younger than the drill").
RESTORE_DRILL_MAX_AGE_DAYS = 7

#: Each item mirrors parity_gate's cells: a verdict is granted by the named
#: evidence at `path`, relative to --root, never by assertion.
#:   kind "suite":  `path` is a script run read-only (python3 <path>); its own
#:                  exit code is the evidence. Missing file is NO-DATA.
#:   kind "record": `path` is a JSON file recording a run, read for a boolean
#:                  "passed" field AND bound to the code the run happened on
#:                  (a `commit` that is an ancestor, or a `covered` list of
#:                  files still byte identical). Missing file, missing field
#:                  or missing binding is NO-DATA. An item may add
#:                  "max_age_days" to also require a fresh `drill_date`.
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
     # Freshness is the ONE thing about this row that is drill specific, so
     # it is the one thing the row declares. The commit and content binding
     # is not declared here any more: every record item gets it (row E107).
     "max_age_days": RESTORE_DRILL_MAX_AGE_DAYS,
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


def _commit_is_ancestor(root, commit):
    """True if commit is an ancestor of (or equal to) root's current HEAD,
    False if git can positively say it is not, None if git could not answer
    at all (missing git binary, root is not a git checkout, commit unknown
    to this history -- e.g. rev-parse exit 128). None is always treated as
    unproven, never as a silent pass: a gate that cannot verify ancestry
    must say NO-DATA, not assume it (a known failure class on this estate:
    a gate can be written against a commit that does not exist and nothing
    says so)."""
    try:
        proc = subprocess.run(["git", "merge-base", "--is-ancestor", commit, "HEAD"],
                               cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    return None


def _covered_matches(root, doc):
    """(ok, detail) for a record's CONTENT binding, the second half of the
    ancestry binding in _check_record below. Every record item is bound this
    way, not only the restore drill (row E107).

    WHY A SECOND BINDING EXISTS. `scripts/export_public.py` builds the public
    tree as an ORPHAN commit (build_orphan_commit), so no hub commit is ever
    an ancestor of a public clone's HEAD and the ancestry test cannot pass
    there by construction. Measured 2026-09-04 on the 1.0.2 cut: the export
    tree read "GATE: NOT READY ... Restore drill NO-DATA ... foreign commit"
    while the hub read READY, and E67's tag-time readiness check turned that
    into a refused tag. Ancestry is a proxy for the real property, which is
    that the drill ran against THIS code. In a foreign history that property
    is still checkable directly: hash the files the drill exercised.

    ok True: every covered file is byte-identical to the sha256 the drill
    recorded. ok False: one is not, named. ok None: the record carries no
    usable covered list, so nothing was measured (NO-DATA, never a pass).
    """
    covered = doc.get("covered")
    if not isinstance(covered, list) or not covered:
        return None, "the record carries no covered list"
    for entry in covered:
        if not isinstance(entry, dict):
            return None, "the covered list holds a non-object entry"
        rel = entry.get("path")
        want = entry.get("sha256")
        if not isinstance(rel, str) or not isinstance(want, str) or not rel or not want:
            return None, "a covered entry names no path or no sha256"
        full = os.path.join(root, *rel.split("/"))
        try:
            with open(full, "rb") as fh:
                got = hashlib.sha256(fh.read()).hexdigest()
        except OSError as exc:
            return False, "covered file %s is unreadable in this tree (%s)" % (rel, exc)
        if got != want:
            return False, "covered file %s has changed since the drill ran" % rel
    return True, "%d covered file(s)" % len(covered)


def _check_record(root, relpath, key="passed", today=None, max_age_days=None):
    """(verdict, evidence). A recorded run's boolean field, BOUND to the code
    the run happened on. Every record item gets this binding; none opts in.

    WHY EVERY RECORD ITEM, not only the restore drill (row E107). Until
    2026-09-04 the binding was a per-item `bind_commit` flag and the default
    record path read only passed=true, which is exactly the defect the
    evidence auditor found on the drill on 2026-09-03: an unbound record
    certifies every later tree forever. A binding that has to be remembered
    is one forgotten keyword away from not being there, and the item that
    forgets it is critical by the time anyone notices. So the flag is gone
    and the binding is the record contract itself.

    TWO WAYS TO SATISFY IT, one property. Ancestry (`commit` is an ancestor
    of HEAD) is the direct reading, and a public clone can never satisfy it:
    scripts/export_public.py builds the export as an ORPHAN commit, so no hub
    commit is an ancestor of it, and on 2026-09-04 that refused the 1.0.2 tag
    with "foreign commit". Content (`covered`, a list of path plus sha256
    still byte identical in this tree) is the same property measured
    directly, and it is checkable in ANY history, which is what makes a
    record verifiable by whoever clones the public repository.

    A record with no commit field, or one whose binding cannot be satisfied
    either way, reads NO-DATA naming the specific gap: never a silent pass,
    and never a FAIL, because neither state is a proven break, only an
    unproven claim. `max_age_days`, when the item declares one, adds a
    freshness bar over the record's `drill_date` on top of the binding."""
    path = os.path.join(root, relpath)
    if not os.path.isfile(path):
        return NODATA, "%s does not exist" % relpath
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        return NODATA, "%s unreadable: %s" % (relpath, exc)

    commit = doc.get("commit")
    if not commit:
        return NODATA, ("%s carries no commit field, so nothing binds it to the "
                         "code it ran on" % relpath)

    content_note = ""
    is_ancestor = _commit_is_ancestor(root, commit)
    if is_ancestor is not True:
        covered_ok, covered_detail = _covered_matches(root, doc)
        if covered_ok is not True:
            return NODATA, ("%s: commit %s is not an ancestor of this tree's HEAD, "
                             "or could not be verified -- foreign commit, and %s"
                             % (relpath, commit[:12], covered_detail))
        content_note = ("; bound by content: %s unchanged since %s"
                         % (covered_detail, commit[:12]))

    age_note = ""
    if max_age_days is not None:
        drill_date = doc.get("drill_date")
        if not drill_date:
            return NODATA, "%s carries no drill_date field" % relpath
        from datetime import date
        try:
            ran = date.fromisoformat(drill_date)
        except ValueError:
            return NODATA, "%s: drill_date %r is not an ISO date" % (relpath, drill_date)
        today_date = date.fromisoformat(today) if today else date.today()
        age_days = (today_date - ran).days
        if age_days > max_age_days:
            return NODATA, ("%s: drill_date %s is %d day(s) old, over the %d day "
                             "freshness bar" % (relpath, drill_date, age_days,
                                                 max_age_days))
        age_note = " age %d days" % age_days

    val = doc.get(key)
    if val is True:
        return PASS, ("%s: %s=true commit %s%s%s"
                       % (relpath, key, commit[:12], age_note, content_note))
    if val is False:
        return FAIL, ("%s: %s=false commit %s%s%s"
                       % (relpath, key, commit[:12], age_note, content_note))
    return NODATA, "%s carries no boolean %r field" % (relpath, key)


def evaluate(root=ROOT, today=None):
    """List of row dicts, one per gate item, each verdict backed by named evidence."""
    rows = []
    for spec in ITEMS:
        if spec["kind"] == "suite":
            verdict, evidence = _check_suite(root, spec["path"])
        else:
            verdict, evidence = _check_record(root, spec["path"], today=today,
                                              max_age_days=spec.get("max_age_days"))
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

    rows = apply_valid_exceptions(evaluate(args.root, args.today), args.root, args.today)
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
