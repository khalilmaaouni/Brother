#!/bin/sh
# rollback-install.sh: the undo the RECOMMENDED install path did not have.
# `claude plugin update brothersbe` moves an installation forward and
# `claude plugin uninstall brothersbe` removes it; neither one puts a machine
# back on the version it was running an hour ago. The clone-based path always
# had that move (check out the older tag, re-run scripts/verify-install.sh,
# which is what scripts/test-upgrade-rollback.sh rehearses); this script gives
# the plugin path the same move, over the same git tags, checked by the same
# verifier.
#
# WHICH DIRECTORY IT OPERATES ON, AND WHY THAT QUESTION IS THE WHOLE SCRIPT.
# On a real machine BOTH of these exist at once:
#
#   ~/.claude/plugins/cache/brothersbe/brothersbe/<version>   the marketplace
#       install: the bytes Claude Code actually loads, put there by
#       `claude plugin install brothersbe@brothersbe`, carrying no .git
#   ~/.claude/skills/brothersbe                               the clone
#       install.sh's own fallback branch makes when no tag is published yet
#
# An earlier draft of this script defaulted to the second one. On a machine
# holding both, that draft would have run to completion, printed a success
# line, and left the installation the person is actually running untouched.
# That is worse than shipping no undo at all: it converts "I have no undo"
# into "I ran the undo and I am fine". So this script ASSUMES NO PATH. It
# asks the harness where the plugin it loads lives, the same question
# `scripts/verify-install.sh` and `src/brothersbe/__init__.py:repo_root()`
# answer for themselves by computing from their own file location rather than
# from a path typed into a document:
#
#   ~/.claude/plugins/installed_plugins.json   ->  .plugins["brothersbe@<marketplace>"][]
#                                                  .installPath, .version, .gitCommitSha
#   ~/.claude/plugins/known_marketplaces.json  ->  [<marketplace>].installLocation
#
# The first record names the INSTALL (the bytes loaded). The second names the
# SOURCE (the git repository carrying the release tags), because a marketplace
# install has no git history of its own to walk. The rollback moves the SOURCE
# to an earlier release tag and re-runs the same marketplace pair install.sh
# uses, then RE-READS the first record to find where the harness put the new
# bytes, and verifies THOSE. A rollback that verified the source clone and
# called it done would be the same silence in a different place.
#
# When the installation is itself a git clone (the fallback path above, or
# --install-dir pointed at a clone), install and source are one directory and
# the same steps collapse onto it. That case is served, not special-cased away.
#
# PREVIEW FIRST, APPLY SECOND, matching `sbe adopt` and `sbe init` (dry run by
# default, --apply to write) rather than install.sh's apply-by-default
# --dry-run flag. That difference from the file this script sits next to is
# deliberate and stated rather than left to be discovered: install.sh's default
# outcome is a working installation, while this one's default outcome moves an
# installation BACKWARD, and a destructive default is not a shape to inherit.
#
# WHAT --install-dir ON AN UNRECORDED DIRECTORY USED TO DO, AND WHY THAT WAS
# REMOVED RATHER THAN KEPT. Before this fix, --install-dir replaced only the
# directory this script operated on; the version, commit, and marketplace it
# printed and rolled back kept coming from whichever entry the harness record
# already had (the one install on the machine, or the first one this script
# happened to choose among several). Pointed at a directory the record did
# not name, the script still ran to completion: it attached A RECORDED
# INSTALLATION'S IDENTITY to bytes that installation never produced, verified
# and reported that identity, and exited 0. That is the defect: a rollback
# claiming to describe the directory named on the command line while actually
# describing a different, unrelated installation. Unrecorded directories are
# refused now, by design (refusal 3 below), not guessed at.
#
# Rejected alternative: keep running, but report the directory's identity as
# UNKNOWN instead of borrowing a recorded one, then roll back release history
# against it anyway. That removes the false identity claim but not the
# wrong-state action underneath it: this script would still check out git
# history and reinstall over a directory it cannot verify was ever a
# brothersbe install, only now silently, with nothing left to compare
# afterward. Refusing holds the same invariant the other ten refusals hold:
# nothing is written until this script can correctly name what it is about to
# change. Constrain, do not widen.
#
# ORDERING CONSEQUENCE: on a machine with at least one brothersbe install
# recorded, an --install-dir naming a path that is BOTH not on disk AND not a
# recorded installPath now fails at refusal 3 (no matching record), never
# reaching refusal 5 (not on disk). Refusal 5 only fires for a path that DOES
# match a recorded installPath but whose directory has since been deleted.
#
# IT REFUSES RATHER THAN GUESSES. ELEVEN refusals, every one of them
# evaluated BEFORE the first write, so a refused run leaves both directories
# byte for byte as it found them:
#
#   1. git is not on PATH. Every step between here and the first write is a
#      git call (rev-parse, status, tag, merge-base), so this is the boundary
#      check that has to come first, and it is the same shape as install.sh's
#      own `need git`.
#   2. python3 is not on PATH. The harness records above are JSON, and this
#      script reads them with python3 for the same reason install.sh parses
#      `sbe init --json` with python3 rather than jq: python3 is already a
#      hard prerequisite of this project and jq is not.
#   3. --install-dir names a directory that disagrees with the harness's own
#      record: the record names a different installPath (or none at all) for
#      every brothersbe install it knows about. Rejected alternative,
#      recorded rather than taken: make the override consistent by
#      recomputing version/commit/marketplace from the named directory
#      itself. That widens what an unverified path can assert about itself;
#      this script constrains instead and refuses, naming both paths.
#   4. the harness's installation record is missing, unreadable, or names no
#      brothersbe install (and no --install-dir was given to answer for it).
#   5. the installation directory that record names is not on disk.
#   6. no marketplace source is on disk for that install, so there is no git
#      history holding earlier releases to roll back through.
#   7. the source is not a git clone (an extracted archive or copied tree has
#      no earlier version to return to).
#   8. the source working tree carries uncommitted changes, which a checkout
#      would clobber.
#   9. an explicit --to names something that is not an earlier release on this
#      line of history.
#  10. there is no earlier release tag at all. This is the refusal this script
#      exists to be honest about: a fresh install whose history carries one
#      release has nothing to go back to, and inventing a target (the previous
#      commit, the default branch, a tag from another line of history) would be
#      a guess dressed as an undo.
#  11. --apply only: `claude` is not on PATH, and re-installing the rolled-back
#      plugin needs it. Discovering that after the checkout would leave the
#      source moved and the harness still loading the version it had.
#
# That count is not maintained by hand: tools/test_sbe_install.py asserts the
# number written above equals the number of `# --- refusal N:` markers below
# and the number of lines this file can print carrying `REFUSED:`, so a
# refusal added or removed without touching this header fails a test.
#
# Release tags are the `v<digit>` family, and each candidate must be an
# ANCESTOR of the installed commit, for the reason
# scripts/test-upgrade-rollback.sh states in its own header: this repository
# also carries `archive/*` tags that are preservation snapshots of deleted
# branch tips, and a tag on an unrelated line of history is not a release
# anyone installed.
#
# POSIX sh only, no bashisms, same portability intent as
# scripts/verify-install.sh and install.sh.
#
# Usage:
#   scripts/rollback-install.sh                      preview: names the
#                                                     install it found and
#                                                     where it found it, the
#                                                     source, the version now,
#                                                     the target version, and
#                                                     every command; writes
#                                                     nothing
#   scripts/rollback-install.sh --apply              performs the rollback and
#                                                     verifies the INSTALLED
#                                                     result, not the source
#   scripts/rollback-install.sh --install-dir <path> selects, by path, WHICH
#                                                     recorded installation to
#                                                     roll back; a path matching
#                                                     no record is REFUSED
#                                                     (refusal 3), never adopted
#   scripts/rollback-install.sh --source-dir <path>  takes release history from
#                                                     <path> instead of the
#                                                     marketplace source the
#                                                     harness records
#   scripts/rollback-install.sh --to <tag>           rolls back to <tag>
#                                                     instead of the newest
#                                                     earlier release; <tag> is
#                                                     still checked for being a
#                                                     real earlier release

