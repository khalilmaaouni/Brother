# Vault human/agent interaction: research and design spec

Research pass, read-only, 2026-09-20. Scope: the Brother-managed vault backend
(products/brothermode/tools/bm_vault*.py, ~65 files) and the separate Kay Vault
(~/Documents/Kay Vault, governed by its own AGENTS.md). No files changed.

## 1. What the real backend already provides

Retrieval and ranking (bm_vault.py):
- `_search()` (bm_vault.py:1958) fuses FTS5 keyword search, a CJK/Japanese
  analyzer path (`_cjk_hits`, :1680), and embedding cosine similarity
  (`_embed_texts`, :1419; `_cosine`, :1540) via Reciprocal Rank Fusion
  (`_rrf`, :1627).
- `_context_rank` (:3527) and `_situation_first` (:3602) re-rank by the
  calling session's own project/path context, not just query text.
- `_anchor_query_ids` (:1664) protects direct path/anchor matches from being
  displaced by link-expansion noise (landed under VR2,
  docs/plan/VAULT-RETRIEVAL-ADVANCEMENT-PLAN-2026-09-09.md).
- A query embedding cache (`_query_cache_get/put`, :1513-1526) and a
  benchmark harness (`docs/plan/vault-recall-benchmark-queries-2026-09-13.json`,
  `bm_vault_enrich_index.py:measure`, :321) exist and are scored (recall@k,
  MRR, nDCG@5), not just built.

Forgetting/decay (bm_vault_decay.py): a real, cited implementation. Docstring
(:1-48) names the source: MemoryBank (Zhong, Guo, Wang et al., AAAI 2024,
arxiv.org/abs/2305.10250, confirmed by direct fetch 2026-09-20), an
Ebbinghaus-curve memory update. `retention()` (:177) computes
`R = exp(-t / S)` where `S = HALF_LIFE_DAYS(30) * (1 + reps)`; `scale()`
(:199) maps that to a rank multiplier with a `FLOOR` of 0.5 so a decayed note
is demoted in ranking, never removed (the vault constitution forbids
deletion; see Kay Vault AGENTS.md section 5). `reinforce()` (:150) lengthens
a note's stability each time recall confirms it prevented a repeat.

Staleness (distinct concept, bm_vault_staleness.py): `classify()` (:153)
reads `verified_at` against per-type horizons (`horizon_days`, :141) and
answers "has this been checked lately," demoting authority, not similarity
rank. Decay and staleness are two independent axes, run in the same sort by
design (bm_vault_decay.py docstring, :20-25).

Filtering/access control: `bm_vault_policy.py` (deny rules, referenced at
bm_vault.py:3093 `_policy_deny`), `bm_vault_authority.py` (authority ranking,
LEVELS/_RANK, :39-40), `bm_vault_lifecycle.py` (quarantine/demotion/
supersession).

Routing: `bm_vault_route.py` groups DEFECTS (from census/triage/rot/doctor/
governance/posture, each an already-shipped checker) by owner
(`route_findings`, :243; `owner_of`, :229) - this is maintenance-defect
routing, not query routing between surfaces.

Serving over the wire: `bm_vault_serve.py` is a dependency-free HTTP front
on the same recall stack (`GET /health`, `POST /recall`, versioned `/v1/`
per VB3-09), with token auth, TLS on non-loopback binds, and an
enterprise/tenant mode (`bm_vault_context.py`). `bm_vault_pane.py` adds a
second, narrower HTTP surface: `GET /pending` / `POST /act` for approving or
rejecting promotions and curation candidates only (HMAC action tokens,
principal-registry revocation check) - not a browse/search UI.

Discovery: `bm_vault_cli.py` is a thin router ("the vault's one front door")
over ~25 sibling CLIs plus a `doctor` verb.

## 2. What's missing, named against a specific file or absence

- **No human-facing query/browse surface that hits the ranked stack.**
  Every retrieval capability above is CLI- or HTTP-API-only. `bm_vault_pane.py`
  is the only browser-facing HTML/JSON surface and it only exposes
  approve/reject actions, never `_search`/`cmd_recall`.
