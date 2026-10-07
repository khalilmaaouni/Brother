"""L5b parent done check: recheck, score from records, waivers named, markers refused.

Usage:
    python3 scripts/donecheck_L5b.py [--root DIR] [--recheck-timeout SECONDS]

Exit codes:
    0  PASS  the L5b audit record meets the bar.
    1  FAIL  a readable record proves the bar is not met.
    2  NO-DATA  the record, a field or a deliverable is missing, unreadable,
                corrupt, unknown or inconsistent, or argv itself is not a
                list of strings.

Reads only. Writes nothing under --root. No shell, no subprocess. The recheck
runs in process: scripts/l5b_audit.py is executed with argv
["scripts/l5b_audit.py", "--recheck", "docs/architecture/l5b_reliability_audit.json"]
and the working directory set to --root. main() refuses a hostile argv (an
int, a bytes value, a list holding a non string) at the entry point with
NO-DATA exit 2, never a raw TypeError.
"""

from __future__ import annotations

import argparse
import json
import os
import runpy
import signal
import sys

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.dirname(HERE)

SCHEME_VERSION = "l5b-v1"
MARKER = "l5b: PROPAGATE_SAFE"
PROBE_NAMES = ("empty", "corrupt", "unknown")
ALLOWED_CHANGES = (
    "ADD_EXCEPT_CLASS",
    "ADD_TRY_EXCEPT_ONE_CALL",
    "PROPOSED_PATCH_ONLY",
)
ALLOWED_CLEARANCES = ("EXEMPTION", "FIRED_TEST")
AUDIT_MODULE = os.path.join("scripts", "l5b_audit.py")
SCORE_MODULE = os.path.join("tools", "l5b_audit", "score.py")
REPORT_JSON = os.path.join("docs", "architecture", "l5b_reliability_audit.json")
REPORT_PATCH = os.path.join("docs", "architecture", "l5b_reliability_audit.patch")
RECHECK_ARGV = [
    "scripts/l5b_audit.py",
    "--recheck",
    "docs/architecture/l5b_reliability_audit.json",
]
RECHECK_TIMEOUT_DEFAULT = 600


def _argv_is_clean(argv):
    """True only when argv is None or a list/tuple whose every element is a str.

    The entry point uses this before argparse sees argv so that a hostile value
    (an int, a bytes value, NaN, a dict, a list holding a non string) is
    refused with NO-DATA rather than a raw TypeError from argparse.
    """
    if argv is None:
        return True
    if isinstance(argv, (bytes, bytearray)):
        return False
    if not isinstance(argv, (list, tuple)):
        return False
    for item in argv:
        if not isinstance(item, str):
            return False
    return True


class _NoData(Exception):
    """Raised when evidence is missing, corrupt or inconsistent."""


class _Timeout(Exception):
    """Raised by the SIGALRM watchdog when the recheck runs too long."""


def _timeout_handler(signum, frame):
    raise _Timeout("recheck timed out")


def _arm_timeout(seconds):
    if not hasattr(signal, "SIGALRM") or not hasattr(signal, "alarm"):
        return None
    previous = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(int(seconds))
    return previous


def _disarm_timeout(previous):
    if previous is None:
        return
    try:
        signal.alarm(0)
    except (AttributeError, ValueError):
        pass
    try:
        signal.signal(signal.SIGALRM, previous)
    except (AttributeError, ValueError):
        pass


def _read_json(path):
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise _NoData("cannot read %s: %s" % (path, exc))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise _NoData("not UTF-8: %s" % path)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise _NoData("not valid JSON: %s: %s" % (path, exc))


def _load_score(root):
    path = os.path.join(root, SCORE_MODULE)
    if not os.path.isfile(path):
        raise _NoData("%s is missing" % SCORE_MODULE)
    try:
        namespace = runpy.run_path(path)
    except (ImportError, SyntaxError) as exc:
        raise _NoData("%s failed to load: %s" % (SCORE_MODULE, exc))
    except OSError as exc:
        raise _NoData("%s could not be read: %s" % (SCORE_MODULE, exc))
    compute = namespace.get("compute")
    probe_cls = namespace.get("FailClosedProbe")
    if compute is None or probe_cls is None:
        raise _NoData("%s does not export compute and FailClosedProbe" % SCORE_MODULE)
    if not callable(compute):
        raise _NoData("%s compute is not callable" % SCORE_MODULE)
    return compute, probe_cls


