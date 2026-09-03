#!/usr/bin/env python3
"""setup.py: the one place BrotherMode's consent config is read and written.

WHY THIS EXISTS (Loop 3 design D-1/D-2, docs/superpowers/specs/
2026-08-01-loop3-consent-install-design.md)
  Before this file existed, tools/bm_sessionstart.py wrote and read on every
  single session start with no consent gate at all: the external review's
  go/no-go row "No content write before setup consent" was the live NO-GO.
  This file is both halves of the fix. It is the SETUP FLOW (interactive and
  flag-driven) that creates ~/.brotherme/config.json only after a founder has
  actually said yes, and it is the SHARED READER every write-capable entry
  point consults first: tools/bm_sessionstart.py calls this file's
  --consent-state probe before it writes anything, and tools/bm_telemetry.py
  imports read_config/is_consented from this file by path (the same
  load-by-path technique it already uses for tools/bm_learning.py), so the
  two python entry points can never drift on what "consented" means. There is
  deliberately no second copy of this schema anywhere in the tree.

THE CONFIG FILE
  ~/.brotherme/config.json (override the path with BROTHERME_CONFIG, used by
  the test suite to point at a throwaway HOME). Exactly five keys:
    setup_complete           bool
    vault_path                string, where private memory (the vault) lives
    privacy_notice_version    string, the notice text version shown at write
    installation_mode         "plugin" or "clone" or "cursor"
    security_mode             "standard" (the only mode this release supports)
  The directory is created mode 0700, the file mode 0600, because this is the
  one file on disk that names where a founder's private notes live.

CONSENT ORDER (design D-1, review 13.3), enforced HERE and by every caller
  1. detect setup is incomplete (absent config, or setup_complete is not
     literally True);
  2. perform no content write;
  3. show one plain notice, in the words a non-engineer reads, naming the
     three things this project writes to disk and where;
  4. ask where private memory should live, defaulting to ~/BrotherModeVault;
  5. create the config file ONLY after an explicit yes;
  6. run doctor and print what it found;
  7. name the one next action.
  A caller (bm_sessionstart.py, bm_telemetry.py) that finds setup incomplete
  prints one sentence naming this file and writes nothing else, ever: this
  file is the ONLY code path in the project allowed to create the config.

WHAT THIS FILE NEVER DOES
  It never creates, moves, or deletes the vault directory itself; it only
  records the path the founder chose. It never overwrites an existing
  CONSENTED config unless --reconfigure is passed explicitly. It never blocks
  on stdin outside a real TTY unless the caller also supplied the full
  flag-mode trio (--vault, --mode, --accept-notice), so a script or a test
  harness that runs this with no arguments gets a usage refusal, not a hang.

Python 3.9, standard library only. Runs a subprocess only to invoke
scripts/doctor.py once, after consent, against this same interpreter; that
matches scripts/install.py's own documented use of subprocess for its smoke
test, and this file lives beside it in scripts/, not under tools/, where the
project's no-subprocess audit does not reach (tools/test_bm.py checks
tools/*.py only, because scripts/ has always shelled out for its smoke tests
and doctor runs).

No em or en dashes anywhere in this file, its comments, or its output.
"""

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_REFUSED = 4

PRIVACY_NOTICE_VERSION = "2026-08-01"

DEFAULT_VAULT = os.path.join(os.path.expanduser("~"), "BrotherModeVault")

NOTICE_LINES = (
    "BrotherMode setup: what gets written, in plain words, before anything is written.",
    "",
    "1. Your private memory (project notes, decisions, session history) is",
    "   written to one folder you choose below, called the vault (your",
    "   project memory). Nothing in it leaves your machine.",
    "2. Inside each project you use BrotherMode in, a small local folder named",
    "   .brothermode holds your project's records: which files are being",
    "   worked on right now, so two sessions do not overwrite each other.",
    "3. Session records (written by the machine, not the model) inside your",
    "   vault track basic usage numbers (tokens used, session length, how",
    "   many tool calls) so you can see effort over time. They never record",
    "   your file contents or your code.",
    "",
    "Nothing above is written until you say yes below.",
)


def _out(text):
    sys.stdout.write(text + "\n")


def _err(text):
    sys.stderr.write(text + "\n")


# ---------------------------------------------------------------------------
# The shared reader. tools/bm_telemetry.py loads this whole module by path
# and calls read_config/is_consented directly; keep both pure (no printing,
# no side effects) so that import is safe from any calling context.
# ---------------------------------------------------------------------------

def config_path():
    override = os.environ.get("BROTHERME_CONFIG")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".brotherme", "config.json")


