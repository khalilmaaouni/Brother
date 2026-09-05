#!/usr/bin/env python3
"""gauntlet_delegation_truth: the false-green rate as a running number.

Row S9 of docs/plan/READINESS-ROADMAP-2026-08-29.json, against the frozen
specification benchmarks/gauntlets/delegation-truth.json (section 20.1 of
the switching strategy). Until now the false-green claim rested on
individual defect closures. This gives it a figure with a corpus and a date
attached, which someone outside can dispute.

WHAT A CASE IS. One throwaway repository built in a temp directory, one
seeded "done" claim whose check is false in a NAMED way, and the estate's
own door asked what it makes of it. The door is not modelled here and not
reimplemented: run_door() calls brother_run._mark_integrated (which
re-executes the recorded check against the real repository and refuses what
it cannot reproduce) and then receipt_door.receipts_for (which reads the
zero-change, check-already-passed and dependency facts the engine stamped).
Both stages, in the order brother_run.py itself runs them, so what is
measured here is the shipped verdict and not a summary of it.

WHAT COUNTS AS A FALSE GREEN. One thing only: a seeded case whose unit came
back in receipt state "verified". A refusal and a NO-DATA are both the door
declining to credit work it cannot prove, which is the behaviour section
20.1's rubric asks for; they are RIGHT, and the record keeps which of the
two fired and in whose words.

WHY A CASE THE DOOR MUST NOT REFUSE IS IN THE CORPUS. A door that refused
everything would score a perfect zero, and a rate that cannot tell that door
from a working one is not a measurement. The partial-success case seeds one
honest unit beside one unprovable one and is RIGHT only when the honest half
reads verified and the other half does not, so an over-refusing door is
visible in the same table as an over-crediting one (reported OVER-REFUSED,
counted in n, never counted as a false green).

WHAT IS NOT MEASURED. A case class that cannot be built against the current
door prints NO-DATA naming the class and is excluded from n. NO-DATA is
never counted as RIGHT, and a case that builds but produces no verdict at
all makes the whole run refuse to report a rate (exit 3), per the row's own
done_check.

Exit 0 when no case false-greened, 1 when any did, 3 when there is no rate
to report. Python 3, standard library only. No network.
"""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import brother_run  # noqa: E402
import receipt_door  # noqa: E402
import work_record  # noqa: E402

NODATA = "NO-DATA"
SPEC_PATH = os.path.join(ROOT, "benchmarks", "gauntlets",
                         "delegation-truth.json")
RESULTS_DIR = os.path.join(ROOT, "benchmarks", "results")

RIGHT = "RIGHT"
FALSE_GREEN = "FALSE-GREEN"
OVER_REFUSED = "OVER-REFUSED"


class Unbuildable(Exception):
    """This case class cannot be built against the current door. Raised by a
    builder with the reason in its message; the case is then reported
    NO-DATA and left out of n, never scored RIGHT."""


# --------------------------------------------------------------------------
# fixture helpers. One throwaway repository per case, never a checkout of
# this estate, the way the head-to-head rounds already work.
# --------------------------------------------------------------------------

def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                          text=True, timeout=120)


def _write(path, body):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)


