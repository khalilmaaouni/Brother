"""The worker for the JBEQ-MDM E2E-001 run, on the MODEL_WORKER_CMD seam.

Again not a model: the engineer wrote the files this delivers, and this
script is the hand that puts each one where the unit declared it. Two kinds
of unit exist in this plan and this handles both.

  A SOURCE UNIT declares a directory or a file that exists under
  JBEQ_AUTHORED. The authored bytes are copied into the lane, unchanged.

  A RUN UNIT declares an out/ artefact that has to be COMPUTED. Those are
  produced by really executing the migration and the reconciliation in the
  lane, so the artefacts in the receipt are the output of code that ran, not
  a file someone typed.

It writes only inside the declared scope, which is what the engine's own
scope audit checks afterward.
"""
import os
import re
import shutil
import subprocess
import sys

AUTHORED = os.environ["JBEQ_AUTHORED"]

# Which command produces which computed artefact. Each is run at most once
# per unit, however many of its outputs the unit declared.
MIGRATION = [sys.executable, "src/mdm_transform.py", "--run",
             "--inject-contradiction", "--data", "data", "--out", "out"]
RECONCILE = [sys.executable, "recon/reconcile.py", "--data", "data",
             "--out", "out"]


def declared_paths(prompt):
    match = re.search(r"Declared write scope: ([^\n]+)", prompt)
    if not match:
        return []
    return [p.strip() for p in match.group(1).split(",") if p.strip()]


def copy_authored(path):
    source = os.path.join(AUTHORED, path)
    if os.path.isdir(source):
        shutil.copytree(source, path, dirs_exist_ok=True)
        return True
    if os.path.isfile(source):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        shutil.copy(source, path)
        return True
    return False


def main():
    prompt = sys.argv[-1] if len(sys.argv) > 1 else ""
    paths = declared_paths(prompt)
    if not paths:
        print("worker: nothing declared, nothing written")
        return 0

    ran = set()
    for path in paths:
        if copy_authored(path):
            print("worker: wrote %s from the authored tree" % path)
            continue
        if path.startswith("out/") and path != "out/reconciliation.json":
            if "migration" not in ran:
                subprocess.run(MIGRATION, check=True)
                ran.add("migration")
            print("worker: %s produced by the migration" % path)
            continue
        if path == "out/reconciliation.json":
            if "reconcile" not in ran:
                subprocess.run(RECONCILE, check=True)
                ran.add("reconcile")
            print("worker: %s produced by the reconciliation" % path)
            continue
        print("worker: NO-DATA, nothing authored or computed for %s" % path)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