- **Structural bypass, stated as design, not a bug**: `products/brothermode/
  docs/VAULT-TRUST-BOUNDARY.md` ("What bypasses every vault control,
  completely") states plainly that Obsidian, a shell, or any process reading
  files directly never goes through `bm_vault.py recall`,
  `bm_vault_policy.py`, `bm_vault_audit.py`, or `bm_vault_serve.py`. Policy
  trimming, decay, staleness demotion, and authority ranking bind only "the
  served path" - the CLI and the HTTP server - never a file opened by
  Obsidian itself. `bm_vault_cli.py`'s own docstring (:159-163) says the same
  thing in one line: "Obsidian, a shell, or any process running as the user
  reads vault files directly, bypassing all of it."
  This is confirmed independently in `products/brothersbe/docs/book/
  17-the-vault-in-obsidian.md` (:130-139): "The graph is not a dashboard and
  it does not compute anything." Obsidian's native graph view is pure
  wikilink adjacency with zero access to ranking, decay, or fusion.
- **No cross-store view.** Kay Vault (hand-maintained markdown, cap-lined
  notes, `AGENTS.md` constitution) and the Brother-managed vault backend
  share only `bm_vault_lint.py` (linter) - there is no single query surface
  spanning both stores; the task's premise that Kay Vault is "a separate"
  vault is correct at the tooling level, not just organizationally.
  Kay Vault's own escalation path (AGENTS.md section 6) is fully manual:
  a human writes a `00-Inbox/*-custodian-question-*.md` file and waits for
  the founder - no programmatic decision-routing exists there at all.
  This is the retrieval-tooling side of the gap that Kay Vault's constitution
  never claims to solve; it explicitly says "Memory only: no task manager"
  (section 1).
- **Community-plugin risk is named, not closed.** `docs/VAULT-PLUGIN-POLICY.md`
  and `bm_vault_plugins.py` audit which Obsidian plugins are *enabled*
  against a policy file, but per `VAULT-TRUST-BOUNDARY.md` any enabled
  plugin runs in-process with full filesystem reach and is invisible to
  every control named above - so even a "smart search" Obsidian plugin
  cannot be the fix without re-deriving ranking/decay/policy a second time,
  duplicating the backend rather than fronting it.
- **No prior WBS row commits to a front-end answer.** Grepped
  `docs/plan/VAULT-*.md/json`: governance (VB10), retrieval (VR0-VR5), and
  pane/approval (VB11-05) rows exist; none addresses a human browse/search
  UI over `_search`/decay/staleness. This gap is open, not merely
  unimplemented against an existing plan.

## 3. External prior art: human+agent memory over one store

**Letta (formerly MemGPT), core memory blocks** (docs.letta.com/guides/
core-concepts/memory/memory-blocks, fetched 2026-09-20). Memory is split into
labeled blocks (`persona`, `human`, etc.) that are always in-context (no
retrieval step) plus archival memory that is retrieved. Blocks default to
agent-editable via memory tools, but a `read_only: true` flag makes a block
human-controlled only - the SAME store, same schema, with a per-block human/
agent write boundary declared in the block's own metadata rather than a
separate UI reimplementing the data. Brother's nearest equivalent is
`bm_vault_lifecycle.py`'s human-approval gate on promotions, but that gate is
enforced only at the CLI/pane layer, not per-note in the schema the way
Letta's `read_only` flag is.

**Mem0, recency-aware ranking as decay** (mem0.ai/blog/memory-decay-for-long-
running-agents-how-recency-aware-ranking-fixes-retrieval-staleness, fetched
2026-09-20). Mem0's decay is explicitly a search-time re-rank (0.3x to 1.5x
multiplier on relevance, up to 20 access timestamps per memory), never a
delete - architecturally identical in spirit to `bm_vault_decay.py`'s floor-
bounded multiplier, and it publishes its own measured effect (rank-1
correction in 3 of 4 test cases, score-separation widening from a 0.0001 to
a 0.15+ margin). Brother's decay module has no equivalent published
before/after measurement on the real vault - the WBS retrieval plan measures
recall@k for ranking changes generally but not decay specifically.

