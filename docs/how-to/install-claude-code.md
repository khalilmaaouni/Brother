# Install Brother on Claude Code

Use this guide to install the public Brother plugin and confirm the one-door experience works.

## Prerequisites

Claude Code authenticated; `git` and `python3`; a low-risk/throwaway repository for smoke testing.

## Install

```bash
claude plugin marketplace add khalilmaaouni/Brother && claude plugin install brother@brother
```

Use lowercase marketplace name `brother` for update/remove operations.

## Smoke the door

Open a repository. With no unfinished work, Brother should ask what you are trying to do, not present internal products as a menu. Choose a small outcome whose expected behavior you understand.

## Verify the result

Confirm the door is reachable, unfinished-work discovery makes sense, hook scope matches your install choice, a small nontrivial run can produce a receipt, and you can explain what that receipt did and did not prove.

Read [Hook scope](../reference/hooks.md) before broad/team installation.

## Upgrade

```bash
claude plugin marketplace update brother && claude plugin update brother@brother
```

## Uninstall

```bash
claude plugin uninstall brother@brother && claude plugin marketplace remove brother
```
