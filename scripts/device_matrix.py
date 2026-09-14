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


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("adapter", nargs="?", default=ADAPTER_LOCAL, choices=ADAPTERS)
    args = ap.parse_args(argv)
    record = physical_device_evidence(args.adapter)
    print(json.dumps(record, indent=1))
    return exit_code_for_verdict(record["verdict"])


if __name__ == "__main__":
    sys.exit(main())
