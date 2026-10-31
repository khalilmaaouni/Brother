#!/usr/bin/env python3
"""The 1.1.9 go / no-go decision, as rows a machine reads. No judgement calls.

WHY THIS SHAPE. A release decision taken in prose is taken again, differently,
every time someone re-reads the prose. These eight rows are the decision: each
one reads a real artifact in the tree or the ledger, each answers exactly one of
PASS, FAIL or NO-DATA, and the whole thing is GO only when every row is PASS.

NO-DATA IS NOT A PASS AND NOT A FAIL. It means the evidence has not arrived. A
row that cannot read its artifact says so and names the artifact, because the
two failures this estate keeps paying for are a NO-DATA quietly counted as a
pass, and a new check treated as a break.

EVERY ROW IS ONE PRE MORTEM FAILURE, INVERTED. The question each answers is
"1.1.9 shipped and failed, how?", and the row is what would have caught it:

  gate-on-candidate     a tag was pushed over a tree no whole gate ever judged
  no-ungated-landings   commits landed between gate runs and nothing re-judged
                        them, so the candidate's middle is unproven (measured
                        on this line 2026-10-28: 16 such landings)
  clock-rows-clear      a gate row went red on a date inside the cut window and
                        blocked the cut on the day (three times already)
  journeys-fresh        the bundle works in the author's checkout and cannot
                        install or take a first goal on a stranger's machine
  claims-match-measured the release notes repeat the TARGET journey scores, so
                        a reader infers work shipped that did not
  scope-no-strays       the ship list shrank but partial work for slipped units
                        is still in the tree, so the tag ships it anyway
  rollback-proven       something shipped broken and nothing had ever rehearsed
                        getting back
  must-haves-done       a must have unit slipped quietly and the decision read
                        green over an unready release (owner's rule 2026-10-30)

USAGE
    python3 scripts/go_no_go_119.py [--root DIR] [--candidate REV] [--json]
    python3 scripts/go_no_go_119.py --selftest

EXIT CODES
    0  GO       every row PASS
    1  NO-GO    at least one row FAIL
    3  NO-DATA  no row FAILS but at least one could not be read; never a GO
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

# The lower level tool owns the release plan rule (decision 2026-10-29, F1): one implementation, imported
# here, so this file and the clock rows tool can never disagree about which plan is the release plan.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import release_window_clock_rows as _clock  # noqa: E402

PASS, FAIL, NODATA = "PASS", "FAIL", "NO-DATA"
GATE_LEDGER = os.path.join(".git", "brother-whole-gate.jsonl")
KERNEL_LIST = "scripts/kernel_closure.txt"
PLAN_GLOB = os.path.join("docs", "plan", "BROTHER-*-WBS.json")
JOURNEY_GLOB = os.path.join("docs", "plan", "JOURNEY-*.json")
ROLLBACK_GLOB = os.path.join("docs", "plan", "RESTORE-DRILL-*.json")
#: Shared or regenerated paths that belong to no single unit. The plan's own split rule R-S10 and its
#: conflict-graph rule both already exclude this family; counting them as a unit's deliverable turned
#: a check registration into "the tag ships a slipped unit's work".
SHARED_REGENERATED = {
    os.path.join("scripts", "check_all.sh"),
    "SYSTEM.md",
}


def git(root, *args):
    try:
        return subprocess.run(["git", "-C", root] + list(args), capture_output=True,
                              text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        class _Dead(object):
            returncode, stdout, stderr = 1, "", "git could not run"
        return _Dead()


def _ledger_path(root):
    """The gate ledger, found the way git itself would.

    In a WORKTREE `.git` is a FILE pointing at the real gitdir, so joining
    root with ".git/brother-whole-gate.jsonl" names nothing. The first run of
    this tool read exactly that and reported NO-DATA on a ledger with 145
    records sitting beside it: a row that reads the wrong path certifies
    nothing, whatever it prints.
    """
    got = git(root, "rev-parse", "--git-common-dir")
    if got.returncode == 0 and got.stdout.strip():
        d = got.stdout.strip()
        if not os.path.isabs(d):
            d = os.path.join(root, d)
        return os.path.join(d, "brother-whole-gate.jsonl")
    return os.path.join(root, GATE_LEDGER)


def _records(root):
    path = _ledger_path(root)
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue  # a torn line is not a reason to refuse the row
    except OSError:
        return None
    return out


# WHICH PLAN is the release plan lives in release_window_clock_rows.release_plan_path. The first run of
# this tool globbed BROTHER-*-WBS.json and picked BROTHER-LOOP-HARDENING-WBS because "LOOP" sorts after
# "1.1.9", then reported NO-DATA because that plan declares no cut band.

_PLAN_OVERRIDE = []


def _candidate_identity(root, candidate):
    """The sha256 of the candidate's bundle runtime manifest, the same bytes
    scripts/journey/run_journey.py identifies a tree by. "" when it cannot be read.

    Read from the WORKING TREE when the candidate is HEAD and the tree is clean, and from git
    otherwise, so a record is compared against the thing that would actually be tagged.
    """
    import hashlib
    rel = os.path.join("bundle", "runtime", "RUNTIME-MANIFEST.json")
    got = git(root, "show", "%s:%s" % (candidate, rel.replace(os.sep, "/")))
    if got.returncode == 0 and got.stdout:
        return hashlib.sha256(got.stdout.encode("utf-8")).hexdigest()
    try:
        with open(os.path.join(root, rel), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return ""


def _kernel_files(root, candidate):
    """The judge kernel the CANDIDATE's tree names in scripts/kernel_closure.txt, a set of repository
    paths, or None when that file cannot be read from the candidate (absent, or git cannot run).

    Read with `git show <candidate>:...`, never from the working tree: the list that matters is the one
    that ships. Same file and same line rule as scripts/pre_push_gate.py _kernel_files, which differs on
    one point on purpose: it reads a tip without the file as an empty kernel, this row reads it as NO-DATA.
    """
    got = git(root, "show", "%s:%s" % (candidate, KERNEL_LIST))
    if got.returncode != 0:
        return None
    return {l.strip() for l in got.stdout.splitlines() if l.strip() and not l.startswith("#")}


def _journey_docs(root):
    """[(basename, doc)] for every readable journey record, or None when there are none.

    Every record, not whichever path sorts last: X01 names two journeys and each writes its own
    file, so reading one of them answers about one journey and silently says nothing about the
    other.
    """
    out = []
    for path in sorted(glob.glob(os.path.join(root, JOURNEY_GLOB))):
        try:
            with open(path, encoding="utf-8") as fh:
                out.append((os.path.basename(path), json.load(fh)))
        except (OSError, ValueError):
            continue
    return out or None


def _plan(root):
    if _PLAN_OVERRIDE:
        paths = list(_PLAN_OVERRIDE)
    else:
        path = _clock.release_plan_path(root)
        if not path:
            return None
        paths = [path]
    try:
        with open(paths[-1], encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------
# the rows
# --------------------------------------------------------------------------
def row_gate_on_candidate(root, candidate):
    """A whole gate PASS record whose tree_sha IS the candidate's tree."""
    recs = _records(root)
    if recs is None:
        return NODATA, "%s is unreadable, so no gate record can be bound" % GATE_LEDGER
    got = git(root, "rev-parse", "%s^{tree}" % candidate)
    if got.returncode != 0:
        return NODATA, "%s does not resolve to a tree here" % candidate
    tree = got.stdout.strip()
    hits = [r for r in recs if r.get("tree_sha") == tree]
    if not hits:
        return FAIL, ("no whole gate record names the candidate's tree %s: the tag would be "
                      "pushed over a tree no gate ever judged" % tree[:12])
    if not any(r.get("status") == PASS and r.get("exit_code") == 0 for r in hits):
        return FAIL, ("%d gate record(s) name tree %s and none is a PASS at exit 0"
                      % (len(hits), tree[:12]))
    return PASS, "a whole gate PASS record names the candidate's tree %s" % tree[:12]