set -eu

APPLY=0
INSTALL_DIR_ARG=""
SOURCE_DIR_ARG=""
TO_ARG=""
while [ $# -gt 0 ]; do
    case "$1" in
        --apply)
            APPLY=1
            shift
            ;;
        --install-dir)
            if [ $# -lt 2 ]; then
                echo "rollback-install: --install-dir requires a path argument"
                exit 2
            fi
            INSTALL_DIR_ARG="$2"
            shift 2
            ;;
        --install-dir=*)
            INSTALL_DIR_ARG="${1#--install-dir=}"
            shift
            ;;
        --source-dir)
            if [ $# -lt 2 ]; then
                echo "rollback-install: --source-dir requires a path argument"
                exit 2
            fi
            SOURCE_DIR_ARG="$2"
            shift 2
            ;;
        --source-dir=*)
            SOURCE_DIR_ARG="${1#--source-dir=}"
            shift
            ;;
        --to)
            if [ $# -lt 2 ]; then
                echo "rollback-install: --to requires a tag argument"
                exit 2
            fi
            TO_ARG="$2"
            shift 2
            ;;
        --to=*)
            TO_ARG="${1#--to=}"
            shift
            ;;
        *)
            echo "rollback-install: unknown argument '$1'; usage: rollback-install.sh [--apply] [--install-dir <path>] [--source-dir <path>] [--to <tag>]"
            exit 2
            ;;
    esac
