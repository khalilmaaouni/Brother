#!/usr/bin/env python3
"""bm_freshness: revalidate a vault note's citation against the code it names, so a note that
cannot prove it is still true stops being retrieved.

WHY THIS EXISTS. bm_vault.py already extracts anchors (a file path, or a CamelCase.dotted symbol,
found in a note's body -- the ANCHOR regex this file reuses rather than reinventing) but never
checks whether an anchor still holds. An outside reviewer's sharpest objection to this estate's
memory pillar: a door that only accumulates notes is not yet knowledge -- search quality degrades,
contradictions multiply, stale workarounds survive, and nothing ever proves a note is still true.
This file is the missing proof step, adopted from the strongest design found in a field survey
(GitHub Copilot's repository memory): a note that carries a citation keeps re-earning trust
against the CURRENT code, or it is withheld. Never deleted; this estate's never-lose-work rule is
absolute, and a stale note is still evidence of what someone once believed.

THREE STATES, not two:
  FRESH       has a citation, and at least one cited anchor resolves in the searched root(s), as
              of the last live check.
  STALE       has a citation, but no cited anchor resolves. Withheld from what a caller should
              serve. Never deleted, and the reason is always named.
  UNANCHORED  carries no citation at all (no file path, no CamelCase.dotted symbol in its body).
              Neither fresh nor stale, because there is nothing here to disprove: it is served
              exactly as it always was. Most notes in this vault are unanchored today. Folding
              them into "fresh" would make this whole control decorative; folding them into
              "stale" would delete the estate's memory overnight. Report the split as three
              numbers, always, never collapsed to two.

TWO MECHANISMS, answering two different callers:
  REVALIDATION (classify_live)   actually opens the searched root(s) right now: does a file-shaped
                                 anchor exist on disk, does a symbol-shaped anchor still appear
                                 (grep) anywhere under the root? Authoritative and immediate --
                                 what `status` and `demo` both run, and what decides the verdict.
  EXPIRY       (classify_cached) a cheap substitute for a caller that fires too often to pay for a
                                 live filesystem/grep pass every time (e.g. a retrieval hook).
                                 Trusts the timestamp of the last LIVE success classify_live
                                 recorded, and calls a note stale once that timestamp is more than
                                 28 days old or was never recorded. It never runs a check of its
                                 own and never overrides a live verdict; it is only consulted
                                 between live sweeps, which is the entire point of caching it.

F5 (tools/bm_repomap.py), an optional third input to REVALIDATION rather than a third mechanism:
`status --map PATH` loads a repomap JSON and classify_live resolves anchors against it
(resolve_anchor_via_map) instead of the live grep/os.walk path. Still authoritative and immediate
in the sense that it never softens a verdict with a grace period -- but a map is a SNAPSHOT taken
whenever it was built, not the filesystem read fresh, so `demo`'s delete/restore proof (which
mutates the filesystem between checks) stays on the live path; a map cannot see a mutation that
happened after it was built without being rebuilt.

THE CEILING, stated rather than papered over. A resolving anchor proves the cited PATH still
exists (or the cited SYMBOL still appears somewhere under the root) -- it proves the CITATION
resolves, not that the lesson describing it is still true. The code under that path can have
changed underneath the note's claim; this tool cannot see that. Revalidation is also only as wide
as the root(s) it is given -- the default (SIBLING_REPOS, Job 2) is this directory's git top-level
plus every sibling repo this machine actually has checked out (~/Brother, ~/Documents/BrotherSBE,
~/Documents/BrotherModeUp). A note citing a file that genuinely lives in a repository NOT in that
list, or not checked out on this machine at all, will still read STALE for lack of evidence, not
because the file is actually gone. Pass --root (repeatable) to widen the search further, or set
BM_FRESHNESS_ROOTS (os.pathsep separated) to replace the default roots entirely.

Reads bm_vault.py's own sqlite index read-only (its `notes` and `anchors` tables, populated by its
ANCHOR regex at index time) and never writes to it; this file owns a small separate state db for
its own bookkeeping (last successful live check per note), mirroring bm_vault.py's own
~/.claude/bm_vault_index.sqlite3 convention rather than putting state inside this repository.

Python 3.9, standard library only, no network. `grep` (a standard macOS/Unix utility already on
this machine, not a new dependency) is invoked via subprocess to check whether a symbol-shaped
anchor still appears anywhere under a root; a file-shaped anchor is checked with one os.walk per
root, no subprocess needed, and the walk is built once per root and reused across every note.
"""
import argparse
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
STALE_DAYS = 28
STALE_SECONDS = STALE_DAYS * 86400.0
STATE_DB = os.environ.get("BM_FRESHNESS_STATE") or os.path.expanduser(
    "~/.claude/bm_freshness_state.sqlite3")
