# Antigravity Adapter Spec & Portability Architecture

Status: CO-AUTHORED & VERIFIED with Antigravity first-party agent runtime (2026-09-20).
Project: Brother 1.1.0 Unified Product Architecture (Epic Unit L1).
Target: First-class client adapter for Google Antigravity (IDE, 2.0 Desktop, CLI `agy`, and SDK), alongside Codex and Cursor twins.

---

## 1. Verified Antigravity Architecture & Extension Points

This specification reflects first-party ground truth from Antigravity's active runtime, customization loader (`agy-customizations`), and CLI/IDE sitemap (`antigravity-guide`).

### A. Customization Roots & Discovery Hierarchy
Antigravity discovers customizations by traversing directories from CWD up to the project root:
1. **Workspace Root (Project-Specific)**:
   - Default directory: `.agents/` (or `.agent/`, `_agents/`, `_agent/`) at repo root.
   - Check-in to VCS: Intended for team and repo-level sharing.
   - Hierarchical rules: `GEMINI.md`, `AGENTS.md`, and `.agents/rules/*.md`.
2. **Global Customizations (Machine-Local)**:
   - Antigravity 2.0 & IDE: `~/.gemini/config/`
   - Antigravity CLI (`agy`): `~/.gemini/antigravity-cli/`
3. **Explicit Discovery Declarations**:
   - `.agents/plugins.json` and `.agents/skills.json` can explicitly register and alias paths outside standard discovery locations.

### B. Plugin Packaging Standard
A plugin in Antigravity is a self-contained directory within a customization root (e.g. `.agents/plugins/<plugin_name>/` or `plugin/.antigravity-plugin/`):
```text
plugin/.antigravity-plugin/
├── plugin.json         # Required manifest declaring the plugin
├── mcp_config.json     # Optional: MCP server configurations
├── hooks.json          # Optional: Lifecycle hooks executed on host
├── rules/              # Optional: Contextual/always-on rules (*.md)
│   └── brother.md
└── skills/             # Optional: On-demand progressive skills
    ├── brother-planner/
    │   └── SKILL.md
    └── using-brother/
        └── SKILL.md
```

### C. Resolution of Previous Negative Findings
- **The `$schema` 404 Question**:
  In Antigravity's actual implementation, `$schema` is optional and non-blocking. Manifest validation does **not** fail if `$schema` is omitted or unavailable. The minimal valid manifest is:
  ```json
  {
    "name": "brother"
  }
  ```
  Full supported fields include: `name` (string, required for CLI, optional for IDE/2.0), `version`, `description`, `author`, `homepage`, `repository`, and `license`.
- **Hook Execution & Trust Model**:
  Antigravity executes `"type": "command"` handlers via `sh -c` on Unix (`cmd /c` on Windows) synchronously in the agent loop. The working directory defaults to the directory containing `hooks.json`. It enforces timeouts (default 30s) and blocks loop execution if gated.

---

## 2. Component Mapping: Brother to Antigravity

| Brother / Claude Code / Cursor Concept | Antigravity Native Equivalent | Packaging Location | Notes & Semantics |
| :--- | :--- | :--- | :--- |
| **Plugin Manifest** | `plugin.json` | `plugin/.antigravity-plugin/plugin.json` | Standard manifest naming plugin and metadata. |
| **Skills** | `skills/<name>/SKILL.md` | `plugin/.antigravity-plugin/skills/` | **100% Identical format**. Uses YAML frontmatter (`name`, `description`) + markdown instructions. Loaded via progressive disclosure. |
| **Agents / Personas** | Skills & Rules | `skills/<agent-name>/SKILL.md` + `rules/` | Antigravity runs unified agents with tool access (`manage_task`, `browser_subagent`). Personas (planner, executor, reviewer) map directly to progressive skills or behavioral rules. |
| **MCP Servers** | `mcp_config.json` | `plugin/.antigravity-plugin/mcp_config.json` | Exact equivalent of Cursor's `mcp.json` and Claude's `.mcp.json`. Exposes `mcpServers` object with `command`, `args`, `env`. |
| **Rules / Guidelines** | `rules/*.md` / `AGENTS.md` | `plugin/.antigravity-plugin/rules/` & repo root | Hierarchical markdown rules loaded on CWD walk-up. |
| **Lifecycle Hooks** | `hooks.json` | `plugin/.antigravity-plugin/hooks.json` | 5 lifecycle events (`PreToolUse`, `PostToolUse`, `PreInvocation`, `PostInvocation`, `Stop`). |

---

## 3. Hook Lifecycle Mapping & I/O Protocol

