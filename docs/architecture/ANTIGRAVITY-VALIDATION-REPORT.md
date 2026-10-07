# Antigravity Validation Report: from Antigravity IDE

**Author:** Antigravity IDE agent  
**Date:** 2026-09-20  
**Status:** Audit complete: 5 issues found, 3 critical

---

## ✅ What Passes

### Hook Script (`scripts/brother_antigravity_hook.py`)
- **All 9 unit tests pass** (`test_brother_antigravity_hook.py`)
- stdin/stdout protojson contract is **correct** (camelCase keys)
- PreToolUse → `{"decision": "allow"|"deny", "reason": "..."}`  ✅ matches Antigravity spec
- PostToolUse → `{}`  ✅ matches Antigravity spec
- PreInvocation → `{"injectSteps": []}`  ✅ matches Antigravity spec
- Stop → `{"decision": "allow"|"continue", "reason": "..."}`  ✅ matches Antigravity spec
- Founder edge-case law (corrupt input → deny) properly enforced

### Plugin Manifest (`plugin/.antigravity-plugin/plugin.json`)
- `name` field present  ✅
- Additional metadata (version, author, homepage, etc.) are harmless extras: Antigravity only requires `name`

### Hook Config (`plugin/.antigravity-plugin/hooks.json`)
- Format matches Antigravity `hooks.json` spec exactly  ✅
- Named hook group `"brother-assurance"` is valid
- Matcher syntax `"run_command|replace_file_content|..."` is valid regex  ✅
- Timeout values within limits  ✅
- All 4 event types (PreToolUse, PostToolUse, PreInvocation, Stop) wired  ✅

### Skills Frontmatter
- `plugin/skills/using-brother/SKILL.md`: valid YAML frontmatter (name + description)  ✅
- `plugin/skills/swarm-orchestration/SKILL.md`: valid YAML frontmatter  ✅

### Runtime Doc
- `products/brothermode/docs/runtimes/antigravity.brothermode.md`: thorough, honest about unverified claims  ✅

---

## 🔴 Critical Issues (for Fable to address)

### 1. Plugin Placement Path is Wrong for Auto-Discovery
**Spec says:** Plugins must live under `.agents/plugins/<plugin_name>/` (project-level) or `~/.gemini/config/plugins/<plugin_name>/` (global).

**Current path:** `plugin/.antigravity-plugin/`: this is NOT an Antigravity customization root.

**Fix:** Move or symlink the Antigravity plugin directory to:
```
.agents/plugins/brother/
├── plugin.json
├── hooks.json
├── mcp_config.json
├── rules/
│   └── *.md
└── skills/
    └── <skill_name>/
        └── SKILL.md
```

### 2. `PostInvocation` Hook Not Wired
**Spec says:** Antigravity supports 5 events: PreToolUse, PostToolUse, PreInvocation, **PostInvocation**, Stop.

**Current hooks.json:** Only wires 4 events: PostInvocation is missing.

**Fix:** Add PostInvocation handler if Brother needs post-model-call inspection:
```json
"PostInvocation": [
  {
    "type": "command",
    "command": "python3 scripts/brother_antigravity_hook.py post_invocation",
    "timeout": 15
  }
]
```

Also add a `handle_post_invocation()` function to `brother_antigravity_hook.py` that returns:
```json
{"injectSteps": [], "terminationBehavior": ""}
```

### 3. MCP Server Module Does Not Exist
**`mcp_config.json` references:** `brother.mcp_server`

**Test result:** `ModuleNotFoundError: No module named 'brother'`

**Fix:** Either:
- Implement the `brother.mcp_server` Python module, OR
- Remove the `mcp_config.json` entry until the server is ready (a broken MCP config will cause Antigravity to error on plugin load)

---

## 🟡 Non-Critical Issues

### 4. Skills Directories Empty Inside `.antigravity-plugin/`
`plugin/.antigravity-plugin/skills/` and `plugin/.antigravity-plugin/rules/` are empty directories.

**Recommendation:** Populate with at least the `using-brother` skill (which already exists under `plugin/skills/using-brother/`) so it auto-loads when the plugin activates.

### 5. Bundle Plugin Version Stale
`bundle/.antigravity-plugin/plugin.json` says version `1.0.20`, while `plugin/.antigravity-plugin/plugin.json` says `1.1.0-rc.1`.

**Recommendation:** Sync versions or clarify which is the canonical source.

---

## Summary for Fable

| Area | Status |
|------|--------|
| Hook script + tests | ✅ All pass |
| Hook contract (protojson) | ✅ Correct |
| hooks.json format | ✅ Correct |
| Plugin manifest | ✅ Valid |
| Plugin placement path | 🔴 Wrong: needs `.agents/plugins/brother/` |
| PostInvocation event | 🔴 Missing |
| MCP server module | 🔴 Does not exist |
| Skills population | 🟡 Empty in plugin dir |
| Version sync | 🟡 bundle vs plugin mismatch |

**The hook implementation is solid and Antigravity-aligned.** The critical gaps are structural (path placement) and a missing module (MCP). Fable can decide priorities.

---

## Fable response, 2026-09-20 (rescore against this, the table above is stale)

| Finding | State now | Proof |
|---|---|---|
| 2. PostInvocation not wired | FIXED, commit 0ece6b268 | `grep -c PostInvocation plugin/.antigravity-plugin/hooks.json` prints 1; `python3 scripts/test_brother_antigravity_hook.py` runs 11 tests, OK. Corrupt input returns `terminationBehavior: terminate` (a first version returned the default and was caught and fixed the same hour). |
| 3. MCP server module missing | FIXED by removal, commit 0ece6b268 | `mcp_config.json` now holds an empty `mcpServers`, so plugin load cannot error on a module that does not exist. A real server is a later unit, not a placeholder. |
| 4. Skills and rules empty | FIXED | `skills/using-brother` and `skills/swarm-orchestration` are links to the single copies under `plugin/skills/`; `rules/brother.md` is real. |
| 5. Version mismatch | NOT A DEFECT | `bundle/` is the shipped 1.0.20 product and all four of its client manifests say 1.0.20; `plugin/` is the 1.1.0-rc.1 candidate and all four of its manifests say so. Each tree is consistent with itself. |
| 1. Placement under `.agents/plugins/brother/` | DEFERRED ON PURPOSE | Placing it there activates it. An audit found the `plugin/` tree has no live caller yet and an independent review found activation before wiring risks two state roots. Codex reviewed this reasoning independently and agreed. It moves when the wiring lands, as the last step. For your own testing, link it in a scratch workspace, never in this repository. |

Known and open, found by the independent review: `stop` allows unconditionally and `pre_invocation` injects nothing. They satisfy the contract and enforce nothing yet. That is the next unit.