done

# This script's own installation root, computed exactly the way
# scripts/verify-install.sh computes DEFAULT_ROOT and
# src/brothersbe/__init__.py computes _ROOT: from this file's location, never
# from the caller's working directory. It is reported alongside the resolved
# install so a person can see whether the copy running is the copy installed,
# and it is never used as a silent default for the thing being rolled back.
SELF_ROOT=$(cd "$(dirname "$0")/.." && pwd)

# --- refusal 1: git is not on PATH. First, because every step from here to
# the first write is a git call, and a boundary that cannot run should say so
# in its own words rather than surface as a shell "command not found" halfway
# through. Same shape as install.sh's own `need git`.
if ! command -v git >/dev/null 2>&1; then
    echo "rollback-install: REFUSED: git is not on PATH, and every step of this rollback before the first write is a git call (rev-parse, status, tag, merge-base). Nothing has been changed. Install git (for example, Xcode Command Line Tools on macOS, or your package manager) and re-run."
    exit 1
fi

# --- refusal 2: python3 is not on PATH. The harness records this script has
# to read are JSON; install.sh parses JSON with python3 for the same stated
# reason (python3 is already a hard prerequisite here and jq is not).
if ! command -v python3 >/dev/null 2>&1; then
    echo "rollback-install: REFUSED: python3 is not on PATH, and this script reads Claude Code's own installation records (JSON) with it to find where the plugin you are running actually lives. Nothing has been changed. Install Python 3.9 or newer and re-run, or name the directories yourself with --install-dir and --source-dir."
    exit 1
fi

# The resolver. Emits `sh` assignments this script evals, with every value
# single-quoted and every embedded quote escaped, because a path is attacker-
# adjacent data (it comes off disk, not out of this file) and eval of an
# unquoted value would be a shell injection. Refusals 3, 4, 5 and 6 are
# decided here, where the records are actually read, and reported back as a status
# plus a one-line reason rather than as a traceback: a boundary that cannot
# answer says what it could not read and what to do instead.
#
# No apostrophe appears anywhere in this program's own text: it is embedded in
# a POSIX sh single-quoted string, so an apostrophe would end that string
# early. install.sh's PY_TEAM_PROFILE_REPORT carries the same constraint for
# the same reason.
PY_RESOLVE='
import io
import json
import os
import sys

install_arg, source_arg, home = sys.argv[1], sys.argv[2], sys.argv[3]


def q(value):
    return "%s" % ("\x27" + str(value).replace("\x27", "\x27\\\x27\x27") + "\x27")


def emit(**fields):
    for key in sorted(fields):
        sys.stdout.write("SBE_RB_%s=%s\n" % (key, q(fields[key])))


def refuse(number, reason):
    emit(STATUS="refused", REFUSAL=number, REASON=reason)
    sys.exit(0)


def read_json(path, label):
    """Returns (data, None) or (None, reason). A record that cannot be parsed
    is named, never treated as an empty record: an empty record and a corrupt
    one lead to different remedies."""
    if not os.path.isfile(path):
        return None, "%s does not exist at %s" % (label, path)
    try:
        with io.open(path, encoding="utf-8") as handle:
            return json.load(handle), None
    except (IOError, OSError) as exc:
        return None, "%s at %s could not be read (%s)" % (label, path, exc)
    except ValueError as exc:
        return None, "%s at %s is not valid JSON (%s)" % (label, path, exc)


plugins_path = os.path.join(home, ".claude", "plugins", "installed_plugins.json")
markets_path = os.path.join(home, ".claude", "plugins", "known_marketplaces.json")

record_dir = ""
record_version = ""
record_sha = ""
record_key = ""
marketplace = ""
record_note = ""
entries = []

data, why = read_json(plugins_path, "the installed-plugin record")
if data is None:
    record_note = why
