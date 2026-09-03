Status: UNAUDITED reference as of 2026-08-06. This page sits outside the mechanical truth guards and is scheduled for the documentation sweep; treat any dated or numeric claim inside with care until then.

# The memory setup: Obsidian, the vault, and working without either

Written 2026-07-26 because a stranger could install this project, run its
tests, and never be told the vault existed or how to open it. Obsidian
appeared nowhere in `README.md` or `docs/QUICKSTART.md` as of the day this
was written (`docs/REMAINING.md`, item 4). This page is the missing guided
path. It does not replace `docs/SETUP.md` step 3, which has the exact copy
command; this page explains what you are copying, why, and what to do with
it once it exists.

## What the vault actually is

The vault is a plain folder of markdown files on your own disk (default
`~/BrotherModeVault`, movable with the `BROTHERMODE_VAULT` environment
variable). It holds the durable record this project depends on: what each
project is, what work is open, what decisions were made, and what mistakes
already cost something once. A session reads it at the start of work and
writes to it at milestones and at the end, so a crash, a restart, or a
context compaction has something to recover from besides a model's own
fading memory of the conversation.

**Why a plain folder of markdown, not a database or an app-specific
format.** Three reasons, in order of how much they'd hurt if ignored:

- **It survives every tool change.** If Obsidian disappeared tomorrow, or
  you switched to a different editor, or the AI tooling itself changed,
  every note is still a `.md` file you can open, read, and grep. Nothing
  here is locked into a proprietary format.
- **It diffs and greps like code**, because it is not a database, so the
  same instincts and tools you already use on a codebase work on the
  vault: `git log` on it if you put it under version control, `grep -r` to
  find a note, a diff to see what changed.
- **It stays legible to a human at 2 a.m.** A markdown file with a heading
  and some bullets is readable half-asleep in a way a JSON blob or a SQLite
  row is not, and the vault's whole purpose is to be the thing a tired
  human (or a fresh, context-less session) can pick up and understand fast.

The fuller design reasoning, including how recall and forgetting are meant
to work, is `docs/DESIGN.md` section 4. This page is the practical path to
actually setting it up.

## Installing Obsidian

Obsidian is a free desktop and mobile note-taking app built around exactly
this shape of data: a folder of linked markdown files, which it calls a
"vault." Checked directly against Obsidian's own site on 2026-07-26:

- **Download page** (`obsidian.md/download`, fetched the same day this was
  written): installers for Windows, macOS, and Linux (AppImage, Snap, Deb,
  and ARM64 builds), plus mobile apps for iOS and Android. The version
  shown at fetch time was v1.12.7; check the page yourself for the current
  number; it changes on its own release schedule, not this project's.
- **Cost** (`obsidian.md/pricing`, fetched the same day): personal use is
  free, no sign-up, no trial period, no feature gate. Obsidian's own words:
  "Free without limits. No sign-up required. No strings attached." A
  commercial license ($50 USD per user per year, per that same page) is
  requested, not required, if you use it for work inside an organization.
  Nothing in this project needs the commercial license; a solo founder
  using it for personal or one-person-business work is squarely inside the
  free tier as Obsidian itself describes it.

To install: go to `obsidian.md/download`, pick your platform, run the
installer. Nothing about this project's own installation needs Obsidian
present at that point; you can do this step before, after, or never (see
"Working without Obsidian at all" below).

## Pointing Obsidian at the vault

1. Create the vault folder, if you have not already done this as part of
   `docs/SETUP.md` step 3:
   ```bash
   cp -R ~/.claude/skills/brothermode/vault-template ~/BrotherModeVault
   ```
   This copies the ready-made layout: a `Home.md` dashboard, the vault
   constitution (`AGENTS.md`), and the numbered folders described below,
   including one worked example project so the layout is not just empty
   directories.