def _run_recheck(root, module_path, timeout):
    saved_argv = list(sys.argv)
    saved_cwd = os.getcwd()
    saved_path = list(sys.path)
    previous = None
    code = 0
    try:
        sys.argv = list(RECHECK_ARGV)
        if root not in sys.path:
            sys.path.insert(0, root)
        os.chdir(root)
        if isinstance(timeout, int) and not isinstance(timeout, bool) and timeout > 0:
            previous = _arm_timeout(timeout)
        try:
            runpy.run_path(module_path, run_name="__main__")
        except SystemExit as exc:
            raw = exc.code
            if raw is None:
                code = 0
            elif isinstance(raw, bool):
                code = 1
            elif isinstance(raw, int):
                code = raw
            else:
                code = 1
        except (ImportError, SyntaxError) as exc:
            raise _NoData("recheck module failed to load: %s" % exc)
    except _Timeout:
        raise _NoData("recheck timed out after %ss" % timeout)
    except OSError as exc:
        raise _NoData("recheck could not run: %s" % exc)
    finally:
        _disarm_timeout(previous)
        sys.argv = saved_argv
        sys.path[:] = saved_path
        try:
            os.chdir(saved_cwd)
        except OSError:
            pass
    return code


def _require(report, key, kind):
    if key not in report:
        raise _NoData("field %s is missing" % key)
    value = report[key]
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise _NoData("field %s must be an int" % key)
    elif kind == "bool":
        if not isinstance(value, bool):
            raise _NoData("field %s must be a boolean" % key)
    elif kind == "str":
        if not isinstance(value, str):
            raise _NoData("field %s must be a string" % key)
    elif kind == "list":
        if not isinstance(value, list):
            raise _NoData("field %s must be a list" % key)
    elif kind == "dict":
        if not isinstance(value, dict):
            raise _NoData("field %s must be an object" % key)
    return value


def _find_marker_line(text):
    for number, line in enumerate(text.splitlines(), start=1):
        if line.startswith("+") and not line.startswith("+++"):
            if MARKER in line:
                return number
    return None


