#!/usr/bin/env python3
"""mutation_probe: score candidate mutants against a test file, in a scratch copy.

mutation_gate.py holds the permanent, named mutants. This is the step before
it: given a JSON list of candidate mutations for one source file (each
{"id", "why", "old", "new"}, "old" unique in the file), apply each one to a
scratch copy of the tree, run the named test module, and report KILLED or
SURVIVED. A survivor names an assertion the suite is missing. The real tree
is never touched.

usage: mutation_probe.py --src PATH --test PATH --mutants FILE.json [--out FILE.json]
       --copy PATH adds a file or tree to the scratch copy; repeatable
       (paths relative to the repository root)
exit: 0 every valid mutant killed, 1 at least one survived, 2 nothing measurable
       a test that times out is TIMEOUT: NO-DATA that blocks, never a kill
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from mutation_gate import _patch_unique  # noqa: E402  reuse, never a second patcher

COPY = ("plugin", "scripts/brother_antigravity_hook.py",
        "scripts/test_brother_antigravity_hook.py")


def load_mutants(path):
    if not isinstance(path, str) or not path:
        raise ValueError("mutants path must be a non-empty string")
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise ValueError("cannot read mutants file %r: %s" % (path, exc))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("mutants file %r is not utf-8: %s" % (path, exc))
    text = text.strip()
    text = re.sub(r"^```[a-z]*\n|\n```$", "", text)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < 0:
        raise ValueError("no JSON array in %s" % path)
    try:
        parsed = json.loads(text[start:end + 1])
    except ValueError as exc:
        raise ValueError("bad JSON in %s: %s" % (path, exc))
    if not isinstance(parsed, list):
        raise ValueError("mutants file %s must hold a JSON array" % path)
    seen = set()
    for item in parsed:
        if not isinstance(item, dict):
            raise ValueError("mutants file %s must hold objects, found %s"
                             % (path, type(item).__name__))
        mid = item.get("id")
        if isinstance(mid, str) and mid:
            if mid in seen:
                raise ValueError("duplicate mutant id: %s" % mid)
            seen.add(mid)
    return parsed


def validate_mutant(m):
    if not isinstance(m, dict):
        raise ValueError("mutant must be an object")
    if "id" not in m:
        return False, "id must be a non-empty string"
    mid = m["id"]
    if not isinstance(mid, str):
        raise ValueError("id must be a string")
    if not mid:
        return False, "id must be a non-empty string"
    if "why" not in m:
        return False, "why must be a non-empty string"
    why = m["why"]
    if not isinstance(why, str):
        raise ValueError("why must be a string")
    if not why:
        return False, "why must be a non-empty string"
    if "old" not in m:
        return False, "old must be a non-empty string"
    old = m["old"]
    if not isinstance(old, str):
        raise ValueError("old must be a string")
    if not old:
        return False, "old must be a non-empty string"
    if "new" not in m:
        return False, "new must be a string"
    new = m["new"]
    if not isinstance(new, str):
        raise ValueError("new must be a string")
    if "expect_test" not in m:
        return False, "expect_test must be a non-empty string"
    expect = m["expect_test"]
    if not isinstance(expect, str):
        raise ValueError("expect_test must be a string")
    if not expect:
        return False, "expect_test must be a non-empty string"
    if "file" in m and m["file"] is not None:
        file = m["file"]
        if not isinstance(file, str):
            raise ValueError("file must be a string when present")
        if not file:
            return False, "file must be a non-empty string when present"
        try:
            _safe_rel(file)
        except ValueError as exc:
            return False, "file: %s" % exc
    return True, ""


def apply_mutant(original, src_rel, m):
    if not isinstance(original, str):
        raise ValueError("original must be a string")
    if not isinstance(src_rel, str) or not src_rel:
        raise ValueError("src_rel must be a non-empty string")
    if not isinstance(m, dict):
        raise ValueError("mutant must be an object")
    if "old" not in m:
        return None, "old must be a non-empty string"
    old = m["old"]
    if not isinstance(old, str):
        raise ValueError("old must be a string")
    if not old:
        return None, "old must be a non-empty string"
    if "new" not in m:
        return None, "new must be a string"
    new = m["new"]
    if not isinstance(new, str):
        raise ValueError("new must be a string")
    patched, problem = _patch_unique(original, old, new)
    if problem:
        return None, problem
    try:
        compile(patched, src_rel, "exec")
    except SyntaxError as exc:
        return None, "syntax: %s" % exc
    return patched, ""


def _safe_rel(path):
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    if os.path.isabs(path):
        raise ValueError("absolute path refused: %s" % path)
    norm = os.path.normpath(path)
    if norm.startswith("..") or os.sep + ".." + os.sep in norm or norm.endswith(os.sep + ".."):
        raise ValueError("escape refused: %s" % path)
    root = os.path.realpath(ROOT)  # resolve BOTH sides: a root reached through a symlink (a temp folder on macOS) refused every honest path
    full = os.path.realpath(os.path.join(root, path))
    if not full.startswith(root + os.sep):
        raise ValueError("outside root refused: %s" % path)
    return full


def default_copy_paths(src_rel, test_rel):
    if not isinstance(src_rel, str) or not isinstance(test_rel, str):
        raise ValueError("default_copy_paths: src and test must be strings")
    roots = []
    for path in (src_rel, test_rel):
        if not path:
            raise ValueError("path must be non-empty")
        head = path.split("/")[0].split(os.sep)[0]
        if head and head not in roots:
            roots.append(head)
    return tuple(roots)


def resolve_copy_paths(src_rel, test_rel, copy_args):
    if not isinstance(src_rel, str) or not isinstance(test_rel, str):
        raise ValueError("resolve_copy_paths: src and test must be strings")
    if not isinstance(copy_args, tuple):
        raise ValueError("copy_args must be a tuple")
    for item in copy_args:
        if not isinstance(item, str) or not item:
            raise ValueError("copy arg must be a non-empty string")
    _safe_rel(src_rel)
    _safe_rel(test_rel)
    for item in copy_args:
        _safe_rel(item)
    ordered = []
    for path in (src_rel, test_rel) + tuple(copy_args) + default_copy_paths(src_rel, test_rel):
        if path not in ordered:
            ordered.append(path)
    return tuple(ordered)


SCRATCH_PREFIX = "mutation-probe-"
STALE_SECONDS = 6 * 60 * 60


def sweep_stale_scratch(now=None, max_age=STALE_SECONDS):
    """Remove scratch trees an earlier run was killed before deleting.

    The per run cleanup is a finally block, which covers every raised
    exception but cannot run when the process is killed, so an interrupted
    probe leaks its whole tree. Measured 2026-09-21: 1067 leaked trees
    holding 16.3 GB, which took the machine from 33 GB free to 4.8 GB in
    one night. This is the one place a tree is created, so sweeping here
    makes the leak self healing whatever killed the previous run.

    Returns the count removed. Trees younger than max_age are left alone,
    so a concurrent probe is never touched, and an unreadable parent or
    entry is skipped rather than deleted: this fails toward keeping.
    """
    if not isinstance(max_age, (int, float)) or isinstance(max_age, bool):
        raise ValueError("max_age must be a number of seconds")
    if max_age <= 0:
        raise ValueError("max_age must be positive")
    if now is None:
        now = time.time()
    elif not isinstance(now, (int, float)) or isinstance(now, bool):
        raise ValueError("now must be a number of seconds")
    parent = tempfile.gettempdir()
    try:
        names = os.listdir(parent)
    except OSError:
        return 0
    removed = 0
    for name in names:
        if not name.startswith(SCRATCH_PREFIX):
            continue
        path = os.path.join(parent, name)
        if not os.path.isdir(path):
            continue
        try:
            age = now - os.path.getmtime(path)
        except OSError:
            continue
        if age < max_age:
            continue
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.isdir(path):
            removed += 1
    return removed


def scratch_tree(extra=()):
    if not isinstance(extra, tuple):
        raise ValueError("extra must be a tuple")
    copy_paths = tuple(COPY) if not extra else tuple(extra)
    for path in copy_paths:
        _safe_rel(path)
    sweep_stale_scratch()
    root = tempfile.mkdtemp(prefix=SCRATCH_PREFIX)
    try:
        for rel in copy_paths:
            src = os.path.join(ROOT, rel)
            if not os.path.exists(src):
                raise ValueError("missing copy source: %s" % rel)
            dst = os.path.join(root, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.isdir(src):
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy2(src, dst)
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return root


def run_test(root, test_rel, timeout):
    if not isinstance(root, str) or not root:
        raise ValueError("root must be a non-empty string")
    if not isinstance(test_rel, str) or not test_rel:
        raise ValueError("test_rel must be a non-empty string")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError("timeout must be a positive integer")
    if test_rel.startswith("scripts/"):
        cmd = [sys.executable, test_rel]
    else:
        cmd = [sys.executable, "-m", "unittest", test_rel[:-3].replace("/", ".")]
    # A mutant can remove the very guard that keeps a test offline. On
    # 2026-09-20 one did: two jobs reached the real dispatcher, made two paid
    # calls and wrote a colliding id into the live ledger. Every suite the probe
    # runs therefore gets a throwaway HOME (the model bridge finds no key there
    # and exits 44 without calling anything) and throwaway state folders, so
    # no mutant can spend money or touch live state, whatever it removes.
    sandbox = os.path.join(root, ".probe-sandbox")
    os.makedirs(sandbox, exist_ok=True)
    os.makedirs(os.path.join(sandbox, "tmp"), exist_ok=True)
    # the grader's and the lander's ONE definition (2026-10-03): it also drops the run's knobs (BROTHER_TRANSPORTS) and
    # credential shaped names, which this copy kept
    sys.path.insert(0, os.path.join(HERE, "loop"))
    from loop_switches import suite_env
    env = suite_env(os.environ, sandbox)
    try:
        proc = subprocess.run(cmd, cwd=root, env=env, capture_output=True,
                              text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return "timeout", "", ""


def run_test_capture(root, test_rel, timeout):
    """Run the test file and return (code, tail).

    tail is the last 1500 characters of stdout plus stderr, or "" when silent.
    A timeout returns ("timeout", "TIMEOUT after <n>s"): TIMEOUT is NO-DATA that
    blocks the run, never a kill.
    """
    if not isinstance(root, str) or not root:
        raise ValueError("root must be a non-empty string")
    if not isinstance(test_rel, str) or not test_rel:
        raise ValueError("test_rel must be a non-empty string")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError("timeout must be a positive integer")
    code, stdout, stderr = run_test(root, test_rel, timeout)
    if code == "timeout":
        return "timeout", "TIMEOUT after %ss" % timeout
    tail = (stdout + stderr)[-1500:]
    return code, tail


def baseline_ok(code, stdout, stderr):
    if isinstance(code, bool) or not isinstance(code, (int, str)):
        return False
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        return False
    if code != 0:
        return False
    combined = stdout + "\n" + stderr
    # "tests?": unittest says "Ran 1 test" for a one test suite, and the plural
    # only pattern refused every such suite as not green (all 37 one test vault
    # facade suites, measured 2026-09-30). Zero tests is still refused.
    if not re.search(r"Ran [1-9][0-9]* tests?\b", combined):
        return False
    if "OK" not in combined:
        return False
    return True


def classify_run(code, stdout, stderr, expect_test):
    if isinstance(code, bool) or not isinstance(code, (int, str)):
        return "INFRA"
    if not isinstance(stdout, str) or not isinstance(stderr, str) or not isinstance(expect_test, str):
        return "INFRA"
    if code == "timeout":
        return "TIMEOUT"
    if code == 0:
        return "SURVIVED"
    combined = stdout + "\n" + stderr
    if "ImportError" in combined: return "INFRA"
    if "ModuleNotFoundError" in combined: return "INFRA"
    if "FileNotFoundError" in combined: return "INFRA"
    if "SyntaxError" in combined: return "INFRA"
    if ("FAIL: " + expect_test) in combined and "AssertionError" in combined:
        return "KILLED"
    return "INFRA"


def _empty_summary(src, test):
    return {"src": src, "test": test, "valid": 0, "killed": 0, "survived": 0,
            "invalid": 0, "infra": 0, "timeout": 0, "rows": []}


def _mutant_detail(result, code, stdout, stderr):
    if result == "KILLED":
        return "AssertionError in expected test"
    if result == "SURVIVED":
        return "no failure"
    if result == "TIMEOUT":
        return "timeout"
    if result == "INFRA":
        return (stdout + "\n" + stderr).strip()[-200:]
    return "invalid"


def _run_one_mutant(root, src, test, m, timeout):
    row = {"id": None, "why": None, "file": src, "expect_test": None,
           "result": "INVALID", "detail": "invalid mutant", "exit": 2,
           "output_tail": ""}
    if isinstance(m, dict):
        row["id"] = m.get("id")
        row["why"] = m.get("why")
        row["file"] = m.get("file") or src
        row["expect_test"] = m.get("expect_test")
    try:
        ok, problem = validate_mutant(m)
    except ValueError as exc:
        row["detail"] = str(exc)
        return row, True
    if not ok:
        row["detail"] = problem
        return row, True
    target = m.get("file") or src
    target_path = os.path.join(root, target)
    try:
        with open(target_path, "r", encoding="utf-8") as fh:
            original = fh.read()
    except OSError as exc:
        row["result"] = "INFRA"
        row["detail"] = "missing file: %s" % exc
        row["exit"] = 2
        return row, True
    patched, problem = apply_mutant(original, target, m)
    if problem:
        row["result"] = "INVALID"
        row["detail"] = problem
        row["exit"] = 2
        return row, True
    try:
        with open(target_path, "w", encoding="utf-8") as fh:
            fh.write(patched)
    except OSError as exc:
        row["result"] = "INFRA"
        row["detail"] = "write failed: %s" % exc
        row["exit"] = 2
        return row, True
    code, tail = run_test_capture(root, test, timeout)
    result = classify_run(code, tail, "", m.get("expect_test", ""))
    row["result"] = result
    row["detail"] = _mutant_detail(result, code, tail, "")
    row["output_tail"] = tail
    row["exit"] = code
    try:
        with open(target_path, "w", encoding="utf-8") as fh:
            fh.write(original)
    except OSError as exc:
        print("NO-DATA: restore failed for %s: %s" % (target_path, exc))
        return row, False
    return row, True


def probe(src, test, mutants, copy_paths=(), timeout=240):
    if not isinstance(src, str) or not src:
        raise ValueError("src must be a non-empty string")
    if not isinstance(test, str) or not test:
        raise ValueError("test must be a non-empty string")
    if not isinstance(mutants, list):
        raise ValueError("mutants must be a list")
    if not isinstance(copy_paths, tuple):
        raise ValueError("copy_paths must be a tuple")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        raise ValueError("timeout must be a positive integer")
    try:
        for path in (src, test) + tuple(copy_paths):
            _safe_rel(path)
        resolved = resolve_copy_paths(src, test, copy_paths)
    except ValueError as exc:
        print("NO-DATA: %s" % exc)
        return _empty_summary(src, test)
    ordered = []
    for path in resolved:
        if os.path.isdir(os.path.join(ROOT, path)):
            ordered.append(path)
    for path in resolved:
        if path not in ordered:
            ordered.append(path)
    try:
        root = scratch_tree(tuple(ordered))
    except (OSError, ValueError, shutil.Error) as exc:
        print("NO-DATA: %s" % exc)
        return _empty_summary(src, test)
    rows = []
    baseline_blocked = False
    restore_failed = False
    cleanup_failed = False
    try:
        src_path = os.path.join(root, src)
        try:
            with open(src_path, "r", encoding="utf-8") as fh:
                original = fh.read()
        except OSError as exc:
            print("NO-DATA: cannot read src %s: %s" % (src_path, exc))
            return _empty_summary(src, test)
        code, stdout, stderr = run_test(root, test, timeout)
        if not baseline_ok(code, stdout, stderr):
            print("NO-DATA: %s is not green on the unmutated copy (exit %s)" % (test, code))
            baseline_blocked = True
        else:
            for m in mutants:
                row, restored = _run_one_mutant(root, src, test, m, timeout)
                rows.append(row)
                print("%-9s %s" % (row["result"], row["id"]))
                if not restored:
                    restore_failed = True
                    break
    finally:
        try:
            shutil.rmtree(root)
        except OSError as exc:
            cleanup_failed = True
            print("NO-DATA: cleanup failed for %s: %s" % (root, exc))
    if baseline_blocked or restore_failed or cleanup_failed:
        return _empty_summary(src, test)
    valid = [r for r in rows if r["result"] not in ("INVALID", "TIMEOUT")]
    killed = sum(1 for r in valid if r["result"] == "KILLED")
    survived = sum(1 for r in valid if r["result"] == "SURVIVED")
    invalid = sum(1 for r in rows if r["result"] == "INVALID")
    infra = sum(1 for r in valid if r["result"] == "INFRA")
    timeout_count = sum(1 for r in rows if r["result"] == "TIMEOUT")
    summary = {"src": src, "test": test, "valid": len(valid), "killed": killed,
               "survived": survived, "invalid": invalid, "infra": infra,
               "timeout": timeout_count, "rows": rows}
    print("mutation_probe: %s: %d of %d valid mutants killed, %d invalid"
          % (src, killed, len(valid), invalid))
    return summary


def _argv_problem(argv):
    if argv is None:
        return None
    if not isinstance(argv, list):
        return "argv must be a list of strings"
    for item in argv:
        if not isinstance(item, str):
            return "argv must contain only strings"
    return None


def main(argv=None):
    problem = _argv_problem(argv)
    if problem is not None:
        print("NO-DATA: %s" % problem)
        return 2
    if argv is None:
        argv = sys.argv[1:]
    else:
        argv = list(argv)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", required=True)
    ap.add_argument("--test", required=True)
    ap.add_argument("--mutants", required=True)
    ap.add_argument("--copy", action="append", default=[], dest="copy")
    ap.add_argument("--out")
    ap.add_argument("--timeout", type=int, default=240)
    a = ap.parse_args(argv)
    try:
        mutants = load_mutants(a.mutants)
    except ValueError as exc:
        print("NO-DATA: %s" % exc)
        return 2
    try:
        summary = probe(a.src, a.test, mutants, tuple(a.copy), a.timeout)
    except ValueError as exc:
        print("NO-DATA: %s" % exc)
        return 2
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=1)
    valid = summary["valid"]
    killed = summary["killed"]
    if valid == 0:
        return 2
    return 0 if killed == valid else 1


if __name__ == "__main__":
    sys.exit(main())
