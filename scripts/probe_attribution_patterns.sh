#!/bin/sh
# Probe for the attribution and watermark patterns in scripts/cleanse.sh.
#
# WHY THIS EXISTS. On 2026-08-24 the push gate refused twice on content that
# breaks no rule: an untracked handover pack, and the PORT-01 harness inventory
# whose entire job is to name the host harness. The cause is pattern A4 in
# cleanse.sh, a bare two word substring, which fires on ANY mention of the
# harness rather than on a claim of authorship.
#
# This script does NOT change the gate. It measures whether a proposed pattern
# set still catches every real attribution form while allowing technical
# reference. Run it before and after any change to those patterns.
#
#   sh scripts/probe_attribution_patterns.sh          # probe the PROPOSED set
#   sh scripts/probe_attribution_patterns.sh current  # probe the CURRENT set
#
# Exit 0 when every case lands as expected, 1 otherwise.

set -u
MODE="${1:-proposed}"

# Needles assembled from fragments so this file cannot match itself, which is
# the same trick cleanse.sh uses and for the same reason.
A1="Co-""Authored-""By: (Claude|Opus|Sonnet|Haiku|Fable)"
A2="noreply@""anthropic"
A3="Generated with \[Claude"" Code\]"
A4_CURRENT="Claude"" Code"
A4_PROPOSED='(generated|built|created|made|written|authored|powered) +(with|by) +\[?Claude'" Code"'\]?'

if [ "$MODE" = "current" ]; then A4="$A4_CURRENT"; else A4="$A4_PROPOSED"; fi

# CATCH cases are real authorship claims the founder's rule forbids.
# ALLOW cases are technical references, all four taken verbatim from files that
# actually exist in this estate and that the current pattern refuses.
cases_catch() {
  # ASSEMBLED FROM FRAGMENTS, never written whole, for exactly the reason
  # cleanse.sh assembles its own needles: a file carrying literal attribution
  # strings trips the gate it exists to test, and worse, puts them into the
  # repository's HISTORY where deleting the file does not remove them.
  # This script learned that by refusing its own commit on 2026-08-25.
  H="Claude"" Code"
  for verb in "Generated with [$H]" "Built with $H" "Made by $H" "powered by $H"; do
    printf '%s\n' "$verb"
  done
  printf 'Co-%sBy: Claude <noreply@%s.com>\n' "Authored-" "anthropic"
}
cases_allow() {
  cat <<'EOT'
An installed copy can live under the Claude Code harness's own skills directory.
to assume one specific host harness (Anthropic's Claude Code): its hook
assumption about the Claude Code harness. A further 34 files, mostly
This data was clearly produced by a Claude Code session identifier.
EOT
}

matched() {
  for p in "$A1" "$A2" "$A3" "$A4"; do
    printf '%s' "$1" | grep -qiE -- "$p" && return 0
  done
  return 1
}

pass=0; fail=0
cases_catch | while :; do break; done   # no-op, keeps shells consistent
for want in CATCH ALLOW; do
  if [ "$want" = CATCH ]; then lines="$(cases_catch)"; else lines="$(cases_allow)"; fi
  printf '%s\n' "$lines" | while IFS= read -r text; do
    [ -z "$text" ] && continue
    if matched "$text"; then got=CATCH; else got=ALLOW; fi
    if [ "$got" = "$want" ]; then v=ok; else v=MISMATCH; fi
    printf '%-9s want=%-5s got=%-5s  %s\n' "$v" "$want" "$got" "$(printf '%s' "$text" | cut -c1-52)"
  done
done > /tmp/_attr_probe_out 2>&1
cat /tmp/_attr_probe_out
# grep -c PRINTS 0 and EXITS 1 when it finds nothing, so a `|| echo 0` here
# appends a second zero and the comparison below dies on "0\n0". Capture the
# count on its own and default it, rather than chaining on the exit code.
fail="$(grep -c MISMATCH /tmp/_attr_probe_out 2>/dev/null)"
[ -n "$fail" ] || fail=0
total="$(wc -l < /tmp/_attr_probe_out | tr -d ' ')"
echo "-----"
echo "mode=$MODE cases=$total mismatches=$fail"
[ "$fail" -eq 0 ] || exit 1
