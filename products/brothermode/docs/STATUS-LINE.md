# Status line and footer links (opt in)

Status: CURRENT

BrotherMode ships two small pieces of Claude Code's own furniture, and turns
neither one on. The first is a status line: a single line at the bottom of
Claude Code that can read "2 of 5 tasks accepted, decision waiting" instead
of the built in keyboard hints. The second is a footer link setting that
turns a decision id or a trace tag anywhere in the transcript into a
clickable badge. Both are useful. Neither is on by default, and this page
says exactly why, and carries the two blocks you paste into your own
settings if you want them.

## Why BrotherMode cannot turn these on for you

A Claude Code plugin's own `settings.json` recognizes exactly two keys,
`agent` and `subagentStatusLine` (verified against the plugin settings
contract, docs/program/absolute-lead/research/visual-surface/
LENS-D-claude-surface.md section 6). `statusLine` is not one of them, so a
plugin cannot install a status line at all. `footerLinksRegexes` is a
different setting again, and it is read only from your own user settings,
the `--settings` flag, or managed settings; the published schema states
plainly that it is "ignored in project and local settings" (verified
against `https://www.schemastore.org/claude-code-settings.json`, the JSON
Schema Claude Code itself publishes for its settings files). A plugin
cannot write either setting for you, on purpose: both live in files that
belong to you, not to whatever you happen to have installed.

So BrotherMode ships the script (`tools/bm_statusline.py`, packaged as the
console command `bm-statusline`) and this page. Turning either setting on
is your call, made once, in your own settings file.

## The status line

### What it shows

One line, plain language, built from the same status fields your project's
own status view already uses (`tools/bm_lead.py`'s `collect_status`):
Progress, and whether a Decision needed stands. For example:

```
2 of 5 tasks accepted, decision waiting
```

or, with nothing waiting on you:

```
3 of 3 tasks accepted, no decision waiting
```

or, before anything has been planned yet:

```
nothing planned yet, no decision waiting
```

No jargon, no store ids, no colour codes. If BrotherMode has not been set
up in the folder Claude Code is running in, or setup has not been consented
to yet, or the folder holds more than one BrotherMode project, the line is
simply blank: BrotherMode's status line never prints a half answer, and it
never prints an error into your terminal's bottom bar.

### The settings block

If you installed BrotherMode with `pip install brothermode` or
`pipx install brothermode`, `bm-statusline` is already on your PATH. Add
this to `~/.claude/settings.json` (create the file if it does not exist
yet):

```json
{
  "statusLine": {
    "type": "command",
    "command": "bm-statusline"
  }
}
```

If you installed BrotherMode by cloning the repository or as a plugin,
`bm-statusline` is not on your PATH. Point the command at the script
directly instead, with the absolute path to wherever BrotherMode sits on
your machine:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 /absolute/path/to/tools/bm_statusline.py"
  }
}
```

`${CLAUDE_PLUGIN_ROOT}` is not used here on purpose: that variable is set
around commands a plugin itself wires (hooks, skill commands), and a status
line lives in your own global settings, which run in every Claude Code
session whether or not BrotherMode is active in it. Write the real path in
by hand.

## Footer links

### What it does

`footerLinksRegexes` turns matching text anywhere in the Claude Code
transcript into a small clickable badge in the footer. BrotherMode's own
trace tags and decision ids already print in one fixed shape everywhere
they appear (`tools/bm_lead.py`'s `render_claim_line` and
`render_decision_card`, both of which print `[i:<32 lowercase hex
characters>]`; a decision's id is a trace tag in this same shape, since a
decision is recorded as an insight like any other claim). One pattern
covers both.

### The settings block

```json
{
  "footerLinksRegexes": [
    {
      "type": "regex",
      "pattern": "\\[i:(?<id>[0-9a-f]{32})\\]",
      "url": "https://claude.ai/public/artifacts/YOUR-ARTIFACT-ID",
      "label": "project view, record {id}"
    }
  ]
}
```

Replace `YOUR-ARTIFACT-ID` with your own project's published page address.
Once you have published your `PROJECT-VIEW.html` once, Claude republishes
it to that same address on later changes rather than minting a new one
(skills/brotherme/SKILL.md, the standing instruction: "publish to that
same stored address so the user's open tab updates instead of collecting a
new page each time"). Find the address with:

```
bm-view url --project-id YOUR-PROJECT-ID
```

(or `python3 tools/bm_view.py url --project-id YOUR-PROJECT-ID` on a clone
or plugin install). It prints "not published yet" until the first publish.

Stated plainly, so nothing here is oversold: the badge opens your project's
page, not the exact record on it. `PROJECT-VIEW.html` does not carry a
per-record anchor for every trace tag yet, so the `record {id}` text on the
badge tells you which id it is, and you find that record on the page
yourself once there. If you have not published a page yet, this setting
has nothing useful to link to; skip it until you have, or point `url` at
`vscode://file/ABSOLUTE/PATH/TO/PROJECT-VIEW.html` instead, which opens the
file in VS Code if that is your editor (`vscode` is one of the URI schemes
Claude Code recognizes for this setting; a plain `file://` link is not, so
it is not offered here as an option).

## What "opt in" means here

Both settings are yours to turn on, change, or remove at any time; nothing
in BrotherMode reads them, writes them, or depends on them being set.
Deleting the `statusLine` or `footerLinksRegexes` block from your
settings.json turns the corresponding feature off immediately, on your next
Claude Code session.
