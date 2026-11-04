#!/usr/bin/env python3
"""The closing ceremony law's enforcer, re-scoped by unit HO1 (owner order
2026-10-31) from pack directories to declared handover records.

WHY THE RE-SCOPE. This gate used to judge whatever DIRECTORY was newest
under a shared folder by marker heuristics (a start file, a board copy, a
session log, a zip beside it). Measured 2026-09-17 and again 2026-10-31: a
scratch bundle in that folder became "the newest pack" and blocked every
push on the machine. A close now DECLARES itself: a `*.handover.json` record
written by scripts/handover_record.py emit, carrying a schema. A directory
that is not a close is kept out of judgment by name, never judged.

What this checks, every run:
  1. EVERY declared record under the root parses to its schema, carries
     every required field, and is clean when its private term, dash and
     credit scans are RE-DERIVED (handover_record.record_problems), so a
     hand edit after emit cannot keep a PASS it no longer earns.
  2. The WHOLE root is clean of every private term (readiness row E35):
     records, legacy packs, their zips and every name, by
     scripts/handover_pack_scan.py's scanner, imported, never copied.
And with --require-current (the cut's freeze and preflight steps):
  3. The newest close for this repository still describes HEAD: a record's
     head_sha IS HEAD; a legacy pack (a directory carrying 01-START-HERE)
     is either inside --max-age-hours or names HEAD (pack_describes_branch).

Exit codes, this estate's convention: 0 PASS, 1 FAIL (each failure named,
a private value never printed), 2 NO-DATA (no root, no close of either kind,
or --require-current with no close for this repository; never a pass).
"""
import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import handover_pack_scan as hps  # noqa: E402
import handover_record as hr  # noqa: E402

ROOT = os.path.expanduser("~/Documents/BrotherModeUp-handovers")
MAX_AGE_HOURS = 24.0


def _boxed():
    """True only when macOS answers that this process runs under a sandbox (sandbox_check on its own pid): the lander's
    box. Anything it cannot ask, another platform included, is False, so the real check runs."""
    if sys.platform != "darwin":
        return False
    try:
        import ctypes
        import ctypes.util
        lib = ctypes.CDLL(ctypes.util.find_library("System") or "/usr/lib/libSystem.B.dylib")
        check = lib.sandbox_check
        check.restype = ctypes.c_int
        check.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        return check(os.getpid(), None, 0) == 1
    except (OSError, AttributeError, ValueError):
        return False


#: The repository a pack is a close OF. A pack names the commits it closed
#: over; this is the tree those shas are resolved against.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SHA = re.compile(r"\b[0-9a-f]{9,40}\b")


