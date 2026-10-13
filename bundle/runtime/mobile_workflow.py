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

import mobile_project_profile as MP
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


@contextlib.contextmanager
def simulator_lease(udid, ttl_seconds):
    """Hold the named simulator's machine-wide device lease (the store
    other device tools share) for the run's working window. Where that
    store is not installed the receipt says NO-DATA and the per user flock
    in lease() still serializes Brother runs. A held, foreign-quarantined
    or corrupt lease is a NO-DATA refusal, never a silent pass."""
    try:
        import mobile_simulator_pool as P
    except ImportError:
        yield {"state": "NO-DATA", "reason": "device lease store not installed; the per user native lease still serializes Brother runs"}
        return
    session = "run-%d-%d" % (int(time.time()), os.getpid())
    try:
        with P.hold_device(udid, "mobile_workflow", session, ttl_seconds) as handle:
            yield {"state": "HELD", "device_id": handle.device_id, "lease_uuid": handle.lease_uuid,
                   "warm_reused": handle.warm_reused}
    except (P.SimulatorPoolError, P.L.LeaseError, P.L.sqlite3.Error) as exc:
        raise Refusal("Simulator device lease unavailable: %s" % exc, "NO-DATA") from exc


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


def listed_device(devices, udid):
    """The (runtime, device) pair simctl lists for exactly this udid."""
    require(isinstance(devices, dict) and isinstance(devices.get("devices"), dict), "Malformed simulator list")
    found = [(runtime, d) for runtime, group in devices["devices"].items() if isinstance(group, list)
             for d in group if isinstance(d, dict) and d.get("udid") == udid]
    require(len(found) == 1, "Selected simulator is unavailable", "NO-DATA")
    return found[0]


def product_path(settings, target):
    """The built .app path -showBuildSettings -json reports for the profile's target."""
    require(isinstance(settings, list), "Build settings must be an array")
    matches = [item.get("buildSettings") for item in settings if isinstance(item, dict) and item.get("target") == target]
    require(len(matches) == 1 and isinstance(matches[0], dict), "Built product target is ambiguous or missing")
    values = matches[0]
    require(all(isinstance(values.get(k), str) and values[k] for k in ("TARGET_BUILD_DIR", "FULL_PRODUCT_NAME")), "Built product path is incomplete")
    product_name = values["FULL_PRODUCT_NAME"]
    require(Path(product_name).name == product_name and product_name.endswith(".app"), "Target product is not an app")
    return Path(values["TARGET_BUILD_DIR"]) / product_name


def same_install(built, installed):
    """The installed copy is the built copy: identity fields and both hashes."""
    return (same_identity(built, installed) and built["executable"]["sha256"] == installed["executable"]["sha256"]
            and built["plist"]["sha256"] == installed["plist"]["sha256"])


def run_workflow(repo, profile_path, reference_path, out):
    repo, out = Path(repo).resolve(), Path(out).resolve()
    require(outside(out, repo), "Run artifacts must stay outside the repository")
    require(not out.exists(), "Run output already exists; preserve it and use a fresh path")
    profile = validate_profile(read_json(profile_path), repo)
    ref, before = verify_reference(reference_path, repo)
    require(profile["bundle_id"] == ref["identity"]["bundle_id"], "Candidate bundle ID differs from the working reference")
    with lease(), simulator_lease(profile["simulator_id"], 3 * profile.get("timeout_seconds", 1800) + 900) as held:
        out.mkdir(parents=True)
        stages = []
        report = {"schema": RUN_SCHEMA, "technical_status": "FAIL", "stages": stages, "simulator_lease": held,
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
            runtime, device = listed_device(json_output(raw_devices, "simctl"), profile["simulator_id"])
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
            app = product_path(settings, profile["target"])
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
            require(same_install(built, installed), "Installed app differs from the tested build")
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


def find_projects(repo):
    """The one project walk doctor and profile-init share: repository relative
    Xcode projects and workspaces, skipping build output, links and depth 5+."""
    repo = Path(repo).resolve()
    found = []
    for root, dirs, _files in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in (".git", "node_modules", "build", ".build", "DerivedData")
                         and not (Path(root) / d).is_symlink())
        for name in list(dirs):
            if name.endswith((".xcodeproj", ".xcworkspace")):
                found.append(str((Path(root) / name).relative_to(repo)))
                dirs.remove(name)
        if len(Path(root).relative_to(repo).parts) >= 5:
            dirs[:] = []
    return found


