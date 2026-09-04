#!/bin/sh
# verify-install.sh: does an installed copy of this repository match a
# published checksum manifest, byte for byte?
#
# This is the direct answer to "how do I know what I installed". The
# install instruction (docs/RELEASE.md, README.md) clones a git ref into
# ~/.claude/skills/brothermode, and that code then runs automatically on
# every Claude Code session via hooks. A manifest you cannot check against
# is not a security control, it is a claim; this script is the check.
#
# POSIX sh only, no bashisms, same portability intent as checksums.sh: run
# directly under both bash and dash to confirm neither rejects it. Uses no
# git commands itself, on purpose: it only ever reads the manifest and
# re-hashes the files it names, so it works identically on a plain
# directory copy (no .git present) and on a real clone.
#
# Usage:
#   scripts/verify-install.sh [manifest-file] [installed-dir]
#
#   manifest-file  defaults to <installed-dir>/CHECKSUMS.sha256
#   installed-dir  defaults to this script's own parent repository root
#
# Typical use: you cloned a tagged release into
# ~/.claude/skills/brothermode. The release ships CHECKSUMS.sha256 at the
# repository root (docs/RELEASE.md, step "cut the release", explains how a
# maintainer produces it with checksums.sh). Run this with no arguments
# from inside that clone, or point it at any two directories explicitly:
#
#   scripts/verify-install.sh
#   scripts/verify-install.sh /path/to/CHECKSUMS.sha256 ~/.claude/skills/brothermode
#   scripts/verify-install.sh --selftest   # checks the gitignored-extra split, see below

set -e

# Self-check for the gitignored-extra split below (git-repo branch only; the
# non-git branch is untouched by this change and needs no new check). No
# framework, no new file to register in any registry: it builds a scratch
# git repo under mktemp, calls this same script against it twice, and
# asserts the two outcomes the split promises. Run it directly with
# `scripts/verify-install.sh --selftest`.
if [ "${1:-}" = "--selftest" ]; then
    SDIR=$(mktemp -d 2>/dev/null || echo "/tmp/bm-verify-install-selftest.$$")
    trap 'rm -rf "$SDIR"' EXIT INT TERM
    STARGET="$SDIR/target"
    mkdir -p "$STARGET"
    git -C "$STARGET" init -q
    printf 'ok\n' > "$STARGET/kept.txt"
    printf 'ignored.txt\n' > "$STARGET/.gitignore"
    printf 'x\n' > "$STARGET/ignored.txt"
    git -C "$STARGET" add kept.txt .gitignore
    git -C "$STARGET" -c user.email=selftest@example.invalid -c user.name=selftest \
        commit -q -m init
    if command -v sha256sum >/dev/null 2>&1; then
        SHASH=$(sha256sum "$STARGET/kept.txt" | cut -c1-64)
        GHASH=$(sha256sum "$STARGET/.gitignore" | cut -c1-64)
    else
        SHASH=$(shasum -a 256 "$STARGET/kept.txt" | cut -c1-64)
        GHASH=$(shasum -a 256 "$STARGET/.gitignore" | cut -c1-64)
    fi
    {
        printf '%s  kept.txt\n' "$SHASH"
        printf '%s  .gitignore\n' "$GHASH"
    } > "$STARGET/CHECKSUMS.sha256"

    # Case 1: only a gitignored extra file present -> must PASS (exit 0).
    if sh "$0" "$STARGET/CHECKSUMS.sha256" "$STARGET" >/dev/null 2>&1; then
        RC1=0
    else
        RC1=$?
    fi
    if [ "$RC1" -ne 0 ]; then
        echo "SELFTEST FAILED: a gitignored extra alone should PASS, got exit $RC1" >&2
        exit 1
    fi

    # Case 2: add a non-ignored extra -> must FAIL and name it in the output.
    printf 'y\n' > "$STARGET/rogue.txt"
    if OUT=$(sh "$0" "$STARGET/CHECKSUMS.sha256" "$STARGET" 2>&1); then
        RC2=0
    else
        RC2=$?
    fi
    if [ "$RC2" -eq 0 ]; then
        echo "SELFTEST FAILED: a non-ignored extra should FAIL, got exit 0" >&2
        exit 1
    fi
    case "$OUT" in
        *rogue.txt*) : ;;
        *) echo "SELFTEST FAILED: FAILED output did not name rogue.txt:" >&2
           echo "$OUT" >&2
           exit 1 ;;
    esac

    echo "SELFTEST OK: a gitignored extra alone passes; adding a non-ignored" \
         "extra fails the run and names it."
    exit 0
fi

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
DEFAULT_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)

TARGET="${2:-$DEFAULT_ROOT}"
MANIFEST="${1:-$TARGET/CHECKSUMS.sha256}"

