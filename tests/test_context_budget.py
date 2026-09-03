"""SR-07: a context budget for the always-loaded surface, with a test behind it.

WHY THIS FILE EXISTS. Every product in Brother injects a digest into every
session before anyone asks a question, so that text is paid for on every single
turn whether it is used or not. Nothing measured it and nothing capped it. This
estate's own law is that a rule is not a control unless a file enforces it, so
the budget lives here rather than in prose alone.

WHAT IT ASSERTS, and what it deliberately does not. It asserts BYTES, because
bytes are deterministic and reproducible on any machine. The token figures in
docs/plan/SR-07-CONTEXT-BUDGET-2026-08-24.md are an ESTIMATE at roughly four
bytes per token and no tokenizer is run here; asserting an estimate would be
inventing precision the measurement does not have.

THE LEAF SURFACES ARE MEASURED FROM WHAT THE REPOSITORY SHIPS, at
products/brothermode/SKILL.md and products/brothersbe/SKILL.md, never from an
installed, machine-specific path (a skills-clone layout or a pinned plugin
cache version). A test pointed at an installed path reports on the machine
rather than on the product, and drifts stale the moment the installed version
moves; a tree path runs the same on any checkout. An absent leaf (a broken or
partial checkout) is still NO-DATA and skips, naming the path it looked for.
NO-DATA is not a pass and it is not a block.
"""
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Budgets in BYTES. Measured 2026-08-24 at 21:15 +07, then given headroom:
# Brother 3,319 actual against 6,000; the two leaves 14,094 and 17,233 against
# 20,000 each; combined 34,646 actual against 46,000. The headroom is roughly a
# third, enough for real growth and not enough to hide a doubling.
BUDGET_BROTHER = 6000
BUDGET_LEAF = 20000
BUDGET_COMBINED = 46000

BROTHER_SKILL = REPO_ROOT / "bundle" / "skills" / "using-brother" / "SKILL.md"
LEAVES = {
    "brothermode": REPO_ROOT / "products" / "brothermode" / "SKILL.md",
    "brothersbe": REPO_ROOT / "products" / "brothersbe" / "SKILL.md",
}


def size_of(path):
    """Bytes on disk, or None when the file is not there."""
    try:
        with path.open("rb") as fh:
            return len(fh.read())
    except OSError:  # sbe: allow-silent every caller below checks for None and skips or reports NO-DATA
        return None


class TestBrotherOwnSurface(unittest.TestCase):
    """Brother's own bootstrap skill is IN this repository, so it always checks."""

    def test_bootstrap_skill_exists(self):
        self.assertIsNotNone(
            size_of(BROTHER_SKILL),
            "the bootstrap skill is missing at %s; decision 13 of 2026-08-22 "
            "puts it there" % BROTHER_SKILL,
        )

    def test_within_budget(self):
        n = size_of(BROTHER_SKILL)
        if n is None:
            self.skipTest("NO-DATA: no bootstrap skill at %s" % BROTHER_SKILL)
        self.assertLessEqual(
            n, BUDGET_BROTHER,
            "Brother's always-loaded surface is %d bytes against a budget of %d. "
            "Every session pays this before anyone asks a question. Either cut "
            "it or raise the budget in a change that says why." % (n, BUDGET_BROTHER),
        )


class TestLeafSurfaces(unittest.TestCase):
    """The leaves are installed outside this repository, so absence is NO-DATA."""

    def test_each_leaf_within_budget(self):
        checked = 0
        for name, path in LEAVES.items():
            n = size_of(path)
            if n is None:
                continue
            checked += 1
            self.assertLessEqual(
                n, BUDGET_LEAF,
                "%s injects %d bytes against a budget of %d" % (name, n, BUDGET_LEAF),
            )
        if checked == 0:
            self.skipTest(
                "NO-DATA: neither leaf is installed on this machine. Looked for: %s"
                % ", ".join(str(p) for p in LEAVES.values())
            )


class TestCombinedSurface(unittest.TestCase):
    """What a merged Brother would inject on every turn, once code actually moves."""

    def test_combined_within_budget(self):
        sizes = {"brother": size_of(BROTHER_SKILL)}
        for name, path in LEAVES.items():
            sizes[name] = size_of(path)
        missing = [k for k, v in sizes.items() if v is None]
        if missing:
            self.skipTest(
                "NO-DATA: cannot total a surface with a missing part. Absent: %s"
                % ", ".join(sorted(missing))
            )
        total = sum(sizes.values())
        self.assertLessEqual(
            total, BUDGET_COMBINED,
            "the combined always-loaded surface is %d bytes against a budget of "
            "%d. Breakdown: %s" % (
                total, BUDGET_COMBINED,
                ", ".join("%s %d" % (k, v) for k, v in sorted(sizes.items()))),
        )


if __name__ == "__main__":
    unittest.main()
