"""JW4.b: the review and deliver verbs each name the second opinion door once.

Reads scripts/codex_surface.py VERBS, the generated skills, and the shipped
runtime. HOME and BROTHER_JEV_STATE_DIR point at a throwaway folder before
any product import; the one door run uses a file that does not exist, so it
asks nothing and makes no Jev call.
"""
import contextlib
import io
import os
import re
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from typing import List

_HOME = tempfile.mkdtemp(prefix="jev-door-verbs-home-")
os.environ["HOME"] = _HOME
os.environ["BROTHER_JEV_STATE_DIR"] = os.path.join(_HOME, "jev-state")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import bundle_runtime  # noqa: E402
import codex_surface  # noqa: E402
import jev_door  # noqa: E402

REVIEW_SENTENCE = (
    " When a check fails and the person wants a second look, save the failing output to a file and "
    "run `python3 \"${BROTHER_PLUGIN_ROOT}/runtime/jev_door.py\" review --output <the file>` "
    "(`${CLAUDE_PLUGIN_ROOT}` under Claude Code); show its one line under the verdict, labelled "
    "second opinion. It is recorded, never acted on, and never changes the verdict.")
DELIVER_SENTENCE = (
    " When a done note exists, run `python3 \"${BROTHER_PLUGIN_ROOT}/runtime/jev_door.py\" deliver "
    "--note <the note file> --quotes yes|no` (yes when the note quotes a verifying command run after "
    "the last edit) and show its one line under the report. It is recorded, never acted on, and "
    "never changes whether the work is done.")
SENTENCES = {"review": REVIEW_SENTENCE, "deliver": DELIVER_SENTENCE}
COMMAND_RE = re.compile(r'jev_door\.py" ([^`]*)`')


def body_of(verb: str) -> str:
    for name, _description, body in codex_surface.VERBS:
        if name == verb:
            return body
    raise KeyError("no verb named %r" % verb)


def verbs_with(sentence: str) -> List[str]:
    return [name for name, _d, body in codex_surface.VERBS if sentence in body]


class JevDoorVerbsTest(unittest.TestCase):
    def test_each_sentence_is_in_its_verb_once_and_nowhere_else(self):
        for verb, sentence in SENTENCES.items():
            self.assertEqual(body_of(verb).count(sentence), 1, verb)
            self.assertEqual(verbs_with(sentence), [verb])
            self.assertEqual(body_of(verb).count("jev_door.py"), 1, verb)
        with self.assertRaises(KeyError):
            body_of("ship")

    def test_the_generated_skills_carry_them(self):
        self.assertEqual(codex_surface.check(Path(ROOT).resolve()), [])
        for verb, sentence in SENTENCES.items():
            for tree in ("skills", "codex-skills"):
                path = os.path.join(ROOT, "bundle", tree, "brother-%s" % verb, "SKILL.md")
                with open(path, "r", encoding="utf-8") as fh:
                    self.assertEqual(fh.read().count(sentence.strip()), 1, path)

    def test_the_door_ships_in_the_runtime(self):
        self.assertIn("jev_door.py", bundle_runtime.compute_closure())
        with open(os.path.join(HERE, "jev_door.py"), "rb") as src, \
                open(os.path.join(ROOT, "bundle", "runtime", "jev_door.py"), "rb") as shipped:
            self.assertEqual(src.read(), shipped.read())

    def test_no_checkout_path_is_named(self):
        for verb, sentence in SENTENCES.items():
            self.assertNotIn("scripts/", sentence)
            self.assertNotIn("data/", sentence)
            folders = re.findall(r'python3 \\?"([^"\\]*)/jev_door\.py', body_of(verb))
            self.assertEqual(folders, ["${BROTHER_PLUGIN_ROOT}/runtime"], verb)

    def test_the_sentence_matches_a_real_parser(self):
        missing = os.path.join(_HOME, "does-not-exist.txt")
        for verb, sentence in SENTENCES.items():
            commands = COMMAND_RE.findall(body_of(verb))
            self.assertEqual(len(commands), 1, verb)
            for quotes in ("yes", "no"):
                text = (commands[0].replace("<the file>", missing)
                        .replace("<the note file>", missing).replace("yes|no", quotes))
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = jev_door.main(shlex.split(text))
                self.assertEqual(code, 0, text)
                lines = out.getvalue().splitlines()
                self.assertEqual(len(lines), 1, out.getvalue())
                self.assertIn("NO-DATA", lines[0])

    def test_the_other_verbs_do_not_mention_the_door(self):
        for name, description, body in codex_surface.VERBS:
            if name not in SENTENCES:
                self.assertNotIn("jev_door", body + description, name)


if __name__ == "__main__":
    unittest.main()
