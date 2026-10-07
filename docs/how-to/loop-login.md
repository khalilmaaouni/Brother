# Give the loop a long-lived Claude login

Use this guide when the loop's native Claude sessions should run on their own login instead of the one your desktop holds. One command signs in once; the token lands in your login Keychain, and no agent, build or test ever sees it.

## Prerequisites

Claude Code installed (`claude --version` works), the Xcode command line tools (`xcode-select --install` once; the first run builds a small Keychain reader with them), and a Brother checkout. macOS only: the login lives in the macOS Keychain and the seat sandbox is `sandbox-exec`.

## Sign in once

```bash
python3 scripts/loop/brother_login.py
```

1. Your browser opens. Sign in to Claude. If it shows a code, paste it into Terminal and press Return (what you type stays hidden).
2. Wait for `Brother login saved and verified in your login Keychain.` Anything else means nothing was saved: the message says what to do, and running the command again is always safe.
3. Turn the lane on for a run with `BROTHER_LOOP_TOKEN=on`, spelt exactly like that. Any other value, or none, leaves the loop exactly as it is today.

The helper runs `claude setup-token` inside a private terminal of its own. Its output never reaches your screen, a log or a file; the one token it prints is written to the Keychain item `claude-loop-token` for your user by Brother's own reader (`~/.claude/bin/brother-keychain`, built on the first run), then read back to prove the write. The Keychain trusts that reader alone: any other program asking for the item, Apple's `security` tool included, makes the Keychain ask you first. Each build of the reader carries a random value, so its identity cannot be rebuilt from the source, which every session's checkout contains. Anthropic documents these tokens as lasting one year.

## If the reader was built before this change

A reader built before 2026-10-03 has no random value in it, so its identity can be rebuilt. Remove the login, the reader and sign in again:

```bash
python3 scripts/loop/brother_login.py forget && rm ~/.claude/bin/brother-keychain && python3 scripts/loop/brother_login.py
```

## If you saved a login before 2026-10-03

An earlier Brother wrote the item with Apple's `security` tool, which the Keychain then trusted to read it. Remove that item once and sign in again; brother-login refuses to overwrite it and prints this same command:

```bash
security delete-generic-password -s claude-loop-token -a "$USER" ~/Library/Keychains/login.keychain-db && python3 scripts/loop/brother_login.py
```

## What the loop does with it

With the lane on, each native session reads the Keychain item fresh and hands it to that one `claude` child on a private pipe (`CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR`), never in an environment: on macOS any program you run can read another of your programs' environment, a session's own shell included. The pipe empties the moment Claude Code reads it. Every credential shaped variable is stripped from the session's environment first, the parent loop never carries the login, and nothing logs it. The child is told to keep its own environment from its tool processes (`CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1`), so a shell the session runs cannot print the login either; because that setting makes Claude Code fall back to its default permission mode, the session's tools are declared explicitly and still run. Inside the seat, `/usr/bin/security` cannot run, the Keychain daemon cannot be asked, and the keychain files cannot be read. On both lanes a session cannot run anything in `~/.claude/bin`, where Brother's reader lives. With the lane on it also cannot run any program stored in your home folder outside its own checkout and the usual tool folders; with the lane off, tools installed elsewhere in your home folder (for example under `~/.asdf` or `~/bin`) still run, as they did before. On both lanes a session cannot ask macOS to launch an app or send Apple Events: `open`, `osascript`, `lsappinfo`, `shortcuts` and `automator` do not run in a seat, and the LaunchServices and Apple Events services are refused, so a copy of one of those tools or a direct call into LaunchServices fails too. Without that, an app a session wrote into its checkout was started by macOS outside the sandbox, where it could run the reader. With the lane on, the loop refuses any headless Claude call that is not a native seat, so nothing runs on your desktop login by accident. A missing or unreadable item parks the round with `run brother-login` in its reason instead of falling back to another login. A token that has expired or been revoked parks the round the same way, naming the step.

What stays exposed with the lane off: the seat sandbox must let Claude Code reach the Keychain, because that is where your desktop login lives, so code a session runs can still ask the Keychain for items that trust Apple's `security` tool. The loop's own item is not one of them; your desktop login is. Turn the lane on to close that.

What stays exposed on either lane, outside a session: the Keychain trusts Brother's reader, not the person or the program asking it. Any program you run yourself, outside a loop session, can run `~/.claude/bin/brother-keychain get` and print the login without a prompt, exactly as it could read any file you own. Brother cannot narrow this without a secret the same programs could not read, and there is no such place for your own user; treat the login like your other credentials and revoke it if something untrusted ran on this Mac. A running `claude` can also be read by a debugger you start yourself.

What stays exposed inside a session, measured 2026-10-03: the sandbox closes the ways out of a seat that are known and tested (launching an app, Apple Events, running the reader, `launchctl`), not every possible one. A new macOS service that starts programs on a session's behalf would reopen the gap, and its program would run as you. The pipe that carries the login stays open in Claude Code for the whole session and its tool processes inherit it, but Claude Code has already read it to the end by then, so a tool reading it gets nothing. The reader cannot tell the loop from any other program of yours: the loop is an ordinary `python3`, so a check of the caller's signature would accept every Python program on this Mac, and any secret the loop could hold to prove itself would be readable by the same programs.

## Remove it

```bash
python3 scripts/loop/brother_login.py forget
```

This removes the local item and verifies it is gone. It does not revoke the token: stop any running loop, then revoke it in Claude's settings under Claude Code.
