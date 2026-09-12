#!/usr/bin/env python3
"""Native mobile workflow support for Brother's existing execution engine.

Doctor, reference binding, simulator tests and preview, and portable evidence.
Exit 0 means the named technical checks passed, never a release or human verdict.
No package installation, source editing, device erase, phone install or upload.
"""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import zipfile

import native_evidence as N

REFERENCE_SCHEMA = "brother-mobile-reference-v1"
PROFILE_SCHEMA = "brother-mobile-profile-v1"
RUN_SCHEMA = "brother-mobile-run-v1"
SHA = re.compile(r"^[a-f0-9]{40}$")
UUID = re.compile(r"^[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}$")


class Refusal(ValueError):
    def __init__(self, message, status="FAIL"):
        super().__init__(message)
        self.status = status


def require(condition, message, status="FAIL"):
    if not condition:
        raise Refusal(message, status)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refusal("Cannot read JSON: %s" % exc) from exc


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as fh:
        json.dump(value, fh, indent=2, sort_keys=True)
        fh.write("\n")


def digest(path):
    record = N.hash_file(str(Path(path).resolve()))
    require(not record.get("error"), "Invalid evidence file: %s" % path)
    return record


def check_digest(record):
    require(N.valid_file_hash(record), "Malformed evidence hash")
    require(N.hash_file(record["path"]) == record, "Evidence changed: %s" % record["path"])


def outside(path, repo):
    return not Path(path).resolve().is_relative_to(Path(repo).resolve())


def clean_snapshot(repo):
    snapshot = N.repo_snapshot(str(Path(repo).resolve()))
    require(N.valid_snapshot(snapshot), "Source state is unavailable")
    require(not snapshot["dirty"], "A clean committed candidate is required")
    return snapshot


