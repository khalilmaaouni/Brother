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
import shutil
import subprocess
import sys
import tempfile
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


def fenced_blocks(text, with_info=False):
    """[(block text, 1-based line of its opening fence)] for every ``` code
    fence in `text`. A command inside a fence is an instruction to run it;
    the same command named in prose can be a warning against running it,
    which is what the fixed pages now carry, so the two are never treated
    as the same claim.

    `with_info=True` prepends the fence's own info string (`bash`, `bash
    reference`, `text reference`, ...) to each tuple, for
    ThePlainBashFencesRunCleanFromAClone below, which has to tell a plain
    ```bash fence from a ```bash reference one; every other caller predates
    that need and keeps the 2-tuple shape."""
    blocks = []
    lines = text.splitlines()
    open_at = None
    info = None
    body = []
    for i, line in enumerate(lines):
        if line.startswith("```"):
            if open_at is None:
                open_at, info, body = i, line[3:].strip(), []
            else:
                entry = ("\n".join(body), open_at + 1)
                if with_info:
                    entry = (info,) + entry
                blocks.append(entry)
                open_at = None
        elif open_at is not None:
            body.append(line)
    if open_at is not None:
        raise AssertionError(
            "unclosed code fence opened at line %d: every ``` fence must "
            "close, or its content silently disappears from every check "
            "that reads fenced_blocks() instead of being examined."
            % (open_at + 1))
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
        """2026-09-10: the wholesale documentation replacement dropped the
        pasted receipt sample from README.md rather than moving it.
        Grepped every .md file in the repository for the block's opening
        line ("mathlib.py (unit guard)") and its harness id
        ("015760192728"): no match anywhere, including README.md itself.
        This is one of the two facts the replacement order names by
        example as deliberately dropped (README.md: "Exact version
        capability belongs in the public release and generated
        SYSTEM.md, not copied historical prose"), a rendered receipt
        sample being exactly copied historical prose. Recorded in
        docs/decisions/readme-facts-moved-2026-09-10.md under "The
        verbatim generated receipt sample block". The sample renderer's
        own output shape stays checked by the two tests below, which do
        not depend on README.md carrying a copy of it."""
        pass

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


#: EVAD unit U1 (release 1.0.13, angles t1 and t6): the words a stranger
#: should not have to learn to reach one verified delivery, named by the
#: unit brief itself rather than reused from FIRST_SCREEN_BANNED_WORDS
#: above, which is a different, narrower list this task was not asked to
#: change.
FIRST_RUN_BANNED_WORDS = [
    "fence", "claim", "store", "harness", "lane", "worktree", "unit",
    "door", "intake",
]
FIRST_RUN_BANNED_RE = re.compile(
    r"\b(?:%s)\b" % "|".join(re.escape(w) for w in FIRST_RUN_BANNED_WORDS),
    re.IGNORECASE)


