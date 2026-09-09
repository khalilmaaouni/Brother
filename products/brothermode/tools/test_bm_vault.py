#!/usr/bin/env python3
"""Tests for bm_vault, on a synthetic corpus built to reproduce the real failure.

The corpus is small and made here rather than read from the founder's vault, so the result cannot
depend on what he happened to have written that week. Its SHAPE is the real one: two distilled
lesson notes that answer the question, several long session logs that mention every term, and an
aggregate index page that mentions everything in the estate. That shape is exactly what defeated
the first version, where the logs and the index page ranked above both lessons.

Run: python3 tools/test_bm_vault.py      (unittest output, exit 0 or 1)
"""
import contextlib
import importlib.util
import io
import inspect
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

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
TOOL = os.path.join(HERE, "bm_vault.py")

# Loaded by path, the same way test_bm_store.py loads it, so this file can build a real approved
# rule through the actual capture/receipt/approve flow rather than poking the schema directly.
_bs_spec = importlib.util.spec_from_file_location("bm_store", os.path.join(HERE, "bm_store.py"))
bs = importlib.util.module_from_spec(_bs_spec)
_bs_spec.loader.exec_module(bs)

# bm_vault itself, loaded the same way, so the link-resolution unit cases below can
# call its helpers directly instead of going through the CLI.
_bv_spec = importlib.util.spec_from_file_location("bm_vault", TOOL)
bm_vault = importlib.util.module_from_spec(_bv_spec)
_bv_spec.loader.exec_module(bm_vault)

LESSON_ROUTE = """---
name: free-text-in-a-routing-field
description: objects lit and rotated but opened nothing, because the router matches exact names
type: project
---
AtriumRoomRoute.forTarget matches exact strings and returns none otherwise; open() then ends in a
silent guard. Meanwhile touchableHotspots chose what LIGHTS using geometry alone and never asked
whether the object could open anything. See AtriumRoomViews.swift. Related: [[what-is-lit]].
"""

LESSON_LIT = """---
name: what-is-lit
description: the lit shape and the hit shape were different objects, and a 12pt slop discarded taps
type: project
---
Three causes behind one complaint that objects sometimes do not activate, all in
AtriumRoomViews.swift: the traced outline is not the hit target, the tap slop is twelve points,
and taps during the reveal vanish.
"""

LOG = """---
type: session-log
---
Long night on the room. We touched AtriumRoomViews.swift and AtriumRoomModel.json, discussed
objects, haptic, toolkit, open, tap, door, routing, lit, and shipped a build. Everything was
mentioned here because a log mentions everything it touched.
"""

INDEX_PAGE = """# Open Items
Every topic in the estate: room, objects, haptic, toolkit, open, tap, routing, lit, door,
AtriumRoomViews.swift, breathing, audio, locales, releases, and everything else.
"""

NEW_FILE_CONTENT = """# Investigating tap failures on the training range

Objects sometimes do not activate when tapped, same shape as the defect already written up
for AtriumRoomViews.swift.
"""

UNRELATED_CODE_CONTENT = """def hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hsl(r, g, b):
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    mx, mn = max(r, g, b), min(r, g, b)
    return (mx + mn) / 2
"""

ENV_VAULT_FIXTURE = """---
name: bm-vault-root-env-fixture
description: proves BM_VAULT_ROOT is honored when no --vault flag is passed
type: project
---
Marker phrase for the environment-resolution test: zephyrine marmalade lighthouse cartography.
"""


def _fixture_correction_rule(store_root):
    """One approved rule, through the real capture -> receipt -> approve flow bm_learn.py itself
    uses (see tools/test_bm_store.py's own `_approved` helper), not a row poked into the schema
    directly. Trigger text matches the recall query the DONE-CHECK names, so the test and the
    manual proof are asking the same question."""
    store = bs.Store(store_root, create=True)
    try:
        cand = store.capture_learning_candidate(
            "explicit_correction", "test fixture, not a real founder correction",
            trigger="which UI should questions and decisions go through",
            action="use the AskUserQuestion window, one decision per window",
            because="the founder said so, in a test",
            scope_type="global")
        rec = store.mint_approval_receipt(cand["candidate_uuid"],
                                          founder_response="yes, in a test")
        store.approve_learning_candidate(cand["candidate_uuid"], receipt=rec["token"],
                                         founder_ref="approved in a test")
    finally:
        store.close()


