#!/bin/sh
# The version cut, as one script the founder runs. Written 2026-09-03 because the
# app safety classifier refuses the version bump to a session (a control-plane
# path), so the bump is the founder's hand; everything after it is mechanical.
#
# Run from the hub root:  sh scripts/cut_v1.0.0.sh [VERSION]
#   VERSION defaults to 1.0.0 (this file's own launch cut); pass any other
#   version to cut that one instead, e.g. sh scripts/cut_v1.0.0.sh 1.0.1.
#
# It bumps both manifests together (the validator fails on a mismatch), points
# every ref at the tag, runs the release invariant and the export dry run, and
# then STOPS before the one irreversible step. It never pushes on its own: the
# push and tag are the last block, commented out, for you to run once the dry
# run reads CLEAR.
set -e
cd "$(dirname "$0")/.."

VERSION="${1:-1.0.0}"
export VERSION

# The public tag this cut publishes as, and the public repository it lands
# on. Named here, once, so nothing below retypes them; scripts/
# release_note_from_tree.py no longer greps this file for the tag (TAG=v
# followed by a shell variable is not a resolved value on disk for any
# version other than the one this run actually performs), it takes its own
# --version argument instead. PUBLIC_REMOTE stays a plain literal, read the
# same way as before, because the remote never changes with the version.
TAG="v$VERSION"
PUBLIC_REMOTE=https://github.com/khalilmaaouni/Brother

echo "== 1. bump the source of truth and every carrier to $VERSION and point refs at the tag =="
# scripts/version_source.py is the one place that knows every carrier
# (.claude-plugin/marketplace.json's metadata.version, the brother plugin
# entry's version, every plugin entry's source.ref, the two bundle
# plugin.json files, and docs/VERSIONING.md's "Current version:" line). It
# reuses that same logic for scripts/test_version_source.py's drift check,
# so the cut and the check can never drift apart from each other.
python3 scripts/version_source.py --write --version "$VERSION"

echo "== 1b. re-pin the product's public install tag to $TAG =="
# Row BAT-103. The 1.0.3 cut moved products/brothermode/README.md's pinned
# clone to v1.0.3 by hand and left PUBLIC_INSTALL_TAG (the constant every
# install page is held equal to) at v1.0.0, so four of that product's own
# documentation tests went red on the merged tip. The re-pin is mechanical,
# so it belongs in the cut rather than in a maintainer's memory.
python3 - <<'REPIN'
import io
import os
import re

VERSION = os.environ['VERSION']
TAG = 'v' + VERSION
FACTS = 'products/brothermode/tools/bm_project_facts.py'
PAGES = ('README.md', 'docs/QUICKSTART.md', 'docs/SETUP.md', 'docs/RELEASE.md')

try:
    with io.open(FACTS, encoding='utf-8') as fh:
        text = fh.read()
except (IOError, OSError) as exc:
    raise SystemExit(
        '%s: cannot read (%s), refusing to re-pin silently' % (FACTS, exc))
m = re.search(r'^PUBLIC_INSTALL_TAG = "([^"]+)"$', text, re.M)
if not m:
    raise SystemExit(
        '%s: no PUBLIC_INSTALL_TAG line in the form this step rewrites, '
        'refusing to re-pin silently' % FACTS)
old = m.group(1)
if old == TAG:
    print('%s -> PUBLIC_INSTALL_TAG already %s' % (FACTS, TAG))
else:
    try:
        with io.open(FACTS, 'w', encoding='utf-8') as fh:
            fh.write(text[:m.start(1)] + TAG + text[m.end(1):])
    except (IOError, OSError) as exc:
        raise SystemExit('%s: cannot write (%s)' % (FACTS, exc))
    print('%s -> PUBLIC_INSTALL_TAG %s (was %s)' % (FACTS, TAG, old))
    for rel in PAGES:
        path = os.path.join('products', 'brothermode', rel)
        try:
            with io.open(path, encoding='utf-8') as fh:
                page = fh.read()
        except (IOError, OSError) as exc:
            raise SystemExit('%s: cannot read (%s)' % (path, exc))
        moved = page.replace('--branch %s ' % old, '--branch %s ' % TAG)
        moved = moved.replace('`--branch %s`' % old, '`--branch %s`' % TAG)
        moved = moved.replace('currently `%s`' % old, 'currently `%s`' % TAG)
        if moved == page:
            continue
        try:
            with io.open(path, 'w', encoding='utf-8') as fh:
                fh.write(moved)
        except (IOError, OSError) as exc:
            raise SystemExit('%s: cannot write (%s)' % (path, exc))
        print('%s -> pinned install %s' % (path, TAG))
REPIN

echo "== 2. drop the release-invariant exception (the tag will exist) =="
python3 - <<'PY'
import json
p = 'docs/plan/BATTERY-EXPECTATIONS.json'
d = json.load(open(p))
if 'release-invariant' in d['checks']:
    del d['checks']['release-invariant']
    json.dump(d, open(p, 'w'), indent=2); open(p, 'a').write('\n')
    print('release-invariant exception removed')
else:
    print('release-invariant exception already absent')
PY

