#!/usr/bin/env python3
"""Detect whether a project's progress page is missing or stale, mechanically.

WHY THIS EXISTS
  The founder's progress page (a Gantt-style HTML page) is how a
  non-engineer sees where a project stands. Sessions keep BUILDING it and
  then never SHOWING it, so the founder has to ask for it every time. This
  project's own founding law is that a rule written in a prompt is NOT a
  control; a control is a check that runs. This tool is that check: the
  mechanical half that detects and reports whether a page is owed. It does
  not build or show the page itself; a human or a model still does that.

THE CONTRACT
  A PLAN signals a project has committed to a shape of work. A PAGE is the
  progress view that shows the founder where that work stands. Both are
  detected purely by filename pattern, module level so an addition to
  either set shows up in a diff:

    PLAN_GLOBS  -- docs/plan/*PLAN*.md, docs/plan/*WBS*.md, PLAN.md (root)
    PAGE_GLOBS  -- docs/plan/*GANTT*.html, docs/plan/*BOARD*.html,
                   PROJECT-VIEW.html (root)

  Exactly four real verdicts, one line each, then exit:

    NO-PLAN        no plan signal found.                          exit 0
    CURRENT         a plan and a page exist; page mtime is newer
                    than the newest plan file's mtime.             exit 0
    OWED-MISSING    a plan exists, no page exists.                 exit 1
    OWED-STALE      a plan and a page exist, but the page is not
                    newer than the newest plan file.                exit 1
    NO-DATA         could not determine (for example the root
                    cannot be resolved). NEVER a pass.              exit 2

  Exit codes are load bearing: 0 nothing owed, 1 page owed, 2 could not
  tell. NO-DATA output always starts with the literal string "NO-DATA:".

EFFECT CLASS: pure_read
  This tool writes NOT ONE BYTE anywhere, ever: no file writes, no
  directories created, no store touched. It never constructs a writable
  bm_store.Store (doing so is itself a write: it probes the filesystem,
  edits git excludes, opens a WAL, and can migrate a schema). The answer
  comes from `glob` and `os.path.getmtime`, both read-only stat/list calls;
  bm_store is loaded only to borrow its `resolve_root()` for --root
  resolution, and is never asked to open a store.

BE SILENT-SAFE
  This runs at every session start. Any unexpected exception anywhere in
  this module is caught by `main` and reported as NO-DATA at exit 2,
  carrying the exception's type and message, rather than raising: a
  crashing check inside a SessionStart hook would break every session on
  this machine, not just this one check.

Python 3.9, standard library only. No network. No subprocess.
No em or en dashes anywhere in this file or its output.

Usage:
  python3 tools/bm_progress_check.py [status] [--root PATH]

`status` is the only verb and is also the default when no verb is given.
"""

import glob
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

#: Any ONE of these matching under the project root means a plan exists.
#: Patterns are relative to the resolved root, matched with `glob.glob`.
#: Module level and visible in a diff on purpose: adding a plan shape is a
#: one-line change here, never a change to the matching logic below it.
PLAN_GLOBS = (
    "docs/plan/*PLAN*.md",
    "docs/plan/*WBS*.md",
    "PLAN.md",
)

