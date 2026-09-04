#!/usr/bin/env bash
# The push gate for Brother. It refuses a tree that carries a client term, an
# attribution watermark, a name other than the sole author, or a typographic
# dash.
#
# Two rules this script exists to obey, both learned the hard way:
#
# 1. A CONTROL THAT OPENED NOTHING REPORTS NO-DATA, NEVER PASS. An earlier
#    version scanned `git ls-files`, which is empty before the first commit, so
#    it examined zero files and printed PASS. That is the failure this estate
#    has recorded twice: a control that cannot reach a verdict reads exactly
#    like a control that passed.
# 2. A SCAN WHOSE PATTERN APPEARS IN THE TEXT IT SCANS REPORTS A HIT IT
#    CREATED. The attribution needles below are assembled from fragments so
#    this file cannot match itself.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || { echo "NO-DATA: cannot enter $ROOT"; exit 2; }
SELF="scripts/cleanse.sh"
# DETECTOR FILES exemption REMOVED 2026-09-03. It named five paths whose own
# tests once had to embed the text they looked for; every tool now reads the
# private-term list from outside the repository (bm_private_scan.py and its
# callers), so a strict scan of the tracked tree finds no tool left that
# still needs it, and the one that most needed it (test_bm_queue_numbers.py,
# exempted while it carried two client terms in lowercase for five weeks,
# exported to the public repository eight times) is the proof that leaving
# the exemption in place was itself the risk. SELF stays exempt below, in
# both the working-tree and the history scans, only because it holds the
# matching logic, never a term. The history scans below also now count only
# lines a commit ADDS ("+" lines, never the "+++" file header), not lines it
# removes, so a cleanup commit that deletes a term from a file reads clean
# instead of refusing this gate forever; the D9 imported-roots exclusion
# further down is unchanged.
HIST_SCOPE="."
for d in $SELF; do HIST_SCOPE="$HIST_SCOPE :(exclude)$d"; done
# ATTRIBUTION_HISTORY_EXEMPT, added 2026-09-03. One commit already in this
# hub's own history, 4a79a7e8, added a line to scripts/pre_push_gate.py that
# spelled the attribution pattern as one literal string; the same day's
# 3f1cfb2a reassembled it from fragments (the trick this file's own A1 to A4
# needles use below), so the working tree has been clean since and this is a
# history-only fact that cannot be fixed by editing anything, because that
# commit cannot be rewritten (never-lose-work). This names exactly that one
# path and touches ONLY the attribution HISTORY scan below: never the term
# scans, never any working-tree scan, never the dash scan.
ATTRIBUTION_HISTORY_EXEMPT="scripts/pre_push_gate.py"
ATTR_HIST_SCOPE="$HIST_SCOPE :(exclude)$ATTRIBUTION_HISTORY_EXEMPT"
# D9, founder-approved 2026-08-31 in the question UI (record with his choice:
# docs/decisions/2026-08-31-scanner-scope-after-subtree-imports.html).
# Commits reachable from the imported product tips in
# IMPORTED-HISTORY-ROOTS.txt were authored in the products' original
# repositories, are legal in this PRIVATE repository, and can never be
# rewritten. The history scans exclude exactly those commits and keep full
# strength on every hub-authored commit. The exporter's payload check has no
# such exclusion, so the public boundary is unchanged. A tip that does not
# resolve in this clone is skipped rather than erroring, which only ever makes
# the scan STRICTER (more commits scanned, never fewer).
ROOTS_FILE="docs/plan/IMPORTED-HISTORY-ROOTS.txt"
HIST_NOT=""
if [ -f "$ROOTS_FILE" ]; then
  while read -r sha _; do
    case "$sha" in ''|\#*) continue;; esac
    if git cat-file -e "$sha" 2>/dev/null; then HIST_NOT="$HIST_NOT ^$sha"; fi
  done < "$ROOTS_FILE"
fi
fail=0

