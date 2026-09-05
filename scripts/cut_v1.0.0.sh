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

echo "== 1. bump both manifests to $VERSION and point refs at the tag =="
python3 - <<'PY'
import json, os, re
VERSION = os.environ['VERSION']
TAG = 'v' + VERSION
# bundle plugin.json
p = 'bundle/.claude-plugin/plugin.json'
d = json.load(open(p))
d['version'] = VERSION
json.dump(d, open(p, 'w'), indent=2); open(p, 'a').write('\n')
print('bundle/.claude-plugin/plugin.json -> %s' % VERSION)
# bundle/.codex-plugin/plugin.json: the Codex half of the same package, which
# ships the same bytes under a second manifest, so a cut that moved only the
# Claude manifest would publish a package declaring two different versions of
# itself. Bumped here rather than by hand for the same reason as the one above.
p = 'bundle/.codex-plugin/plugin.json'
d = json.load(open(p))
d['version'] = VERSION
json.dump(d, open(p, 'w'), indent=2); open(p, 'a').write('\n')
print('bundle/.codex-plugin/plugin.json -> %s' % VERSION)
# marketplace.json: the brother entry version, and every ref pinned to the tag
p = '.claude-plugin/marketplace.json'
s = open(p).read()
m = json.loads(s)
m['metadata']['version'] = VERSION
for plug in m['plugins']:
    if plug['name'] == 'brother':
        plug['version'] = VERSION
    src = plug.get('source')
    if isinstance(src, dict) and src.get('ref'):
        src['ref'] = TAG
json.dump(m, open(p, 'w'), indent=2); open(p, 'a').write('\n')
print('.claude-plugin/marketplace.json -> metadata %s, brother %s, refs %s' % (VERSION, VERSION, TAG))
# docs/VERSIONING.md: keep its stated umbrella version in agreement
p = 'docs/VERSIONING.md'
with open(p) as fh:
    s = fh.read()
s2 = re.sub(r'Current version: [0-9.]+\.', 'Current version: %s.' % VERSION, s)
# A re-run of the same cut (after merging main, say, so the note describes the
# merged tree) leaves this file byte-identical, which is agreement, not a
# missing line. Only an absent line refuses.
if s2 == s and ('Current version: %s.' % VERSION) not in s:
    raise SystemExit('docs/VERSIONING.md: "Current version: X." line not found, refusing to bump silently')
with open(p, 'w') as fh:
    fh.write(s2)
print('docs/VERSIONING.md -> Current version: %s.' % VERSION)
PY

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

echo "== 2b. regenerate the release note from the tree (manifests now say $VERSION) =="
python3 scripts/release_note_from_tree.py --write --version "$VERSION"

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

echo "== 3. validate the manifests =="
claude plugin validate bundle
claude plugin validate .

echo "== 4. release invariant and export dry run (must read CLEAR) =="
python3 scripts/release_invariant.py || echo "NOTE: release_invariant may FAIL until the tag exists; that is expected pre-tag"
python3 scripts/export_public.py --dry-run

echo
echo "== STOP. Review the output above. The dry run must read CLEAR. =="
echo "When it does, commit the bump on a branch, push through the four gates,"
echo "then run the one irreversible line below by hand (it pushes and tags the"
echo "PUBLIC repository, which cannot be undone cleanly):"
echo
echo "  git add -A && git commit -m '$VERSION: the receipt and the recall'"
echo "  python3 scripts/export_public.py --push --remote $PUBLIC_REMOTE --tag $TAG"
echo
echo "Then reinstall from the tag and re-run doctor:"
echo "  claude plugin update brother@brother"
echo "  (open a fresh session and run /brother doctor)"
echo
echo "Then submit the unified brother at platform.claude.com/plugins/submit,"
echo "from the one public repo at the $TAG tag. See docs/plan/STORE-READINESS-2026-09-03.html."
