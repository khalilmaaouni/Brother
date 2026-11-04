#!/usr/bin/env python3
"""voice_pipeline: collect and count an author's own public posts (authored content, not speech).

No model calls, safe to run from cron. Generic: the author is --handle, the
working folder is --dir, and that folder belongs OUTSIDE any repository, since
it holds a person's content (a folder inside a git repository is refused).
It is not speech recognition and not a writing engine: it reads an archive of
post ids, fetches public pages under its own limits, and counts.

Every run, in order:
  1. collect post ids from every source present in this folder:
       ids.txt            one activity id per line (the in-page walker's dump)
       slugs.txt          public post slugs found by search
       Shares.csv         LinkedIn's own archive ("Get a copy of your data"), if dropped here
  2. fetch the public page of each id not fetched yet (one polite request every
     3 seconds, at most MAX_FETCH per run, never the same page twice)
  3. rebuild dataset.jsonl from the pages on disk
  4. rebuild REPORT.md: counts only (cadence per month, top posts, what is missing)

A page without structured data is kept as NO-DATA with its reason, never dropped.
Exit 0 when the run completed, 1 when a source could not be read (nothing is
written when no id was found), 2 when nothing was started: a bad folder or
handle, a folder inside a repository, no archive source in the folder, or
sources that name no post id. Zero ids is never a completed run.
"""
import csv
import datetime
import json
import os
import re
import stat
import subprocess
import sys
import time

HERE = os.getcwd()   # set from --dir in main()
RAW = os.path.join(HERE, "raw")
HANDLE = ""          # set from --handle in main()
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
MAX_FETCH = 60
PAUSE_S = 3
ID_RE = re.compile(r"(?:activity[-:]|urn:li:activity:|urn:li:share:)(\d{15,20})")


def when(activity_id):
    """LinkedIn ids carry their creation time in the high bits (checked against page dates)."""
    return datetime.datetime.fromtimestamp((int(activity_id) >> 22) / 1000, datetime.timezone.utc)


SOURCES = ("ids.txt", "slugs.txt", "Shares.csv")