def read_tool(argv, cwd, label, timeout=180):
    """A read only tool call for the init commands: missing tool NO-DATA, nonzero FAIL."""
    try:
        proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Refusal("%s unavailable: %s" % (label, exc), "NO-DATA") from exc
    require(proc.returncode == 0, "%s exited %s: %s" % (label, proc.returncode, (proc.stderr or proc.stdout).strip()[:400]))
    return proc.stdout


def names(values, limit=8):
    values = list(values)
    return ", ".join(values[:limit]) + (" and %d more" % (len(values) - limit) if len(values) > limit else "")


def pick_simulator(devices, simulator_id=None, simulator_name=None):
    """Exactly one listed iOS simulator, never a guess."""
    require(isinstance(devices, dict) and isinstance(devices.get("devices"), dict), "Malformed simulator list")
    listed = [(runtime, d) for runtime, group in sorted(devices["devices"].items())
              if isinstance(group, list) and re.search(r"(?:^|[.])iOS-", runtime)
              for d in group if isinstance(d, dict) and isinstance(d.get("udid"), str) and UUID.fullmatch(d["udid"])]
    require(bool(listed), "no simulator is available for any installed iOS runtime", "NO-DATA")
    label = lambda pairs: names("%s (%s, %s)" % (d.get("name"), d["udid"], r) for r, d in pairs)
    if simulator_id is not None:
        require(isinstance(simulator_id, str) and UUID.fullmatch(simulator_id), "--simulator-id needs an exact simulator UUID")
        chosen = [(r, d) for r, d in listed if d["udid"].lower() == simulator_id.lower()]
        require(len(chosen) == 1, "simulator %s is not listed; available: %s" % (simulator_id, label(listed)), "NO-DATA")
    elif simulator_name is not None:
        chosen = [(r, d) for r, d in listed if d.get("name") == simulator_name]
        require(bool(chosen), "no simulator is named %r; available: %s" % (simulator_name, label(listed)), "NO-DATA")
        require(len(chosen) == 1, "%d simulators are named %r: %s; pass --simulator-id" % (len(chosen), simulator_name, label(chosen)), "NO-DATA")
    else:
        chosen = [(r, d) for r, d in listed if d.get("state") == "Booted"]
        require(len(chosen) == 1, "%s booted iOS simulator; candidates: %s; pass --simulator-id or --simulator-name"
                % ("no" if not chosen else "more than one", label(chosen or listed)), "NO-DATA")
    return chosen[0][1]["udid"]


def app_settings(settings):
    """The one app target row of -showBuildSettings -json, with its identity settings."""
    require(isinstance(settings, list), "Build settings must be an array")
    require(all(isinstance(item, dict) and isinstance(item.get("buildSettings"), dict) for item in settings),
            "Build settings array holds a non object")
    apps = [item for item in settings if str(item["buildSettings"].get("FULL_PRODUCT_NAME", "")).endswith(".app")]
    require(len(apps) == 1, "expected exactly one app target, found %d: %s" % (len(apps), names(str(i.get("target")) for i in apps)), "NO-DATA")
    target, values = apps[0].get("target"), apps[0]["buildSettings"]
    require(isinstance(target, str) and target.strip(), "the app target has no name", "NO-DATA")
    result = {"target": target}
    for key, setting in (("bundle_id", "PRODUCT_BUNDLE_IDENTIFIER"), ("version", "MARKETING_VERSION"), ("build", "CURRENT_PROJECT_VERSION")):
        value = values.get(setting)
        require(isinstance(value, str) and value.strip() and "$(" not in value and "${" not in value,
                "build setting %s of target %s is missing, empty or an unresolved variable" % (setting, target), "NO-DATA")
        result[key] = value
    return result


