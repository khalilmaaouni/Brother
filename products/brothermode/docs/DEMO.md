Status: UNAUDITED reference as of 2026-08-06. This page sits outside the mechanical truth guards and is scheduled for the documentation sweep; treat any dated or numeric claim inside with care until then.

# Five minutes, copy and paste

A short walkthrough that shows the three things this project is actually for:
a correction that survives the session, a fence that stops a second session
writing over your file, and a closing step that refuses to accept "done"
without evidence.

Every command below was run, exactly as written, in a throwaway repository
under `/tmp` on 2026-07-29, and the outputs shown are that run's real output.
Ids and timestamps will differ on your machine; the shape will not.

You need: python3 (3.9 or newer), git, and a clone of this repository. Nothing
else, no network, no keys.

## 0. Set up a scratch project (30 seconds)

Do this in a throwaway directory the first time. Nothing here touches your real
work.

```bash
export BM=~/.claude/skills/brothermode      # wherever you cloned this repo
mkdir /tmp/bmdemo && cd /tmp/bmdemo
git init -q .
printf "/.brothermode\n" >> .git/info/exclude
export BROTHERMODE_ROOT=/tmp/bmdemo
python3 $BM/tools/bm_store.py init
```

```
bm_store: initialized /tmp/bmdemo/.brothermode/store.sqlite3 (root resolved via env)
```

That sqlite file is the whole system's memory. It is inside your project, it
is git-excluded, and deleting it is the uninstall.

## 1. Correct me once (60 seconds)

```bash
python3 $BM/tools/bm_learn.py capture \
  --trigger "pushing commits or publishing a branch to GitHub" \
  --action "use the GitHub Desktop app, never a bare git push" \
  --because "the founder wants every push visible on screen" \
  --scope global
```

```
captured 967dd016 (pending, nothing changes until you approve it)
```

Nothing has changed yet. That is the point. Look at what is waiting:

```bash
python3 $BM/tools/bm_learn.py candidates
```

```
  967dd016  [global]  pending
     When: pushing commits or publishing a branch to GitHub
     Do  : use the GitHub Desktop app, never a bare git push

1 candidate(s). Approve with: bm_learn.py approve <id> --ref "why you approved"
```

## 2. Approve it, which is the only way anything becomes a rule (30 seconds)

Use the id your own run printed.

```bash
python3 $BM/tools/bm_learn.py approve 967dd016 --gate --ref "I said this in session 2026-07-29"
```

```
approved as rule 6f385e1f
  6f385e1f  [global, approved, gate] v1
     When: pushing commits or publishing a branch to GitHub
     Do  : use the GitHub Desktop app, never a bare git push
```

`--gate` marks it a safety rule, which changes one thing: it is surfaced even
when your words share nothing with it, and a result limit cannot silence it.

## 3. Ask what applies before you work (30 seconds)

Two separate commands, on purpose: `lookup` reads and records nothing, for
exploring what applies or for checking whether a task even needs the recorded
path. `apply --session <id>` does the same retrieval AND records that the
rules were surfaced, so it needs a work identity, and is what substantial work
should use. The old `relevant` still runs, kept only as a deprecated alias
that prints a warning on every call and tells you which of the two it means.

```bash
python3 $BM/tools/bm_learn.py lookup --query "I want to push this branch to github"
```

```
MANDATORY FOUNDER GATES - 1 ACTIVE

Gc316d907  use the GitHub Desktop app, never a bare git push

Full gate text available by id (--expand <id>).
Manifest hash: 4128605122ba0aaaa8f7386aaf90afab92acc8e9af7c7d410e80df4b25c379f7

RELEVANT FOUNDER RULES (mode=lexical)

  c316d907  rank=1
  Scope: global     State: approved     GATE
  When : pushing commits or publishing a branch to GitHub
  Do   : use the GitHub Desktop app, never a bare git push
  Why  : the founder wants every push visible on screen
  Match: terms ['branch', 'github', 'push'], relevance 0.6
  Expanded: trigger-matched

Constitution overrides learned rules.
Gates: 1 of 1 applicable returned. A result limit cannot hide one.
  of those: 1 shown in full (bounded per call), 0 in the compact manifest only. Pull one by id with --expand.
Soft rules: 0 shown, none omitted.
```

