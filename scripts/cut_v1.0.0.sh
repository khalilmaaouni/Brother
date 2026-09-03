#!/bin/sh
# The v1.0.0 cut, as one script the founder runs. Written 2026-09-03 because the
# app safety classifier refuses the version bump to a session (a control-plane
# path), so the bump is the founder's hand; everything after it is mechanical.
#
# Run from the hub root:  sh scripts/cut_v1.0.0.sh
#
# It bumps both manifests together (the validator fails on a mismatch), points
# every ref at the tag, runs the release invariant and the export dry run, and
# then STOPS before the one irreversible step. It never pushes on its own: the
# push and tag are the last block, commented out, for you to run once the dry
# run reads CLEAR.
set -e
cd "$(dirname "$0")/.."

# The public tag this cut publishes as, and the public repository it lands
# on. Named here, once, so nothing below (or scripts/release_note_from_tree.py,
# which greps this file for these two lines) retypes them.
TAG=v1.0.0
PUBLIC_REMOTE=https://github.com/khalilmaaouni/Brother

echo "== 1. bump both manifests to 1.0.0 and point refs at the tag =="
python3 - <<'PY'
import json, re
# bundle plugin.json
p = 'bundle/.claude-plugin/plugin.json'
d = json.load(open(p))
d['version'] = '1.0.0'
json.dump(d, open(p, 'w'), indent=2); open(p, 'a').write('\n')
print('bundle/.claude-plugin/plugin.json -> 1.0.0')
# marketplace.json: the brother entry version, and every ref pinned to the tag
p = '.claude-plugin/marketplace.json'
s = open(p).read()
m = json.loads(s)
m['metadata']['version'] = '1.0.0'
for plug in m['plugins']:
    if plug['name'] == 'brother':
        plug['version'] = '1.0.0'
    src = plug.get('source')
    if isinstance(src, dict) and src.get('ref'):
        src['ref'] = 'v1.0.0'
json.dump(m, open(p, 'w'), indent=2); open(p, 'a').write('\n')
print('.claude-plugin/marketplace.json -> metadata 1.0.0, brother 1.0.0, refs v1.0.0')
# docs/VERSIONING.md: keep its stated umbrella version in agreement
p = 'docs/VERSIONING.md'
with open(p) as fh:
    s = fh.read()
s2 = re.sub(r'Current version: [0-9.]+\.', 'Current version: 1.0.0.', s)
if s2 == s:
    raise SystemExit('docs/VERSIONING.md: "Current version: X." line not found, refusing to bump silently')
with open(p, 'w') as fh:
    fh.write(s2)
print('docs/VERSIONING.md -> Current version: 1.0.0.')
PY

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

echo "== 2b. regenerate the release note from the tree (manifests now say 1.0.0) =="
python3 scripts/release_note_from_tree.py --write

echo "== 2c. refuse if any release note the export ships still carries the placeholder stamp =="
python3 scripts/release_notes_stamped.py

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
echo "  git add -A && git commit -m 'v1.0.0: the receipt and the recall'"
echo "  python3 scripts/export_public.py --push --remote $PUBLIC_REMOTE --tag $TAG"
echo
echo "Then reinstall from the tag and re-run doctor:"
echo "  claude plugin update brother@brother"
echo "  (open a fresh session and run /brother doctor)"
echo
echo "Then submit the unified brother at platform.claude.com/plugins/submit,"
echo "from the one public repo at the v1.0.0 tag. See docs/plan/STORE-READINESS-2026-09-03.html."
