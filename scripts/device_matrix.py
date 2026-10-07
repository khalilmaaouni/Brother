#!/usr/bin/env python3
"""Device Matrix abstraction, physical-device adapter seam (WBS-30.07/30.08).

Represents execution environments as adapter contracts. 1.0.17 scope: the
interface plus simulator support (already covered by mobile_workflow.py's
own simctl calls) plus a real physical-device adapter for a locally attached
device. External device farm and future provider integration stay explicit
NO-DATA stubs -- no provider is configured, and inventing one here would be
a false source of truth for a capability nothing actually implements yet.

Never build a device cloud. Never infer real-device behavior from a
simulator PASS: this module reports DEVICE PRESENCE evidence only (a real
device is locally attached, paired, and its identity), never that an app
was installed, launched, or behaves correctly there -- mobile_workflow.py's
own "no phone install or upload" boundary stays intact.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid as _uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evidence_obligation import exit_code_for_verdict  # noqa: E402

ADAPTER_LOCAL = "local"
ADAPTER_FARM = "farm"
ADAPTER_PROVIDER = "provider"
ADAPTERS = (ADAPTER_LOCAL, ADAPTER_FARM, ADAPTER_PROVIDER)


def run(argv, timeout=45):
    """(stdout_text, error_or_None). Never raises; a missing tool or a
    timeout is a fact about the environment, not a crash."""
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if proc.returncode != 0:
        return None, "%s exited %s: %s" % (
            " ".join(argv), proc.returncode,
            proc.stderr.decode("utf-8", "replace").strip())
    return proc.stdout.decode("utf-8", "replace"), None


def parse_devicectl_table(text):
    """devicectl's own column-aligned table, not JSON (checked: no --json
    flag exists on `devicectl list devices` on this Xcode version). Parses
    by the header's own column start offsets so a value containing spaces
    (a device name) is not split apart."""
    lines = [l for l in text.splitlines() if l.strip()]
    header_idx = next((i for i, l in enumerate(lines) if l.startswith("Name")), None)
    if header_idx is None:
        return []
    header = lines[header_idx]
    cols = ["Name", "Hostname", "Identifier", "State", "Model"]
    present = [c for c in cols if c in header]
    starts = [header.index(c) for c in present]
    rows = []
    for line in lines[header_idx + 1:]:
        # The header separator is dashes AND spaces (column-width padding),
        # not a pure dash run -- stripping spaces first before the all-dash
        # check catches it; checking line.strip() alone did not.
        if not line.strip() or set(line.replace(" ", "")) <= {"-"}:
            continue
        values = []
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(line)
            values.append(line[start:end].strip())
        if len(values) == len(present):
            rows.append(dict(zip([c.lower() for c in present], values)))
    return rows


def local_physical_devices():
    """(verdict, evidence). PASS with a device list when devicectl finds at
    least one paired, available device; NO-DATA naming why otherwise. Uses
    `xcrun devicectl list devices` (Xcode's current device tool) rather than
    the older `xcrun xctrace list devices`, whose device state can lag --
    measured directly on this machine: xctrace read a real device "Offline"
    in the same second devicectl reported it "available (paired)"."""
    out, err = run(["xcrun", "devicectl", "list", "devices"])
    if out is None:
        return "NO-DATA", {"reason": "devicectl unavailable: %s" % err, "devices": []}
    devices = parse_devicectl_table(out)
    # Do not gate on one exact state string: measured directly on this
    # machine that devicectl's own reported state for the SAME device
    # changed between two calls a few seconds apart ("available (paired)"
    # then "connected"). This module's job is honest presence, not judging
    # which state string means "ready" -- the raw state is reported as
    # evidence for the caller to read, never filtered on a guessed
    # vocabulary this module was never shown to be exhaustive.
    attached = [d for d in devices if d.get("identifier")]
    if not attached:
        return "NO-DATA", {"reason": "devicectl reports no locally attached device",
                            "devices": devices}
    return "PASS", {"reason": "%d device(s) attached" % len(attached), "devices": attached}


def farm_adapter():
    """No external device-farm provider is configured. Named explicitly as
    NO-DATA rather than silently absent from a menu, per the plan's own
    'define adapter contracts... if no real device/provider is available:
    NO-DATA'."""
    return "NO-DATA", {"reason": "no external device-farm provider is configured"}


def provider_adapter():
    """Same shape as farm_adapter: a real future integration point, not yet
    wired to anything."""
    return "NO-DATA", {"reason": "no future-provider integration is configured"}


ADAPTER_FUNCS = {
    ADAPTER_LOCAL: local_physical_devices,
    ADAPTER_FARM: farm_adapter,
    ADAPTER_PROVIDER: provider_adapter,
}


def physical_device_evidence(adapter=ADAPTER_LOCAL):
    """The WBS-30.08 seam: one adapter contract, three named backends.
    Refuses an unknown adapter name rather than silently picking one."""
    if adapter not in ADAPTER_FUNCS:
        raise ValueError("unknown adapter %r, must be one of %s" % (adapter, ADAPTERS))
    verdict, evidence = ADAPTER_FUNCS[adapter]()
    return {
        "schema": "brother-physical-device-evidence-v1",
        "adapter": adapter,
        "verdict": verdict,
        "evidence": evidence,
        "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "limits": [
            "Presence and pairing only: never infers app install, launch, or behavior "
            "on the device from this record alone.",
            "Simulator PASS is a separate signal and must never be read as proof of "
            "real-device behavior.",
        ],
    }


#: -- M4.03: reservation-through-cleanup lifecycle for a local physical iOS
#: device --------------------------------------------------------------
#:
#: local_physical_devices() above only reports PRESENCE. This half is the
#: part the M4 unit decomposition doc (docs/plan/MOBILE-EPIC-M4-UNITS.md,
#: M4.03) names as missing: reserve a device through bm_device_lease.py's
#: real claim store (M4.01, products/brothermode/tools/bm_device_lease.py),
#: install the exact candidate app, verify its on-device identity actually
#: matches the candidate (never trust devicectl's own install exit code
#: alone), launch/observe/collect evidence, terminate and uninstall per
#: policy, and release the lease -- on EVERY exit path, including a
#: mid-lifecycle failure.
#:
#: WHAT WAS EXERCISED FOR REAL vs MOCKED, stated here once rather than
#: repeated per function: this unit was built against a real attached
#: iPhone (`xcrun devicectl list devices` found one paired and available
#: during development). `device info details`, `device info processes`,
#: and `device info apps` were each run for real against that device and
#: their JSON shapes below (info.outcome, result.runningProcesses,
#: result.apps with bundleIdentifier/version/bundleVersion/url) are copied
#: from those real responses. `device install app`, `device process
#: launch`, `device process terminate`, and `device uninstall app` were
#: NOT exercised for real: doing so would install and launch something on
#: the founder's own personal phone as a side effect of an unattended
#: background task, which this unit does not have standing consent for.
#: Their command syntax (argument names, flag order, --device/--pid shape)
#: is copied verbatim from `xcrun devicectl device <subcommand> --help`
#: (never guessed), and their JSON-parsing paths are covered by mocked
#: tests using devicectl's own real response shape as the fixture, the
#: same pattern test_device_matrix.py already uses for the discovery half
#: and test_mobile_workflow.py uses for fixture-tool tests. Their exact
#: on-success result-object shape is therefore UNVERIFIED for real; this
#: module never trusts it blindly regardless (verify_installed_identity()
#: below re-reads a fresh `device info apps` listing rather than trusting
#: install's own self-reported outcome, and find_running_pid() below
#: cross-references the two READS this unit did verify for real rather
#: than trusting launch's own self-reported pid).

DEFAULT_LEASE_TTL_SECONDS = 1800  # 30 minutes; matches mobile_workflow.py's own default run timeout

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BM_TOOLS = os.path.join(_ROOT, "products", "brothermode", "tools")


def _bm_device_lease_module():
    """(module, None) once products/brothermode/tools/bm_device_lease.py
    (M4.01) is importable, or (None, a NO-DATA reason) when it is not: a
    product moved, renamed, or a checkout missing M4.01 is a fact this
    lifecycle reports, never a crash. Mirrors receipt_door.py's own
    one-directional sys.path mount for a sibling product's tools/
    (products/brothersbe/tools there); same shape here for
    products/brothermode/tools."""
    if _BM_TOOLS not in sys.path:
        sys.path.insert(0, _BM_TOOLS)
    try:
        import bm_device_lease  # noqa: E402  (mounted onto sys.path just above)
    except ImportError as exc:
        return None, ("bm_device_lease unavailable: products/brothermode/tools/"
                       "bm_device_lease.py could not be imported (%s)" % exc)
    return bm_device_lease, None


def _mobile_workflow_module():
    """Same shape as _bm_device_lease_module, for mobile_workflow.identity()
    (Info.plist based bundle_id/version/build extraction): it lives in this
    same scripts/ directory, already on sys.path, so only the import can
    fail, never the mount."""
    try:
        import mobile_workflow
    except ImportError as exc:
        return None, "mobile_workflow unavailable: could not be imported (%s)" % exc
    return mobile_workflow, None


def _run_raw(argv, timeout):
    """Like run() above, but reports the actual exit code instead of
    collapsing every nonzero exit to (None, err): the lifecycle commands
    below often need to read devicectl's own JSON output on a NONZERO exit
    (a refused install, an already-terminated pid, etc is an expected,
    informative outcome here, not a tool-missing NO-DATA). Never raises."""
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"returncode": None, "stdout": "", "stderr": "", "exception": str(exc)}
    return {"returncode": proc.returncode,
            "stdout": proc.stdout.decode("utf-8", "replace"),
            "stderr": proc.stderr.decode("utf-8", "replace"), "exception": None}


