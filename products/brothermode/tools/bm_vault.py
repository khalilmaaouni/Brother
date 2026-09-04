#!/usr/bin/env python3
"""bm_vault: read the estate's own written memory, and surface it at the moment of need.

WHY THIS EXISTS. On 2026-08-27 a founder-facing build scored 0 of 5 on a defect this estate had
root-caused TWICE in writing, weeks earlier. Neither note was read before shipping, and one of
them would have ruled out the way the defect was being reproduced. The founder's verdict the
next morning: the tool's memory usage to avoid past mistakes is very weak, even when he warns of
them. He was right, and `docs/FEEDBACK-MEMORY-2026-08-28.md` measures the four reasons.

The gap was never that too little is written down. This estate keeps 676 vault notes and 81
project memory files. The gap is that a lesson was DUMPED ONCE at session start, into an index
that had outgrown its budget and was silently truncated, in a store disconnected from the
technical knowledge, with retrieval too weak to match a symptom to a cause.

WHAT THIS IS, and what it is honestly not. The shape follows what the current agent-memory work
converges on (MemOS: local SQLite, hybrid retrieval, zero cloud; Mem0: multi-signal retrieval;
A-MEM: notes linked into a Zettelkasten graph). One half of the standard hybrid is DELIBERATELY
ABSENT and must not be claimed: dense vector retrieval needs an embedding model, this machine
has none offline, and a lexical index alone fails exactly when a note uses different words than
the question. Three signals compensate, and the missing half is stated rather than papered over:

  A  BM25 full text over every note                 (sqlite FTS5, the library's own ranking)
  B  exact anchor match on a file path or symbol    (the case the literature says to weight
                                                     hardest for identifier lookups)
  C  link expansion across [[wikilinks]]            (the vault ALREADY carries this graph)

fused by Reciprocal Rank Fusion, which combines ranks rather than scores and so needs no
calibration between signals that are not on the same scale.

The fused score is then scaled by TIME DECAY AND REINFORCEMENT (bm_vault_decay.py, borrowed
from MemoryBank, https://arxiv.org/abs/2305.10250): an old note nobody has confirmed fades in
RANK on an Ebbinghaus curve while a confirmed one strengthens. Ranking only, bounded by a
floor: a decayed note is reordered, never removed, and nothing here ever writes to a note.

  index    build or refresh the index from the vault and every project memory directory
  refresh  the SessionStart step: index ONLY when the index is behind the notes, only the
           notes that changed, never for longer than REFRESH_BUDGET_S, and always exit 0
  status-line  the one line naming the index age, read-only, for the point-of-need hook
  recall   a symptom, in words: what has this estate already learned about this
  check    a set of file paths: what has already gone wrong in these files
  status   what is indexed, how fresh, and what is missing

STAGED RETRIEVAL (2026-08-28). Loading the dense embedder cost 30-75 SECONDS wall clock on this
machine, measured, because it starts a subprocess that imports torch and transformers and loads
bge-small-en-v1.5 from scratch every single call. Anchors (exact file/symbol match against the
query) and FTS5 BM25 run FIRST and are both sub-second; the dense embedder loads only when those
two return nothing, return fewer than the requested --limit, or disagree (no overlap in their top
results). `--fast` skips the dense stage unconditionally (for hook and session-start paths, where
a bounded budget matters more than the last bit of recall depth). `--explain` prints which signal
ran and how many milliseconds each took, so the staging is observable rather than asserted.

Python 3.9, standard library only, no network, no embedding model, no subprocess.
"""
import array
import calendar
import contextlib
import datetime
import hashlib
import importlib.util
import io
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
# C3: the config directory is resolved by brother_paths, the one seam
# that knows which coding client is running (docs/codex/HOOKS-MAPPING.md).
# Loaded from beside this file because tools/ is not a package. GUARDED, the
# same way vault_recall_hook.py guards it: a deployed snapshot directory holds
# bm_vault.py without every sibling, and an unguarded import turned that into
# a traceback at import time, so recall died instead of degrading.
sys.path.insert(0, HERE)
try:
    import brother_paths  # noqa: E402
except ImportError:  # pragma: no cover, exercised only by a partial deployment
    brother_paths = None


def _config_dir():
    """brother_paths' answer, or the pre-C3 literal when the helper is absent."""
    if brother_paths is None:
        return os.path.join(os.path.expanduser("~"), ".claude")
    return brother_paths.config_dir()


def _config_path(*parts):
    return os.path.join(_config_dir(), *parts)

#: The installer-written config, shared with vault_recall_hook.py: the D01 contract
#: (2026-08-30) is environment first, config file second, and NO guessed home path
#: when neither is set. The old home-guess default was portable in
#: spelling and machine-bound in fact: a second machine indexed an empty guess.
CONFIG_PATH = _config_path("bm_vault.json")


def _config():
    """The installer-written config, or {} when absent or unreadable. Never raises:
    a corrupt config must degrade to an audible NO-DATA at the caller, not a crash."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            loaded = json.load(f)
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        return {}


def _default_vault():
    """Precedence, highest first: an explicit --vault (checked at the one call
    site, cmd_index), then BM_VAULT_ROOT, then BROTHERMODE_VAULT, then the
    config file. None when nothing is configured: the caller refuses audibly
    rather than indexing a guessed path (D01)."""
    env_vault = os.environ.get("BM_VAULT_ROOT") or os.environ.get("BROTHERMODE_VAULT")
    if env_vault:
        return env_vault
    # A config value of the wrong shape ({"vault": 5}) must degrade to
    # unconfigured (None), never propagate a non-string into a path join at
    # the caller and crash there instead of reporting NO-DATA.
    cfg_vault = _config().get("vault")
    return cfg_vault if isinstance(cfg_vault, str) and cfg_vault else None


# ---------------------------------------------------------------------------
# Consent gate for cmd_refresh (row E54, 2026-09-04). refresh is wired
# directly at SessionStart (hooks/hooks.json) and, unlike status-line and
# recall (which vault_recall_hook.py gates before ever shelling out to this
# file), it can WRITE the index at ~/.claude/bm_vault_index.sqlite3 before
# anyone has consented to BrotherMode touching a stranger's machine. Same
# technique, same schema, same fail-CLOSED-on-any-error direction as every
# other write-capable entry point in this project (tools/bm_bash_audit.py,
# tools/vault_recall_hook.py): a private, duplicated load of scripts/
# setup.py, never a shared import, because each write-capable entry point
# owns its own gate rather than trusting a shared import to still be
# gating tomorrow.
# ---------------------------------------------------------------------------
_bm_setup_cache = []


def _load_bm_setup():
    try:
        root = os.path.dirname(_TOOLS_DIR)
        spec = importlib.util.spec_from_file_location(
            "bm_setup_for_vault", os.path.join(root, "scripts", "setup.py"))
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # sbe: allow-silent optional consent module load; _consented() fails closed on None
        return None


def _get_bm_setup():
    if not _bm_setup_cache:
        _bm_setup_cache.append(_load_bm_setup())
    return _bm_setup_cache[0]


def _consented():
    """True only when scripts/setup.py's own is_consented() says so. Fails
    CLOSED (not consented) on any load error, missing config, or a corrupt
    one."""
    mod = _get_bm_setup()
    if mod is None:
        return False
    try:
        cfg, _err = mod.read_config()
        return bool(mod.is_consented(cfg))
    except Exception:
        return False


PROJECTS_ROOT = _config_path("projects")
INDEX_PATH = _config_path("bm_vault_index.sqlite3")
# VB2-05: the answer ledger. One JSON line per recall, sitting beside the index it reads,
# so the retention tool (bm_vault_retention.py) can find and report on it with the same
# path arithmetic it already uses for INDEX_PATH.
LEDGER_PATH = os.path.join(os.path.dirname(INDEX_PATH), "bm_vault_answers.jsonl")
# The dense machine, in preference order. bge-small WON the discrimination test on this corpus
# (true answer first at 0.623); Apple's NLEmbedding LOST it (an unrelated note above the true
# answer) and stays only as the zero-dependency fallback, better than nothing and said so.
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
EMBED_BINS = [os.path.join(_TOOLS_DIR, "bm-embed-bge"), os.path.join(_TOOLS_DIR, "bm-embed")]

#: THE WALL CLOCK A SessionStart REFRESH GETS (cmd_refresh), before it stops and leaves the
#: rest for the next start. Small on purpose: a session start that waits on the index is a
#: session start someone turns off, and the whole point of E54 is a refresh nobody has to
#: remember. The work is RESUMABLE because an unfinished pass deliberately does not stamp
#: indexed_at, so the next start still sees the index as behind and carries on.
REFRESH_BUDGET_S = 5.0

#: What the dense embedder needs before it is worth starting at all: tools/bm-embed-bge pays
#: a 7 to 9 second cold model load on every call (SECURITY.md's own measurement of it), so a
#: pass holding less than this skips embedding and SAYS SO, rather than blowing the budget it
#: was handed. A hand-run `index` passes no budget and is unaffected.
EMBED_MIN_S = 10.0

WIKILINK = re.compile(r"\[\[([^\]|]+)")
# An anchor is a thing you can grep for: a source file, or a CamelCase / dotted symbol. These are
# what a person is holding when the lesson matters, which is why they get their own signal.
ANCHOR = re.compile(
    r"\b(?:[A-Za-z0-9_./-]+\.(?:swift|py|js|ts|json|sh|md|yml|yaml)"
    r"|[A-Z][A-Za-z0-9]+(?:\.[A-Za-z][A-Za-z0-9]+)+)\b")
# VB2-03: a cheap inline duplicate of bm_vault_analyzer.needs_analysis's own
# gate pattern (Hiragana, Katakana full or half width, CJK ideographs, PLUS
# full-width ASCII and the ideographic space, so a query typed entirely in
# full-width digits and letters -- no CJK script at all -- still reaches the
# seam). Checked BEFORE the CJK signal below ever imports that module, so a
# genuinely pure-ASCII query never pays the dynamic-import cost and the
# anchor/bm25 signals above run on the exact path they always have (proven
# byte-identical in test_bm_vault_analyzer.py). bm_vault_analyzer.py stays
# the one place the segmentation and dictionary logic itself lives; this is
# a hot-path gate only.
_CJK_PROBE_RE = re.compile(
    u"[\u3041-\u30FF\u3400-\u9FFF\uF900-\uFAFF\uFF01-\uFF9F\u3000]")
FRONT_NAME = re.compile(r"^name:\s*(.+)$", re.M)
FRONT_DESC = re.compile(r"^description:\s*(.+)$", re.M)
FRONT_TYPE = re.compile(r"^type:\s*(.+)$", re.M)
#: The recording contract's supersedes: field, matched EXACTLY as bm_vault_graph.py
#: matches it (whole value line, WIKILINK extracts the targets inside), so the two
#: subsystems cannot drift into two different readings of one field. An empty value,
#: which is what most notes carry, yields zero targets rather than a false edge.
FRONT_SUPERSEDES = re.compile(r"^supersedes:\s*(.*)$", re.M)
# D10 (vault benchmark v2): contradicts: names a note this one conflicts with, not
# supersedes. Unlike supersedes:, retrieval must never withhold either side -- both
# carry authority and evidence -- so this is read into its own table and surfaced as
# a flag on an ordinary hit, never as a WITHHELD branch. Symmetric like relates: in
# bm_vault_graph.py: expanded both ways at rebuild time so either side surfaces it.
FRONT_CONTRADICTS = re.compile(r"^contradicts:\s*(.*)$", re.M)
RRF_K = 60          # the usual constant; large enough that no single signal dominates rank 1


def _connect():
    con = sqlite3.connect(INDEX_PATH)
    con.row_factory = sqlite3.Row
    return con


def _load_bm_repo_scope():
    """Dynamic import by path, the same defensive pattern as
    _load_bm_freshness right below: E76 per-repository hook scoping,
    checked at the top of cmd_refresh before anything is touched."""
    try:
        spec = importlib.util.spec_from_file_location(
            "bm_repo_scope_for_vault", os.path.join(_TOOLS_DIR, "bm_repo_scope.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # sbe: allow-silent optional gate module load; hooks_off degrades to active when this returns None
        return None


def _load_bm_freshness():
    """Dynamic import by path, the same pattern bm_freshness.py already uses (in the other
    direction) to load THIS module. Read-only use here: classify_live, _default_roots,
    _state_connect, STATE_DB -- the recall path never writes its own freshness verdicts anywhere
    but bm_freshness's own small state db, and never touches this file's own index."""
    spec = importlib.util.spec_from_file_location(
        "bm_freshness", os.path.join(_TOOLS_DIR, "bm_freshness.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_bm_vault_lifecycle():
    """Dynamic import by path, the same defensive pattern as _load_bm_freshness right
    above: this file sets up no sys.path of its own, so a bare `import
    bm_vault_lifecycle` would only resolve by accident of cwd and fail outright in a
    deployed snapshot directory. Read-only use here: read_promotion, the one
    classifier D12 needs to tell an unvalidated candidate from everything else."""
    spec = importlib.util.spec_from_file_location(
        "bm_vault_lifecycle", os.path.join(_TOOLS_DIR, "bm_vault_lifecycle.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_enrichment():
    """The contract modules that annotate a served hit (VB-12, the D01 done_check:
    recall returns memory IDs, authority, temporal state, evidence and provenance).
    Same by-path load as _load_bm_freshness, but each one GUARDED independently:
    these are sibling contract modules that may be absent in a deployed copy of the
    tree, and an absent one must degrade its own annotation line, never the hit."""
    mods = {}
    for fname, mname in (("bm_vault_ids.py", "bm_vault_ids"),
                         ("bm_vault_temporal.py", "bm_vault_temporal"),
                         ("bm_vault_asof.py", "bm_vault_asof"),
                         ("bm_vault_authority.py", "bm_vault_authority"),
                         ("bm_vault_provenance.py", "bm_vault_provenance")):
        try:
            spec = importlib.util.spec_from_file_location(
                mname, os.path.join(_TOOLS_DIR, fname))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mods[mname] = mod
        except Exception:
            mods[mname] = None
    return mods


def _load_bm_vault_authority():
    """Dynamic import by path, the same pattern _load_bm_freshness uses. The contract module
    (bm_vault_authority.py, PR 73) is the ONE owner of the vocabulary and the comparator; this
    file only calls it, so retrieval can never drift into a second reading of the same field."""
    spec = importlib.util.spec_from_file_location(
        "bm_vault_authority", os.path.join(_TOOLS_DIR, "bm_vault_authority.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_bm_vault_analyzer():
    """Dynamic import by path, the same pattern _load_bm_freshness uses. VB2-03: the
    Japanese-first analyzer seam (normalize, segment, kana_alias, analyze, has_cjk).
    Loaded only when _CJK_PROBE_RE already matched the query text, so a pure-ASCII
    query never pays this cost."""
    spec = importlib.util.spec_from_file_location(
        "bm_vault_analyzer", os.path.join(_TOOLS_DIR, "bm_vault_analyzer.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_bm_vault_staleness():
    """Dynamic import by path, the same pattern _load_bm_vault_authority uses right above.
    (VB2-06.) The contract module (bm_vault_staleness.py) is the ONE owner of the horizon
    table and the fresh/stale/unverified-no-clock vocabulary; this file only calls
    is_stale, so the demotion seam can never drift into a second reading of verified_at."""
    spec = importlib.util.spec_from_file_location(
        "bm_vault_staleness", os.path.join(_TOOLS_DIR, "bm_vault_staleness.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_bm_vault_decay():
    """Dynamic import by path, the same pattern _load_bm_vault_staleness uses right
    above. (E57 mechanism 2, borrowed from MemoryBank, https://arxiv.org/abs/2305.10250.)
    The contract module (bm_vault_decay.py) is the ONE owner of the curve, the floor
    and the sidecar store, so retrieval can never grow a second opinion about how fast
    a note fades. Guarded at the caller exactly like staleness: an ABSENT module means
    no decay at all, stated on stderr, never a crash."""
    spec = importlib.util.spec_from_file_location(
        "bm_vault_decay", os.path.join(_TOOLS_DIR, "bm_vault_decay.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_bm_vault_policy():
    """Dynamic import by path, the same pattern _load_bm_vault_staleness uses right
    above. (VB2-01.) The contract module (bm_vault_policy.py) is the ONE owner of
    the policy file's shape and the allow/deny decision; this file only calls
    policy_path, load and decide, so the trim can never drift into a second
    reading of the same rules. Guarded at the caller: an ABSENT module means no
    trimming, stated on stderr, never a crash -- exactly the degradation every
    sibling contract module already keeps."""
    spec = importlib.util.spec_from_file_location(
        "bm_vault_policy", os.path.join(_TOOLS_DIR, "bm_vault_policy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_bm_vault_principals():
    """Dynamic import by path, the same pattern _load_bm_vault_policy uses right above.
    (VB7-05.) The contract module (bm_vault_principals.py) is the ONE owner of the
    principal registry's shape and the active/revoked read; this file only calls
    registry_path, load and status_of, so the revocation check can never drift into a
    second reading of the same file. Guarded at the caller: an ABSENT module means no
    revocation is enforced, stated on stderr, never a crash -- the same degradation
    every sibling contract module already keeps."""
    spec = importlib.util.spec_from_file_location(
        "bm_vault_principals", os.path.join(_TOOLS_DIR, "bm_vault_principals.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_bm_vault_audit():
    """Dynamic import by path, the same pattern _load_bm_vault_policy uses right above.
    (VB7-04.) The contract module (bm_vault_audit.py) is the ONE owner of the access-audit
    file's shape and path; this file only calls append, so the audit trail can never drift
    into a second reading of the same record. Guarded at the caller: an ABSENT module means
    no audit record for this recall, stated on stderr, never a crash -- the same degradation
    every sibling contract module already keeps."""
    spec = importlib.util.spec_from_file_location(
        "bm_vault_audit", os.path.join(_TOOLS_DIR, "bm_vault_audit.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _schema(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY, path TEXT UNIQUE, title TEXT, descr TEXT,
            source TEXT, kind TEXT, mtime REAL, body TEXT);
        CREATE TABLE IF NOT EXISTS anchors (note_id INTEGER, anchor TEXT);
        CREATE INDEX IF NOT EXISTS anchors_a ON anchors(anchor);
        CREATE TABLE IF NOT EXISTS links (note_id INTEGER, target TEXT);
        CREATE INDEX IF NOT EXISTS links_t ON links(target);
        CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
            title, descr, body, content='notes', content_rowid='id');
        CREATE TABLE IF NOT EXISTS vectors (note_id INTEGER PRIMARY KEY, v BLOB);
        CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
        -- Which note stems have been SUPERSEDED, and by which note. Retrieval
        -- read supersession only as a substring of a path until 2026-08-29,
        -- so a note formally superseded in frontmatter still surfaced at point
        -- of need as though it were current.
        CREATE TABLE IF NOT EXISTS supersessions (stem TEXT, by_note_id INTEGER);
        CREATE INDEX IF NOT EXISTS supersessions_s ON supersessions(stem);
        -- Which note stems contradict which other notes, symmetric (both directions
        -- stored so a hit surfaces the conflict whichever side declared it).
        CREATE TABLE IF NOT EXISTS contradictions (stem TEXT, other_title TEXT);
        CREATE INDEX IF NOT EXISTS contradictions_s ON contradictions(stem);
        -- VB5-03: one cached dense query vector per (embed model, exact query text), so a
        -- repeated query never pays the per-call subprocess/model-load cost twice. Bounded
        -- (see QUERY_CACHE_MAX) and evicted oldest-last_used-first, the same shape as any
        -- small LRU.
        CREATE TABLE IF NOT EXISTS query_cache (qhash TEXT PRIMARY KEY, v BLOB, last_used REAL);
    """)
    # content_hash: added after notes already shipped, so an existing index.sqlite3 predates
    # the column. ALTER TABLE ADD COLUMN, guarded, rather than a fresh CREATE TABLE, so a
    # deployed index keeps every row it already has (never-lose-work).
    cols = {r[1] for r in con.execute("PRAGMA table_info(notes)").fetchall()}
    if "content_hash" not in cols:
        con.execute("ALTER TABLE notes ADD COLUMN content_hash TEXT")