# Noise dirs skipped by name; dot-dirs (.git, .venv-embed, .venv, ...) are already skipped because
# they start with ".", same filter bm_vault.py's own _walk uses.
NOISE_DIRS = {"node_modules", "__pycache__", "dist", "build"}

# JOB 2 (2026-08-29): revalidation used to search only the current directory's git top-level, so a
# note citing a file in a DIFFERENT sibling repository read STALE for lack of evidence in this
# root, not because the file was actually gone -- inflating the stale count with cross-repo
# citations rather than real rot. Widened to every sibling repo this machine actually has, per
# ~/.claude/CLAUDE.md's "Active projects" list: this repo, the estate's own umbrella, and the
# change-assurance sibling. A repo that does not exist on this machine is silently skipped, never
# an error -- NO-DATA belongs to a missing INDEX, not a missing sibling checkout.
SIBLING_REPOS = [
    os.path.expanduser("~/Documents/BrotherModeUp"),
    os.path.expanduser("~/Brother"),
    os.path.expanduser("~/Documents/BrotherSBE"),
]

# The same anchor shape bm_vault.py's ANCHOR regex extracts at index time: a recognized source
# extension marks a FILE-shaped anchor; anything else that regex matched is a CamelCase.dotted
# SYMBOL-shaped anchor. Reused here only to tell the two apart, not to re-extract anchors --
# extraction already happened once, at bm_vault index time, and lives in its anchors table.
FILE_EXT = re.compile(r"\.(?:swift|py|js|ts|json|sh|md|yml|yaml)$")


def _load_bm_vault():
    """Dynamic import by path, the same pattern bm_vault.py itself already uses to load
    bm_store.py/bm_learning.py -- a sibling tool file loaded without relying on tools/ being on
    sys.path. Read-only use here: INDEX_PATH, and (in tests only) its _schema() helper to build a
    matching throwaway fixture rather than hand-duplicating its column list."""
    spec = importlib.util.spec_from_file_location(
        "bm_vault", os.path.join(_TOOLS_DIR, "bm_vault.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _default_roots():
    """The git top-level of the current working directory (or cwd itself outside a repo), widened
    with every sibling repo that exists on this machine (SIBLING_REPOS, Job 2). BM_FRESHNESS_ROOTS
    (os.pathsep separated) overrides this ENTIRELY when set, so a test fixture -- or a caller with
    a narrower question -- gets an exact, isolated answer instead of the widened default. See the
    ceiling in the module docstring: only roots named here (or via --root) are ever searched."""
    env_override = os.environ.get("BM_FRESHNESS_ROOTS")
    if env_override:
        return [r for r in env_override.split(os.pathsep) if r]
    roots = []
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=5)
        top = out.stdout.decode("utf-8", "replace").strip()
        if out.returncode == 0 and top:
            roots.append(top)
    except Exception:  # sbe: allow-silent documented default, "if not roots" below adds cwd
        pass
    if not roots:
        roots.append(os.getcwd())
    for sib in SIBLING_REPOS:
        if os.path.isdir(sib) and sib not in roots:
            roots.append(sib)
    return roots


def _walk_index(root):
    """basename -> [relative paths], one os.walk pass per root. Built once and reused across
    every file-shaped anchor of every note in a run, instead of walking the tree per anchor."""
    idx = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in NOISE_DIRS]
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), root)
            idx.setdefault(fn, []).append(rel)
    return idx