def profile_init(repo, out, *, project=None, scheme=None, configuration="Debug", simulator_id=None,
                 simulator_name=None, expected_tests=(), xcresult=None, only_testing=()):
    """Write a brother-mobile-profile-v1 run profile from the tools' own answers."""
    repo, out = Path(repo).resolve(), Path(out).resolve()
    require(outside(out, repo), "Profile output must stay outside the repository")
    require(not out.exists(), "Profile output already exists; use a new path")
    require(isinstance(configuration, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", configuration), "Malformed configuration name")
    projects = find_projects(repo)
    if project is not None:
        require(project in projects, "project %r is not an Xcode project under the repository; found: %s" % (project, names(projects) or "none"), "NO-DATA")
    else:
        require(bool(projects), "no Xcode project or workspace found under the repository", "NO-DATA")
        require(len(projects) == 1, "several Xcode projects found: %s; pass --project" % names(projects, 50), "NO-DATA")
        project = projects[0]
    try:
        seed = MP.to_run_profile_seed(MP.detect(str((repo / project).parent)), project)
    except ValueError as exc:
        raise Refusal(str(exc), "NO-DATA") from exc
    selector = "-workspace" if project.endswith(".xcworkspace") else "-project"
    listing = json_output(read_tool(["xcodebuild", "-list", "-json", selector, project], repo, "xcodebuild -list"), "xcodebuild -list")
    body = listing.get("workspace", listing.get("project")) if isinstance(listing, dict) else None
    require(isinstance(body, dict) and isinstance(body.get("schemes", []), list), "xcodebuild -list has no scheme list")
    schemes = [s for s in body.get("schemes", []) if isinstance(s, str)]
    if scheme is not None:
        require(scheme in schemes, "scheme %r is not listed; schemes: %s" % (scheme, names(schemes) or "none"), "NO-DATA")
    else:
        require(bool(schemes), "the project lists no scheme", "NO-DATA")
        require(len(schemes) == 1, "several schemes: %s; pass --scheme" % names(schemes, 50), "NO-DATA")
        scheme = schemes[0]
    devices = json_output(read_tool(["xcrun", "simctl", "list", "devices", "available", "-j"], repo, "simctl list"), "simctl")
    udid = pick_simulator(devices, simulator_id, simulator_name)
    settings = json_output(read_tool(["xcodebuild", selector, project, "-scheme", scheme, "-configuration", configuration,
                                      "-destination", "platform=iOS Simulator,id=" + udid, "-showBuildSettings", "-json"],
                                     repo, "xcodebuild -showBuildSettings"), "build settings")
    product = app_settings(settings)
    tests = list(expected_tests or ())
    if tests:
        require(not xcresult, "pass --expected-test or --xcresult, not both")
        require(all(isinstance(t, str) and t.startswith("test://") for t in tests), "every expected test must start test://")
        require(len(tests) == len(set(tests)), "expected tests must be unique")
    elif xcresult:
        doc, error = N.run_xcresulttool(str(xcresult))
        require(error is None, "cannot read the result bundle: %s" % error)
        try:
            leaves = N.test_leaves(doc)
        except ValueError as exc:
            raise Refusal("cannot read the result bundle: %s" % exc) from exc
        tests = sorted({leaf["identity"] for leaf in leaves if leaf["result"] == "Passed"})
        require(bool(tests), "the result bundle names no passed test", "NO-DATA")
    else:
        raise Refusal("expected tests: pass --expected-test test://... (one per test) or --xcresult <a result bundle "
                      "from one run of the project's tests>; profile-init does not run your tests and does not guess identities", "NO-DATA")
    profile = dict(seed, scheme=scheme, target=product["target"], configuration=configuration, simulator_id=udid,
                   bundle_id=product["bundle_id"], version=product["version"], build=product["build"], expected_tests=tests)
    if only_testing:
        profile["only_testing"] = list(only_testing)
    validate_profile(profile, repo)
    write_new(out, profile)
    return profile


REFERENCE_STATEMENT = ("This working reference is the source checkout itself, built from revision %s and installed on "
                       "simulator %s. It is not a store release, not a distributed binary and not a physical device install.")


def reference_init(repo, profile_path, out, *, source_revision=None):
    """Record the current clean checkout, built and installed on the profile's
    simulator, as the working reference, through the engine's record_reference."""
    repo, out = Path(repo).resolve(), Path(out).resolve()
    require(outside(out, repo), "Reference evidence must stay outside the source repository")
    side = lambda name: out.parent / ("%s-%s" % (out.stem, name))
    observation_path, mapping_path, stages_path, logs = (side("observation.json"), side("mapping.md"),
                                                          side("stages.json"), side("logs"))
    require(not any(p.exists() for p in (out, observation_path, mapping_path, stages_path, logs)),
            "Reference output already exists; preserve it and use a new path")
    profile = validate_profile(read_json(profile_path), repo)
    revision = clean_snapshot(repo)["revision"]
    if source_revision is not None:
        require(isinstance(source_revision, str) and SHA.fullmatch(source_revision), "Reference needs a full source revision")
        require(source_revision == revision, "--source-revision %s is not the checkout (%s)" % (source_revision, revision))
    env = dict(os.environ, **profile.get("environment", {}))
    stages = []
    record = {"schema": "brother-mobile-reference-init-v1", "status": "FAIL", "revision": revision, "stages": stages}
    with lease():
        logs.mkdir(parents=True)
        def call(argv, name, timeout=180):
            return invoke(argv, repo, logs, stages, name, timeout=timeout, env=env)
        try:
            udid, stamp = profile["simulator_id"], profile.get("source_stamp")
            _runtime, device = listed_device(json_output(call(["xcrun", "simctl", "list", "devices", "available", "-j"], "simulators"), "simctl"), udid)
            args = build_arguments(profile, revision)
            command = [str(repo / profile["build_wrapper"])] if profile.get("build_wrapper") else ["xcodebuild"]
            call(command + args + ["build"], "build", timeout=profile.get("timeout_seconds", 1800))
            settings = json_output(call(["xcodebuild"] + args + ["-showBuildSettings", "-json"], "product-path"), "build settings")
            app = product_path(settings, profile["target"])
            built = identity(app, stamp["plist_key"] if stamp else None)
            require(same_identity(built, profile), "Built bundle/version/build contradicts the profile")
            if stamp:
                require(built.get("source_stamp") == revision, "Compiled source stamp does not match the checkout")
            if device.get("state") != "Booted":
                call(["xcrun", "simctl", "boot", udid], "boot")
            call(["xcrun", "simctl", "bootstatus", udid, "-b"], "boot-ready", timeout=240)
            call(["xcrun", "simctl", "install", udid, str(app)], "install")
            installed_path = call(["xcrun", "simctl", "get_app_container", udid, profile["bundle_id"], "app"], "installed-path").strip()
            installed = identity(installed_path, stamp["plist_key"] if stamp else None)
            require(same_install(built, installed), "Installed app differs from the build")
            write_new(observation_path, {"result": {"apps": [{"bundleIdentifier": installed["bundle_id"], "version": installed["version"],
                                                              "bundleVersion": installed["build"]}]},
                                         "source": "simulator app container %s on simulator %s" % (installed["app"], udid)})
            with mapping_path.open("x", encoding="utf-8") as fh:
                fh.write("# Working reference\n\n" + REFERENCE_STATEMENT % (revision, udid) + "\n\n"
                         "- revision: %s\n- bundle id: %s\n- version: %s (%s)\n- simulator: %s\n"
                         % (revision, installed["bundle_id"], installed["version"], installed["build"], udid))
            ref = record_reference(repo, installed["app"], observation_path, mapping_path, revision, out)
            record["status"] = "PASS"
        except (Refusal, OSError, ValueError) as exc:
            record["status"], record["reason"] = getattr(exc, "status", "FAIL"), str(exc)
            kept = [str(p) for p in (observation_path, mapping_path) if p.exists()]
            raise Refusal(str(exc) + ("; kept, not deleted: " + ", ".join(kept) if kept else ""), record["status"]) from exc
        finally:
            write_new(stages_path, record)
    return ref


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
    result["projects"] = find_projects(repo)
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


def build_parser():
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
    e = sub.add_parser("reference-init", help="record the current clean checkout, built and installed, as the working reference")
    for key in ("repo", "profile", "out"):
        e.add_argument("--" + key, required=True)
    e.add_argument("--source-revision")
    i = sub.add_parser("profile-init", help="write a run profile from the project and the simulator list (read only tools)")
    i.add_argument("--repo", required=True); i.add_argument("--out", required=True)
    i.add_argument("--project"); i.add_argument("--scheme"); i.add_argument("--configuration", default="Debug")
    sim = i.add_mutually_exclusive_group()
    sim.add_argument("--simulator-id"); sim.add_argument("--simulator-name")
    i.add_argument("--expected-test", action="append", default=[])
    i.add_argument("--xcresult")
    i.add_argument("--only-testing", action="append", default=[])
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
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
        elif args.action == "reference-init":
            ref = reference_init(args.repo, args.profile, args.out, source_revision=args.source_revision)
            print("mobile_workflow: PASS: working reference recorded from the clean checkout at %s" % ref["base_revision"])
            print(Path(args.out).resolve())
            return 0
        elif args.action == "profile-init":
            profile_init(args.repo, args.out, project=args.project, scheme=args.scheme, configuration=args.configuration,
                         simulator_id=args.simulator_id, simulator_name=args.simulator_name, expected_tests=args.expected_test,
                         xcresult=args.xcresult, only_testing=args.only_testing)
            print("mobile_workflow: PASS: profile written")
            print(Path(args.out).resolve())
            return 0
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