def row_no_ungated_landings(root, candidate, base):
    """Every commit in base..candidate is covered by some gate record's tree."""
    recs = _records(root)
    if recs is None:
        return NODATA, "%s is unreadable, so coverage cannot be counted" % GATE_LEDGER
    gated = {r.get("tree_sha") for r in recs
             if r.get("status") == PASS and r.get("exit_code") == 0}
    got = git(root, "rev-list", "%s..%s" % (base, candidate))
    if got.returncode != 0:
        return NODATA, "the range %s..%s does not resolve here" % (base, candidate)
    shas = [s for s in got.stdout.split() if s]
    if not shas:
        return PASS, "no commits between %s and the candidate, so nothing is ungated" % base
    # D6 (2026-10-29): when a JUDGE KERNEL file changed inside the range, every intermediate tree was
    # judged by a gate that no longer ships, so re-gating them proves less than the tip's gate does. The
    # question then is the tip's own record. With no kernel change the per commit rule below is the right
    # question and stands unchanged. An unreadable kernel list is NO-DATA, never the easier branch.
    kernel = _kernel_files(root, candidate)
    if kernel is None:
        return NODATA, ("%s could not be read from the candidate's tree, so whether the judge kernel "
                        "changed in the range cannot be told" % KERNEL_LIST)
    names = git(root, "diff", "--name-only", "%s..%s" % (base, candidate))
    if names.returncode != 0:
        return NODATA, "the range %s..%s does not diff here" % (base, candidate)
    changed = sorted(kernel & {p for p in names.stdout.splitlines() if p.strip()})
    if changed:
        tip = git(root, "rev-parse", "%s^{tree}" % candidate)
        if tip.returncode != 0:
            return NODATA, "%s does not resolve to a tree here" % candidate
        shown = " ".join(changed[:6]) + (" ..." if len(changed) > 6 else "")
        said = ("the judge kernel changed in the range (%d file(s): %s), so the %d intermediate "
                "commit(s) were judged by a superseded gate" % (len(changed), shown, len(shas)))
        if tip.stdout.strip() in gated:
            return PASS, ("%s; the candidate's own tree %s has a whole gate PASS record, and the tip's "
                          "gate is the evidence of record" % (said, tip.stdout.strip()[:12]))
        return FAIL, ("%s, and the candidate's own tree %s has no whole gate PASS record, so no gate "
                      "of record judges what would be tagged" % (said, tip.stdout.strip()[:12]))
    uncovered = []
    for sha in shas:
        t = git(root, "rev-parse", "%s^{tree}" % sha)
        if t.returncode != 0 or t.stdout.strip() not in gated:
            uncovered.append(sha[:9])
    if uncovered:
        return FAIL, ("%d of %d candidate commit(s) have no whole gate PASS record for their "
                      "own tree, so the candidate's middle is unproven: %s"
                      % (len(uncovered), len(shas), " ".join(uncovered[:8])))
    return PASS, "every one of %d candidate commit(s) is covered by a gate PASS" % len(shas)