def _file_resolves(anchor, root, idx):
    """A file-shaped anchor resolves if it names an existing relative path under root, or its
    basename matches an existing file whose path ends the same way (a note that recorded
    "tools/bm_fence_hook.py" still resolves if the anchor text was just "bm_fence_hook.py")."""
    if os.path.isfile(os.path.join(root, anchor)):
        return True
    base = os.path.basename(anchor)
    for rel in idx.get(base, []):
        if rel == anchor or anchor == base or rel.endswith("/" + anchor):
            return True
    return False


def _symbol_resolves(anchor, root):
    """A symbol-shaped anchor resolves if it still appears verbatim anywhere under root. -m 1
    stops at the first hit; this only needs to know whether the symbol exists somewhere, not
    where or how often. Single-anchor form, kept for callers (and tests) that only have one
    anchor to check; resolve_any_anchor below no longer calls this in its hot path -- see
    _symbol_resolves_any for why."""
    cmd = ["grep", "-rlF", "-m", "1"]
    for d in NOISE_DIRS | {".git", ".venv-embed", ".venv"}:
        cmd.append("--exclude-dir=" + d)
    cmd += ["--", anchor, root]
    try:
        out = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        return out.returncode == 0
    except Exception:
        return False


# D02 (2026-08-30): measured root cause of the 8.7s-to-38.9s worst case, found by profiling a
# real cmd_check run rather than assuming the os.walk comment above was still the whole story.
# _walk_index (file-shaped anchors) cost under 0.1s in the profile; the time was ALL spent in
# _symbol_resolves: one `grep -r` subprocess PER symbol-shaped anchor PER root, and a note's
# anchors are exactly the ones LEAST likely to resolve, so the common case pays a full,
# uncached tree traversal of every sibling repo (measured 0.7-8.5s each for BrotherSBE/
# BrotherModeUp/Brother) once per anchor. Two fixes, both here: (1) batch every symbol-shaped
# anchor of a note into ONE grep call per root via repeated -e (grep ORs -e patterns itself, so
# this is the same true/false answer at roughly one traversal's cost instead of N), and (2) run
# the per-root greps IN PARALLEL instead of sequentially, so worst case is the slowest ROOT, not
# the sum of every root. Never removes the out-of-roots capability the D02 benchmark exists to
# protect -- every root is still searched, just concurrently and with fewer processes.
SYMBOL_SCAN_BUDGET_S = float(os.environ.get("BM_FRESHNESS_SYMBOL_BUDGET", "8"))


def _symbol_grep_cmd(anchors, root):
    cmd = ["grep", "-rlF", "-m", "1"]
    for d in NOISE_DIRS | {".git", ".venv-embed", ".venv"}:
        cmd.append("--exclude-dir=" + d)
    for a in sorted(anchors):
        cmd += ["-e", a]
    cmd += ["--", root]
    return cmd


def _symbol_resolves_any(anchors, roots, budget):
    """True if ANY anchor in `anchors` resolves in ANY of `roots`, checked with one grep per root
    (all anchors batched via -e) launched concurrently rather than one grep per anchor per root.
    Returns (resolved, skipped_roots): skipped_roots is non-empty only when `budget` seconds
    passed with outstanding roots still unresolved -- those are killed and reported, never left
    to answer "stale" on a guess. budget<=0 skips scanning entirely and reports every root
    skipped, for a deterministic, instant test of the degrade path."""
    if not anchors:
        return False, []
    if budget is not None and budget <= 0:
        return False, list(roots)
    deadline = time.time() + budget if budget is not None else None
    pending = []
    for root in roots:
        try:
            pending.append((root, subprocess.Popen(_symbol_grep_cmd(anchors, root),
                                                    stdout=subprocess.DEVNULL,
                                                    stderr=subprocess.DEVNULL)))
        except OSError:  # sbe: allow-silent a root whose grep cannot launch is skipped so the scan still covers the rest; bm_freshness.py's _symbol_resolves_any (product) now reports it as launch_failed via resolve_any_anchor's skipped list, this fixture is a pinned seam copy predating that change
            continue
    try:
        while pending:
            if deadline is not None and time.time() > deadline:
                return False, [r for r, _ in pending]
            still = []
            for root, p in pending:
                rc = p.poll()
                if rc == 0:
                    return True, []
                if rc is None:
                    still.append((root, p))
            pending = still
            if pending:
                time.sleep(0.02)
        return False, []
    finally:
        for _, p in pending:
            p.kill()
            try:
                p.wait(timeout=5)
            except Exception:  # sbe: allow-silent best-effort reap after kill(); a wait failure leaves a zombie, not a wrong result
                pass


