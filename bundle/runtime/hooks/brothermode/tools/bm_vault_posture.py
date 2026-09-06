#!/usr/bin/env python3
"""bm_vault_posture: the encryption posture census (WBS row VB8-01).

WHY THIS EXISTS. Nothing in this estate ever asked "is the disk holding the vault, and
every derived copy the tools write from it, actually encrypted at rest". The vault is
plain files (docs/VAULT-TRUST-BOUNDARY.md: "no database server, no per-file permission
bit beyond the operating system's own"), and the operating system's own disk-encryption
state is exactly the layer that trust-boundary page names as the real security boundary
in single-machine mode. This module measures that layer and nothing more.

STORAGE STATE, macOS only. `diskutil apfs list` is the OS's own inventory of every APFS
volume, each carrying its own FileVault line (an "Encrypted:" line too, on the small
number of volume roles that print one, e.g. the signed System volume). Matching a
vault path to ONE of those volumes by string-prefixing its Mount Point does not work
in general: macOS firmlinks make `/Users/...`, `/Library/...` and friends resolve
onto the Data volume at the kernel level while `os.path.realpath` (which only
resolves symlinks, not firmlinks) still reports the path as `/Users/...`, which is
not a prefix of the Data volume's own reported mount point (`/System/Volumes/Data`).
Measured directly on this machine: naive prefix matching against Mount Point picked
the wrong volume (the signed System volume) for an ordinary home-directory path.
`df`, a second OS tool, resolves firmlinks correctly and reports the real backing
device (`/dev/diskNsN`); this module uses `df` to name the device, then reads THAT
device's own block out of `diskutil apfs list`'s output. Any failure anywhere in that
chain -- wrong platform, `diskutil` or `df` missing, either command failing, or no
matching device in the apfs list -- prints NO-DATA with the reason. It NEVER falls
back to guessing "probably encrypted" from anything but the OS's own answer.

DERIVED-STORE CENSUS. Every derived copy of vault content this estate's own tools
write, at its REAL resolved path -- loaded from the module that owns each path by
the same by-path import every sibling contract module in tools/ already uses, never
re-typed here, so this census cannot silently drift from where the data actually
lives:

  sqlite retrieval index    bm_vault.INDEX_PATH
  embeddings / vectors      the SAME file (bm_vault.py's `vectors` table lives inside
                            the retrieval index; there is no second file)
  query cache               vault_recall_hook.SEEN (the recall hook's own seen-note
                            cache, the same path bm_vault_retention.py already names
                            as a MANUAL follow-up it cannot reach)
  the audit file            bm_vault_audit.AUDIT_PATH
  the answer ledger         bm_vault.LEDGER_PATH
  outcomes                  bm_vault_ledger.OUTCOME_PATH
  serve logs                bm_vault_serve.py writes none today (checked by reading
                            the module; reported ABSENT, not skipped)

An absent store is reported absent, with its resolved path, never skipped from the
list. A store that resolves onto a DIFFERENT volume than the vault itself gets its
own, independently measured, volume verdict; nothing here assumes co-location.

THE HONEST LIMIT. Embedding and full-text search both need the plaintext bytes in
memory to do their job; nothing in this estate's retrieval path (bm_vault.py, the
bm-embed subprocess) works on ciphertext. This module reports what the DISK does
when the machine is off or the volume is locked, never a claim about secrecy while
the machine is on and unlocked and a process is actually reading these files.

Exit codes: 0 the report ran (verdicts may individually be NO-DATA), 2 an unknown verb.
Python 3.9, standard library only, no network. No em or en dashes anywhere in this file.
"""
import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))

VOLUME_HEADER = re.compile(r"\bVolume\s+(disk\S+)\b")
#: A stripped line's own label prefix -- never `.search()`ed against the raw line,
#: because "Snapshot Mount Point:" contains "Mount Point:" as a substring and a
#: bare search matched it, silently overwriting the volume's real mount point with
#: the snapshot's (measured: this is what first sent a real vault path to the wrong
#: volume). Stripping the tree-drawing characters first and anchoring at the start
#: of what remains is what tells the two labels apart.
_TREE_CHARS = "|+->"


