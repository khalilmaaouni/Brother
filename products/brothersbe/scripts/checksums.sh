#!/bin/sh
# checksums.sh: a deterministic SHA256 manifest of every shipped file.
#
# Why this exists: docs/RELEASE.md explains the question it answers ("how do
# I know what I installed"). The install instruction clones a git branch or
# tag into a location whose code runs automatically on every Claude Code
# session (the hooks wired into ~/.claude/settings.json). This script is the
# maintainer-side half of the answer: it lists every file that ships in a
# release and its SHA256 hash, in a fixed order, so the same source tree
# always produces the same manifest byte for byte. verify-install.sh is the
# user-side half: it re-hashes an installed copy and compares against a
# manifest this script produced. What this pair does NOT do: prove the
# manifest itself is authentic. That half is where you got the manifest from.
#
# POSIX sh only, no bashisms: no arrays, no [[ ]], no local, no process
# substitution, no here-strings.
#
# Usage, always from the repository root:
#   scripts/checksums.sh                    # print the manifest to stdout
#   scripts/checksums.sh CHECKSUMS.sha256   # write it to that file instead
#
# "Shipped file" is defined as: every file git tracks in this repository at
# the commit being released. That is deliberate, not lazy: it is exactly the
# set of bytes a `git clone` (or `git checkout <tag>`) hands a user, so the
# manifest and the install are talking about the same tree by construction,
# and it can never drift from .gitignore because it does not reimplement
# .gitignore's rules. If this is ever run against a tree copied without its
# .git directory, it falls back to a plain filesystem walk with the same
# exclusions .gitignore already documents (STATE.md, __pycache__/,
# .DS_Store, *.pyc, ~$*, *.docx, .superpowers/, docs/superpowers/).

set -e

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$ROOT"

OUT_FILE="$1"

# --- pick a SHA256 tool: Linux ships sha256sum, macOS ships shasum -a 256.
# Detect, never assume: a machine with coreutils installed via Homebrew can
# have both, and a minimal container can have neither.
# The file is fed on STDIN and the manifest line is assembled here, rather than
# letting the tool print its own "<hash>  <name>" line. GNU coreutils escapes a
# filename containing a backslash or a newline: it doubles the backslashes and
# prefixes the line with one, which would write a manifest that verify-install.sh
# reads one column off, so a maintainer on Linux and a maintainer on macOS would
# produce different manifests for the same tree. Building the line here keeps the
# format identical on every platform. Kept deliberately in step with the same
# change in verify-install.sh: a generator and a checker that escape differently
# is worse than either bug alone.
if command -v sha256sum >/dev/null 2>&1; then
    hash_file() { printf '%s  %s\n' "$(sha256sum < "$1" | cut -c1-64)" "$1"; }
elif command -v shasum >/dev/null 2>&1; then
    hash_file() { printf '%s  %s\n' "$(shasum -a 256 < "$1" | cut -c1-64)" "$1"; }
else
    echo "checksums.sh: neither sha256sum nor shasum is on PATH; cannot hash anything" >&2
    exit 1
fi

WORKDIR=$(mktemp -d 2>/dev/null || echo "/tmp/sbe-checksums-work.$$")
mkdir -p "$WORKDIR"
trap 'rm -rf "$WORKDIR"' EXIT INT TERM

# --- build the file list, git-tracked files preferred (see header comment).
#
# `git ls-files -z`, never plain `git ls-files`: the plain form quotes any
# tracked path containing a quote, a backslash, or a non-ASCII byte (git's
# default core.quotePath behavior), and the quoted form it prints is not a
# real path on disk, so a later `[ -f "$f" ]` check fails for it and a
# silent `continue` drops it from the manifest with nothing said. `-z` asks
# git for the exact bytes of every tracked path, NUL-terminated, never
# quoted, so every entry below is always the real, literal path. Residual,
# stated rather than hidden: a path containing a literal newline byte
# (legal on POSIX, vanishingly rare in practice) would still be misread
# once `tr` turns NUL into newline; this list handles the quoting case,
# not every conceivable byte a filename could contain.
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git ls-files -z | tr '\0' '\n' > "$WORKDIR/filelist"
else
    find . -type f \
        ! -path './.git/*' \
        ! -path '*/__pycache__/*' \
        ! -path './.superpowers/*' \
        ! -path './docs/superpowers/*' \
        ! -name '.DS_Store' \
        ! -name '*.pyc' \
        ! -name 'STATE.md' \
        ! -name '~$*' \
        ! -name '*.docx' \
        | sed 's|^\./||' > "$WORKDIR/filelist"
fi

# Never hash the manifest into itself. OUT_FILE, when given, is documented
# above as a path relative to the repository root (the common, supported
# case); strip a leading "./" the same way the fallback file list already
# does, so a re-run after a previous release's manifest is already
# committed does not fold that file's own bytes into the new one.
if [ -n "$OUT_FILE" ]; then
    OUT_FILE_NORMALIZED=$(printf '%s' "$OUT_FILE" | sed 's|^\./||')
    # Same shape as the verify-install defect: a filename handed to grep as a
    # bare operand is option syntax the moment it starts with a dash. The input
    # here is maintainer-controlled rather than attacker-controlled, so this is
    # hardening rather than a live hole, and it is fixed anyway because the
    # lesson of this defect class is that the SHAPE is the defect.
    grep -v -x -F -e "$OUT_FILE_NORMALIZED" -- "$WORKDIR/filelist" > "$WORKDIR/filelist.filtered" </dev/null || true
    mv "$WORKDIR/filelist.filtered" "$WORKDIR/filelist"
fi

# Deterministic order regardless of the machine's locale.
LC_ALL=C sort "$WORKDIR/filelist" > "$WORKDIR/sorted"

COUNT=0
LISTED_COUNT=0
while IFS= read -r f; do
    [ -z "$f" ] && continue
    LISTED_COUNT=$((LISTED_COUNT + 1))
    if [ ! -f "$f" ]; then
        # A listed path that is not a regular file (a submodule gitlink, a
        # broken symlink, a name that exists in the index and not on disk)
        # is refused by name, never silently skipped: a manifest that
        # quietly disagrees with its own file list is worse than no
        # manifest, because it prints a completeness sentence over a set it
        # truncated itself.
        echo "checksums.sh: '$f' is listed as a tracked file but is not a" \
             "regular file on disk; refusing to silently drop it from the" \
             "manifest" >&2
        exit 1
    fi
    hash_file "$f" >> "$WORKDIR/manifest"
    COUNT=$((COUNT + 1))
done < "$WORKDIR/sorted"

if [ "$COUNT" -ne "$LISTED_COUNT" ]; then
    # Belt and suspenders: the per-file check above should already have
    # caught any mismatch and exited, so reaching here with unequal counts
    # means the loop logic itself disagrees with what it just did. Fail
    # loudly rather than write a manifest whose own file count cannot be
    # trusted.
    echo "checksums.sh: internal error: hashed $COUNT file(s) but listed" \
         "$LISTED_COUNT; refusing to write a manifest that disagrees with" \
         "its own file list" >&2
    exit 1
fi

if [ -n "$OUT_FILE" ]; then
    cp "$WORKDIR/manifest" "$OUT_FILE"
    echo "checksums.sh: wrote $COUNT file hash(es) to $OUT_FILE" >&2
else
    cat "$WORKDIR/manifest"
    echo "checksums.sh: $COUNT file(s) hashed" >&2
fi
