#!/usr/bin/env python3
"""RECON done check: every change since v1.0.21 has a disposition, and every INTEGRATE item is in the run line.

Reads docs/plan/RECONCILIATION-1.1.0.json. FAIL (exit 1) while any item carries no disposition from the allowed
set, or any INTEGRATE item's own `check` command (the one that proves its integration) exits non zero. NO-DATA
(exit 2) when the file is missing, unreadable, not the expected shape, or an INTEGRATE item has no check to run,
or the run line ref the checks name is absent from this repository: an unreadable inventory is never a pass.
PASS (exit 0) only when every item is disposed and every INTEGRATE check exits 0.

usage: donecheck_recon.py [--file docs/plan/RECONCILIATION-1.1.0.json] [--timeout 120]
"""
import argparse, json, os, subprocess, sys

DISPOSITIONS = ("INTEGRATE", "SUPERSEDED", "DUPLICATE", "DEFERRED", "DROP-SAFE")
RUN_LINE = "hub/loop/run-2026-09-30"


def load(path):
    """The inventory's items, or (None, reason) when it cannot be read as the expected shape."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, "cannot read %s: %s" % (path, exc)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        return None, "%s has no items list" % path
    if not all(isinstance(i, dict) and i.get("id") for i in items):
        return None, "%s holds an item with no id" % path
    return items, ""


def run_check(cmd, cwd, timeout):
    """exit code of the item's check, or None when it could not run to an answer."""
    try:
        p = subprocess.run(["sh", "-c", cmd], cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return p.returncode


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=os.path.join("docs", "plan", "RECONCILIATION-1.1.0.json"))
    ap.add_argument("--timeout", type=int, default=120)
    a = ap.parse_args(argv)
    items, why = load(a.file)
    if items is None:
        print("NO-DATA: " + why); return 2
    undisposed = [i["id"] for i in items if i.get("disposition") not in DISPOSITIONS]
    integrate = [i for i in items if i.get("disposition") == "INTEGRATE"]
    missing_check = [i["id"] for i in integrate if not isinstance(i.get("check"), str) or not i["check"].strip()]
    if missing_check:
        print("NO-DATA: %d INTEGRATE item(s) carry no check command: %s" % (len(missing_check), ", ".join(missing_check[:10]))); return 2
    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if integrate and any(RUN_LINE in i["check"] for i in integrate):
        if subprocess.run(["git", "-C", cwd, "rev-parse", "--verify", "--quiet", RUN_LINE + "^{commit}"], capture_output=True).returncode != 0:
            print("NO-DATA: %s is not a ref in this repository; the INTEGRATE checks cannot run" % RUN_LINE); return 2
    not_in, unrun = [], []
    for i in integrate:
        rc = run_check(i["check"], cwd, a.timeout)
        if rc is None:
            unrun.append(i["id"])
        elif rc != 0:
            not_in.append(i["id"])
    print("items %d, undisposed %d, INTEGRATE %d, not in run line %d, checks unrun %d" % (len(items), len(undisposed), len(integrate), len(not_in), len(unrun)))
    if unrun:
        print("NO-DATA: check could not run for: " + ", ".join(unrun[:10])); return 2
    if undisposed or not_in:
        if undisposed:
            print("FAIL: no disposition: " + ", ".join(undisposed[:10]))
        if not_in:
            print("FAIL: INTEGRATE not contained in %s: %s" % (RUN_LINE, ", ".join(not_in[:20])))
        return 1
    print("PASS: every item disposed, every INTEGRATE item contained in " + RUN_LINE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