def _devicectl_json(subcommand_args, timeout=60):
    """Run `xcrun devicectl <subcommand_args>` with a fresh --json-output
    file (devicectl's own documented "ONLY supported interface for
    scripts/programs to consume command output", per `devicectl --help`),
    parse it, and always clean the temp file up win or lose. Returns a
    dict: ok (bool), outcome (devicectl's own info.outcome, or None),
    result (devicectl's own result object, or None), returncode, stderr,
    reason (None, or a short NO-DATA/failure reason). Never raises."""
    fd, jpath = tempfile.mkstemp(prefix="devicectl-", suffix=".json")
    os.close(fd)
    os.remove(jpath)  # devicectl writes a fresh file; verified directly against a real device
    argv = ["xcrun", "devicectl"] + list(subcommand_args) + [
        "--json-output", jpath, "--timeout", str(timeout)]
    raw_run = _run_raw(argv, timeout=timeout + 15)
    try:
        if raw_run["exception"] is not None:
            return {"ok": False, "outcome": None, "result": None,
                    "returncode": None, "stderr": "",
                    "reason": "devicectl unavailable: %s" % raw_run["exception"]}
        if not os.path.isfile(jpath):
            return {"ok": False, "outcome": None, "result": None,
                    "returncode": raw_run["returncode"], "stderr": raw_run["stderr"],
                    "reason": "devicectl wrote no JSON output (exit %s): %s"
                              % (raw_run["returncode"], raw_run["stderr"].strip())}
        with open(jpath, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        return {"ok": False, "outcome": None, "result": None,
                "returncode": raw_run.get("returncode"), "stderr": raw_run.get("stderr", ""),
                "reason": "devicectl JSON output unreadable: %s" % exc}
    finally:
        try:
            os.remove(jpath)
        except OSError:  # ponytail: best-effort temp cleanup, never the verdict
            pass
    info = raw.get("info") if isinstance(raw, dict) else None
    outcome = info.get("outcome") if isinstance(info, dict) else None
    result = raw.get("result") if isinstance(raw, dict) else None
    ok = outcome == "success" and raw_run["returncode"] == 0
    reason = None if ok else ("devicectl reported outcome=%r (exit %s): %s"
                               % (outcome, raw_run["returncode"], raw_run["stderr"].strip()))
    return {"ok": ok, "outcome": outcome, "result": result,
            "returncode": raw_run["returncode"], "stderr": raw_run["stderr"], "reason": reason}


def _lifecycle_verdict(r):
    """A devicectl call that never ran at all (missing tool, timeout, no
    JSON written) is NO-DATA; one that ran and reported a real failure is
    FAIL. Keeps that distinction consistent across every function below."""
    return "NO-DATA" if r["returncode"] is None else "FAIL"


_VERDICT_RANK = {"PASS": 0, "NO-DATA": 1, "FAIL": 2}


def _verdict_from_stages(stages):
    """The record's verdict IS the worst verdict its stages actually
    recorded: any FAIL is FAIL, any NO-DATA with no FAIL is NO-DATA,
    otherwise PASS.

    This exists because the alternative does not work. Composing the
    verdict from a hand-picked subset of stages is how a run where the
    device was unplugged right after launch (both later stages FAIL)
    still reported PASS and exit 0: the stages were appended to the list
    faithfully, then nothing ever read them. Deriving from the list the
    function already builds means a stage cannot be added, or a check
    inserted, without the verdict hearing about it. An unrecognized
    verdict string counts as FAIL rather than being skipped, so a typo in
    a future stage fails loudly instead of silently voting PASS."""
    worst = "PASS"
    for stage in stages:
        v = stage.get("verdict")
        rank = _VERDICT_RANK.get(v, _VERDICT_RANK["FAIL"])
        if rank > _VERDICT_RANK[worst]:
            worst = v if v in _VERDICT_RANK else "FAIL"
    return worst


def get_device_details(device_id, timeout=30):
    """(verdict, evidence) via a real `devicectl device info details`
    read. Verified directly against a real attached device."""
    r = _devicectl_json(["device", "info", "details", "--device", device_id], timeout)
    if not r["ok"]:
        return _lifecycle_verdict(r), {"reason": r["reason"]}
    return "PASS", {"details": r["result"]}


def _shaped_inventory(result, key, required_identity_field):
    """(verdict, list) for an inventory field expected to be a list of
    dict records, each carrying `required_identity_field`.

    Found by review: `(result or {}).get(key) or []` collapses three
    different observations into the same empty list -- result is None,
    the key is missing or None, and a genuinely confirmed empty list --
    so a malformed or absent devicectl response read exactly like a clean
    device. verify_uninstalled then trusted that collapsed emptiness as
    proof of absence. A present, non-empty list carrying non-dict or
    identity-less entries is equally not proof of anything: not silently
    dropped, not silently accepted, refused as NO-DATA by name.

    Returns ("PASS", the list) only when `result` is a dict, `key` is
    present and is actually a list, and every entry is a dict carrying
    `required_identity_field`. An explicit empty list remains legitimate:
    PASS with []. Otherwise ("NO-DATA", []), naming which shape failed."""
    if not isinstance(result, dict):
        return "NO-DATA", []
    if key not in result:
        return "NO-DATA", []
    value = result[key]
    if not isinstance(value, list):
        return "NO-DATA", []
    for entry in value:
        if not isinstance(entry, dict) or not entry.get(required_identity_field):
            return "NO-DATA", []
    return "PASS", value


def list_processes(device_id, timeout=30):
    """(verdict, {"processes": [...]}) via a real `devicectl device info
    processes` read. Verified directly against a real attached device:
    result.runningProcesses, each {"executable": "file://...", "processIdentifier": N}."""
    r = _devicectl_json(["device", "info", "processes", "--device", device_id], timeout)
    if not r["ok"]:
        return _lifecycle_verdict(r), {"reason": r["reason"], "processes": []}
    verdict, processes = _shaped_inventory(r["result"], "runningProcesses", "processIdentifier")
    if verdict != "PASS":
        return verdict, {"reason": "devicectl reported success but its processes "
                                    "listing is missing or malformed",
                          "processes": []}
    return "PASS", {"processes": processes}


def list_installed_apps(device_id, timeout=30):
    """(verdict, {"apps": [...]}) via a real `devicectl device info apps`
    read. Verified directly against a real attached device: result.apps,
    each carrying bundleIdentifier/version/bundleVersion/name/url."""
    r = _devicectl_json(["device", "info", "apps", "--device", device_id], timeout)
    if not r["ok"]:
        return _lifecycle_verdict(r), {"reason": r["reason"], "apps": []}
    verdict, apps = _shaped_inventory(r["result"], "apps", "bundleIdentifier")
    if verdict != "PASS":
        return verdict, {"reason": "devicectl reported success but its apps "
                                    "listing is missing or malformed",
                          "apps": []}
    return "PASS", {"apps": apps}


def install_app(device_id, app_path, timeout=180):
    """`devicectl device install app --device <id> <path>`. Command syntax
    from `devicectl device install app --help`; NOT exercised against a
    real device this session (see the module-note above). A caller must
    treat a PASS here as "devicectl reported success", never as "the app
    is on the device" -- verify_installed_identity() below is the check
    that actually confirms that, from an independent read."""
    r = _devicectl_json(["device", "install", "app", "--device", device_id, str(app_path)], timeout)
    if not r["ok"]:
        return _lifecycle_verdict(r), {"reason": r["reason"]}
    return "PASS", {"install_result": r["result"]}


def uninstall_app(device_id, bundle_id, timeout=60):
    """`devicectl device uninstall app --device <id> <bundle-id>`. Command
    syntax from --help; not exercised against a real device this session."""
    r = _devicectl_json(["device", "uninstall", "app", "--device", device_id, bundle_id], timeout)
    if not r["ok"]:
        return _lifecycle_verdict(r), {"reason": r["reason"]}
    return "PASS", {"uninstall_result": r["result"]}


def launch_app(device_id, bundle_id, terminate_existing=True, timeout=60):
    """`devicectl device process launch --device <id> [--terminate-existing]
    <bundle-id>`. Command syntax from --help; not exercised against a real
    device this session. find_running_pid() below never trusts this call's
    own reported pid, it independently cross-references two reads this
    unit did verify for real."""
    args = ["device", "process", "launch", "--device", device_id]
    if terminate_existing:
        args.append("--terminate-existing")
    args.append(bundle_id)
    r = _devicectl_json(args, timeout)
    if not r["ok"]:
        return _lifecycle_verdict(r), {"reason": r["reason"]}
    return "PASS", {"launch_result": r["result"]}


def terminate_process(device_id, pid, kill=False, timeout=30):
    """`devicectl device process terminate --device <id> --pid <pid>
    [--kill]`. Command syntax from --help; not exercised against a real
    device this session."""
    args = ["device", "process", "terminate", "--device", device_id, "--pid", str(pid)]
    if kill:
        args.append("--kill")
    r = _devicectl_json(args, timeout)
    if not r["ok"]:
        return _lifecycle_verdict(r), {"reason": r["reason"]}
    return "PASS", {"terminate_result": r["result"]}


def verify_installed_identity(device_id, bundle_id, expected_version=None, expected_build=None, timeout=30):
    """PASS only when a FRESH, real `devicectl device info apps` read (never
    install_app()'s own self-reported success) lists bundle_id, and, when
    given, expected_version/expected_build match that listing's own
    version/bundleVersion fields exactly. This is the check standing
    between "devicectl install exited 0" and "the candidate app is
    actually on the device" -- an install that returns success from
    devicectl but whose on-device identity does not match is FAIL here,
    never PASS (adversarial-review target: a device left
    installed-but-unverified reported as success)."""
    verdict, detail = list_installed_apps(device_id, timeout=timeout)
    if verdict != "PASS":
        return verdict, detail
    matches = [a for a in detail["apps"] if isinstance(a, dict) and a.get("bundleIdentifier") == bundle_id]
    if not matches:
        return "FAIL", {"reason": "bundle_id %r not found in the device's installed-apps listing" % bundle_id,
                         "apps_seen": [a.get("bundleIdentifier") for a in detail["apps"] if isinstance(a, dict)]}
    app = matches[0]
    mismatches = {}
    if expected_version is not None and app.get("version") != expected_version:
        mismatches["version"] = {"expected": expected_version, "found": app.get("version")}
    if expected_build is not None and app.get("bundleVersion") != expected_build:
        mismatches["build"] = {"expected": expected_build, "found": app.get("bundleVersion")}
    if mismatches:
        return "FAIL", {"reason": "installed app identity differs from the candidate", "mismatches": mismatches, "app": app}
    return "PASS", {"app": app}


def verify_uninstalled(device_id, bundle_id, timeout=30):
    """PASS only when a FRESH, real `devicectl device info apps` read
    shows bundle_id absent, never uninstall_app()'s own self-reported
    success.

    The module already applies "never trust devicectl's own exit code" to
    install (verify_installed_identity re-reads a fresh listing); this is
    the same rule applied to the step that decides whether the device is
    clean enough for the next claimant. Without it, uninstall_app could
    return PASS while the bundle was still listed on the device, and the
    device went back to the pool as available carrying it."""
    verdict, detail = list_installed_apps(device_id, timeout=timeout)
    if verdict != "PASS":
        return verdict, {"reason": detail.get("reason"),
                          "note": "could not read the device's apps listing, so "
                                  "absence of %r is unconfirmed" % bundle_id}
    still_there = [a for a in detail["apps"]
                   if isinstance(a, dict) and a.get("bundleIdentifier") == bundle_id]
    if still_there:
        return "FAIL", {"reason": "bundle_id %r is STILL listed on the device after "
                                   "uninstall reported success" % bundle_id,
                         "app": still_there[0]}
    return "PASS", {"absent": bundle_id}


def find_running_pid(device_id, bundle_id, timeout=30):
    """Cross-references a fresh `devicectl device info apps` read (for
    bundle_id's own on-device install url) against a fresh `devicectl
    device info processes` read (each running process' own executable
    url), matching by path prefix -- independent of launch_app()'s own
    JSON shape, which this unit could not verify against a real launch.
    Both reads this depends on WERE verified for real."""
    apps_verdict, apps_detail = list_installed_apps(device_id, timeout=timeout)
    if apps_verdict != "PASS":
        return apps_verdict, {"reason": apps_detail.get("reason"), "pid": None}
    matches = [a for a in apps_detail["apps"] if isinstance(a, dict) and a.get("bundleIdentifier") == bundle_id]
    if not matches or not matches[0].get("url"):
        return "NO-DATA", {"reason": "bundle_id %r has no installed url to match a process against" % bundle_id, "pid": None}
    app_url = matches[0]["url"]
    # Match on a PATH BOUNDARY, never a bare string prefix: a sibling
    # bundle whose url merely extends this one's (".../Demo.app" against
    # ".../Demo.app2/") would otherwise match, and the caller terminates
    # the pid it gets back. Getting this wrong kills the wrong process on
    # a real phone, so the boundary is explicit.
    app_prefix = app_url if app_url.endswith("/") else app_url + "/"
    procs_verdict, procs_detail = list_processes(device_id, timeout=timeout)
    if procs_verdict != "PASS":
        return procs_verdict, {"reason": procs_detail.get("reason"), "pid": None}
    running = [p for p in procs_detail["processes"]
               if isinstance(p, dict) and isinstance(p.get("executable"), str)
               and (p["executable"] == app_url or p["executable"].startswith(app_prefix))]
    if not running:
        return "NO-DATA", {"reason": "no running process executable matched the installed app's own url", "pid": None}
    return "PASS", {"pid": running[0].get("processIdentifier"), "executable": running[0].get("executable")}


def _collect_evidence(device_id, out_dir, timeout=30):
    """Snapshot the device's own running-processes and installed-apps
    listings (both real devicectl reads) to out_dir when given, always
    also returned inline. NO-DATA on either read is reported, never
    silently dropped (adversarial-review target: an evidence claim with no
    real observation behind it)."""
    procs_verdict, procs_detail = list_processes(device_id, timeout=timeout)
    apps_verdict, apps_detail = list_installed_apps(device_id, timeout=timeout)
    evidence = {"processes": procs_detail, "processes_verdict": procs_verdict,
                "apps": apps_detail, "apps_verdict": apps_verdict}
    if out_dir:
        try:
            os.makedirs(out_dir, exist_ok=True)
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            path = os.path.join(out_dir, "device-evidence-%s-%s.json" % (device_id, stamp))
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(evidence, fh, indent=1)
            evidence["saved_to"] = path
        except OSError as exc:
            evidence["save_error"] = str(exc)
    if procs_verdict == "PASS" and apps_verdict == "PASS":
        return "PASS", evidence
    if procs_verdict == "FAIL" or apps_verdict == "FAIL":
        return "FAIL", evidence
    return "NO-DATA", evidence


def _best_effort_uninstall(device_id, bundle_id, stages):
    """Attempt to remove a partially-installed or unverifiable candidate
    before the lease is released or quarantined; returns None when the
    device is OBSERVED clean, or a reason string that becomes the lease's
    dirty_reason, so a device this run could not clean up is quarantined
    rather than handed to the next claimant still carrying today's
    candidate.

    The verdict comes from re-reading the device, not from devicectl's
    exit code, because that exit code cannot tell "cleanup genuinely
    failed" apart from "there was nothing to clean up": devicectl errors
    uninstalling an already-absent bundle the same way it errors on a
    real failure. Trusting it quarantined already-clean devices after a
    failed install, and released partially-installed ones on a lucky
    zero. The observed fact is the apps listing."""
    verdict, detail = uninstall_app(device_id, bundle_id)
    stages.append({"name": "cleanup-uninstall", "verdict": verdict, "detail": detail})
    confirm_verdict, confirm_detail = verify_uninstalled(device_id, bundle_id)
    stages.append({"name": "cleanup-verify-absent", "verdict": confirm_verdict,
                    "detail": confirm_detail})
    if confirm_verdict == "PASS":
        return None  # observed absent: clean, whatever devicectl claimed either way
    if confirm_verdict == "FAIL":
        return ("cleanup left the candidate on the device after an earlier "
                "lifecycle failure: %s" % confirm_detail.get("reason"))
    return ("cleanup could not be confirmed after an earlier lifecycle failure "
            "(device unreadable): %s" % confirm_detail.get("reason"))


def reserve_device(device_id, owner, session_id=None, ttl_seconds=DEFAULT_LEASE_TTL_SECONDS):
    """(verdict, detail) claiming device_id through bm_device_lease.py's
    real atomic claim() (M4.01). PASS with the lease info, or NO-DATA
    naming why (store unreachable, or the device is dirty/already leased --
    LeaseRefused's own reason is passed through, never re-guessed)."""
    dl_module, reason = _bm_device_lease_module()
    if dl_module is None:
        return "NO-DATA", {"reason": reason}
    session_id = session_id or _uuid.uuid4().hex
    store = dl_module.DeviceLeaseStore()
    try:
        try:
            return "PASS", store.claim(device_id, owner, session_id, ttl_seconds)
        except dl_module.LeaseRefused as exc:
            return "NO-DATA", {"reason": exc.reason, "message": str(exc)}
    finally:
        store.close()


def release_device(device_id, lease_uuid):
    """(verdict, detail) releasing a lease held by lease_uuid back to
    'available' through bm_device_lease.py's real release()."""
    dl_module, reason = _bm_device_lease_module()
    if dl_module is None:
        return "NO-DATA", {"reason": reason}
    store = dl_module.DeviceLeaseStore()
    try:
        try:
            return "PASS", store.release(device_id, lease_uuid)
        except dl_module.LeaseRefused as exc:
            return "NO-DATA", {"reason": exc.reason, "message": str(exc)}
    finally:
        store.close()


def run_physical_lifecycle(device_id, app_path, bundle_id, owner, session_id=None,
                            ttl_seconds=DEFAULT_LEASE_TTL_SECONDS, uninstall_after=True,
                            launch=True, out_dir=None, install_timeout=180,
                            launch_timeout=60, observe_seconds=0):
    """Reserve device_id through bm_device_lease.py's real claim API
    (M4.01), install app_path, verify its on-device identity actually
    matches the candidate, launch/observe/collect evidence, terminate and
    uninstall per policy, and release the lease -- on EVERY exit path,
    including a mid-lifecycle failure (adversarial-review target: a lease
    claimed but never released strands the device for every later
    claimant until its TTL happens to expire).

    Returns a brother-physical-device-lifecycle-v1 record. verdict is PASS
    only when install, identity verification, and (if requested) launch
    and cleanup all independently verified; devicectl reporting success by
    itself never sets PASS alone. On any failure after a successful
    install, this function attempts a best-effort uninstall before
    quarantining the lease dirty rather than releasing it clean, so a
    stranded candidate is never silently handed to the next claimant."""
    stages = []
    record = {
        "schema": "brother-physical-device-lifecycle-v1", "device_id": device_id,
        "bundle_id": bundle_id, "owner": owner, "verdict": "FAIL", "reason": None,
        "stages": stages, "lease": None,
        "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "limits": [
            "Every stage is a real devicectl invocation against this exact device_id; "
            "a stage this run could not exercise is recorded NO-DATA with its own "
            "reason, never silently skipped.",
            "Installed-identity verification gates the verdict: devicectl reporting "
            "install success is not by itself sufficient for PASS.",
            "install/launch/terminate/uninstall command syntax was taken from "
            "`devicectl --help`, not exercised against a real device by this unit "
            "(see the module-level note above); verify_installed_identity() and "
            "find_running_pid() independently re-check rather than trusting them.",
        ],
    }

    dl_module, dl_reason = _bm_device_lease_module()
    if dl_module is None:
        record["verdict"], record["reason"] = "NO-DATA", dl_reason
        stages.append({"name": "reserve", "verdict": "NO-DATA", "detail": {"reason": dl_reason}})
        return record

    session_id = session_id or _uuid.uuid4().hex
    store = dl_module.DeviceLeaseStore()
    lease_info = None
    cleanup_failed_reason = None
    residue_reason = None
    try:
        try:
            lease_info = store.claim(device_id, owner, session_id, ttl_seconds)
        except dl_module.LeaseRefused as exc:
            record["verdict"], record["reason"] = "NO-DATA", str(exc)
            stages.append({"name": "reserve", "verdict": "NO-DATA",
                            "detail": {"reason": exc.reason, "message": str(exc)}})
            return record
        record["lease"] = {"lease_uuid": lease_info["lease_uuid"], "expires_at": lease_info["expires_at"],
                            "recovered_from_stale": lease_info["recovered_from_stale"], "final_state": None}
        stages.append({"name": "reserve", "verdict": "PASS", "detail": lease_info})

        try:
            verdict, detail = get_device_details(device_id)
            stages.append({"name": "prepare", "verdict": verdict, "detail": detail})
            if verdict != "PASS":
                record["verdict"], record["reason"] = verdict, detail.get("reason")
                return record

            mw_module, mw_reason = _mobile_workflow_module()
            candidate = None
            if mw_module is None:
                stages.append({"name": "candidate-identity", "verdict": "NO-DATA", "detail": {"reason": mw_reason}})
            else:
                try:
                    candidate = mw_module.identity(app_path)
                    stages.append({"name": "candidate-identity", "verdict": "PASS",
                                    "detail": {k: candidate[k] for k in ("bundle_id", "version", "build")}})
                except mw_module.Refusal as exc:
                    stages.append({"name": "candidate-identity", "verdict": "FAIL", "detail": {"reason": str(exc)}})
                    record["verdict"], record["reason"] = "FAIL", str(exc)
                    return record
            if candidate is not None and candidate["bundle_id"] != bundle_id:
                reason = ("candidate app bundle id %r does not match requested bundle_id %r"
                          % (candidate["bundle_id"], bundle_id))
                stages.append({"name": "candidate-identity-check", "verdict": "FAIL", "detail": {"reason": reason}})
                record["verdict"], record["reason"] = "FAIL", reason
                return record

            verdict, detail = install_app(device_id, app_path, timeout=install_timeout)
            stages.append({"name": "install", "verdict": verdict, "detail": detail})
            if verdict != "PASS":
                record["verdict"], record["reason"] = verdict, detail.get("reason")
                cleanup_failed_reason = _best_effort_uninstall(device_id, bundle_id, stages)
                return record

            verdict, detail = verify_installed_identity(
                device_id, bundle_id, expected_version=candidate["version"] if candidate else None,
                expected_build=candidate["build"] if candidate else None)
            stages.append({"name": "verify-installed-identity", "verdict": verdict, "detail": detail})
            if verdict != "PASS":
                record["verdict"], record["reason"] = verdict, detail.get("reason")
                cleanup_failed_reason = _best_effort_uninstall(device_id, bundle_id, stages)
                return record

            pid = None
            if launch:
                verdict, detail = launch_app(device_id, bundle_id, timeout=launch_timeout)
                stages.append({"name": "launch", "verdict": verdict, "detail": detail})
                if verdict != "PASS":
                    record["verdict"], record["reason"] = verdict, detail.get("reason")
                    cleanup_failed_reason = _best_effort_uninstall(device_id, bundle_id, stages)
                    return record
                if observe_seconds > 0:
                    time.sleep(observe_seconds)
                verdict, detail = find_running_pid(device_id, bundle_id)
                stages.append({"name": "observe-process", "verdict": verdict, "detail": detail})
                pid = detail.get("pid")

            verdict, detail = _collect_evidence(device_id, out_dir)
            stages.append({"name": "collect-evidence", "verdict": verdict, "detail": detail})

            if launch:
                if pid is not None:
                    verdict, detail = terminate_process(device_id, pid)
                    stages.append({"name": "terminate", "verdict": verdict, "detail": detail})
                    if verdict != "PASS":
                        cleanup_failed_reason = "terminate failed for pid %s: %s" % (pid, detail.get("reason"))
                else:
                    stages.append({"name": "terminate", "verdict": "NO-DATA",
                                    "detail": {"reason": "no running pid observed to terminate"}})

            if uninstall_after:
                verdict, detail = uninstall_app(device_id, bundle_id)
                stages.append({"name": "uninstall", "verdict": verdict, "detail": detail})
                # devicectl's own success is not evidence the bundle is gone,
                # and this is the step that decides whether the device is
                # clean for the next claimant, so re-read it.
                verdict, detail = verify_uninstalled(device_id, bundle_id)
                stages.append({"name": "verify-uninstalled", "verdict": verdict, "detail": detail})
                if verdict != "PASS" and cleanup_failed_reason is None:
                    cleanup_failed_reason = detail.get("reason")
            else:
                # --keep-installed is a legitimate request, but it leaves
                # KNOWN residue: the candidate app, and possibly a live
                # process. Returning that device to the pool as available
                # tells the next claimant a clean device is waiting. It is
                # quarantined dirty naming the residue instead, and the
                # clear-dirty CLI is how it comes back.
                residue_reason = ("candidate %s deliberately left installed "
                                  "(--keep-installed); device carries known residue"
                                  % bundle_id)
                stages.append({"name": "keep-installed-residue", "verdict": "PASS",
                                "detail": {"quarantined": True, "reason": residue_reason}})

            if cleanup_failed_reason is None:
                record["reason"] = ("install and identity verification verified; "
                                    + ("candidate uninstalled and re-verified absent"
                                       if uninstall_after
                                       else "candidate deliberately left installed, "
                                            "device quarantined dirty"))
            else:
                record["reason"] = "lifecycle ran but cleanup was not verified: %s" % cleanup_failed_reason
            return record
        except dl_module.LeaseError:
            raise  # a raw lease-API misuse bug must surface, never be papered over as "dirty device"
        except Exception as exc:  # noqa: BLE001 -- last-resort net, see docstring: every exit path releases or quarantines
            cleanup_failed_reason = cleanup_failed_reason or ("unexpected error: %s" % exc)
            record["verdict"], record["reason"] = "FAIL", cleanup_failed_reason
            stages.append({"name": "unexpected-error", "verdict": "FAIL", "detail": {"reason": str(exc)}})
            return record
    finally:
        # Whatever happens to the lease is a STAGE, not a side field. It
        # used to be recorded only in lease.release_error, which nothing
        # read: a run whose TTL expired mid-lifecycle, letting another
        # session legitimately claim the same physical device while this
        # one kept driving it, still reported PASS and exit 0 with the
        # double-hold named in a field beside the verdict. Appending the
        # outcome here, before the verdict is derived below, is what puts
        # it in front of the caller.
        if lease_info is not None:
            quarantine_reason = cleanup_failed_reason or residue_reason
            try:
                if quarantine_reason:
                    # Fenced on THIS run's lease uuid: if the lease already
                    # expired and someone else legitimately holds the device,
                    # this refuses rather than revoking their live lease.
                    store.mark_dirty(device_id, quarantine_reason,
                                     lease_uuid=lease_info["lease_uuid"])
                    record["lease"]["final_state"] = "dirty"
                    record["lease"]["dirty_reason"] = quarantine_reason
                    stages.append({"name": "lease-final", "verdict": "PASS",
                                    "detail": {"final_state": "dirty",
                                               "dirty_reason": quarantine_reason}})
                else:
                    store.release(device_id, lease_info["lease_uuid"])
                    record["lease"]["final_state"] = "available"
                    stages.append({"name": "lease-final", "verdict": "PASS",
                                    "detail": {"final_state": "available"}})
            except dl_module.LeaseError as exc:
                record["lease"]["release_error"] = str(exc)
                stages.append({"name": "lease-final", "verdict": "FAIL",
                                "detail": {"reason": str(exc),
                                           "note": "this run did not hold the lease it "
                                                   "was driving the device under"}})
        store.close()

        # The verdict is derived from the stages, last, so no stage can be
        # recorded and then not counted. See _verdict_from_stages.
        record["verdict"] = _verdict_from_stages(stages)
        if record["verdict"] != "PASS":
            offenders = [s.get("name") for s in stages
                         if s.get("verdict") != "PASS"]
            summary = "non-PASS stages: %s" % ", ".join(str(n) for n in offenders)
            record["reason"] = ("%s (%s)" % (record["reason"], summary)
                                if record["reason"] else summary)