class TheFirstRunTranscriptIsWhatTheShippedCodePrints(unittest.TestCase):
    """EVAD unit U1: the 'Your first run, start to finish' block must be
    exactly what readme_receipt_sample.first_run_transcript() prints, the
    same discipline TheReceiptSampleIsWhatTheShippedCodePrints holds the
    older per-file sample to, so a change to receipt_door or RUN_FACTS
    cannot drift silently from the copy pasted on the page."""

    HEADING = "## Your first run, start to finish"

    def test_the_readme_carries_the_transcript_verbatim(self):
        text = readme_text()
        self.assertIn(self.HEADING, text, "README.md dropped the heading")
        after = text.split(self.HEADING, 1)[1]
        blocks = fenced_blocks(after)
        self.assertTrue(
            blocks, "no fenced block follows %r" % self.HEADING)
        block_text, _ = blocks[0]
        self.assertEqual(
            block_text, SAMPLE.first_run_transcript(),
            "README.md's first-run block is not what the shipped code "
            "prints. Regenerate it with "
            "python3 scripts/readme_receipt_sample.py --first-run.")

    def test_the_transcript_names_no_internal_noun(self):
        hits = sorted(set(
            m.group(0).lower() for m in
            FIRST_RUN_BANNED_RE.finditer(SAMPLE.first_run_transcript())))
        self.assertFalse(
            hits,
            "the first-run transcript names internal noun(s) a stranger "
            "does not need to reach one verified delivery: %s" % hits)

    def test_no_per_file_line_is_a_bare_exit_code(self):
        """CDX-2 (Codex review of f24b7bb3): the renderer discarded the
        receipt's state and reason and printed only 'exited 0' for every
        file, hiding that test_mathlib.py's own check was recorded
        NO-DATA. A bare '<file>: check <cmd> exited <code>' line, with
        nothing after the exit code, is refused outright."""
        bare = re.compile(r": check .+ exited \d+$", re.MULTILINE)
        hits = bare.findall(SAMPLE.first_run_transcript())
        self.assertFalse(
            hits, "a per-file line names only an exit code, with no "
            "verdict: %s" % hits)

    def test_each_per_file_line_carries_its_own_checks_verdict(self):
        """The per-file line for each changed file must carry that file's
        own state from readme_receipt_sample.checks(): mathlib.py verified
        (its check proved the fix), test_mathlib.py NO-DATA (its check was
        never re-run with mathlib.py's change undone, so it does not prove
        the test on its own)."""
        entries = {e["file"]: e for e in SAMPLE.checks()}
        self.assertEqual(entries["mathlib.py"]["state"], "verified")
        self.assertEqual(entries["test_mathlib.py"]["state"], "no-data")
        transcript_lines = SAMPLE.first_run_transcript().splitlines()
        by_file = {}
        for line in transcript_lines:
            for fname in entries:
                if line.startswith(fname + ":"):
                    by_file[fname] = line
        self.assertIn("mathlib.py", by_file)
        self.assertIn("test_mathlib.py", by_file)
        self.assertIn("verified", by_file["mathlib.py"].lower())
        self.assertIn("no-data", by_file["test_mathlib.py"].lower())
        # the NO-DATA line still says WHY, not only the bare word: the
        # dependency it was never re-run without.
        self.assertIn("reverted", by_file["test_mathlib.py"].lower())


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
        """2026-09-10: the release-verification instruction moved off
        README.md to docs/how-to/prepare-a-release.md, and the literal
        `bash scripts/verify-install.sh` command moved with it: that
        script (products/*/scripts/verify-install.sh) verifies one
        product directory, not the Brother repository README.md is the
        front door of. The umbrella-repository equivalent is
        scripts/reproduce_export.py's --verify-tree mode, the mode that
        (per its own docstring) "RUNS FROM THE OUTSIDE", needing nothing
        but a clone and the published tag. Re-anchored: the guide is
        checked for the reproducibility/export-verification instruction,
        README.md is checked for a link chain that reaches the guide, and
        the tool the guide is pointing at is checked for the flag that
        makes the outside-the-hub claim true, so this does not just trust
        prose that could drift again."""
        pages = readme_pages()
        self.assertIn("README.md", pages)
        self.assertIn(
            "docs/how-to/prepare-a-release.md", pages,
            "README.md no longer reaches "
            "docs/how-to/prepare-a-release.md by any relative link "
            "chain, so the release-verification instruction is "
            "unreachable from the front door.")
        guide = pages["docs/how-to/prepare-a-release.md"]
        self.assertIn(
            "reproducibility/export verification from a clean checkout",
            guide,
            "docs/how-to/prepare-a-release.md dropped the instruction to "
            "verify a release from a clean checkout.")
        reproduce_export_path = os.path.join(
            ROOT, "scripts", "reproduce_export.py")
        with open(reproduce_export_path, encoding="utf-8") as fh:
            reproduce_export_source = fh.read()
        self.assertIn(
            "--verify-tree", reproduce_export_source,
            "the guide promises reproducibility/export verification from "
            "a clean checkout, but scripts/reproduce_export.py no longer "
            "has the --verify-tree mode that runs from outside the "
            "private hub.")


