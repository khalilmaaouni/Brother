#!/usr/bin/env python3
"""Repair a STRANDED BrotherMode install (M1, docs/plan/QUEUE.json): a
skill-directory clone sitting at ~/.claude/skills/<plugin> with no fence
hook wired anywhere -- not in settings.json, and not in its own
hooks/hooks.json either -- which scripts/doctor.py's own install_shape
check now names by key. Measured on the founder's own machine
(docs/NORTH-STAR-CHAIN.md finding 1): the files were on disk and nothing
loaded them.

ONE command. By default it only DETECTS and PRINTS A PLAN: what it would
remove, what it would (re)install, and the settings lines
scripts/install.py itself would add (that preview comes straight from
running `install.py --dry-run`, never reimplemented here, so this plan
can never drift from what install.py actually does). It changes NOTHING
unless --apply is given, and --apply is refused, with the reason stated,
unless --i-understand-this-changes-every-session is ALSO given: the
settings.json wiring this touches is read at the start of every future
Claude Code session under that home, not just this one.

NEVER defaults to the real machine in a TEST: --home is the home this
script inspects (default: the operator's own real ~, for real, standalone
use), and every test in tools/test_bm.py's migrate_install class passes
it explicitly, pointed at a throwaway fixture directory that is deleted
when the test ends. No test in this project ever omits --home when
driving this script.

Subprocess calls (to scripts/install.py) always pass encoding="utf-8"
explicitly, never universal_newlines=True: that flag decodes with the
machine's own locale, which corrupts output silently on a machine whose
locale is not UTF-8 (see the checked-in lesson: subprocess text mode
decodes with the machine locale). Every subprocess.run call also pins an
explicit errors= policy rather than leaving Python's own default: strict
(errors="strict") everywhere a decode failure could otherwise be read as
a passing verdict or a false DONE, and replace (errors="replace", this
project's own lossy idiom, see scripts/bm_commit_msg_hook.py and
tools/bm_project.py) only on the one path that is pure human display and
decides nothing (the dry-run plan preview).

Removing the stranded skill directory is sequenced AFTER a preflight
reinstall dry run succeeds, never before: shutil.rmtree only runs once
scripts/install.py --dry-run has already proven, against the same
target and settings, that a real reinstall can actually run end to end
(decode included). A preflight failure, for any reason including a
decode error, refuses the whole apply and leaves the directory in
place, so an install.py that cannot even be read never causes this
script to delete the one copy it could not get back.

Python 3.9, standard library only. No network.

No em or en dashes anywhere in this file, its comments, or its output.
"""
import argparse
import ast
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Duplicated from scripts/doctor.py on purpose: each script in this
# directory is self-contained (doctor.py's own module docstring states
# this house rule), so this constant and the small detection helpers
# below are a deliberate, small copy rather than a cross-script import.
FENCE_BASENAME = "bm_fence_hook.py"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2


def _out(line=""):
    sys.stdout.write(line + "\n")


def _err(line=""):
    sys.stderr.write(line + "\n")


def _plugin_name():
    """The name THIS source tree's own plugin manifest claims. Falls
    back to 'brotherme' (the shipped name) when the manifest cannot be
    read, mirroring scripts/doctor.py's own _plugin_name."""
    manifest_path = os.path.join(ROOT, ".claude-plugin", "plugin.json")
    try:
        with io.open(manifest_path, encoding="utf-8") as fh:
            manifest = json.loads(fh.read())
    except (IOError, OSError, ValueError):
        return "brotherme"
    name = manifest.get("name") if isinstance(manifest, dict) else None
    return name if isinstance(name, str) and name else "brotherme"


def _read_settings(path):
    """(settings_dict, error_or_None). A missing file is a valid empty
    settings object (nothing wired yet), never an error; an unparsable
    or non-object file is an error, reported rather than guessed past."""
    if not os.path.isfile(path):
        return {}, None
    try:
        with io.open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except (IOError, OSError) as exc:
        return None, "could not be read: %s" % exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return None, "is not valid JSON: %s" % exc
    if not isinstance(data, dict):
        return None, "is JSON but not an object"
    return data, None


def _plugin_enabled(settings, plugin_name):
    enabled = settings.get("enabledPlugins")
    if not isinstance(enabled, dict):
        return False
    for key, value in enabled.items():
        if not value or not isinstance(key, str):
            continue
        if key.split("@", 1)[0] == plugin_name:
            return True
    return False