def inside_repository(path):
    """The repository root holding `path` (the nearest parent carrying .git), or None. Personal content stays outside
    every repository, so a working folder under one is refused before anything is read or written."""
    here = os.path.realpath(path)
    while True:
        if os.path.exists(os.path.join(here, ".git")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


def not_regular(path):
    """Why `path` must not be written over: it exists and is a symlink, a folder or a device (anything but a regular
    file), so a hostile archive cannot aim an output at a file elsewhere. None when it is absent or a regular file."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return "%s cannot be examined: %s" % (path, exc)
    if stat.S_ISLNK(st.st_mode):
        return "%s is a symlink, so it is not written" % path
    if not stat.S_ISREG(st.st_mode):
        return "%s is not a regular file, so it is not written" % path
    return None


def fresh_temp(path):
    """A fresh, exclusively created temp file beside `path` (O_CREAT|O_EXCL|O_NOFOLLOW, mode 600): its descriptor
    and its name. Nothing pre-existing is ever opened, so no link can redirect the write."""
    folder, name = os.path.split(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    while True:
        tmp = os.path.join(folder, ".%s.%d.%s.tmp" % (name, os.getpid(), os.urandom(4).hex()))
        try:
            return os.open(tmp, flags, 0o600), tmp
        except FileExistsError:
            continue


def write_file(path, text):
    """Write `text` to `path` by a fresh temp file and os.replace, refusing a target that is not absent or a regular
    file. Returns the problem, or None when written. os.replace swaps the directory entry, so even a link planted
    between the check and the swap is replaced, never written through."""
    why = not_regular(path)
    if why:
        return why
    fd, tmp = fresh_temp(path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)  # a lone surrogate in a page raises UnicodeEncodeError here, a named problem below
        os.replace(tmp, path)
        tmp = None
    except (OSError, UnicodeError) as exc:
        return "%s could not be written: %s" % (path, exc)
    finally:  # any failure, named above or not, removes the temp file
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return None


def raw_folder_problem():
    """Why raw/ cannot hold pages: it is a symlink, not a folder, or resolves outside the archive folder. None when it
    is absent (it is then created) or a real folder inside the archive."""
    if os.path.islink(RAW):
        return "%s is a symlink, so no page is written there" % RAW
    if os.path.exists(RAW) and not os.path.isdir(RAW):
        return "%s is not a folder, so no page is written there" % RAW
    inside = os.path.realpath(HERE)
    real = os.path.realpath(RAW)
    if real != os.path.join(inside, "raw"):
        return "%s resolves to %s, outside the archive folder" % (RAW, real)
    return None


def collect_ids():
    """(ids, problems, sources found): the deduplicated post ids of every source present (one id is one id however
    many sources or lines name it), the sources that could not be read, and the names of the sources present."""
    ids, problems, found = set(), [], []
    for name in ("ids.txt", "slugs.txt"):
        path = os.path.join(HERE, name)
        if not os.path.exists(path):
            continue
        found.append(name)
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().split("\n")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append("%s unreadable: %s" % (name, exc))
            continue
        for line in lines:
            line = line.strip()
            if re.fullmatch(r"\d{15,20}", line):
                ids.add(line)
            else:
                ids.update(ID_RE.findall(line))
    shares = os.path.join(HERE, "Shares.csv")
    if os.path.exists(shares):
        found.append("Shares.csv")
        try:
            with open(shares, encoding="utf-8-sig", newline="") as fh:
                for row in csv.DictReader(fh):
                    ids.update(ID_RE.findall(" ".join(str(v) for v in row.values())))
        except (OSError, csv.Error, UnicodeDecodeError) as exc:
            problems.append("Shares.csv unreadable: %s" % exc)
    return ids, problems, found


def fetch_missing(ids, problems, limit=MAX_FETCH):
    """Fetch the pages not yet on disk, at most `limit` (0 under --no-fetch), each into a fresh temp file in raw/
    that is renamed into place only when the target is absent or a regular file; a page whose target is a link is a
    named problem and is never a fetch candidate. Returns (fetched, still waiting)."""
    if limit:
        os.makedirs(RAW, exist_ok=True)
    todo = []
    for activity in sorted(ids, reverse=True):
        target = os.path.join(RAW, activity + ".html")
        why = not_regular(target) if limit else None  # a linked page is never a fetch candidate; parse() names it
        if why:
            problems.append(why)
        elif not os.path.exists(target) or os.path.getsize(target) < 5000:
            todo.append(activity)
    fetched = 0
    for activity in todo[:limit]:
        target = os.path.join(RAW, activity + ".html")
        fd, tmp = fresh_temp(target)
        os.close(fd)
        try:
            done = subprocess.run(["curl", "-sL", "-m", "40", "-A", UA, "-o", tmp,
                                   "https://www.linkedin.com/feed/update/urn:li:activity:%s/" % activity])
            why = not_regular(target)
            if done.returncode == 0 and why is None:
                os.replace(tmp, target)
                tmp = None
                fetched += 1
            elif why:
                problems.append(why)
            else:
                sys.stderr.write("voice_pipeline: fetch of %s failed, curl exit %d; it is retried on the next run\n"
                                 % (activity, done.returncode))
        finally:  # a failed or refused fetch, or any exception, removes the temp file (a SIGTERM does not: limit)
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        time.sleep(PAUSE_S)
    return fetched, max(0, len(todo) - limit)


def parse(activity):
    row = {"activity": activity, "decoded_date": when(activity).strftime("%Y-%m-%d")}
    path = os.path.join(RAW, activity + ".html")
    try:
        why = not_regular(path)
        if why:  # named by its place in the archive: a long archive path must not push the cause past the cut below
            raise LookupError(why.replace(path, "raw/%s.html" % activity).replace("not written", "not read"))
        text = open(path, encoding="utf-8", errors="replace").read()
        blocks = []
        for m in re.findall(r'<script type="application/ld\+json">(.*?)</script>', text, re.S):
            try:
                blocks.append(json.loads(m))
            except ValueError:
                continue
        data = next((b for b in blocks if isinstance(b, dict)
                     and b.get("interactionStatistic") is not None), None)
        if data is None:
            raise LookupError("no structured data (login wall, deleted post, or a repost)")
        stats = {s["interactionType"].rsplit("/", 1)[-1]: s["userInteractionCount"]
                 for s in data["interactionStatistic"]}
        slug = re.search(r"linkedin\.com/posts/([A-Za-z0-9%_\-]+)", text)
        row.update(status="OK", published=data.get("datePublished"),
                   likes=stats.get("LikeAction"),
                   comments=stats.get("CommentAction", data.get("commentCount")),
                   text=data.get("articleBody") or data.get("description") or "",
                   headline=data.get("headline"),
                   has_video=bool(data.get("contentUrl") or data.get("embedUrl")),
                   has_image=bool(data.get("image")),
                   url="https://www.linkedin.com/posts/" + slug.group(1) if slug else None,
                   own_post=bool(slug and slug.group(1).startswith(HANDLE + "_")))
    except (OSError, LookupError, KeyError, TypeError) as exc:
        row.update(status="NO-DATA", reason="%s: %s" % (type(exc).__name__, str(exc)[:140]))
    return row


def report(rows, fetched, left, problems):
    ok = [r for r in rows if r["status"] == "OK" and r.get("own_post") and r["text"].strip()]
    for r in ok:
        r["eng"] = (r["likes"] or 0) + 3 * (r["comments"] or 0)
    months = {}
    for r in ok:
        months[r["decoded_date"][:7]] = months.get(r["decoded_date"][:7], 0) + 1
    out = ["# LinkedIn posts: mechanical report",
           "", "Generated %s by voice_pipeline.py. Counts only, no model involved."
           % datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"),
           "", "- ids known: %d" % len(rows),
           "- own posts with text and metrics: %d" % len(ok),
           "- NO-DATA pages: %d" % sum(r["status"] != "OK" for r in rows),
           "- fetched this run: %d, still waiting: %d" % (fetched, left)]
    out += ["- PROBLEM: " + p for p in problems]
    out += ["", "## Posts per month (from the id's own timestamp)", ""]
    out += ["- %s: %d" % (m, n) for m, n in sorted(months.items())]
    out += ["", "## Top 25 by likes + 3 x comments", ""]
    for r in sorted(ok, key=lambda r: -r["eng"])[:25]:
        first = r["text"].strip().split("\n")[0][:90]
        out.append("- %s | %s likes, %s comments | %d words | %s" % (
            r["decoded_date"], r["likes"], r["comments"], len(r["text"].split()), first))
    return write_file(os.path.join(HERE, "REPORT.md"), "\n".join(out) + "\n")


def main(argv=None):
    import argparse
    global HERE, RAW, HANDLE
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", required=True, help="working folder, outside any repository")
    ap.add_argument("--handle", required=True, help="the author's public profile handle")
    ap.add_argument("--no-fetch", action="store_true", help="rebuild from pages already on disk")
    args = ap.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9\-]{3,100}", args.handle):
        print("NO-DATA: --handle is not a plain profile handle")
        return 2
    HERE, HANDLE = os.path.abspath(args.dir), args.handle
    RAW = os.path.join(HERE, "raw")
    if not os.path.isdir(HERE):
        print("NO-DATA: %s is not a folder" % HERE)
        return 2
    if os.path.islink(HERE):
        print("NO-DATA: %s is a symlink to %s, not the folder it says; name the real folder, nothing was written"
              % (HERE, os.path.realpath(HERE)))
        return 2
    repo = inside_repository(HERE)
    if repo is not None:
        print("NO-DATA: %s is inside the repository %s; a person's content stays outside every repository, so "
              "nothing was read or written" % (HERE, repo))
        return 2
    ids, problems, found = collect_ids()
    if not found:
        print("NO-DATA: %s holds no archive source (%s); nothing was written" % (HERE, ", ".join(SOURCES)))
        return 2
    if not ids:
        print("NO-DATA: %s names no post id; nothing was written%s" % (
            ", ".join(found), " | PROBLEMS: " + "; ".join(problems) if problems else ""))
        return 1 if problems else 2
    refused = [w for w in (not_regular(os.path.join(HERE, n)) for n in ("dataset.jsonl", "REPORT.md")) if w]
    refused += [w for w in [raw_folder_problem()] if w]
    if refused:
        print("NO-DATA: %s; nothing was written" % "; ".join(refused))
        return 2
    fetched, left = fetch_missing(ids, problems, 0 if args.no_fetch else MAX_FETCH)
    rows = [parse(i) for i in sorted(ids)]
    for why in (write_file(os.path.join(HERE, "dataset.jsonl"),
                           "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)),
                report(rows, fetched, left, problems)):
        if why:
            print("NO-DATA: %s" % why)
            return 2
    print("ids %d | OK %d | NO-DATA %d | fetched %d | waiting %d%s" % (
        len(rows), sum(r["status"] == "OK" for r in rows),
        sum(r["status"] != "OK" for r in rows), fetched, left,
        " | PROBLEMS: " + "; ".join(problems) if problems else ""))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
