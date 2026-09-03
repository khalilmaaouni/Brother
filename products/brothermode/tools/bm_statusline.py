#!/usr/bin/env python3
"""tools/bm_statusline.py: an opt in Claude Code status line for BrotherMode.

Design: docs/program/absolute-lead/DESIGN-visual-surface.md section 14,
item 1 (the founder's 2026-08-05 "answer 7"). Contract verified against:
docs/program/absolute-lead/research/visual-surface/LENS-D-claude-surface.md
section 6, and against the real code of tools/bm_view.py and
tools/bm_lead.py (see the note below on why this does not call bm-view).
The one paste settings block a founder needs is docs/STATUS-LINE.md; this
file is not installed by anything in BrotherMode, on purpose.

WHAT THIS IS
  A status line SCRIPT in the exact sense the Claude Code contract means
  the word: a program that reads one JSON object on stdin and prints at
  most one line on stdout. BrotherMode SHIPS this file. Nothing in
  BrotherMode installs it: plugin settings.json supports only the `agent`
  and `subagentStatusLine` keys, never `statusLine`, and the separate
  `footerLinksRegexes` setting is read only from the founder's own user
  settings, never from a plugin (LENS-D section 6, and confirmed directly
  against https://www.schemastore.org/claude-code-settings.json while
  writing docs/STATUS-LINE.md). The founder pastes one settings block into
  his own Claude Code settings himself.

WHAT IT SHOWS
  One line, plain language, built from two of the same eight fields the
  founder's own status view already uses: tools/bm_lead.py's
  collect_status returns Goal, Direction, Progress, Time remaining,
  Decision needed, Risk, Evidence and Next step, and this prints Progress
  next to whether a Decision needed stands. For example: "2 of 5 tasks
  accepted, decision waiting". No jargon, no store ids, no colour codes.

WHY THIS DOES NOT CALL `bm-view render`
  The design names `bm-view render --json` as the intended source. Read
  against the code rather than the design's expectation: cmd_render's
  --json branch (tools/bm_view.py, _render_once) prints
  {path, rel_path, fingerprint, changed, artifact_url}, never a status
  payload, and on every call it WRITES PROJECT-VIEW.html to disk and
  records a view row in the store. A status line that ran that on every
  keystroke would break this file's own first law below on every single
  invocation, and it would still have no status fields to show. So this
  file calls the cheapest read only accessor that actually carries the
  eight fields instead: tools/bm_lead.py's collect_status, against a
  bs.ReadOnlyStore. That is the exact same collector tools/bm_view.py's
  own header section and tools/bm_lead.py's own `status` subcommand
  already read from; no second collector is defined here.

FAIL SILENT IS LAW
  A status line runs on nearly every turn of a session (event driven,
  debounced at 300ms per the Claude Code contract). On ANY error at all:
  no BrotherMode project here, no store yet, setup not consented, a
  corrupt config, a malformed stdin payload, a missing field, more than
  one project in this folder, an exception of any kind, this prints
  NOTHING, to stdout or stderr, and exits 0. A status line that prints a
  traceback into the founder's terminal bar is a worse failure than a
  status line that stays blank. This file writes no file, ever: it never
  constructs a writable Store, only bs.ReadOnlyStore, and it calls no
  function that records anything.

Python 3.9, standard library only. No network. No subprocess.
No em or en dashes anywhere in this file, its comments, or its output.

Usage (this is what Claude Code itself runs, per docs/STATUS-LINE.md):
  python3 tools/bm_statusline.py < payload.json
  bm-statusline < payload.json   # the packaged console script
"""

import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def _load(name):
    """Load a sibling tool by PATH, the same technique every other CLI in
    this project uses (tools/bm_view.py, tools/bm_lead.py): a hostile
    sys.path cannot shadow bm_store this way."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_setup():
    """scripts/setup.py, loaded by explicit path exactly the way
    tools/bm_view.py's _load_bm_setup and tools/bm_lead.py's
    _load_bm_setup do: there is no second definition of what consent
    means anywhere in this project, and this file does not start one."""
    spec = importlib.util.spec_from_file_location(
        "bm_setup_for_statusline", os.path.join(REPO, "scripts", "setup.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _consented():
    """True only when scripts/setup.py's own is_consented says so. Fails
    CLOSED on any load or read error: no config, a corrupt config, an
    import failure, all mean no line, same posture as every write gate in
    this project (tools/bm_view.py's and tools/bm_lead.py's
    _consent_state). This function itself never raises."""
    try:
        setup_mod = _load_setup()
        cfg, _read_error = setup_mod.read_config()
        return bool(setup_mod.is_consented(cfg))
    except Exception:
        return False


def _project_id(store):
    """The one project this cwd's store belongs to, or None when there is
    not exactly one. A status line that guessed among several projects
    would show the wrong one silently, which is worse than showing none;
    tools/bm_view.py's own _only_project makes the identical call."""
    projects = store.list_projects(raw=True)
    if len(projects) != 1:
        return None
    return projects[0].get("project_id")


def one_line(fields):
    """The founder facing line, built from two of collect_status's eight
    fields: Progress (bl.collect_status, tools/bm_lead.py) says where the
    work stands in the founder's own words, and Decision needed says
    whether he is being asked something. Returns None when Progress
    carries nothing to show rather than printing a half sentence.

    `fields` is the (label, value, extra_lines) list collect_status
    returns; only label and value are read here, on purpose, since the
    extra lines are the multi line detail the founder's status view
    prints and a status line is one line by contract."""
    by_label = dict((label, value) for label, value, _extra in fields)
    progress = (by_label.get("Progress") or "").strip()
    if not progress:
        return None
    decision = (by_label.get("Decision needed") or "").strip()
    waiting = "no decision waiting" if (not decision or decision == "none") \
        else "decision waiting"
    return "%s, %s" % (progress, waiting)


def _status_line(payload):
    """The line to print for one stdin payload, or None to print nothing.
    Every store, consent and project ambiguity failure returns None here;
    genuinely unexpected shapes (a payload that never made it to a dict)
    are refused by the caller before this is reached.

    Opens a bs.ReadOnlyStore and nothing else: this function, and every
    function it calls, never constructs a writable Store and never calls
    a Store method that records anything, so there is no path from here
    to a write."""
    if not isinstance(payload, dict):
        return None
    if not _consented():
        return None
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        cwd = os.getcwd()

    bl = _load("bm_lead")
    bs = bl.bs
    root, _source = bs.resolve_root(cwd)
    if not root:
        return None
    store = bs.ReadOnlyStore(root)
    try:
        project_id = _project_id(store)
        if project_id is None:
            return None
        view = bl.collect_status(store, project_id)
        return one_line(view["fields"])
    finally:
        try:
            store.close()
        except Exception:  # sbe: allow-silent best-effort close of a read-only diagnostic handle
            pass


def main():
    """Console script entry point (pyproject.toml: bm-statusline). Reads
    stdin, prints at most one line, and always exits 0. The single broad
    guard below is deliberate and total: this is the one and only place
    in the file allowed to swallow an exception, and it swallows every
    one, the same shape tools/bm_view.py's cmd_alert already uses for its
    own hook entry point."""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw and raw.strip() else None
        line = _status_line(payload)
    except Exception:
        return 0
    if line:
        try:
            sys.stdout.write(line + "\n")
        except Exception:  # sbe: allow-silent per docstring: this hook entry point always exits 0, never crashes the caller
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
