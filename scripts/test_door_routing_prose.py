"""Prose-as-code for bare `/brother` routing (P0.1).

This estate's own recorded lesson ("a green suite that never read the word")
is that a check can pass while the words it was meant to pin have drifted or
never existed at all. Bare `/brother` used to be told two different things by
two sections of the same command file: "No argument" said ask a question
unconditionally, "RECOVERY" said a bare invocation does not ask and instead
resumes unfinished work. Both fired on the same trigger (an empty argument),
so which one a session followed was luck.

The fix moved the single decision order into bundle/skills/using-brother/
SKILL.md and made the command file reference it rather than restate it. This
suite pins THAT SHAPE, not just that some text exists:

  1. The skill file names exactly one section as the authoritative decision
     order (a heading matching "authoritative decision order").
  2. The specific sentence that used to make the ask unconditional (with no
     prior check for unfinished work) does not appear anywhere in bundle/.
     If it comes back, the contradiction is back with it.
  3. The authoritative section never instructs telling the person a run id
     or a run directory; it only prohibits doing so.
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
BUNDLE_DIR = os.path.join(REPO_ROOT, "bundle")
SKILL_PATH = os.path.join(BUNDLE_DIR, "skills", "using-brother", "SKILL.md")
COMMAND_PATH = os.path.join(BUNDLE_DIR, "commands", "brother.md")

# The exact sentence the old "No argument" section used to open with: it told
# the assistant to ask a question whenever $ARGUMENTS was empty, with no
# mention of checking for unfinished work first. That is precisely what the
# old "RECOVERY" section contradicted ("does not ask what to build" on the
# very same trigger). Its return anywhere in bundle/ means the contradiction
# is back.
REMOVED_CONTRADICTING_PHRASE = (
    "If `$ARGUMENTS` is empty, say exactly one plain sentence and ask "
    "exactly one question, nothing else:"
)

AUTHORITATIVE_HEADING = re.compile(
    r"^#+.*authoritative decision order", re.IGNORECASE)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _iter_bundle_text_files():
    for dirpath, _dirnames, filenames in os.walk(BUNDLE_DIR):
        for name in filenames:
            if name.endswith((".md", ".py", ".sh")):
                path = os.path.join(dirpath, name)
                yield path, _read(path)


def _authoritative_section_text(skill_text):
    """The authoritative section's own body: from its heading up to (but not
    including) the next heading of the same or a shallower level."""
    lines = skill_text.splitlines()
    start = None
    start_level = None
    for i, line in enumerate(lines):
        m = AUTHORITATIVE_HEADING.match(line)
        if m:
            start = i
            start_level = len(line) - len(line.lstrip("#"))
            break
    assert start is not None, "authoritative heading not found"
    end = len(lines)
    for j in range(start + 1, len(lines)):
        stripped = lines[j]
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            if level <= start_level:
                end = j
                break
    return "\n".join(lines[start:end])


class OneAuthoritativeSection(unittest.TestCase):

    def test_skill_names_exactly_one_authoritative_section(self):
        text = _read(SKILL_PATH)
        headings = [ln for ln in text.splitlines()
                    if AUTHORITATIVE_HEADING.match(ln)]
        self.assertEqual(len(headings), 1,
                          "expected exactly one authoritative-decision-order "
                          "heading in %s, found %r" % (SKILL_PATH, headings))

    def test_no_other_bundle_file_claims_the_authority(self):
        for path, text in _iter_bundle_text_files():
            if path == SKILL_PATH:
                continue
            headings = [ln for ln in text.splitlines()
                        if AUTHORITATIVE_HEADING.match(ln)]
            self.assertEqual(
                headings, [],
                "%s claims a second authoritative decision order: %r"
                % (path, headings))


class NoContradictingProse(unittest.TestCase):

    def test_removed_unconditional_ask_phrase_stays_absent(self):
        for path, text in _iter_bundle_text_files():
            self.assertNotIn(
                REMOVED_CONTRADICTING_PHRASE, text,
                "%s still carries the unconditional-ask phrasing that "
                "contradicted the recovery path" % path)

    def test_command_file_points_at_the_skill_instead_of_restating(self):
        text = _read(COMMAND_PATH)
        self.assertIn("using-brother/SKILL.md", text,
                       "brother.md no longer references the single "
                       "authority for bare /brother")
        # The command file's own "ask a question" step must not be reachable
        # before its "check for unfinished work" step in the source order,
        # which is the shape that made the old contradiction possible.
        check_pos = text.find("check for unfinished work")
        ask_pos = text.find("ask the one question")
        self.assertNotEqual(check_pos, -1, "Step 1 wording missing")
        self.assertNotEqual(ask_pos, -1, "Step 2 wording missing")
        self.assertLess(check_pos, ask_pos,
                         "the unfinished-work check must be written before "
                         "the ask-a-question step, not after")


class NeverNamesTheStorage(unittest.TestCase):

    def test_authoritative_section_never_tells_user_a_run_id_or_directory(self):
        section = _authoritative_section_text(_read(SKILL_PATH))
        # It must PROHIBIT naming the run id/directory, never instruct it.
        self.assertRegex(
            section, r"[Nn]ever.{0,40}run id.{0,40}run directory",
            "the authoritative section must explicitly forbid naming a "
            "run id or run directory to the person")
        # No internal identifier names leaking into user-facing prose.
        for leaked in ("run_dir", "%(run_dir)s", "print(run_dir"):
            self.assertNotIn(leaked, section,
                              "internal identifier %r found in prose meant "
                              "for a person" % leaked)


if __name__ == "__main__":
    unittest.main()
