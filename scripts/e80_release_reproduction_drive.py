#!/usr/bin/env python3
"""E80's done check, run end to end against a fixture release.

THE DEFECT this proves closed. The v1.0.1 release note's headline claim was a
HUB COMMIT, and a clone of the public repository cannot resolve it (`git
cat-file` exits 128 on it), so a release integrity reviewer rebuilding the
release from source could not even start. scripts/reproduce_export.py's
original mode has the same problem by construction: --source-rev names a hub
revision that only the private hub holds.

WHAT THIS DRIVES, and why a fixture rather than the real tag: the claim is
about the NEXT tag, which does not exist yet, so the only honest way to check
it before cutting is to build a fixture release out of this tree and verify it
the way a stranger would. It:

  1. builds the export tree with the exporter's OWN build_export_tree,
  2. writes the manifest and a release note carrying its digest,
  3. commits and tags it in a throwaway repository, committing with
     `git add -A -f` exactly as export_public.py does (a plain `git add -A`
     obeys the hub .gitignore the export copies in and silently drops four
     ignored fixture CSVs the exporter really ships: this drive found that on
     its first run, and it is the estate's recorded "the manifest describes
     the export tree but the commit obeys the copied ignore" failure),
  4. runs the release note's own reproduction command FROM INSIDE that
     repository, with the clone's own copy of the script and no hub access,
  5. tampers one byte in a shipped runtime file, re-tags, and runs it again.

PASS requires BOTH directions: exit 0 on the untouched tag and exit 1 on the
tampered one. A check that only ever sees the clean case proves nothing about
whether it can fail.

EXITS: 0 both directions behaved; 1 either did not. Python 3, standard
library only, no network. Costs about a minute: it builds the export tree.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import export_public as EP  # noqa: E402
import reproduce_export as RE  # noqa: E402

VERSION = "9.9.9"
TAG = "v" + VERSION
VICTIM = os.path.join("bundle", "runtime", "brother_run.py")


def run(cmd, cwd):
    """A fixture step that fails is a broken drive, not a verdict about the
    product: it stops here rather than being reported as a FAIL below."""
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit("NO-DATA: fixture step failed: %s\n%s"
                          % (" ".join(cmd), proc.stderr.strip()))
    return proc.stdout


def build_fixture_release(dest):
    """The export tree, its manifest, its note, committed and tagged."""
    allowlist = EP.load_allowlist()
    if allowlist is None:
        raise SystemExit("NO-DATA: no export allowlist; nothing to export")
    copied = EP.build_export_tree(dest, allowlist, root=ROOT)
    if not copied:
        raise SystemExit("NO-DATA: the allowlist copied nothing from %s" % ROOT)
    manifest = RE.manifest_from_dir(dest)
    digest = RE.manifest_digest(manifest)
    os.makedirs(os.path.join(dest, "docs", "releases"), exist_ok=True)
    with open(os.path.join(dest, RE.manifest_path_for(VERSION)), "w",
              encoding="utf-8") as fh:
        fh.write(manifest)
    with open(os.path.join(dest, "docs", "releases", "%s.md" % VERSION), "w",
              encoding="utf-8") as fh:
        fh.write("# Brother %s\n\nExport manifest digest `%s` over %d "
                 "exported file(s).\n"
                 % (VERSION, digest, len(manifest.splitlines())))
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.email", "khalil@example.invalid"],
                ["git", "config", "user.name", "Khalil Maaouni"],
                ["git", "add", "-A", "-f"],
                ["git", "commit", "-q", "-m", "export"],
                ["git", "tag", TAG]):
        run(cmd, dest)
    return len(manifest.splitlines())


def reproduce_from(dest):
    """The release note's own command, run by the clone's own copy."""
    proc = subprocess.run([sys.executable, "scripts/reproduce_export.py",
                            "--verify-tree", "--tag", TAG],
                           cwd=dest, capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "").strip()


def main():
    dest = tempfile.mkdtemp(prefix="e80-release-drive-")
    try:
        count = build_fixture_release(dest)
        print("fixture release %s built and tagged: %d file(s) in the manifest"
              % (TAG, count))

        # The other half of the same trial: export_public.load_allowlist()
        # looks for docs/plan/EXPORT-ALLOWLIST.txt and REFUSES when it is
        # absent, and the tag did not carry it, so both scripts refused on a
        # public clone before either could do any work.
        shipped_allowlist = os.path.join(dest, "docs", "plan",
                                          "EXPORT-ALLOWLIST.txt")
        carried = EP.load_allowlist(shipped_allowlist)
        print("the clone carries its own allowlist: %s (%s entries)"
              % (os.path.isfile(shipped_allowlist),
                 len(carried) if carried is not None else "NO-DATA"))
        allowlist_ok = carried is not None and len(carried) > 0

        print("\n1. the reproduction command, from the clone, no hub access")
        clean_code, clean_out = reproduce_from(dest)
        print(clean_out)
        print("exit=%d" % clean_code)

        print("\n2. one byte changed in %s" % VICTIM)
        victim = os.path.join(dest, VICTIM)
        with open(victim, "rb") as fh:
            data = fh.read()
        with open(victim, "wb") as fh:
            fh.write(data + b"\n# tampered\n")
        run(["git", "add", "-A", "-f"], dest)
        run(["git", "commit", "-q", "-m", "tamper"], dest)
        run(["git", "tag", "-f", TAG], dest)
        bad_code, bad_out = reproduce_from(dest)
        print(bad_out)
        print("exit=%d" % bad_code)

        ok = clean_code == 0 and bad_code == 1 and allowlist_ok
        print("\n%s: the clone loads its own allowlist (%s), the untouched "
              "tag reproduces (exit %d) and a one byte tamper is caught "
              "(exit %d)" % ("PASS" if ok else "FAIL", allowlist_ok,
                              clean_code, bad_code))
        return 0 if ok else 1
    finally:
        shutil.rmtree(dest, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
