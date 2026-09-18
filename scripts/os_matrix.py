#!/usr/bin/env python3
"""DOM-50.09 of the 1.0.20 orchestration control plane: the one Dominance
topic with no code at all, before this module.

WHY THIS EXISTS. scripts/process_containment.py names its own limit in its
module docstring: "POSIX only (os.killpg, os.setsid have no Windows
equivalent used here)". A grep across scripts/*.py (2026-09-18) for
os.killpg, os.setsid, fcntl, a hardcoded /tmp/ path, or shutil.which finds
44 further hits, spread across benchmark_harness.py, fault_lab.py,
evad_release_smoke.py, native_evidence.py, product_acceptance.py,
release_note_perturb.py, mobile_workflow.py and others. Every one of those
call sites assumes, rather than reports, that the host it runs on supports
the operation it is about to call. This machine's own measured behaviour
sharpens the point further: killpg on a zombie-only process group returns
EPERM here, not the ESRCH a caller might expect, so even "this call exists
on this platform" is not the same question as "this call behaves the way a
POSIX-only caller assumes it does". This module answers the narrower,
useful question: for a named platform and a named operation, is the
operation supported, unsupported, or has this codebase simply never
measured it. It answers that question from a pinned table, not a live
probe, so a caller can report a POSIX assumption on a platform that does
not support it as a finding, before the call is ever attempted, rather
than discover it at a crash.

THE DECIDING PROPERTY (docs/plan/ORCH-1020-WBS.json, DOM-50.09): platform
differences are reported rather than assumed. A POSIX-only operation
attempted on a platform that does not support it is reported as
unsupported, never silently attempted. An unknown platform is NO-DATA,
never treated as POSIX: guessing that an unrecognised sys.platform string,
or a value that is not even a string, behaves like POSIX is exactly the
assumption this module exists to refuse. The same refusal covers an
unrecognised operation name.

WHAT THIS MODULE DOES NOT DO. It never calls os.killpg, os.setsid,
fcntl.flock, or shutil.which itself, and it never touches the filesystem,
the network, or a subprocess. It is a lookup, the same shape as
host_capability.py's CAPABILITY_TABLE: a fact read from a pinned table,
never a live probe. A caller that wants to know whether THIS process can
call os.killpg right now asks hasattr(os, "killpg") itself; that only ever
answers for the one host already running. This module answers the
cross-platform question a single hasattr cannot: whether a NAMED platform,
possibly one nobody is currently running on, supports a named operation,
so a report can be built and read before any code ever runs there.

Python 3.9 floor, standard library only (json, sys), no network.
"""
import json
import sys

#: The operations this repository's own scripts/*.py actually assume
#: without checking platform first, grepped 2026-09-18, plus one operation
#: kept as a deliberate contrast case (shutil.which, already used as the
#: portable alternative in acceptance_time.py, adapter_conformance.py,
#: cursor_smoke.py and capability_probe.py): an operation this table says
#: "yes" for on every platform, so a reader of this module is never left
#: wondering whether "yes" is even a value the table can produce.
OPERATIONS = (
    "process_group_kill",   # os.killpg / os.setsid: process_containment.py,
                             # fault_lab.py, product_acceptance.py, and others
    "advisory_file_lock",   # fcntl.flock: mobile_workflow.py
    "hardcoded_tmp_path",   # a literal "/tmp/..." path assumed writable
    "which_lookup",         # shutil.which: the portable contrast case
)

#: Platform identifiers this table carries a row for, spelled the way
#: Python's own sys.platform actually returns them: "darwin" (macOS),
#: "linux", "win32" (Windows), "cygwin" (a POSIX emulation layer on top of
#: Windows). A real POSIX sys.platform value this table has no row for,
#: such as "aix", is deliberately left out: this estate has never run or
#: shipped there, so a row for it would be an invented fact rather than a
#: measured one, and it falls through to the same unknown-platform NO-DATA
#: path as any other value capability() has not been given a row for.
PLATFORMS = ("darwin", "linux", "win32", "cygwin")