else:
    plugins = data.get("plugins") or {}
    for key in sorted(plugins):
        if key.split("@")[0] != "brothersbe":
            continue
        installs = plugins.get(key) or []
        for item in installs:
            if isinstance(item, dict):
                entries.append((key, item))
    if not entries:
        record_note = "%s names no brothersbe install" % plugins_path
    else:
        # More than one scope (user, project) can hold an install. Prefer the
        # one whose installPath exists on disk; state the count rather than
        # picking silently out of several.
        chosen = None
        for key, item in entries:
            path = item.get("installPath") or ""
            if path and os.path.isdir(path):
                chosen = (key, item)
                break
        if chosen is None:
            chosen = entries[0]
        record_key, item = chosen
        record_dir = item.get("installPath") or ""
        record_version = item.get("version") or ""
        record_sha = item.get("gitCommitSha") or ""
        marketplace = record_key.split("@")[-1]
        if len(entries) > 1:
            record_note = "%d brothersbe installs are recorded (%s); this run takes %s" % (
                len(entries), ", ".join(sorted(k for k, _ in entries)), record_dir)

# --- refusal 3: --install-dir must never borrow ANOTHER installations
# identity. Before this check, --install-dir only ever replaced install_dir;
# record_version, record_sha, record_key and marketplace kept coming from
# whichever entry the harness record happened to name (record_dir above), so
# pointing the flag at installation B while the record named A rolled B back
# using A version, A commit, and A tag history, an A-shaped undo performed
# against B bytes and reported as B. When the record names at least one
# brothersbe install, the override is matched against it (by realpath, so a
# trailing slash or a symlink cannot hide a real match) and only accepted
# when it agrees; the matched entry own fields are then used instead of
# "chosen", so a --install-dir naming a real but non-default entry (a second
# scope, say) still gets its own identity rather than the first one. A
# record naming no install at all (entries empty) has nothing to disagree
# with, and refusal 4 below covers that case on its own.
if install_arg and entries:
    install_arg_real = os.path.realpath(install_arg)
    matched = None
    for key, item in entries:
        path = item.get("installPath") or ""
        if path and os.path.realpath(path) == install_arg_real:
            matched = (key, item)
            break
    if matched is None:
        refuse(3, "--install-dir %s does not match any installPath Claude Code has on record for brothersbe (%s: %s). Nothing has been changed. Rolling back a directory the harness never recorded there would attach a DIFFERENT installation version and commit identity to it, which is the wrong-install rewrite this refusal exists to prevent. Re-run without --install-dir to use the recorded installation, or point --install-dir at one of the paths just named." % (
            install_arg, plugins_path,
            ", ".join(sorted((item.get("installPath") or "(no installPath)") for _, item in entries))))
    record_key, item = matched
    record_dir = item.get("installPath") or ""
    record_version = item.get("version") or ""
    record_sha = item.get("gitCommitSha") or ""
    marketplace = record_key.split("@")[-1]

if install_arg:
    install_dir = install_arg
    install_source = "--install-dir"
else:
    install_dir = record_dir
    install_source = "the harness record at %s" % plugins_path

# --- refusal 4:
if not install_dir:
    refuse(4, "Claude Code has no record of a brothersbe install on this machine (%s). Nothing has been changed. This is the record `claude plugin install` writes, so either the plugin is not installed under this HOME (%s), or you are rolling back a copy that lives somewhere else, in which case name it: --install-dir /path/to/installation." % (record_note or plugins_path, home))

# --- refusal 5:
if not os.path.isdir(install_dir):
    refuse(5, "the installation directory %s (from %s) is not on disk, so there is nothing here to roll back. Nothing has been changed. If the plugin was removed by hand, re-install it before rolling back; if it lives elsewhere, name it with --install-dir." % (install_dir, install_source))

install_dir = os.path.realpath(install_dir)

# The source: where the release history lives. An explicit --source-dir wins;
# otherwise the marketplace this install came from, as the harness recorded
# it; otherwise the installation itself, but only when it is a git clone, the
# case install.sh takes when no tag is published yet.
source_dir = ""
source_from = ""
market_note = ""
if source_arg:
    source_dir = source_arg
    source_from = "--source-dir"
