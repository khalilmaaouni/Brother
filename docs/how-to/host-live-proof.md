# Host live proof: owner steps

Under the 1.1.0 scope, only Claude Code and Codex rows decide. Antigravity is experimental and unverified, its rows are NO-DATA and decide nothing. Antigravity steps below are optional and labelled for 1.1.1.

The loop prints the commands and reads the verdict. A signed in owner runs the host steps and collects the evidence. No step asks you to type a secret into a command line. Sign in through the host's own login screen.

<!-- owner-values: same-home -->

## Step 1: optional Antigravity run for 1.1.1

Hand: optional owner run for 1.1.1. Print the exact commands:

```bash
python3 scripts/host_live_proof.py --print-owner-commands antigravity
```

```expected
^# HP1 Antigravity live proof, run \S+: quit Antigravity, then start it from this terminal$
```

Follow the printed instructions, including the guarded write probe. Use the same throwaway home named in the printed commands. End this host's steps by collecting its trace with `python3 scripts/host_live_proof.py --collect antigravity --home <same-home>`, replacing `<same-home>` with that exact home.

## Step 2: Codex live run

Hand: owner. Print the exact commands:

```bash
python3 scripts/host_live_proof.py --print-owner-commands codex
```

```expected
^# HP1 Codex live proof, run \S+: sign in once, then run the three probes non interactively$
```

The throwaway home reuses your existing ChatGPT login through a symlinked `auth.json`; do not sign in separately in that home. Run the printed command lines, including the three probes, and use the same throwaway home named in those lines. End this host's steps by collecting its trace with `python3 scripts/host_live_proof.py --collect codex --home <same-home>`, replacing `<same-home>` with that exact home.

## Step 3: Claude Code live run

Hand: owner. The Claude Code session is driven by `python3 scripts/host_live_proof.py`, wired to `scripts/host_live_claude.py`. Print the exact commands:

```bash
python3 scripts/host_live_proof.py --print-owner-commands claude
```

```expected
^# HP1 Claude Code live proof, run \S+: sign in to Claude Code once, then run the three probes non interactively$
```

Follow the printed commands to run the three probes, and use the same throwaway home named in those commands. End this host's steps by collecting its trace with `python3 scripts/host_live_proof.py --collect claude --home <same-home>`, replacing `<same-home>` with that exact home.

## Read the verdict

The collect command prints the host verdict after writing its evidence rows. The verifier reads `docs/plan/evidence/HP1-host-live.jsonl`. Its line names the host and condition. Exit codes are 0 for GREEN, 1 for RED, and 3 for NO-DATA. A NO-DATA line means that required live evidence has not been recorded yet.