#: platform -> operation -> "yes", "no", or a NO-DATA string naming why.
#: "yes" and "no" are platform facts: POSIX process-group signalling and
#: fcntl advisory locks do not exist on native Windows, and a literal /tmp
#: path is not guaranteed to exist or be writable there either (Python's
#: own tempfile module resolves TEMP/TMP on Windows instead of assuming
#: /tmp). cygwin gets NO-DATA for the three POSIX-shaped operations rather
#: than a guessed "yes": it layers a POSIX emulation over win32, and this
#: repository has never measured os.killpg, fcntl.flock, or a hardcoded
#: /tmp path under it. Answering "yes" there because Cygwin looks
#: POSIX-like is exactly the assumption this module exists to refuse.
CAPABILITY_TABLE = {
    "darwin": {
        "process_group_kill": "yes",
        "advisory_file_lock": "yes",
        "hardcoded_tmp_path": "yes",
        "which_lookup": "yes",
    },
    "linux": {
        "process_group_kill": "yes",
        "advisory_file_lock": "yes",
        "hardcoded_tmp_path": "yes",
        "which_lookup": "yes",
    },
    "win32": {
        "process_group_kill": "no",
        "advisory_file_lock": "no",
        "hardcoded_tmp_path": "no",
        "which_lookup": "yes",
    },
    "cygwin": {
        "process_group_kill": "NO-DATA: Cygwin process group signalling "
            "has never been measured on this estate",
        "advisory_file_lock": "NO-DATA: Cygwin advisory file locking has "
            "never been measured on this estate",
        "hardcoded_tmp_path": "NO-DATA: a hardcoded /tmp path under "
            "Cygwin has never been measured on this estate",
        "which_lookup": "yes",
    },
}

NODATA = "NO-DATA"


def capability(platform, operation):
    """The pinned fact for (platform, operation): exactly "yes", exactly
    "no", or a string starting with NO-DATA naming why. Never probes the
    live machine and never raises: an unrecognised platform (including a
    value that is not even a string) returns NO-DATA naming the platform,
    and an unrecognised operation on a known platform returns NO-DATA
    naming the operation. Neither case is silently read as "yes": that
    refusal is this function's whole reason to exist, so a caller building
    a report never has to special-case an unknown value itself.
    """
    if not isinstance(platform, str) or platform not in CAPABILITY_TABLE:
        return "%s: platform %r has no row in this table" % (NODATA, platform)
    row = CAPABILITY_TABLE[platform]
    if not isinstance(operation, str) or operation not in row:
        return "%s: operation %r has no row in this table" % (NODATA, operation)
    return row[operation]


def report(platform, operations=OPERATIONS):
    """capability() for every operation in `operations` (all of OPERATIONS
    by default), as a dict {operation: fact}. Always returns one entry per
    requested operation, whatever `platform` is: an unknown platform fills
    every entry with its own NO-DATA fact rather than raising, so a caller
    gets a complete report to print or log even for a platform this table
    has never heard of.
    """
    return {operation: capability(platform, operation) for operation in operations}


def assumption_is_safe(platform, operation):
    """True only when capability(platform, operation) is exactly "yes".
    False for an explicit "no", and False for any NO-DATA fact, whether
    that NO-DATA came from an unknown platform, an unknown operation, or a
    known platform with a cell this table marks unmeasured (cygwin's three
    POSIX-shaped rows). A caller deciding whether it is safe to go ahead
    and call a POSIX-only operation must never read "we do not know" as
    permission: that is the unknown-input edge case this function exists
    to close off, kept separate from capability() (which returns the raw
    three-way fact, not a bool) so a caller that wants the reason string
    still gets it from capability(), and a caller that only wants a
    go/no-go gate gets exactly that from here.
    """
    return capability(platform, operation) == "yes"


def findings(platform, operations=OPERATIONS):
    """One finding string per operation in `operations` that is not
    safely "yes" on `platform` (per assumption_is_safe), naming the
    operation, the platform, and the fact behind it. An operation whose
    fact is "yes" produces no finding. An empty list means every named
    operation is safely "yes" on this platform. This is the report the
    deciding property asks for: a POSIX assumption on a platform that does
    not support it, or on a platform this table has never measured,
    surfaces here by name instead of failing silently at the call site.
    """
    return [
        "%s on %s: %s" % (operation, platform, capability(platform, operation))
        for operation in operations
        if not assumption_is_safe(platform, operation)
    ]


def current_platform():
    """sys.platform of the process actually running this call. The one
    function in this module that reads the live machine; every other
    function takes platform as a plain string argument so a test can
    exercise any platform in PLATFORMS, or one this table has never heard
    of, without depending on which machine runs the test.
    """
    return sys.platform


def main(argv=None):
    """CLI entry point. `argv` is the argument list excluding the program
    name (sys.argv[1:] when argv is None): its first element, if present,
    is the platform to report on; with no argument, current_platform() is
    used. Prints one JSON object with "platform" and "capabilities" keys
    and returns 0.
    """
    args = sys.argv[1:] if argv is None else list(argv)
    platform = args[0] if args else current_platform()
    payload = {"platform": platform, "capabilities": report(platform)}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
