"""MB1.a: the door routes mobile words to one named, checked sequence.

The door's description carries the iPhone, iPad, SwiftUI and simulator
trigger words, the native mobile reference teaches doctor, profile-init,
reference-init and run in that order, every taught command parses through the
real argument parser, and the receipt fields the reference names exist in a
receipt the fixture run writes. No new command, skill or verb is added.
"""
import argparse
import shlex
import subprocess
import sys
import unittest
from pathlib import Path
from typing import List, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import mobile_workflow as M  # noqa: E402

SKILL = ROOT / "bundle/skills/using-brother/SKILL.md"
REFERENCE = ROOT / "bundle/skills/using-brother/references/native-mobile.md"
MIRROR_SKILL = ROOT / "bundle/codex-skills/using-brother/SKILL.md"
MIRROR_REFERENCE = ROOT / "bundle/codex-skills/using-brother/references/native-mobile.md"
PREFIX = 'python3 "$BROTHER_PLUGIN_ROOT/runtime/mobile_workflow.py"'
SECTION = "## Native mobile and creative work"


def door_description(skill_path: str) -> str:
    """Return the quoted description value on line 3 of a SKILL.md."""
    lines = Path(skill_path).read_text(encoding="utf-8").split("\n")
    line = lines[2] if len(lines) > 2 else ""
    head = 'description: "'
    if not (line.startswith(head) and line.endswith('"') and len(line) > len(head)):
        raise AssertionError("%s: line 3 is not a quoted single line description" % skill_path)
    return line[len(head):-1]


def reference_commands(reference_path: str) -> List[Tuple[str, List[str]]]:
    """Every taught mobile_workflow command inside a fenced sh block, in order."""
    found = []
    fence = None
    for number, line in enumerate(Path(reference_path).read_text(encoding="utf-8").split("\n"), 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            fence = None if fence is not None else stripped[3:].strip()
            continue
        if fence == "sh" and stripped.startswith(PREFIX + " "):
            words = shlex.split(stripped[len(PREFIX):])
            if words and not words[0].startswith("-"):
                found.append((words[0], words[1:]))
    if fence is not None:
        raise AssertionError("%s: unterminated code fence" % reference_path)
    return found


def parse_command(parser: argparse.ArgumentParser, argv: List[str]) -> argparse.Namespace:
    try:
        return parser.parse_args(argv)
    except SystemExit:
        raise AssertionError("taught command does not parse: %s" % " ".join(argv))


def section_text(path: Path, heading: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start:] if end < 0 else text[start:end]


class MobileDoorTest(unittest.TestCase):
    def test_the_description_carries_the_mobile_words(self):
        for path in (SKILL, MIRROR_SKILL):
            description = door_description(str(path))
            for word in ("iPhone", "iPad", "SwiftUI", "simulator"):
                self.assertTrue(word in description, "%s lacks %s" % (path, word))
            self.assertLessEqual(len(description), 800, str(path))

    def test_the_section_names_the_sequence(self):
        self.assertIn("doctor, profile-init, reference-init, run", section_text(SKILL, SECTION))

    def test_the_reference_teaches_the_four_commands_in_order(self):
        names = [name for name, _ in reference_commands(str(REFERENCE))]
        self.assertEqual(names, ["doctor", "profile-init", "reference-init", "run"])

    def test_every_taught_command_parses_with_its_arguments(self):
        commands = reference_commands(str(REFERENCE))
        self.assertTrue(commands, "no taught command")
        for name, argv in commands:
            parse_command(M.build_parser(), [name] + argv)
        run = [argv for name, argv in commands if name == "run"]
        self.assertEqual(len(run), 1)
        for flag in ("--repo", "--profile", "--reference", "--out"):
            self.assertIn(flag, run[0])

    def test_the_reference_stops_calling_the_tools_internal(self):
        self.assertFalse("internal support tools" in REFERENCE.read_text(encoding="utf-8"), "reference calls the tools internal")

    def test_the_receipt_fields_named_are_real(self):
        text = REFERENCE.read_text(encoding="utf-8")
        for field in ("simulator.name", "simulator.runtime", "screenshot.sha256",
                      "observations.human_acceptance"):
            self.assertTrue("`%s`" % field in text, "missing field: %s" % field)
        import test_mobile_workflow as TW
        case = TW.MobileWorkflowTests("test_full_fixture_workflow_binds_test_install_capture_and_preserves_uncertainty")
        case.setUp()
        try:
            report, receipt = case.run_flow()
        finally:
            case.doCleanups()
        self.assertEqual(report["technical_status"], "PASS", report.get("reason"))
        self.assertTrue(report["simulator"]["name"])
        self.assertIn("runtime", report["simulator"])
        self.assertTrue(report["screenshot"]["sha256"])
        self.assertEqual(report["observations"]["human_acceptance"], "NO-DATA")

    def test_the_asks_are_written_as_a_user_says_them(self):
        text = " ".join(REFERENCE.read_text(encoding="utf-8").split())
        for ask in ("check my change on iPhone 16", "Can I build iOS here?",
                    "Does it fit the SE and the iPad?"):
            self.assertTrue(ask in text, "missing ask: %s" % ask)

    def test_no_new_command_is_added(self):
        for path in (SKILL, REFERENCE, MIRROR_SKILL, MIRROR_REFERENCE):
            self.assertNotIn("/brother mobile", path.read_text(encoding="utf-8"), str(path))
        flat = " ".join(SKILL.read_text(encoding="utf-8").split())
        self.assertIn("six verbs stand behind it, each a skill named `brother-<verb>`: "
                      "start, status, next, review, deliver, help.", flat)

    def test_the_mirrors_are_current(self):
        done = subprocess.run([sys.executable, "scripts/codex_skills.py", "--check"], cwd=str(ROOT),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertEqual(MIRROR_SKILL.read_bytes(), SKILL.read_bytes())
        self.assertEqual(MIRROR_REFERENCE.read_bytes(), REFERENCE.read_bytes())

    def test_the_shipped_helper_exists(self):
        self.assertTrue((ROOT / "bundle/runtime/mobile_workflow.py").is_file())


if __name__ == "__main__":
    unittest.main()
