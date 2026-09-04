# BrotherMode identity contract

Status: CURRENT as of 2026-08-04, PARTIALLY SUPERSEDED as of 2026-08-07 night.
Section 8 records the supersession. Read section 8 before trusting the plugin
id, the marketplace id, or the slash-command form anywhere below it: those
three rows changed, the rest of this page did not.

This page fixes the names this project uses, says which of them may appear in
a page a user reads today, and says what a future rename would have to touch.
It exists because the same idea currently has five written forms in the tree,
and until now nothing said which form was correct where.

The machine-readable copy of section 1 is `product.identity.json` at the
repository root. The two must agree, and a test in `tools/test_bm_docs.py`
refuses a disagreement between that file, `.claude-plugin/plugin.json` and
`pyproject.toml`.

## 1. Canonical forms

| Surface | Canonical form | Where the string actually lives |
|---|---|---|
| Product name, everything a user reads | `BrotherMode` | README.md, the pages under docs/, the launch drafts, project-template prose |
| Persona voice | `BrotherME` | `skills/brotherme/SKILL.md` and the seven `/brotherme-*` commands, nowhere else as a current name |
| Plugin id | `brothermode` (v3, section 8; was `brotherme`) | `.claude-plugin/plugin.json`, field `name` |
| Marketplace id | `brothermode-marketplace` (v3, section 8; was `brotherme-marketplace`) | `.claude-plugin/marketplace.json`, field `name` |
| pip distribution name | `brothermode` | `pyproject.toml`, `[project] name` |
| Python import package | `brotherme` (unchanged in v3, section 8) | `brotherme/__init__.py`, `packages` in `pyproject.toml` |
| Console script prefix | `bm-` | the console scripts in `pyproject.toml` |
| Module file prefix | `bm_` | `tools/bm_*.py`, `mcp/bm_mcp_server.py` |
| Slash commands | `/brothermode` for the expert skill; `/brothermode:start`, `:status`, `:next`, `:review`, `:deliver`, `:view`, `:help`, `:doctor`, `:update` for the nine v3 canonical skills (section 8); `/brotherme-*` survives as fifteen legacy command shims |
| Durable-state environment prefix | `BROTHERMODE_` | 15 distinct names across `scripts/` and `tools/` |
| Code-identity environment variable | `BROTHERME_CONFIG` | `scripts/setup.py` |
| Skill root on disk | `~/.claude/skills/brothermode` | `scripts/install.py` |
| Cursor install root on disk | `~/.cursor/brothermode` | `scripts/install_cursor.py` |
| Cursor hooks on disk | `~/.cursor/hooks.json` and optional `<project>/.cursor/hooks.json` | `scripts/install_cursor.py` |
| Vault on disk | `~/BrotherModeVault` | the default behind `BROTHERMODE_VAULT` |
| Per-project store | `.brothermode/` | the root of every project the tool has run against |
| Cursor harness mailbox | `.brothermode/cursor-mailbox/` | `tools/bm_cursor.py` |
| Git ref namespace | `refs/brothermode` | the autosave refs written by the installer |
| Consent config | `~/.brotherme/config.json` | `scripts/setup.py` |
| Repository slug | `BrotherModeUp` | `github.com/khalilmaaouni/BrotherModeUp` |

## 2. The two-namespace rule

There are two spellings in the code and on disk, `brothermode` and
`brotherme`, and the split between them is permanent and intentional. It is
not drift waiting to be tidied up.

`brothermode` owns durable state, meaning anything a user already has written
to their disk: the skill root `~/.claude/skills/brothermode`, the Cursor
install root `~/.cursor/brothermode`, the vault `~/BrotherModeVault`, the
per-project store `.brothermode/` (including the Cursor harness mailbox
`.brothermode/cursor-mailbox/`), the git refs under `refs/brothermode`, and
the `BROTHERMODE_` environment variables.

`brotherme` owns code identity, meaning the names a package registry or an
import statement keys off: the plugin id `brotherme`, the marketplace id
`brotherme-marketplace`, the Python import package `brotherme`, the consent
config `~/.brotherme/config.json`, and `BROTHERME_CONFIG`.

