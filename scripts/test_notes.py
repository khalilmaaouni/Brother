#!/usr/bin/env python3
"""The bounded notes stay true, in meaning and not only in paths: the root CLAUDE.md imports @AGENTS.md exactly once;
every path a note names exists; no note carries a dash of the forbidden kinds; the bundle note agrees with the generator
about what is authored and what is generated; the loop note's chain is the chain the scripts call; the product note keeps
its baton ceremony and no longer says direct to main; the release line the root note names is a real ref.

    python3 -B scripts/test_notes.py
"""
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOTES = ("CLAUDE.md", "scripts/loop/CLAUDE.md", "bundle/CLAUDE.md", "products/brothermode/CLAUDE.md")
PATH_RE = re.compile(r"`((?:scripts|bundle|products|docs)/[A-Za-z0-9_./-]+|PROJECT\.md|AGENTS\.md|SYSTEM\.md|README\.md)`")
EM, EN = chr(0x2014), chr(0x2013)
VERBS = ("start", "status", "next", "review", "deliver", "help")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _exported():
    """True only on a tree the export built: export_public.EXPORT_OVERRIDES swaps the root PROJECT.md for
    docs/plan/PROJECT.public.md there, and ships no root CLAUDE.md on purpose (that note describes the private hub's
    process). The export is told by that deliberate swap, never by a note's absence: a missing note is a defect in
    every tree. Unreadable or absent variant: not an export, so every test runs and a missing note fails."""
    try:
        return read("PROJECT.md") == read("docs/plan/PROJECT.public.md")
    except OSError:
        return False


EXPORTED = _exported()
ROOT_NOTE_OMITTED = "NO-DATA: the exported tree omits the root CLAUDE.md on purpose; the root note is judged in the hub"
#: the notes this tree is expected to carry; the three shipped notes are judged in the export tree too
PRESENT = NOTES[1:] if EXPORTED else NOTES
SIZE_BARS = {"CLAUDE.md": 40, "scripts/loop/CLAUDE.md": 30, "bundle/CLAUDE.md": 30}


class Notes(unittest.TestCase):
    @unittest.skipIf(EXPORTED, ROOT_NOTE_OMITTED)
    def test_the_root_note_imports_agents_once(self):
        text = read("CLAUDE.md")
        self.assertEqual(1, text.count("@AGENTS.md"), "the root note must import @AGENTS.md exactly once")
        self.assertTrue(text.startswith("@AGENTS.md"), "the import is the first line")
        self.assertNotIn("@SYSTEM.md", text, "SYSTEM.md is generated and never imported whole")

    def test_every_named_path_exists(self):
        """A path in a note resolves against the note's own folder first (a product note names its own scripts/),
        then against the repository root; a path that resolves nowhere is a stale note."""
        for rel in PRESENT:
            base = os.path.dirname(os.path.join(ROOT, rel))
            for m in PATH_RE.finditer(read(rel)):
                path = m.group(1).rstrip("/")
                found = os.path.exists(os.path.join(base, path)) or os.path.exists(os.path.join(ROOT, path))
                self.assertTrue(found, "%s names %s, which exists neither beside the note nor at the root" % (rel, path))

    def test_no_forbidden_dash_in_any_note(self):
        for rel in PRESENT:
            text = read(rel)
            self.assertNotIn(EM, text, rel)
            self.assertNotIn(EN, text, rel)

    @unittest.skipIf(EXPORTED, ROOT_NOTE_OMITTED)
    def test_the_release_line_the_root_note_names_is_a_ref(self):
        m = re.search(r"today `(plan/[A-Za-z0-9./-]+)`", read("CLAUDE.md"))
        self.assertIsNotNone(m, "the root note names no release line")
        got = subprocess.run(["git", "-C", ROOT, "show-ref", "--verify", "--quiet", "refs/heads/%s" % m.group(1)])
        self.assertEqual(0, got.returncode, "the root note names %s, which is not a local branch" % m.group(1))

    def test_the_bundle_note_agrees_with_the_generator_about_the_door(self):
        """scripts/codex_surface.py states in its first lines that the door is hand written and six verb skills are
        generated; the note must say the same and name all six verb skills that exist."""
        head = "\n".join(read("scripts/codex_surface.py").splitlines()[:12]).lower()
        self.assertIn("hand written", head, "codex_surface.py no longer says the door is hand written: move the note with it")
        note = read("bundle/CLAUDE.md")
        self.assertIn("bundle/skills/using-brother/SKILL.md", note)
        self.assertRegex(note, r"[Aa]uthored.*using-brother", "the note does not list the door as authored")
        for verb in VERBS:
            self.assertTrue(os.path.isdir(os.path.join(ROOT, "bundle", "skills", "brother-%s" % verb)), verb)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "bundle", "skills", "using-brother", "SKILL.md")))

    def test_the_bundle_note_names_exactly_the_generated_references(self):
        """The generated references are whatever codex_surface.expected() writes under the door's references folder,
        read from the generator itself; the note must name exactly those as generated and the others as authored."""
        import pathlib
        import sys
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import codex_surface
        expected = codex_surface.expected(pathlib.Path(ROOT))
        generated = sorted(k.split("/")[-1] for k in expected if "/references/" in k)
        self.assertTrue(generated, "the generator writes no reference, so the note's claim has no source")
        note = read("bundle/CLAUDE.md")
        for name in generated:
            self.assertIn(name, note, "the note does not name the generated reference %s" % name)
        folder = os.path.join(ROOT, "bundle", "skills", "using-brother", "references")
        authored = sorted(n for n in os.listdir(folder) if n.endswith(".md") and n not in generated)
        for name in authored:
            self.assertIn(name, note, "the note does not name the authored reference %s" % name)
        gen_line = next(l for l in note.splitlines() if "generated by `codex_surface.expected()`" in l)
        for name in authored:
            self.assertNotIn(name + " and", gen_line.split("exactly")[1][:200] if "exactly" in gen_line else "")

    def test_the_loop_note_names_the_chain_the_scripts_call(self):
        """The chain in the note is what loop_pass.sh really invokes."""
        pass_sh = read("scripts/loop/loop_pass.sh")
        for callee in ("land_batch.py", "runner_pool.py"):
            self.assertIn(callee, pass_sh, "loop_pass.sh no longer calls %s: move the note with it" % callee)
        self.assertIn("loop_pass.sh", read("scripts/loop/loop_until.sh"))
        note = read("scripts/loop/CLAUDE.md")
        for name in ("loop_until.sh", "loop_pass.sh", "runner_pool.py", "unit_runner.py", "land_batch.py", "close_unit.py"):
            self.assertIn(name, note, "the loop note omits %s" % name)

    def test_the_product_note_keeps_the_baton_and_drops_direct_to_main(self):
        text = read("products/brothermode/CLAUDE.md")
        self.assertNotIn("Push policy: direct to main", text)
        self.assertIn("never direct to main", text)
        self.assertIn("## The baton ceremony", text, "the baton ceremony (open, resume, verified close) must stay")
        for word in ("resume", "close"):
            self.assertIn(word, text.lower())

    def test_sizes_are_bounded(self):
        """Size targets from the efficiency brief, not tests of usefulness: a note that grows past them is a smell."""
        for rel in PRESENT:
            if rel in SIZE_BARS:
                self.assertLessEqual(len(read(rel).splitlines()), SIZE_BARS[rel], rel)


if __name__ == "__main__":
    unittest.main()