def _clone_fence_present(settings):
    """True when settings.json has a PreToolUse entry naming a REAL,
    on-disk bm_fence_hook.py. Mirrors scripts/doctor.py's own
    find_fence_entries plus _clone_fence_present, condensed to the one
    boolean this script needs."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return False
    groups = hooks.get("PreToolUse")
    if not isinstance(groups, list):
        return False
    for group in groups:
        if not isinstance(group, dict):
            continue
        for entry in group.get("hooks", []) or []:
            if not isinstance(entry, dict):
                continue
            command = entry.get("command")
            if not isinstance(command, str):
                continue
            try:
                words = shlex.split(command)
            except ValueError:  # sbe: allow-silent a hook command string this parser cannot tokenize simply cannot match the fence basename; other hook entries are still scanned
                continue
            for word in words:
                if os.path.basename(word) == FENCE_BASENAME \
                        and os.path.isfile(word):
                    return True
    return False


def _loader_wired(skill_dir):
    """True when `skill_dir`'s OWN hooks/hooks.json names the fence
    hook, the shape Claude Code auto-loads as a plugin with no
    settings.json block at all (scripts/doctor.py's
    loader_managed_fence_copies)."""
    hooks_path = os.path.join(skill_dir, "hooks", "hooks.json")
    try:
        with io.open(hooks_path, encoding="utf-8") as fh:
            text = fh.read()
    except (IOError, OSError):
        return False
    return FENCE_BASENAME in text


def _schema_version_of(path):
    """The SCHEMA_VERSION `path` would ACTUALLY USE, or None when that
    cannot be told without running the file.

    Still not an import: `path` may be a stale or foreign copy of
    bm_store.py, and importing it would run its module-level code. But
    the old text search asked the wrong question (M22, docs/plan/
    QUEUE.json, family evidence-integrity, cross-family debate
    2026-08-21 finding 10). It returned the integer after the FIRST
    "SCHEMA_VERSION = " line, which answers about the LITERAL rather
    than about the value the module would end up with. A file that
    assigns the name twice, or assigns it inside an `if`, or rebinds it
    from a function, certifies a version that is not real, and the
    caller then reports an install as current when it cannot reach a
    verdict at all.

    So the question is asked better instead of asked at runtime: parse
    the file (no execution) and answer only when the answer is
    unambiguous, which means the name is bound EXACTLY ONCE in the whole
    file, that binding sits at module level rather than inside any
    branch or body, and its value is a plain integer literal. Anything
    else is None, which every caller renders as NO-DATA. NO-DATA is
    never a pass and never a block: it says the version could not be
    told, which is the truth, where a guessed number is not."""
    try:
        with io.open(path, encoding="utf-8") as fh:
            source = fh.read()
    except (IOError, OSError):
        return None
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError):
        return None

    bindings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        elif isinstance(node, ast.NamedExpr):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "SCHEMA_VERSION":
                bindings.append(node)

    if len(bindings) != 1:
        return None
    node = bindings[0]
    # Module level and unconditional: a binding nested in an `if`, a
    # loop, a `try`, or a function body is decided at runtime.
    if not any(node is child for child in tree.body):
        return None
    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
        return None
    if isinstance(node, ast.Assign) and len(node.targets) != 1:
        return None
    value = node.value
    if value is None:
        return None
    # bool is a subclass of int and `SCHEMA_VERSION = True` is not a
    # schema number, so the type is checked exactly.
    if isinstance(value, ast.Constant) and type(value.value) is int:
        return value.value
    return None


def _real_schema_probe(current_store, wired_store):
    """(verdict, detail): verdict is "ok" (the wired copy reached a
    verdict against a real store), "gap" (it refused with a schema
    reason), or "no-data" (anything else: it refused for some other
    reason, crashed, or could not even be started). `detail` is the raw
    subprocess output for "gap" and "no-data", ignored for "ok".

    M26 (docs/plan/QUEUE.json, family required-proof): comparing
    SCHEMA_VERSION integers answers "does the source say it is behind",
    not "can this copy reach a verdict against a real store" -- a copy
    whose constant matches but is broken some OTHER way (a corrupt
    module, a missing dependency) would pass a constant comparison and
    still fail open on every write. So this asks the wired copy
    directly: build a throwaway project, let THIS project's OWN current
    bm_store.py `init` a real store in it (current_version by
    construction), then run the WIRED copy's own bm_store.py `verify`
    against that same store, read-only. Same throwaway-store technique
    tools/test_bm.py's TestM17FenceLivenessAgainstRealStore already uses
    (_old_install_one_schema_behind /
    test_real_store_ahead_of_wired_hook_fails_not_passes) and
    scripts/doctor.py's own _verify_real_store already runs against a
    real project's store, reused rather than reinvented.

    sys.executable, not the wired command's own interpreter: doctor.py's
    _verify_real_store can read the wired interpreter off the real
    settings.json command line (command_words[0]) because it already has
    that command in hand; this script only has a skill_dir path, no
    command line to read an interpreter from, so sys.executable is the
    only interpreter it can name without guessing. The M17 family
    recorded that which interpreter runs a real-store probe can matter
    (doctor.py fix-round B5); that tradeoff is accepted here, not
    silently defaulted.

    Never raises: every subprocess failure not started, timed out, or
    exiting nonzero is folded into "no-data" rather than an exception,
    per this project's own NO-DATA discipline."""
    tmp = tempfile.mkdtemp(prefix="bm-schema-probe-")
    try:
        env = dict(os.environ)
        env["BROTHERMODE_ROOT"] = tmp
        try:
            init_result = subprocess.run(
                [sys.executable, current_store, "init"],
                cwd=tmp, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, encoding="utf-8",
                # strict: this output only ever becomes a NO-DATA detail
                # string, never a silent pass, so a corrupted byte must
                # raise rather than be guessed past.
                errors="strict", timeout=60)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            return "no-data", "could not build the throwaway probe " \
                "store with %s: %s" % (current_store, exc)
        if init_result.returncode != 0:
            return "no-data", (
                "%s init exited %d building the throwaway probe store: %s"
                % (current_store, init_result.returncode,
                   ((init_result.stdout or "") + (init_result.stderr or "")).strip()))
        try:
            probe = subprocess.run(
                [sys.executable, wired_store, "verify"],
                cwd=tmp, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, encoding="utf-8",
                # strict: this output is pattern-matched below ("refused
                # (schema-ahead)" etc) to decide ok/gap/no-data, so a
                # lossy decode could silently swap one verdict for
                # another instead of raising.
                errors="strict", timeout=60)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            return "no-data", "could not run %s verify against the " \
                "real probe store: %s" % (wired_store, exc)
        output = (probe.stdout or "") + (probe.stderr or "")
        if probe.returncode == 0:
            return "ok", output
        if "refused (schema-ahead)" in output or \
                "refused (schema-behind)" in output:
            return "gap", output
        return "no-data", (
            "%s verify against the real probe store exited %d for a "
            "reason other than a schema refusal: %s"
            % (wired_store, probe.returncode, output.strip()[:500] or
               "(no output)"))
    finally:
        # A dry run must write nowhere except its own temporary
        # directory (M18's own docstring promise); this is that promise
        # kept for the probe's own scratch space.
        shutil.rmtree(tmp, ignore_errors=True)