# A LESSON is distilled guidance ("this is how it fails, do this instead"). A LOG is provenance
# ("here is what happened that night"). Both are worth keeping and they answer DIFFERENT
# questions, but a log mentions everything it touched, so on any lexical signal the logs bury the
# lessons: asking the night's real symptom returned four session logs and neither of the two
# notes that actually root-caused it. Separating the two and ranking lessons first is the whole
# fix, and it is why this tool answers where a single undifferentiated index did not.
#
# P11: type: data_semantic (a team-agreed metric definition) and type: test_oracle (an approved
# expected-result source) are accepted here the same way every type this function does not name
# explicitly already is: neither is a log-shaped type above, so both fall through to "lesson" and
# index, retrieve and rank exactly like any other lesson. Their two extra frontmatter fields,
# source_receipt (which run produced the note) and human_approved (whether a person signed off on
# it), are body text as far as this indexer is concerned; vault_recall_hook.py's lesson_states is
# the reader that acts on human_approved before a drafted note is ever shown as advice.
def _classify(path, body):
    low = path.replace("\\", "/").lower()
    m = FRONT_TYPE.search(body[:1200])
    ftype = (m.group(1).strip().lower() if m else "")
    if ftype in ("session-log", "index", "overview", "context-pack"):
        return "log"
    if "/sessions/" in low or "/telemetry/" in low or "/handovers/" in low:
        return "log"
    # An AGGREGATE page (an index, a home page, an open-items list, a canvas) mentions every
    # topic in the estate and therefore matches every query, which is how "Open Items" ranked
    # above the note that actually root-caused a defect. It is a table of contents, not a lesson.
    base = os.path.basename(low)
    if base in ("memory.md", "home.md", "open-items.md", "failures-index.md", "readme.md",
                "index.md", "canvas.md", "state.md") or base.startswith("canvas-"):
        return "log"
    return "lesson"


def _walk(roots):
    for root, source in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            # A folder that names itself superseded or archived is history, not memory: indexing
            # it puts three copies of one note into every result list (measured: 35 duplicate
            # titles, one of them from a folder literally called superseded-session-1), which is
            # the founder's "growing the vault for no reason" made visible.
            low = dirpath.lower()
            if "superseded" in low or "/archive" in low or "/attic" in low:
                continue
            for fn in filenames:
                if fn.endswith(".md"):
                    yield os.path.join(dirpath, fn), source


def _content_hash(body):
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _upsert_note(con, path, title, descr, source, kind, mtime, body):
    """One note INSERT-or-UPDATE, fts/anchors/links kept in lockstep. Shared by the file walk and
    the correction-rule loader below so the two note sources have exactly one place to drift out
    of sync. Returns True on a fresh insert, False on a full refresh, "touched" when the mtime
    moved but the content did not (see below), None when skipped clean (mtime unchanged) -- the
    same result shape the file loop already relied on inline, extended by one case.

    VB5-03 content-hash gate: a bumped mtime with UNCHANGED content (a touch, a git checkout that
    resets timestamps, an rsync) is common and, before this gate, paid the full cost anyway --
    anchors/links/fts rebuilt and the vector deleted for a re-embed that would recompute the exact
    same numbers. The mtime check above (in the caller, before the file is even read) is still the
    first and cheapest filter for the true no-op case; content_hash is the second, exact check for
    the "touched but not modified" case that mtime alone cannot distinguish from a real edit."""
    chash = _content_hash(body)
    row = con.execute("SELECT id, mtime, content_hash FROM notes WHERE path=?", (path,)).fetchone()
    if row and abs(row["mtime"] - mtime) < 0.001:
        return None
    if row and row["content_hash"] == chash:
        con.execute("UPDATE notes SET mtime=? WHERE id=?", (mtime, row["id"]))
        return "touched"
    if row:
        nid = row["id"]
        con.execute("UPDATE notes SET title=?,descr=?,source=?,kind=?,mtime=?,body=?,"
                    "content_hash=? WHERE id=?",
                    (title, descr, source, kind, mtime, body, chash, nid))
        con.execute("DELETE FROM anchors WHERE note_id=?", (nid,))
        con.execute("DELETE FROM links WHERE note_id=?", (nid,))
        con.execute("DELETE FROM notes_fts WHERE rowid=?", (nid,))
        con.execute("DELETE FROM vectors WHERE note_id=?", (nid,))
        fresh = False
    else:
        cur = con.execute(
            "INSERT INTO notes (path,title,descr,source,kind,mtime,body,content_hash) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (path, title, descr, source, kind, mtime, body, chash))
        nid = cur.lastrowid
        fresh = True
    con.execute("INSERT INTO notes_fts (rowid,title,descr,body) VALUES (?,?,?,?)",
                (nid, title, descr, body))
    anchors = {a for a in ANCHOR.findall(body)}
    con.executemany("INSERT INTO anchors (note_id,anchor) VALUES (?,?)",
                    [(nid, a) for a in anchors])
    con.executemany("INSERT INTO links (note_id,target) VALUES (?,?)",
                    [(nid, t.strip()) for t in set(WIKILINK.findall(body))])
    return fresh


CORRECTION_RULE_PATH_PREFIX = "correction-rule:"


def _iso_to_epoch(s):
    """bm_store's own timestamp shape ("2026-07-29T06:20:10Z") to epoch seconds, so a rule's
    updated_at can drive the SAME mtime-diff reindex check the file walk already uses -- no
    separate freshness mechanism invented for this second source. calendar.timegm (not
    time.mktime) because the struct is UTC already and must not pick up the local offset."""
    try:
        return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return time.time()


def _correction_rules():
    """Approved correction rules from tools/bm_store.py's SQLite store (the single writer
    tools/bm_learn.py delegates every mutation to), filtered to the SAME states bm_learn's own
    `rules` and `apply` commands already treat as live: bm_learning.INJECTABLE_STATES (settled,
    confirmed, approved). That excludes superseded, contradicted, deprecated and forgotten --
    the existing filter, not a new one invented here.

    Returns [] and prints a NO-DATA line when no store is reachable from the current directory
    (no project root, or one that has never run `bm_learn.py capture`): a project with no
    correction store yet must not fail the whole vault reindex."""
    try:
        bs_spec = importlib.util.spec_from_file_location(
            "bm_store", os.path.join(_TOOLS_DIR, "bm_store.py"))
        bs = importlib.util.module_from_spec(bs_spec)
        bs_spec.loader.exec_module(bs)
        l_spec = importlib.util.spec_from_file_location(
            "bm_learning", os.path.join(_TOOLS_DIR, "bm_learning.py"))
        L = importlib.util.module_from_spec(l_spec)
        l_spec.loader.exec_module(L)
        root, _source = bs.require_root()
        store = bs.Store(root, create=False)
    except Exception as e:
        print("NO-DATA correction-rule store: %s" % e)
        return []
    try:
        return store.list_learning_rules(states=L.INJECTABLE_STATES)
    finally:
        store.close()


def _index_correction_rules(con, seen):
    added = updated = touched = 0
    for rule in _correction_rules():
        path = CORRECTION_RULE_PATH_PREFIX + rule["rule_uuid"]
        seen.add(path)
        mtime = _iso_to_epoch(rule["updated_at"])
        title = rule["trigger_text"][:160] or rule["rule_uuid"]
        descr = rule["action_text"][:200]
        body = ("When: %s\nDo: %s\nBecause: %s\nScope: %s%s  State: %s  Severity: %s"
                % (rule["trigger_text"], rule["action_text"], rule.get("because_text") or "",
                   rule["scope_type"],
                   (":" + rule["scope_key"] if rule["scope_key"] else ""),
                   rule["state"], rule["severity"]))
        fresh = _upsert_note(con, path, title, descr, "correction-rule", "lesson", mtime, body)
        if fresh is True:
            added += 1
        elif fresh is False:
            updated += 1
        elif fresh == "touched":
            touched += 1
    return added, updated, touched


def _index_roots(vault):
    """The roots the index walks: the vault, plus every project memory directory. ONE
    definition, shared by cmd_index and the staleness check below, so the check can never
    measure a different set of notes than the index actually reads."""
    roots = [(vault, "vault")]
    if os.path.isdir(PROJECTS_ROOT):
        for entry in sorted(os.listdir(PROJECTS_ROOT)):
            mem = os.path.join(PROJECTS_ROOT, entry, "memory")
            if os.path.isdir(mem):
                roots.append((mem, "project-memory"))
    return roots


def _budget_seconds(args):
    """The --budget in seconds, or None for an unbudgeted (hand-run) pass. A malformed
    budget is unbudgeted rather than a crash mid-index: this value arrives from a hook."""
    budget = args.get("budget")
    if budget is None or budget is True:
        return None
    try:
        return float(budget)
    except (TypeError, ValueError):
        return None


def _deadline(args):
    """Absolute wall-clock stop time for this pass, or None when it is unbudgeted. Takes the
    args dict (never a bare number) so the string a CLI --budget hands over is coerced in one
    place instead of reaching arithmetic as text."""
    seconds = _budget_seconds(args)
    return None if seconds is None else time.time() + seconds


def _unindexed(con, roots):
    """How many notes on disk the index does not hold at their CURRENT mtime, which is the
    whole staleness question. One walk and one query: it pays a stat per note and skips the
    file read, the front-matter parse and every write, which is what makes it cheap enough
    to run at every session start whether or not anything changed.

    None (never 0) when the index cannot be read, so the caller says NO-DATA rather than
    reporting a clean index it never actually measured."""
    known = {}
    try:
        for row in con.execute("SELECT path, mtime FROM notes"):
            known[row["path"]] = row["mtime"]
    except sqlite3.Error:
        return None  # sbe: allow-silent unreadable notes table; both callers already turn this
        # exact None into a printed NO-DATA line (cmd_status_line, cmd_refresh), never a clean count
    behind = 0
    for path, _source in _walk(roots):
        try:
            mtime = os.path.getmtime(path)
        except OSError:  # sbe: allow-silent file raced-deleted between walk and stat; the next pass counts it
            continue
        was = known.get(path)
        # The same 0.001s tolerance cmd_index uses to decide a note is unchanged, so the
        # staleness check and the indexer can never disagree about one note.
        if was is None or abs(was - mtime) >= 0.001:
            behind += 1
    return behind


def _status_line(con, roots, behind=None):
    """The one line the session-start refresh and the point-of-need hook both print, so a
    stale index is visible where the work happens instead of only when somebody remembers to
    run `status`. `behind` is passed in when the caller already measured it, so a refresh
    never walks the vault twice for one line."""
    try:
        total = con.execute("SELECT COUNT(*) c FROM notes").fetchone()["c"]
        at = con.execute("SELECT v FROM meta WHERE k='indexed_at'").fetchone()
    except sqlite3.Error as exc:
        return "vault-index: NO-DATA: the index could not be read (%s)" % exc
    if at is None:
        age = "NEVER"
    else:
        try:
            age = "%d minutes ago" % int((time.time() - float(at["v"])) / 60.0)
        except (TypeError, ValueError):
            age = "NEVER"
    if behind is None:
        behind = _unindexed(con, roots)
    return "vault-index: last indexed %s, %d notes, %s unindexed" % (
        age, total, "NO-DATA" if behind is None else behind)


def cmd_status_line(args):
    """READ-ONLY: print the status line and touch nothing. vault_recall_hook.py runs this
    once per session, inside a PreToolUse hook, so it must never index and never be slow.
    Always exits 0: an unreadable index is a NO-DATA line, never a blocked edit."""
    vault = args.get("vault") or _default_vault()
    if not vault:
        print("vault-index: NO-DATA: no vault root configured (%s), so nothing is indexed "
              "and point-of-need recall is empty" % CONFIG_PATH)
        return 0
    try:
        con = _connect()
        _schema(con)
        print(_status_line(con, _index_roots(vault)))
        con.close()
    except (sqlite3.Error, OSError) as exc:
        print("vault-index: NO-DATA: the index could not be read (%s)" % exc)
    return 0


def cmd_refresh(args):
    """THE SessionStart STEP (readiness row E54). The index the point-of-need recall hook
    serves from had nothing refreshing it: measured 2026-09-03, 79 hours stale with 61 notes
    unindexed, so every lesson written that week was invisible at the moment of need.

    Three properties, in this order, because a session start is not a place to be clever:
    a CHEAP staleness check first (stat per note, no reads, no writes); a refresh only when
    the index is actually behind, and only of the notes that changed; and a hard wall-clock
    budget after which it stops and leaves the rest for the next start. It FAILS OPEN on
    everything: every path prints one `vault-index:` line and returns 0, because a session
    that will not start is worse than an index a few notes behind.

    GATED ON CONSENT (row E54, tools/test_bm_consent.py): this is wired directly at
    SessionStart, and unlike status-line and recall (which vault_recall_hook.py gates
    before ever shelling out to this file), it can write the index at
    ~/.claude/bm_vault_index.sqlite3 on a stranger's machine before anyone has consented.
    Checked first, before the index is even touched, and printed as a NO-DATA line rather
    than silence, so an unconsented install still says why nothing was refreshed."""
    # E76: per-repository hook scoping, checked before consent (a repository
    # that turned hooks off should not even see the consent NO-DATA line).
    _rs = _load_bm_repo_scope()
    if _rs is not None:
        try:
            _payload = sys.stdin.read()
        except (OSError, ValueError):
            _payload = None
        if _rs.hooks_off(payload=_payload):
            return 0
    if not _consented():
        print("vault-index: NO-DATA: setup is not complete yet; run: "
              "python3 scripts/setup.py")
        return 0
    vault = args.get("vault") or _default_vault()
    if not vault:
        print("vault-index: NO-DATA: no vault root configured. Set BM_VAULT_ROOT or write "
              "{\"vault\": \"...\"} to %s; point-of-need recall stays empty until then."
              % CONFIG_PATH)
        return 0
    budget = _budget_seconds(args)
    if budget is None:
        budget = REFRESH_BUDGET_S
    try:
        roots = _index_roots(vault)
        con = _connect()
        _schema(con)
        behind = _unindexed(con, roots)
    except (sqlite3.Error, OSError) as exc:
        print("vault-index: NO-DATA: the index could not be read (%s); recall serves whatever "
              "it already holds" % exc)
        return 0
    if behind == 0:
        # The common case, and the reason the check comes first: nothing to do, nothing
        # written, one line printed.
        print(_status_line(con, roots, behind))
        con.close()
        return 0
    con.close()
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured):
            cmd_index({"vault": vault, "budget": budget, "paths": []})
    except (sqlite3.Error, OSError, ValueError) as exc:
        print("vault-index: NO-DATA: the refresh failed (%s); recall serves whatever it "
              "already holds" % exc)
        return 0
    finally:
        for line in captured.getvalue().splitlines():
            if line.strip():
                print("vault-index: %s" % line.strip())
    try:
        con = _connect()
        print(_status_line(con, roots))
        con.close()
    except (sqlite3.Error, OSError) as exc:
        print("vault-index: NO-DATA: the index could not be read after the refresh (%s)" % exc)
    return 0