if [ ! -f "$MANIFEST" ]; then
    echo "verify-install: no manifest found at $MANIFEST" >&2
    echo "verify-install: a maintainer generates one with scripts/checksums.sh" \
         "as part of cutting a release (see docs/RELEASE.md); if this repo" \
         "predates that (no tagged release exists yet as of this writing)," \
         "there is nothing to verify against." >&2
    exit 2
fi

if command -v sha256sum >/dev/null 2>&1; then
    hash_file() { sha256sum "$1" | cut -c1-64; }
elif command -v shasum >/dev/null 2>&1; then
    hash_file() { shasum -a 256 "$1" | cut -c1-64; }
else
    echo "verify-install: neither sha256sum nor shasum is on PATH; cannot check anything" >&2
    exit 1
fi

WORKDIR=$(mktemp -d 2>/dev/null || echo "/tmp/bm-verify-install-work.$$")
mkdir -p "$WORKDIR"
trap 'rm -rf "$WORKDIR"' EXIT INT TERM

OK=0
MISMATCHED=0
MISSING=0
TYPESWAPPED=0
: > "$WORKDIR/manifest_paths"

# SBE7 (2026-08-16): a registry is a file this project's own tooling reads
# to know what exists (PO-6, CLAUDE.md: "before adding an entry to any
# registry, open the file that READS that registry first"). A registry that
# goes missing or stale across the install boundary is a silent capability
# gap, not ordinary doc churn, so it gets called out on its own line instead
# of blending into a long MISMATCH/MISSING list where it is easy to miss.
# This is the same manifest-vs-disk loop below, not a second comparison
# mechanism: it only tags a subset of the outcomes that loop already
# produces. Extend this list when a new registry is added (see the file that
# reads it, per PO-6, before adding here).
REGISTRIES="tools/write_sites.json tools/toolkit_routes.json"
REGISTRY_DRIFT=0
REGISTRY_NAMES=""
is_registry() {
    case " $REGISTRIES " in
        *" $1 "*) return 0 ;;
        *) return 1 ;;
    esac
}

# Manifest lines are "<64 hex chars><two spaces><path>", the format both
# sha256sum and shasum -a 256 produce. Splitting by fixed column position
# (not by whitespace) is deliberate: a path may itself contain spaces, and
# field-splitting would cut it apart.
while IFS= read -r line; do
    [ -z "$line" ] && continue
    expected=$(printf '%s' "$line" | cut -c1-64)
    path=$(printf '%s' "$line" | cut -c67-)
    [ -z "$path" ] && continue
    printf '%s\n' "$path" >> "$WORKDIR/manifest_paths"
    full="$TARGET/$path"
    # FINDING 4 (2026-07-27): check the entry TYPE, by lstat, before hashing
    # anything. scripts/checksums.sh refuses to manifest anything that is not
    # a regular file, so every path named here is attested to BE a regular
    # file; that is the manifest's type claim and this is where it is
    # enforced. It matters because `-f` follows symlinks: a regular file
    # swapped on disk for a symlink pointing at an attacker's copy of the
    # same bytes used to hash equal and be reported as a match. `-L` is the
    # only test that sees the link rather than its target, so it goes first,
    # and a broken symlink (where -e is false) is caught here too rather than
    # being misreported as merely MISSING.
    if [ -L "$full" ]; then
        echo "TYPESWAP:  $path (a symlink on disk, but the manifest attests a regular file)"
        TYPESWAPPED=$((TYPESWAPPED + 1))
        continue
    fi
    if [ ! -e "$full" ]; then
        echo "MISSING:   $path"
        MISSING=$((MISSING + 1))
        if is_registry "$path"; then
            echo "REGISTRY:  $path is a tracked registry and is missing from this install"
            REGISTRY_DRIFT=$((REGISTRY_DRIFT + 1))
            REGISTRY_NAMES="$REGISTRY_NAMES $path"
        fi
        continue
    fi
    if [ ! -f "$full" ]; then
        echo "TYPESWAP:  $path (not a regular file on disk, but the manifest attests one)"
        TYPESWAPPED=$((TYPESWAPPED + 1))
        continue
    fi
    actual=$(hash_file "$full")
    if [ "$actual" = "$expected" ]; then
        OK=$((OK + 1))
    else
        echo "MISMATCH:  $path"
        MISMATCHED=$((MISMATCHED + 1))
        if is_registry "$path"; then
            echo "REGISTRY:  $path is a tracked registry and differs from this install"
            REGISTRY_DRIFT=$((REGISTRY_DRIFT + 1))
            REGISTRY_NAMES="$REGISTRY_NAMES $path"
        fi
    fi
done < "$MANIFEST"

