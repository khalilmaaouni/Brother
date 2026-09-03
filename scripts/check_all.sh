#!/bin/sh
# Run every check this repository ships, and report EACH ONE'S OWN EXIT CODE.
#
# WHY THIS EXISTS. PROJECT.md lists these commands and every session runs them
# by hand, re-writing the same loop each time. This session wrote that loop six
# times in one night and put a `grep -c` bug in one of them, which is the exact
# class of error a script removes.
#
# THREE RULES IT OBEYS, all learned here the hard way:
#
# 1. EACH COMMAND'S OWN EXIT CODE, captured from the command, never read after
#    a pipe. `$?` after a pipe belongs to the last stage, not to the gate.
# 2. NAME WHAT FAILED. A summary that says "2 failed" without saying which is a
#    report you have to re-run to interpret. That cost another lane hours
#    tonight.
# 3. NO-DATA IS NOT A PASS AND NOT A FAIL. Exit 2 is reported as its own verdict
#    and does not turn the run red, because a check that could not run has not
#    said the thing is broken.
#
# Exit 0 when every check passes or reports NO-DATA. Exit 1 if any check FAILS.

cd "$(dirname "$0")/.." || exit 1

pass=0; fail=0; nodata=0
failed_names=""
nodata_names=""

run_check() {
  name="$1"; shift
  out="$("$@" 2>&1)"
  code=$?                      # the COMMAND's code, captured before anything else
  last="$(printf '%s\n' "$out" | tail -1 | cut -c1-72)"
  names=""
  case "$code" in
    0) # A python unittest suite that SKIPS a test still exits 0 and prints
       # "OK (skipped=N)". A skip whose own reason says NO-DATA (this
       # estate's convention for "could not run, not a failure") is not
       # evidence the thing it skipped is healthy, so it is never a PASS.
       # A skip with no NO-DATA reason is a deliberate skip and stays PASS.
       # Matched ONLY against the skip's own verbose line ("... skipped
       # 'REASON'"), never the whole captured output: an unrelated PASSING
       # test that merely prints the string NO-DATA in its own assertion
       # (test_loop_bridge.py's CLI-exit-code test) must never flip an
       # ordinary skip elsewhere in the same suite into a false NO-DATA.
       skip_lines="$(printf '%s\n' "$out" | grep -E '\.\.\. skipped ')"
       if [ -n "$skip_lines" ] && printf '%s\n' "$skip_lines" | grep -q 'NO-DATA'; then
         nodata=$((nodata+1)); verdict="NO-DATA"; nodata_names="$nodata_names $name"
       else
         pass=$((pass+1));   verdict="PASS   "
       fi ;;
    2) nodata=$((nodata+1)); verdict="NO-DATA"; nodata_names="$nodata_names $name" ;;
    *) fail=$((fail+1));   verdict="FAIL   "; failed_names="$failed_names $name"
       # CAPTURE EVERYTHING, READ A SLICE. Keeping only the last line
       # destroyed the failing test's own name twice in one night (a flake
       # in a concurrent battery could not be diagnosed either time). A
       # failing check's FULL output now lands in a file the summary names.
       keep="${TMPDIR:-/tmp}/check-all-fail-$name-$$.txt"
       printf '%s\n' "$out" > "$keep" 2>/dev/null && last="$last  [full: $keep]"
       # NAME THE FAILING TESTS IN THE LOG ITSELF. A unittest suite prints one
       # "FAIL: test_x (module.Class)" header per failing test (ERROR: for one
       # that raised), and the product battery (tools/test_all.py) reprints
       # them under its FAILURES block. They are copied under the verdict
       # line, indented, so scripts/battery_verdict.py can diff the failing
       # NAMES against the declared ones instead of sheltering the whole
       # check: an exception keyed on the check hid eight undeclared failures
       # in one suite on 2026-09-03. The [full: ...] file above is temp and
       # does not survive; the saved log does.
       names="$(printf '%s\n' "$out" | grep -E '^ *(FAIL|ERROR): [A-Za-z0-9_]+ \([A-Za-z0-9_.]+\)' | sed 's/^ */        /')"
       ;;
  esac
  printf '%-7s exit %-3s %-34s %s\n' "$verdict" "$code" "$name" "$last"
  if [ -n "$names" ]; then printf '%s\n' "$names"; fi
}

# Name the revision this run measures. Without this, a critic holding a
# saved ~/.claude/evidence/check-all-*.log has no way to tie it to the
# commit it actually ran against (no 40-hex SHA anywhere in the log).
# battery_verdict.py parses this exact line for its "commit" field.
_sha="$(git rev-parse HEAD 2>/dev/null)"
_describe="$(git describe --tags --always 2>/dev/null)"
_dirty=""
[ -n "$(git status --porcelain 2>/dev/null)" ] && _dirty=" +dirty"
echo "Brother: measuring commit $_sha ($_describe)$_dirty"
echo

echo "Brother: every shipped check, each reporting its own exit code"
echo

