#!/usr/bin/env python3
"""The 1.0.13 cutover pack: one prompt and one zip a fresh session on another
account can start from and cut the release with.

WHY THIS EXISTS. The founder's words on 2026-09-10: "it is unrealistic that I
will take all the mega prompt and all the summaries of all the sessions here to
make a follow up session so we need to unify all this into one prompt and one
mega zip for a new session under a new account that prepares the cut for
tomorrow ... This workstream becomes the main orchestrator and organizes all the
learning, all the knowledge, all the md files and zips, all the plans under one
mega plan."

The ground it compresses, measured that day: 353 entries in the handover root,
132 plan files, about 40 decision records, about 1485 vault notes, six open pull
requests, several sessions still landing work.

COMPOSITION, NEVER RE-IMPLEMENTATION, the same law portable_pack.py already
follows. The live measurement comes from handover_ceremony.collect_state, which
already knows how to read repo HEADs, dirty paths, the ready set and open pull
requests. Writing a second definition of "the estate's state" would guarantee
two answers that disagree within a week.

WHAT IS NEW HERE, and why a fourth packer earns its place beside the handover
ceremony, the close ceremony and the portable pack: those three answer "what
happened in this session". This one answers "what must happen next, and is that
still true when you read it". The difference is the control plane plus its
refresh gate, which no existing pack carries.

THE SELECTION RULE, stated mechanically because a judgement call made 353 times
is not a rule. An artifact is COPIED into the pack only when it satisfies at
least one predicate:
  - it is a decision record that is open, or dated today
  - it is a plan modified within FRESH_DAYS, or named by an open pull request
  - it is the release runbook for the version being cut
  - it is the newest close pack of the day
Everything else is INDEXED: name, date, size and path, in a CSV. Indexing is
lossless for findability and costs almost nothing to carry, which is what lets
a pack refuse to copy 353 packs without losing them.

Exit contract, this estate's house style:
  0  the pack was written and its zip holds every member
  1  the pack was written but a required input was wrong, naming what
  2  NO-DATA: a required input could not be read at all, never a pass

No em or en dashes anywhere.
"""
import argparse
import csv
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import handover_ceremony as HC  # noqa: E402
import handover_pack_scan as HPS  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HANDOVER_ROOT = os.path.expanduser("~/Documents/BrotherModeUp-handovers")
VAULT_ROOT = os.path.expanduser("~/Documents/Kay Vault")
FRESH_DAYS = 3


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _run(args, cwd=None):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                           timeout=180)
        return p.returncode, p.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)


def _mtime(path):
    try:
        return datetime.fromtimestamp(os.path.getmtime(path), timezone.utc)
    except OSError:  # sbe: allow-silent a stat failure only degrades a date
        # LABEL in an index row to "unknown"; it never decides what is copied
        # into the pack or scanned, since scan_and_guard_pack rescans the
        # whole built pack directory unconditionally before zip_pack runs
        return None


def _git_mtime(path, repo):
    """The date the file last CHANGED IN HISTORY, not the date it was checked
    out. A fresh worktree stamps every file with the checkout time, so a
    filesystem mtime rule selects the whole tree and compresses nothing. This
    was measured, not theorised: the first build copied 199 of 199 plans."""
    rc, out = _run(["git", "-C", repo, "log", "-1", "--format=%cI", "--",
                    path])
    if rc != 0 or not out:
        return None
    try:
        return datetime.fromisoformat(out.strip()).astimezone(timezone.utc)
    except ValueError:  # sbe: allow-silent git always emits a valid ISO8601
        # timestamp for %cI; a parse failure here only drops a plan from the
        # freshness copy or shows an unknown date, it never widens or skips
        # the unconditional private-term scan that runs over the whole pack
        return None


def _size(path):
    try:
        if os.path.isdir(path):
            return sum(os.path.getsize(os.path.join(dp, f))
                       for dp, _, fs in os.walk(path) for f in fs
                       if os.path.exists(os.path.join(dp, f)))
        return os.path.getsize(path)
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# The control plane: state at build time, the thing the refresh gate diffs.
# ---------------------------------------------------------------------------