else:
    markets, why = read_json(markets_path, "the marketplace record")
    if markets is None:
        market_note = why
    elif marketplace and marketplace in markets:
        entry = markets.get(marketplace) or {}
        located = entry.get("installLocation") or ""
        if not located:
            source = entry.get("source") or {}
            located = source.get("path") or ""
        if located:
            source_dir = located
            source_from = "the marketplace record at %s (marketplace %s)" % (markets_path, marketplace)
        else:
            market_note = "marketplace %s in %s names no installLocation" % (marketplace, markets_path)
    else:
        market_note = "%s has no entry for marketplace %s" % (markets_path, marketplace or "(unknown)")

    if not source_dir and os.path.isdir(os.path.join(install_dir, ".git")):
        source_dir = install_dir
        source_from = "the installation itself, which is a git clone"

# --- refusal 6:
if not source_dir or not os.path.isdir(source_dir):
    refuse(6, "no release history is reachable for the install at %s: %s, and that installation is not itself a git clone. Nothing has been changed. A marketplace install carries no git history of its own, so the rollback needs the repository it was installed FROM; name it with --source-dir /path/to/brothersbe-repository, or re-add it with `claude plugin marketplace add <repository>`." % (install_dir, market_note or ("the source %s named is not a directory" % (source_dir or "(none)"))))

emit(STATUS="ok",
     INSTALL_DIR=install_dir,
     INSTALL_FROM=install_source,
     INSTALL_VERSION=record_version,
     INSTALL_SHA=record_sha,
     KEY=record_key,
     MARKET=marketplace,
     NOTE=record_note,
     RECORD=plugins_path,
     SOURCE_DIR=os.path.realpath(source_dir),
     SOURCE_FROM=source_from)
'

# `${HOME:-}`, not `$HOME`: under `set -u` an unset HOME (cron, a stripped
# environment) would abort with a shell message about an unbound variable
# instead of this script's own refusal, and a boundary that cannot answer
# should say so in its own words. With HOME unset, the record paths below do
# not exist and refusal 3 fires with the remedy.
RESOLVED=$(python3 -c "$PY_RESOLVE" "$INSTALL_DIR_ARG" "$SOURCE_DIR_ARG" "${HOME:-}") || {
    echo "rollback-install: FAILED: could not read Claude Code's installation records (python3 exited nonzero; its own message is above). Nothing has been changed." >&2
    exit 1
}

SBE_RB_STATUS=""
SBE_RB_REASON=""
SBE_RB_REFUSAL=""
SBE_RB_INSTALL_DIR=""
SBE_RB_INSTALL_FROM=""
SBE_RB_INSTALL_VERSION=""
SBE_RB_INSTALL_SHA=""
SBE_RB_KEY=""
SBE_RB_MARKET=""
SBE_RB_NOTE=""
SBE_RB_RECORD=""
SBE_RB_SOURCE_DIR=""
SBE_RB_SOURCE_FROM=""
eval "$RESOLVED"

if [ "$SBE_RB_STATUS" != "ok" ]; then
    echo "rollback-install: REFUSED: $SBE_RB_REASON"
    exit 1
fi

INSTALL_DIR="$SBE_RB_INSTALL_DIR"
SOURCE_DIR="$SBE_RB_SOURCE_DIR"

# --- refusal 7: the source has to be a git clone, because every remaining
# step is a git operation on it and a release tag is the only target this
# script will accept.
if ! git -C "$SOURCE_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "rollback-install: REFUSED: the release source '$SOURCE_DIR' ($SBE_RB_SOURCE_FROM) is not a git clone. This rollback moves an installation between release tags, which needs the git history a clone carries; a copied or extracted directory has no earlier version to return to. Nothing has been changed. Re-add the marketplace from a real clone (claude plugin marketplace add /path/to/clone) or name one with --source-dir."
    exit 1
fi

# --- refusal 8: uncommitted changes in the source. `git checkout` would
# either clobber them or fail halfway; refusing up front is the only outcome
# that leaves the tree exactly as it was found.
DIRTY=$(git -C "$SOURCE_DIR" status --porcelain 2>/dev/null || true)
if [ -n "$DIRTY" ]; then
    echo "rollback-install: REFUSED: the release source at $SOURCE_DIR has uncommitted changes, and rolling back would overwrite them. Nothing has been changed. Save or discard them yourself (git -C $SOURCE_DIR status) and re-run; this script will not decide for you which of your edits are disposable."
    exit 1
fi

SOURCE_HEAD=$(git -C "$SOURCE_DIR" rev-parse HEAD)
SOURCE_RETURN=$(git -C "$SOURCE_DIR" symbolic-ref --quiet --short HEAD 2>/dev/null || printf '%s' "$SOURCE_HEAD")