Antigravity hooks communicate with external commands via **protojson (camelCase JSON) over stdin/stdout**.

### Event Mapping Table
| Claude Code / Cursor Hook | Antigravity Hook | Trigger Point | Brother Purpose & Implementation |
| :--- | :--- | :--- | :--- |
| `preToolUse`, `beforeShellExecution` | `PreToolUse` | Before any tool executes | Gating & Fences (`bm_fence_hook.py`, `sbe_fence_hook.py`, `sbe_bash_write_guard.py`). Matcher filters tools (e.g. `run_command\|write_to_file\|replace_file_content`). |
| `postToolUse`, `afterShellExecution`, `afterFileEdit` | `PostToolUse` | After tool step completes | Audit ledger (`bm_bash_audit.py`) and memory recall trigger (`vault_recall_hook.py`). |
| `sessionStart`, `userPromptSubmit` | `PreInvocation` | Before model generation turn | Context injection (`bm_sessionstart.py`, `bm_vault.py refresh`). Turn `invocationNum: 1` acts as session start. |
| `preCompact` | `PostInvocation` or SDK hook | After turn tool execution completes | State compact / autosave (`sbe_autosave.py`). |
| `stop`, `sessionEnd` | `Stop` | When agent attempts to finish loop | Completion verification & reconciliation (`bm_hookchain.py stop`, `sbe_session_reconcile.py`). Can return `{"decision": "continue"}` to refuse early exit until checks pass! |

### Input / Output Payloads

#### 1. `PreToolUse` (Fence and Write Protection)
- **Input (stdin)**:
  ```json
  {
    "toolCall": {
      "name": "run_command",
      "args": {
        "CommandLine": "rm -rf docs/"
      }
    },
    "stepIdx": 12,
    "conversationId": "...",
    "workspacePaths": ["/Users/.../Brother"]
  }
  ```
- **Output (stdout)**:
  ```json
  {
    "decision": "deny",
    "reason": "Brother Fence: modification to protected docs/ directory without active lease."
  }
  ```
  *(Allowed decisions: `"allow"`, `"deny"`, `"ask"`, `"force_ask"`)*

#### 2. `PostToolUse` (Audit & Post-Write Hooks)
- **Input (stdin)**:
  ```json
  {
    "stepIdx": 12,
    "error": "",
    "conversationId": "..."
  }
  ```
- **Output (stdout)**: `{}`

#### 3. `PreInvocation` (Session Start & Ephemeral Context Injection)
- **Input (stdin)**:
  ```json
  {
    "invocationNum": 1,
    "conversationId": "...",
    "workspacePaths": ["..."]
  }
  ```
- **Output (stdout)**:
  ```json
  {
    "injectSteps": [
      {
        "ephemeralMessage": "[Brother] Session started. Run #803 active. Fence clear. Local memory recalled."
      }
    ]
  }
  ```

#### 4. `Stop` (Receipt Verification Gate)
- **Input (stdin)**:
  ```json
  {
    "executionNum": 1,
    "terminationReason": "model_stop",
    "fullyIdle": true
  }
  ```
- **Output (stdout)**:
  ```json
  {
    "decision": "continue",
    "reason": "Brother: Pre-merge verification incomplete. Run scripts/required_fast.sh before terminating."
  }
  ```
  *(If decision is `"continue"`, Antigravity stays in the loop and injects the reason to the agent)*

---

### Fail-closed hook block schemas

`validate_hook_input` and `validate_hook_output` (entry point
`scripts/test_l1_hooks.py`) enforce the same defaults for all four hook events
(`PreToolUse`, `PostToolUse`, `PreInvocation`, `Stop`). Missing, unknown or
corrupt input is never treated as the safe case.

- Empty stdin (0 bytes) and truncated JSON return BLOCK with a fixed reason and no partial parse.
- Any event name outside the known set returns BLOCK.
- Any payload over 256 KB returns BLOCK before a parse is attempted.
- `PreToolUse` without a `toolCall` object carrying `name` and `args` returns BLOCK as NO-DATA.
- `PreInvocation` output may carry only `injectSteps` whose entries carry `ephemeralMessage`, and must never carry a `decision` field; a claimed block returns BLOCK.
- Any internal crash path returns BLOCK with a fixed string and leaks nothing.

## 4. Concrete Antigravity Plugin Artifacts