#: Any ONE of these matching under the project root is the progress page.
#: *BOARD* joined on 2026-08-15 because the control had gone blind to the
#: real page: a session published docs/plan/NORTH-STAR-PUSH-BOARD.html,
#: linked it from PROJECT.md as the project's stable artifact, and this
#: check went on reporting OWED (stale) against a GANTT file three days
#: older, because "board" is the word the founder actually uses and no
#: pattern here matched it. A check that cannot see the page it exists to
#: police is the failure class this repository files as "the sweep you
#: wrote is not the control", so the pattern list widened rather than the
#: page being renamed to satisfy the tool.
#: The ROOT patterns joined 2026-08-24, for the second occurrence of exactly
#: the failure the paragraph above describes, one directory up. The Brother
#: umbrella publishes GANTT.html AT ITS ROOT, names it in PROJECT.md as the
#: project's one stable artifact, and regenerates it from
#: scripts/gen_command_center.py at every closed loop. This check globbed only
#: docs/plan/ and the single literal PROJECT-VIEW.html, so it reported OWED
#: permanently over a page that was current and freshly published, and no
#: session could clear it by doing the work the nag asked for.
#:
#: Widening again rather than moving the page, for the same reason as last
#: time: the page is where its project puts it, and a control that cannot see
#: the thing it polices is the defect. The general form, which is why this
#: comment is longer than the fix: A PERMANENT NAG IS NOT A BACKLOG ITEM, IT IS
#: A BROKEN CONTROL. A reminder nobody has closed in weeks is worth ONE trace
#: before it is worth another attempt.
PAGE_GLOBS = (
    "docs/plan/*GANTT*.html",
    "docs/plan/*BOARD*.html",
    "*GANTT*.html",
    "*BOARD*.html",
    "PROJECT-VIEW.html",
)


# ---------------------------------------------------------------------------
# Root resolution. Honors --root when given; otherwise borrows bm_store's
# resolve_root() purely for that lookup, never touching the store itself.
# ---------------------------------------------------------------------------

def _load_bm_store():
    """Load bm_store.py by PATH, the same shape bm_bash_audit.py and
    bm_runtimes.py use: this tool is invoked by Claude Code (and by a
    SessionStart hook) with an arbitrary cwd, and a plain `import bm_store`
    would depend on whichever sys.path the caller happened to have. Never
    raises: an unimportable module degrades to an explicit NO-DATA reason,
    not a crash."""
    try:
        import importlib.util
        path = os.path.join(HERE, "bm_store.py")
        spec = importlib.util.spec_from_file_location(
            "bm_store_for_progress_check", path)
        if spec is None or spec.loader is None:
            return None, "could not build an import spec for bm_store.py"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, None
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)


def resolve_project_root(explicit_root):
    """Return (root, None) or (None, reason). `explicit_root` (--root) always
    wins and is checked directly against the filesystem; nothing else here
    reads bm_store at all in that case. Otherwise bm_store.resolve_root() is
    borrowed, which returns a (path, source) TUPLE that must be unpacked."""
    if explicit_root:
        candidate = os.path.realpath(os.path.expanduser(explicit_root))
        if not os.path.isdir(candidate):
            return None, "no such directory: %s" % candidate
        return candidate, None
    mod, err = _load_bm_store()
    if mod is None:
        return None, ("could not load bm_store.py to resolve the project "
                      "root (%s); pass --root explicitly" % err)
    root, _source = mod.resolve_root()
    if not root:
        return None, ("nothing anchors a BrotherMode project here (no "
                      "BROTHERMODE_ROOT, no marker directory, no git repo "
                      "found); pass --root explicitly")
    return root, None


# ---------------------------------------------------------------------------
# Pure filesystem inspection: glob only the named patterns, read only their
# mtimes. No directory walk, no store, nothing else opened.
# ---------------------------------------------------------------------------

def _all_matches(root, patterns):
    """Every existing file matching any pattern, deduplicated. Separate from
    _newest_match because the verdict needs to NAME what it did not judge,
    and a picker that only ever returns one path cannot say that."""
    seen = set()
    for pattern in patterns:
        for path in glob.glob(os.path.join(root, pattern)):
            if os.path.isfile(path):
                seen.add(path)
    return sorted(seen)


def _newest_match(root, patterns):
    """The (path, mtime) of the most recently modified file across all
    `patterns` (each relative to `root`), or (None, None) if none match.
    Ties broken by path so the result is deterministic across runs."""
    matches = []
    for pattern in patterns:
        for path in glob.glob(os.path.join(root, pattern)):
            if os.path.isfile(path):
                matches.append((os.path.getmtime(path), path))
    if not matches:
        return None, None
    matches.sort(key=lambda entry: (-entry[0], entry[1]))
    mtime, path = matches[0]
    return path, mtime


