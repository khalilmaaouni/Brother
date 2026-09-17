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

    return ap


def main(argv=None):
    ap = build_arg_parser()
    args = ap.parse_args(argv)
    command = args.command or ADAPTER_LOCAL

    if command in ADAPTERS:
        record = physical_device_evidence(command)
        print(json.dumps(record, indent=1))
        return exit_code_for_verdict(record["verdict"])

    if command in ("lifecycle", "reserve") and args.ttl_seconds < 1:
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

    ap.error("unknown command %r" % command)


if __name__ == "__main__":
    sys.exit(main())
