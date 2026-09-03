#!/usr/bin/env python3
"""Run every BrotherMode suite SERIALLY and return one exit code.

WHY THIS EXISTS
  There are four suites, not one, and the number keeps growing. Until now the
  loop-close gate was "run the tests", which in practice meant whichever suites
  the person remembered, and a suite nobody ran is a suite that is not gating
  anything. This file is the gate: one command, one exit code, every suite.

WHY SERIALLY, AND WHY THAT IS NOT A STYLE CHOICE
  The suites used to rename a module aside mid-run, so two running at once
  corrupted each other (reproduced 2026-07-27: the fence hook suite failed once
  under contention and passed on re-run). The P9 fix round removed the rename
  technique itself, so that particular hazard is gone rather than guarded. This
  still runs serially: the suites share one checkout, spawn subprocesses, and
  compete for the same temp and git state, so a parallel runner here would buy
  wall time at the cost of flakes nobody can reproduce.

WHY EACH SUITE GETS ITS OWN PROCESS
  Importing two suites into one Python process is not isolation: they patch
  module globals, install fakes, and load the same modules under different
  names. A subprocess per suite is the isolation, and it is also what makes a
  crashed or hung suite reportable instead of fatal to the run.

WHY THIS FILE IS NAMED test_all.py
  tools/test_bm.py's no-network/no-subprocess check bans `import subprocess` in
  every SHIPPING module under tools/, with one documented per-file exemption. A
  test runner cannot exist without subprocess. Rather than widen that exemption
  (which would let a future shipping module inherit it quietly), this file takes
  the `test_` prefix the check already excludes for exactly this stated reason:
  "the test files themselves may import subprocess to drive the CLI they are
  testing, which is local execution, not a network call". This IS test
  infrastructure. The shipping ban is untouched and still covers every module a
  founder actually runs.

WHAT LOOP 9 ADDED
  1. An INTERPROCESS LOCK, so two gate runs started in two terminals against
     the same checkout do not interleave. The rename hazard it was originally
     written for is gone (the P9 fix round removed the technique), but two
     concurrent runs still share one working tree, so the lock stays.
  2. PER SUITE TIMEOUTS with hung-suite diagnostics, so a wedged suite is
     reported as a timeout with its partial output instead of hanging the gate
     until somebody notices and kills it. The timeout kills the suite with
     SIGKILL, which runs no cleanup in the child: that is safe only because no
     suite mutates this checkout any more, and it must stay that way.
  3. --artifacts DIR, which writes the FULL output of every suite to disk so CI
     can upload it on failure. Without that flag this file still writes no files.
  4. A CI INVENTORY CHECK: every suite in SUITES must be EXECUTED by a step in
     .github/workflows/tests.yml, so a suite cannot be in the local gate and
     absent from CI, or the reverse. Local and CI run the SAME check, because CI
     runs this file. "Executed", not "mentioned": see the CI inventory section.

Python 3.9, standard library only. No network. Writes no files except the lock
described above, and the suite logs when --artifacts is passed.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import errno
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WORKFLOW = os.path.join(REPO, ".github", "workflows", "tests.yml")

# A suite that hangs is worse than a suite that fails: it burns the whole gate
# with no diagnosis. 900s is roughly 4x the worst observed wall time under heavy
# contention (test_bm went 20s to 202s on 2026-07-27), so it fires on a wedge
# rather than on a slow machine.
DEFAULT_TIMEOUT = float(os.environ.get("BROTHERMODE_TEST_TIMEOUT", "900"))

# Declared in the order that fails fastest and cheapest first, so a broken store
# is reported in eight seconds rather than after a full minute of other suites.
# The store suite is the foundation every other one stands on.
SUITES = (
    # Registered 2026-08-29. These four shipped today and the gate correctly
    # REFUSED to run without them, which is the same refusal that made the
    # superseded local main look like a draft. A suite on disk that the gate
    # does not know about is a suite nobody runs.
    "test_bm_freshness.py",
    "test_bm_plan.py",
    "test_bm_recurrence.py",
    "test_bm_summary.py",
    # W9.1, registered in the SAME change that created it. Two suites shipped
    # unregistered earlier today and this gate caught both; nothing forces
    # registration at birth, so every author forgets independently.
    "test_bm_worker_spawn.py",
    "test_bm_verify.py",
    "test_bm_repair.py",
    # Documentation consistency runs first: it takes a fraction of a second and
    # it fails on a fact that has drifted rather than on code, so a stale README
    # is reported before the long suites run rather than after them.
    "test_bm_docs.py",
    "test_bm_store.py",
    # Startup reconciliation, added in this same change beside
    # tools/bm_reconcile.py. Registered here because the inventory gate
    # REFUSES TO RUN AT ALL while a test_*.py file on disk is missing from
    # SUITES, and an unregistered suite is one the battery never executes.
    "test_bm_reconcile.py",
    # The 3.4.0 release train, added 2026-08-24. All three are registered in
    # the same change that lands their suites, because test_all REFUSES TO RUN
    # while any test_*.py on disk is missing from SUITES, and that refusal is
    # the control that stops a new suite from silently never executing. It
    # fired on exactly these three, which is the gate working.
    #
    # Placed beside test_bm_reconcile.py: the worktree classifier extends the
    # same reconciliation family, and the other two are cheap.
    "test_bm_reconcile_worktrees.py",
    "test_bm_authority_sink.py",
    "test_bm_release_invariant.py",
    # M24, moved here from scripts/ in the same change. A suite the
    # inventory cannot see is a suite the battery never runs, and it
    # cannot complain, because _discover below scans only this directory.
    "test_migrate_install_encoding.py",
    # Schema 22, evidence origin/provenance, added 2026-08-23. Placed
    # right after the store suite it extends, before the loop 2 suite
    # below, since it proves the migration, the validated write, and the
    # satisfaction function against a real store, then checks the
    # readers this loop touches (bm_passport.py, bm_cursor.py) through
    # the same fixture technique those suites already use.
    "test_bm_evidence_origin.py",
    # The done review's third state (2026-08-23): NO-DATA beside pass and
    # fail. Registered in the same change that adds the suite, because the
    # gate refuses in BOTH directions and an unregistered suite is one the
    # battery never executes.
    "test_bm_done_no_data.py",
    # Loop 2 (2026-08-01): the mechanical command line over the Store
    # service methods and D-2 read accessors in bm_store.py. Placed right
    # after the store suite it wraps, before the other subprocess-heavy
    # suites, since it drives tools/bm_project.py as a real subprocess and
    # depends on the store schema the suite above already proved sound.
    # APOSTROPHES ARE SAFE IN THESE COMMENTS AGAIN, since SBE6. The fact
    # loader in bm_project_facts.py used to extract every quoted span in
    # this tuple with a plain quote-to-quote regex over the source text, so
    # one apostrophe in a comment opened a fake quoted region that swallowed
    # real suite names; it broke test_bm_docs dot py, the test named test
    # every named suite file exists. _suites now parses with ast.parse and
    # ast.literal_eval, which never see comments at all, so the fourteen
    # NO APOSTROPHE warnings this file used to carry were removed on
    # 2026-08-24. If a future loader goes back to reading this file as text
    # rather than as a syntax tree, the hazard returns with it.
    "test_bm_project.py",
    # U1, the autonomy contract layer (loop L02, 2026-08-05): the CLI over the
    # schema 14 autonomy Store service methods added to bm_store.py. placed
    # right after the project CLI suite, before the other subprocess-heavy
    # suites, since it drives tools/bm_autonomy.py as a real subprocess and
    # depends on the schema-14 tables the store suite two entries up already
    # proved sound.
    "test_bm_autonomy.py",
    # U2, the durable Full-Auto controller (loop L03, 2026-08-05): the engine
    # tests drive ControllerEngine in process against the schema 15 tables,
    # and the CLI section drives tools/bm_controller.py as a real subprocess,
    # the same way test_bm_autonomy.py drives its own CLI. placed right after
    # the autonomy suite it builds on, since the controller run needs a live
    # contract the autonomy CLI already proved sound.
    "test_bm_controller.py",
    # L04 (2026-08-05): founder mode, IC mode, the record of what was decided,
    # the half hour catch-up and the handover pages, over the schema 16
    # tables. placed right after the controller suite whose run state and unit
    # rows this one reads, and it drives tools/bm_lead.py in process for most
    # of its work plus as a real subprocess for the pre-consent proof.
    "test_bm_lead.py",
    # L05 (2026-08-05): the visual surface. The vocabulary suite first (the
    # drawn shapes, the alert ladder, the insight box and the rewritten
    # refusals in tools/bm_visual.py), then the page and CLI suite
    # (tools/bm_view.py: the live project view, the consent door, the empty
    # states and the developer brief page). placed right after the lead
    # surface suite whose collectors both modules read, over the schema 17
    # tables the store suite already proved sound.
    "test_bm_visual.py",
    "test_bm_view.py",
    # v3 Wave B (2026-08-08): the native agent definitions pin and the
    # hook chain measurement suite, registered the same day they landed
    # so the inventory gate never runs past an unlisted suite.
    "test_bm_agents.py",
    "test_bm_hookperf.py",
    # v3 Wave C (2026-08-08): the lifecycle canary step pins. The live
    # canary script itself stays unregistered like probe-installed, it
    # needs a real claude binary; these pins are fast and deterministic.
    "test_bm_e2e_pins.py",
    "test_bm_fence_hook.py",
    # Loop 6 WP-G (2026-08-01): the Bash-write detection suite for
    # tools/bm_bash_audit.py. Placed right after the fence hook suite it
    # extends: both drive real subprocesses against a throwaway project and
    # share the same session-identity module.
    "test_bm_bash_audit.py",
    # D6 (2026-08-07): the hook cost benchmark, tools/bm_hookbench.py, and the
    # page it generates, docs/PERFORMANCE.md. Placed right after the two hook
    # suites whose programs it times, since it drives the same hook
    # entrypoints against a throwaway store and asserts nothing about how long
    # they took, only that the page is generated from a real run and refuses
    # the numbers it cannot take honestly.
    "test_bm_hookbench.py",
    # The wall-clock lint and its own calibration, added 2026-08-10. It is the
    # machine that refuses the single most frequent defect in the recorded
    # history of this repository: a test asserting an absolute wall-clock
    # duration, which measures the HOST rather than the code and goes red on a
    # busy laptop while nothing changed. Found and fixed SEVEN separate times
    # in ten days by seven different people; review caught it every time and
    # prevented it zero times, which is the whole argument for a lint over
    # more care. Placed beside the two benchmark suites because it is the rule
    # those suites are the sanctioned exception to: the allow list names them,
    # so an absolute ceiling stays legal exactly where timing IS the
    # measurement and nowhere else. Written once with an apostrophe on
    # 2026-08-10, which made the fact loader read a bare s as a suite name and
    # turned test_bm_docs red, exactly as the note two hundred lines above
    # says it would.
    "test_bm_lint_walltime.py",
    # The progress-page check, added 2026-08-10 on a founder directive. The
    # progress page is how a non-engineer founder sees where a project stands,
    # and sessions kept BUILDING it and never SHOWING it, so it had to be
    # asked for. tools/bm_progress_check.py decides mechanically, per project,
    # whether a plan exists and whether the page is missing or older than that
    # plan, and tools/bm_sessionstart.sh runs it at every session start so the
    # verdict arrives in context instead of depending on memory. This suite
    # holds its calibration, including the purity assertion: the tool must
    # change zero bytes, because it runs on every single session start.
    "test_bm_progress_check.py",
    # The forecast calibration and closure check, added 2026-08-12 on a
    # founder directive after every judgement-derived estimate in one night
    # was wrong by four to five times in the SAME direction while the one
    # estimate derived from a measured run was accurate. Nothing recorded
    # forecast against actual, so the bias survived four estimates and would
    # have survived forever. tools/bm_forecast.py records both, computes the
    # correction from real history, and REFUSES to invent one below three
    # usable pairs. This suite holds that refusal: the NO-DATA path at zero,
    # one and two pairs is the load bearing behavior, because a tool that
    # guesses a multiplier is worse than no tool at all.
    "test_bm_forecast.py",
    # The idle and queue-depth check, added 2026-08-12 alongside the forecast
    # suite above and for the same night. A plan of thirteen tasks finished in
    # ninety minutes with nothing queued behind it, so an unattended night
    # would have produced nothing for six of its eight hours. tools/bm_idle.py
    # reads docs/plan/QUEUE.json and answers whether the machine is about to
    # sit still. Its load bearing rule, held by this suite, is that a BLOCKED
    # item never counts toward depth: an item nobody can start cannot rescue a
    # night, and counting it would turn the check into exactly the false
    # reassurance it exists to remove. Like the progress check above it is
    # pure_read and its suite asserts that.
    "test_bm_idle.py",
    # The change passport producer suite, added 2026-08-15. Its load bearing
    # test is that a hollow value is never deposited: an empty string, a
    # whitespace string, an empty list and a null all read as absence on the
    # consuming side, so writing one to make a field look filled fills
    # nothing and lies to the reader. Its second is direction of travel,
    # asserted by pointing the producer at a root holding a poisoned
    # assurance file and showing the output does not move. Reaching across
    # the seam to fill a field is the failure the seam exists to prevent.
    "test_bm_passport.py",
    # The smallest verified-reality record, added 2026-08-20 (A5), sitting
    # right after the passport suite above because bm_passport.py is this
    # tool's own structural template. The north-star chain
    # (docs/NORTH-STAR-CHAIN.md) ends in a stage named verified-reality that
    # nothing wrote a row for before this suite existed. Its load bearing
    # tests are the three refusals (an anonymous acceptance, a record that
    # links back to nothing, a defect that creates no new intent) and the
    # return edge end to end: a defect must append new queued intent to
    # docs/plan/QUEUE.json AND fail to write its reality row at all if that
    # queue append fails, never one without the other.
    "test_bm_reality.py",
    # 2026-08-29, landed in 2849a46 with the queue-number work and NOT registered.
    # The consequence was not a red gate but an UNRUNNABLE one: test_all refuses to
    # start at all while a test_*.py on disk is missing from SUITES, so for a stretch
    # this evening nobody could verify anything against main, including every open
    # pull request. Registered here rather than left for its author because a gate
    # that cannot run blocks the whole repository, and this is one line.
    "test_bm_queue_numbers.py",
    # 2026-08-29, the stable-id tooling for D05. Registered with its module
    # and its CI step in one change, which is the whole lesson of the five
    # registry failures this branch already closes.
    "test_bm_vault_ids.py",
    "test_bm_vault_temporal.py",
    # VB-05, D09 part B: the as-of query built on top of the contract above,
    # registered with its module and its CI step in the same change per the
    # same lesson.
    "test_bm_vault_asof.py",
    "test_bm_vault_authority.py",
    "test_bm_vault_lifecycle.py",
    # 2026-08-30, D12 part B: the write side of the lifecycle contract.
    "test_bm_vault_promotions.py",
    # 2026-08-30, D07: claim-level provenance, the tooling half.
    "test_bm_vault_provenance.py",
    # 2026-08-30, D14: the entity layer. Registered with its module and its
    # CI step in one change, same lesson as the entries above.
    "test_bm_vault_entity.py",
    # 2026-08-30, D06: the crosswalk. Registered with its module and its CI
    # step in one change, same lesson as the entries above.
    "test_bm_vault_crosswalk.py",
    # D13 (2026-08-30): retention and deletion propagation. Registered in the
    # same change that creates the suite, because the inventory gate refuses
    # to run while a test_*.py on disk is missing from SUITES.
    "test_bm_vault_retention.py",
    # VB2-06 (2026-08-30): staleness demotes authority rank until re-verified.
    # Registered in the same change that creates the suite, same lesson as
    # the entries above: the inventory gate refuses a test_*.py on disk that
    # SUITES does not name.
    "test_bm_vault_staleness.py",
    # VB2-05 (2026-08-30): the answer ledger. Registered in the same change
    # that creates the suite, same lesson as the entries above: the inventory
    # gate refuses a test_*.py on disk that SUITES does not name.
    "test_bm_vault_ledger.py",
    # VB2-01 (2026-08-30): permission-aware retrieval, recall trims by
    # identity before content is served. Registered in the same change that
    # creates the suite, same lesson as the entries above.
    "test_bm_vault_policy.py",
    # VB2-02 (2026-08-30): the vault answers over the wire. Registered in the
    # same change that creates the suite; the inventory gate refuses a
    # test_*.py on disk that SUITES does not name.
    "test_bm_vault_serve.py",
    # VB3-03 (2026-08-31): RequestContext and tenancy. Registered in the same
    # change that creates the suite; the inventory gate refuses a test_*.py
    # on disk that SUITES does not name.
    "test_bm_vault_context.py",
    # VB4-02 (2026-08-30): the typed-edge curation queue and its three
    # finders. Registered in the same change that creates the suite; the
    # inventory gate refuses a test_*.py on disk that SUITES does not name.
    "test_bm_vault_curate.py",
    # VB6-01 (2026-08-30): the intake airlock, the one front door for content
    # entering a vault. Registered in the same change that creates the
    # suite, same lesson as the entries above: the inventory gate refuses a
    # test_*.py on disk that SUITES does not name.
    "test_bm_vault_intake.py",
    # The MCP connector catalog (2026-08-30, founder connector order).
    # Registered in the same change that creates the suite, because the
    # inventory gate refuses to run while a test_*.py on disk is missing
    # from SUITES. The suite mocks every subprocess, so it is fast and
    # touches no network and no credential.
    "test_bm_connectors.py",
    # VB7-04 (2026-08-30): the access audit, every recall records its
    # principal. Registered in the same change that creates the suite; the
    # inventory gate refuses a test_*.py on disk that SUITES does not name.
    "test_bm_vault_audit.py",
    # VB7-01 (2026-08-30): the vault front door, bm_vault_cli.py. Registered
    # in the same change that creates the suite; the inventory gate refuses
    # a test_*.py on disk that SUITES does not name.
    "test_bm_vault_cli.py",
    # VB6-05 (2026-08-30): scope-first contradiction triage, report-only.
    # Registered in the same change that creates the suite; the inventory
    # gate refuses a test_*.py on disk that SUITES does not name.
    "test_bm_vault_triage.py",
    # VB12-01 (2026-08-30): survivorship ranking for contested claims,
    # report-only. Registered in the same change that creates the suite; the
    # inventory gate refuses a test_*.py on disk that SUITES does not name.
    "test_bm_vault_survivorship.py",
    # VB6-02 (2026-08-30): citations bind to a note's content, not just its
    # name. Registered in the same change that creates the suite; the
    # inventory gate refuses a test_*.py on disk that SUITES does not name.
    "test_bm_vault_cite.py",
    # VB6-08 (2026-08-30): the interchange replay contract. Registered in
    # the same change that creates the suite; the inventory gate refuses a
    # test_*.py on disk that SUITES does not name.
    "test_bm_vault_events.py",
    # VB7-05 (2026-08-30): the principal registry and the offboarding proof.
    # Registered in the same change that creates the suite; the inventory
    # gate refuses a test_*.py on disk that SUITES does not name.
    "test_bm_vault_principals.py",
    # VB7-02 (2026-08-30): the cross-tool JSON envelope pin. Restored after a
    # rebase automerge dropped it; the inventory gate is what caught the drop.
    "test_bm_vault_json_schema.py",
    # VB8-02 (2026-08-30): vault-to-vault exchange over an age-encrypted
    # relay. Registered in the same change that creates the suite; the
    # inventory gate refuses a test_*.py on disk that SUITES does not name.
    "test_bm_vault_exchange.py",
    # VB8-01 (2026-08-30): the encryption posture census. Registered in the
    # same change that creates the suite; the inventory gate refuses a
    # test_*.py on disk that SUITES does not name.
    "test_bm_vault_posture.py",
    # VB8-04 (2026-08-30): the secure clearing house, JSONL assertions/events
    # tables plus a sha256 manifest. Registered in the same change that
    # creates the suite; the inventory gate refuses a test_*.py on disk that
    # SUITES does not name.
    # VB10-02: the per-class metadata contract. Restored after a rebase
    # automerge dropped it; the inventory gate caught the drop, again.
    "test_bm_vault_contract.py",
    # VB12-02 (2026-08-30): named hierarchy edges with validity intervals,
    # dated placement records, and the competitor namespace's syndicated-id
    # mapping table. Registered in the same change that creates the suite;
    # the inventory gate refuses a test_*.py on disk that SUITES does not name.
    "test_bm_vault_shapes.py",
    "test_bm_vault_export.py",
    # VB3-16 (2026-08-31): the interchange contract, language-neutral typed
    # schemas with stable field ids, Iceberg-shaped export rows, and the
    # star projection labeled lossy. Registered in the same change that
    # creates the suite; the inventory gate refuses a test_*.py on disk
    # that SUITES does not name.
    "test_bm_vault_interchange.py",
    "test_bm_repomap.py",
    "test_bm_playbook.py",
    "test_bm_consolidate.py",
    # The first Toolkit verb, added 2026-08-12. bm_toolkit.py inventories
    # every capability the runtime can see: marketplaces, plugins and their
    # versions, hook events, skills, MCP servers and settings layers. The load
    # bearing behavior this suite holds is the difference between a plugin
    # reported NOT ENABLED and a plugin whose enabled state could not be read
    # at all, false versus null, because collapsing those two is how a
    # conflict report becomes confidently wrong. Like the two suites above it
    # the tool is pure_read and its suite asserts that.
    "test_bm_toolkit.py",
    # The effect-class registry and its purity tests, added 2026-08-10. Every
    # public command declares one of five effect classes, and a command absent
    # from the registry is an error rather than a default, so a new command
    # cannot be added without a declaration. The purity half proves that a
    # command declared pure_read changes zero bytes. WHY THE INTERESTING TEST
    # IS THE SCHEMA ONE. The obvious purity check, a before and after snapshot
    # of a throwaway tree, PASSES for every command including the broken ones:
    # sqlite auto-checkpoints and removes its journal files on clean close, so
    # the write leaves no trace the probe can find. The reachable condition is
    # a store that is BEHIND, where opening a writable Store migrates the
    # database. That is what caught three commands documented as read
    # accessors.
    "test_bm_effects.py",
    "test_bm_stall.py",
    # The hook contract (2026-08-17, the Windows lane). Every wired hook
    # command must run python3, no command may wrap a shell, and the heavy
    # PreCompact entry must declare a timeout. Registered in the SAME change
    # as the suite file, because this gate refuses to run at all when a suite
    # exists on disk and is not listed here, and a session that hits that
    # refusal has to unpick somebody else half landing. Stdlib only, reads
    # hooks/hooks.json and the chain table, writes nothing.
    "test_bm_hooks.py",
    # The two-host reporting arm (2026-08-17). tools/bm_bbstatus.py routes the
    # gate verdict on the origin remote and posts a Bitbucket build status;
    # this suite also reads scripts/local-gates.sh itself, because the routing
    # contract (GitHub unchanged, a Bitbucket reporting failure never changing
    # the exit status) lives in that script rather than in the tool. It makes
    # no network call: the one POST path is driven through a recording opener.
    "test_bm_bbstatus.py",
    # The CI revision-identity capture (2026-08-18). A pull request build has
    # three git identities, and a run that cannot name them has produced a
    # verdict about an unknown tree, which is worse than no verdict because it
    # looks like one. This suite drives the refusal in both directions and
    # reads bitbucket-pipelines.yml to prove the capture runs BEFORE the gate.
    # Stdlib only; it builds a throwaway two-commit repository per test.
    "test_bm_ci_context.py",
    # The escalation surface (tools/bm_escalate.py), committed at 707d80c by
    # a concurrent session that landed the suite without registering it here.
    # The inventory check then did exactly its job: it REFUSED to run the
    # gate at all rather than quietly running 35 suites while a 36th sat on
    # disk unreached, which is the whole point of pairing a suite with this
    # tuple. Registered on 2026-08-16 by the session that hit the refusal,
    # after running the suite alone first (26 tests, OK), because
    # registering a red suite would trade one silent gap for a loud one
    # somebody else would have to unpick.
    "test_bm_escalate.py",
    # The status line and footer links, the 2026-08-05 answer 7 named in
    # docs/program/absolute-lead/DESIGN-visual-surface.md section 14, item 1:
    # tools/bm_statusline.py, a fail silent script BrotherMode ships but does
    # not install, and the footerLinksRegexes calibration against
    # docs/STATUS-LINE.md. Placed right after the hook cost benchmark suite:
    # both are small, fast, stdlib and filesystem only additions that read the
    # same tools/bm_lead.py collector the visual surface suites above already
    # proved sound.
    "test_bm_statusline.py",
    "test_install.py",
    # Loop 3 WP-D (2026-08-01): the consent gate suite for scripts/setup.py,
    # tools/bm_sessionstart.sh, and the SessionEnd path in
    # tools/bm_telemetry.py. placed right after the installer suite; both
    # drive real subprocesses against a fake HOME, and this one is fast.
    "test_bm_consent.py",
    "test_bm_runtimes.py",
    "test_bm_cursor.py",
    "test_bm_autosave.py",
    # The loop estimate ledger, landed from the live install 2026-07-31. Early
    # for the same reason as the docs suite: it is fast and it fails on one of
    # its own three laws rather than on timing.
    "test_bm_ledger.py",
    # The baton ceremony (2026-08-11): the session handover tool, its pack
    # skeleton, the close verifier, the zip and the successor detect. Placed
    # beside the ledger suite for the same reason: stdlib only, fast, and it
    # fails on its own contract rather than on timing.
    "test_bm_handover.py",
    # The canonical protocol schema (Loop 0, 2026-07-31): fast, pure library
    # tests, and its drift test fails on a spec/code mismatch rather than on
    # timing, so it runs with the other cheap early suites.
    "test_bm_schema.py",
    # The Memory Sentinel (Phase 1 of the Full-Auto program, 2026-08-02): the
    # schema 13 tables, the Store methods over them, and the deterministic
    # selector. Placed with the cheap early suites because most of it drives a
    # pure function with plain data and the rest uses a throwaway store, so it
    # fails on a policy branch rather than on timing.
    "test_bm_sentinel.py",
    # The machine-wide session cap (2026-08-09, after an unattended chain
    # ran fifteen sessions at once): pure functions over a throwaway
    # directory of fake transcripts, no store and no subprocess, so it
    # belongs with the cheap early suites and fails on a policy branch
    # rather than on machine state. No apostrophes and no quoted words in
    # this comment: the docs suite parses this block with quote tracking,
    # and both a possessive and a quoted phrase here have each become a
    # phantom suite name once.
    "test_bm_session_cap.py",
    # C-06 (closure register): builds a real wheel, installs it into a
    # throwaway venv, and invokes every console script pyproject.toml
    # declares, the exact adversarial test the register names. Runs near
    # the end of the fast suites because it needs network egress once (to
    # upgrade pip inside its own throwaway venv) and takes real seconds
    # rather than a fraction of one; it reports SKIPPED rather than
    # failing red when that egress is not available, so an offline
    # machine still gets ALL GREEN rather than a false alarm.
    "test_bm_packaging_install.py",
    # The plugin path install suite (N-3 / B-3, 2026-08-04): drives the real
    # claude CLI through marketplace add, install, uninstall and marketplace
    # remove, against a throwaway copy of the tree. Placed with the other
    # environment dependent suites at the end: class B and class C need a
    # claude binary on PATH and skip without one, but class A always runs.
    "test_bm_plugin_install.py",
    # The vault memory tooling (2026-08-28). All seven are registered in the
    # same change that lands their suites, because the gate REFUSES TO RUN
    # while the two inventories disagree in EITHER direction: a suite on disk
    # and not named here never executes, and a suite named here with no CI
    # step is tested on one machine and nowhere else. test_bm_private_scan.py
    # is the load bearing one: it proves the control that keeps private terms
    # out of this public repository, and a control whose test never runs is a
    # claim rather than a guarantee.
    "test_bm_gate.py",
    "test_bm_store_comma_files.py",
    "test_bm_memory_budget.py",
    "test_bm_private_scan.py",
    "test_bm_vault.py",
    "test_bm_vault_catalog.py",
    "test_bm_vault_graph.py",
    # 2026-08-30, VB4-04: the frontmatter schema linter, registered with its
    # module and its CI step in the same change per the same lesson every
    # vault entry above already closes.
    "test_bm_vault_lint.py",
    # 2026-08-29, the vault writer lock. Registered in the same change that
    # declares it in CI below, because the gate refuses to run while the two
    # inventories disagree in EITHER direction. It arrived on the branch in
    # neither, which is what made the whole battery refuse rather than merely
    # skip it: an unregistered suite is one nobody executes.
    "test_bm_vault_lock.py",
    # 2026-08-29, the memory-ceremony distillation. Registered in the same change
    # that lands it, same reasoning as test_bm_vault_lock.py above: a suite on
    # disk and absent here is a claim the gate refuses to accept as a guarantee.
    "test_bm_vault_distill.py",
    # 2026-08-29, WBS 9, the promotion counter. Same reasoning as the two entries
    # right above: registered in the same change that lands it.
    "test_bm_vault_promote.py",
    # 2026-08-30, VB4-06: the split/merge Note Composer port. Registered with
    # its module and its CI step in the same change per the same lesson every
    # vault entry above already closes.
    "test_bm_vault_compose.py",
    # 2026-08-30, VB5-03: the speed pack (content-hash incremental indexing, lexical-first
    # routing, the warm-embedder query cache). Registered in the same change that lands it,
    # same reasoning as every vault entry above: a suite on disk and absent here is a claim
    # the gate refuses to accept as a guarantee.
    "test_bm_vault_speed.py",
    # 2026-08-29: the point-of-need hook itself, which ships as a product module
    # and had no behavioural test at all. Registered in the same change that
    # lands it, because an unregistered suite is one the battery never executes,
    # and that exact hole (test_bm_vault_lock.py, unregistered since PR 63) hid
    # two real defects until PR 68 tripped over it.
    "test_vault_recall_hook.py",
    # tools/brothermode_cli.py, the one deterministic public runtime boundary
    # named in V3-FREEZE-2026-08-07.md answer 4: a thin dispatch layer over
    # tools/bm_project.py, tools/bm_lead.py, tools/bm_view.py,
    # scripts/doctor.py, tools/bm_autosave.py and scripts/install.py. Placed
    # right before the largest suite, after every adapter it wraps has already
    # proven sound in its own suite above: the load bearing test in this suite
    # runs its own status subcommand and the real bm_lead.py status side by
    # side as two subprocesses and diffs their stdout byte for byte, so the
    # adapters must already be trustworthy before this suite can say anything
    # meaningful about the boundary in front of them.
    "test_brothermode_cli.py",
    "test_bm.py",
    # VB8-02 (2026-08-30): vault-to-vault exchange over an age-encrypted
    # relay. Registered in the same change that creates the suite; the
    # inventory gate refuses a test_*.py on disk that SUITES does not name.
    "test_bm_vault_exchange.py",
    # VB10-01, registered in the same change that adds it: the severity-tiered
    # write gate with quarantine, and the cmd_commit rewire onto it. The
    # inventory gate refuses a suite on disk that SUITES does not name.
    "test_bm_vault_tiers.py",
    # VB10-04, registered in the same change that adds it: the LLM enrichment
    # lane. The inventory gate refuses a suite on disk that SUITES does not
    # name.
    "test_bm_vault_enrich.py",
    # VB10-03 (2026-08-30): per-owner defect routing across the estate's own
    # findings. Registered in the same change that creates the suite; the
    # inventory gate refuses a test_*.py on disk that SUITES does not name.
    "test_bm_vault_route.py",
    # VB13-03, registered in the same change that adds it: attribute-management
    # discipline (inheritance, per-channel requiredness, governed code lists)
    # extending the per-class contract. The inventory gate refuses a suite on
    # disk that SUITES does not name.
    "test_bm_vault_attributes.py",
    # VB13-06 (2026-08-30): hierarchy and attribute census extensions.
    # Registered in the same change that creates the suite; the inventory
    # gate refuses a test_*.py on disk that SUITES does not name.
    "test_bm_vault_census_ext.py",
    # VB13-01 (2026-08-30): named hierarchy query modes (direct/transitive)
    # and the hierarchy_edges request flow. Registered in the same change
    # that creates the suite; the inventory gate refuses a test_*.py on
    # disk that SUITES does not name.
    "test_bm_vault_hierarchy_req.py",
    # VB13-04 (2026-08-30): per-attribute provenance with verification
    # status. Registered in the same change that creates the suite; the
    # inventory gate refuses a test_*.py on disk that SUITES does not name.
    "test_bm_vault_attribute_provenance.py",
    # VB13-02 (2026-08-30): the closure table for ragged rollups over
    # hierarchy_edges. Registered in the same change that creates the
    # suite; the inventory gate refuses a test_*.py on disk that SUITES
    # does not name.
    "test_bm_vault_closure.py",
    # VB13-05 (2026-08-30): the GLEIF-toyota real-business-data golden set
    # over hierarchy_edges, closure and crosswalk together. Registered in
    # the same change that creates the suite; the inventory gate refuses a
    # test_*.py on disk that SUITES does not name.
    "test_bm_vault_realdata.py",
    # VB11-01 (2026-08-30): lineage read, one origin answer over the
    # intake, events and citation seams, no new store. Registered in the
    # same change that creates the suite; the inventory gate refuses a
    "test_bm_vault_lineage.py",
    # VB3-13 (2026-08-31): derived memory cannot declassify. Registered in
    # the same change that creates the suite; the inventory gate refuses a
    # test_*.py on disk that SUITES does not name.
    "test_bm_vault_labels.py",
    # VB11-02 (2026-08-30): the retrieval contract page, docs/RETRIEVAL-RULES.md,
    # pinning six served-path rules to named existing tests, with a drift test
    # over the page itself. Registered in the same change that creates the
    # suite; the inventory gate refuses a test_*.py on disk that SUITES does
    # not name.
    "test_bm_retrieval_rules.py",
    # VB11-03 (2026-08-30): the retrieval enrichment pre-compute lane.
    # Registered in the same change that creates the suite; the inventory
    # gate refuses a test_*.py on disk that SUITES does not name.
    "test_bm_vault_enrich_index.py",
    # VB11-04 (2026-08-30): per-person daily digests built on
    # bm_vault_route's own routing lanes, the pending-work Bases view, and
    # the immediate-class bypass for revocations and security findings.
    "test_bm_vault_digest.py",
    # VB11-05 (2026-08-30): the approval pane, pending promotions and
    # curation accepts click-driven through the same posture
    # bm_vault_serve.py already proved. Registered in the same change that
    # creates the suite; the inventory gate refuses a test_*.py on disk
    # that SUITES does not name.
    "test_bm_vault_pane.py",
    # VB11-06 (2026-08-30): one renderer, three outputs (page, HTML email,
    # Teams Adaptive Card) from one facts dict, plus the email and
    # fixture-mode teams send adapters. Registered in the same change that
    # creates the suite; the inventory gate refuses a test_*.py on disk that
    # SUITES does not name.
    "test_bm_vault_notify.py",
    # VB13-05 (2026-08-30): the enrichment draft gate and golden-set evals.
    # Registered in the same change that creates the suite; the inventory
    # gate refuses a test_*.py on disk that SUITES does not name.
    "test_bm_vault_enrich_gate.py",
    # VB5-06 (2026-08-31): the warm embedder, a loopback daemon holding
    # bge-small in memory across calls, with bm_vault.py's dense path
    # falling back to the original subprocess when it is absent. Registered
    # in the same change that creates the suite; the inventory gate refuses
    # a test_*.py on disk that SUITES does not name.
    "test_bm_embed_warm.py",
    # VB3-05 (2026-08-31): subject-predicate assertions and scoped
    # CanonicalResolutions, institutional truth above the note layer.
    # Registered in the same change that creates the suite; the inventory
    # gate refuses a test_*.py on disk that SUITES does not name.
    "test_bm_vault_assertions.py",
    # VB3-17 (2026-08-31): opaque ids, tenant as a field, merge history as
    # events. Registered in the same change that creates the suite; the
    # inventory gate refuses a test_*.py on disk that SUITES does not name.
    "test_bm_vault_identity.py",
    # VB2-03 (2026-08-31): the Japanese-first analyzer seam, normalize,
    # segment, kana_alias, analyze, and the seam in bm_vault.py. Registered
    # in the same change that creates the suite; the inventory gate refuses
    # a test_*.py on disk that SUITES does not name.
    "test_bm_vault_analyzer.py",
    # VB2-03 (2026-08-31): the 245-case Japanese benchmark runner, per-class
    # floors measured against the shipped fixture. Registered in the same
    # change that creates the suite; the inventory gate refuses a test_*.py
    # on disk that SUITES does not name.
    "test_bm_vault_jbench.py",
    # VB2-08 (2026-08-31): entity disambiguation in bm_vault._search, the pass
    # that drops the wrong member of a near-duplicate entity pair after fusion.
    # Registered in the same change that creates the suite; the inventory gate
    # refuses a test_*.py on disk that SUITES does not name.
    "test_bm_vault_disambig.py",
)

# unittest writes its summary to stderr. Both shapes appear in real output:
#   "Ran 244 tests in 7.452s"
#   "OK (skipped=2)"  /  "FAILED (failures=1, errors=2)"
_RAN_RE = re.compile(r"^Ran (\d+) tests? in ([\d.]+)s", re.M)
_SKIP_RE = re.compile(r"skipped=(\d+)")
# One header per failing test, in unittest's own shape: "FAIL: test_x
# (module.Class)" or, from Python 3.11, "FAIL: test_x (module.Class.test_x)";
# ERROR: is the same shape for a test that raised. Reprinted under the
# FAILURES block so a saved battery log names WHICH tests failed and not only
# how many: an exception declared for a whole suite hid eight undeclared
# failures on 2026-09-03, because the tail above carries only the count.
_FAILED_TEST_RE = re.compile(r"^(?:FAIL|ERROR): \w+ \([\w.]+\).*$", re.M)


# -- interprocess gate lock -------------------------------------------------
#
# The lock is keyed to THIS checkout, not to the machine: two worktrees exercise
# their OWN copy of the tools, so they cannot corrupt each other and must not
# block each other either. It lives in the system temp directory rather than in
# the repo so it never shows up in git status.

LOCK_ENV = "BROTHERMODE_TEST_GATE_LOCK"
LOCK_TIMEOUT = float(os.environ.get("BROTHERMODE_TEST_LOCK_TIMEOUT", "900"))
# AGE IS THE FALLBACK, NOT THE TEST (fix round, 2026-07-29). This used to be
# 3600s and was consulted BEFORE the holder's liveness, so a run that had been
# going for an hour and one second was declared dead and had its lock taken
# from underneath it. 3600s was not even longer than a legitimate run: the
# default 900s timeout times four suites is exactly 3600s, and the CI gate job
# passes --timeout 1200, so up to 4800s. Now liveness decides wherever a pid
# can be probed, and this threshold only applies where it cannot (Windows, or
# an unparseable holder record). 24 hours is longer than any run that is not
# already abandoned.
LOCK_STALE_SECONDS = 86400.0

# Paths this process actually holds, mapped to the exact bytes it wrote. A
# release must never remove a lock file this process did not take: if its own
# lock was stolen while it ran, deleting the thief's file lets a THIRD run in.
_HELD = {}


class GateLockBusy(Exception):
    """Another gate run holds the lock and did not release it in time."""


def lock_path(tools_dir=None):
    """The lock file for the checkout whose tools directory is `tools_dir`,
    defaulting to this one. Parameterized (battery fence, 2026-08-17) so
    tools/bm_fence_hook.py can ask about the checkout it is GUARDING rather
    than the checkout it happens to be installed in. The key stays the tools
    directory, unchanged since the lock shipped, so runners and readers
    built at different times keep meeting the same path."""
    key = os.path.realpath(HERE if tools_dir is None else tools_dir)
    tag = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return os.path.join(tempfile.gettempdir(),
                        "brothermode-test-gate-%s.lock" % tag)


def _lock_holder(path):
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip() or "(holder record not yet written)"
    except (IOError, OSError):
        return "(holder record unreadable)"


def lock_state(path):
    """("absent" | "live" | "stale" | "unreadable", holder_text). The one
    classifier of the lock token; _lock_is_stale delegates here so exactly
    one reading of the format exists for the runner and for the fence hook
    that consumes it (battery fence, 2026-08-17).

    LIVENESS IS CHECKED FIRST, AND IT WINS. A process that answers
    os.kill(pid, 0) is running, and a running gate is not stale no matter how
    long it has been running. An unparseable or empty file is a lock being
    taken RIGHT NOW, not a dead one, so age decides for it. A file that
    EXISTS but cannot be read is "unreadable", which callers must report
    rather than swallow: it is never the same answer as absent."""
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except (IOError, OSError):
        if not os.path.exists(path):
            return "absent", ""
        return "unreadable", "(holder record unreadable)"
    fields = raw.split()
    holder = raw.strip() or "(holder record not yet written)"
    try:
        pid = int(fields[0])
        stamp = float(fields[1])
    except (IndexError, ValueError):
        try:
            old = (time.time() - os.path.getmtime(path)) > LOCK_STALE_SECONDS
        except OSError:
            return "absent", ""
        return ("stale" if old else "live"), holder
    if os.name == "posix" and pid > 0:
        try:
            os.kill(pid, 0)
        except OSError as exc:
            # ESRCH means no such process, which is the ONLY provably gone
            # case. EPERM means it exists and is not ours, which is alive.
            if exc.errno == errno.ESRCH:
                return "stale", holder
            return "live", holder
        # Alive. Not stale, however old. A slow gate is not a dead one.
        return "live", holder
    return ("stale" if (time.time() - stamp) > LOCK_STALE_SECONDS
            else "live"), holder


def _lock_is_stale(path):
    """True only when the holder is provably gone. Delegates to lock_state,
    which keeps the token's one classifier; see its docstring for the rules.
    An unreadable-but-present file is NOT stale, exactly as before."""
    return lock_state(path)[0] == "stale"


def acquire_gate_lock(timeout=None, owner="test_all", quiet=False,
                      tools_dir=None):
    """Take the checkout-wide test lock. Returns a handle, or None when an
    ancestor process already holds it (test_all running a suite as a child), in
    which case there is nothing to take and nothing to release. `tools_dir`
    keys the lock to another checkout, which only tests use: the runner
    always locks its own. The default path calls lock_path with NO argument
    rather than passing None through, because the P9 suite patches
    lock_path with a zero-argument stand-in to redirect the lock into a
    private directory, and that seam is part of the function's contract."""
    path = lock_path() if tools_dir is None else lock_path(tools_dir)
    inherited = os.environ.get(LOCK_ENV)
    if inherited:
        if inherited == path and os.path.exists(path):
            return None
        # Any other value cannot mean "an ancestor holds this checkout's
        # lock". Treating it as if it did turned one exported environment
        # variable into a silent, unannounced disabling of the only mutual
        # exclusion the suites have. Say so, then take the lock properly.
        if not quiet:
            sys.stderr.write(
                "test_all: ignoring %s=%r: it does not name this checkout's "
                "live gate lock (%s), so it cannot mean an ancestor holds it. "
                "Taking the lock normally.\n" % (LOCK_ENV, inherited, path))
    deadline = time.time() + (LOCK_TIMEOUT if timeout is None else timeout)
    announced = False
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
            if _lock_is_stale(path):
                if not quiet:
                    sys.stderr.write(
                        "test_all: removing a stale gate lock from a crashed "
                        "run: %s\n" % _lock_holder(path))
                try:
                    os.remove(path)
                except OSError:  # sbe: allow-silent best-effort removal of a lock already detected as stale; the loop retries regardless of whether the remove lands
                    pass
                continue
            if time.time() >= deadline:
                raise GateLockBusy(
                    "another test run holds %s (%s). The suites share this one "
                    "checkout and its temp and git state, so two at once "
                    "produce flakes nobody can reproduce. Wait for it, or "
                    "delete that file if you are sure the holder is dead."
                    % (path, _lock_holder(path)))
            if not announced and not quiet:
                sys.stderr.write(
                    "test_all: waiting for another test run to finish: %s\n"
                    % _lock_holder(path))
                announced = True
            time.sleep(0.25)
            continue
        token = "%d %.3f %s\n" % (os.getpid(), time.time(), owner)
        with os.fdopen(fd, "w") as fh:
            fh.write(token)
        _HELD[path] = token
        # Children inherit this, which is how a suite launched BY this runner
        # knows the lock is already held rather than deadlocking against it.
        os.environ[LOCK_ENV] = path
        return path


