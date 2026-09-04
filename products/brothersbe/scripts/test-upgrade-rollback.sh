#!/bin/sh
# test-upgrade-rollback.sh: does upgrading from the previous published tag to
# HEAD, then rolling back to that previous tag, work with nothing more than
# "install, verify, install again, verify, install the old one back, verify"?
#
# What "install" means here, deliberately: the same `git archive <ref> | tar
# x` an operator running docs/RELEASE.md's pin command effectively gets
# (nothing in this project documents an in-place `git pull` upgrade yet), so
# every step below wipes the target directory and re-extracts fresh, rather
# than extracting a newer archive OVER an older one's leftover files. That
# is a stated scope decision, not a silently assumed one: this script proves
# "the previous tag, then HEAD, then the previous tag again each install
# clean and verify clean", not "extracting a newer archive over an older
# checkout in place never leaves a stale file", which is a different claim
# this script does not make.
#
# What counts as a failure here, and what does not: this gate proves the
# CURRENT release's rollback safety, HEAD installs clean, and the rollback
# reproduces the previous tag's tree IDENTICALLY (the same verification
# result a fresh install of that tag gets), both still hard requirements
# below. A defect INTRINSIC to a previous PUBLISHED tag (its own tree not
# matching its own shipped manifest, visible on the very first install of
# that tag, before HEAD is ever touched) is recorded as a NAMED finding
# rather than failed here, because a published tag is immutable and the
# defect is not this release's to fix. The concrete case this was written
# against: v3.2.0 shipped CHECKSUMS entries that do not match its own tree
# for two static files. A structural failure (git archive not extracting,
# verify-install.sh missing from the tree, or verify-install.sh exiting
# nonzero with no MISMATCH, MISSING, or EXTRA line to show for it), any
# problem against HEAD's own manifest, or a rollback whose verification
# result differs from a fresh previous-release install, all remain hard
# failures below, unchanged.
#
# The kill criterion the plugin conversion plan states for this wave is "an
# install that needs a manual global settings edit"; every install below
# writes only inside one temporary directory this script creates and
# removes on exit, the same discipline scripts/test-install-artifact.sh
# holds itself to.
#
# The honesty this script exists to enforce: as of this wave, no release of
# THIS PRODUCT has been tagged in this repository (`git tag --list
# 'brothersbe--v[0-9]*'` is empty; the hub's plain `v<digit>` tags are the
# umbrella's, not this product's, see the selection block below). An
# upgrade-and-rollback test with no previous release to upgrade FROM cannot
# exercise an upgrade, and a script that reported PASSED anyway, or silently
# skipped with exit 0 and no explanation, would be claiming a control ran
# when it did not. So: when no previous product tag exists, this prints a
# NO-DATA verdict naming exactly what it looked for and what it deliberately
# ignored, and exits 0 without ever claiming an upgrade was tested. Exit 0 here means
# "nothing failed", the same as everywhere else in this project; it does
# NOT mean "passed". No unit suite asserts this branch, deliberately: the
# moment this repository cuts its first tag the branch stops being
# reachable here, and a suite pinned to it would rot. The honesty was
# exercised directly while this script was calibrated (docs/ROLLOUT.md
# names where), and the sentence it prints says exactly what was not run.
#
# Runs identically from a normal `git clone` and from a git worktree (see
# scripts/test-install-artifact.sh's header for why: `git archive` and
# `git tag`/`git describe` resolve through git's own plumbing, never by
# reading the .git path's type directly).
#
# POSIX sh only, no bashisms. Calls scripts/verify-install.sh rather than
# reimplementing it, same as scripts/test-install-artifact.sh.
#
# Usage:
#   scripts/test-upgrade-rollback.sh

set -e

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$ROOT"