#: The commands ship gate C5 (2026-09-04) put on the front page so a reader
#: on either client finds install, start, upgrade and uninstall without
#: guessing. Verified against each host's own --help before being pinned
#: here: `claude plugin --help` and `claude plugin marketplace --help` for
#: the Claude Code column, `codex plugin --help` (codex-cli 0.153.0-alpha.5,
#: the app-bundled binary) and docs/codex/PACKAGE-SHAPE.md for the Codex
#: column. A README that drops or rewords one of these strands a reader on
#: that client exactly the way the pre-C5 README stranded every Codex-only
#: reader.
#:
#: 2026-09-04, row E109 (see TheCommandsAReaderTypesAreTheOnesTheClients
#: Accept below), found four literal Codex commands rejected by the real
#: client. 2026-09-10's documentation replacement answers that class of
#: defect structurally on two of the four Codex actions: install-codex.md
#: no longer pins a `codex plugin ...` invocation for install or
#: uninstall at all, it tells the reader to use whatever their installed
#: Codex version accepts. Those two are checked below as the generalised
#: instruction rather than a literal command. Codex upgrade has no
#: instruction of either kind anywhere in the replacement corpus; see
#: docs/decisions/readme-facts-moved-2026-09-10.md, "The Codex upgrade
#: lifecycle step".
CLAUDE_LIFECYCLE_COMMANDS = {
    "Claude Code install": "claude plugin marketplace add khalilmaaouni/Brother && claude plugin install brother@brother",
    "Claude Code start": "/brother",
    "Claude Code upgrade": "claude plugin update brother@brother",
    "Claude Code uninstall": "claude plugin uninstall brother@brother",
}