run_check "surface"        /usr/bin/python3 -m unittest -v tests/test_surface.py
run_check "context-budget" /usr/bin/python3 -m unittest -v tests/test_context_budget.py
run_check "truth-claims"   /usr/bin/python3 -m unittest -v tests/test_truth_claims.py
run_check "foreign-method" /usr/bin/python3 -m unittest -v tests/test_foreign_method_compat.py
run_check "acceptance-rev" /usr/bin/python3 -m unittest -v tests/test_acceptance_revision.py
run_check "coverage"       python3 scripts/coverage_check.py
run_check "coverage-self"  python3 -m unittest -v scripts/test_coverage_check.py
# 2026-08-29, D03: the memory ON/OFF harness. Registered in the same change
# that lands it; an unregistered suite is one this gate never runs.
run_check "memory-ab"      python3 -m unittest -v scripts/test_memory_ab.py
run_check "leaf-pins"      python3 scripts/leaf_pin_check.py
# NOTE: this one is run DIRECTLY, not via `-m unittest <path>`, which errors
# on it with an import problem while the direct form passes. The first draft
# of this script used the module form and reported a FALSE RED. Copy the
# invocation a check actually documents; do not assume the forms are equal.
run_check "leaf-pins-self" python3 scripts/test_leaf_pin_check.py -v
run_check "gen-board-self" python3 scripts/test_gen_command_center.py -v
run_check "repeat-guard"   python3 tools/repeat-guard/test_repeat_guard.py
run_check "wisdom-capture" /usr/bin/python3 scripts/test_wisdom_capture.py -v
run_check "handover-ceremony" /usr/bin/python3 scripts/test_handover_ceremony.py -v
# R25.1/R25.3: the limit watcher (classifies a transcript's last record
# into NORMAL or one of four measured limit classes, arms the restart
# flag) and the dynamic restart scheduler (rewrites the launchd plist to
# fire once at the measured reset time). Registered the same change that
# lands it, per this estate's own recorded lesson that an unregistered
# tool is invisible to every check the project owns.
run_check "limit-watch-self" python3 scripts/test_limit_watch.py -v
# The intake diagram gate and the scorer behind it. ADDED 2026-08-29 because
# `grep -n intake scripts/check_all.sh` exited 1: scripts/intake_score.py has
# scored diagrams at weight 10 since it was written, could already return 0.0
# for a record with no fence, and NO AUTOMATED RUN EVER INVOKED IT. (Corrected
# after audit: test_intake_score.py did shell out to it, so 'nothing ran it'
# was an overclaim; nothing ran it WITHOUT a human remembering to.) The check
# is named intake-record-diagrams, not intake-diagram-gate, because it gates
# RECORDS IN THIS REPOSITORY and proves nothing about a live intake turn.
run_check "intake-score-self" python3 scripts/test_intake_score.py -v
# --require-weighted-options became NON-OPTIONAL 2026-08-29, the same change
# that fixed the six records. R16 required exactly that, "or it reverts":
# a debt paid without the gate that stops it recurring is a debt that
# recurs. All six records now carry a weight table, so this is green on
# the population and red the moment a record drops one.
run_check "intake-record-diagrams" python3 scripts/intake_score.py --gate --require-weighted-options
# Delivery tracking for the readiness roadmap. ADDED 2026-08-29 on founder
# direction: put exact dates on the board, then TRACK the misses, because a
# plan that quietly re-dates a slipped row teaches nobody anything and the
# blocker behind it survives to cause the next one. It runs HERE rather than
# on demand precisely so a session cannot produce a perfect board by never
# looking. FAILS when a row is late with no blocker recorded.
run_check "readiness-board-self" python3 scripts/test_gen_readiness_board.py -v
run_check "delivery-tracker-self" python3 scripts/test_track_delivery.py -v
run_check "delivery-tracking"     python3 scripts/track_delivery.py
# W4 of the orchestration watchdog. A claim with no expiry cannot retire
# itself: two such fences held five contended files today and blocked two
# sessions until a human reaped them. FAILS on a claim that can never expire;
# an expiry that merely lapsed is the system working, not a finding.
run_check "fence-expiry-self"     python3 scripts/test_fence_expiry.py -v
run_check "fence-expiry"          python3 scripts/fence_expiry.py
# The graph loop. Codified 2026-08-29 on founder direction, after measuring
# that this estate was using two of the ready-set standard's five practices.
# It answers what may run BESIDE what, which a dependency edge cannot express,
# and which is the class every multi-agent failure today belonged to.
run_check "graph-loop-self"       python3 scripts/test_graph_loop.py -v
run_check "graph-loop"            python3 scripts/graph_loop.py

# W8: the conflict-aware merge queue, reusing graph_loop.py's own write-set
# conflict logic rather than a second copy of it. Registered the same change
# that lands it, per this estate's own recorded lesson that an unregistered
# tool is invisible to every check the project owns.
run_check "merge-queue-self"      python3 scripts/test_merge_queue.py -v
run_check "merge-queue"           python3 scripts/merge_queue.py --demo
# The decomposition standard. INFORMATIONAL for now (--stats exits 0) because
# the board itself violates it: 12 of 16 open nodes are still above the four
# hour work package limit. Row W7 pays that debt and makes this call blocking
# in the SAME change, "or it reverts", exactly as the weighted-options clause
# was handled. A standard nobody can pass yet is wired visibly rather than
# quietly, so the debt is on the board instead of in somebody memory.
run_check "wbs-self"             python3 scripts/test_wbs.py -v
# The private terms scan. Its SUITE runs here, deterministically and with fake
# terms only; the SCAN itself is a pre-push gate, because it needs the real
# list that deliberately lives outside every repository. Wiring the scan here
# instead would either fail on every machine without the list, or tempt
# somebody to commit the list, which is the exact thing it prevents.
run_check "private-terms-self"   python3 scripts/test_private_terms_scan.py -v
run_check "loop-bridge-self"     python3 scripts/test_loop_bridge.py -v
run_check "installed-surface"    python3 scripts/test_check_installed_surface.py -v
# The L5 layer is only worth having if its commands are real. Wired the day it
# landed, after five of its own steps named flags that do not exist.
run_check "l5-self"              python3 scripts/test_check_l5_commands.py -v
run_check "l5-commands"          python3 scripts/check_l5_commands.py
# Priority by what somebody actually asked for. The scheduler orders by graph
# shape and cost; this orders by the verified complaints, and reports every
# complaint no open node claims. An uncovered complaint is not a scheduling
# problem, it is a plan that does not contain the work.
run_check "priority-self"        python3 scripts/test_priority.py -v
run_check "priority"             python3 scripts/priority.py
# F12, the enforcement layer. The hook itself is NOT registered on this
# machine: adding a PreToolUse entry edits machine configuration, which is a
# founder decision. Its suite runs here so the refusal and the fail-open
# behaviour are both proven before anybody is asked to install it.
run_check "lifecycle-hooks-self" python3 scripts/test_lifecycle_hooks.py -v
# Evidence capture. Written after a ten minute battery was captured with
# tail -4, leaving four lines, which made six failing suites unknowable and
# stopped a ready merge. Capture everything, read a slice.
run_check "run-evidence-self"    python3 scripts/test_run_evidence.py -v
# The post-run scope audit: what a run ACTUALLY changed against what it
# declared. The founder chose detect-and-quarantine over declaring every
# hidden surface in advance, because the collisions that hurt are the ones
# nobody thought to declare.
run_check "scope-audit-self"     python3 scripts/test_scope_audit.py -v
# Does the record still match reality after the work landed? The one gap this
# estate had no control for: every instance on 2026-08-29 was found by a
# person asking or by a peer, never by a check.
run_check "record-drift-self"    python3 scripts/test_record_drift.py -v
run_check "record-drift"         python3 scripts/record_drift.py
# The per-task watchdog: 704 lines with 40 passing tests, and ZERO mentions in
# this file until 2026-08-29. Its check_lane_cap already emits IDLE-LANE with
# the unlock printed, which is one of the seven failures this estate hit,
# already detected, with the remedy named. It never fired because nothing ran
# it. The estate did not lack a watchdog, it lacked a trigger.
run_check "task-watchdog-self"   python3 -m unittest -v scripts.test_task_watchdog
# Alive is not advancing. The one watchdog piece that survived every objection
# to building a supervisor, because it is not one: no model, no judgement, one
# question. Borrowed from a framework that flags agent monologue as a stuck
# pattern, which is exactly the 72 minute failure this estate produced.
run_check "progress-deadline-self" python3 scripts/test_progress_deadline.py -v

