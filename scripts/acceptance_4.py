#!/usr/bin/env python3
"""Acceptance test for capability area 4: monorepos and generated code
(G1-M3.7 of docs/plan/READINESS-ROADMAP-2026-08-29.json, node G1-M3,
following the template G1-M3.3 left behind).

Area 4's own definition (docs/plan/CAPABILITY-AREAS.json): a contributor
works inside a large monorepo with generated files present, asking for a
change scoped to hand-written source only. It fails when the tool edits
generated files, loses track of package boundaries, or times out scanning
the whole tree for a scoped change.

THE REAL MACHINERY UNDER TEST is this estate's own scope gate: a unit
declares its write scope (owns), scripts/loop_bridge.py turns that into
write_scope on dispatch (run_node, "write_scope": node.get("owns") or []),
and scripts/scope_audit.py compares what the worker actually changed, via
git, against that declaration after the fact. loop_bridge.run_node already
wires this end to end and records the verdict as record["scope"] and
record["integrable"] (see loop_bridge.py's own comment: "A unit that wrote
outside its declared scope is not integrable, whatever its own check
says"). That is the mechanism a package boundary and a protected generated
file both reduce to here: a declared prefix, and a real diff checked
against it. This test drives loop_bridge.run_node directly (not only
through its CLI, which prints no machine-readable scope verdict), the same
real spine areas 1 and 2 drive.

REAL REPOSITORY, NOT A FIXTURE: a git repository in a temp directory shaped
like a small monorepo: several package directories, a generated/ directory
per package that must never be touched, and enough packages that the scope
audit is genuinely scanning a tree wider than the one change, not a
single-file toy.

Exit contract, matching the estate's other acceptance scripts:
  0  PASS      a change scoped to one package's hand-written source landed,
               every generated file and every other package's files are
               byte-for-byte unchanged, and the scope gate reports CLEAN
  1  FAIL      a generated file or another package's file changed anyway,
               the scope gate did not catch it, or the scoped change never
               landed
  2  NO-DATA   the scope gate's own machinery (loop_bridge/scope_audit) is
               not present in this checkout

Usage: python3 scripts/acceptance_4.py [--explain] [--calibrate]
--calibrate forces this test red by using a worker that also edits the
protected generated file while still declaring the narrow scope, the
mechanical shape of "the tool edits generated files": the declaration was
honest, the tool was not. Passes only if this test correctly reads the
scope gate's QUARANTINE verdict as a failure to integrate.

PRODUCER: this module is the sole producer of the files it writes. The
_write() helper (lines 92-94) is used by build_monorepo() (lines 119-131)
to seed the hand-written and generated fixture files across the sibling
packages, and by build_worker() (line 154) to write the scripted worker
that performs the scoped edit (and, under --calibrate, touches the
protected generated file too). All of these live inside the
tempfile.TemporaryDirectory opened in _run() (line 183) and are deleted
when that with-block exits; nothing else in this repo writes through this
module's helper.
"""
import argparse
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))

TIME_BUDGET_SECONDS = 30.0

#: A handful of sibling packages plus one generated file per package, wide
#: enough that the scope audit is reading a real multi-package diff rather
#: than a single file, without making the test itself slow.
SIBLING_PACKAGE_COUNT = 8

TEMPLATE = """area 4 template addition to G1-M3.3's shape:
  - a monorepo is modelled as several package directories in one real repo,
    each with its own generated/ subdirectory the test seeds and never
    expects touched
  - "package boundaries" and "generated code" both reduce to the same real
    control this estate already has: a declared write scope (owns), audited
    after the fact by scope_audit.py against git's own diff, wired end to
    end by loop_bridge.run_node
  - the declaration is checked as HONEST separately from whether the TOOL
    respected it: --calibrate keeps the scope declaration narrow and
    correct, and instead makes the worker (the tool) misbehave, because a
    contributor's own scope declaration is not the thing under test here
  - both directions are checked: the scoped file changed, and every
    sibling package plus every generated file did not, against their real
    pre-edit bytes
What areas 1 through 3's shape got wrong that this corrects: nothing did.
What this area adds for the next ones: when the estate's real answer to a
capability is an after-the-fact audit rather than prevention, PASS means
the audit caught (or never needed to catch) a violation, not that a
violation was made structurally impossible."""