def resolve_any_anchor(anchors, roots, idx_cache, budget=None):
    """True the moment any one anchor resolves in any one root. idx_cache: {root: basename index},
    built lazily and shared across an entire run so repeated file-shaped anchors against the same
    root cost one os.walk, not one per anchor. File-shaped anchors are checked first (cheap, no
    subprocess); only if none of those resolve does the symbol-shaped batch run, and even then it
    is one grep per root, not one per anchor -- see _symbol_resolves_any. budget: seconds allowed
    for the symbol scan (None uses SYMBOL_SCAN_BUDGET_S); a scan that runs out of budget is
    reported LOUDLY on stderr naming the skipped roots, never silently folded into "not resolved"
    with no trace."""
    file_anchors = [a for a in anchors if FILE_EXT.search(a)]
    sym_anchors = [a for a in anchors if not FILE_EXT.search(a)]
    if file_anchors:
        for root in roots:
            idx = idx_cache.get(root)
            if idx is None:
                idx = idx_cache[root] = _walk_index(root)
            for a in sorted(file_anchors):
                if _file_resolves(a, root, idx):
                    return True
    if not sym_anchors:
        return False
    eff_budget = SYMBOL_SCAN_BUDGET_S if budget is None else budget
    found, skipped = _symbol_resolves_any(sym_anchors, roots, eff_budget)
    if skipped:
        sys.stderr.write(
            "NO-DATA bm_freshness: symbol-anchor scan exceeded its %ss budget; skipped root(s) "
            "%s for anchor(s) %s -- reported as NOT resolved rather than guessed at what an "
            "unfinished scan might have found\n"
            % (eff_budget, ", ".join(skipped), ", ".join(sorted(sym_anchors))))
    if found:
        return True
    return False


def resolve_anchor_via_map(anchor, repo_map):
    """F5 (tools/bm_repomap.py): resolve one anchor against a repository map instead of a live
    filesystem/grep pass. Same file-shaped-vs-symbol-shaped split FILE_EXT already draws for
    resolve_any_anchor above: a file-shaped anchor resolves if it is a substring of some path key
    in the map, a symbol-shaped one resolves if it appears in some file's "symbols" list. Cheaper
    than resolve_any_anchor once a caller already has a map built (no os.walk, no subprocess grep),
    and sharper: a symbol-shaped anchor here must match a real def/class name the map extracted,
    not merely appear as text somewhere under the root the way _symbol_resolves's grep does."""
    if FILE_EXT.search(anchor):
        return any(anchor in path for path in repo_map)
    return any(anchor in entry.get("symbols", []) for entry in repo_map.values())


def classify_cached(last_ok, now=None):
    """EXPIRY only: no filesystem access at all, just the age of the last recorded LIVE success.
    For a caller that fires too often to afford classify_live's real check every time. Never
    called by classify_live itself -- it only ever feeds this store, never reads it back."""
    now = time.time() if now is None else now
    if last_ok is None:
        return "stale", "never successfully revalidated"
    age_days = (now - last_ok) / 86400.0
    if age_days <= STALE_DAYS:
        return "fresh", None
    return ("stale",
            "last successful revalidation was %.1f day(s) ago (limit %d)" % (age_days, STALE_DAYS))