# The decision screen. Its last tests are aimed at the SHIPPED decision rather
# than the module: every code anchor in it is a line number in a file somebody
# will edit, so this goes red the day the page starts showing the wrong code,
# not the day somebody notices. Driven backwards 2026-08-29 by rotting one
# anchor: FAILED(1), restored: OK.
run_check "decide-self"            python3 scripts/test_decide.py -v

# The memory measurement. Measured 2026-08-29 on this session's own 34 writes:
# 0 lesson matches on tool plus path, 56 with content folded in, across 28 of
# them. That is the mechanical reason memory read as silent for Write and Edit.
run_check "memory-lift-self"       python3 scripts/test_memory_lift.py -v

# The attempt breaker. Its first test class replays a real week: three builds
# tuning something never drawn, then six attempts at one class, all measured
# green, scored 0 of 5. The ledger refuses at three, so attempts 3 to 6 never
# happen. Driven backwards 2026-08-29 by setting the strike limit to 99:
# FAILED(4), restored OK.
run_check "attempt-ledger-self"    python3 scripts/test_attempt_ledger.py -v

# The pattern store. Closes a measured gap: 186 vault notes typed failure and
# zero typed as a pattern that worked. Its important tests are the FINDING ones,
# because a second archive nobody can search is not an improvement.
run_check "pattern-note-self"      python3 scripts/test_pattern_note.py -v

# The progress bars. A bar is the most flatterable object on a status page, so
# its tests try to make it lie: a DONE with no evidence must not raise it.
# Driven backwards 2026-08-29 by relabelling an untouched feature DONE, which
# fails the suite and makes the tool exit 1 naming the claim.
run_check "board-status-self"      python3 scripts/test_board_status.py -v

# The adversarial suite against the reporting itself: board_status.py,
# gen_readiness_board.py and run_evidence.py driven backwards through their
# real command lines, on copies of the real roadmap and real subprocesses,
# never against docs/plan/READINESS-ROADMAP-2026-08-29.json. Closes the gap
# "no adversarial case has been run against the reporting itself".
run_check "reporting-adversarial-self" python3 scripts/test_reporting_adversarial.py -v

# The living system record, team complaint P12: fifty designs after a year and
# none describing the system. The check is the point, not the document: adding a
# script and not regenerating turns this red, so the record cannot be wrong for
# longer than it takes to run the battery.
run_check "system-doc-self"        python3 scripts/test_system_doc.py -v
run_check "release-note-self"      python3 scripts/test_release_note_from_tree.py -v
run_check "system-doc-current"     python3 scripts/system_doc.py --check

# The parity gate's own tests only. The GATE ITSELF is deliberately NOT a
# battery check: it exits 1 while parity is unreached, and that is a true state
# of the product rather than a broken build. Wiring it here would either fail
# every run until the team gate opens, or tempt somebody to soften the gate to
# get green, and softening a gate to get green is the failure this estate
# spends most of its effort refusing.
run_check "parity-gate-self"       python3 scripts/test_parity_gate.py -v

# Per-writer isolation, parity blocker P0.2. Its acceptance test is the
# directive's own, driven against a real repository: distinct directories,
# distinct branches, canonical untouched, and destroying one lane leaves the
# other intact. Concurrency is proven by a BARRIER rather than by timing,
# because a timing test passes on a fast machine that ran everything serially.
run_check "worktree-lane-self"     python3 scripts/test_worktree_lane.py -v

# The durable exclusive claim, parity blocker P0.1. Its exclusion test spawns
# SIX REAL PROCESSES racing for one unit and asserts exactly one wins, because a
# threading test would pass on a module that guards nothing across process
# boundaries, and two SESSIONS is the failure being prevented.
run_check "claim-store-self"       python3 scripts/test_claim_store.py -v

# The forecast. Its tests are aimed at the ways an estimate lies: a single
# number instead of a range, a base rate from the wrong sample, coupled work
# counted separately, and the same hours billed twice.
run_check "forecast-self"          python3 scripts/test_forecast.py -v

