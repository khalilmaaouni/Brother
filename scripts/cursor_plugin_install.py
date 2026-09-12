#!/usr/bin/env python3
"""Install the Brother Cursor plugin to ~/.cursor/plugins/local/brother.

WHAT IT DOES
  Copies bundle/ (the umbrella Brother door) into Cursor's documented
  local-plugin path, then tells the person to reload the window.

WHAT IT DOES NOT CLAIM
  A live Cursor Agent canary that plugin hooks fire and honour
  permission: deny has not been measured. Install is real. Enforcement
  stays ADVISORY. See docs/how-to/install-cursor.md.

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

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BUNDLE = os.path.join(REPO, "bundle")

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_REFUSED = 4

PLUGIN_NAME = "brother"
EXCLUDE_DIR = {".git", "__pycache__", ".venv-embed", ".brothermode"}


def _out(text):
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def _err(text):
    sys.stderr.write(text if text.endswith("\n") else text + "\n")


def default_plugin_dir(env=None):
    mapping = os.environ if env is None else env
    home = mapping.get("HOME") or os.path.expanduser("~")
    return os.path.join(home, ".cursor", "plugins", "local", PLUGIN_NAME)


def copy_tree(source, dest, dry=False):
    copied = 0
    if not dry:
        os.makedirs(dest, exist_ok=True)
    for dirpath, dirnames, filenames in os.walk(source):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIR]
        rel = os.path.relpath(dirpath, source)
        out_dir = dest if rel == "." else os.path.join(dest, rel)
        if not dry:
            os.makedirs(out_dir, exist_ok=True)
        for fn in filenames:
            if fn.endswith(".pyc"):
                continue
            src = os.path.join(dirpath, fn)
            if os.path.islink(src):
                continue
            if not dry:
                shutil.copy2(src, os.path.join(out_dir, fn))
            copied += 1
    return copied


def validate_bundle(root):
    problems = []
    manifest = os.path.join(root, ".cursor-plugin", "plugin.json")
    if not os.path.isfile(manifest):
        problems.append("missing .cursor-plugin/plugin.json")
        return problems
    try:
        with io.open(manifest, encoding="utf-8") as fh:
            doc = json.loads(fh.read())
    except (IOError, OSError, ValueError) as exc:
        problems.append("plugin.json unreadable: %s" % exc)
        return problems
    if doc.get("name") != PLUGIN_NAME:
        problems.append("plugin.json name is %r, want %r" % (doc.get("name"),
                                                             PLUGIN_NAME))
    hooks = os.path.join(root, "cursor-hooks", "hooks.json")
    if not os.path.isfile(hooks):
        problems.append("missing cursor-hooks/hooks.json")
    door = os.path.join(root, "commands", "brother.md")
    if not os.path.isfile(door):
        problems.append("missing commands/brother.md")
    skill = os.path.join(root, "skills", "using-brother", "SKILL.md")
    if not os.path.isfile(skill):
        problems.append("missing skills/using-brother/SKILL.md")
    return problems


def install(dest, force=False, dry=False):
    dest = os.path.abspath(dest)
    problems = validate_bundle(BUNDLE)
    if problems:
        for line in problems:
            _err("cursor_plugin_install: %s" % line)
        return EXIT_FAILED, 0
    if os.path.exists(dest) and not force:
        _err("cursor_plugin_install: %s exists; pass --force to overwrite"
             % dest)
        return EXIT_REFUSED, 0
    if os.path.isdir(dest) and force and not dry:
        shutil.rmtree(dest)
    copied = copy_tree(BUNDLE, dest, dry=dry)
    record = {
        "product": "Brother",
        "runtime": "cursor-plugin",
        "name": PLUGIN_NAME,
        "source": BUNDLE,
        "target": dest,
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if not dry:
        parent = os.path.dirname(dest)
        os.makedirs(parent, exist_ok=True)
        rec = os.path.join(parent, "brother-plugin-install.json")
        tmp = rec + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, rec)
    _out("%sinstalled %s files to %s" % ("[dry-run] " if dry else "",
                                         copied, dest))
    _out("Reload Cursor (Developer: Reload Window) so it picks up "
         "~/.cursor/plugins/local/brother.")
    return EXIT_OK, copied


def uninstall(dest, dry=False):
    dest = os.path.abspath(dest)
    marker = os.path.join(dest, ".cursor-plugin", "plugin.json")
    if not os.path.isfile(marker):
        _err("cursor_plugin_install: %s is not a Brother Cursor plugin"
             % dest)
        return EXIT_REFUSED
    if dry:
        _out("[dry-run] would remove %s" % dest)
        return EXIT_OK
    shutil.rmtree(dest)
    sidecar = os.path.join(os.path.dirname(dest), "brother-plugin-install.json")
    if os.path.isfile(sidecar):
        os.unlink(sidecar)
    _out("removed %s" % dest)
    return EXIT_OK


def build_parser():
    p = argparse.ArgumentParser(prog="cursor_plugin_install.py")
    p.add_argument("action", nargs="?", default="install",
                   choices=("install", "uninstall", "validate"))
    p.add_argument("--target", default=None)
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    dest = os.path.abspath(args.target or default_plugin_dir())
    if args.action == "validate":
        problems = validate_bundle(BUNDLE)
        if problems:
            for line in problems:
                _out("FAIL: %s" % line)
            return EXIT_FAILED
        _out("PASS: Brother Cursor plugin package is complete")
        return EXIT_OK
    if args.action == "uninstall":
        return uninstall(dest, dry=args.dry_run)
    code, _copied = install(dest, force=args.force, dry=args.dry_run)
    return code


if __name__ == "__main__":
    sys.exit(main())