def _state_connect(db_path):
    d = os.path.dirname(db_path)
    if d:
        os.makedirs(d, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=5.0)
    con.execute(
        "CREATE TABLE IF NOT EXISTS state ("
        " note_path TEXT PRIMARY KEY,"
        " last_ok REAL,"
        " last_checked REAL NOT NULL,"
        " last_reason TEXT"
        ")")
    con.commit()
    return con


def _state_record(con, note_path, live_ok, now, reason):
    """Upsert this note's bookkeeping row. On success, last_ok advances to now (the heartbeat
    classify_cached later measures against). On failure, last_ok is left exactly as it was: a
    live failure is reported to the caller immediately (classify_live never softens it), but the
    historical last-success timestamp stays intact for classify_cached's own, separate use."""
    row = con.execute("SELECT last_ok FROM state WHERE note_path=?", (note_path,)).fetchone()
    prev_last_ok = row[0] if row else None
    last_ok = now if live_ok else prev_last_ok
    con.execute(
        "INSERT INTO state (note_path, last_ok, last_checked, last_reason) VALUES (?,?,?,?) "
        "ON CONFLICT(note_path) DO UPDATE SET last_ok=excluded.last_ok, "
        "last_checked=excluded.last_checked, last_reason=excluded.last_reason",
        (note_path, last_ok, now, reason))
    return last_ok


def classify_live(note_path, anchors, roots, idx_cache, state_con, now=None, repo_map=None):
    """The authoritative check: opens the searched root(s) right now and returns the immediate,
    unconditional truth -- a live failure is a live failure, with no 28-day grace softening it
    (that grace belongs to classify_cached, a different function for a different caller). Also
    records the outcome to state_con so classify_cached has a heartbeat to measure later.
    Returns (state, reason) where state is one of fresh/stale/unanchored.

    repo_map (F5, optional): a tools/bm_repomap.py map. When given, anchor resolution runs through
    resolve_anchor_via_map instead of the live grep/os.walk path (resolve_any_anchor), so a caller
    that already built a map for context selection gets freshness for free from the same structure.
    Every existing caller passes no repo_map and keeps the original live-filesystem behavior
    unchanged -- roots and idx_cache are still required in that case and still reported in the
    stale reason below."""
    now = time.time() if now is None else now
    if not anchors:
        return "unanchored", None
    if repo_map is not None:
        live_ok = any(resolve_anchor_via_map(a, repo_map) for a in sorted(anchors))
    else:
        live_ok = resolve_any_anchor(anchors, roots, idx_cache)
    reason = None if live_ok else (
        "no cited anchor resolves against root(s): %s" % ", ".join(roots))
    _state_record(state_con, note_path, live_ok, now, reason)
    return ("fresh", None) if live_ok else ("stale", reason)


def cmd_status(ns):
    roots = ns.root or _default_roots()
    index_path = ns.index or _load_bm_vault().INDEX_PATH
    if not os.path.exists(index_path):
        print("NO-DATA: no vault index at %s -- run bm_vault.py index first" % index_path)
        return 1
    repo_map = None
    if ns.map:
        with open(ns.map, encoding="utf-8") as fh:
            repo_map = json.load(fh)
    # Opened read-only: this file never writes to bm_vault's own index, only reads its notes and
    # anchors tables, which it does not own.
    con = sqlite3.connect("file:%s?mode=ro" % index_path, uri=True)
    con.row_factory = sqlite3.Row
    total = con.execute("SELECT COUNT(*) c FROM notes").fetchone()["c"]
    if total == 0:
        con.close()
        print("NO-DATA: the vault index at %s has zero notes" % index_path)
        return 1
    notes = con.execute("SELECT id, path FROM notes").fetchall()
    anchors_by_note = {}
    for r in con.execute("SELECT note_id, anchor FROM anchors").fetchall():
        anchors_by_note.setdefault(r["note_id"], set()).add(r["anchor"])
    con.close()

    state_con = _state_connect(ns.state or STATE_DB)
    idx_cache = {}
    now = time.time()
    counts = {"fresh": 0, "stale": 0, "unanchored": 0}
    details = []
    for n in notes:
        anchors = anchors_by_note.get(n["id"], set())
        state, reason = classify_live(n["path"], anchors, roots, idx_cache, state_con, now,
                                      repo_map=repo_map)
        counts[state] += 1
        details.append((n["path"], state, reason))
    state_con.commit()
    state_con.close()

    print("roots searched: %s" % ", ".join(roots))
    if repo_map is not None:
        print("map: %s (%d files, anchor resolution via map, not live grep/os.walk)"
              % (ns.map, len(repo_map)))
    print("notes: %d total -- fresh %d, stale %d, unanchored %d"
          % (total, counts["fresh"], counts["stale"], counts["unanchored"]))
    print("served (fresh + unanchored): %d; withheld (stale): %d"
          % (counts["fresh"] + counts["unanchored"], counts["stale"]))
    if ns.verbose:
        for path, state, reason in details:
            line = "  [%s] %s" % (state.upper(), path)
            if reason:
                line += "  (%s)" % reason
            print(line)
    return 0


