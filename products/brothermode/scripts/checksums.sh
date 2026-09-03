#!/bin/sh
# checksums.sh: a deterministic SHA256 manifest of every shipped file.
#
# Why this exists: docs/RELEASE.md explains the security problem it answers
# ("how do I know what I installed"). The install instruction clones a git
# branch or tag into a location whose code runs automatically on every
# Claude Code session (the hooks in ~/.claude/settings.json). This script is
# the maintainer-side half of the answer: it lists every file that ships in
# a release and its SHA256 hash, in a fixed order, so the same source tree
# always produces the same manifest byte for byte. verify-install.sh is the
# user-side half: it re-hashes an installed copy and compares against a
# manifest this script produced.
#
# POSIX sh only, no bashisms: no arrays, no [[ ]], no local, no process
# substitution, no here-strings. Every construct used here (command
# substitution, case, for/while with IFS, plain shell functions) is in
# POSIX.1-2017 section 2, and this has been run directly under both bash
# and dash to confirm neither rejects it.
#
# Usage, always from the repository root:
#   scripts/checksums.sh                    # print the manifest to stdout
#   scripts/checksums.sh CHECKSUMS.sha256   # write it to that file instead
#
# WHAT THE MANIFEST ATTESTS (finding 4B, 2026-07-27). A manifest line is
# "<sha256>  <path>", which on its own says nothing about what KIND of
# directory entry that path is. That gap was exploitable: `[ ! -f "$f" ]`
# follows symlinks, so a tracked symlink whose target happened to be a
# regular file sailed through the guard and hash_file then hashed the
# TARGET's bytes, producing a line indistinguishable from a real file while
# the actual shipped bytes (the link target string) went unattested.
# Reproduced: a git repo tracking a symlink as mode 120000 yielded a
# manifest entry whose hash was the hash of the link target's contents.
#
# The fix is to make the entry type part of what the manifest attests,
# rather than adding a per-entry type column that every existing published
# manifest would lack. This script now REFUSES to manifest anything that is
# not a regular file, so "every path in this manifest is a regular file" is
# an invariant of the format itself, established here at generation time and
# enforced by verify-install.sh at check time (it lstats each manifested
# path and fails if a regular file was swapped for a symlink). Refusing is
# chosen over recording the link target string on purpose: this project
# ships no symlinks (verified, every tracked entry is git mode 100644 or
# 100755), so a symlink appearing in a release tree is a defect or an
# attack, and a release is the right place to stop rather than to describe
# it. Classification is by lstat (`test -L` BEFORE `test -f`, because -L is
# the only test that sees the link itself and not what it points at) and,
# when git is available, by the tracked mode (120000 symlink, 160000
# submodule gitlink, 100644/100755 regular blob).
#
# "Shipped file" is defined as: every file git tracks in this repository at
# the commit being released. That is deliberate, not lazy: it is exactly
# the set of bytes a `git clone` (or `git checkout <tag>`) hands a user, so
# the manifest and the install are talking about the same tree by
# construction, and it can never drift from .gitignore because it does not
# reimplement .gitignore's rules. If this is ever run against a tree copied
# without its .git directory, it falls back to a plain filesystem walk with
# the same exclusions .gitignore already documents (STATE.md, CANVAS.md,
# threads/, __pycache__/, .DS_Store, *.bak*, .superpowers/) plus this repo's
# own machine state (.brothermode/). CANVAS.md joined that list on
# 2026-08-04: it is generated per machine by `bm_project.py start` and
# gitignored exactly like STATE.md, but only STATE.md was named here, so
# verify-install.sh reported CANVAS.md as an EXTRA file on every machine
# where anyone had ever started a project. A verifier that says FAILED on a
# correct installation trains people to ignore it, which costs more than the
# file was ever worth.

set -e

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$ROOT"

OUT_FILE="$1"

# --- pick a SHA256 tool: Linux ships sha256sum, macOS ships shasum -a 256.
# Detect, never assume: a machine with coreutils installed via Homebrew can
# have both, and a minimal container can have neither.
if command -v sha256sum >/dev/null 2>&1; then
    hash_file() { sha256sum "$1"; }
elif command -v shasum >/dev/null 2>&1; then
    hash_file() { shasum -a 256 "$1"; }
else
    echo "checksums.sh: neither sha256sum nor shasum is on PATH; cannot hash anything" >&2
    exit 1
fi

WORKDIR=$(mktemp -d 2>/dev/null || echo "/tmp/bm-checksums-work.$$")
mkdir -p "$WORKDIR"
trap 'rm -rf "$WORKDIR"' EXIT INT TERM