# Find the most recent RELEASE tag OF THIS PRODUCT that is an ANCESTOR of
# HEAD, i.e. a real previous release on this line of history, not a tag
# sitting on an unrelated branch or pointing at HEAD itself.
#
# THE PATTERN IS PRODUCT-PREFIXED, and that is the whole point. This product
# no longer owns its repository: it is one of three products inside the
# Brother hub, and the hub carries its OWN umbrella tags in the plain
# `v<digit>` form (v0.9.6, v1.0.1, v2.1.1 as of 2026-09-04). Those are
# releases of the hub, not of this product, and at most of them
# products/brothersbe/ did not exist at all. The plain pattern selected
# v0.9.6 as "the previous release of this product" and then failed because
# scripts/verify-install.sh did not ship in it, which is a true statement
# about a tree that was never this product's release.
#
# The estate's documented per-product form is `{plugin-name}--v{version}`,
# TWO dashes (products/brothermode/docs/plan/ADR-2026-08-23-one-brother-repository.md,
# corrected against the plugin-dependencies documentation; a single dash
# fails silently with no-matching-tag), so this product's releases are
# `brothersbe--v<version>` and nothing else. The old standalone repository
# also cut plain `v3.2.0`-style tags before the merge; those live in
# BrotherSBE's history, not in this repository, so they are unreachable here
# and are deliberately NOT matched (matching them would re-open the exact
# hole above: a plain `v` pattern reads the hub's umbrella tags).
#
# The pattern also keeps out `archive/*` tags, preservation snapshots of
# deleted branch tips that nobody installed. Without a pattern this loop once
# selected `archive/worktree-agent-a2e2f84b3d27f281f` as "the previous
# release" and failed verify-install against a tree nobody ever published.
#
# `git tag --list` can be empty (today, always: no `brothersbe--v*` tag has
# been cut in the hub yet); guarded explicitly rather than let a bare `for`
# over an empty command substitution misbehave under `set -e` on some shells.
TAG_PATTERN='brothersbe--v[0-9]*'
PREV_TAG=""
ALL_TAGS=$(git tag --list "$TAG_PATTERN" 2>/dev/null || true)
if [ -n "$ALL_TAGS" ]; then
    for t in $ALL_TAGS; do
        if ! git merge-base --is-ancestor "$t" HEAD 2>/dev/null; then
            continue
        fi
        if [ "$(git rev-parse "$t^{commit}" 2>/dev/null)" = "$(git rev-parse HEAD)" ]; then
            continue
        fi
        if [ -z "$PREV_TAG" ]; then
            PREV_TAG="$t"
        else
            t_date=$(git log -1 --format=%ct "$t")
            prev_date=$(git log -1 --format=%ct "$PREV_TAG")
            if [ "$t_date" -gt "$prev_date" ]; then
                PREV_TAG="$t"
            fi
        fi
    done
fi

if [ -z "$PREV_TAG" ]; then
    echo "test-upgrade-rollback: NO-DATA. No previous release of THIS PRODUCT was found."
    echo "test-upgrade-rollback: What was looked for, exactly: \`git tag --list '$TAG_PATTERN'\`"
    echo "test-upgrade-rollback: (the estate's per-product tag form, {plugin-name}--v{version},"
    echo "test-upgrade-rollback: two dashes), restricted to tags that are ancestors of HEAD and"
    echo "test-upgrade-rollback: are not HEAD itself. That list is empty in this repository."
    echo "test-upgrade-rollback: What was deliberately NOT looked at: the hub's own umbrella tags"
    echo "test-upgrade-rollback: in the plain \`v<digit>\` form. Those are releases of the Brother"
    echo "test-upgrade-rollback: hub, not of this product; most of them predate"
    echo "test-upgrade-rollback: products/brothersbe/ existing, and installing one of them as"
    echo "test-upgrade-rollback: 'the previous release of this product' proves nothing about this"
    echo "test-upgrade-rollback: product's upgrade path."
    echo "test-upgrade-rollback: So there is no earlier release to upgrade FROM, and an"
    echo "test-upgrade-rollback: upgrade-then-rollback cannot be exercised against real history"
    echo "test-upgrade-rollback: on this run. This is not a pass: it is a stated absence of the"
    echo "test-upgrade-rollback: one fixture this script needs. No claim is made that an upgrade"
    echo "test-upgrade-rollback: was tested. Once the first $TAG_PATTERN tag exists, this script"
    echo "test-upgrade-rollback: finds it automatically and exercises the real path below."
    exit 0
fi

echo "test-upgrade-rollback: previous tag found: $PREV_TAG"

WORKDIR=$(mktemp -d 2>/dev/null || echo "/tmp/sbe-test-upgrade-rollback-work.$$")
mkdir -p "$WORKDIR"
TARGET="$WORKDIR/install"
trap 'rm -rf "$WORKDIR"' EXIT INT TERM

FAILED=0
PREV_INTRINSIC=""