def identity(app, source_key=None):
    app = Path(app)
    require(app.is_dir() and not app.is_symlink(), "App bundle is missing or linked")
    info_path = app / "Info.plist"
    require(info_path.is_file() and not info_path.is_symlink(), "App Info.plist is missing or linked")
    try:
        info = plistlib.loads(info_path.read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        raise Refusal("Invalid app Info.plist") from exc
    require(isinstance(info, dict), "App Info.plist is not a dictionary")
    keys = ("CFBundleIdentifier", "CFBundleShortVersionString", "CFBundleVersion", "CFBundleExecutable")
    require(all(isinstance(info.get(k), str) and info[k].strip() for k in keys), "Incomplete built app identity")
    executable = info["CFBundleExecutable"]
    require(Path(executable).name == executable and executable not in (".", ".."), "Executable escapes app bundle")
    require(not (app / executable).is_symlink(), "App executable is linked")
    result = {"bundle_id": info[keys[0]], "version": info[keys[1]], "build": info[keys[2]],
              "app": str(app.resolve()), "plist": digest(info_path), "executable": digest(app / executable)}
    if source_key:
        result["source_stamp"] = info.get(source_key)
    return result


def app_observation(value, bundle_id):
    matches = []
    def walk(node):
        if isinstance(node, dict):
            if node.get("bundleIdentifier") == bundle_id:
                matches.append(node)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(value)
    require(len(matches) == 1, "Device observation must contain exactly one matching app")
    match = matches[0]
    require(all(isinstance(match.get(k), str) and match[k] for k in ("version", "bundleVersion")),
            "Device observation lacks version or build")
    return {"bundle_id": bundle_id, "version": match["version"], "build": match["bundleVersion"]}


def same_identity(left, right):
    return all(left.get(k) == right.get(k) for k in ("bundle_id", "version", "build"))


def record_reference(repo, app, observation, mapping, source_revision, out):
    require(outside(out, repo), "Reference evidence must stay outside the source repository")
    snapshot = clean_snapshot(repo)
    require(isinstance(source_revision, str) and SHA.fullmatch(source_revision), "Reference needs a full source revision")
    require(snapshot["revision"] == source_revision, "Reference checkout is not the recorded release source")
    built = identity(app)
    observed = app_observation(read_json(observation), built["bundle_id"])
    require(same_identity(built, observed), "Reference app and observed device version/build differ")
    value = {"schema": REFERENCE_SCHEMA, "status": "PASS", "base_revision": source_revision,
             "source_snapshot": snapshot, "identity": built, "observed_identity": observed,
             "observation": digest(observation), "mapping": digest(mapping),
             "association": "recorded-release-mapping",
             "limits": ["The supplied release record associates source and binary; it is not an embedded Git attestation.",
                        "File integrity and matching version fields do not establish physical or human acceptance."]}
    write_new(out, value)
    return value


def verify_reference(path, repo):
    ref = read_json(path)
    require(isinstance(ref, dict) and ref.get("schema") == REFERENCE_SCHEMA, "Malformed reference schema")
    require(ref.get("status") == "PASS", "Reference was not technically verified")
    base = ref.get("base_revision")
    require(isinstance(base, str) and SHA.fullmatch(base), "Malformed reference source revision")
    recorded = ref.get("source_snapshot")
    require(N.valid_snapshot(recorded) and not recorded["dirty"] and recorded["revision"] == base,
            "Reference source snapshot contradicts its base")
    require(ref.get("association") == "recorded-release-mapping", "Reference association must remain explicit")
    require(isinstance(ref.get("limits"), list) and len(ref["limits"]) >= 2, "Reference limits are missing")
    for field in ("observation", "mapping"):
        check_digest(ref.get(field))
    built = ref.get("identity")
    require(isinstance(built, dict) and isinstance(built.get("app"), str), "Malformed reference app")
    actual = identity(built["app"])
    require(actual == built, "Reference app identity or executable changed")
    observed = app_observation(read_json(ref["observation"]["path"]), built["bundle_id"])
    require(observed == ref.get("observed_identity") and same_identity(built, observed), "Reference observation contradicts the app")
    current = clean_snapshot(repo)
    output, error = N.git_call(str(repo), ["merge-base", "--is-ancestor", base, current["revision"]])
    require(error is None, "Candidate does not descend from the working reference source")
    return ref, current


def validate_profile(profile, repo):
    require(isinstance(profile, dict) and profile.get("schema") == PROFILE_SCHEMA, "Malformed mobile profile")
    required = ("project", "scheme", "target", "configuration", "simulator_id", "bundle_id", "version", "build")
    require(all(isinstance(profile.get(k), str) and profile[k].strip() for k in required), "Incomplete mobile profile")
    project = Path(profile["project"])
    require(not project.is_absolute() and ".." not in project.parts, "Project must be inside the repository")
    require(project.suffix in (".xcodeproj", ".xcworkspace"), "Expected an Xcode project or workspace")
    require((Path(repo) / project).resolve().is_relative_to(Path(repo).resolve()), "Project resolves outside repository")
    require((Path(repo) / project).is_dir(), "Profile project does not exist")
    require(UUID.fullmatch(profile["simulator_id"]), "An exact simulator UUID is required")
    tests = profile.get("expected_tests")
    require(isinstance(tests, list) and bool(tests) and all(isinstance(t, str) and t.startswith("test://") for t in tests)
            and len(tests) == len(set(tests)), "Expected tests must be unique exact test identities")
    filters = profile.get("only_testing", [])
    require(isinstance(filters, list) and all(isinstance(t, str) and re.fullmatch(r"[A-Za-z0-9_./()]+", t) for t in filters), "Malformed test filters")
    environment = profile.get("environment", {})
    require(isinstance(environment, dict) and all(isinstance(k, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)
            and isinstance(v, str) for k, v in environment.items()), "Malformed environment")
    require(not any(k in environment for k in ("PATH", "HOME", "CODEX_HOME", "GIT_DIR", "GIT_WORK_TREE", "DEVELOPER_DIR")), "Profile cannot redirect tool or source identity")
    timeout = profile.get("timeout_seconds", 1800)
    require(type(timeout) is int and 1 <= timeout <= 7200, "Invalid command timeout")
    wrapper = profile.get("build_wrapper")
    if wrapper is not None:
        require(isinstance(wrapper, str), "Build wrapper must be a repository-relative executable")
        w = Path(wrapper)
        require(not w.is_absolute() and ".." not in w.parts and (Path(repo) / w).resolve().is_relative_to(Path(repo).resolve()), "Build wrapper escapes repository")
        require((Path(repo) / w).is_file() and os.access(Path(repo) / w, os.X_OK), "Build wrapper is not executable")
    stamp = profile.get("source_stamp")
    if stamp is not None:
        require(isinstance(stamp, dict) and all(isinstance(stamp.get(k), str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", stamp[k])
                for k in ("build_setting", "plist_key")), "Malformed source stamp")
    return profile


@contextlib.contextmanager
def lease():
    path = Path(tempfile.gettempdir()) / ("brother-mobile-%s.lock" % os.getuid())
    with path.open("a+") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Refusal("Another Brother mobile run owns the native tool lease", "NO-DATA") from exc
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def invoke(argv, cwd, root, stages, name, timeout=120, env=None, allowed_codes=(0,)):
    log = root / ("%02d-%s.log" % (len(stages) + 1, name))
    start = time.monotonic()
    entry = {"name": name, "argv": argv, "exit_code": None, "status": "FAIL"}
    stages.append(entry)
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=timeout, env=env)
        entry["exit_code"] = proc.returncode
        log.write_bytes(proc.stdout + proc.stderr)
        # Successful commands with no output still need a nonempty command record.
        if not log.stat().st_size:
            log.write_text("Command exited %s without output.\n" % proc.returncode)
        entry["log"] = digest(log)
        require(proc.returncode in allowed_codes, "%s exited %s" % (name, proc.returncode))
        entry["status"] = "PASS"
        return proc.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.write_text(str(exc) + "\n")
        entry["log"] = digest(log)
        raise Refusal("%s unavailable: %s" % (name, exc), "NO-DATA") from exc
    finally:
        entry["seconds"] = round(time.monotonic() - start, 3)


def json_output(value, label):
    try:
        return json.loads(value)
    except ValueError as exc:
        raise Refusal("%s returned malformed JSON" % label) from exc


def screenshot_identity(path):
    data = Path(path).read_bytes()
    require(len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR", "Screenshot is not a PNG")
    width, height = struct.unpack(">II", data[16:24])
    require(width > 0 and height > 0, "Screenshot has no pixel dimensions")
    return {**digest(path), "width": width, "height": height, "visual_quality": "NO-DATA"}


def build_arguments(profile, revision):
    selector = "-workspace" if profile["project"].endswith(".xcworkspace") else "-project"
    args = [selector, profile["project"], "-scheme", profile["scheme"], "-configuration", profile["configuration"],
            "-destination", "platform=iOS Simulator,id=" + profile["simulator_id"],
            "CURRENT_PROJECT_VERSION=" + profile["build"], "MARKETING_VERSION=" + profile["version"], "ONLY_ACTIVE_ARCH=YES"]
    if profile.get("source_stamp"):
        args.append(profile["source_stamp"]["build_setting"] + "=" + revision)
    return args


def run_workflow(repo, profile_path, reference_path, out):
    repo, out = Path(repo).resolve(), Path(out).resolve()
    require(outside(out, repo), "Run artifacts must stay outside the repository")
    require(not out.exists(), "Run output already exists; preserve it and use a fresh path")
    profile = validate_profile(read_json(profile_path), repo)
    ref, before = verify_reference(reference_path, repo)
    require(profile["bundle_id"] == ref["identity"]["bundle_id"], "Candidate bundle ID differs from the working reference")
    with lease():
        out.mkdir(parents=True)
        stages = []
        report = {"schema": RUN_SCHEMA, "technical_status": "FAIL", "stages": stages,
                  "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  "source_before": before, "reference": digest(reference_path), "reference_revision": ref["base_revision"],
                  "profile": digest(profile_path), "profile_summary": {k: v for k, v in profile.items() if k != "environment"},
                  "environment_keys": sorted(profile.get("environment", {})),
                  "observations": {"visual_quality": "NO-DATA", "physical_device_feel": "NO-DATA", "human_acceptance": "NO-DATA"},
                  "limits": ["Technical execution and artifact integrity are checked; screenshot content and physical feel require observation.",
                             "Reference ancestry prevents the wrong base; it does not prove every reference behavior was retained.",
                             "No phone installation or store upload was performed."]}
        receipt = out / "receipt" / "receipt.json"
        write_new(out / "profile.json", report["profile_summary"])
        write_new(out / "reference.json", ref)
        env = dict(os.environ, **profile.get("environment", {}))
        def call(*args, **kwargs):
            kwargs.setdefault("env", env)
            return invoke(*args, **kwargs)
        try:
            call(["xcodebuild", "-version"], repo, out, stages, "toolchain")
            raw_devices = call(["xcrun", "simctl", "list", "devices", "available", "-j"], repo, out, stages, "simulators")
            devices = json_output(raw_devices, "simctl")
            require(isinstance(devices, dict) and isinstance(devices.get("devices"), dict), "Malformed simulator list")
            found = [(runtime, d) for runtime, group in devices["devices"].items() if isinstance(group, list)
                     for d in group if isinstance(d, dict) and d.get("udid") == profile["simulator_id"]]
            require(len(found) == 1, "Selected simulator is unavailable", "NO-DATA")
            runtime, device = found[0]
            report["simulator"] = {"runtime": runtime, **device}
            if device.get("state") != "Booted":
                call(["xcrun", "simctl", "boot", profile["simulator_id"]], repo, out, stages, "boot")
            call(["xcrun", "simctl", "bootstatus", profile["simulator_id"], "-b"], repo, out, stages, "boot-ready", timeout=240)
            args = build_arguments(profile, before["revision"])
            command = [str(repo / profile["build_wrapper"])] if profile.get("build_wrapper") else ["xcodebuild"]
            command += ["test"] + args + ["-parallel-testing-enabled", "NO", "-resultBundlePath", str(out / "tests.xcresult")]
            command += ["-only-testing:" + value for value in profile.get("only_testing", [])]
            native_args = argparse.Namespace(repo=str(repo), out=str(out / "native-evidence.json"),
                result_bundle=str(out / "tests.xcresult"), command=command, requirement=[], artifact=[],
                log=str(out / "test.log"), tests_json=str(out / "tests.json"), expected_test=profile["expected_tests"],
                xcrun="xcrun", timeout=profile.get("timeout_seconds", 1800))
            old_environment = dict(os.environ)
            try:
                os.environ.update(profile.get("environment", {}))
                verdict, detail = N.record(native_args)
            finally:
                os.environ.clear()
                os.environ.update(old_environment)
            report["native_evidence"] = digest(out / "native-evidence.json")
            stages.append({"name": "native-tests", "status": verdict, "detail": detail, "argv": command,
                           "exit_code": read_json(out / "native-evidence.json")["command"].get("exit_code"), "log": digest(out / "test.log")})
            require(verdict == "PASS", "Native evidence did not pass: " + "; ".join(detail), verdict)
            settings = json_output(call(["xcodebuild"] + args + ["-showBuildSettings", "-json"], repo, out, stages, "product-path", timeout=180), "build settings")
            require(isinstance(settings, list), "Build settings must be an array")
            matches = [item.get("buildSettings") for item in settings if isinstance(item, dict) and item.get("target") == profile["target"]]
            require(len(matches) == 1 and isinstance(matches[0], dict), "Built product target is ambiguous or missing")
            values = matches[0]
            require(all(isinstance(values.get(k), str) and values[k] for k in ("TARGET_BUILD_DIR", "FULL_PRODUCT_NAME")), "Built product path is incomplete")
            product_name = values["FULL_PRODUCT_NAME"]
            require(Path(product_name).name == product_name and product_name.endswith(".app"), "Target product is not an app")
            app = Path(values["TARGET_BUILD_DIR"]) / product_name
            stamp = profile.get("source_stamp")
            built = identity(app, stamp["plist_key"] if stamp else None)
            require(same_identity(built, profile), "Built bundle/version/build contradicts the profile")
            if stamp:
                require(built.get("source_stamp") == before["revision"], "Compiled source stamp does not match the candidate")
            else:
                report["observations"]["embedded_source_stamp"] = "NO-DATA"
            report["built_app"] = built
            call(["xcrun", "simctl", "install", profile["simulator_id"], str(app)], repo, out, stages, "install", timeout=180)
            installed_path = call(["xcrun", "simctl", "get_app_container", profile["simulator_id"], profile["bundle_id"], "app"], repo, out, stages, "installed-path").strip()
            installed = identity(installed_path, stamp["plist_key"] if stamp else None)
            require(same_identity(built, installed) and built["executable"]["sha256"] == installed["executable"]["sha256"]
                    and built["plist"]["sha256"] == installed["plist"]["sha256"], "Installed app differs from the tested build")
            report["installed_app"] = installed
            call(["xcrun", "simctl", "launch", "--terminate-running-process", profile["simulator_id"], profile["bundle_id"]], repo, out, stages, "launch", env=env)
            call(["xcrun", "simctl", "io", profile["simulator_id"], "screenshot", str(out / "preview.png")], repo, out, stages, "capture")
            report["screenshot"] = screenshot_identity(out / "preview.png")
            after = clean_snapshot(repo)
            report["source_after"] = after
            require(N.same_snapshot(before, after), "Source changed during mobile workflow")
            check_digest(report["reference"])
            check_digest(report["profile"])
            report["technical_status"] = "PASS"
        except (Refusal, OSError, ValueError) as exc:
            report["technical_status"] = getattr(exc, "status", "FAIL")
            report["reason"] = str(exc)
        finally:
            write_new(receipt, report)
        return report, receipt


def doctor(repo, out):
    require(outside(out, repo), "Doctor output must stay outside the repository")
    require(not Path(out).exists(), "Doctor output already exists")
    result = {"schema": "brother-mobile-doctor-v1", "tools": {}, "projects": [], "source": N.repo_snapshot(str(repo)),
              "free_bytes": shutil.disk_usage(repo).free,
              "limits": ["Executable presence is not MCP connectivity, signing readiness or successful native compilation."]}
    for name, command in (("xcodebuild", ["xcodebuild", "-version"]), ("simctl", ["xcrun", "--find", "simctl"]),
                          ("xcresulttool", ["xcrun", "--find", "xcresulttool"]), ("xcode_mcp", ["xcrun", "--find", "mcpbridge"])):
        try:
            p = subprocess.run(command, capture_output=True, text=True, timeout=30)
            result["tools"][name] = {"status": "PASS" if p.returncode == 0 else "NO-DATA", "command": command,
                                     "exit_code": p.returncode, "detail": (p.stdout + p.stderr).strip()}
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["tools"][name] = {"status": "NO-DATA", "command": command, "detail": str(exc)}
    for root, dirs, _files in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in (".git", "node_modules", "build", ".build", "DerivedData")
                         and not (Path(root) / d).is_symlink())
        for name in list(dirs):
            if name.endswith((".xcodeproj", ".xcworkspace")):
                result["projects"].append(str((Path(root) / name).relative_to(repo)))
                dirs.remove(name)
        if len(Path(root).relative_to(repo).parts) >= 5:
            dirs[:] = []
    result["status"] = "PASS" if all(result["tools"][k]["status"] == "PASS" for k in ("xcodebuild", "simctl", "xcresulttool")) else "NO-DATA"
    write_new(out, result)
    return result


def pack(run_dir, output):
    root, output = Path(run_dir).resolve(), Path(output).resolve()
    require(not output.exists() and not output.is_relative_to(root), "Handoff output must be new and outside the run")
    receipt = read_json(root / "receipt" / "receipt.json")
    require(isinstance(receipt, dict) and receipt.get("schema") == RUN_SCHEMA, "Not a mobile run")
    entries = {}
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), "Handoff contains a symlink")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            require(relative != "MANIFEST.json", "Reserved handoff manifest name")
            hashed = N.hash_file(str(path), require_nonempty=False)
            require(not hashed.get("error"), "Cannot hash handoff file: " + relative)
            entries[relative] = hashed["sha256"]
    require(bool(entries), "Handoff is empty")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in entries:
            archive.write(root / relative, relative)
        archive.writestr("MANIFEST.json", json.dumps({"schema": "brother-mobile-handoff-v1", "files": entries,
            "source_revision": receipt.get("source_before", {}).get("revision"), "technical_status": receipt.get("technical_status"),
            "limits": ["Archive hashes prove internal integrity, not authorship or trustworthiness of the source records.",
                       "Original receipts retain their original local paths. Revalidate execution by checking out the source and rerunning the profile."]}, indent=2))
    return verify_pack(output)


