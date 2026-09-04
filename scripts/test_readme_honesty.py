#!/usr/bin/env python3
"""test_readme_honesty: the README's executable claims, driven backwards.

The 2026-09-04 docs honesty audit of the public tag v1.0.1 found three
claims on the front page that a reader following them verbatim could not
reproduce. Each class gets a test here, and each test is written so it goes
RED on the v1.0.1 wording and GREEN on the current one:

  1. The receipt sample printed a shape no format string in the shipped
     code produces (the sample had been hand-edited as receipt_sentence
     grew clauses). Pinned against scripts/readme_receipt_sample.py, which
     renders it through the shipped code from the run's own recorded facts.
  2. "In each product directory, run `sh scripts/checksums.sh
     CHECKSUMS.sha256` and then `bash scripts/verify-install.sh`" told the
     reader to WRITE the manifest before verifying against it, so the
     auditor's `git status` showed the product modified by its own
     verification instructions and the PASS proved nothing about the
     shipped bytes.
  3. "The current public tag is unsigned. `git tag -v v1.0.0`" named a tag
     the clone was not at. A version typed into that sentence is stale from
     the next release onward, so this refuses any concrete tag there: the
     sentence must stay parameterised, and the tags that really exist are
     read from docs/releases/, never typed.

Exit contract, the shape every check_all.sh suite uses: 0 every assertion
held, 1 an assertion failed. No NO-DATA path: every input it reads is a
file this repository carries, and a missing one is a failure, not an
absence of data.
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import readme_receipt_sample as SAMPLE  # noqa: E402

ROOT = os.path.dirname(HERE)
README = os.path.join(ROOT, "README.md")
RELEASES = os.path.join(ROOT, "docs", "releases")

#: The sentence that names the unsigned tag, and the shape of a concrete
#: tag typed into it. `<the tag>` is the parameterised form that cannot go
#: stale; `v1.0.0` is what v1.0.1 shipped.
TAG_VERIFY_RE = re.compile(r"git tag -v\s+(\S+)")
CONCRETE_TAG_RE = re.compile(r"^v?\d+\.\d+\.\d+$")


#: The link shape the exporter's own check_markdown_links reads, spelled
#: the same way here so the two never disagree about what a link is.
MD_LINK_RE = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)\)")


def readme_text():
    with open(README, encoding="utf-8") as fh:
        return fh.read()


def fenced_blocks(text):
    """[(block text, 1-based line of its opening fence)] for every ``` code
    fence in `text`. A command inside a fence is an instruction to run it;
    the same command named in prose can be a warning against running it,
    which is what the fixed pages now carry, so the two are never treated
    as the same claim."""
    blocks = []
    lines = text.splitlines()
    open_at = None
    body = []
    for i, line in enumerate(lines):
        if line.startswith("```"):
            if open_at is None:
                open_at, body = i, []
            else:
                blocks.append(("\n".join(body), open_at + 1))
                open_at = None
        elif open_at is not None:
            body.append(line)
    return blocks


def readme_pages():
    """{relative path: text} for README.md and every markdown page a reader
    reaches from it by following relative links, transitively. The guides
    are part of the front door, so a claim this file refuses on README.md
    is refused on them too: the release-verification instruction that
    rewrote its own evidence lived on both."""
    pages = {}
    queue = ["README.md"]
    while queue:
        rel = queue.pop()
        if rel in pages:
            continue
        path = os.path.join(ROOT, rel)
        if not rel.endswith(".md") or not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            pages[rel] = fh.read()
        base = os.path.dirname(path)
        for target in MD_LINK_RE.findall(pages[rel]):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target_path = target.split("#", 1)[0]
            if not target_path:
                continue
            full = os.path.abspath(os.path.join(base, target_path))
            if full.startswith(os.path.abspath(ROOT) + os.sep):
                queue.append(os.path.relpath(full, ROOT))
    return pages


