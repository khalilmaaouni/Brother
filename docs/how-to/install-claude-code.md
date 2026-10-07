# Install Brother on Claude Code

Use this guide to install the public Brother plugin and confirm the one-door experience works.

## Prerequisites

Claude Code authenticated; `git` and `python3`; a low-risk/throwaway repository for smoke testing.

Confirm `claude --version`, `git --version`, and `python3 --version` work in your terminal. Model access and usage costs belong to your coding host; plugin installation does not supply model access.

## Install

```bash
claude plugin marketplace add khalilmaaouni/Brother && claude plugin install brother@brother
```

Use lowercase marketplace name `brother` for update/remove operations.

## Smoke the door

Open a repository. With no unfinished work, Brother should ask what you are trying to do, not present internal products as a menu. Choose a small outcome whose expected behavior you understand.

Start a fresh Claude Code session after installation. Type `/brother`, then describe the outcome. If the command is absent, inspect `claude plugin list`, confirm `brother@brother` is installed/enabled in the intended scope, and restart the session before retrying. Do not install a second copy merely because an existing session has not refreshed.

Use the [first verified change](../tutorials/first-verified-change.md) for a complete example. A successful install is not a successful work run: require the saved receipt and inspect its checks.

## Verify the result

Confirm the door is reachable, unfinished-work discovery makes sense, hook scope matches your install choice, a small nontrivial run can produce a receipt, and you can explain what that receipt did and did not prove.

Read [Hook scope](../reference/hooks.md) before broad/team installation.

Read the run's effective safety message. `not enforced` is not equivalent to an enforced boundary. Keep production access and broad permissions outside your first trial; follow [safe delegation](delegate-safely.md).

## Live proof on Claude Code

Claude Code is the reference host of the live proof. The session runs `claude --print --output-format stream-json --include-hook-events --plugin-dir <bundle>` with the witness shim first on `PATH`, and its stdout is kept beside the evidence file. `scripts/host_live_claude.py` compares the hook events the host reports with the witness rows, per run. The binary comes from `HP1_CLAUDE_BIN`, then `CLAUDE_BIN`, then the newest install under `~/Library/Application Support/Claude/claude-code/`, never from `PATH` alone. Never pass `--bare`: it skips hooks. A missing binary or a stream line without the expected fields is NO-DATA, not a pass.

## Upgrade

```bash
claude plugin marketplace update brother && claude plugin update brother@brother
```

Restart the session, inspect `claude plugin list`, and repeat the small verification task. Retain the prior release identity and run evidence when evaluating an upgrade.

## Uninstall

```bash
claude plugin uninstall brother@brother && claude plugin marketplace remove brother
```

Restart and confirm Brother is absent from the plugin list. If you separately installed product-level hooks, remove that wiring through its installer rather than deleting shared host configuration. Uninstall is not a request to erase project history or Vault notes.
