"""MB1.b: the README and the how-to show the full simulator run.

The README mobile block teaches profile-init, reference-init and run with
their required arguments (never the bare `run` line that cannot run), the
how-to leads with that short route and keeps the hand route, every mobile
command in both documents parses through the real argument parser, and the
honesty limits stay as narrow as before.
"""
import argparse
import json
import shlex
import sys
import unittest
from pathlib import Path
from typing import List

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import mobile_workflow as M  # noqa: E402

README = ROOT / "README.md"
HOWTO = ROOT / "docs/how-to/native-mobile-workflow.md"
MARKER = "scripts/mobile_workflow.py"
README_HEADING = "### An iOS simulator result needs context"
HOWTO_HEADING = "## Reference and simulator run"


def _fenced(text: str, name: str):
    """Yield (language, line) for every line inside a fenced block."""
    fence = None
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            fence = None if fence is not None else stripped[3:].strip()
            continue
        if fence is not None:
            yield fence, stripped
    if fence is not None:
        raise AssertionError("%s: unterminated code fence" % name)


def sh_commands(path: str, marker: str) -> List[str]:
    """Every fenced text or sh line naming marker, with `python3 <marker>` removed."""
    found = []
    for language, line in _fenced(Path(path).read_text(encoding="utf-8"), path):
        if language in ("text", "sh") and marker in line:
            head = "python3 " + marker
            if not line.startswith(head):
                raise AssertionError("%s: mobile line does not start with %s: %s" % (path, head, line))
            found.append(line[len(head):].strip())
    return found


def section(path: Path, heading: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(heading)
    level = heading.split(" ", 1)[0] + " "
    end = text.find("\n" + level, start + len(heading))
    return text[start:] if end < 0 else text[start:end]


def parse(argv: List[str]) -> argparse.Namespace:
    try:
        return M.build_parser().parse_args(argv)
    except SystemExit:
        raise AssertionError("documented command does not parse: %s" % " ".join(argv))


def flat(text: str) -> str:
    return " ".join(text.split())


class MobileDoorDocsTest(unittest.TestCase):
    def test_the_readme_has_no_bare_run_line(self):
        for line in README.read_text(encoding="utf-8").split("\n"):
            self.assertNotEqual(line.strip(), "python3 scripts/mobile_workflow.py run")

    def test_the_readme_block_teaches_the_three_commands_in_order(self):
        block = section(README, README_HEADING)
        names = [shlex.split(c)[0] for c in sh_commands(str(README), MARKER) if c in block]
        self.assertEqual(names, ["profile-init", "reference-init", "run"])
        self.assertEqual(len(sh_commands(str(README), MARKER)), 3, "a mobile command outside the block")

    def test_every_readme_mobile_command_parses(self):
        commands = sh_commands(str(README), MARKER)
        self.assertTrue(commands, "no README mobile command")
        for command in commands:
            parse(shlex.split(command))
        run = [shlex.split(c) for c in commands if shlex.split(c)[0] == "run"]
        self.assertEqual(len(run), 1)
        for flag in ("--repo", "--profile", "--reference", "--out"):
            self.assertIn(flag, run[0])

    def test_the_howto_names_the_short_route_before_the_hand_route(self):
        text = section(HOWTO, HOWTO_HEADING)
        short, hand = text.find("The short route"), text.find("To write the files by hand instead:")
        self.assertTrue(0 <= short < hand, "short route must lead, hand route must remain")
        names = [shlex.split(c)[0] for c in sh_commands(str(HOWTO), MARKER) if c in text]
        self.assertEqual(names[:3], ["profile-init", "reference-init", "run"])
        self.assertIn("reference", names[3:], "the hand route's reference command is gone")

    def test_every_howto_mobile_command_parses(self):
        commands = sh_commands(str(HOWTO), MARKER)
        names = [shlex.split(c)[0] for c in commands]
        for name in ("doctor", "reference", "check-reference", "run", "pack", "verify-pack",
                     "profile-init", "reference-init"):
            self.assertIn(name, names)
        for command in commands:
            parse(shlex.split(command))

    def test_the_profile_example_is_valid_json_with_the_current_schema(self):
        lines, inside = [], False
        for line in HOWTO.read_text(encoding="utf-8").split("\n"):
            if line.strip() == "```json" and not lines:
                inside = True
                continue
            if inside and line.strip() == "```":
                break
            if inside:
                lines.append(line)
        self.assertEqual(json.loads("\n".join(lines))["schema"], M.PROFILE_SCHEMA)

    def test_the_honesty_limits_remain(self):
        block = flat(section(README, README_HEADING))
        for claim in ("Use simulated devices, not accounts containing real data.",
                      "Human acceptance remains a separate decision.",
                      "The broader device matrix is planned work."):
            self.assertTrue(claim in block, "README block lost: %s" % claim)
        self.assertTrue("Capture validity is not visual acceptance." in flat(HOWTO.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