def _fmt_mtime(mtime):
    """A founder-readable timestamp, local time, for the OWED-STALE line."""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))


def _rel(root, path):
    return os.path.relpath(path, root)


def check_status(root):
    """Return (verdict, exit_code, line) for `root`. Pure read: only globs
    the two pattern sets above and stats the matches."""
    plan_path, plan_mtime = _newest_match(root, PLAN_GLOBS)
    if plan_path is None:
        return ("NO-PLAN", 0, "progress page: no plan yet, nothing owed.")

    plan_rel = _rel(root, plan_path)
    page_path, page_mtime = _newest_match(root, PAGE_GLOBS)

    if page_path is None:
        return ("OWED-MISSING", 1,
                "progress page: OWED. Plan is ready at %s but no progress "
                "page exists yet. Build docs/plan/*GANTT*.html, "
                "docs/plan/*BOARD*.html or "
                "PROJECT-VIEW.html so the founder can see where things "
                "stand." % plan_rel)

    page_rel = _rel(root, page_path)
    # M9. Several files can match PAGE_GLOBS at once, and this check judged
    # the newest silently. Measured 2026-08-19: three matched, it judged
    # docs/GANTT.html and printed a clean CURRENT, while PROJECT.md names a
    # DIFFERENT page as "the one current board" by founder decision. A pass
    # computed from a file nobody considers canonical is worse than the
    # OWED it replaced, because an OWED gets investigated and a pass does
    # not. Which page is canonical is a founder decision this tool must not
    # invent, so it does the one thing it can do honestly: name the others
    # so the ambiguity is visible rather than silently resolved by mtime.
    others = [_rel(root, p) for p in _all_matches(root, PAGE_GLOBS)
              if p != page_path]
    also = ""
    if others:
        also = (" %d other page(s) also match and were NOT the one judged: %s."
                " If the canonical board is one of those, this verdict is"
                " about the wrong file." % (len(others), ", ".join(sorted(others))))
    if page_mtime > plan_mtime:
        return ("CURRENT", 0, "progress page: current at %s.%s" % (page_rel, also))

    return ("OWED-STALE", 1,
            "progress page: OWED (stale). %s (%s) is older than the plan "
            "%s (%s). Refresh the page.%s" % (
                page_rel, _fmt_mtime(page_mtime),
                plan_rel, _fmt_mtime(plan_mtime), also))


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def _parse_argv(argv):
    """Return (verb, root, error). `error` set means stop and report a plain
    usage failure (exit 2, no "NO-DATA:" prefix: that prefix is reserved for
    "could not determine the verdict", not "bad arguments")."""
    args = list(argv)
    verb = "status"
    if args and not args[0].startswith("--"):
        verb = args.pop(0)
    if verb != "status":
        return None, None, "bm_progress_check: unknown verb: %s" % verb

    root = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--root":
            if i + 1 >= len(args):
                return None, None, "bm_progress_check: --root requires a value"
            root = args[i + 1]
            i += 2
        else:
            return None, None, "bm_progress_check: unknown argument: %s" % arg
    return verb, root, None


def _run(argv):
    """The real body of main, split out so `main` can wrap the whole thing
    in one try/except and guarantee NO-DATA on any unexpected exception."""
    _verb, root_arg, err = _parse_argv(argv)
    if err:
        sys.stderr.write(err + "\n")
        return 2

    root, reason = resolve_project_root(root_arg)
    if root is None:
        sys.stdout.write("NO-DATA: %s\n" % reason)
        return 2

    _verdict, exit_code, line = check_status(root)
    sys.stdout.write(line + "\n")
    return exit_code


def main(argv):
    try:
        return _run(argv)
    except Exception as exc:
        sys.stdout.write("NO-DATA: %s: %s\n" % (type(exc).__name__, exc))
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
