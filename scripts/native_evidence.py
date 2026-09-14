#!/usr/bin/env python3
"""native_evidence: record and validate a native build or test evidence bundle.

The record binds one command to the candidate repository state seen before and
after it runs. It records the command's exit code and log, the expected test
identities, a fresh result bundle, the raw result query, and every claimed
capture with a content hash. Validation reads those files again and queries
the result bundle again. A count typed into a JSON file can therefore never
turn an unexecuted test into a pass.

This tool establishes record integrity only. It does not assess application
quality, accessibility semantics, physical device feel, or human acceptance.
Those observations belong in separate evidence with their own verdicts.

The result query is the Xcode 26 form:

    xcrun xcresulttool get test-results tests --path RESULT.xcresult --compact

Expected tests are exact ``nodeIdentifierURL`` values. That identifier carries
the test target as well as the test case, avoiding a same named test case in a
second target being accepted by accident. A result bundle path must not exist
before ``record`` runs. The command must create it, which prevents a previous
green bundle being reused as fresh evidence.

Exit status: 0 PASS, 1 FAIL, 2 NO-DATA. A required device or instrument that
is unavailable is NO-DATA when its requirement row says so. Malformed,
missing, contradictory, stale, or empty claimed evidence is FAIL.

Python 3, standard library only. No network.
"""
import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys

PASS = "PASS"
FAIL = "FAIL"
NODATA = "NO-DATA"
VERDICTS = {PASS, FAIL, NODATA}
SCHEMA = "brother-native-evidence-v1"


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def hash_file(path, require_nonempty=True):
    """A regular file hash record, optionally refusing an empty file."""
    if not isinstance(path, str) or not path:
        return {"path": path, "error": "path is not a nonempty string"}
    if not os.path.isfile(path) or os.path.islink(path):
        return {"path": path, "error": "missing or not a regular file"}
    try:
        size = os.path.getsize(path)
        if require_nonempty and size <= 0:
            return {"path": path, "size": size, "error": "empty file"}
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(block)
        return {"path": path, "size": size, "sha256": digest.hexdigest()}
    except OSError as exc:
        return {"path": path, "error": "unreadable: %s" % exc}


def hash_tree(path):
    """A nonempty directory tree's deterministic hash record, or an error."""
    if not isinstance(path, str) or not path:
        return {"path": path, "error": "path is not a nonempty string"}
    if not os.path.isdir(path) or os.path.islink(path):
        return {"path": path, "error": "missing or not a directory"}
    entries = []
    try:
        for root, dirs, names in os.walk(path):
            linked_dirs = [d for d in dirs if os.path.islink(
                os.path.join(root, d))]
            if linked_dirs:
                return {"path": path, "error": "contains a symlink directory"}
            dirs[:] = sorted(dirs)
            for name in sorted(names):
                full = os.path.join(root, name)
                if os.path.islink(full):
                    return {"path": path, "error": "contains a symlink"}
                # xcresult bundles can contain empty marker files. The bundle
                # must have a nonzero total size, while a claimed capture must
                # itself be nonempty.
                entry = hash_file(full, require_nonempty=False)
                if entry.get("error"):
                    return {"path": path, "error": "%s: %s" % (
                        os.path.relpath(full, path), entry["error"])}
                entries.append({"path": os.path.relpath(full, path).replace(
                    os.sep, "/"), "sha256": entry["sha256"],
                    "size": entry["size"]})
    except OSError as exc:
        return {"path": path, "error": "unreadable: %s" % exc}
    if not entries or sum(entry["size"] for entry in entries) <= 0:
        return {"path": path, "error": "empty directory"}
    encoded = json.dumps(entries, separators=(",", ":"),
                         sort_keys=True).encode("utf-8")
    return {"path": path, "files": entries, "sha256": sha256_bytes(encoded),
            "size": sum(entry["size"] for entry in entries)}