### A. Manifest (`plugin/.antigravity-plugin/plugin.json`)
```json
{
  "name": "brother",
  "version": "1.1.0-rc.1",
  "description": "An assurance and execution layer for delegated AI engineering",
  "author": {
    "name": "Khalil Maaouni"
  },
  "homepage": "",
  "repository": "",
  "license": "MIT",
  "keywords": [
    "orchestration",
    "project-management",
    "delivery",
    "forecasting",
    "assurance",
    "antigravity"
  ]
}
```

### B. MCP Config (`plugin/.antigravity-plugin/mcp_config.json`)
```json
{
  "mcpServers": {
    "brother": {
      "command": "python3",
      "args": ["-m", "brother.mcp_server"],
      "env": {}
    }
  }
}
```

### C. Lifecycle Hooks (`plugin/.antigravity-plugin/hooks.json`)
```json
{
  "brother-assurance": {
    "PreToolUse": [
      {
        "matcher": "run_command|replace_file_content|write_to_file|multi_replace_file_content",
        "hooks": [
          {
            "type": "command",
            "command": "python3 scripts/brother_antigravity_hook.py pre_tool",
            "timeout": 15
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "run_command|replace_file_content|write_to_file|multi_replace_file_content",
        "hooks": [
          {
            "type": "command",
            "command": "python3 scripts/brother_antigravity_hook.py post_tool",
            "timeout": 15
          }
        ]
      }
    ],
    "PreInvocation": [
      {
        "type": "command",
        "command": "python3 scripts/brother_antigravity_hook.py pre_invocation",
        "timeout": 10
      }
    ],
    "Stop": [
      {
        "type": "command",
        "command": "python3 scripts/brother_antigravity_hook.py stop",
        "timeout": 30
      }
    ]
  }
}
```

---

## 5. Parity Gate Integration Plan for `scripts/client_parity.py`

### L1.4 Inactive parity plan and activation block

The parity plan for Antigravity is named but inactive in L1. The parity constants
are `ANTIGRAVITY_PAIRS` and `SURFACE_TWINS`. Plugin twin:
`plugin/.codex-plugin/plugin.json` to `plugin/.antigravity-plugin/plugin.json`.
Bundle twin: `bundle/.codex-plugin/plugin.json` to
`bundle/.antigravity-plugin/plugin.json`.

ParityPlan gate:

```json
{
  "active": false,
  "codex_path": "plugin/.codex-plugin/plugin.json",
  "antigravity_path": "plugin/.antigravity-plugin/plugin.json"
}
```

Do not register before L1b.


`scripts/client_parity.py` currently enforces parity between Codex and Cursor.
To elevate Antigravity to a first-class client target in Brother 1.1.0 without breaking existing test guarantees, we implement **Tri-Client Parity Tracking**:

### Step 1: Register Plugin Twins in `PAIRS`
Extend `PAIRS` to track the Antigravity twin manifests:
```python
PAIRS = {
    # Existing Codex <-> Cursor pairs ...
    "bundle/.codex-plugin/plugin.json": "bundle/.cursor-plugin/plugin.json",
    "plugin/.codex-plugin/plugin.json": "plugin/.cursor-plugin/plugin.json",

    # Antigravity Twins (Codex -> Antigravity)
    "plugin/.codex-plugin/plugin.json": "plugin/.antigravity-plugin/plugin.json",
    "bundle/.codex-plugin/plugin.json": "bundle/.antigravity-plugin/plugin.json",
}
```
*Note*: Because `PAIRS` is currently a 1:1 `dict` mapping from Codex to Cursor, generalizing to multiple client targets is cleanest via:
```python
ANTIGRAVITY_PAIRS = {
    "plugin/.codex-plugin/plugin.json": "plugin/.antigravity-plugin/plugin.json",
    "bundle/.codex-plugin/plugin.json": "bundle/.antigravity-plugin/plugin.json",
    "docs/how-to/install-codex.md": "docs/how-to/install-antigravity.md",
}
```
Or refactoring `PAIRS` to map each Codex canonical surface to a tuple/dict of clients:
```python
SURFACE_TWINS = {
    "plugin/.codex-plugin/plugin.json": {
        "cursor": "plugin/.cursor-plugin/plugin.json",
        "antigravity": "plugin/.antigravity-plugin/plugin.json",
    },
    "bundle/.codex-plugin/plugin.json": {
        "cursor": "bundle/.cursor-plugin/plugin.json",
        "antigravity": "bundle/.antigravity-plugin/plugin.json",
    },
    "docs/how-to/install-codex.md": {
        "cursor": "docs/how-to/install-cursor.md",
        "antigravity": "docs/how-to/install-antigravity.md",
    },
}
```

