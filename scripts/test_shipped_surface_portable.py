"""The shipped surface must only tell a stranger things a stranger can run.

E43, EVAD run 5 critic 1 stumble 8 (2026-09-03). bundle/skills/using-brother/
SKILL.md told every installed session to run
`$HOME/.claude/vault-tools/tools/bm_vault_catalog.py bake`, under a heading
opened by "FOUNDER ORDER 2026-08-30". Neither the tool nor the order exists on
anyone else's machine. The bundle-install smoke read green throughout, because
it checks that files land, never what the words in them say.

SCOPE, stated rather than assumed: this greps the shipped PROSE, the files an
installed session reads as instructions (bundle/**/*.md and bundle/
MANIFEST.json). It deliberately does not grep bundle/runtime/*.py, whose
source comments are provenance notes for a maintainer reading the engine, not
instructions handed to a user; those bytes are mirrored from scripts/ by
bundle_runtime.py and are checked there for drift, not for prose.

WHAT COUNTS AS A HIT, and why each one:
  - an absolute `/Users/` path: names one machine's home directory;
  - `vault-tools`: an estate-only checkout nothing in the marketplace installs;
  - the words "founder order" (any case): an instruction addressed to this
    estate's founder, carrying an authority a stranger's session cannot check.

A hit is a FAIL naming the file and line. The maintainer half of the cut block
lives in docs/how-to/MAINTAINER-CLOSING-CEREMONY.md, which is outside the
shipped surface and is therefore not scanned.

A SEPARATE CHECK, scoped to bundle/skills/using-brother/references/*.md only
(not the whole bundle: router-details.md names scripts/gen_door_table.py as
how it was GENERATED, a provenance note a maintainer reads, never an
instruction the installed session is told to run, and that file does not
ship): every `scripts/<name>.py` an instruction in that directory names must
resolve to `bundle/runtime/<name>.py`, the actual shipped mirror. This is
the gap that let bundle/skills/using-brother/references/intake.md tell a
session to run scripts/intake_inflight.py and scripts/receipt_check.py,
neither of which bundle_runtime.py ever mirrors.
"""
import io
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
BUNDLE_DIR = os.path.join(REPO_ROOT, "bundle")

FORBIDDEN = (
    ("absolute home path", re.compile(r"/Users/")),
    ("estate-only vault-tools checkout", re.compile(r"vault-tools")),
    ("founder-order block", re.compile(r"founder\s+order", re.IGNORECASE)),
)

RUNTIME_DIR = os.path.join(BUNDLE_DIR, "runtime")
# Only an actual invocation, never a provenance mention like
# "GENERATED... by `scripts/gen_door_table.py`" (router-details.md),
# which describes how a file was built, not an instruction to run it.
SCRIPTS_PY_REF = re.compile(r"\bpython3?\s+scripts/([A-Za-z0-9_]+\.py)\b")
# ANY mention of a scripts/ path, invocation or not: no install carries scripts/.
SCRIPTS_PATH = re.compile(r"(?<![\w./-])scripts/([A-Za-z0-9_]+\.py)\b")
# A path spelled bundle/..., which only a checkout of the repository has.
BUNDLE_PREFIXED = re.compile(r"(?<![\w./-])bundle/")
# A path spelled from the plugin root, bare or behind a plugin root variable.
PLUGIN_PATH = re.compile(
    r"(?:\$\{?[A-Z_]*PLUGIN_ROOT\}?/|(?<![\w./${}-]))"
    r"((?:skills|runtime|commands)/[A-Za-z0-9_./-]*[A-Za-z0-9_-]\.(?:md|py|json))")
USING_BROTHER_REFS_DIR = os.path.join(
    BUNDLE_DIR, "skills", "using-brother", "references")


def shipped_prose_files():
    """Every .md under bundle/, plus bundle/MANIFEST.json, sorted.

    A missing or unreadable bundle/ is not silently an empty scan: the caller
    asserts the list is non-empty, so an empty result FAILS rather than passing
    a check that read nothing.
    """
    found = []
    for root, dirs, names in os.walk(BUNDLE_DIR):
        dirs.sort()
        for name in sorted(names):
            if name.endswith(".md") or name == "MANIFEST.json":
                found.append(os.path.join(root, name))
    return sorted(found)