#: -- M4.04: local Android physical device execution (adb equivalent of
#: M4.03's devicectl lifecycle) --------------------------------------------
#:
#: Same reservation-through-cleanup shape as M4.03 above, over adb instead
#: of devicectl, reusing the SAME bm_device_lease.py store (M4.01,
#: _bm_device_lease_module() above is device-type-agnostic already) --
#: a physical device is a physical device regardless of platform, so the
#: lease store is shared rather than a second mechanism being invented.
#:
#: NO ANDROID TOOLING WAS AVAILABLE TO EXERCISE THIS UNIT FOR REAL: `which
#: adb`, `type adb`, and a check of ANDROID_HOME/ANDROID_SDK_ROOT were all
#: run directly on this machine before writing a line of this section --
#: no adb binary on PATH, no SDK env vars set, confirmed 2026-09-15. Every
#: adb/pm/am/monkey subcommand below was copied from real, current Android
#: SDK platform-tools documentation (developer.android.com/tools/adb and
#: developer.android.com/studio/test/other-testing-tools/monkey, both
#: fetched directly this session), never guessed, and NONE of it was
#: exercised against a real device or emulator: every function below is
#: therefore NO-DATA in practice on this machine ("adb unavailable"),
#: covered only by fixture-tool tests using adb's own documented
#: text-output shapes as fixtures, the same pattern test_device_matrix.py
#: already uses for the iOS half above and test_mobile_workflow.py uses
#: for fixture-tool tests.
#:
#: WHY NO CANDIDATE-IDENTITY READER FOR THE .apk ITSELF (unlike M4.03's
#: mobile_workflow.identity() for a .app's Info.plist): an APK's own
#: manifest (AndroidManifest.xml) is compiled BINARY XML inside the zip,
#: not a plain-text plist -- reading it for real needs `aapt`/`aapt2`
#: (part of Android SDK build-tools, not just platform-tools, and equally
#: unavailable on this machine) or a hand-rolled binary-XML parser, which
#: would be real scope creep for a physical-device-only unit (the M4 unit
#: doc explicitly warns against that for M4.04). The authoritative
#: on-device identity check already happens post-install via a fresh
#: `dumpsys package` read (adb_verify_installed_identity below), which is
#: the same "never trust the artifact's own claim, verify the device"
#: philosophy M4.03 uses -- so the pre-install candidate check here is
#: intentionally just "does the apk file exist and is it readable",
#: nothing more.

