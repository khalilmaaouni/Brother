"""receipt_check: a source with no receipt is UNVERIFIED, and a source that
claims a receipt is checked against the thing it names.

docs/plan/intake-drafter-brief.md states the rule in prose: a citation with
no receipt id renders an UNVERIFIED pill. This is the mechanical check
behind that pill, so "renders UNVERIFIED" is never just a claim the page
happens to make. Three receipt shapes, each resolved against something that
actually exists on disk, never against the string alone:

  file:<path>:<start>-<end>   the path exists relative to the repo root and
                               the line range is inside the file's own length
  evidence:<name>              ~/.claude/evidence/<name> exists
  url:<url>                    RESOLVED only if a captured file
                               ~/.claude/evidence/<sha1 of the url>.txt
                               exists; a bare url with nothing captured is
                               still UNVERIFIED, the same as no receipt at all

A missing or empty receipt, or one in a scheme this checker does not know,
is UNVERIFIED with a reason naming why. Nothing here ever upgrades a source
to RESOLVED on the strength of the string looking right.

Python 3, standard library only. No network.
"""
import argparse
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RESOLVED = "RESOLVED"
UNVERIFIED = "UNVERIFIED"
NODATA = "NO-DATA"


def resolve_receipt(receipt, root, home):
    """(status, reason). RESOLVED or UNVERIFIED, never anything else, and
    never a crash: a malformed receipt is UNVERIFIED with a reason, exactly
    like a missing one, because a caller renders the same pill for both."""
    receipt = (receipt or "").strip()
    if not receipt:
        return UNVERIFIED, "no receipt field"

    if receipt.startswith("file:"):
        body = receipt[len("file:"):]
        path_part, sep, range_part = body.rpartition(":")
        if not sep or not path_part:
            return UNVERIFIED, "malformed file receipt, expected file:<path>:<start>-<end>"
        full = os.path.join(root, path_part)
        if not os.path.isfile(full):
            return UNVERIFIED, "%s does not exist under the repo root" % path_part
        first, _, last = range_part.partition("-")
        try:
            start = int(first)
            end = int(last) if last else start
        except ValueError:
            return UNVERIFIED, "the range %r is not <start>-<end>" % range_part
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                nlines = sum(1 for _ in fh)
        except OSError as exc:
            return UNVERIFIED, "%s could not be read: %s" % (path_part, exc)
        if start < 1 or start > nlines or end < start or end > nlines:
            return UNVERIFIED, ("%s has %d line(s); the range %s does not fit"
                                 % (path_part, nlines, range_part))
        return RESOLVED, "%s lines %s (%d line file)" % (path_part, range_part, nlines)

    if receipt.startswith("evidence:"):
        name = receipt[len("evidence:"):]
        if not name:
            return UNVERIFIED, "evidence: receipt names no file"
        path = os.path.join(home, ".claude", "evidence", name)
        if os.path.isfile(path):
            return RESOLVED, "captured at %s" % path
        return UNVERIFIED, "no evidence file at %s" % path

    if receipt.startswith("url:"):
        url = receipt[len("url:"):]
        if not url:
            return UNVERIFIED, "url: receipt names no url"
        sha = hashlib.sha1(url.encode("utf-8")).hexdigest()
        path = os.path.join(home, ".claude", "evidence", sha + ".txt")
        if os.path.isfile(path):
            return RESOLVED, "captured at %s" % path
        return UNVERIFIED, "no captured evidence file for this url (expected %s)" % path

    return UNVERIFIED, "unrecognised receipt scheme %r" % receipt


def _sources(record):
    """(option_id, source) for every source of every option, in order.
    A record shaped without options or without sources yields nothing,
    which is how the caller tells a real, all-clear record apart from one
    with nothing to check."""
    out = []
    for opt in record.get("options") or []:
        opt_id = opt.get("id", "")
        for src in opt.get("sources") or []:
            out.append((opt_id, src))
    return out


def _contract_receipts(record):
    """[(id, path, ref)] for a top-level receipts[] list, the
    outcome-contract-v1 shape (docs/schema/outcome-contract-v1.json): a
    record with no options key but a receipts key. None when the record is
    not shaped this way at all (has options, or has no receipts key), so a
    caller can tell "not a contract record" apart from "a contract record
    with an empty receipts list" (the latter is still NO-DATA, but for a
    different, more specific reason)."""
    if "options" in record:
        return None
    receipts = record.get("receipts")
    if receipts is None:
        return None
    return [(r.get("id", ""), r.get("path", ""), r.get("ref", "")) for r in receipts]


def check(record):
    """[{option, status, receipt, reason, what}]. Pure, so a caller (this
    module's own main, or a future daybook citation index) can format the
    same rows differently."""
    rows = []
    for opt_id, src in _sources(record):
        receipt = src.get("receipt", "")
        status, reason = resolve_receipt(receipt, ROOT, os.path.expanduser("~"))
        rows.append({"option": opt_id, "status": status, "receipt": receipt,
                     "reason": reason, "what": src.get("what", "")})
    return rows


def check_contract(record):
    """[{option, status, receipt, reason, what}], same row shape as check(),
    for the outcome-contract-v1 shape: each receipts[] entry resolved
    through the same resolve_receipt() the options path uses. "option"
    carries the receipt's own id and "what" its human readable path, so the
    same print/--json formatting in main() works unchanged for both shapes."""
    rows = []
    for rid, rpath, ref in _contract_receipts(record) or []:
        status, reason = resolve_receipt(ref, ROOT, os.path.expanduser("~"))
        rows.append({"option": rid, "status": status, "receipt": ref,
                     "reason": reason, "what": rpath})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("record", help="a decision JSON, decide.py's own spec shape")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        with open(args.record, encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError) as exc:
        print("%s: could not read %s: %s" % (NODATA, args.record, exc), file=sys.stderr)
        return 2

    contract_receipts = _contract_receipts(record)
    if contract_receipts is not None:
        if not contract_receipts:
            print("%s: %s carries an empty receipts list"
                  % (NODATA, args.record), file=sys.stderr)
            return 2
        rows = check_contract(record)
    else:
        rows = check(record)
        if not rows:
            print("%s: no source carries a receipt field anywhere in %s"
                  % (NODATA, args.record), file=sys.stderr)
            return 2

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            print("%-6s %-11s %-40s %s"
                  % (r["option"] or NODATA, r["status"], r["receipt"] or NODATA, r["reason"]))

    unresolved = sum(1 for r in rows if r["status"] != RESOLVED)
    if unresolved:
        print("\n%d of %d source(s) UNVERIFIED" % (unresolved, len(rows)), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