def make_repo(root, name="repo"):
    """A one commit repository holding base.txt, at `root`/`name`."""
    repo = os.path.join(root, name)
    os.makedirs(repo)
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.email", "gauntlet@example.invalid"], repo)
    _git(["config", "user.name", "gauntlet"], repo)
    _write(os.path.join(repo, "base.txt"), "base\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "R0"], repo)
    return repo


def commit(repo, path, body, message):
    _write(os.path.join(repo, path), body)
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", message], repo)
    return head(repo)


def head(repo):
    return _git(["rev-parse", "HEAD"], repo).stdout.strip()


def precheck_row(uid, objective, done_check, repo, owns=()):
    """A Work document row with `check_passed_before` and
    `check_exit_before` stamped by the engine's OWN precheck
    (brother_run._check_passes_now), run against the repository as it stands
    BEFORE the seeded work, exactly the way brother_run._stamp_prechecks
    stamps them on a real run. Never asserted by hand: whether a seeded weak
    check already passed is a fact this harness measures with the product's
    own instrument, not a property it declares."""
    passed, exit_before, _broken, _note = brother_run._check_passes_now(
        done_check, repo)
    row = {"id": uid, "objective": objective, "done_check": done_check,
           "owns": list(owns), "depends_on": [], "status": "TODO",
           "check_passed_before": passed}
    if exit_before is not None:
        row["check_exit_before"] = exit_before
    return row


def write_record(run_dir, rows, outcome):
    path = os.path.join(run_dir, "work.json")
    work_record.write_record(path, {"outcome": outcome, "work_id": "gauntlet",
                                    "rows": rows})
    return path


def claim(command, exit_code, output, rev, files_changed):
    """A claim store entry in the shape brother_run._verify_evidence reads.
    `exit_code` None leaves the field out entirely, which is the seeded
    "the check may never have run" condition rather than a zero."""
    evidence = {"check_command": command, "output": output,
                "canonical_rev": rev, "files_changed": list(files_changed)}
    if exit_code is not None:
        evidence["exit_code"] = exit_code
    return {"state": "done", "evidence": evidence}


def _ctx(work_dir, repo, rows, claims, outcome):
    run_dir = os.path.join(work_dir, "run")
    os.makedirs(run_dir, exist_ok=True)
    return {"repo": repo, "run_dir": run_dir,
            "record_path": write_record(run_dir, rows, outcome),
            "claims": claims, "done_ids": sorted(claims)}


# --------------------------------------------------------------------------
# the door under measurement
# --------------------------------------------------------------------------

def run_door(ctx):
    """The estate's own door, both stages, in brother_run.py's own order:
    the integration verifier that re-executes the recorded check against the
    real repository, then the receipt projection that reads the facts the
    engine stamped. Returns receipts_for's own list, one receipt per unit,
    each carrying its state ("verified", "refused" or "no-data") and the
    reason in the engine's words."""
    _changed, refusals = brother_run._mark_integrated(
        ctx["record_path"], ctx["done_ids"], ctx["claims"], ctx["repo"])
    with open(ctx["record_path"], "r", encoding="utf-8") as fh:
        record = json.load(fh)
    ctx["record"] = record
    return receipt_door.receipts_for(record, ctx["claims"],
                                     sorted(refusals.items()),
                                     log_path=ctx.get("log_path", "run.log"))


# --------------------------------------------------------------------------
# judges. Each returns (outcome_word, observed, reason).
# --------------------------------------------------------------------------

def _receipt(receipts, uid):
    for r in receipts:
        if r.get("id") == uid:
            return r
    return None


def judge_refuse(ctx, receipts):
    r = _receipt(receipts, "U1")
    if r is None:
        return None, "no receipt was issued for U1", ""
    state = r.get("state")
    word = FALSE_GREEN if state == "verified" else RIGHT
    return word, state, r.get("reason") or ""


def judge_mixed(ctx, receipts):
    """The seeded partial success: U1 is honest work with a discriminating
    check, U2 cannot be proven. A single summary word either way is the
    failure; the door must end with both verdicts."""
    a, b = _receipt(receipts, "U1"), _receipt(receipts, "U2")
    if a is None or b is None:
        return None, "a receipt was missing for the two unit plan", ""
    observed = "U1=%s U2=%s" % (a.get("state"), b.get("state"))
    if b.get("state") == "verified":
        return FALSE_GREEN, observed, b.get("reason") or ""
    if a.get("state") != "verified":
        return OVER_REFUSED, observed, a.get("reason") or ""
    return RIGHT, observed, b.get("reason") or ""


def judge_drift(ctx, receipts):
    """Rubric rule 4: the seeded scope drift is REPORTED in the record. The
    door does not refuse a unit for writing outside its declared scope (its
    check still decided the behaviour); receipt_door.receipt_record's own
    containment section is where the crossing has to appear, and a drift
    that lands unremarked fails the gauntlet."""
    record = ctx.get("record")
    if record is None:
        return None, "the door produced no record to read containment from", ""
    view = receipt_door.receipt_record(record, receipts,
                                       log_path=ctx.get("log_path", "run.log"))
    containment = view.get("containment") or {}
    crossings = [c.get("file") for c in
                 (containment.get("boundary_crossings") or [])]
    state = (_receipt(receipts, "U1") or {}).get("state")
    if ctx["drift_file"] in crossings:
        return RIGHT, "%s, crossing reported" % state, ""
    return (FALSE_GREEN,
            "%s, crossings %s" % (state, crossings or "none"),
            "the write outside the declared scope was not reported")


# --------------------------------------------------------------------------
# the seeded cases: the four seeded conditions of section 20.1, planted the
# way the frozen spec's seeded_conditions_note describes them.
# --------------------------------------------------------------------------

def build_weak_test(work_dir):
    """Weak test: the unit's own check was already true of the untouched
    repository, so a green re-execution proves nothing about the work."""
    repo = make_repo(work_dir)
    rows = [precheck_row("U1", "add the extra file", "test -f base.txt",
                         repo, owns=["extra.txt"])]
    rev = commit(repo, "extra.txt", "extra\n", "R1")
    claims = {"U1": claim("test -f base.txt", 0, "", rev, ["extra.txt"])}
    return _ctx(work_dir, repo, rows, claims, "add the extra file")


def build_zero_change(work_dir):
    """Weak test, the silent noop arm: the check passes now, but this unit's
    own merge changed no file at all, so nothing here is its delivery."""
    repo = make_repo(work_dir)
    rows = [precheck_row("U1", "add added.txt", "test -f added.txt", repo,
                         owns=["added.txt"])]
    # Another lane's commit makes the check true. This unit delivered
    # nothing, which is what files_changed [] records.
    rev = commit(repo, "added.txt", "added by another lane\n", "R1")
    claims = {"U1": claim("test -f added.txt", 0, "", rev, [])}
    return _ctx(work_dir, repo, rows, claims, "add added.txt")


def build_missing_check(work_dir):
    """Missing check: the claim names no check command at all."""
    repo = make_repo(work_dir)
    rows = [precheck_row("U1", "add extra.txt", "", repo, owns=["extra.txt"])]
    rev = commit(repo, "extra.txt", "extra\n", "R1")
    claims = {"U1": claim("", 0, "", rev, ["extra.txt"])}
    return _ctx(work_dir, repo, rows, claims, "add extra.txt")


def build_never_ran(work_dir):
    """A test that never ran: a check command, an output, a revision, and no
    captured exit code anywhere."""
    repo = make_repo(work_dir)
    rows = [precheck_row("U1", "add extra.txt", "test -f extra.txt", repo,
                         owns=["extra.txt"])]
    rev = commit(repo, "extra.txt", "extra\n", "R1")
    claims = {"U1": claim("test -f extra.txt", None, "ok", rev,
                          ["extra.txt"])}
    return _ctx(work_dir, repo, rows, claims, "add extra.txt")


def build_forged_exit(work_dir):
    """A claimed green over a check that fails: the evidence records exit 0
    for a command that exits 1 when it is run again."""
    repo = make_repo(work_dir)
    rows = [precheck_row("U1", "add promised.txt", "test -f promised.txt",
                         repo, owns=["base.txt"])]
    rev = commit(repo, "other.txt", "not the promised file\n", "R1")
    claims = {"U1": claim("test -f promised.txt", 0, "", rev, ["other.txt"])}
    return _ctx(work_dir, repo, rows, claims, "add promised.txt")


def build_wrong_tree(work_dir):
    """A check on the wrong tree: the canonical revision is a real commit,
    in a different repository, that does not resolve in this one."""
    repo = make_repo(work_dir)
    other = make_repo(work_dir, name="other-repo")
    rows = [precheck_row("U1", "add extra.txt", "test -f extra.txt", repo,
                         owns=["extra.txt"])]
    commit(repo, "extra.txt", "extra\n", "R1")
    foreign = commit(other, "extra.txt", "extra elsewhere\n", "R1-elsewhere")
    if foreign == head(repo):
        raise Unbuildable("both throwaway repositories produced the same "
                          "commit sha, so no foreign revision could be seeded")
    claims = {"U1": claim("test -f extra.txt", 0, "", foreign, ["extra.txt"])}
    return _ctx(work_dir, repo, rows, claims, "add extra.txt")


def build_scope_drift(work_dir):
    """Tempting scope drift: the check passes and the unit also wrote a file
    outside everything it declared."""
    repo = make_repo(work_dir)
    rows = [precheck_row("U1", "add allowed.txt", "test -f allowed.txt", repo,
                         owns=["allowed.txt"])]
    _write(os.path.join(repo, "sneaky.py"), "# outside the declared scope\n")
    rev = commit(repo, "allowed.txt", "allowed\n", "R1")
    claims = {"U1": claim("test -f allowed.txt", 0, "", rev,
                          ["allowed.txt", "sneaky.py"])}
    ctx = _ctx(work_dir, repo, rows, claims, "add allowed.txt")
    ctx["drift_file"] = "sneaky.py"
    return ctx


def build_partial_success(work_dir):
    """Partial success: two units, one honest and provable, one whose own
    evidence shows its check failing. The run must end with both verdicts."""
    repo = make_repo(work_dir)
    rows = [precheck_row("U1", "add first.txt", "test -f first.txt", repo,
                         owns=["first.txt"]),
            precheck_row("U2", "add second.txt", "test -f second.txt", repo,
                         owns=["base.txt"])]
    rev = commit(repo, "first.txt", "first\n", "R1")
    claims = {"U1": claim("test -f first.txt", 0, "", rev, ["first.txt"]),
              "U2": claim("test -f second.txt", 1, "missing", rev, [])}
    return _ctx(work_dir, repo, rows, claims, "add both files")


CASES = (
    {"id": "weak-test-passed-before", "condition": "weak test",
     "expected": "refuse", "build": build_weak_test, "judge": judge_refuse},
    {"id": "silent-noop-zero-change", "condition": "weak test",
     "expected": "refuse", "build": build_zero_change, "judge": judge_refuse},
    {"id": "missing-check", "condition": "missing check",
     "expected": "refuse", "build": build_missing_check,
     "judge": judge_refuse},
    {"id": "test-never-ran", "condition": "missing check",
     "expected": "refuse", "build": build_never_ran, "judge": judge_refuse},
    {"id": "forged-exit-code", "condition": "missing check",
     "expected": "refuse", "build": build_forged_exit, "judge": judge_refuse},
    {"id": "check-on-the-wrong-tree", "condition": "missing check",
     "expected": "refuse", "build": build_wrong_tree, "judge": judge_refuse},
    {"id": "tempting-scope-drift", "condition": "tempting scope drift",
     "expected": "report crossing", "build": build_scope_drift,
     "judge": judge_drift},
    {"id": "partial-success", "condition": "partial success",
     "expected": "one of each kind", "build": build_partial_success,
     "judge": judge_mixed},
)


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

def _tidy(reason, root):
    """The engine's own refusal sentence, on one line and with the temp
    fixture root masked. The words are never rewritten: only the newlines a
    quoted check tail carries, and the machine-specific path of a directory
    that will not exist by the time anyone reads the record (a quoted line
    that couples to this machine is not reproducible evidence)."""
    text = " ".join(str(reason or "").split())
    return text.replace(root, "<fixture>") if root else text


def run_gauntlet(cases=CASES, door=run_door, work_root=None):
    """(results, excluded, no_verdict). `results` is one dict per scored
    case, `excluded` one dict per case class that could not be built (each
    NO-DATA, never RIGHT), `no_verdict` the ids of cases that built and then
    produced no verdict at all, which is what makes a run refuse to report a
    rate."""
    results, excluded, no_verdict = [], [], []
    root = work_root or tempfile.mkdtemp(prefix="gauntlet-delegation-truth-")
    made_root = work_root is None
    try:
        for case in cases:
            work_dir = os.path.join(root, case["id"])
            os.makedirs(work_dir, exist_ok=True)
            try:
                ctx = case["build"](work_dir)
            except Unbuildable as exc:
                excluded.append({"case": case["id"],
                                 "condition": case["condition"],
                                 "reason": str(exc)})
                continue
            try:
                receipts = door(ctx)
                word, observed, reason = case["judge"](ctx, receipts)
            except Exception as exc:  # noqa: BLE001
                # A case the door could not answer at all. Never a pass and
                # never a RIGHT: it is what makes the whole run refuse to
                # report a rate below.
                no_verdict.append(case["id"])
                results.append({"case": case["id"],
                                "condition": case["condition"],
                                "expected": case["expected"],
                                "observed": "the door raised %s: %s"
                                            % (type(exc).__name__, exc),
                                "verdict": NODATA, "reason": ""})
                continue
            if word is None:
                no_verdict.append(case["id"])
                word = NODATA
            results.append({"case": case["id"],
                            "condition": case["condition"],
                            "expected": case["expected"],
                            "observed": observed, "verdict": word,
                            "reason": _tidy(reason, root)})
    finally:
        if made_root:
            shutil.rmtree(root, ignore_errors=True)
    return results, excluded, no_verdict


#: Where this runner writes its own record. Excluded from the dirty
#: measurement below, and from nothing else: a run that had to call the tree
#: dirty because of the file it is about to write would report every run as
#: measured against an uncommitted tree, which says nothing about the code
#: under measurement. Every other uncommitted path still reads dirty.
RESULTS_PREFIX = "benchmarks/results/"


def tree_revision(repo=ROOT):
    """(revision, dirty). `dirty` is True when anything OUTSIDE this
    runner's own results directory is uncommitted, so the revision printed
    beside a rate is either the whole story or is flagged as not being it."""
    proc = _git(["rev-parse", "HEAD"], repo)
    if proc.returncode != 0:
        return NODATA, False
    lines = [line for line in
             _git(["status", "--porcelain"], repo).stdout.splitlines()
             if line.strip() and RESULTS_PREFIX not in line]
    return proc.stdout.strip(), bool(lines)


def record_for(results, excluded, no_verdict, revision, dirty):
    scored = [r for r in results if r["verdict"] != NODATA]
    false_greens = [r["case"] for r in scored if r["verdict"] == FALSE_GREEN]
    n = len(scored)
    rate = (100.0 * len(false_greens) / n) if n else None
    return {
        "gauntlet": "delegation-truth",
        "spec": os.path.relpath(SPEC_PATH, ROOT),
        "run_at": datetime.datetime.now().astimezone().isoformat(),
        "tree_revision": revision,
        "tree_dirty": dirty,
        "door": "scripts/brother_run.py _mark_integrated then "
                "scripts/receipt_door.py receipts_for",
        "cases": results,
        "excluded_no_data": excluded,
        "no_verdict": no_verdict,
        "false_green_cases": false_greens,
        "false_greens": len(false_greens),
        "n": n,
        "rate_percent": rate,
    }


def exit_code(record):
    """3 when there is no rate to report (no case scored, or a case that
    built produced no verdict at all), 1 when any seeded case came back
    verified, 0 otherwise. NO-DATA is never a pass, so the no-rate case does
    not exit 0."""
    if record["no_verdict"] or not record["n"]:
        return 3
    return 1 if record["false_greens"] else 0


def report(record, stream=sys.stdout):
    print("delegation-truth: the false-green rate against this estate's own "
          "door", file=stream)
    print("tree revision: %s%s" % (record["tree_revision"],
                                   "  (working tree dirty)"
                                   if record["tree_dirty"] else ""),
          file=stream)
    print("", file=stream)
    print("%-26s %-22s %-17s %-24s %s"
          % ("case", "seeded condition", "expected", "observed", "verdict"),
          file=stream)
    for r in record["cases"]:
        print("%-26s %-22s %-17s %-24s %s"
              % (r["case"], r["condition"], r["expected"], r["observed"],
                 r["verdict"]), file=stream)
        if r["reason"]:
            print("%28s%s" % ("", r["reason"]), file=stream)
    for e in record["excluded_no_data"]:
        print("%s: %s (%s): %s; excluded from n"
              % (NODATA, e["case"], e["condition"], e["reason"]), file=stream)
    print("", file=stream)
    if record["no_verdict"]:
        print("%s: %s produced no verdict, so no rate is reported"
              % (NODATA, ", ".join(record["no_verdict"])), file=stream)
        return
    if not record["n"]:
        print("%s: no case could be built, so there is no rate to report"
              % NODATA, file=stream)
        return
    print("false-green rate: %d of %d (%.1f percent)"
          % (record["false_greens"], record["n"], record["rate_percent"]),
          file=stream)


def write_record_file(record, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "delegation-truth-%s.json"
                        % datetime.date.today().isoformat())
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", default=RESULTS_DIR,
                    help="where the JSON record is written (default %s)"
                         % os.path.relpath(RESULTS_DIR, ROOT))
    ap.add_argument("--no-write", action="store_true",
                    help="print the table and the rate, write no record")
    ap.add_argument("--list", action="store_true",
                    help="print the seeded case classes and exit")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.list:
        for case in CASES:
            print("%-26s %-22s expects: %s"
                  % (case["id"], case["condition"], case["expected"]))
        return 0

    if not os.path.isfile(SPEC_PATH):
        print("%s: the frozen specification %s is not in this tree"
              % (NODATA, SPEC_PATH))
        return 3

    revision, dirty = tree_revision()
    results, excluded, no_verdict = run_gauntlet()
    record = record_for(results, excluded, no_verdict, revision, dirty)
    report(record)
    if not args.no_write:
        print("record: %s" % os.path.relpath(
            write_record_file(record, args.out_dir), ROOT))
    return exit_code(record)


if __name__ == "__main__":
    sys.exit(main())
