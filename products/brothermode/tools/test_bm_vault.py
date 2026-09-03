#!/usr/bin/env python3
"""Tests for bm_vault, on a synthetic corpus built to reproduce the real failure.

The corpus is small and made here rather than read from the founder's vault, so the result cannot
depend on what he happened to have written that week. Its SHAPE is the real one: two distilled
lesson notes that answer the question, several long session logs that mention every term, and an
aggregate index page that mentions everything in the estate. That shape is exactly what defeated
the first version, where the logs and the index page ranked above both lessons.

Run: python3 tools/test_bm_vault.py      (unittest output, exit 0 or 1)
"""
import importlib.util
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main(verbosity=1)