def sh(args, cwd=None, timeout=60):
    import subprocess
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout)


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def build_monorepo(tmp):
    """A real git repository shaped like a small monorepo. Returns
    (repo, snapshot) where snapshot maps every seeded path (relative to
    repo) to its original text, for the after-the-fact byte comparison."""
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "a@b.c"],
                 ["config", "user.name", "acceptance-test"]):
        sh(["git"] + args, repo)

    snapshot = {}
    # The package under test: hand-written source plus a generated file.
    os.makedirs(os.path.join(repo, "packages", "app", "src"))
    os.makedirs(os.path.join(repo, "packages", "app", "generated"))
    rel_hand = "packages/app/src/hand.py"
    rel_gen = "packages/app/generated/gen.py"
    _write(os.path.join(repo, rel_hand), "def existing():\n    return 0\n")
    _write(os.path.join(repo, rel_gen),
          "# GENERATED - do not edit by hand\nVALUE = 1\n")
    snapshot[rel_hand] = _read(os.path.join(repo, rel_hand))
    snapshot[rel_gen] = _read(os.path.join(repo, rel_gen))

    # Sibling packages: a wider tree the scoped change must leave alone.
    for i in range(SIBLING_PACKAGE_COUNT):
        pkg = "packages/sibling%d" % i
        os.makedirs(os.path.join(repo, pkg, "src"))
        os.makedirs(os.path.join(repo, pkg, "generated"))
        for rel in (pkg + "/src/code.py", pkg + "/generated/gen.py"):
            _write(os.path.join(repo, rel), "# sibling %d: %s\n" % (i, rel))
            snapshot[rel] = _read(os.path.join(repo, rel))

    sh(["git", "add", "-A"], repo)
    sh(["git", "commit", "-q", "-m", "seed monorepo"], repo)
    return repo, snapshot


def build_worker(tmp, touch_generated_too):
    """The scripted worker: appends a helper to the hand-written file. When
    touch_generated_too is set (--calibrate only) it ALSO edits the
    protected generated file, the mechanical shape of a tool that edits
    generated files despite a narrow, honest scope declaration."""
    worker = os.path.join(tmp, "worker.sh")
    lines = [
        "#!/bin/sh",
        "cat >/dev/null",
        "printf '\\ndef helper():\\n    return 1\\n' >> packages/app/src/hand.py",
    ]
    if touch_generated_too:
        lines.append(
            "printf '\\n# tampered\\n' >> packages/app/generated/gen.py")
    lines += ["git add -A", "git commit -qm 'scoped change'"]
    _write(worker, "\n".join(lines) + "\n")
    os.chmod(worker, 0o755)
    return worker


def _unchanged(repo, snapshot, skip):
    """Every seeded path except `skip` still reads its original text."""
    bad = []
    for rel, original in snapshot.items():
        if rel == skip:
            continue
        path = os.path.join(repo, rel)
        if not os.path.isfile(path) or _read(path) != original:
            bad.append(rel)
    return bad