# The canonical Work contract. Refusal is the feature: every clause exists
# because something downstream breaks without it, usually invisibly and usually
# at run time. Its last class asserts the module exposes NO decomposer, because
# pretending to turn English into units would be the easiest lie on this board.
run_check "work-record-self"       python3 scripts/test_work_record.py -v

# The decomposition seam, NIGHT-02: an outcome typed in plain English becomes
# a canonical Work document, through a model decomposition that work_record.py
# itself validates. Its refused case is the important one, the same way
# work-record-self's is: a cyclic decomposition must leave the store untouched.
run_check "door-self"              python3 scripts/test_door.py -v

# Adversarial intake, the "Simple intake" parity capability's L4 evidence:
# empty/whitespace outcomes, prompt-injection-shaped text, an oversized
# outcome, invalid UTF-8 in the outcome argument, a decomposer that never
# answers valid JSON, and a unit whose write scope escapes the repository.
# Every case is driven backwards: force the bad input, assert the store is
# never left holding a partial or corrupted write.
run_check "door-adversarial-self"  python3 scripts/test_door_adversarial.py -v

# Serial canonical integration, parity blockers P0.4 and P0.5. The headline
# test is the directive's own scenario: two changes with NO git conflict that
# become semantically incompatible after the first lands. B is green on R0,
# merges cleanly onto R1, fails its own check THERE, is unwound, and keeps its
# work in its lane for repair on the new base.
run_check "integrate-self"         python3 scripts/test_integrate.py -v

# The crash and resume proof, parity blocker P0.7. Drives the REAL controller
# as a subprocess and kills it with SIGKILL mid-run, because a proof that lets
# the dying process tidy up first proves the tidy-up, not the crash. Asserts
# from durable state alone: claims survive, no duplicate claim while leased,
# reconcile reports the dead owner's units, the rightful owner resumes at
# attempt 2, and a takeover after expiry names whose work it inherits.
run_check "crash-resume-proof"     python3 scripts/test_crash_resume.py -v

# The orphan worktree clause: crash reconcile above reports an abandoned
# CLAIM, and until now nothing paired that claim back to the LANE a SIGKILLed
# run leaves on disk. Reports OWNED, ABANDONED or UNKNOWN (NO-DATA) for every
# lane worktree found for a repository; never deletes one, matching
# claim_store's own reporting philosophy exactly.
run_check "orphan-lane-self"       python3 scripts/test_orphan_lane.py -v

# THE SPINE, the estate's most important regression test as of 2026-08-29:
# an outcome reaches merged canonical through the real command line with no
# manual step between, and the second unit verifies on the base the first one
# advanced. Built by running it, which found two defects reading had missed.
run_check "spine"                  python3 scripts/test_spine.py -v
# NIGHT-01: the real coding-model worker loop_bridge's LaneWorker spawns.
# Registered here so it runs the same night it lands (the estate's own
# recorded lesson is that a tool joining no registry is invisible to every
# check the project owns).
run_check "model-worker"           python3 scripts/test_model_worker.py -v
# The push boundary: the last moment a mistake is cheap and the first moment
# it becomes somebody else's problem. Runs the checks that already existed at
# the event that matters, which was the gap all along.
run_check "pre-push-gate"        python3 scripts/pre_push_gate.py
run_check "pre-push-gate-self"   python3 scripts/test_pre_push_gate.py -v
# The handback guard: a sub-session finishing work never pushes the default
# branch, it pushes its feature branch and hands back for review. Registered
# the same change that lands it, per this estate's own recorded lesson that
# an unregistered tool is invisible to every check the project owns.
run_check "handback-guard-self"  python3 scripts/test_handback_guard.py -v
run_check "wbs-granularity"       python3 scripts/wbs.py
# E28: the assurance product's silent-failure lint (law L11) was invisible to
# every battery round because scripts/check_all.sh never called sbe_score.py;
# silent failures sat in the tree until someone ran the product's own status
# ladder by hand. This runs the IN-TREE scorer (never the installed plugin
# copy under ~/.claude, which would couple the battery to this machine) over
# the repository root: --repo-only NAMES rather than runs the checks fed by
# a vault, a session ledger or the installed plugin, so only a check that
# opens a file in this tree can turn this line red, and --strict makes that
# verdict block the exit code. Registered the same change that lands it, per
# this estate's own recorded lesson that an unregistered tool is invisible to
# every check the project owns.
run_check "silent-failure-lints" python3 products/brothersbe/tools/sbe_score.py . --repo-only --strict
# R10: the invocable surface has a ceiling and a test that fails above it.
# Held at TODAY'S count as a ratchet against growth, never an aspiration, so
# it is green now and red the moment the surface grows. Lowering it is a
# separate, deliberate act.
run_check "surface-budget"        python3 scripts/surface_budget.py
# The ceiling's own suite, joined to no registry until now: 24 tests
# calibrated both ways (a fixture at or under the ceiling passes, one built
# to exceed it fails) sat green beside a check that never ran them.
run_check "surface-budget-self"   python3 scripts/test_surface_budget.py -v
run_check "attribution"    sh scripts/probe_attribution_patterns.sh proposed
run_check "cleanse"        bash scripts/cleanse.sh
run_check "blocker-fresh"  python3 scripts/blocker_freshness.py
# The identity leak cleanse.sh and private_terms_scan.py cannot see: git
# config and commit trailers come from `git config`, not a file either scan
# opens. 48 public commits carried the founder's work email this way.
# Mechanical third of "Shield now, rewrite later"
# (docs/decisions/2026-08-30-work-email-in-public-commits.html); the history
# rewrite itself stays a separate, founder-only act.
run_check "identity-guard"       python3 scripts/identity_guard.py
# W2, resource admission control. Deferred out of v1 on 2026-08-29 (the board
# calls it prep for W9, not a cancellation) but built and wired now so it is
# ready the day the flip condition closes. Every band here is calibrated
# with an INJECTED reading, never the real machine, because a check that
# passes or fails by accident of this machine's current disk or load is the
# exact class of false signal the module itself exists to remove.
run_check "resource-gate-self"    python3 scripts/test_resource_gate.py -v