def row_clock_rows_clear(root):
    """No clock bound record this repository gates on expires inside the window."""
    tool = os.path.join(root, "scripts", "release_window_clock_rows.py")
    if not os.path.isfile(tool):
        return NODATA, "scripts/release_window_clock_rows.py is not in this tree"
    argv = [sys.executable, "-B", tool, "--root", root]
    if _PLAN_OVERRIDE:
        argv += ["--plan", _PLAN_OVERRIDE[-1]]
    got = subprocess.run(argv, capture_output=True, text=True)
    tail = [l for l in got.stdout.splitlines() if l.startswith(("FAIL", "PASS", "NO-DATA"))]
    if got.returncode == 0:
        return PASS, "no gated clock bound record expires inside the release window"
    if got.returncode == 3:
        return NODATA, "no release window could be read: %s" % (tail[-1] if tail else "")
    return FAIL, (tail[-1] if tail else "clock bound record(s) expire inside the window")


def row_journeys_fresh(root, candidate="HEAD"):
    """JT0's own result: J1 and J2 rows PASS on a clean home with no dev tooling."""
    docs = _journey_docs(root)
    if docs is None:
        return NODATA, ("no journey result file matches %s: X01's macOS half has not been run "
                        "on a clean home" % JOURNEY_GLOB)
    # X01 names J1 AND J2, which are two records. Reading whichever file sorts last answered about
    # J5 and reported "names no J1 or J2 row", so the rows are collected across every record and
    # keyed by the journey the record declares, never by a row id prefix.
    # THE RECORD IS BOUND TO THE TREE BEING GATED, NOT TO THE ONE IT WAS WRITTEN FOR. A record is a
    # file in docs/plan, so it survives into later candidates. Trusting its own clean_home would let
    # a walk of an older tree certify this one, which is the same floating-artifact hole this gate
    # exists to close. The candidate's identity is recomputed here and compared.
    want = _candidate_identity(root, candidate)
    if not want:
        return NODATA, ("the candidate's own bundle runtime manifest could not be read, so no "
                        "journey record can be bound to it")
    rows, dirty = {}, []
    for name, doc in docs:
        if doc.get("journey") not in ("J1", "J2"):
            continue
        if doc.get("identity") != want:
            dirty.append("%s was walked on another tree (record %s, candidate %s)"
                         % (name, (doc.get("identity") or "unreadable")[:12], want[:12]))
            continue
        if doc.get("clean_home") is not True:
            dirty.append("%s (%s)" % (name, doc.get("reason") or "clean_home is not true"))
            continue
        for rid, verdict in (doc.get("rows") or {}).items():
            rows["%s/%s" % (doc["journey"], rid)] = verdict
    if dirty:
        return FAIL, ("a J1 or J2 record cannot answer X01, either because it was walked on "
                      "another tree or because it records no clean home: %s" % "; ".join(dirty))
    if not rows:
        return NODATA, ("no record declares journey J1 or J2, so X01's own two journeys have not "
                        "been walked")
    # D1 and D2 (2026-10-29): the cut line claims the rows its kept units own, and the release publishes
    # what it measured of THOSE. The claim is the plan's journey_rows ({row id: journey}); a plan that
    # does not carry the key claims nothing this row can read, which is NO-DATA, never "all rows".
    plan = _plan(root)
    if plan is None:
        return NODATA, "no release plan matched %s, so the rows this release claims cannot be read" % PLAN_GLOB
    claims = plan.get("journey_rows")
    if not isinstance(claims, dict):
        return NODATA, ("the release plan carries no journey_rows, so which J1/J2 rows this release "
                        "claims cannot be read")
    claimed = {"%s/%s" % (j, rid) for rid, j in claims.items() if j in ("J1", "J2")}
    if not claimed:
        return NODATA, "the release plan's journey_rows claims no J1 or J2 row, so none is judged here"
    # F3b (2026-10-29): a claimed row the record itself calls NO-DATA is evidence not yet arrived, so it
    # reads NO-DATA like an unwalked row, never FAIL. Anything else that is not PASS (FAIL, or a verdict
    # this file does not know) is FAIL, closed.
    bad = sorted(k for k in claimed if k in rows and rows[k] not in (PASS, NODATA))
    unseen = sorted((claimed - set(rows)) | {k for k in claimed if rows.get(k) == NODATA})
    unclaimed = sorted(set(rows) - claimed)
    note = ("; not claimed by this release, so not judged: %s" % " ".join(unclaimed)) if unclaimed else ""
    if bad:  # a FAIL outranks a NO-DATA, so it is read first
        return FAIL, ("%d of %d claimed J1/J2 row(s) are not PASS on a clean home: %s%s"
                      % (len(bad), len(claimed), " ".join(bad), note))
    if unseen:
        return NODATA, ("%d claimed J1/J2 row(s) are in no journey record or are recorded NO-DATA, so "
                        "they have not been walked: %s%s" % (len(unseen), " ".join(unseen), note))
    return PASS, ("%d claimed J1/J2 row(s) PASS on a clean home with no developer tooling%s"
                  % (len(claimed), note))


