#!/usr/bin/env python3
import argparse
import json
import os
import sys

# WBS-20.02 (docs/decisions/evidence-vocabulary-2026-09-13.json, EV-3):
# this is the one definition site for both triples, since this module is
# the one actually wired to the enforced merge gate (required_fast.sh).
# scripts/receipt_attest.py imports these instead of redeclaring them.
# Tuple, not a set: order is part of what a caller may rely on (e.g. in a
# "must be one of %s" message), and receipt_attest.py's prior declaration
# used this exact order.
VERDICTS = ("PASS", "FAIL", "NO-DATA")
LEVELS = ("OPTIONAL", "REQUIRED_FOR_MERGE", "REQUIRED_FOR_RELEASE")


class ObligationError(Exception):
    pass


def obligations_path(repo):
    return os.path.join(repo, "scripts", "gate_obligations.json")


def expected_absent_escapes(repo, expected):
    """Return True when expected is not a relative path that resolves inside repo."""
    if not expected:
        return False
    if os.path.isabs(expected):
        return True
    normalized = expected.replace(os.sep, "/")
    if os.altsep:
        normalized = normalized.replace(os.altsep, "/")
    if ".." in normalized.split("/"):
        return True
    repo_real = os.path.realpath(repo)
    candidate_real = os.path.realpath(os.path.join(repo, expected))
    if candidate_real == repo_real:
        return False
    return not candidate_real.startswith(repo_real + os.sep)


def read_json_file(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise ObligationError("missing")
    except Exception as exc:
        raise ObligationError("unreadable: %s" % exc)


def validate_obligations(data):
    if not isinstance(data, dict):
        raise ObligationError("top level not object")
    default = data.get("default")
    if default not in LEVELS:
        raise ObligationError("default missing or unknown")
    checks = data.get("checks", {})
    if not isinstance(checks, dict):
        raise ObligationError("checks not object")
    for name, entry in checks.items():
        if not isinstance(entry, dict):
            raise ObligationError("check %s not object" % name)
        level = entry.get("obligation")
        if level not in LEVELS:
            raise ObligationError("check %s unknown level" % name)
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ObligationError("check %s missing reason" % name)
        expected = entry.get("expected_absent_input")
        if expected is not None and not isinstance(expected, str):
            raise ObligationError("check %s bad expected_absent_input" % name)
    return data


def load_obligations(repo):
    path = obligations_path(repo)
    data = read_json_file(path)
    return validate_obligations(data)


def verdict_for_code(code):
    if code == 0:
        return "PASS"
    if code == 2:
        return "NO-DATA"
    return "FAIL"


def exit_code_for_verdict(verdict):
    """The forward mapping (verdict to process exit code), the counterpart
    of verdict_for_code above. This is the one definition site: every other
    module that needs it (reversibility_gate.py, merge_passport.py,
    master_source_snapshot.py, device_matrix.py) imports this instead of
    redeclaring it."""
    return {"PASS": 0, "FAIL": 1, "NO-DATA": 2}.get(verdict, 1)


def transition(args):
    try:
        data = load_obligations(args.repo)
    except ObligationError as exc:
        print("transition: NO-DATA (%s)" % exc, file=sys.stderr)
        return 2

    entries = []
    for line in sys.stdin.read().splitlines():
        if not line.strip():
            continue
        if "\t" in line:
            name, code_text = line.split("\t", 1)
        else:
            name, code_text = line, ""
        try:
            code = int(code_text)
        except ValueError:
            code = 1
        entries.append((name.strip(), code))

    if not entries:
        print("transition: NO-DATA (empty stdin)", file=sys.stderr)
        return 2

    default = data["default"]
    checks = data.get("checks", {})
    blocked = []

    for name, code in entries:
        verdict = verdict_for_code(code)
        entry = checks.get(name)
        if entry is None:
            obligation = default
            reason = "required and unlisted"
            expected = None
        else:
            obligation = entry.get("obligation", default)
            reason = entry.get("reason", "required and unlisted")
            expected = entry.get("expected_absent_input")
            if isinstance(expected, str) and expected_absent_escapes(args.repo, expected):
                expected = None
                reason = "expected_absent_input escapes the repo"

        if verdict == "FAIL":
            outcome = "BLOCKED"
            out_reason = "FAIL_BLOCKING"
        elif verdict == "PASS":
            outcome = "ALLOWED"
            out_reason = "PASS_ALLOWED"
        else:
            if expected:
                expected_path = os.path.join(args.repo, expected)
                if not os.path.exists(expected_path):
                    outcome = "ALLOWED"
                    out_reason = "NO-DATA_ALLOWED: " + reason
                elif obligation == "OPTIONAL":
                    outcome = "ALLOWED"
                    out_reason = "NO-DATA_ALLOWED: optional"
                elif obligation == "REQUIRED_FOR_RELEASE" and args.stage == "merge":
                    outcome = "ALLOWED"
                    out_reason = "NO-DATA_ALLOWED: required only at release: " + reason
                else:
                    outcome = "BLOCKED"
                    out_reason = "NO-DATA_BLOCKING: " + reason
            else:
                if obligation == "OPTIONAL":
                    outcome = "ALLOWED"
                    out_reason = "NO-DATA_ALLOWED: optional"
                elif obligation == "REQUIRED_FOR_RELEASE" and args.stage == "merge":
                    outcome = "ALLOWED"
                    out_reason = "NO-DATA_ALLOWED: required only at release: " + reason
                else:
                    outcome = "BLOCKED"
                    out_reason = "NO-DATA_BLOCKING: " + reason

        if outcome == "BLOCKED":
            blocked.append(name)
        print("%s\t%s\t%s\t%s\t%s" % (name, verdict, obligation, outcome, out_reason))

    if blocked:
        print("transition: BLOCKED (%s)" % " ".join(blocked))
        return 1
    print("transition: ALLOWED")
    return 0


def check(args):
    path = obligations_path(args.repo)
    if not os.path.exists(path):
        print("check: missing %s" % path, file=sys.stderr)
        return 2

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        print("check: cannot read %s: %s" % (path, exc), file=sys.stderr)
        return 2

    if not isinstance(data, dict):
        print("check: bad file: top level not object")
        return 1

    default = data.get("default")
    if default not in LEVELS:
        print("check: bad default: %r" % default)
        return 1

    checks = data.get("checks", {})
    if not isinstance(checks, dict):
        print("check: bad checks object")
        return 1

    for name, entry in checks.items():
        if not isinstance(entry, dict):
            print("check: bad entry %s: not object" % name)
            return 1
        level = entry.get("obligation")
        if level not in LEVELS:
            print("check: bad entry %s: unknown level %r" % (name, level))
            return 1
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            print("check: bad entry %s: missing reason" % name)
            return 1
        expected = entry.get("expected_absent_input")
        if expected is not None and not isinstance(expected, str):
            print("check: bad entry %s: expected_absent_input must be string" % name)
            return 1
        if isinstance(expected, str) and expected_absent_escapes(args.repo, expected):
            print("check: bad entry %s: expected_absent_input escapes the repo" % name)
            return 1

    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    transition_parser = subparsers.add_parser("transition")
    transition_parser.add_argument("--stage", choices=["merge", "release"], required=True)
    transition_parser.add_argument("--repo", required=True)
    transition_parser.set_defaults(func=transition)

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--repo", required=True)
    check_parser.set_defaults(func=check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