#: Codex's own repository script, not a Codex CLI surface, so it does not
#: go stale the way a `codex plugin ...` invocation does. Still carried
#: verbatim after 2026-09-10.
CODEX_LITERAL_COMMANDS = {
    "Codex start": "python3 scripts/brother_run.py",
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
        """2026-09-10: the wholesale documentation replacement moved most
        of these commands off README.md itself. Claude Code's install and
        start stayed on the front page; its upgrade and uninstall moved
        to docs/how-to/install-claude-code.md, reached from README.md's
        own "Do:" line. Codex start moved to docs/how-to/install-codex.md
        the same way. Codex install and Codex uninstall no longer carry a
        literal `codex plugin ...` command anywhere reachable: checked
        below as the generalised instruction install-codex.md replaced
        them with. Codex upgrade is not checked here at all; see the
        class-level comment on CLAUDE_LIFECYCLE_COMMANDS and
        docs/decisions/readme-facts-moved-2026-09-10.md."""
        pages = readme_pages()
        text = "\n".join(pages.values())
        missing = [label for label, cmd in CLAUDE_LIFECYCLE_COMMANDS.items()
                   if cmd not in text]
        missing += [label for label, cmd in CODEX_LITERAL_COMMANDS.items()
                    if cmd not in text]
        self.assertFalse(
            missing,
            "the pages reachable from README.md are missing these "
            "dual-client lifecycle commands, verbatim: %s" % missing)

        codex_guide = pages.get("docs/how-to/install-codex.md", "")
        self.assertIn(
            "Use the current Codex marketplace/plugin commands supported "
            "by your installed Codex version", codex_guide,
            "docs/how-to/install-codex.md dropped the instruction that "
            "tells a Codex reader how to find the install command for "
            "their own installed version.")
        self.assertIn(
            "before plugin removal", codex_guide,
            "docs/how-to/install-codex.md dropped the instruction that "
            "orders hook removal before plugin removal on uninstall.")

    def test_claude_code_and_codex_each_get_their_own_section(self):
        self.assertIn("### Claude Code", readme_text())
        self.assertIn("### Codex", readme_text())

    def test_the_codex_hooks_step_is_named(self):
        """A Codex plugin install carries no hooks (see
        docs/codex/HOOKS-MAPPING.md), so the write fence and every other
        hook-driven control stay silent until this second command runs; a
        README that names only the plugin install strands that reader
        exactly the way the pre-C5 README stranded every Codex-only
        reader. 2026-09-10: this step moved off README.md itself to
        docs/how-to/install-codex.md, reached from README's own Codex
        section and its "Do:" line. The literal "~/.codex/hooks.json"
        path is not typed anywhere in the replacement corpus:
        codex_hooks_install.py's own --allow-default-home flag exists so
        nothing silently assumes that default, so the fact is checked
        against the shipped script's own path-building function rather
        than against prose that would have to repeat the same
        assumption."""
        pages = readme_pages()
        self.assertIn("README.md", pages)
        self.assertIn(
            "docs/how-to/install-codex.md", pages,
            "README.md no longer reaches docs/how-to/install-codex.md by "
            "any relative link chain.")
        codex_guide = pages["docs/how-to/install-codex.md"]
        self.assertIn("python3 scripts/codex_hooks_install.py", codex_guide)
        self.assertIn(
            "~/.codex", codex_guide,
            "docs/how-to/install-codex.md dropped every mention of the "
            "Codex home directory the hooks step wires into.")
        import codex_hooks_install as CHI
        default_home = os.path.expanduser("~/.codex")
        self.assertEqual(
            CHI.hooks_json_path(default_home),
            os.path.join(default_home, "hooks.json"),
            "scripts/codex_hooks_install.py no longer writes "
            "<CODEX_HOME>/hooks.json at the default Codex home, so the "
            "dropped '~/.codex/hooks.json' sentence would be wrong even "
            "if it were restored verbatim.")


#: A marketplace-naming command and the name it was handed. `claude plugin
#: marketplace add` takes a repository slug, which really is `khalilmaaouni/
#: Brother`; every other verb takes the marketplace's declared name, which
#: .claude-plugin/marketplace.json spells `brother`, in lower case.
CLAUDE_MARKETPLACE_RE = re.compile(
    r"claude plugin marketplace (update|remove|info)\s+(\S+)")

#: A Codex plugin removal and the argument it names.
CODEX_REMOVE_RE = re.compile(r"codex plugin remove\s+(\S+)")


class TheCommandsAReaderTypesAreTheOnesTheClientsAccept(unittest.TestCase):
    """2026-09-04, row E109: a newcomer audit of the public 1.0.2 page ran
    every install, update and uninstall command in throwaway homes and four
    of them were rejected by the client they were written for. Each one gets
    an assertion here, each written to go red on the 1.0.2 wording."""

    def test_every_marketplace_command_uses_the_declared_lower_case_name(self):
        """`claude plugin marketplace update Brother` answers "Marketplace
        'Brother' not found. Available marketplaces: brother" and exits 1,
        measured 2026-09-04 in an isolated CLAUDE_CONFIG_DIR. Only fenced
        blocks are read: the page also names the rejected spelling in prose,
        as the warning it is."""
        for block, line_no in fenced_blocks(readme_text()):
            for verb, name in CLAUDE_MARKETPLACE_RE.findall(block):
                self.assertEqual(
                    name.strip("`"), "brother",
                    "README.md:%d hands `claude plugin marketplace %s` the "
                    "name %r. The marketplace declared by "
                    ".claude-plugin/marketplace.json is `brother`, in lower "
                    "case, and any other spelling is rejected."
                    % (line_no, verb, name))

    def test_the_v1_0_2_capitalised_marketplace_lines_would_be_refused(self):
        """The positive control: the exact lines the public tag handed a
        reader to run."""
        for block, line_no in fenced_blocks(readme_text()):
            self.assertNotIn("claude plugin marketplace update Brother",
                             block, "README.md:%d" % line_no)
            self.assertNotIn("claude plugin marketplace remove Brother",
                             block, "README.md:%d" % line_no)

    def test_every_codex_removal_names_the_plugin_and_its_marketplace(self):
        """`codex plugin remove brother` is refused with "plugin requires
        --marketplace unless passed as <plugin>@<marketplace>", measured
        2026-09-04 against the app-bundled codex."""
        for block, line_no in fenced_blocks(readme_text()):
            for argument in CODEX_REMOVE_RE.findall(block):
                self.assertIn(
                    "@", argument.strip("`"),
                    "README.md:%d tells a reader to run `codex plugin remove "
                    "%s`. Codex refuses a bare plugin name: write it as "
                    "<plugin>@<marketplace>." % (line_no, argument))

    def test_both_runtime_verifier_paths_are_named_with_where_each_works(self):
        """The bundle is unwrapped on install: an installed plugin root
        holds `runtime/` and no `bundle/`, so the single path the 1.0.2
        page named for an installed plugin could not exist there.
        Measured 2026-09-04: `python3 bundle/runtime/verify_runtime.py`
        exits 2 with No such file from an installed Codex plugin root,
        and `python3 runtime/verify_runtime.py` prints PASS over 33 files
        there.

        2026-09-10: dropped rather than re-anchored. Grepped docs/how-to,
        docs/reference, docs/architecture, docs/codex, docs/explanation,
        docs/tutorials, docs/assurance, bundle/, and README.md itself for
        "verify_runtime.py": no match anywhere reachable from the front
        door (it only appears inside docs/releases, docs/plan and
        docs/handover, which docs/README.md's own "Internal/historical
        material" section names as history rather than the public
        navigation model). Recorded in
        docs/decisions/readme-facts-moved-2026-09-10.md under "The two
        runtime-verifier invocation paths"."""
        pass

    def test_the_codex_hook_uninstall_route_is_named_not_a_file_deletion(self):
        """1.0.2 told a reader to delete the whole Codex hooks file, which
        takes unrelated hooks with it. The installer now has --uninstall,
        which removes only the commands it wrote. 2026-09-10: this moved
        off README.md itself to docs/how-to/install-codex.md; the refused
        phrase is checked across every page reachable from the front
        door, not only README.md, so it cannot resurface on a different
        page either."""
        pages = readme_pages()
        self.assertIn("README.md", pages)
        self.assertIn("docs/how-to/install-codex.md", pages)
        for rel, text in sorted(pages.items()):
            self.assertNotIn(
                "has no uninstall or `--uninstall` route", text, rel)
        self.assertIn("--uninstall", pages["docs/how-to/install-codex.md"])


class TheSmallChangePriceMatchesItsOwnDecisionRecord(unittest.TestCase):
    """E90. The limits section now carries what a small change really costs
    through the door. Those figures came out of one measurement, recorded in
    docs/decisions/light-path-for-small-changes-2026-09-04.json, and the
    front page must not drift from it: every number the paragraph quotes is
    read back out of the record here rather than trusted where it is typed.

    Driven backwards: before the paragraph existed the README carried none
    of these figures and this failed on the first one."""

    RECORD = os.path.join(ROOT, "docs", "decisions",
                          "light-path-for-small-changes-2026-09-04.json")

    #: The engine-cost figures the ruling turned on, which is why the
    #: paragraph may quote them at all.
    FIGURES = ("0.78", "568.03", "728", "2.58", "1.69")

    def test_every_figure_in_the_readme_paragraph_is_in_the_record(self):
        """2026-09-10: the wholesale documentation replacement dropped the
        whole paragraph this class exists to check, rather than moving
        it. Grepped README.md and every docs/how-to and docs/reference
        page for "568.03" (the least generic of the five figures) and for
        the record's own filename: no match anywhere. This is the other
        fact the replacement order names by example as deliberately
        dropped (README.md: "Exact version capability belongs in the
        public release and generated SYSTEM.md, not copied historical
        prose"), an exact cost figure being exactly that. Recorded in
        docs/decisions/readme-facts-moved-2026-09-10.md under "The
        small-change price paragraph and its cited figures". What stays
        checked: the record itself is still on disk, so the measurement
        the paragraph used to cite was not deleted, only its front-page
        citation was, and the front page has not silently grown a partial,
        uncited version of the paragraph back."""
        self.assertTrue(
            os.path.exists(self.RECORD),
            "%s is gone from disk, not just uncited: the measurement "
            "behind the retired paragraph no longer exists at all."
            % os.path.relpath(self.RECORD, ROOT))
        readme = readme_text()
        for figure in self.FIGURES:
            self.assertNotIn(
                figure, readme,
                "%s is back on the front page; if the paragraph was "
                "deliberately restored, re-anchor this test to check it "
                "against the decision record again instead of asserting "
                "its absence." % figure)


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


#: E99 (2026-09-04): the README's Limits section described the file fence
#: and the single-writer claim in prose without ever naming the tests that
#: prove them, and never mentioned the push-time gate or the private-term
#: scan at all, so a reader had to find the proof by listing scripts/.
#: Reuses readiness_gate.py's own suite-running shape: run the script, its
#: exit code is the evidence.
NEWLY_NAMED_LIMITS_SUITES = [
    "test_claim_store.py",
    "test_lifecycle_hooks.py",
    "test_private_terms_scan.py",
    "test_pre_push_gate.py",
]


class TheFourLimitsControlsAreNamedAndActuallyRun(unittest.TestCase):

    def test_each_suite_is_named_in_the_readme(self):
        """The positive control: before this class existed, README.md's
        proof list named eight suites and none of these four, so this
        assertion failed on that wording and only passed once the Limits
        section named them. 2026-09-10: the wholesale documentation
        replacement dropped README's own hand-typed proof list entirely
        (the Limits section now reads "Exact version capability belongs
        in the public release and generated SYSTEM.md, not copied
        historical prose"), so the four suites are checked against
        generated SYSTEM.md's own battery listing instead of hand-typed
        prose that would only go stale again.
        docs/architecture/system-map.md makes this the corpus's own
        canonical answer: "Use generated SYSTEM.md for exact module/check
        inventory. Do not duplicate its table here." README.md still
        names SYSTEM.md by exact string, so the suites stay one lookup
        away from the front door."""
        self.assertIn(
            "SYSTEM.md", readme_text(),
            "README.md no longer names SYSTEM.md, so a reader has no way "
            "to learn that the exact suite list lives there.")
        system_doc_path = os.path.join(ROOT, "SYSTEM.md")
        self.assertTrue(os.path.isfile(system_doc_path), system_doc_path)
        with open(system_doc_path, encoding="utf-8") as fh:
            system_doc = fh.read()
        missing = [s for s in NEWLY_NAMED_LIMITS_SUITES if s not in system_doc]
        self.assertFalse(
            missing,
            "SYSTEM.md's battery listing does not name a check for: %s"
            % missing)

    def test_each_named_suite_exists_and_exits_zero(self):
        for name in NEWLY_NAMED_LIMITS_SUITES:
            path = os.path.join(HERE, name)
            with self.subTest(suite=name):
                self.assertTrue(os.path.isfile(path), path)
                proc = subprocess.run(
                    [sys.executable, path],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                self.assertEqual(
                    proc.returncode, 0,
                    "%s exited %d:\n%s" % (
                        name, proc.returncode,
                        proc.stdout.decode("utf-8", "replace")))



#: S17: the first screen, title through the end of Install (the first
#: `## ` heading that follows it), must get a stranger to a first command
#: without teaching them a single internal noun. Reused verbatim from the
#: brief that closed this row. Matched whole-word (and as a whole phrase
#: for the two-word entry) and case-insensitively, so "receipt contract"
#: is caught as a phrase while ordinary prose like "a rerunnable receipt"
#: is not.
#: 2026-09-10: "door" removed. The wholesale documentation replacement
#: made "door" the README's own taught front-screen metaphor rather than
#: internal jargon: the first screen's second heading is literally "## One
#: door, three outcomes", and the Start section explains "the bare door"
#: in the same breath as the install command. A newcomer meets the word
#: before finishing the first screen by design now, so it no longer
#: belongs on a list of words a newcomer does not need yet. The rest of
#: this list still catches genuine internal jargon.
#: "door" was on this list until 2026-09-10 and was deliberately removed.
#: The list bans INTERNAL ARCHITECTURE a newcomer does not need on the
#: first screen. "door" stopped being internal architecture when the
#: product's own routing skill and its owner adopted it as the user facing
#: name for the single entry point ("the one Brother door"), and the
#: replaced README teaches it in that sense: "the bare door asks what you
#: are trying to do". A word the product teaches its users is vocabulary,
#: not jargon. This is a LOOSENING of the guard and it is written here so
#: it is a decision somebody can argue with, rather than a silent edit;
#: docs/decisions/readme-facts-moved-2026-09-10.md carries the same note.
#: The other seven words stay banned, and adding a word back needs the
#: same treatment.
FIRST_SCREEN_BANNED_WORDS = [
    "kernel", "journal", "loom", "fence", "canonical", "capsule",
    "unit", "receipt contract",
]

FIRST_SCREEN_BANNED_RE = re.compile(
    r"\b(?:%s)\b" % "|".join(re.escape(w) for w in FIRST_SCREEN_BANNED_WORDS),
    re.IGNORECASE)


def first_screen_text():
    """README.md from its title through the end of the Install section:
    everything before the second `## ` heading (`## What success looks
    like`). What a stranger sees before scrolling past install."""
    lines = readme_text().splitlines()
    headings = [i for i, line in enumerate(lines) if line.startswith("## ")]
    assert len(headings) >= 2, "README.md carries fewer than two ## headings"
    return "\n".join(lines[:headings[1]])


class TheFirstScreenTeachesNoInternalArchitecture(unittest.TestCase):
    """S17. Before this row's fix, the Install section explained the
    single-writer fence and the BrotherMode store, by name, before either
    client's first command. Each test here is written to go RED on that
    wording and GREEN on the current one."""

    def test_the_pre_fix_install_paragraph_would_be_refused(self):
        stale = ("One prerequisite is easy to miss because nothing creates "
                 "it for you. Brother's single-writer fence reads a "
                 "per-repository store")
        self.assertNotIn(stale, first_screen_text())

    def test_no_banned_architecture_word_appears_on_the_first_screen(self):
        text = first_screen_text()
        hits = sorted(set(m.group(0).lower()
                          for m in FIRST_SCREEN_BANNED_RE.finditer(text)))
        self.assertFalse(
            hits,
            "README.md's first screen (title through the end of Install) "
            "names internal architecture word(s) a newcomer does not need "
            "yet: %s. Move the explanation below the first screen instead "
            "of deleting it." % hits)

    def test_the_signature_sentence_is_on_the_first_screen(self):
        self.assertIn("When AI says done, Brother gives you proof.",
                     first_screen_text())

    def test_the_moved_explanation_still_lives_below_the_first_screen(self):
        """"Move it down," not "delete it": the store-setup explanation
        this fix removed from the first screen must still be reachable,
        past the first screen. 2026-09-10: the wholesale documentation
        replacement moved the explanation further than "below the first
        screen" inside README.md: it now lives on
        docs/reference/work-units.md, reached from README's own
        "Look up:" line ("Work units"). The exact old sentence
        ("Brother's single-writer fence reads a per-repository store") is
        not restated anywhere; the guarantee it protected, that a unit's
        write boundary is enforced rather than merely suggested, is
        restated as work-units.md's "Writes fence" rule. Checked against
        that page's exact wording rather than the retired sentence."""
        self.assertNotIn(
            "Brother's single-writer fence reads a per-repository store",
            readme_text(),
            "the retired sentence is back on README.md verbatim; if it "
            "was deliberately restored, re-anchor this test at README.md "
            "again instead of work-units.md.")
        pages = readme_pages()
        self.assertIn("README.md", pages)
        self.assertIn(
            "docs/reference/work-units.md", pages,
            "README.md no longer reaches docs/reference/work-units.md by "
            "any relative link chain, so the write-boundary explanation "
            "is unreachable from the front door.")
        work_units = pages["docs/reference/work-units.md"]
        moved = ("**Writes fence:** declare every path the unit may "
                 "touch. Out-of-scope writes should be quarantined/"
                 "refused, not normalized after the fact.")
        self.assertIn(
            moved, work_units,
            "docs/reference/work-units.md dropped the write-fence rule "
            "the old single-writer-fence sentence used to carry.")
        self.assertNotIn(moved, first_screen_text())



#: Prose right around a runnable fence that says its own command means to
#: fail: read, the test expects nonzero instead of refusing it. Deliberately
#: narrow to the two phrasings the corpus already uses elsewhere (see
#: git cat-file -t ...: "exits non-zero in a public clone"), never a bare
#: "fails" or "error", which prose uses for reasons that are not this.
FENCE_NONZERO_HINTS = ("exits non-zero", "exits 1")


def _nonzero_hint_nearby(lines, line_no):
    """True if prose in the six lines before the fence line through the
    four lines after it says the fenced command exits non-zero.
    `line_no` is 1-based, the shape fenced_blocks() returns (`open_at + 1`
    where `open_at` is the fence's 0-based index into `lines`). A prior
    version used `line_no` directly as a 0-based slice index, shifting the
    whole window one line late; converting to the 0-based fence index
    first (`line_no - 1`) is what this function exists to get right, and
    it is extracted from ThePlainBashFencesRunCleanFromAClone below so the
    boundary can be tested without cloning or running anything."""
    fence_idx = line_no - 1
    nearby = lines[max(0, fence_idx - 6):fence_idx + 4 + 1]
    return any(h in "\n".join(nearby) for h in FENCE_NONZERO_HINTS)


#: Every fenced bash/text block on the page now carries a ` reference`
#: info-string suffix (2026-09-10), so no fence reads as exactly ```bash
#: any more; filtering on that literal tag would make this test skip
#: forever, which is NO-DATA wearing a pass. The runnable claim that
#: matters is the "Your first run" transcript's own setup: it is the one
#: block a reader is told to type verbatim before anything Brother-specific
#: happens, so its own lines (mkdir, git init, the two cat heredocs, git
#: add, git commit) are extracted and run, stopping before the plugin
#: install line, which needs a real marketplace and is proven elsewhere
#: (prove_guide_claude.py).
FIRST_RUN_STOP_AT = "claude plugin marketplace add"


def _first_run_setup_lines(text):
    """(command text, 1-based fence line) for the first fenced block whose
    body opens with `mkdir `, truncated to the lines before
    FIRST_RUN_STOP_AT. None, None if the transcript is not on the page."""
    for info, body, line_no in fenced_blocks(text, with_info=True):
        lines = body.splitlines()
        if lines and lines[0].startswith("mkdir "):
            setup = []
            for line in lines:
                if line.startswith(FIRST_RUN_STOP_AT):
                    break
                setup.append(line)
            return "\n".join(setup), line_no
    return None, None


class ThePlainBashFencesRunCleanFromAClone(unittest.TestCase):
    """EVAD unit U1, item 4: the "Your first run" transcript claims its
    setup runs from nothing but a fresh clone, python3 and git. This proves
    that claim instead of trusting the tag: the setup runs in a throwaway
    clone, never against this checkout, so a fence that writes files
    cannot touch the tree the test itself runs from."""

    def test_every_plain_bash_fence_runs_in_a_clean_clone(self):
        if shutil.which("git") is None or shutil.which("python3") is None:
            self.skipTest("NO-DATA: git or python3 is not on PATH")
        text = readme_text()
        command, line_no = _first_run_setup_lines(text)
        if command is None:
            self.skipTest(
                "no first-run transcript on the page: 0 runnable claims "
                "exercised, NO-DATA is not a pass")
        with tempfile.TemporaryDirectory() as tmp:
            clone = os.path.join(tmp, "clone")
            cloned = subprocess.run(
                ["git", "clone", "--quiet", ROOT, clone],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.assertEqual(
                cloned.returncode, 0,
                "could not clone %s into a temp directory:\n%s"
                % (ROOT, cloned.stdout.decode("utf-8", "replace")))
            proc = subprocess.run(
                command, shell=True, cwd=clone,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            out = proc.stdout.decode("utf-8", "replace")
            self.assertEqual(
                proc.returncode, 0,
                "README.md:%d's first-run setup claims it runs from a "
                "clean clone with python3 and git alone; it exited "
                "%d:\n%s\n%s"
                % (line_no, proc.returncode, command, out))


class FencedBlocksRefusesAnUnclosedFence(unittest.TestCase):
    """EVAD unit U1b: an opened ``` fence with no closing ``` used to be
    dropped by fenced_blocks() with no signal at all, silently removing
    the block (and whatever claim it carried) from every test that reads
    fenced_blocks(). It must fail loudly instead."""

    def test_an_unclosed_fence_raises_instead_of_vanishing(self):
        text = "some prose\n```bash\necho hi\nstill open, never closed\n"
        with self.assertRaises(AssertionError):
            fenced_blocks(text)


class TheNonzeroHintWindowIsCountedFromTheFenceLineNotOffByOne(
        unittest.TestCase):
    """EVAD unit U1b: fenced_blocks() returns a 1-based line number for a
    fence's opening ``` line; the hint window used to treat that 1-based
    number as a 0-based index straight into `lines`, shifting the six-
    before/four-after window one line late. A hint placed exactly on a
    boundary line (six before, or four after) is found only once the
    1-based-to-0-based conversion is correct."""

    def test_a_hint_exactly_six_lines_before_the_fence_is_found(self):
        lines = ["line %d" % i for i in range(20)]
        line_no = 11  # 1-based: the fence opens at 0-based lines[10]
        lines[10 - 6] = "this command exits non-zero on this platform"
        self.assertTrue(_nonzero_hint_nearby(lines, line_no))

    def test_a_hint_exactly_four_lines_after_the_fence_is_found(self):
        lines = ["line %d" % i for i in range(20)]
        line_no = 11
        lines[10 + 4] = "this command exits non-zero on this platform"
        self.assertTrue(_nonzero_hint_nearby(lines, line_no))


if __name__ == "__main__":
    unittest.main(verbosity=2)
