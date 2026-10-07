#!/usr/bin/env python3
"""Claude native worker, adapter half (owner order 2026-10-02: "The way you give briefing and tasks to Claude cannot be
the same as deepseek"; then "wire the Claude-native worker into the loop"). Turns a checkout a Claude session edited with
real tools into the build JSON the grader already accepts, so grading, probes and landing stay unchanged.

usage: native_adapter.py <workdir> <base_rev> <out build.json>      exit 0 written, exit 2 refused (nothing written)
- A file that existed at base_rev becomes {path, find: the whole base text, replace: the whole new text}: unique by
  construction, so there is no find string to get wrong (the grader takes new_file_content for NEW files only).
  A new file becomes {path, new_file_content}. Files named test_*.py or *_test.py go under "tests", the rest under "edits".
- The worker leaves three files under <workdir>/.brother/: done_check.txt (one command), mutations.json (3 or more
  {name, path, find, replace, caught_by}; replace may be empty, deleting a guard is a mutation) and notes.json (optional,
  {"callers_checked": [...], "unknowns": [...]}).
- REFUSED, nothing written: not a checkout, a deleted or renamed file (the build format has neither), no changed file, a
  file that is not utf-8, an edited file that was empty at base (an empty find cannot be anchored), a missing or
  multi-line done check, fewer than 3 mutations, a malformed mutation, or a find not present exactly once in its file.
  An unreadable input refuses; it never reads as a clean build.
Test: python3 -B scripts/loop/test_native_adapter.py
"""
import difflib, json, os, stat, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grade_build as _G  # noqa: E402  the ONE uniqueness test (overlapping matches), so adapter and grader never disagree

META = ".brother"
SCRATCH = ".tmp"
#: the loop's own files that can appear in a seat and are never product content: the STATUS writers' lock (2026-10-03,
#: every native build carried it and the pool serialized unrelated units on it)
LOOP_ARTIFACTS = (".status.lock",)
HUNK_LINES_MAX = 4000   # files longer than this keep the whole file find (review: 20,000 lines took minutes)
HUNK_CONTEXT_MAX = 32   # lines of context tried per hunk; past it the whole file is the find (second review: unbounded
                        # context made 20,000 repeated lines take minutes after a 45 minute session)   # the session's TMPDIR inside its seat: never part of a build
#: NOTHING THE SESSION WROTE RUNS OUT HERE (Fable review 2026-10-02, reproduced: a seat whose .git the session replaced
#: with a git dir carrying core.fsmonitor ran that hook through this module's `git status`, outside the sandbox).
SAFE_GIT = ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"]


class Refused(Exception):
    """The checkout cannot be turned into a faithful build; the message says why."""


def _git(wd, *args):
    try:
        p = subprocess.run(SAFE_GIT + list(args), cwd=wd, capture_output=True)
    except OSError as exc:   # a missing or unreadable workdir
        raise Refused("git %s could not run in %s: %s" % (args[0], wd, exc))
    if p.returncode != 0:
        raise Refused("git %s failed: %s" % (args[0], p.stderr.decode("utf-8", "replace").strip()[:200]))
    return p.stdout


def _text(raw, path):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise Refused("%s is not utf-8; the build format carries text only" % path)


def changed_paths(wd, base):
    """Every path changed or added since base, in the working tree or in commits the worker made; .brother/ excluded."""
    out, seen = _git(wd, "status", "--porcelain=v1", "-z", "--untracked-files=all").split(b"\0"), set()
    i = 0
    while i < len(out):
        entry = out[i]; i += 1   # an empty entry yields path "", dropped by the filter below
        code, path = entry[:2].decode("ascii", "replace"), entry[3:].decode("utf-8", "replace")
        if "R" in code or "C" in code:
            raise Refused("%s was renamed or copied; the build format has no rename" % path)
        if "D" in code:
            raise Refused("%s was deleted; the build format has no delete" % path)
        if "T" in code:
            raise Refused("%s changed type (file, symlink); the build carries file contents only" % path)
        seen.add(path)
    diff = _git(wd, "diff", "--no-renames", "--name-status", "-z", base, "HEAD").split(b"\0")
    for status, raw in zip(diff[0::2], diff[1::2]):
        path = raw.decode("utf-8", "replace")
        if status.startswith(b"D"):
            raise Refused("%s was deleted; the build format has no delete" % path)
        if status.startswith(b"T"):
            raise Refused("%s changed type (file, symlink); the build carries file contents only" % path)
        seen.add(path)
    return sorted(p for p in seen if p and not any(p == d or p.startswith(d + "/") for d in (META, SCRATCH))
                  and os.path.basename(p) not in LOOP_ARTIFACTS)


def hunks(before, after):
    """The smallest find/replace pairs that turn `before` into `after`, applied in order, each find present exactly
    once where it is applied (the grader's rule), [] when the texts are equal, None when no such pairs exist (the caller
    then keeps the whole base text as the find). WHY (Fable review 2026-10-02): a whole file find is refused the moment
    any landing touches that file during a 45 minute session; a hunk find survives every landing elsewhere in the file.
    Proven, not trusted: the pairs are replayed on `before` and must give `after` exactly, or None."""
    a, b = before.splitlines(True), after.splitlines(True)
    if max(len(a), len(b)) > HUNK_LINES_MAX:
        return None   # difflib is quadratic on repetitive text: past the cap the whole file is the find
    ops = [op for op in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op[0] != "equal"]
    pairs = []
    for _tag, i1, i2, j1, j2 in ops:
        for k in range(min(len(a), HUNK_CONTEXT_MAX) + 1):
            lo, hi = max(0, i1 - k), min(len(a), i2 + k)
            find = "".join(a[lo:hi])
            if find and len(_G.occurrences(before, find)) == 1:
                break
        else:
            return None
        pairs.append((find, "".join(b[j1 - (i1 - lo):j2 + (hi - i2)])))
    text = before
    for find, repl in pairs:
        nxt = _G.replace_once(text, find, repl)
        if nxt is None:
            return None
        text = nxt
    return pairs if text == after else None


