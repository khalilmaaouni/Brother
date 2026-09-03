#!/usr/bin/env python3
"""The foreign-runtime generator, and the drift it exists to prevent.

Run: python3 tools/test_sbe_port.py

Codex and Cursor both read `skills/<name>/SKILL.md` with `name:` and
`description:` frontmatter, which is the same format this project already
ships. That fact is what makes a port cheap, and it is also what makes a
hand-maintained second copy dangerous: twelve duplicated files drift within a
week and nobody reads both sides to notice. So the port is generated, and the
assertion that matters is that `--check` actually FAILS when the two disagree.
A drift check that cannot go red is decoration.
"""

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
PORT = os.path.join(HERE, "sbe_port.py")


def run(*args):
    return subprocess.run([sys.executable, PORT] + list(args),
                          capture_output=True, text=True, cwd=ROOT)


class TheGenerator(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="sbe-port-")
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)

    def test_it_generates_one_file_per_source_skill(self):
        made = run("codex", "--out", self.out)
        self.assertEqual(made.returncode, 0, made.stderr)
        sources = sorted(d for d in os.listdir(os.path.join(ROOT, "skills"))
                         if os.path.isfile(os.path.join(ROOT, "skills", d, "SKILL.md")))
        ported = sorted(os.listdir(os.path.join(self.out, "skills")))
        self.assertEqual(ported, sources,
                         "the port is missing or inventing skills: %r versus %r"
                         % (ported, sources))

    def test_a_generated_file_keeps_the_frontmatter_both_hosts_read(self):
        run("codex", "--out", self.out)
        any_skill = sorted(os.listdir(os.path.join(self.out, "skills")))[0]
        with io.open(os.path.join(self.out, "skills", any_skill, "SKILL.md"),
                     encoding="utf-8") as handle:
            text = handle.read()
        self.assertTrue(text.startswith("---\nname: "),
                        "the generated file does not open with the frontmatter Codex and "
                        "Cursor read, so it would not load in either")
        self.assertIn("\ndescription: ", text.split("\n---", 1)[0])

    def test_every_generated_file_says_the_refusals_did_not_travel(self):
        """The overclaim this whole port could have shipped. Guidance moving to
        another host must never read as if the gates moved with it."""
        run("codex", "--out", self.out)
        for name in os.listdir(os.path.join(self.out, "skills")):
            with io.open(os.path.join(self.out, "skills", name, "SKILL.md"),
                         encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("DO NOT travel to this runtime", text,
                          "%s does not tell the reader the refusals stayed behind" % name)

    def test_check_passes_on_a_freshly_generated_tree(self):
        run("codex", "--out", self.out)
        verdict = run("codex", "--out", self.out, "--check")
        self.assertEqual(verdict.returncode, 0, verdict.stderr)

    def test_check_FAILS_when_a_generated_file_drifts(self):
        """The calibration. Without this the check above could pass over a
        comparison that never compared anything."""
        run("codex", "--out", self.out)
        victim = sorted(os.listdir(os.path.join(self.out, "skills")))[0]
        target = os.path.join(self.out, "skills", victim, "SKILL.md")
        with io.open(target, "a", encoding="utf-8") as handle:
            handle.write("\nan edit nobody made in the source\n")
        verdict = run("codex", "--out", self.out, "--check")
        self.assertNotEqual(verdict.returncode, 0,
                            "a generated file was edited and --check still passed, so the "
                            "drift guard guards nothing")
        self.assertIn(victim, verdict.stderr,
                      "the drift was detected without naming which skill drifted")

    def test_check_FAILS_when_a_generated_file_is_missing(self):
        run("codex", "--out", self.out)
        victim = sorted(os.listdir(os.path.join(self.out, "skills")))[0]
        os.remove(os.path.join(self.out, "skills", victim, "SKILL.md"))
        verdict = run("codex", "--out", self.out, "--check")
        self.assertNotEqual(verdict.returncode, 0)
        self.assertIn("not generated", verdict.stderr)

    def test_cursor_uses_the_same_shape_as_codex(self):
        """Both hosts were verified by reading a real installed example. If that
        stops being true the port for one of them is silently wrong."""
        run("cursor", "--out", self.out)
        verdict = run("cursor", "--out", self.out, "--check")
        self.assertEqual(verdict.returncode, 0, verdict.stderr)

    def test_it_reads_nothing_outside_this_repository(self):
        """A generator that reached into a runtime's config or credential
        directory to build a package would be a tool nobody should run. The
        format came from reading one public bundled example, by hand, once."""
        with io.open(PORT, encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("~/.codex", "~/.cursor", "expanduser", "config.toml", "auth.json"):
            self.assertNotIn(forbidden, source,
                             "sbe_port.py references %r; the generator must read only this "
                             "repository" % forbidden)


if __name__ == "__main__":
    unittest.main(verbosity=2)
