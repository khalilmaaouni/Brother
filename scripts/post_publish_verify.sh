#!/bin/sh
# CD1.b2 (docs/plan/specs/CD1.md): read only, did <version> reach the public repository properly?
# Each line prints PASS, FAIL or NO-DATA; the last line is fails=<n> nodata=<n>; exit 0 only when both are 0.
# Changes nothing and pushes nothing: git ls-remote and gh reads only. Run after cut.py exits 0.
# Usage: sh scripts/post_publish_verify.sh <version> <release notes file>
set -u
VER=${1:-}
BODY=${2:-}
R=khalilmaaouni/Brother
fails=0; nodata=0
ok()  { echo "PASS    $*"; }
bad() { echo "FAIL    $*"; fails=$((fails+1)); }
nd()  { echo "NO-DATA $*"; nodata=$((nodata+1)); }
end() { echo "fails=$fails nodata=$nodata"; [ $fails -eq 0 ] && [ $nodata -eq 0 ] && exit 0; [ $fails -gt 0 ] && exit 1; exit 2; }

if [ -z "$VER" ] || [ -z "$BODY" ] || [ ! -r "$BODY" ]; then
  nd "usage: post_publish_verify.sh <version> <readable release notes file>"; end
fi

# 1. the tag exists and peels to a commit
T=$(git ls-remote "https://github.com/$R" "refs/tags/v$VER^{}" "refs/tags/v$VER" 2>/dev/null) || { nd "git ls-remote of $R failed"; end; }
TAGC=$(echo "$T" | awk '/\^\{\}$/{print $1}'); [ -n "$TAGC" ] || TAGC=$(echo "$T" | awk 'NR==1{print $1}')
[ -n "$TAGC" ] && ok "tag v$VER -> $TAGC" || { bad "tag v$VER absent on $R"; end; }

# 2. public main carries the tagged commit
MAINC=$(git ls-remote "https://github.com/$R" refs/heads/main | awk '{print $1}')
[ "$MAINC" = "$TAGC" ] && ok "main == tag ($MAINC)" || bad "main $MAINC != tag $TAGC"

# 3. this cut left no open PR or release/$VER branch (older public items are notes, not this cut's)
OPEN=$(gh pr list -R "$R" --state open --json number,headRefName -q '.[] | "\(.number) \(.headRefName)"' 2>/dev/null) || { nd "gh pr list failed"; OPEN="?"; }
if [ "$OPEN" != "?" ]; then
  echo "$OPEN" | grep -q " release/$VER$" && bad "open release PR: $(echo "$OPEN" | grep " release/$VER$")" || ok "no open release/$VER pull request"
  OTHERPR=$(echo "$OPEN" | grep -v " release/$VER$" | grep . | tr '\n' ';'); [ -n "$OTHERPR" ] && echo "NOTE    older open PR(s), not this cut's: $OTHERPR"
fi
RB=$(git ls-remote --heads "https://github.com/$R" 'release/*' 2>/dev/null) || { nd "git ls-remote of release branches failed"; RB="?"; }
if [ "$RB" != "?" ]; then
  RB=$(echo "$RB" | awk '{print $2}')
  echo "$RB" | grep -qx "refs/heads/release/$VER" && bad "leftover branch release/$VER" || ok "no release/$VER branch left"
  OLDRB=$(echo "$RB" | grep -vx "refs/heads/release/$VER" | grep . | tr '\n' ' '); [ -n "$OLDRB" ] && echo "NOTE    older branch(es), not this cut's: $OLDRB"
fi

# 4. the GitHub Release exists, is published, and carries the body file
J=$(gh release view "v$VER" -R "$R" --json tagName,isDraft,isPrerelease,publishedAt,body 2>/dev/null)
if [ -z "$J" ]; then bad "no GitHub Release v$VER"; else
  echo "$J" | python3 -c 'import json,sys; d=json.load(sys.stdin); b=open(sys.argv[1]).read(); ok=not d["isDraft"] and not d["isPrerelease"] and d["body"].strip()==b.strip(); print(("PASS    " if ok else "FAIL    ")+"release published %s, draft %s, prerelease %s, body equals file: %s" % (d["publishedAt"], d["isDraft"], d["isPrerelease"], d["body"].strip()==b.strip())); sys.exit(0 if ok else 1)' "$BODY" || fails=$((fails+1))
fi

# 5. the release PR's required check passed (required-fast runs on pull_request to main)
PR=$(gh pr list -R "$R" --state merged --head "release/$VER" --json number -q '.[0].number' 2>/dev/null)
if [ -z "$PR" ]; then nd "no merged release/$VER pull request found"; else
  C=$(gh pr checks "$PR" -R "$R" --json name,state -q '.[] | "\(.name)=\(.state)"' 2>/dev/null | tr '\n' ' ')
  echo "  PR $PR checks: $C"
  echo "$C" | grep -q 'required-fast=SUCCESS' && ok "PR $PR required-fast SUCCESS" || bad "PR $PR required-fast not SUCCESS ($C)"
fi

# 6. the published marketplace and plugin say $VER
for f in .claude-plugin/marketplace.json bundle/.claude-plugin/plugin.json; do
  V=$(gh api "repos/$R/contents/$f?ref=v$VER" -q .content 2>/dev/null | base64 -d 2>/dev/null | grep -o '"version": *"[^"]*"' | sort -u)
  OTHER=$(echo "$V" | grep -vc "\"$VER\"")
  [ -z "$V" ] && nd "$f unreadable at v$VER" || { [ "$OTHER" -gt 0 ] && bad "$f versions: $(echo $V)" || ok "$f versions: $(echo $V)"; }
done

end