def read_config(path=None):
    """Return (dict, error_or_None). A missing file is NOT an error: it means
    setup has never run, and returns ({}, None). A present-but-broken file
    returns ({}, "reason"), so a caller can tell "never set up" apart from
    "set up and then corrupted", which callers like --show report honestly
    instead of collapsing both into the same silence. Never raises."""
    p = path or config_path()
    try:
        with io.open(p, encoding="utf-8") as fh:
            raw = fh.read()
    except (IOError, OSError):
        return {}, None
    if raw.strip() == "":
        return {}, None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return {}, "not valid JSON (%s)" % exc
    if not isinstance(data, dict):
        return {}, "JSON but not an object (found %s)" % type(data).__name__
    return data, None


def is_consented(cfg):
    """True only when setup_complete is literally True. Fails CLOSED on
    anything else (absent key, a string "true", a corrupt config read as
    {}): this is the one function every write-gate in the project calls, and
    an accidental truthy string here would be a silent consent bypass."""
    return isinstance(cfg, dict) and cfg.get("setup_complete") is True


# ---------------------------------------------------------------------------
# Writing the config. Only reachable after the explicit yes.
# ---------------------------------------------------------------------------

def _ensure_dir_0700(path):
    if not os.path.isdir(path):
        os.makedirs(path)
    try:
        os.chmod(path, 0o700)
    except OSError as exc:
        _err("setup.py: warning: could not set %s to 0700 (%s)" % (path, exc))