def _schema_gap_message(skill_dir):
    """None when there is nothing to report: either this project's own
    copy could not be read (nothing to compare against, and that is
    this script's own tree, not the install being inspected, so it is
    not this install's fault), or the wired copy reached a verdict
    against a REAL, current-schema store. Otherwise a plain-language
    line: a NO-DATA line when the wired copy IS this project's own tree
    (running this same migrate_install.py from an installed copy makes
    ROOT and skill_dir the same directory; a symlinked dev install
    collapses them the same way), a NO-DATA line when the wired copy has
    no store file, no readable SCHEMA_VERSION, or fails the real probe
    for a reason other than schema, or the concrete SCHEMA GAP when the
    real probe shows it refused for a schema reason.

    WIRED is not the same as ABLE TO REACH A VERDICT (M18, docs/plan/
    QUEUE.json, family evidence-integrity): bm_store.py refuses to open
    a store already past the schema it understands (its own
    schema-ahead refusal), so a wired hook running a stale copy fails
    open on every write, same as no hook at all. scripts/doctor.py's own
    fence-liveness check had the identical blind spot until M17: it
    proved liveness only against a throwaway store the same code had
    just built, never against a real store already ahead of it.

    M26 (docs/plan/QUEUE.json, family required-proof) found the first
    fix for M18 only half done it: it compared SCHEMA_VERSION integers,
    which answers about the SOURCE, not about whether the wired copy can
    actually open a real store. The two integers below are kept only for
    the wording of the message; the verdict itself now comes from
    _real_schema_probe, a real subprocess run of the wired copy's own
    code against a real store."""
    current_store = os.path.join(ROOT, "tools", "bm_store.py")
    current_version = _schema_version_of(current_store)
    if current_version is None:
        return None
    wired_store = os.path.join(skill_dir, "tools", "bm_store.py")
    if os.path.realpath(wired_store) == os.path.realpath(current_store):
        return (
            "NO-DATA: %s is the same file as this project's own %s "
            "(same real path once symlinks resolve), so whether its "
            "wired fence hook can reach a verdict against this "
            "project's own schema %d store cannot be determined: a "
            "copy cannot be compared against itself."
            % (wired_store, current_store, current_version))
    if not os.path.isfile(wired_store):
        return (
            "NO-DATA: %s has no tools/bm_store.py, so whether its wired "
            "fence hook can reach a verdict against this project's own "
            "schema %d store cannot be determined."
            % (wired_store, current_version))
    # M22 (unchanged; a different property from M26 below): a
    # SCHEMA_VERSION this script cannot read WITHOUT AMBIGUITY (no such
    # line, set more than once, or set inside a branch) stays NO-DATA
    # here, before any probe. Probing would still return a real verdict
    # in most such cases (the module's LAST assignment is the one Python
    # actually uses), but the honest answer about what the SOURCE names
    # is that it cannot be told, and M22's own test pins that wording.
    wired_version = _schema_version_of(wired_store)
    if wired_version is None:
        return (
            "NO-DATA: %s does not name one plain SCHEMA_VERSION this "
            "script can read without running the file (no such line, "
            "or the name is set more than once, or set inside a branch, "
            "so the value it would really use is a runtime fact). "
            "Whether its wired fence hook can reach a verdict against "
            "this project's own schema %d store therefore cannot be "
            "determined." % (wired_store, current_version))
    # M26 (docs/plan/QUEUE.json, family required-proof): wired_version is
    # kept only for the wording below. The DECISION comes from a real
    # probe, never from comparing it against current_version: a
    # comparison that AGREES can still hide a copy that cannot actually
    # open a real store for some other reason.
    verdict, detail = _real_schema_probe(current_store, wired_store)
    if verdict == "ok":
        return None
    if verdict == "gap":
        return (
            "SCHEMA GAP: %s is stuck at schema %d while this project's "
            "own tools/bm_store.py is already at schema %d. A wired "
            "hook this far behind cannot reach a verdict: it refuses to "
            "open a store already past the schema it understands, and "
            "fails open on every write here, the same as if nothing "
            "were wired at all. Confirmed against a real probe store, "
            "not just the source constants: %s"
            % (wired_store, wired_version, current_version, detail.strip()))
    return (
        "NO-DATA: whether %s's wired fence hook can reach a verdict "
        "against this project's own schema %d store cannot be "
        "determined: %s" % (wired_store, current_version, detail.strip()))


