#!/usr/bin/env python3
"""ST1.a: the runbook teaches the wait that never ends a turn, with a wake reason the owner's hook accepts."""
import os
import re
import unittest
from typing import List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
RUNBOOK = os.path.join(os.path.dirname(HERE), "docs", "plan", "specs", "RUNBOOK-1.1.3.md")
HOOK = os.path.join(os.path.expanduser("~"), ".claude", "hooks", "keep_pulling_the_backlog.py")
WAIT_WORDS_TEXT = r"waiting on|blocked on|external|\b\d{1,2}:\d{2}\b"
HEADING = "## ST1 Waiting without ending the turn"


def section_of(text: str, heading: str) -> str:
    i = text.find(heading + "\n")
    if i < 0:
        return ""
    nxt = text.find("\n## ", i + len(heading))
    return text[i:] if nxt < 0 else text[i:nxt]


def wake_reason_of(section: str) -> Optional[str]:
    m = re.search(r"ScheduleWakeup and the reason `([^`]+)`", section)
    return m.group(1) if m else None


def accepted_by_hook(reason: str) -> bool:
    return re.search(WAIT_WORDS_TEXT, reason, re.I) is not None


def owner_question_of(section: str) -> List[str]:
    return [l for l in section.splitlines() if re.match(r"^- Option \d", l)]


@unittest.skipUnless(os.path.exists(RUNBOOK), "docs/plan/specs/RUNBOOK-1.1.3.md is not shipped here")
class TheRunbook(unittest.TestCase):
    def setUp(self):
        with open(RUNBOOK, encoding="utf-8") as fh:
            self.sec = section_of(fh.read(), HEADING)

    def test_the_runbook_has_the_wait_shape(self):   # REQ-ST1-01
        self.assertTrue(self.sec, "no ST1 section")
        self.assertRegex(self.sec, r"cut_driver\.py wait --phase <name> --until HH:MM")
        self.assertTrue(accepted_by_hook(wake_reason_of(self.sec) or ""))

    def test_the_owner_question_has_two_options_and_the_dated_record(self):   # REQ-ST1-04
        opts = owner_question_of(self.sec)
        self.assertEqual(len(opts), 2)
        self.assertEqual(sum("recommended" in o for o in opts), 1)
        self.assertTrue(all("Cost:" in o for o in opts))
        for date in ("2026-08-24", "2026-10-11", "2026-10-12"):
            self.assertIn(date, self.sec)

    def test_the_section_is_ascii_without_dashes(self):   # REQ-ST1-05
        self.assertTrue(all(ord(c) < 128 for c in self.sec))
        self.assertNotIn(chr(0x2014), self.sec)
        self.assertNotIn(chr(0x2013), self.sec)


class TheHookWords(unittest.TestCase):
    def test_the_wake_reason_is_accepted_and_a_vague_one_is_not(self):   # REQ-ST1-02
        self.assertTrue(accepted_by_hook("waiting on cut_driver phase rehearsal until 09:40"))
        self.assertFalse(accepted_by_hook("checking back later"))
        self.assertEqual(section_of("## A\nx\n", HEADING), "")
        self.assertIsNone(wake_reason_of("nothing here"))

    @unittest.skipUnless(os.path.exists(HOOK), "NO-DATA: the owner's hook file is not on this machine")
    def test_the_literal_equals_the_real_hook_where_present(self):   # REQ-ST1-03
        with open(HOOK, encoding="utf-8") as fh:
            self.assertIn(WAIT_WORDS_TEXT, fh.read())


if __name__ == "__main__":
    unittest.main()
