# Known-mistakes ledger (never repeat these)

LOAD WHEN: before repeating a pattern that has failed before, such as dispatching writers, resuming after a kill, or running a build.

This file is the LIVE home of the ledger. It began as an extract of SKILL.md's
section 13, and that section no longer exists: the core was cut down and its
routing table now points here instead. Corrected 2026-08-02, when the stale
provenance line was found during a session whose whole subject was recording
its own errors.

## The per-incident record lives in docs/mistakes/

Sixteen incidents from the 2026-08-05 run are written up one file each, with
their evidence, their fix, and the rule each produces, at `docs/mistakes/`
(imported 2026-08-06). Read that directory before working in an area it names.
This page holds the rules; that directory holds the incidents that earned them,
and a rule whose incident has been forgotten becomes ceremony.

Two of those sixteen produced standing steps that now belong to EVERY loop:

- DELTAS AT CLOSE. A worker that finds an obligation in a file it may not edit
  writes the exact change down and stops, which is correct. The orchestrator
  then collects every such recorded delta from every worker report and closes
  each one BY NAME before the loop is called finished. M16 is the case where
  that handoff dropped silently and the obligation sat open at HEAD.
- REFUTE THE ACCOUNT, not only the code. Point an adversary at the session's
  own CLAIMS as well as its work. One hour of this on 2026-08-06 found an
  unpushed security fix nobody knew about, a broken documented procedure, a
  wrong count, a stale quoted result, and a commit hash in a founder report
  that resolved nowhere. None was a defect in what shipped; every one was a
  defect in what was said about it.

## The ledger (never repeat these)
- A GATE RUN ON A MOVING TREE IS NOT A RESULT. A suite claims the whole tree for
  its run: if any file lands while it runs, its verdict describes a tree that no
  longer exists, and it still prints OK. State the load average and the timing in
  the same sentence as the result. Two runs were wasted this way in one night.
- Two writers in one tree collide: fence first, dispatch second.
- Session limits kill agents mid-flight: edits survive; message-resume by id works
  and is first choice (proven repeatedly); when resume fails, the TREE is the truth:
  diff the fence set, distill into STATE.md, relaunch fresh from there; never
  duplicate a possibly-live writer.
- Scratchpads are wiped: durable path under home the moment a deliverable exists;
  and because disk-first is prose the DYING context cannot be trusted to run, a
  PreCompact hook (tools/bm_autosave.py) snapshots the whole tree, untracked files
  included, to refs/brothermode/autosave (local git only, never pushed) at the
  token-death moment, and `bm_autosave.py recover` restores it. A resumed session gets the THREAD back too: a PreCompact brief
  (bm_telemetry.py precompact-brief) distills the dying transcript tail, and a
  write-ahead intent line (bm_telemetry.py intent) logged BEFORE a risky action
  means death leaves a forward-looking record, not just files.
- Compilers catch what reading misses: build after every edit, even one line.
- This machine's disk fills mid-build: clear DerivedData and stale simulators before
  large builds; never let ENOSPC kill a gate run.
- Simulator limits exist (CoreAnimation video compositing crashes in sim): verify at
  the reachable layer and name the device-only remainder.
- Paths, flags, API names, and column names are never typed from memory.
- A disproven plan assumption stops the plan: rewrite before more code.
- Generated files are never hand-edited; edit sources and regenerate.
- A headline number shown before its independent second check is not a result.
- Filed models drift from their formulas: recompute totals from components.
- Refuted and by-design findings are settled: check the ledger before reopening.
- Batch scripts log each move AT the move, never at script end (a crash orphaned 5
  unlogged moves); dedup hashing skips symlinks (a link got crowned over its target).

- Structured-output caps guessed too tight lose whole agents, and the loss is
  silent until the run dies: size them generously (an unused allowance costs
  nothing, a tight one costs the answer), give a FAILING agent its own relaxed
  schema rather than editing the shared one on resume (a shared-schema edit
  changes every cache key and re-runs work that already succeeded), and never
  let one agent's schema failure kill a fan-out its synthesis depends on. Cost
  when learned: four agents across two runs in one session, 1.3M tokens partly
  re-spent. Evidence: docs/closure/evidence/2026-08-02-wastage-and-errors.md.