def using_brother_reference_files():
    """Every .md under bundle/skills/using-brother/references/, sorted."""
    if not os.path.isdir(USING_BROTHER_REFS_DIR):
        return []
    return sorted(
        os.path.join(USING_BROTHER_REFS_DIR, name)
        for name in os.listdir(USING_BROTHER_REFS_DIR)
        if name.endswith(".md"))


class ShippedSurfaceIsPortable(unittest.TestCase):
    def test_scan_actually_reads_files(self):
        """A scan over nothing is not evidence of anything."""
        files = shipped_prose_files()
        self.assertTrue(files, "no shipped prose found under %s" % BUNDLE_DIR)
        skill = os.path.join(BUNDLE_DIR, "skills", "using-brother", "SKILL.md")
        self.assertIn(skill, files, "the file E43 was written about is not scanned")

    def test_no_machine_specific_instruction(self):
        hits = []
        for path in shipped_prose_files():
            try:
                with io.open(path, encoding="utf-8") as fh:
                    lines = fh.read().splitlines()
            except (OSError, UnicodeDecodeError) as exc:
                self.fail("cannot read shipped file %s: %s" % (path, exc))
            for number, line in enumerate(lines, 1):
                for label, pattern in FORBIDDEN:
                    if pattern.search(line):
                        hits.append("%s:%d: %s" % (
                            os.path.relpath(path, REPO_ROOT), number, label))
        self.assertEqual([], hits, "shipped surface carries machine-specific "
                         "or founder-only instruction:\n  " + "\n  ".join(hits))

    def test_using_brother_reference_scripts_resolve_in_the_shipped_tree(self):
        """A path an installed session is told to run must exist once shipped."""
        files = using_brother_reference_files()
        self.assertTrue(files, "no references/*.md found under %s"
                        % USING_BROTHER_REFS_DIR)
        hits = []
        for path in files:
            try:
                with io.open(path, encoding="utf-8") as fh:
                    lines = fh.read().splitlines()
            except (OSError, UnicodeDecodeError) as exc:
                self.fail("cannot read shipped file %s: %s" % (path, exc))
            for number, line in enumerate(lines, 1):
                for name in SCRIPTS_PY_REF.findall(line):
                    if not os.path.isfile(os.path.join(RUNTIME_DIR, name)):
                        hits.append("%s:%d: scripts/%s does not ship (no "
                                   "bundle/runtime/%s)" % (
                                       os.path.relpath(path, REPO_ROOT),
                                       number, name, name))
        self.assertEqual([], hits, "using-brother references a script that "
                         "does not ship:\n  " + "\n  ".join(hits))

    def test_the_door_defines_its_launcher_by_a_path_an_install_carries(self):
        """The door's first sentence defines `brother-run`, which every later
        step runs. Until 2026-10-05 it defined it by pointing at
        docs/maintainer/BROTHER-MAINTAINER-VERBS.md, a file no install carries
        (bundle/ ships no docs/), so an installed session had to guess the
        launcher. The definition spells the launcher under each host's plugin
        root, and that launcher ships."""
        door = os.path.join(BUNDLE_DIR, "commands", "brother.md")
        try:
            with io.open(door, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            self.fail("cannot read the door %s: %s" % (door, exc))
        definition = [line for line in lines if "`brother-run` below" in line]
        self.assertEqual(1, len(definition), "the door defines `brother-run` "
                         "exactly once, found %d definition line(s)"
                         % len(definition))
        for variable in ("CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT",
                         "BROTHER_PLUGIN_ROOT"):
            self.assertIn('"${%s}/runtime/brother-run"' % variable,
                          definition[0], "the door's launcher definition "
                          "does not spell it under %s" % variable)
        self.assertNotIn("docs/maintainer", definition[0], "the door defines "
                         "its launcher by a maintainer document no install "
                         "carries")
        self.assertTrue(os.path.isfile(os.path.join(RUNTIME_DIR, "brother-run")),
                        "the launcher the door names does not ship (no "
                        "bundle/runtime/brother-run)")

    def numbered(self, path):
        """[(line number, line)] of a shipped file; unreadable is a FAIL."""
        try:
            with io.open(path, encoding="utf-8") as fh:
                return list(enumerate(fh.read().splitlines(), 1))
        except (OSError, UnicodeDecodeError) as exc:
            self.fail("cannot read shipped file %s: %s" % (path, exc))

    def test_the_door_speaks_of_its_launcher_by_name_on_one_line_only(self):
        """A SECOND definition is a contradiction no session can settle.
        Review of 2026-10-06: a line defining `brother-run` another way
        passed, because only the first definition's wording was counted.
        Every later step USES the launcher, the name followed by its
        arguments (`brother-run --continue ...`). The bare name, closed by
        its backtick, is how a sentence DEFINES it, so it appears on the
        definition line and on no other."""
        lines = self.numbered(os.path.join(BUNDLE_DIR, "commands", "brother.md"))
        named = [n for n, line in lines if "`brother-run`" in line]
        defined = [n for n, line in lines if "`brother-run` below" in line]
        self.assertEqual(1, len(defined), "the door defines `brother-run` on "
                         "%d line(s), exactly one is owed" % len(defined))
        self.assertEqual(defined, named, "the door speaks of `brother-run` by "
                         "its bare name on line(s) %s, and its one definition "
                         "is on line(s) %s: any other line is a second "
                         "definition" % (named, defined))

    def test_the_door_names_no_path_under_bundle(self):
        """bundle/ IS the plugin root of an install, so a path spelled
        bundle/... exists in a checkout of the repository and nowhere else."""
        hits = ["%d: %s" % (n, line.strip()[:110]) for n, line
                in self.numbered(os.path.join(BUNDLE_DIR, "commands", "brother.md"))
                if BUNDLE_PREFIXED.search(line)]
        self.assertEqual([], hits, "the door names a path under bundle/, which "
                         "no install carries:\n  " + "\n  ".join(hits))

    def test_every_plugin_path_the_door_and_its_references_name_ships(self):
        """THE INSTALL ONLY CHECK: a path spelled from the plugin root
        (skills/..., runtime/..., commands/...), bare or behind a plugin root
        variable, is opened or run by an installed session, so it exists
        under bundle/, which is all an install carries."""
        files = [os.path.join(BUNDLE_DIR, "commands", "brother.md")] + using_brother_reference_files()
        named, hits = 0, []
        for path in files:
            for n, line in self.numbered(path):
                for rel in PLUGIN_PATH.findall(line):
                    named += 1
                    if not os.path.isfile(os.path.join(BUNDLE_DIR, rel)):
                        hits.append("%s:%d: %s does not ship (no bundle/%s)" % (
                            os.path.relpath(path, REPO_ROOT), n, rel, rel))
        self.assertGreaterEqual(named, 4, "the scan found %d plugin path(s) in "
                                "the door and its references; a scan that "
                                "reads nothing proves nothing" % named)
        self.assertEqual([], hits, "a path an installed session is told to "
                         "open or run does not ship:\n  " + "\n  ".join(hits))

    def test_a_scripts_path_in_the_references_is_only_the_checkout_spelling(self):
        """WHY intake.md's `scripts/annotations_store.py add <record>.json`
        slipped through until 2026-10-06: the check above it reads only a
        mention that follows the word python3, and accepts it whenever the
        mirror exists, so a bare `scripts/<name>.py` an install cannot open
        passed twice over. No install carries scripts/. A scripts/ path may
        stand in a reference only as the stated checkout spelling (the SAME
        line says "checkout"); the path an install opens is spelled from the
        plugin root and is held to shipping by the check above. A GENERATED
        line is provenance for a maintainer, never an instruction."""
        files = using_brother_reference_files()
        self.assertTrue(files, "no references/*.md found under %s"
                        % USING_BROTHER_REFS_DIR)
        seen, hits = 0, []
        for path in files:
            for n, line in self.numbered(path):
                if "GENERATED" in line:
                    continue
                for name in SCRIPTS_PATH.findall(line):
                    seen += 1
                    if "checkout" not in line:
                        hits.append("%s:%d: scripts/%s is named as if an install "
                                    "carried it" % (os.path.relpath(path, REPO_ROOT), n, name))
        self.assertGreaterEqual(seen, 1, "the scan found no scripts/ mention at "
                                "all; a scan that reads nothing proves nothing")
        self.assertEqual([], hits, "using-brother references a scripts/ path "
                         "an install cannot open:\n  " + "\n  ".join(hits))

    def test_the_launcher_ships_executable(self):
        """The door runs "<plugin root>/runtime/brother-run" directly, so a
        launcher without its executable bit is a launcher that does not
        start. scripts/bundle_runtime.py sets the bit when it writes the
        file (os.chmod 0o755); its --check compares bytes and would not
        notice the bit lost, so this does."""
        launcher = os.path.join(RUNTIME_DIR, "brother-run")
        self.assertTrue(os.path.isfile(launcher), "the launcher does not ship")
        self.assertTrue(os.access(launcher, os.X_OK), "the shipped launcher "
                        "%s is not executable" % launcher)

    def schema_named_where_it_ships(self, name):
        text = "\n".join(line for _n, line in
                         self.numbered(os.path.join(USING_BROTHER_REFS_DIR, name)))
        self.assertIn("`runtime/outcome-contract-v1.json`", text, "references/"
                      "%s does not name the schema where it ships" % name)
        self.assertTrue(
            os.path.isfile(os.path.join(RUNTIME_DIR, "outcome-contract-v1.json")),
            "the schema references/%s names does not ship (no "
            "bundle/runtime/outcome-contract-v1.json)" % name)

    def test_the_intake_reference_names_the_schema_where_it_ships(self):
        """references/intake.md named the contract schema only at
        docs/schema/outcome-contract-v1.json, which no install carries; it
        ships at runtime/outcome-contract-v1.json."""
        self.schema_named_where_it_ships("intake.md")

    def test_the_router_reference_names_the_schema_where_it_ships(self):
        """references/router-details.md carried the same docs/schema/ only
        mention and was corrected with intake.md, but nothing read it: the
        review of 2026-10-06 reverted it and every test stayed green."""
        self.schema_named_where_it_ships("router-details.md")

    def test_the_maintainer_document_holds_the_cut_block(self):
        """The block was MOVED, not deleted: losing it is its own defect."""
        # MOVED 2026-09-10, and the move is the point. The 2026-09-10
        # documentation replacement made docs/how-to the PUBLIC how-to
        # corpus and gave it a directory entry in the export allowlist, so
        # a maintainer-only page left there would have been published on
        # the next export. This page says of itself that it is "NOT a
        # shipped instruction" and "names tools and paths that exist on
        # this estate and on no installed machine", so it now lives beside
        # the other maintainer document, under docs/maintainer, which no
        # allowlist entry carries.
        doc = os.path.join(REPO_ROOT, "docs", "maintainer",
                           "MAINTAINER-CLOSING-CEREMONY.md")
        # docs/maintainer is never exported, so in the public tree there is no document to judge. The hub's
        # edition marker (tracked in the hub, a hard exclude of every export) tells the two apart: where it is
        # present the document MUST exist, where it is absent this case has nothing to read and says so.
        if not os.path.exists(os.path.join(REPO_ROOT, ".brother-edition")):
            self.skipTest("public export: docs/maintainer is not shipped here, so there is no maintainer "
                          "document to judge; the hub tree, which carries .brother-edition, runs this case")
        self.assertTrue(os.path.exists(doc), "maintainer document missing: " + doc)
        try:
            with io.open(doc, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            self.fail("cannot read %s: %s" % (doc, exc))
        for needle in ("vault-tools", "bm_vault_catalog.py", "FOUNDER ORDER"):
            self.assertIn(needle, text,
                          "maintainer document lost %r from the cut block" % needle)


if __name__ == "__main__":
    unittest.main()