# install_and_verify REF LABEL PROBLEMS_FILE: wipe TARGET, archive REF into it
# fresh, run verify-install.sh from that fresh copy, echoing its full output
# exactly as before (nothing hidden). Every MISMATCH, MISSING, or EXTRA path
# verify-install.sh reports is written sorted to PROBLEMS_FILE, one path per
# line; PROBLEMS_FILE is always created, empty on a clean pass, so a caller
# can diff one install's problem set against another's.
#
# `git archive` failing to extract, verify-install.sh missing from the
# extracted tree, or verify-install.sh exiting nonzero with PROBLEMS_FILE
# left empty (it errored for some reason other than a content mismatch, a
# structural problem, not a content difference) all set FAILED=1 here
# directly and return, same as before. A nonzero exit WITH a nonempty
# PROBLEMS_FILE (a real content difference against that install's own
# manifest) is reported but left for the caller to score: what a content
# difference means differs by call site below (intrinsic to a previous
# immutable tag, a defect in HEAD's own manifest, or a rollback that failed
# to reproduce a fresh install of the previous tag are three different
# things), so this function states the fact and stops short of the verdict.
install_and_verify() {
    ref="$1"
    label="$2"
    problems_file="$3"
    : > "$problems_file"
    rm -rf "$TARGET"
    mkdir -p "$TARGET"
    echo "test-upgrade-rollback: installing $label ($ref) into a fresh directory"
    if ! git archive --format=tar "$ref" | (cd "$TARGET" && tar xf -); then
        echo "test-upgrade-rollback: FAIL, \`git archive $ref\` did not extract cleanly" >&2
        FAILED=1
        return
    fi
    if [ ! -f "$TARGET/scripts/verify-install.sh" ]; then
        echo "test-upgrade-rollback: FAIL, scripts/verify-install.sh did not ship at $label ($ref)" >&2
        FAILED=1
        return
    fi
    echo "test-upgrade-rollback: verifying $label"
    if verify_out=$(sh "$TARGET/scripts/verify-install.sh" 2>&1); then
        verify_status=0
    else
        verify_status=$?
    fi
    printf '%s\n' "$verify_out"
    printf '%s\n' "$verify_out" \
        | grep -E '^(MISMATCH|MISSING|EXTRA):' \
        | sed -E 's/^[A-Za-z]+:[[:space:]]+//' \
        | sort > "$problems_file"

    if [ "$verify_status" -eq 0 ]; then
        echo "test-upgrade-rollback: $label verified clean"
        return
    fi

    if [ ! -s "$problems_file" ]; then
        echo "test-upgrade-rollback: FAIL, verify-install.sh did not pass for $label ($ref), and reported no MISMATCH, MISSING, or EXTRA line (a structural problem, not a content difference; see its output above)" >&2
        FAILED=1
        return
    fi

    echo "test-upgrade-rollback: $label reported $(wc -l < "$problems_file" | tr -d ' ') problem path(s) against its own manifest (see the MISMATCH, MISSING, or EXTRA lines above); scored by the caller below"
}

# THE ONE RECORDED IMMUTABLE EXEMPTION, pinned, not a class. A previous
# release's own manifest defect is excused ONLY when it is EXACTLY this: tag
# v3.2.0, and every offending path is one of these two static files. A
# different path, an EXTRA entry (exactly the shape of a planted file), or
# any other previous tag is NOT this exemption and hard fails below, the
# same as before this exemption existed. Widening this list is a
# deliberate, reviewed decision, never an automatic one.
#
# NOTE, 2026-09-04: v3.2.0 was cut in the standalone BrotherSBE repository,
# whose history is not this repository's, and the tag selection above now
# matches only `brothersbe--v*`. So this exemption is unreachable here and
# excuses nothing today. It is KEPT rather than deleted because it records a
# real defect in a published, immutable tag: if that history is ever grafted
# in, or the tag re-cut under the prefixed form, the exemption must still be
# the pinned two-file one it always was, not a rediscovered class.
EXEMPT_TAG="v3.2.0"
printf '%s\n' "docs/book/assets/mermaid.min.js" "scripts/derive_refusal_table.py" | sort > "$WORKDIR/prev_allowed"