def detect(home):
    """Returns a dict describing the install shape found under `home`.
    Never raises: every filesystem or JSON problem is folded into
    settings_error, and stranded is None (cannot tell) rather than
    guessed when settings.json could not be read."""
    plugin_name = _plugin_name()
    skill_dir = os.path.join(home, ".claude", "skills", plugin_name)
    settings_path = os.path.join(home, ".claude", "settings.json")
    has_skill_dir = os.path.isdir(skill_dir)
    settings, settings_error = _read_settings(settings_path)
    if settings_error is not None:
        has_plugin = False
        has_settings_wiring = False
        stranded = None
    else:
        has_plugin = _plugin_enabled(settings, plugin_name)
        has_settings_wiring = _clone_fence_present(settings)
        loader_wired = has_skill_dir and _loader_wired(skill_dir)
        stranded = has_skill_dir and not (
            has_plugin or has_settings_wiring or loader_wired)
    return {
        "plugin_name": plugin_name,
        "home": home,
        "skill_dir": skill_dir,
        "settings_path": settings_path,
        "has_skill_dir": has_skill_dir,
        "settings_error": settings_error,
        "has_plugin": has_plugin,
        "has_settings_wiring": has_settings_wiring,
        "stranded": stranded,
    }


def _install_dry_run_preview(info):
    """Runs scripts/install.py --dry-run against the SAME target and
    settings this migration would use, and returns its stdout, so the
    plan's "what it would install" and "which settings lines" sections
    are never reimplemented and never able to drift from what install.py
    actually does. Returns (lines, error): error is a plain string when
    install.py could not be run at all."""
    install_py = os.path.join(ROOT, "scripts", "install.py")
    try:
        result = subprocess.run(
            [sys.executable, install_py, "--dry-run", "--upgrade",
             "--target", info["skill_dir"],
             "--settings", info["settings_path"]],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8",
            # replace, not strict: this text only ever becomes a printed
            # preview line for a human (build_plan) or gates the apply
            # preflight on whether the call SUCCEEDED at all (main); no
            # caller parses this string to decide what gets deleted or
            # installed, so a lossy decode here trades a rare mangled
            # character for a preview that still renders, the same
            # errors="replace" idiom scripts/bm_commit_msg_hook.py and
            # tools/bm_project.py already use for a display-only read.
            errors="replace", timeout=300)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        # ValueError also catches UnicodeDecodeError: this call already
        # runs (as the printed plan's preview) before main()'s apply
        # branch ever touches the stranded directory, so a decode
        # failure caught here, rather than left to propagate, is what
        # lets that preflight refuse the apply before anything is
        # removed.
        return None, "could not run install.py --dry-run: %s" % exc
    text = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        return None, ("install.py --dry-run exited %d: %s"
                      % (result.returncode, text.strip()[:500] or "(no output)"))
    return text.rstrip("\n").splitlines(), None