def row_claims_match_measured(root, candidate="HEAD"):
    """The release must not claim a journey score it did not measure.

    The failure this prevents: the notes repeat the plan's TARGET numbers, a
    reader takes them for what shipped, and the release has lied without any
    single sentence in it being false.

    ONE MEASUREMENT CONTRACT (2026-10-30, after two reproductions: a record walked on another tree carrying
    scores={J1: "not-a-score"} read PASS here, and a valid hand re-measure satisfied cut_preflight while this row
    read NO-DATA). The measurement is the hand re-measure record docs/plan/REMEASURE-<version>.json (RDM1.c):
    cells of 0 to 2 per journey row, a seat other than the walker's, bound to the candidate. Its derivation lives
    in cut_preflight.remeasure_scores and is the only one read; a `scores` key in a walk record counts for nothing.
    """
    plan = _plan(root)
    if plan is None:
        return NODATA, "no release plan matched %s" % PLAN_GLOB
    targets = ((plan.get("journey_targets") or {}).get("baseline_and_target_1.1.9") or {})
    if not targets:
        return NODATA, "the plan declares no journey targets to compare claims against"
    claims = plan.get("journey_rows")
    if not isinstance(claims, dict):
        return NODATA, "the release plan carries no journey_rows, so which journeys it claims cannot be read"
    claimed = sorted({j for j in claims.values() if isinstance(j, str)})
    if not claimed:
        return NODATA, "the release plan claims no journey row, so there is nothing to match a measurement to"
    path = _PLAN_OVERRIDE[-1] if _PLAN_OVERRIDE else _clock.release_plan_path(root)
    m = re.match(r"BROTHER-(\d+\.\d+\.\d+)-WBS\.json\Z", os.path.basename(path or ""))
    if not m:
        return NODATA, "the release plan's file name carries no version, so its re-measure record cannot be named"
    version = m.group(1)
    want = _candidate_identity(root, candidate)
    if not want:
        return NODATA, ("the candidate's own bundle runtime manifest could not be read, so no re-measure "
                        "record can be bound to it")
    import cut_preflight as P  # the one derivation, never a second copy
    scores, why = P.remeasure_scores(root, version)
    if not scores:
        return NODATA, ("the hand re-measure (RDM1.c, docs/plan/REMEASURE-%s.json) does not stand for this "
                        "candidate, so nothing measured can be published in place of the plan's targets: %s"
                        % (version, why))
    try:
        with open(os.path.join(root, "docs", "plan", "REMEASURE-%s.json" % version), encoding="utf-8") as fh:
            bound = json.load(fh).get("candidate_identity")
    except (OSError, ValueError, AttributeError):
        bound = None
    if bound != want:
        return NODATA, ("docs/plan/REMEASURE-%s.json is bound to another candidate (%.12s, this one is %.12s)"
                        % (version, bound or "unreadable", want))
    missing = [j for j in claimed if j not in scores]
    if missing:
        return FAIL, ("the release claims %d journey(s) with no measured score, so it cannot state what it "
                      "achieved: %s" % (len(missing), " ".join(missing)))
    shown = " ".join("%s %d/%d" % (j, scores[j][0], scores[j][1]) for j in claimed)
    unclaimed = sorted(k for k in targets if k.split()[0] not in claimed)
    note = ("; targeted but not claimed, published as not measured: %s" % "; ".join(unclaimed)) if unclaimed else ""
    return PASS, "every claimed journey carries a hand measured score to publish instead of its target: %s%s" % (shown, note)