# R22: The Daybook, a generated decision feed across Brother, BrotherSBE and
# BrotherModeUp. Registered the same change that lands it, mirroring the
# delivery-tracker and fence-expiry pairs above: a "-self" suite plus the
# real generator, which reports NO-DATA (exit 2) rather than FAIL when a
# sibling repository's decision store is absent, never a silent skip.
run_check "daybook-self"   python3 scripts/test_daybook.py -v
run_check "daybook"        python3 scripts/daybook.py

# G1-M3.1/M3.2: the eleven capability-area acceptance harness. Registered the
# same night it lands, per the estate's own recorded lesson that a tool
# joining no registry is invisible to every check the project owns. The
# harness's own exit contract is nonzero ONLY on FAIL (NO-DATA never flips
# it), so a night where every area is still NO-DATA reads as exit 0 here,
# which is correct: nothing has been proven broken yet, only unmeasured.
run_check "acceptance-self" python3 scripts/test_acceptance.py -v
run_check "acceptance"      python3 scripts/acceptance.py

# P0.1/P0.2, the composition wave (docs/plan/P0-COMPOSITION-WAVE-2026-08-30.md):
# one door, an outcome in and a verified delivery report out, driving the
# already-tested door/loop_bridge/model_worker/integrate spine as one command
# rather than a person running each by hand. Registered the same change that
# lands it, per this estate's own recorded lesson that an unregistered tool
# is invisible to every check the project owns.
run_check "brother-run-self" python3 scripts/test_brother_run.py -v

# The receipt door, option A of the door redesign decided 2026-08-31
# (docs/plan/DOOR-REDESIGN-STUDY-2026-08-31.md): every delivery ends with its
# own proof in plain words, the engine's internal narration goes to the run
# log rather than at the person, and the release and acceptance screens are
# computed from receipts through a fixed marks table rather than written by a
# model. Driven on a real fixture run, and backwards: the machinery strings
# must be ABSENT from the surface and PRESENT in the log, so "we removed the
# noise" cannot be satisfied by deleting the record.
run_check "receipt-door-self" python3 scripts/test_receipt_door.py -v

# P0.1 packaging: the installed bundle ships no code of its own beside
# commands and skills, so /brother's BUILD IT route was dead on an installed
# machine. bundle/runtime/ is the fix; this is its own drift gate, proving
# the closure is computed from the real files (not a hand-typed list), the
# packaged copy is byte-identical, and the installed launcher actually runs.
run_check "bundle-runtime-self" python3 scripts/test_bundle_runtime.py -v
# P0.4, the same wave: the eleven capability areas re-proven THROUGH the
# public entry point (a plain outcome sentence into brother_run.py), never
# through a hand-built Work document or a named internal worker command.
run_check "product-acceptance-self" python3 scripts/test_product_acceptance.py -v

# P0.5: the clean-install proof. A real `claude plugin install brother`
# into a throwaway CLAUDE_CONFIG_DIR and HOME, the installed launcher
# resolved from the real plugin cache by its manifest, one stubbed unit
# integrated into a fresh target repo, and a forced bad state (the launcher
# deleted from the cache) proven to read as a named FAIL rather than a
# stack trace. Needs network (brothermode and brothersbe resolve from
# GitHub) and the real claude CLI; skipped rather than failed when either
# is absent. Measured full run: about 6 seconds, well under the 90 second
# bar, so it is registered here rather than left purely on-demand.
run_check "clean-install-e2e-self" python3 scripts/test_clean_install_e2e.py -v

# R27.1: the installed-artifact lifecycle fault lab (the hardening program's
# first mechanism; docs/plan/HARDENING-2026-08-30-CODEX.md). This registers
# only the harness's own meta-test (fast, no install, no network): it
# proves fault_lab.py imports no product module, which is the one law that
# keeps the lab from recreating the blind spot it exists to close. The four
# seeded scenarios themselves (python3 scripts/fault_lab.py, a real `claude
# plugin install` per run, tens of seconds) are run on demand and at
# release candidates, the same cadence as clean-install-e2e-self above.
run_check "fault-lab-self" python3 scripts/test_fault_lab.py -v

# R27.2: the generated negative-space contract audit (the hardening
# program's second mechanism; docs/plan/HARDENING-2026-08-30-CODEX.md). It
# mechanically inventories every durable-noun writer module and asks the
# same thirteen questions of each; a NO-DATA cell is named, never silent,
# and (without --strict) reports as this estate's own NO-DATA exit code
# (2), so a real, honest first-run finding shows up in every battery run
# without turning it red. test-negative-space-audit-self is the harness's
# own meta-test: it proves the extractor actually sees a fixture built to
# look like a real writer, and that deleting one answer flips exactly one
# cell to NO-DATA. Registered the same change that lands both, per this
# estate's own recorded lesson that an unregistered tool is invisible to
# every check the project owns.
run_check "negative-space-audit-self" python3 scripts/test_negative_space_audit.py -v
run_check "negative-space-audit"      python3 scripts/negative_space_audit.py