class TheSampleDoesNotDependOnTheCheckoutsRemotes(unittest.TestCase):
    """2026-09-04: the block read "harness <sha>" in ~/brother-hub, where
    origin is the hub, and "harness <sha> (private hub revision)" in the
    dual-remote checkouts, where origin is the public repository, so the
    honesty gate was green in one tree and red in every lane worktree. The
    sample now pins the public ref to one that never resolves; this test
    renders it under both a resolving and a non-resolving ref and asserts
    one text, carrying the private clause."""

    def test_the_block_is_the_same_under_any_public_ref(self):
        import receipt_door as RD
        saved = RD.PUBLIC_REMOTE_REF
        try:
            RD.PUBLIC_REMOTE_REF = "HEAD"
            resolving = SAMPLE.sample_block()
            RD.PUBLIC_REMOTE_REF = "refs/does/not/exist"
            unresolving = SAMPLE.sample_block()
        finally:
            RD.PUBLIC_REMOTE_REF = saved
        self.assertEqual(resolving, unresolving)
        self.assertIn("(private hub revision)", resolving)


class TheReceiptSampleIsWhatTheShippedCodePrints(unittest.TestCase):

    def test_the_readme_carries_the_generated_block_verbatim(self):
        block = SAMPLE.sample_block()
        self.assertIn(
            block, readme_text(),
            "README.md's receipt sample is not what the shipped code "
            "produces from the run's recorded facts. Regenerate it with "
            "python3 scripts/readme_receipt_sample.py and paste the block "
            "in. What the code prints today:\n\n%s" % block)

    def test_the_v1_0_1_sample_would_be_refused(self):
        """The positive control: the exact shape the public tag shipped is
        not the shape the code prints, so this test really discriminates
        rather than passing on any text at all."""
        stale = ("mathlib.py (unit guard): guard delivered: the check was "
                 "run and exited 0. Check written by the planning model, "
                 "harness 015760192728. verdict: PASS")
        self.assertNotIn(stale, SAMPLE.sample_block())

    def test_every_line_names_its_file_its_unit_and_a_check(self):
        lines = SAMPLE.sample_lines()
        self.assertTrue(lines, "the sample renders no line at all")
        for line, entry in zip(lines, SAMPLE.checks()):
            self.assertTrue(line.startswith("%s (unit %s): "
                                            % (entry["file"], entry["unit"])),
                            line)
            self.assertIn(entry["check_command"], line, line)


class TheReleaseVerificationInstructionNeverWritesItsOwnEvidence(
        unittest.TestCase):

    def test_the_readme_does_not_tell_a_reader_to_regenerate_the_manifest(
            self):
        """checksums.sh WITH a filename argument writes the manifest
        (products/*/scripts/checksums.sh calls itself the maintainer-side
        half). A page that names it as a step before verify-install.sh
        instructs the reader to destroy the evidence they came to check.
        Every page a reader reaches from the front door is read, not only
        README.md: the same instruction shipped on
        docs/for-engineers/ADOPTING-ON-YOUR-TEAM.md."""
        pages = readme_pages()
        self.assertIn("README.md", pages)
        for rel, text in sorted(pages.items()):
            for block, line_no in fenced_blocks(text):
                self.assertNotIn(
                    "checksums.sh CHECKSUMS.sha256", block,
                    "%s:%d hands a READER the command that writes "
                    "CHECKSUMS.sha256. Regenerating the manifest is the "
                    "maintainer's step; a reader who runs it before "
                    "verify-install.sh compares the tree against a "
                    "manifest made from that same tree:\n%s"
                    % (rel, line_no, block))

    def test_the_readme_still_names_the_verifier(self):
        self.assertIn("bash scripts/verify-install.sh", readme_text())


