#!/usr/bin/env python3
"""S9's frozen harness: the owner's H1 activation, proven by the first product landing's manifest.

Written by the session before the unit is built and never by its builder (HARN1). S9's builder is the
owner's own hand (his brother-approve code activates H1), so this file cannot be his, and the spec says
so in as many words: "the frozen harness: written by the session, red first, before the code is built,
never by its builder".

RED ON THE BASE BY AN ASSERTION, never by an import error and never by a skip. There is no record on the
base, so judge() answers NO-DATA and the deliverable case fails on that answer. Nothing here imports the
record, calls a symbol that does not exist, or reaches the network.

WHY THE JUDGE LIVES IN THE HARNESS. S9 ships exactly one new file,
docs/plan/specs/1.1.9/H1-ACTIVATION.json. The record IS the deliverable, so there is no module to import
and the record's contract has to be stated somewhere first. It is stated here, before any record exists,
rather than inferred later from whatever the first record happens to contain.

THE CONTRACT this harness holds the record to:

    {"approval": "<the owner's own words activating H1>",
     "commit": "<FIRST1's landed commit, full or abbreviated sha>",
     "verify_install_exit": 0,
     "products": {
        "brothermode": {"manifest_sha256": "<sha256 of products/brothermode/CHECKSUMS.sha256 AT commit>",
                        "regenerated_sha256": "<sha256 of a fresh scripts/checksums.sh run at landing>"},
        "brothersbe":  {...}}}

manifest_sha256 is what git holds at that commit; regenerated_sha256 is what the lander computed fresh.
Recording BOTH is the whole point: one number alone cannot say whether the landing regenerated anything.

THE SUBJECT IS THE DELIVERABLE, NOT A FIXTURE (the 2026-09-21 delivery law, after a unit closed on a test
that built its own 70 row catalogue while the real file held 10). test_the_first_landing_regenerated_its_
manifest reads the REAL record path and nothing else; it is the case that closes S9. The fixture cases
beside it exist only to prove each guard can refuse, which is what makes the spec's mutation catchable,
and they are named so no reader mistakes one for the other.

FIVE GUARDS, one fixture each, so no guard can hide behind a sibling (the 2026-09-21 rule: a fixture that
trips two guards proves neither):
  G1 a record that is absent, unreadable, or not a JSON object       -> NO-DATA
  G2 an empty or missing approval line                              -> FAIL
  G3 a commit that is not an ancestor of this branch                 -> FAIL
  G4 a recorded manifest_sha256 that is not what git holds there     -> FAIL
  G5 regenerated_sha256 != manifest_sha256, or a non zero
     verify_install_exit                                            -> FAIL   (M-S9A-STALE-OK aims here)

Done check: python3 -B scripts/test_h1_activation.py Activated
"""
import hashlib
import json
import os
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RECORD = os.path.join(ROOT, "docs/plan/specs/1.1.9/H1-ACTIVATION.json")
NODATA, FAIL, PASS = "NO-DATA", "FAIL", "PASS"

# Every product that carries a manifest in this tree. brotherds has no checksums.sh and no manifest, so
# requiring it would refuse a correct record. Read from disk, never typed in, so a fourth product that
# gains a manifest is covered without touching this frozen file.
def manifest_products(root):
    base = os.path.join(root, "products")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return None
    return [n for n in names if os.path.isfile(os.path.join(base, n, "CHECKSUMS.sha256"))]


