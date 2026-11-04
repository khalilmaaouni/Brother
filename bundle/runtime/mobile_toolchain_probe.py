#!/usr/bin/env python3
"""EPIC M1.02 Mobile Toolchain Probe: a read-only probe that reports which
real developer tool versions and executable paths are actually present
and resolvable ON THIS MACHINE right now.

This is NOT a project-file detector -- mobile_project_profile.py (EPIC
M1.01) already answers "what is this project". This module answers a
different question entirely: "what tools can this machine actually run".
Every tool's resolved_path comes only from shutil.which() against the
process's real environment PATH; every version comes only from actually
running the tool's own --version-style flag and reading its real
stdout/stderr. Nothing is ever read from a project directory, a config
file, or a caller-supplied argument: probe() takes NO parameters at all,
so a project directory that happens to contain a file literally named
"xcodebuild" can never influence what this probe reports as the real,
trusted xcodebuild (the "no redirecting trusted tool identity"
requirement -- enforced by having no input surface for it, not by a
runtime check).

SCOPE OF THE GUARANTEE (adversarial review, 2026-09-15): the guarantee
above is that mobile_project_profile.py's own detection of a project's
files can never reach this probe's tool resolution -- there is no code
path connecting the two. It is NOT a defense against a compromised
process environment: shutil.which() and every subprocess call here trust
the process's own PATH (and inherit its environment) exactly as
mobile_project_profile.py's own _profiled_revision() trusts PATH for
`git`, and as every other subprocess call in this codebase does. A shell
that has already prepended a hostile directory to its own PATH, or set
DEVELOPER_DIR/JAVA_HOME to something adversarial, is a compromised
machine, which is out of this unit's scope ("detection comes first", not
a supply-chain/code-signing verifier). Hardening against that would be a
different, much larger unit.

Tools probed (all read-only, no writes, no network):
  xcodebuild    Apple's Xcode build tool           xcodebuild -version
  simctl        Apple's Simulator control          xcrun simctl --version
  xcresulttool  Apple's Xcode result reader         xcrun xcresulttool version
  gradle        Gradle build tool                  gradle --version
  javac         JDK compiler (JDK representative)   javac -version
  adb           Android Debug Bridge                adb --version

A tool not found by shutil.which(), or whose --version invocation
raises/fails/times out, is recorded as resolved_path: "NO-DATA" and
version: "NO-DATA" -- never a guess, never an empty string, matching
mobile_project_profile.py's own NO-DATA convention.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

import contract_check as CC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SCHEMA = os.path.join(ROOT, "docs", "schema", "mobile-toolchain-probe-v1.json")

_TAG = "NO-DATA"
_TIMEOUT_SECONDS = 10


def _no_data():
    return {"resolved_path": _TAG, "version": _TAG, "status": "no-data"}


def _found(path, version):
    return {"resolved_path": path, "version": version, "status": "found"}


def _run(argv):
    """Run argv with both stdout and stderr captured, never raising:
    (stdout, stderr, returncode) on a completed run, (None, None, None) on
    any failure mode (missing binary, timeout, OS error). Only ever called
    on a path we resolved ourselves via shutil.which() the line before.

    The returncode matters as much as the text: on this machine
    /usr/bin/javac resolves via shutil.which() (macOS ships a stub there)
    but exits 1 and prints "Unable to locate a Java Runtime." to stderr
    when no real JDK is installed. Treating any non-empty stdout/stderr as
    a version string would silently report that stub as a found, working
    javac. Only a clean exit is trusted as a real version."""
    try:
        out = subprocess.run(
            argv, capture_output=True, text=True,
            timeout=_TIMEOUT_SECONDS, check=False)
        return out.stdout, out.stderr, out.returncode
    except (subprocess.TimeoutExpired, OSError, UnicodeDecodeError, ValueError):
        # UnicodeDecodeError: text=True decodes with the locale's default
        # encoding; a tool that writes non-decodable bytes must not crash
        # the whole probe, only make that one tool NO-DATA.
        return None, None, None


def _first_line(text):
    """The first real content line of text: blank lines are skipped, and
    so is a line made up only of '-' characters (real `gradle --version`
    prints a banner separator line of dashes before the actual "Gradle
    X.Y" line; taking a literal first-non-empty-line would report that
    separator as the version, a real bug this module's own adversarial
    review caught before any Gradle install was on hand to test live)."""
    if not text:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if line and line.strip("-"):
            return line
    return ""


def _probe_direct(which_name, version_argv):
    """which_name resolved via shutil.which() only, then version_argv run
    against that resolved path. NO-DATA on any failure to resolve, run, or
    on a nonzero exit (a resolvable-but-broken stub, e.g. macOS's
    /usr/bin/javac with no JDK installed, must never be reported as
    found)."""
    path = shutil.which(which_name)
    if not path:
        return _no_data()
    out, err, code = _run(version_argv(path))
    if out is None or code != 0:
        return _no_data()
    version = _first_line(out) or _first_line(err)
    if not version:
        return _no_data()
    return _found(path, version)


def _probe_via_xcrun(subcommand):
    """simctl and xcresulttool are not standalone binaries on PATH; they
    live inside the active Xcode and are invoked through xcrun.
    resolved_path is xcrun's own resolved path, since that is the
    executable this probe actually resolves and runs -- never a guess at
    where simctl 'would' live if it were a standalone binary."""
    xcrun = shutil.which("xcrun")
    if not xcrun:
        return _no_data()
    out, err, code = _run([xcrun] + list(subcommand))
    if out is None or code != 0:
        return _no_data()
    version = _first_line(out) or _first_line(err)
    if not version:
        return _no_data()
    return _found(xcrun, version)


def _probe_xcodebuild():
    return _probe_direct("xcodebuild", lambda path: [path, "-version"])


def _probe_gradle():
    return _probe_direct("gradle", lambda path: [path, "--version"])


def _probe_javac():
    # javac -version prints to stderr on some JDK builds and stdout on
    # others; _first_line(out) or _first_line(err) accepts whichever side
    # actually carries the text.
    return _probe_direct("javac", lambda path: [path, "-version"])


def _probe_adb():
    return _probe_direct("adb", lambda path: [path, "--version"])


def _probed_at_revision():
    """The real git revision of THIS repo (Brother), or NO-DATA if it is
    not a git checkout. Mirrors mobile_project_profile._profiled_revision,
    but the revision recorded is this probe's own source tree, never any
    target project's."""
    try:
        out = subprocess.run(
            ["git", "-C", ROOT, "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
            timeout=_TIMEOUT_SECONDS)
        revision = out.stdout.strip()
        return revision if revision else _TAG
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError, OSError, UnicodeDecodeError, ValueError):
        return _TAG


