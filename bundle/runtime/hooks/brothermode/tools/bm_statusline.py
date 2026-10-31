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
  One line: the newest finished run, read from the run index
  (~/.brother/runs-index.jsonl, scripts/proof_card.py's read_index), as
  "<date> <the engine's result line>", for example "2026-10-29 DONE |
  units=3 PASS=3 FAIL=0 NO-DATA=0 | release=NOT_REQUESTED | next=review-
  receipt". PC1H.b: this used to open the project's claim store on every
  refresh and build its line from the store's status fields; it now reads
  ONE small file, so a refresh costs a file read, not a store open. The
  index is per user, not per project, so the payload's folder no longer
  matters. No index yet prints "NO-DATA: no run recorded yet", never an
  error.

WHY THIS DOES NOT CALL `bm-view render`
  cmd_render's --json branch (tools/bm_view.py, _render_once) prints
  {path, rel_path, fingerprint, changed, artifact_url}, never a status
  payload, and on every call it WRITES PROJECT-VIEW.html to disk and
  records a view row in the store. A status line that ran that on every
  keystroke would break this file's own first law below on every single
  invocation.

FAIL SILENT IS LAW
  A status line runs on nearly every turn of a session (event driven,
  debounced at 300ms per the Claude Code contract). On ANY error at all:
  setup not consented, a corrupt config, a malformed stdin payload, a
  proof_card.py that cannot be loaded, an exception of any kind, this prints
  NOTHING, to stdout or stderr, and exits 0. A status line that prints a
  traceback into the founder's terminal bar is a worse failure than a
  status line that stays blank. The one line it prints on purpose when
  there is nothing to show is the NO-DATA line above. This file writes no
  file, ever, and opens no store.

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


def _load_proof_card():
    """scripts/proof_card.py, loaded by path from EITHER layout this tool
    ships in, exactly as vault_recall_hook._load_journal documents: the
    source checkout keeps it at <repo>/scripts/proof_card.py, an installed
    bundle at <root>/runtime/proof_card.py, both three directories above
    HERE. The first candidate that exists wins; none degrades to None (no
    line), never a crash. proof_card puts its own directory on sys.path to
    import safe_unwatched_time, which sits beside it in both layouts."""
    try:
        root = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
        for path in (os.path.join(root, "scripts", "proof_card.py"),
                     os.path.join(root, "proof_card.py")):
            if os.path.isfile(path):
                break
        else:
            return None
        spec = importlib.util.spec_from_file_location(
            "proof_card_for_statusline", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # sbe: allow-silent a status line without its index prints nothing, never a traceback
        return None


def _status_line(payload):
    """The line to print for one stdin payload, or None to print nothing.
    Reads the run index and nothing else: no store is opened, no folder is
    scanned, no file is written, so there is no path from here to a write.
    An absent index is the NO-DATA line; the payload's contents are not used
    beyond being a JSON object."""
    if not isinstance(payload, dict):
        return None
    if not _consented():
        return None
    pc = _load_proof_card()
    if pc is None:
        return None
    rows = pc.read_index()
    return pc.index_line(rows[0]) if rows else "NO-DATA: no run recorded yet"


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
