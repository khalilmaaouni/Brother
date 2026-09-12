# Security Policy

## Supported Versions

Only the latest release receives security fixes. You can see the latest release on the Releases page of this repository.

## Reporting a Vulnerability

Please use GitHub private vulnerability reporting. Go to the Security tab of this repository and select "Report a vulnerability".

Do not open a public issue for a security report.

Please include:

- version you are using
- steps to reproduce
- the command you ran
- what you expected to happen
- what happened instead

## What is in Scope

- the write fence and Bash guards that stop an agent writing outside its declared scope
- the merge gate and its verdicts
- receipts and their signatures
- the release and public export pipeline
- the installers

## What is Out of Scope

- the behaviour of the AI host or model itself
- third party plugins
- findings that need an already compromised machine

## Coordinated Disclosure

Fixes are released first and details are published after. A known issue may be kept private until its fix ships.

## Security Design Notes

See docs/reference/safety-boundaries.md for the current safety boundaries.

Some developer mode hooks fail open on an internal error so they do not block unrelated work, and this is a known limit being addressed.
