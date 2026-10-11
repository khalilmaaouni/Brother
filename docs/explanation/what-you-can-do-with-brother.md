# What you can do with Brother

Brother helps you hand over work without handing over your judgment. You
describe the result you want, set the boundaries, and return to a record of
what changed, what was checked, and what remains unknown.

## Start a useful task

You can ask Brother to make a small change whose result you can recognize. For
example, you can ask it to reject bad input while keeping good input working,
then show the check that proves both parts.

### How to use it

Type `/brother make the input reject bad values, keep good values working, and show the deciding check`.

### What it will not do

It will not turn an unclear request into a trustworthy result. You still need
to say what should change and what must stay the same.

## Keep work inside a boundary

You can name the files and actions that are in scope before work begins. This
is useful when a change sits beside configuration, generated files, or other
work you did not ask to touch.

### How to use it

Type `/brother update the pricing rule, change only the rule and its check, and leave generated files alone`.

### What it will not do

The boundary depends on the controls that are active in the current setup. A
worktree separates repository changes, but it does not isolate the computer,
network, or accounts.

## Stop, redirect, or resume work

You can stop a run that is taking the wrong path, give a new instruction, and
continue from the saved outcome later. This is useful when a long task hits a
known problem and you want to preserve the useful progress instead of starting
over.

### How to use it

Type `/brother stop the current run`. Later, type `/brother resume the unfinished work` in the same repository.

### What it will not do

It cannot recover evidence that was never saved, and a resumed task is not
proof that the original problem was fixed. Read the new receipt and check the
original requirement again.

## Work safely around changes already in progress

You can work in a repository that contains unrelated edits and ask Brother to
preserve them through ordinary branch or rebase work. You can also scope a
change in a large repository and keep hand-written work separate from
generated output.

### How to use it

Type `/brother change the login message, preserve unrelated edits, and update only the hand-written source and its check`.

### What it will not do

It will not decide that an unrelated edit is safe to overwrite. It also does
not provide an interactive editor for jumping to definitions, renaming a
symbol, or accepting arbitrary pieces of a diff.

## Make a plan you can inspect

You can turn a broad request into a short outcome, allowed changes, and checks
that must be run. This helps when several pieces need to happen in order, or
when someone else must review the plan before work starts.

### How to use it

Type `/brother write a plan for the import change, with the result, allowed files, order, and checks before editing`.

### What it will not do

A plan is not evidence that the result works. A check that already passed or
does not distinguish the requested behavior is not proof.

## Leave a task running with a morning handoff

You can arrange a bounded run that records completed work, unfinished work,
failures, decisions, and missing evidence for review later. This is useful for
a long maintenance task that should stop at a known time instead of running
without a clear owner.

### How to use it

Say: `run this bounded maintenance task until the agreed stop time and leave a morning handoff`.

### What it will not do

The control plane is not an operating system sandbox and it does not make
destructive decisions on its own. A live process, a promising answer, or an
empty response is not completion evidence.

## Know what was actually checked

You can open a delivery receipt and see the changed files, exact commands,
results, check authorship, and unresolved `NO-DATA`. This makes it easier to
tell the difference between a check that passed and a check that never ran or
could not establish the claim.

### How to use it

Type `/brother review this change before I accept it`.

### What it will not do

A receipt cannot make a weak check sufficient. Acceptance and release remain
your decisions.

## Protect risky engineering changes

You can ask Brother to examine a migration, sensitive backend change,
production fix, or other risky change against the evidence it needs. It can
keep forward and recovery evidence, approval, executed checks, numbers, and
stated behavior separate.

### How to use it

Type `/brother review this migration before I accept it, including recovery evidence and the affected consumers`.

### What it will not do

It does not merge, rebase, push, deploy, or replace engineering, security,
data, or quality review. `NO-DATA` means the evidence is missing, not that the
change is safe.

## Check a number before it guides a decision

You can ask for a customer list, metric, forecast, experiment result, or other
important figure to be defined before anyone relies on it. Brother can require
an independent calculation, boundary checks, and a record of the source and
data identity.

### How to use it

Type `/brother verify this number before it guides the decision, including an independent calculation and its data identity`.

### What it will not do

A successful query is not independent proof. Brother cannot repair missing
data access, unclear business definitions, or a denominator nobody agreed on.

## Verify native build and test evidence

For the complete mobile capability map, see [Mobile development and assurance with Brother](mobile-development-and-assurance.md).

You can record that a native build or test ran against the intended source and
result bundle. This is useful when a captured screen or test result must be
handed to a reviewer with its hashes and command history.

### How to use it

Run `python3 scripts/native_evidence.py validate --evidence evidence.json` after recording the evidence.

### What it will not do

The record proves integrity, not application quality, accessibility, visual
meaning, or human acceptance. An unavailable device or other missing
requirement remains `NO-DATA`.

## Keep useful lessons for the next task

You can save a failure, constraint, or decision as a searchable local lesson.
Later, Brother can recall a relevant note near the moment it matters, such as
when a retry can duplicate an action.

### How to use it

Run `python3 products/brothermode/tools/bm_vault.py recall --query "retry timeout duplicate charge" --limit 3 --fast --explain`.

### What it will not do

Memory is context, not authority or current proof. Do not store credentials,
secrets, or raw customer data, and recheck every recalled lesson against the
current work.

## Prepare a release decision

You can ask Brother to gather the evidence for a release and show the known
failures and unknowns before anything is published. This helps separate a
completed delivery from the separate decision to release it.

### How to use it

Say: `is this release ready?`

### What it will not do

It does not make organizational release decisions or prove what will happen
after release. A person must decide what risks to accept and how to respond to
what happens in production.

## Help a team adopt a shared practice

You can start with one expensive evidence problem, agree who accepts work,
define the minimum evidence, and preserve the team's existing checks. This is
useful when different people need the same handoff without forcing every task
through the same amount of ceremony.

### How to use it

Type `/brother adopt this workflow for our team, starting with one risky change and clear acceptance authority`.

### What it will not do

It does not invent authority or replace the team's tools. Adoption is only
useful when reviewers measure what they still had to reconstruct.

## Check a decision-grade claim

You can give the claim checker a structured claim, have it re-run the
derivations and see `PASS`, `FAIL`, or `NO-DATA` for the evidence gates. This is
for a number that reaches a decision, not for ordinary project status.

### How to use it

From the `products/brotherds` folder, run `python3 bds.py check examples/example-descriptive.json`, then `python3 bds.py receipt examples/example-descriptive.json`.

### What it will not do

Claim verification is experimental and outside the standard bundle. It does
not make a claim true because its format is valid, and `NO-DATA` is never a
pass.

## Use Jev as a calibrated second opinion

Jev can give a typed second opinion when a normal Brother check has a narrow
question. For example, it can flag a missing constraint in a draft before
dispatch, catch a worker's unsupported done claim before release, or rerank a
recalled Vault note so the most useful lesson is easier to see.

### How to use it

Jev is enabled through a selected entry in the `modes` object. Every check is
switched off by default. Read [how to use calibrated decisions](../how-to/use-calibrated-decisions.md) before enabling one.

### What it will not do

Jev does not write code, replies, or explanations, and it is not an agent. It
does not do code generation, arithmetic, counting, date calculations, subtle
test-coverage judgment alone, or act as the sole security gate. The public copy
ships the code without the registry file, so these checks cannot run from a
public install yet. When it cannot provide a usable result, the answer is
`NO-DATA`, and the normal check remains in control.