`mode=lexical` is the tool telling you how it matched: shared words, the way a
search box works. Not an AI judging relevance, and it says so rather than
letting you assume. The rule id above (`c316d907`) is from a separate run than
steps 1 and 2's `6f385e1f`; per the note at the top, ids differ run to run and
the shape is what stays constant.

## 4. Take a fence over a file (45 seconds)

The session label comes from a secret token this process had to be able to
open, so quoting somebody else's label buys nothing.

```bash
export BM_SESSION=$(python3 $BM/tools/bm_fence_hook.py session-label --session-id my-demo-session)
python3 $BM/tools/bm_store.py claim release-notes \
  --lifetime ephemeral --objective "write the v1 release notes" \
  --files NOTES.md --session "$BM_SESSION"
```

```
claimed 'release-notes' as lifecycle 6252081d84c24f0cb5591ff701ec9505 (version 1, session bm1-1190c464c767795c1ee53a46)
```

## 5. Watch a second session get refused (45 seconds)

This is the payload Claude Code's PreToolUse hook sends before a file edit. Run
it as a DIFFERENT session id and the fence answers.

```bash
printf '{"session_id":"some-other-session","cwd":"/tmp/bmdemo","hook_event_name":"PreToolUse","tool_name":"Edit","tool_input":{"file_path":"NOTES.md"}}' \
  | python3 $BM/tools/bm_fence_hook.py
```

```
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
"permissionDecisionReason": "BrotherMode fence: NOTES.md is inside the fence of the active
record release-notes (lifecycle 6252081d..., version 1), which is owned by session ... .
This session is bm1-a9ee2164..., so it is not the writer for that path. ...
To take the fence over deliberately, run:
  python3 tools/bm_store.py adopt 6252081d... --version 1 --session bm1-a9ee2164... --adopt-from-live-session ..."}}
```

Read the last part: the deny names the record, the owner, and the exact command
that would move the fence. It refuses, it does not hide the door.

To make this automatic in Claude Code rather than something you run by hand,
wire the hook: `docs/HOOKS.md`.

## 6. Close it, and be refused for the right reason (60 seconds)

Try to finish without evidence:

```bash
python3 $BM/tools/bm_store.py complete 6252081d84c24f0cb5591ff701ec9505 --version 1 --note "notes written"
```

```
refused (missing-evidence): active -> complete requires non-empty evidence (the check_cmd result)
```

Now with the evidence, and as the session that owns it:

```bash
python3 $BM/tools/bm_store.py complete 6252081d84c24f0cb5591ff701ec9505 \
  --version 1 --session "$BM_SESSION" --evidence "python3 tools/test_all.py: OK"
```

```
bm_store: saved the previous STATE.md as /tmp/bmdemo/STATE.md.bak-20260729T094705331770 before rewriting it
complete: 'release-notes' (lifecycle 6252081d84c24f0cb5591ff701ec9505) is now complete at version 2
```

Two things happened on purpose. `complete` wants the FULL lifecycle uuid, not a
prefix, because closing the wrong record silently is worse than typing more.
And the old `STATE.md` was backed up before it was regenerated, because that
file can hold your own prose outside the generated markers.

## 7. Check the store's own integrity (15 seconds)

```bash
python3 $BM/tools/bm_learn.py verify
```

```
learning-verify: 1 rule(s), 0 edge(s), 9 check(s) run
  note: fts-drift: no FTS index exists in this schema, retrieval mode is lexical, so there is nothing to drift from
  no findings
```

## Clean up

```bash
cd / && rm -rf /tmp/bmdemo
```

That is the whole uninstall for this project: the store lives inside the
project directory, and nothing was written to your home directory. Scenario 13
of `docs/BENCHMARK.md` checks exactly that claim mechanically.

## What you have NOT seen here

- Any of this running through a real working day. It has not
  (`docs/NOT-FINALIZED.md` item 1).
- Semantic retrieval. Step 3 matched on shared words and said so.
- Automatic correction capture. It exists, it runs in English, French and
  Japanese, and it can only ever file a candidate. Approval in step 2 is
  human-confirmed, one-time receipt-gated, always: automatic capture can
  never approve or promote its own candidate.
- Windows. Every Windows claim in this project comes from CI, not from a
  machine on this desk.