- A gate run started while a fleet is in flight measures the machine, not the
  code, and dies at EXIT=143 (SIGTERM, not a test failure): check the suite lock
  AND the load AND what agents are running before starting a suite. Read the
  exit code before calling a gate red.
- Before "fixing" something that looks obviously broken, grep the requirements
  and design records for whether it is deliberate. A true fact with a wrong
  consequence bolted onto it is still a wrong finding (a .gitignore "fix" that
  contradicted requirement R-06, shipped and reverted the same hour).
- When eliminating yourself as the holder of a shared resource, cite the RECEIPT
  in your own transcript, never a probe of the resource. A refusal is one sample
  at one instant; a grant is a recorded fact. Three sessions once triangulated
  onto a wrong conclusion from one unverified premise and sent the founder a
  false alarm.
- A tag that resolves is not a tag whose tree contains what the docs promise:
  check the CONTENTS with git ls-tree, not the ref.
- Documentation suites scan the WORKING TREE, not only tracked files: an
  untracked dated draft sitting in the tree will fail a gate that has nothing to
  do with your change. Run git status before the gate, and preserve rather than
  delete whatever it finds.
- Do NOT implement from a probe's findings while a design for the same item is
  still in flight: implement, then reconcile, and expect the design to be
  better. Shipping first cost a security fix that leaked absolute paths into a
  model-visible deny reason and refused writes in every directory that was not
  a project, with a TEST asserting the wrong behaviour, which is worse than the
  bug. Two rules fell out of it: an error channel a HUMAN reads may name paths,
  an error channel a MODEL reads may not, because nothing is verified at the
  moment a refusal is produced; and when adding a mode that can refuse, the
  case with nothing to enforce must still allow, or the switch bricks every
  unrelated directory the moment it is exported.
- A new failure code, kind or branch gets a DEFAULT that fails safe, so a later
  addition that forgets to name one is refused rather than silently waved
  through the hole the mechanism was audited for.
- A GUI button's label is a SAMPLE, not a property: the app can re-render between
  the screenshot and the click. Re-read the control in the same call that clicks
  it, or click something inert first. Learned by pressing what a screenshot
  showed as "Fetch origin" and what had become "Push origin" by the time the
  click landed. The push was correct and wanted, which is the only reason this
  is a near-miss rather than an incident; the same misread on a Merge or a
  Force-push button is not recoverable.
- A CI result belongs to ONE COMMIT. Quoting a green run beside a newer SHA
  borrows an older commit's evidence for code it never tested, and it reads as
  verification to anyone who trusts the sentence. Name the commit next to the
  verdict, and if the current one has not finished, say "in progress" rather
  than reaching back one commit for a number that looks better.
- A PROBE THAT CANNOT REACH THE DEFECT MEASURES NOTHING, and its silence reads
  exactly like an all-clear. C-11's adversarial test was written, measured 4.0x
  where a quadratic gives about 16x, and was withdrawn with a note concluding
  the pattern "is not quadratic on a run of one repeated character". The
  pattern was quadratic. The probe text was a run of LETTERS, and the pattern
  opens with (?<![A-Za-z0-9]), so every offset inside that run was rejected
  before any backtracking could happen: two starting positions in the whole
  string, linear by construction, and no input of that shape could ever have
  shown the defect. A run of UNDERSCORES clears that lookbehind at every offset
  and reproduces at 15.8x. Before concluding "the defect is not there", show
  that the probe can detect the defect WHEN IT IS there. A negative result from
  an instrument of unproven sensitivity is NO-DATA, never a finding, and
  writing it down as a finding is how a real defect gets an alibi.
- HOME ISOLATION IS NOT VAULT ISOLATION. A verification probe ran with HOME
  overridden to a throwaway directory and still wrote a synthetic row into the
  operator's real memory vault, because BROTHERMODE_VAULT was exported ambient in
  that shell and takes precedence over HOME. Overriding the variable a tool
  USUALLY resolves through does not contain a tool that resolves through a
  second one first. Any probe that could reach a ledger, a store or a vault
  pins EVERY variable that can redirect it (HOME, BROTHERMODE_VAULT,
  BROTHERSBE_VAULT) and then asserts the real target is untouched afterwards,
  rather than inferring containment from the one override it set.
