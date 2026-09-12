# Delegate without giving up control

The goal is not to turn every approval off. It is to delegate a bounded task whose result and failure modes you can inspect without watching every step.

## Start with one task you can judge

Use a clean, disposable repository first. Choose a behavior with a known expected result, such as rejecting invalid input while preserving valid input. Avoid production credentials, deployments, payments, and destructive data operations in your first trial.

Write down:

- the outcome and what must remain unchanged;
- the files the agent may change;
- an independently understandable deciding check;
- what requires your decision, including any wider scope or release;
- how you will inspect and recover the work if the session stops.

Ask Brother to show that scope and evidence plan before execution. On Claude Code, use `/brother` followed by the outcome. On Codex, invoke the installed Brother skill with the same request; do not translate it into a slash command.

## Check the actual safety mode

Installing a skill is not proof that hooks are active. Read the run's `managed execution safety` line and any capability remedy. If it says `not enforced`, do not interpret the run as an enforced unattended sandbox.

The current engine refuses an explicit A0 higher-autonomy request when its capability floor cannot support A0. Other modes can continue with a disclosed lower protection level. That is a real difference, not a warning to click past. See [the enforcement matrix](../reference/safety-boundaries.md).

Keep host permissions and credentials restrictive. A Git worktree isolates repository changes; it does not isolate the machine, network, or accounts. A shell check is executable code. Inspect unfamiliar commands before granting them access.

## Inspect the first return before increasing scope

Read the final receipt path. Inspect changed files, exact commands, exit codes, check authorship, unresolved findings, and `NO-DATA`. Rerun the deciding checks in the stated working directory. Confirm that the expected result comes from your requirement, not merely from what the implementation currently does.

Record how often you intervened and how long acceptance took. A quiet terminal or long run does not establish useful autonomy. Repeat on representative tasks before increasing scope. The [Safe Unwatched Time protocol](../../benchmarks/SAFE-UNWATCHED-TIME.md) currently lacks intervention instrumentation, so its duration is an upper bound.

## Stop or narrow the delegation when

- a required control is missing or untrusted;
- the expected behavior remains ambiguous;
- the work asks to expand permissions, touch excluded files, or weaken checks;
- a required outcome has no supporting evidence;
- the decision depends on production facts the environment cannot observe.

Missing evidence is not automatically a global hard block in Brother. It is your reason not to accept a claim that requires that evidence. [Recover the work](recover-from-failure.md) instead of turning an unknown into a pass.

## Keep the lesson

Save reusable constraints and failure symptoms in the [Vault](use-the-vault.md), with the evidence and conditions under which they apply. On the next task, recheck recalled advice against the current repository. More memory is useful only when it changes the right decision.