def release_gate_lock(handle, quiet=False):
    """Give up a lock THIS process took. Never removes a lock file whose
    contents are not the ones this process wrote: if the lock was stolen (a
    stale-detection mistake, or somebody deleting the file by hand), the file
    now on disk belongs to another run, and removing it would admit a third."""
    if not handle:
        return
    token = _HELD.pop(handle, None)
    if os.environ.get(LOCK_ENV) == handle:
        os.environ.pop(LOCK_ENV, None)
    if token is None:
        if not quiet:
            sys.stderr.write(
                "test_all: NOT removing %s: this process never took it.\n"
                % handle)
        return
    try:
        with io.open(handle, encoding="utf-8", errors="replace") as fh:
            current = fh.read()
    except (IOError, OSError):
        return
    if current != token:
        if not quiet:
            sys.stderr.write(
                "test_all: NOT removing %s: it is held by another run now "
                "(%s), so this run's lock was taken from it while it ran. "
                "Report this: it means two gates may have overlapped.\n"
                % (handle, current.strip()))
        return
    try:
        os.remove(handle)
    except OSError:  # sbe: allow-silent best-effort removal after this run's own ownership of the lock is already confirmed above
        pass


# -- CI inventory ------------------------------------------------------------
#
# WHY THIS IS NOT A regex OVER THE WHOLE FILE (fix round, 2026-07-29)
#   It used to be. re.findall over the raw workflow text answers "is this
#   filename MENTIONED", and the check needs "is this suite RUN". Four
#   mutations were reproduced against the old version and all four were
#   reported as agreement: commenting the step out, adding `if: false` to it,
#   replacing its command with `echo TODO <path>`, and (the reverse direction)
#   a prose comment naming a deleted suite, which hard blocked the whole gate
#   at exit 2 with zero tests run. The workflow already leaned on the
#   looseness: tools/test_all.py was matched from a comment.
#
#   So this reads the workflow as the small, specific structure GitHub Actions
#   actually uses (steps, each a mapping with `run:` and maybe `if:`) and
#   counts a suite only when a real shell command executes it through a Python
#   interpreter. It is deliberately NOT a general YAML parser: stdlib only, no
#   PyYAML, and a parser this file cannot fully implement is a parser nobody
#   should trust. Every place it gives up, it gives up by NOT counting the
#   suite as covered, which fails closed (the gate refuses) rather than open
#   (a suite silently untested).