# The commit the installed bytes came from. The harness records it
# (gitCommitSha); it is used only when the source can actually resolve it,
# because a record naming a commit this clone has never fetched is not a
# fact about this clone. Which basis was used is printed, never implied.
BASIS="$SOURCE_HEAD"
BASIS_FROM="the source's current HEAD (the harness record names no commit this clone can resolve)"
if [ -n "$SBE_RB_INSTALL_SHA" ] && git -C "$SOURCE_DIR" rev-parse --quiet --verify "$SBE_RB_INSTALL_SHA^{commit}" >/dev/null 2>&1; then
    BASIS=$(git -C "$SOURCE_DIR" rev-parse "$SBE_RB_INSTALL_SHA^{commit}")
    BASIS_FROM="the commit the harness recorded for the installed copy"
fi
BASIS_SHORT=$(git -C "$SOURCE_DIR" rev-parse --short "$BASIS")

INSTALL_VERSION="$SBE_RB_INSTALL_VERSION"
if [ -z "$INSTALL_VERSION" ] && [ -f "$INSTALL_DIR/VERSION" ]; then
    INSTALL_VERSION=$(cat "$INSTALL_DIR/VERSION")
fi
if [ -z "$INSTALL_VERSION" ]; then
    INSTALL_VERSION="unknown (no version in the record and no VERSION file at $INSTALL_DIR)"
fi

# is_earlier_release TAG: true when TAG names a commit that is a real
# ancestor of the installed commit and is not that commit itself. Same test
# scripts/test-upgrade-rollback.sh applies when it picks a previous release,
# applied here against the source the installation came from.
is_earlier_release() {
    tag="$1"
    if ! git -C "$SOURCE_DIR" rev-parse --quiet --verify "$tag^{commit}" >/dev/null 2>&1; then
        return 1
    fi
    if ! git -C "$SOURCE_DIR" merge-base --is-ancestor "$tag" "$BASIS" 2>/dev/null; then
        return 1
    fi
    if [ "$(git -C "$SOURCE_DIR" rev-parse "$tag^{commit}")" = "$BASIS" ]; then
        return 1
    fi
    return 0
}

TARGET_TAG=""
if [ -n "$TO_ARG" ]; then
    # --- refusal 9: an explicit target is checked, never trusted.
    if ! is_earlier_release "$TO_ARG"; then
        echo "rollback-install: REFUSED: --to '$TO_ARG' is not an earlier release of this installation. It has to name a tag that exists in $SOURCE_DIR, is an ancestor of the installed commit ($BASIS_SHORT), and is not that same commit. Nothing has been changed. Run 'git -C $SOURCE_DIR tag --list' to see what is actually there."
        exit 1
    fi
    TARGET_TAG="$TO_ARG"
else
    ALL_TAGS=$(git -C "$SOURCE_DIR" tag --list 'v[0-9]*' 2>/dev/null || true)
    for t in $ALL_TAGS; do
        if ! is_earlier_release "$t"; then
            continue
        fi
        if [ -z "$TARGET_TAG" ]; then
            TARGET_TAG="$t"
        else
            t_date=$(git -C "$SOURCE_DIR" log -1 --format=%ct "$t")
            best_date=$(git -C "$SOURCE_DIR" log -1 --format=%ct "$TARGET_TAG")
            if [ "$t_date" -gt "$best_date" ]; then
                TARGET_TAG="$t"
            fi
        fi
    done
fi

# --- refusal 10: nothing to roll back TO. The honest end of this script, and
# the reason it exists in this shape.
if [ -z "$TARGET_TAG" ]; then
    echo "rollback-install: REFUSED: no earlier release to roll back to."
    echo "rollback-install: the install at $INSTALL_DIR is version $INSTALL_VERSION, built from $SOURCE_DIR at $BASIS_SHORT ($BASIS_FROM), and that history carries no v<number> tag that is both an ancestor of that commit and not that commit itself."
    echo "rollback-install: this is a refusal, not a failure and not a pass: the one thing a rollback needs, a previous version somebody actually installed, is absent here. Nothing has been changed."
    echo "rollback-install: if you know the release you want, name it with --to <tag> and it will be checked the same way; 'git -C $SOURCE_DIR fetch --tags' first if the tag was published after this clone was made."
    exit 1
fi

TARGET_VERSION=$(git -C "$SOURCE_DIR" show "$TARGET_TAG:VERSION" 2>/dev/null || printf '%s' "unknown (no VERSION file at that tag)")