def load_record(path):
    """The record as a dict, or None for anything that is not a readable JSON object.

    Hostile inputs answer None rather than raising: None, an empty or blank string, a number, a path
    outside the tree, a directory, a truncated file, a JSON list. A reader that raises on junk turns a
    refusal into an error, and an error is red for the wrong reason."""
    if not isinstance(path, str) or not path.strip():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError, TypeError, UnicodeDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def blob_sha256(root, commit, rel):
    """sha256 of what git holds at <commit>:<rel>, or None when it holds nothing readable there."""
    try:
        out = subprocess.run(["git", "-C", root, "show", "%s:%s" % (commit, rel)],
                             capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return hashlib.sha256(out.stdout).hexdigest()


def is_ancestor(root, commit):
    """True when the commit is reachable from HEAD, False when it is not, None when git cannot answer.
    An unanswerable git is never the safe case: judge() turns None into NO-DATA, not PASS."""
    if not isinstance(commit, str) or not commit.strip():
        return False
    try:
        out = subprocess.run(["git", "-C", root, "merge-base", "--is-ancestor", commit, "HEAD"],
                             capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode == 0:
        return True
    if out.returncode == 1:
        return False
    return None


def judge(rec, root, products, sha_at=blob_sha256, ancestor=is_ancestor):
    """(verdict, reason) for one activation record. PASS only when every guard is satisfied.

    sha_at and ancestor are arguments so a fixture can isolate one guard without a git repository, and so
    the deliverable case runs against the real git. Nothing here writes."""
    if not isinstance(rec, dict):
        return NODATA, "no readable H1 activation record at %s" % os.path.relpath(RECORD, ROOT)
    if products is None:
        return NODATA, "products/ could not be listed, so no manifest can be judged"
    if not products:
        return NODATA, "no product in this tree carries a CHECKSUMS.sha256"

    approval = rec.get("approval")
    if not isinstance(approval, str) or not approval.strip():
        return FAIL, "the record carries no approval line, so nothing says the owner activated H1"

    commit = rec.get("commit")
    reach = ancestor(root, commit)
    if reach is None:
        return NODATA, "git could not say whether %r is on this line" % (commit,)
    if not reach:
        return FAIL, "the recorded commit %r is not an ancestor of HEAD" % (commit,)

    got = rec.get("products")
    if not isinstance(got, dict):
        return FAIL, "the record names no products, so no manifest is covered"
    missing = [p for p in products if p not in got]
    if missing:
        return FAIL, "the record covers no manifest for %s" % ", ".join(missing)

    exit_code = rec.get("verify_install_exit")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool) or exit_code != 0:
        return FAIL, "verify_install_exit reads %r, not 0" % (exit_code,)

    for product in products:
        entry = got.get(product)
        if not isinstance(entry, dict):
            return FAIL, "the %s entry is not an object" % product
        claimed = entry.get("manifest_sha256")
        fresh = entry.get("regenerated_sha256")
        for name, value in (("manifest_sha256", claimed), ("regenerated_sha256", fresh)):
            if not isinstance(value, str) or len(value) != 64:
                return FAIL, "%s's %s is not a sha256: %r" % (product, name, value)
        rel = "products/%s/CHECKSUMS.sha256" % product
        in_git = sha_at(root, commit, rel)
        if in_git is None:
            return NODATA, "git holds nothing readable at %s:%s" % (commit, rel)
        if claimed != in_git:
            return FAIL, ("%s's recorded manifest_sha256 is not what %s holds at %s"
                          % (product, commit, rel))
        if fresh != claimed:
            return FAIL, ("%s's manifest was not regenerated by that landing: a fresh run reads %s, the "
                          "landed manifest reads %s" % (product, fresh[:12], claimed[:12]))
    return PASS, "H1 activated on %r; every product manifest was regenerated by %s" % (approval[:40],
                                                                                       commit)


def _record(**over):
    """A fixture record that PASSES, so each case below changes exactly one field."""
    sha = "a" * 64
    rec = {"approval": "the owner activated H1", "commit": "deadbee", "verify_install_exit": 0,
           "products": {"brothermode": {"manifest_sha256": sha, "regenerated_sha256": sha}}}
    rec.update(over)
    return rec


class Activated(unittest.TestCase):
    """S9.a. The deliverable case is the first one; every other case is a guard's own fixture."""

    # REQ-S9A-01, the case that closes S9: the real record, the real git, nothing synthetic.
    def test_the_first_landing_regenerated_its_manifest(self):
        verdict, reason = judge(load_record(RECORD), ROOT, manifest_products(ROOT))
        self.assertEqual(PASS, verdict, reason)

    # G1, one fixture per hostile input: a reader that raises would be an error, not a refusal.
    def test_a_record_that_cannot_be_read_is_no_data(self):
        for bad in (None, "", "   ", 0, 1.5, [], {}, "/etc/hosts-does-not-exist",
                    os.path.join(ROOT, "docs"), __file__):
            self.assertIsNone(load_record(bad), "load_record(%r) should answer None" % (bad,))
        self.assertEqual(NODATA, judge(None, ROOT, ["brothermode"])[0])
        self.assertEqual(NODATA, judge([], ROOT, ["brothermode"])[0])

    # G2
    def test_fixture_an_empty_approval_line_refuses(self):
        for value in (None, "", "  ", 7):
            got = judge(_record(approval=value), ROOT, ["brothermode"], sha_at=lambda *a: "a" * 64,
                        ancestor=lambda *a: True)
            self.assertEqual(FAIL, got[0], "approval=%r should refuse, got %s" % (value, got))

    # G3
    def test_fixture_a_commit_off_this_line_refuses(self):
        got = judge(_record(), ROOT, ["brothermode"], sha_at=lambda *a: "a" * 64,
                    ancestor=lambda *a: False)
        self.assertEqual(FAIL, got[0], got[1])
        unknown = judge(_record(), ROOT, ["brothermode"], sha_at=lambda *a: "a" * 64,
                        ancestor=lambda *a: None)
        self.assertEqual(NODATA, unknown[0], unknown[1])

    # G4
    def test_fixture_a_manifest_sha_git_does_not_hold_refuses(self):
        got = judge(_record(), ROOT, ["brothermode"], sha_at=lambda *a: "b" * 64,
                    ancestor=lambda *a: True)
        self.assertEqual(FAIL, got[0], got[1])
        absent = judge(_record(), ROOT, ["brothermode"], sha_at=lambda *a: None,
                       ancestor=lambda *a: True)
        self.assertEqual(NODATA, absent[0], absent[1])

    # G5, the guard M-S9A-STALE-OK deletes. Isolated: the commit is on the line and the claimed manifest
    # is what git holds, so only the fresh-versus-landed comparison can refuse this record.
    def test_fixture_a_stale_manifest_refuses(self):
        stale = _record()
        stale["products"]["brothermode"]["regenerated_sha256"] = "c" * 64
        got = judge(stale, ROOT, ["brothermode"], sha_at=lambda *a: "a" * 64, ancestor=lambda *a: True)
        self.assertEqual(FAIL, got[0], "a landing that did not regenerate its manifest must refuse")
        self.assertIn("not regenerated", got[1])

    # G5's sibling, its own fixture: the exit code, not the manifest.
    def test_fixture_a_failed_verify_install_refuses(self):
        for value in (1, -1, "0", None, True, 0.0):
            got = judge(_record(verify_install_exit=value), ROOT, ["brothermode"],
                        sha_at=lambda *a: "a" * 64, ancestor=lambda *a: True)
            self.assertEqual(FAIL, got[0], "verify_install_exit=%r should refuse" % (value,))

    # A record that covers only some products is not a pass: the whole product class is the subject.
    def test_fixture_a_missing_product_refuses(self):
        got = judge(_record(), ROOT, ["brothermode", "brothersbe"], sha_at=lambda *a: "a" * 64,
                    ancestor=lambda *a: True)
        self.assertEqual(FAIL, got[0], got[1])
        self.assertIn("brothersbe", got[1])
        # The reason matters, not only the verdict: without this guard the per product entry check
        # below refuses the same record with a message about an entry that is not an object, which
        # also names the product, so the verdict alone cannot tell the two apart.
        self.assertIn("covers no manifest", got[1])

    # The shape of a recorded sha, isolated: git is made to answer the same malformed value, so the
    # comparison against git cannot refuse and only the shape guard is left.
    def test_fixture_a_sha_that_is_not_a_sha256_refuses(self):
        for value in ("abc", "", None, 64, "A" * 63, "a" * 65, ["a" * 64]):
            for field in ("manifest_sha256", "regenerated_sha256"):
                rec = _record()
                rec["products"]["brothermode"]["manifest_sha256"] = value
                rec["products"]["brothermode"]["regenerated_sha256"] = value
                got = judge(rec, ROOT, ["brothermode"], sha_at=lambda *a: value,
                            ancestor=lambda *a: True)
                self.assertEqual(FAIL, got[0], "%s=%r should refuse, got %s" % (field, value, got))
                self.assertIn("not a sha256", got[1])

    # The fixture itself must be able to pass, or every case above proves only that judge() says FAIL.
    def test_fixture_a_complete_record_passes(self):
        got = judge(_record(), ROOT, ["brothermode"], sha_at=lambda *a: "a" * 64,
                    ancestor=lambda *a: True)
        self.assertEqual(PASS, got[0], got[1])


if __name__ == "__main__":
    unittest.main(verbosity=1)
