#!/bin/sh
# verify-install.sh: does an installed copy of this repository match a
# published checksum manifest, byte for byte?
#
# This is the direct answer to "how do I know what I installed". The install
# instruction (docs/RELEASE.md, README.md) clones a git ref into
# ~/.claude/skills/brothersbe, and that code then runs automatically on
# every Claude Code session via hooks. A manifest you cannot check against
# is not a security control, it is a claim; this script is the check. It is
# the same product claim in security form: it refuses to say PASSED over a
# file it never examined, in either direction.
#
# POSIX sh only, no bashisms, same portability intent as checksums.sh. Uses
# no git commands itself, on purpose: it only ever reads the manifest and
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
# ~/.claude/skills/brothersbe. The release ships CHECKSUMS.sha256 at the
# repository root (docs/RELEASE.md explains how a maintainer produces it
# with checksums.sh). Run this with no arguments from inside that clone, or
# point it at any two locations explicitly:
#
#   scripts/verify-install.sh
#   scripts/verify-install.sh /path/to/CHECKSUMS.sha256 ~/.claude/skills/brothersbe

set -e

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
DEFAULT_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)

TARGET="${2:-$DEFAULT_ROOT}"

# TARGET is canonicalised ONCE, here, before anything derives a path from it.
# The defect this removes is not a comparison bug, it is two DERIVATIONS of one
# value: the walked files got their relative path by stripping `$TARGET` exactly
# as the caller spelled it (that is what `find` echoes back), while the manifest
# got its relative path through `cd ... && pwd`, which answers in the shell's own
# spelling. Any input where those two disagree makes the manifest fail to
# recognise itself, so it is walked, not matched, and reported as an added file:
# `1 extra`, on an installation where nothing is wrong.
#
# On Windows that disagreement is guaranteed rather than incidental, because the
# Git for Windows shell answers `pwd` as `/d/a/BrotherSBE` for a root the caller
# passed as `D:\a\BrotherSBE`, and no amount of careful stripping reconciles two
# spellings. It is reproducible on POSIX too, which is how it was fixed here
# rather than guessed at: passing a root containing a `.` segment
# (`/tmp/x/./inst`) makes `find` echo the dotted form while `pwd` answers the
# normalised one, and the run reports `EXTRA: CHECKSUMS.sha256` and FAILED.
#
# Canonicalising once means every later path, walked or manifest, is derived the
# same way from the same string, so the two can no longer drift apart. The
# failure to enter the directory is surfaced rather than swallowed: a check that
# cannot reach what it is checking must say so, not verify nothing and pass.
if ! TARGET_CANONICAL=$(cd "$TARGET" 2>/dev/null && pwd); then
    echo "verify-install: cannot enter $TARGET, so nothing here was checked" >&2
    echo "verify-install: this is NOT a pass; the directory is missing, unreadable," \
         "or not a directory." >&2
    exit 2
fi
TARGET="$TARGET_CANONICAL"

MANIFEST="${1:-$TARGET/CHECKSUMS.sha256}"

if [ ! -f "$MANIFEST" ]; then
    echo "verify-install: no manifest found at $MANIFEST" >&2
    echo "verify-install: a maintainer generates one with scripts/checksums.sh" \
         "as part of cutting a release (see docs/RELEASE.md); if this copy" \
         "predates the first manifest, there is nothing to verify against." >&2
    exit 2
fi

# The file is fed on STDIN, never named as an argument. GNU coreutils escapes
# a filename containing a backslash or a newline: it doubles the backslashes
# and prefixes the whole output line with one, which shifts the hash a column
# to the right, so `cut -c1-64` returned a backslash glued to 63 hex digits and
# every such file reported MISMATCH. Apple's and BSD's tools do not escape,
# which is exactly why this only ever failed on the Linux leg while passing on
# macOS. Reading from stdin keeps the name out of the output entirely, so no
# escaping rule can apply on any platform.
#
# This is the THIRD defect in this one script of a single shape: a path being
# read as syntax rather than as data (the first was a CRLF manifest line, the
# second a path spliced into a sed regular expression). Recorded here because
# the shape is the lesson, not the individual bug.
if command -v sha256sum >/dev/null 2>&1; then
    hash_file() { sha256sum < "$1" | cut -c1-64; }