# --- refusal 11: (--apply only) the re-install step needs `claude`, and
# discovering that AFTER the checkout would leave the source moved with the
# harness still loading the version it had.
if [ "$APPLY" = "1" ] && ! command -v claude >/dev/null 2>&1; then
    echo "rollback-install: REFUSED: claude is not on PATH, and re-installing the rolled-back plugin needs it. Nothing has been changed. Install the Claude Code CLI and re-run; a rollback that moved the source and could not tell the harness would leave you unsure which version is loaded, which is the confusion this whole path exists to remove."
    exit 1
fi

echo "rollback-install: installation:  $INSTALL_DIR"
echo "rollback-install: found via:     $SBE_RB_INSTALL_FROM"
if [ -n "$SBE_RB_NOTE" ]; then
    echo "rollback-install: note:         $SBE_RB_NOTE"
fi
if [ "$SELF_ROOT" = "$INSTALL_DIR" ]; then
    echo "rollback-install: this script is running from that same installation."
else
    echo "rollback-install: this script is running from $SELF_ROOT, which is NOT the installation above; the installation above is the one the harness loads, and the one this rolls back."
fi
echo "rollback-install: release source: $SOURCE_DIR ($SBE_RB_SOURCE_FROM)"
echo "rollback-install: installed now:  version $INSTALL_VERSION, from $BASIS_SHORT ($BASIS_FROM)"
echo "rollback-install: would roll back to: $TARGET_TAG (version $TARGET_VERSION)"
echo "rollback-install: exact steps:"
echo "rollback-install:   1. git -C $SOURCE_DIR checkout --detach $TARGET_TAG"
if [ "$INSTALL_DIR" = "$SOURCE_DIR" ]; then
    echo "rollback-install:   2. (the installation IS the source, so step 1 already moved the installed bytes)"
else
    echo "rollback-install:   2. claude plugin marketplace add $SOURCE_DIR, then claude plugin install brothersbe@${SBE_RB_MARKET:-brothersbe}, so the harness copies $TARGET_TAG into its own plugin store"
fi
echo "rollback-install:   3. re-read $SBE_RB_RECORD to find where the harness put the rolled-back bytes, and refuse to claim success if the version it reports did not move"
echo "rollback-install:   4. sh <that directory>/scripts/verify-install.sh, so the files checked are the files loaded, not the source they came from"
echo "rollback-install: nothing outside those two directories is written, and no settings file is edited at any point."

if [ "$APPLY" != "1" ]; then
    echo "rollback-install: PREVIEW only, nothing has been changed. Re-run with --apply to perform the steps above."
    exit 0
fi

echo ""
echo "rollback-install: applying. Step 1: checking out $TARGET_TAG in $SOURCE_DIR"
if ! git -C "$SOURCE_DIR" checkout --detach "$TARGET_TAG"; then
    echo "rollback-install: FAILED at step 1: git checkout of $TARGET_TAG did not succeed (its own message is above). The source is still at $BASIS_SHORT and nothing was installed; read that message before re-running." >&2
    exit 1
fi

NEW_INSTALL_DIR="$INSTALL_DIR"
NEW_VERSION=""
if [ "$INSTALL_DIR" = "$SOURCE_DIR" ]; then
    echo "rollback-install: step 2: the installation is the source, so the bytes moved with step 1; re-registering it so the harness reads the rolled-back manifest"
    if ! claude plugin marketplace add "$SOURCE_DIR"; then
        echo "rollback-install: FAILED at step 2: 'claude plugin marketplace add $SOURCE_DIR' did not succeed (its own message is above). The files are at $TARGET_TAG; the harness may still be loading the previous version until this command succeeds. Return with 'git -C $SOURCE_DIR checkout $SOURCE_RETURN'." >&2
        exit 1
    fi
    if ! claude plugin install "brothersbe@${SBE_RB_MARKET:-brothersbe}"; then
        echo "rollback-install: FAILED at step 2: 'claude plugin install brothersbe@${SBE_RB_MARKET:-brothersbe}' did not succeed (its own message is above). The files are at $TARGET_TAG; the harness may still be loading the previous version. Return with 'git -C $SOURCE_DIR checkout $SOURCE_RETURN'." >&2
        exit 1
    fi
    if [ -f "$NEW_INSTALL_DIR/VERSION" ]; then
        NEW_VERSION=$(cat "$NEW_INSTALL_DIR/VERSION")
    fi
