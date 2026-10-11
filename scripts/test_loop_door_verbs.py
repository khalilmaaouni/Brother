"""What the five verb skill bodies must keep true about the loop (LW1.b): start, status, next, review and deliver
each carry exactly one sentence routing the person's plain words to the loop's tools, help carries none, no body
teaches the hand commands, the generated skills are current, and the start body still teaches the controller stop
beside the loop stop.

Python 3.9, standard library only. Run: python3 -B scripts/test_loop_door_verbs.py
"""
import sys
import unittest
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import codex_surface  # noqa: E402  VERBS and check(): the one source of the six verb skills

SENTENCES: Dict[str, str] = {
    'start': (
        'A run until a time or on a budget, such as "start a run until 22:00 with 40 dollars" or "run the loop overnight", is the maintainer loop: follow the loop section of `${CLAUDE_PLUGIN_ROOT}/skills/using-brother/SKILL.md`, start it only through `runtime/loop/start_run.sh`, say every refusal as `START REFUSED [step]: fix`, and stop it with `runtime/loop/stop_loop.sh --reason` when the person says "stop the loop".'),
    'status': (
        'Answer "loop status" or "is the loop healthy" from `runtime/loop/loop_watch.py --log <the newest driver log>` and `runtime/loop/loop_intake.py status --running`, saying each line as printed.'),
    'next': (
        'When the person asks why nothing is landing, give the class printed by `runtime/loop/loop_watch.py` to `runtime/loop/watch_act.py <class> <reason>` and name the one action it returns.'),
    'review': (
        'Answer "What did the loop land" from `runtime/loop/loop_report.py --run <the driver log>`, giving every figure with the file it names.'),
    'deliver': (
        "Give the loop's morning report from `runtime/loop/loop_report.py --since-hours N` in the report shape (key takeaway, actions, blockers, next steps, facilitators) before any handover pack."),
}
HAND_COMMANDS = ("loop_until.sh", "loop_canary.py", "loop_intake.py prepare")
CONTROLLER_STOP = "`/brother start stop` ENDS the running controller run"


def body_of(verb: str) -> str:
    return next(body for name, _desc, body in codex_surface.VERBS if name == verb)


def generated_text(verb: str) -> str:
    return (ROOT / "bundle" / "skills" / ("brother-%s" % verb) / "SKILL.md").read_text(encoding="utf-8")


class LoopDoorVerbsTest(unittest.TestCase):

    def test_each_verb_body_holds_its_sentence_once(self):
        for verb, sentence in SENTENCES.items():
            with self.subTest(verb=verb):
                self.assertEqual(body_of(verb).count(sentence), 1)

    def test_no_other_body_holds_a_loop_tool(self):
        for name, _desc, body in codex_surface.VERBS:
            rest = body.replace(SENTENCES.get(name, "\0"), "")
            self.assertNotIn("runtime/loop/", rest, "%s names a loop tool outside its one sentence" % name)

    def test_no_body_teaches_the_hand_commands(self):
        for name, _desc, body in codex_surface.VERBS:
            for hand in HAND_COMMANDS:
                self.assertNotIn(hand, body, (name, hand))

    def test_the_generated_skills_carry_them(self):
        for verb, sentence in SENTENCES.items():
            with self.subTest(verb=verb):
                self.assertIn(sentence, generated_text(verb))
        self.assertEqual(codex_surface.check(ROOT), [])

    def test_the_controller_stop_is_still_taught(self):
        self.assertIn(CONTROLLER_STOP, body_of("start"))


if __name__ == "__main__":
    unittest.main()