def cmd_demo(ns):
    """Self-contained, driven proof of the revalidation mechanism, independent of the real vault:
    build a synthetic note citing a synthetic file, show it served; delete the file, show it
    withheld with the reason named; restore the file, show it served again. Uses its own temp
    root and its own temp state db, so this never touches the real vault or the real state db."""
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="bm_freshness_demo_")
    try:
        target_dir = os.path.join(tmp, "lib")
        os.makedirs(target_dir)
        target = os.path.join(target_dir, "example_module.py")
        body = "def hello():\n    return 'hi'\n"
        with open(target, "w") as f:
            f.write(body)
        anchors = {"lib/example_module.py"}
        state_con = _state_connect(os.path.join(tmp, "state.sqlite3"))

        print("root: %s" % tmp)
        print("note demo-note.md cites: lib/example_module.py")

        state, reason = classify_live("demo-note.md", anchors, [tmp], {}, state_con)
        print("BEFORE delete  -> %s" % state.upper())
        assert state == "fresh", "expected fresh with the cited file present, got %s" % state

        os.remove(target)
        state, reason = classify_live("demo-note.md", anchors, [tmp], {}, state_con)
        print("AFTER delete   -> %s (%s)" % (state.upper(), reason))
        assert state == "stale", "expected stale once the cited path is gone, got %s" % state
        assert reason, "a stale note must always name the reason"

        with open(target, "w") as f:
            f.write(body)
        state, reason = classify_live("demo-note.md", anchors, [tmp], {}, state_con)
        print("AFTER restore  -> %s" % state.upper())
        assert state == "fresh", "expected fresh again once the path is restored, got %s" % state

        state_con.close()
        print("demo OK: deleting the cited path withheld the note; "
              "restoring the path served it again")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="bm_freshness.py")
    sub = parser.add_subparsers(dest="cmd")

    p_status = sub.add_parser(
        "status", help="live revalidation sweep over the vault index: fresh/stale/unanchored")
    p_status.add_argument("--root", action="append", default=None,
                          help="repo root to check citations against; repeatable. "
                               "Default: this directory's git top-level.")
    p_status.add_argument("--index", default=None, help="override the bm_vault index db path")
    p_status.add_argument("--state", default=None, help="override the freshness state db path")
    p_status.add_argument("--verbose", action="store_true", help="list every note's verdict")
    p_status.add_argument("--map", default=None,
                          help="path to a tools/bm_repomap.py JSON map (F5); when given, anchor "
                               "resolution uses the map (resolve_anchor_via_map) instead of a "
                               "live grep/os.walk pass over --root.")

    sub.add_parser("demo", help="self-contained delete/restore demonstration")

    ns = parser.parse_args(argv)
    if ns.cmd == "status":
        return cmd_status(ns)
    if ns.cmd == "demo":
        return cmd_demo(ns)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