**Generative Agents memory stream** (Park et al., "Generative Agents:
Interactive Simulacra of Human Behavior," arxiv.org/abs/2304.03442, full
formula confirmed via arxiv.org/html/2304.03442v2, fetched 2026-09-20):
`score = a_recency*recency + a_importance*importance + a_relevance*relevance`,
all weights equal to 1, recency an exponential decay with factor 0.995/hour.
This is the same three-signal shape Brother's `_rrf` fusion + `_context_rank`
+ `bm_vault_decay` implement, but as three SEPARATE modules invoked in
sequence rather than one declared scoring function - a plausible
simplification target, not a missing capability.

**What to borrow**: Letta's per-block (here: per-note) human/agent write
boundary declared in the note's own frontmatter (Brother already has an
`authority:` field close to this shape, per `bm_vault_authority.py`) - extend
it to a `human_editable:`/`agent_editable:` pair rather than only enforcing
the boundary at the promotion-pane layer.

## 4. Recommendation: the front-end gap

**Not a new Obsidian plugin.** `VAULT-TRUST-BOUNDARY.md` and
`VAULT-PLUGIN-POLICY.md` both treat an Obsidian plugin as a full,
un-sandboxed in-process filesystem crossing - building a "smart search"
plugin means re-deriving ranking/decay/policy client-side in JavaScript,
duplicating `bm_vault.py`'s ~2000-line `_search`/`_rrf`/decay/staleness stack
a second time, in a language this estate does not otherwise maintain, with
no path to reuse the Python fusion logic.

**Recommendation: a separate lightweight web UI that is a pure client of the
existing `bm_vault_serve.py` HTTP API**, sibling to `bm_vault_pane.py`
(same auth posture: loopback default, token-file + TLS off-loopback) but
adding the read surface pane.py deliberately left out: a search box hitting
`POST /recall`, results annotated with the SAME fields `_print_hits` already
prints (authority, temporal state, decay-adjusted rank, demotion/conflict
flags), and a queue view over `bm_vault_pane.py`'s existing `/pending`. This
is the smallest change that (a) never bypasses the served-path boundary the
way Obsidian structurally does, (b) reuses `_search`'s fusion/decay/policy
logic exactly once, in Python, behind the API that already exists, and
(c) gives agents and humans the identical ranked view, since both would call
the same `/v1/recall` endpoint. Obsidian stays the editing surface for
prose notes (its actual strength); it is not asked to become a ranking
engine it was never built to be.

## 5. L3.2 read surface contract

The read surface is a pure HTTP client over the served recall path. It
never imports backend modules, never re-ranks rows, and never sorts,
filters, scores, decays, or demotes. It maps each served row into a
`HitView` and preserves the backend row order exactly. The runtime
guard `install_backend_guard` denies any backend module import, and
`assert_no_backend_import` checks a loaded module's namespace for
backend module objects. `recall` posts to `/v1/recall` with a bearer
token and raises `MissingField` when any required row key is absent.
`RecallQuery` validates `query` non-empty, `limit` an int between 1 and
50, and `identity`/`tenant` as str or None.

## 6. L3.3 per-note write boundary

The write boundary is opt-in per note and lives in the note's own
frontmatter, the same block `authority:` already lives in. Four keys are
required and all four are validated: `human_editable` (bool),
`agent_editable` (bool), `authority` (str), `owner` (`human` or `agent`).

`read_frontmatter(path)` is the ONE place a target file is read and
validated. It reads the file as bytes, decodes utf-8, and raises
`CorruptFrontmatter` on an empty file, absent frontmatter, truncated
frontmatter, oversized frontmatter, malformed frontmatter, non-utf-8
bytes, or a directory where a file belongs. `boundary_of` raises
`CorruptFrontmatter` for `None`, a non-mapping, a missing key, a non-bool
flag or an unknown owner; it never substitutes a default. `may_write`
returns True only when the calling actor's own flag is exactly True; an
unrecognized actor value, or a boundary that is not a `WriteBoundary`,
denies.