echo "== 2r. regenerate what the bump above may have gone stale (SYSTEM.md, bundle/runtime, product checksums) =="
# Row measured on 1.0.4: these three landed in a commit AFTER the release
# note had already stamped an earlier HEAD, so the note named a revision
# whose export was short exactly these files (X7 FAIL). Running them here,
# before the bump is committed, means the commit below carries all of it
# together, and refresh_cut.py's own dirty-tree refusal (2s onward) is the
# backstop if a caller ever skips straight to 2b anyway.
python3 scripts/system_doc.py
python3 scripts/bundle_runtime.py
# checksums.sh cd's into its OWN product root by $0's location before it
# reads its argument, so the argument is that product's own relative path
# (CHECKSUMS.sha256), never prefixed with the product directory again.
sh products/brothermode/scripts/checksums.sh CHECKSUMS.sha256
sh products/brothersbe/scripts/checksums.sh CHECKSUMS.sha256

echo "== 2s. commit the bump and the regeneration together =="
BUMP_MSG="$VERSION: the version bump and the regenerated manifests"
echo "git add -A && git commit -q -m \"$BUMP_MSG\""
git add -A
git commit -q -m "$BUMP_MSG"
git log -1 --oneline

echo "== 2b. refresh the release note and export manifest (tree is clean now, so the note's stamped revision covers everything above) =="
python3 scripts/refresh_cut.py --version "$VERSION"

echo "== 2b2. preflight: the export's own tag-time checks on the export tree, right after the note exists and before any long step =="
# Row DEL-15. The 1.0.10 cut ran the ~45 minutes of steps below (note
# refresh, perturbation drive, plugin validate, release invariant, the
# export --dry-run at step 4) and read CLEAR at 21:12, then the export
# itself refused at 21:5x with "the export tree's own readiness gate does
# not read READY: Restore drill (NO-DATA)": export_public.py's tag_time_
# checks (the release note stamped, every product's own verify-install.sh,
# readiness_gate.py, every relative markdown link, every README prove
# command) only ran under --push --tag, 45 minutes after this line's own
# candidate export tree was first built. The 1.0.2 cut hit the same class
# on 2026-09-04 (scripts/readiness_gate.py's own docstring records it).
# --tag-time-checks reaches the same verdict here, in a plain dry run, in
# about 3 minutes, before the perturbation drive, validate and the final
# dry run below. It sits AFTER 2b on purpose: before the note for $VERSION
# exists, the readiness gate's release-invariant item fails by construction
# (measured 2026-09-07 22:0x in a built export tree of the drill revision),
# so a preflight at 1c would refuse every cut.
python3 scripts/export_public.py --dry-run --tag-time-checks

echo "== 2c. refuse if any release note the export ships still carries the placeholder stamp =="
python3 scripts/release_notes_stamped.py

echo "== 2d. drive the note's own files table: every file it names must go red =="
# Row E95. The generator above MEASURES this table, so this line is the
# independent read-back: it parses the note that was just written and breaks
# each file it names, requiring the suite beside it to fail. Slow (one suite
# run per file row) and deliberately part of the cut rather than the fast
# battery. Not tolerated with an "expected pre-tag" note like the invariant
# below: a table naming a check that cannot fail is a defect at any point in
# the release, so this one stops the cut.
python3 scripts/release_note_perturb.py --version "$VERSION"

echo "== 2t. commit the note and the manifest that describes it (the second, self-naming commit) =="
NOTE_MSG="$VERSION: the export manifest that describes the tree and the note it ships"
echo "git add -A && git commit -q -m \"$NOTE_MSG\""
git add -A
git commit -q -m "$NOTE_MSG"
git log -1 --oneline

echo "== 3. validate the manifests =="
claude plugin validate bundle
claude plugin validate .

echo "== 4. release invariant and export dry run (must read CLEAR) =="
# No `|| echo NOTE` swallow here: `set -e` (line 15) is what must stop this
# script on a real FAIL, and a command joined with `||` always exits 0 on
# its own, which silently defeated `set -e` for exit 1 (a genuine identity
# contradiction) exactly like it did for the honest pre-tag NO-DATA text on
# stdout at exit 0 (release_invariant.py's own docstring: the tag link
# reads NO-DATA, never a FAIL, between a cut and its tag). CDX-4,
# scripts/test_cut_invariant_step.py drives both cases.
python3 scripts/release_invariant.py
# --tag-time-checks here too (row DEL-15): by now the note and manifest for
# $VERSION exist (steps 2b/2t), so this CLEAR means the same thing the
# real --push --tag will check, not just the ordinary export gates step 1c
# already covers.
python3 scripts/export_public.py --dry-run --tag-time-checks

echo
echo "== STOP. Review the output above. The dry run must read CLEAR. =="
echo "Both commits above are already made (the bump plus regeneration, then the"
echo "note plus manifest); nothing here is left to commit. Push the branch through"
echo "the four gates, then run the one irreversible line below by hand (it pushes"
echo "and tags the PUBLIC repository, which cannot be undone cleanly):"
echo
echo "  python3 scripts/export_public.py --push --remote $PUBLIC_REMOTE --tag $TAG"
echo
echo "Then reinstall from the tag and re-run doctor:"
echo "  claude plugin update brother@brother"
echo "  (open a fresh session and run /brother doctor)"
echo
echo "Then submit the unified brother at platform.claude.com/plugins/submit,"
echo "from the one public repo at the $TAG tag. See docs/plan/STORE-READINESS-2026-09-03.html."