_ADB_VERSION_NAME_RE = re.compile(r"versionName=(\S+)")
_ADB_VERSION_CODE_RE = re.compile(r"versionCode=(\d+)")
_ADB_INSTALL_FAILURE_RE = re.compile(r"Failure\s*\[([^\]]*)\]")

#: Android's own documented package-name grammar (developer.android.com/
#: build/configure-app-module, "applicationId"): one or more dot-separated
#: segments, each starting with a letter, the rest letters/digits/
#: underscores. `adb shell` re-parses its trailing arguments on the DEVICE's
#: own shell, so an unvalidated package_id reaching `monkey -p`/`am
#: force-stop`/`dumpsys package` is a real on-device shell-metacharacter
#: risk even though the host-side subprocess call here never uses
#: shell=True (flagged by an automated review on this exact code, 2026-09-15;
#: fixed by refusing anything that does not match this grammar before any
#: adb shell call, never by escaping or quoting, which is the weaker
#: defense against a re-parsing shell we do not control).
_ANDROID_PACKAGE_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+\Z")


def _require_valid_package_id(package_id):
    """None if package_id matches Android's real grammar, else a FAIL
    reason string -- callers return ("FAIL", {"reason": ...}) on a non-None
    result rather than ever reaching an adb shell call with it. Uses
    fullmatch/\\Z rather than match+$, since $ (without re.MULTILINE) still
    matches just before a trailing newline -- "com.foo\\n" would otherwise
    pass this check and reach an adb shell call with an embedded newline."""
    if not isinstance(package_id, str) or not _ANDROID_PACKAGE_ID_RE.fullmatch(package_id):
        return "package_id %r does not match Android's package-name grammar, refused before any adb shell call" % (package_id,)
    return None