def pack_describes_branch(pack, start, repo):
    """(moved, note): does this pack still describe the branch it closed?

    WHY. Measured three times (2026-10-20, 2026-10-21, 2026-10-25): this row
    turned every tree red because HOURS had passed, on trees nobody touched,
    in the middle of a cut. The pack's age is not the property anyone cares
    about. The property is whether the pack still DESCRIBES the branch: a
    pack written two days ago over a branch that has not moved since is a
    correct description, and a pack written an hour ago over a branch that
    has moved twenty commits since is not.

    `moved` is False only when the newest sha the pack names resolves here
    and no commit has landed on HEAD since it. Anything this cannot read
    (no sha, no git, a sha from another repository, a detached or unborn
    HEAD) returns True, so an unreadable pack keeps the old, stricter
    age rule rather than earning a pass from a failed measurement.

    Bounded on purpose: one rev-parse per candidate sha and one rev-list
    --count. This gate once scanned 1.1 GB of history to prove one close
    (vault, 2026-09-14); nothing here walks history.
    """
    import subprocess
    if not start:
        return True, "the pack names no START-HERE, so no sha to compare"
    try:
        with open(os.path.join(pack, start), encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        return True, "START-HERE unreadable (%s)" % exc

    def git(*args):
        return subprocess.run(["git", "-C", repo] + list(args),
                              capture_output=True, text=True, timeout=30)

    head = git("rev-parse", "HEAD")
    if head.returncode != 0:
        return True, "HEAD does not resolve in %s" % os.path.basename(repo)
    best = None
    for raw in dict.fromkeys(_SHA.findall(text)):
        got = git("rev-parse", "--verify", "--quiet", raw + "^{commit}")
        if got.returncode != 0 or not got.stdout.strip():
            continue
        sha = got.stdout.strip()
        anc = git("merge-base", "--is-ancestor", sha, "HEAD")
        if anc.returncode != 0:
            continue
        count = git("rev-list", "--count", "%s..HEAD" % sha)
        if count.returncode != 0:
            continue
        try:
            behind = int(count.stdout.strip())
        except ValueError:
            continue
        if best is None or behind < best[0]:
            best = (behind, raw)
    if best is None:
        return True, "no sha the pack names resolves to an ancestor of HEAD here"
    behind, raw = best
    if behind == 0:
        return False, ("the pack names %s, which is HEAD: the branch has not moved since the "
                       "pack was written, so the pack still describes it" % raw)
    return True, ("the pack's newest named commit %s is %d commit(s) behind HEAD, so the pack "
                  "no longer describes the branch: write a pack for the current tip" % (raw, behind))


def newest_pack(root):
    dirs = [os.path.join(root, d) for d in os.listdir(root)
            if os.path.isdir(os.path.join(root, d))]
    return max(dirs, key=os.path.getmtime) if dirs else None


def start_here(pack):
    """The 01-START-HERE file a legacy pack directory carries, or None. That file is what makes a directory a legacy
    close; a board copy, a session log or a zip beside it alone does not (HO1: the scratch bundle of 2026-10-31 had a
    zip beside it and was judged as a broken pack)."""
    try:
        return next((f for f in sorted(os.listdir(pack)) if f.upper().startswith("01-START-HERE")), None)
    except OSError:
        return None


def _common_dir(path):
    """The repository a folder belongs to, the same for every worktree of it, or None."""
    rc, out = hr.git(path, "rev-parse", "--git-common-dir") if os.path.isdir(path) else (1, "")
    return os.path.realpath(os.path.join(path, out.strip())) if rc == 0 and out.strip() else None


def closes(root, repo=None):
    """(closes, ignored): closes is [(mtime, path, kind)] newest first, kind "record" for a declared
    *.handover.json and "pack" for a directory carrying 01-START-HERE; ignored lists every other directory, which is
    not a close and is never judged. With `repo`, a record counts only when it was emitted from that repository
    (any worktree of it); a legacy pack names no repository and always counts. Only a record that PARSES to its
    schema and contract is a close: a file merely named *.handover.json is not one, so junk can never become the
    newest close (review finding 2). main() judges every such file by name all the same."""
    want = _common_dir(repo) if repo else None
    found, ignored = [], []
    for path in hr.records_in(root):
        record, errs = hr.load_record(path)
        if record is None or errs or hr.contract_errors(record):
            continue
        if want is not None and not hr.same_path(_common_dir(str(record["project"]["repo_path"])), want):
            continue
        found.append((os.lstat(path).st_mtime, path, "record"))
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if os.path.isdir(path):
            if start_here(path):
                found.append((os.path.getmtime(path), path, "pack"))
            else:
                ignored.append(path)
    found.sort(reverse=True)
    return found, ignored


def newest_close(root, repo=None):
    """(path, kind) of the newest close under root (see closes), or (None, None)."""
    found, _ = closes(root, repo)
    return (found[0][1], found[0][2]) if found else (None, None)


def expiry_errors(root, deadline, now=None, repo=None):
    """Refusal strings for a run that ends at `deadline` (epoch seconds): [] when the newest close under `root` is a
    declared record (bound to a change, never to the clock) or a legacy pack still inside the freshness bar
    (MAX_AGE_HOURS) at that moment, else one named refusal. A clock-bound row read at the deadline, never only at
    launch (1.1.7 LR1.b). `now` is accepted so a caller can pin its clock; the expiry is absolute, so it is not read.
    A deadline that is not a finite number and a root that is not a directory raise ValueError before any file is
    read. With `repo`, only that repository's records count (another project's record says nothing about this
    run); a legacy pack names no repository and always counts."""
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or deadline != deadline \
            or deadline in (float("inf"), float("-inf")):
        raise ValueError("deadline must be a finite number of seconds, got %r" % (deadline,))
    if now is not None and (isinstance(now, bool) or not isinstance(now, (int, float)) or now != now):
        raise ValueError("now must be a number of seconds or None, got %r" % (now,))
    if not isinstance(root, str) or not os.path.isdir(root):
        raise ValueError("root must be a directory, got %r" % (root,))
    fix = "refresh with python3 scripts/close_ceremony_check.py"
    try:
        # the newest CLOSE: a scratch folder with no 01-START-HERE is not judged, so it cannot make a stale pack look
        # fresh, and a declared record is bound to its change, not to the clock
        pack, kind = newest_close(root, repo)
        mtime = None if pack is None else os.lstat(pack).st_mtime
    except OSError as exc:
        return ["close ceremony: the closes under %s cannot be read (%s); %s" % (root, exc, fix)]
    if pack is None:
        return ["close ceremony: no handover record and no pack under %s; %s" % (root, fix)]
    if kind == "record":
        return []
    expires = mtime + MAX_AGE_HOURS * 3600.0
    if expires <= deadline:
        return ["close ceremony: newest pack %s expires before %s; %s"
                % (os.path.basename(pack), time.strftime("%Y-%m-%d %H:%M", time.localtime(deadline)), fix)]
    return []

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=None,
                    help="the handover root (default: $BROTHER_HANDOVER_ROOT, else %s)" % ROOT)
    ap.add_argument("--require-current", action="store_true",
                    help="also require the newest close for --repo to describe HEAD (the cut's freeze and "
                         "preflight steps); without it a close's age or branch is not judged")
    ap.add_argument("--max-age-hours", type=float, default=MAX_AGE_HOURS,
                    help="with --require-current, a LEGACY pack inside this age counts as current")
    ap.add_argument("--repo", default=REPO,
                    help="the repository whose HEAD a close must describe, and whose records count")
    ap.add_argument("--age-binding", choices=("change", "clock"), default="change",
                    help="change (default): a pack over the age bar is stale only when the "
                         "branch has moved since the pack was written. clock: the old rule, "
                         "where the hours alone decide.")
    ap.add_argument("--terms", default=hps.TERMS_FILE,
                    help="private terms list (default: %s)" % hps.TERMS_FILE)
    ap.add_argument("--no-cache", action="store_true",
                    help="force a full rescan of every pack, ignoring and not updating the "
                         "persistent scan cache (default: cache on, opt out only to force-"
                         "verify or to debug a suspected stale cache)")
    args = ap.parse_args(argv)
    # resolved HERE, not at import: the box branch below compares against ROOT as it is when main() runs
    args.root = args.root or os.environ.get("BROTHER_HANDOVER_ROOT") or ROOT
    # Measured 2026-09-13: this scan alone took ~52s over 1,560 directories,
    # 6,321 files and 205 zips, none of which had changed since the prior
    # close. Cached and stored beside the packs themselves (a sibling
    # dotfile, excluded from the scan it accelerates) since old packs are
    # effectively immutable; see handover_pack_scan.py's own docstring for
    # the exact correctness rule.
    cache_path = None if args.no_cache else os.path.join(
        args.root, hps.CACHE_BASENAME)

    # THE BOX (owner ruling 2026-10-19, A2, "Box-aware rows"): the lander's sandbox runs the gate with a throwaway HOME,
    # where the default root cannot exist; that is asserted, never skipped, and the session's unboxed gate reads the real
    # root at every merge window and at the cut. A box whose HOME is the account's own, or that holds the root, is not
    # that box: FAIL, so the real check is wired in there. An explicit --root (the fixture suites) is never this branch.
    if args.root == ROOT and _boxed():
        import pwd
        account = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
        if os.path.realpath(os.path.expanduser("~")) == account or os.path.isdir(args.root):
            print("FAIL: inside a sandbox whose HOME holds the handover root (%s); run the real check here" % args.root)
            return 1
        print("BOXED: the sandbox's throwaway HOME holds no handover root (%s); the session's unboxed gate reads the "
              "real one" % args.root)
        return 0
    if not os.path.isdir(args.root):
        print("NO-DATA: handover root %s does not exist" % args.root)
        return 2
    found, ignored = closes(args.root)
    records = hr.records_in(args.root)  # every file named a record is judged, valid or not
    packs = [p for _m, p, k in found if k == "pack"]
    problems = []

    # Terms cleanliness (readiness row E35): the WHOLE root, records and legacy
    # packs alike, not only the newest close. A private term anywhere under it
    # (a file name, a directory name, a scanned text file's content, or a zip
    # member) is a term one copy away from leaving the machine. The 2026-09-03
    # audit found 24 such files sitting in OLDER packs a newest-only check
    # never looked at. Reuses handover_pack_scan's scanner by import, never a
    # second copy of the matching rule, so the standalone dry run and this
    # gate agree.
    hits, short_patterns, long_patterns, _stats, no_data_reason = hps.run_scan(
        args.root, args.terms, cache_path=cache_path)

    def masked(path):
        # escaped too: a newline or a terminal escape in a peer's file name never starts a line of its own
        return hr.shown(hps.mask_path(os.path.basename(path), short_patterns, long_patterns), False)

    for path in records:
        record_failures, record_notes = hr.record_problems(path, args.terms)
        problems += ["record %s: %s" % (masked(path), problem) for problem in record_failures]
        for note in record_notes:
            print("NO-DATA: record %s: %s" % (masked(path), note))

    if hits is None:
        problems.append("private terms list unreadable, could not check any "
                        "close (%s): a stop, not a pass" % no_data_reason)
    elif hits:
        by_pack = {}
        for _kind, relpath, _n in hits:
            # A zip-member hit's relpath is "<zip path>::<member path>", and
            # a member can itself sit inside an internal folder ("pack.zip::
            # inner/file.md"): strip the "::member" suffix BEFORE splitting
            # on os.sep, so grouping always lands on the zip (or the plain
            # file), never one level inside a zip's own internal structure.
            zip_relpath = relpath.split("::", 1)[0]
            pack_name = zip_relpath.split(os.sep, 1)[0]
            by_pack[pack_name] = by_pack.get(pack_name, 0) + 1
        for pack_name in sorted(by_pack):
            # The NAME itself can be the offender (the audit's file-name case),
            # so it is masked the same way handover_pack_scan.py masks every
            # path it prints, never trusted to be clean because it is a label.
            problems.append(
                "pack %s carries %d private-term hit(s) (value not "
                "printed; run scripts/handover_pack_scan.py for detail)"
                % (masked(pack_name), by_pack[pack_name]))

    current, current_note = None, ""
    if args.require_current:
        mine, _ = closes(args.root, args.repo)
        current = mine[0] if mine else None
    if current is not None and current[2] == "record":
        path = current[1]
        record, errs = hr.load_record(path)
        rc, now = hr.git(args.repo, "rev-parse", "HEAD")
        head = str(record["project"]["head_sha"]) if record is not None and not errs else None
        if head is None:
            pass  # already a problem above, by name
        elif rc != 0:
            problems.append("HEAD does not resolve in %s, so record %s cannot be checked as current"
                            % (args.repo, masked(path)))
        elif head == now.strip():
            current_note = ", newest close %s names HEAD %s" % (masked(path), head[:12])
        elif hr.git(args.repo, "merge-base", "--is-ancestor", head, "HEAD")[0] == 0:
            _, count = hr.git(args.repo, "rev-list", "--count", "%s..HEAD" % head)
            problems.append("record %s names %s, %s commit(s) behind HEAD: the branch moved since the close; "
                            "emit a record for the current tip" % (masked(path), head[:12], count.strip()))
        else:
            problems.append("record %s names %s, which is not an ancestor of HEAD: emit a record for this branch"
                            % (masked(path), head[:12]))
    elif current is not None:
        pack = current[1]
        age_h = (time.time() - os.path.getmtime(pack)) / 3600.0
        if age_h > args.max_age_hours:
            moved, note = pack_describes_branch(pack, start_here(pack), args.repo)
            if args.age_binding == "clock" or moved is not False:
                # land_batch.PACK_OLD_RE reads this exact line: keep its shape
                problems.append("pack %s is %.1f hours old, over the %.0f hour "
                                "freshness bar: a stale pack cannot green a new close (%s)"
                                % (masked(pack), age_h, args.max_age_hours, note))
            else:
                current_note = ", newest close %s is a legacy pack over the bar that %s" % (masked(pack), note)
        else:
            current_note = ", newest close %s is a legacy pack %.1f hours old" % (masked(pack), age_h)

    newest = found[0][0] if found else None
    loud = [p for p in ignored if newest is None or os.path.getmtime(p) > newest]
    for path in sorted(loud, key=os.path.getmtime, reverse=True)[:5]:
        print("IGNORED: %s is not a close (no handover record, no 01-START-HERE), kept out of judgment"
              % masked(path))

    if problems:
        print("FAIL: the handover root does not satisfy the closing ceremony law")
        for p in problems:
            print("  - " + p)
        return 1
    if not found:
        print("NO-DATA: no handover record and no legacy pack under %s: nothing for the ceremony "
              "law to judge, never a pass" % args.root)
        return 2
    if args.require_current and current is None:
        print("NO-DATA: no close for %s under %s, so none describes HEAD; emit one with "
              "python3 scripts/handover_record.py emit" % (args.repo, args.root))
        return 2
    print("PASS: %d handover record(s) valid and %d legacy pack(s) under the root, terms clean%s"
          % (len(records), len(packs), current_note))
    return 0


if __name__ == "__main__":
    sys.exit(main())
