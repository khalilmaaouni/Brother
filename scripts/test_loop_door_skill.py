"""What the door's loop section (bundle/skills/using-brother/SKILL.md, "## The loop") must keep true (LW1.a): a
person never types the loop's commands. Brother starts a run only through the one launcher, with a deadline and a
budget, asks the one question when either is missing, relays every refusal as the launcher printed it, maps
"Claude only" to the registry's Claude seats, lifts a hold only on the person's own words, and keeps the hand
commands as the explanation of the launcher, never as steps. Every path the door names must ship in the bundle.

Python 3.9, standard library only. Run: python3 -B scripts/test_loop_door_skill.py
"""
import re
import sys
import unittest
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "bundle" / "skills" / "using-brother" / "SKILL.md"
sys.path.insert(0, str(ROOT / "scripts"))
import test_skills_portable  # noqa: E402  check_paths: rule 6, every named path ships

HEADING = "## The loop"
BEFORE = "## Cursor-native agents"
AFTER = "## More detail: verbs, boundaries, handback, closing"
EXPLAIN = "**What the launcher does**"
REQUIRED: List[str] = [
    "You do not type the loop's commands.",
    "runtime/loop/start_run.sh --deadline HH:MM --budget-usd N",
    "bash scripts/loop/start_run.sh --deadline 22:00 --budget-usd 40",
    "If the person names no deadline or no budget, ask that one question and start nothing.",
    "--worker opus55 --adversary opus55 --documenter sonnet55",
    "runtime/loop/loop_watch.py --log",
    "runtime/loop/loop_intake.py status --running",
    "runtime/loop/watch_act.py <class> <reason>",
    "runtime/loop/loop_report.py --run",
    "runtime/loop/loop_report.py --since-hours N",
    "runtime/loop/stop_loop.sh --reason",
    "START REFUSED [step]: fix",
    "the person's own words",
    "sign in once in a terminal by running",
    # D-N1-3: the remedy follows the login the fix names; the saved loop login has its own fix.
    "say to run `runtime/loop/brother_login.py` once in a terminal",
    EXPLAIN,
    "Every number the loop reports is read from a ledger or a log.",
    "Maintainer tooling, not a supported public feature of 1.1.0, and no endurance claim is made for it",
]
FENCE = re.compile(r"^(```|~~~)")


def loop_section(text: str) -> str:
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == HEADING]
    if len(starts) != 1:
        raise AssertionError("%d %r headings in %s, expected exactly one" % (len(starts), HEADING, SKILL))
    out = []
    for line in lines[starts[0] + 1:]:
        if line.startswith("## "):
            break
        out.append(line)
    return "\n".join(out)


def missing_fragments(section: str) -> List[str]:
    folded = " ".join(section.split())
    return [f for f in REQUIRED if f not in folded]


def lines_naming(section: str, name: str) -> List[int]:
    return [i for i, line in enumerate(section.splitlines()) if name in line]


def portable_problems(text: str) -> List[str]:
    problems: List[str] = []
    test_skills_portable.check_paths(SKILL, ROOT / "bundle", text, problems)
    return problems


def _prose_and_fenced(section):
    prose, fenced, inside = [], [], False
    for line in section.splitlines():
        if FENCE.match(line.strip()):
            inside = not inside
            continue
        (fenced if inside else prose).append(line)
    return prose, fenced


class LoopDoorSkillTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.text = SKILL.read_text(encoding="utf-8")
        cls.section = loop_section(cls.text)

    def test_the_section_sits_between_its_neighbours(self):
        heads = [line.strip() for line in self.text.splitlines() if line.startswith("## ")]
        self.assertEqual(heads.count(HEADING), 1)
        at = heads.index(HEADING)
        self.assertIn(BEFORE, heads[:at])
        self.assertIn(AFTER, heads[at + 1:])

    def test_every_required_fragment_is_present(self):
        folded = " ".join(self.section.split())
        for fragment in REQUIRED:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, folded)

    def test_the_hand_commands_are_only_the_explanation(self):
        explain = lines_naming(self.section, EXPLAIN)
        self.assertEqual(len(explain), 1, "one explanation heading")
        for name in ("loop_until.sh", "loop_canary.py"):
            where = lines_naming(self.section, name)
            self.assertEqual(len(where), 1, "%s named on %d lines" % (name, len(where)))
            self.assertGreater(where[0], explain[0], "%s named before the explanation" % name)
        steps = [line for line in self.section.splitlines() if re.match(r"\s*\d+\.", line)]
        self.assertEqual(steps, [], "a numbered hand step is back")

    def test_no_path_needs_a_checkout(self):
        self.assertEqual(portable_problems(self.text), [])

    def test_the_example_is_fenced_and_the_prose_names_no_scripts_path(self):
        prose, fenced = _prose_and_fenced(self.section)
        self.assertTrue(any("bash scripts/loop/start_run.sh" in line for line in fenced))
        self.assertEqual([line for line in prose if "scripts/loop/start_run.sh" in line], [])


if __name__ == "__main__":
    unittest.main()