def cmd_index(args):
    vault = args.get("vault") or _default_vault()
    if not vault:
        print("NO-DATA vault root: nothing configured. Pass --vault, set BM_VAULT_ROOT or "
              "BROTHERMODE_VAULT, or write {\"vault\": \"...\"} to %s. Refusing to index a "
              "guessed path (D01)." % CONFIG_PATH)
        return 2
    roots = _index_roots(vault)
    con = _connect()
    _schema(con)
    # A budget arrives only from cmd_refresh (the SessionStart step). A hand-run index passes
    # none and runs to completion, exactly as it always has.
    deadline = _deadline(args)
    seen, added, updated, touched, stopped = set(), 0, 0, 0, False
    for path, source in _walk(roots):
        if deadline is not None and time.time() >= deadline:
            stopped = True
            break
        seen.add(path)
        try:
            mtime = os.path.getmtime(path)
        except OSError:  # sbe: allow-silent file raced-deleted between walk and stat, skip this run, next index pass corrects
            continue
        # Checked BEFORE the read, same as before this refactor: _upsert_note would catch an
        # unchanged mtime too, but only after paying for the disk read and front-matter parse on
        # every unchanged note on every run, which is exactly the cost this early exit avoids.
        row = con.execute("SELECT id, mtime FROM notes WHERE path=?", (path,)).fetchone()
        if row and abs(row["mtime"] - mtime) < 0.001:
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                body = f.read()
        except (IOError, OSError):
            continue
        m = FRONT_NAME.search(body[:1200])
        title = (m.group(1).strip() if m
                 else os.path.splitext(os.path.basename(path))[0].replace("-", " "))
        d = FRONT_DESC.search(body[:1200])
        descr = d.group(1).strip() if d else ""
        kind = _classify(path, body)
        fresh = _upsert_note(con, path, title, descr, source, kind, mtime, body)
        if fresh is True:
            added += 1
        elif fresh is False:
            updated += 1
        elif fresh == "touched":
            touched += 1
    if stopped:
        # EVERYTHING BELOW THIS POINT ASSUMES A COMPLETE WALK, and two steps would do real
        # damage without one: the removal pass deletes every note missing from `seen` (which
        # after a break is most of them), and the indexed_at stamp would tell the next pass
        # the index is current when it is not. So an interrupted pass commits the rows it
        # did write, keeps the OLD stamp, and stops.
        con.commit()
        done = added + updated + touched
        print("stopped at the %.1fs budget after %d note(s) this pass (%d new, %d refreshed, "
              "%d unchanged); the last-indexed stamp is unchanged so the next pass resumes"
              % (_budget_seconds(args) or 0.0, done, added, updated, touched))
        return 0
    rule_added, rule_updated, rule_touched = _index_correction_rules(con, seen)
    added += rule_added
    updated += rule_updated
    touched += rule_touched
    # Dense vectors for every note missing one (new and refreshed both land here, because a
    # refresh deleted nothing from vectors but the text may have changed; re-embed on mtime
    # change is handled by deleting the row alongside the fts rebuild above).
    current_model = os.path.basename(_embed_bin() or "none")
    prev = con.execute("SELECT v FROM meta WHERE k='embed_model'").fetchone()
    prev_name = prev["v"] if prev is not None else "unknown"
    # "unknown" counts as a change too: vectors written before the model stamp existed are from
    # whatever machine was present then, and a 512-dim Apple row cosined against a 384-dim bge
    # query would not error, zip() would silently TRUNCATE, which is a wrong number, not a crash.
    if prev_name != current_model and con.execute(
            "SELECT COUNT(*) c FROM vectors").fetchone()["c"]:
        con.execute("DELETE FROM vectors")
        print("embed model changed %s -> %s: all vectors cleared for re-embedding"
              % (prev_name, current_model))
    con.execute("INSERT OR REPLACE INTO meta (k,v) VALUES ('embed_model',?)", (current_model,))
    pending = con.execute(
        "SELECT n.id, n.title, n.descr, n.body FROM notes n "
        "LEFT JOIN vectors v ON v.note_id = n.id WHERE v.note_id IS NULL").fetchall()
    if pending and deadline is not None and (deadline - time.time()) < EMBED_MIN_S:
        # The honest half of E54's third ask: the embedder is local and already wired here,
        # but its cold load alone is longer than a session-start budget, so under a budget it
        # is not started and the count is reported instead of a silent skip. Nothing is
        # invented and no network is called: `bm_vault.py index` by hand embeds these.
        print("NO-DATA: %d note(s) without a dense embedding, reason: the embedder needs "
              "about %.0f seconds to load and this pass has %.1f left; run "
              "`bm_vault.py index` by hand to embed them"
              % (len(pending), EMBED_MIN_S, max(0.0, deadline - time.time())))
    elif pending:
        vecs = _embed_texts([(r["id"], "%s. %s. %s" % (r["title"], r["descr"], r["body"][:900]))
                             for r in pending])
        if vecs is None:
            print("NO-DATA dense signal: no embed machine present; %d note(s) stay lexical only"
                  % len(pending))
        else:
            con.executemany("INSERT OR REPLACE INTO vectors (note_id, v) VALUES (?,?)",
                            [(i, _pack(v)) for i, v in vecs.items()])
            print("embedded %d of %d pending note(s)" % (len(vecs), len(pending)))
    removed = 0
    for row in con.execute("SELECT id, path FROM notes").fetchall():
        if row["path"] not in seen:
            con.execute("DELETE FROM notes WHERE id=?", (row["id"],))
            con.execute("DELETE FROM notes_fts WHERE rowid=?", (row["id"],))
            con.execute("DELETE FROM vectors WHERE note_id=?", (row["id"],))
            con.execute("DELETE FROM anchors WHERE note_id=?", (row["id"],))
            con.execute("DELETE FROM links WHERE note_id=?", (row["id"],))
            removed += 1
    _rebuild_supersessions(con)
    _rebuild_contradictions(con)
    con.execute("INSERT OR REPLACE INTO meta (k,v) VALUES ('indexed_at',?)", (str(time.time()),))
    con.commit()
    total = con.execute("SELECT COUNT(*) c FROM notes").fetchone()["c"]
    print("indexed %d note(s): %d new, %d refreshed, %d gone, %d touched (content hash "
          "unchanged, mtime only)" % (total, added, updated, removed, touched))
    return 0


def _rebuild_supersessions(con):
    """Rebuild the supersession index from the bodies ALREADY IN THE DATABASE.

    Rebuilt whole rather than maintained per note inside _upsert_note, and that
    is the load-bearing choice. _upsert_note returns early on an unchanged
    mtime, so a per-note write would leave every note that predates this
    feature with no row, forever, and the fix would silently do nothing on
    every existing index while looking installed. Reading notes.body costs no
    file IO because the body is already stored.

    Frontmatter only, never prose: a note DISCUSSING supersession must not
    thereby supersede anything.
    """
    con.execute("DELETE FROM supersessions")
    rows = []
    for r in con.execute("SELECT id, body FROM notes").fetchall():
        m = FRONT_SUPERSEDES.search((r["body"] or "")[:1200])
        if not m:
            continue
        for raw in WIKILINK.findall(m.group(1)):
            stem = raw.strip().split("/")[-1].strip()
            if stem.lower().endswith(".md"):
                stem = stem[:-3]
            if stem:
                rows.append((stem, r["id"]))
    con.executemany("INSERT INTO supersessions (stem, by_note_id) VALUES (?,?)", rows)
    return len(rows)


def _superseded_by(con, path):
    """The titles of the notes that supersede this one, or an empty list.

    Tolerates an index built before the table existed, because an older
    database is a real state and a crash there would take down every query.
    """
    stem = os.path.splitext(os.path.basename(path or ""))[0]
    if not stem:
        return []
    try:
        rows = con.execute(
            "SELECT n.title AS t FROM supersessions s JOIN notes n ON n.id = s.by_note_id "
            "WHERE s.stem = ?", (stem,)).fetchall()
    except sqlite3.Error:
        return []
    return [r["t"] for r in rows]


def _frontmatter_block(body):
    """The text between the opening and closing --- fences, or "" outside one.

    Same shape as bm_vault_graph.py's helper of the same name: a plain
    body[:1200] slice (the old technique here) greps PROSE too, so a note
    merely discussing "contradicts: [[X]]" in its own text forges an edge,
    and frontmatter past 1200 chars loses a real one. Parsing the fenced
    block only fixes both directions at once.
    """
    if not body.startswith("---"):
        return ""
    end = body.find("\n---", 3)
    return body[3:end] if end != -1 else ""


