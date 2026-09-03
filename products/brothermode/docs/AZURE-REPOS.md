Status: CURRENT, and DORMANT by founder direction: hours after this page
landed, the target was constrained to GitHub or Bitbucket
(PRODUCT-DIRECTION.md amendment 2026-08-16, "the Azure distraction
removed") and no further Azure work is scheduled. The page stays because
what it records was executed and stays true; nothing below is a promise
of coming work. Flip condition: the founder naming a client on Azure.

# Using BrotherMode with Azure Repos

Written under the original tri-host order of 2026-08-16, superseded the
same day by the two-host law in CLAUDE.md: GitHub canonical, Bitbucket
for the adopter team. BrotherMode's engine speaks plain
git and calls no host API, so the enforcement layer runs identically on an
Azure remote. This page follows docs/BITBUCKET.md's pattern exactly: what
was executed is quoted, and what nobody has executed is labeled UNVERIFIED
rather than left looking checked.

## What already works, with the proving command

Executed on this machine on 2026-08-16, anonymously, against Microsoft's
own public mirror:

    GIT_TERMINAL_PROMPT=0 git ls-remote https://dev.azure.com/git-for-windows/git/_git/git HEAD
    15c6308cf7ad276b306aa5b3ababfbdebfb1a917  HEAD

Every release-truth and remote-verification check in this repository is
built on git ls-remote and friends, so they run against Azure unchanged.

## Remote formats

- https: `https://dev.azure.com/<organization>/<project>/_git/<repository>`
  (the shape the executed command above proves)
- ssh: `git@ssh.dev.azure.com:v3/<organization>/<project>/<repository>`
  per Microsoft's documentation; not exercised here (needs a registered
  key), UNVERIFIED on this machine for that reason.

## Installing from an Azure mirror

Claude Code's marketplace command accepts full git URLs, and its own
documentation names Azure DevOps URL support explicitly (research pass
2026-08-16 against code.claude.com/docs/en/plugin-marketplaces). So:

```bash
claude plugin marketplace add https://dev.azure.com/<organization>/<project>/_git/BrotherModeUp
```

UNVERIFIED end to end: no Azure mirror of this repository exists, so the
command has never been executed against one, same label and same closing
condition as the Bitbucket page: the founder creates the project (an
account step), one session pushes the release and quotes the output here.

## Pull requests

Two paths, both requiring an Azure DevOps account:

1. The az CLI (the devops extension): `az repos pr create` with the
   repository, source branch, target branch and title. Precision matters
   here and is stated rather than blurred: Microsoft's reference page
   (learn.microsoft.com/en-us/cli/azure/repos/pr) marks NO parameter as
   required, because organization, project and repository can resolve
   from configured defaults or the git remote; whether the command
   errors at runtime without them was NOT verified, since the az CLI is
   not installed on this machine. UNVERIFIED at the command level.
2. The REST API: pull request creation lives under the Azure DevOps Git
   pullrequests resource with an api-version parameter
   (learn.microsoft.com Azure DevOps REST documentation). The exact
   endpoint string is deliberately not quoted here because this session
   verified the resource exists but did not execute a call; quote it
   from a live run when the mirror exists. UNVERIFIED.

Authentication is a personal access token or Entra token; tokens come
from the environment, never a file in the repository, per CLAUDE.md.

## Continuous integration

`azure-pipelines.yml` at the repository root runs the documented full
gate. One real advantage over the Bitbucket leg, from Microsoft's own
hosted-pool documentation: Azure Pipelines hosts ubuntu, macOS AND
windows images, so Azure is the one non-GitHub host that could reproduce
the full three-platform evidence .github/workflows/tests.yml publishes.
The shipped file starts with the two Linux legs for parity with
Bitbucket; the macOS and Windows legs are written but commented, to be
enabled when a real mirror proves the Linux legs first.

UNVERIFIED: the pipelines file parses locally but has never executed on
Azure DevOps, because no mirror with Pipelines enabled exists. First
green run closes this label.

## What full support still needs, in order

1. Founder creates the Azure DevOps organization or project and the
   empty repository, and enables Pipelines. Account step, his alone.
2. One session pushes main and the release tag, runs the marketplace and
   clone installs against the mirror, quotes the output here.
3. One green Pipelines run quoted here; then enable the macOS and
   Windows legs and quote those.
4. The az CLI pull request shape verified by one live run, replacing the
   UNVERIFIED labels above.

## Sources

- Anonymous ls-remote proof: executed on this machine 2026-08-16, quoted
  above.
- Remote formats, az repos pr reference, hosted pools, marketplace Azure
  support: research pass 2026-08-16 against learn.microsoft.com and
  code.claude.com documentation; the pass's one inference (which pr
  flags are practically required) is labeled as inference above, not as
  fact.
