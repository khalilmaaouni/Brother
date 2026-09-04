#!/usr/bin/env python3
"""Is this tree the private hub, or a published subset of it?

WHY THIS MODULE EXISTS
  The product's own suites read files. Some of those files are HUB RECORDS
  that scripts/export_public.py deliberately withholds: a CI wiring file, an
  internal queue, a specification the export does not publish, the generated
  runtime adapters. In a clone of the published release those checks do not
  fail, they have NOTHING TO READ, and the two are not the same answer.

  Measured on a rebuilt export tree, 2026-09-04: seven suites exited 1
  (test_bm_release_invariant, test_bm_e2e_pins, test_bm_idle,
  test_bm_ci_context, test_bm_runtimes, test_bm_schema, test_bm), every one
  of them on a path or ref the export withholds on purpose. A withheld file
  reported as FAIL tells a newcomer the product is broken; it is not.

  The answer lived twice already, once in tools/test_all.py and once in
  tools/test_bm_docs.py. Nine copies of one predicate is how the copies
  drift, so it lives here once and the suites import it.

THE DISCRIMINATOR
  `editions` at the REPOSITORY root. scripts/export_public.py HARD_EXCLUDEs
  that directory in its own code, not by a list entry, so no export can ever
  carry it and every hub checkout tracks it. Its absence therefore means
  "this is a public export", and nothing else can turn it off by accident.

WHAT THIS DELIBERATELY DOES NOT DO
  It does not make a missing file a skip everywhere. In the hub every one of
  these checks still FAILS on an absent path, because there the path is
  supposed to be on disk and its disappearance is the exact defect the suite
  exists to catch. NO-DATA is never a pass: the skip reason names every
  absent item so a run says which claims it could not test.

Standard library only. Python 3.9. Reads directory entries, writes none.
No em or en dashes anywhere in this file or its output.
"""
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
# tools -> products/brothermode
PRODUCT_ROOT = os.path.dirname(HERE)
# products/brothermode -> products -> the repository root
REPO_ROOT = os.path.dirname(os.path.dirname(PRODUCT_ROOT))

HUB_MARKER = "editions"

NO_DATA_SENTENCE = (
    "NO-DATA: this checkout does not carry %s, so this check has nothing "
    "to read. This is not the private hub, so they are treated as "
    "withheld by the export rather than deleted. NO-DATA is not a pass.")


def in_private_hub(repo_root=None):
    """True when this tree is the private hub, false when it is an export.

    Reads the marker on EVERY call rather than caching it at import: a test
    that drives the seam the other way passes a tree without the marker and
    gets the export answer, with no global to restore afterwards. The default
    is resolved INSIDE the body, never as a default argument value, so a test
    that reassigns REPO_ROOT is actually obeyed."""
    root = REPO_ROOT if repo_root is None else repo_root
    return os.path.isdir(os.path.join(root, HUB_MARKER))


def absent_here(rels, root=None):
    """The paths among `rels` this tree does not carry, in the order given.

    `rels` are relative to the product root unless `root` says otherwise."""
    base = PRODUCT_ROOT if root is None else root
    return [r for r in rels if not os.path.exists(os.path.join(base, r))]


def no_data_outside_the_hub(rels, root=None, repo_root=None):
    """Report NO-DATA, rather than fail, for paths this tree does not carry.

    Only outside the private hub: see the module docstring for why that
    condition is the whole point. Raises unittest.SkipTest DIRECTLY rather
    than calling a TestCase's skipTest, because the callers are not all test
    bodies: two of the seven suites read their withheld file in setUp and one
    in setUpClass, where no bound instance exists to call skipTest on, and
    unittest handles the raised exception identically in all three.

    Returns the absent paths when it does not skip, so a caller can branch on
    them; a caller that reaches the next line is in the hub or missing
    nothing."""
    missing = absent_here(rels, root)
    if not missing or in_private_hub(repo_root):
        return missing
    raise unittest.SkipTest(NO_DATA_SENTENCE % ", ".join(missing))


def no_data_for_absent_names(names, repo_root=None):
    """The same NO-DATA, for things that are not files on disk.

    One caller today: a git REF. scripts/export_public.py copies FILES into a
    fresh tree, so an export carries no hub history at all and the hub's own
    release tags cannot resolve there. That absence is withheld history, the
    same class as a withheld file, and it reads the same sentence."""
    names = list(names)
    if not names or in_private_hub(repo_root):
        return names
    raise unittest.SkipTest(NO_DATA_SENTENCE % ", ".join(names))
