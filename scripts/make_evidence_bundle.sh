#!/bin/sh
# make_evidence_bundle.sh: assemble ONE commit-pinned, buyer-rerunnable
# evidence bundle from what is already merged, so a buyer who owes Brother
# nothing unpacks it on a second machine and reruns every green number for
# themselves. This is Root 1 of the red-team directive (evidence that is
# ASSERTED must become evidence the buyer REPRODUCES).
#
# WHAT GOES IN, and why only this. Three of the four critical readiness-gate
# proofs are self-contained inside scripts/ (the benchmark bundle builder, the
# tenancy attack suite, and the fail-closed attack suite: the last two carry
# their own frozen vault seam under scripts/fixtures/bmu_vault_seam/). Those
# rerun anywhere with a Python 3 and no network. The restore drill's RESULT is
# shipped as recorded evidence (docs/plan/RESTORE-DRILL-RESULT.json) plus its
# own script for a buyer who has a live vault; it is not re-derivable from the
# bundle alone and verify.sh says so rather than pretending. The Japanese
# threshold needs the BrotherModeUp vault to run and is documented, not faked.
#
# HOW IT PROVES ITSELF. verify.sh inside the bundle reruns the three
# self-contained suites and fails loudly on any nonzero exit; MANIFEST.json
# records the source commit and the tree hash of scripts/ so a buyer can
# confirm the bundle is the released code, not a hand-edited copy.
#
# Usage: sh scripts/make_evidence_bundle.sh [out_dir]   (default: dist/)
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

OUT_DIR=${1:-dist}
SHA=$(git rev-parse HEAD)
SHA8=$(printf '%s' "$SHA" | cut -c1-8)
TREE_SCRIPTS=$(git rev-parse "HEAD:scripts")

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

# Only the evidence-relevant subtree, straight from the commit (no working-tree
# drift, no untracked files leak in). benchmarks/ carries the memory-ab and
# graph-value result artifacts the benchmark bundle builder reads relative to
# the repo, so the reproducible-benchmark proof reruns without the full repo.
git archive HEAD scripts benchmarks docs/plan/RESTORE-DRILL-RESULT.json | tar -x -C "$STAGE"

cat > "$STAGE/MANIFEST.json" <<EOF
{
  "bundle": "brother-enterprise-evidence",
  "source_commit": "$SHA",
  "scripts_tree_hash": "$TREE_SCRIPTS",
  "self_contained_proofs": [
    "scripts/test_make_benchmark_bundle.py",
    "scripts/test_tenancy_isolation.py",
    "scripts/test_policy_fail_closed.py"
  ],
  "recorded_evidence": [
    "docs/plan/RESTORE-DRILL-RESULT.json"
  ],
  "requires_live_vault": [
    "scripts/test_japanese_threshold.py"
  ]
}
EOF

cat > "$STAGE/verify.sh" <<'EOF'
#!/bin/sh
# verify.sh: rerun every self-contained proof in this bundle and confirm the
# recorded evidence. Exits 0 only if all three suites pass. No network, no
# other repository needed. Requires: python3.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"

fail=0
run() {
  name=$1; shift
  printf '\n=== %s ===\n' "$name"
  if "$@"; then
    printf 'PASS: %s\n' "$name"
  else
    printf 'FAIL: %s (exit %s)\n' "$name" "$?"
    fail=1
  fi
}

command -v python3 >/dev/null 2>&1 || { echo "NO-DATA: python3 not found"; exit 2; }

run "reproducible benchmark bundle" python3 scripts/test_make_benchmark_bundle.py
run "tenant isolation (black-box attack suite)" python3 scripts/test_tenancy_isolation.py
run "fail-closed policy (black-box attack suite)" python3 scripts/test_policy_fail_closed.py

printf '\n=== restore drill (recorded evidence, not re-derived here) ===\n'
if python3 - <<'PY'
import json, sys
d = json.load(open("docs/plan/RESTORE-DRILL-RESULT.json"))
print("restore drill passed:", d.get("passed"))
sys.exit(0 if d.get("passed") is True else 1)
PY
then :; else echo "FAIL: recorded restore drill did not pass"; fail=1; fi

printf '\n'
if [ "$fail" -eq 0 ]; then
  echo "ALL SELF-CONTAINED PROOFS REPRODUCED GREEN."
  echo "Note: the Japanese-threshold proof needs the BrotherModeUp vault and is not run here (see MANIFEST.json requires_live_vault)."
  exit 0
fi
echo "ONE OR MORE PROOFS FAILED. See lines above."
exit 1
EOF
chmod +x "$STAGE/verify.sh"

mkdir -p "$OUT_DIR"
TARBALL="$OUT_DIR/evidence-bundle-$SHA8.tgz"
tar -czf "$TARBALL" -C "$STAGE" .
printf 'wrote %s\n' "$TARBALL"
printf 'source_commit %s\n' "$SHA"
printf 'scripts_tree_hash %s\n' "$TREE_SCRIPTS"
