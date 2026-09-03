#!/usr/bin/env bash
# Finish the Bitbucket estate check in one command, once a credential exists.
#
# WHY THIS FILE: enabling Pipelines on a repository and pressing Run are web
# UI actions, and this machine's assistant is granted browsers at READ tier
# only, so those two clicks blocked the whole check. The REST API can do both,
# so a single app password removes the browser from this and every future run.
# Nothing here ever prints the credential.
#
# ONE-TIME SETUP, by the founder:
#   1. Create an app password at
#      https://bitbucket.org/account/settings/app-passwords/
#      Scopes needed: Repositories Read AND Write, Pipelines Read AND Write.
#   2. Store it (the prompt hides what you type; it is never echoed):
#      security add-generic-password -a bitbucket-api -s bitbucket-api-token -w
#   3. Export the account name once, since the app password pairs with it:
#      export BITBUCKET_USER=<your bitbucket username>
#
# USAGE:
#   bash tools/sbe_bb_estate_check.sh <workspace> <repo-slug> [pipeline-name]
#
# The pipeline name defaults to estate-check, the custom (manual only)
# pipeline. This script never creates a pipeline that fires by itself: it
# starts ONE run, on purpose, and reports what it cost.
set -euo pipefail

WORKSPACE="${1:?usage: sbe_bb_estate_check.sh <workspace> <repo-slug> [pipeline]}"
REPO="${2:?usage: sbe_bb_estate_check.sh <workspace> <repo-slug> [pipeline]}"
PIPELINE="${3:-estate-check}"
BRANCH="${BITBUCKET_BRANCH:-main}"
API="https://api.bitbucket.org/2.0/repositories/${WORKSPACE}/${REPO}"

USER_NAME="${BITBUCKET_USER:-}"
if [ -z "$USER_NAME" ]; then
  echo "BITBUCKET_USER is unset. Export your Bitbucket username; the app" >&2
  echo "password authenticates as that account and cannot be used alone." >&2
  exit 2
fi

# Read the secret into a variable and never echo it. If it is absent, say so
# in the product's own vocabulary: this is NO-DATA, not a pass and not a fail.
if ! TOKEN="$(security find-generic-password -a bitbucket-api -s bitbucket-api-token -w 2>/dev/null)"; then
  echo "NO-DATA: no keychain item 'bitbucket-api-token' for account" >&2
  echo "'bitbucket-api'. Nothing was run, and nothing is claimed about the" >&2
  echo "estate. Create it with the command in this file's header." >&2
  exit 3
fi

auth() { curl -sS -u "${USER_NAME}:${TOKEN}" "$@"; }

echo "== 1. Enabling Pipelines on ${WORKSPACE}/${REPO} (idempotent)"
auth -X PUT "${API}/pipelines_config" \
     -H 'Content-Type: application/json' \
     -d '{"enabled": true}' | head -c 400
echo

echo "== 2. Starting ONE run of the custom pipeline '${PIPELINE}' on ${BRANCH}"
START_BODY=$(printf '{"target":{"type":"pipeline_ref_target","ref_type":"branch","type":"pipeline_ref_target","ref_name":"%s","selector":{"type":"custom","pattern":"%s"}}}' "$BRANCH" "$PIPELINE")
RUN=$(auth -X POST "${API}/pipelines/" -H 'Content-Type: application/json' -d "$START_BODY")
NUMBER=$(printf '%s' "$RUN" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("build_number",""))' 2>/dev/null || true)
if [ -z "$NUMBER" ]; then
  echo "FAIL: the run did not start. The API said:" >&2
  printf '%s\n' "$RUN" | head -c 800 >&2
  exit 1
fi
echo "started run #${NUMBER}"

echo "== 3. Waiting for it to finish"
for _ in $(seq 1 60); do
  sleep 10
  STATE=$(auth "${API}/pipelines/${NUMBER}" | python3 -c '
import json, sys
d = json.load(sys.stdin)
state = d.get("state") or {}
result = (state.get("result") or {}).get("name") or ""
print("%s %s %s" % (state.get("name", "?"), result, d.get("duration_in_seconds", "")))')
  echo "  ${STATE}"
  case "$STATE" in
    COMPLETED*) break ;;
  esac
done

echo "== 4. Verdict and cost"
auth "${API}/pipelines/${NUMBER}" | python3 -c '
import json, sys
d = json.load(sys.stdin)
state = d.get("state") or {}
result = (state.get("result") or {}).get("name") or "NO-DATA"
seconds = d.get("duration_in_seconds")
print("pipeline    : %s #%s" % (d.get("target", {}).get("selector", {}).get("pattern", "?"), d.get("build_number")))
print("state       : %s" % state.get("name", "NO-DATA"))
print("result      : %s" % result)
print("duration    : %s second(s)" % (seconds if seconds is not None else "NO-DATA"))
if isinstance(seconds, int):
    # Bitbucket bills whole minutes, rounding up, against a 50 a month floor
    # on the free plan. Printed so a run never costs something nobody counted.
    print("minutes bill: ~%d of the 50 free minutes this month" % max(1, (seconds + 59) // 60))
print("SUCCESSFUL is the only result that means the estate ran this check.")'