def _load_by_path(name, path):
    """Same by-path import technique every sibling contract module in tools/ already
    uses (bm_vault_cli.py's _load_by_path, bm_vault.py's _load_bm_vault_authority and
    neighbors): a bare `import bm_vault` only resolves by accident of sys.path, and
    this file sets up none of its own."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load(fname, mname=None):
    return _load_by_path(mname or os.path.splitext(fname)[0], os.path.join(_TOOLS_DIR, fname))


# ---------------------------------------------------------------------------
# STORAGE STATE: the OS's own answer, parsed, never guessed.
# ---------------------------------------------------------------------------

def _real_apfs_list(diskutil_bin):
    """Runs `diskutil apfs list` for real. Returns (text, None) on success or
    (None, reason) on any failure. One of the two functions the test seam replaces."""
    try:
        proc = subprocess.run([diskutil_bin, "apfs", "list"], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return None, "diskutil apfs list failed to run: %s" % e
    if proc.returncode != 0:
        return None, "diskutil apfs list exited %d: %s" % (
            proc.returncode, proc.stderr.strip() or "no stderr")
    return proc.stdout, None


def _real_device_for_path(path):
    """Runs `df -P <path>` for real. Returns (device_id, None) on success (e.g.
    "disk3s5", the "/dev/" prefix stripped to match diskutil's own device naming) or
    (None, reason) on any failure. `df` (unlike a plain prefix match against
    diskutil's Mount Point) correctly resolves a macOS firmlink, which is why this
    is the OS tool asked "which volume actually backs this path" rather than
    string-matching mount points ourselves (see the module docstring)."""
    try:
        proc = subprocess.run(["df", "-P", path], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return None, "df -P failed to run: %s" % e
    if proc.returncode != 0:
        return None, "df -P exited %d: %s" % (proc.returncode, proc.stderr.strip() or "no stderr")
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    if len(lines) < 2:
        return None, "df -P produced no data line for %s" % path
    fs = lines[1].split()[0] if lines[1].split() else ""
    if not fs.startswith("/dev/"):
        return None, "df -P reported an unexpected filesystem field %r for %s" % (fs, path)
    return fs[len("/dev/"):], None


def _parse_apfs_volumes(text):
    """Every volume block in `diskutil apfs list` output, keyed by device id (e.g.
    "disk3s5"), each holding mount_point (informational only), filevault and
    encrypted (each None when that line never appeared for this volume). A new
    "Volume diskX" header line always starts a fresh block; every label line before
    the next header belongs to the block most recently opened, which is how
    diskutil's own indented tree output is structured."""
    volumes = {}
    current = None
    for line in text.splitlines():
        m = VOLUME_HEADER.search(line)
        if m:
            current = {"mount_point": None, "filevault": None, "encrypted": None}
            volumes[m.group(1)] = current
            continue
        if current is None:
            continue
        stripped = line.strip(" " + _TREE_CHARS)
        if stripped.startswith("Mount Point:"):
            val = stripped[len("Mount Point:"):].strip()
            current["mount_point"] = None if (not val or val == "Not Mounted") else val
            continue
        if stripped.startswith("FileVault:"):
            current["filevault"] = stripped[len("FileVault:"):].strip() or None
            continue
        if stripped.startswith("Encrypted:"):
            current["encrypted"] = stripped[len("Encrypted:"):].strip() or None
    return volumes


def _verdict_for_volume(device, vol):
    """(verdict, detail). verdict is "encrypted", "plaintext" or "NO-DATA". Prefers the
    volume's own "Encrypted:" line when diskutil printed one (the literal at-rest
    answer for that specific volume); falls back to "FileVault:" otherwise, which is
    what most volumes (including the ordinary user Data volume) actually carry."""
    for field, label in (("encrypted", "Encrypted"), ("filevault", "FileVault")):
        val = vol.get(field)
        if val is None:
            continue
        if val.startswith("Yes"):
            return "encrypted", "%s: %s (device %s)" % (label, val, device)
        if val == "No":
            return "plaintext", "%s: %s (device %s)" % (label, val, device)
        return "NO-DATA", "%s printed an unrecognized value %r (device %s)" % (
            label, val, device)
    return "NO-DATA", "no FileVault or Encrypted line for device %s" % device


def _nearest_existing_ancestor(path):
    """path itself if it exists, else the first existing directory walking up its
    dirname chain, else "/". `df` (unlike diskutil apfs list) needs a path that
    actually exists; a derived store not yet written (e.g. an outcomes file no
    recall has appended to) still lives on some real volume, and the nearest
    existing ancestor directory is that same volume in every layout this estate
    creates (it never mkdirs a store onto a separate mount from its parent)."""
    p = os.path.abspath(path)
    while p and not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            return "/"
        p = parent
    return p


def storage_state(path, apfs_list_fn=_real_apfs_list, device_fn=_real_device_for_path):
    """(verdict, detail) for the volume backing `path`. verdict is one of "encrypted",
    "plaintext" or "NO-DATA" (detail then names the reason). Never raises: every
    failure mode -- wrong platform, diskutil or df absent, either command failing,
    output that does not name a device diskutil also lists -- is a NO-DATA return,
    not an exception. apfs_list_fn/device_fn are the two test seams: pass replacements
    to drive the verdict off an injected OS answer instead of this machine's real
    disk (they are explicit parameters, never a module attribute a test would have to
    monkeypatch and hope a default argument re-reads -- a default binds at function
    definition time, not at call time)."""
    if sys.platform != "darwin":
        return "NO-DATA", "unsupported platform: %s (storage state is macOS-only today)" \
            % sys.platform
    diskutil_bin = shutil.which("diskutil")
    if not diskutil_bin:
        return "NO-DATA", "diskutil not found on PATH"
    device, err = device_fn(_nearest_existing_ancestor(path))
    if device is None:
        return "NO-DATA", err
    text, err = apfs_list_fn(diskutil_bin)
    if text is None:
        return "NO-DATA", err
    volumes = _parse_apfs_volumes(text)
    vol = volumes.get(device)
    if vol is None:
        return "NO-DATA", "no diskutil apfs list entry for device %s (%s's own filesystem)" \
            % (device, path)
    return _verdict_for_volume(device, vol)


# ---------------------------------------------------------------------------
# DERIVED-STORE CENSUS
# ---------------------------------------------------------------------------

def _derived_stores():
    """[(label, path_or_None)], path_or_None is None for a store the estate's own
    tools never create (serve logs today). Every path is read off the module that
    owns it, never re-typed, so this list cannot drift from the real resolution."""
    bm_vault = _load("bm_vault.py")
    bm_vault_audit = _load("bm_vault_audit.py")
    bm_vault_ledger = _load("bm_vault_ledger.py")
    vault_recall_hook = _load("vault_recall_hook.py")
    return [
        ("sqlite retrieval index", bm_vault.INDEX_PATH),
        ("embeddings/vectors (co-located in the retrieval index above, same file)",
         bm_vault.INDEX_PATH),
        ("query cache (recall hook SEEN cache)", vault_recall_hook.SEEN),
        ("audit file", bm_vault_audit.AUDIT_PATH),
        ("answer ledger", bm_vault.LEDGER_PATH),
        ("outcomes", bm_vault_ledger.OUTCOME_PATH),
        ("serve logs", None),
    ]


def cmd_report(args):
    vault = args.vault or os.environ.get("BM_VAULT_ROOT") or os.environ.get("BROTHERMODE_VAULT")
    print("== storage state ==")
    if not vault:
        print("NO-DATA: no vault resolved (pass --vault, or set BM_VAULT_ROOT / "
              "BROTHERMODE_VAULT)")
    else:
        verdict, detail = storage_state(vault)
        print("vault path: %s" % vault)
        print("verdict: %s (%s)" % (verdict, detail))

    print()
    print("== derived-store census ==")
    for label, path in _derived_stores():
        if path is None:
            print("%s: ABSENT (this estate's tools write none today)" % label)
            continue
        exists = os.path.exists(path)
        verdict, detail = storage_state(path)
        print("%s: %s%s -> %s (%s)"
              % (label, path, "" if exists else " (not yet written)", verdict, detail))

    print()
    print("== the honest limit ==")
    print("embedding and search require plaintext; posture reports what the disk does, "
          "not end-to-end secrecy.")
    return 0


def _build_parser():
    p = argparse.ArgumentParser(prog="bm_vault_posture.py")
    sub = p.add_subparsers(dest="verb")
    report = sub.add_parser("report", help="print the storage state and derived-store census")
    report.add_argument("--vault", default=None)
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        _build_parser().print_help()
        return 0
    if argv[0] != "report":
        sys.stderr.write("bm_vault_posture: unknown verb %r; known: report\n" % argv[0])
        return 2
    args = _build_parser().parse_args(argv)
    return cmd_report(args)


if __name__ == "__main__":
    sys.exit(main())