# Second direction of the check. The loop above only asks "does every file
# the manifest NAMES match on disk", which never notices a file that was
# ADDED (an extra file is neither a MISMATCH nor a MISSING), so this script
# used to report PASSED with a planted extra file still present; see
# docs/superpowers/specs/2026-07-26-final-blockers.md, BLOCKER 2, for the
# reproduction that motivated this second pass. It asks the other
# direction instead: does every file that actually EXISTS on disk appear
# in the manifest. The exclusion list below is the same one
# scripts/checksums.sh already applies when it cannot use git (kept in
# sync by comment in both files, since each script is self-contained POSIX
# sh with no shared file to hold this list once): machine state and
# generated files this project's own .gitignore already keeps out of what
# git tracks, so their absence from the manifest is expected, not an added
# file. Stated limit: if $TARGET itself contains a character `find -path`
# treats as glob syntax (*, ?, [ ]), the exclusions below can under- or
# over-match; named here rather than silently assumed correct.
MANIFEST_ABS=$(cd "$(dirname "$MANIFEST")" && pwd)/$(basename "$MANIFEST")
MANIFEST_REL=$(printf '%s\n' "$MANIFEST_ABS" | sed "s|^$TARGET/||")

#
# FINDING 4 (2026-07-27): this enumeration used to say `-type f`, and that
# one flag was a hole straight through the whole check. POSIX find without
# -L does not follow symlinks, so a symlink is -type l and NEVER matches
# -type f: an unmanifested symlink was invisible to this loop, and invisible
# to the manifest loop above too, because the manifest by definition never
# named it. The attack that exercised it: plant tools/json.py as a symlink to
# an attacker-controlled file, and this script prints PASSED and exits 0,
# while every session hook that runs `python3 tools/bm_telemetry.py` loads
# the attacker's module first, because Python puts the script's own directory
# at the front of sys.path and shadowing the standard library from there is
# trivial. Reproduced end to end before the fix (exit 0, nothing reported).
#
# `! -type d` is the fix: it enumerates EVERY non-directory entry, which is
# regular files, symlinks (including symlinks that point at directories,
# which are type l and not type d), devices, FIFOs and sockets. Real
# directories are the only thing left out, and a real directory is inert
# here: it holds no bytes of its own, nothing executes it, and anything
# inside it is enumerated on its own account. The excluded roots are now
# also matched as bare names, not only as "<root>/*" prefixes, because a
# symlink NAMED .brothermode or threads was previously skipped for free by
# -type f and would otherwise walk right back in through the exclusion list.
# find is not asked to descend through any link (no -L, deliberately), so
# nothing below a planted link is traversed or trusted.
#
# E80 (2026-09-04): the project working-state names below are matched as
# ROOT-ANCHORED PATHS, not as bare names. A bare ! -name 'CANVAS.md' matches
# at any depth, and the product ships project-template/CANVAS.md and
# project-template/DELIVERY-PACKET.md as tracked template files, so those two
# shipped files were dropped from this listing while the manifest still named
# them: a clean install reported them MISSING. Root-anchored keeps the
# intent (a user's own CANVAS.md at the install root is state, not payload)
# without reaching into the shipped template directory. The same change is
# in scripts/checksums.sh, which builds the manifest this listing is
# compared against; the two lists must stay in step.
find "$TARGET" ! -type d \
    ! -path "$TARGET/.git" \
    ! -path "$TARGET/.git/*" \
    ! -path "$TARGET/.brothermode" \
    ! -path "$TARGET/.brothermode/*" \
    ! -path '*/__pycache__/*' \
    ! -path "$TARGET/threads" \
    ! -path "$TARGET/threads/*" \
    ! -path "$TARGET/.superpowers" \
    ! -path "$TARGET/.superpowers/*" \
    ! -path "$TARGET/.venv-embed" \
    ! -path "$TARGET/.venv-embed/*" \
    ! -path "$TARGET/Documentation" \
    ! -path "$TARGET/Documentation/*" \
    ! -path "$TARGET/.claude" \
    ! -path "$TARGET/.claude/*" \
    ! -path "$TARGET/Handover" \
    ! -path "$TARGET/Handover/*" \
    ! -path "$TARGET/Handover-*" \
    ! -path "$TARGET/Handover-*/*" \
    ! -name '.DS_Store' \
    ! -name '*.bak*' \
    ! -path "$TARGET/STATE.md" \
    ! -path "$TARGET/CANVAS.md" \
    ! -path "$TARGET/CANVAS-*.md" \
    ! -path "$TARGET/DELIVERY-PACKET.md" \
    ! -path "$TARGET/DELIVERY-PACKET-*.md" \
    ! -path "$TARGET/PROJECT-VIEW.html" \
    > "$WORKDIR/installed_raw"