def build_plan(info):
    """[line, ...]: the human-readable plan, never executing anything."""
    lines = ["PLAN for %s:" % info["home"], ""]
    if info["settings_error"] is not None:
        lines.append(
            "NO-DATA: %s %s, so whether %s is a stranded install cannot "
            "be determined. Fix that file first."
            % (info["settings_path"], info["settings_error"],
               info["skill_dir"]))
        return lines
    if not info["has_skill_dir"]:
        lines.append(
            "NOTHING TO DO: no skill-directory clone was found at %s; "
            "there is nothing to migrate." % info["skill_dir"])
        return lines
    if not info["stranded"]:
        which = ("a plugin install" if info["has_plugin"] else
                "a settings.json fence entry" if info["has_settings_wiring"]
                else "its own loader-managed hooks.json")
        gap = _schema_gap_message(info["skill_dir"])
        if gap is not None:
            lines.append(
                "%s has a wired fence hook (%s), but WIRED is not the "
                "whole question:" % (info["skill_dir"], which))
            lines.append(gap)
            lines.append("")
            lines.append(
                "FIX: reinstall over the same path to bring it current: "
                "scripts/install.py --upgrade --target %s --settings %s"
                % (info["skill_dir"], info["settings_path"]))
            return lines
        lines.append(
            "NOTHING TO DO: %s already has a wired fence hook (%s); it "
            "is not stranded." % (info["skill_dir"], which))
        return lines
    lines.append(
        "REMOVE: %s (a stranded skill-directory clone: the files are on "
        "disk, but no fence hook is wired for it anywhere, not in %s "
        "and not in its own hooks/hooks.json)."
        % (info["skill_dir"], info["settings_path"]))
    lines.append("")
    lines.append(
        "INSTALL: reinstall into the same path, freshly, via "
        "scripts/install.py --upgrade --target %s --settings %s"
        % (info["skill_dir"], info["settings_path"]))
    lines.append("")
    preview, preview_error = _install_dry_run_preview(info)
    if preview_error is not None:
        lines.append(
            "SETTINGS LINES: could not be previewed (%s). The plan above "
            "still stands; --apply would run install.py for real."
            % preview_error)
    else:
        lines.append("SETTINGS LINES install.py --dry-run would add "
                     "(this is its own real preview, not a guess):")
        for line in preview:
            lines.append("  " + line)
    return lines


def build_parser():
    p = argparse.ArgumentParser(
        prog="migrate_install.py",
        description="Detect and repair a stranded BrotherMode "
                    "skill-directory clone: print a dry-run plan by "
                    "default, apply only with --apply plus the "
                    "confirmation flag.")
    p.add_argument("--home", default=None,
                   help="the Claude Code home to inspect (default: the "
                        "operator's own real ~; every test in this "
                        "project always passes this explicitly, pointed "
                        "at a throwaway fixture)")
    p.add_argument("--apply", action="store_true",
                   help="perform the migration instead of only printing "
                        "the plan")
    p.add_argument("--i-understand-this-changes-every-session",
                   dest="confirmed", action="store_true",
                   help="required alongside --apply: confirms you "
                        "understand this rewires the hook wiring every "
                        "future Claude Code session under --home loads")
    return p