def _judge(root, timeout):
    module_path = os.path.join(root, AUDIT_MODULE)
    if not os.path.isfile(module_path):
        raise _NoData("L5b.6 has not landed")
    if not os.access(module_path, os.R_OK):
        raise _NoData("L5b.6 is not readable")

    report = _read_json(os.path.join(root, REPORT_JSON))
    if not isinstance(report, dict):
        raise _NoData("L5b report must be a JSON object")
    if report.get("scheme_version") != SCHEME_VERSION:
        raise _NoData("scheme_version is not %s" % SCHEME_VERSION)

    total = _require(report, "boundary_calls_total", "int")
    stated = _require(report, "boundary_calls_stated", "int")
    tested = _require(report, "boundary_calls_tested", "int")
    hits = _require(report, "hits_unexempted", "int")
    fail_closed = _require(report, "fail_closed", "dict")
    blocked = _require(report, "blocked", "bool")
    reason = _require(report, "reason", "str")
    exemptions = _require(report, "exemptions", "list")
    exemptions_applied = _require(report, "exemptions_applied", "int")
    clearances = _require(report, "annotation_clearances", "list")
    fixes_applied = _require(report, "fixes_applied", "list")
    patches = _require(report, "proposed_patches", "list")

    if set(fail_closed.keys()) != set(PROBE_NAMES):
        raise _NoData("fail_closed must name exactly empty, corrupt and unknown")
    for probe_name in PROBE_NAMES:
        if not isinstance(fail_closed[probe_name], bool):
            raise _NoData("fail_closed.%s must be a boolean" % probe_name)

    compute, probe_cls = _load_score(root)
    probes = tuple(
        probe_cls(name, 0 if fail_closed[name] else 1, "BLOCK" if fail_closed[name] else "")
        for name in PROBE_NAMES
    )

    recheck_code = _run_recheck(root, module_path, timeout)
    if recheck_code == 1:
        return (["FAIL: recheck exited 1"], 1)
    if recheck_code != 0:
        raise _NoData("recheck exited %d" % recheck_code)

    try:
        result = compute(total, stated, tested, probes, hits)
    except ValueError as exc:
        raise _NoData("compute refused the record: %s" % exc)

    if not result.meets_bar:
        return (["FAIL: score from records is %.3f (%s); bar is 8.5" % (result.score, result.reason)], 1)
    if blocked or reason != "COMPLETE_PASS":
        return (["FAIL: record is blocked=%r reason=%r" % (blocked, reason)], 1)

    if exemptions_applied != len(exemptions):
        return (["FAIL: hidden waiver: exemptions_applied=%d but %d listed" % (exemptions_applied, len(exemptions))], 1)

    lines = ["waivers: %d" % len(exemptions)]
    for entry in exemptions:
        if not isinstance(entry, dict):
            raise _NoData("exemption entry must be an object")
        hit_id = entry.get("hit_id")
        entry_reason = entry.get("reason")
        if not isinstance(hit_id, str) or not isinstance(entry_reason, str):
            raise _NoData("exemption entry must carry string hit_id and reason")
        lines.append("%s: %s" % (hit_id, entry_reason))

    lines.append("annotation clearances: %d" % len(clearances))
    for entry in clearances:
        if not isinstance(entry, dict):
            raise _NoData("clearance entry must be an object")
        call_id = entry.get("call_id")
        covered_by = entry.get("covered_by")
        if not isinstance(call_id, str) or not isinstance(covered_by, str):
            raise _NoData("clearance entry must carry string call_id and covered_by")
        lines.append("%s covered by %s" % (call_id, covered_by))
        if covered_by not in ALLOWED_CLEARANCES:
            return (["FAIL: clearance %s covered by %s" % (call_id, covered_by)], 1)

    if fixes_applied != []:
        return (["FAIL: fixes_applied is not empty"], 1)

    for index, patch in enumerate(patches):
        if not isinstance(patch, dict):
            raise _NoData("proposed_patches[%d] must be an object" % index)
        change = patch.get("change")
        if not isinstance(change, str):
            raise _NoData("proposed_patches[%d].change must be a string" % index)
        if change not in ALLOWED_CHANGES:
            return (["FAIL: proposed_patches[%d].change is %s" % (index, change)], 1)
        diff = patch.get("patch_unified_diff")
        if diff is not None:
            if not isinstance(diff, str):
                raise _NoData("proposed_patches[%d].patch_unified_diff must be a string" % index)
            marker_line = _find_marker_line(diff)
            if marker_line is not None:
                return (["FAIL: marker %s added at proposed_patches[%d] line %d" % (MARKER, index, marker_line)], 1)

    patch_path = os.path.join(root, REPORT_PATCH)
    if os.path.exists(patch_path):
        try:
            with open(patch_path, "rb") as handle:
                raw_patch = handle.read()
        except OSError as exc:
            raise _NoData("patch file is unreadable: %s" % exc)
        try:
            patch_text = raw_patch.decode("utf-8")
        except UnicodeDecodeError:
            raise _NoData("patch file is not UTF-8")
        marker_line = _find_marker_line(patch_text)
        if marker_line is not None:
            return (["FAIL: marker %s added in %s line %d" % (MARKER, REPORT_PATCH, marker_line)], 1)

    lines.append(
        "PASS: score from records meets 8.5 (stated %d of %d, tested %d), waivers %d named, 0 markers added"
        % (stated, total, tested, len(exemptions))
    )
    return (lines, 0)


def main(argv=None):
    if not _argv_is_clean(argv):
        print("NO-DATA: argv must be None or a list of strings")
        return 2
    if argv is not None:
        argv = list(argv)
    parser = argparse.ArgumentParser(description="L5b parent done check")
    parser.add_argument("--root", default=DEFAULT_ROOT, help="repository root")
    parser.add_argument(
        "--recheck-timeout",
        type=int,
        default=RECHECK_TIMEOUT_DEFAULT,
        help="seconds allowed for the recheck",
    )
    args = parser.parse_args(argv)
    if not isinstance(args.root, str) or not args.root:
        print("NO-DATA: --root must be a non-empty path")
        return 2
    root = os.path.abspath(args.root)
    try:
        lines, code = _judge(root, args.recheck_timeout)
    except _NoData as exc:
        print("NO-DATA: %s" % exc)
        return 2
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