def _atomic_write_text(path, text):
    parent = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".bm-setup-")
    try:
        with io.open(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:  # sbe: allow-silent best-effort tmp cleanup on the failure path, original exception is re-raised regardless
            pass
        raise


def write_config(vault_path, mode, path=None):
    """Write the consented config. The only function in this file that
    creates ~/.brotherme or the config file; every caller reaches it only
    after the explicit-yes gate (interactive) or the full flag-mode trio."""
    p = path or config_path()
    _ensure_dir_0700(os.path.dirname(os.path.abspath(p)))
    cfg = {
        "setup_complete": True,
        "vault_path": vault_path,
        "privacy_notice_version": PRIVACY_NOTICE_VERSION,
        "installation_mode": mode,
        "security_mode": "standard",
    }
    _atomic_write_text(p, json.dumps(cfg, indent=2, sort_keys=True) + "\n")
    try:
        os.chmod(p, 0o600)
    except OSError as exc:
        _err("setup.py: warning: could not set %s to 0600 (%s)" % (p, exc))
    return cfg


def detect_mode():
    """plugin when this checkout lives under a Claude Code skills directory
    (scripts/install.py's own default target), clone otherwise. A heuristic,
    not a certainty, which is why --mode overrides it in flag mode and the
    interactive prompt states which one it picked."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    norm = here.replace(os.sep, "/")
    if "/.claude/skills/" in norm or "/.claude/plugins/" in norm:
        return "plugin"
    return "clone"


# ---------------------------------------------------------------------------
# doctor, run once, after consent.
# ---------------------------------------------------------------------------

def _run_doctor():
    here = os.path.dirname(os.path.abspath(__file__))
    doctor = os.path.join(here, "doctor.py")
    if not os.path.isfile(doctor):
        _out("doctor: not found at %s, skipped" % doctor)
        return
    try:
        r = subprocess.run([sys.executable, doctor], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True,
                           timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        _out("doctor: could not run (%s)" % exc)
        return
    _out("")
    _out((r.stdout or "").strip())
    if r.returncode != 0:
        _out("doctor found problems (see above). Setup itself is done; this "
             "is a separate, ongoing check you can re-run any time with "
             "python3 scripts/doctor.py.")


def _next_action():
    _out("")
    _out("Next: run /brotherme-start (inside Claude Code), or "
         "python3 tools/bm_project.py start, to begin a project.")


# ---------------------------------------------------------------------------
# --show
# ---------------------------------------------------------------------------

def cmd_show():
    path = config_path()
    exists = os.path.isfile(path)
    cfg, err = read_config(path)
    if not exists:
        _out("consent: NOT SET UP (no config at %s)" % path)
        _out("run: python3 scripts/setup.py")
        return EXIT_OK
    if err:
        _out("consent: INVALID config at %s (%s)" % (path, err))
        _out("run: python3 scripts/setup.py --reconfigure")
        return EXIT_OK
    _out("consent: %s" % ("SETUP COMPLETE" if is_consented(cfg) else "INCOMPLETE"))
    _out("  config file: %s" % path)
    _out("  project memory (vault) location: %s" % cfg.get("vault_path", "(not set)"))
    _out("  install type: %s" % cfg.get("installation_mode", "(not set)"))
    _out("  privacy notice version: %s" % cfg.get("privacy_notice_version", "(not set)"))
    _out("  security mode: %s" % cfg.get("security_mode", "(not set)"))
    return EXIT_OK


# ---------------------------------------------------------------------------
# Refusal shared by both modes: never overwrite a consented config silently.
# ---------------------------------------------------------------------------

def _refuse_existing_consent_unless_reconfigure(reconfigure):
    path = config_path()
    cfg, err = read_config(path)
    if err or not is_consented(cfg):
        return None
    if reconfigure:
        return None
    _err("setup.py: refusing to overwrite an existing, already-consented "
         "config at %s." % path)
    _err("  vault_path: %s" % cfg.get("vault_path", "(not set)"))
    _err("  installation_mode: %s" % cfg.get("installation_mode", "(not set)"))
    _err("Nothing has been changed. Re-run with --reconfigure if that is "
         "what you want.")
    return EXIT_REFUSED


# ---------------------------------------------------------------------------
# Flag mode: --vault PATH --mode plugin|clone|cursor --accept-notice
# ---------------------------------------------------------------------------

def run_flag_mode(args):
    missing = [name for name, val in (
        ("--vault", args.vault), ("--mode", args.mode),
        ("--accept-notice", args.accept_notice)) if not val]
    if missing:
        _err("setup.py: flag mode needs all three of --vault PATH --mode "
             "plugin|clone --accept-notice together; missing %s."
             % ", ".join(missing))
        return EXIT_USAGE
    refusal = _refuse_existing_consent_unless_reconfigure(args.reconfigure)
    if refusal is not None:
        return refusal
    vault = os.path.expanduser(args.vault)
    cfg = write_config(vault, args.mode)
    _out("setup: config written to %s" % config_path())
    _out("  vault_path: %s" % cfg["vault_path"])
    _out("  installation_mode: %s" % cfg["installation_mode"])
    _run_doctor()
    _next_action()
    return EXIT_OK


# ---------------------------------------------------------------------------
# Interactive TTY mode.
# ---------------------------------------------------------------------------

def run_interactive_mode(args):
    if not sys.stdin.isatty():
        _err("setup.py: no terminal to ask questions on, and no flag-mode "
             "arguments were given. For a scripted or automated run, use: "
             "python3 scripts/setup.py --vault PATH --mode plugin|clone "
             "--accept-notice")
        return EXIT_USAGE
    refusal = _refuse_existing_consent_unless_reconfigure(args.reconfigure)
    if refusal is not None:
        return refusal

    for line in NOTICE_LINES:
        _out(line)
    _out("")

    mode = detect_mode()
    mode_gloss = " (a copied folder install)" if mode == "clone" else ""
    _out("Detected installation mode: %s%s" % (mode, mode_gloss))

    try:
        vault_in = input("Where should your private memory live? [%s]: "
                         % DEFAULT_VAULT)
    except EOFError:
        vault_in = ""
    vault = os.path.expanduser(vault_in.strip()) if vault_in.strip() else DEFAULT_VAULT

    try:
        answer = input("Type yes to continue: ")
    except EOFError:
        answer = ""
    if answer.strip().lower() != "yes":
        _out("Setup was not completed; nothing was written. Run this again "
             "any time: python3 scripts/setup.py")
        return EXIT_OK

    cfg = write_config(vault, mode)
    _out("")
    _out("setup: config written to %s" % config_path())
    _out("  vault_path: %s" % cfg["vault_path"])
    _out("  installation_mode: %s" % cfg["installation_mode"])
    _run_doctor()
    _next_action()
    return EXIT_OK


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="setup.py",
        description="BrotherMode first-run consent and setup.")
    p.add_argument("--vault", default=None,
                   help="where private memory lives (default %s)" % DEFAULT_VAULT)
    p.add_argument("--mode", choices=("plugin", "clone", "cursor"), default=None,
                   help="installation_mode to record")
    p.add_argument("--accept-notice", action="store_true",
                   help="accept the privacy notice non-interactively "
                        "(required, with --vault and --mode, for a scripted run)")
    p.add_argument("--reconfigure", action="store_true",
                   help="allow overwriting an existing, already-consented config")
    p.add_argument("--show", action="store_true",
                   help="print current consent state and exit; writes nothing")
    p.add_argument("--consent-state", action="store_true",
                   help="exit 0 if setup is complete, 1 otherwise; prints "
                        "nothing (the cheap probe hook entry points call)")
    return p


def main(argv):
    args = build_parser().parse_args(argv)

    if args.consent_state:
        cfg, _err_msg = read_config()
        return EXIT_OK if is_consented(cfg) else EXIT_FAILED

    if args.show:
        return cmd_show()

    if args.vault or args.mode or args.accept_notice:
        return run_flag_mode(args)

    return run_interactive_mode(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