#: The commands ship gate C5 (2026-09-04) put on the front page so a reader
#: on either client finds install, start, upgrade and uninstall without
#: guessing. Verified against each host's own --help before being pinned
#: here: `claude plugin --help` and `claude plugin marketplace --help` for
#: the Claude Code column, `codex plugin --help` (codex-cli 0.153.0-alpha.5,
#: the app-bundled binary) and docs/codex/PACKAGE-SHAPE.md for the Codex
#: column. A README that drops or rewords one of these strands a reader on
#: that client exactly the way the pre-C5 README stranded every Codex-only
#: reader.
DUAL_CLIENT_COMMANDS = {
    "Claude Code install": "claude plugin marketplace add khalilmaaouni/Brother && claude plugin install brother@brother",
    "Claude Code start": "/brother",
    "Claude Code upgrade": "claude plugin update brother@brother",
    "Claude Code uninstall": "claude plugin uninstall brother@brother",
    "Codex install (marketplace)": "codex plugin marketplace add https://github.com/khalilmaaouni/Brother",
    "Codex install (plugin)": "codex plugin add brother@brother --json",
    "Codex start": "python3 scripts/brother_run.py",
    "Codex upgrade": "codex plugin marketplace add https://github.com/khalilmaaouni/Brother --ref",
    "Codex uninstall (plugin)": "codex plugin remove brother",
    "Codex uninstall (marketplace)": "codex plugin marketplace remove brother",
}


class TheReadmeNamesBothClientsFullLifecycle(unittest.TestCase):

    def test_the_pre_c5_readme_named_only_claude_code(self):
        """The positive control: the wording this test replaces named no
        Codex install, start, upgrade or uninstall command at all, so this
        class really discriminates rather than passing on any README."""
        pre_c5 = (
            "Brother runs inside Claude Code, so sign in there first. "
            "Keep `python3` and `git` on the machine")
        self.assertNotIn(pre_c5, readme_text())

    def test_every_lifecycle_command_for_both_clients_is_named(self):
        text = readme_text()
        missing = [label for label, cmd in DUAL_CLIENT_COMMANDS.items()
                   if cmd not in text]
        self.assertFalse(
            missing,
            "README.md is missing these dual-client lifecycle commands, "
            "verbatim: %s" % missing)

    def test_claude_code_and_codex_each_get_their_own_section(self):
        self.assertIn("### Claude Code", readme_text())
        self.assertIn("### Codex", readme_text())

    def test_the_codex_hooks_step_is_named(self):
        """A Codex plugin install carries no hooks (see
        docs/codex/HOOKS-MAPPING.md), so the write fence and every other
        hook-driven control stay silent until this second command runs; a
        README that names only the plugin install strands that reader
        exactly the way the pre-C5 README stranded every Codex-only reader."""
        text = readme_text()
        self.assertIn("python3 scripts/codex_hooks_install.py", text)
        self.assertIn("~/.codex/hooks.json", text)


class TheTagSentenceIsNeverATypedVersion(unittest.TestCase):

    def test_no_concrete_tag_is_typed_into_a_git_tag_v_instruction(self):
        for tag in TAG_VERIFY_RE.findall(readme_text()):
            self.assertIsNone(
                CONCRETE_TAG_RE.match(tag.strip("`")),
                "README.md types the concrete tag %r into a `git tag -v` "
                "instruction. It goes stale at the next release exactly as "
                "v1.0.0 did in the v1.0.1 clone: keep the sentence "
                "parameterised." % tag)

    def test_the_release_notes_are_the_place_the_versions_are_read_from(self):
        """The versions this repository really published are the release
        notes on disk, so a reader (and any tool) has a derived source
        instead of a typed one."""
        self.assertTrue(os.path.isdir(RELEASES), RELEASES)
        notes = [n[:-3] for n in os.listdir(RELEASES) if n.endswith(".md")]
        self.assertTrue(notes, "docs/releases carries no release note")
        for note in notes:
            self.assertRegex(note, r"^\d+\.\d+\.\d+$", note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