elif command -v shasum >/dev/null 2>&1; then
    hash_file() { shasum -a 256 < "$1" | cut -c1-64; }
else
    echo "verify-install: neither sha256sum nor shasum is on PATH; cannot check anything" >&2
    exit 1
fi

WORKDIR=$(mktemp -d 2>/dev/null || echo "/tmp/sbe-verify-install-work.$$")
mkdir -p "$WORKDIR"
trap 'rm -rf "$WORKDIR"' EXIT INT TERM

OK=0
MISMATCHED=0
MISSING=0
CRLF_LINES=0
CR=$(printf '\r')
: > "$WORKDIR/manifest_paths"

# Manifest lines are "<64 hex chars><two spaces><path>", the format both
# sha256sum and shasum -a 256 produce. Splitting by fixed column position
# (not by whitespace) is deliberate: a path may itself contain spaces, and
# field-splitting would cut it apart.
#
# A trailing carriage return is removed before the split, and COUNTED so the
# removal is never silent. Why this is a correctness fix and not a courtesy:
# `read -r` keeps the CR of a CRLF manifest inside the line, so the path this
# loop extracted was "tools/sbe_gate.py<CR>", which exists on no filesystem.
# Every named file then reported MISSING and every real file reported EXTRA,
# which is this script's loudest alarm ("exactly the shape of a planted
# backdoor") fired over an installation where nothing at all was wrong. A
# manifest reaches a CRLF state without anybody doing anything unusual: it is
# written by a tool running in text mode on Windows, or carried through an
# editor or an artifact upload that normalises line endings. Reproduced on
# POSIX and pinned by the eval
# `a-crlf-manifest-verifies-instead-of-reporting-every-file-missing`.
# Narrowing, stated rather than implied: a path whose last byte is genuinely a
# carriage return is indistinguishable from this and is read one byte short.
# That name is legal on POSIX and illegal on Windows, and the trade is
# deliberate: the CRLF manifest happens, and the CR-terminated filename does
# not.
while IFS= read -r line; do
    case "$line" in
        *"$CR")
            line=${line%"$CR"}
            CRLF_LINES=$((CRLF_LINES + 1))
            ;;
    esac
    [ -z "$line" ] && continue
    expected=$(printf '%s' "$line" | cut -c1-64)
    path=$(printf '%s' "$line" | cut -c67-)
    [ -z "$path" ] && continue
    printf '%s\n' "$path" >> "$WORKDIR/manifest_paths"
    full="$TARGET/$path"
    if [ ! -f "$full" ]; then
        echo "MISSING:   $path"
        MISSING=$((MISSING + 1))
        continue
    fi
    actual=$(hash_file "$full")
    if [ "$actual" = "$expected" ]; then
        OK=$((OK + 1))
    else
        echo "MISMATCH:  $path"
        MISMATCHED=$((MISMATCHED + 1))
    fi
done < "$MANIFEST"

