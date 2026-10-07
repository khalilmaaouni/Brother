#!/usr/bin/env python3
"""L5a acceptance threshold 10: apply one named mutation (T1 to T9 in the L5a spec, section 10) to a
DISPOSABLE copy of the tree at HEAD, run its named test there, and quote the red output.

usage (repo root): python3 scripts/l5a_mutation_probe.py docs/architecture/l5a-mutations/T1.json
Prints deterministic lines (no paths, no timings) so the L5a-6 run of record can carry them as an
evidence excerpt and the done check can reproduce them. The named test runs UNMUTATED first and must
pass (else NO-DATA naming why: a test that is already red, missing or erroring proves nothing); then
the mutation is applied and the test must end in an assertion FAIL of that named test. An ERROR line,
a missing test, a find string that no longer occurs exactly once, an artifact outside
docs/architecture/l5a-mutations, or a timed out run is NO-DATA (exit 3), never RED (attack of
2026-09-30: `FAILED (errors=1)` from a nonexistent test name had counted as red). A test the mutation
leaves green is SURVIVED (exit 1). RED is exit 0. The live tree is never written: the copy is
`git archive HEAD` in a temp directory removed at the end.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ARTIFACT_DIR = os.path.join("docs", "architecture", "l5a-mutations")
_TIMEOUT_S = 900


def _no_data(why):
    print("NO-DATA: %s" % why)
    return 3


def _run_test(copy, test_id):
    """(exit code, FAIL lines naming the test, ERROR lines, ran, verdict) of one test run in the copy, or None on timeout."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env.pop("BROTHER_WORKSPACE_ROOT", None)
    try:
        run = subprocess.run([sys.executable, "-B", "-m", "unittest", test_id], cwd=copy,
                             capture_output=True, text=True, env=env, timeout=_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None
    out = run.stdout + run.stderr
    method = test_id.rsplit(".", 1)[-1]
    fails = [l for l in out.splitlines() if re.match(r"^FAIL: %s\b" % re.escape(method), l)]
    errors = [l for l in out.splitlines() if l.startswith("ERROR: ")]
    ran = re.search(r"^Ran (\d+) tests?", out, re.M)
    verdict = re.search(r"^(OK|FAILED \([^)]*\))\s*$", out, re.M)
    return run.returncode, fails, errors, (ran.group(1) if ran else "?"), (verdict.group(1) if verdict else "")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if len(argv) != 1 or not isinstance(argv[0], str):
        return _no_data("usage: l5a_mutation_probe.py <artifact.json>")
    rel = os.path.normpath(argv[0])
    if os.path.isabs(rel) or not rel.startswith(ARTIFACT_DIR + os.sep) or not rel.endswith(".json"):
        return _no_data("the artifact must be a .json under %s" % ARTIFACT_DIR)
    try:
        with open(rel, "rb") as fh:
            art = json.loads(fh.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return _no_data("artifact unreadable: %s" % exc.__class__.__name__)
    if not isinstance(art, dict) or not isinstance(art.get("id"), str) or not isinstance(art.get("test"), str) \
            or art["test"].count(".") < 2 or not art["test"].rsplit(".", 1)[-1].startswith("test") \
            or not isinstance(art.get("edits"), list) or not art["edits"]:
        return _no_data("artifact must carry id, a dotted id naming ONE test method (module.Class.test_x) and a non-empty edits list")
    copy = tempfile.mkdtemp(prefix="l5a-mut-")
    try:
        archive = subprocess.run(["git", "archive", "HEAD"], capture_output=True)
        if archive.returncode != 0:
            return _no_data("git archive HEAD refused")
        untar = subprocess.run(["tar", "-x", "-C", copy], input=archive.stdout, capture_output=True)
        if untar.returncode != 0:
            return _no_data("the copy could not be unpacked")
        base = _run_test(copy, art["test"])
        if base is None:
            return _no_data("the unmutated run timed out")
        code, fails, errors, ran, verdict = base
        if code != 0 or verdict != "OK" or errors or ran in ("?", "0"):
            return _no_data("the named test is not green unmutated (exit %s, %s, %d error line(s), ran %s): it proves nothing"
                            % (code, verdict or "no verdict", len(errors), ran))
        for edit in art["edits"]:
            if not isinstance(edit, dict) or not isinstance(edit.get("file"), str) or os.path.isabs(edit["file"]) \
                    or ".." in edit["file"].split("/"):
                return _no_data("an edit names no file inside the tree")
            target = os.path.join(copy, edit["file"])
            if "add" in edit:
                if os.path.exists(target) or not isinstance(edit["add"], str):
                    return _no_data("add target already exists or carries no text: %s" % edit["file"])
                with open(target, "w", encoding="utf-8") as fh:
                    fh.write(edit["add"])
                continue
            find = edit.get("find")
            if not isinstance(find, str) or not find or not isinstance(edit.get("replace"), str) or find == edit["replace"]:
                return _no_data("an edit must carry a non-empty find and a different replace")
            try:
                with open(target, encoding="utf-8") as fh:
                    src = fh.read()
            except OSError:
                return _no_data("edit target missing: %s" % edit["file"])
            if src.count(find) != 1:
                return _no_data("find occurs %d times in %s, need 1" % (src.count(find), edit["file"]))
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(src.replace(find, edit["replace"], 1))
        mutated = _run_test(copy, art["test"])
        if mutated is None:
            return _no_data("the mutated run timed out")
        code, fails, errors, ran, verdict = mutated
        for line in fails:
            print(line)
        print("Ran %s test(s)" % ran)
        print(verdict or "no unittest verdict line")
        if errors:
            return _no_data("the mutated run ended in %d ERROR line(s), not an assertion failure of %s" % (len(errors), art["test"]))
        if code != 0 and fails and verdict.startswith("FAILED"):
            print("MUTATION %s: RED" % art["id"])
            return 0
        print("MUTATION %s: SURVIVED (exit %s)" % (art["id"], code))
        return 1
    finally:
        shutil.rmtree(copy, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