def row_scope_no_strays(root, candidate, base, ship):
    """No file owned ONLY by a slipped unit may change in the candidate range.

    The failure this prevents: the ship list shrinks, the tree does not, and
    the tag carries half built work for units the notes say slipped.
    """
    plan = _plan(root)
    if plan is None:
        return NODATA, "no release plan matched %s" % PLAN_GLOB
    units = plan.get("units") or []
    if not units:
        return NODATA, "the plan names no units"
    # F4 and F4b (2026-10-29): nobody passes --ship in a real run, which made this row NO-DATA every time.
    # --ship stays the override. With none given, a plan that carries a `slipped` record names what slipped
    # and what each slipped unit owns, so that record is the cut line. A plan with no `slipped` key falls
    # back to F4: the ship list is every unit id, which leaves nothing slipped, so that row can only fail
    # when the caller names the cut line. The text says which list it judged.
    owners = {}  # file -> the slipped unit ids that own it, for the FAIL text
    if ship:
        source = "ship list from --ship"
    elif "slipped" in plan:
        recs = (plan["slipped"] or {}).get("units") if isinstance(plan["slipped"], dict) else None
        if not isinstance(recs, dict):
            return NODATA, ("the plan's slipped record has no units map, so which units slipped cannot "
                            "be read")
        unreadable = sorted(k for k, r in recs.items()
                            if not isinstance(r, dict) or not isinstance(r.get("owns"), list))
        if unreadable:
            return NODATA, ("the plan's slipped record carries no owns list for %d unit(s), so what "
                            "they own cannot be read: %s" % (len(unreadable), " ".join(unreadable)))
        kept = set()
        for u in units:
            if u.get("id") not in recs:
                kept.update(u.get("owns") or [])
        for uid, r in recs.items():
            for path in r["owns"]:
                owners.setdefault(path, []).append(uid)
        only_slipped = set(owners) - kept
        source = "slipped units taken from the plan's slipped record: %d" % len(recs)
    else:
        ship = [u.get("id") for u in units if u.get("id")]
        source = ("ship list taken from the plan's %d unit id(s) (%s%s)"
                  % (len(ship), " ".join(ship[:8]), " ..." if len(ship) > 8 else ""))
    if ship:
        shipset = set(ship)
        known = {u.get("id") for u in units}
        unknown = shipset - known
        if unknown:
            return FAIL, "the ship list names unit(s) no plan knows: %s" % " ".join(sorted(unknown))
        kept, slipped = set(), set()
        for u in units:
            owns = u.get("owns") or []
            target = kept if (u.get("id") in shipset or u.get("state") == "DONE") else slipped
            for path in owns:
                target.add(path)
        only_slipped = slipped - kept
    got = git(root, "diff", "--name-only", "%s..%s" % (base, candidate))
    if got.returncode != 0:
        return NODATA, "the range %s..%s does not resolve here" % (base, candidate)
    touched = {p for p in got.stdout.splitlines() if p.strip()}
    # The plan's own conflict-graph rule already excludes these: none is hand written as a unit's
    # deliverable, every one is REGENERATED or is a shared registration point every unit appends to,
    # so an edge through them is an artefact. Measured: registering two new checks in
    # scripts/check_all.sh read as shipping a slipped unit's work.
    touched -= SHARED_REGENERATED
    strays = sorted(touched & only_slipped)
    # Kernel maintenance (2026-10-30): a judge kernel file (the CANDIDATE's scripts/kernel_closure.txt) is
    # judged by the tip's whole gate, never a slipped unit's feature work, so it is no stray. Measured:
    # scripts/close_unit.py, owned only by a slipped unit, fixed at its source. An unreadable list exempts
    # nothing: the strict rule stays the fail direction.
    maintenance = []
    if strays:
        kernel = _kernel_files(root, candidate) or set()
        maintenance = [f for f in strays if f in kernel]
        strays = [f for f in strays if f not in kernel]
    if strays:
        named = ["%s (%s)" % (f, " ".join(owners[f])) if f in owners else f for f in strays[:6]]
        return FAIL, ("%d file(s) owned only by slipped unit(s) changed in the candidate, so the "
                      "tag would ship work the notes say slipped: %s"
                      % (len(strays), " ".join(named)))
    return PASS, ("no file owned only by a slipped unit changed in the candidate (%d file(s) "
                  "touched, %d owned only by slipped units; %s)%s"
                  % (len(touched), len(only_slipped), source,
                     ("; kernel maintenance, judged by the tip's gate: %s" % " ".join(maintenance))
                     if maintenance else ""))