2. Open Obsidian. On first launch (or from the file menu later) choose
   **"Open folder as vault"** and select `~/BrotherModeVault` (or wherever
   you put it, including a different path set via `BROTHERMODE_VAULT`).
3. Obsidian will likely show a "trust this vault" style prompt the first
   time, since it can run community plugins; you do not need any plugin for
   this project to work; the default, plugin-free install is sufficient.
4. Open `Home.md`. The bracketed items like `[[10-Projects/example-project/Overview]]`
   are wiki-links; in Obsidian they render as clickable links, and the
   graph view (usually a icon in the left ribbon, or `Ctrl/Cmd+G`) draws
   how every project, failure, and decision connects to the others. Neither
   the links nor the vault's usefulness depend on the graph view; it is a
   nice way to see the shape of your own memory, not a required step.

## What the vault-template folders are for

Copied as-is by the command above, then filled in as you actually use the
project:

- **`Home.md`**: the front door. Links out to every project and to the
  system-level notes below it. Update its project list when you add or
  retire a project.
- **`AGENTS.md`**: the vault's own constitution, the exact rules for how a
  session should read and write this folder (what to read at session
  start, when to checkpoint, what to do at session end, how linking and
  forgetting work). Read this once yourself; every session is expected to
  follow it too.
- **`10-Projects/`**: one subfolder per project, each with an `Overview.md`
  (what the project is, key facts, key links) and an `Open-Items.md`
  (dated, newest first, closed the session they resolve). The shipped
  `example-project/` is a worked template to copy, not a real project to
  keep.
- **`40-Failures/`**: `Failures-Index.md` is read before starting work in
  any area, one line per costly or repeatable mistake, linking to the full
  note. This is the "do not step in the same hole twice" folder.
- **`50-Reference/`**: standing reference material that is not a project
  and not a failure: `pending-amendments.md` (the append-only log of
  observed weaknesses feeding the weekly review) lives here, and anything
  else you want available across every project.
- **`90-Archive/`**: where superseded notes go, each leaving a one-line
  pointer behind at their old location so a stale link still tells you
  where the current version moved to. A vault that only grows becomes
  noise; this folder is the deliberate-forgetting half of that discipline.
- **`99-System/telemetry/`**: machine-written only. Hooks append here
  (`outcomes.jsonl`, `ratings.jsonl`, `reviews.jsonl`, `corrections.jsonl`);
  humans and sessions read it but never hand-edit it, the same way you
  would not hand-edit a log file a program is actively appending to.

## Working without Obsidian at all

Nothing here requires Obsidian. The vault is just files: open
`~/BrotherModeVault/Home.md` in any text editor, any IDE, `less`, `cat`, a
phone's notes app synced over whatever you already use, and every note
reads exactly the same. The `[[wiki-link]]` brackets are plain text outside
Obsidian; they will not be clickable, but they are still perfectly readable
as "this note connects to that one," which is the entire point of writing
them that way. Nothing about the hooks, the CLI tools, or the fence
mechanics in this project checks whether Obsidian is installed, running, or
has ever been opened. Obsidian is a nicer window onto the same folder, not
a dependency.

## Mem0: mentioned, not built, off by default

You may see Mem0 referenced in `docs/DESIGN.md` as a possible future
extension. To be direct about its status here: **this project ships no Mem0
integration.** Mem0 is a separate, optional semantic-memory service; the
usual way to use it with Claude Code is a hosted MCP endpoint, which means
your data would leave your machine to a third party's servers. That is a
straightforward conflict with this project's own claim, stated in
`SECURITY.md`, that it makes no network calls. Nothing here wires it up,
nothing here recommends it, and if you ever add it yourself, understand
that you are opting into a hosted network service this project's security
posture explicitly does not include, and that decision is yours alone, not
something a default configuration would ever make for you. If you do try
it, keep the vault as the one source of truth for decisions and failures;
a self-report-style memory system and a curated one will eventually
disagree, and trusting both equally is worse than trusting one.