def _read_meta(wd):
    meta = os.path.join(wd, META)
    try:
        with open(os.path.join(meta, "done_check.txt"), "rb") as fh:
            done = _text(fh.read(), "done_check.txt").strip()
        with open(os.path.join(meta, "mutations.json"), "rb") as fh:
            muts = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError) as exc:   # UnicodeDecodeError is a ValueError
        raise Refused("missing or unreadable .brother file: %s" % exc)
    notes = {}
    if os.path.isfile(os.path.join(meta, "notes.json")):
        try:
            with open(os.path.join(meta, "notes.json"), "rb") as fh:
                notes = json.loads(fh.read().decode("utf-8"))
        except (OSError, ValueError) as exc:
            raise Refused("notes.json is not JSON: %s" % exc)
        if not isinstance(notes, dict):
            raise Refused("notes.json is not JSON: an object is required")
    return done, muts, notes


def _read_regular(wd, path):
    """The text of path inside wd, read ONLY if it is a regular file there, or Refused. Sixth review 2026-10-02: this
    module runs OUTSIDE the sandbox, so a session that planted a symlink (to any file the loop's user can read) or a
    FIFO (which blocks the read forever) turned the adapter into its reader. One rule for every read: the grader's own
    path check (grade_build.dest: no absolute path, no .., no .git, no symlinked parent), then lstat must say regular,
    then an O_NOFOLLOW, O_NONBLOCK open, so a swap between the check and the open still cannot follow or block."""
    full, why = _G.dest(wd, path)
    if full is None:
        raise Refused(why)
    try:
        if not stat.S_ISREG(os.lstat(full).st_mode):
            raise Refused("%s is not a regular file (a symlink, FIFO or device); the build carries file contents only" % path)
        fd = os.open(full, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as fh:
            return _text(fh.read(), path)
    except FileNotFoundError:
        raise Refused("%s does not exist" % path)
    except OSError as exc:
        raise Refused("%s cannot be read: %s" % (path, exc))


def build_from_checkout(wd, base):
    """The grader's build dict for the checkout at wd edited since base, or Refused."""
    changed = changed_paths(wd, base)
    if not changed:
        raise Refused("no file changed since %s" % base)
    edits, tests, content = [], [], {}
    for path in changed:
        now = _read_regular(wd, path)   # carriage returns ride as they are: grader and lander apply exact bytes
        shown = subprocess.run(SAFE_GIT + ["show", "%s:%s" % (base, path)], cwd=wd, capture_output=True)
        if shown.returncode == 0:
            before = _text(shown.stdout, path)
            if not before:
                raise Refused("%s is empty at base; an empty find cannot be anchored" % path)
            pairs = hunks(before, now)
            if pairs == []:
                continue   # a mode or timestamp change only: nothing for the grader to apply
            items = ([{"path": path, "find": f, "replace": r} for f, r in pairs] if pairs
                     else [{"path": path, "find": before, "replace": now}])
        else:
            items = [{"path": path, "new_file_content": now}]
        content[path] = now
        name = os.path.basename(path)
        (tests if name.startswith("test_") or name.endswith("_test.py") else edits).extend(items)
    if not edits and not tests:
        raise Refused("no file changed since %s" % base)
    done, muts, notes = _read_meta(wd)
    if not done or "\n" in done:
        raise Refused("done_check.txt must hold exactly one command")
    if not isinstance(muts, list) or len(muts) < 3:
        raise Refused("mutations.json must list 3 or more mutations")
    for m in muts:
        if not isinstance(m, dict) or not all(isinstance(m.get(k), str) and m.get(k) for k in ("name", "path", "find")) \
                or not isinstance(m.get("replace"), str):
            raise Refused("a mutation lacks name, path, find or a string replace")
        text = content.get(m["path"])
        if text is None:
            try:
                text = _read_regular(wd, m["path"])
            except Refused as exc:
                raise Refused("mutation %s names %s: %s" % (m["name"], m["path"], exc))
        if len(_G.occurrences(text, m["find"])) != 1:
            raise Refused("mutation %s: find occurs %d times in %s, needs exactly once"
                          % (m["name"], len(_G.occurrences(text, m["find"])), m["path"]))
    return {"edits": edits, "tests": tests, "done_check": done, "mutations": muts,
            "callers_checked": notes.get("callers_checked", []), "unknowns": notes.get("unknowns", [])}


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    wd, base, out = argv
    try:
        build = build_from_checkout(wd, base)
    except Refused as exc:
        print("ADAPTER REFUSED: %s" % exc)
        return 2
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(build, fh, indent=1)
    os.replace(tmp, out)   # whole or absent: the round's cut globs the folder while workers are still writing
    print("ADAPTER OK: %d edit(s), %d test file(s), %d mutation(s) -> %s"
          % (len(build["edits"]), len(build["tests"]), len(build["mutations"]), out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
