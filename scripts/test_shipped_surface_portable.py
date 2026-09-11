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