def _rebuild_contradictions(con):
    """Same load-bearing rebuild-whole choice as _rebuild_supersessions and for the
    identical reason: _upsert_note returns early on an unchanged mtime, so a per-note
    write would leave every note written before this feature existed with no row.

    Frontmatter only, never prose, same as supersedes:. Stored symmetric: a pair
    (A contradicts B) is written as two rows so a query on either stem finds it,
    which is what lets retrieval flag a hit without knowing in advance which side of
    the pair declared the edge.
    """
    con.execute("DELETE FROM contradictions")
    by_stem_title = {}
    for r in con.execute("SELECT path, title FROM notes").fetchall():
        stem = os.path.splitext(os.path.basename(r["path"] or ""))[0]
        if stem:
            by_stem_title[stem] = r["title"]
    pairs = []
    for r in con.execute("SELECT path, title, body FROM notes").fetchall():
        m = FRONT_CONTRADICTS.search(_frontmatter_block(r["body"] or ""))
        if not m:
            continue
        my_stem = os.path.splitext(os.path.basename(r["path"] or ""))[0]
        for raw in WIKILINK.findall(m.group(1)):
            target = raw.strip()
            # Strip a #Section anchor the same way the graph gate resolves
            # [[Note#Section]], so a contradicts: target with an anchor still
            # surfaces at recall instead of missing by_stem_title on the
            # anchor-qualified string.
            if "#" in target:
                target = target.split("#", 1)[0].strip()
            target_stem = target.split("/")[-1].strip()
            if target_stem.lower().endswith(".md"):
                target_stem = target_stem[:-3]
            target_title = by_stem_title.get(target_stem)
            if not my_stem or not target_title:
                continue  # unresolved target: bm_vault_graph's check gate is the
                          # honesty path for a dangling contradicts:, not recall
            pairs.append((my_stem, target_title))
            pairs.append((target_stem, r["title"]))
    con.executemany("INSERT INTO contradictions (stem, other_title) VALUES (?,?)", pairs)
    return len(pairs)


def _contradicted_by(con, path):
    """The titles of the notes that contradict this one, or an empty list. Tolerates
    an index built before the table existed, same guard as _superseded_by."""
    stem = os.path.splitext(os.path.basename(path or ""))[0]
    if not stem:
        return []
    try:
        rows = con.execute("SELECT other_title FROM contradictions WHERE stem = ?",
                           (stem,)).fetchall()
    except sqlite3.Error:
        return []
    return sorted({r["other_title"] for r in rows})


def _embed_bin():
    for b in EMBED_BINS:
        if os.path.exists(b):
            return b
    return None


def _embed_texts(pairs, query=False):
    """pairs: [(id, text)]. Returns {id: [float]} for what embedded, None when NO machine is
    present. The dense signal ARRIVING EMPTY must be a stated NO-DATA at the caller, never a
    silent shrug, because a missing signal that looks like "no matches" is the lying shape this
    estate keeps paying for. The embedder itself skips unparseable rows rather than zeroing
    them: a zero vector ranks everywhere. query=True marks asymmetric-encoder queries (bge needs
    its instruction prefix on the query side only).

    VB5-06: tries the warm daemon (tools/bm_embed_warm.py) first, a fast loopback call with a
    tight connect timeout. On ANY failure -- daemon absent, refused, timeout, malformed reply --
    embed_via_warm returns None and this falls back to _embed_texts_subprocess below, the
    original path, UNCHANGED. Silent-correct: no exception, no behavior change in results,
    at most a NO-DATA note on stderr (BM_EMBED_WARM_DEBUG=1) naming why the warm hop was
    skipped."""
    try:
        from bm_embed_warm import embed_via_warm
        warm = embed_via_warm(pairs, query=query)
    except Exception:
        warm = None
    if warm is not None:
        return warm
    return _embed_texts_subprocess(pairs, query=query)


def _embed_texts_subprocess(pairs, query=False):
    """The original subprocess embed path, factored out of _embed_texts so VB5-06's warm hop
    can sit in front of it and so bm_embed_warm.py's own `measure` subcommand can time this
    path directly, without also paying for a warm-daemon round trip."""
    binpath = _embed_bin()
    if binpath is None:
        return None
    lines = "\n".join(json.dumps({"id": i, "text": t, "query": query}) for i, t in pairs)
    try:
        out = subprocess.run([binpath], input=lines.encode("utf-8"),
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=max(120, 2 * len(pairs)))
    except Exception:  # sbe: allow-silent embed subprocess failure or timeout, caller falls back to non-vector recall
        return None
    vecs = {}
    for line in out.stdout.decode("utf-8", "replace").splitlines():
        try:
            row = json.loads(line)
            vecs[row["id"]] = row["v"]
        except (ValueError, KeyError):
            continue
    return vecs


def _pack(vec):
    return array.array("f", vec).tobytes()


def _unpack(blob):
    a = array.array("f")
    a.frombytes(blob)
    return a


# VB5-03 warm embedder. Measured on this machine: the embedder is a per-call subprocess that
# imports torch/transformers and loads bge-small from scratch every time, 7-9 SECONDS even warm
# (HF cache hot) -- a genuine long-lived model-server process would need a socket, a lifecycle,
# and its own failure handling, none of it measured or built here (never build a daemon without
# measuring first). The honest cheap win that fits the measured architecture: cache the QUERY
# vector by (embed model, exact query text) in the same sqlite index the caller already opens, so
# a repeated query never re-pays the subprocess cost. A genuinely NEW query still pays the full
# 7-9s; only a REPEAT of the same wording is warm. The persistent-process embedder remains open.
QUERY_CACHE_MAX = 500   # ponytail: bounded, oldest-last_used evicted; revisit only if measured hot


def _query_cache_key(text):
    # Keyed on the embed model too: a cached vector from a retired or swapped model must never
    # answer a query under a new one, the same reasoning cmd_index already applies to the
    # vectors table on an embed_model change.
    model = os.path.basename(_embed_bin() or "none")
    return hashlib.sha256(("%s\x00%s" % (model, text)).encode("utf-8")).hexdigest()


def _query_cache_get(con, text):
    try:
        row = con.execute("SELECT v FROM query_cache WHERE qhash=?",
                          (_query_cache_key(text),)).fetchone()
    except sqlite3.OperationalError:  # sbe: allow-silent an index built before this table existed: no cache, not a crash
        return None
    if row is None:
        return None
    con.execute("UPDATE query_cache SET last_used=? WHERE qhash=?",
               (time.time(), _query_cache_key(text)))
    return list(_unpack(row["v"]))


def _query_cache_put(con, text, vec):
    try:
        con.execute("INSERT OR REPLACE INTO query_cache (qhash, v, last_used) VALUES (?,?,?)",
                   (_query_cache_key(text), _pack(vec), time.time()))
        n = con.execute("SELECT COUNT(*) c FROM query_cache").fetchone()["c"]
        if n > QUERY_CACHE_MAX:
            con.execute(
                "DELETE FROM query_cache WHERE qhash IN "
                "(SELECT qhash FROM query_cache ORDER BY last_used ASC LIMIT ?)",
                (n - QUERY_CACHE_MAX,))
    except sqlite3.OperationalError:  # sbe: allow-silent same degrade-not-crash stance: no cache table yet, recall still answers
        pass


def _cosine(a, b):
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


STOP = set("""the and for with that this from was were are into out not but you your they
their there here when what which who how why all any can could would should has have had its
it's about over under again very just only same than then them these those thing things one two
some more most much many make made does did doing done being been because before after while
during through above below down off own each few nor too our ours out per via yes no""".split())


def _fts_query(text):
    """FTS5 has its own query syntax and raises on stray punctuation, so every term is quoted."""
    terms = [t.lower() for t in re.findall(r"[A-Za-z0-9_.]{3,}", text)]
    return [t for t in dict.fromkeys(terms) if t not in STOP][:24]


def _term_hits(con, term):
    try:
        return con.execute("SELECT COUNT(*) c FROM notes_fts WHERE notes_fts MATCH ?",
                           ('"%s"' % term.replace('"', ""),)).fetchone()["c"]
    except sqlite3.OperationalError:
        return 0


def _ranked_by_rewrite(con, text, kind=None, limit=60, allow_or_relax=True):
    """Rarest terms first, ANDed, then relaxed. A bare OR of every term ranks a note that shares
    one common project word ("room", "atrium") above the note that actually describes the
    failure: measured, the two notes that root-caused the night's defect sat below two unrelated
    notes that merely mention the product. Requiring the RAREST terms together is what separates
    a real match from a word in common, and relaxing only when nothing matches keeps a narrow
    query from returning nothing at all.

    allow_or_relax=False refuses that relaxation and returns [] instead: measured on real code
    files against the real vault (2026-08-29), the OR pass has no floor and BM25-ranks whichever
    note happens to share the LEAST-rare surviving term, which for code vocabulary is boilerplate
    ("def", "usr", "pipefail") rather than anything about the file's actual content, and it fired
    on every file tried, including a pure color-conversion module with no defect in it at all.
    The AND-with-2-agreeing-hits floor is real signal; the OR fallback beyond it is not, for a
    caller (the content-only fallback in cmd_check) that has no anchor or human-typed-query
    signal to lean on instead."""
    terms = _fts_query(text)
    if not terms:
        return []
    scored = sorted(((_term_hits(con, t), t) for t in terms), key=lambda x: x[0])
    rare = [t for c, t in scored if c > 0]
    if not rare:
        return []

    def ask(q):
        sql = ("SELECT f.rowid FROM notes_fts f JOIN notes n ON n.id = f.rowid "
               "WHERE notes_fts MATCH ?")
        params = [q]
        if kind == "lesson":
            sql += " AND n.kind != 'log'"
        elif kind == "log":
            sql += " AND n.kind = 'log'"
        sql += " ORDER BY bm25(notes_fts, 3.0, 3.0, 1.0) LIMIT ?"
        params.append(limit)
        try:
            return [r["rowid"] for r in con.execute(sql, params).fetchall()]
        except sqlite3.OperationalError:
            return []

    # A SINGLE and-hit is noise, not signal: one document sharing 4 rare words by chance (measured
    # on the real vault: an unrelated reference note) used to win outright and block the OR pass
    # that would have found the real answer two ranks down. Two or more agreeing hits is what
    # actually separates a real match from a coincidence; anything thinner falls through to OR
    # (or returns empty, for a caller that asked not to relax).
    for width in (4, 3, 2):
        if len(rare) < width:
            continue
        rows = ask(" AND ".join('"%s"' % t for t in rare[:width]))
        if len(rows) >= 2:
            return rows
    if not allow_or_relax:
        return []
    return ask(" OR ".join('"%s"' % t for t in rare))


def _rrf(ranked_lists):
    """Reciprocal Rank Fusion: combine RANKS, not scores, so signals that are not on the same
    scale can be mixed without inventing a calibration nobody measured."""
    fused = {}
    for weight, lst in ranked_lists:
        for rank, nid in enumerate(lst, start=1):
            fused[nid] = fused.get(nid, 0.0) + weight * (1.0 / (RRF_K + rank))
    return sorted(fused.items(), key=lambda kv: -kv[1])


def _split_kind(con, ids):
    """Return (lessons, logs) preserving the incoming rank order within each."""
    if not ids:
        return [], []
    marks = ",".join("?" * len(ids))
    kinds = {r["id"]: r["kind"] for r in con.execute(
        "SELECT id, kind FROM notes WHERE id IN (%s)" % marks, ids).fetchall()}
    lessons = [i for i in ids if kinds.get(i) != "log"]
    logs = [i for i in ids if kinds.get(i) == "log"]
    return lessons, logs


def _anchor_query_ids(con, text, limit=60):
    """The exact-match half of the staged fix: anchor-shaped tokens IN THE QUERY TEXT (a file
    name, a CamelCase or dotted symbol) looked up directly against the anchors table built at
    index time -- the same table and the same ANCHOR regex `check` already uses for a supplied
    path list, just run against free text instead. A plain-English symptom query usually has no
    such token, and an empty result here is a normal, expected outcome, not a failure."""
    tokens = set(ANCHOR.findall(text))
    if not tokens:
        return []
    marks = ",".join("?" * len(tokens))
    rows = con.execute(
        "SELECT DISTINCT note_id FROM anchors WHERE anchor IN (%s) LIMIT ?" % marks,
        list(tokens) + [limit]).fetchall()
    return [r["note_id"] for r in rows]