def row_rollback_proven(root, candidate):
    """A rollback rehearsal bound to the candidate, not to some older tree."""
    paths = sorted(glob.glob(os.path.join(root, ROLLBACK_GLOB)))
    if not paths:
        return NODATA, "no rollback or restore record matches %s" % ROLLBACK_GLOB
    best = None
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            continue
        commit = doc.get("commit")
        if not commit:
            continue
        anc = git(root, "merge-base", "--is-ancestor", commit, candidate)
        if anc.returncode != 0:
            continue
        passed = doc.get("passed")
        best = (os.path.basename(path), commit[:12], passed)
        if passed is True:
            break
    if best is None:
        return NODATA, ("no rollback record names a commit that is an ancestor of the "
                        "candidate, so nothing rehearsed getting back from this tree")
    name, commit, passed = best
    if passed is not True:
        return FAIL, "%s names commit %s and does not record passed true" % (name, commit)
    return PASS, "%s rehearsed a restore at commit %s, an ancestor of the candidate" % (name, commit)


def row_must_haves_done(root):
    """Every unit the plan names as a must have is DONE; a slipped or open must have keeps the release unready.

    Owner's rule, 2026-10-30: a must have that fails is never slipped to make this decision green. The plan carries
    `must_have` (a list of unit ids); a plan without the key claims no must have and reads NO-DATA, never PASS, so a
    plan that forgot to name them cannot pass by silence. A named unit missing from the plan is FAIL.
    """
    plan = _plan(root)
    if plan is None:
        return NODATA, "no release plan matched %s" % PLAN_GLOB
    names = plan.get("must_have")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        return NODATA, "the release plan names no must have unit (key must_have), so release readiness cannot be read"
    if not names:
        # An EMPTY list passes only with a recorded decision the plan names and the tree holds (owner, 2026-10-30:
        # 1.1.9 cuts as the stepping stone base, the six must haves open the next plan). Silence stays NO-DATA.
        decision = plan.get("must_have_decision")
        if not isinstance(decision, str) or not decision.strip():
            return NODATA, "the release plan names no must have unit and no decision that says why, so readiness cannot be read"
        if not os.path.isfile(os.path.join(root, decision)):
            return NODATA, "the release plan names the decision %s for its empty must have list, and that record is not in the tree" % decision
        return PASS, "the release plan names no must have unit of its own by recorded decision %s" % decision
    states = {u.get("id"): u.get("state") for u in (plan.get("units") or []) if isinstance(u, dict)}
    slipped = (plan.get("slipped") or {}).get("units") if isinstance(plan.get("slipped"), dict) else {}
    bad = []
    for n in names:
        if n in (slipped or {}):
            bad.append("%s slipped" % n)
        elif n not in states:
            bad.append("%s not in the plan" % n)
        elif states[n] != "DONE":
            bad.append("%s %s" % (n, states[n] or "no state"))
    if bad:
        return FAIL, ("%d of %d must have unit(s) are not DONE, so the release is unready: %s"
                      % (len(bad), len(names), ", ".join(bad)))
    return PASS, "every one of %d must have unit(s) is DONE: %s" % (len(names), " ".join(names))