def git_call(repo, args):
    try:
        proc = subprocess.run(["git", "-C", repo] + list(args),
                              capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "git unavailable: %s" % exc
    if proc.returncode != 0:
        return None, "git %s exited %s" % (" ".join(args), proc.returncode)
    return proc.stdout, None


def untracked_hashes(repo, names):
    """Hash untracked regular files so an unchanged status line is not enough."""
    records = []
    for raw_name in names.split(b"\0"):
        if not raw_name:
            continue
        rel = raw_name.decode("utf-8", "surrogateescape")
        full = os.path.join(repo, rel)
        if os.path.isdir(full):
            record = hash_tree(full)
        else:
            record = hash_file(full)
        record["path"] = rel.replace(os.sep, "/")
        records.append(record)
    return records


def repo_snapshot(repo):
    """Exact candidate state, including worktree, index and untracked bytes."""
    repo = os.path.abspath(repo)
    revision, error = git_call(repo, ["rev-parse", "HEAD"])
    if error:
        return {"repo": repo, "error": error}
    status, error = git_call(repo, ["status", "--porcelain=v1", "-z"])
    if error:
        return {"repo": repo, "error": error}
    worktree, error = git_call(repo, ["diff", "--binary"])
    if error:
        return {"repo": repo, "error": error}
    index, error = git_call(repo, ["diff", "--cached", "--binary"])
    if error:
        return {"repo": repo, "error": error}
    untracked, error = git_call(repo, ["ls-files", "--others",
                                        "--exclude-standard", "-z"])
    if error:
        return {"repo": repo, "error": error}
    extras = untracked_hashes(repo, untracked)
    if any(item.get("error") for item in extras):
        return {"repo": repo, "error": "could not hash an untracked file"}
    return {
        "repo": repo,
        "revision": revision.decode("ascii", "replace").strip(),
        "dirty": bool(status),
        "status_sha256": sha256_bytes(status),
        "worktree_sha256": sha256_bytes(worktree),
        "index_sha256": sha256_bytes(index),
        "untracked": extras,
    }


def same_snapshot(left, right):
    keys = ("repo", "revision", "dirty", "status_sha256",
            "worktree_sha256", "index_sha256", "untracked")
    return all(left.get(key) == right.get(key) for key in keys)


def is_sha256(value):
    return (isinstance(value, str) and len(value) == 64
            and all(char in "0123456789abcdef" for char in value))


def valid_file_hash(record):
    """A structural check before validation opens an untrusted path."""
    return (isinstance(record, dict) and isinstance(record.get("path"), str)
            and isinstance(record.get("size"), int)
            and not isinstance(record.get("size"), bool)
            and record["size"] > 0 and is_sha256(record.get("sha256")))


def valid_tree_hash(record):
    return (valid_file_hash(record) and isinstance(record.get("files"), list))


def valid_snapshot(snapshot):
    required_hashes = ("status_sha256", "worktree_sha256", "index_sha256")
    return (isinstance(snapshot, dict) and isinstance(snapshot.get("repo"), str)
            and bool(snapshot["repo"])
            and isinstance(snapshot.get("revision"), str)
            and len(snapshot["revision"]) == 40
            and isinstance(snapshot.get("dirty"), bool)
            and isinstance(snapshot.get("untracked"), list)
            and all(is_sha256(snapshot.get(key)) for key in required_hashes))


def artifact_spec(value):
    """Validate KIND=PATH before the command, hash it only after the command."""
    if "=" not in value:
        raise ValueError("artifact must be KIND=PATH")
    kind, path = value.split("=", 1)
    if not kind or not path:
        raise ValueError("artifact must have a nonempty kind and path")
    return kind, os.path.abspath(path)


def parse_requirement(value):
    if "=" not in value:
        raise ValueError("requirement must be NAME=VERDICT[:DETAIL]")
    name, body = value.split("=", 1)
    verdict, separator, detail = body.partition(":")
    if not name or verdict not in VERDICTS:
        raise ValueError("requirement must name PASS, FAIL, or NO-DATA")
    if verdict == NODATA and not detail.strip():
        raise ValueError("NO-DATA requirement needs a reason")
    return {"name": name, "verdict": verdict, "detail": detail.strip()}


def run_xcresulttool(bundle, xcrun="xcrun"):
    args = [xcrun, "xcresulttool", "get", "test-results", "tests",
            "--path", bundle, "--compact"]
    try:
        proc = subprocess.run(args, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "xcresulttool unavailable: %s" % exc
    if proc.returncode != 0:
        return None, "xcresulttool exited %s: %s" % (
            proc.returncode, proc.stderr.decode("utf-8", "replace").strip())
    if not proc.stdout.strip():
        return None, "xcresulttool returned empty output"
    try:
        return json.loads(proc.stdout.decode("utf-8")), None
    except (UnicodeDecodeError, ValueError) as exc:
        return None, "xcresulttool returned malformed JSON: %s" % exc


def test_leaves(doc):
    """Exact executed Xcode test leaves, never a self reported total."""
    if not isinstance(doc, dict) or not isinstance(doc.get("testNodes"), list):
        raise ValueError("result JSON has no testNodes list")
    leaves = []

    def walk(node):
        if not isinstance(node, dict):
            raise ValueError("testNodes contains a non-object")
        children = node.get("children", [])
        if not isinstance(children, list):
            raise ValueError("a test node has non-list children")
        if node.get("nodeType") == "Test Case":
            identity = node.get("nodeIdentifierURL")
            result = node.get("result")
            if not isinstance(identity, str) or not identity:
                raise ValueError("a test case has no nodeIdentifierURL")
            if not isinstance(result, str) or not result:
                raise ValueError("a test case has no result")
            leaves.append({"identity": identity, "result": result})
        for child in children:
            walk(child)

    for root in doc["testNodes"]:
        walk(root)
    if not leaves:
        raise ValueError("result JSON names zero executed test cases")
    return sorted(leaves, key=lambda leaf: (leaf["identity"], leaf["result"]))


def tests_record(doc):
    leaves = test_leaves(doc)
    return {"executed_count": len(leaves), "leaves": leaves,
            "sha256": sha256_bytes(json.dumps(leaves, separators=(",", ":"),
                                               sort_keys=True).encode("utf-8"))}


def read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, "unreadable JSON: %s" % exc
    return value, None


def write_json(path, value):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(value, fh, indent=2, sort_keys=True)
        fh.write("\n")


def record(args):
    out = os.path.abspath(args.out)
    bundle = os.path.abspath(args.result_bundle)
    log_path = os.path.abspath(args.log or (out + ".log"))
    raw_tests_path = os.path.abspath(args.tests_json or (out + ".tests.json"))
    command = list(args.command or [])
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise ValueError("record needs a command after --command")
    if os.path.exists(bundle):
        raise ValueError("result bundle already exists: %s" % bundle)
    requirements = [parse_requirement(value) for value in args.requirement]
    artifact_specs = [artifact_spec(value) for value in args.artifact]
    before = repo_snapshot(args.repo)
    command_record = {"argv": command, "log": log_path}
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "wb") as log:
            log.write(("native_evidence command: %s\n" %
                       " ".join(command)).encode("utf-8"))
            try:
                proc = subprocess.Popen(command, cwd=os.path.abspath(args.repo),
                                        stdout=log, stderr=subprocess.STDOUT,
                                        start_new_session=(os.name == "posix"))
                try:
                    command_record["exit_code"] = proc.wait(timeout=args.timeout)
                except subprocess.TimeoutExpired:
                    # The wrapper and its build descendants must stop before
                    # the caller releases its simulator/build lease.
                    if os.name == "posix":
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:  # sbe: allow-silent process group already exited, the kill's goal is already met
                            pass
                    else:
                        proc.kill()
                    proc.wait()
                    raise
            except subprocess.TimeoutExpired:
                command_record["exit_code"] = None
                command_record["error"] = "command timed out"
            except OSError as exc:
                command_record["exit_code"] = None
                command_record["error"] = "command could not start: %s" % exc
    finally:
        after = repo_snapshot(args.repo)
    command_record["log_hash"] = hash_file(log_path)
    artifacts = [{"kind": kind, **hash_file(path)}
                 for kind, path in artifact_specs]
    # Some result readers materialize an index on their first query. Query
    # before sealing the bundle hash, then validate subsequent reads against
    # the final bytes. This does not weaken the later hash comparison.
    initial_result = hash_tree(bundle)
    tests_doc, tests_error = run_xcresulttool(bundle, args.xcrun) if not initial_result.get(
        "error") else (None, "result bundle unavailable")
    result = {"path": bundle, "hash": hash_tree(bundle)}
    tests = {"raw_json": raw_tests_path}
    if tests_doc is None:
        tests["error_kind"] = "unavailable"
        tests["error"] = tests_error
    else:
        write_json(raw_tests_path, tests_doc)
        tests["raw_hash"] = hash_file(raw_tests_path)
        try:
            tests.update(tests_record(tests_doc))
        except ValueError as exc:
            tests["error_kind"] = "malformed"
            tests["error"] = str(exc)
    doc = {
        "schema": SCHEMA,
        "candidate_before": before,
        "candidate_after": after,
        "command": command_record,
        "expected_tests": list(args.expected_test),
        "result_bundle": result,
        "tests": tests,
        "artifacts": artifacts,
        "requirements": requirements,
        "boundaries": {
            "record_integrity": "validated by this tool",
            "application_quality": "NOT-ASSESSED",
            "physical_and_human_acceptance": "NOT-ASSESSED",
        },
    }
    write_json(out, doc)
    return validate_record(doc, repo=args.repo, xcrun=args.xcrun)


def validate_record(doc, repo=None, xcrun="xcrun"):
    failures = []
    no_data = []
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return FAIL, ["malformed or unsupported evidence schema"]
    before, after = doc.get("candidate_before"), doc.get("candidate_after")
    if not valid_snapshot(before):
        failures.append("candidate state before command is unavailable")
    if not valid_snapshot(after):
        failures.append("candidate state after command is unavailable")
    if valid_snapshot(before) and valid_snapshot(after) and not same_snapshot(before, after):
        failures.append("candidate revision or dirty state changed during command")
    after_repo = after.get("repo", "") if valid_snapshot(after) else ""
    current_repo = os.path.abspath(repo or after_repo) if (repo or after_repo) else ""
    current = repo_snapshot(current_repo) if current_repo else {"error": "no repo"}
    if current.get("error"):
        no_data.append("current candidate state is unavailable: %s" %
                       current.get("error"))
    elif valid_snapshot(after) and not same_snapshot(after, current):
        failures.append("current candidate revision or dirty state differs from record")
    command = doc.get("command")
    if (not isinstance(command, dict) or not isinstance(command.get("argv"), list)
            or not command["argv"] or not all(isinstance(arg, str) and arg
                                               for arg in command["argv"])):
        failures.append("record has no executed command")
    elif command.get("exit_code") != 0:
        failures.append("command exit code is not zero")
    log_hash = command.get("log_hash") if isinstance(command, dict) else None
    if not valid_file_hash(log_hash):
        failures.append("command log is missing or empty")
    elif hash_file(log_hash["path"]) != log_hash:
        failures.append("command log hash no longer matches")
    result = doc.get("result_bundle")
    result_hash = result.get("hash") if isinstance(result, dict) else None
    if not valid_tree_hash(result_hash):
        failures.append("result bundle is missing, malformed, or empty")
    elif hash_tree(result_hash["path"]) != result_hash:
        failures.append("result bundle hash no longer matches")
    artifacts = doc.get("artifacts", [])
    if not isinstance(artifacts, list):
        failures.append("artifacts are malformed")
    else:
        for artifact in artifacts:
            if (not isinstance(artifact, dict)
                    or not isinstance(artifact.get("kind"), str)
                    or not artifact["kind"]):
                failures.append("artifact entry is malformed")
            elif artifact.get("error"):
                failures.append("claimed %s is %s" % (artifact["kind"],
                                                       artifact["error"]))
            elif not valid_file_hash(artifact):
                failures.append("claimed %s has malformed hash data" %
                                artifact["kind"])
            else:
                current_artifact = hash_file(artifact["path"])
                if current_artifact != {k: v for k, v in artifact.items()
                                        if k != "kind"}:
                    failures.append("claimed %s hash no longer matches" %
                                    artifact["kind"])
    tests = doc.get("tests")
    expected = doc.get("expected_tests")
    expected_ok = isinstance(expected, list) and bool(expected) and all(
            isinstance(item, str) and item for item in expected)
    if not expected_ok:
        failures.append("record has no exact expected test identities")
    if not isinstance(tests, dict):
        failures.append("tests are malformed")
    elif tests.get("error"):
        if tests.get("error_kind") == "malformed":
            failures.append("test result is malformed: %s" % tests["error"])
        else:
            no_data.append("test result query unavailable: %s" %
                           tests.get("error", "missing record"))
    else:
        raw_hash = tests.get("raw_hash")
        if not valid_file_hash(raw_hash):
            failures.append("raw result JSON is missing or empty")
        elif hash_file(raw_hash["path"]) != raw_hash:
            failures.append("raw result JSON hash no longer matches")
        bundle_path = result.get("path") if isinstance(result, dict) else None
        if not isinstance(bundle_path, str) or not bundle_path:
            live_doc, live_error = None, "result bundle path missing"
            failures.append("result bundle path is malformed")
        else:
            live_doc, live_error = run_xcresulttool(bundle_path, xcrun)
        if live_doc is None:
            no_data.append("cannot requery result bundle: %s" % live_error)
        else:
            try:
                live = tests_record(live_doc)
            except ValueError as exc:
                failures.append("requeried result is malformed: %s" % exc)
            else:
                if live != {key: tests.get(key) for key in ("executed_count", "leaves", "sha256")}:
                    failures.append("requeried result does not match recorded test identities")
                identities = {leaf["identity"]: leaf["result"] for leaf in live["leaves"]}
                if expected_ok:
                    missing = [item for item in expected if item not in identities]
                    if missing:
                        failures.append("expected test identity did not execute")
                    skipped = [item for item in expected if identities.get(item) != "Passed"]
                    if skipped:
                        failures.append("expected test did not pass")
                if any(leaf["result"] == "Failed" for leaf in live["leaves"]):
                    failures.append("result contains a failed test")
    requirements = doc.get("requirements", [])
    if not isinstance(requirements, list):
        failures.append("requirements are malformed")
    else:
        for requirement in requirements:
            if (not isinstance(requirement, dict)
                    or not isinstance(requirement.get("verdict"), str)
                    or requirement["verdict"] not in VERDICTS
                    or not isinstance(requirement.get("name"), str)
                    or not requirement["name"]
                    or not isinstance(requirement.get("detail"), str)):
                failures.append("requirement entry is malformed")
            elif requirement["verdict"] == FAIL:
                failures.append("required %s reports FAIL" % requirement["name"])
            elif requirement["verdict"] == NODATA:
                if not requirement.get("detail"):
                    failures.append("NO-DATA requirement has no reason")
                else:
                    no_data.append("required %s: %s" % (requirement["name"],
                                                          requirement["detail"]))
    boundaries = doc.get("boundaries")
    if not isinstance(boundaries, dict) or boundaries.get("record_integrity") != "validated by this tool" or boundaries.get("application_quality") != "NOT-ASSESSED" or boundaries.get("physical_and_human_acceptance") != "NOT-ASSESSED":
        failures.append("record boundaries are malformed")
    if failures:
        return FAIL, failures + no_data
    if no_data:
        return NODATA, no_data
    return PASS, ["record integrity holds; application quality and human acceptance are NOT-ASSESSED"]


def validate(args):
    doc, error = read_json(args.evidence)
    if error:
        return FAIL, [error]
    return validate_record(doc, repo=args.repo, xcrun=args.xcrun)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="action", required=True)
    record_parser = sub.add_parser("record")
    record_parser.add_argument("--repo", required=True)
    record_parser.add_argument("--out", required=True)
    record_parser.add_argument("--result-bundle", required=True)
    record_parser.add_argument("--expected-test", action="append", default=[])
    record_parser.add_argument("--artifact", action="append", default=[],
                               help="claimed evidence as KIND=PATH")
    record_parser.add_argument("--requirement", action="append", default=[],
                               help="required observation as NAME=VERDICT[:DETAIL]")
    record_parser.add_argument("--log")
    record_parser.add_argument("--tests-json")
    record_parser.add_argument("--xcrun", default="xcrun")
    record_parser.add_argument("--timeout", type=int, default=1800)
    record_parser.add_argument("--command", nargs=argparse.REMAINDER,
                               required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--evidence", required=True)
    validate_parser.add_argument("--repo")
    validate_parser.add_argument("--xcrun", default="xcrun")
    args = parser.parse_args(argv)
    try:
        verdict, lines = record(args) if args.action == "record" else validate(args)
    except ValueError as exc:
        verdict, lines = FAIL, [str(exc)]
    for line in lines:
        print("native_evidence: %s: %s" % (verdict, line))
    return {PASS: 0, FAIL: 1, NODATA: 2}[verdict]


if __name__ == "__main__":
    sys.exit(main())