def verify_pack(path):
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            require(len(names) == len(set(names)), "Duplicate handoff member")
            for info in archive.infolist():
                name = info.filename
                require(name and not name.startswith("/") and "\\" not in name and ".." not in PurePosixPath(name).parts,
                        "Unsafe handoff member")
                require(not stat.S_ISLNK(info.external_attr >> 16), "Linked handoff member")
            manifest = json.loads(archive.read("MANIFEST.json"))
            require(isinstance(manifest, dict) and manifest.get("schema") == "brother-mobile-handoff-v1", "Malformed handoff manifest")
            files = manifest.get("files")
            require(isinstance(files, dict) and bool(files) and set(names) == set(files) | {"MANIFEST.json"}, "Handoff inventory differs")
            for name, expected in files.items():
                require(N.is_sha256(expected) and hashlib.sha256(archive.read(name)).hexdigest() == expected, "Handoff content differs: %s" % name)
            require("receipt/receipt.json" in files, "Handoff receipt missing")
            return {"status": "PASS", "files": len(files), "archive": digest(path), "source_revision": manifest.get("source_revision"),
                    "technical_status": manifest.get("technical_status"), "limit": "Archive integrity only; execution and human acceptance are separate."}
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        if isinstance(exc, Refusal):
            raise
        raise Refusal("Invalid handoff: %s" % exc) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="action", required=True)
    d = sub.add_parser("doctor")
    d.add_argument("--repo", required=True); d.add_argument("--out", required=True)
    r = sub.add_parser("reference")
    for key in ("repo", "app", "observation", "mapping", "source-revision", "out"):
        r.add_argument("--" + key, required=True)
    v = sub.add_parser("check-reference")
    v.add_argument("--repo", required=True); v.add_argument("--reference", required=True)
    w = sub.add_parser("run")
    for key in ("repo", "profile", "reference", "out"):
        w.add_argument("--" + key, required=True)
    p = sub.add_parser("pack")
    p.add_argument("--run", required=True); p.add_argument("--out", required=True)
    z = sub.add_parser("verify-pack")
    z.add_argument("--archive", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "doctor":
            result = doctor(Path(args.repo).resolve(), args.out)
        elif args.action == "reference":
            result = record_reference(args.repo, args.app, args.observation, args.mapping, args.source_revision, args.out)
        elif args.action == "check-reference":
            ref, current = verify_reference(args.reference, args.repo)
            result = {"status": "PASS", "reference": ref["base_revision"], "candidate": current["revision"], "limit": "Ancestry and evidence integrity, not behavioral equivalence."}
        elif args.action == "run":
            result, receipt = run_workflow(args.repo, args.profile, args.reference, args.out)
            print("mobile_workflow: %s: %s" % (result["technical_status"], result.get("reason", "Tests, installed identity and capture verified; human acceptance is NO-DATA.")))
            print(receipt)
            return {"PASS": 0, "FAIL": 1, "NO-DATA": 2}[result["technical_status"]]
        elif args.action == "pack":
            result = pack(args.run, args.out)
        else:
            result = verify_pack(args.archive)
        print(json.dumps(result, indent=2))
        return {"PASS": 0, "FAIL": 1, "NO-DATA": 2}.get(result.get("status"), 1)
    except (Refusal, OSError, ValueError) as exc:
        status = getattr(exc, "status", "FAIL")
        print("mobile_workflow: %s: %s" % (status, exc))
        return 2 if status == "NO-DATA" else 1


if __name__ == "__main__":
    sys.exit(main())