_CI_SUITE_RE = re.compile(r"tools/(test_[A-Za-z0-9_]+\.py)")
# A python interpreter as the command word: python, python3, py, or a path
# ending in one of those.
_PYTHON_RE = re.compile(r"(?:^|[\s;&|(=])(?:[\w./\\:-]*[/\\])?py(?:thon3?)?"
                        r"(?:\.exe)?(?:\s|$)")
# Command separators inside one `run:` body. Each segment is judged on its own,
# so a suite named in a different command than the interpreter does not count.
_SEGMENT_RE = re.compile(r"[\n;]|&&|\|\||\|")
_FALSE_IF_RE = re.compile(
    r"^(?:false|'false'|\"false\"|\$\{\{\s*false\s*\}\})$", re.I)
# YAML block scalar headers: |, >, |-, >+, |2 and so on.
_BLOCK_RE = re.compile(r"^[|>][+-]?\d*$")


def _strip_comment(line):
    """Drop a trailing comment, respecting quotes. Serves double duty: a YAML
    comment outside a block scalar and a shell comment inside one both start at
    a '#' that begins the line or follows whitespace. Dropping either can only
    REMOVE a mention, never invent one, so the worst case is a refusal."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
    return line


def _ci_steps(text):
    """Every step block under a `steps:` key, as raw text. Comment-only lines
    are already gone, which is the point: a commented-out step is not a step."""
    blocks = []
    cur = None
    steps_indent = None
    for raw in text.splitlines():
        line = _strip_comment(raw.rstrip())
        if not line.strip():
            if cur is not None:
                cur.append(line)
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if steps_indent is not None:
            closed = indent < steps_indent or (
                indent == steps_indent and not stripped.startswith("-"))
            if closed:
                if cur is not None:
                    blocks.append("\n".join(cur))
                    cur = None
                steps_indent = None
        if stripped == "steps:":
            steps_indent = indent
            continue
        if steps_indent is None:
            continue
        if stripped == "-" or stripped.startswith("- "):
            if cur is not None:
                blocks.append("\n".join(cur))
            cur = [line]
        elif cur is not None:
            cur.append(line)
    if cur is not None:
        blocks.append("\n".join(cur))
    return blocks


def _step_field(block, key):
    """One top-level field of a step mapping, or None. Handles both an inline
    scalar (run: python3 x.py) and a block scalar (run: | plus indented lines).
    A nested mapping (a `with:` body) sits below the key indent and is
    therefore never mistaken for a sibling field."""
    lines = block.split("\n")
    first = lines[0]
    lead = len(first) - len(first.lstrip())
    key_indent = lead + 2
    head = first.strip()
    if head.startswith("- "):
        norm = [" " * key_indent + head[2:]]
    elif head == "-":
        norm = [""]
    else:
        norm = [first]
    norm.extend(lines[1:])
    for idx, line in enumerate(norm):
        if not line.strip():
            continue
        if (len(line) - len(line.lstrip())) != key_indent:
            continue
        stripped = line.strip()
        if not stripped.startswith(key + ":"):
            continue
        value = stripped[len(key) + 1:].strip()
        if value and not _BLOCK_RE.match(value):
            return value
        body = []
        for nxt in norm[idx + 1:]:
            if not nxt.strip():
                body.append("")
                continue
            if (len(nxt) - len(nxt.lstrip())) <= key_indent:
                break
            body.append(nxt.strip())
        return "\n".join(body)
    return None


def _ci_inventory():
    """Suites a CI step actually EXECUTES. None when there is no workflow file,
    which is the normal case for an extracted copy of the skill: an end user's
    checkout has no .github, and that must not be reported as a CI gap."""
    try:
        with io.open(WORKFLOW, encoding="utf-8") as fh:
            text = fh.read()
    except (IOError, OSError):
        return None
    executed = set()
    for block in _ci_steps(text):
        condition = _step_field(block, "if")
        if condition is not None and _FALSE_IF_RE.match(condition.strip()):
            # A step that can never run is not coverage. Any OTHER condition
            # (a platform guard, for instance) still counts: it runs on at
            # least one leg, which is more than zero.
            continue
        command = _step_field(block, "run")
        if not command:
            continue
        for segment in _SEGMENT_RE.split(command):
            if not _PYTHON_RE.search(segment):
                # `echo TODO tools/test_x.py` mentions a suite and runs
                # nothing. Only an interpreter invocation counts.
                continue
            executed.update(_CI_SUITE_RE.findall(segment))
    return executed


def _discover():
    """Every suite that exists on disk, so a NEW suite cannot be silently left
    out of the gate by whoever forgot to add it to SUITES. A file matching
    test_*.py that is not in SUITES is reported loudly rather than skipped: an
    unlisted suite is the exact failure this runner exists to prevent."""
    on_disk = sorted(
        n for n in os.listdir(HERE)
        if n.startswith("test_") and n.endswith(".py") and n != "test_all.py")
    known = list(SUITES)
    unlisted = [n for n in on_disk if n not in known]
    missing = [n for n in known if n not in on_disk]
    return known, unlisted, missing


def _child_env():
    """The environment each suite subprocess runs in: the runner's own
    environment minus BM_FENCE_MODE. ~/.claude/settings.json sets that
    variable machine-wide (a deliberate, separate decision this file does not
    touch), and a suite that reads it inherits whichever mode the machine
    happens to be in instead of the mode it is actually testing. Measured
    2026-09-02: with BM_FENCE_MODE inherited enforced, test_bm_fence_hook.py
    fails 16 tests and test_install.py fails 8; stripping it here drops
    test_bm_fence_hook.py to 0 failures and test_install.py to 6, the 6
    being a separate, pre-existing DoctorCase defect this file does not
    touch (present with BM_FENCE_MODE unset too). A suite that wants
    enforced mode sets it itself, inside the test, on top of this."""
    env = dict(os.environ)
    env.pop("BM_FENCE_MODE", None)
    return env


def _run_until_silent(path, silence_limit):
    """Run a suite, killing it only when it STOPS PRODUCING OUTPUT.

    CHANGED 2026-07-31, and the reason is the same one that produced two test
    fixes earlier the same day: a bare wall-clock budget measures the MACHINE,
    not the code. The old rule killed any suite whose TOTAL runtime passed the
    limit, so on a machine running six sessions a suite that normally takes 40
    to 90 seconds was killed at 907 seconds while its output showed tests still
    passing, on two different machines within an hour. A red that names no
    defect is worse than a slow green: it teaches the reader to re-run instead
    of to read.

    The distinction that fixes it: a HANG is a suite that stops progressing,
    and that is observable, while slowness is not a defect at all. unittest
    emits a line per test, so silence is the signal and elapsed time is noise.
    A genuinely wedged suite still trips this, at the same limit as before,
    because a wedged suite emits nothing.

    HOW THE LIMIT IS DERIVED, because the obvious next mistake is to lower it.
    Silence between lines is ONE TEST'S RUNTIME, and that is exactly as
    load-sensitive as the total was. Shortening this number reintroduces the
    same defect one level down: a test that takes 8s unloaded takes 400s at
    load 50, and a 60s or 120s limit would kill it and print STOPPED
    PROGRESSING just as wrongly as the old message did, only sooner and with
    more confidence.

    So the limit is: the slowest SINGLE test in the slowest suite, times the
    worst load multiple worth tolerating. Both halves are measured, not felt.
    Load multiples actually observed on 2026-07-31 with six sessions on one
    box: the store suite 12.4s to 584.5s (47x) and the docs suite 15s to 670.7s
    (45x). A 45x design margin is therefore ordinary here, not paranoid.

    900s is KEPT unchanged for that reason: it clears a 45x multiple on a 20s
    test. Nobody has yet measured the slowest single test, so nobody has earned
    the right to lower it. The cheap measurement when someone wants the real
    number: run one suite with -v on a QUIET machine and take the largest gap
    between consecutive test lines. That figure, times the multiple you choose
    on purpose, is the limit.

    Returns (ok, output, timed_out). Never raises for a suite that merely runs
    long. Reads incrementally on a thread so a suite writing a lot cannot fill
    the pipe buffer and deadlock, which plain Popen.wait() would allow.

    THE PUMP READS BY CHUNK, NOT BY LINE, AND THAT IS THE WHOLE POINT (SBE2,
    2026-08-16). It used to call readline, which returns nothing until a
    NEWLINE arrives. unittest at default verbosity writes ONE DOT PER TEST
    WITH NO NEWLINE, so a suite that was working perfectly delivered not one
    byte to this reader until its entire dot row finished. The silence timer
    therefore measured the whole suite as silent and killed any suite slower
    than the limit, reporting `FAIL 0 tests` for a suite making steady
    progress. That happened three times in one night and is why every long
    gate looked stalled to the person watching it.

    BOTH HALVES ARE REQUIRED, and finding that out cost two wrong fixes,
    which is worth recording because each looked sufficient alone:

      1. The reader must not wait for newlines. read(1) here, because a
         single byte is enough to prove the child is alive, and that is the
         only question the silence limit asks.
      2. The CHILD must not sit on those bytes. unittest writes its dots to
         stderr, and a Python child whose stderr is a pipe line-buffers it,
         so the dots never leave the child until a newline arrives. `-u`
         turns that off.

    Fixing only the reader still killed a healthy suite (measured: a three
    test suite emitting dots against a 3 second limit was killed with an
    empty capture). An earlier experiment had concluded `-u` changed
    nothing, and it was wrong for an instructive reason: that experiment
    measured through a LINE reader, which hid the difference `-u` makes. A
    measurement can only see what its instrument does not mask.

    The captured text is byte-identical to what readline produced, so the
    suite-line parser and the test-count extraction downstream are untouched;
    only the arrival TIME of each byte changes."""
    proc = subprocess.Popen(
        [sys.executable, "-u", path], cwd=HERE,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        universal_newlines=True, bufsize=1, env=_child_env())
    chunks = []
    last = [time.time()]

    def _pump():
        while True:
            piece = proc.stdout.read(1)
            if piece == "":
                break
            chunks.append(piece)
            last[0] = time.time()
        try:
            proc.stdout.close()
        except Exception:  # sbe: allow-silent best-effort close of the pump's stdout handle after the read loop already broke on EOF
            pass

    pump = threading.Thread(target=_pump)
    pump.daemon = True
    pump.start()
    timed_out = False
    while True:
        if proc.poll() is not None:
            break
        if time.time() - last[0] > silence_limit:
            timed_out = True
            proc.kill()
            break
        time.sleep(0.5)
    pump.join(timeout=10)
    try:
        proc.wait(timeout=10)
    except Exception:  # sbe: allow-silent best-effort final reap after proc.poll()/kill already determined returncode and timed_out above
        pass
    return (proc.returncode == 0 and not timed_out), "".join(chunks), timed_out


def _run_one(name, timeout=None):
    """Run one suite in its own process. Returns (ok, tests, skipped, seconds,
    tail, output). Never raises: a suite that cannot start at all, or hangs, is
    a RESULT reported with its reason, not an exception that hides the rest."""
    path = os.path.join(HERE, name)
    if timeout is None:
        timeout = DEFAULT_TIMEOUT
    started = time.time()
    try:
        ok_run, partial, timed_out = _run_until_silent(path, timeout)
    except OSError as exc:
        return (False, 0, 0, time.time() - started,
                "could not start: %s" % exc, "could not start: %s" % exc)
    if timed_out:
        # A hung suite used to hang the gate. Report it as a timeout, keep the
        # partial output (it names the last test that started, which is almost
        # always the one that wedged), and carry on with the other suites.
        elapsed = time.time() - started
        lines = [ln for ln in partial.splitlines() if ln.strip()]
        tail = ("STOPPED PROGRESSING: no output for %.0fs (limit %.0fs), killed "
                "after %.0fs total. Last output: %s"
                % (timeout, timeout, elapsed,
                   " | ".join(lines[-3:]) if lines else "(none)"))
        return False, 0, 0, elapsed, tail, partial + "\n" + tail + "\n"
    class _P(object):
        pass
    proc = _P()
    proc.stdout = partial
    proc.returncode = 0 if ok_run else 1
    elapsed = time.time() - started
    out = proc.stdout or ""
    m = _RAN_RE.search(out)
    tests = int(m.group(1)) if m else 0
    sm = _SKIP_RE.search(out)
    skipped = int(sm.group(1)) if sm else 0
    ok = proc.returncode == 0
    if ok and tests == 0:
        # A suite that exits 0 having run nothing is not a pass. This is the
        # exit-code-tests-pass-for-the-wrong-reason class from docs/knowledge/
        # LESSONS.md: an import error can exit 0 in some runners and would
        # otherwise be counted as green here.
        ok = False
        tail = "exited 0 but ran 0 tests (import error or empty suite?)"
    else:
        lines = [ln for ln in out.splitlines() if ln.strip()]
        tail = " | ".join(lines[-3:]) if lines else "(no output)"
    return ok, tests, skipped, elapsed, tail, out


def _battery_sha():
    """HEAD of this checkout, short, or "unknown". Fail-soft on purpose: the
    lock must still announce a running gate in a checkout where git cannot
    answer, and a missing sha is displayed as exactly that."""
    try:
        r = subprocess.run(
            ["git", "-C", REPO, "rev-parse", "--short=12", "HEAD"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=30,
            env={k: v for k, v in os.environ.items()
                 if not k.startswith("GIT_")})
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return (r.stdout.strip()
            if r.returncode == 0 and r.stdout.strip() else "unknown")


def _battery_session():
    """The session that started this gate, for ATTRIBUTION in the fence
    hook's refusal message, never for its decision: a same-session write
    mid-gate invalidates the run exactly as a foreign one does, so the
    battery fence exempts nobody. Env order matches tools/bm_handover.py."""
    for var in ("BM_FENCE_SESSION_ID", "CLAUDE_SESSION_ID"):
        v = os.environ.get(var, "").strip()
        if v:
            return v
    return "unrecorded"


def main(argv):
    if any(a in ("-h", "--help") for a in argv):
        sys.stdout.write(__doc__)
        return 0
    artifacts = None
    timeout = DEFAULT_TIMEOUT
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        if arg == "--artifacts":
            if not rest:
                sys.stderr.write("test_all: --artifacts needs a directory\n")
                return 2
            artifacts = rest.pop(0)
        elif arg == "--timeout":
            if not rest:
                sys.stderr.write("test_all: --timeout needs seconds\n")
                return 2
            try:
                timeout = float(rest.pop(0))
            except ValueError:
                sys.stderr.write("test_all: --timeout wants a number\n")
                return 2
        elif arg == "--check-only":
            # Inventory checks without running anything. For a CI leg that only
            # needs to prove local and CI agree.
            known, unlisted, missing = _discover()
            return _inventory_gate(known, unlisted, missing)
        else:
            sys.stderr.write(
                "test_all: unrecognized argument %s (recognized: --help, "
                "--artifacts DIR, --timeout SECONDS, --check-only)\n" % arg)
            return 2

    known, unlisted, missing = _discover()
    rc = _inventory_gate(known, unlisted, missing)
    if rc:
        return rc

    try:
        lock = acquire_gate_lock(owner="test_all pid %d sha %s session %s"
                                 % (os.getpid(), _battery_sha(),
                                    _battery_session()))
    except GateLockBusy as exc:
        sys.stderr.write("test_all: REFUSING to run. %s\n" % exc)
        return 2
    try:
        return _run_all(known, artifacts, timeout)
    finally:
        release_gate_lock(lock)


def _worktree_dirt():
    """Tracked paths the suites modified, as (paths, skip_reason). Exactly one
    of the two is ever truthy: a reason means the question could not be asked,
    which is never the same answer as "nothing changed"."""
    if not os.path.isdir(os.path.join(REPO, ".git")):
        return [], "this checkout has no .git directory"
    try:
        r = subprocess.run(
            ["git", "-C", REPO, "status", "--porcelain", "--untracked-files=no"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=30,
            # git honours GIT_DIR and GIT_WORK_TREE over -C, so an inherited
            # pair would have this report another repository's tree.
            env={k: v for k, v in os.environ.items()
                 if not k.startswith("GIT_")})
    except (OSError, subprocess.SubprocessError) as exc:
        return [], "git could not be run here (%s)" % exc
    if r.returncode != 0:
        return [], "git status exited %d here" % r.returncode
    return sorted(ln[3:].strip() for ln in r.stdout.splitlines() if ln.strip()), ""


def _inventory_gate(known, unlisted, missing):
    """Every refusal that must fire before a single test runs. Returns 0 or 2."""
    if unlisted:
        sys.stderr.write(
            "test_all: REFUSING to run. These suites exist on disk but are not "
            "in the gate: %s. Add them to SUITES in tools/test_all.py so the "
            "gate covers them, then re-run.\n" % ", ".join(unlisted))
        return 2
    if missing:
        sys.stderr.write(
            "test_all: REFUSING to run. These suites are in the gate but not on "
            "disk: %s. Either restore them or remove them from SUITES.\n"
            % ", ".join(missing))
        return 2
    ci = _ci_inventory()
    if ci is None:
        sys.stdout.write(
            "test_all: no %s in this checkout, so the CI inventory check is "
            "SKIPPED (normal for an extracted copy).\n"
            % os.path.relpath(WORKFLOW, REPO))
        return 0
    absent = [n for n in known if n not in ci]
    if absent:
        sys.stderr.write(
            "test_all: REFUSING to run. These suites are in the local gate but "
            "are never run by CI: %s. Add a step for each to %s, or the same "
            "claim is tested on your machine and nowhere else.\n"
            % (", ".join(absent), os.path.relpath(WORKFLOW, REPO)))
        return 2
    phantom = sorted(n for n in ci
                     if n != "test_all.py" and n not in known)
    if phantom:
        sys.stderr.write(
            "test_all: REFUSING to run. CI runs suites that this gate does not "
            "know about: %s. Either add them to SUITES or remove them from %s.\n"
            % (", ".join(phantom), os.path.relpath(WORKFLOW, REPO)))
        return 2
    return 0


def _write_artifact(artifacts, name, body):
    """Full output to disk so CI can upload it on failure. Best effort by
    design: losing a log must never turn a green gate red or a red one green."""
    try:
        if not os.path.isdir(artifacts):
            os.makedirs(artifacts)
        with io.open(os.path.join(artifacts, name), "w", encoding="utf-8") as fh:
            fh.write(body)
    except (IOError, OSError) as exc:
        sys.stderr.write("test_all: could not write artifact %s: %s\n"
                         % (name, exc))


GATE_RECEIPT_RELPATH = os.path.join(".brothermode", "gate-receipt.json")


def _gate_receipt_path(repo):
    return os.path.join(repo, GATE_RECEIPT_RELPATH)


def write_gate_receipt(repo, results, suites_total, wall, exit_code):
    """Write the machine-readable gate receipt (RF-5) so pages, ledgers, and
    handovers read a fact instead of hand-quoting the summary line beside a
    hand-copied SHA. Written to repo/.brothermode/gate-receipt.json, which is
    gitignored: the receipt must never land in the tracked tree, because a
    dirty tracked tree is exactly what _worktree_dirt above refuses the gate
    over.

    FAILS OPEN, on purpose: git missing, a repo that is not a checkout, or a
    full disk must warn on stderr and return, never raise and never change
    the gate's exit code or verdict. The receipt is a convenience the
    verdict must not depend on. Any failure also means NO receipt is
    written at all: a partial or wrong receipt is worse than none, because a
    reader trusts the file's shape without re-deriving the numbers."""
    path = _gate_receipt_path(repo)
    try:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        sha_r = subprocess.run(
            ["git", "-C", repo, "rev-parse", "HEAD"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=30, env=env)
        if sha_r.returncode != 0:
            raise RuntimeError("git rev-parse HEAD exited %d: %s"
                               % (sha_r.returncode, sha_r.stderr.strip()))
        status_r = subprocess.run(
            ["git", "-C", repo, "status", "--porcelain"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=30, env=env)
        if status_r.returncode != 0:
            raise RuntimeError("git status --porcelain exited %d: %s"
                               % (status_r.returncode, status_r.stderr.strip()))
        receipt = {
            "sha": sha_r.stdout.strip(),
            "tree_dirty": bool(status_r.stdout.strip()),
            "tests_total": sum(r[2] for r in results),
            "suites_total": suites_total,
            "skipped": sum(r[3] for r in results),
            "wall_seconds": round(wall, 3),
            "exit_code": exit_code,
            "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        out_dir = os.path.dirname(path)
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        tmp = path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
    except Exception as exc:
        sys.stderr.write(
            "test_all: could not write the gate receipt (%s): %s. The "
            "gate's verdict is unaffected.\n" % (path, exc))


def _run_all(known, artifacts, timeout):
    sys.stdout.write(
        "test_all: %d suites, serially, one process each, %.0fs timeout each\n\n"
        % (len(known), timeout))
    results = []
    failed_tests = {}
    total_tests = total_skipped = 0
    wall = time.time()
    for name in known:
        sys.stdout.write("  running %-24s " % name)
        sys.stdout.flush()
        ok, tests, skipped, elapsed, tail, out = _run_one(name, timeout)
        results.append((name, ok, tests, skipped, elapsed, tail))
        if not ok:
            # A suite run as a script reports its tests under __main__; name
            # the suite instead, so the line says which file the test is in.
            failed_tests[name] = [
                ln.replace("(__main__.", "(%s." % name[:-3], 1)
                for ln in _FAILED_TEST_RE.findall(out)]
        total_tests += tests
        total_skipped += skipped
        if artifacts:
            _write_artifact(artifacts, name + ".log", out)
        sys.stdout.write("%s  %3d tests  %5.1fs\n"
                         % ("OK  " if ok else "FAIL", tests, elapsed))
        sys.stdout.flush()
    wall = time.time() - wall

    failed = [r for r in results if not r[1]]
    sys.stdout.write("\n")
    if failed:
        sys.stdout.write("FAILURES (%d of %d suites):\n" % (len(failed), len(known)))
        for name, _ok, _t, _s, _e, tail in failed:
            sys.stdout.write("  %s: %s\n" % (name, tail))
            for line in failed_tests.get(name, ()):
                sys.stdout.write("    %s\n" % line)
        sys.stdout.write("\n")
    dirty, dirt_skip = _worktree_dirt()
    if dirt_skip:
        sys.stdout.write("test_all: the clean-checkout check is SKIPPED, %s. A "
                         "SKIP is not a pass: nothing here proves the suites "
                         "left this tree alone.\n" % dirt_skip)
    elif dirty:
        failed = failed + [("clean-checkout", False, 0, 0, 0.0,
                            "the suites modified tracked files")]
        sys.stdout.write(
            "test_all: REFUSING to report green. The suites left these tracked "
            "paths modified: %s. A suite that writes into this checkout "
            "invalidates CHECKSUMS.sha256, so scripts/verify-install.sh then "
            "tells the user their install may be tampered with, and "
            "scripts/doctor.py SKIPS its integrity check on any dirty tree, "
            "which is the documented quickstart order. Write the artifact "
            "outside the tree, or behind an opt-in environment variable, the "
            "way tools/test_bm_controller.py does for the E4 evidence.\n"
            % ", ".join(dirty))
    summary = (
        "test_all: %d tests across %d suites, %d skipped, %.1fs wall. %s\n"
        % (total_tests, len(known), total_skipped, wall,
           "ALL GREEN" if not failed else "%d SUITE(S) FAILED" % len(failed)))
    sys.stdout.write(summary)
    if artifacts:
        _write_artifact(artifacts, "summary.txt", "".join(
            ["%s  %s  %d tests  %d skipped  %.1fs  %s\n"
             % (("OK" if ok else "FAIL"), name, tests, skipped, elapsed, tail)
             for name, ok, tests, skipped, elapsed, tail in results]) + summary)
    verdict = 1 if failed else 0
    write_gate_receipt(REPO, results, len(known), wall, verdict)
    return verdict


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