# R27.3: the assurance mutation gate (the hardening program's third and last
# adopted mechanism; docs/plan/HARDENING-2026-08-30-CODEX.md). Four bounded,
# named mutants (a termination-condition comparison flip, a dict field
# deletion, a boundary check removal, a parse-failure silently continued)
# are seeded one at a time into a scratch copy of scripts/, never the
# working tree, and each must die by its own NAMED killer test. This
# registers both the meta-test (proves every anchor still matches the real
# source, drives every real mutant to KILLED, and drives a deliberately
# unkillable fixture mutant to SURVIVED so the gate is proven to fail by
# name, not just print PASS) and the plain gate itself. Registered the same
# change that lands it, per this estate's own recorded lesson that an
# unregistered tool is invisible to every check the project owns.
run_check "mutation-gate-self" python3 scripts/test_mutation_gate.py -v
run_check "mutation-gate"      python3 scripts/mutation_gate.py

# R25.2's weekly half and R25.4: the portable pack (a Md summary, one zip,
# learnings, and the WBS board html) that lets work resume on another
# account or machine after a weekly limit pause. Registered the same change
# that lands it, per this estate's own recorded lesson that an unregistered
# tool is invisible to every check the project owns.
run_check "portable-pack-self" python3 scripts/test_portable_pack.py -v

# P0.1: bare /brother's decision order is now single-authority, not two
# sections telling the reader different things on the same trigger. Prose as
# code, pinning the words this estate's own lesson says a suite can otherwise
# pass over: one authoritative heading, the old contradicting sentence stays
# gone, and the storage (a run id or run directory) never reaches the person.
run_check "door-routing-prose-self" python3 scripts/test_door_routing_prose.py -v

# R26.1: the privacy filtering spec (docs/plan/PRIVACY-FILTERING-SPEC.md)
# names, by content class, which edition owns it, the rule a session applies
# before writing, and what generalization means if it must cross toward
# public. This check is mechanical only: it does not judge the spec's
# quality, just that every content class and the default rule are still
# present in the file, so a future edit cannot silently drop a row.
# Registered the same change that lands it, per this estate's own recorded
# lesson that an unregistered tool is invisible to every check the project
# owns.
run_check "filtering-spec"      python3 scripts/check_filtering_spec.py
run_check "filtering-spec-self" python3 scripts/test_check_filtering_spec.py -v

# H2: the human decision node the readiness roadmap's H_series names missing
# (the chain ends at a green gate and a merge, never at whether a person
# accepted the result). Registered the same change that lands it, per this
# estate's own recorded lesson that an unregistered tool is invisible to
# every check the project owns.
run_check "accept-delivery-self" python3 scripts/test_accept_delivery.py -v

# R26.2/R26.4: the allowlist exporter and the edition guard, docs/plan/
# HUB-MIGRATION-PLAN-2026-08-30.md steps 4 and 5. edition_guard.py binds a
# directory to its nearest .brother-edition and refuses a push toward the
# public export target from any edition except the exporter's own marked
# invocation; export_public.py builds the candidate export tree from
# docs/plan/EXPORT-ALLOWLIST.txt and refuses to run when cleanse, identity
# or the private-terms scan does not clear it. Registered the same change
# that lands them, per this estate's own recorded lesson that an
# unregistered tool is invisible to every check the project owns.
run_check "edition-guard-self" python3 scripts/test_edition_guard.py -v
run_check "export-public-self" python3 scripts/test_export_public.py -v
# R28.1: the law auditor (docs/plan/READINESS-ROADMAP-2026-08-29.json). Parses
# the ENFORCEMENT/ENFORCED bullets already in the law books and checks every
# ENFORCED law's named file is actually on disk; a law naming a missing file
# FAILS by name, an UNENFORCED law is a finding, never a failure. Registered
# the same change that lands it, per this estate's own recorded lesson that
# an unregistered tool is invisible to every check the project owns.
run_check "laws-audit-self" python3 scripts/test_laws_audit.py -v
run_check "laws-audit"      python3 scripts/laws_audit.py

# W3: who decides when the founder is away (docs/plan/READINESS-ROADMAP-2026-08-29.json,
# row W3). GREEN proceeds, AMBER records PROVISIONAL-FABLE carrying its overrule
# sentence, RED is refused and queued, never acted on. Registered the same
# change that lands it, per this estate's own recorded lesson that an
# unregistered tool is invisible to every check the project owns.
run_check "fable-authority-self" python3 scripts/test_fable_authority.py -v
# VB3-01: the benchmark bundle (make_benchmark_bundle.py) and the D04
# outcome-lift honesty checks it depends on. Registered the same change
# that lands them, per this estate's own recorded lesson that an
# unregistered suite is invisible to every check the project owns.
run_check "vault-benchmark-v2-self" python3 -m unittest -v scripts/test_vault_benchmark_v2.py
run_check "benchmark-bundle-self"   python3 -m unittest -v scripts/test_make_benchmark_bundle.py
run_check "fable-authority"      python3 scripts/fable_authority.py --selftest

# B3 and B4: black-box proofs that VB3-03 (tenancy) and VB3-04 (policy
# fail-closed) hold, invoked at the real product boundary (a served
# bm_vault_serve.py process and bm_vault.py's own CLI recall command) over
# a vendored, frozen copy of the BrotherModeUp modules that merged those
# rows -- see scripts/fixtures/bmu_vault_seam/PROVENANCE.md for exactly
# which commits and why the hub carries a copy instead of BrotherModeUp
# itself. Each file is both the proof (its own __main__ prints one line per
# assertion and a final PASS/FAIL/NO-DATA verdict line) and its own
# backwards-driven regression suite (a unittest class in the same file that
# deliberately collapses the seam under test and demands the SAME check
# catch it). readiness_gate.py's "tenancy-leakage-zero" and
# "fail-closed-policy" rows read these exact paths; registering them here
# is the same "an unregistered tool is invisible to every check the
# project owns" lesson as every other row in this file.
run_check "tenancy-isolation"    python3 scripts/test_tenancy_isolation.py
run_check "policy-fail-closed"   python3 scripts/test_policy_fail_closed.py

