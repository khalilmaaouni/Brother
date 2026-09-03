---
name: adopt
description: "Use when someone is adding risk and evidence checks to a repository that has never had them, or is checking whether an existing installation is actually wired up. Inspects the repository for readiness, proposes a configuration, and reports what is present, what is missing and what it could not check. Dry run by default. Invoke as /brothersbe:adopt."
---

# Adopt

Dry run by default. This skill proposes and reports; it does not silently change a repository
it was pointed at.

Read `${CLAUDE_PLUGIN_ROOT}/SKILL.md` first, then `${CLAUDE_PLUGIN_ROOT}/docs/SETUP.md`.

## Run the proposal first

Before inspecting anything by hand, run:

```
"${CLAUDE_PLUGIN_ROOT}/bin/sbe" adopt .
```

This is a dry run by default: it detects the stack and proposes the configuration without
changing the repository. Read its output before doing anything else, it is the primary
source the rest of this skill builds on. Reserve `--apply` for when the user explicitly asks
to install the proposed configuration.

## What to inspect, in this order

1. **Stack**: languages present, test runner, migration tool, CI system, whether a data
   transformation layer exists. Detected from files, not asked.
2. **Wiring**: is the plugin installed and enabled; do the hooks resolve; is there a CI
   workflow that runs the gates on the merge path, or only beside it.
3. **Dossier**: does a design dossier exist, and does the tier it declares match the artifacts
   present.
4. **Protection**: can the workflow file, the hooks, or the policy be edited without review.
   This is the control everything else rests on: a gate that anyone can delete is a
   suggestion.

## Verify the installation mechanically

```
sh   "${CLAUDE_PLUGIN_ROOT}/scripts/verify-install.sh"
"${CLAUDE_PLUGIN_ROOT}/bin/sbe" fences .
claude plugin validate "${CLAUDE_PLUGIN_ROOT}"
```

The middle command prints the live fences the hook would actually enforce and names anything
it could not read, which is the honest way to find out whether the write fence is real in this
checkout or only documented.

## Report honestly, including the parts you could not reach

Three buckets, never two: **present**, **missing**, and **not checkable from here**. Branch
protection, required status checks, CODEOWNERS enforcement and reviewer group membership live
on the code review platform, and reading them needs credentials this skill does not have and
must never ask for. Report them as unchecked with the reason, and give the operator the exact
thing to look at. A report that quietly omits what it could not verify is the failure mode
this whole project exists to prevent.