def run(argv, env):
    p = subprocess.run([sys.executable, TOOL] + argv, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


class VaultRetrieval(unittest.TestCase):
    """One synthetic corpus, indexed once, then queried from many angles.

    The methods are NUMBERED and that is load bearing. Building the index is the
    expensive part, so it happens once in setUpClass; and the environment-root
    case reindexes with a roots list that EXCLUDES the fixture vault, which
    purges these notes out of the shared index. Every query case must therefore
    run before it. unittest orders methods alphabetically, not by source
    position, so the ordering lives in the names.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-")
        cls.vault = os.path.join(cls.tmp, "vault")
        sessions = os.path.join(cls.vault, "Sessions")
        os.makedirs(sessions)

        with open(os.path.join(cls.vault, "route.md"), "w") as f:
            f.write(LESSON_ROUTE)
        with open(os.path.join(cls.vault, "lit.md"), "w") as f:
            f.write(LESSON_LIT)
        with open(os.path.join(cls.vault, "Open-Items.md"), "w") as f:
            f.write(INDEX_PAGE)
        for i in range(4):
            with open(os.path.join(sessions, "night-%d.md" % i), "w") as f:
                f.write(LOG)

        store_root = os.path.join(cls.tmp, "store-root")
        os.makedirs(store_root)
        _fixture_correction_rule(store_root)

        # A stub code root so bm_freshness's live wiring (Job 1) finds the fixture lessons'
        # citations resolvable and serves them as FRESH; this suite is testing retrieval ranking,
        # not freshness itself (that lives in test_bm_freshness.py), so the citations here must
        # not go stale for want of a file that was never going to exist on this machine. One file
        # is enough: both LESSON_ROUTE and LESSON_LIT cite AtriumRoomViews.swift, and
        # resolve_any_anchor only needs one cited anchor to resolve.
        cls.code_root = os.path.join(cls.tmp, "code")
        os.makedirs(cls.code_root)
        with open(os.path.join(cls.code_root, "AtriumRoomViews.swift"), "w") as f:
            f.write("// stub for retrieval-ranking tests, not a real source file\n")

        cls.env = dict(os.environ)
        # Point the tool at the synthetic corpus and its own throwaway index.
        cls.env["HOME"] = cls.tmp              # moves INDEX_PATH and the projects root
        # Pins the correction-rule federation at the fixture store above, not whatever real
        # project this suite happens to run inside of: BROTHERMODE_ROOT wins over marker/git
        # discovery (bm_store.resolve_root's own documented precedence), so the real approved
        # rules in this repo's own store never leak into a supposedly-isolated test corpus.
        cls.env["BROTHERMODE_ROOT"] = store_root
        # Pins freshness revalidation at the stub code root above instead of bm_freshness's own
        # widened sibling-repo default (BM_FRESHNESS_ROOTS overrides that default entirely), and
        # gives it a throwaway state db so this suite never touches the real
        # ~/.claude/bm_freshness_state.sqlite3.
        cls.env["BM_FRESHNESS_ROOTS"] = cls.code_root
        cls.env["BM_FRESHNESS_STATE"] = os.path.join(cls.tmp, "freshness_state.sqlite3")
        os.makedirs(os.path.join(cls.tmp, ".claude"))

        cls.index_code, cls.index_out = run(["index", "--vault", cls.vault], cls.env)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def _first_block(out):
        return out.split("\n\n")[1] if "\n\n" in out else out

    def test_01_the_corpus_indexes(self):
        self.assertTrue(self.index_code == 0 and "indexed" in self.index_out,
                        "index failed: %s" % self.index_out.strip()[:200])

    def test_02_a_distilled_lesson_outranks_the_logs_and_the_index_page(self):
        # THE REAL REGRESSION: asked with the symptom, a distilled lesson must come first,
        # and the session logs and the index page must not bury it.
        code, out = run(["recall", "--query",
                         "objects give haptic but do not open, routing, lit",
                         "--limit", "3"], self.env)
        first_block = self._first_block(out)
        self.assertIn("[lesson", first_block,
                      "recall ranked a non lesson first:\n%s" % out.strip()[:400])
        self.assertNotIn("Open Items", first_block,
                         "the aggregate index page ranked first, which is the original defect")

    def test_03_an_anchored_lookup_finds_the_lesson_for_a_named_file(self):
        # This is what the point-of-need hook uses.
        code, out = run(["check", "--paths", "AtriumRoomViews.swift", "--limit", "3"], self.env)
        self.assertEqual(code, 0, "check exited %d" % code)
        self.assertTrue("free-text-in-a-routing-field" in out or "what-is-lit" in out,
                        "check on a named file found neither lesson:\n%s" % out.strip()[:400])
        self.assertNotIn("[log", self._first_block(out),
                         "a log outranked a lesson on an anchored lookup")

    def test_035_a_new_file_surfaces_a_recorded_failure_by_content_not_filename(self):
        # A file at a path NO note has ever named, whose CONTENT mentions the same anchor-shaped
        # token (AtriumRoomViews.swift) a recorded lesson already names. --fast opts out of the
        # content fallback too, so the first call is the "before" state (anchor-only lookup on
        # the PATH, the original gap: NO-DATA on an unnamed path even though the content mentions
        # a known anchor) and the second is the "after" state. This exercises the reliable half of
        # the fallback (exact anchor-token matching), not the BM25 rare-terms half: measured on
        # 2026-08-29, pure prose-vs-code term rarity is not a signal that generalizes past the
        # size of whatever corpus happens to be indexed, so this suite does not claim it does.
        new_path = os.path.join(self.tmp, "training-range-tap-notes.md")
        with open(new_path, "w") as f:
            f.write(NEW_FILE_CONTENT)

        code, out = run(["check", "--paths", new_path, "--limit", "3", "--fast"], self.env)
        self.assertEqual(code, 1, "an unnamed file's anchor-only check unexpectedly matched:\n%s"
                         % out.strip()[:400])
        self.assertIn("NO-DATA", out,
                      "anchor-only check on an unnamed new file did not report NO-DATA:\n%s"
                      % out.strip()[:400])

        code, out = run(["check", "--paths", new_path, "--limit", "3"], self.env)
        self.assertIn("possible pattern match (content, not filename)", out,
                      "content fallback did not fire for a file whose content names a known "
                      "anchor:\n%s" % out.strip()[:400])
        self.assertTrue("what-is-lit" in out or "free-text-in-a-routing-field" in out,
                        "content fallback did not surface the lesson naming that anchor:\n%s"
                        % out.strip()[:400])

    def test_036_unrelated_code_never_fires_the_content_fallback(self):
        # The regression this fix exists for: measured 2026-08-29, the pre-fix fallback printed
        # two unrelated lesson notes for a plain hex/RGB colour-conversion module, because its
        # bare-OR relaxation had no relevance floor and fired on any file with ordinary text. A
        # false positive here is worse than the gap it replaced: silence trains nobody to distrust
        # the hook, noise does.
        new_path = os.path.join(self.tmp, "color_palette.py")
        with open(new_path, "w") as f:
            f.write(UNRELATED_CODE_CONTENT)

        code, out = run(["check", "--paths", new_path, "--limit", "3"], self.env)
        self.assertNotIn("possible pattern match", out,
                         "the content fallback fired on unrelated code with no real match:\n%s"
                         % out.strip()[:400])
        self.assertIn("NO-DATA", out,
                      "unrelated code did not report a clean NO-DATA:\n%s" % out.strip()[:400])

    def test_04_a_question_the_estate_has_never_met_reports_no_data(self):
        # It must say so, not invent a match.
        code, out = run(["recall", "--query",
                         "quantum entanglement of sourdough starters"], self.env)
        self.assertIn("NO-DATA", out,
                      "an unmatched query did not report NO-DATA:\n%s" % out.strip()[:200])

    def test_05_status_states_what_the_dense_signal_is_either_way(self):
        # Its absence as a KNOWN LIMIT, or its presence with the machine named.
        # Silence about it either way is the failure.
        code, out = run(["status"], self.env)
        self.assertTrue("KNOWN LIMIT" in out or "dense signal:" in out,
                        "status says nothing about the dense signal either way")

    def test_06_an_approved_correction_rule_federates_in_with_its_source_tag(self):
        code, out = run(["recall", "--query",
                         "which UI should questions and decisions go through",
                         "--limit", "3"], self.env)
        self.assertIn("correction-rule", out,
                      "the fixture correction rule did not surface with its source tag:\n%s"
                      % out.strip()[:400])
        self.assertIn("AskUserQuestion", out,
                      "the fixture rule's action text is missing from the recall output:\n%s"
                      % out.strip()[:400])

    def test_07_the_dense_stage_is_skipped_when_lexical_signals_already_answer(self):
        # STAGED RETRIEVAL. --explain must show the dense stage was skipped, not loaded.
        # This is the assertion that the embedder subprocess (30-75s on this machine) never
        # ran, without needing to hide the real binary to prove it.
        code, out = run(["recall", "--query",
                         "objects give haptic but do not open, routing, lit",
                         "--limit", "3", "--explain"], self.env)
        self.assertIn("EXPLAIN dense: skipped", out,
                      "staged recall loaded the dense embedder when lexical signals already "
                      "answered:\n%s" % out.strip()[:400])

    def test_08_fast_never_loads_the_embedder_and_still_returns_results(self):
        code, out = run(["recall", "--query",
                         "objects give haptic but do not open, routing, lit",
                         "--limit", "3", "--fast", "--explain"], self.env)
        self.assertIn("EXPLAIN dense: skipped (--fast)", out,
                      "--fast did not skip the dense stage:\n%s" % out.strip()[:400])
        self.assertIn("[lesson", out,
                      "--fast returned no lesson result:\n%s" % out.strip()[:400])

    def test_09_bm_vault_root_wins_when_no_vault_flag_is_passed(self):
        # Runs LAST on purpose: this reindexes with a roots list that excludes the fixture
        # vault, which purges the notes every case above depends on.
        vault2 = os.path.join(self.tmp, "vault2")
        os.makedirs(vault2)
        with open(os.path.join(vault2, "env-fixture.md"), "w") as f:
            f.write(ENV_VAULT_FIXTURE)
        env2 = dict(self.env)
        env2["BM_VAULT_ROOT"] = vault2
        code, out = run(["index"], env2)
        self.assertTrue(code == 0 and "indexed" in out,
                        "index with BM_VAULT_ROOT and no --vault failed: %s"
                        % out.strip()[:200])
        code, out = run(["recall", "--query",
                         "zephyrine marmalade lighthouse cartography"], env2)
        self.assertIn("bm-vault-root-env-fixture", out,
                      "BM_VAULT_ROOT was not honored when --vault was omitted:\n%s"
                      % out.strip()[:400])


OLD_LESSON = """---
type: failure
status: closed
created: 2026-01-01
description: "the old ruling about quibblewax handling"
---

# the old quibblewax ruling

Always flarn the quibblewax before serving. See ZorbleWidget.swift.
"""

NEW_LESSON = """---
type: failure
status: standing
created: 2026-08-29
description: "the current ruling about quibblewax handling"
supersedes: [[old-quibblewax]]
---

# the current quibblewax ruling

Never flarn the quibblewax; that ruling was reversed. See ZorbleWidget.swift.
"""


class ASupersededLessonIsNotServedAsCurrent(unittest.TestCase):
    """VF-14, 2026-08-29.

    bm_vault.py recognised supersession ONLY as a substring of a path
    ("superseded" in the directory name, or /archive, or /attic). It never read
    the supersedes: frontmatter field, which work package 16 shipped as a real
    traversable edge and which bm_vault_graph.py walks correctly at 28 of 28
    tests. Two subsystems modelled the same graph and the one consulted at the
    moment of an edit was blind to it.

    So the only way to retire a lesson from retrieval was to MOVE ITS FILE into
    a directory whose name contained the word superseded. A memory system that
    cannot retire a lesson keeps teaching the thing that was corrected, and a
    wrong lesson delivered confidently while someone is opening a file gets
    acted on, which is worse than silence.

    Its own class with its own corpus on purpose: VaultRetrieval indexes one
    shared corpus in setUpClass and its numbered cases depend on that ordering.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-supersede-")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(cls.vault)
        with open(os.path.join(cls.vault, "old-quibblewax.md"), "w") as f:
            f.write(OLD_LESSON)
        with open(os.path.join(cls.vault, "new-quibblewax.md"), "w") as f:
            f.write(NEW_LESSON)
        cls.code = os.path.join(cls.tmp, "code")
        os.makedirs(cls.code)
        with open(os.path.join(cls.code, "ZorbleWidget.swift"), "w") as f:
            f.write("// stub so the citations resolve and freshness is not the variable\n")
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BROTHERMODE_ROOT"] = cls.tmp
        cls.env["BM_FRESHNESS_ROOTS"] = cls.code
        cls.env["BM_FRESHNESS_STATE"] = os.path.join(cls.tmp, "freshness_state.sqlite3")
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        cls.index_code, cls.index_out = run(["index", "--vault", cls.vault], cls.env)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_01_the_corpus_indexed(self):
        self.assertEqual(self.index_code, 0, self.index_out)

    def test_02_the_superseded_note_is_withheld_and_names_its_successor(self):
        code, out = run(["check", "--paths", "ZorbleWidget.swift", "--limit", "5"], self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("WITHHELD (superseded)", out,
                      "the superseded lesson was served as an ordinary current "
                      "result:\n%s" % out[:900])
        self.assertIn("superseded by", out)

    def test_03_the_replacement_is_still_served_normally(self):
        """Calibration in the other direction: the mechanism must not withhold
        the note that did the superseding, which would retire both."""
        code, out = run(["check", "--paths", "ZorbleWidget.swift", "--limit", "5"], self.env)
        self.assertEqual(code, 0, out)
        # Titles fall back to the FILENAME when frontmatter carries no name:,
        # which is what these fixtures do, so assert on the title the tool
        # actually prints rather than on the markdown heading.
        self.assertIn("new quibblewax", out)
        self.assertNotIn("WITHHELD (superseded)  new quibblewax", out)
        # And it must appear ABOVE the withheld one, as an ordinary served hit.
        self.assertLess(out.index("new quibblewax"), out.index("WITHHELD (superseded)"))

    def test_04_the_superseded_note_is_still_on_disk(self):
        """Withheld, never deleted. The estate's never-lose-work rule applies to
        a retired lesson exactly as it applies to a file."""
        self.assertTrue(os.path.exists(os.path.join(self.vault, "old-quibblewax.md")))


CITED_OLD_LESSON = """---
type: failure
status: open
created: 2026-01-01
description: "the old ruling about widget handling"
---

# the old widget ruling

Always flarn the widget before serving. See WidgetV13.swift.
"""


class ACorrectionWrittenByVaultCorrectIsWithheldTheSameWay(unittest.TestCase):
    """Row V13 (docs/plan/READINESS-ROADMAP-2026-08-29.json): the note above
    proves the WITHHELD (superseded) mechanism works on a hand-authored
    `supersedes:` frontmatter line. This class proves the same mechanism
    fires on a note actually produced by scripts/vault_correct.py, so the
    two halves (the writer and this reader) are proven together rather than
    each proven only against its own fixtures."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-correct-recall-")
        cls.vault = os.path.join(cls.tmp, "vault")
        cls.failures = os.path.join(cls.vault, "40-Failures")
        os.makedirs(cls.failures)
        with open(os.path.join(cls.failures, "old-widget.md"), "w") as f:
            f.write(CITED_OLD_LESSON)
        cls.code = os.path.join(cls.tmp, "code")
        os.makedirs(cls.code)
        with open(os.path.join(cls.code, "WidgetV13.swift"), "w") as f:
            f.write("// stub so the citation resolves and freshness is not the variable\n")
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BROTHERMODE_ROOT"] = cls.tmp
        cls.env["BM_FRESHNESS_ROOTS"] = cls.code
        cls.env["BM_FRESHNESS_STATE"] = os.path.join(cls.tmp, "freshness_state.sqlite3")
        os.makedirs(os.path.join(cls.tmp, ".claude"))

        vault_correct = os.path.join(HERE, "..", "..", "..", "scripts", "vault_correct.py")
        p = subprocess.run(
            [sys.executable, vault_correct, "--vault", cls.vault, "--note", "old-widget",
             "the old widget ruling was measured wrong"],
            env=cls.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        cls.correct_code = p.returncode
        cls.correct_out = (p.stdout + p.stderr).decode("utf-8", "replace")
        cls.index_code, cls.index_out = run(["index", "--vault", cls.vault], cls.env)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_01_vault_correct_ran_clean_and_the_corpus_indexed(self):
        self.assertEqual(self.correct_code, 0, self.correct_out)
        self.assertEqual(self.index_code, 0, self.index_out)

    def test_02_the_note_vault_correct_supersedes_is_withheld(self):
        code, out = run(["check", "--paths", "WidgetV13.swift", "--limit", "5"], self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("WITHHELD (superseded)", out,
                      "a note produced by vault_correct.py's own superseding "
                      "note was served as ordinary current instead of "
                      "withheld:\n%s" % out[:900])
        self.assertIn("superseded by", out)


RECORD_NOTE = """---
name: grumbleflux-retention-ruling
description: the approved ruling on grumbleflux retention
type: project
authority: source_of_record
---

The approved ruling: grumbleflux export retention policy is ninety days, decided and signed off.
"""

CASUAL_UNDECLARED = """---
name: grumbleflux-export-notes
description: working notes about grumbleflux export retention policy questions
type: project
---

Grumbleflux export retention policy came up again. We talked about the grumbleflux export
retention policy at length, the export retention policy for grumbleflux, retention policy this,
export policy that. Nothing decided, just grumbleflux export retention policy chatter.
"""

CASUAL_DECLARED = """---
name: grumbleflux-aside
description: a passing aside that mentions grumbleflux
type: project
authority: casual
---

An aside: someone mentioned the grumbleflux export retention policy once in a hallway.
"""


class AuthorityOutranksSimilarity(unittest.TestCase):
    """D08 part B, 2026-08-30. The comparator is bm_vault_authority's, lexicographic:
    authority first, similarity second. A source_of_record note with LOWER similarity
    must beat a casual note with HIGHER similarity, and an undeclared note ranks as
    casual (absence never ranks above a declaration, and never below plain casual
    either). Own class with its own corpus, same reason as the supersession suite.
    Queries run --fast: authority ranking is lexical-signal territory and must not
    pay the 30-75s dense load."""

    QUERY = ["recall", "--query", "grumbleflux export retention policy",
             "--limit", "3", "--fast"]

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-authority-")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(cls.vault)
        for fn, text in (("ruling.md", RECORD_NOTE),
                         ("chatter.md", CASUAL_UNDECLARED),
                         ("aside.md", CASUAL_DECLARED)):
            with open(os.path.join(cls.vault, fn), "w") as f:
                f.write(text)
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BROTHERMODE_ROOT"] = cls.tmp
        cls.env["BM_FRESHNESS_ROOTS"] = cls.tmp   # no citations in these fixtures anyway
        cls.env["BM_FRESHNESS_STATE"] = os.path.join(cls.tmp, "freshness_state.sqlite3")
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        cls.index_code, cls.index_out = run(["index", "--vault", cls.vault], cls.env)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_01_the_corpus_indexed(self):
        self.assertEqual(self.index_code, 0, self.index_out)

    def test_02_lower_similarity_source_of_record_beats_higher_similarity_casual(self):
        # The chatter note repeats every query term and wins on BM25 alone; the ruling
        # mentions them once. Authority must put the ruling first anyway.
        code, out = run(self.QUERY, self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("grumbleflux-retention-ruling", out)
        self.assertIn("grumbleflux-export-notes", out)
        self.assertLess(out.index("grumbleflux-retention-ruling"),
                        out.index("grumbleflux-export-notes"),
                        "a casual note with better wording outranked the source of "
                        "record, the exact D08 defect:\n%s" % out[:900])
        self.assertIn("authority: source_of_record", out,
                      "the winning hit does not say WHY it won")

    def test_03_undeclared_ranks_as_casual_not_below_it(self):
        # Absence = casual, stated in the contract: the undeclared note with stronger
        # wording must still beat the note that DECLARES authority: casual with weaker
        # wording, so similarity keeps deciding within one level.
        code, out = run(self.QUERY, self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("grumbleflux-aside", out)
        self.assertLess(out.index("grumbleflux-export-notes"),
                        out.index("grumbleflux-aside"),
                        "an undeclared note ranked below a declared casual one, which "
                        "would punish the 800 undeclared notes:\n%s" % out[:900])


UNKNOWN_DECLARED = """---
name: grumbleflux-typoed-ruling
description: a ruling whose authority value carries a typo
type: project
authority: source-of-record
---

The typoed ruling also discusses grumbleflux export retention policy in passing.
"""


class AuthorityReviewFindings(unittest.TestCase):
    """Review findings 2026-08-30, both Criticals pinned. An unknown authority value
    must rank casual with a visible warning, never silently delete the note from
    results; and a deployed snapshot missing the contract module must degrade to
    fused order on stderr, never kill every recall."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-authfind-")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(cls.vault)
        for fn, text in (("ruling.md", RECORD_NOTE),
                         ("chatter.md", CASUAL_UNDECLARED),
                         ("typoed.md", UNKNOWN_DECLARED)):
            with open(os.path.join(cls.vault, fn), "w") as f:
                f.write(text)
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BROTHERMODE_ROOT"] = cls.tmp
        cls.env["BM_FRESHNESS_ROOTS"] = cls.tmp
        cls.env["BM_FRESHNESS_STATE"] = os.path.join(cls.tmp, "freshness_state.sqlite3")
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        cls.index_code, cls.index_out = run(["index", "--vault", cls.vault], cls.env)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    QUERY = ["recall", "--query", "grumbleflux export retention policy",
             "--limit", "5", "--fast"]

    def test_an_unknown_authority_value_ranks_casual_not_deleted(self):
        code, out = run(self.QUERY, self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("grumbleflux-typoed-ruling", out,
                      "a note with a typoed authority value vanished from results, "
                      "the silent-deletion Critical:\n%s" % out[:900])
        self.assertIn("ranks casual", out,
                      "the unknown value was not warned about anywhere")

    def test_a_missing_contract_module_degrades_to_fused_order(self):
        # A deployed snapshot directory carries bm_vault.py without the contract
        # module. Copy only the files such a snapshot holds and recall must still
        # answer, saying on stderr that authority ranking is unavailable.
        snap = os.path.join(self.tmp, "snapshot")
        os.makedirs(snap)
        for fn in ("bm_vault.py", "bm_freshness.py"):
            shutil.copy(os.path.join(HERE, fn), os.path.join(snap, fn))
        env = dict(self.env)
        env["HOME"] = os.path.join(self.tmp, "snaphome")
        os.makedirs(os.path.join(env["HOME"], ".claude"), exist_ok=True)
        tool = os.path.join(snap, "bm_vault.py")
        p = subprocess.run([sys.executable, tool, "index", "--vault", self.vault],
                           env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        p = subprocess.run([sys.executable, tool] + self.QUERY, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        combined = (p.stdout + p.stderr).decode("utf-8", "replace")
        self.assertEqual(p.returncode, 0,
                         "recall died in a snapshot missing the contract module, "
                         "the deployed-crash Critical:\n%s" % combined[:900])
        self.assertIn("grumbleflux", combined, "no hits survived the fallback")
        self.assertIn("authority ranking unavailable", combined,
                      "the degraded mode did not announce itself")


CANDIDATE_NOTE = """---
name: flibber-candidate-theory
description: a model-written theory about flibber calibration nobody has checked
type: failure
promotion: candidate
---

A theory: the flibber calibration drifts under load. See FlibberWidget.swift. Unvalidated.
"""

LEGACY_NOTE = """---
name: flibber-standing-lesson
description: the standing lesson about flibber calibration drift
type: failure
---

The standing lesson: flibber calibration drift comes from the clock, not the sensor.
See FlibberWidget.swift.
"""


class ACandidateNoteIsWithheldFromRetrieval(unittest.TestCase):
    """VB-07 clause, 2026-08-30. The lifecycle contract (D12) lets the estate declare
    `promotion: candidate` on a note: written, by anyone or anything, nobody has
    validated it. Recording that state does nothing if retrieval keeps serving a
    candidate exactly like a validated note, so retrieval withholds it AUDIBLY,
    while a legacy note (no promotion: field at all) stays served exactly as today,
    per the contract's own instruction. Own class, own corpus, same reason as the
    supersession suite."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-candidate-")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(cls.vault)
        # The promotion: field is written directly into the fixture, which is
        # exactly how a note promoted through bm_vault_promotions would look.
        with open(os.path.join(cls.vault, "flibber-candidate.md"), "w") as f:
            f.write(CANDIDATE_NOTE)
        with open(os.path.join(cls.vault, "flibber-legacy.md"), "w") as f:
            f.write(LEGACY_NOTE)
        cls.code = os.path.join(cls.tmp, "code")
        os.makedirs(cls.code)
        with open(os.path.join(cls.code, "FlibberWidget.swift"), "w") as f:
            f.write("// stub so citations resolve and freshness is not the variable\n")
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BROTHERMODE_ROOT"] = cls.tmp
        cls.env["BM_FRESHNESS_ROOTS"] = cls.code
        cls.env["BM_FRESHNESS_STATE"] = os.path.join(cls.tmp, "freshness_state.sqlite3")
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        cls.index_code, cls.index_out = run(["index", "--vault", cls.vault], cls.env)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_01_the_corpus_indexed(self):
        self.assertEqual(self.index_code, 0, self.index_out)

    def test_02_the_candidate_is_withheld_with_the_reason_said_out_loud(self):
        code, out = run(["check", "--paths", "FlibberWidget.swift", "--limit", "5"],
                        self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("WITHHELD (candidate, not yet validated)", out,
                      "an unvalidated candidate was served as an ordinary current "
                      "result, the exact D12 defect:\n%s" % out[:900])
        self.assertIn("flibber-candidate-theory", out)

    def test_03_the_legacy_note_is_still_served_normally(self):
        """Calibration in the other direction: a note with NO promotion: field is
        legacy, and legacy stays in ordinary retrieval exactly as today. Withholding
        it too would retire 825 of 825 real notes at a stroke."""
        code, out = run(["check", "--paths", "FlibberWidget.swift", "--limit", "5"],
                        self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("flibber-standing-lesson", out)
        self.assertNotIn("WITHHELD (candidate, not yet validated)  flibber-standing-lesson",
                         out)

    def test_04_the_candidate_note_is_still_on_disk(self):
        self.assertTrue(os.path.exists(os.path.join(self.vault, "flibber-candidate.md")))


CONTRA_A = """---
name: glimmer-mask-ruling
description: the ruling that glimmer output must be masked before quoting
type: failure
id: n-00c0ffee00c0ffee
authority: source_of_record
valid_from: 2026-08-01
contradicts: [[glimmer-verbatim-ruling]]
---

Mask glimmer output before quoting it. See GlimmerGadget.swift.

claim: masking was decided on 2026-08-01 [evidence: https://example.invalid/ruling]
"""

CONTRA_B = """---
name: glimmer-verbatim-ruling
description: the ruling that glimmer output must be quoted verbatim
type: failure
---

Quote glimmer output verbatim, never masked. See GlimmerGadget.swift.
"""

NEUTRAL_NOTE = """---
name: glimmer-sizing-note
description: an unrelated note about glimmer gadget sizing
type: failure
---

Glimmer gadget sizing is fixed at forty points. See GlimmerGadget.swift.
"""


class AContradictionIsSurfacedNeverHidden(unittest.TestCase):
    """VB-08 clause, 2026-08-30 (D10): a returned note that carries or is targeted
    by contradicts: prints the conflict beside the hit, never silently one side.
    Withholding either side would be the overwrite-or-coexist failure D10 names,
    so both notes stay ordinary hits and each flags the other, whichever side
    declared the edge (the fixture declares it on ONE side only, on purpose).
    This corpus also carries the VB-12 annotation fields (id:, authority:,
    valid_from:, a claim line), so the D01 done_check's output contract is
    asserted here rather than claimed."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-contra-")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(cls.vault)
        for fn, text in (("glimmer-mask-ruling.md", CONTRA_A),
                         ("glimmer-verbatim-ruling.md", CONTRA_B),
                         ("glimmer-sizing.md", NEUTRAL_NOTE)):
            with open(os.path.join(cls.vault, fn), "w") as f:
                f.write(text)
        cls.code = os.path.join(cls.tmp, "code")
        os.makedirs(cls.code)
        with open(os.path.join(cls.code, "GlimmerGadget.swift"), "w") as f:
            f.write("// stub so citations resolve and freshness is not the variable\n")
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BROTHERMODE_ROOT"] = cls.tmp
        cls.env["BM_FRESHNESS_ROOTS"] = cls.code
        cls.env["BM_FRESHNESS_STATE"] = os.path.join(cls.tmp, "freshness_state.sqlite3")
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        cls.index_code, cls.index_out = run(["index", "--vault", cls.vault], cls.env)
        cls.check_code, cls.check_out = run(
            ["check", "--paths", "GlimmerGadget.swift", "--limit", "5"], cls.env)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _block(self, title):
        """The printed block for one hit: from its TITLE LINE (two-space indent,
        kind bracket after) to the next blank line. Anchored on the title line's
        own shape, because a bare title substring also occurs inside the OTHER
        hit's CONTRADICTS line, which is this suite's whole subject."""
        out = self.check_out
        start = out.index("\n  %s  [" % title)
        end = out.find("\n\n", start + 1)
        return out[start:end if end != -1 else len(out)]

    def test_01_the_corpus_indexed(self):
        self.assertEqual(self.index_code, 0, self.index_out)
        self.assertEqual(self.check_code, 0, self.check_out)

    def test_02_the_declaring_side_flags_its_counterpart(self):
        self.assertIn("CONTRADICTS: glimmer-verbatim-ruling",
                      self._block("glimmer-mask-ruling"),
                      "the declaring side surfaced no conflict:\n%s" % self.check_out[:900])

    def test_03_the_targeted_side_flags_it_back_without_declaring_anything(self):
        """Symmetric on purpose: the fixture's verbatim note carries NO contradicts:
        of its own, so this only passes if the edge was expanded both ways at
        rebuild time."""
        self.assertIn("CONTRADICTS: glimmer-mask-ruling",
                      self._block("glimmer-verbatim-ruling"),
                      "only the declaring side surfaced the conflict, which serves "
                      "one side silently:\n%s" % self.check_out[:900])

    def test_04_neither_side_is_withheld(self):
        """Calibration the other way: a contradiction is a live disagreement, not a
        supersession, so both sides must stay ordinary served hits."""
        self.assertNotIn("WITHHELD", self.check_out)

    def test_05_an_uncontradicted_hit_carries_no_conflict_line(self):
        self.assertNotIn("CONTRADICTS", self._block("glimmer-sizing-note"))

    def test_06_a_served_hit_carries_id_authority_and_temporal_state(self):
        """The D01 done_check's output contract: memory ID, authority, temporal
        state on the hit itself. valid_from is past with no valid_to, so the
        as-of vocabulary calls it declared_true today; the undeclared fixtures
        read id none / authority casual / timeless_current."""
        block = self._block("glimmer-mask-ruling")
        self.assertIn("id: n-00c0ffee00c0ffee", block)
        self.assertIn("authority: source_of_record", block)
        self.assertIn("temporal: declared_true", block)
        neutral = self._block("glimmer-sizing-note")
        self.assertIn("id: none", neutral)
        self.assertIn("authority: casual", neutral)
        self.assertIn("temporal: timeless_current", neutral)

    def test_07_claim_level_evidence_is_printed_where_present(self):
        self.assertIn("evidence: https://example.invalid/ruling",
                      self._block("glimmer-mask-ruling"))


CONTRA_ANCHOR = """---
name: glimmer-anchor-ruling
description: a ruling that targets its counterpart through a #Section anchor
type: failure
contradicts: [[glimmer-verbatim-ruling#Verbatim Requirement]]
---

Mask glimmer output before quoting it, same subject as the other ruling,
targeted this time through an anchored wikilink. See GlimmerGadget.swift.
"""

_PADDING = "\n".join(
    "padding_field_%02d: filler text long enough to push the real "
    "declaration well past the old body[:1200] cutoff" % i for i in range(20))

CONTRA_LATE = """---
name: glimmer-late-ruling
description: a ruling whose contradicts: field is declared past byte 1200
type: failure
%s
contradicts: [[glimmer-verbatim-ruling]]
---

This note's frontmatter is padded on purpose so contradicts: lands past the
old body[:1200] slice, proving the fix reads the whole frontmatter block.
See GlimmerGadget.swift.
""" % _PADDING

PROSE_MENTION = """---
name: glimmer-prose-mention
description: a note that only talks about contradicts in its prose
type: failure
---

Meeting notes.

contradicts: [[glimmer-verbatim-ruling]] came up as a suggestion in the body
of this note, never in its frontmatter, and must never forge a real edge.
See GlimmerGadget.swift.
"""


class AContradictsFieldIsReadFromFrontmatterOnly(unittest.TestCase):
    """VB-12 minor: _rebuild_contradictions used to grep body[:1200] with no
    frontmatter delimiter, so a prose "contradicts:" line forged an edge and a
    real declaration past byte 1200 of a padded frontmatter block was missed.
    A #Section anchor on the target also failed to resolve, unlike the graph
    gate's own [[Note#Section]] handling."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-contra-front-")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(cls.vault)
        for fn, text in (("glimmer-verbatim-ruling.md", CONTRA_B),
                         ("glimmer-anchor-ruling.md", CONTRA_ANCHOR),
                         ("glimmer-late-ruling.md", CONTRA_LATE),
                         ("glimmer-prose-mention.md", PROSE_MENTION)):
            with open(os.path.join(cls.vault, fn), "w") as f:
                f.write(text)
        cls.code = os.path.join(cls.tmp, "code")
        os.makedirs(cls.code)
        with open(os.path.join(cls.code, "GlimmerGadget.swift"), "w") as f:
            f.write("// stub so citations resolve\n")
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BROTHERMODE_ROOT"] = cls.tmp
        cls.env["BM_FRESHNESS_ROOTS"] = cls.code
        cls.env["BM_FRESHNESS_STATE"] = os.path.join(cls.tmp, "freshness_state.sqlite3")
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        cls.index_code, cls.index_out = run(["index", "--vault", cls.vault], cls.env)
        cls.check_code, cls.check_out = run(
            ["check", "--paths", "GlimmerGadget.swift", "--limit", "5"], cls.env)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _block(self, title):
        out = self.check_out
        start = out.index("\n  %s  [" % title)
        end = out.find("\n\n", start + 1)
        return out[start:end if end != -1 else len(out)]

    def test_01_the_corpus_indexed(self):
        self.assertEqual(self.index_code, 0, self.index_out)
        self.assertEqual(self.check_code, 0, self.check_out)

    def test_02_an_anchored_target_still_resolves(self):
        self.assertIn("CONTRADICTS: glimmer-verbatim-ruling",
                      self._block("glimmer-anchor-ruling"),
                      "a #Section anchor on the target stem must not stop it "
                      "from resolving:\n%s" % self.check_out[:900])
        self.assertIn("CONTRADICTS: glimmer-anchor-ruling",
                      self._block("glimmer-verbatim-ruling"))

    def test_03_a_declaration_past_byte_1200_still_registers(self):
        self.assertIn("CONTRADICTS: glimmer-verbatim-ruling",
                      self._block("glimmer-late-ruling"),
                      "a contradicts: field declared past the old "
                      "body[:1200] cutoff must still be read from the "
                      "frontmatter block:\n%s" % self.check_out[:900])

    def test_04_a_prose_mention_never_forges_an_edge(self):
        self.assertNotIn("CONTRADICTS", self._block("glimmer-prose-mention"),
                         "a body line that merely starts with 'contradicts:' "
                         "in prose must never be read as a frontmatter "
                         "declaration:\n%s" % self.check_out[:900])


WIDGET_CURRENT_BODY = """---
lesson_id: widget-alpha-timeout-current
statement: widget alpha's timeout is 30 seconds
scope: widget-alpha-timeout
status: verified
verified_at: 2026-09-01
---

Widget alpha's timeout is 30 seconds, confirmed by the current test run.
See WidgetAlpha.swift.

GAUNTLET-POISON-LL3-FIXTURE-DO-NOT-COPY-INTO-A-REAL-VAULT
"""

WIDGET_OLD_BODY = """---
lesson_id: widget-alpha-timeout-old
statement: widget alpha's timeout is 60 seconds
scope: widget-alpha-timeout
---

Widget alpha's timeout is 60 seconds, an old, unverified claim.
See WidgetAlpha.swift.

GAUNTLET-POISON-LL3-FIXTURE-DO-NOT-COPY-INTO-A-REAL-VAULT
"""

CANDIDATE_VN1_BODY = """---
promotion: candidate
---

A model-written, unvalidated theory about widget alpha calibration.
See WidgetAlpha.swift.

GAUNTLET-POISON-LL3-FIXTURE-DO-NOT-COPY-INTO-A-REAL-VAULT
"""


class AVN1ApplicationVerdictFailsClosed(unittest.TestCase):
    """VN1 (2026-09-08): MISSING AUTHORITY FAILS CLOSED FOR APPLICATION. A same-scope,
    otherwise-authoritative CONTRADICTS pair (real applies_to anchor into a temp code
    tree, current verified_at, one side status: verified so current evidence actually
    selects a winner) is built once as an in-memory index (bm_vault._schema on a
    sqlite3(":memory:") connection, the same direct-index pattern
    LinkExpansionResolvesByFilenameStem already uses above), so _print_hits is called
    directly with a hand-built `fused`/`why` rather than through a real recall/search
    pass -- the fastest, most deterministic way to drive this exact code path.

    test_01 is the calibration: today's happy path (current evidence resolves the
    conflict) must still resolve exactly as before this fix. test_02 to test_04 are
    the three ways THIS unit's own authority can go missing at the exact point that
    conflict is decided: the lifecycle module fails to load, the contradiction module
    fails to load, and the contradiction module loads but recall_verdict itself
    raises. All three must withhold both notes with a NO-DATA reason, never serve the
    old plain annotation -- the VN1 defect this suite exists to close."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-vn1-")
        cls.code = os.path.join(cls.tmp, "code")
        os.makedirs(cls.code)
        with open(os.path.join(cls.code, "WidgetAlpha.swift"), "w") as f:
            f.write("// stub so citations resolve and freshness is not the variable\n")
        cls.state_env = os.path.join(cls.tmp, "freshness_state.sqlite3")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _con(self):
        # S6 (2026-09-08 VN1 fix): these two fixture notes are ordinary,
        # file-backed vault notes (not correction rules), so their D12
        # lifecycle read must actually succeed the way it would in
        # production, where _walk indexes only files that exist on disk --
        # a DB row with no backing file used to slide through on the old
        # OSError->legacy fallback S6 closed, which would now (correctly)
        # withhold them as unreadable and mask what these tests exist to
        # exercise.
        current_path = os.path.join(self.tmp, "widget-alpha-current.md")
        old_path = os.path.join(self.tmp, "widget-alpha-old.md")
        with open(current_path, "w", encoding="utf-8") as f:
            f.write(WIDGET_CURRENT_BODY)
        with open(old_path, "w", encoding="utf-8") as f:
            f.write(WIDGET_OLD_BODY)
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        bm_vault._schema(con)
        con.execute(
            "INSERT INTO notes (id, path, title, descr, source, kind, mtime, body) "
            "VALUES (1, ?, 'widget-alpha-timeout-current', 'the current timeout "
            "ruling', 'vault', 'lesson', 0, ?)",
            (current_path, WIDGET_CURRENT_BODY))
        con.execute(
            "INSERT INTO notes (id, path, title, descr, source, kind, mtime, body) "
            "VALUES (2, ?, 'widget-alpha-timeout-old', 'the old timeout claim', "
            "'vault', 'lesson', 0, ?)",
            (old_path, WIDGET_OLD_BODY))
        con.execute("INSERT INTO contradictions (stem, other_title) VALUES "
                    "('widget-alpha-current', 'widget-alpha-timeout-old')")
        con.execute("INSERT INTO contradictions (stem, other_title) VALUES "
                    "('widget-alpha-old', 'widget-alpha-timeout-current')")
        con.commit()
        return con

    def _run(self):
        con = self._con()
        fused = [(1, 10.0), (2, 9.0)]
        why = {1: ["symptom"], 2: ["symptom"]}
        os.environ["BM_FRESHNESS_STATE"] = self.state_env
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = bm_vault._print_hits(con, fused, why, "TEST HEADER:",
                                          roots=[self.code])
        finally:
            con.close()
        return rc, buf.getvalue()

    def test_01_the_happy_path_still_resolves_a_winner_as_today(self):
        """Calibration: nothing in this fix changes the case where both modules load
        and the resolver runs cleanly. Current evidence (status: verified) picks
        widget-alpha-timeout-current; the old, unverified side is withheld as
        contradicted, exactly as before VN1."""
        rc, out = self._run()
        self.assertEqual(rc, 0, out)
        self.assertIn("RESOLVED (applies)", out,
                      "the winning side of a resolvable contradiction was not "
                      "applied:\n%s" % out[:1200])
        self.assertIn("WITHHELD (contradicted, current evidence favors "
                      "widget-alpha-timeout-current)", out,
                      "the losing side was not withheld by name:\n%s" % out[:1200])
        self.assertNotIn("NO-DATA", out)

    def test_02_a_lifecycle_load_failure_withholds_every_note_no_data(self):
        """Fix 1: the lifecycle contract fails to load, so this loop can no longer
        tell a candidate from an ordinary note for ANY note reaching this point --
        both notes here are withheld, never served exactly as before."""
        real_loader = bm_vault._load_bm_vault_lifecycle

        def _raise():
            raise RuntimeError("VN1 test: lifecycle module deliberately broken")

        bm_vault._load_bm_vault_lifecycle = _raise
        try:
            rc, out = self._run()
        finally:
            bm_vault._load_bm_vault_lifecycle = real_loader
        self.assertEqual(rc, 0, out)
        self.assertIn("WITHHELD (NO-DATA: lifecycle contract unavailable "
                      "(VN1 test: lifecycle module deliberately broken))", out,
                      "a lifecycle load failure did not withhold with the NO-DATA "
                      "reason:\n%s" % out[:1600])
        self.assertEqual(out.count("WITHHELD (NO-DATA: lifecycle contract "
                                   "unavailable"), 2,
                         "both notes must be withheld, not just the one the old "
                         "D12 check would have caught:\n%s" % out[:1600])
        # N8(a) (2026-09-08 VN1 fix): _print_hits never prints raw body text for
        # ANY note, withheld or served -- only descr[:160] and extracted
        # annotations -- so asserting the body sentence is absent proved nothing
        # about withholding; it would pass even if this fix did nothing. Assert
        # on the description text instead, the field a served note's own
        # descr[:160] line would actually carry.
        self.assertNotIn("the current timeout ruling", out,
                         "a note description reached the served output while its "
                         "own authority module was missing:\n%s" % out[:1600])
        self.assertNotIn("the old timeout claim", out,
                         "a note description reached the served output while its "
                         "own authority module was missing:\n%s" % out[:1600])

    def test_03_a_contradiction_load_failure_withholds_the_conflicted_pair(self):
        """Fix 2: the contradiction resolver fails to load, so neither side of a
        declared CONTRADICTS pair can be told apart -- both are withheld, never
        served with the old plain, unresolved annotation."""
        real_loader = bm_vault._load_bm_vault_contradiction

        def _raise():
            raise RuntimeError("VN1 test: contradiction module deliberately broken")

        bm_vault._load_bm_vault_contradiction = _raise
        try:
            rc, out = self._run()
        finally:
            bm_vault._load_bm_vault_contradiction = real_loader
        self.assertEqual(rc, 0, out)
        self.assertIn("WITHHELD (NO-DATA: contradiction resolver unavailable "
                      "(VN1 test: contradiction module deliberately broken))", out,
                      "a contradiction load failure did not withhold with the "
                      "NO-DATA reason:\n%s" % out[:1600])
        self.assertEqual(out.count("WITHHELD (NO-DATA: contradiction resolver "
                                   "unavailable"), 2,
                         "both sides of the pair must be withheld:\n%s" % out[:1600])
        self.assertNotIn("CONTRADICTS:", out,
                         "the old plain, unresolved annotation was served instead "
                         "of a withhold:\n%s" % out[:1600])
        # N8(a): the body-text tautology, same fix as test_02 above.
        self.assertNotIn("the current timeout ruling", out)
        self.assertNotIn("the old timeout claim", out)

    def test_04_a_resolver_crash_withholds_never_serves_the_old_annotation(self):
        """Fix 3: the contradiction module loads fine, but recall_verdict itself
        raises. This is NO-DATA about the resolution, never the resolver's own
        genuine NO_DATA answer, and it must never fall through to the plain
        CONTRADICTS annotation the way it did before VN1."""
        real_loader = bm_vault._load_bm_vault_contradiction
        real_mod = real_loader()

        def _raising_recall_verdict(con, row, conflicting_titles, base_dir=None,
                                    allowed_roots=None):
            raise RuntimeError("VN1 test: resolver deliberately broken")

        real_mod.recall_verdict = _raising_recall_verdict
        bm_vault._load_bm_vault_contradiction = lambda: real_mod
        try:
            rc, out = self._run()
        finally:
            bm_vault._load_bm_vault_contradiction = real_loader
        self.assertEqual(rc, 0, out)
        self.assertIn("WITHHELD (NO-DATA: resolver error: VN1 test: resolver "
                      "deliberately broken)", out,
                      "a resolver crash did not withhold with a NO-DATA reason "
                      "naming the exception:\n%s" % out[:1600])
        self.assertEqual(out.count("WITHHELD (NO-DATA: resolver error:"), 2,
                         "both sides of the pair must be withheld on a crash, "
                         "same as a missing module:\n%s" % out[:1600])
        self.assertNotIn("CONTRADICTS:", out,
                         "a crash fell through to the old plain annotation, the "
                         "exact NO_DATA/crash conflation VN1 closes:\n%s"
                         % out[:1600])
        self.assertNotIn("RESOLVED (applies)", out,
                         "a crash must never be read as the resolver's own "
                         "APPLY verdict:\n%s" % out[:1600])

    def test_05_lifecycle_failure_withholds_a_genuine_candidate_note_too(self):
        """B: the exact case the D12 gate exists for -- a note explicitly declared
        promotion: candidate -- must ALSO withhold NO-DATA when the lifecycle
        module cannot even load, not just an ordinary note (test_02's broader
        calibration). Own single-note index: pairing this with the widget
        contradiction fixture would leave two reasons a note could be withheld,
        and this test wants exactly one."""
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        bm_vault._schema(con)
        con.execute(
            "INSERT INTO notes (id, path, title, descr, source, kind, mtime, body) "
            "VALUES (1, ?, 'widget-alpha-candidate-theory', 'an unvalidated "
            "theory', 'vault', 'lesson', 0, ?)",
            (os.path.join(self.tmp, "widget-alpha-candidate.md"), CANDIDATE_VN1_BODY))
        con.commit()
        fused = [(1, 10.0)]
        why = {1: ["symptom"]}
        os.environ["BM_FRESHNESS_STATE"] = self.state_env
        real_loader = bm_vault._load_bm_vault_lifecycle

        def _raise():
            raise RuntimeError("VN1 test: lifecycle module deliberately broken")

        bm_vault._load_bm_vault_lifecycle = _raise
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = bm_vault._print_hits(con, fused, why, "TEST HEADER:",
                                          roots=[self.code])
        finally:
            bm_vault._load_bm_vault_lifecycle = real_loader
            con.close()
        out = buf.getvalue()
        self.assertEqual(rc, 0, out)
        self.assertIn("WITHHELD (NO-DATA: lifecycle contract unavailable", out,
                      "the genuine candidate note was not withheld when the "
                      "module that would have caught it as candidate failed to "
                      "load:\n%s" % out[:1200])
        # N8(a): "A model-written..." is body text, never printed by _print_hits
        # for any note, withheld or served -- only descr[:160] is -- so this
        # used to pass regardless of whether the withhold fired. Assert on the
        # descr field this fixture actually declares instead.
        self.assertNotIn("an unvalidated theory", out,
                         "the candidate note's description reached served output "
                         "while the lifecycle module that would have caught it "
                         "was missing:\n%s" % out[:1200])


FORGED_APPROVAL_BODY = """---
human_approved: true
promoted_by: nobody-real
---

Forged-looking approval on a note nobody actually reviewed: the evidence
tier's own approval-forgery check would normally decide this, but that
check lives inside the contradiction module this fixture never lets load.

GAUNTLET-POISON-B2-FIXTURE-DO-NOT-COPY-INTO-A-REAL-VAULT
"""

DEAD_LOCATOR_BODY = """---
evidence_locator: /this/path/does/not/exist/NoSuchFile.swift
status: verified
verified_at: 2026-01-01
---

Claims a verified evidence_locator that points nowhere: the evidence
tier's own dead-locator check would normally refuse this, but that check
lives inside the contradiction module this fixture never lets load.

GAUNTLET-POISON-B2-FIXTURE-DO-NOT-COPY-INTO-A-REAL-VAULT
"""


class B2AnEvidenceTierLoadFailureWithholdsEveryHitOutright(unittest.TestCase):
    """B2 (2026-09-08 VN1 fix): LL-2's evidence tier (evidence_locator, verified_at,
    duplicate-slug checks) is the module's ONLY gate against a forged approval or a
    dead-locator claim, and it runs on EVERY hit, conflicted or not -- unlike the
    lifecycle/contradiction-pair checks above, there is no OTHER check in this loop
    that could catch a forged note when this one cannot run. So an unloadable
    contradiction module (the module that owns evidence_tier) must withhold EVERY
    note reaching this point, never only the notes that happen to be part of a
    declared CONTRADICTS pair, and never serve a note's description while its own
    forgery check could not run.

    Two notes seeded here, neither declaring a contradicts: pair (so the pre-VN1
    behavior would have served both untouched): one with forged human_approved/
    promoted_by frontmatter, one with a dead evidence_locator claiming verified
    status. Before this fix both would have been served in full, forged claims and
    all, because the evidence tier block was gated on `if contradiction is not
    None:` and simply skipped when the module failed to load."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-vn1-b2-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _con(self):
        # S6 (2026-09-08 VN1 fix): both fixture notes are ordinary, file-backed
        # vault notes, so the D12 lifecycle read must actually succeed the way
        # it would in production -- a DB row with no backing file now (correctly)
        # withholds as unreadable, which would mask what THIS test exists to
        # exercise (the evidence-tier withhold, not the promotion-state one).
        forged_path = os.path.join(self.tmp, "forged-approval.md")
        dead_locator_path = os.path.join(self.tmp, "dead-locator.md")
        with open(forged_path, "w", encoding="utf-8") as f:
            f.write(FORGED_APPROVAL_BODY)
        with open(dead_locator_path, "w", encoding="utf-8") as f:
            f.write(DEAD_LOCATOR_BODY)
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        bm_vault._schema(con)
        con.execute(
            "INSERT INTO notes (id, path, title, descr, source, kind, mtime, body) "
            "VALUES (1, ?, 'forged-approval-note', 'DESCR-FORGED-APPROVAL-B2', "
            "'vault', 'lesson', 0, ?)",
            (forged_path, FORGED_APPROVAL_BODY))
        con.execute(
            "INSERT INTO notes (id, path, title, descr, source, kind, mtime, body) "
            "VALUES (2, ?, 'dead-locator-note', 'DESCR-DEAD-LOCATOR-B2', "
            "'vault', 'lesson', 0, ?)",
            (dead_locator_path, DEAD_LOCATOR_BODY))
        con.commit()
        return con

    def test_both_notes_are_withheld_when_the_contradiction_module_cannot_load(self):
        con = self._con()
        fused = [(1, 10.0), (2, 9.0)]
        why = {1: ["symptom"], 2: ["symptom"]}
        os.environ["BM_FRESHNESS_STATE"] = os.path.join(self.tmp, "freshness_state.sqlite3")
        real_loader = bm_vault._load_bm_vault_contradiction

        def _raise():
            raise RuntimeError("VN1 B2 test: contradiction module deliberately broken")

        bm_vault._load_bm_vault_contradiction = _raise
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = bm_vault._print_hits(con, fused, why, "TEST HEADER:", roots=[self.tmp])
        finally:
            bm_vault._load_bm_vault_contradiction = real_loader
            con.close()
        out = buf.getvalue()
        self.assertEqual(rc, 0, out)
        reason = "WITHHELD (NO-DATA: evidence tier unavailable (contradiction module not loaded))"
        self.assertIn(reason, out,
                      "an absent contradiction module did not withhold with the "
                      "evidence-tier NO-DATA reason:\n%s" % out[:1600])
        self.assertEqual(out.count(reason), 2,
                         "both notes must be withheld outright, not only a "
                         "conflicting pair -- LL-2 runs on EVERY hit:\n%s" % out[:1600])
        self.assertNotIn("DESCR-FORGED-APPROVAL-B2", out,
                         "the forged-approval note's description reached served "
                         "output while the module that would have refused it was "
                         "missing:\n%s" % out[:1600])
        self.assertNotIn("DESCR-DEAD-LOCATOR-B2", out,
                         "the dead-locator note's description reached served "
                         "output while the module that would have refused it was "
                         "missing:\n%s" % out[:1600])


class ADefaultVaultDegradesOnAShapeInvalidConfig(unittest.TestCase):
    """VB-12 major, the bm_vault.py half: {"vault": 5} in the installer config
    used to reach os.path.isdir(5) / os.path.join(5, ...) downstream and crash
    instead of the documented degrade to an audible NO-DATA at exit 2."""

    def test_a_non_string_vault_value_is_nodata_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = os.path.join(tmp, ".claude")
            os.makedirs(cfg_dir)
            with open(os.path.join(cfg_dir, "bm_vault.json"), "w") as f:
                f.write('{"vault": 5}')
            env = dict(os.environ)
            for key in ("BM_VAULT_ROOT", "BROTHERMODE_VAULT"):
                env.pop(key, None)
            env["HOME"] = tmp
            code, out = run(["index"], env)
            self.assertEqual(code, 2, out)
            self.assertIn("NO-DATA", out)


DECAY_LOUD = """---
name: grumbleflux-decayed-notes
description: the louder note, which wins on wording alone
type: project
decay: 0.0
---

Grumbleflux telemetry rotation. Grumbleflux telemetry rotation is the grumbleflux
telemetry rotation everybody means when they say grumbleflux telemetry rotation.
"""

DECAY_QUIET = """---
name: grumbleflux-current-notes
description: the quieter note, which mentions the terms once
type: project
---

Grumbleflux telemetry rotation was settled here.
"""


class DecayReordersInsideOneAuthorityTier(unittest.TestCase):
    """E57 mechanism 2, borrowed from MemoryBank's Ebbinghaus decay and
    reinforcement (https://arxiv.org/abs/2305.10250, code at
    https://github.com/zhongwanjun/MemoryBank-SiliconFriend).

    THE GAP THIS CLOSES. Until this row bm_vault ranked a note written in
    February and never confirmed since exactly like a note confirmed
    yesterday: BM25 plus anchors plus links has no notion of a note's own
    usefulness over time. The loud note below repeats every query term and
    wins the fused score outright, which is what happens before the change;
    declaring decay: 0.0 must drop it below the quiet one.

    THE BOUNDARY, tested as hard as the reordering: the decayed note is still
    SERVED. The vault constitution says notes are never deleted, and a note
    that falls out of the result set has been deleted from the reader's view
    whatever the file system still holds, so bm_vault_decay.FLOOR keeps it in.
    Both notes are casual, so authority is a tie and the decayed similarity
    score is what decides, which is the seam this mechanism is wired into.

    Queries run --fast for the same reason every other ranking suite here
    does: this is lexical-signal territory and must not pay the dense load."""

    QUERY = ["recall", "--query", "grumbleflux telemetry rotation",
             "--limit", "3", "--fast"]

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-decay-")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(cls.vault)
        for fn, text in (("loud.md", DECAY_LOUD), ("quiet.md", DECAY_QUIET)):
            with open(os.path.join(cls.vault, fn), "w") as f:
                f.write(text)
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BROTHERMODE_ROOT"] = cls.tmp
        cls.env["BM_FRESHNESS_ROOTS"] = cls.tmp
        cls.env["BM_FRESHNESS_STATE"] = os.path.join(cls.tmp, "freshness_state.sqlite3")
        # The sidecar store, pointed at the temp tree: no test ever reads or
        # writes this machine's real reinforcement record.
        cls.env["BM_VAULT_DECAY"] = os.path.join(cls.tmp, "vault-decay.json")
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        cls.index_code, cls.index_out = run(["index", "--vault", cls.vault], cls.env)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_01_the_corpus_indexed(self):
        self.assertEqual(self.index_code, 0, self.index_out)

    def test_02_a_decayed_note_loses_rank_to_a_quieter_current_one(self):
        code, out = run(self.QUERY, self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("grumbleflux-current-notes", out)
        self.assertIn("grumbleflux-decayed-notes", out)
        self.assertLess(out.index("grumbleflux-current-notes"),
                        out.index("grumbleflux-decayed-notes"),
                        "a note declaring decay: 0.0 still outranked a current "
                        "note on wording alone:\n%s" % out[:900])

    def test_03_a_decayed_note_is_still_served_never_dropped(self):
        """The constitution's line, at the ranking seam: decay reorders and
        never removes."""
        code, out = run(self.QUERY, self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("grumbleflux-decayed-notes", out,
                      "decay deleted a note from the reader's view, which is "
                      "the one thing it may never do:\n%s" % out[:900])


class DecayIsACurveNotAFlag(unittest.TestCase):
    """bm_vault_decay's own contract, decided without an index. The curve is
    MemoryBank's Ebbinghaus shape (https://arxiv.org/abs/2305.10250) and the
    reinforcement half is what makes a confirmed note last longer rather than
    merely restart."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "bm_vault_decay", os.path.join(HERE, "bm_vault_decay.py"))
        cls.decay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.decay)

    def test_a_note_nobody_has_touched_is_never_penalised(self):
        """Absence is not a measurement: 898 notes declare nothing today, and
        demoting every one of them would be a rewrite of the ranking dressed
        up as a decay curve."""
        self.assertEqual(self.decay.retention("no-entry", "", {}), 1.0)
        self.assertEqual(self.decay.scale("no-entry", "", {}), 1.0)

    def test_retention_falls_with_the_days_since_the_last_reinforcement(self):
        now = 1_000_000_000.0
        store = {"n": {"reps": 0, "last": now - 60 * self.decay.SECONDS_PER_DAY}}
        old = self.decay.retention("n", "", store, now)
        store = {"n": {"reps": 0, "last": now - 1 * self.decay.SECONDS_PER_DAY}}
        fresh = self.decay.retention("n", "", store, now)
        self.assertLess(old, fresh)
        self.assertGreater(old, 0.0, "the curve never reaches zero")

    def test_reinforcement_lengthens_the_memory_rather_than_only_resetting_it(self):
        now = 1_000_000_000.0
        days = 60 * self.decay.SECONDS_PER_DAY
        once = self.decay.retention("n", "", {"n": {"reps": 1, "last": now - days}}, now)
        thrice = self.decay.retention("n", "", {"n": {"reps": 3, "last": now - days}}, now)
        self.assertGreater(thrice, once,
                           "a note confirmed three times fades as fast as one "
                           "confirmed once, so reinforcement does nothing")

    def test_the_scale_never_falls_below_the_floor(self):
        self.assertEqual(self.decay.scale("n", "---\ndecay: 0.0\n---\nbody"),
                         self.decay.FLOOR)
        self.assertGreater(self.decay.FLOOR, 0.0,
                           "a floor of zero deletes a note from the reader's view")

    def test_an_unreadable_declared_value_is_not_guessed_at(self):
        for body in ("---\ndecay: soon\n---\n", "---\ndecay: 7\n---\n",
                     "---\ndecay: -1\n---\n"):
            self.assertIsNone(self.decay.declared_retention(body), body)

    def test_reinforce_writes_the_sidecar_and_never_the_note(self):
        tmp = tempfile.mkdtemp(prefix="bm-vault-decay-store-")
        self.addCleanup(shutil.rmtree, tmp, True)
        note = os.path.join(tmp, "a-note.md")
        body = "---\nname: a-note\n---\nbody\n"
        with open(note, "w") as fh:
            fh.write(body)
        store = os.path.join(tmp, "vault-decay.json")
        first = self.decay.reinforce("a-note", path=store)
        second = self.decay.reinforce("a-note", path=store)
        self.assertEqual(first["reps"], 1)
        self.assertEqual(second["reps"], 2)
        self.assertTrue(os.path.exists(store))
        with open(note) as fh:
            self.assertEqual(fh.read(), body,
                             "reinforcement rewrote the note; the vault "
                             "constitution says notes are never rewritten")


class LinkExpansionResolvesByFilenameStem(unittest.TestCase):
    """The defect: link expansion joined a RAW WIKILINK STEM against a note title
    derived from frontmatter, and almost no note carries that frontmatter field, so
    signal C walked a fraction of its own graph. Measured on the live corpus before
    the fix: of 855 wikilink targets naming a note expansion is allowed to return,
    the old join reached 178, which is 20.8 percent. A lesson could be structurally
    reachable in the vault and still never arrive at the point of need.

    These cases pin the resolution rule itself against a fixture index, so the
    regression is caught by a unit test rather than by a corpus measurement nobody
    reruns."""

    def _index(self, rows, links):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, path TEXT UNIQUE, "
                    "title TEXT, descr TEXT, source TEXT, kind TEXT, mtime REAL, "
                    "body TEXT, content_hash TEXT)")
        con.execute("CREATE TABLE links (note_id INTEGER, target TEXT)")
        for nid, path, title, kind in rows:
            con.execute("INSERT INTO notes (id, path, title, kind) VALUES (?,?,?,?)",
                        (nid, path, title, kind))
        for nid, target in links:
            con.execute("INSERT INTO links (note_id, target) VALUES (?,?)", (nid, target))
        return con

    def test_a_target_whose_title_differs_from_its_filename_still_resolves(self):
        """The exact shape of the defect: the neighbour's title is nothing like the
        wikilink, because the wikilink names the FILE. Under the old join this row
        was invisible."""
        con = self._index(
            [(1, "/v/asking.md", "asking", "lesson"),
             (2, "/v/a-gate-can-be-red.md", "Some Human Written Title", "lesson")],
            [(1, "a-gate-can-be-red")])
        got = [r["id"] for r in bm_vault._linked_neighbors(con, [1])]
        self.assertEqual(got, [2])

    def test_anchors_paths_and_the_md_suffix_are_all_normalized(self):
        con = self._index(
            [(1, "/v/src.md", "src", "lesson"),
             (2, "/v/target-note.md", "unrelated title", "lesson")],
            [(1, "target-note#Section"), (1, "40-Failures/target-note"),
             (1, "target-note.md")])
        got = [r["id"] for r in bm_vault._linked_neighbors(con, [1])]
        self.assertEqual(got, [2], "all three spellings name the same note, once")

    def test_session_logs_are_still_excluded(self):
        """kind != 'log' was the one filter the old join carried and it must survive
        the fix: a session log is not a lesson and expansion must not drag it in."""
        con = self._index(
            [(1, "/v/src.md", "src", "lesson"),
             (2, "/v/night-1.md", "night 1", "log")],
            [(1, "night-1")])
        self.assertEqual(bm_vault._linked_neighbors(con, [1]), [])

    def test_a_dangling_target_resolves_to_nothing_rather_than_erroring(self):
        con = self._index([(1, "/v/src.md", "src", "lesson")], [(1, "#Links"), (1, "1,-49")])
        self.assertEqual(bm_vault._linked_neighbors(con, [1]), [])

    def test_no_top_hits_means_no_query(self):
        con = self._index([(1, "/v/src.md", "src", "lesson")], [(1, "x")])
        self.assertEqual(bm_vault._linked_neighbors(con, []), [])


VR1_ALPHA = """---
name: vr1-alpha
description: the note whose distinctive term an edit removes
type: project
---
commonword zebrafish alpha. A distilled lesson about the quibble handler.
"""

VR1_ALPHA_EDITED = """---
name: vr1-alpha
description: the same note after the edit that removed the distinctive term
type: project
---
commonword alpha. A distilled lesson about the quibble handler.
"""

VR1_BETA = """---
name: vr1-beta
description: an untouched neighbour, so a rebuild that lost real rows would show
type: project
---
commonword beta. Another lesson, never edited and never deleted.
"""

VR1_GAMMA = """---
name: vr1-gamma
description: the note whose FILE is deleted between two index passes
type: project
---
commonword gamma quokkaterm. The lesson that goes away with its file.
"""


class TheTextIndexHoldsNoTermsForANoteThatChangedOrWent(unittest.TestCase):
    """VR1, 2026-09-08. notes_fts is an EXTERNAL CONTENT fts5 table (content='notes'),
    so it keeps no copy of the text and a delete must be HANDED the values the row was
    indexed with. Both writers issued `DELETE FROM notes_fts WHERE rowid=?` instead, and
    both issued it AFTER the notes row had already been updated or deleted, so FTS5 read
    the new (or missing) content and subtracted the wrong terms, leaving the old ones in
    the index pointing at a rowid that no longer holds them.

    Measured on the live index before the fix: MATCH 'brother' returned 1472 rowids of
    which 1094 had no notes row at all; 'vault' 2533 against 350 real; 'retrieval' 404
    against 47. _term_hits picks the rarest terms off that same index and FTS5's BM25
    reads it for inverse document frequency, so every ranking was scored against a corpus
    that does not exist.

    The methods are NUMBERED and that is load bearing: each one mutates the corpus the
    next one inherits (edit, then delete, then repair), the same shape VaultRetrieval
    above already relies on.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-fts-")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(cls.vault)
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        cls.alpha = os.path.join(cls.vault, "vr1-alpha.md")
        cls.gamma = os.path.join(cls.vault, "vr1-gamma.md")
        for path, text in ((cls.alpha, VR1_ALPHA),
                           (os.path.join(cls.vault, "vr1-beta.md"), VR1_BETA),
                           (cls.gamma, VR1_GAMMA)):
            with open(path, "w") as f:
                f.write(text)
        cls.env = dict(os.environ)
        # HOME moves INDEX_PATH, the projects root and the repo-scope marker, so this
        # suite never reads or writes the machine's real index or the real vault.
        cls.env["HOME"] = cls.tmp
        cls.env["BROTHERMODE_ROOT"] = cls.tmp
        cls.env["BM_FRESHNESS_ROOTS"] = cls.tmp
        cls.env["BM_FRESHNESS_STATE"] = os.path.join(cls.tmp, "freshness_state.sqlite3")
        # cmd_refresh is consent gated (row E54) and reads setup.py's config through
        # this override, so the repair case below reaches the refresh path at all.
        cls.env["BROTHERME_CONFIG"] = os.path.join(cls.tmp, "brotherme-config.json")
        with open(cls.env["BROTHERME_CONFIG"], "w") as f:
            f.write('{"setup_complete": true}')
        cls.index = os.path.join(cls.tmp, ".claude", "bm_vault_index.sqlite3")
        cls.index_code, cls.index_out = run(["index", "--vault", cls.vault], cls.env)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _con(self):
        con = sqlite3.connect(self.index)
        con.row_factory = sqlite3.Row
        return con

    def _fts_and_notes(self, con):
        """The two rowid sets. %_docsize (one row per INDEXED document) is the only
        honest reading of what the text index holds: on an external content table a
        plain `SELECT rowid FROM notes_fts` scans the CONTENT table instead, so it
        would agree with notes no matter how polluted the index is."""
        fts = {r[0] for r in con.execute("SELECT id FROM notes_fts_docsize")}
        notes = {r[0] for r in con.execute("SELECT id FROM notes")}
        return fts, notes

    def _match(self, con, term):
        return sorted(r[0] for r in con.execute(
            "SELECT rowid FROM notes_fts WHERE notes_fts MATCH ?", (term,)))

    def _notes_holding(self, con, word):
        return con.execute("SELECT COUNT(*) c FROM notes WHERE lower(body) LIKE ?",
                           ("%%%s%%" % word,)).fetchone()["c"]

    def test_01_the_corpus_indexed_and_the_two_rowid_sets_agree(self):
        self.assertEqual(self.index_code, 0, self.index_out)
        con = self._con()
        try:
            fts, notes = self._fts_and_notes(con)
            self.assertEqual(len(notes), 3, self.index_out)
            self.assertEqual(fts, notes)
            self.assertEqual(bm_vault._term_hits(con, "commonword"),
                             self._notes_holding(con, "commonword"),
                             "the rare-term ladder counts documents that exist")
        finally:
            con.close()

    def test_02_an_edited_note_keeps_none_of_its_old_terms(self):
        with open(self.alpha, "w") as f:
            f.write(VR1_ALPHA_EDITED)
        # The index skips a note whose mtime has not moved, so move it: this case is
        # about the delete, not about staleness detection.
        stamp = os.path.getmtime(self.alpha) + 2
        os.utime(self.alpha, (stamp, stamp))
        code, out = run(["index", "--vault", self.vault], self.env)
        self.assertEqual(code, 0, out)
        con = self._con()
        try:
            self.assertEqual(self._match(con, "zebrafish"), [],
                             "the removed word still matches, so the old terms stayed "
                             "in the index:\n%s" % out[:400])
            fts, notes = self._fts_and_notes(con)
            self.assertEqual(fts, notes)
            self.assertEqual(bm_vault._term_hits(con, "commonword"),
                             self._notes_holding(con, "commonword"))
        finally:
            con.close()

    def test_03_a_deleted_note_leaves_no_terms_behind(self):
        os.remove(self.gamma)
        code, out = run(["index", "--vault", self.vault], self.env)
        self.assertEqual(code, 0, out)
        con = self._con()
        try:
            self.assertEqual(self._match(con, "quokkaterm"), [],
                             "the gone note still matches:\n%s" % out[:400])
            fts, notes = self._fts_and_notes(con)
            self.assertEqual(len(notes), 2, out)
            self.assertEqual(fts, notes)
            self.assertEqual(bm_vault._term_hits(con, "commonword"),
                             self._notes_holding(con, "commonword"))
        finally:
            con.close()

    def _refresh(self):
        """refresh, with stdin closed: cmd_refresh reads the hook payload off stdin and
        would otherwise block on an inherited terminal."""
        p = subprocess.run([sys.executable, TOOL, "refresh", "--vault", self.vault],
                           env=self.env, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return (p.returncode, p.stdout.decode("utf-8", "replace"),
                p.stderr.decode("utf-8", "replace"))

    def test_04_refresh_repairs_an_index_polluted_before_the_fix(self):
        """The historical state, reproduced: an fts row whose note is gone. No
        incremental delete can remove it (its text is not in notes to subtract with),
        so refresh must rebuild."""
        con = self._con()
        con.execute("INSERT INTO notes_fts (rowid, title, descr, body) VALUES (?,?,?,?)",
                    (9999, "ghost", "ghost note", "phantomterm ghost"))
        con.commit()
        self.assertEqual(self._match(con, "phantomterm"), [9999],
                         "the fixture did not create the orphan it is testing")
        con.close()
        code, out, err = self._refresh()
        self.assertEqual(code, 0, out + err)
        self.assertIn("vault-index: rebuilt the text index, 1 orphan rows removed", err,
                      "refresh said nothing about the orphan:\n%s" % (out + err)[:600])
        con = self._con()
        try:
            self.assertEqual(self._match(con, "phantomterm"), [])
            fts, notes = self._fts_and_notes(con)
            self.assertEqual(fts, notes)
            self.assertEqual(bm_vault._term_hits(con, "commonword"),
                             self._notes_holding(con, "commonword"))
        finally:
            con.close()

    def test_05_a_clean_index_prints_no_repair_line(self):
        code, out, err = self._refresh()
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("rebuilt the text index", out + err)


class VR2SearchTailProtections(unittest.TestCase):
    """VR2, 2026-09-09. The four protections in the search tail, each pinned by
    the shape RR3 measured on the real vault at the hook own --limit 2, where
    recall@2 was 0.60 and every crowded-anchor query landed at rank 3 or worse.

    P1 a link-only candidate can never displace a direct exact-anchor match.
       RR3 bucket g, the night most serious finding: two queries where the note
       carrying the EXACT anchor never reached the top 5, while hits reading
       "authority: source_of_record, linked from a match" took the slots.
    P2 near-duplicate notes collapse by normalized filename stem BEFORE the
       limit cut. RR1 5.5: 87 stems appear more than once on the live vault,
       and at --limit 2 two copies of one note is the entire answer.
    P3 candidates carrying the IDENTICAL fused score are ordered
       deterministically: the context match first, then mtime, then path.
    P4 a relevance floor drops the noise tail before the authority sort, and
       never drops a direct anchor match however low it scores (RR1 5.4).

    One corpus, indexed once. The three CLI fixtures use disjoint vocabulary,
    so one query can never see another fixture notes."""

    @staticmethod
    def _note(name, descr, body, extra=""):
        return ("---\nname: %s\ndescription: %s\ntype: project\n%s---\n%s\n"
                % (name, descr, extra, body))

    @classmethod
    def _write(cls, vault, rel, text):
        path = os.path.join(vault, rel)
        if not os.path.isdir(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            f.write(text)

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-vr2-")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(cls.vault)
        # The cited files exist on disk under the freshness root, so a hit is
        # SERVED rather than withheld as a citation that no longer resolves.
        for fn in ("ZigzagCrowdedWidget.swift", "FloorProbeWidget.swift"):
            with open(os.path.join(cls.tmp, fn), "w") as f:
                f.write("// fixture\n")
        # P1 fixture: nine notes crowding one anchor, every one of them linking
        # to a source_of_record note that does NOT name the anchor itself.
        for i in range(1, 10):
            cls._write(cls.vault, "crowd-%02d.md" % i, cls._note(
                "zigzag-crowd-%02d" % i,
                "crowded note %02d about the zigzag widget" % i,
                "ZigzagCrowdedWidget.swift misbehaved, note %02d. "
                "See [[zigzag-source-ruling]]." % i))
        cls._write(cls.vault, "zigzag-source-ruling.md", cls._note(
            "zigzag-source-ruling", "the standing ruling on zigzag rendering",
            "The standing ruling on zigzag rendering. No widget file is named here.",
            extra="authority: source_of_record\n"))
        # P4 fixture: one strong lesson, one session log that NAMES the anchor
        # (a direct match scoring far below the floor), and one session log that
        # only shares the words (the noise tail the floor exists to drop).
        cls._write(cls.vault, "floor-primary.md", cls._note(
            "floorprobe-primary", "the flumdiddle spinner stalls",
            "FloorProbeWidget.swift stalls the flumdiddle spinner every time."))
        cls._write(cls.vault, "floor-anchor-log.md",
                   "---\ntype: session-log\n---\nNight on FloorProbeWidget.swift. "
                   "We touched flumdiddle and spinner and everything else.\n")
        cls._write(cls.vault, "floor-noise-log.md",
                   "---\ntype: session-log\n---\nA long log about flumdiddle "
                   "spinner work, with no widget file named here at all.\n")
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BROTHERMODE_ROOT"] = cls.tmp
        cls.env["BM_FRESHNESS_ROOTS"] = cls.tmp
        cls.env["BM_FRESHNESS_STATE"] = os.path.join(cls.tmp, "freshness_state.sqlite3")
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        cls.index_code, cls.index_out = run(["index", "--vault", cls.vault], cls.env)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_01_the_corpus_indexed(self):
        self.assertEqual(self.index_code, 0, self.index_out)

    def test_02_a_link_only_note_never_displaces_a_direct_anchor_match(self):
        """P1. Nine notes carry the exact anchor; the source_of_record note
        carries none and arrives only through one-hop link expansion. At the
        hook own --limit 2 both slots must go to notes that matched the query
        itself. Before VR2 the re-sort after expansion ranked the whole list by
        authority tier first, so the linked ruling took the leading slot."""
        code, out = run(["check", "--paths", "ZigzagCrowdedWidget.swift",
                         "--limit", "2"], self.env)
        self.assertEqual(code, 0, out)
        self.assertNotIn("zigzag-source-ruling", out,
                         "a link-only source_of_record note displaced a direct "
                         "exact-anchor match, the RR3 bucket g defect:\n%s" % out)
        self.assertNotIn("linked from a match", out, out)
        shown = [l.strip() for l in out.splitlines() if l.strip().endswith(".md")]
        self.assertEqual(len(shown), 2, out)
        for line in shown:
            self.assertIn("/crowd-", line, out)

    @staticmethod
    def _mem_index(rows, anchor):
        """An in-memory index with the real schema. The CLI cannot answer the
        two questions below on its own: _print_hits runs its own duplicate-slug
        WITHHELD branch over the served list, which suppresses a same-stem twin
        whether or not _search collapsed it, so an assertion on printed output
        would pass for the wrong reason. These cases read what _search RETURNS."""
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        bm_vault._schema(con)
        for nid, path, mtime, body in rows:
            con.execute("INSERT INTO notes (id, path, title, descr, source, kind, "
                        "mtime, body, content_hash) VALUES (?,?,?,?,?,?,?,?,?)",
                        (nid, path, os.path.basename(path), "a note", "vault",
                         "lesson", mtime, body, "hash%d" % nid))
            con.execute("INSERT INTO anchors (note_id, anchor) VALUES (?,?)",
                        (nid, anchor))
        return con

    @contextlib.contextmanager
    def _isolated_decay(self):
        """bm_vault_decay reads its store path from the environment at load time,
        and _search loads the module on every call: without this an in-process
        case would read the founder real vault-decay.json."""
        was = os.environ.get("BM_VAULT_DECAY")
        os.environ["BM_VAULT_DECAY"] = os.path.join(self.tmp, "decay.json")
        try:
            yield
        finally:
            if was is None:
                os.environ.pop("BM_VAULT_DECAY", None)
            else:
                os.environ["BM_VAULT_DECAY"] = was

    def test_03_two_notes_sharing_a_stem_collapse_to_one(self):
        """P2. Two copies of one lesson, filed under two folders with the same
        filename, both matching the anchor exactly. Between them they would take
        two of the served slots, which at the hook own --limit 2 is the whole
        answer. One survives, the differently named note keeps its slot, and the
        collapsed count is reported rather than silently swallowed."""
        con = self._mem_index(
            [(1, "/v/folder-a/twinstem-note.md", 1.0, "TwinStemGadget.swift, first copy."),
             (2, "/v/folder-b/twinstem-note.md", 2.0, "TwinStemGadget.swift, second copy."),
             (3, "/v/twinstem-other.md", 3.0, "TwinStemGadget.swift, a different note.")],
            "TwinStemGadget.swift")
        with self._isolated_decay():
            fused, why, total = bm_vault._search(
                con, paths=["TwinStemGadget.swift"], limit=5, fast=True)
        served = [nid for nid, _ in fused]
        self.assertEqual(len(served), 2,
                         "two copies of one note took two slots: %r" % (served,))
        self.assertIn(3, served, "dedup collapsed a note sharing no stem: %r" % (served,))
        self.assertEqual(len(set(served) & {1, 2}), 1, served)
        self.assertEqual(why.get("__stem_collapsed__"), 1, why)
        self.assertEqual(total, 2,
                         "the returned total still counts the collapsed copy")

    def test_04_a_context_path_breaks_a_tie_between_equal_fused_scores(self):
        """P3. Fused scores tie only when two signals land two notes at the same
        rank with the same weight, so the tie is BUILT here at the fusion seam
        rather than hunted for in a corpus: what is under test is the tail own
        ordering of a tie, not fusion ability to produce one. Both notes are
        real rows and _search runs its real tail over them. The mtimes are equal,
        so with no context the path decides and alpha wins; with the caller
        working inside beta folder, beta wins."""
        con = self._mem_index(
            [(1, "/v/ctxproj_alpha/tiebreak-alpha.md", 1.0, "CtxTwinThing.swift"),
             (2, "/v/ctxproj_beta/tiebreak-beta.md", 1.0, "CtxTwinThing.swift")],
            "CtxTwinThing.swift")
        real_rrf = bm_vault._rrf
        bm_vault._rrf = lambda lists: [(1, 1.0), (2, 1.0)]
        try:
            with self._isolated_decay():
                plain, _w1, _t1 = bm_vault._search(
                    con, paths=["CtxTwinThing.swift"], limit=2, fast=True)
                ctx, _w2, _t2 = bm_vault._search(
                    con, paths=["CtxTwinThing.swift"], limit=2, fast=True,
                    context_path="/repo/ctxproj_beta/inner/thing.py")
        finally:
            bm_vault._rrf = real_rrf
        self.assertEqual([nid for nid, _ in plain], [1, 2],
                         "with no context a tie must fall back to the path, "
                         "deterministically: %r" % (plain,))
        self.assertEqual([nid for nid, _ in ctx][0], 2,
                         "the note in the folder the caller is working in lost a "
                         "tie it should have won: %r" % (ctx,))

    def test_05_the_floor_drops_noise_but_never_a_direct_anchor_match(self):
        """P4. Both session logs score far below the lesson that answers the
        query. One of them NAMES the anchor, which makes it a direct match and
        protected: it survives however low it scores, because it is the file the
        person is holding. The other only shares the words and is dropped."""
        code, out = run(["recall", "--query",
                         "flumdiddle spinner FloorProbeWidget.swift",
                         "--limit", "10", "--fast"], self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("floorprobe-primary", out, out)
        self.assertIn("floor-anchor-log.md", out,
                      "the floor dropped a DIRECT exact-anchor match:\n%s" % out)
        self.assertNotIn("floor-noise-log.md", out,
                         "the noise tail reached the reader; the floor is not "
                         "doing anything:\n%s" % out)


VR3_DECOY = """---
name: an-unrelated-bm-vault-somewhere-else
description: a second file of the same name in a different tree entirely
type: project
---
A quite different tool named bm_vault.py, in somebody else's tree, kept its own
index and failed for its own reasons. Nothing here is about this project.
"""

VR3_TARGET = """---
name: the-bm-vault-in-this-projects-tools-directory
description: the note about THIS directory's copy of the tool
type: project
---
products/brothermode/tools/bm_vault.py resolves the vault from BM_VAULT_ROOT
first, and a session that assumes the config file wins reads the wrong corpus.
"""


class TheContextPathBreaksTiesAmongEqualAnchorMatches(unittest.TestCase):
    """VR3 (plan row VR3, RR1 section 5.7).

    THE DEFECT. `check --paths` matched on the BARE FILE NAME and nothing else,
    so two notes about two different files that happen to share a name were
    indistinguishable, and among notes sharing one crowded anchor nothing
    preferred the one about the directory actually being edited. RR3 measured
    that as the whole of the crowded band: 0 of 5 at rank 1.

    THE FIX UNDER TEST. `check` takes `--context <repo-relative path>` beside
    the unchanged `--paths <basename>`; among candidates the fused score is not
    really separating, the one whose own anchors sit under the same directories
    wins. The `--paths` value is untouched, so every existing caller keeps its
    behaviour, and test_02 below proves the ordering actually flips rather than
    asserting an order the corpus would have produced anyway.

    Two notes, one anchor each: the decoy names the bare `bm_vault.py`, the
    target names `products/brothermode/tools/bm_vault.py` (the ANCHOR regex
    keeps `/`, so a repo-relative path is one anchor). Both match the query,
    because the anchor lookup is `anchor = ? OR anchor LIKE '%/' || ?`."""

    CONTEXT = "products/brothermode/tools/bm_vault.py"

    # ---- VR5x: a CROWDED anchor, which the two-note corpus above cannot express ----
    #
    # SEVEN notes carry one anchor and only one of them names the caller's own
    # directory. Seven, not nine, and the reason is measured rather than chosen:
    # a paths query fuses one list, so candidate r scores 6/(60+r) and the gap from
    # the leader to rank r is (r-1)/(60+r). That is 8.96 percent at rank 7 and 10.29
    # percent at rank 8, so a seven note band sits inside _CONTEXT_TIE_EPS as ONE
    # group and an eight note band does not. Sizing it to the band is what makes
    # this test deterministic: os.walk is not sorted, so which note lands at which
    # rank is a property of the filesystem, and a fixture that only worked when the
    # target landed in the right half of the band would be a coin toss.
    CROWDED_ANCHOR = "crimsonledger.py"
    CROWDED_CONTEXT = "alpha-app/crimson-ledger/crimsonledger.py"
    CROWDED_TARGET = "vr5x-crowded-target"
    # The same crowd with two source_of_record notes added: the case the over-fetch
    # does NOT win, pinned so the limit of this change is on the record rather than
    # implied.
    TIERED_ANCHOR = "tealregistry.py"
    TIERED_CONTEXT = "alpha-app/teal-registry/tealregistry.py"
    TIERED_TARGET = "vr5x-tiered-target"
    CROWD_SIZE = 7
    # VR6: a third crowd where a DECLARED note also names the caller's directory.
    # It is written in the SECOND index pass, so it carries the LOWEST fused score
    # of the crowd while the casual target (first pass) carries a higher one: an
    # implementation that sorted the lifted candidates by score would serve the
    # casual note first and this fixture goes red, which is the whole point of it.
    GOLD_ANCHOR = "goldregistry.py"
    GOLD_CONTEXT = "alpha-app/gold-registry/goldregistry.py"
    GOLD_TARGET = "vr6-gold-target"
    GOLD_DECLARED = "vr6-gold-declared"
    GOLD_DECOYS = 3

    @staticmethod
    def _write_crowd_note(vault, stem, name, anchor, context, authority=None):
        """One note on a crowded anchor. `context` is None for a decoy, which then
        names only the BARE anchor and can never win the tiebreak; the target names
        the full repo-relative path, so its own anchors carry the caller's directory
        segments (the ANCHOR regex keeps `/`, so a path is one anchor)."""
        front = ("---\nname: %s\ndescription: a crowded-anchor fixture note about %s\n"
                 "type: project\n" % (name, anchor))
        if authority:
            front += "authority: %s\n" % authority
        body = "The fixture note %s is a recorded failure about `%s`.\n" % (name, anchor)
        if context:
            body += "The file it is about lives at %s.\n" % context
        with open(os.path.join(vault, "%s.md" % stem), "w") as f:
            f.write(front + "---\n" + body)

    @staticmethod
    def _tiebreak_is_the_cmd_check_fallback():
        """True while `_search` does not take a context_path of its own.

        VR2 is moving this tiebreak INSIDE the ranking; when it lands,
        cmd_check hands the context to _search and its own narrow fallback
        (and the line that fallback prints) is never reached. The ORDER is the
        contract either way and is asserted unconditionally below; only the
        line naming which code did it is conditional, so this suite does not
        go red the day the better implementation arrives."""
        try:
            return "context_path" not in inspect.signature(bm_vault._search).parameters
        except (TypeError, ValueError):
            return False

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-vr3-context-")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(cls.vault)
        # a- and z- so the walk order puts the DECOY in the index first: the
        # ordering this test flips has to start out wrong, or test_03 would
        # pass on a corpus that never needed the fix.
        for fn, text in (("a-decoy.md", VR3_DECOY), ("z-target.md", VR3_TARGET)):
            with open(os.path.join(cls.vault, fn), "w") as f:
                f.write(text)
        # VR5x's two crowds, each under its own anchor, so every assertion above
        # about `bm_vault.py` still sees exactly the corpus it saw before. Only the
        # DECOYS are written here; the two targets go in after the first index pass,
        # for the reason spelled out at that second pass below.
        for i in range(1, cls.CROWD_SIZE):
            cls._write_crowd_note(cls.vault, "c%d" % i, "c-crowd-%d" % i,
                                  cls.CROWDED_ANCHOR, None)
            cls._write_crowd_note(cls.vault, "t%d" % i, "t-crowd-%d" % i,
                                  cls.TIERED_ANCHOR, None,
                                  authority="source_of_record" if i in (1, 2) else None)
        for i in range(1, cls.GOLD_DECOYS + 1):
            cls._write_crowd_note(cls.vault, "g%d" % i, "g-crowd-%d" % i,
                                  cls.GOLD_ANCHOR, None)
        cls._write_crowd_note(cls.vault, "g-target", cls.GOLD_TARGET,
                              cls.GOLD_ANCHOR, cls.GOLD_CONTEXT)
        # A stub code root so both notes' citations resolve and both are SERVED
        # rather than withheld as stale: this suite is about ranking, and a
        # note withheld for want of a file that was never going to exist here
        # would be testing freshness instead (same rationale as VaultRetrieval's
        # own AtriumRoomViews.swift stub above).
        cls.code_root = os.path.join(cls.tmp, "code")
        deep = os.path.join(cls.code_root, "products", "brothermode", "tools")
        os.makedirs(deep)
        for p in (os.path.join(cls.code_root, "bm_vault.py"),
                  os.path.join(deep, "bm_vault.py")):
            with open(p, "w") as f:
                f.write("# stub for the VR3 context-ranking test\n")
        # The same stubs for VR5x's crowds, bare anchor at the root and full path
        # below, so no crowd note is WITHHELD as stale and these cases measure
        # ranking rather than freshness.
        for ctx in (cls.CROWDED_CONTEXT, cls.TIERED_CONTEXT, cls.GOLD_CONTEXT):
            full = os.path.join(cls.code_root, *ctx.split("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            for p in (os.path.join(cls.code_root, os.path.basename(ctx)), full):
                with open(p, "w") as f:
                    f.write("# stub for the VR5x crowded-anchor test\n")
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BROTHERMODE_ROOT"] = cls.tmp
        cls.env["BM_FRESHNESS_ROOTS"] = cls.code_root
        cls.env["BM_FRESHNESS_STATE"] = os.path.join(cls.tmp, "freshness_state.sqlite3")
        cls.env["BM_VAULT_DECAY"] = os.path.join(cls.tmp, "vault-decay.json")
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        cls.index_code, cls.index_out = run(["index", "--vault", cls.vault], cls.env)
        # THE SECOND PASS, and the reason it exists. A --paths query fuses ONE list
        # and ranks it in the order the anchors table returns, which is insertion
        # order, which is os.walk order, which is NOT SORTED: which crowd note lands
        # at which rank is otherwise a property of the filesystem, and a fixture
        # whose target might start INSIDE the caller's --limit could not prove that
        # the over-fetch is what reaches it. Indexing the decoys first and the two
        # TARGETS second makes the targets the last rows in the anchors table, so
        # they start last in their band on any filesystem. The index is incremental
        # (an unchanged mtime is skipped), so this pass adds two notes and re-reads
        # nothing.
        cls._write_crowd_note(cls.vault, "c%d" % cls.CROWD_SIZE, cls.CROWDED_TARGET,
                              cls.CROWDED_ANCHOR, cls.CROWDED_CONTEXT)
        cls._write_crowd_note(cls.vault, "t%d" % cls.CROWD_SIZE, cls.TIERED_TARGET,
                              cls.TIERED_ANCHOR, cls.TIERED_CONTEXT)
        cls._write_crowd_note(cls.vault, "g-declared", cls.GOLD_DECLARED,
                              cls.GOLD_ANCHOR, cls.GOLD_CONTEXT,
                              authority="source_of_record")
        cls.index2_code, cls.index2_out = run(["index", "--vault", cls.vault], cls.env)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _order(self, out):
        """(first, second) note name in the order they were printed."""
        at_decoy = out.find("an-unrelated-bm-vault-somewhere-else")
        at_target = out.find("the-bm-vault-in-this-projects-tools-directory")
        self.assertNotEqual(at_decoy, -1, "the decoy never matched at all:\n%s" % out)
        self.assertNotEqual(at_target, -1, "the target never matched at all:\n%s" % out)
        return ("decoy", "target") if at_decoy < at_target else ("target", "decoy")

    def test_01_the_corpus_indexed(self):
        self.assertEqual(self.index_code, 0, self.index_out)
        self.assertEqual(self.index2_code, 0, self.index2_out)

    def test_02_without_context_the_wrong_note_leads(self):
        """The defect itself, pinned: this is the state the fix has to change,
        and it is also what stops test_03 from being a tautology."""
        code, out = run(["check", "--paths", "bm_vault.py", "--limit", "2"], self.env)
        self.assertEqual(code, 0, out)
        self.assertEqual(self._order(out)[0], "decoy",
                         "the corpus already led with the right note, so "
                         "test_03 would prove nothing:\n%s" % out)

    def test_03_with_context_the_note_about_this_directory_leads(self):
        code, out = run(["check", "--paths", "bm_vault.py", "--context", self.CONTEXT,
                         "--limit", "2"], self.env)
        self.assertEqual(code, 0, out)
        self.assertEqual(self._order(out)[0], "target",
                         "--context did not lift the note about this "
                         "directory:\n%s" % out)
        if self._tiebreak_is_the_cmd_check_fallback():
            self.assertIn("context tiebreak: 1 group(s) reordered", out, out)

    def test_04_both_notes_are_still_served_the_tiebreak_only_reorders(self):
        """The constitution's line at this seam, the same one decay is held
        to: a tiebreak reorders and never removes."""
        code, out = run(["check", "--paths", "bm_vault.py", "--context", self.CONTEXT,
                         "--limit", "2"], self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("an-unrelated-bm-vault-somewhere-else", out, out)
        self.assertIn("the-bm-vault-in-this-projects-tools-directory", out, out)

    def test_05_a_context_no_candidate_sits_under_says_no_data(self):
        """An honest nothing, not a silent pass: when no candidate names a path
        under the context, the tiebreak says so and the order is unchanged."""
        code, out = run(["check", "--paths", "bm_vault.py", "--context",
                         "somewhere/else/entirely/bm_vault.py", "--limit", "2"], self.env)
        self.assertEqual(code, 0, out)
        if self._tiebreak_is_the_cmd_check_fallback():
            self.assertIn("NO-DATA context tiebreak:", out, out)
        self.assertEqual(self._order(out)[0], "decoy", out)

    def test_06_a_context_with_no_directory_says_no_data(self):
        code, out = run(["check", "--paths", "bm_vault.py", "--context", "bm_vault.py",
                         "--limit", "2"], self.env)
        self.assertEqual(code, 0, out)
        if self._tiebreak_is_the_cmd_check_fallback():
            self.assertIn("NO-DATA context tiebreak: --context carries no "
                          "directory segment", out, out)
        self.assertEqual(self._order(out)[0], "decoy", out)

    # ---- VR5x: the tiebreak cannot lift a note it was never handed ----

    def _titles(self, out):
        """The served note names, in printed order. _print_hits indents a title by
        exactly two spaces and its detail lines by four, and appends the note's kind
        after two spaces as "  [lesson, vault]", which is trimmed off here."""
        out_titles = []
        for ln in out.splitlines():
            if not ln.startswith("  ") or ln.startswith("    ") or not ln.strip():
                continue
            out_titles.append(ln.strip().rsplit("  [", 1)[0].strip())
        return out_titles

    def test_07_the_crowd_without_a_context_says_nothing_about_this_directory(self):
        """The state to overturn. Seven notes carry one anchor, the caller gets two
        of them, and with no context nothing prefers the one about the directory
        being edited: whichever two the fusion happened to rank first are served."""
        code, out = run(["check", "--paths", self.CROWDED_ANCHOR, "--limit", "2"],
                        self.env)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self._titles(out)), 2, out)
        self.assertNotIn("context tiebreak", out, out)

    def test_08_a_crowded_anchor_leads_with_the_note_about_this_directory(self):
        """DEFECT 1, the whole of it. cmd_check used to hand _search the CALLER'S
        --limit, so at the point of need hook's production limit of 2 the tiebreak
        was handed two notes about something else and printed
        "NO-DATA context tiebreak: no candidate names a path under ...", while the
        note naming this directory sat further down the same band and was never a
        candidate for the reorder at all. With CONTEXT_OVERFETCH the tiebreak is
        handed the whole band, reorders it, and the cut to --limit happens after.

        THE MUTATION: set CONTEXT_OVERFETCH to 1, or pass `limit` straight through
        to _search again, and this goes red, because the target is no longer among
        the candidates the tiebreak sees. It does not depend on WHICH rank the
        target starts at (os.walk is not sorted), only on the band being one group,
        which is what CROWD_SIZE is sized for."""
        code, out = run(["check", "--paths", self.CROWDED_ANCHOR,
                         "--context", self.CROWDED_CONTEXT, "--limit", "2"], self.env)
        self.assertEqual(code, 0, out)
        titles = self._titles(out)
        self.assertIn(self.CROWDED_TARGET, titles,
                      "--context did not reach past --limit into the crowd:\n%s" % out)
        self.assertEqual(titles[0], self.CROWDED_TARGET,
                         "the note about this directory was served, but not first:"
                         "\n%s" % out)

    def test_09_the_over_fetch_is_invisible_to_the_caller(self):
        """It asks _search for more and still prints only what was asked for, and
        the count of what was not shown still counts the whole band. A change that
        quietly widened the answer would be a different change from this one."""
        code, out = run(["check", "--paths", self.CROWDED_ANCHOR,
                         "--context", self.CROWDED_CONTEXT, "--limit", "2"], self.env)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self._titles(out)), 2, out)
        self.assertIn("Vault: %d more lesson(s) matched" % (self.CROWD_SIZE - 2),
                      out, out)

    def _env_mode(self, mode):
        """VR6: this suite's environment with one order mode pinned. Pinned rather
        than inherited, so a case says which order it is asserting and neither
        setting of the committed default can make it pass by accident."""
        env = dict(self.env)
        env["BM_VAULT_ORDER_MODE"] = mode
        return env

    def test_10_the_authority_plane_is_still_not_crossed(self):
        """The measured LIMIT of the VR5x change, pinned rather than left implied,
        and since VR6 the "authority-first" half of that experiment (case b).

        Same crowd plus two source_of_record notes on the same anchor. The
        over-fetch makes the tiebreak SEE the note about this directory (it stops
        saying no candidate names a path under the context), but under
        "authority-first" the two declared notes still lead, because authority
        outranks similarity lexicographically and this tiebreak only ever reorders
        inside one level. That is why the retrieval benchmark's crowded band stays
        at zero at --limit 2 in that mode: what blocks it there is the authority
        plane, not the cut VR5x fixed. test_11 runs the same query under
        "situation-first" and gets the other answer."""
        code, out = run(["check", "--paths", self.TIERED_ANCHOR,
                         "--context", self.TIERED_CONTEXT, "--limit", "2"],
                        self._env_mode("authority-first"))
        self.assertEqual(code, 0, out)
        self.assertNotIn("NO-DATA context tiebreak: no candidate names a path under",
                         out, out)
        self.assertEqual(sorted(self._titles(out)), ["t-crowd-1", "t-crowd-2"], out)

    # ---- VR6: the served order, measured both ways ----

    def test_11_situation_first_serves_the_note_about_this_directory(self):
        """VR6 case (a). The SAME crowd and the SAME query as test_10, under
        "situation-first": the casual note that is the recorded lesson for the
        caller's own directory is served FIRST, ahead of two source_of_record
        notes about the same file name that name no path under it.

        This is the one plane the change crosses. Everything else holds: both
        modes serve two notes, each still prints its own authority label, and the
        verdict machinery is untouched.

        THE MUTATION: make cmd_check ignore _order_mode() (or drop the
        _situation_first call) and this goes red while test_10 stays green."""
        code, out = run(["check", "--paths", self.TIERED_ANCHOR,
                         "--context", self.TIERED_CONTEXT, "--limit", "2"],
                        self._env_mode("situation-first"))
        self.assertEqual(code, 0, out)
        titles = self._titles(out)
        self.assertEqual(titles[0], self.TIERED_TARGET,
                         "situation-first did not serve the note about this "
                         "directory first:\n%s" % out)
        self.assertIn("authority: source_of_record", out,
                      "the served notes lost their authority labels:\n%s" % out)

    def test_12_situation_first_without_a_context_changes_nothing(self):
        """VR6 case (c). The rule is inert without a situation: with no --context
        the two modes return the identical order, so no caller that sends no
        context can be affected by which default is committed."""
        argv = ["check", "--paths", self.TIERED_ANCHOR, "--limit", "2"]
        code_a, out_a = run(argv, self._env_mode("authority-first"))
        code_s, out_s = run(argv, self._env_mode("situation-first"))
        self.assertEqual(code_a, 0, out_a)
        self.assertEqual(code_s, 0, out_s)
        self.assertEqual(self._titles(out_a), self._titles(out_s),
                         "the order moved with no --context to move it:\n%s\n%s"
                         % (out_a, out_s))
        self.assertNotIn("situation-first:", out_s, out_s)

    def test_13_a_declared_note_about_this_directory_still_leads(self):
        """VR6 case (d), the limit of the crossing. When the higher-authority note
        ALSO names the caller's directory, authority still decides between the two:
        situation-first only lifts past a higher tier that is about somewhere else.

        The declared note is indexed LAST and so carries the LOWEST fused score of
        this crowd, while the casual target carries a higher one. An implementation
        that re-sorted the lifted candidates by score would serve the casual note
        first, and this case is what refuses that."""
        code, out = run(["check", "--paths", self.GOLD_ANCHOR,
                         "--context", self.GOLD_CONTEXT, "--limit", "2"],
                        self._env_mode("situation-first"))
        self.assertEqual(code, 0, out)
        self.assertEqual(self._titles(out), [self.GOLD_DECLARED, self.GOLD_TARGET],
                         "the declared note about this directory stopped leading "
                         "the casual one about it:\n%s" % out)


def _vr4_note(name, project, anchor, words):
    """One fixture note. project=None writes NO project: line at all, which is the
    case the no-filter test turns on."""
    front = "---\nname: %s\ndescription: the %s note about %s\ntype: project\n" % (
        name, name, anchor)
    if project is not None:
        front += "project: %s\n" % project
    return front + "---\n%s A distilled lesson about `%s`.\n" % (words, anchor)


class VR4ProjectScoping(unittest.TestCase):
    """VR4, 2026-09-09. RR1 5.3: ~845 vault notes carry a clean `project:` value and
    `_search` never read one, so a session under one project competed on equal terms
    with every other project's notes about the same file name.

    The fix is a `project` column populated at index time and ONE multiplier
    (PROJECT_BOOST) applied to a matching candidate's fused score, after fusion and
    before the relevance floor. A MULTIPLIER, NEVER A FILTER, per RR2 table item 3: a
    note carrying no project is unboosted, never penalised and never removed, which is
    the whole difference between scoping and hiding. test_04 is that case, and it is the
    test that goes red the moment anyone turns the boost into a WHERE clause.

    The methods are NUMBERED, the same load-bearing reason as the sibling suites above:
    test_08 mutilates the index (it removes the column to reproduce a pre-VR4 index) and
    every other case must have run first.
    """

    ANCHOR = "widgetgizmo.py"
    LONE = "lonelysprocket.py"
    ALPHA = "vr4-alpha-note"
    BETA = "vr4-beta-note"
    GAMMA = "vr4-gamma-note"
    ORPHAN = "vr4-orphan-note"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-vr4-")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(cls.vault)
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        # a-, b-, c- so the walk order (and so the RRF rank order the anchor pass
        # produces) puts alpha first: the beta case below therefore has to OVERTURN
        # the unscoped order, not merely agree with it.
        for fn, name, project in (("a.md", cls.ALPHA, "alpha-app"),
                                  ("b.md", cls.BETA, "beta-cli"),
                                  ("c.md", cls.GAMMA, "gamma-site")):
            with open(os.path.join(cls.vault, fn), "w") as f:
                f.write(_vr4_note(name, project, cls.ANCHOR, "quokkaphrase"))
        with open(os.path.join(cls.vault, "d.md"), "w") as f:
            f.write(_vr4_note(cls.ORPHAN, None, cls.LONE, "narwhalphrase"))
        # A stub code root so every citation resolves and no note is WITHHELD as stale:
        # same rationale as VR3ContextRanking above, this suite is about ranking.
        cls.code_root = os.path.join(cls.tmp, "code")
        os.makedirs(cls.code_root)
        for stub in (cls.ANCHOR, cls.LONE):
            with open(os.path.join(cls.code_root, stub), "w") as f:
                f.write("# stub for the VR4 project-scoping test\n")
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BROTHERMODE_ROOT"] = cls.tmp
        cls.env["BM_FRESHNESS_ROOTS"] = cls.code_root
        cls.env["BM_FRESHNESS_STATE"] = os.path.join(cls.tmp, "freshness_state.sqlite3")
        cls.env["BM_VAULT_DECAY"] = os.path.join(cls.tmp, "vault-decay.json")
        cls.env["BROTHERME_CONFIG"] = os.path.join(cls.tmp, "brotherme-config.json")
        with open(cls.env["BROTHERME_CONFIG"], "w") as f:
            f.write('{"setup_complete": true}')
        cls.index = os.path.join(cls.tmp, ".claude", "bm_vault_index.sqlite3")
        cls.index_code, cls.index_out = run(["index", "--vault", cls.vault], cls.env)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _con(self):
        con = sqlite3.connect(self.index)
        con.row_factory = sqlite3.Row
        return con

    def _served(self, out):
        """The fixture note names present in the output, in the order printed."""
        seen = [(out.find(n), n) for n in
                (self.ALPHA, self.BETA, self.GAMMA, self.ORPHAN)]
        return [n for at, n in sorted(seen) if at != -1]

    def test_01_the_corpus_indexed_and_every_project_landed_in_the_column(self):
        self.assertEqual(self.index_code, 0, self.index_out)
        con = self._con()
        try:
            got = {os.path.basename(r["path"]): r["project"]
                   for r in con.execute("SELECT path, project FROM notes")}
        finally:
            con.close()
        self.assertEqual(got, {"a.md": "alpha-app", "b.md": "beta-cli",
                               "c.md": "gamma-site", "d.md": ""}, self.index_out)

    def test_02_without_a_project_the_beta_note_leads_this_anchor(self):
        """The unscoped order, pinned: three notes carry this anchor and BETA wins the
        band on the fused score alone. That is the state test_03 and test_05 have to
        OVERTURN, and it is what stops either of them from being a tautology."""
        code, out = run(["check", "--paths", self.ANCHOR, "--limit", "1"], self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("NO-DATA project boost: no project resolved", out, out)
        self.assertEqual(self._served(out), [self.BETA], out)

    def test_03_the_alpha_project_gets_the_alpha_note(self):
        """Same anchor, same limit, a project named: the answer flips off the note the
        unscoped order led with. This is the case PROJECT_BOOST = 1.0 kills."""
        code, out = run(["check", "--paths", self.ANCHOR, "--project", "alpha-app",
                         "--limit", "1"], self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("project boost: alpha-app", out, out)
        self.assertEqual(self._served(out), [self.ALPHA], out)

    def test_04_the_beta_project_gets_the_beta_note_from_the_same_anchor(self):
        code, out = run(["check", "--paths", self.ANCHOR, "--project", "beta-cli",
                         "--limit", "1"], self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("project boost: beta-cli", out, out)
        self.assertEqual(self._served(out), [self.BETA], out)

    def test_05_the_gamma_project_gets_the_gamma_note_from_the_same_anchor(self):
        """The second flip, from the note the unscoped order ranked LAST of the three:
        the boost is reading the project, not merely reordering two neighbours."""
        code, out = run(["check", "--paths", self.ANCHOR, "--project", "gamma-site",
                         "--limit", "1"], self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("project boost: gamma-site", out, out)
        self.assertEqual(self._served(out), [self.GAMMA], out)

    def test_06_a_note_with_no_project_is_still_served_under_a_project(self):
        """A multiplier, never a filter (RR2 table item 3). The orphan note carries no
        `project:` at all and is the only match for its anchor: a filter would return
        nothing here, which is memory silently hidden rather than scoped."""
        code, out = run(["check", "--paths", self.LONE, "--project", "alpha-app",
                         "--limit", "2"], self.env)
        self.assertEqual(code, 0, out)
        self.assertEqual(self._served(out), [self.ORPHAN], out)

    def test_07_the_default_keyword_changes_nothing_for_an_existing_caller(self):
        """_search's new keyword is inert at its default: the three-note anchor band
        comes back in the same order whether the caller passes project=None or, like
        every caller that predates VR4, passes nothing at all."""
        con = self._con()
        try:
            plain = [nid for nid, _ in
                     bm_vault._search(con, paths=[self.ANCHOR], limit=3, fast=True)[0]]
            explicit = [nid for nid, _ in
                        bm_vault._search(con, paths=[self.ANCHOR], limit=3, fast=True,
                                         project=None)[0]]
            names = {r["id"]: r["title"] for r in
                     con.execute("SELECT id, title FROM notes")}
        finally:
            con.close()
        self.assertEqual(len(plain), 3, plain)
        self.assertEqual(plain, explicit)
        self.assertEqual([names[i] for i in plain],
                         [self.BETA, self.GAMMA, self.ALPHA])

    def test_08_an_index_built_before_the_column_is_migrated_without_losing_a_row(self):
        """The deployed case: an index written before VR4 has no `project` column at
        all. It gains one on the next refresh, BACKFILLED from the bodies the index
        already holds, so no note is re-read, no note is dropped, and no rebuild is
        needed. Reproduced by rebuilding the notes table without the column, which is
        exactly the shape an older bm_vault.py created."""
        con = self._con()
        try:
            con.executescript("""
                CREATE TABLE notes_pre_vr4 (
                    id INTEGER PRIMARY KEY, path TEXT UNIQUE, title TEXT, descr TEXT,
                    source TEXT, kind TEXT, mtime REAL, body TEXT, content_hash TEXT);
                INSERT INTO notes_pre_vr4
                    SELECT id,path,title,descr,source,kind,mtime,body,content_hash
                    FROM notes;
                DROP TABLE notes;
                ALTER TABLE notes_pre_vr4 RENAME TO notes;
            """)
            con.commit()
            cols = {r[1] for r in con.execute("PRAGMA table_info(notes)")}
            self.assertNotIn("project", cols,
                             "the fixture did not remove the column it is testing")
            before = con.execute("SELECT COUNT(*) c FROM notes").fetchone()["c"]
        finally:
            con.close()
        # refresh, stdin closed: cmd_refresh reads the hook payload off stdin and would
        # otherwise block on an inherited terminal. Every note's mtime is unchanged, so
        # nothing is re-indexed and the values below can only come from the backfill.
        p = subprocess.run([sys.executable, TOOL, "refresh", "--vault", self.vault],
                           env=self.env, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out = (p.stdout + p.stderr).decode("utf-8", "replace")
        self.assertEqual(p.returncode, 0, out)
        con = self._con()
        try:
            self.assertIn("project",
                          {r[1] for r in con.execute("PRAGMA table_info(notes)")}, out)
            self.assertEqual(con.execute("SELECT COUNT(*) c FROM notes").fetchone()["c"],
                             before, "the migration lost rows:\n%s" % out)
            got = {os.path.basename(r["path"]): r["project"]
                   for r in con.execute("SELECT path, project FROM notes")}
        finally:
            con.close()
        self.assertEqual(got, {"a.md": "alpha-app", "b.md": "beta-cli",
                               "c.md": "gamma-site", "d.md": ""}, out)

    def test_09_the_project_falls_back_to_the_git_root_basename_of_the_context(self):
        """How a hook that only knows the edited file resolves a project: the basename
        of the git root above --context, lowercased. --project still wins outright, and
        neither present means NO project rather than a guess off the home directory:
        a guess that lands on the wrong project boosts the wrong notes silently."""
        root = os.path.join(self.tmp, "Some-Repo")
        os.makedirs(os.path.join(root, ".git"))
        os.makedirs(os.path.join(root, "tools"))
        ctx = os.path.join(root, "tools", self.ANCHOR)
        self.assertEqual(bm_vault._resolve_project({"context": ctx}), "some-repo")
        self.assertEqual(
            bm_vault._resolve_project({"context": ctx, "project": "Alpha-App"}),
            "alpha-app")
        self.assertIsNone(bm_vault._resolve_project({"paths": [self.ANCHOR]}))
        self.assertIsNone(
            bm_vault._resolve_project({"context": os.path.join(self.tmp, "nope.py")}),
            "a context with no git root above it must resolve to no project")


class HP2DeclaredLessonIdCollision(unittest.TestCase):
    """VN-HP2 (night run 2026-09-08, held-out pack family F): two notes
    DECLARING the same lesson_id with different bodies are duplicates for
    _make_duplicate_probe whatever folder they sit in, so evidence_tier
    refuses BOTH sides (constitution 6: an unresolved contradiction
    applies neither). Before this the probe compared filename stems only
    and exempted same-directory pairs, so a declared-id collision inside
    one harvest folder served both opposite claims.
    """

    def _con(self, rows):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, path TEXT UNIQUE, "
                    "title TEXT, descr TEXT, source TEXT, kind TEXT, mtime REAL, body TEXT)")
        for path, body in rows:
            con.execute("INSERT INTO notes (path, title, body) VALUES (?, ?, ?)",
                        (path, os.path.basename(path), body))
        return con

    @staticmethod
    def _note(lines, body_text):
        return "---\n" + "\n".join(lines) + "\n---\n\n" + body_text + "\n"

    def test_same_declared_id_different_bodies_in_one_folder_is_a_duplicate(self):
        a = "/v/20-Harvest/timeout-seconds.md"
        b = "/v/20-Harvest/timeout-millis.md"
        con = self._con([
            (a, self._note(["lesson_id: shared-timeout-unit"], "the timeout is in seconds")),
            (b, self._note(["lesson_id: shared-timeout-unit"], "the timeout is in milliseconds")),
        ])
        probe = bm_vault._make_duplicate_probe(con)
        self.assertEqual(probe({"path": a}), b)
        self.assertEqual(probe({"path": b}), a)

    def test_a_correction_declaring_the_same_id_is_not_refused(self):
        a = "/v/20-Harvest/timeout-seconds.md"
        b = "/v/20-Harvest/timeout-corrected.md"
        con = self._con([
            (a, self._note(["lesson_id: shared-timeout-unit"], "the timeout is in seconds")),
            (b, self._note(["lesson_id: shared-timeout-unit", "type: correction"],
                           "the timeout is in milliseconds")),
        ])
        probe = bm_vault._make_duplicate_probe(con)
        self.assertIsNone(probe({"path": b}), "a correction may re-use the id it corrects")
        self.assertIsNone(probe({"path": a}), "and the note it corrects is not refused either")

    def test_a_supersedes_field_exempts_the_same_way(self):
        a = "/v/20-Harvest/timeout-seconds.md"
        b = "/v/20-Harvest/timeout-new.md"
        con = self._con([
            (a, self._note(["lesson_id: shared-timeout-unit"], "the timeout is in seconds")),
            (b, self._note(["lesson_id: shared-timeout-unit", "supersedes: [[timeout-seconds]]"],
                           "the timeout is in milliseconds")),
        ])
        probe = bm_vault._make_duplicate_probe(con)
        self.assertIsNone(probe({"path": b}))
        self.assertIsNone(probe({"path": a}))

    def test_a_stem_derived_id_is_never_compared_only_a_declared_one(self):
        # Neither note declares lesson_id, and the stems differ: nothing
        # to collide on, so this pair stays servable exactly as before.
        a = "/v/20-Harvest/timeout-seconds.md"
        b = "/v/20-Harvest/timeout-millis.md"
        con = self._con([
            (a, self._note(["status: verified"], "the timeout is in seconds")),
            (b, self._note(["status: verified"], "the timeout is in milliseconds")),
        ])
        probe = bm_vault._make_duplicate_probe(con)
        self.assertIsNone(probe({"path": a}))

    def test_identical_bodies_under_one_declared_id_are_a_copy_not_a_conflict(self):
        a = "/v/20-Harvest/timeout-a.md"
        b = "/v/30-Distilled/timeout-b.md"
        same = self._note(["lesson_id: shared-timeout-unit"], "the timeout is in seconds")
        con = self._con([(a, same), (b, same)])
        probe = bm_vault._make_duplicate_probe(con)
        self.assertIsNone(probe({"path": a}))

    def test_the_filename_stem_rule_still_holds_for_notes_with_no_declared_id(self):
        a = "/v/20-Harvest/lesson-one.md"
        b = "/v/30-Distilled/lesson-one.md"
        con = self._con([
            (a, self._note(["status: verified"], "one claim")),
            (b, self._note(["status: verified"], "the opposite claim")),
        ])
        probe = bm_vault._make_duplicate_probe(con)
        self.assertEqual(probe({"path": a}), b)



class VR7DeclaredProjectSlug(unittest.TestCase):
    """VR7, night run 2026-09-09 (NIGHT-RESULT section G). `_resolve_project` read the
    project off the git root's BASENAME, and vault notes declare slugs such as
    `tonari-app` while the checkout on disk is named TonariSimple, so the VR4 boost
    fired only where the directory name and the declared slug happened to coincide.
    The fix reads the slug that git root's own PROJECT.md DECLARES (the estate law puts
    one at every project root), and keeps the basename as the fallback.

    The methods are NUMBERED for the reason the sibling suites are: test_00 pins the
    UNSCOPED order that the end-to-end case in test_06 has to overturn, and without it
    that case could pass while reading nothing at all.
    """

    ANCHOR = "vr7widget.py"
    TONARI = "vr7-tonari-note"
    OTHER = "vr7-other-note"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-vr7-")
        cls.vault = os.path.join(cls.tmp, "vault")
        cls.roots = os.path.join(cls.tmp, "roots")
        os.makedirs(cls.vault)
        os.makedirs(cls.roots)
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        for fn, name, project in (("a.md", cls.TONARI, "tonari-app"),
                                  ("b.md", cls.OTHER, "other-cli")):
            with open(os.path.join(cls.vault, fn), "w") as f:
                f.write(_vr4_note(name, project, cls.ANCHOR, "quokkaphrase"))
        # A stub code root so both citations resolve and neither note is WITHHELD as
        # stale: same rationale as VR4ProjectScoping, this suite is about scoping.
        cls.code_root = os.path.join(cls.tmp, "code")
        os.makedirs(cls.code_root)
        with open(os.path.join(cls.code_root, cls.ANCHOR), "w") as f:
            f.write("# stub for the VR7 declared-slug test\n")
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BROTHERMODE_ROOT"] = cls.tmp
        cls.env["BM_FRESHNESS_ROOTS"] = cls.code_root
        cls.env["BM_FRESHNESS_STATE"] = os.path.join(cls.tmp, "freshness_state.sqlite3")
        cls.env["BM_VAULT_DECAY"] = os.path.join(cls.tmp, "vault-decay.json")
        cls.env["BROTHERME_CONFIG"] = os.path.join(cls.tmp, "brotherme-config.json")
        with open(cls.env["BROTHERME_CONFIG"], "w") as f:
            f.write('{"setup_complete": true}')
        cls.index_code, cls.index_out = run(["index", "--vault", cls.vault], cls.env)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _served(self, out):
        """The fixture note names present in the output, in the order printed."""
        seen = [(out.find(n), n) for n in (self.TONARI, self.OTHER)]
        return [n for at, n in sorted(seen) if at != -1]

    def _root(self, name, project_md=None, git_as_file=False):
        """A throwaway git root named `name`, and the path of a file inside it to hand
        to --context. `.git` is written as a DIRECTORY or as a FILE (the shape a git
        worktree checkout has), because _resolve_project walks on os.path.exists and
        must treat the two the same."""
        root = os.path.join(self.roots, name)
        os.makedirs(os.path.join(root, "tools"))
        if git_as_file:
            with open(os.path.join(root, ".git"), "w") as f:
                f.write("gitdir: /elsewhere/.git/worktrees/%s\n" % name)
        else:
            os.makedirs(os.path.join(root, ".git"))
        if project_md is not None:
            with open(os.path.join(root, "PROJECT.md"), "w") as f:
                f.write(project_md)
        return os.path.join(root, "tools", self.ANCHOR)

    def test_00_the_corpus_indexed_and_the_unscoped_order_is_pinned(self):
        """Both notes carry the same anchor and the OTHER note leads the band on the
        fused score alone. That is the state test_06 overturns, and pinning it here is
        what stops test_06 from being a tautology."""
        self.assertEqual(self.index_code, 0, self.index_out)
        code, out = run(["check", "--paths", self.ANCHOR, "--limit", "1"], self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("NO-DATA project boost: no project resolved", out, out)
        self.assertEqual(self._served(out), [self.OTHER], out)

    def test_01_a_declared_slug_beats_the_directory_name(self):
        """(a) The whole gap: the checkout is TonariSimple, the notes say tonari-app."""
        ctx = self._root("TonariSimple",
                         "---\nname: Tonari\nproject: tonari-app\n---\n\nThe app.\n")
        self.assertEqual(bm_vault._resolve_project({"context": ctx}), "tonari-app")

    def test_01b_a_declaration_outside_a_frontmatter_block_is_read_too(self):
        """A PROJECT.md that opens with prose rather than `---` still declares: the
        first 40 lines are read, which is where a header of any shape puts it."""
        ctx = self._root("Some-Checkout",
                         "# Some Checkout\n\nproject: some-app\n\nCanonical path: ...\n")
        self.assertEqual(bm_vault._resolve_project({"context": ctx}), "some-app")

    def test_02_no_project_md_falls_back_to_the_basename(self):
        """(b) Unchanged behaviour where nothing declares anything, both when `.git` is
        a directory and when it is the FILE a worktree checkout carries."""
        self.assertEqual(
            bm_vault._resolve_project({"context": self._root("Plain-Repo")}),
            "plain-repo")
        self.assertEqual(
            bm_vault._resolve_project(
                {"context": self._root("Worktree-Repo", git_as_file=True)}),
            "worktree-repo")

    def test_03_a_project_md_without_a_project_line_falls_back_to_the_basename(self):
        """(c) A PROJECT.md is not itself a declaration; only a `project:` line is. A
        `project:` in the PROSE, below the frontmatter block, is not one either."""
        ctx = self._root("Quiet-Repo",
                         "---\nname: Quiet\nurl: https://example.invalid\n---\n\n"
                         "project: mentioned-in-prose\n")
        self.assertEqual(bm_vault._resolve_project({"context": ctx}), "quiet-repo")

    def test_04_two_different_declarations_resolve_to_no_project(self):
        """(d) An ambiguity resolves to NO project, never to the first value seen and
        never to the basename: either would pick a side silently. The same value twice
        is not an ambiguity, so it still resolves."""
        ctx = self._root("Two-Voices",
                         "---\nproject: tonari-app\nproject: other-cli\n---\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = bm_vault._resolve_project({"context": ctx})
        self.assertIsNone(got)
        line = err.getvalue()
        self.assertIn("NO-DATA project", line)
        self.assertIn("tonari-app and other-cli", line)
        self.assertIn("PROJECT.md", line)
        same = self._root("One-Voice-Twice",
                          "---\nproject: tonari-app\nproject: Tonari-App\n---\n")
        self.assertEqual(bm_vault._resolve_project({"context": same}), "tonari-app")

    def test_05_an_explicit_project_wins_over_both(self):
        """(e) --project still wins outright, over a declaration and over a basename."""
        ctx = self._root("Overridden",
                         "---\nproject: declared-app\n---\n")
        self.assertEqual(
            bm_vault._resolve_project({"context": ctx, "project": "Asked-For"}),
            "asked-for")

    def test_06_the_declared_slug_scopes_a_real_check(self):
        """End to end, through the command a hook actually runs: two notes share the
        anchor, the context sits under a TonariSimple root whose PROJECT.md declares
        tonari-app, and the answer flips off the note test_00 pinned as the unscoped
        winner."""
        ctx = self._root("TonariSimple-Live",
                         "---\nproject: tonari-app\n---\n")
        code, out = run(["check", "--paths", self.ANCHOR, "--context", ctx,
                         "--limit", "1"], self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("project boost: tonari-app", out, out)
        self.assertEqual(self._served(out), [self.TONARI], out)


# ---------------------------------------------------------------------------- VR5

import bm_vault_enrich as _vr5_enrich            # noqa: E402
import bm_vault_enrich_index as _vr5_eix         # noqa: E402
import bm_vault_promotions as _vr5_promo         # noqa: E402

# The alias is the phrase a person would actually type. The note's own prose never
# uses one of its words, which is the whole failing case RR1 section 5.2 names: a
# note titled "a lane's throwaway home was the founder's real home" is unreachable
# from "isolated HOME for a test".
VR5_ALIAS = "isolated HOME for a test"
VR5_QUESTION = "how do I keep a suite off the real machine"

VR5_TARGET = """---
name: the throwaway root a suite writes into
description: a suite that writes where the founder lives
type: lesson
status: verified
---