def _cjk_hits(con, text, analyzer_mod, limit=60):
    """VB2-03: the analyzer-driven CJK lexical signal. Only called from _search once
    _CJK_PROBE_RE has already matched TEXT, so this never runs for a pure-ASCII query.

    WHY A SUBSTRING SCAN, not another notes_fts MATCH query like _ranked_by_rewrite's:
    notes_fts uses FTS5's default (unicode61) tokenizer, which has no word-boundary
    rule for CJK scripts and so treats one whole unbroken CJK run as ONE token. A
    query for a 2-character segment (segment()'s own bigram fallback) can therefore
    never MATCH a token that is a much longer run containing it; FTS5's own prefix
    syntax only matches a token's START, not an arbitrary interior substring. Python's
    stdlib sqlite3 binding exposes no hook to register a custom FTS5 tokenizer either
    (that needs the C fts5_api, not bound by CPython's sqlite3 module), so a direct
    LIKE '%token%' scan over notes.title/descr/body is the honest stdlib ceiling for
    CJK substring matching, not a shortcut taken in place of a better option.

    Ranked by how many DISTINCT analyzed tokens matched a note (ties broken by note id
    ascending, for determinism), which is exactly the lever a dictionary entry moves:
    a term segment() would otherwise slice into several generic bigrams collapses into
    one specific token once it is in the user or company dictionary, so a decoy that
    only coincidentally shares those bigrams stops matching at all (see
    test_bm_vault_analyzer.py's dictionary-flip case).

    JA78 (2026-09-05): THE NOTE SIDE IS FOLDED WITH THE SAME NORMALIZER THE QUERY SIDE
    ALREADY GETS. analyze() runs the query through analyzer.normalize() and this scan
    used to compare the result against RAW note text, so the width fold was applied on
    one side only: a full-width query reached a half-width note (the direction that
    happened to be tested) and a half-width query could never reach a full-width note.
    Measured on the frozen blind corpus, case wv05: a company whose note declares its
    own English designation full-width was unreachable by its own name, while an
    unrelated company that writes CO., LTD. half-width matched the query's legal-form
    words and outranked it. One normal form on both sides is the fix, and it is why
    this is now a SINGLE pass over the notes (folding each note once) instead of one
    full table scan per token: fewer scans than before, and the fold cannot be paid
    per token. LIKE's own ASCII case folding is reproduced by lower()ing both sides,
    and LIKE's % and _ wildcards stop existing at all under a literal `in` test, so a
    dictionary term carrying either now matches itself rather than a defanged
    approximation of itself."""
    tokens = [t.lower() for t in analyzer_mod.analyze(text, vault_dir=_default_vault())
              if len(t) >= 2]
    if not tokens:
        return []
    counts = {}
    for r in con.execute("SELECT id, title, descr, body FROM notes"):
        hay = " ".join(f for f in (r["title"], r["descr"], r["body"]) if f)
        if not hay:
            continue
        hay = analyzer_mod.normalize(hay).lower()
        n = 0
        for tok in tokens:
            if tok in hay:
                n += 1
        if n:
            counts[r["id"]] = n
    return [nid for nid, _c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


# VB2-08 (adversarial-JA disambiguation): the CJK lexical signals above rank a note by how many
# of the query's tokens it contains, which floats the WRONG member of a near-duplicate entity pair
# to the top: a query for 東西銀行 ranks 東西信用金庫 first (shared 東西, plus the shinkin note's own
# disclaimer naming the bank), a query naming 本村精工 with 時計部品 ranks 本村 over the 木村精工 that
# actually makes clocks. A blind adversarial JA corpus scored the negative (disambiguation) class
# 1/13 on exactly these. This pass runs AFTER fusion, over the top candidates only, and DROPS the
# confusable decoy so it stops being served. It is lexical and dictionary-driven, no model, and is
# a no-op whenever the query names no clear entity or no confusable pair is present (so the 245-case
# JA benchmark and every non-entity query are unaffected, proven in test_bm_vault_jbench.py).
_DISAMBIG_CAND_CAP = 40
#: relationship queries ("Aの関連会社は"): the sibling may be the answer, so R1 must not drop it.
_JA_REL_MARKERS = ("関連", "子会社", "グループ", "関係会社", "傘下", "親会社")
#: generic Japanese business/metadata words: kanji, and rare enough in a small vault to look
#: distinguishing, but never an entity's own attribute. Entity-agnostic (names no company or
#: product), so it cannot tune the blind corpus; it only stops 会社/略称-style domination.
_JA_GENERIC = frozenset(
    "会社 企業 略称 名称 代表 本社 設立 創業 現在 所在 資本 従業 業者 製品 主力".split())
_JA_SEP_RE = re.compile(u"[\\s・･　]+")
_JA_NAME_IDF = 1.0    #: a shared name token this rare marks two notes as one confusable family.
_JA_ATTR_IDF = 1.5    #: a query content word this rare counts as a distinguishing attribute.
#: JA13: the full-spelling legal forms used as a NAME ANCHOR when reading the company the
#: query names. bm_vault_analyzer._LEGAL_FORMS owns the complete list (this is a subset on
#: purpose): the bracketed spellings there, (株) and the circled glyphs, are stripped as
#: noise wherever they appear and are not a reliable boundary for the name beside them.
_JA_NAME_ANCHORS = ("株式会社", "有限会社", "合同会社")
#: the run that can carry a company name beside one of the anchors above: kanji, katakana,
#: the iteration mark and the long vowel mark, and NO hiragana. Excluding hiragana is what
#: keeps the particles in その会社は肥後物産株式会社 out of the captured name; it also declines
#: the rare all-hiragana trade name, which only makes this rule fire less often.
_JA_NAME_RUN_RE = re.compile(
    u"([゠-ヿ一-鿿々ｦ-ﾟ]{2,12})$")


def _ja_query_named(qnorm):
    """The company names the query writes with a legal form attached, e.g. the
    肥後物産 of 肥後物産株式会社. Empty when the query names no company that way.

    Why the anchor and not just any token: a Japanese company is written with its
    legal form glued to the name, so the run immediately before 株式会社 is a name
    the ASKER asserted, as opposed to a word the ranker merely matched. That
    distinction is what _ja_disambiguate needs in order to tell 'this candidate is
    the company you named' from 'this candidate happens to fit your description'."""
    out = []
    for anchor in _JA_NAME_ANCHORS:
        start = 0
        while True:
            at = qnorm.find(anchor, start)
            if at < 0:
                break
            m = _JA_NAME_RUN_RE.search(qnorm[:at])
            if m:
                out.append(m.group(1))
            start = at + len(anchor)
    return out


def _ja_content_tok(t):
    """True for a distinguishing-ATTRIBUTE candidate: a content word, not grammatical glue.
    Japanese content nouns in these descriptions are kanji or katakana loanwords; particles,
    inflections and question tails (の, は, ですか, ...) are hiragana. So a token is content
    iff it is 2+ chars, carries NO hiragana, and is kanji-bearing or all-katakana. Deriving
    attributes this way (from raw segment(), before analyze()'s kana-variant doubling) is what
    keeps は誰/ですか/産の out of the attribute set that decides an exclusion."""
    if len(t) < 2:
        return False
    for c in t:
        if u"ぁ" <= c <= u"ゟ":          # any hiragana -> grammatical, not an attribute
            return False
    has_kanji = any(c >= u"㐀" and not (u"ぁ" <= c <= u"ヿ") for c in t)
    all_kata = all(u"゠" <= c <= u"ヿ" for c in t)
    return has_kanji or all_kata


def _ja_disambiguate(con, text, analyzer_mod, fused, why, note):
    """Return fused with confusable-entity decoys removed (or fused unchanged). Reads note
    title/body from con; never raises past its own guard (the _search caller wraps it too).

    Three exclusion rules, all gated to entity queries and confusable pairs so a plain query
    or an absent pair changes nothing:
      R1  the query NAMES an entity (a note whose legal-form-stripped title is a substring of
          the query): drop a different-named sibling that merely shares the name-stem or is
          mentioned in the subject's own disclaimer body. Kept: a sub-unit carrying the whole
          subject name (人事部), a sibling owning a distinguishing attribute (a related company),
          and everything under a relationship query.
      R2  among confusable entities (shared rare name/industry token, e.g. 精工), drop the one
          an equally-confusable peer DOMINATES on distinguishing attributes, or drop both when
          their attributes CONFLICT (each owns one the other lacks: 空圧 vs 尼崎).
      R3  a surname collision: an entity that matches only a fragment of the query name and owns
          NO attribute, when a peer matches part of the name AND owns the query's attributes
          (山口電子+YD-300 -> 山田電子 wins, 山口電気 drops)."""
    if not fused or not text:
        return fused
    qtoks = {t for t in analyzer_mod.analyze(text, vault_dir=_default_vault()) if len(t) >= 2}
    if not qtoks:
        return fused
    qnorm = analyzer_mod.normalize(text)
    qcore_c = _JA_SEP_RE.sub("", analyzer_mod.strip_legal_forms(qnorm))
    uterms, cterms = analyzer_mod.load_dictionaries(_default_vault())
    raw_q = set(analyzer_mod.segment(analyzer_mod.strip_legal_forms(qnorm), uterms, cterms))
    q_content = {t for t in raw_q if _ja_content_tok(t)}

    n_row = con.execute("SELECT COUNT(*) AS n FROM notes").fetchone()
    total = n_row["n"] if n_row else 0
    if total <= 0:
        return fused
    _df_cache = {}

    def df(tok):
        if tok in _df_cache:
            return _df_cache[tok]
        safe = tok.replace("%", "").replace("_", "")
        if not safe:
            _df_cache[tok] = 0
            return 0
        like = "%" + safe + "%"
        r = con.execute(
            "SELECT COUNT(*) AS n FROM notes WHERE title LIKE ? OR descr LIKE ? OR body LIKE ?",
            (like, like, like)).fetchone()
        _df_cache[tok] = r["n"] if r else 0
        return _df_cache[tok]

    def idf(tok):
        return math.log((total + 1.0) / (df(tok) + 0.5))

    q_rare = {t for t in qtoks if idf(t) >= _JA_NAME_IDF}
    if not q_rare:
        return fused
    has_rel = any(m in qnorm for m in _JA_REL_MARKERS)

    info = {}
    for nid, _score in fused[:_DISAMBIG_CAND_CAP]:
        row = con.execute("SELECT title, descr, body FROM notes WHERE id=?", (nid,)).fetchone()
        if not row:
            continue
        title = row["title"] or ""
        norm_tb = analyzer_mod.normalize(
            title + " " + (row["descr"] or "") + " " + (row["body"] or ""))
        name = analyzer_mod.strip_legal_forms(analyzer_mod.normalize(title))
        ntk = {t for t in analyzer_mod.analyze(title, vault_dir=_default_vault()) if len(t) >= 2}
        info[nid] = {
            "name": name, "ntk": ntk, "norm_tb": norm_tb,
            "entity": len(name) >= 3,
            "subject": len(name) >= 3 and name and _JA_SEP_RE.sub("", name) in qcore_c,
        }
    if not info:
        return fused
    all_name_tok = set().union(*[i["ntk"] for i in info.values()])
    q_attr = {t for t in raw_q if _ja_content_tok(t) and t not in all_name_tok
              and t not in _JA_GENERIC and idf(t) >= _JA_ATTR_IDF}
    for i in info.values():
        i["attr"] = {a for a in q_attr if a in i["norm_tb"]}

    def name_share(a, b):
        ia, ib = info[a], info[b]
        if ia["name"][:2] and ia["name"][:2] == ib["name"][:2]:
            return True
        return any(idf(t) >= _JA_NAME_IDF for t in (ia["ntk"] & ib["ntk"]))

    subjects = [n for n, i in info.items() if i["subject"]]
    excl = {}

    # R1: subject-sibling decoy. Skipped entirely for relationship queries.
    if subjects and not has_rel:
        for nid, i in info.items():
            if i["subject"] or not i["entity"]:
                continue
            near = any(name_share(nid, s) or (info[s]["name"] and info[s]["name"] in i["norm_tb"])
                       for s in subjects)
            if not near:
                continue
            if any(info[s]["name"] and info[s]["name"] in i["name"] for s in subjects):
                continue                                  # sub-unit (人事部): keep
            excl[nid] = "confusable name"

    # R2: confusable-entity attribute domination / conflict.
    # R2's domination arm elects a family SURVIVOR from the query's attributes. That is
    # sound only while the attributes are evidence about a company the vault actually
    # holds. JA13: when the query NAMES a company with a legal form attached and no
    # candidate carries that name (`subjects` empty), the attributes are unverified claims
    # about an ABSENT entity, and electing a survivor by them hands rank 1 to whichever
    # same-family company happens to fit the description. Measured on the frozen blind
    # corpus: 肥後物産株式会社は東京都中央区日本橋に本社を置く繊維商社ですか dropped every 物産
    # peer and served 桜田物産, a different company that owns that address and that trade,
    # at rank 1. So in exactly that state the arm INVERTS: the dominator is the one being
    # mistaken for the entity nobody holds, and it is what this arm drops. What becomes of
    # the rest of the family is R1's and R3's business, unchanged here: a peer can still
    # go for a name collision, and on the corpus case above one does.
    named_absent = bool(_ja_query_named(qnorm)) and not subjects
    ents = [n for n, i in info.items() if i["entity"]]
    for s in ents:
        for m in ents:
            if m == s or not name_share(s, m):
                continue
            a_s, a_m = info[s]["attr"], info[m]["attr"]
            if a_m and a_m > a_s:
                if named_absent:
                    excl.setdefault(m, "described but not named")
                else:
                    excl.setdefault(s, "attribute mismatch")
            elif (a_m - a_s) and (a_s - a_m):
                excl.setdefault(s, "attribute conflict")

    # R3: surname/name-fragment collision.
    competitors = [n for n, i in info.items()
                   if i["entity"] and i["attr"] and (i["ntk"] & q_content)]
    for nid, i in info.items():
        if not i["entity"] or i["subject"] or i["attr"]:
            continue
        if not (i["ntk"] & q_content):
            continue
        if any(c != nid for c in competitors):
            excl.setdefault(nid, "name collision")

    if not excl:
        return fused
    for nid, reason in excl.items():
        note("disambiguation: dropped note %s (%s)" % (nid, reason))
        why.pop(nid, None)
    why["__disambiguated__"] = why.get("__disambiguated__", 0) + len(excl)
    return [(nid, score) for nid, score in fused if nid not in excl]


def _search(con, text=None, paths=None, limit=6, fast=False, explain=None, deny=None):
    """explain: pass a list to have staging decisions and per-signal timings appended to it as
    strings, in the order the signals ran; pass None (the default) to skip the bookkeeping.

    deny: an optional predicate(path) -> bool from the VB2-01 access policy seam. A note it
    denies is dropped at CANDIDATE stage, right after rank fusion and BEFORE the authority
    sort reads any body, so forbidden content never participates in what gets served. Denied
    notes are COUNTED into why["__policy_withheld__"], never named: title, path and content
    of a withheld note must not appear anywhere in the output, because naming what someone
    may not see is itself a leak. None (the default) means no policy: today's behavior."""
    why = {}
    lists = []
    denied_ids = set()

    def _denied(nid, path):
        if deny is None:
            return False
        if nid in denied_ids:
            return True
        if deny(path):
            denied_ids.add(nid)
            why.pop(nid, None)
            why["__policy_withheld__"] = why.get("__policy_withheld__", 0) + 1
            return True
        return False

    def note(msg):
        if explain is not None:
            explain.append(msg)

    def add(ids, base_weight, label):
        """Every signal is split into its LESSON half and its LOG half before fusion. A log
        mentions everything it touched, so undifferentiated it buries the distilled note that
        actually answers the question; ranked separately, both stay reachable and the guidance
        comes first."""
        lessons, logs = _split_kind(con, ids)
        lists.append((base_weight * 3.0, lessons))
        lists.append((base_weight * 0.4, logs))
        for i in ids:
            why.setdefault(i, []).append(label)

    if text:
        t0 = time.time()
        anchor_ids = _anchor_query_ids(con, text)
        note("anchor: %d hit(s) in %dms" % (len(anchor_ids), int((time.time() - t0) * 1000)))
        add(anchor_ids, 2.0, "anchor")

        t0 = time.time()
        lesson_ids = _ranked_by_rewrite(con, text, kind="lesson")
        log_ids = _ranked_by_rewrite(con, text, kind="log")
        note("bm25: %d hit(s) in %dms"
             % (len(lesson_ids) + len(log_ids), int((time.time() - t0) * 1000)))
        lists.append((3.0, lesson_ids))
        lists.append((0.4, log_ids))
        for i in lesson_ids + log_ids:
            why.setdefault(i, []).append("wording")

        # VB2-03: Japanese-first lexical signal, gated on the QUERY TEXT itself
        # carrying a CJK character (_CJK_PROBE_RE: hiragana, katakana full or half
        # width, or a CJK ideograph). A pure-ASCII query never even imports the
        # analyzer module: this whole block is skipped outright, so the anchor and
        # bm25 signals above run on the exact path they always have (proven
        # byte-identical in test_bm_vault_analyzer.py). The signal itself is a
        # substring scan, not another FTS5 MATCH: see _cjk_hits's own docstring for
        # why (FTS5's default tokenizer treats one whole CJK run as one token, and
        # stdlib sqlite3 exposes no custom-tokenizer hook to fix that at index time).
        cjk_ids = []
        if _CJK_PROBE_RE.search(text):
            t0 = time.time()
            try:
                analyzer_mod = _load_bm_vault_analyzer()
                cjk_ids = _cjk_hits(con, text, analyzer_mod)
            except Exception as e:
                print("cjk signal unavailable (%s); ranking runs without it" % e,
                      file=sys.stderr)
            note("cjk: %d hit(s) in %dms" % (len(cjk_ids), int((time.time() - t0) * 1000)))
            add(cjk_ids, 3.0, "cjk")

        # D: the dense signal, STAGED. It costs 30-75 SECONDS on this machine (a subprocess that
        # imports torch/transformers and loads bge-small from scratch), so it runs only when the
        # two sub-second signals above did not already answer: nothing found, fewer than the
        # requested --limit, or (both non-empty) no overlap in their top results, which means they
        # are pointing at different notes and neither can be trusted alone. --fast skips it
        # unconditionally, for callers where a bounded budget matters more than recall depth.
        lexical_ids = lesson_ids + log_ids + cjk_ids
        if fast:
            note("dense: skipped (--fast)")
        else:
            combined = set(anchor_ids) | set(lexical_ids)
            top_overlap = set(anchor_ids[:10]) & set(lexical_ids[:10])
            disagree = bool(anchor_ids) and bool(lexical_ids) and not top_overlap
            if not combined:
                need_dense, reason = True, "anchor and bm25 both empty"
            elif len(combined) < limit:
                need_dense, reason = True, "fewer than %d result(s)" % limit
            elif disagree:
                need_dense, reason = True, "anchor and bm25 disagree"
            else:
                need_dense, reason = False, "lexical signals sufficient"
            if not need_dense:
                note("dense: skipped (%s)" % reason)
            else:
                t0 = time.time()
                cached = _query_cache_get(con, text)
                if cached is not None:
                    note("dense: query cache hit in %dms (%s)"
                         % (int((time.time() - t0) * 1000), reason))
                    qv = {0: cached}
                else:
                    note("dense: loading (%s)" % reason)
                    qv = _embed_texts([(0, text)], query=True)
                    note("dense: %dms" % int((time.time() - t0) * 1000))
                    if qv is not None and 0 in qv:
                        _query_cache_put(con, text, qv[0])
                if qv is None:
                    why["__nodata__"] = ["dense signal absent (bm-embed missing)"]
                elif 0 in qv:
                    q = qv[0]
                    scored = []
                    for r in con.execute("SELECT note_id, v FROM vectors").fetchall():
                        scored.append((_cosine(q, _unpack(r["v"])), r["note_id"]))
                    scored.sort(reverse=True)
                    # The floor is the MODEL'S, not universal, and bge compresses scores: measured
                    # on this machine WITH the query prefix, an off-estate nonsense query still
                    # scores 0.476 against an arbitrary note, plain unrelated text 0.35, and true
                    # matches 0.585 to 0.623. 0.55 sits in the measured gap. If the model ever
                    # changes, remeasure these three points before trusting the floor; the meta
                    # table records which machine built the vectors for exactly this reason.
                    top_dense = [nid for c, nid in scored[:40] if c > 0.55]
                    d_lessons, d_logs = _split_kind(con, top_dense)
                    lists.append((2.0, d_lessons))
                    lists.append((0.3, d_logs))
                    for i in top_dense:
                        why.setdefault(i, []).append("meaning")
    if paths:
        hits, seen = [], set()
        for p in paths:
            base = os.path.basename(p.strip())
            if not base:
                continue
            for r in con.execute(
                    "SELECT DISTINCT note_id FROM anchors WHERE anchor=? OR anchor LIKE ?",
                    (base, "%/" + base)).fetchall():
                if r["note_id"] not in seen:
                    seen.add(r["note_id"])
                    hits.append(r["note_id"])
                why.setdefault(r["note_id"], []).append("names " + base)
        # weighted hardest on purpose: an exact identifier match is the case a lexical index is
        # actually good at, and it is what a person is holding when the lesson matters.
        add(hits, 2.0, "path")
    fused = _rrf(lists)
    # VB2-01: the access-policy trim, applied HERE on purpose: after fusion (so there is one
    # list to trim) and before the authority sort below (which reads note bodies), so a
    # denied note's content is never read for ranking and never printed anywhere.
    if deny is not None and fused:
        kept = []
        for nid, score in fused:
            row = con.execute("SELECT path FROM notes WHERE id=?", (nid,)).fetchone()
            if row and _denied(nid, row["path"]):
                continue
            kept.append((nid, score))
        fused = kept
    # VB2-08: entity disambiguation, run HERE: after fusion and the policy trim, before the
    # authority sort (so a dropped decoy never has its body read for ranking) and before the
    # limit cut (so the drop actually removes it from what gets served, not just reorders it,
    # which reordering could not do when the served list is shorter than the confusable set).
    # Gated on a CJK query and guarded like every other optional signal: an analyzer failure
    # degrades to the fused order on stderr rather than killing the whole recall.
    if text and fused and _CJK_PROBE_RE.search(text):
        t0 = time.time()
        try:
            fused = _ja_disambiguate(con, text, _load_bm_vault_analyzer(), fused, why, note)
            note("disambiguation: %dms" % int((time.time() - t0) * 1000))
        except Exception as e:
            print("disambiguation unavailable (%s); results keep fused order" % e,
                  file=sys.stderr)
    # E57 mechanism 2: time decay and reinforcement, applied HERE on purpose. AFTER the
    # policy trim (a denied note's body is never read, not even to age it) and BEFORE the
    # authority sort, because decay is a SIMILARITY signal, not an authority one: it scales
    # the fused score that the authority comparator uses as its second key, so a decayed
    # note falls WITHIN its own authority tier and a decayed source of record still outranks
    # every casual note. Bounded by bm_vault_decay.FLOOR, so this reorders and never removes.
    # Guarded like every other optional signal: an absent or broken module degrades to the
    # fused order on stderr rather than killing the recall.
    if fused:
        try:
            decay_mod = _load_bm_vault_decay()
        except Exception as e:
            decay_mod = None
            print("decay ranking unavailable (%s); results keep fused order" % e,
                  file=sys.stderr)
        if decay_mod is not None:
            # One store read for the whole result set, never one per note.
            dstore = decay_mod.read_store()
            rescored = []
            for nid, score in fused:
                row = con.execute("SELECT path, body FROM notes WHERE id=?",
                                  (nid,)).fetchone()
                path_ = row["path"] if row else ""
                slug = os.path.splitext(os.path.basename(path_))[0] if path_ else ""
                factor = decay_mod.scale(slug, row["body"] if row else "", dstore)
                if factor < 1.0:
                    msg = "decayed: rank scaled %.2f" % factor
                    note("decay: note %s %s" % (nid, msg))
                    why.setdefault(nid, []).append(msg)
                rescored.append((nid, score * factor))
            fused = sorted(rescored, key=lambda kv: kv[1], reverse=True)
    # D08 part B: authority outranks similarity, LEXICOGRAPHICALLY, per bm_vault_authority's
    # contract: a source_of_record note with lower fused score beats a casual note with a higher
    # one, always, because blending them into one weighted score is how similarity smuggles
    # itself back on top. Absent declaration ranks casual (the contract states this openly).
    # Review findings 2026-08-30, both fixed here: the contract module may be ABSENT in a
    # deployed snapshot (~/.claude/vault-tools carries only three files), so a failed load
    # degrades to fused order on stderr instead of killing every recall; and an unknown
    # authority value RANKS CASUAL with an unconditional stderr warning instead of silently
    # deleting the note, because a note someone marked source-of-record with a typo must never
    # vanish from search with no output anywhere.
    auth = None
    stale_mod = None
    if fused:
        try:
            auth = _load_bm_vault_authority()
        except Exception as e:
            print("authority ranking unavailable (%s); results keep fused order" % e,
                  file=sys.stderr)
        try:
            stale_mod = _load_bm_vault_staleness()
        except Exception as e:
            print("staleness demotion unavailable (%s); authority ranks as declared" % e,
                  file=sys.stderr)

    def _authority_sort(pairs):
        levels = {}
        for nid, score in pairs:
            row = con.execute("SELECT body FROM notes WHERE id=?", (nid,)).fetchone()
            body = row["body"] if row else ""
            level, problem = auth.read_authority(body)
            if problem:
                print("authority: note %s ranks casual, %s" % (nid, problem),
                      file=sys.stderr)
                note("authority: note %s ranks casual, %s" % (nid, problem))
                level = "casual"
            # VB2-06: a fact that quietly stopped being true loses rank until
            # re-verified. Demoted ONE authority step below what it declares,
            # never below casual: a stale source_of_record still outranks an
            # ordinary casual note, it just stops outranking a FRESH one.
            # Guarded exactly like auth itself: an absent staleness module
            # degrades to no demotion, audibly, never a crash.
            if stale_mod is not None and level != "casual":
                stale, verified = stale_mod.is_stale(body)
                if stale:
                    idx = auth.LEVELS.index(level)
                    level = auth.LEVELS[idx - 1]
                    msg = "authority demoted, unverified since %s" % verified
                    print("staleness: note %s %s" % (nid, msg), file=sys.stderr)
                    note("staleness: note %s %s" % (nid, msg))
                    why.setdefault(nid, []).append(msg)
            levels[nid] = level
            if level != "casual" and not any(
                    w.startswith("authority: ") for w in why.get(nid, [])):
                why.setdefault(nid, []).append("authority: " + level)
        return sorted(pairs, key=lambda kv: auth.rank_key(levels[kv[0]], kv[1]),
                      reverse=True)

    if fused and auth is not None:
        fused = _authority_sort(fused)
    top = [nid for nid, _ in fused[:limit]]
    # C: link expansion. A note the top hits POINT AT is part of the same lesson; the vault
    # already carries this graph, so following it costs nothing and recovers notes whose wording
    # differs from the question, which is the exact weakness of a lexical index.
    if top:
        known = {nid for nid, _ in fused}
        expanded = False
        for r in _linked_neighbors(con, top):
            if r["id"] not in known:
                # VB2-01: link expansion must not resurrect a note the policy
                # trim dropped above, and must not add a denied neighbor either.
                if _denied(r["id"], r["path"]):
                    continue
                fused.append((r["id"], 0.0))
                why.setdefault(r["id"], []).append("linked from a match")
                expanded = True
        # Review finding 2026-08-30: a source-of-record note recovered by links used to be
        # appended AFTER the authority sort, ranking below every casual note, the exact
        # inversion this change exists to prevent. Re-sort the whole list once expansion adds
        # anything; expanded notes carry score 0.0, so within a tier they still rank last.
        if expanded and auth is not None:
            fused = _authority_sort(fused)
    return fused[:limit], why


def _link_stem(target):
    """A wikilink target reduced to the filename stem it actually names.

    Same normalization the graph gate applies, and the same three steps
    _rebuild_contradictions already performs a few hundred lines above: drop a
    `#Section` anchor, keep only the last path segment, drop a trailing `.md`.
    """
    target = (target or "").strip()
    if "#" in target:
        target = target.split("#", 1)[0].strip()
    stem = target.split("/")[-1].strip()
    if stem.lower().endswith(".md"):
        stem = stem[:-3]
    return stem


def _linked_neighbors(con, top):
    """Rows (id, path) for the notes the `top` hits link to.

    CORRECTED 2026-09-01, and the correction is the whole point of this helper.
    The join used to read `notes.title = links.target`, comparing a RAW WIKILINK
    STEM against a title derived from frontmatter, and only 2 of 849 notes carry
    that frontmatter field. Measured over 1959 distinct wikilink targets in the
    live corpus: 16 resolved through the old join (0.8 percent) against 1945
    through the graph resolver (99.3 percent). Link expansion is advertised as
    one of three compensating retrieval signals and it was walking under one
    percent of its own graph, which is why a lesson recorded in the vault could
    be structurally reachable and still never arrive at the point of need.

    Resolution is by FILENAME STEM, which every note has, so the two sides can
    actually meet. `kind != 'log'` is preserved exactly as before: session logs
    are not lessons and expansion must not drag them in.
    """
    if not top:
        return []
    marks = ",".join("?" * len(top))
    by_stem = {}
    for r in con.execute("SELECT id, path, kind FROM notes").fetchall():
        if (r["kind"] or "") == "log":
            continue
        stem = os.path.splitext(os.path.basename(r["path"] or ""))[0]
        if stem:
            by_stem.setdefault(stem, r)
    out, seen = [], set()
    for row in con.execute(
            "SELECT DISTINCT l.target FROM links l WHERE l.note_id IN (%s)" % marks,
            top).fetchall():
        hit = by_stem.get(_link_stem(row["target"]))
        if hit is not None and hit["id"] not in seen:
            seen.add(hit["id"])
            out.append(hit)
    return out


def _note_freshness(con, freshness, nid, roots, idx_cache, state_con):
    """One note's live freshness verdict, on demand: anchors are already sitting in bm_vault's
    own anchors table (built at index time by the ANCHOR regex both files share), so this is one
    small read plus bm_freshness.classify_live's real filesystem/grep check -- never a parallel
    extraction path."""
    anchors = {r["anchor"] for r in con.execute(
        "SELECT anchor FROM anchors WHERE note_id=?", (nid,)).fetchall()}
    row = con.execute("SELECT path FROM notes WHERE id=?", (nid,)).fetchone()
    path = row["path"] if row else "note-%s" % nid
    return freshness.classify_live(path, anchors, roots, idx_cache, state_con)


def _print_annotations(enrich, body):
    """One line per served hit carrying the D01 done_check's contract fields: the
    note's stable id (id: frontmatter, bm_vault_ids), its authority level
    (bm_vault_authority), and its temporal state at query time (bm_vault_temporal's
    window classified by bm_vault_asof's vocabulary: timeless_current,
    declared_true, declared_false, malformed). Claim-level evidence
    (bm_vault_provenance's `claim: ... [evidence: ...]` lines) follows where
    present. Every module is optional here: an absent one drops its own field,
    with "unavailable" said in its place rather than a guess."""
    ids_mod = enrich.get("bm_vault_ids")
    auth_mod = enrich.get("bm_vault_authority")
    bt = enrich.get("bm_vault_temporal")
    asof = enrich.get("bm_vault_asof")
    prov = enrich.get("bm_vault_provenance")
    note_id = (ids_mod.read_id(body) if ids_mod else None) or "none"
    if auth_mod:
        level, problem = auth_mod.read_authority(body)
        authority = level if not problem else "unrankable"
    else:
        authority = "unavailable"
    if bt and asof:
        window, problems = bt.parse(body)
        temporal = asof.classify(window, problems, datetime.date.today(), bt)
    else:
        temporal = "unavailable"
    print("    id: %s  authority: %s  temporal: %s" % (note_id, authority, temporal))
    if prov:
        for claim, locator in prov.find_claims(body)[:3]:
            print("    evidence: %s (claim: %s)" % (locator, claim[:100]))
    return note_id


def _print_hits(con, fused, why, header, roots=None, ledger_hits=None, withheld_out=None):
    """ledger_hits: pass a list to have it appended, one dict per note actually SERVED
    below (never a withheld/superseded/candidate one -- those never reached the reader,
    so they are not part of what the answer read), for the caller (cmd_recall) to record
    in the answer ledger (VB2-05). None (the default) skips this, unchanged for cmd_check
    and the content-fallback pass, neither of which is in scope for the ledger.

    withheld_out: pass a list to have this call's own withheld count (supersession, D12
    candidate, staleness -- everything counted below, NOT the caller's separate policy
    count) appended to it once, for cmd_recall's access-audit record (VB7-04). None (the
    default) skips this the same way ledger_hits does.

    WIRED (Job 1, 2026-08-29): every hit is revalidated live before it is served. A note whose
    only matching anchor is stale is never printed as an ordinary result -- it is printed WITHHELD,
    with the reason bm_freshness.classify_live gave, so a caller cannot mistake "this estate once
    wrote this down" for "this is still true". An unanchored note (no citation at all -- most of
    the vault today) carries nothing to disprove and is served exactly as it always was; see
    bm_freshness.py's own module docstring for why that third state exists and is never collapsed
    into either of the other two.

    THE CEILING carries through unchanged from bm_freshness: a resolving anchor proves the
    citation resolves, not that the lesson is still true, and revalidation only sees as far as
    `roots` reaches (bm_freshness._default_roots() when roots is None here -- this repo plus its
    live siblings, or an exact override via --root / BM_FRESHNESS_ROOTS). Wiring this in must never
    claim more than the unwired classify_live already honestly claims."""
    if "__nodata__" in why:
        print("NOTE: " + why.pop("__nodata__")[0])
    if not fused:
        print("NO-DATA %s" % header)
        print("  Nothing in the vault or project memory matched. That is a real answer: say so, "
              "rather than assuming the estate has never met this.")
        if withheld_out is not None:
            withheld_out.append(0)
        return 1
    print(header)
    freshness = _load_bm_freshness()
    try:
        lifecycle = _load_bm_vault_lifecycle()
    except Exception as e:
        lifecycle = None
        print("NOTE: D12 lifecycle contract unavailable (%s); candidate "
              "withholding is off for this run, every hit is served exactly "
              "as before" % e, file=sys.stderr)
    enrich = _load_enrichment()
    fresh_roots = roots if roots else freshness._default_roots()
    state_con = freshness._state_connect(freshness.STATE_DB)
    idx_cache = {}
    shown_titles = set()
    withheld = 0
    try:
        for nid, score in fused:
            row = con.execute("SELECT path,title,descr,source,kind,body FROM notes WHERE id=?",
                              (nid,)).fetchone()
            if not row:
                continue
            # One title, one row: harvest folders keep near-identical copies of a note, and a
            # result list that spends all three slots on one lesson answers a third of the
            # question.
            if row["title"] in shown_titles:
                continue
            shown_titles.add(row["title"])
            # SUPERSESSION IS CHECKED FIRST, before freshness, because it is the
            # stronger statement. Freshness says a citation no longer resolves;
            # supersession says a human decided this note was replaced. A
            # superseded note that still reads as current is worse than a missing
            # one, because a wrong lesson delivered at the moment of an edit gets
            # acted on. Withheld, never hidden: the successor is named so the
            # reader can follow it, and the note itself is still on disk.
            replaced_by = _superseded_by(con, row["path"])
            if replaced_by:
                withheld += 1
                print("\n  WITHHELD (superseded)  %s  [%s, %s]" % (row["title"], row["kind"],
                                                                    row["source"]))
                print("    superseded by: %s" % ", ".join(sorted(replaced_by)))
                print("    %s" % row["path"])
                continue
            # D12 (2026-08-30): a note explicitly declared "candidate" is written,
            # by anyone or anything, and nobody has validated it -- the lifecycle
            # contract's own definition. A legacy note (no promotion: field at
            # all) is NOT touched here, per that same contract's own instruction
            # that legacy stays in ordinary retrieval exactly as today; only an
            # explicit, unvalidated declaration is withheld. `lifecycle` is None,
            # and this whole check is skipped, whenever the module failed to load
            # above -- an absent contract module degrades retrieval, it never
            # crashes it.
            if lifecycle is not None:
                try:
                    with open(row["path"], encoding="utf-8", errors="replace") as _fh:
                        _promo_state, _rec, _prob = lifecycle.read_promotion(_fh.read())
                except OSError:
                    _promo_state = "legacy"
                if _promo_state == "candidate":
                    withheld += 1
                    print("\n  WITHHELD (candidate, not yet validated)  %s  [%s, %s]"
                          % (row["title"], row["kind"], row["source"]))
                    print("    reason: declared a candidate under the D12 lifecycle "
                          "contract; nobody has validated it yet")
                    print("    %s" % row["path"])
                    continue
            state, reason = _note_freshness(con, freshness, nid, fresh_roots, idx_cache, state_con)
            if state == "stale":
                withheld += 1
                print("\n  WITHHELD (stale)  %s  [%s, %s]" % (row["title"], row["kind"],
                                                               row["source"]))
                print("    reason: %s" % reason)
                print("    %s" % row["path"])
                continue
            print("\n  %s  [%s, %s]" % (row["title"], row["kind"], row["source"]))
            if row["descr"]:
                print("    %s" % row["descr"][:160])
            note_id = _print_annotations(enrich, row["body"] or "")
            if ledger_hits is not None:
                ledger_hits.append({
                    "id": note_id,
                    "path": row["path"],
                    "content_sha256": hashlib.sha256(
                        (row["body"] or "").encode("utf-8")).hexdigest(),
                })
            conflicting = _contradicted_by(con, row["path"])
            if conflicting:
                print("    CONTRADICTS: %s (see both before treating this as settled)"
                      % ", ".join(conflicting))
            print("    matched on: %s" % ", ".join(sorted(set(why.get(nid, ["wording"])))))
            print("    %s" % row["path"])
        state_con.commit()
    finally:
        state_con.close()
    if withheld:
        print("\nNOTE: a resolving anchor proves the citation resolves, not that the lesson is "
              "still true; a withheld note above may still be worth reading by hand.")
    if withheld_out is not None:
        withheld_out.append(withheld)
    return 0


def _freshness_roots(args):
    """--root FOO (repeatable via the hand-rolled parser's own last-value-wins shape, so this
    accepts one override path) or None to fall back to bm_freshness._default_roots() at print
    time -- the widened sibling-repo default, or its own BM_FRESHNESS_ROOTS env override."""
    return [args["root"]] if args.get("root") else None


def _ledger_lookup(event_id):
    """The ledger row already on disk under event_id (VB6-03), or None. Read fresh every
    call, never cached in-process: two independent processes can append to this file, so
    neither one can trust its own memory of what the other has written. Returns the whole
    row, not just presence, so the caller can compare the retry's query against what is
    already recorded there -- a caller resending the SAME query under an old id is a
    genuine retry, but a DIFFERENT query landing on that id is a collision with an
    unrelated answer, and the two must never be handled the same way.
    ponytail: full-file linear scan per append; fine at the answer ledger's append rate,
    revisit with a side index only if this is ever measured hot."""
    if not os.path.exists(LEDGER_PATH):
        return None
    try:
        with open(LEDGER_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:  # sbe: allow-silent malformed ledger line skipped, tolerant scan per docstring
                    continue
                if row.get("event_id") == event_id:
                    return row
    except OSError:  # sbe: allow-silent unreadable ledger treated as event not found, same as a missing file above
        pass
    return None


def _append_ledger(query, hits, mode, event_id):
    """VB2-05: append one JSON line to the answer ledger recording what a recall actually
    read -- an id, path and content hash per served hit, under whose identity, from which
    mode. AVAILABILITY OVER BOOKKEEPING: this is audit trail, not the answer itself, so a
    write failure (unwritable dir, full disk) prints one stderr warning and returns without
    raising -- a ledger that can break the recall it is auditing would be worse than no
    ledger. Single-write append-only semantics via os.open(O_APPEND) (POSIX guarantees an
    append() write below PIPE_BUF is atomic against other appenders to the same file), so
    concurrent recalls interleave whole lines, never partial ones.

    VB6-03: event_id is minted ONCE per answer by the caller (cmd_recall) and stamped into
    both this row and the access-audit row of the same answer, so a telemetry outcome can
    join back to exactly one ledger row by that id alone. The retry/collision decision for
    a caller-supplied --event-id is made by cmd_recall BEFORE this function is ever
    called (see _ledger_lookup there): by the time control reaches here, event_id is known
    to be fresh, so this function only ever writes, never skips."""
    row = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "event_id": event_id,
        "query": query,
        "identity": os.environ.get("BM_IDENTITY") or "unset",
        "hits": hits,
        "mode": mode,
    }
    try:
        line = (json.dumps(row, sort_keys=True) + "\n").encode("utf-8")
        # 0o600, not 0o644 (fixed 2026-08-30): this row carries the verbatim query text,
        # the same sensitive field bm_vault_audit.py's MAJOR security-review fix made
        # owner-only for its own row (test_append_creates_the_audit_file_mode_0600). The
        # ledger sat beside that file holding the identical field and was never backfilled.
        fd = os.open(LEDGER_PATH, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError as e:
        sys.stderr.write("bm_vault: answer ledger write failed (%s); recall continues\n" % e)


def _append_audit(args, query, ledger_hits, withheld_out, withheld_by_policy, event_id,
                   refusal_reason=None, degraded_reason=None):
    """VB7-04: record who recalled what, for the access audit. Delegates to
    bm_vault_audit.append (loaded by path, the same dynamic pattern as every sibling
    contract module here) so this file never duplicates the audit record's shape or path.

    principal is --as, and ONLY --as: --identity already exists for the separate VB2-01
    policy trim and is not reused here on purpose, because a caller may legitimately query
    under one identity for access purposes while the audit wants a different accountable
    principal recorded (a service account querying on a user's behalf, for instance).
    --as absent records "NO-DATA": never a guess, never a skipped append.

    VB3-04: purpose (--purpose, no env fallback) is recorded on every row this function
    appends, "NO-DATA" when the caller gave none -- the row's own requirement that a
    decision's purpose is always in the audit, never only sometimes. degraded_reason
    (from _policy_deny's fail-closed fallback) is recorded exactly like refusal_reason:
    present only when the enterprise fail-closed path actually fired for this recall.

    VB6-03: event_id is minted once by cmd_recall and passed in here, never minted fresh
    in this function -- the audit row must carry the SAME id as the ledger row of the same
    answer, or a telemetry outcome could never join both stores by one key.

    A failed load degrades to a stderr warning and no record, the same audible-but-never-
    fatal stance every optional contract module in this file already takes.

    Returns the VB6-06 provenance marker line for this event (bm_vault_audit.marker_line),
    or None when the audit module could not be loaded -- the caller prints it as the last
    line of the recall's own output, sharing the SAME event_id as the audit row just
    appended, so a served answer re-ingested elsewhere can be traced back to this exact
    recall rather than merely to "some recall, sometime"."""
    try:
        audit = _load_bm_vault_audit()
    except Exception as e:
        sys.stderr.write("bm_vault: access audit unavailable (%s); recall continues\n" % e)
        return None
    principal = args.get("as")
    if not isinstance(principal, str) or not principal:
        principal = "NO-DATA"
    purpose = args.get("purpose")
    if not isinstance(purpose, str) or not purpose:
        purpose = "NO-DATA"
    served_ids = [h["id"] for h in ledger_hits]
    withheld_total = (withheld_out[0] if withheld_out else 0) + withheld_by_policy
    audit.append(principal, query, served_ids, withheld_total, event_id,
                 refused=refusal_reason, purpose=purpose, degraded=degraded_reason)
    return audit.marker_line(event_id)


def _enterprise_mode():
    """VB3-04: enterprise mode is one opt-in env flag, the same shape as every other
    opt-in switch this file already reads (BM_IDENTITY, BM_FRESHNESS_ROOTS, ...). No
    switch of this name existed anywhere in the tree when this was written (grepped
    clean); a concurrently-landing VB3-03 RequestContext may introduce its own service-
    mode switch in bm_vault_serve.py later, and unioning the two names is that row's
    job, not this one's. In enterprise mode, a policy that cannot be consulted fails
    CLOSED (see _policy_deny below); off (the default, and single-machine mode),
    today's fail-open-with-a-warning behavior is unchanged."""
    return os.environ.get("BROTHERMODE_ENTERPRISE") == "1"


# VB3-04's fail-closed definition of RESTRICTED, used ONLY when the real policy module
# is itself the thing that is missing or broken -- it deliberately does NOT call back
# into bm_vault_policy.py in that case, because a module already shown untrustworthy
# should not be trusted for its fallback either. Mirrors bm_vault_export.RESTRICTED_RE
# and read_restricted verbatim (same regex, same accepted spellings) rather than
# importing that heavier module for one regex, the same "mirror, don't share" call
# bm_vault_principals.py's own normalize_identity docstring already makes for a
# one-line pattern.
_RESTRICTED_RE = re.compile(r"^restricted:\s*(\S+)\s*$", re.M)


def _is_restricted(con, path):
    """True when the note at path carries `restricted: true` frontmatter, read from
    this connection's own indexed body so no second disk read is needed. A note the
    index has no body for, or an unreadable frontmatter block, reads as restricted:
    fail closed means the unknown case is denied, never served."""
    row = con.execute("SELECT body FROM notes WHERE path=?", (path,)).fetchone()
    if not row or not row["body"]:
        return True
    text = row["body"]
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    block = text[3:end] if end != -1 else text
    m = _RESTRICTED_RE.search(block)
    if not m:
        return False
    return m.group(1).strip().strip('"').strip("'").lower() in ("true", "yes", "1")


def _has_canonical_promotion(path):
    """REQUIRE_APPROVAL's existing-approval check (VB3-04): a note counts as already
    approved when the D12 lifecycle contract (bm_vault_lifecycle.py, the same module
    the candidate-withholding check above already loads) reads its promotion state as
    a CLEAN canonical -- the state only bm_vault_promotions.py's promote command
    (typed directly, or clicked through bm_vault_pane.py's Approve) can reach. No new
    approval store: the promotions and pane ceremony the row itself names are the
    smallest honest seam. A missing lifecycle module or an unreadable file reads as
    NOT approved -- require_approval fails toward withholding, never toward serving."""
    try:
        lifecycle = _load_bm_vault_lifecycle()
    except Exception:
        return False
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            state, _rec, problems = lifecycle.read_promotion(fh.read())
    except OSError:
        return False
    return lifecycle.counts_as_canonical(state, problems)


def _policy_deny(args, con):
    """(deny predicate | None, error | None, refusal reason | None, degradation box)
    for VB2-01's identity trim, joined by VB7-05's principal registry and now by
    VB3-04's dual principals, REQUIRE_APPROVAL and enterprise fail-closed behavior.

    None deny means no trimming, and every such case is STATED: an absent contract
    module degrades audibly (stderr), an absent policy file is the documented opt-in
    default (today's behavior, silent by design), and a PRESENT but broken policy is
    an error that fails the recall CLOSED, because an ACL that fails open on a typo is
    not an ACL. The registry is the same opt-in shape: no registry file, or an
    identity the registry does not know, changes nothing.

    Identity: --identity, else BM_IDENTITY, else anonymous (None) -- the HUMAN
    principal, unchanged in name and meaning from VB2-01. --agent-identity, else
    BM_AGENT_IDENTITY, else None, is the new, OPTIONAL agent principal (VB3-04): the
    two are combined by bm_vault_policy.decide_dual's intersection, never the union.
    Omitting it makes this byte-identical to the single-identity trim, so a
    single-machine, human-only caller sees no change at all.

    VB7-05: a REVOKED identity -- human or agent, either one -- is denied every note
    outright, before the path-level policy rules even run, and the reason string
    travels back to the caller so it can be recorded in the access audit as a
    refusal, not merely counted as an ordinary withholding.

    VB3-04 FAIL CLOSED: in enterprise mode (_enterprise_mode() above), a policy
    module that cannot be imported, or whose decide_dual() raises for a given note,
    is never read as "nothing is restricted". The returned deny predicate instead
    falls back to _is_restricted(con, path) for that note: RESTRICTED content stays
    withheld, unrestricted content still serves. The 4th return value is a list,
    empty when nothing degraded, holding one reason string the first time the
    fallback actually fires, so the caller can record the degradation in the access
    audit rather than only inferring it from a changed withheld count. Outside
    enterprise mode this fallback never engages: a broken module still degrades to
    "not trimmed" (module missing) or still raises uncaught (decide_dual crashes),
    exactly today's behavior."""
    degraded = []
    identity = args.get("identity")
    if not isinstance(identity, str):
        identity = os.environ.get("BM_IDENTITY") or None
    agent_identity = args.get("agent-identity")
    if not isinstance(agent_identity, str):
        agent_identity = os.environ.get("BM_AGENT_IDENTITY") or None
    purpose = args.get("purpose")
    if not isinstance(purpose, str):
        purpose = None
    enterprise = _enterprise_mode()

    candidates = {i for i in (identity, agent_identity) if i}
    if candidates:
        try:
            principals = _load_bm_vault_principals()
        except Exception as e:
            print("principal registry unavailable (%s); revocation is NOT enforced"
                  % e, file=sys.stderr)
            principals = None
        if principals is not None:
            vault_for_registry = _default_vault()
            rpath = principals.registry_path(vault_for_registry, args.get("registry"))
            registry, problems = principals.load(rpath)
            if problems:
                return None, "; ".join(problems), None, degraded
            for candidate in sorted(candidates):
                if principals.status_of(registry, candidate) == "revoked":
                    reason = "principal %r is revoked" % candidate
                    return (lambda path: True), None, reason, degraded

    try:
        pol = _load_bm_vault_policy()
    except Exception as e:
        msg = "access policy unavailable (%s)" % e
        if enterprise:
            print(msg + "; ENTERPRISE MODE fails CLOSED: restricted notes withheld, "
                  "unrestricted notes still served", file=sys.stderr)
            degraded.append(msg)
            return (lambda path: _is_restricted(con, path)), None, None, degraded
        print(msg + "; recall is NOT trimmed by identity", file=sys.stderr)
        return None, None, None, degraded
    vault = _default_vault()
    override = args.get("policy")
    if not isinstance(override, str):
        override = None
    ppath = pol.policy_path(vault, override)
    policy, problems = pol.load(ppath)
    if problems:
        return None, "; ".join(problems), None, degraded
    if policy is None:
        return None, None, None, degraded  # no policy file: everything readable, as always
    abs_vault = os.path.abspath(vault) if vault else None

    def deny(path):
        if abs_vault and os.path.abspath(path).startswith(abs_vault + os.sep):
            rel = os.path.relpath(path, vault)
        else:
            rel = path  # outside the vault: full path; "*" globs still cover it
        try:
            verdict = pol.decide_dual(policy, identity, agent_identity, purpose, rel)
        except Exception as e:
            if enterprise:
                if not degraded:
                    degraded.append("access policy crashed on decide (%s)" % e)
                return _is_restricted(con, path)
            raise
        if verdict == "require_approval":
            return not _has_canonical_promotion(path)
        return verdict == "deny"

    return deny, None, None, degraded


def cmd_recall(args):
    text = args.get("query")
    if not text:
        sys.stderr.write("bm_vault: recall needs --query \"a symptom in words\"\n")
        return 2
    # con is opened before _policy_deny (VB3-04): the enterprise fail-closed fallback
    # reads a candidate's own indexed body to tell restricted content apart from
    # ordinary content, and it needs this same connection to do that without a second
    # disk read.
    con = _connect()
    _schema(con)
    deny, policy_error, refusal_reason, degraded = _policy_deny(args, con)
    if policy_error:
        print("NO-DATA access policy: %s. Fix it (bm_vault_policy.py check) or "
              "remove it; a broken policy fails closed, not open." % policy_error)
        return 2
    if refusal_reason:
        print("REFUSED: %s; recall trimmed to zero notes" % refusal_reason)
    explain = [] if args.get("explain") else None
    fast = bool(args.get("fast"))
    fused, why = _search(con, text=text, limit=int(args.get("limit", 6)),
                         fast=fast, explain=explain, deny=deny)
    # A query-cache write (VB5-03) may have happened inside _search; this connection is never
    # committed anywhere else in this command, and an uncommitted write is lost when the
    # process exits, silently un-warming every "warm" repeat.
    con.commit()
    # VB2-01 and VB2-05 meet here, semantically merged at their rebase: the
    # policy trim runs inside the search so a denied note never reaches
    # ranking, and the ledger records only the hits actually served, so a
    # withheld note can never leak through the audit trail either.
    withheld_by_policy = why.pop("__policy_withheld__", 0)
    for line in explain or []:
        print("EXPLAIN %s" % line)
    ledger_hits = []
    withheld_out = []
    rc = _print_hits(con, fused, why,
                     "What this estate has already written about that:",
                     roots=_freshness_roots(args), ledger_hits=ledger_hits,
                     withheld_out=withheld_out)
    if withheld_by_policy:
        print("%d note(s) withheld by access policy" % withheld_by_policy)
    # VB3-04: degraded is populated by _policy_deny's own enterprise fail-closed
    # fallback, either at setup time (policy module unavailable) or lazily, the first
    # time the deny() closure caught decide_dual() raising (checked only after _search
    # above has actually run every candidate through it).
    if degraded:
        print("NOTE: %s; enterprise mode served unrestricted notes only until this "
              "is fixed" % degraded[0])
    # VB6-03: one id per answer, minted here and shared by both stores below. A caller
    # can pass --event-id to retry a specific answer idempotently. The id is resolved
    # against the ledger BEFORE either store is touched (fix for the collision-divergence
    # bug: appending the ledger conditionally but the audit unconditionally let the two
    # stores drift apart under the same join key). Three outcomes:
    #   - no caller id, or a caller id not yet on disk: fresh answer, both stores append.
    #   - a caller id already on disk under the SAME query: a genuine retry (a caller
    #     resending a dropped response, say) -- skip BOTH appends, counted on stderr.
    #   - a caller id already on disk under a DIFFERENT query: a collision with an
    #     unrelated answer -- refuse the whole append path, nothing written to either
    #     store, and the id is never handed back as if it were this answer's.
    event_id = args.get("event-id")
    forced = isinstance(event_id, str) and bool(event_id)
    existing = _ledger_lookup(event_id) if forced else None
    if not forced:
        event_id = uuid.uuid4().hex
    if existing is None:
        print("event: %s" % event_id)
        _append_ledger(text, ledger_hits, "fast" if fast else "dense", event_id)
        # VB6-06: the self-echo provenance marker, printed only when this answer's audit
        # row actually appended (a retry or refused collision never re-marks the output).
        marker = _append_audit(args, text, ledger_hits, withheld_out, withheld_by_policy,
                               event_id, refusal_reason=refusal_reason,
                               degraded_reason=(degraded[0] if degraded else None))
        if marker:
            print(marker)
    elif existing.get("query") == text:
        print("event: %s" % event_id)
        sys.stderr.write(
            "bm_vault: answer ledger already holds event %s; duplicate append skipped "
            "(ledger and audit both unchanged, 1 skipped)\n" % event_id)
    else:
        sys.stderr.write(
            "bm_vault: event %s already recorded under a different query (%r...); "
            "REFUSED-COLLISION, nothing appended to ledger or audit\n"
            % (event_id[:8], existing.get("query", "")[:20]))
        print("event: REFUSED-COLLISION")
    return rc


CONTENT_EXCERPT_CHARS = 2000   # same order of magnitude as _upsert_note's own embedding excerpt


def cmd_check(args):
    paths = args.get("paths", [])
    if not paths:
        sys.stderr.write("bm_vault: check needs --paths FILE [FILE ...]\n")
        return 2
    con = _connect()
    _schema(con)
    explain = [] if args.get("explain") else None
    limit = int(args.get("limit", 5))
    fast = bool(args.get("fast"))
    fused, why = _search(con, paths=paths, limit=limit, fast=fast, explain=explain)
    for line in explain or []:
        print("EXPLAIN %s" % line)
    roots = _freshness_roots(args)
    rc = _print_hits(con, fused, why,
                     "RECORDED FAILURES in the files you are about to touch:", roots=roots)
    # Content fallback. The anchor pass above only matches a FILE NAME some note already names,
    # so a brand-new path finds nothing even when its actual code repeats a documented failure
    # pattern verbatim. When that pass is empty or thin (the same under-fill condition _search's
    # own dense-staging logic already computes), read each path that exists on disk and run the
    # SAME lexical/BM25 pipeline `recall` already uses (_search text=..., forced fast=True: the
    # dense embedder never loads on this path, because this fires on every anchor miss -- the
    # common case for any file not yet named in a note -- and must stay sub-second). Lesson-class
    # notes only: a code excerpt is code vocabulary, not a symptom description, so log-kind notes
    # (which mention everything they touched) are pure noise here. --fast opts out of this too,
    # for cost-sensitive callers, though it costs nothing new: same excerpt cap _upsert_note uses,
    # same fast=True lexical pass recall already pays.
    if not fast and len(fused) < limit:
        already_shown = {nid for nid, _ in fused}
        for p in paths:
            if not os.path.isfile(p):
                continue
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    excerpt = f.read(CONTENT_EXCERPT_CHARS)
            except OSError:  # sbe: allow-silent unreadable file skipped, this is best-effort excerpt enrichment for recall only
                continue
            if not excerpt.strip():
                continue
            # allow_or_relax=False: the bare-OR fallback inside _ranked_by_rewrite has no
            # relevance floor and, measured on real code files, surfaces whichever note shares
            # the least-rare boilerplate token ("def", "usr") rather than anything about the
            # file's actual content -- it fired on every fixture tried, including an unrelated
            # colour-conversion module. The AND-with-2-agreeing-hits floor is the only part of
            # this signal that is real for a caller with no human-typed query to lean on.
            anchor_ids, _ = _split_kind(con, _anchor_query_ids(con, excerpt))
            lesson_ids = _ranked_by_rewrite(con, excerpt, kind="lesson", allow_or_relax=False)
            seen, ordered = set(), []
            for nid in anchor_ids + lesson_ids:
                if nid in already_shown or nid in seen:
                    continue
                seen.add(nid)
                ordered.append(nid)
            top = [(nid, 0.0) for nid in ordered[:2]]
            if top:
                print()
                print("possible pattern match (content, not filename): %s" % p)
                _print_hits(con, top, {},
                           "RECORDED FAILURES matching this file's content:", roots=roots)
    return rc


def cmd_status(args):
    con = _connect()
    _schema(con)
    total = con.execute("SELECT COUNT(*) c FROM notes").fetchone()["c"]
    by_src = con.execute("SELECT source, COUNT(*) c FROM notes GROUP BY source").fetchall()
    anchors = con.execute("SELECT COUNT(DISTINCT anchor) c FROM anchors").fetchone()["c"]
    links = con.execute("SELECT COUNT(*) c FROM links").fetchone()["c"]
    at = con.execute("SELECT v FROM meta WHERE k='indexed_at'").fetchone()
    print("index: %s" % INDEX_PATH)
    print("notes: %d (%s)" % (total, ", ".join("%s %d" % (r["source"], r["c"]) for r in by_src)
                              or "none"))
    vecs = con.execute("SELECT COUNT(*) c FROM vectors").fetchone()["c"]
    print("anchors: %d distinct file or symbol names; links: %d; dense vectors: %d of %d"
          % (anchors, links, vecs, total))
    if at:
        age = (time.time() - float(at["v"])) / 3600.0
        print("last indexed: %.1f hours ago" % age)
    else:
        print("last indexed: NEVER, run `bm_vault.py index` first")
    b = _embed_bin()
    if b:
        print("dense signal: %s (English only; a note written mostly in another language embeds "
              "poorly and leans on the lexical signals)" % os.path.basename(b))
    else:
        print("KNOWN LIMIT: no embed machine, so retrieval is lexical and anchors only; a note "
              "describing this problem in different words can be missed. Preferred: the bge "
              "wrapper tools/bm-embed-bge; fallback: swiftc -O tools/bm_embed.swift -o "
              "tools/bm-embed")
    return 0


# Flags that take a value: once one of these is the pending key, the very next token is
# consumed as its value UNCONDITIONALLY, even if that token itself starts with "--". Before
# this set existed, a value beginning with "--" (an identity spoofing itself as "--policy",
# say) was mistaken for a new flag, and the pending flag silently kept its True placeholder
# -- for --as specifically, that meant the access audit fell back to principal "NO-DATA",
# letting a caller suppress their own audit row. "paths" is deliberately excluded: it
# collects a run of bare positional file arguments, and a real flag must still be able to
# end that run.
_VALUE_FLAGS = {"vault", "root", "as", "policy", "identity", "query", "limit", "event-id",
                "registry", "agent-identity", "purpose", "budget"}


def _parse(argv):
    args, key = {"paths": []}, None
    for a in argv:
        if key in _VALUE_FLAGS:
            args[key] = a
            key = None
        elif a.startswith("--"):
            key = a[2:]
            args.setdefault(key, True)
        elif key == "paths":
            args["paths"].append(a)
        elif key:
            args[key] = a
            key = None
    return args


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    fns = {"index": cmd_index, "refresh": cmd_refresh, "status-line": cmd_status_line,
           "recall": cmd_recall, "check": cmd_check, "status": cmd_status}
    if argv[0] not in fns:
        sys.stderr.write("bm_vault: unknown command %r; known: %s\n"
                         % (argv[0], ", ".join(sorted(fns))))
        return 2
    return fns[argv[0]](_parse(argv[1:]))


if __name__ == "__main__":
    sys.exit(main())
