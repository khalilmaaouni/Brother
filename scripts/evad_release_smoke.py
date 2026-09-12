#!/usr/bin/env python3
"""Run a bounded EVAD regression family, without changing EVAD scores.

Every required case must actually complete its selected tests. Exit 0 means
PASS, 1 means FAIL, and 2 means NO-DATA. Either nonzero exit prevents the
required release transition; unavailable evidence does not become a failure.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
FAMILY = ROOT / "docs/plan/EVAD-RELEASE-FAMILY.json"
REQUIRED_IDS = {"t2", "t3", "t5", "t6"}


def now():
    return datetime.now(timezone.utc).isoformat()


def load_family(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("family must be an object")
    cases = data.get("cases")
    if not isinstance(cases, list) or len(cases) != len(REQUIRED_IDS):
        raise ValueError("the four required cases must all be present")
    if {c.get("id") for c in cases if isinstance(c, dict)} != REQUIRED_IDS:
        raise ValueError("missing, duplicate or unknown required case")
    for case in cases:
        command = case.get("command")
        if (case.get("required") is not True or not isinstance(command, list)
                or len(command) < 4 or not all(isinstance(s, str) for s in command)
                or command[0] != "python3" or command[-1] != "-v"
                or not re.fullmatch(r"scripts/test_[a-z_]+\.py", command[1])
                or not all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", s)
                           for s in command[2:-1])):
            raise ValueError("required case must name explicit local test classes")
        if type(case.get("expected_tests")) is not int or case["expected_tests"] <= 0:
            raise ValueError("required case must name a positive test count")
        if type(case.get("timeout_seconds")) is not int or not 1 <= case["timeout_seconds"] <= 30:
            raise ValueError("case timeout must be between 1 and 30 seconds")
    return data


def identity(root):
    def git(*args):
        result = subprocess.run(["git", *args], cwd=root, capture_output=True,
                                timeout=10)
        if result.returncode:
            raise ValueError("git identity unavailable")
        return result.stdout
    try:
        head = git("rev-parse", "HEAD").decode().strip()
        status = git("status", "--porcelain", "--untracked-files=all")
        diff = git("diff", "HEAD", "--binary")
        untracked = hashlib.sha256()
        for name in git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
            if name:
                path = Path(root) / os.fsdecode(name)
                content = os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes()
                untracked.update(name + b"\0" + hashlib.sha256(content).digest())
        return {"commit": head, "dirty": bool(status),
                "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
                "untracked_tree_sha256": untracked.hexdigest(),
                "status_sha256": hashlib.sha256(status).hexdigest()}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"commit": "NO-DATA", "reason": "git identity unavailable"}


def classify(exit_code, stdout, stderr, expected):
    combined = stdout + "\n" + stderr
    counts = re.findall(r"^Ran (\d+) tests? in [0-9.]+s$", combined, re.MULTILINE)
    if len(counts) == 1 and int(counts[0]) == 0:
        return "NO-DATA", "no tests were discovered"
    if exit_code == 2:
        return "NO-DATA", "test command reported unavailable evidence"
    if exit_code != 0:
        return "FAIL", "test command exited nonzero"
    if len(counts) != 1 or int(counts[0]) != expected:
        return "NO-DATA", "expected test count was not demonstrated"
    if re.search(r"^OK \(skipped=", combined, re.MULTILINE):
        return "NO-DATA", "a required test was skipped"
    if not re.search(r"^OK$", combined, re.MULTILINE):
        return "NO-DATA", "completed successful test result is absent"
    return "PASS", "all expected tests completed successfully"


def run_case(case, root):
    started = time.monotonic()
    result = {"id": case["id"], "required": True,
              "command": case["command"], "started_at": now(),
              "expected_tests": case["expected_tests"], "exit_code": None,
              "stdout": "", "stderr": ""}
    path = Path(root) / case["command"][1]
    if not path.is_file():
        result.update(verdict="NO-DATA", reason="required test file is absent")
    else:
        result["test_file_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        try:
            process = subprocess.Popen(case["command"], cwd=root,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, start_new_session=(os.name == "posix"))
            try:
                stdout, stderr = process.communicate(timeout=case["timeout_seconds"])
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass  # sbe: allow-silent process exited before group cleanup; verdict remains NO-DATA
                else:
                    process.kill()
                stdout, stderr = process.communicate(timeout=5)
                result.update(stdout=stdout, stderr=stderr, exit_code=process.returncode,
                              verdict="NO-DATA", reason="required case timed out")
            else:
                verdict, reason = classify(process.returncode, stdout, stderr,
                                           case["expected_tests"])
                result.update(stdout=stdout, stderr=stderr, exit_code=process.returncode,
                              verdict=verdict, reason=reason)
        except (OSError, subprocess.SubprocessError) as exc:
            result.update(verdict="NO-DATA", reason="cannot complete required case: " +
                          type(exc).__name__)
    result.update(finished_at=now(), duration_seconds=round(time.monotonic() - started, 3))
    return result


def run_family(path=FAMILY, root=ROOT):
    started = time.monotonic()
    report = {"schema_version": 1, "family": "evad-release-smoke",
              "scope": "selected deterministic regressions, not a full EVAD score",
              "started_at": now(), "source": identity(root), "cases": []}
    try:
        family = load_family(path)
        report["family_sha256"] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        report["unmeasured"] = family.get("unmeasured", [])
        report["cases"] = [run_case(case, root) for case in family["cases"]]
        verdicts = {case["verdict"] for case in report["cases"]}
        report["source_after"] = identity(root)
        if "FAIL" in verdicts:
            report["verdict"] = "FAIL"
        elif ("NO-DATA" in verdicts or report["source"]["commit"] == "NO-DATA"
              or report["source_after"] != report["source"]):
            report["verdict"] = "NO-DATA"
            report["reason"] = "required evidence or stable source identity is unavailable"
        else:
            report["verdict"] = "PASS"
    except (OSError, ValueError, TypeError, KeyError) as exc:
        report.update(verdict="NO-DATA", reason="family unavailable or malformed: " +
                      type(exc).__name__)
    report.update(finished_at=now(), duration_seconds=round(time.monotonic() - started, 3),
                  required_transition_ready=report["verdict"] == "PASS")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", type=Path, default=FAMILY)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = run_family(args.family)
    output = args.output or Path(tempfile.gettempdir()) / "brother-evad-release-smoke" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         delete=False) as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
            temporary = handle.name
        os.replace(temporary, output)
    except OSError:
        print("FAIL: cannot persist the release family evidence")
        return 1
    for case in report["cases"]:
        print("{} {}: {}".format(case["verdict"], case["id"], case["reason"]))
    print("{}: EVAD release regression family; required transition {}".format(
        report["verdict"], "ready" if report["required_transition_ready"] else "blocked"))
    print(output.resolve())
    return {"PASS": 0, "FAIL": 1, "NO-DATA": 2}[report["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
