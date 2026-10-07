# U0 inventory provenance (2026-09-19)

Five parallel Sonnet workers (brothermode:fast-worker) produced the JSON files in this
directory. Four of the five (manifests, state-and-tests, hooks-scripts-schemas,
duplicates-and-imports) ran in agent-isolated worktrees that the harness forked from
this repo's local `main`, at commit `708086256` ("Merge pull request #39 from
khalilmaaouni/release/1.0.20"): **6,730 commits behind `hub/main`**, not the
`refactor/brother-unified-1.1` branch (HEAD `3e7070eb7`) they were briefed to use. One
worker (products.json) correctly wrote into this worktree directly.

**What this means for trust in the numbers:**

- Cheap direct recheck against this worktree's actual `hub/main` HEAD confirmed exact
  match on: `products/brothermode` (1477 files), `products/brothersbe` (836 files),
  `products/brotherds` (115 files), and presence/absence of the five manifests checked
  by hand (`.claude-plugin/marketplace.json`, `.cursor-plugin/marketplace.json`,
  `bundle/.claude-plugin/plugin.json`, `bundle/MANIFEST.json` present; root `VERSION`
  missing: matches worker A's own `not_found` list).
- Top-level structure (products/, bundle/, .claude-plugin/, .cursor-plugin/, .agents/)
  is identical between the stale base and hub/main HEAD as far as this recheck went.
- **Not individually reverified**: per-file duplicate hashes in
  `duplicates-and-imports.json`, the exact line numbers in `hooks-scripts-schemas.json`,
  and the per-subdirectory detail in `state-and-tests.json` / `manifests.json`. These
  are carried forward on the strength of the top-level count match, not a full diff.
- Before any WBS phase actually MOVES or DELETES a file named in these inventories,
  re-run a targeted `find`/`git show hub/main:<path>` check on that specific path first;
  this file is not a substitute for that.

**Root cause, for the record:** the Agent tool's isolation defaults for
`brothermode:fast-worker` forked from this repo's local `main` branch rather than the
branch/worktree named in the brief. The brief's own `cd` instruction did not override
this. Flagging as a real tooling gap, not a one-off mistake, since it will recur for
any future subagent dispatch that assumes it inherits the orchestrator's cwd.

## Addendum: Muse adversarial check, spot-verified (2026-09-19, ~07:40 JST)

Muse (meta/muse-spark-1.3-contributor, xhigh) reviewed the assembled inventory and
found several gaps the five workers missed. Two were spot-checked directly against
the tree and confirmed real (see the commands below); the rest are recorded as
Muse's claims, not independently reverified, and should not be treated as more
solid than that until someone checks them:

1. **CONFIRMED, version drift**: `bundle/.claude-plugin/plugin.json` declares
   `"version": "1.0.20"`, while `products/brothermode/VERSION` says `3.4.5` and
   `products/brothersbe/VERSION` says `3.7.4`. `products/brotherds/VERSION` does
   not exist. There is no version-promotion rule for 1.1.0 yet; U10 needs one.
2. **CONFIRMED, brother_paths.py drift**: six copies exist
   (`scripts/`, `bundle/runtime/`, `bundle/runtime/hooks/brothermode/tools/`,
   `bundle/runtime/hooks/brothersbe/tools/`, `products/brothermode/tools/`,
   `products/brothersbe/tools/`). Five are byte-identical (md5 `960d0dba...`); the
   sixth, `products/brothersbe/tools/brother_paths.py` (303 lines vs 264), is a
   superset adding `one_line()`/`say()`: a log-injection defense against control
   characters and line breaks in environment-supplied paths, needed because a hook
   loads this file by path rather than importing it. The brothersbe copy should be
   the canonical source once brother.core absorbs this in U2, not the other five.
3. **NOT independently reverified** (Muse's claims, carried forward as-is): the
   hooks union arithmetic does not sum (`bundle/hooks/union.json` has 8 entries,
   `brothermode/hooks/hooks.json` has 10, `brothersbe/hooks/hooks.json` has 7);
   `products/brotherds` and `products/brothersbe` each have unmapped files at the
   product root that worker B's subdirectory breakdown does not sum to; `schema`
   and `schemas` subdirectories both target the same Mode schema path in worker D's
   inventory, an unflagged naming collision; `editions/personal|dev|client-one|
   client-two/.brother-edition` reference an `editions/` tree that no worker
   inventoried at all; and `internal_imports.brothermode.file_count: 0` looks
   implausible given `bm_passport.py:24` and `bm_repo_scope.py:6` reference
   brothermode-owned state roots by path, suggesting worker E's grep missed
   dynamic or `sys.path`-based imports.

Verification commands run for items 1 and 2 (paste-able, both exit 0):

    cat .claude-plugin/marketplace.json bundle/.claude-plugin/plugin.json \
        products/brothermode/VERSION products/brothersbe/VERSION
    find . -iname "brother_paths.py" -not -path "*/.claude/worktrees/*" -exec md5 {} \;
    diff ./scripts/brother_paths.py ./products/brothersbe/tools/brother_paths.py
