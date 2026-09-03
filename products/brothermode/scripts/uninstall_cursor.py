#!/usr/bin/env python3
"""uninstall_cursor.py: remove BrotherMode's Cursor wiring, and nothing else.

Removes only hook entries whose command names this installation's
bm_cursor_hook.py, plus the Cursor install record. Never touches the vault,
never touches Claude Code settings, never deletes project .brothermode/
stores. --remove-files deletes the ~/.cursor/brothermode checkout when it
looks like a BrotherMode tree.

Python 3.9, standard library only. No network.
No em or en dashes anywhere in this file, its comments, or its output.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))

import install as _install  # noqa: E402
import bm_cursor as _bmc  # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 4

RECORD_NAME = "brothermode-install.json"


def _out(text):
    sys.stdout.write(text + "\n")


def _err(text):
    sys.stderr.write(text + "\n")


def strip_our_hooks(doc, target):
    hooks = doc.get("hooks")
    if not isinstance(hooks, dict):
        return doc, 0
    removed = 0
    new_hooks = {}
    target_abs = os.path.abspath(target)
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            new_hooks[event] = entries
            continue
        kept = []
        for entry in entries:
            if not isinstance(entry, dict):
                kept.append(entry)
                continue
            cmd = entry.get("command")
            if (isinstance(cmd, str)
                    and "bm_cursor_hook.py" in cmd
                    and (target_abs in cmd or target in cmd
                         or "brothermode" in cmd)):
                # Narrow: must name bm_cursor_hook AND this target (or the
                # conventional brothermode install path segment when the
                # record is missing). Prefer target match.
                if target_abs in cmd or target in cmd:
                    removed += 1
                    continue
            kept.append(entry)
        if kept:
            new_hooks[event] = kept
    out = dict(doc)
    out["hooks"] = new_hooks
    out["version"] = doc.get("version", 1)
    return out, removed


def build_parser():
    p = argparse.ArgumentParser(prog="uninstall_cursor.py")
    p.add_argument("--target", default=None)
    p.add_argument("--hooks", default=None)
    p.add_argument("--remove-files", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--project", default=None,
                   help="also strip project .cursor/hooks.json entries")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    dry = args.dry_run
    prefix = "[dry-run] " if dry else ""

    record_path = os.path.join(
        os.path.dirname(os.path.abspath(args.hooks or _bmc.default_hooks_path())),
        RECORD_NAME)
    target = args.target
    if target is None and os.path.isfile(record_path):
        try:
            target = json.loads(
                io.open(record_path, encoding="utf-8").read()).get("target")
        except (IOError, OSError, ValueError):
            target = None
    target = os.path.abspath(target or _bmc.default_install_target())
    hooks_path = os.path.abspath(args.hooks or _bmc.default_hooks_path())

    _out("%sBrotherMode Cursor uninstall" % prefix)
    _out("%s  target: %s" % (prefix, target))
    _out("%s  hooks:  %s" % (prefix, hooks_path))

    removed_total = 0
    if os.path.isfile(hooks_path):
        try:
            doc = json.loads(io.open(hooks_path, encoding="utf-8").read())
        except ValueError as exc:
            _err("uninstall_cursor.py: hooks.json is not valid JSON: %s" % exc)
            return EXIT_REFUSED
        new_doc, removed = strip_our_hooks(doc, target)
        removed_total += removed
        if dry:
            _out("%s  would remove %d hook entr(y/ies) from user hooks"
                 % (prefix, removed))
        else:
            if removed:
                tmp = hooks_path + ".tmp"
                with io.open(tmp, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps(new_doc, indent=2, sort_keys=True)
                             + "\n")
                os.replace(tmp, hooks_path)
            _out("  removed %d hook entr(y/ies) from user hooks" % removed)
    else:
        _out("%s  no user hooks file" % prefix)

    if args.project:
        project_hooks = os.path.join(os.path.abspath(args.project),
                                     ".cursor", "hooks.json")
        if os.path.isfile(project_hooks):
            doc = json.loads(io.open(project_hooks, encoding="utf-8").read())
            new_doc, removed = strip_our_hooks(doc, target)
            removed_total += removed
            if dry:
                _out("%s  would remove %d project hook entr(y/ies)"
                     % (prefix, removed))
            else:
                if removed:
                    with io.open(project_hooks, "w", encoding="utf-8") as fh:
                        fh.write(json.dumps(new_doc, indent=2, sort_keys=True)
                                 + "\n")
                _out("  removed %d project hook entr(y/ies)" % removed)

    if os.path.isfile(record_path):
        if dry:
            _out("%s  would remove record %s" % (prefix, record_path))
        else:
            os.unlink(record_path)
            _out("  removed record %s" % record_path)

    if args.remove_files:
        if not _install.looks_like_brothermode(target):
            _err("uninstall_cursor.py: %s does not look like a BrotherMode "
                 "checkout; refusing --remove-files" % target)
            return EXIT_REFUSED
        if dry:
            _out("%s  would delete %s" % (prefix, target))
        else:
            shutil.rmtree(target)
            _out("  deleted %s" % target)

    _out("")
    _out("%sCursor uninstall done (%d hook entries removed)."
         % (prefix, removed_total))
    _out("%sLeft untouched: vault, Claude Code settings, per-project "
         ".brothermode/ stores, mailbox packets." % prefix)
    _out("%sTo remove project rules by hand: "
         "rm .cursor/rules/brothermode.mdc" % prefix)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
