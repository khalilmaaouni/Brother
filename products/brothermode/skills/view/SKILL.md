---
name: view
description: Write the page that shows where this project stands, and offer it to the user
---

Outcome to produce: one page the user can open, showing where the project stands right now, plus one line in the chat saying what it is a picture of and what it is waiting on.

Run the mechanical command `python3 "${CLAUDE_PLUGIN_ROOT}/tools/brothermode_cli.py" view --project-id <id>` (the packaged console script is `brothermode view`) and read its output; never describe the page from memory of this conversation and never write any part of it by hand. A plugin install exports `${CLAUDE_PLUGIN_ROOT}` for skill and command content, so that path resolves on its own; on a clone install, where the variable is unset, run `python3 tools/brothermode_cli.py view --project-id <id>` instead, from the BrotherMode root (`~/.claude/skills/brothermode`). Either way, run it from the user's project folder so it reads that project's own records.

The command writes one file, `PROJECT-VIEW.html`, at the top of the user's project folder, beside `CANVAS.md`. It is one self contained file: no fonts, no images and nothing fetched from anywhere when it opens, so it opens with no internet connection and it can be kept, copied, or attached to an email.

## Say these three things when you offer it

1. **It is a picture of the records at the moment it was written, not a live screen.** The page prints the newest record it was built from and a short code that changes when the records change, so an old tab is visibly older than a fresh one. Nothing on the page updates itself while it sits open.
2. **There are exactly three ways it changes**, and none of them is the page acting on its own: you ask for it, with this command; a session ending rewrites it if the records moved since the last write; and the published copy changes only when Claude publishes it again, which is a request to Claude and not a promise the page makes.
3. **Nothing on the page can act on the project.** There is no button that starts work. Where the page offers you a choice, it gives you the exact words to paste back into the chat, which is what keeps you in the loop rather than watching a machine.

## The two copies, and why the file comes first

The file on disk is what this product promises. Publishing it as a private page you keep open in a browser is an addition, and it needs a paid plan, a signed in session, and other conditions that are listed in full in `docs/KNOWN-LIMITS.md`. Say which one the user got: the command's own output names the file it wrote and, when there is one, the address of the published copy. If publishing is not available, the file is still there and still complete, and that is worth saying plainly rather than reporting a failure.

The first time a published page is approved, the user approves it. After that, publishing the same page again does not ask, which is the behaviour they want and also something they should be told once, at the start, rather than discover.

## About pictures in the chat, said honestly and never over claimed

The user asked for process flows and graphs in the chat. In the Claude Code terminal that is not available, and no wording makes it available. What exists instead, and it is worth saying in these words:

- the picture lives one click away, in a page that is rewritten in place while the work runs, so keeping it open beside the terminal is the closest thing to watching;
- the chat carries the same facts as text on the same turn, so nothing is hidden behind the click. A drawing in the page has a plain text form in the chat, and the text form is not a lesser version;
- if the user works in the Claude desktop chat or in Cowork rather than in the terminal, pictures inside the conversation may additionally be available there. That is a fact about where they are working, not a thing this product installs.

Never promise a picture in the terminal, a page that refreshes itself, or a screenshot of their application running. The last one is worth naming: this product does not run their application and has no browser, so it will not show one.

## Honesty rules for this command

Read out only what the command printed. If a section of the page has nothing in it yet, the page already says what will fill it and the one thing that fills it, so read that out rather than apologising for an empty project. If the command refuses, report it with the error card format in `references/kickoff.md` (What happened, Impact, Recommended action, What remains safe) and never print raw output at the user unless they ask to see exactly what happened.

v3 note: this skill is the canonical replacement for the legacy `/brotherme-view` command (the founder's 2026-08-07 night rename decision). Its mechanical command now calls `tools/brothermode_cli.py view`, a thin pass-through to the exact `bm_view.py render` function the legacy command named, per freeze answer 4 ("view: tools/bm_view.py render... NON-AUTHORITATIVE, a render, never a source of truth"). Nothing about that non-authoritative status changed: this skill still writes nothing by itself, it only reaches the internal tool that already owns the write.
