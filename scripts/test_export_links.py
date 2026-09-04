"""test_export_links: every relative markdown link in the REAL export tree
must resolve inside it.

The exporter already owns the rule (export_public.check_markdown_links) and
runs it at tag time, over a tree it builds and throws away. Nothing ran it
before that: the 1.0.2 tag was refused by 39 dead links, all of them pointing
at pages docs/plan/EXPORT-ALLOWLIST.txt deliberately withholds, and the only
way to see them was to attempt the release. This builds the same tree from
the same allowlist and reads the same verdict, so the refusal arrives at
merge time instead.

It is deliberately an INTEGRATION check over the hub's own files, not a
fixture: scripts/test_export_public.py already drives check_markdown_links
backwards over fixture trees, and a second fixture suite would restate that
without ever looking at the pages this repository actually ships.

Driven backwards the only way a whole-tree check can be: the negative case
re-adds one withheld link to a COPY of the export tree (never to the hub) and
asserts the check refuses it and names it. A checker that has only ever been
seen green is a claim, not a check.
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import export_public as EP  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

#: A path the allowlist withholds on purpose (internal strategy, named in the
#: allowlist's own M6 exclusion note). Linking at it is the exact defect this
#: check exists to catch, so the negative case plants this one.
WITHHELD = "PRODUCT-DIRECTION.md"


def _build_tree(dest):
    """The candidate export tree, built from the hub's own allowlist the way
    the exporter builds it. Returns the tree path. Raises rather than
    returning an empty tree: an allowlist this checker cannot read is
    NO-DATA, never a pass."""
    allowlist = EP.load_allowlist()
    if not allowlist:
        raise RuntimeError("NO-DATA: %s is missing or empty, so no export "
                           "tree could be built" % EP.DEFAULT_ALLOWLIST)
    tree = os.path.join(dest, "export")
    os.makedirs(tree)
    copied = EP.build_export_tree(tree, allowlist, root=EP.ROOT)
    if not copied:
        raise RuntimeError("NO-DATA: the allowlist contributed no files, so "
                           "there is nothing to check")
    return tree


class ExportTreeLinks(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="export-links-")
        try:
            cls.tree = _build_tree(cls.tmp)
        except (RuntimeError, OSError):
            shutil.rmtree(cls.tmp, ignore_errors=True)
            raise

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_no_dead_links_in_the_export_tree(self):
        """Zero dead links, and nothing weaker: a summary line reporting
        dead links while the boolean says ok would pass this too, so both
        are asserted."""
        ok, lines = EP.check_markdown_links(self.tree)
        summary = lines[-1]
        self.assertTrue(ok, "the export tree carries dead relative links:\n"
                            + "\n".join(lines))
        self.assertIn(", 0 dead", summary, summary)

    def test_a_withheld_link_is_caught(self):
        """The negative half, on a COPY: re-add one link at a page the
        allowlist withholds and the same check must refuse and name it."""
        probe = os.path.join(self.tmp, "probe")
        shutil.rmtree(probe, ignore_errors=True)
        shutil.copytree(self.tree, probe)
        planted = os.path.join(probe, "products", "brothermode", "SKILL.md")
        self.assertTrue(os.path.isfile(planted), planted)
        with open(planted, "a", encoding="utf-8") as fh:
            fh.write("\n\n[direction](%s)\n" % WITHHELD)
        ok, lines = EP.check_markdown_links(probe)
        self.assertFalse(ok, "a link at a withheld page was not caught:\n"
                             + "\n".join(lines))
        self.assertTrue(any(WITHHELD in line and "dead link" in line
                            for line in lines), "\n".join(lines))


if __name__ == "__main__":
    unittest.main(verbosity=2)
