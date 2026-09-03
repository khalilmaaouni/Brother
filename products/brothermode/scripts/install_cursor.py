#!/usr/bin/env python3
"""install_cursor.py: install BrotherMode for Cursor, independently of Claude Code.

WHAT IT DOES
  1. Copies this checkout into ~/.cursor/brothermode (or --target).
  2. Writes ~/.cursor/hooks.json (or --hooks) in Cursor's native version-1
     shape, pointing at tools/bm_cursor_hook.py with absolute paths.
  3. Optionally writes project rules (.cursor/rules/brothermode.mdc) and
     project hooks when --project is given.
  4. Writes ~/.cursor/brothermode-install.json.
  5. Smokes the Cursor adapter before claiming success.

WHAT IT GUARANTEES
  Same ownership discipline as scripts/install.py: never deletes a hook
  entry it did not write; refuses rather than overwrites without
  --upgrade; backs up hooks.json before every write; re-reads what it
  wrote.

WHAT IT DOES NOT CLAIM
  A live Cursor Agent canary proving the fence fires has not been
  measured in this tree. Install is real; enforcement is ADVISORY until
  that canary lands (docs/CURSOR-COMPAT.md).

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
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))

import install as _install  # noqa: E402
import bm_cursor as _bmc  # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_UNSUPPORTED = 3
EXIT_REFUSED = 4

RECORD_NAME = "brothermode-install.json"


def _out(text):
    sys.stdout.write(text + "\n")


def _err(text):
    sys.stderr.write(text + "\n")


def backup_file(path, dry):
    if not os.path.isfile(path):
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path + ".brothermode-backup-" + stamp
    if dry:
        _out("[dry-run] would backup %s -> %s" % (path, backup))
        return backup
    shutil.copy2(path, backup)
    return backup


def merge_hooks(existing, ours, checkout):
    """Replace only BrotherMode-owned entries; keep foreign ones."""
    if not isinstance(existing, dict):
        existing = {}
    hooks = existing.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    new_hooks = dict(hooks)
    our_doc = _bmc.build_hooks_json(checkout)
    for event, entries in our_doc["hooks"].items():
        kept = []
        prior = hooks.get(event) or []
        if isinstance(prior, list):
            for entry in prior:
                if not isinstance(entry, dict):
                    kept.append(entry)
                    continue
                cmd = entry.get("command")
                if isinstance(cmd, str) and "bm_cursor_hook.py" in cmd and (
                        checkout in cmd or os.path.abspath(checkout) in cmd):
                    continue  # drop our old entry; replace below
                kept.append(entry)
        kept.extend(entries)
        new_hooks[event] = kept
    return {"version": 1, "hooks": new_hooks}


def write_hooks(path, doc, dry):
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if dry:
        _out("[dry-run] would write %s (%d bytes)" % (path, len(text)))
        return
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    verify = io.open(path, encoding="utf-8").read()
    json.loads(verify)


def smoke(target, hooks_path):
    adapter = os.path.join(target, "tools", "bm_cursor_hook.py")
    if not os.path.isfile(adapter):
        return ["missing adapter: %s" % adapter]
    old = os.environ.get("BROTHERMODE_ROOT")
    os.environ["BROTHERMODE_ROOT"] = target
    try:
        code = _bmc.cmd_doctor(["--checkout", target, "--hooks", hooks_path])
    finally:
        if old is None:
            os.environ.pop("BROTHERMODE_ROOT", None)
        else:
            os.environ["BROTHERMODE_ROOT"] = old
    if code != 0:
        return ["bm-cursor doctor failed (exit %d)" % code]
    return []


def build_parser():
    p = argparse.ArgumentParser(
        prog="install_cursor.py",
        description="Install BrotherMode for Cursor.")
    p.add_argument("--target", default=None,
                   help="install root (default ~/.cursor/brothermode)")
    p.add_argument("--hooks", default=None,
                   help="Cursor hooks.json (default ~/.cursor/hooks.json)")
    p.add_argument("--project", default=None,
                   help="also write project rules and project hooks here")
    p.add_argument("--upgrade", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-hooks", action="store_true",
                   help="copy files only; leave hooks.json untouched")
    p.add_argument("--user-hooks", action="store_true", default=True,
                   help=argparse.SUPPRESS)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    bad = _install.check_platform("install_cursor.py")
    if bad is not None:
        return bad

    dry = args.dry_run
    prefix = "[dry-run] " if dry else ""
    source = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not _install.looks_like_brothermode(source):
        _err("install_cursor.py: %s does not look like a BrotherMode checkout"
             % source)
        return EXIT_FAILED

    target = os.path.abspath(args.target or _bmc.default_install_target())
    hooks_path = os.path.abspath(args.hooks or _bmc.default_hooks_path())
    record_path = os.path.join(os.path.dirname(hooks_path), RECORD_NAME)
    version = _install.read_version(source)
    in_place = (os.path.exists(target)
                and os.path.realpath(source) == os.path.realpath(target))

    prior = os.path.isfile(record_path) or (
        os.path.isdir(target) and _install.looks_like_brothermode(target)
        and not in_place)
    if prior and not args.upgrade and not in_place:
        _err("install_cursor.py: refusing to overwrite an existing install "
             "at %s. Re-run with --upgrade." % target)
        return EXIT_REFUSED

    _out("%sBrotherMode %s for Cursor" % (prefix, version))
    _out("%s  source:  %s" % (prefix, source))
    _out("%s  target:  %s%s" % (prefix, target,
                                " (in place)" if in_place else ""))
    _out("%s  hooks:   %s" % (prefix, hooks_path))

    if not in_place:
        if os.path.isdir(target) and args.upgrade and not dry:
            # Overlay copy; same prune limit as install.py.
            pass
        copied = _install.copy_tree(source, target, dry)
        _out("%s  copied:  %d files" % (prefix, copied))
    else:
        _out("%s  copied:  0 (in-place checkout)" % prefix)

    if not args.no_hooks:
        existing = {}
        if os.path.isfile(hooks_path):
            try:
                existing = json.loads(
                    io.open(hooks_path, encoding="utf-8").read())
            except ValueError as exc:
                _err("install_cursor.py: %s is not valid JSON: %s"
                     % (hooks_path, exc))
                return EXIT_REFUSED
            if (not args.upgrade and _bmc._hooks_are_ours(existing, target)
                    and not in_place):
                _err("install_cursor.py: hooks.json already has BrotherMode "
                     "entries. Re-run with --upgrade.")
                return EXIT_REFUSED
        backup_file(hooks_path, dry)
        merged = merge_hooks(existing, None, target if not dry or in_place
                             else source)
        # When dry-run and not in-place, emit hooks pointing at the future
        # target path so the preview matches the post-install world.
        if dry and not in_place:
            merged = merge_hooks(existing, None, target)
        write_hooks(hooks_path, merged, dry)
        _out("%s  wired:   Cursor hooks at %s" % (prefix, hooks_path))

    if args.project:
        project = os.path.abspath(args.project)
        checkout_for_rules = target
        if dry and not in_place:
            checkout_for_rules = target
        rules_argv = ["--project", project, "--checkout", checkout_for_rules]
        if args.upgrade:
            rules_argv.append("--force")
        if dry:
            _out("%s  rules:   would write .cursor/rules/brothermode.mdc "
                 "under %s" % (prefix, project))
        else:
            code = _bmc.cmd_emit_rules(rules_argv)
            if code != 0:
                return code
        project_hooks = os.path.join(project, ".cursor", "hooks.json")
        if dry:
            _out("%s  project hooks: would write %s"
                 % (prefix, project_hooks))
        else:
            backup_file(project_hooks, dry=False)
            existing_p = {}
            if os.path.isfile(project_hooks):
                try:
                    existing_p = json.loads(
                        io.open(project_hooks, encoding="utf-8").read())
                except ValueError:
                    existing_p = {}
            write_hooks(project_hooks,
                        merge_hooks(existing_p, None, target), dry=False)
            _out("  project hooks: %s" % project_hooks)

    record = {
        "product": "BrotherMode",
        "runtime": "cursor",
        "version": version,
        "source": source,
        "target": target,
        "hooks": hooks_path,
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "installer": "scripts/install_cursor.py",
        "enforcement": "advisory-until-live-canary",
    }
    if dry:
        _out("%s  record:  would write %s" % (prefix, record_path))
    else:
        _bmc._write_json(record_path, record)
        _out("  record:  %s" % record_path)

    if not dry:
        problems = smoke(target, hooks_path)
        if problems:
            _err("install_cursor.py: smoke failed:")
            for line in problems:
                _err("  - %s" % line)
            return EXIT_FAILED

    _out("")
    _out("%sInstalled. Next steps:" % prefix)
    _out("%s  1. python3 %s/tools/bm_cursor.py status"
         % (prefix, target))
    _out("%s  2. python3 %s/tools/bm_cursor.py doctor"
         % (prefix, target))
    _out("%s  3. In a project: python3 %s/tools/bm_cursor.py emit-rules "
         "--force" % (prefix, target))
    _out("%s  4. Run scripts/setup.py once for consent before telemetry "
         "writes." % prefix)
    _out("%sFence under Cursor stays ADVISORY until a live canary is "
         "recorded." % prefix)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
