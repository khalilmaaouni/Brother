#!/usr/bin/env python3
"""uninstall.py: remove BrotherMode's hook wiring, and nothing else.

WHAT IT REMOVES
  Only hook entries every one of whose command paths names this installation's
  own tools/bm_* files (install.py decides that, and decides it by reading the
  command the way a shell does), plus the install record scripts/install.py
  wrote. Every other hook you have, and every other key in settings.json, is
  left exactly where it was, in order. If it finds nothing of its own but does
  find hooks pointing at BrotherMode tools somewhere else, it stops and prints
  them rather than reporting a clean uninstall over hooks that still run.

WHAT IT NEVER TOUCHES
  Your vault. Not by default, not with a flag, not with --purge. There is no
  code path in this file that deletes a vault, because a vault is months of
  your own notes and an uninstaller is the last place that decision should be
  made. It prints the vault path so you can delete it yourself if you want to.

  Plugin-managed hooks (Loop 3 design D-5, docs/superpowers/specs/2026-08-01-
  loop3-consent-install-design.md). This script's ownership rule is about
  scripts/install.py's own hook entries; a BrotherMode plugin enabled through
  Claude Code wires its hooks a different way entirely, and this file has no
  bookkeeping for that path at all. If BrotherMode was also installed as a
  plugin, run `/plugin uninstall <name>` for that half; this script only
  ever prints the honest caveat, it never guesses at removing what it cannot
  see.

WHAT IT OFFERS, ASKED, NEVER SILENT
  ~/.brotherme/config.json (or BROTHERME_CONFIG), the one consent file
  scripts/setup.py writes. After the hook removal above, if that file
  exists, this script asks whether to remove it too: interactively when
  there is a real terminal, or via the explicit --remove-consent flag in a
  scripted run. Neither given, the file is left in place and the exact
  command to remove it later is printed. It is never deleted without one of
  those two, and it is never silently left unmentioned either.

  It also does not touch what BrotherMode wrote inside your projects: the
  per-project .brothermode/ store, thread files, STATE.md and its backups, the
  local autosave git refs, or the lines in .git/info/exclude. Those are listed
  at the end with the commands to remove them, because they live in YOUR
  repositories and a tool that reaches into those uninvited is worse than one
  that leaves litter.

  Removing the installed files themselves needs --remove-files, and even then
  it refuses unless the directory actually looks like a BrotherMode checkout.

Python 3.9, standard library only, no network, no subprocess.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import argparse
import io
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import install as _install  # noqa: E402  (same directory, deliberate)
import setup as _bm_setup  # noqa: E402  (same directory, deliberate)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 4


def _out(text):
    sys.stdout.write(text + "\n")


def _err(text):
    sys.stderr.write(text + "\n")


def _plugin_name(target):
    """The name this installation's own plugin manifest claims, so the
    caveat sentence names the real plugin rather than a hardcoded guess.
    Falls back to the shipped name when the manifest cannot be read (a
    hand-wired or partial install may not have one)."""
    manifest_path = os.path.join(target, ".claude-plugin", "plugin.json")
    try:
        manifest = json.loads(io.open(manifest_path, encoding="utf-8").read())
    except (IOError, OSError, ValueError):
        return "brotherme"
    name = manifest.get("name") if isinstance(manifest, dict) else None
    return name if isinstance(name, str) and name else "brotherme"


def maybe_remove_consent_config(remove_consent_flag, dry):
    """D-5: offer to remove the consent config too, after the hook removal
    is settled. ASKED, never silent: interactive when there is a real
    terminal, the explicit --remove-consent flag otherwise. Neither one and
    the file survives, with the exact command to remove it later printed
    rather than the question being skipped without a word. Does nothing at
    all, prints nothing at all, when there is no config to begin with:
    honors BROTHERME_CONFIG and HOME the same way scripts/setup.py does,
    since this reads config_path() from that same module."""
    cfg_path = _bm_setup.config_path()
    if not os.path.isfile(cfg_path):
        return
    _out("")
    _out("consent: a setup config exists at %s." % cfg_path)
    if remove_consent_flag:
        do_remove = True
        _out("consent: --remove-consent was given; removing it.")
    elif sys.stdin.isatty():
        try:
            answer = input("Remove the saved consent settings too? You "
                           "would run setup again on the next install. "
                           "[y/N]: ")
        except EOFError:
            answer = ""
        do_remove = answer.strip().lower() in ("y", "yes")
        if not do_remove:
            _out("consent: keeping it. Remove it yourself later with: "
                 "rm %s" % cfg_path)
    else:
        _out("consent: no terminal to ask on, and --remove-consent was not "
             "given, so it is being left in place. Pass --remove-consent to "
             "delete it non-interactively, or remove it yourself: rm %s"
             % cfg_path)
        do_remove = False
    if not do_remove:
        return
    if dry:
        _out("[dry-run] consent: would remove %s" % cfg_path)
        return
    try:
        os.remove(cfg_path)
    except OSError as exc:
        _err("uninstall.py: could not remove %s: %s" % (cfg_path, exc))
        return
    _out("consent: removed %s" % cfg_path)


def data_locations(vault=None):
    """The one list of every place BrotherMode's data lives, as printed
    lines without their leading bullet.

    Extracted from main() for V3 Final B2 so that `doctor` can answer "where
    does my data live" from the SAME definition this uninstaller acts on. It
    was previously typed inline here and nowhere else, which meant any second
    page answering that question would be a transcription, correct on the day
    it was written and silently wrong the first time a location changed.

    `vault` is read from the environment by the caller, not here, so this
    function stays pure and testable.
    """
    return [
        "Your vault%s. Nothing in the uninstaller deletes a vault, with or "
        "without a flag. Delete it yourself if you want it gone."
        % (" (%s)" % vault if vault else
           " (BROTHERMODE_VAULT is not set in this shell, so its path is "
           "not known here)"),
        "Per-project state in every repository you used it in: "
        ".brothermode/ (the sqlite store), threads/, STATE.md and "
        "STATE.md.bak*, the local autosave git refs "
        "(git for-each-ref refs/brothermode), and the /.brothermode line in "
        ".git/info/exclude. See README.md's Uninstall section for the exact "
        "removal commands.",
        "Any BROTHERMODE_* export you added to your shell profile.",
    ]


def strip_hooks(settings, target):
    """Return (new_settings, removed_count). Pure."""
    new = json.loads(json.dumps(settings))
    hooks = new.get("hooks")
    if not isinstance(hooks, dict):
        return new, 0
    removed = 0
    for event in list(hooks.keys()):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept = [g for g in groups if not _install.group_is_ours(g, target)]
        removed += len(groups) - len(kept)
        if kept:
            hooks[event] = kept
        else:
            # Only ours were here. Remove the key rather than leaving an empty
            # list, which reads as a configured but broken hook.
            del hooks[event]
    if not hooks:
        del new["hooks"]
    return new, removed


def find_brothermode_looking_hooks(settings, target):
    """(event, index, command) for hooks naming BrotherMode tools elsewhere.

    Deliberately loose where ownership is strict. Ownership decides what may be
    DELETED, so it refuses anything it cannot prove. This decides what to WARN
    about, so it takes any command that names one of our tool basenames and
    does not belong to the target given. False positives here cost the user one
    printed line; a miss costs them hooks that keep running after they were
    told the uninstall was done."""
    out = []
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return out
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for i, group in enumerate(groups):
            if not isinstance(group, dict):
                continue
            if _install.group_is_ours(group, target):
                continue
            entries = group.get("hooks")
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                command = entry.get("command")
                if not isinstance(command, str):
                    continue
                if any(name in command for name in _install.OWNED_TOOLS):
                    out.append((event, i, command))
    return out


def build_parser():
    p = argparse.ArgumentParser(
        prog="uninstall.py",
        description="Remove BrotherMode hook entries. Never touches your vault.")
    p.add_argument("--target", default=None,
                   help="the install directory; read from the install record "
                        "when not given")
    p.add_argument("--settings", default=None,
                   help="Claude Code settings file (default ~/.claude/settings.json)")
    p.add_argument("--remove-files", action="store_true",
                   help="also delete the installed skill directory")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would be removed and change nothing")
    p.add_argument("--remove-consent", action="store_true",
                   help="also remove ~/.brotherme/config.json (the setup.py "
                        "consent file) non-interactively, if it exists")
    return p


def main(argv):
    args = build_parser().parse_args(argv)
    dry = args.dry_run
    prefix = "[dry-run] " if dry else ""

    settings_path = os.path.abspath(args.settings or _install.default_settings_path())
    record_path = os.path.join(os.path.dirname(settings_path), _install.RECORD_NAME)

    record = None
    if os.path.exists(record_path):
        try:
            record = json.loads(io.open(record_path, encoding="utf-8").read())
        except (ValueError, IOError, OSError) as exc:
            _err("uninstall.py: the install record %s is unreadable (%s). "
                 "Pass --target explicitly." % (record_path, exc))
            record = None

    target = args.target or (record or {}).get("target")
    if not target:
        _err("uninstall.py: cannot tell which installation to remove.\n"
             "There is no install record at %s and no --target was given. "
             "Refusing to guess: guessing here means deciding on your behalf "
             "which hook entries are mine, and a wrong guess deletes yours.\n"
             "Re-run as: python3 scripts/uninstall.py --target "
             "/path/to/your/brothermode" % record_path)
        return EXIT_REFUSED
    target = os.path.abspath(target)

    try:
        settings, raw_before = _install.read_settings(settings_path)
    except ValueError as exc:
        _err("uninstall.py: %s" % exc)
        return EXIT_REFUSED

    owned = _install.find_existing_hooks(settings, target)
    _out("%starget:   %s" % (prefix, target))
    _out("%ssettings: %s" % (prefix, settings_path))

    if owned:
        for event, i in owned:
            _out("%shooks: removing %s[%d]" % (prefix, event, i))
    else:
        _out("%shooks: none found that belong to %s. Nothing to unwire."
             % (prefix, target))
        strays = find_brothermode_looking_hooks(settings, target)
        if strays:
            # "Nothing to unwire" plus exit 0 plus deleting the install record
            # is the ambiguous success this file exists to avoid: the user is
            # told it is gone while hooks still run at every session, and the
            # record that named the right target has been thrown away. Stop
            # instead, and print the entries so the right --target is readable
            # off them.
            _err("uninstall.py: refusing to finish. Nothing here belongs to %s, "
                 "but %d hook entry(ies) in %s do name BrotherMode tools:"
                 % (target, len(strays), settings_path))
            for event, i, command in strays:
                _err("  - %s[%d]: %s" % (event, i, command))
            _err("So the --target above is not the installation those hooks "
                 "point at. Nothing has been changed and the install record "
                 "was kept. Re-run with --target set to the directory named in "
                 "the lines above.")
            return EXIT_REFUSED

    new_settings, removed = strip_hooks(settings, target)
    if removed and not dry:
        try:
            backup = _install.write_settings(
                settings_path, new_settings, raw_before, False)
        except (ValueError, IOError, OSError) as exc:
            _err("uninstall.py: writing %s failed: %s" % (settings_path, exc))
            return EXIT_FAILED
        real_settings = _install.resolve_settings_link(settings_path)
        if real_settings != settings_path:
            _out("hooks: %s is a symlink; the file it points at (%s) is what "
                 "was edited, and the link is left as it was."
                 % (settings_path, real_settings))
        if backup:
            _out("hooks: previous settings backed up to %s" % backup)
        _out("hooks: %d entry(ies) removed; every other hook left in place, in "
             "order." % removed)

    if os.path.exists(record_path):
        _out("%srecord: removing %s" % (prefix, record_path))
        if not dry:
            try:
                os.unlink(record_path)
            except OSError as exc:
                _err("uninstall.py: could not remove %s: %s" % (record_path, exc))
                return EXIT_FAILED

    if args.remove_files:
        if not os.path.isdir(target):
            _out("%sfiles: %s does not exist; nothing to remove." % (prefix, target))
        elif not _install.looks_like_brothermode(target):
            _err("uninstall.py: refusing to delete %s with --remove-files: it "
                 "does not look like a BrotherMode checkout (SKILL.md, VERSION "
                 "and tools/bm_store.py are not all there). Delete it yourself "
                 "if you are sure." % target)
            return EXIT_REFUSED
        else:
            _out("%sfiles: removing %s" % (prefix, target))
            if not dry:
                try:
                    shutil.rmtree(target)
                except OSError as exc:
                    _err("uninstall.py: could not remove %s: %s" % (target, exc))
                    return EXIT_FAILED
    else:
        _out("%sfiles: left in place at %s. Pass --remove-files to delete them."
             % (prefix, target))

    maybe_remove_consent_config(args.remove_consent, dry)

    vault = os.environ.get("BROTHERMODE_VAULT")
    _out("")
    _out("Untouched, deliberately:")
    for line in data_locations(vault):
        _out("  - %s" % line)
    _out("  - Plugin-managed hooks. If BrotherMode was also installed as a "
         "Claude Code plugin, that wiring is not this script's bookkeeping "
         "at all: run '/plugin uninstall %s' to remove it." % _plugin_name(target))
    if dry:
        _out("")
        _out("[dry-run] Nothing above was changed.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