# the throwaway root a suite writes into

A suite pointed at the founder's own root wrote his real log. Point every
run at a scratch root it makes and removes itself.
"""

VR5_DECOY = """---
name: the decoy note
description: a second row so the corpus is never one document
type: lesson
status: verified
---

# the decoy note

Unrelated prose about a queue that drains, carrying none of the words above.
"""


class APromotedAliasReachesRecallAndADraftDoesNot(unittest.TestCase):
    """VR5, 2026-09-08. bm_vault_enrich_index.py has drafted aliases and question
    forms since VB11-03 and a human has been able to promote them, but `grep -n alias
    bm_vault.py` returned one unrelated line: the promoted text reached the baked
    catalog and never the retrieval index, so recall could not use it. This joins it
    into the indexed descr at INDEX time (LongMemEval's fact augmented key expansion),
    which is why _search is not touched by this row and query time is unchanged.

    The methods are NUMBERED and that is load bearing: each one inherits the corpus
    the previous one left (index, then draft, then promote), the same shape
    TheTextIndexHoldsNoTermsForANoteThatChangedOrWent above already relies on.

    THE HARD CASE IS test_03. A promotion edits the ENRICHMENT note, never the target
    note, so the target's mtime and its content hash both sit still and every
    short-circuit in the index path reads it as unmodified. test_03 asserts the
    target file's mtime did not move, so a fix that only worked because the file was
    touched cannot pass it.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-vr5-")
        cls.vault = os.path.join(cls.tmp, "vault")
        cls.notes_dir = os.path.join(cls.vault, "10-Projects", "demo")
        os.makedirs(cls.notes_dir)
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        cls.target_rel = "10-Projects/demo/throwaway-root.md"
        cls.target = os.path.join(cls.vault, "10-Projects", "demo", "throwaway-root.md")
        cls.decoy = os.path.join(cls.notes_dir, "decoy.md")
        for path, text in ((cls.target, VR5_TARGET), (cls.decoy, VR5_DECOY)):
            with open(path, "w") as f:
                f.write(text)
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BROTHERMODE_ROOT"] = cls.tmp
        cls.env["BM_FRESHNESS_ROOTS"] = cls.tmp
        cls.env["BM_FRESHNESS_STATE"] = os.path.join(cls.tmp, "freshness_state.sqlite3")
        cls.env["BROTHERME_CONFIG"] = os.path.join(cls.tmp, "brotherme-config.json")
        with open(cls.env["BROTHERME_CONFIG"], "w") as f:
            f.write('{"setup_complete": true}')
        cls.index = os.path.join(cls.tmp, ".claude", "bm_vault_index.sqlite3")
        cls.index_code, cls.index_out = run(["index", "--vault", cls.vault], cls.env)
        cls.target_mtime = os.path.getmtime(cls.target)
        cls.baseline = {}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _row(self, path):
        con = sqlite3.connect(self.index)
        con.row_factory = sqlite3.Row
        try:
            r = con.execute("SELECT descr, content_hash FROM notes WHERE path=?",
                            (path,)).fetchone()
            return (None, None) if r is None else (r["descr"], r["content_hash"])
        finally:
            con.close()

    def _recall(self):
        return run(["recall", "--query", VR5_ALIAS, "--limit", "5", "--fast"], self.env)

    def _drafted_relpath(self, field):
        rows = _vr5_enrich.list_drafts(self.vault, self.target_rel, field=field)
        self.assertEqual(len(rows), 1, rows)
        return rows[0][0]

    def test_01_the_alias_query_misses_the_note_before_any_alias_exists(self):
        self.assertEqual(self.index_code, 0, self.index_out)
        descr, chash = self._row(self.target)
        self.assertEqual(descr, "a suite that writes where the founder lives",
                         "the note did not index at all:\n%s" % self.index_out[:400])
        type(self).baseline = {
            "target": (descr, chash),
            "decoy": self._row(self.decoy),
        }
        code, out = self._recall()
        # Exit 1 is recall's own NO-DATA, which is what a genuine miss looks like; the
        # code is not the assertion here, the absence of the note is.
        self.assertIn(code, (0, 1), out)
        self.assertNotIn(self.target, out,
                         "the note was already reachable from the alias, so this suite "
                         "would pass without the fix:\n%s" % out[:600])

    def test_02_a_drafted_but_unpromoted_alias_is_not_indexed(self):
        ok, messages = _vr5_eix.draft_aliases(
            self.vault, self.target_rel, "test-model-x", self._alias_file())
        self.assertTrue(ok, messages)
        code, out = run(["index", "--vault", self.vault], self.env)
        self.assertEqual(code, 0, out)
        self.assertEqual(self._row(self.target), self.baseline["target"],
                         "an unpromoted draft reached the index; only a promoted "
                         "(canonical, clean record) one may:\n%s" % out[:400])
        code, out = self._recall()
        self.assertIn(code, (0, 1), out)
        self.assertNotIn(self.target, out,
                         "a draft nobody promoted made the note recallable:\n%s" % out[:600])

    def test_03_a_promoted_alias_reaches_recall_without_the_note_being_touched(self):
        for field in ("alias", "question_form"):
            rel = self._drafted_relpath(field)
            for state in ("validated", "canonical"):
                rc = _vr5_promo.cmd_promote(self.vault, rel, state, "khalil",
                                            "2026-08-30", apply_changes=True)
                self.assertEqual(rc, 0, (rel, state))
        self.assertEqual(os.path.getmtime(self.target), self.target_mtime,
                         "the target note itself was modified, so this case would no "
                         "longer prove that a promotion alone reindexes it")
        code, out = run(["index", "--vault", self.vault], self.env)
        self.assertEqual(code, 0, out)
        descr, chash = self._row(self.target)
        self.assertIn("aka: %s" % VR5_ALIAS, descr or "",
                      "the promoted alias never reached the indexed descr:\n%s" % out[:400])
        self.assertIn("asks: %s" % VR5_QUESTION, descr or "")
        self.assertNotEqual(chash, self.baseline["target"][1],
                            "the content hash ignored the promotion, so the next pass "
                            "would skip this note as unchanged")
        code, out = self._recall()
        self.assertEqual(code, 0, out)
        self.assertIn(self.target, out,
                      "the alias is in the index but recall still does not return the "
                      "note:\n%s" % out[:600])

    def test_04_a_note_with_no_promoted_metadata_indexes_byte_identically(self):
        self.assertEqual(self._row(self.decoy), self.baseline["decoy"],
                         "a note with no promoted alias or question form must index "
                         "exactly as it did before this row, descr and hash both")

    def _alias_file(self):
        path = os.path.join(self.tmp, "aliases.txt")
        with open(path, "w") as f:
            f.write("# a comment, skipped\n")
            f.write(VR5_ALIAS + "\n")
            f.write("Q: " + VR5_QUESTION + "\n")
        return path


if __name__ == "__main__":
    unittest.main(verbosity=1)
