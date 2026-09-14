#!/usr/bin/env python3
"""Safe Unwatched Time benchmark harness, row S10: a per-workload-family run of
scripts/safe_unwatched_time.py plus the four preservation checks the row's
done_check names (false greens, scope drift, unrecoverable state, repeated
mistakes), refusing a duration for any family where one of the four is
unmeasured.

WHERE THE TWELVE FAMILIES COME FROM. docs/plan/SWITCHING-STRATEGY-2026-09-04.md
section 19 names them. docs/plan/GAUNTLETS-2026-09-05.md's "twelve workload
families against fixtures that exist" table is this estate's own record of
which family has a real run directory backing it; only families 6 and 7 cite
one (the rest cite a script, a corpus, or a discarded trial the table itself
says is "not a fixture", or nothing at all). This harness reads that table by
hand, once, into FAMILIES below, and never invents a mapping the table does
not state.

WHY EVERY FAMILY BELOW STILL COMES BACK NO-DATA TODAY. Two of the four
preservation checks (unrecoverable_state, repeated_mistakes) have no
per-run instrument anywhere on this estate: docs/plan/GAUNTLETS-2026-09-05.md
says so plainly for recoverability, and repeated-mistake counting is named as
a raw metric in section 19 but implemented nowhere. A third
(scope_drift) can only ever be positively confirmed when the record proves a
VIOLATION (an integrate.refused event); a run with zero such events is
silence, not a clean bill, since no journal event kind on this estate records
"scope was checked and stayed clean". Only false_greens is fully measurable
from a real run's own journal.jsonl/claims.json/receipt (a complete census:
every closed unit's own exit code, every receipt's own proven/unproven tally).
So today, honestly, every family this harness can reach is missing at least
one of the four checks, and this harness says so instead of printing a number.

See benchmarks/safe-unwatched-time/README.md for the full account and
benchmarks/safe-unwatched-time/fixtures/synthetic-complete-example/ for a
labeled, non-real fixture that proves the computation itself is correct when
all four checks DO have a signal.

Exit 0 always: this is a reporter, like scripts/safe_unwatched_time.py, not a
gate. NO-DATA for a family is a correct, complete answer, not a failure.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
import safe_unwatched_time as sut  # noqa: E402

RUNS_ROOT = os.path.join(REPO_ROOT, "docs", "plan", "runs")

# The twelve workload families of docs/plan/SWITCHING-STRATEGY-2026-09-04.md
# section 19, numbered as there. The third element is the run directory name
# under docs/plan/runs that docs/plan/GAUNTLETS-2026-09-05.md's family table
# cites for that family, or None where the table cites nothing that is a real
# run directory (a script, a corpus, or an explicitly-not-a-fixture trial).
FAMILIES = [
    (1, "tiny bug fix", None),
    (2, "medium multi-file feature", None),
    (3, "ambiguous feature requiring autonomous judgment", None),
    (4, "authentication/payment/security change", None),
    (5, "schema/data migration", None),
    (6, "parallelizable multi-component change",
        "parallel-scheduling-adversity-2026-09-04"),
    (7, "crash during execution", "live-autonomous-adversity-2026-09-04"),
    (8, "resume after 24-72 hours with repository drift", None),
    (9, "seeded weak test that produces a false green", None),
    (10, "stale memory contradicting current source", None),
    (11, "long unattended task", None),
    (12, "hostile Japanese identity/disambiguation task", None),
]

# Real run directories on this machine (journal.jsonl + claims.json + a
# receipt record, confirmed present at build time) that
# docs/plan/GAUNTLETS-2026-09-05.md's family table does not tie to any of the
# twelve numbered families. Reported for transparency; never forced into a
# family the estate's own docs do not assign them to.
UNMAPPED_REAL_RUNS = [
    "decomposition-adversity-2026-09-04",
    "scope-auditing-adversity-2026-09-04",
    "serial-integration-adversity-2026-09-04",
    "verification-repair-adversity-2026-09-04",
    os.path.join("i3-loom-2026-09-04", "run"),
]


def _read_preservation(run_dir):
    """preservation.json is this HARNESS's own fixture-only convention (see
    fixtures/synthetic-complete-example/preservation.json), never a record any
    real Brother engine writes. No docs/plan/runs directory carries one; that
    absence is exactly the finding the NO-DATA verdicts below report."""
    path = os.path.join(run_dir, "preservation.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def check_false_greens(receipts, claims, breaks):
    """Break kinds 1 (REFUTED) and 2 (UNPROVEN) of SAFE-UNWATCHED-TIME.md.
    MEASURED iff the run left a complete census to check against: a receipt
    tally (total vs. unproven) and a claims.json giving every closed unit's
    own exit code. Both are full counts, not samples, so their presence really
    does mean the check ran across the whole record."""
    measured = receipts > 0 and bool(claims)
    if not measured:
        return {"measured": False, "ok": None,
                "reason": "no receipt tally and/or no claims.json: nothing "
                          "gives a complete census of verified checks"}
    saw_it = any(kind in (sut.REFUTED, sut.UNPROVEN) for _, kind, _ in breaks)
    return {"measured": True, "ok": not saw_it,
            "reason": ("a claim was refuted by its own check, or a receipt "
                       "was left unproven where a pass was claimed" if saw_it
                       else "every claim's check ran and every receipt was "
                            "proven, across the whole record")}


def check_scope_drift(run_dir, breaks):
    """Break kind 3 (SCOPE) of SAFE-UNWATCHED-TIME.md. A violation
    (integrate.refused naming an undeclared path or a dirty canonical) is
    always MEASURED and always a fail: the engine caught it and said so.
    Zero such events is NOT proof scope stayed clean: no event kind in this
    journal vocabulary records a positive "scope checked, nothing to refuse"
    verdict, so silence cannot be told apart from scope tracking never having
    engaged for this run at all."""
    if any(kind == sut.SCOPE for _, kind, _ in breaks):
        return {"measured": True, "ok": False,
                "reason": "integrate.refused recorded a write outside "
                          "declared scope"}
    preservation = _read_preservation(run_dir)
    if preservation is not None and "scope_checked_clean" in preservation:
        return {"measured": True, "ok": bool(preservation["scope_checked_clean"]),
                "reason": "preservation.json recorded that scope was checked "
                          "and found clean (fixture only, see its _NOTE)"}
    return {"measured": False, "ok": None,
            "reason": "no integrate.refused event fired, which is silence "
                      "rather than proof scope was ever checked"}


def check_unrecoverable_state(run_dir):
    """UNMEASURED for every real run today.
    benchmarks/gauntlets/long-horizon-recovery.json itself carries the
    evidence: RECOVERY TIME's own entry reads `"status": "partial"`, and
    HUMAN INTERVENTIONS' own entry reads `"instrument": "NO INSTRUMENT YET"`.
    No journal event kind or claim field on this estate carries a
    recoverability verdict per run."""
    preservation = _read_preservation(run_dir)
    if preservation is not None and "recoverability_ok" in preservation:
        return {"measured": True, "ok": bool(preservation["recoverability_ok"]),
                "reason": "preservation.json recorded a recoverability "
                          "verdict (fixture only, see its _NOTE)"}
    return {"measured": False, "ok": None,
            "reason": "no instrument on this estate measures recoverability "
                      "per run (benchmarks/gauntlets/long-horizon-recovery.json)"}


def check_repeated_mistakes(run_dir):
    """UNMEASURED for every real run today. REPEATED FAILURE is named as a raw
    metric in docs/plan/SWITCHING-STRATEGY-2026-09-04.md section 19, but no
    script, journal event kind, or claim field on this estate computes it."""
    preservation = _read_preservation(run_dir)
    if preservation is not None and "repeated_mistakes_ok" in preservation:
        return {"measured": True, "ok": bool(preservation["repeated_mistakes_ok"]),
                "reason": "preservation.json recorded a repeated-mistakes "
                          "verdict (fixture only, see its _NOTE)"}
    return {"measured": False, "ok": None,
            "reason": "no instrument on this estate counts repeated mistakes "
                      "per run"}


def evaluate_run(run_dir):
    """The full verdict for one run directory: raw instrument reading (never
    the family's reported duration on its own), the four preservation checks,
    and the family verdict those checks gate."""
    if not os.path.isdir(run_dir):
        return {"verdict": "NO-DATA",
                "reason": "%s does not exist on this machine" % run_dir}
    report, code = sut.measure(run_dir)
    if code != 0:
        return {"verdict": "NO-DATA",
                "reason": report.get("nodata") or
                          "safe_unwatched_time.py returned exit %d" % code}

    events, _skipped = sut.read_journal(run_dir)
    claims = sut.read_claims(run_dir)
    breaks = sut.find_breaks(events, claims)

    checks = {
        "false_greens": check_false_greens(report["receipts"], claims, breaks),
        "scope_drift": check_scope_drift(run_dir, breaks),
        "unrecoverable_state": check_unrecoverable_state(run_dir),
        "repeated_mistakes": check_repeated_mistakes(run_dir),
    }
    unmeasured = [name for name, c in checks.items() if not c["measured"]]

    result = {"raw_instrument": sut.report_line(report), "checks": checks}
    if unmeasured:
        result["verdict"] = "NO-DATA"
        result["reason"] = ("%d of 4 preservation check(s) unmeasured: %s"
                             % (len(unmeasured), ", ".join(sorted(unmeasured))))
    else:
        result["minutes"] = report["minutes"]
        result["units"] = report["units"]
        result["all_preserved"] = all(c["ok"] for c in checks.values())
        result["verdict"] = "%.1f min over %d units" % (
            report["minutes"], report["units"])
    return result


def evaluate_family(family_id, name, run_name):
    if run_name is None:
        return {"family": family_id, "name": name, "run": None,
                "verdict": "NO-DATA",
                "reason": "no real run directory is documented for this "
                          "workload family in docs/plan/GAUNTLETS-2026-09-05.md's "
                          "workload-family table"}
    run_dir = os.path.join(RUNS_ROOT, run_name)
    result = evaluate_run(run_dir)
    result.update({"family": family_id, "name": name, "run": run_name,
                    "run_dir": run_dir})
    return result


def _display_path(path):
    """path, relative to the repo root when it lives under it, else the
    absolute path as given. Always the ACTUAL resolved path: never a
    hardcoded 'docs/plan/runs/' prefix, which renders wrong (doubled or
    nonsensical) for an absolute --extra-run path outside that tree."""
    repo_prefix = REPO_ROOT + os.sep
    if path.startswith(repo_prefix):
        return os.path.relpath(path, REPO_ROOT)
    return path


def _print_result(result, label):
    print(label)
    if result.get("run_dir"):
        print("  run: %s" % _display_path(result["run_dir"]))
    elif result.get("run"):
        print("  run: %s" % result["run"])
    if "raw_instrument" in result:
        print("  raw instrument reading (diagnostic only, NOT the family "
              "verdict): %s" % result["raw_instrument"])
    for check_name in ("false_greens", "scope_drift", "unrecoverable_state",
                        "repeated_mistakes"):
        check = result.get("checks", {}).get(check_name)
        if check is None:
            continue
        print("    %-20s measured=%-5s ok=%-5s %s" % (
            check_name, check["measured"], check["ok"], check["reason"]))
    print("  VERDICT: %s%s" % (
        result["verdict"],
        "" if not result.get("reason") else " -- %s" % result["reason"]))
    print()


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--extra-run", action="append", default=[], metavar="PATH",
        help="an additional run directory (absolute, or a name under "
             "docs/plan/runs) to report on outside the twelve families")
    ap.add_argument(
        "--families-only", action="store_true",
        help="skip the unmapped-real-run section")
    args = ap.parse_args(argv)

    print("Safe Unwatched Time benchmark: twelve workload families "
          "(docs/plan/SWITCHING-STRATEGY-2026-09-04.md section 19)")
    print("=" * 78)
    for family_id, name, run_name in FAMILIES:
        result = evaluate_family(family_id, name, run_name)
        _print_result(result, "family %2d  %s" % (family_id, name))

    # --families-only means "skip the built-in unmapped-real-run list", never
    # "silently drop a run the user explicitly asked for with --extra-run":
    # a user passing both flags together gets --extra-run honored, with a
    # printed note explaining why the built-in list is missing, rather than
    # no output and no explanation.
    extra = list(args.extra_run)
    if args.families_only and args.extra_run:
        print("(--families-only set: skipping the built-in unmapped-real-run "
              "list, still reporting the %d --extra-run path(s) given)"
              % len(args.extra_run))
        print()
    elif not args.families_only:
        extra = list(UNMAPPED_REAL_RUNS) + extra

    if extra:
        print("Additional real run directories, not tied to any of the twelve")
        print("families by docs/plan/GAUNTLETS-2026-09-05.md:")
        print("=" * 78)
        for run_name in extra:
            run_dir = (run_name if os.path.isabs(run_name)
                       else os.path.join(RUNS_ROOT, run_name))
            result = evaluate_run(run_dir)
            result["run"] = run_name
            result["run_dir"] = run_dir
            _print_result(result, "run  %s" % run_name)

    return 0


if __name__ == "__main__":
    sys.exit(run())
