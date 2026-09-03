# What is verified where

BrotherSBE's enforcement lives in Claude Code hooks. Everything else this
project can offer another runtime is advice, and advice that calls itself
enforcement is the exact overclaim these gates exist to refuse. This page says,
per runtime, which controls are enforced, which are consulted, and which are
simply absent, with the evidence for each cell.

Three words, used precisely:

- **ENFORCED**: the runtime has a pre-action hook that can REFUSE the write.
  "The gate blocked it" is a true sentence here.
- **CONSULTED**: the runtime reads instructions this project writes and usually
  follows them. Nothing refuses. A determined agent proceeds.
- **ABSENT**: the runtime offers no surface this project can write to. The only
  integration is a human or an agent running `sbe` and reading its verdicts.

## The matrix

| Control | Claude Code | Codex | Gemini | Cursor | Antigravity | Copilot |
|---|---|---|---|---|---|---|
| Design gates before work (the dossier and its tiers) | ENFORCED | CONSULTED | not measured | not measured | not measured | not measured |
| Write boundary on editor tools | ENFORCED | not measured | not measured | not measured | not measured | not measured |
| Write boundary on shell commands | ENFORCED | not measured | not measured | not measured | not measured | not measured |
| Session-end reconciliation of undeclared changes | ENFORCED | not measured | not measured | not measured | not measured | not measured |
| Evidence and policy verdicts from the command line | ENFORCED | ENFORCED | ENFORCED | ENFORCED | ENFORCED | ENFORCED |

"not measured" is not a polite ABSENT. It means nobody has looked yet, and it
is written that way so a reader can tell the difference between a runtime that
was tested and found lacking and one that was never tested at all.

The bottom row is ENFORCED everywhere for a boring reason: `sbe` is a command
line tool with real exit codes, so any runtime that can run a shell command can
be stopped by it. That is the floor this project offers every host.

## What was actually inspected, and when

Survey run 2026-08-07 on the maintainer's machine, read-only, nothing written
to any runtime.

**Codex.** Config root `~/.codex/`. Carries an `AGENTS.md` that exists and is
empty, a `config.toml` declaring a marketplace and plugin system with local
sources, and a `notify` hook that fires on `turn-ended`. A bundled plugin
(`plugins/latex`) has the layout `skills/<name>/SKILL.md` plus `bin`,
`scripts`, `assets`, `tests`, and its SKILL.md frontmatter is `name:` and
`description:` between `---` markers. **That is byte-compatible with the
frontmatter this project's own skills already use**, which is the single most
useful fact in the survey: the guided surface may port with a manifest and a
path change rather than a rewrite. What is NOT established is any pre-action
hook. `notify` fires after a turn ends, which is too late to refuse anything,
so Codex is CONSULTED until something is found that can say no beforehand.

**Gemini.** Config root `~/.gemini/` with `config/config.json`,
`config/mcp_config.json`, and directories named `antigravity`,
`antigravity-backup` and `antigravity-ide`, so Gemini and Antigravity share a
lineage here. The MCP config suggests the richer integration is a server rather
than an instruction file. Nothing measured yet.

**Cursor.** Config root `~/.cursor/` with `agents`, `extensions`, `plugins`,
`projects`, `skills-cursor`. The presence of `skills-cursor` and `plugins`
suggests an extension surface rather than a plain instruction file, so a naive
instruction drop is probably the wrong shape. Nothing measured yet.

**Antigravity.** Config root `~/.antigravity/` with `antigravity`,
`extensions`, `argv.json`, and the app at `/Applications/Antigravity IDE.app`.
The layout mirrors Cursor's, which mirrors VS Code's. Nothing measured yet.

**Copilot.** No standalone config root found in this pass. Most likely an
extension inside one of the editors above rather than a host of its own, which
would make it ABSENT as a host and reachable only through whatever editor
carries it. Nothing measured yet.

## How a cell moves off "not measured"

By running something, not by reading a changelog. A cell becomes CONSULTED when
a session in that runtime demonstrably read the instructions this project
wrote. It becomes ENFORCED only when an attempted protected write is REFUSED,
and the refusal is quoted here with the command that produced it. Until then
the honest word is the one in the table.

## Cursor: one unknown found while porting, recorded rather than assumed

A real installed Cursor skill (`~/.cursor/skills-cursor/automate/SKILL.md`)
carries a third frontmatter key this project does not emit:

```
environments:
  - local
```

Our generated files carry `name` and `description` only, which is the shape
Codex uses and the shape Cursor's own file also has for those two keys.
Whether Cursor REQUIRES `environments`, defaults it, or ignores its absence is
NOT MEASURED. Nobody has loaded a generated tree in Cursor yet, and comparing
the two files by eye is not the same as loading one.

Two outcomes, and `tools/sbe_port.py` is written so either is cheap: if Cursor
needs the key, the generator grows one runtime-specific line and the drift test
keeps both hosts correct. If it does not, nothing changes. What must not happen
is this page claiming Cursor works because the frontmatter looked similar,
which is exactly the inference that was wrong about Copilot on the same day.