def main(argv):
    # This script's OWN stdout/stderr, reconfigured defensively: on a
    # machine whose locale is not UTF-8, the default text stream can be
    # ascii+strict (or ascii+surrogateescape, which still raises on a
    # genuine non-ASCII character, only forgiving already-invalid raw
    # bytes), so _out()/_err() printing a real --home path with a
    # non-ASCII directory name in it would raise here, before the plan
    # is even fully printed, let alone before any subprocess call is
    # reached. This is the direct, narrow fix for "a non-ASCII path or
    # message can raise" (this file's own module docstring): never
    # silently mangled (backslashreplace shows the exact byte rather
    # than dropping it), never a crash over a message this script is
    # about to print anyway.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
    args = build_parser().parse_args(argv)
    home = os.path.abspath(args.home or os.path.expanduser("~"))
    info = detect(home)
    for line in build_plan(info):
        _out(line)

    if not args.apply:
        _out("")
        _out("[dry-run] Nothing was changed. Pass --apply "
             "--i-understand-this-changes-every-session to perform this.")
        return EXIT_OK

    # The founder gate. A live swap rewires settings.json, and that file
    # is read at the start of EVERY future Claude Code session under
    # this home, not just this one, so --apply alone is never enough.
    if not args.confirmed:
        _out("")
        _err("REFUSED: --apply changes the Claude Code hook wiring that "
             "every session started under %s will load from now on. "
             "Pass --i-understand-this-changes-every-session as well to "
             "confirm you mean this. Nothing was changed." % home)
        return EXIT_REFUSED

    if info["settings_error"] is not None:
        _out("")
        _err("REFUSED: %s %s, so applying would mean guessing at what "
             "is currently wired rather than reading it. Fix that file "
             "first. Nothing was changed."
             % (info["settings_path"], info["settings_error"]))
        return EXIT_REFUSED

    if not info["has_skill_dir"] or not info["stranded"]:
        _out("")
        _out("Nothing to apply: not a stranded install.")
        return EXIT_OK

    _out("")
    # Preflight, BEFORE the one irreversible step: prove a real reinstall
    # against this same target and settings can actually run, decode
    # included, while the stranded directory still exists to fall back
    # on. This is the same install.py --dry-run call build_plan already
    # ran once for the printed plan above; running it again here costs a
    # few seconds (dry_run writes nothing, per install.py's own
    # contract) and is the only way to know, before shutil.rmtree, that
    # this is not the worst order: destroy the one copy, then discover
    # the reinstall could never even be read.
    _, preflight_error = _install_dry_run_preview(info)
    if preflight_error is not None:
        _err("migrate_install.py: refusing to remove %s: a reinstall "
             "preflight against the same target and settings failed, so "
             "removing it first would destroy the only copy on a path "
             "that cannot be proven to come back: %s"
             % (info["skill_dir"], preflight_error))
        return EXIT_FAILED

    _out("APPLYING:")
    try:
        shutil.rmtree(info["skill_dir"])
    except OSError as exc:
        _err("migrate_install.py: could not remove %s: %s"
             % (info["skill_dir"], exc))
        return EXIT_FAILED
    _out("  removed %s" % info["skill_dir"])

    install_py = os.path.join(ROOT, "scripts", "install.py")
    try:
        result = subprocess.run(
            [sys.executable, install_py, "--upgrade",
             "--target", info["skill_dir"],
             "--settings", info["settings_path"]],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8",
            # strict: this is the real install, and its exit code is
            # this script's own success signal (DONE below); a lossy
            # decode here could let a corrupted read stand next to a
            # returncode of 0, which is exactly the "exits 0 with
            # mangled content" outcome this row exists to rule out.
            errors="strict", timeout=300)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        _err("migrate_install.py: could not run install.py: %s" % exc)
        return EXIT_FAILED
    if result.stdout:
        _out(result.stdout.rstrip("\n"))
    if result.stderr:
        _err(result.stderr.rstrip("\n"))
    if result.returncode != 0:
        _err("migrate_install.py: install.py exited %d; the migration "
             "did not complete cleanly." % result.returncode)
        return EXIT_FAILED
    _out("")
    _out("DONE: %s is no longer stranded." % info["skill_dir"])
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