# V2 (docs/plan/VAULT-HARDENING-SCOPE-2026-08-31.md): the compliance persona's
# erasure-propagation probe. It proves the primary store and its index are
# cleaned by forget-execute (STRICT surfaces absent), and it MAPS honestly
# that forget does not cascade to derived notes or catalogs (NOT-PROPAGATED
# surfaces, recorded not hidden). Driven backwards by test_test_*.
run_check "erasure-propagation"      python3 scripts/test_erasure_propagation.py
run_check "erasure-propagation-self" python3 -m unittest -v scripts/test_test_erasure_propagation.py

# V3 (docs/plan/VAULT-HARDENING-SCOPE-2026-08-31.md): the security persona's
# two tenancy hardening probes, deepening the two rows just above rather
# than duplicating them.
#
# (a) test_tenancy_routing_mutation.py mutation-tests the cross-tenant
# ROUTING seam itself (bm_vault_context.tenant_env), which
# test_tenancy_isolation.py's own backwards class never touched (that one
# collapses the FIXTURE via a symlink, never the routing CODE). This file
# forces tenant_env to ignore the requested tenant and always resolve
# tenant-a, in a throwaway copy, and demands the same isolation assertion
# catch the resulting leak -- proving the routing seam is guarded, not
# merely that the fixture-collapse case is.
run_check "tenancy-routing-mutation" python3 scripts/test_tenancy_routing_mutation.py
#
# (b) test_wire_dual_principal.py probes whether the served HTTP boundary
# can express BOTH a human and an agent principal on one request, the way
# the CLI already can (VB3-04's decide_dual intersection guarantee). It
# cannot today: bm_vault_serve.py's do_POST reads no agent-shaped field,
# static or behavioral. That is a genuine, honest FAIL-BY-DESIGN, not new
# capability to add here -- declared in docs/plan/BATTERY-EXPECTATIONS.json
# (class expected_unavailable) so scripts/battery_verdict.py's "is main
# healthy" verdict reads it as a known, reviewed gap rather than a fresh
# regression, while this raw script's own exit code stays honestly red
# until the wire actually gains the field.
run_check "wire-dual-principal"      python3 scripts/test_wire_dual_principal.py

# V4 (docs/plan/VAULT-HARDENING-SCOPE-2026-08-31.md): the ops persona's
# second-machine restore finding. restore_drill_enterprise.py's own drill
# restores into a subdirectory of the same tempfile.mkdtemp() root the
# source lived in; this proves restore into an environment that shares
# NOTHING with the source instead: fresh HOME, fresh TMPDIR, a PATH trimmed
# off the source tree, and the source vault MOVED away (not deleted) after
# backup and before restore, so any accidental read of it fails loudly. No
# true second physical machine is available tonight, named honestly in the
# result's own "limit" field; this proves environment isolation on one host.
run_check "clean-env-restore"        python3 scripts/test_clean_env_restore.py

# VB3-12: the enterprise readiness gate (six review items) and the
# fifteen-question PR bar as checkable surfaces. The GATE ITSELF is
# deliberately NOT a battery check, same reasoning as parity-gate-self above:
# a gate that is allowed to go NOT READY between check_all runs (the
# japanese-threshold and reproducible-release-artifact rows are honestly
# NO-DATA today, and are non-critical by design) must never make a clean
# battery run red on their account. Wiring the gate itself in here would
# either fail every run until every row lands, or tempt someone to soften
# the gate to get green. Registered the same change that lands it, per this
# estate's own recorded lesson that an unregistered tool is invisible to
# every check the project owns.
run_check "readiness-gate-self"  python3 -m unittest -v scripts/test_readiness_gate.py

# A4 / Root 3 (docs/plan/ROOT-CAUSE-REGISTER-2026-08-31.md): the release
# identity chain, checked mechanically at the exact path the readiness gate
# names. The self test drives every verdict class backwards; the live check
# tolerates an unreachable remote or an uninstalled copy as printed NO-DATA
# lines but FAILS on any contradicting link. Registered the same change that
# lands it, per this estate's own recorded lesson that an unregistered tool
# is invisible to every check the project owns.
run_check "release-invariant-self" python3 scripts/test_release_invariant.py -v
run_check "release-invariant"      python3 scripts/release_invariant.py
# Red-team item 3 / infra persona: the exported content is reproducible from
# its tested source. The self test drives match, tamper and NO-DATA backwards;
# the live check needs a --tag and a public checkout, so it is self-test only
# in the battery (the reproduce run is a release-time, argument-bearing act).
run_check "reproduce-export-self"  python3 scripts/test_reproduce_export.py -v
# The japanese-threshold gate item's own evidence: runs BrotherModeUp's
# bm_vault_jbench.py (VB2-03, PR 176) as a black-box subprocess and asserts
# per-class floors plus an overall threshold. Exit 2 (NO-DATA) when the
# other repository's tools cannot be located on this machine, which
# run_check's own 0/2/* convention reports as no-data, never a fail.
# Registered the same change that lands it, per this estate's own recorded
# lesson that an unregistered tool is invisible to every check the project
# owns.
run_check "japanese-threshold"      python3 scripts/test_japanese_threshold.py
run_check "japanese-threshold-self" python3 -m unittest -v scripts/test_test_japanese_threshold.py

# A6: battery_verdict.py, the canonical machine-readable "is current main
# healthy" answer, separating PASS/FAIL/NO-DATA into declared-vs-undeclared
# classes against docs/plan/BATTERY-EXPECTATIONS.json. The TOOL ITSELF is
# deliberately NOT a battery check, same reasoning as parity-gate-self and
# readiness-gate-self above: it reads a completed run of THIS SCRIPT, so
# wiring it in as a normal check would have it summarize a run that has not
# finished yet. Only its own suite runs here.
run_check "battery-verdict-self" python3 scripts/test_battery_verdict.py -v

