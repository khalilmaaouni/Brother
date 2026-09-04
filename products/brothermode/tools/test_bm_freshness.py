#!/usr/bin/env python3
"""Regression tests for tools/bm_freshness.py, the vault note revalidation/expiry mechanism.
Standard library only. Run: python3 tools/test_bm_freshness.py

Every test runs against tempfile.TemporaryDirectory() roots and a state db path passed
explicitly, and never touches the real ~/.claude/bm_vault_index.sqlite3 or the real
~/.claude/bm_freshness_state.sqlite3 (BM_FRESHNESS_STATE is never read here; every state db is
created at an explicit tmp path). Assertions are on return values (classify_live/classify_cached
return tuples, cmd_status/cmd_demo/main return int exit codes) per this estate's rule that tests
assert exit codes, never printed verdicts; a few tests additionally capture stdout to prove the
human-facing text (NO-DATA, the reason string, the three-way counts) matches the contract, layered
on top of an exit-code assertion, never a substitute for one.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("bm_freshness", os.path.join(HERE, "bm_freshness.py"))
bf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bf)
sys.modules["bm_freshness"] = bf

_bv_spec = importlib.util.spec_from_file_location("bm_vault", os.path.join(HERE, "bm_vault.py"))
bv = importlib.util.module_from_spec(_bv_spec)
_bv_spec.loader.exec_module(bv)


def _build_vault_index(db_path, notes):
    """notes: [(path, anchors_set)]. Builds a throwaway sqlite db with bm_vault.py's own real
    schema (via its _schema() helper, so this fixture cannot drift from the columns cmd_status
    actually reads) and inserts the minimum rows cmd_status touches: notes.id/path, anchors."""
    con = sqlite3.connect(db_path)
    bv._schema(con)
    for path, anchors in notes:
        cur = con.execute(
            "INSERT INTO notes (path,title,descr,source,kind,mtime,body) "
            "VALUES (?,?,?,?,?,?,?)", (path, path, "", "vault", "lesson", 0.0, ""))
        nid = cur.lastrowid
        con.executemany("INSERT INTO anchors (note_id,anchor) VALUES (?,?)",
                        [(nid, a) for a in anchors])
    con.commit()
    con.close()


class TempRootCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.state_db = os.path.join(self.root, "state.sqlite3")

    def tearDown(self):
        self._tmp.cleanup()

    def touch(self, rel):
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write("content\n")
        return full


class TestAnchorResolution(TempRootCase):
    def test_file_anchor_resolves_when_present(self):
        self.touch("tools/thing.py")
        idx = bf._walk_index(self.root)
        self.assertTrue(bf._file_resolves("tools/thing.py", self.root, idx))

    def test_file_anchor_resolves_by_basename_suffix(self):
        # a note that only recorded the bare filename still resolves against the deeper path
        self.touch("tools/thing.py")
        idx = bf._walk_index(self.root)
        self.assertTrue(bf._file_resolves("thing.py", self.root, idx))

    def test_file_anchor_fails_when_absent(self):
        idx = bf._walk_index(self.root)
        self.assertFalse(bf._file_resolves("tools/nope.py", self.root, idx))

    def test_symbol_anchor_resolves_when_present(self):
        self.touch("src/Widget.swift")
        with open(os.path.join(self.root, "src", "Widget.swift"), "w") as f:
            f.write("struct Widget { func Widget.render() {} }\n")
        self.assertTrue(bf._symbol_resolves("Widget.render", self.root))

    def test_symbol_anchor_fails_when_absent(self):
        self.touch("src/Widget.swift")
        self.assertFalse(bf._symbol_resolves("NoSuchThing.method", self.root))

    def test_resolve_any_anchor_true_if_one_of_several_resolves(self):
        self.touch("tools/real.py")
        anchors = {"tools/ghost.py", "tools/real.py", "Other.symbol"}
        self.assertTrue(bf.resolve_any_anchor(anchors, [self.root], {}))

    def test_resolve_any_anchor_false_when_none_resolve(self):
        anchors = {"tools/ghost.py", "Nowhere.symbol"}
        self.assertFalse(bf.resolve_any_anchor(anchors, [self.root], {}))


class _FakeProc(object):
    """A Popen stand-in that is already finished with a fixed returncode, so a mocked grep never
    actually forks -- used to prove HOW MANY subprocesses a call spawns, independent of how fast
    the real filesystem happens to be on whatever machine runs this test."""
    def __init__(self, rc):
        self._rc = rc

    def poll(self):
        return self._rc

    def kill(self):
        pass

    def wait(self, timeout=None):
        return self._rc


class TestSymbolBatching(unittest.TestCase):
    """D02 (2026-08-30): the measured 8.7s-to-38.9s worst case was one `grep -r` subprocess PER
    symbol-shaped anchor PER root -- a note with several non-resolving anchors paid a full,
    uncached tree traversal once per anchor. Driven backwards: the OLD shape is "subprocess count
    scales with anchor count x root count"; the fix collapses that to "one grep per root,
    regardless of anchor count" by batching every anchor into that grep's own -e patterns. These
    tests assert the collapsed call count directly (a deterministic proxy for the wall-clock win,
    which does not depend on how fast this particular machine's disk happens to be today)."""

    def test_one_grep_per_root_not_one_per_anchor(self):
        calls = []

        def fake_popen(cmd, **kw):
            calls.append(cmd)
            return _FakeProc(1)  # never a match: exercises the full no-resolution path

        anchors = {"Widget.render", "Ledger.commit", "Ghost.method"}
        roots = ["/r1", "/r2"]
        with mock.patch.object(bf.subprocess, "Popen", side_effect=fake_popen):
            found, skipped = bf._symbol_resolves_any(anchors, roots, budget=5)
        self.assertFalse(found)
        self.assertEqual(skipped, [])
        self.assertEqual(len(calls), len(roots),
                         "expected exactly one grep per root (%d), regardless of %d anchors; "
                         "got %d calls -- the old per-anchor-per-root shape is back"
                         % (len(roots), len(anchors), len(calls)))
        for cmd in calls:
            e_count = sum(1 for tok in cmd if tok == "-e")
            self.assertEqual(e_count, len(anchors),
                             "every anchor must be batched into the same grep call via -e")

    def test_first_matching_root_short_circuits_the_rest(self):
        calls = []

        def fake_popen(cmd, **kw):
            calls.append(cmd)
            return _FakeProc(0 if len(calls) == 1 else 1)  # first root resolves immediately

        with mock.patch.object(bf.subprocess, "Popen", side_effect=fake_popen):
            found, skipped = bf._symbol_resolves_any({"Widget.render"}, ["/r1", "/r2"], budget=5)
        self.assertTrue(found)
        self.assertEqual(skipped, [])


class TestSymbolScanBudget(TempRootCase):
    """The loud-degrade half of the fix: a symbol scan that cannot finish inside its budget must
    say so, never silently answer "not resolved" as if it had actually looked everywhere."""

    def test_budget_exhausted_reports_every_pending_root_as_skipped_not_resolved(self):
        found, skipped = bf._symbol_resolves_any({"Some.symbol"}, [self.root], budget=0)
        self.assertFalse(found)
        self.assertEqual(skipped, [self.root])

    def test_resolve_any_anchor_writes_a_loud_notice_when_over_budget(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            result = bf.resolve_any_anchor({"Some.symbol"}, [self.root], {}, budget=0)
        self.assertFalse(result, "an unfinished scan must never be reported as resolved")
        err = buf.getvalue()
        self.assertIn("NO-DATA", err)
        self.assertIn(self.root, err)
        self.assertIn("Some.symbol", err)

    def test_ample_budget_does_not_print_anything(self):
        with open(os.path.join(self.root, "Widget.swift"), "w") as f:
            f.write("func Widget.render() {}\n")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            result = bf.resolve_any_anchor({"Widget.render"}, [self.root], {}, budget=5)
        self.assertTrue(result)
        self.assertEqual(buf.getvalue(), "")


class TestResolveAnchorViaMap(unittest.TestCase):
    """F5: anchor resolution against a tools/bm_repomap.py-shaped map, no filesystem at all."""

    def setUp(self):
        self.repo_map = {
            "tools/real.py": {"symbols": ["foo", "Bar.baz"], "imports": [], "parse_error": False},
        }

    def test_file_shaped_anchor_resolves_by_substring_of_a_path_key(self):
        self.assertTrue(bf.resolve_anchor_via_map("tools/real.py", self.repo_map))
        self.assertTrue(bf.resolve_anchor_via_map("real.py", self.repo_map))

    def test_file_shaped_anchor_fails_when_no_path_key_matches(self):
        self.assertFalse(bf.resolve_anchor_via_map("tools/ghost.py", self.repo_map))

    def test_symbol_shaped_anchor_resolves_when_listed(self):
        self.assertTrue(bf.resolve_anchor_via_map("foo", self.repo_map))
        self.assertTrue(bf.resolve_anchor_via_map("Bar.baz", self.repo_map))

    def test_symbol_shaped_anchor_fails_when_not_listed(self):
        self.assertFalse(bf.resolve_anchor_via_map("Nowhere.symbol", self.repo_map))

    def test_empty_map_resolves_nothing(self):
        self.assertFalse(bf.resolve_anchor_via_map("tools/real.py", {}))
        self.assertFalse(bf.resolve_anchor_via_map("foo", {}))


class TestClassifyLive(TempRootCase):
    def test_unanchored_note_is_its_own_state(self):
        con = bf._state_connect(self.state_db)
        state, reason = bf.classify_live("n.md", set(), [self.root], {}, con)
        self.assertEqual(state, "unanchored")
        self.assertIsNone(reason)
        con.close()

    def test_map_given_resolves_via_map_and_ignores_the_live_filesystem(self):
        # deliberately never touched on disk -- if this passed by falling back to the live
        # grep/os.walk path it would (correctly) read stale, so a "fresh" verdict here proves the
        # map branch actually ran instead of resolve_any_anchor.
        repo_map = {"tools/real.py": {"symbols": [], "imports": [], "parse_error": False}}
        con = bf._state_connect(self.state_db)
        state, reason = bf.classify_live("n.md", {"tools/real.py"}, [self.root], {}, con,
                                         repo_map=repo_map)
        self.assertEqual(state, "fresh")
        self.assertIsNone(reason)
        con.close()

    def test_map_given_but_anchor_absent_is_stale(self):
        con = bf._state_connect(self.state_db)
        state, reason = bf.classify_live("n.md", {"tools/ghost.py"}, [self.root], {}, con,
                                         repo_map={})
        self.assertEqual(state, "stale")
        self.assertTrue(reason)
        con.close()

    def test_fresh_when_anchor_resolves(self):
        self.touch("tools/real.py")
        con = bf._state_connect(self.state_db)
        state, reason = bf.classify_live("n.md", {"tools/real.py"}, [self.root], {}, con)
        self.assertEqual(state, "fresh")
        self.assertIsNone(reason)
        con.close()

    def test_stale_when_anchor_does_not_resolve_and_names_the_reason(self):
        con = bf._state_connect(self.state_db)
        state, reason = bf.classify_live("n.md", {"tools/ghost.py"}, [self.root], {}, con)
        self.assertEqual(state, "stale")
        self.assertTrue(reason)
        con.close()

    def test_live_failure_is_immediate_even_with_a_recent_prior_success(self):
        # a note that resolved a moment ago and then loses its target must go stale on the very
        # next live check -- classify_live carries no grace period of its own (that belongs only
        # to classify_cached, a different function for a different caller).
        target = self.touch("tools/real.py")
        con = bf._state_connect(self.state_db)
        state, _ = bf.classify_live("n.md", {"tools/real.py"}, [self.root], {}, con)
        self.assertEqual(state, "fresh")
        os.remove(target)
        state, reason = bf.classify_live("n.md", {"tools/real.py"}, [self.root], {}, con)
        self.assertEqual(state, "stale")
        self.assertTrue(reason)
        con.close()


class TestClassifyCached(unittest.TestCase):
    """The EXPIRY half: pure timestamp arithmetic, no filesystem. Calibrated both directions."""

    def test_never_checked_is_stale(self):
        state, reason = bf.classify_cached(None)
        self.assertEqual(state, "stale")
        self.assertIn("never", reason)

    def test_recent_success_is_fresh(self):
        now = 1_000_000.0
        five_days_ago = now - 5 * 86400
        state, reason = bf.classify_cached(five_days_ago, now=now)
        self.assertEqual(state, "fresh")
        self.assertIsNone(reason)

    def test_success_older_than_28_days_is_stale(self):
        now = 1_000_000.0
        thirty_days_ago = now - 30 * 86400
        state, reason = bf.classify_cached(thirty_days_ago, now=now)
        self.assertEqual(state, "stale")
        self.assertIn("28", reason)

    def test_exactly_28_days_is_still_fresh_29_is_not(self):
        now = 1_000_000.0
        state28, _ = bf.classify_cached(now - 28 * 86400, now=now)
        state29, _ = bf.classify_cached(now - 29 * 86400, now=now)
        self.assertEqual(state28, "fresh")
        self.assertEqual(state29, "stale")


class TestStateBookkeeping(TempRootCase):
    def test_success_advances_last_ok_failure_preserves_it(self):
        target = self.touch("tools/real.py")
        con = bf._state_connect(self.state_db)
        bf.classify_live("n.md", {"tools/real.py"}, [self.root], {}, con, now=100.0)
        row = con.execute("SELECT last_ok FROM state WHERE note_path=?", ("n.md",)).fetchone()
        self.assertEqual(row[0], 100.0)
        os.remove(target)
        bf.classify_live("n.md", {"tools/real.py"}, [self.root], {}, con, now=200.0)
        row = con.execute("SELECT last_ok, last_checked FROM state WHERE note_path=?",
                          ("n.md",)).fetchone()
        # last_ok is untouched by the failure (still the earlier success timestamp); last_checked
        # advances to the failing attempt, so the two can diverge and that divergence is legible.
        self.assertEqual(row[0], 100.0)
        self.assertEqual(row[1], 200.0)
        con.close()


class TestCmdStatus(TempRootCase):
    def test_no_data_on_missing_index(self):
        buf = io.StringIO()
        ns = _ns(root=[self.root], index=os.path.join(self.root, "does-not-exist.sqlite3"),
                state=self.state_db, verbose=False)
        with contextlib.redirect_stdout(buf):
            rc = bf.cmd_status(ns)
        self.assertEqual(rc, 1)
        self.assertIn("NO-DATA", buf.getvalue())

    def test_no_data_on_empty_index(self):
        index_db = os.path.join(self.root, "vault_index.sqlite3")
        _build_vault_index(index_db, [])
        buf = io.StringIO()
        ns = _ns(root=[self.root], index=index_db, state=self.state_db, verbose=False)
        with contextlib.redirect_stdout(buf):
            rc = bf.cmd_status(ns)
        self.assertEqual(rc, 1)
        self.assertIn("NO-DATA", buf.getvalue())

    def test_three_way_split_calibrated(self):
        # one note whose anchor resolves (fresh), one whose anchor does not (stale), one with no
        # anchor at all (unanchored) -- every bucket exercised in the same fixture.
        self.touch("tools/real.py")
        index_db = os.path.join(self.root, "vault_index.sqlite3")
        _build_vault_index(index_db, [
            ("fresh-note.md", {"tools/real.py"}),
            ("stale-note.md", {"tools/ghost.py"}),
            ("unanchored-note.md", set()),
        ])
        buf = io.StringIO()
        ns = _ns(root=[self.root], index=index_db, state=self.state_db, verbose=True)
        with contextlib.redirect_stdout(buf):
            rc = bf.cmd_status(ns)
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("fresh 1, stale 1, unanchored 1", out)
        self.assertIn("served (fresh + unanchored): 2; withheld (stale): 1", out)
        self.assertIn("[FRESH] fresh-note.md", out)
        self.assertIn("[STALE] stale-note.md", out)
        self.assertIn("[UNANCHORED] unanchored-note.md", out)

    def test_flipping_the_fixture_flips_the_counts(self):
        # same shape, opposite outcome: proves the check can go red, not just green.
        index_db = os.path.join(self.root, "vault_index.sqlite3")
        _build_vault_index(index_db, [
            ("only-note.md", {"tools/ghost.py"}),
        ])
        buf = io.StringIO()
        ns = _ns(root=[self.root], index=index_db, state=self.state_db, verbose=False)
        with contextlib.redirect_stdout(buf):
            rc = bf.cmd_status(ns)
        self.assertEqual(rc, 0)
        self.assertIn("fresh 0, stale 1, unanchored 0", buf.getvalue())

    def test_map_flag_resolves_a_citation_the_live_root_cannot_see(self):
        # the cited file is never written to self.root at all -- without --map this would read
        # STALE (the same shape test_flipping_the_fixture_flips_the_counts proves), so a FRESH
        # verdict here is proof --map actually switched the resolution path, not a coincidence.
        index_db = os.path.join(self.root, "vault_index.sqlite3")
        _build_vault_index(index_db, [("mapped-note.md", {"tools/elsewhere.py"})])
        map_path = os.path.join(self.root, "repomap.json")
        with open(map_path, "w", encoding="utf-8") as fh:
            json.dump({"tools/elsewhere.py": {"symbols": [], "imports": [],
                                               "parse_error": False}}, fh)
        buf = io.StringIO()
        ns = _ns(root=[self.root], index=index_db, state=self.state_db, verbose=False,
                map=map_path)
        with contextlib.redirect_stdout(buf):
            rc = bf.cmd_status(ns)
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("fresh 1, stale 0, unanchored 0", out)
        self.assertIn("anchor resolution via map", out)


class TestCmdDemo(unittest.TestCase):
    def test_demo_runs_clean_and_narrates_withhold_then_serve(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bf.cmd_demo(_ns())
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("BEFORE delete  -> FRESH", out)
        self.assertIn("AFTER delete   -> STALE", out)
        self.assertIn("AFTER restore  -> FRESH", out)


class TestDefaultRootsWidening(unittest.TestCase):
    """Job 2: revalidation used to search only the current repo, which inflated the stale count
    with citations to files that genuinely live in a sibling repository. Monkeypatches
    bf.SIBLING_REPOS and clears BM_FRESHNESS_ROOTS so this exercises the real widening logic in
    _default_roots(), not a restatement of it; restores both afterward so no other test in this
    file (or a later run) inherits the patch."""

    def setUp(self):
        self._orig_siblings = list(bf.SIBLING_REPOS)
        self._had_env = "BM_FRESHNESS_ROOTS" in os.environ
        self._orig_env = os.environ.pop("BM_FRESHNESS_ROOTS", None)
        self.tmp = tempfile.mkdtemp(prefix="bm-freshness-siblings-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def tearDown(self):
        bf.SIBLING_REPOS = self._orig_siblings
        if self._had_env:
            os.environ["BM_FRESHNESS_ROOTS"] = self._orig_env

    def test_a_missing_sibling_is_skipped_not_errored(self):
        ghost = os.path.join(self.tmp, "does-not-exist")
        bf.SIBLING_REPOS = [ghost]
        roots = bf._default_roots()
        self.assertNotIn(ghost, roots)

    def test_an_existing_sibling_is_added_to_the_default(self):
        sib = os.path.join(self.tmp, "sibling-repo")
        os.makedirs(sib)
        bf.SIBLING_REPOS = [sib]
        self.assertIn(sib, bf._default_roots())

    def test_env_override_replaces_the_widened_default_entirely(self):
        sib = os.path.join(self.tmp, "sibling-repo")
        os.makedirs(sib)
        bf.SIBLING_REPOS = [sib]
        override = os.path.join(self.tmp, "override-only")
        os.makedirs(override)
        os.environ["BM_FRESHNESS_ROOTS"] = override
        roots = bf._default_roots()
        self.assertEqual(roots, [override])
        self.assertNotIn(sib, roots)


BM_VAULT_TOOL = os.path.join(HERE, "bm_vault.py")


def _run_vault(argv, env):
    p = subprocess.run([sys.executable, BM_VAULT_TOOL] + argv, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


class TestWiredRecall(unittest.TestCase):
    """Job 1's actual proof: freshness wired into bm_vault's LIVE recall path (cmd_recall ->
    _print_hits in tools/bm_vault.py), not just the standalone classify_live function every class
    above this one exercises. Runs the real CLI end to end -- a real index, a real recall -- and
    is calibrated BOTH WAYS on that wired path per the row's own hard rule: served before the
    citation breaks, withheld with its reason once it breaks, served again once it is restored."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-wired-recall-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        vault = os.path.join(self.tmp, "vault")
        os.makedirs(vault)
        self.code_root = os.path.join(self.tmp, "code")
        os.makedirs(self.code_root)
        os.makedirs(os.path.join(self.tmp, ".claude"))
        self.cited_file = os.path.join(self.code_root, "PayoutLedger.py")
        with open(self.cited_file, "w") as f:
            f.write("# stub, not real source\n")
        with open(os.path.join(vault, "note.md"), "w") as f:
            f.write(
                "---\n"
                "name: payout-ledger-double-count\n"
                "description: PayoutLedger.py double counted a refund on retry\n"
                "type: project\n"
                "---\n"
                "Retries re-applied the same refund because PayoutLedger.py never checked "
                "idempotency before writing. See PayoutLedger.py.\n")

        self.env = dict(os.environ)
        self.env["HOME"] = self.tmp
        # Isolated root and state db: this must never resolve against the real sibling repos or
        # write to the real ~/.claude/bm_freshness_state.sqlite3.
        self.env["BM_FRESHNESS_ROOTS"] = self.code_root
        self.env["BM_FRESHNESS_STATE"] = os.path.join(self.tmp, "freshness_state.sqlite3")
        code, out = _run_vault(["index", "--vault", vault], self.env)
        self.assertEqual(code, 0, "fixture index build failed: %s" % out[:400])

    def _recall(self):
        # --fast: skip only the dense-embedder stage (30-75s on this machine when the binary is
        # present), which is an unrelated cost this test has no reason to pay. Freshness checking
        # itself is NOT gated by --fast -- it is cheap (an os.walk plus a grep, not a subprocess
        # loading torch) and is exactly the mechanism under test here.
        return _run_vault(
            ["recall", "--query", "PayoutLedger double counted a refund on retry",
             "--limit", "3", "--fast"], self.env)

    def test_served_then_withheld_then_served_again_on_the_same_recall(self):
        code, out = self._recall()
        self.assertEqual(code, 0, out[:400])
        self.assertIn("payout-ledger-double-count", out,
                      "the fixture lesson did not surface at all:\n%s" % out[:400])
        self.assertNotIn("WITHHELD", out,
                         "a note with a live citation was withheld before it ever broke:\n%s"
                         % out[:400])

        os.remove(self.cited_file)
        code, out = self._recall()
        self.assertEqual(code, 0, out[:400])
        self.assertIn("WITHHELD", out,
                      "recall served a stale note as current once its citation broke:\n%s"
                      % out[:400])
        self.assertIn("payout-ledger-double-count", out,
                      "the withheld note's title is missing, not just its normal framing:\n%s"
                      % out[:400])
        self.assertIn("no cited anchor resolves", out,
                      "the withheld note did not name its reason:\n%s" % out[:400])

        with open(self.cited_file, "w") as f:
            f.write("# stub, restored\n")
        code, out = self._recall()
        self.assertEqual(code, 0, out[:400])
        self.assertIn("payout-ledger-double-count", out, out[:400])
        self.assertNotIn("WITHHELD", out,
                         "recall kept withholding a note whose citation was restored:\n%s"
                         % out[:400])


class TestMainDispatch(unittest.TestCase):
    def test_no_subcommand_is_a_usage_error(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bf.main([])
        self.assertEqual(rc, 2)

    def test_demo_via_main(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bf.main(["demo"])
        self.assertEqual(rc, 0)


class _ns(object):
    """A tiny stand-in for argparse.Namespace so tests can call cmd_status/cmd_demo directly
    without building real argv, mirroring test_bm_recurrence.py's preference for exercising
    functions over shelling out. cmd_demo ignores every attribute, so the no-arg default works
    for it unchanged."""
    def __init__(self, root=None, index=None, state=None, verbose=False, map=None):
        self.root = root
        self.index = index
        self.state = state
        self.verbose = verbose
        self.map = map


if __name__ == "__main__":
    unittest.main(verbosity=2)