#: adb's own serial grammar, as `adb devices -l` actually prints it: a USB
#: serial (letters/digits, sometimes with "_" or "-"), an emulator name
#: ("emulator-5554"), or a network target ("192.168.1.5:5555"). The first
#: character is deliberately narrower than the rest: a value starting with
#: "-" is what adb PARSES AS ONE OF ITS OWN FLAGS, and this module's whole
#: purpose is per-device isolation, so `adb -s --version uninstall <pkg>`
#: silently retargeting a different device than the one the lease was taken
#: on is the defect this refuses. There is no shell injection here (_run_raw
#: passes a list argv with no shell=True, checked), so this is argument
#: injection: it changes WHICH PHONE gets acted on, not what runs.
#: Same fullmatch/\Z reason as the package_id guard above: "SERIAL\n" must
#: not pass and reach adb with an embedded newline.
_ADB_DEVICE_SERIAL_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._:-]*\Z")


def _require_valid_device_serial(device_id):
    """None if device_id is a plausible adb serial, else a FAIL reason
    string -- callers return ("FAIL", {"reason": ...}) on a non-None result
    rather than ever letting it become an adb argument. Mirrors
    _require_valid_package_id exactly, applied at the same boundary."""
    if not isinstance(device_id, str) or not _ADB_DEVICE_SERIAL_RE.fullmatch(device_id):
        return ("device_id %r is not an adb device serial (a value adb would parse as one "
                "of its own flags can retarget the command at a different device), refused "
                "before any adb call" % (device_id,))
    return None


def _safe_device_id_for_path(device_id):
    """device_id, sanitized for use as a filename component (never a full
    path): non-safe characters become "_". A value that would still
    traverse or vanish after sanitizing (empty, or literally "." or "..")
    raises ValueError -- callers catch this and return FAIL rather than
    ever reaching os.path.join with the raw value. Also flagged by the
    same automated review: device_id previously reached
    os.path.join(out_dir, ...) unsanitized."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", device_id or "")
    if safe in ("", ".", ".."):
        raise ValueError("device_id %r sanitizes to an unsafe path component" % (device_id,))
    return safe


def _adb(args, timeout):
    """Same contract as _run_raw above (never raises): {"returncode",
    "stdout", "stderr", "exception"}, exception a STRING or None (never an
    exception object -- _run_raw stringifies it; a caller checking
    isinstance() on it is a bug, not a feature)."""
    return _run_raw(["adb"] + list(args), timeout)


def parse_adb_devices(text):
    """Parses real `adb devices -l` output: a header line "List of devices
    attached", then one line per device as "SERIAL<TAB>STATE key:value
    key:value ...", then a blank separator line. Returns a list of dicts
    with at least "serial" and "state"; extra key:value tokens (product,
    model, device, transport_id) are included verbatim. "no permissions"
    is the one documented state containing a space, handled explicitly
    rather than split on whitespace like every other state -- splitting
    naively there would misread "permissions" as an extra key:value
    token's worth of garbage."""
    rows = []
    for line in text.splitlines():
        if "\t" not in line:
            continue  # the header line and the blank separator have no tab
        serial, rest = line.split("\t", 1)
        serial, rest = serial.strip(), rest.strip()
        if not serial or not rest:
            continue
        if rest.startswith("no permissions"):
            state, tail = "no permissions", rest[len("no permissions"):].strip()
        else:
            parts = rest.split(None, 1)
            state, tail = parts[0], (parts[1] if len(parts) > 1 else "")
        row = {"serial": serial, "state": state}
        for tok in tail.split():
            if ":" in tok:
                k, v = tok.split(":", 1)
                row[k] = v
        rows.append(row)
    return rows


def adb_list_devices(timeout=15):
    """(verdict, evidence) via a real `adb devices -l` read. NO-DATA when
    adb itself could not run; PASS with a device list otherwise -- an
    empty list is still PASS (adb ran fine and honestly found nothing),
    a fact distinct from adb being unavailable at all."""
    r = _adb(["devices", "-l"], timeout)
    if r["exception"] is not None:
        return "NO-DATA", {"reason": "adb unavailable: %s" % r["exception"], "devices": []}
    if r["returncode"] != 0:
        return "NO-DATA", {"reason": "adb devices exited %s: %s" % (r["returncode"], r["stderr"].strip()),
                            "devices": []}
    return "PASS", {"devices": parse_adb_devices(r["stdout"])}


def check_device_authorized(device_id, timeout=15):
    """(verdict, evidence). PASS only when device_id's own row reports
    state=="device" (adb's own vocabulary for ready-and-authorized).
    Every other state is a DISTINCT, HONESTLY NAMED NO-DATA reason --
    an unauthorized or offline device must never be silently treated as
    ready (adversarial-review target named in this unit's own brief)."""
    reason = _require_valid_device_serial(device_id)
    if reason is not None:
        return "FAIL", {"reason": reason}
    verdict, detail = adb_list_devices(timeout)
    if verdict != "PASS":
        return verdict, {"reason": detail["reason"]}
    match = next((d for d in detail["devices"] if d.get("serial") == device_id), None)
    if match is None:
        return "NO-DATA", {"reason": "device %r not found by adb devices -l" % device_id}
    state = match.get("state")
    if state == "device":
        return "PASS", {"state": state, "entry": match}
    return "NO-DATA", {"reason": ("device %r reports state %r, not ready (adb's own vocabulary: "
                                   "device=ready, unauthorized=needs on-device RSA-key confirmation, "
                                   "offline=bridge lost the connection, 'no permissions'=host udev "
                                   "rules)" % (device_id, state)),
                        "state": state, "entry": match}


def adb_install_app(device_id, apk_path, reinstall=False, allow_test=True, timeout=180):
    """`adb -s <id> install [-r] [-t] <apk>`: -r reinstalls keeping data,
    -t allows a test-only APK. Flags verified against
    developer.android.com/tools/adb. adb's own text convention: exit 0
    with stdout containing "Success", or "Failure [CODE]" -- checked
    directly, never trusted from exit code alone.

    reinstall DEFAULTS OFF, and that default is the point. On Android, -r
    reinstalls KEEPING the previous install's app data, so a device that
    carried this package before hands the new install the previous
    holder's data -- and adb_verify_installed_identity then passes,
    because versionName and versionCode do match, reporting a verified
    clean install layered over inherited state. That is an evidence
    collision this module exists to prevent, and it is invisible to a
    test that only checks the install succeeded. iOS has no equivalent
    because devicectl replaces the bundle container. Without -r, a
    package already present fails loudly (INSTALL_FAILED_ALREADY_EXISTS),
    which is the honest outcome: the device was not clean. -r stays
    available as an explicit caller opt-in, and the lifecycle records
    that the install was layered when it is used."""
    reason = _require_valid_device_serial(device_id)
    if reason is not None:
        return "FAIL", {"reason": reason}
    args = ["-s", device_id, "install"]
    if reinstall:
        args.append("-r")
    if allow_test:
        args.append("-t")
    args.append(str(apk_path))
    r = _adb(args, timeout)
    if r["exception"] is not None:
        return "NO-DATA", {"reason": "adb unavailable: %s" % r["exception"]}
    combined = (r["stdout"] or "") + "\n" + (r["stderr"] or "")
    m = _ADB_INSTALL_FAILURE_RE.search(combined)
    if m:
        return "FAIL", {"reason": "adb install failed: %s" % m.group(1)}
    if r["returncode"] != 0 or "Success" not in (r["stdout"] or ""):
        return "FAIL", {"reason": "adb install exited %s without a Success marker: %s"
                                   % (r["returncode"], combined.strip()[:400])}
    return "PASS", {"stdout": r["stdout"].strip()}