def _run(explain, calibrate):
    sys.path.insert(0, HERE)
    try:
        import loop_bridge
        import scope_audit
    except ImportError as exc:
        return 2, "NO-DATA: could not import the scope gate: %s" % exc
    if scope_audit is None or getattr(loop_bridge, "scope_audit", None) is None:
        return 2, ("NO-DATA: loop_bridge could not load scope_audit in this "
                   "checkout, so the scope gate is not present")

    prefix = "acceptance-4-calibrate-" if calibrate else "acceptance-4-"
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        repo, snapshot = build_monorepo(tmp)
        worker_script = build_worker(tmp, touch_generated_too=calibrate)
        parts, problem = loop_bridge.load_parts()
        if parts is None:
            return 2, "NO-DATA: %s" % problem
        worker = loop_bridge.LaneWorker(parts["spawn"], ["sh", worker_script])

        node = {"id": "U1", "name": "add a helper to hand-written source only",
                "done_check": "test -f packages/app/src/hand.py && "
                              "grep -q helper packages/app/src/hand.py",
                "owns": ["packages/app/src"]}

        start = time.monotonic()
        record = loop_bridge.run_node(node, parts, worker, cwd=repo, max_attempts=1)
        elapsed = time.monotonic() - start

        if explain:
            print(TEMPLATE)

        scope = record.get("scope") or {}
        verdict = scope.get("verdict")
        rel_hand = "packages/app/src/hand.py"

        if calibrate:
            if verdict == scope_audit.QUARANTINE and not record.get("integrable", True):
                return 0, ("PASS: calibration used a worker that edited the "
                           "protected generated file under a narrow, honest "
                           "scope declaration, and the scope gate correctly "
                           "quarantined it (verdict=%s, integrable=%s): %s"
                           % (verdict, record.get("integrable"),
                              record.get("integration_block", "")[:160]))
            return 1, ("FAIL: calibration could not force the scope gate to "
                       "quarantine a worker that edited a protected generated "
                       "file (verdict=%s, integrable=%s): a green reading of "
                       "this test would be decoration"
                       % (verdict, record.get("integrable")))

        landed = os.path.isfile(os.path.join(repo, rel_hand)) and \
            "helper" in _read(os.path.join(repo, rel_hand))
        untouched_violations = _unchanged(repo, snapshot, skip=rel_hand)

        if not landed:
            return 1, "FAIL: the scoped change to %s never landed" % rel_hand
        if untouched_violations:
            return 1, ("FAIL: %d path(s) outside the declared scope changed: %s"
                       % (len(untouched_violations), ", ".join(untouched_violations[:6])))
        if verdict != scope_audit.CLEAN:
            return 1, ("FAIL: the change stayed inside scope but the scope "
                       "gate reported %s, not CLEAN: %s"
                       % (verdict, scope.get("reason", "")))
        if not record.get("integrable", False):
            return 1, "FAIL: the scope gate read CLEAN but marked the unit not integrable"
        if elapsed > TIME_BUDGET_SECONDS:
            return 1, ("FAIL: the scoped change landed correctly but scanning "
                       "%d sibling package(s) took %.2fs, over the %.0fs budget"
                       % (SIBLING_PACKAGE_COUNT, elapsed, TIME_BUDGET_SECONDS))
        return 0, ("PASS: a change scoped to %s landed in %.2fs (budget %.0fs) "
                   "across a %d-package monorepo, every generated file and "
                   "every sibling package stayed byte-identical, and the "
                   "scope gate reported CLEAN"
                   % (rel_hand, elapsed, TIME_BUDGET_SECONDS, SIBLING_PACKAGE_COUNT))


def run(explain=False):
    return _run(explain, calibrate=False)


def calibrate():
    """G1-M3.7.2: force this test red once. The worker edits the protected
    generated file even though the unit's own scope declaration stayed
    narrow and honest, and this test passes its own calibration only if it
    correctly reads the scope gate's QUARANTINE verdict as a failure."""
    code, evidence = _run(explain=False, calibrate=True)
    if code == 0:
        return 0, evidence
    if code == 2:
        return 1, ("FAIL: calibration could not run at all (%s), so nothing "
                   "was proven about this test's ability to fail" % evidence)
    return 1, ("FAIL: calibration could not force this test red (got %s): a "
               "green reading of this test would be decoration" % evidence)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Acceptance test for capability area 4: monorepos and "
                    "generated code.")
    parser.add_argument("--explain", action="store_true",
                        help="also print the template this area leaves behind")
    parser.add_argument("--calibrate", action="store_true",
                        help="prove this test can fail, instead of running it")
    args = parser.parse_args(argv)
    if args.calibrate:
        code, evidence = calibrate()
    else:
        code, evidence = run(explain=args.explain)
    print(evidence)
    return code


if __name__ == "__main__":
    sys.exit(main())