else
    echo "rollback-install: step 2: re-registering the source and re-installing the plugin from $TARGET_TAG"
    if ! claude plugin marketplace add "$SOURCE_DIR"; then
        echo "rollback-install: FAILED at step 2: 'claude plugin marketplace add $SOURCE_DIR' did not succeed (its own message is above). The source is at $TARGET_TAG and the installation at $INSTALL_DIR is untouched, so the harness is still loading version $INSTALL_VERSION. Return with 'git -C $SOURCE_DIR checkout $SOURCE_RETURN'." >&2
        exit 1
    fi
    if ! claude plugin install "brothersbe@${SBE_RB_MARKET:-brothersbe}"; then
        echo "rollback-install: FAILED at step 2: 'claude plugin install brothersbe@${SBE_RB_MARKET:-brothersbe}' did not succeed (its own message is above). The source is at $TARGET_TAG and the installation at $INSTALL_DIR is untouched, so the harness is still loading version $INSTALL_VERSION. Return with 'git -C $SOURCE_DIR checkout $SOURCE_RETURN'." >&2
        exit 1
    fi

    echo "rollback-install: step 3: re-reading $SBE_RB_RECORD for where the harness put the rolled-back bytes"
    AFTER=$(python3 -c "$PY_RESOLVE" "" "$SOURCE_DIR" "${HOME:-}") || {
        echo "rollback-install: FAILED at step 3: could not re-read Claude Code's installation record after installing (python3 exited nonzero; its own message is above). The source is at $TARGET_TAG. Check 'claude plugin list' yourself before trusting what is loaded." >&2
        exit 1
    }
    SBE_RB_STATUS=""
    SBE_RB_REASON=""
    eval "$AFTER"
    if [ "$SBE_RB_STATUS" != "ok" ]; then
        echo "rollback-install: FAILED at step 3: after installing, the harness record still does not name a usable brothersbe install: $SBE_RB_REASON" >&2
        exit 1
    fi
    NEW_INSTALL_DIR="$SBE_RB_INSTALL_DIR"
    NEW_VERSION="$SBE_RB_INSTALL_VERSION"
fi

if [ -z "$NEW_VERSION" ]; then
    NEW_VERSION="unknown"
fi

# The step that makes this an undo rather than a report of one. If the
# harness still names the version that was installed before, the two claude
# commands ran and changed nothing that matters, and saying ROLLED BACK here
# would be exactly the silence this script was rewritten to remove.
if [ "$NEW_VERSION" = "$INSTALL_VERSION" ]; then
    echo "rollback-install: FAILED at step 3: the source moved to $TARGET_TAG, but the harness still reports version $NEW_VERSION at $NEW_INSTALL_DIR, the same version as before. The rollback did NOT take effect, and this run will not call it done. Check 'claude plugin list' and whether another marketplace also provides brothersbe; return the source with 'git -C $SOURCE_DIR checkout $SOURCE_RETURN'." >&2
    exit 1
fi

echo "rollback-install: step 4: verifying the INSTALLED bytes at $NEW_INSTALL_DIR against that release's own manifest"
if [ ! -f "$NEW_INSTALL_DIR/scripts/verify-install.sh" ]; then
    echo "rollback-install: FAILED at step 4: $NEW_INSTALL_DIR does not carry scripts/verify-install.sh, so this rollback cannot be checked. Version $NEW_VERSION is installed and unverified; return the source with 'git -C $SOURCE_DIR checkout $SOURCE_RETURN' and re-install if that is not what you wanted." >&2
    exit 1
fi
if ! sh "$NEW_INSTALL_DIR/scripts/verify-install.sh"; then
    echo "rollback-install: FAILED at step 4: verify-install.sh did not pass for the installed copy at $NEW_INSTALL_DIR (its output is above). The rollback moved the bytes and this run will not vouch for them; return the source with 'git -C $SOURCE_DIR checkout $SOURCE_RETURN' and re-install." >&2
    exit 1
fi

echo ""
echo "rollback-install: ROLLED BACK. The installation the harness loads moved from version $INSTALL_VERSION to version $NEW_VERSION ($TARGET_TAG), it now lives at $NEW_INSTALL_DIR, and that directory was checked against its own release's scripts/verify-install.sh."
echo "rollback-install: restart Claude Code to load it, the same restart 'claude plugin update brothersbe' needs."
echo "rollback-install: to come forward again: git -C $SOURCE_DIR checkout $SOURCE_RETURN, then claude plugin marketplace add $SOURCE_DIR and claude plugin install brothersbe@${SBE_RB_MARKET:-brothersbe}."