def adb_uninstall_app(device_id, package_id, timeout=60):
    """`adb -s <id> uninstall <package>` (no -k: a clean uninstall, never
    keeping data behind for the next claimant)."""
    reason = (_require_valid_device_serial(device_id)
              or _require_valid_package_id(package_id))
    if reason is not None:
        return "FAIL", {"reason": reason}
    r = _adb(["-s", device_id, "uninstall", package_id], timeout)
    if r["exception"] is not None:
        return "NO-DATA", {"reason": "adb unavailable: %s" % r["exception"]}
    combined = (r["stdout"] or "") + "\n" + (r["stderr"] or "")
    m = _ADB_INSTALL_FAILURE_RE.search(combined)
    if m:
        return "FAIL", {"reason": "adb uninstall failed: %s" % m.group(1)}
    if r["returncode"] != 0 or "Success" not in (r["stdout"] or ""):
        return "FAIL", {"reason": "adb uninstall exited %s without a Success marker: %s"
                                   % (r["returncode"], combined.strip()[:400])}
    return "PASS", {"stdout": r["stdout"].strip()}


def adb_verify_installed_identity(device_id, package_id, expected_version_name=None,
                                   expected_version_code=None, timeout=30):
    """PASS only when a FRESH, real `adb shell dumpsys package <id>` read
    (never adb_install_app's own self-reported Success) confirms the
    package is present, and, when given, expected_version_name /
    expected_version_code match that read's own versionName=/
    versionCode= fields exactly. Mirrors M4.03's verify_installed_identity:
    an install that reports success but whose on-device identity does not
    match is FAIL here, never PASS (the same adversarial-review target:
    a device left installed-but-unverified reported as success)."""
    reason = (_require_valid_device_serial(device_id)
              or _require_valid_package_id(package_id))
    if reason is not None:
        return "FAIL", {"reason": reason}
    r = _adb(["-s", device_id, "shell", "dumpsys", "package", package_id], timeout)
    if r["exception"] is not None:
        return "NO-DATA", {"reason": "adb unavailable: %s" % r["exception"]}
    if r["returncode"] != 0:
        return "NO-DATA", {"reason": "adb dumpsys exited %s: %s" % (r["returncode"], r["stderr"].strip())}
    out = r["stdout"] or ""
    if ("Package [%s]" % package_id) not in out:
        return "FAIL", {"reason": "package %r not found in dumpsys output (no installed identity to verify)"
                                   % package_id}
    name_m = _ADB_VERSION_NAME_RE.search(out)
    code_m = _ADB_VERSION_CODE_RE.search(out)
    got_version = name_m.group(1) if name_m else None
    got_build = code_m.group(1) if code_m else None
    mismatches = {}
    if expected_version_name is not None and got_version != expected_version_name:
        mismatches["versionName"] = {"expected": expected_version_name, "found": got_version}
    if expected_version_code is not None and got_build != str(expected_version_code):
        mismatches["versionCode"] = {"expected": str(expected_version_code), "found": got_build}
    if mismatches:
        return "FAIL", {"reason": "installed package identity differs from the candidate",
                         "mismatches": mismatches}
    return "PASS", {"package_id": package_id, "versionName": got_version, "versionCode": got_build}


def adb_verify_uninstalled(device_id, package_id, timeout=30):
    """PASS only when a FRESH, real `adb shell dumpsys package <id>` read
    shows the package ABSENT, never adb_uninstall_app's own self-reported
    "Success".

    The module already applies "never trust adb's own output" rigorously
    to install (adb_verify_installed_identity re-reads a fresh dumpsys);
    nothing guarded the step that decides whether the device is clean for
    the NEXT claimant. Without this, an uninstall returning PASS while the
    package was still installed sent the device back to the pool as
    available, still carrying today's candidate. Same rule, same read,
    applied to the other end of the lifecycle."""
    reason = (_require_valid_device_serial(device_id)
              or _require_valid_package_id(package_id))
    if reason is not None:
        return "FAIL", {"reason": reason}
    r = _adb(["-s", device_id, "shell", "dumpsys", "package", package_id], timeout)
    if r["exception"] is not None:
        return "NO-DATA", {"reason": "adb unavailable: %s" % r["exception"],
                            "note": "absence of %r is unconfirmed" % package_id}
    if r["returncode"] != 0:
        return "NO-DATA", {"reason": "adb dumpsys exited %s: %s" % (r["returncode"], r["stderr"].strip()),
                            "note": "absence of %r is unconfirmed" % package_id}
    if ("Package [%s]" % package_id) in (r["stdout"] or ""):
        return "FAIL", {"reason": "package %r is STILL installed on the device after "
                                   "uninstall reported success" % package_id}
    return "PASS", {"absent": package_id}


def adb_find_running_pid(device_id, package_id, timeout=15):
    """`adb -s <id> shell pidof <package>` -- an independent post-launch
    read, never trusting adb_launch_app's own reported outcome. Empty
    output means not running: NO-DATA, not FAIL, mirroring M4.03's own
    find_running_pid, which never aborts the lifecycle on a failed
    observation alone (an app can legitimately take a moment to settle,
    or Android's monkey can report success before the process is visible
    to pidof yet)."""
    reason = (_require_valid_device_serial(device_id)
              or _require_valid_package_id(package_id))
    if reason is not None:
        return "FAIL", {"reason": reason, "pid": None}
    r = _adb(["-s", device_id, "shell", "pidof", package_id], timeout)
    if r["exception"] is not None:
        return "NO-DATA", {"reason": "adb unavailable: %s" % r["exception"], "pid": None}
    out = (r["stdout"] or "").strip()
    pids = [tok for tok in out.split() if tok.isdigit()]
    if not pids:
        return "NO-DATA", {"reason": "pidof reported no running process for %r" % package_id, "pid": None}
    return "PASS", {"pid": int(pids[0]), "raw": out}


def adb_launch_app(device_id, package_id, timeout=30):
    """Generic launch with no known activity class: `adb -s <id> shell
    monkey -p <package> -c android.intent.category.LAUNCHER 1` (event
    count 1, never a larger fuzz count -- this is a launch, not a fuzz
    run). Command form verified against developer.android.com/studio/
    test/other-testing-tools/monkey. Success prints "Events injected: 1"
    and exits 0; a missing launcher activity fails on a nonzero exit,
    often with "No activities found to run, monkey aborted." -- checked
    as a text marker too, never trusted from exit code alone."""
    reason = (_require_valid_device_serial(device_id)
              or _require_valid_package_id(package_id))
    if reason is not None:
        return "FAIL", {"reason": reason}
    r = _adb(["-s", device_id, "shell", "monkey", "-p", package_id, "-c",
              "android.intent.category.LAUNCHER", "1"], timeout)
    if r["exception"] is not None:
        return "NO-DATA", {"reason": "adb unavailable: %s" % r["exception"]}
    out = r["stdout"] or ""
    combined = out + "\n" + (r["stderr"] or "")
    if r["returncode"] != 0 or "Events injected: 1" not in out:
        return "FAIL", {"reason": "monkey launch failed (exit %s): %s"
                                   % (r["returncode"], combined.strip()[:400])}
    return "PASS", {"stdout": out.strip()}


def adb_force_stop_app(device_id, package_id, timeout=15):
    """`adb -s <id> shell am force-stop <package>` -- Android's real
    terminate primitive, PACKAGE-scoped rather than pid-scoped (unlike
    iOS's devicectl terminate, which needs an observed pid): this is a
    deliberate adaptation to the real tool, not a shortcut, and it means
    force-stop can run unconditionally after launch regardless of whether
    adb_find_running_pid observed a pid -- it is safe and idempotent even
    if nothing is currently running."""
    reason = (_require_valid_device_serial(device_id)
              or _require_valid_package_id(package_id))
    if reason is not None:
        return "FAIL", {"reason": reason}
    r = _adb(["-s", device_id, "shell", "am", "force-stop", package_id], timeout)
    if r["exception"] is not None:
        return "NO-DATA", {"reason": "adb unavailable: %s" % r["exception"]}
    if r["returncode"] != 0:
        return "FAIL", {"reason": "am force-stop exited %s: %s" % (r["returncode"], r["stderr"].strip())}
    return "PASS", {"package_id": package_id}


def adb_capture_logcat(device_id, out_dir, timeout=30):
    """`adb -s <id> logcat -d`: dumps the CURRENT log buffer and exits,
    WITHOUT clearing it (never -c, which clears/destroys the log --
    evidence must never be destroyed). Always returned inline (a trailing
    excerpt) and, when out_dir is given, saved to disk in full."""
    reason = _require_valid_device_serial(device_id)
    if reason is not None:
        return "FAIL", {"reason": reason}
    r = _adb(["-s", device_id, "logcat", "-d"], timeout)
    if r["exception"] is not None:
        return "NO-DATA", {"reason": "adb unavailable: %s" % r["exception"]}
    if r["returncode"] != 0:
        return "FAIL", {"reason": "adb logcat exited %s: %s" % (r["returncode"], r["stderr"].strip())}
    out = r["stdout"] or ""
    evidence = {"bytes": len(out), "excerpt": out[-2000:]}
    if out_dir:
        try:
            safe_id = _safe_device_id_for_path(device_id)
            os.makedirs(out_dir, exist_ok=True)
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            path = os.path.join(out_dir, "logcat-%s-%s.txt" % (safe_id, stamp))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(out)
            evidence["saved_to"] = path
        except (OSError, ValueError) as exc:
            evidence["save_error"] = str(exc)
    return "PASS", evidence