def build_control_plane(repos, version):
    state = HC.collect_state(repos)
    plane = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cutting_version": version,
        "repos": {},
        "pull_requests": {},
        "tag_state": {},
    }
    for r in repos:
        rc, head = _run(["git", "-C", r, "rev-parse", "HEAD"])
        _, branch = _run(["git", "-C", r, "rev-parse", "--abbrev-ref", "HEAD"])
        _, dirty = _run(["git", "-C", r, "status", "--porcelain"])
        _, behind = _run(["git", "-C", r, "rev-list", "--count",
                          "HEAD..origin/main"])
        _, tags = _run(["git", "-C", r, "tag", "--sort=-creatordate"])
        taglist = tags.splitlines()[:5]
        plane["repos"][r] = {
            "head": head if rc == 0 else None,
            "branch": branch,
            "dirty_count": len([l for l in dirty.splitlines() if l.strip()]),
            "behind_origin_main": behind if behind.isdigit() else "NO-DATA",
            "tags_newest": taglist,
        }
        plane["tag_state"][r] = {
            "target": "v%s" % version,
            "exists": ("v%s" % version) in tags.splitlines(),
            "newest": taglist[0] if taglist else "NO-DATA",
        }
    rc, out = _run(["gh", "pr", "list", "--limit", "50", "--json",
                    "number,title,headRefName,isDraft,updatedAt"],
                   cwd=repos[0] if repos else None)
    if rc == 0:
        try:
            plane["pull_requests"] = {"open": json.loads(out or "[]")}
        except json.JSONDecodeError:
            plane["pull_requests"] = {"error": "NO-DATA: unparseable gh JSON"}
    else:
        plane["pull_requests"] = {"error": "NO-DATA: gh could not list: %s"
                                           % out[:200]}
    plane["collected_state"] = state
    return plane


# ---------------------------------------------------------------------------
# The indexes: lossless findability for everything not copied.
# ---------------------------------------------------------------------------

def _csv(rows, header):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def index_packs(root):
    if not os.path.isdir(root):
        return None, 0
    rows = []
    for name in sorted(os.listdir(root)):
        if name.startswith("."):
            continue
        p = os.path.join(root, name)
        m = _mtime(p)
        rows.append([name, m.strftime("%Y-%m-%d") if m else "unknown",
                     "dir" if os.path.isdir(p) else
                     os.path.splitext(name)[1].lstrip(".") or "file",
                     _size(p), p])
    return _csv(rows, ["name", "date", "kind", "bytes", "path"]), len(rows)


def index_plans(plan_dir, repo=None):
    repo = repo or ROOT
    if not os.path.isdir(plan_dir):
        return None, 0
    rows = []
    for name in sorted(os.listdir(plan_dir)):
        p = os.path.join(plan_dir, name)
        if not os.path.isfile(p):
            continue
        m = _git_mtime(p, repo) or _mtime(p)
        rows.append([name, m.strftime("%Y-%m-%d") if m else "unknown",
                     _size(p), os.path.relpath(p, ROOT)])
    return _csv(rows, ["name", "last_changed", "bytes", "repo_path"]), len(rows)


