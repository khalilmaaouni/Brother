"""The founder queue: one file, read the same way by board_status.py's CLI
line and gen_readiness_board.py's card section. Three things must hold: the
seed file renders every OPEN item as a card, a missing file is NO-DATA (never
an empty section and never a crash) in both tools, and a DONE item drops out
of the count.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_status as BS  # noqa: E402
import gen_readiness_board as G  # noqa: E402


SEED = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "docs", "plan", "FOUNDER-QUEUE.json")


def write_queue(items):
    fh = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                     encoding="utf-8")
    json.dump(items, fh)
    fh.close()
    return fh.name


class TheSeedFileRendersNCards(unittest.TestCase):
    def test_the_seed_file_has_five_open_items(self):
        items, err = BS.open_founder_queue_items(SEED)
        self.assertIsNone(err)
        self.assertEqual(len(items), 6)

    def test_the_board_renders_one_card_per_open_item(self):
        old = BS.FOUNDER_QUEUE_PATH
        try:
            BS.FOUNDER_QUEUE_PATH = os.path.abspath(SEED)
            html = G.render(G.load())
        finally:
            BS.FOUNDER_QUEUE_PATH = old
        start = html.find('<section class="fqueue">')
        end = html.find('</section>', start)
        section = html[start:end]
        self.assertEqual(section.count('class="norow"'), 6)
        for fid in ("FQ-1", "FQ-2", "FQ-3", "FQ-4", "FQ-5", "FQ-6"):
            self.assertIn(fid, section)

    def test_the_status_line_names_every_open_id(self):
        line = BS.founder_queue_status_line(os.path.abspath(SEED))
        self.assertTrue(line.startswith("Founder queue: 6 open"))
        for fid in ("FQ-1", "FQ-2", "FQ-3", "FQ-4", "FQ-5", "FQ-6"):
            self.assertIn(fid, line)


class MissingFileIsNoDataInBothTools(unittest.TestCase):
    def test_board_status_reports_no_data(self):
        line = BS.founder_queue_status_line("/no/such/founder-queue.json")
        self.assertTrue(line.startswith("Founder queue: %s" % BS.NODATA))

    def test_the_board_section_shows_no_data_not_a_crash(self):
        old = BS.FOUNDER_QUEUE_PATH
        try:
            BS.FOUNDER_QUEUE_PATH = "/no/such/founder-queue.json"
            html = G.render(G.load())  # must not raise
        finally:
            BS.FOUNDER_QUEUE_PATH = old
        self.assertIn(BS.NODATA, html[html.find('Founder queue'):html.find('Founder queue') + 200])

    def test_a_malformed_file_is_also_no_data(self):
        path = write_queue({"not": "a list"})
        try:
            items, err = BS.open_founder_queue_items(path)
            self.assertIsNone(items)
            self.assertIsNotNone(err)
        finally:
            os.unlink(path)


class ADoneItemIsNotCounted(unittest.TestCase):
    def test_done_item_excluded_from_open_items(self):
        path = write_queue([
            {"id": "FQ-A", "since": "2026-09-01", "title": "t", "command": "c",
             "blocks": [], "status": "OPEN", "done_evidence": None},
            {"id": "FQ-B", "since": "2026-08-01", "title": "t2", "command": "c2",
             "blocks": [], "status": "DONE", "done_evidence": "closed"},
        ])
        try:
            items, err = BS.open_founder_queue_items(path)
            self.assertIsNone(err)
            self.assertEqual([it["id"] for it in items], ["FQ-A"])
            line = BS.founder_queue_status_line(path)
            self.assertTrue(line.startswith("Founder queue: 1 open"))
            self.assertNotIn("FQ-B", line)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