def _rollup_status(entries):
    """'complete' iff every entry is found; 'none' iff none are found;
    else 'partial'."""
    found = sum(1 for e in entries if e.get("status") == "found")
    if found == len(entries):
        return "complete"
    if found == 0:
        return "none"
    return "partial"


def probe():
    """The whole read-only toolchain probe. Takes no parameters on
    purpose: there is deliberately no way for a caller (or a project's
    own files) to supply, override, or redirect a tool's path or
    version. Trusted tool identity comes only from shutil.which() against
    the process's real PATH and from the tool's own --version
    invocation."""
    tools = {
        "xcodebuild": _probe_xcodebuild(),
        "simctl": _probe_via_xcrun(["simctl", "--version"]),
        "xcresulttool": _probe_via_xcrun(["xcresulttool", "version"]),
        "gradle": _probe_gradle(),
        "javac": _probe_javac(),
        "adb": _probe_adb(),
    }
    ios_status = _rollup_status(
        [tools["xcodebuild"], tools["simctl"], tools["xcresulttool"]])
    android_status = _rollup_status(
        [tools["gradle"], tools["javac"], tools["adb"]])
    return {
        "schema_version": "mobile-toolchain-probe-v1",
        "probed_at_revision": _probed_at_revision(),
        "tools": tools,
        "ios_toolchain_status": ios_status,
        "android_toolchain_status": android_status,
    }


def hand_rules(record):
    """The one rule the keyword subset cannot express: a tool entry's
    status must never contradict its own fields, in both directions.
    'found' requires a real resolved_path and version (never NO-DATA);
    'no-data' requires both fields to be exactly NO-DATA, so a
    half-populated entry can never pass silently."""
    problems = []
    tools = record.get("tools") or {}
    for name in sorted(tools):
        entry = tools[name] or {}
        status = entry.get("status")
        path = entry.get("resolved_path")
        version = entry.get("version")
        if status == "found" and (path == _TAG or version == _TAG):
            problems.append(
                "tools.%s: status 'found' but resolved_path=%r version=%r "
                "-- a found tool must carry its real path and version"
                % (name, path, version))
        if status == "no-data" and (path != _TAG or version != _TAG):
            problems.append(
                "tools.%s: status 'no-data' but resolved_path=%r version=%r "
                "-- a no-data entry must carry NO-DATA in both fields"
                % (name, path, version))
    return problems


def check(record, schema):
    problems = []
    CC.validate(record, schema, "", problems)
    problems.extend(hand_rules(record))
    seen, out = set(), []
    for p in problems:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    args = parser.parse_args(argv)
    record = probe()
    schema = CC.load_json(args.schema, "mobile-toolchain-probe-v1 schema")
    problems = check(record, schema)
    print(json.dumps(record, indent=2, sort_keys=True))
    if problems:
        print("PROBLEMS:", file=sys.stderr)
        for p in problems:
            print(" - %s" % p, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