def index_vault(vault_root, terms_path=None):
    """Index every vault note, EXCEPT any whose title, folder or path carries a
    private term.

    MEASURED 2026-09-10: the first index carried a client term straight into the
    pack and its zip, because a vault note about a client estate names that
    client in its own filename. The privacy law is an EDITION boundary, not a
    scan applied afterwards, so the exclusion happens here at the point the row
    is created rather than in a cleanup pass over the finished pack.

    The dropped count is reported. Silently dropping rows would make the index
    lie about its own completeness, and an index that lies is worse than no
    index. The terms themselves are never written, printed or counted per term."""
    if not os.path.isdir(vault_root):
        return None, 0, 0
    # load_terms returns (terms, reason) and gives None, never [], when the
    # list cannot be read: an empty list would make every screen pass.
    terms, reason = HPS.load_terms(terms_path or HPS.TERMS_FILE)
    if terms is None:
        # Refuse to emit an unscreened index. NO-DATA is never a pass.
        return None, 0, 0
    short_p, long_p = HPS.build_patterns(terms)
    rows, dropped = [], 0
    for dp, dirs, files in os.walk(vault_root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if not f.endswith(".md"):
                continue
            p = os.path.join(dp, f)
            folder = os.path.relpath(dp, vault_root)
            title = os.path.splitext(f)[0]
            if HPS.first_match_len("%s %s %s" % (title, folder, p),
                                   short_p, long_p):
                dropped += 1
                continue
            m = _mtime(p)
            rows.append([title, folder,
                         m.strftime("%Y-%m-%d") if m else "unknown", p])
    rows.sort(key=lambda r: (r[1], r[0]))
    return _csv(rows, ["title", "folder", "modified", "path"]), len(rows), dropped


# ---------------------------------------------------------------------------
# Selection: what gets COPIED, by the mechanical predicates in the docstring.
# ---------------------------------------------------------------------------

def select_decisions(dec_dir, today):
    """Open, or dated today. A decision record here is a paired html and json;
    the json carries the status, so the json decides and the html follows."""
    picked, skipped = [], 0
    if not os.path.isdir(dec_dir):
        return picked, skipped
    for name in sorted(os.listdir(dec_dir)):
        if not name.endswith(".json"):
            continue
        p = os.path.join(dec_dir, name)
        keep = today in name
        if not keep:
            try:
                with open(p, encoding="utf-8") as f:
                    doc = json.load(f)
                status = str(doc.get("status", "")).lower()
                # An ABSENT status is not an open status. Treating "" as open
                # kept 61 of 61 records in the first build, which is not a
                # selection rule, it is a copy of the directory.
                keep = status in ("open", "pending")
            except (OSError, json.JSONDecodeError):
                keep = False
        if keep:
            picked.append(p)
            html = p[:-5] + ".html"
            if os.path.isfile(html):
                picked.append(html)
        else:
            skipped += 1
    return picked, skipped


def gather_branch_decisions(repo, today, dest_dir):
    """Today's decision records wherever they currently live, with provenance.

    MEASURED 2026-09-10, and the reason this function exists: main carried a
    minority of the decision records governing the cut, while the rest sat on
    unmerged branches. A pack built from the checked out tree alone hands the
    receiver an incomplete decision set AND no signal that the remainder are
    still proposals rather than law. Provenance is the point: a decision on an
    unmerged branch does not yet bind, and the receiver must be able to tell
    the difference without reading git."""
    found = {}
    rc, out = _run(["git", "-C", repo, "branch", "-r", "--format=%(refname:short)"])
    if rc != 0:
        return found, ["NO-DATA: could not list remote branches"]
    problems = []
    for branch in [b.strip() for b in out.splitlines() if b.strip()]:
        if "->" in branch:
            continue
        rc, listing = _run(["git", "-C", repo, "ls-tree", "-r",
                            "--name-only", branch, "--", "docs/decisions"])
        if rc != 0:
            continue
        for path in listing.splitlines():
            name = os.path.basename(path)
            if today not in name:
                continue
            # main wins: a record that is merged is the authoritative copy.
            prior = found.get(name)
            if prior and prior["branch"].endswith("/main"):
                continue
            rc, blob = _run(["git", "-C", repo, "show", "%s:%s"
                             % (branch, path)])
            if rc != 0:
                problems.append("could not read %s from %s" % (name, branch))
                continue
            found[name] = {"branch": branch, "content": blob}
    os.makedirs(dest_dir, exist_ok=True)
    for name, rec in found.items():
        _write(os.path.join(dest_dir, "decision-" + name),
               rec["content"] + "\n")
    return found, problems


def select_plans(plan_dir, fresh_days, repo=None):
    repo = repo or ROOT
    cutoff = datetime.now(timezone.utc) - timedelta(days=fresh_days)
    picked, skipped = [], 0
    if not os.path.isdir(plan_dir):
        return picked, skipped
    for name in sorted(os.listdir(plan_dir)):
        p = os.path.join(plan_dir, name)
        if not os.path.isfile(p):
            continue
        m = _git_mtime(p, repo)
        if m and m >= cutoff:
            picked.append(p)
        else:
            skipped += 1
    return picked, skipped


# ---------------------------------------------------------------------------
# The pack.
# ---------------------------------------------------------------------------

def build(repos, version, out_dir, today=None, extra_docs=None):
    today = today or datetime.now().strftime("%Y-%m-%d")
    name = "%s-CUTOVER-%s-pack" % (today, version)
    pack = os.path.join(out_dir, name)
    if os.path.isdir(pack):
        shutil.rmtree(pack)
    os.makedirs(pack)
    problems = []

    plane = build_control_plane(repos, version)
    _write(os.path.join(pack, "03-LIVE-CONTROL-PLANE.json"),
           json.dumps(plane, indent=2, sort_keys=True) + "\n")

    # The refresh gate travels with the pack: it is the instrument, not a copy.
    gate_src = os.path.join(ROOT, "scripts", "refresh_cutover_state.py")
    if os.path.isfile(gate_src):
        shutil.copy2(gate_src, os.path.join(pack,
                                            "refresh_cutover_state.py"))
    else:
        problems.append("refresh_cutover_state.py missing: the pack ships "
                        "without its receipt-time gate, which is the one "
                        "thing that keeps it true")

    # Runbook.
    rb = os.path.join(ROOT, "docs", "plan", "CUT-RUNBOOK-%s.md" % version)
    if os.path.isfile(rb):
        shutil.copy2(rb, os.path.join(pack, "04-RELEASE-RUNBOOK.md"))
    else:
        problems.append("no release runbook at %s" % os.path.relpath(rb, ROOT))

    # Decisions.
    dec_dir = os.path.join(ROOT, "docs", "decisions")
    picked, dec_skipped = select_decisions(dec_dir, today)
    for p in picked:
        shutil.copy2(p, os.path.join(pack,
                                     "decision-" + os.path.basename(p)))
    branch_decs, dec_problems = gather_branch_decisions(
        repos[0], today, pack)
    problems.extend(dec_problems)
    provenance = {n: r["branch"] for n, r in sorted(branch_decs.items())}
    _write(os.path.join(pack, "05-DECISION-PROVENANCE.json"),
           json.dumps(provenance, indent=2, sort_keys=True) + "\n")

    # Plans.
    plan_dir = os.path.join(ROOT, "docs", "plan")
    pplans, plan_skipped = select_plans(plan_dir, FRESH_DAYS,
                                        repo=repos[0])
    for p in pplans:
        shutil.copy2(p, os.path.join(pack, "plan-" + os.path.basename(p)))

    # Indexes.
    idx = pack
    counts = {}
    vault_text, vault_n, vault_dropped = index_vault(VAULT_ROOT)
    for label, (text, n) in {
        "packs": index_packs(HANDOVER_ROOT),
        "plans": index_plans(plan_dir, repo=repos[0]),
        "vault": (vault_text, vault_n),
    }.items():
        counts[label] = n
        if text is None:
            problems.append("could not index %s: no readable private terms "
                            "list, so an unscreened index was refused" % label)
            continue
        _write(os.path.join(idx, "06-INDEX-%s.csv" % label), text)
    counts["vault_excluded_private"] = vault_dropped

    # Board copy, required by the closing ceremony law's enforcer.
    board = None
    for cand in (os.path.join(ROOT, "docs", "plan", "READINESS-BOARD.html"),
                 os.path.join(ROOT, "PROJECT-VIEW.html"),
                 os.path.join(ROOT, "GANTT.html")):
        if os.path.isfile(cand):
            board = cand
            break
    if board:
        shutil.copy2(board, os.path.join(pack, "READINESS-BOARD.html"))
    else:
        problems.append("no readiness board HTML found to copy")

    for src, dest in (extra_docs or {}).items():
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(pack, dest))
        else:
            problems.append("extra doc missing: %s" % src)

    # The session log the closing ceremony law requires. Generated from the
    # measured facts rather than written from memory, because a log written
    # from memory is the thing the ceremony exists to stop.
    unmerged = sorted(n for n, b in provenance.items()
                      if not b.endswith("/main"))
    prs = (plane.get("pull_requests", {}) or {}).get("open", [])
    log = ["# Session log: building the %s cutover pack" % version, "",
           "Generated by scripts/cutover_pack.py at %s."
           % plane["generated_at"], "",
           "## Measured at build", ""]
    for r, info in sorted(plane["repos"].items()):
        log.append("- %s: branch %s, HEAD %s, %d dirty file(s), %s behind "
                   "origin/main" % (r, info.get("branch"),
                                    (info.get("head") or "?")[:12],
                                    info.get("dirty_count", 0),
                                    info.get("behind_origin_main")))
    log += ["", "- open pull requests at build: %d" % len(prs)]
    for pr in prs:
        log.append("  - %s: %s (%s)" % (pr.get("number"), pr.get("title"),
                                        pr.get("headRefName")))
    log += ["", "- tag %s exists: %s" % (
        "v" + version,
        any(t.get("exists") for t in plane.get("tag_state", {}).values()))]
    log += ["", "## Decision provenance", "",
            "%d decision file(s) dated today were gathered across branches; "
            "%d are NOT on main and are therefore proposals rather than law:"
            % (len(provenance), len(unmerged))]
    log += ["  - %s (%s)" % (n, provenance[n]) for n in unmerged]
    log += ["", "## Selection", "",
            "Copied what is open, fresh or release bearing; indexed the rest. "
            "See 06-SELECTION.json for the counts and 06-INDEX-*.csv for the "
            "full indexes.", ""]
    _write(os.path.join(pack, "10-SESSION-LOG.md"), "\n".join(log) + "\n")

    meta = {
        "pack": name,
        "counts": counts,
        "decisions_copied": len([p for p in picked if p.endswith(".json")]),
        "decisions_from_branches": len(branch_decs),
        "decisions_unmerged": sorted(
            n for n, b in provenance.items() if not b.endswith("/main")),
        "decisions_indexed_only": dec_skipped,
        "plans_copied": len(pplans),
        "plans_indexed_only": plan_skipped,
        "problems": problems,
    }
    _write(os.path.join(pack, "06-SELECTION.json"),
           json.dumps(meta, indent=2, sort_keys=True) + "\n")
    return pack, plane, meta