# An EXTRA entry is exactly the shape of a planted backdoor (see the FAILED
# explanation below), and that alarm must not soften. But a real working
# checkout of THIS repository also carries its own runtime state that was
# never meant to be installed anywhere (.sbe/tasks.json and friends), and
# that state is already named in this repository's own .gitignore. Asking
# git which of the two an entry is, rather than hand-listing paths here a
# second time, means the exclusion list can never drift out of sync with the
# .gitignore that is the actual source of truth for "this is expected repo
# state, not a backdoor".
#
# This distinction is drawn ONLY when $TARGET is itself a git repository. A
# real installed copy (the case this script exists for) is a plain directory
# with no .git and no .gitignore to consult, so there is nothing to ask, and
# every extra entry there keeps today's full alarm and fails the run, exactly
# as before. That branch is the security-critical one and is unchanged.
IS_GIT=0
if git -C "$TARGET" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    IS_GIT=1
fi

EXTRA_ALARM=0
EXTRA_IGNORED=0
while IFS= read -r full; do
    [ -z "$full" ] && continue
    rel=$(printf '%s\n' "$full" | sed "s|^$TARGET/||")
    [ "$rel" = "$MANIFEST_REL" ] && continue
    if grep -q -x -F "$rel" "$WORKDIR/manifest_paths"; then
        continue
    fi
    # Name the type, by lstat, so the report distinguishes an unexpected but
    # ordinary file from a planted link or device. -L comes first for the
    # usual reason: it is the only test that reports on the entry rather than
    # on whatever the entry points at.
    #
    # A symlink is deliberately NEVER moved into the quiet, ignored-by-git
    # bucket below even when git-ignored: a gitignored build or venv
    # directory is exactly where a planted link would be hidden on purpose,
    # and this script's own history (FINDING 4, 2026-07-27) is a symlink
    # planted to shadow a Python import. Loud, every time.
    if [ -L "$full" ]; then
        echo "EXTRA (symlink): $rel"
        EXTRA_ALARM=$((EXTRA_ALARM + 1))
        continue
    fi
    if [ "$IS_GIT" -eq 1 ] && git -C "$TARGET" check-ignore -q -- "$rel"; then
        echo "EXTRA (ignored, not failing): $rel"
        EXTRA_IGNORED=$((EXTRA_IGNORED + 1))
        continue
    fi
    if [ -f "$full" ]; then
        echo "EXTRA:     $rel"
    else
        echo "EXTRA (irregular): $rel"
    fi
    EXTRA_ALARM=$((EXTRA_ALARM + 1))
done < "$WORKDIR/installed_raw"

echo ""
echo "verify-install: checked against $MANIFEST"
echo "verify-install: $OK file(s) match, $MISMATCHED mismatched, $MISSING missing, $TYPESWAPPED wrong type, $EXTRA_ALARM extra (present on disk, absent from the manifest, not ignored by git), $EXTRA_IGNORED extra (present on disk, absent from the manifest, but ignored by this repository's own .gitignore)"
if [ "$REGISTRY_DRIFT" -gt 0 ]; then
    echo "verify-install: $REGISTRY_DRIFT registry file(s) missing or differing:$REGISTRY_NAMES"
fi

if [ "$MISMATCHED" -gt 0 ] || [ "$MISSING" -gt 0 ] || [ "$TYPESWAPPED" -gt 0 ] || [ "$EXTRA_ALARM" -gt 0 ]; then
    echo "verify-install: FAILED. Do not trust this installed copy until you" \
         "understand why the entries above differ from the published manifest." >&2
    if [ "$EXTRA_ALARM" -gt 0 ]; then
        echo "verify-install: an EXTRA entry (not ignored by git, or listed" \
             "as a symlink) is exactly the shape of a planted backdoor: it" \
             "runs automatically along with everything else in this" \
             "installation, and the manifest says nothing about it because" \
             "nothing here declared it. A symlink is the sharpest version of" \
             "that: it can drop attacker-controlled code into a directory" \
             "that is already on Python's import path." >&2
    fi
    if [ "$TYPESWAPPED" -gt 0 ]; then
        echo "verify-install: a TYPESWAP entry means the manifest attests a" \
             "regular file at that path but something else is there now," \
             "typically a symlink pointing somewhere outside this install." \
             "Content hashes cannot catch that on their own, because the hash" \
             "of a link's target can be made to match perfectly." >&2
    fi
    exit 1
fi

echo "verify-install: PASSED. Every entry the manifest names matches on disk,"
echo "verify-install: in content and in type, and no entry exists on disk that"
echo "verify-install: the manifest does not name."
echo "verify-install: this does not prove the manifest itself is authentic;" \
     "it proves your files match whatever manifest you pointed this at. Get" \
     "the manifest from the release you trust (the tag's git history, or a" \
     "release asset), not from the same untrusted channel as the code."