def adb_capture_screenshot(device_id, out_dir, timeout=30):
    """`adb -s <id> exec-out screencap -p`: raw PNG bytes on stdout.
    exec-out is used rather than shell specifically to avoid the
    text-mode corruption `adb shell` applies to its output; the PNG
    magic bytes are checked before trusting the saved file as a real
    image. Needs out_dir (a screenshot cannot be usefully returned
    inline as text) -- NO-DATA, not a crash, when none is given."""
    reason = _require_valid_device_serial(device_id)
    if reason is not None:
        return "FAIL", {"reason": reason}
    if not out_dir:
        return "NO-DATA", {"reason": "no out_dir given, screenshot not captured"}
    try:
        safe_id = _safe_device_id_for_path(device_id)
        os.makedirs(out_dir, exist_ok=True)
    except (OSError, ValueError) as exc:
        return "FAIL", {"reason": "could not prepare out_dir/path: %s" % exc}
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(out_dir, "screenshot-%s-%s.png" % (safe_id, stamp))
    argv = ["adb", "-s", device_id, "exec-out", "screencap", "-p"]
    try:
        with open(path, "wb") as fh:
            proc = subprocess.run(argv, stdout=fh, stderr=subprocess.PIPE, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "NO-DATA", {"reason": "adb unavailable: %s" % exc}
    if proc.returncode != 0:
        return "FAIL", {"reason": "adb exec-out screencap exited %s: %s"
                                   % (proc.returncode, proc.stderr.decode("utf-8", "replace").strip())}
    try:
        with open(path, "rb") as fh:
            magic = fh.read(8)
    except OSError as exc:
        return "FAIL", {"reason": "could not read back the saved screenshot: %s" % exc}
    if not magic.startswith(b"\x89PNG\r\n\x1a\n"):
        return "FAIL", {"reason": "exec-out screencap output is not a valid PNG (got %r)" % magic}
    return "PASS", {"saved_to": path}


def _collect_evidence_android(device_id, out_dir, timeout=30):
    """Snapshot the device's own logcat buffer and a screenshot (both real
    adb reads) to out_dir when given. Mirrors M4.03's _collect_evidence:
    NO-DATA on either read is reported, never silently dropped."""
    logcat_verdict, logcat_detail = adb_capture_logcat(device_id, out_dir, timeout=timeout)
    shot_verdict, shot_detail = adb_capture_screenshot(device_id, out_dir, timeout=timeout)
    evidence = {"logcat": logcat_detail, "logcat_verdict": logcat_verdict,
                "screenshot": shot_detail, "screenshot_verdict": shot_verdict}
    verdicts = {logcat_verdict, shot_verdict}
    if verdicts == {"PASS"}:
        return "PASS", evidence
    if "FAIL" in verdicts:
        return "FAIL", evidence
    return "NO-DATA", evidence


def _best_effort_uninstall_android(device_id, package_id, stages):
    """Same shape as M4.03's _best_effort_uninstall: attempt to remove a
    partially-installed or unverifiable candidate before the lease is
    released or quarantined; returns None on success or a reason string
    that becomes the lease's dirty_reason when it fails.

    Like the iOS sibling, the verdict comes from RE-READING the device,
    not from adb's exit code: adb errors uninstalling an already-absent
    package the same way it errors on a real failure, so trusting the
    exit code quarantines already-clean devices after a failed install
    and releases partially-installed ones on a lucky zero. The observed
    fact is the fresh dumpsys read."""
    verdict, detail = adb_uninstall_app(device_id, package_id)
    stages.append({"name": "cleanup-uninstall", "verdict": verdict, "detail": detail})
    confirm_verdict, confirm_detail = adb_verify_uninstalled(device_id, package_id)
    stages.append({"name": "cleanup-verify-absent", "verdict": confirm_verdict,
                    "detail": confirm_detail})
    if confirm_verdict == "PASS":
        return None  # observed absent: clean, whatever adb claimed either way
    if confirm_verdict == "FAIL":
        return ("cleanup left the candidate on the device after an earlier "
                "lifecycle failure: %s" % confirm_detail.get("reason"))
    return ("cleanup could not be confirmed after an earlier lifecycle failure "
            "(device unreadable): %s" % confirm_detail.get("reason"))


def run_android_physical_lifecycle(device_id, apk_path, package_id, owner, session_id=None,
                                    ttl_seconds=DEFAULT_LEASE_TTL_SECONDS, uninstall_after=True,
                                    launch=True, out_dir=None, install_timeout=180,
                                    launch_timeout=30, observe_seconds=0,
                                    expected_version_name=None, expected_version_code=None,
                                    reinstall_keep_data=False):
    """Android equivalent of M4.03's run_physical_lifecycle: reserve
    device_id through bm_device_lease.py's real claim API (M4.01, the SAME
    store, not a second mechanism), check authorization health, install,
    verify installed identity from a fresh independent read, launch,
    observe, collect evidence (logcat, screenshot), force-stop, uninstall,
    and release the lease on EVERY exit path -- including a mid-lifecycle
    failure (the same adversarial-review target M4.03 was built against:
    a lease claimed but never released strands the device for every later
    claimant until its TTL happens to expire).

    Returns a brother-android-physical-device-lifecycle-v1 record. verdict
    is PASS only when install, identity verification, and (if requested)
    launch and cleanup all independently verified; adb reporting success
    by itself never sets PASS alone. The verdict is DERIVED from the
    stages list this function builds, computed last in the finally block
    (see _verdict_from_stages), so a stage cannot be recorded and then not
    counted -- the same fix M4.03's iOS lifecycle carries, sharing its one
    derivation rather than keeping a second copy for Android."""
    stages = []
    record = {
        "schema": "brother-android-physical-device-lifecycle-v1", "device_id": device_id,
        "package_id": package_id, "owner": owner, "verdict": "FAIL", "reason": None,
        "stages": stages, "lease": None,
        "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "limits": [
            "Every stage is a real adb invocation against this exact device_id; a stage "
            "this run could not exercise is recorded NO-DATA with its own reason, never "
            "silently skipped.",
            "Installed-identity verification (a fresh dumpsys package read) gates the "
            "verdict: adb install reporting Success is not by itself sufficient for PASS.",
            "No Android tooling was available on the machine that built this unit: every "
            "adb/am/pm/monkey command was copied from real, current Android SDK "
            "platform-tools documentation, never exercised against a real device or "
            "emulator by this unit (see the module-level note above).",
        ],
    }

    dl_module, dl_reason = _bm_device_lease_module()
    if dl_module is None:
        record["verdict"], record["reason"] = "NO-DATA", dl_reason
        stages.append({"name": "reserve", "verdict": "NO-DATA", "detail": {"reason": dl_reason}})
        return record

    session_id = session_id or _uuid.uuid4().hex
    store = dl_module.DeviceLeaseStore()
    lease_info = None
    cleanup_failed_reason = None
    residue_reason = None
    try:
        try:
            lease_info = store.claim(device_id, owner, session_id, ttl_seconds)
        except dl_module.LeaseRefused as exc:
            record["verdict"], record["reason"] = "NO-DATA", str(exc)
            stages.append({"name": "reserve", "verdict": "NO-DATA",
                            "detail": {"reason": exc.reason, "message": str(exc)}})
            return record
        record["lease"] = {"lease_uuid": lease_info["lease_uuid"], "expires_at": lease_info["expires_at"],
                            "recovered_from_stale": lease_info["recovered_from_stale"], "final_state": None}
        stages.append({"name": "reserve", "verdict": "PASS", "detail": lease_info})

        try:
            verdict, detail = check_device_authorized(device_id)
            stages.append({"name": "authorization-check", "verdict": verdict, "detail": detail})
            if verdict != "PASS":
                record["verdict"], record["reason"] = verdict, detail.get("reason")
                return record

            if not os.path.isfile(apk_path):
                reason = "candidate apk not found at %r" % apk_path
                stages.append({"name": "candidate-apk", "verdict": "FAIL", "detail": {"reason": reason}})
                record["verdict"], record["reason"] = "FAIL", reason
                return record
            stages.append({"name": "candidate-apk", "verdict": "PASS", "detail": {"apk_path": apk_path}})

            verdict, detail = adb_install_app(device_id, apk_path, reinstall=reinstall_keep_data,
                                               timeout=install_timeout)
            stages.append({"name": "install", "verdict": verdict,
                            "detail": dict(detail, layered_over_existing_data=bool(reinstall_keep_data))})
            if reinstall_keep_data:
                # An explicit caller opt-in, recorded so no later reader of
                # this record can mistake it for an isolated install: -r
                # keeps the previous install's app data, so this run's
                # evidence may reflect a previous holder's state.
                record["limits"].append(
                    "Installed with adb's -r (--reinstall-keep-data): the candidate was "
                    "LAYERED OVER any existing app data for this package, so this run's "
                    "evidence is not evidence about a clean install.")
            if verdict != "PASS":
                record["verdict"], record["reason"] = verdict, detail.get("reason")
                cleanup_failed_reason = _best_effort_uninstall_android(device_id, package_id, stages)
                return record

            verdict, detail = adb_verify_installed_identity(
                device_id, package_id, expected_version_name=expected_version_name,
                expected_version_code=expected_version_code)
            stages.append({"name": "verify-installed-identity", "verdict": verdict, "detail": detail})
            if verdict != "PASS":
                record["verdict"], record["reason"] = verdict, detail.get("reason")
                cleanup_failed_reason = _best_effort_uninstall_android(device_id, package_id, stages)
                return record

            if launch:
                verdict, detail = adb_launch_app(device_id, package_id, timeout=launch_timeout)
                stages.append({"name": "launch", "verdict": verdict, "detail": detail})
                if verdict != "PASS":
                    record["verdict"], record["reason"] = verdict, detail.get("reason")
                    cleanup_failed_reason = _best_effort_uninstall_android(device_id, package_id, stages)
                    return record
                if observe_seconds > 0:
                    time.sleep(observe_seconds)
                # Observation only: never gates the lifecycle (mirrors M4.03's
                # own find_running_pid, which is evidentiary, not a hard stop).
                verdict, detail = adb_find_running_pid(device_id, package_id)
                stages.append({"name": "observe-process", "verdict": verdict, "detail": detail})

            verdict, detail = _collect_evidence_android(device_id, out_dir)
            stages.append({"name": "collect-evidence", "verdict": verdict, "detail": detail})

            if launch:
                # Package-scoped, unlike iOS's pid-scoped terminate: always
                # attempted regardless of whether a pid was observed above.
                verdict, detail = adb_force_stop_app(device_id, package_id)
                stages.append({"name": "terminate", "verdict": verdict, "detail": detail})
                if verdict != "PASS":
                    cleanup_failed_reason = "terminate failed: %s" % detail.get("reason")

            if uninstall_after:
                verdict, detail = adb_uninstall_app(device_id, package_id)
                stages.append({"name": "uninstall", "verdict": verdict, "detail": detail})
                # adb's own "Success" is not evidence the package is gone,
                # and this is the step that decides whether the device is
                # clean for the next claimant, so re-read it.
                verdict, detail = adb_verify_uninstalled(device_id, package_id)
                stages.append({"name": "verify-uninstalled", "verdict": verdict, "detail": detail})
                if verdict != "PASS" and cleanup_failed_reason is None:
                    cleanup_failed_reason = detail.get("reason")
            else:
                # --keep-installed is a legitimate request, but it leaves
                # KNOWN residue: the candidate package, and its app data.
                # Returning that device to the pool as available tells the
                # next claimant a clean device is waiting. It is
                # quarantined dirty naming the residue instead, and
                # bm_device_lease.py's clear-dirty CLI is how it comes back.
                residue_reason = ("candidate %s deliberately left installed "
                                  "(--keep-installed); device carries known residue"
                                  % package_id)
                stages.append({"name": "keep-installed-residue", "verdict": "PASS",
                                "detail": {"quarantined": True, "reason": residue_reason}})

            if cleanup_failed_reason is None:
                record["reason"] = ("install and identity verification verified; "
                                    + ("candidate uninstalled and re-verified absent"
                                       if uninstall_after
                                       else "candidate deliberately left installed, "
                                            "device quarantined dirty"))
            else:
                record["reason"] = "lifecycle ran but cleanup was not verified: %s" % cleanup_failed_reason
            return record
        except dl_module.LeaseError:
            raise  # a raw lease-API misuse bug must surface, never be papered over as "dirty device"
        except Exception as exc:  # noqa: BLE001 -- last-resort net, see docstring: every exit path releases or quarantines
            cleanup_failed_reason = cleanup_failed_reason or ("unexpected error: %s" % exc)
            record["verdict"], record["reason"] = "FAIL", cleanup_failed_reason
            stages.append({"name": "unexpected-error", "verdict": "FAIL", "detail": {"reason": str(exc)}})
            return record
    finally:
        # Whatever happens to the lease is a STAGE, not a side field. It
        # used to be recorded only in lease.release_error, which nothing
        # read: a run whose TTL expired mid-lifecycle, letting another
        # session legitimately claim the same physical phone while this one
        # kept installing, launching and uninstalling on it, still reported
        # PASS and exit 0 with the double-hold named in a field beside the
        # verdict. Appending the outcome here, before the verdict is derived
        # below, is what puts it in front of the caller.
        if lease_info is not None:
            quarantine_reason = cleanup_failed_reason or residue_reason
            try:
                if quarantine_reason:
                    # Fenced on THIS run's lease uuid: if the lease already
                    # expired and someone else legitimately holds the device,
                    # this refuses rather than revoking their live lease.
                    store.mark_dirty(device_id, quarantine_reason,
                                     lease_uuid=lease_info["lease_uuid"])
                    record["lease"]["final_state"] = "dirty"
                    record["lease"]["dirty_reason"] = quarantine_reason
                    stages.append({"name": "lease-final", "verdict": "PASS",
                                    "detail": {"final_state": "dirty",
                                               "dirty_reason": quarantine_reason}})
                else:
                    store.release(device_id, lease_info["lease_uuid"])
                    record["lease"]["final_state"] = "available"
                    stages.append({"name": "lease-final", "verdict": "PASS",
                                    "detail": {"final_state": "available"}})
            except dl_module.LeaseError as exc:
                record["lease"]["release_error"] = str(exc)
                stages.append({"name": "lease-final", "verdict": "FAIL",
                                "detail": {"reason": str(exc),
                                           "note": "this run did not hold the lease it "
                                                   "was driving the device under"}})
        store.close()

        # The verdict is derived from the stages, last, so no stage can be
        # recorded and then not counted. See _verdict_from_stages.
        record["verdict"] = _verdict_from_stages(stages)
        if record["verdict"] != "PASS":
            offenders = [s.get("name") for s in stages
                         if s.get("verdict") != "PASS"]
            summary = "non-PASS stages: %s" % ", ".join(str(n) for n in offenders)
            record["reason"] = ("%s (%s)" % (record["reason"], summary)
                                if record["reason"] else summary)


