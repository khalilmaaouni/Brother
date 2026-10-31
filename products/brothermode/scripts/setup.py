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


def write_config(vault_path, mode, path=None, fence_mode=None, lessons=None):
    """Write the consented config. The only function in this file that
    creates ~/.brotherme or the config file; every caller reaches it only
    after the explicit-yes gate (interactive) or the full flag-mode trio.

    FIRST1.c: fence_mode and lessons are the CONSENT RECORD, written only by
    answer_consent below and omitted entirely when they are None, so the five
    key config every existing caller and fixture knows is byte for byte what
    it was. They are separate keys rather than a new value for security_mode
    because security_mode is read for display in nineteen places and means
    something else; see D11 in docs/handover/2026-10-28/05-DECISIONS-TAKEN.md."""
    p = path or config_path()
    _ensure_dir_0700(os.path.dirname(os.path.abspath(p)))
    cfg = {
        "setup_complete": True,
        "vault_path": vault_path,
        "privacy_notice_version": PRIVACY_NOTICE_VERSION,
        "installation_mode": mode,
        "security_mode": "standard",
    }
    if fence_mode is not None:
        cfg["fence_mode"] = fence_mode
    if lessons is not None:
        cfg["lessons_root"] = lessons
    _atomic_write_text(p, json.dumps(cfg, indent=2, sort_keys=True) + "\n")
    try:
        os.chmod(p, 0o600)
    except OSError as exc:
        _err("setup.py: warning: could not set %s to 0600 (%s)" % (p, exc))
    return cfg


# ---------------------------------------------------------------------------
# FIRST1.c: the one consent question (D-2: at most 600 bytes, at most 20 s).
#
# THE ORDER THIS KEEPS, which is the whole point of the unit: nothing is armed
# before the yes. answer_consent is the ONLY function that writes the arming
# record, a no writes nothing at all, and the answer itself is accepted only
# from outside the session (an interactive terminal, or the answer file the
# write guard denies to the session), so an agent cannot consent on the
# person's behalf. bm_project.py's `consent` route is the surface; the rule
# lives here, with write_config, because this file is the one code path in the
# project allowed to create the config.
# ---------------------------------------------------------------------------

#: The fence mode a yes arms. The fence hook's own word for fail closed.
FENCE_ENFORCED = "enforced"

#: The bytes D-2 allows the question. Counted as UTF-8, not characters: a
#: translated question pays for its own encoding rather than overrunning it.
CONSENT_QUESTION_MAX_BYTES = 600


def lessons_root():
    """Where a yes binds lessons. A function, not a module constant, for the
    same reason config_path() is one: a test pointing HOME at a throwaway
    directory must not read a path frozen at import time."""
    return os.path.join(os.path.expanduser("~"), ".brother", "lessons")


def consent_answer_path():
    """The file a person answers in when there is no terminal to answer from.

    THE DENIAL IS NOT IN PLACE YET, and this docstring said otherwise until a
    security review caught it on 2026-10-29. The design is that HD1.d adds this
    folder to the set sbe_bash_write_guard and the Write hook deny to the
    session, which is what would make an answer here evidence of a PERSON: the
    session that benefits from a yes could not write one. HD1 is OPEN.
    sbe_bash_write_guard protects families under the repository root and carries
    its own off switch, so it does not reach a path under the home directory at
    all today. Until HD1.d lands, an answer in this file is evidence that
    SOMETHING wrote it, and the arming path says so out loud rather than
    implying a guarantee it does not have."""
    return os.path.join(os.path.expanduser("~"), ".brother", "consent", "answer")


def consent_question():
    """REQ-FIRST1C-01: the ONE question, at most 600 bytes of UTF-8.

    It names the three things that get written, where each one lands, and the
    off switch, because a consent question that does not say what it is
    consenting to is a formality rather than a question."""
    return (
        "Before anything is written, one question.\n"
        "\n"
        "Brother writes three things on this machine and nothing else:\n"
        "  1. a config at ~/.brotherme/config.json, naming where your notes live\n"
        "  2. lessons at ~/.brother/lessons, what it learned about how you work\n"
        "  3. receipts beside the work, the commands it ran and what they returned\n"
        "\n"
        "Yes arms the write gate: a tool that cannot name its own check refuses\n"
        "instead of guessing. No writes nothing at all.\n"
        "\n"
        "Off later: setup.py --reconfigure, or BM_FENCE_MODE=advisory for one run."
    )


def answer_consent(yes, path=None):
    """REQ-FIRST1C-03. A yes writes the config through write_config with the
    fence mode enforced and the lessons root bound, and returns the written
    dict. A no writes NOTHING and returns {}.

    `yes is not True` rather than `not yes`, deliberately, and for the same
    reason is_consented above reads `is True`: this is an arming gate, and a
    truthy string or a stray 1 reaching it must fail CLOSED rather than arm
    the fence on a value nobody meant as consent.

    An existing vault path and installation mode are carried forward so that
    answering the question does not silently relocate a vault the person
    already chose; absent either, the same defaults setup itself uses."""
    if yes is not True:
        return {}
    p = path or config_path()
    cfg, _err = read_config(p)
    vault = cfg.get("vault_path")
    mode = cfg.get("installation_mode")
    return write_config(vault if isinstance(vault, str) and vault else DEFAULT_VAULT,
                        mode if isinstance(mode, str) and mode else detect_mode(),
                        path=p, fence_mode=FENCE_ENFORCED, lessons=lessons_root())


def consented_fence_mode(path=None):
    """The armed fence mode on disk, or None when nothing is armed.

    Fails CLOSED on a corrupt config (read_config's error arm), so an
    unreadable file reads as not armed rather than as whatever was there."""
    cfg, err = read_config(path)
    if err:
        return None
    mode = cfg.get("fence_mode")
    return mode if isinstance(mode, str) and mode else None


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
    _out("Next: run /brother start <what you want> (inside Claude Code), or "
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
