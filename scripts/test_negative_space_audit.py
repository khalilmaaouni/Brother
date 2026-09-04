"""test_negative_space_audit.py: proves scripts/negative_space_audit.py
actually sees what it claims to see, rather than reporting a table nobody
checked against real code.

Two backwards drives, per the hardening brief (R27.2,
docs/plan/HARDENING-2026-08-30-CODEX.md mechanism 2):

  (a) a hand-added fixture noun the extractor would miss, if the extractor's
      own detection regressed, MUST be caught: a fixture module built to look
      exactly like a real durable-noun writer (persists JSON, owns a
      create/list/show/resume/close-shaped function each) is written to a
      throwaway directory and find_nouns() must pick it up. This is the
      extractor's own canary: if a future edit narrows the verb sets or the
      write-detection regex so this fixture stops matching, this test fails
      first, before a real noun silently drops off the grid.

  (b) removing one answer flips that cell to NO-DATA, driven and asserted:
      the SAME fixture, with its "close" function deleted, must see its
      "close" cell flip from ANSWERED to NO-DATA and nothing else move,
      proving the verdict tracks the code rather than being cached, hard
      coded, or answered by the noun's mere presence in the grid.

Every fixture lives under a tempfile.mkdtemp() directory, never inside this
repository, so this suite touches no product file.
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import negative_space_audit as nsa  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
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


_FIXTURE_NOUN_SOURCE = '''"""fixture_widget_store: a fake durable-noun module for test_negative_space_audit.

Shaped exactly like a real writer this estate ships (claim_store.py,
attempt_ledger.py): persists JSON, and owns one function for each of the
five function-shaped lifecycle questions, plus field-shaped hits for the
rest, so a healthy extractor answers every one of the thirteen questions.

Every field-shaped answer (origin, opened_time, closed_time,
subject_binding, freshness, malformed_disposition, producer) lives here in
the module docstring or in create(), deliberately NEVER inside close(), so
deleting close() in drive (b) below removes exactly one function-shaped
answer and nothing else: unit_id (subject_binding), producer_version
(producer), ttl (freshness), closed_at (closed_time).
"""
import json

STORE = "widgets.json"


def create(widget_id, unit_id=None, origin="fixture", created_at=0,
          closed_at=None, ttl=60, producer_version=1):
    row = {"widget_id": widget_id, "unit_id": unit_id, "origin": origin,
          "created_at": created_at, "closed_at": closed_at, "ttl": ttl,
          "producer_version": producer_version}
    try:
        with open(STORE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\\n")
    except (ValueError, OSError):
        return None
    return row


def list_widgets(store=STORE):
    return [json.loads(l) for l in open(store, encoding="utf-8")]


def show(widget_id, store=STORE):
    for row in list_widgets(store):
        if row["widget_id"] == widget_id:
            return row
    return None


def resume(widget_id):
    return show(widget_id)


def close(widget_id):
    """The one function drive (b) deletes. Answers only the "close"
    question; every field-shaped answer lives in create() above."""
    return show(widget_id)
'''

_FIXTURE_IMPORTER_SOURCE = '''"""fixture_widget_consumer: imports the fixture noun above, so the consumer
question has something real to find."""
import fixture_widget_store  # noqa: F401
'''


class NegativeSpaceAuditFixtureDrives(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nsa-fixture-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fixture_path = os.path.join(self.tmp, "fixture_widget_store.py")
        with open(self.fixture_path, "w", encoding="utf-8") as fh:
            fh.write(_FIXTURE_NOUN_SOURCE)
        with open(os.path.join(self.tmp, "fixture_widget_consumer.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(_FIXTURE_IMPORTER_SOURCE)

    # -- drive (a): the extractor must see a real writer fixture -----------

    def test_a_fixture_noun_writer_is_found_by_the_extractor(self):
        nouns = nsa.find_nouns(dirs=(self.tmp,))
        names = {r["noun"] for r in nouns}
        self.assertIn("fixture_widget", names,
                      "the extractor missed a fixture built to look exactly "
                      "like a real durable-noun writer (persists JSON, owns "
                      "a create/list/show/resume/close-shaped function "
                      "each); this is the extractor's own blind spot, "
                      "sees-what-the-writers-see failed")

    def test_a_healthy_fixture_answers_every_question(self):
        grid = nsa.build_grid(dirs=(self.tmp,))
        row = next(r for r in grid if r["noun"] == "fixture_widget")
        unanswered = [q for q in nsa.QUESTIONS
                     if row["cells"][q][0] == nsa.NODATA]
        self.assertEqual(unanswered, [],
                         "the fixture was built to answer all thirteen "
                         "questions; a NO-DATA here means the extractor's "
                         "own pattern set, not the fixture, is missing "
                         "something: %r" % unanswered)

    # -- drive (b): removing one answer flips exactly that cell -----------

    def test_b_removing_the_close_function_flips_only_that_cell(self):
        before = {r["noun"]: r["cells"]
                 for r in nsa.build_grid(dirs=(self.tmp,))}["fixture_widget"]
        self.assertEqual(before["close"][0], "ANSWERED")

        with open(self.fixture_path, encoding="utf-8") as fh:
            source = fh.read()
        # Delete only the close() function's def line and body, leaving
        # every other lifecycle function untouched, so a change in exactly
        # one place should move exactly one cell.
        start = source.index("def close(")
        patched = source[:start]
        with open(self.fixture_path, "w", encoding="utf-8") as fh:
            fh.write(patched)

        after = {r["noun"]: r["cells"]
                 for r in nsa.build_grid(dirs=(self.tmp,))}["fixture_widget"]
        self.assertEqual(after["close"][0], nsa.NODATA,
                         "deleting close() must flip the close cell to "
                         "NO-DATA; it did not, so the verdict is not "
                         "actually tracking the code")
        moved = [q for q in nsa.QUESTIONS if before[q][0] != after[q][0]]
        self.assertEqual(moved, ["close"],
                         "removing one function moved more than one cell: "
                         "%r (want exactly ['close'])" % moved)

    def test_consumer_cell_finds_a_real_importer_in_the_same_scanned_dirs(self):
        grid = nsa.build_grid(dirs=(self.tmp,))
        row = next(r for r in grid if r["noun"] == "fixture_widget")
        verdict, detail = row["cells"]["consumer"]
        self.assertEqual(verdict, "ANSWERED", detail)
        self.assertIn("fixture_widget_consumer", detail)


class NegativeSpaceAuditMechanics(unittest.TestCase):
    def test_empty_dir_yields_zero_nouns_not_a_crash(self):
        empty = tempfile.mkdtemp(prefix="nsa-empty-")
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        self.assertEqual(nsa.find_nouns(dirs=(empty,)), [])

    def test_render_counts_match_the_grid(self):
        grid = nsa.build_grid(dirs=(HERE,))
        text, counts = nsa.render(grid)
        total = sum(1 for r in grid for _ in nsa.QUESTIONS)
        self.assertEqual(sum(counts.values()), total)
        self.assertIn("no-data=%d" % counts[nsa.NODATA], text)

    def test_real_repo_scan_exits_0_1_or_2_never_crashes(self):
        # A live smoke check against the real repo dirs: whatever the exit
        # code, it must be one of the three documented verdicts, and the
        # process must not raise. --strict is not passed, so a NO-DATA
        # verdict (2) is expected and acceptable on an honest first run.
        rc = nsa.main([])
        self.assertIn(rc, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
