#!/usr/bin/env python3
"""mutation_gate: R27.3, the assurance mutation gate.

WHY THIS EXISTS (docs/plan/HARDENING-2026-08-30-CODEX.md, mechanism 5, the
third and last mechanism the Codex consultation's ruling adopted). fault_lab.py
(R27.1) and negative_space_audit.py (R27.2) both close the "nobody ever asked
this question" family of miss. This closes a different one: a check EXISTS,
but does it actually notice when the code it guards breaks? A suite that
passes on healthy code and would ALSO pass on broken code is not evidence of
anything. The only way to know a killer test really kills is to break the
code on purpose and watch it die.

THE FOUR BOUNDED MUTANT CLASSES, the consultation's own list, each seeded at
exactly one NAMED, STABLE seam in a real product module (scripts/ is the
canonical source; bundle/runtime is its generated, byte-identical mirror per
test_bundle_runtime.py, so mutating scripts/ alone is mutating the product):

  termination-condition comparison flip
      scripts/claim_store.py, reconcile(): the filter deciding which claims
      even get looked at ("state != 'claimed': continue"). Flipping the
      comparison inverts which claims reconcile() reports on: every actual
      in-flight or abandoned claim gets skipped, and every already-released
      one gets reported instead. This is the same shape as the recorded
      dead-owner family (the crash-recovery seam looking at the wrong set of
      claims), one row over from the boundary mutant below. Killer:
      test_claim_store.py (test_a_live_claim_reads_in_flight,
      test_an_expired_claim_reads_abandoned_and_names_its_owner and
      test_a_released_unit_is_not_reported_as_outstanding all depend on
      reconcile() looking at exactly the claimed entries).

  tuple or dict field deletion
      scripts/work_record.py, create(): the "depends_on" field in the row
      dict the scheduler reads. Guards the forgotten-contract-field class
      negative_space_audit.py (R27.2) was built for: a durable record
      silently missing a field nothing then complains about until a
      dependent unit runs early. Killer: test_work_record.py
      (test_it_is_written_in_the_shape_the_scheduler_already_reads asserts
      every key the scheduler contract needs is present on the row).

  boundary check removal
      scripts/claim_store.py, live(): the lease-expiry boundary
      ("expires_at <= now" means dead). Guards the lease-boundary family:
      remove the boundary and an expired claim reads live forever, which is
      the same shape of miss as the recorded dead-owner-wait defect (a claim
      that should read dead does not). Killer: test_claim_store.py
      (test_an_expired_lease_may_be_taken_by_somebody_else and the dead-pid
      tests depend on this exact comparison).

  parse-failure-to-continue
      scripts/door.py, main(): the decomposer's JSON reply fails to parse.
      The correct behaviour records WHY (last_problems, printed) before
      moving to the next attempt; the mutant silently continues with no
      diagnostic at all, so a run that never once got valid JSON reports the
      placeholder "the decomposer was never asked" instead of the real
      cause. Killer: test_door_adversarial.py
      (test_json_that_never_parses_is_refused_after_bounded_attempts asserts
      the literal diagnostic string appears in the process output).

MECHANICS. Never the working tree: scripts/ is copied whole into a fresh
tempfile.mkdtemp() scratch directory (sibling modules the target or its
killer import must resolve identically to the real product, which is why the
whole directory is copied rather than one file in isolation), exactly one
named module in that COPY is patched by exact, unique text replacement
against an anchor read fresh from this repository's own current source
(never a hard coded line number, which drifts), and the named killer test
file is run as a real subprocess from inside the scratch copy so its own
`sys.path.insert(0, HERE)` resolves to the mutant, not the real module. A
missing anchor (the seam moved or was refactored away) is NO-DATA by name,
never a silent skip and never a guess at a new line number.

VERDICT: a mutant is KILLED when its named killer exits non-zero. Any mutant
that SURVIVES (killer still exits 0 against the broken code) fails the gate,
naming the surviving mutation, exit 1. A missing target module or killer test
(in the real tree, checked before any scratch copy is made) is NO-DATA,
exit 2, never a pass. Exit 0 only when every mutant is KILLED.

--list prints the four classes and what each guards, no scratch copy made.
--reintroduce ID drives one mutant on its own and prints its full output,
for hand inspection. --write-ledger writes MUTANTS' shape and the last run's
verdicts to docs/plan/mutation-gate-ledger.json as committed evidence of the
run shape (never required to match the working tree; a plain run does not
rewrite it, so the registered check_all.sh entry stays silent on disk).

Python 3, standard library only. No network. No em or en dashes.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NODATA = "NO-DATA"
LEDGER_PATH = os.path.join(ROOT, "docs", "plan", "mutation-gate-ledger.json")


def _patch_unique(text, old, new):
    """(patched_text, problem). problem is "" on success. Refuses a zero or
    a non-unique anchor rather than guessing which occurrence was meant; a
    seam that moved or was duplicated by a later edit must read NO-DATA, not
    silently patch the wrong spot."""
    hits = text.count(old)
    if hits == 0:
        return None, "anchor text not found (the seam moved or was refactored)"
    if hits > 1:
        return None, "anchor text is not unique (%d occurrences)" % hits
    return text.replace(old, new, 1), ""


# ---------------------------------------------------------------------------
# THE FOUR MUTANTS. Each anchor is copied verbatim from this repository's own
# current scripts/ source (never invented, never a line number). See the
# module docstring above for what each guards and why.
# ---------------------------------------------------------------------------
MUTANTS = {
    "termination-flip": {
        "class": "termination-condition comparison flip",
        "guards": "reconcile()'s own filter for which claims it even looks "
                  "at: a flipped comparison reports the wrong claims as "
                  "in-flight or abandoned and skips the real ones entirely",
        "target": "claim_store.py",
        "anchor_old": '        if state != "claimed":\n',
        "anchor_new": '        if state == "claimed":\n',
        "killer": "test_claim_store.py",
    },
    "field-deletion": {
        "class": "tuple or dict field deletion",
        "guards": "the forgotten-contract-field family: a durable record "
                  "silently missing a field its consumer needs",
        "target": "work_record.py",
        "anchor_old": '                  "depends_on": [str(d) for d in '
                      '(u.get("depends_on") or [])],\n',
        "anchor_new": "",
        "killer": "test_work_record.py",
    },
    "boundary-removal": {
        "class": "boundary check removal",
        "guards": "the lease-boundary family: an expired claim that never "
                  "reads dead because the expiry comparison itself is gone",
        "target": "claim_store.py",
        "anchor_old": "    if expires <= now:\n"
                      '        return "the lease expired %.0fs ago" '
                      "% (now - expires)\n",
        "anchor_new": "",
        "killer": "test_claim_store.py",
    },
    "parse-failure-continue": {
        "class": "parse-failure-to-continue",
        "guards": "a decomposer reply that never parses reporting the "
                  "placeholder \"never asked\" instead of the real, "
                  "recorded diagnostic",
        "target": "door.py",
        "anchor_old": '        except ValueError as exc:\n            # THE EXIT CODE IS THE FIRST FACT, and until 2026-09-05 it was\n            # thrown away: a decomposer that failed and wrote nothing was\n            # reported as though it had answered badly, which is what made\n            # the Codex finding take four probes to diagnose instead of one.\n            detail = "the decomposer\'s answer could not be read as JSON: %s" % exc\n            if proc.returncode != 0:\n                detail += (" (the decomposer command %r exited %d; %s)"\n                           % (cmd[0], proc.returncode, stderr_tail(proc.stderr)))\n                if is_codex_cmd(cmd) or MW.model_client() == brother_paths.CODEX:\n                    detail += ". %s" % CODEX_SANDBOX_HINT\n            last_problems = [detail]\n            refusal = refusal_text(last_problems)\n            print(refusal, file=sys.stderr)\n            continue\n',
        "anchor_new": "        except ValueError as exc:\n"
                      "            continue\n",
        "killer": "test_door_adversarial.py",
    },
}


def _copy_scripts_scratch():
    scratch = tempfile.mkdtemp(prefix="mutation-gate-")
    dst = os.path.join(scratch, "scripts")
    shutil.copytree(HERE, dst,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return scratch, dst


def apply_mutation(scratch_scripts, entry):
    """(ok, problem). Patches entry["target"] inside the scratch copy only."""
    path = os.path.join(scratch_scripts, entry["target"])
    if not os.path.isfile(path):
        return False, "%s: missing from the scratch copy" % entry["target"]
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    patched, problem = _patch_unique(text, entry["anchor_old"], entry["anchor_new"])
    if patched is None:
        return False, "%s: %s" % (entry["target"], problem)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(patched)
    return True, ""


def run_killer(scratch_scripts, killer, timeout=180):
    path = os.path.join(scratch_scripts, killer)
    if not os.path.isfile(path):
        return None, "%s: killer test missing from the scratch copy" % killer
    try:
        proc = subprocess.run([sys.executable, path], cwd=scratch_scripts,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:  # sbe: allow-silent documented (None, reason) sentinel; run_mutant below turns it into a NO-DATA verdict carrying this same detail
        return None, "%s: timed out after %ss" % (killer, timeout)
    return proc, ""


def run_mutant(mutant_id, entry):
    """One mutant, start to finish, against its own fresh scratch copy.
    Returns {"id", "verdict", "detail", "output"}; verdict is one of
    KILLED, SURVIVED, NO-DATA."""
    scratch = None
    try:
        scratch, scratch_scripts = _copy_scripts_scratch()
        ok, problem = apply_mutation(scratch_scripts, entry)
        if not ok:
            return {"id": mutant_id, "verdict": NODATA, "detail": problem,
                    "output": ""}
        proc, problem = run_killer(scratch_scripts, entry["killer"])
        if proc is None:
            return {"id": mutant_id, "verdict": NODATA, "detail": problem,
                    "output": ""}
        out = (proc.stdout or "") + (proc.stderr or "")
        verdict = "SURVIVED" if proc.returncode == 0 else "KILLED"
        detail = "%s exit=%d" % (entry["killer"], proc.returncode)
        return {"id": mutant_id, "verdict": verdict, "detail": detail,
                "output": out}
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)


def run_battery(mutants, out=sys.stdout):
    """Runs every mutant in `mutants` (a dict shaped like MUTANTS), printing
    to `out` as it goes. Returns (results, exit_code): exit_code is 1 if any
    mutant SURVIVED (named), else 2 if any read NO-DATA (named), else 0."""
    results = []
    for mutant_id in sorted(mutants):
        entry = mutants[mutant_id]
        if not os.path.isfile(os.path.join(HERE, entry["target"])):
            r = {"id": mutant_id, "verdict": NODATA,
                "detail": "%s: not present in the real tree" % entry["target"],
                "output": ""}
        elif not os.path.isfile(os.path.join(HERE, entry["killer"])):
            r = {"id": mutant_id, "verdict": NODATA,
                "detail": "%s: not present in the real tree" % entry["killer"],
                "output": ""}
        else:
            r = run_mutant(mutant_id, entry)
        results.append(r)
        print("%-9s %-24s %s (%s)"
             % (r["verdict"], mutant_id, entry["class"], r["detail"]), file=out)
        if r["verdict"] != "KILLED" and r["output"]:
            print(r["output"][-1500:], file=out)

    survived = [r["id"] for r in results if r["verdict"] == "SURVIVED"]
    nodata = [r for r in results if r["verdict"] == NODATA]
    print(file=out)
    print("%d mutant(s): %d killed, %d survived, %d no-data"
         % (len(results),
            sum(1 for r in results if r["verdict"] == "KILLED"),
            len(survived), len(nodata)), file=out)

    if survived:
        for mid in survived:
            print("SURVIVED: %s (an unkilled mutant)" % mid, file=out)
        return results, 1
    if nodata:
        for r in nodata:
            print("%s: %s: %s" % (NODATA, r["id"], r["detail"]), file=out)
        return results, 2
    return results, 0


def _write_ledger(results):
    rows = []
    for r in results:
        entry = MUTANTS.get(r["id"], {})
        rows.append({"id": r["id"], "class": entry.get("class", ""),
                     "guards": entry.get("guards", ""),
                     "target": entry.get("target", ""),
                     "killer": entry.get("killer", ""),
                     "verdict": r["verdict"], "detail": r["detail"]})
    doc = {"generated_by": "scripts/mutation_gate.py --write-ledger",
          "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
          "mutants": rows}
    os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)
    with open(LEDGER_PATH, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return LEDGER_PATH


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true",
                    help="print the four mutant classes and what each "
                         "guards, no scratch copy made")
    ap.add_argument("--reintroduce", choices=sorted(MUTANTS),
                    help="drive one named mutant on its own and print its "
                         "full killer output")
    ap.add_argument("--write-ledger", action="store_true",
                    help="write this run's verdicts to %s as committed "
                         "evidence (never required to match the working "
                         "tree; a plain run does not do this)" % LEDGER_PATH)
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.list:
        for mutant_id in sorted(MUTANTS):
            entry = MUTANTS[mutant_id]
            print("%-24s %-40s guards: %s"
                 % (mutant_id, entry["class"], entry["guards"]))
        return 0

    if not os.path.isdir(HERE):
        print("%s: %s does not exist, nothing to mutate" % (NODATA, HERE))
        return 2

    if args.reintroduce:
        entry = MUTANTS[args.reintroduce]
        if not os.path.isfile(os.path.join(HERE, entry["target"])):
            print("%s: %s: not present in the real tree"
                 % (NODATA, entry["target"]))
            return 2
        if not os.path.isfile(os.path.join(HERE, entry["killer"])):
            print("%s: %s: not present in the real tree"
                 % (NODATA, entry["killer"]))
            return 2
        r = run_mutant(args.reintroduce, entry)
        print("%-9s %-24s %s (%s)"
             % (r["verdict"], args.reintroduce, entry["class"], r["detail"]))
        print(r["output"])
        return {"KILLED": 0, "SURVIVED": 1, NODATA: 2}[r["verdict"]]

    results, code = run_battery(MUTANTS)
    if args.write_ledger and code in (0, 1):
        path = _write_ledger(results)
        print("ledger written: %s" % path)
    return code


if __name__ == "__main__":
    sys.exit(main())