def build_arg_parser():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command")
    for name in ADAPTERS:
        sub.add_parser(name)

    lc = sub.add_parser("lifecycle", help="reserve, install, launch, observe, collect, and clean up a real physical device (M4.03)")
    lc.add_argument("--device", required=True)
    lc.add_argument("--app", required=True)
    lc.add_argument("--bundle-id", required=True)
    lc.add_argument("--owner", required=True)
    lc.add_argument("--session-id")
    lc.add_argument("--ttl-seconds", type=int, default=DEFAULT_LEASE_TTL_SECONDS)
    lc.add_argument("--no-launch", action="store_true")
    lc.add_argument("--keep-installed", action="store_true")
    lc.add_argument("--out-dir")
    lc.add_argument("--install-timeout", type=int, default=180)
    lc.add_argument("--launch-timeout", type=int, default=60)
    lc.add_argument("--observe-seconds", type=int, default=0)

    rs = sub.add_parser("reserve")
    rs.add_argument("--device", required=True)
    rs.add_argument("--owner", required=True)
    rs.add_argument("--session-id")
    rs.add_argument("--ttl-seconds", type=int, default=DEFAULT_LEASE_TTL_SECONDS)

    rl = sub.add_parser("release")
    rl.add_argument("--device", required=True)
    rl.add_argument("--lease-uuid", required=True)

    for verb in ("processes", "apps", "details"):
        p = sub.add_parser(verb)
        p.add_argument("--device", required=True)

    alc = sub.add_parser("android-lifecycle", help="reserve, install, launch, observe, collect, and clean up a real Android physical device (M4.04)")
    alc.add_argument("--device", required=True)
    alc.add_argument("--apk", required=True)
    alc.add_argument("--package", required=True)
    alc.add_argument("--owner", required=True)
    alc.add_argument("--session-id")
    alc.add_argument("--ttl-seconds", type=int, default=DEFAULT_LEASE_TTL_SECONDS)
    alc.add_argument("--no-launch", action="store_true")
    alc.add_argument("--keep-installed", action="store_true")
    alc.add_argument(
        "--reinstall-keep-data", action="store_true",
        help="install with adb's -r, which KEEPS the previous install's app data. "
             "Off by default: a run claiming an isolated device must not silently "
             "layer today's candidate over the previous holder's data.")
    alc.add_argument("--out-dir")
    alc.add_argument("--install-timeout", type=int, default=180)
    alc.add_argument("--launch-timeout", type=int, default=30)
    alc.add_argument("--observe-seconds", type=int, default=0)
    alc.add_argument("--expected-version-name")
    alc.add_argument("--expected-version-code")

    sub.add_parser("android-devices")

    aa = sub.add_parser("android-authorized")
    aa.add_argument("--device", required=True)

    return ap


def main(argv=None):
    ap = build_arg_parser()
    args = ap.parse_args(argv)
    command = args.command or ADAPTER_LOCAL

    if command in ADAPTERS:
        record = physical_device_evidence(command)
        print(json.dumps(record, indent=1))
        return exit_code_for_verdict(record["verdict"])

    if command in ("lifecycle", "android-lifecycle", "reserve") and args.ttl_seconds < 1:
        # A bad --ttl-seconds used to escape as a raw ValueError traceback
        # from deep inside the lease store. It is a caller mistake, so it
        # gets the same clean record shape every other refusal gets.
        print(json.dumps({"verdict": "NO-DATA",
                          "reason": "--ttl-seconds must be a positive whole number of "
                                    "seconds, got %r" % args.ttl_seconds}, indent=1))
        return exit_code_for_verdict("NO-DATA")

    if command == "lifecycle":
        record = run_physical_lifecycle(
            args.device, args.app, args.bundle_id, args.owner, session_id=args.session_id,
            ttl_seconds=args.ttl_seconds, uninstall_after=not args.keep_installed,
            launch=not args.no_launch, out_dir=args.out_dir, install_timeout=args.install_timeout,
            launch_timeout=args.launch_timeout, observe_seconds=args.observe_seconds)
        print(json.dumps(record, indent=1))
        return exit_code_for_verdict(record["verdict"])

    if command == "reserve":
        verdict, detail = reserve_device(args.device, args.owner, session_id=args.session_id, ttl_seconds=args.ttl_seconds)
        print(json.dumps(dict(detail, verdict=verdict), indent=1))
        return exit_code_for_verdict(verdict)

    if command == "release":
        verdict, detail = release_device(args.device, args.lease_uuid)
        print(json.dumps(dict(detail, verdict=verdict), indent=1))
        return exit_code_for_verdict(verdict)

    if command in ("processes", "apps", "details"):
        func = {"processes": list_processes, "apps": list_installed_apps, "details": get_device_details}[command]
        verdict, detail = func(args.device)
        print(json.dumps(dict(detail, verdict=verdict), indent=1))
        return exit_code_for_verdict(verdict)

    if command == "android-lifecycle":
        record = run_android_physical_lifecycle(
            args.device, args.apk, args.package, args.owner, session_id=args.session_id,
            ttl_seconds=args.ttl_seconds, uninstall_after=not args.keep_installed,
            launch=not args.no_launch, out_dir=args.out_dir, install_timeout=args.install_timeout,
            launch_timeout=args.launch_timeout, observe_seconds=args.observe_seconds,
            expected_version_name=args.expected_version_name,
            expected_version_code=args.expected_version_code,
            reinstall_keep_data=args.reinstall_keep_data)
        print(json.dumps(record, indent=1))
        return exit_code_for_verdict(record["verdict"])

    if command == "android-devices":
        verdict, detail = adb_list_devices()
        print(json.dumps(dict(detail, verdict=verdict), indent=1))
        return exit_code_for_verdict(verdict)

    if command == "android-authorized":
        verdict, detail = check_device_authorized(args.device)
        print(json.dumps(dict(detail, verdict=verdict), indent=1))
        return exit_code_for_verdict(verdict)

    ap.error("unknown command %r" % command)


if __name__ == "__main__":
    sys.exit(main())