The two namespaces do not collide, each is internally consistent, and
unifying either direction would break real state on the disk of every person
who has already installed the tool. So neither is unified.

The pip distribution name stays `brothermode`, the console prefix stays
`bm-`, and the module prefix stays `bm_`. None of the three changes.

## 3. Where BrotherME may be written

`BrotherME` is a persona voice, not a product name. It is allowed in exactly
two places: inside the guided beginner skill at `skills/brotherme/`, and in
the `/brotherme-*` commands that skill drives. In those places it is the name
the guided flow speaks under.

Everywhere else that a reader treats as current state, the product is called
`BrotherMode`. That includes README.md, every page under docs/ that is not a
dated record, the launch drafts, and any new copy.

Two uses of the string stay legal outside that scope because they are not the
product name at all: a path or filename that contains it, such as the dated
source plan under `docs/evidence/`, and a quotation of the persona's own
speech.

## 4. Where BrotherModeUp may be written

`BrotherModeUp` is the repository slug. It is never the name of the product.
It is legal in a URL, in a clone or plugin-install command that a user is
told to type, and in a code fence. It is not legal in prose that presents it
as the thing itself.

The install commands keep the literal slug because it is the real path on
GitHub and a command that does not work is worse than a name that is not
preferred.

## 5. What a rename would have to touch

If a future decision changes any canonical form, the change is not a
find-and-replace. The 2026-08-04 identity survey counted, with `git grep`
over the tracked tree: `BrotherModeUp` 165 times in 44 files, `BrotherMode`
excluding that slug 641 times in 130 files, `BrotherME` 131 times in 19
files, lowercase `brothermode` 700 times in 110 files, lowercase `brotherme`
466 times in 70 files, `BROTHERMODE_*` 357 times across 15 distinct names,
and `BROTHERME_CONFIG` 53 times.

The survey's break list, meaning the surfaces where a rename breaks something
a user already has, is:

1. The pip distribution name `brothermode`, already in installed-package
   lists and requirements files.
2. The Python import package `brotherme`, a second and separate break from
   the one above.
3. The plugin id `brotherme` and the marketplace id `brotherme-marketplace`,
   which the documented install forms hardcode.
4. The clone-install directory `~/.claude/skills/brothermode`, which exists
   on disk today.
5. The consent config `~/.brotherme/config.json` and `BROTHERME_CONFIG`.
6. The vault `~/BrotherModeVault` and `BROTHERMODE_VAULT`, which hold a
   user's own written memory, not display text.
7. The per-project store `.brothermode/` in every project the tool has run
   against.
8. The git ref namespace `refs/brothermode` and the `.git/info/exclude` line
   the installer writes.
9. The settings backup name `settings.json.brothermode-backup-<timestamp>`
   and the hook-ownership marker `brothermode-install.json`, which is how the
   installer recognizes its own entries.
10. The 15 `BROTHERMODE_*` environment variables, which stop being read
    silently if the prefix moves.
11. The console prefix `bm-` and the `tools/bm_*.py` filenames, if code
    identity is being changed as well as the product name.
12. The repo slug in `tools/bm_project_facts.py` (`REPO_URL`) and the tests
    that assert against it, which need updating in the same change even
    though GitHub redirects an old repository name.

Any such change updates the canonical-forms table, `product.identity.json`,
and every call site in the same commit, and says in the release notes what an
existing install has to do.

## 6. Historical records are never rewritten

Dated records under `docs/closure/` and `docs/evidence/`, and the entries
already written in `CHANGELOG.md`, are evidence of what was done and said on
a date. They keep whatever names they were written with. A rename pass
excludes them, and so does the naming test described below. Rewriting a name
inside a dated record would falsify the record, which costs more than the
inconsistency it removes.

The same applies to the review records under `docs/craft/` and the design
specs under `docs/superpowers/`.

## 7. Enforcement

`tools/test_bm_docs.py` holds the checks:

- A naming test walks the pages a reader treats as current state and refuses
  `BrotherModeUp` or `BrotherME` used as a current product name there.