ROWS = ("gate-on-candidate", "no-ungated-landings", "clock-rows-clear", "journeys-fresh",
        "claims-match-measured", "scope-no-strays", "rollback-proven", "must-haves-done")


def decide(root, candidate="HEAD", base="origin/main", ship=None):
    out = [
        ("gate-on-candidate",) + row_gate_on_candidate(root, candidate),
        ("no-ungated-landings",) + row_no_ungated_landings(root, candidate, base),
        ("clock-rows-clear",) + row_clock_rows_clear(root),
        ("journeys-fresh",) + row_journeys_fresh(root, candidate),
        ("claims-match-measured",) + row_claims_match_measured(root, candidate),
        ("scope-no-strays",) + row_scope_no_strays(root, candidate, base, ship or []),
        ("rollback-proven",) + row_rollback_proven(root, candidate),
        ("must-haves-done",) + row_must_haves_done(root),
    ]
    if any(v == FAIL for _, v, _ in out):
        return "NO-GO", 1, out
    if any(v == NODATA for _, v, _ in out):
        return NODATA, 3, out
    return "GO", 0, out


def selftest():
    """Each case isolates ONE property of the decision function itself."""
    cases = []

    def case(label, rows, want_verdict, want_code):
        verdict, code = _fold(rows)
        ok = (verdict, code) == (want_verdict, want_code)
        cases.append((label, ok, verdict, code))

    def _fold(rows):
        if any(v == FAIL for v in rows):
            return "NO-GO", 1
        if any(v == NODATA for v in rows):
            return NODATA, 3
        return "GO", 0

    case("every row PASS is GO", [PASS] * 8, "GO", 0)
    case("one FAIL is NO-GO", [PASS] * 7 + [FAIL], "NO-GO", 1)
    case("one NO-DATA is never GO", [PASS] * 7 + [NODATA], NODATA, 3)
    case("a FAIL outranks a NO-DATA", [PASS] * 6 + [NODATA, FAIL], "NO-GO", 1)
    case("all NO-DATA is not GO", [NODATA] * 8, NODATA, 3)
    bad = 0
    for label, ok, verdict, code in cases:
        bad += 0 if ok else 1
        print("%-36s %-7s exit %d  %s" % (label, verdict, code, "ok" if ok else "MISMATCH"))
    print("%d row name(s) declared: %s" % (len(ROWS), " ".join(ROWS)))
    print("%d case(s), %s" % (len(cases), "OK" if not bad else "%d FAILURE(S)" % bad))
    return 0 if not bad else 1


