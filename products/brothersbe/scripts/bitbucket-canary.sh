#!/bin/bash
# Dry-run harness for the Bitbucket side of the CI/SCM handshake. It never
# makes a network call itself: the credential-present branch below only
# ECHOES the manual steps and API calls a real run would make, it does not
# make them. Zero-network shell scripts are this repository's own rule
# (SECURITY.md): the one allow-listed network client is
# src/brothersbe/bbstatus.py, a Python module, never a shell script.
#
# NO-CREDENTIAL PATH (the one this script actually exercises tonight): with
# no bitbucket-api-token in the keychain, print exactly one NO-DATA line
# naming the blocker and exit 0. NO-DATA is never a pass and never a block,
# so "nothing to do here yet" is a clean exit, not a failure.
set -u

CRED="$(security find-generic-password -s bitbucket-api-token -w 2>/dev/null)"
if [ -z "$CRED" ]; then
  echo "NO-DATA: bitbucket-api-token absent from keychain; the canary needs the founder grant"
  exit 0
fi

# --- everything below here is UNTESTED LIVE as of this run ------------------
# A credential was found. This branch reads the workspace/repo slug from
# STATE.md (never hardcoded: a slug typed here would silently drift from
# whatever STATE.md says the moment either one changes) and prints, rather
# than performs, the steps a real canary run would take.
CRED=""  # scrubbed immediately: this script never holds the secret past the check above

ROOT="$(cd "$(dirname "$0")/.." && pwd)" || exit 2
STATE_FILE="$ROOT/STATE.md"
if [ ! -f "$STATE_FILE" ]; then
  echo "NO-DATA: $STATE_FILE does not exist, so no Bitbucket slug could be read; nothing else ran"
  exit 0
fi

SLUG="$(grep -oE '[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+' "$STATE_FILE" | grep -m1 -E '^kmaaouni/' || true)"
if [ -z "$SLUG" ]; then
  echo "NO-DATA: no bitbucket.org workspace/repo slug found in $STATE_FILE; nothing else ran"
  exit 0
fi

echo "canary: credential present, slug read from STATE.md: $SLUG"
echo "canary: this run would take these steps (echo only, no network attempted):"
echo "  1. verify the 'origin' remote resolves to bitbucket.org/$SLUG"
echo "     (git remote get-url origin, compared against the slug above)"
echo "  2. GET  https://api.bitbucket.org/2.0/repositories/$SLUG"
echo "     (confirm the repository is reachable with this credential)"
echo "  3. POST https://api.bitbucket.org/2.0/repositories/$SLUG/commit/<sha>/statuses/build"
echo "     (the same call src/brothersbe/bbstatus.py makes; run that module directly"
echo "      for a real post, this script never calls it)"
echo "canary: dry run only, nothing above was actually sent"
exit 0