# Second direction of the check, and the half a one-way comparison silently
# lacks. The loop above only asks "does every file the manifest NAMES match
# on disk", which never notices a file that was ADDED: an extra file is
# neither a MISMATCH nor a MISSING, so a one-direction script reports
# PASSED with a planted file still present, which is a completeness
# sentence over a set it never enumerated. This pass asks the other
# direction: does every file that actually EXISTS on disk appear in the
# manifest. The exclusion list below is the same one scripts/checksums.sh
# applies when it cannot use git (kept in sync by comment in both files,
# since each script is self-contained POSIX sh with no shared file to hold
# this list once): machine state and generated files this project's own
# .gitignore already keeps out of what git tracks, so their absence from
# the manifest is expected, not an added file.
#
# TARGET IS NEVER SPLICED INTO A `find -path` PATTERN. The previous shape of
# both walks below built each exclusion as `-path "$TARGET/.claude/*"`,
# feeding a glob-pattern language a filesystem path that can itself contain
# glob metacharacters (*, ?, [ ]). A root such as /tmp/sbe-glob/x[1]/inst
# turned the intended literal `.claude/*` exclusion into a character-class
# test over the `[1]` segment, so it stopped matching the real `.claude`
# directory: `.claude/settings.json`, a documented exclusion, walked through
# as an unexplained EXTRA file and a clean installation reported FAILED. The
# fix is the same shape as the MANIFEST_REL fix below it: never interpolate
# attacker- or filesystem-adjacent data into an expression language. Both
# walks now `cd "$TARGET"` first and enumerate the relative path `.`, so
# every `-path` pattern is a literal, TARGET-independent string that no
# character in the install path can reinterpret.
# Prefix removal is done with parameter expansion, NOT with sed. The previous
# form, sed "s|^$TARGET/||", spliced the install path into a REGULAR
# EXPRESSION, so every metacharacter in the path changed what the pattern
# meant. A path holding a backslash was the case that shipped: the strip
# silently failed, every walked file kept its absolute path, none of them
# matched a manifest entry, and this script told the reader their clean
# installation held files "exactly the shape of a planted backdoor". That is
# the same false accusation the CRLF fix removed, reached by a different route.
# Windows supplies backslashes in every path, which is why the windows-latest
# leg failed on it; a POSIX path containing a backslash, a `.`, a `*` or the
# `|` delimiter itself hits it too, which is how the eval below reproduces it
# on every leg. `${var#"$prefix"}` is POSIX, and the quotes make the pattern
# literal, so no character in the path can be read as syntax.
MANIFEST_ABS=$(cd "$(dirname "$MANIFEST")" && pwd)/$(basename "$MANIFEST")
MANIFEST_REL=${MANIFEST_ABS#"$TARGET/"}

# Every directory entry, whatever its TYPE, not just `-type f`. A manifest is
# a set of hashes of regular files, and `find -type f` never returns a
# symlink, so a planted tools/backdoor.py that is a SYMLINK to code outside
# the tree was neither a MISMATCH, nor a MISSING, nor an EXTRA: it was
# reported as nothing at all, while it ran automatically like every other
# module and the honesty suite imported and executed it. A control-flow file
# the manifest cannot hash is exactly the shape of a planted backdoor, so
# every non-regular entry (symlink, FIFO, socket) inside the install tree is
# named loudly here, never skipped. `-type f -o -type l -o ...` enumerates
# them all; the type of each is decided per entry below.
# The walk's own failures are CAPTURED, not fatal. Under `set -e` a directory
# find cannot enter made find exit nonzero and killed the script with no
# verdict block printed at all: the exit status was still nonzero, so nothing
# passed silently, but the reader got a bare "Permission denied" and no
# statement of what was and was not checked. A check that could not look says
# so, in its own sentence, and still prints everything it did establish.
#
# `(cd "$TARGET" && find . -mindepth 1 ...)`, not `find "$TARGET" ...`, for
# two reasons at once. The first is the TARGET-into-`-path` defect the
# comment above this one describes. The second is a symlinked TARGET: a
# clone install.sh itself documents as supported (install.sh lines 36 and
# 88, "a symlink or a trailing slash cannot hide the match"). `find` given a
# symlink as its own starting argument, with neither -H nor -L, does not
# follow it: it reports the argument itself as a single `-type l` entry and
# does not descend into what it points at at all (checked directly on this
# host: `find /a/symlink` prints exactly one line, the symlink, with nothing
# beneath it). The relative-path stripping two loops below,
# `rel=${full#"$TARGET/"}`, could not strip that entry either, because
# `full` equalled `$TARGET` exactly with no trailing slash to match against,
# so the whole installation was reported NON-REGULAR and FAILED on its root
# alone, every real file inside left unchecked. `cd "$TARGET"` dereferences
# the symlink itself (the kernel resolves it as part of changing directory),
# so `find .` then starts from the real directory, walks its actual
# contents normally, and with `-mindepth 1` never emits an entry for `.` in
# the first place; a symlink PLANTED INSIDE the tree is still lstat'd during
# the walk exactly as before and still reported.
(cd "$TARGET" && find . -mindepth 1 \( -type f -o -type l -o -type p -o -type s \) \
    ! -path './.git/*' \
    ! -path '*/__pycache__/*' \
    ! -path './.superpowers/*' \
    ! -path './.claude/*' \
    ! -path './.brothermode/*' \
    ! -path './docs/superpowers/*' \
    ! -name '.DS_Store' \
    ! -name '*.pyc' \
    ! -name 'STATE.md' \
    ! -path './.brothersbe/install-receipt.json' \
    ! -path './docs/book/BrotherSBE-for-Dummies.html' \
    ! -path './docs/book/.replay-*.sh' \
    ! -path './docs/book/estate/orders.csv' \
    ! -path './docs/book/estate/daily_totals.json' \
    ! -name '~$*' \
    ! -name '*.docx') \
    > "$WORKDIR/installed_raw" 2> "$WORKDIR/walk_errors" || true

DENIED=0
if [ -s "$WORKDIR/walk_errors" ]; then
    DENIED=$(wc -l < "$WORKDIR/walk_errors" | tr -d ' ')
    while IFS= read -r err; do
        [ -z "$err" ] && continue
        echo "UNWALKABLE: $err (this check could not enumerate that location, so no verdict below covers what is inside it)"
    done < "$WORKDIR/walk_errors"
fi

EXTRA=0
NONREGULAR=0
while IFS= read -r walked; do
    [ -z "$walked" ] && continue
    # Strips the `find .` walk's own "./" prefix, a fixed two-character
    # literal that holds no glob metacharacters regardless of what $TARGET
    # is spelled as, then rebuilds the absolute path by literal
    # concatenation (never a pattern-matching context) for the -f/-L tests
    # below, which need a real path to stat.
    rel=${walked#./}
    full="$TARGET/$rel"
    [ "$rel" = "$MANIFEST_REL" ] && continue
    # A non-regular entry is a failure in its own right: a manifest is hashes
    # of regular files, and it cannot vouch for what a symlink points at or
    # what a FIFO yields, so a control-flow path this check cannot hash is
    # named rather than measured against a set of hashes it can never be in.
    if [ ! -f "$full" ] || [ -L "$full" ]; then
        echo "NON-REGULAR: $rel (a symlink, pipe, or other non-regular file in the install tree; the manifest hashes regular files only, so it cannot vouch for what this resolves to, and something on this host may run it)"
        NONREGULAR=$((NONREGULAR + 1))
        continue
    fi
    # A FILENAME IS DATA, NEVER OPTION SYNTAX. This is the same class as the
    # seven path defects before it, one layer further out: `$rel` was handed to
    # grep as a bare operand, so an installed file whose name begins with a dash
    # was parsed as an OPTION. Two things then happened, and the second is the
    # dangerous one. The option ate the manifest-paths filename as its own
    # argument, leaving grep with no FILE operand, so grep read STDIN instead,
    # and stdin here is the walk output this very loop is consuming. Every
    # remaining entry was swallowed and never checked. Reproduced: a tree
    # holding an undeclared `-v` and an undeclared `skills/backdoor.py` reported
    # `0 extra`, `verify-install: PASSED`, exit 0. The one tool whose whole job
    # is detecting a planted file certified a planted file.
    # -e names the pattern, -- ends option parsing, and </dev/null denies the
    # stdin theft even if some future edit reintroduces the operand mistake.
    if ! grep -q -x -F -e "$rel" -- "$WORKDIR/manifest_paths" </dev/null; then
        echo "EXTRA:     $rel"
        EXTRA=$((EXTRA + 1))
    fi
done < "$WORKDIR/installed_raw"

# The excluded set is ENUMERATED, not silently assumed empty. The completeness
# sentence below used to claim "no file exists on disk that the manifest does
# not name" with no qualifier, which was false whenever anything sat inside an
# excluded path, and one excluded path (*/__pycache__/*) held files the test
# suite would execute. So: every file the exclusions swallowed on THIS run is
# counted and the first few are named; a file among them whose extension is
# executable source is a failure in its own right, because machine state is
# what the exclusions are for and code is not machine state. .git/ itself is
# not enumerated (it is git's own object store, holding thousands of objects).
# EVERY directory entry, whatever its type, exactly as the first walk does.
# The rule "enumerate regardless of type" was applied to the extra-file walk
# and NOT to this one, so the defect it closed was alive one find-invocation
# later, in the walk whose whole reason for existing is that code inside an
# excluded path is not machine state: a SYMLINK to code planted at
# tools/__pycache__/planted.py was skipped by `-type f` here and excluded by
# path up there, so it was reported as nothing at all and the qualifier
# sentence actively vouched for it ("0 file(s), 0 of them source code",
# PASSED). A non-regular entry inside an excluded path is its own named
# failure: the manifest cannot hash what it points at AND it is not
# enumerable as machine state, which is precisely the shape of a plant.
# Same TARGET-never-in-a-pattern fix as the walk above: cd first, walk the
# relative path, every -path exclusion a literal string.
(cd "$TARGET" && find . -mindepth 1 \( -type f -o -type l -o -type p -o -type s \) \
    ! -path './.git/*' \
    \( -path '*/__pycache__/*' \
       -o -path './.superpowers/*' \
       -o -path './.claude/*' \
       -o -path './.brothermode/*' \
       -o -path './docs/superpowers/*' \
       -o -name '.DS_Store' \
       -o -name '*.pyc' \
       -o -name 'STATE.md' \
       -o -path './.brothersbe/install-receipt.json' \
       -o -path './docs/book/BrotherSBE-for-Dummies.html' \
       -o -path './docs/book/estate/orders.csv' \
       -o -path './docs/book/estate/daily_totals.json' \
       -o -name '~$*' \
       -o -name '*.docx' \)) \
    > "$WORKDIR/excluded_files" 2>/dev/null || true

EXCLUDED=0
EXCLUDED_SOURCE=0
EXCLUDED_CONFIG=0
EXCLUDED_NONREGULAR=0
while IFS= read -r walked; do
    [ -z "$walked" ] && continue
    rel=${walked#./}
    full="$TARGET/$rel"
    EXCLUDED=$((EXCLUDED + 1))
    if [ ! -f "$full" ] || [ -L "$full" ]; then
        echo "EXCLUDED-NON-REGULAR: $rel (a symlink, pipe, or other non-regular entry inside an excluded path; the manifest cannot hash what it resolves to, the exclusions are for machine state and this is not machine state, and something on this host may execute what it points at)"
        EXCLUDED_NONREGULAR=$((EXCLUDED_NONREGULAR + 1))
        continue
    fi
    case "$rel" in
        # EXECUTABLE CONFIGURATION, REPORTED WITHOUT ACCUSING A CLEAN TREE.
        # An adversarial review planted a `.claude/settings.json` declaring a
        # SessionStart hook, which the harness runs on every session, and this
        # script said "0 of them source code" and PASSED. That gap is real: it
        # is the highest-value execution vector on this platform and it was
        # invisible here.
        #
        # The obvious repair, adding *.json to the list below, was TRIED AND
        # REVERTED, because it turned every healthy installation red: a
        # `.claude/settings.json` is ordinary harness state that a correct
        # install is supposed to have. This script has already shipped four
        # separate defects that told a clean installation it looked compromised,
        # and a fifth is not worth trading for this.
        #
        # So configuration is counted and named on its own line, and it does NOT
        # by itself decide the verdict. That is this project's own vocabulary
        # applied honestly: the file's PRESENCE is expected, its CONTENT is
        # something this script cannot read, and NO-DATA is neither a pass nor a
        # block. The residual risk is written down in docs/KNOWN-LIMITS.md
        # rather than papered over with a verdict this evidence cannot support.
        *.json|*.toml|*.yaml|*.yml)
            echo "EXCLUDED-CONFIG: $rel (configuration inside an excluded path; the harness may EXECUTE what this declares, and this script reads no file contents, so it can neither vouch for it nor accuse it)"
            EXCLUDED_CONFIG=$((EXCLUDED_CONFIG + 1))
            ;;
        *.py|*.sh|*.js|*.rb|*.pl|*.php|*.mjs|*.cjs|*.bash|*.zsh|*.ps1|*.bat|*.cmd)
            echo "EXCLUDED-SOURCE: $rel (source code inside an excluded path; the manifest cannot vouch for it and something on this host may execute it)"
            EXCLUDED_SOURCE=$((EXCLUDED_SOURCE + 1))
            ;;
    esac
done < "$WORKDIR/excluded_files"

echo ""
echo "verify-install: checked against $MANIFEST"
echo "verify-install: $OK file(s) match, $MISMATCHED mismatched, $MISSING missing, $EXTRA extra (present on disk, absent from the manifest), $NONREGULAR non-regular (a symlink or pipe the manifest cannot hash)"
if [ "$DENIED" -gt 0 ]; then
    echo "verify-install: $DENIED location(s) could not be enumerated (named UNWALKABLE above), so no sentence here covers what is inside them."
fi
if [ "$CRLF_LINES" -gt 0 ]; then
    echo "verify-install: $CRLF_LINES manifest line(s) ended in a carriage return (a CRLF manifest, which a text-mode write on Windows or a line-ending-normalising transport produces); the return was removed from each path before it was looked up, and is reported here rather than removed in silence."
fi
echo "verify-install: the excluded paths (*/__pycache__/*, .superpowers/, docs/superpowers/, .claude/ and .brothermode/ (harness-written local state, and NOTE that a linked git worktree under .claude/worktrees/ puts whole source trees inside an excluded path, which is why the excluded-source count below can be large and is reported rather than assumed harmless), .brothersbe/install-receipt.json (the local install record, gitignored because it names this machine's absolute path), the built book and the book estate's two generated data files (all three are build outputs regenerated on every run, never fixtures), docs/book/.replay-*.sh (scratch the excerpt replay harness writes beside a chapter while re-executing its blocks and removes when it finishes), and files named .DS_Store, *.pyc, STATE.md, ~\$*, *.docx; .git/ not enumerated) currently hold $EXCLUDED entr(y/ies) of any type, $EXCLUDED_SOURCE of them source code, $EXCLUDED_CONFIG of them configuration this script cannot read the contents of, and $EXCLUDED_NONREGULAR of them non-regular (a symlink or pipe this check cannot hash)."

if [ "$MISMATCHED" -gt 0 ] || [ "$MISSING" -gt 0 ] || [ "$EXTRA" -gt 0 ] || [ "$EXCLUDED_SOURCE" -gt 0 ] || [ "$EXCLUDED_NONREGULAR" -gt 0 ] || [ "$NONREGULAR" -gt 0 ] || [ "$DENIED" -gt 0 ]; then
    echo "verify-install: FAILED. Do not trust this installed copy until you" \
         "understand why the files above differ from the published manifest." >&2
    if [ "$EXTRA" -gt 0 ]; then
        echo "verify-install: an EXTRA file is exactly the shape of a" \
             "planted backdoor: it runs automatically along with everything" \
             "else in this installation, and the manifest says nothing" \
             "about it because nothing here declared it." >&2
    fi
    if [ "$NONREGULAR" -gt 0 ]; then
        echo "verify-install: a NON-REGULAR file is the same shape by another" \
             "mechanism: a symlink or pipe the manifest cannot hash, pointing" \
             "at code the manifest never saw, invisible to a check that only" \
             "walked regular files." >&2
    fi
    if [ "$EXCLUDED_SOURCE" -gt 0 ]; then
        echo "verify-install: an EXCLUDED-SOURCE file is the same shape one" \
             "level deeper: source code sitting in a path this check does not" \
             "hash, where an earlier version of this script would have said" \
             "PASSED over it without qualification." >&2
    fi
    if [ "$EXCLUDED_NONREGULAR" -gt 0 ]; then
        echo "verify-install: an EXCLUDED-NON-REGULAR entry is the deepest" \
             "shape of the same plant: a symlink inside an excluded path," \
             "invisible to the extra-file walk (excluded by path) and to a" \
             "\`find -type f\` (skipped by type), so both counts read zero" \
             "while the code it resolves to runs like everything else here." >&2
    fi
    exit 1
fi

echo "verify-install: PASSED. Every file the manifest names matches on disk,"
echo "verify-install: and no file exists on disk that the manifest does not name,"
echo "verify-install: outside the excluded paths enumerated above (their current"
echo "verify-install: file count is printed on every run, and source code among"
echo "verify-install: them fails this check)."
echo "verify-install: a manifest records CONTENT, not file mode: a data file" \
     "that arrived with the execute bit set still matches its hash here, so" \
     "this says the bytes are the published bytes and says nothing about" \
     "permissions."
echo "verify-install: this does not prove the manifest itself is authentic;" \
     "it proves your files match whatever manifest you pointed this at. Get" \
     "the manifest from the release you trust (the tag's git history, or a" \
     "release asset), not from the same untrusted channel as the code."