`write_note(path, body, actor)` calls `read_frontmatter`, then
`boundary_of`, then `may_write`, and raises `BoundaryBlocked` before the
target is opened for writing, so a denied write writes nothing at all. The
note's own frontmatter block is preserved unchanged and only the body is
replaced. `CorruptFrontmatter` is a subclass of `BoundaryBlocked`. Nothing
is written unless the note's own declaration and the caller's actor agree,
which is the same deny-unless-explicit posture `bm_vault_policy.py`
already takes.

## 7. L3.4 pending queue view over the exact action surface

The pending queue view is a pure HTTP client over `bm_vault_pane.py`'s existing `GET /pending` and `POST /act` routes. It never accepts or renders a raw action token. `pending_view` reads `action_tokens` out of the raw payload before stripping, registers both tokens per item in a fresh `ActionRegistry` keyed by `item_id`, then strips the payload for rendering and returns `(items, registry)`. `drop_token_shaped_keys` recursively drops any key whose name contains `token` case-insensitively or whose name matches exactly `^[0-9a-f]{64}$`; it raises `PendingBlocked` on non-mapping input, oversized input, or wrong value types. `act` never accepts a token from its caller: it resolves the real token for an `(item_id, decision)` pair from the registry, raises `PendingBlocked` for an unregistered pair, and posts that resolved token to `/act` with the auth token header. No token field appears in `PendingItem` or any rendered response.

## 8. L3.5 failure model and edge case reference

`failure_model.py` is the one place a named failure becomes a verdict. It
holds no client, no transport and no store, and reimplements no ranking,
policy or lifecycle logic. `decide(failure, state)` returns `BLOCK` for
`token_missing`, `token_invalid`, `principal_revoked` and `pending_blocked`;
`NO-DATA` for `response_key_missing`; and `PROCEED` only for `none`.
`rollback(action_id, state)` returns `noop` for `read` and `pending` (a
pending-queue read holds nothing to unwind), and for `recall` and `act`,
which hold no local backup in this model; it returns `restore_from_backup`
for a `write` that failed after its backup was taken.

Both raise `UnknownFailure` on any value outside the declared `Failure` and
`State` enumerations, and on hostile input: a wrong type, `None`, a bool, an
integer, NaN, bytes, a container, or an empty label. `rollback` additionally
routes every action id through one validation, so a non-string, a string that
is empty or whitespace only, a string padded with whitespace, a string
carrying a control character (a null byte included), and a string longer than
the sane limit are all unusable, and an unusable action id blocks rather than
reading as "nothing to restore". An unclassifiable value is never mapped to
a safe default and never treated as "no failure" or as "no recovery
needed"; a missing or corrupt input blocks rather than proceeding. Nothing
here substitutes a default for a declaration it cannot read.

`pending_view` is the one L3 call site wired into this model. It calls
`decide` on every failure path (`token_invalid`, `principal_revoked`,
`response_key_missing`, `pending_blocked`) and acts on the verdict before
returning: `BLOCK` raises `PendingBlocked` and returns nothing, `NO-DATA`
returns `([], registry)` with an empty item list, `PROCEED` returns the
mapped items. `rollback` is consulted for `state="pending"` and returns
`noop`, since `pending_view` only reads. `act` follows the same rule for an
unregistered `(item_id, decision)` pair, deciding `pending_blocked` and
blocking rather than proceeding. `recall` (L3.2) and `write_note` (L3.3)
landed raising their own typed exceptions (`MissingField`, `BoundaryBlocked`)
directly on every failure path; they are not wired into `decide` or
`rollback`, and that wiring is FOLLOW-UP against already shipped code, not an
L3 acceptance item.