- A manifest test refuses a disagreement between `product.identity.json`,
  `capabilities.status.json`, `.claude-plugin/plugin.json` and
  `pyproject.toml`.
- A capability test refuses a capability entry with an unknown state or an
  empty evidence field.
- A banned-absolutes test refuses the words `fully supported`,
  `production-ready`, `works everywhere` and the bare `all platforms` on a
  current page unless the line points at a file that carries the evidence.

Run them with `python3 tools/test_bm_docs.py`.

Two current pages do not comply yet, and the naming test names them as
exclusions with the reason in a comment rather than pretending they pass:
`docs/brotherme-explained.html`, which presents the persona as the product
alongside legitimate persona speech, and
`docs/specs/canonical-project-protocol.md`, which cites the dated source plan
by the title it was written under. Both are copy changes, and both are open.

## 8. The v3 revision, 2026-08-07 night: the two-namespace rule is reversed
for the plugin id and the marketplace id, and held everywhere else

Section 2 above states the split between `brothermode` (durable state) and
`brotherme` (code identity) is "permanent and intentional... not drift
waiting to be tidied up." That statement was true on 2026-08-04 and it was
knowingly reversed, in part, by the founder on 2026-08-07 night.

**What changed.** The plugin id and the marketplace id move from the code-
identity namespace to the durable-state spelling: plugin id `brotherme` ->
`brothermode`; marketplace id `brotherme-marketplace` -> `brothermode-
marketplace`. The public skill surface moves from the flat `/brotherme-*`
command layout to `skills/<name>/SKILL.md`, invoked as `/brothermode:start`,
`:status`, `:next`, `:review`, `:deliver`, `:view`, `:help`, `:doctor`,
`:update`. The full-auto trio (`auto`, `auto-status`, `stop`) and the
founder-mode quartet (`brief`, `decisions`, `handback`, `handover-pack`)
become hidden internal skills under the same plugin: reachable, behavior
preserved, not part of the nine advertised in `/help` or a first-run menu.

**What did not change.** The Python import package stays `brotherme/`
(`brotherme/__init__.py`, `brotherme/core/schema.py`). The consent config
stays `~/.brotherme/config.json` and `BROTHERME_CONFIG`. The pip
distribution name, the console script prefix `bm-`, and the module prefix
`bm_` were already `brothermode`-independent per section 2 and stay exactly
as section 2 describes. These three surfaces sit outside the file-ownership
fence this revision's dispatch was given (they are `brotherme/`, `scripts/
setup.py`, and `scripts/uninstall.py`, none of them assigned to the lane
that made this change), and renaming the Python package specifically would
touch `tools/bm_store.py`'s by-path module loader, which is runtime core
this dispatch was told not to refactor. They remain a named, open item, not
a silent omission: `product.identity.json`'s `v3_revision.not_renamed_this_
run` records them so the next change that touches them starts from a
written list instead of rediscovering the gap.

**Authority.** `BrotherModeUp-handovers/V3-FREEZE-2026-08-07.md`, founder
decision 1 ("full rename to `brothermode`... superseding the afternoon
keep-brotherme answer with the conflict stated") and freeze answer 5. The
architecture refutation's ruling B3 (`v3/architecture-refutation.md`)
adjudicated the twelve break surfaces this reversal costs an existing
install (section 5 above) as accepted wholesale for the plugin id and the
marketplace id, with the upgrade path itself ruled separately under B4:
v2 installs are declared abandoned rather than upgraded, with a migration
note owed to the pilot, not a `/plugin marketplace update` that silently
resolves to nothing.

**What this means for section 2's "permanent and intentional."** It no
longer describes the plugin id or the marketplace id. It still describes
the Python import package and the consent config, for the reason given
above: not because the split is philosophically right there and wrong
elsewhere, but because closing it fully was out of scope for the dispatch
that made this call. A future revision that finishes the unification, or
that decides to leave the Python package and consent config split
permanently after all, records that decision here the same way this one
did: what changed, what was weighed, what it cost, and who authorized it.