# --- build the file list, git-tracked files preferred (see header comment).
#
# GATE 5 (final-blockers spec 2026-07-26, VERIFIED BY ORCHESTRATOR): plain
# `git ls-files` quotes any tracked path containing a quote, a backslash, or
# a non-ASCII byte (git's default core.quotePath behavior), and the quoted
# form it prints is not a real path on disk. The old loop's `[ -f "$f" ]`
# check then failed for it and silently "continue"d past it, exit 0, no
# message: 91 tracked files in, 89 hashed, two dropped with nothing said.
# `-z` asks git for the exact bytes of every tracked path, NUL-terminated,
# never quoted, so every entry below is always the real, literal path.
# Residual, stated rather than hidden: a path containing a literal newline
# byte (legal on POSIX, vanishingly rare in practice, and not what this gate
# was about) would still be misread once `tr` turns NUL into newline; this
# closes the quoting defect the orchestrator reproduced, not every
# conceivable byte a filename could contain.
#
# FINDING 4B: `-s` is new and load-bearing. It makes git print the tracked
# mode alongside each path ("<mode> <object> <stage>\t<path>"), which is the
# only way to see that an entry is tracked AS a symlink (120000) or as a
# submodule gitlink (160000) rather than as a regular blob. Checking the
# tracked mode catches the entry type as the release records it; the lstat
# check in the hashing loop below independently catches the entry type as it
# exists on disk. Both run, because they answer different questions and an
# attacker only has to win one of them.
: > "$WORKDIR/filelist"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git ls-files -s -z | tr '\0' '\n' > "$WORKDIR/staged"
    TAB=$(printf '\t')
    while IFS= read -r rec; do
        [ -z "$rec" ] && continue
        rec_mode=${rec%% *}
        rec_path=${rec#*"$TAB"}
        case "$rec_mode" in
            100644|100755)
                ;;
            120000)
                echo "checksums.sh: '$rec_path' is tracked as a SYMLINK (git mode" \
                     "120000). Refusing to manifest it: a manifest line records a" \
                     "content hash and a path, so hashing this entry would silently" \
                     "attest the bytes of whatever it points at, and nothing in the" \
                     "manifest would say the shipped entry is a link or where it" \
                     "leads. Remove the symlink or ship a real file." >&2
                exit 1
                ;;
            160000)
                echo "checksums.sh: '$rec_path' is a submodule gitlink (git mode" \
                     "160000). Refusing to manifest it: its contents are not part of" \
                     "this tree's bytes and cannot be attested here." >&2
                exit 1
                ;;
            *)
                echo "checksums.sh: '$rec_path' has git mode '$rec_mode', which is" \
                     "neither a regular blob (100644, 100755) nor a type this script" \
                     "knows how to attest. Refusing rather than guessing." >&2
                exit 1
                ;;
        esac
        printf '%s\n' "$rec_path" >> "$WORKDIR/filelist"
    done < "$WORKDIR/staged"
else
    # FINDING 4B: `-type f` here would have hidden exactly what the loop
    # below exists to catch, because POSIX find without -L never matches a
    # symlink with -type f. `! -type d` enumerates every non-directory entry
    # instead (regular files, symlinks including symlinks to directories,
    # devices, FIFOs, sockets) and lets the classification loop below refuse
    # the ones that are not regular files, loudly, by name. The four
    # excluded roots are also matched as bare names, not only as "<name>/*"
    # prefixes, so that machine state stays excluded whether it is a real
    # directory (already skipped by ! -type d) or a symlink standing in for
    # one.
    find . ! -type d \
        ! -path './.git' \
        ! -path './.git/*' \
        ! -path './.brothermode' \
        ! -path './.brothermode/*' \
        ! -path '*/__pycache__/*' \
        ! -path './threads' \
        ! -path './threads/*' \
        ! -path './.superpowers' \
        ! -path './.superpowers/*' \
        ! -path './.venv-embed' \
        ! -path './.venv-embed/*' \
        ! -path './Documentation' \
        ! -path './Documentation/*' \
        ! -path './.claude' \
        ! -path './.claude/*' \
        ! -path './Handover' \
        ! -path './Handover/*' \
        ! -path './Handover-*' \
        ! -path './Handover-*/*' \
        ! -name '.DS_Store' \
        ! -name '*.bak*' \
        ! -name 'STATE.md' \
        ! -name 'CANVAS.md' \
        ! -name 'CANVAS-*.md' \
        ! -name 'DELIVERY-PACKET.md' \
        ! -name 'DELIVERY-PACKET-*.md' \
        ! -name 'PROJECT-VIEW.html' \
        | sed 's|^\./||' > "$WORKDIR/filelist"
fi

# Never hash the manifest into itself. OUT_FILE, when given, is documented
# above as a path relative to the repository root (the common, supported
# case); strip a leading "./" the same way the fallback file list already
# does, so a re-run after a previous release's manifest is already
# committed does not fold that file's own bytes into the new one.
if [ -n "$OUT_FILE" ]; then
    OUT_FILE_NORMALIZED=$(printf '%s' "$OUT_FILE" | sed 's|^\./||')
    grep -v -x -F "$OUT_FILE_NORMALIZED" "$WORKDIR/filelist" > "$WORKDIR/filelist.filtered" || true
    mv "$WORKDIR/filelist.filtered" "$WORKDIR/filelist"
fi

# Deterministic order regardless of the machine's locale.
LC_ALL=C sort "$WORKDIR/filelist" > "$WORKDIR/sorted"

COUNT=0
LISTED_COUNT=0
while IFS= read -r f; do
    [ -z "$f" ] && continue
    LISTED_COUNT=$((LISTED_COUNT + 1))
    # FINDING 4B: -L FIRST, always. `test -L` is the only one of these that
    # lstats, meaning it reports on the entry itself; every other file test
    # (-f, -e, -r, -s) follows the link and answers about the target. Asking
    # `-f` first is what let a symlink-to-a-regular-file pass as a regular
    # file and get its target's bytes hashed into the manifest.
    if [ -L "$f" ]; then
        echo "checksums.sh: '$f' is a SYMLINK on disk. Refusing to manifest it:" \
             "hashing it would attest the bytes of its target while the manifest" \
             "line looked exactly like an ordinary file, so nothing would record" \
             "that the shipped entry is a link or where it points." >&2
        exit 1
    fi
    if [ ! -f "$f" ]; then
        # GATE 5: a listed path that is not a regular file (a submodule
        # gitlink, or, before that fix, a git-quoted name that never existed
        # as a literal path at all) used to be silently skipped, exit 0.
        # Refuse instead: a manifest that quietly disagrees with its own file
        # list is worse than no manifest. Since finding 4B this arm no longer
        # sees links of any kind (the -L test above takes them first); what
        # reaches here is a directory, a device, a FIFO, a socket, or a path
        # that does not exist at all.
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