def main(argv=None):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=here)
    ap.add_argument("--candidate", default="HEAD", help="the tree that would be tagged")
    ap.add_argument("--base", default="origin/main", help="what the candidate is measured from")
    ap.add_argument("--plan", default="", help="the release plan to read (default: the "
                                               "highest BROTHER-<x.y.z>-WBS.json in the tree)")
    ap.add_argument("--ship", default="", help="comma separated unit ids in the cut line")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.plan:
        _PLAN_OVERRIDE.append(args.plan if os.path.isabs(args.plan)
                              else os.path.join(args.root, args.plan))
    ship = [s.strip() for s in args.ship.split(",") if s.strip()]
    verdict, code, rows = decide(args.root, args.candidate, args.base, ship)
    if args.json:
        print(json.dumps({"verdict": verdict, "exit": code,
                          "rows": [{"id": i, "verdict": v, "why": w} for i, v, w in rows]},
                         indent=1))
        return code
    for name, v, why in rows:
        print("%-8s %-22s %s" % (v, name, why))
    print()
    print("%s: %d PASS, %d FAIL, %d NO-DATA"
          % (verdict, sum(1 for _, v, _ in rows if v == PASS),
             sum(1 for _, v, _ in rows if v == FAIL),
             sum(1 for _, v, _ in rows if v == NODATA)))
    return code


if __name__ == "__main__":
    sys.exit(main())