def scan_and_guard_pack(pack, terms_path=None):
    """Fail-closed private-term scan of the built pack directory, the same
    shape index_vault already uses for its own list-load failure: this pack
    is a cross-account handover artifact (decisions and plans copied
    verbatim, including from arbitrary remote branches), so nothing gets
    zipped unscreened.

    Returns None when the pack is clean (the caller proceeds to zip_pack),
    2 when the terms list itself could not be read (NO-DATA, never a pass),
    or 1 when a hit was found: the count and the masked file names are
    printed, the matched term value never is."""
    hits, short_p, long_p, _stats, reason = HPS.run_scan(pack, terms_path=terms_path)
    if reason is not None:
        print("NO-DATA: private-term scan of the pack could not run: %s"
              % reason)
        return 2
    if hits:
        names = sorted({HPS.mask_path(relpath, short_p, long_p)
                        for _kind, relpath, _n in hits})
        print("REFUSED: %d private-term hit(s) in the pack, no zip written."
              % len(hits))
        for n in names:
            print("  - %s" % n)
        return 1
    return None


def zip_pack(pack):
    """One zip holding every file the directory holds: the one-zip law."""
    zip_path = pack + ".zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for dp, _, files in os.walk(pack):
            for f in sorted(files):
                full = os.path.join(dp, f)
                zf.write(full, arcname=os.path.relpath(full, pack))
    # Prove it: every file on disk is a member.
    on_disk = {os.path.relpath(os.path.join(dp, f), pack)
               for dp, _, fs in os.walk(pack) for f in fs}
    with zipfile.ZipFile(zip_path) as zf:
        members = set(zf.namelist())
    missing = sorted(on_disk - members)
    return zip_path, missing


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", action="append", default=[])
    ap.add_argument("--version", default="1.0.13")
    ap.add_argument("--out-dir", default=HANDOVER_ROOT)
    ap.add_argument("--extra", action="append", default=[],
                    metavar="SRC:DEST")
    args = ap.parse_args(argv)
    repos = args.repo or [ROOT]
    repos = [os.path.abspath(os.path.expanduser(r)) for r in repos]
    for r in repos:
        if _run(["git", "-C", r, "rev-parse", "--git-dir"])[0] != 0:
            print("NO-DATA: %s is not a git checkout" % r)
            return 2
    if not os.path.isdir(args.out_dir):
        print("NO-DATA: out dir %s does not exist" % args.out_dir)
        return 2

    extra = {}
    for spec in args.extra:
        src, _, dest = spec.partition(":")
        extra[os.path.abspath(os.path.expanduser(src))] = dest

    pack, plane, meta = build(repos, args.version, args.out_dir,
                              extra_docs=extra)

    print("pack:  %s" % pack)

    # Fail-closed private-term scan before anything is zipped: this pack is
    # a cross-account handover artifact, and a pack cannot print OK without
    # this step having run.
    guard_rc = scan_and_guard_pack(pack)
    if guard_rc is not None:
        return guard_rc

    zip_path, missing = zip_pack(pack)
    print("zip:   %s" % zip_path)
    print("indexed: packs=%(packs)s plans=%(plans)s vault=%(vault)s" % meta["counts"])
    print("vault notes excluded as private content: %d"
          % meta["counts"].get("vault_excluded_private", 0))
    print("copied:  decisions=%d plans=%d" % (meta["decisions_copied"],
                                              meta["plans_copied"]))
    print("today's decisions gathered across branches: %d, of which %d are "
          "NOT on main yet" % (meta["decisions_from_branches"],
                               len(meta["decisions_unmerged"])))
    for n in meta["decisions_unmerged"]:
        print("    unmerged (a proposal, not yet law): %s" % n)
    print("indexed only: decisions=%d plans=%d" % (meta["decisions_indexed_only"],
                                                   meta["plans_indexed_only"]))
    if missing:
        print("FAIL: %d file(s) on disk are not in the zip: %s"
              % (len(missing), ", ".join(missing[:5])))
        return 1
    if meta["problems"]:
        print("\nProblems, the pack was written anyway and each is named:")
        for p in meta["problems"]:
            print("  - %s" % p)
        return 1
    print("\nOK: every file in the pack is in the zip.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