# The file list: tracked files when there are any, otherwise the working tree.
# Never an empty list silently.
# The UNION of tracked and working-tree files. Tracked alone let a brand new
# untracked file escape every scan, which is exactly when it most needs one:
# GANTT.html was written and the gate reported PASS without ever opening it.
# NARROWED 2026-08-29 from a `find .` union to git's own answer. `find` walked
# IGNORED paths too, so a compiled __pycache__/*.pyc, which .gitignore excludes
# and which therefore can never be pushed, refused this gate. That is a FALSE
# REFUSAL on a build artifact, and a false refusal is the worst failure mode a
# gate has: it teaches people to bypass it, after which it protects nothing.
#
# THIS IS A SCOPE CORRECTION, NOT A WEAKENING, and the distinction was checked
# rather than asserted: `--cached --others --exclude-standard` is exactly the
# population that can reach a remote, tracked files PLUS untracked ones git
# would let you add. The protection the union was built for is kept, because a
# brand new untracked file is still listed (proven by seeding a dash into one
# and watching this gate refuse). The only files dropped are ones git is
# configured to ignore, and an ignored file cannot be committed without an
# explicit -f, after which it is tracked and `--cached` sees it again.
files="$(git ls-files --cached --others --exclude-standard 2>/dev/null | sort -u)"
# For a directory that is not a git repository at all. Not a workaround for the
# rule above: the previous code's `git ls-files` half was equally empty there,
# and without this the NO-DATA branch fires, which is honest but useless.
if [ -z "$files" ]; then
  files="$(find . -type f -not -path './.git/*' | sed 's|^\./||' | sort -u)"
fi
# The count EXCLUDES this script, because this script is excluded from every
# scan below. Counting it let a tree holding only the scanner report "1 file
# opened, PASS" when nothing had actually been examined.
count="$(printf '%s\n' "$files" | grep -v "^$SELF$" | grep -c . || true)"
commits="$(git rev-list --count --all $HIST_NOT 2>/dev/null || echo 0)"
echo "scope: $count file(s), $commits commit(s) of history"
if [ "$count" -eq 0 ]; then
  echo "NO-DATA: this gate opened no file it is allowed to scan, so it proved nothing. Not a pass."
  exit 2
fi
# D9 extension, addendum recorded 2026-08-31 in
# docs/decisions/2026-08-31-scanner-scope-after-subtree-imports.html. The dash
# scan below and the term and attribution TREE scans all excluded products/
# blanket, so an old imported handover document under products/ could not
# trip them pre-cutover. Their HISTORY halves need no such change: HIST_NOT
# above already drops every commit reachable from the imported roots.
#
# M6 NARROWING, this cutover, the review D9 promised at its own "review at
# the M6 row" note. THE SHIPPABLE HALVES ARE NOW CLEANED DELIBERATELY, so a
# blanket products/ exclusion would let a dash or a term in the exact files
# this cutover ships publicly slip past unscanned, inside their own copy of
# this script, once the exporter copies them into its candidate export tree.
# Narrowed instead of removed: docs/plan/EXPORT-ALLOWLIST.txt is the single
# list of exactly which products/ paths are shippable, so a products/ file
# is scanned when EITHER it is not under products/ at all, OR its path sits
# under one of that file's own products/... entries. Everything under
# products/ NOT on the allowlist (the internal archive: docs/handover,
# docs/plan, session logs, dossiers, and everything else not yet curated)
# stays excluded, same as before, until it too is cleaned and added. A path
# also named in docs/plan/EXPORT-DENYLIST.txt is withheld the same as a
# not-yet-allowlisted one, even though a broader allowlist entry covers it:
# the exporter withholds it from the payload for the same reason, so it
# would be dishonest for this working-tree scan to treat it as scanned and
# safe while the export treats it as unshipped and unsafe.
ALLOWLIST_FILE="docs/plan/EXPORT-ALLOWLIST.txt"
DENYLIST_FILE="docs/plan/EXPORT-DENYLIST.txt"
PRODUCTS_ALLOWED_FILE="$(mktemp 2>/dev/null || echo /tmp/cleanse-products-allowed-$$)"
PRODUCTS_DENIED_FILE="$(mktemp 2>/dev/null || echo /tmp/cleanse-products-denied-$$)"
# ALLOWLIST_MODE, added 2026-09-03. An auditor drove this backwards inside a
# CANDIDATE EXPORT TREE, where docs/plan/EXPORT-ALLOWLIST.txt is itself never
# exported (it is not a shippable path on any real allowlist): the old code
# read zero prefixes, and the awk loop below, built to NARROW an already
# present list, had no branch for narrowing an ABSENT one, so it printed no
# products/ path at all. Measured: 1084 git-visible files, 757 under
# products/, SCAN_FILES 327, none of them under products/; a dash seeded
# under products/ passed while the identical dash at the tree root refused.
# A missing allowlist now means SCAN EVERY FILE GIT SEES, never scan none:
# absence is the export tree's ordinary state, and the narrowing this file
# was built for protects the HUB's own not-yet-curated internal archive
# under products/, not an already-curated export payload that has nothing
# left to narrow. The mode taken is printed so a run's own output says which
# rule applied, never left to be inferred from a file count.
if [ -f "$ALLOWLIST_FILE" ]; then
  ALLOWLIST_MODE="narrow"
  grep '^products/' "$ALLOWLIST_FILE" > "$PRODUCTS_ALLOWED_FILE" 2>/dev/null || true