# First install: the previous release, on its own. A nonempty problem set
# here is checked against the one pinned exemption above, never excused as a
# whole class. `comm -23 problems.prev1 prev_allowed` (both sorted) lists
# paths in problems.prev1 that are NOT in the allowed set; a POSIX box
# without `comm` falls back to the equivalent `grep -vxF -f` set-difference.
install_and_verify "$PREV_TAG" "the previous release" "$WORKDIR/problems.prev1"
if [ -s "$WORKDIR/problems.prev1" ]; then
    if [ "$PREV_TAG" = "$EXEMPT_TAG" ]; then
        if command -v comm >/dev/null 2>&1; then
            comm -23 "$WORKDIR/problems.prev1" "$WORKDIR/prev_allowed" > "$WORKDIR/prev_unexpected"
        else
            grep -vxF -f "$WORKDIR/prev_allowed" "$WORKDIR/problems.prev1" > "$WORKDIR/prev_unexpected" || true
        fi
    else
        cp "$WORKDIR/problems.prev1" "$WORKDIR/prev_unexpected"
    fi
    if [ -s "$WORKDIR/prev_unexpected" ]; then
        echo "test-upgrade-rollback: FAIL, the previous release $PREV_TAG has a manifest defect that is NOT the one recorded immutable exemption (v3.2.0's two static files); unexpected problem path(s):" >&2
        while IFS= read -r p; do
            [ -z "$p" ] && continue
            echo "test-upgrade-rollback:   $p" >&2
        done < "$WORKDIR/prev_unexpected"
        FAILED=1
    else
        PREV_INTRINSIC=1
    fi
fi

# Second install: HEAD. Unchanged scope: THIS release's own manifest must be
# clean, structural or content, no exception.
install_and_verify "HEAD" "the upgrade to HEAD" "$WORKDIR/problems.head"
if [ -s "$WORKDIR/problems.head" ]; then
    echo "test-upgrade-rollback: FAIL, the upgrade to HEAD reported a problem against HEAD's own manifest (see the MISMATCH, MISSING, or EXTRA lines above); this release's manifest must be clean" >&2
    FAILED=1
fi

# Third install: roll back to the previous release again. The question this
# leg asks is not "does the previous release verify clean" (already asked
# and answered above); it is "did the rollback reproduce the SAME
# verification result as a fresh install of the previous tag" (a
# consistency check): problems.prev1 and problems.prev3 read identical.
# Every install here is a fresh `git archive` extraction into a wiped
# directory (see the header), so an IN-PLACE stale file left by an upgrade
# done some other way is OUT OF THIS HARNESS'S SCOPE by design; this leg
# cannot detect that and does not claim to.
install_and_verify "$PREV_TAG" "the rollback to the previous release" "$WORKDIR/problems.prev3"
if diff "$WORKDIR/problems.prev1" "$WORKDIR/problems.prev3" > "$WORKDIR/rollback.diff" 2>&1; then
    echo "test-upgrade-rollback: the rollback to $PREV_TAG reproduced the previous release's own verification result exactly (rollback fidelity assertion passed)"
else
    echo "test-upgrade-rollback: FAIL, the rollback to $PREV_TAG did not reproduce a fresh install of $PREV_TAG: the verification results differ, which is the stale file after rollback anomaly this gate exists to catch" >&2
    cat "$WORKDIR/rollback.diff" >&2
    FAILED=1
fi

echo ""
if [ "$FAILED" -ne 0 ]; then
    echo "test-upgrade-rollback: FAILED. Upgrading $PREV_TAG -> HEAD -> $PREV_TAG did not verify clean at every step; see the FAIL line(s) above." >&2
    exit 1
fi
echo "test-upgrade-rollback: PASSED. $PREV_TAG -> HEAD -> $PREV_TAG, each step archived into a fresh directory and verified with scripts/verify-install.sh, nothing written outside the one temporary directory this script created and removed on exit."
if [ -n "$PREV_INTRINSIC" ]; then
    echo "test-upgrade-rollback: NOTE, a recorded finding, not a failure: the previous release $PREV_TAG shipped a CHECKSUMS manifest that does not match its own tree for:"
    while IFS= read -r p; do
        [ -z "$p" ] && continue
        echo "test-upgrade-rollback:   $p"
    done < "$WORKDIR/problems.prev1"
    echo "test-upgrade-rollback: that is a defect in a PUBLISHED, immutable release; it is recorded here rather than blocking the current release, because a published tag cannot be altered. HEAD installed clean, and the rollback reproduced $PREV_TAG's tree identically (the same verification result as a fresh install)."
fi
exit 0