# The closing ceremony law (founder order 2026-08-30): a session close on
# this estate is FINISHED only when the newest handover pack carries the
# start-here with its four sections, the board HTML, the session log and a
# complete zip, fresh within 24 hours and clean of private terms. On a
# machine or moment with no pack this is NO-DATA (exit 2), named, never a
# pass and never red: the battery is not always run at close time.
run_check "close-ceremony"     python3 scripts/close_ceremony_check.py
run_check "close-ceremony-tests" python3 scripts/test_close_ceremony_check.py
run_check "attempt-hook-tests" python3 scripts/test_attempt_hook.py
run_check "find-out-tests" python3 scripts/test_find_out.py
run_check "repeat-control-tests" python3 scripts/test_repeat_control.py

# One-repo transition M3 (docs/plan/ONE-REPO-TRANSITION-2026-08-31.md): the
# consolidated product's OWN battery, delegated, run from its subtree path
# with its own exit code, so the one battery answers for every product it
# holds. Registered the same change that lands the subtree, per this
# estate's recorded lesson that an unregistered tool is invisible to every
# check the project owns. The product's plugin keeps shipping from its old
# repository until the M6 cutover; this line proves the SOURCE stays green
# inside the one repo.
run_check "product-brothersbe" sh -c 'cd products/brothersbe && python3 evals/run_evals.py'

# Row E33: products/brothersbe/tools/test_sbe.py (147 tests) was invisible to
# every battery round; the product line above runs evals/run_evals.py only.
# Registered the same change that wires it into the product's own gates too.
run_check "product-brothersbe-tests" sh -c 'cd products/brothersbe && python3 tools/test_sbe.py'

# One-repo transition M4: BrotherModeUp's OWN battery, delegated, run from
# its subtree path with its own exit code, exactly as the line above does
# for its sibling. Above the summary block ON PURPOSE: the first
# registration of the sibling line was appended below this script's own
# exit and never ran (memory: a registered check is proven by its output
# line, not its presence). The product's plugin keeps shipping from its
# original repository until the M6 cutover.
run_check "product-brothermode" sh -c 'cd products/brothermode && python3 tools/test_all.py'

# The EVAD follow-up, founder 2026-08-31: "we have terrible score with EVAD we
# are not following up on to see if we improve upon". The gauntlet was a review
# run once; these two lines make it an instrument. The self line proves the
# scorer cannot flatter itself (an unrun trial excluded, a stale run refused);
# the score line goes RED when the standing score regressed or nobody has
# re-measured within its staleness window, so "we stopped following up" is a
# battery failure rather than a thing somebody has to notice.
run_check "evad-score-self"    python3 scripts/evad_score.py --selftest
run_check "evad-score"         python3 scripts/evad_score.py

# The loom, the committed second step of the door redesign
# (docs/decisions/door-redesign-2026-08-31.json, option A). It is the
# interaction layer above the receipt door: a risky piece is PARKED before it
# runs so the release screen is a gate rather than an obituary, and the
# person's answer to a screen is recorded in their own words and never
# scored. Driven both ways, and end to end: the proof that parking is a gate
# is the parked unit's own file not existing after the run. Registered the
# same change that lands it, per this estate's own recorded lesson that an
# unregistered tool is invisible to every check the project owns.
run_check "loom-self" python3 scripts/test_loom.py

# E3.2: one source of version truth. An external audit found three
# disagreeing BrotherSBE version numbers in this repository at once (README
# 3.5.1, marketplace.json 3.7.0, actually installed 3.7.1); this fails the
# moment README.md repeats a shipped plugin's version and the number does not
# match .claude-plugin/marketplace.json.
run_check "version-truth" python3 scripts/test_version_truth.py

# tool-bypass: the containment claim may never exceed the measurement. This does not
# assert that shell writes are contained (5 of 7 spellings are, two are not); it
# asserts that no page or register says otherwise. Registered 2026-09-01 after a recon
# found the estate had no mechanical guard against upgrading that sentence, which is
# exactly what had to be walked back once before.
run_check "tool-bypass" python3 scripts/tool_bypass_test.py

# R25: the limit drill. The row's done-check is a DRILL, not a unit test, so it
# is registered as one: a session is driven into each of the three limit classes
# (session ceiling, daily ceiling, simulated account limit) and each must pause
# itself, run the ceremony, and resume the same work with the same decisions.
# Driven both ways in every class, so a harness that could only ever report a
# pause fails here. Everything runs against fixtures in a temporary directory:
# the spend guard's own path constants are repointed there and asserted to be
# outside ~/.claude before the first call, because a drill that disabled a live
# brake to prove the brake works would be a catastrophe, not a proof.
run_check "limit-drill" python3 scripts/limit_drill.py

# write-ledger-self: W1's suite existed and passed and NOTHING RAN IT, so the
# generated system map reported write_ledger as NO-DATA while 20 tests sat green
# beside it. That is this estate's recorded failure of a tool joining no registry,
# in its quietest form: not a missing test, a test nobody asked for.
run_check "write-ledger-self" python3 scripts/test_write_ledger.py -v

# fence-enforced: the fence has had two modes for a month and only one of them was
# ever driven. Advisory fails OPEN on fifteen enumerated conditions, enforced is
# meant to fail CLOSED on the same fifteen, and NOTHING PROVED IT. This drives both
# modes side by side against fixtures, in its own temporary directory, and carries a
# positive control (a properly claimed write must still ALLOW under enforced) so
# "refuses everything" can never read as "enforces correctly". It sets the mode only
# in a child process's environment; it never writes machine configuration.
run_check "fence-enforced" python3 scripts/fence_enforced_drill.py

echo
echo "pass $pass   fail $fail   no-data $nodata"
[ -n "$failed_names" ] && echo "FAILED:$failed_names"
[ -n "$nodata_names" ] && echo "NO-DATA:$nodata_names  (not a pass, and not a failure)"
[ "$fail" -eq 0 ] || exit 1
exit 0