else
  ALLOWLIST_MODE="all"
fi
if [ -f "$DENYLIST_FILE" ]; then
  grep '^products/' "$DENYLIST_FILE" > "$PRODUCTS_DENIED_FILE" 2>/dev/null || true
fi
if [ "$ALLOWLIST_MODE" = "all" ]; then
  echo "products/ scope: no $ALLOWLIST_FILE found, scanning every file git sees"
else
  echo "products/ scope: allowlist mode, $ALLOWLIST_FILE present"
fi
SCAN_FILES="$(printf '%s\n' "$files" | awk -v pf="$PRODUCTS_ALLOWED_FILE" -v df="$PRODUCTS_DENIED_FILE" -v mode="$ALLOWLIST_MODE" '
  BEGIN {
    while ((getline p < pf) > 0) if (p != "") prefixes[++n] = p
    while ((getline d < df) > 0) if (d != "") denied[++m] = d
  }
  {
    if ($0 !~ /^products\//) { print; next }
    for (j = 1; j <= m; j++) {
      d = denied[j]
      if ($0 == d || index($0, d "/") == 1) next
    }
    if (mode == "all") { print; next }
    for (i = 1; i <= n; i++) {
      p = prefixes[i]
      if ($0 == p || index($0, p "/") == 1) { print; next }
    }
  }
')"
rm -f "$PRODUCTS_ALLOWED_FILE" "$PRODUCTS_DENIED_FILE"
scan_count="$(printf '%s\n' "$SCAN_FILES" | grep -v "^$SELF$" | grep -c . || true)"

# 1. Client terms, read from outside the repository. Never printed.
# ONE LIST FILE, E37 2026-09-03: the default is the file the estate's law
# names, the same one bm_private_scan.py and the assurance product's history
# test read. The old ~/.claude/private-terms.txt default was a second copy
# that happened to hold the same terms with nothing keeping it so.
TERMS="${BROTHER_PRIVATE_TERMS:-$HOME/.brothersbe-private-names}"
# WHOLE WORD, E37 2026-09-03: bounded by anything that is not a letter or a
# digit. `grep -w` counts the underscore as a word character, so a spelling
# like path_<term>_file walked through this gate while the assurance
# product's history test (isalnum bounds) refused it. macOS grep has no -P
# for lookarounds, and an ERE with hand-escaped needles is where the next
# bug lives, so the two matchers below use perl, which this script already
# depends on for the dash scan further down: \Q...\E escapes the needle for
# free, the needle travels in the environment rather than argv (never a
# process-list leak), and the ASCII lookarounds are the same bound
# bm_private_scan.py compiles. Output shape is grep -n's (file:line:text)
# so the SELF filter after each call keeps working.
# NUL-DELIMITED, E78 2026-09-03: xargs without -0 splits its stdin on
# WHITESPACE as well as newlines, so a tracked path holding a space (SCAN_FILES
# itself is built newline-safe, one path per line, $0 in the awk above) was
# handed to xargs as two or more separate, non-existent paths, neither of
# which perl could open; the error went to /dev/null at the call site and the
# path went unscanned with no visible failure. -0 here, paired with every
# caller feeding it a NUL-delimited list (tr '\n' '\0'), keeps a path with a
# space as the one argument it is.
term_grep_files() {
  NEEDLE="$1" xargs -0 perl -ne 'print "$ARGV:$.:$_" if /(?<![A-Za-z0-9])\Q$ENV{NEEDLE}\E(?![A-Za-z0-9])/i; close ARGV if eof'
}
term_grep_stream() {
  NEEDLE="$1" perl -ne 'print if /(?<![A-Za-z0-9])\Q$ENV{NEEDLE}\E(?![A-Za-z0-9])/i'
}
if [ ! -f "$TERMS" ]; then
  echo "NO-DATA: no private terms file at $TERMS; refusing to certify."
  exit 2
fi
n=0
while IFS= read -r needle; do
  case "$needle" in ''|\#*) continue;; esac
  n=$((n+1))
  # A term of five characters or fewer matches as a WHOLE WORD and now also
  # CASE INSENSITIVELY (grep -niwF). A term over five characters matches case
  # insensitively as a substring (grep -niF), unchanged.
  #
  # CORRECTED 2026-08-26. This branch used to drop the -i, on the reasoning that
  # a short term needs case sensitivity so a client term does not match inside
  # an ordinary English word. THE WHOLE-WORD FLAG ALREADY DOES THAT JOB, and the
  # missing -i meant a LOWERCASE client name walked straight through the gate.
  # Measured on all three cases before and after:
  #
  #     content                          -nwF (old)  -niwF (new)  wanted
  #     an English word CONTAINING the
  #       term as a substring              miss        miss         miss
  #     the term in LOWERCASE, whole word  MISS        HIT          HIT
  #     the term in UPPERCASE, whole word  HIT         HIT          HIT
  #
  # The false positive this branch was protecting against is prevented by -w,
  # not by the absence of -i, so the case sensitivity bought nothing and cost
  # every lowercase form. Real instance: a sibling repository holds five files
  # whose names and contents carry a client term entirely in lowercase.
  #
  # NOTE THE ABSENCE OF EXAMPLES ABOVE, and it is deliberate. The first draft of
  # this comment spelled the terms out to show the before and after, and THIS
  # GATE THEN REFUSED ITS OWN SOURCE, on a public repository. That is the trap
  # this file's own header already names: a scan whose pattern appears in the
  # text it scans reports a hit it caused. The needles are assembled from
  # fragments for exactly that reason, and a comment must not undo it.
  #
  # CORRECTED 2026-09-03. The history pipelines below used to keep only
  # lines starting with "+", which drops the COMMIT MESSAGE: `git log -p`
  # indents every message line with four literal spaces, no "+" in sight,
  # so a term sitting only in a message text (never in a diff line) went
  # unseen, where the pre-fix pipeline (which kept every line) still caught
  # it. `grep -E '^\+|^    '` keeps both: an added diff line, or a message
  # line, while `grep -vE '^\+\+\+ '` still drops the file-header line.
  if [ "${#needle}" -le 5 ]; then
    hits="$(printf '%s\n' "$SCAN_FILES" | tr '\n' '\0' | term_grep_files "$needle" 2>/dev/null | grep -v "^$SELF:" || true)"
    [ "$commits" -gt 0 ] && hits="$hits$(git log -p --all $HIST_NOT -- $HIST_SCOPE 2>/dev/null | grep -E '^\+|^    ' | grep -vE '^\+\+\+ ' | term_grep_stream "$needle" || true)"
  else
    # CORRECTED 2026-08-30. This branch used substring matching, and a longer
    # term whose letters happen to sit inside ordinary English words refused
    # the gate on prose that never named anyone: measured on this repository,
    # 17 history hits for one term, every single one inside a common English
    # word, zero standalone. The whole-word flag fixes the class the same way
    # the short branch's 2026-08-26 correction did, and the trade is stated
    # rather than hidden: a term glued directly to letters or an underscore
    # (term followed by more word characters with no separator) no longer
    # matches here. A hyphenated or space-separated compound still hits,
    # because those are word boundaries. Since E37 (2026-09-03) so is the
    # underscore: see term_grep_files above.
    hits="$(printf '%s\n' "$SCAN_FILES" | tr '\n' '\0' | term_grep_files "$needle" 2>/dev/null | grep -v "^$SELF:" || true)"
    [ "$commits" -gt 0 ] && hits="$hits$(git log -p --all $HIST_NOT -- $HIST_SCOPE 2>/dev/null | grep -E '^\+|^    ' | grep -vE '^\+\+\+ ' | term_grep_stream "$needle" || true)"
  fi
  if [ -n "$(printf '%s' "$hits" | tr -d '[:space:]')" ]; then
    echo "FAIL: NAME-$n appears in the tree or its history"
    fail=1
  fi
done < "$TERMS"
# NO-DATA on zero usable terms, added 2026-09-03. An empty file, or one with
# only blank or comment lines, used to fall through this loop having run its
# body zero times, then print "checked 0 client term(s)" and PASS: a control
# that opened nothing about the one thing it exists to check, reading exactly
# like a pass, which this file's own header rule at the top already forbids.
if [ "$n" -eq 0 ]; then
  echo "NO-DATA: $TERMS has no usable term (blank or comment-only); refusing to certify."
  exit 2
fi
echo "checked $n client term(s), value never printed"

# 2. Attribution and watermark. Needles assembled so this file cannot self-match.
A1="Co-""Authored-""By: (Claude|Opus|Sonnet|Haiku|Fable)"
A2="noreply@""anthropic"
A3="Generated with \[Claude"" Code\]"
# A4 NARROWED 2026-08-25 on founder ruling. It was a bare two word substring
# and it fired on any document that merely NAMED the harness, which refused
# the harness inventory whose entire job is to name it, and an untracked
# handover pack that could never be pushed. The rule this gate serves
# forbids trailers, footers, badges and CREDIT LINES: claims of authorship.
# A sentence naming a dependency is none of those, so this narrowing makes
# the check MATCH the rule rather than weaken it. A3 above still catches the
# exact watermark, and A1 and A2 catch the co-author trailer.
# Proven by scripts/probe_attribution_patterns.sh over 9 cases: 5 real
# attribution forms still CAUGHT, 4 technical references now ALLOWED.
A4="(generated|built|created|made|written|authored|powered) +(with|by) +\[?Claude"" Code\]?"
for pat in "$A1" "$A2" "$A3" "$A4"; do
  hits="$(printf '%s\n' "$SCAN_FILES" | tr '\n' '\0' | xargs -0 grep -niE -- "$pat" 2>/dev/null | grep -v "^$SELF:" || true)"
  [ "$commits" -gt 0 ] && hits="$hits$(git log -p --all $HIST_NOT -- $ATTR_HIST_SCOPE 2>/dev/null | grep -E '^\+|^    ' | grep -vE '^\+\+\+ ' | grep -niE -- "$pat" | grep -v "$SELF" || true)"
  if [ -n "$(printf '%s' "$hits" | tr -d '[:space:]')" ]; then
    echo "FAIL: attribution or watermark pattern found"
    printf '%s\n' "$hits" | head -5
    fail=1
  fi
done

# 3. Typographic dashes, working tree, excluding this file.
# `close ARGV if eof` is load bearing, not tidiness. Perl's $. counts input
# lines and does NOT reset between files, so without it every reported line
# number after the first file is an offset into the whole concatenated stream:
# this gate pointed at line 732935 of a 191 line file. A finding nobody can
# locate is a finding nobody fixes. Found 2026-08-29.
# D9, narrowed at M6 the same way SCAN_FILES was above: products/ paths on
# the export allowlist are the shippable halves, cleaned deliberately as
# part of this cutover, so they are scanned here like any other file.
# products/ paths NOT on the allowlist stay excluded (not yet curated).
dash="$(printf '%s\n' "$SCAN_FILES" | grep -v "^$SELF$" | tr '\n' '\0' | xargs -0 perl -CSD -ne 'print "$ARGV:$.\n" if /\x{2014}|\x{2013}/; close ARGV if eof' 2>/dev/null || true)"
if [ -n "$dash" ]; then
  echo "FAIL: em or en dash"
  printf '%s\n' "$dash" | head -5
  fail=1
fi

if [ "$fail" -eq 0 ]; then echo "PASS: $scan_count file(s) opened, $commits commit(s) of history scanned"; exit 0; fi
echo "REFUSED"; exit 1