### Step 2: Add Antigravity Battery Smoke
Pair `scripts/antigravity_smoke.py` alongside `scripts/codex_smoke.py` and `scripts/cursor_smoke.py`:
- Checks manifest syntax and validity.
- Validates that `hooks.json` complies with Antigravity protojson event definitions.
- Confirms skills adhere to `skills/<name>/SKILL.md` format.

---

## 6. Official Antigravity References & Verification Sources

- **Customization System Guide**: `https://antigravity.google/docs/skills`, `https://antigravity.google/docs/rules-workflows`, `https://antigravity.google/docs/plugins`
- **Lifecycle Hooks Specification**: `https://antigravity.google/docs/hooks`
- **MCP Integration Specification**: `https://antigravity.google/docs/mcp`
- **Antigravity CLI Reference (`agy`)**: `https://antigravity.google/docs/cli/reference`
- **Antigravity Python SDK**: local package documentation (`pip install google-antigravity`)
- **Local Host First-Party Skills**:
  - `agy-customizations`: `~/.gemini/antigravity-ide/builtin/skills/agy-customizations/SKILL.md`
  - `antigravity-guide`: `~/.gemini/antigravity-ide/builtin/skills/antigravity_guide/SKILL.md`

---

## 7. Fable's verification response, 2026-09-20T08:24:30+0900

Read `implementation_plan.md` and this document's own diagram/overview, both
handed over via `fable_message.txt`. Two things independently checked before
accepting them.

**The `$schema` claim: substance confirmed, citation corrected.** I checked
both files cited in section 6 for "Local Host First-Party Skills"
(`agy-customizations/SKILL.md`, `antigravity_guide/SKILL.md`): neither
contains the word "schema" anywhere (`grep -in schema` on both, zero hits).
The real answer lives one level deeper, in
`~/.gemini/antigravity-ide/builtin/skills/agy-customizations/docs/plugins.md`,
which documents the manifest as `{"name": "team-developer-kit"}` with no
`$schema` field at all, and states `name` itself is optional. So the
conclusion (`$schema` is not required, a bare `{"name": "brother"}` is valid)
is now independently confirmed by me, from a real local file, but the two
sources named as proof did not actually contain it. Correcting the citation
here so the next reader checks the right file.

**What's already built, ahead of the implementation plan's proposed
timeline.** `plugin/.antigravity-plugin/` (manifest, hooks.json,
mcp_config.json), `scripts/brother_antigravity_hook.py` (9 real tests,
fails closed on unknown/corrupt input per the founder's own standing
edge-case law, an explicit exception granted for this file), and
`ANTIGRAVITY_PAIRS` in `scripts/client_parity.py` are all real, committed,
and pushed to `hub/refactor/brother-unified-1.1` as of this response
(`python3 scripts/test_client_parity.py` -> `OK`, 2 tests). The
implementation plan's Milestones 1, 3, and part of 5 (overview + diagram,
plugin skeleton, parity gate step 1) are done; no new migration-plan
document or fresh diagram-generation round is needed for those.

**What's still genuinely open, and why it's deliberately not done yet.**
Registering `plugin/` in `.claude-plugin/marketplace.json` (implementation
plan's "packaging" question) is intentionally NOT done: a real audit
tonight (`docs/architecture/ONE-SYSTEM-WIRING-AUDIT.md`) found 0 of 68
`plugin/runtime/brother/` modules have any caller outside their own
package, and a real adversarial review (Muse) found that registering the
plugin before that wiring exists risks a split-brain state: a user
installs it, writes to `plugin/`'s state paths while the live agent loop
still reads the old `bundle/` state, silent data loss. Registration is
gated behind `scripts/required_fast.sh` actually running `plugin/`'s tests
first (`docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json` unit L2b, applied and
measured at 24s real, well within budget). Recommend the same order here:
finish wiring before registering, not the reverse.

---

## Facts a builder needs

- Customization guide URLs live in section 6 of `docs/architecture/ANTIGRAVITY-ADAPTER-SPEC.md`
- Lifecycle hooks reference is `https://antigravity.google/docs/hooks` in `docs/architecture/ANTIGRAVITY-ADAPTER-SPEC.md`
- MCP reference is `https://antigravity.google/docs/mcp` in `docs/architecture/ANTIGRAVITY-ADAPTER-SPEC.md`
- Local proof path is `~/.gemini/antigravity-ide/builtin/skills/agy-customizations/docs/plugins.md` in `docs/architecture/ANTIGRAVITY-ADAPTER-SPEC.md`
- CLI reference is `https://antigravity.google/docs/cli/reference` in `docs/architecture/ANTIGRAVITY-ADAPTER-SPEC.md`

