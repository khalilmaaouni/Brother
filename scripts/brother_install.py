#!/usr/bin/env python3
"""A2: one tool that takes a Codex home from an unknown state to Brother's
END STATE and back, honestly.

END STATE: plugin brother@brother at the requested tag, and NOTHING ELSE of
Brother's: no brothermode@brother plugin, no standalone brothermode skill
under `<CODEX_HOME>/skills/`, no stale user-scope Brother hooks in
`<CODEX_HOME>/hooks.json`.

With explicit --product-skills, two isolated companion packages expose the
existing product skills. The umbrella remains the sole hook owner. Companion
packages come from the pinned public checkout or an explicitly named export.

WHY THIS EXISTS. scripts/codex_hooks_install.py wires ONE thing (hooks.json)
into an already-installed Codex home. This tool drives the plugin lifecycle
itself, end to end, against the app-bundled binary
(/Applications/ChatGPT.app/Contents/Resources/codex, the same default as that
sibling), so an upgrade or a rollback is one command instead of a remembered
sequence of `codex plugin` calls.

MEASURED, on this machine on 2026-09-06, against the same bundled binary:

  - `codex plugin marketplace add <url> --ref <tag> --json` prints
    {"marketplaceName", "installedRoot", "alreadyAdded": bool}. Calling it
    twice at the SAME url and ref is a no-op (alreadyAdded: true). Calling it
    at a DIFFERENT ref while the marketplace is already added fails with
    "already added from a different source; remove it before adding this
    source" on stderr, exit 1: re-pointing therefore means remove then add,
    which is exactly what a sibling battery leg (leg_b8 in
    ~/.claude/evidence/codex-battery/run_battery.py) already does by hand.
  - `codex plugin marketplace remove <name> --json` prints
    {"marketplaceName", "installedRoot"} (no alreadyAdded key).
  - `codex plugin add <id>@<marketplace> --json` prints {"pluginId", "name",
    "marketplaceName", "version", "installedPath", "authPolicy"} on success,
    or "Error: ..." on stderr at exit 1 (a plugin absent from that ref's
    marketplace reads "was not found in marketplace").
  - `codex plugin remove <id>@<marketplace> --json` prints {"pluginId",
    "name", "marketplaceName"} and DELETES the plugin's own
    [plugins."<id>"] table from config.toml and its cache directory itself:
    this tool never hand-edits config.toml for plugin or marketplace state,
    it only reads `ref = "..."` back from a `[marketplaces.<name>]` table
    for `status`, because `plugin marketplace list --json` does not report
    the ref it holds.
  - On the public marketplace, brothermode@brother is installable on v1.0.8
    and NOT on v1.0.6 (both checked live): the MEASURED FACTS handed to this
    tool said both tags carried it, and that was wrong for v1.0.6. Every
    verb here treats "was it present" as a live question, not an assumption
    from a tag number.
  - Setting HOME for a Codex subprocess is deliberately NOT done here (a
    sibling battery script does it to isolate `auth.json`): only CODEX_HOME
    is set, so a session's own git identity and shell state are untouched.

Python 3.9, standard library only. No em or en dashes anywhere in this file
or its output. Every file read, write and subprocess call has an explicit
failure path: a broken input is a reported FAIL or NO-DATA, never a
traceback under someone's install.
"""

import argparse
import glob
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

import brother_paths
import codex_product_skills

CODEX_BIN_DEFAULT = "/Applications/ChatGPT.app/Contents/Resources/codex"
MARKETPLACE_URL_DEFAULT = "https://github.com/khalilmaaouni/Brother"
PLUGIN_NAME = "brother"
STANDALONE_PLUGIN_NAME = "brothermode"

PROG = "brother_install"


def repo_root():
    """This checkout's root: the parent of the directory holding this file."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_codex_home():
    """~/.codex or CODEX_HOME, never ~/.claude: brother_paths.config_dir()
    picks between the two clients, and this tool is Codex-only, so the
    client rung is pinned here rather than left to guess at the running
    process (which might be a Claude Code session driving this script)."""
    env = dict(os.environ)
    env["BROTHER_CLIENT"] = "codex"
    return brother_paths.config_dir(env)


def resolve_home(named, allow_default):
    """{"path": ..., "problem": None} or a refusal. Mirrors
    codex_hooks_install.resolve_home: the founder's own ~/.codex is refused
    unless named AND allowed, and the path is REALPATH'd because macOS
    resolves /tmp and /var through symlinks and a mismatch there is how a
    write lands somewhere this tool never reports."""
    path = named or default_codex_home()
    if not path:
        return {"path": None, "problem": "no Codex home given: pass --codex-home <dir>"}
    path = os.path.realpath(os.path.expanduser(path))
    # A Codex home is a directory of its own, never a filesystem root and
    # never the user's whole home: uninstall removes <home>/skills,
    # <home>/hooks.json and <home>/brother/*, and pointed at "/" or "~" by
    # a typo those names may already belong to somebody else.
    real_home = os.path.realpath(os.path.expanduser("~"))
    if path == os.path.dirname(path) or path == real_home:
        return {"path": None, "problem":
                "refusing %s as a Codex home: it is a filesystem root or "
                "the user's own home directory, and a Codex home is a "
                "directory of its own (for example ~/.codex)" % path}
    real_default = os.path.realpath(os.path.expanduser(os.path.join("~", ".codex")))
    if path == real_default and not allow_default:
        return {"path": None, "problem":
                "refusing to write %s, the real Codex home: pass "
                "--allow-default-home to mean it" % path}
    return {"path": path, "problem": None}


def hooks_json_path(home):
    return os.path.join(home, "hooks.json")


def read_json_file(path):
    """{"data", "problem", "missing"}. "missing" distinguishes "nothing to
    read" (never a failure) from "something unreadable sits there" (a FAIL),
    which a plain {"data": None} could not tell apart."""
    if not os.path.exists(path):
        return {"data": None, "problem": None, "missing": True}
    try:
        with io.open(path, encoding="utf-8") as handle:
            return {"data": json.load(handle), "problem": None, "missing": False}
    except (ValueError, OSError) as exc:
        return {"data": None, "problem": "%s could not be read: %s" % (path, exc),
                "missing": False}


def atomic_write(path, data):
    """{"problem": None} on success. A temp file beside `path` then
    os.replace: a crash mid-write, or a simulated one in a test, leaves the
    original byte for byte where it was."""
    directory = os.path.dirname(path) or "."
    tmp = os.path.join(directory, ".%s.tmp-%d" % (os.path.basename(path), os.getpid()))
    try:
        if not os.path.isdir(directory):
            os.makedirs(directory)
        with io.open(tmp, "w", encoding="utf-8") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except OSError as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:  # sbe: allow-silent cleanup of an interrupted atomic-write temp file cannot change the reported original write failure
            pass
        return {"problem": "could not write %s: %s" % (path, exc)}
    return {"problem": None}


def sha256_file(path):
    """{"digest": hex or None, "problem": None or str}. Never raises: a file
    that vanishes mid-hash (a race with something else on the machine) is a
    reported problem, not a crash."""
    digest = hashlib.sha256()
    try:
        with io.open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
    except OSError as exc:
        return {"digest": None, "problem": str(exc)}
    return {"digest": digest.hexdigest(), "problem": None}


def marketplace_name_from_source(source):
    """The name Codex gives a marketplace added from `source`: the basename
    of the URL or path, `.git` stripped, lowercased. Matches what was
    measured live: "https://github.com/khalilmaaouni/Brother" names itself
    "brother"."""
    base = os.path.basename(source.rstrip("/"))
    if base.endswith(".git"):
        base = base[:-4]
    return base.lower()


def read_marketplace_ref(config_text, name):
    """The `ref` value from `[marketplaces.<name>]` in config.toml's own
    text, or None if that table or key is absent. A line-scoped regex, not a
    TOML parser: Codex itself writes this table (see the module docstring),
    so this tool only ever needs to read it back for `status`."""
    header = re.search(r'^\[marketplaces\.%s\]\s*$' % re.escape(name),
                        config_text, re.M)
    if not header:
        return None
    tail = config_text[header.end():]
    next_header = re.search(r'^\[', tail, re.M)
    body = tail[:next_header.start()] if next_header else tail
    ref_line = re.search(r'^ref\s*=\s*"([^"]*)"\s*$', body, re.M)
    return ref_line.group(1) if ref_line else None


# ---------------------------------------------------------------------------
# The Codex subprocess seam. Tests substitute --codex-bin with a fake script.


def run_codex(codex_bin, args, home, timeout=120):
    """{"returncode", "stdout", "stderr", "problem"}. `problem` is set only
    when the binary itself could not be run (missing, timed out); a normal
    non-zero exit from Codex is reported through returncode/stderr like any
    other command, never folded into `problem`."""
    if not os.path.exists(codex_bin):
        return {"returncode": None, "stdout": "", "stderr": "",
                "problem": "no Codex binary at %s" % codex_bin}
    env = dict(os.environ)
    env["CODEX_HOME"] = home
    try:
        proc = subprocess.run([codex_bin] + list(args), env=env, timeout=timeout,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"returncode": None, "stdout": "", "stderr": "",
                "problem": "%s %s failed: %s" % (codex_bin, " ".join(args), exc)}
    return {"returncode": proc.returncode, "stdout": proc.stdout,
            "stderr": proc.stderr, "problem": None}


def plugin_list(codex_bin, home):
    """{"installed": [...], "problem": None or str}: the parsed body of
    `codex plugin list --json`."""
    result = run_codex(codex_bin, ["plugin", "list", "--json"], home)
    if result["problem"]:
        return {"installed": [], "problem": result["problem"]}
    if result["returncode"] != 0:
        return {"installed": [], "problem": (result["stderr"] or result["stdout"]).strip()
                or "codex plugin list --json exited %s" % result["returncode"]}
    try:
        data = json.loads(result["stdout"])
    except ValueError as exc:
        return {"installed": [], "problem": "plugin list --json did not parse: %s" % exc}
    return {"installed": data.get("installed", []), "problem": None}


def ensure_marketplace(codex_bin, home, source, ref):
    """{"status": PASS|NO-CHANGE|FAIL, "detail": str}. Adds the marketplace
    at `ref`; if it is already present from a DIFFERENT source (Codex's own
    wording, measured live), removes it first and re-adds, since Codex
    refuses to re-point one in place."""
    args = ["plugin", "marketplace", "add", source]
    if ref is not None:
        args += ["--ref", ref]
    args += ["--json"]
    result = run_codex(codex_bin, args, home)
    if result["problem"]:
        return {"status": "FAIL", "detail": result["problem"]}
    body = (result["stdout"] or "") + (result["stderr"] or "")
    if result["returncode"] == 0:
        try:
            data = json.loads(result["stdout"])
        except ValueError:
            data = {}
        changed = not data.get("alreadyAdded", False)
        return {"status": "PASS" if changed else "NO-CHANGE",
                "detail": "ref %s (%s)" % (ref, "added" if changed else "already present"),
                "installed_root": data.get("installedRoot")}
    if "already added from a different source" in body:
        name = marketplace_name_from_source(source)
        rm = run_codex(codex_bin, ["plugin", "marketplace", "remove", name, "--json"], home)
        if rm["problem"] or rm["returncode"] != 0:
            return {"status": "FAIL", "detail":
                    "could not remove marketplace %s to re-point: %s"
                    % (name, rm["problem"] or (rm["stderr"] or rm["stdout"]).strip())}
        add2 = run_codex(codex_bin, args, home)
        if add2["problem"] or add2["returncode"] != 0:
            return {"status": "FAIL", "detail":
                    "could not re-add marketplace at %s: %s"
                    % (ref, add2["problem"] or (add2["stderr"] or add2["stdout"]).strip())}
        try:
            data = json.loads(add2["stdout"])
        except ValueError:
            data = {}
        return {"status": "PASS", "detail": "re-pointed from a different source to %s" % ref,
                "installed_root": data.get("installedRoot")}
    return {"status": "FAIL", "detail": body.strip() or "marketplace add failed"}


def ensure_plugin_added(codex_bin, home, plugin_id):
    """{"status", "detail", "version"}. `codex plugin add` always succeeds
    again over an already-installed plugin (measured live: no error, no
    "already installed" field), so NO-CHANGE is decided by comparing the
    version before and after, not by skipping the call."""
    before = plugin_list(codex_bin, home)
    before_version = None
    if before["problem"] is None:
        before_version = next((e.get("version") for e in before["installed"]
                                if e.get("pluginId") == plugin_id), None)
    result = run_codex(codex_bin, ["plugin", "add", plugin_id, "--json"], home)
    if result["problem"] or result["returncode"] != 0:
        detail = result["problem"] or (result["stderr"] or result["stdout"]).strip()
        return {"status": "FAIL", "detail": detail or "plugin add failed", "version": None}
    try:
        data = json.loads(result["stdout"])
    except ValueError as exc:
        return {"status": "FAIL", "detail": "plugin add --json did not parse: %s" % exc,
                "version": None}
    version = data.get("version")
    status = "NO-CHANGE" if version and version == before_version else "PASS"
    return {"status": status, "detail": "version %s" % version, "version": version,
            "installed_path": data.get("installedPath")}


def ensure_plugin_removed(codex_bin, home, plugin_id):
    """{"status", "detail"}: unconditional `codex plugin remove`. Callers
    check presence first via plugin_list so an absent plugin is reported
    NO-CHANGE by them, not FAIL by this function."""
    result = run_codex(codex_bin, ["plugin", "remove", plugin_id, "--json"], home)
    if result["problem"] or result["returncode"] != 0:
        detail = result["problem"] or (result["stderr"] or result["stdout"]).strip()
        return {"status": "FAIL", "detail": detail or "plugin remove failed"}
    return {"status": "PASS", "detail": "removed"}


# ---------------------------------------------------------------------------
# Standalone skill detection (filesystem only, no Codex subprocess).

_SKILL_NAME_RE = re.compile(r'(?im)^name:\s*brothermode\s*$')


def find_brothermode_skill_dirs(codex_home):
    """Every directory under `<CODEX_HOME>/skills/` that is Brother's
    standalone brothermode skill: the literal `skills/brothermode` path, and
    any other directory whose SKILL.md front matter names it `brothermode`.
    Never descends into dotted directories (Codex's own `.system` skills
    live there and are never Brother's)."""
    skills_dir = os.path.join(codex_home, "skills")
    found = []
    if not os.path.isdir(skills_dir):
        return found
    direct = os.path.join(skills_dir, "brothermode")
    if os.path.isdir(direct):
        found.append(direct)
    try:
        names = sorted(os.listdir(skills_dir))
    except OSError:  # sbe: allow-silent an unreadable skill directory contributes no discoverable standalone skill
        return found
    for name in names:
        if name.startswith("."):
            continue
        path = os.path.join(skills_dir, name)
        if path in found or not os.path.isdir(path):
            continue
        skill_md = os.path.join(path, "SKILL.md")
        if not os.path.isfile(skill_md):
            continue
        try:
            with io.open(skill_md, encoding="utf-8", errors="replace") as handle:
                head = handle.read(4096)
        except OSError:  # sbe: allow-silent a temp-root canonicalization failure only omits that root from stale-target detection
            continue
        if _SKILL_NAME_RE.search(head):
            found.append(path)
    return found


# ---------------------------------------------------------------------------
# hooks.json pruning (JSON only, no TOML, no Codex subprocess).


def _command_paths(command):
    """Every absolute or home-relative path token in a hook command, via
    shlex so quoting is respected. Never raises: a command shlex cannot
    tokenize yields no paths, which reads as "nothing to check"."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return []
    return [t for t in tokens if t.startswith("/") or t.startswith("~")]


def is_brother_command(command):
    """A hook command names a Brother tool when one of its path tokens
    contains "brother" (case-insensitive): this catches brother_run.py,
    codex_hooks_install.py's own writes, and anything under a brothermode
    plugin cache path, without needing a registry of every tool name."""
    for path in _command_paths(command):
        if "brother" in path.lower():
            return True
    return False


def _tempdirs():
    dirs = set()
    for candidate in (tempfile.gettempdir(), "/tmp", "/private/tmp",
                      os.environ.get("TMPDIR", "")):
        if not candidate:
            continue
        try:
            dirs.add(os.path.realpath(candidate))
        except OSError:  # sbe: allow-silent optional tempdir alias is not needed for installation safety
            pass
    return dirs


def is_stale_target(command):
    """True when the command's first path token sits under a temp
    directory, or does not exist at all. Only meaningful once
    is_brother_command has already said yes; called separately so the two
    conditions stay readable at each call site."""
    paths = _command_paths(command)
    if not paths:
        return False
    target = os.path.expanduser(paths[0])
    if not os.path.exists(target):
        return True
    real = os.path.realpath(target)
    return any(real == d or real.startswith(d + os.sep) for d in _tempdirs())


def prune_brother_hooks(document, only_stale):
    """{"document", "removed": [(event, command), ...]}. `only_stale=True`
    is the install-time prune (a Brother hook whose target is gone or under
    a temp dir); `only_stale=False` is the uninstall-time prune (every
    Brother hook, live or not). A non-Brother hook is never inspected for
    staleness at all: is_brother_command gates first, every time."""
    removed = []
    kept = {}
    for event, blocks in (document or {}).get("hooks", {}).items():
        kept_blocks = []
        for block in blocks:
            entries = []
            for hook in block.get("hooks", []):
                command = hook.get("command", "")
                brother = is_brother_command(command)
                take = brother and (not only_stale or is_stale_target(command))
                if take:
                    removed.append((event, command))
                    continue
                entries.append(hook)
            if entries:
                kept_block = dict(block)
                kept_block["hooks"] = entries
                kept_blocks.append(kept_block)
        if kept_blocks:
            kept[event] = kept_blocks
    doc = dict(document or {})
    doc["hooks"] = kept
    return {"document": doc, "removed": removed}


def write_or_remove_hooks_document(path, document):
    """Write `document` if it still carries any hooks, else remove the file:
    an empty {"hooks": {}} left on disk is indistinguishable from "still
    wired" to a casual `ls`, so it is removed instead."""
    if document.get("hooks"):
        return atomic_write(path, json.dumps(document, indent=2) + "\n")
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as exc:
        return {"problem": "could not remove %s: %s" % (path, exc)}
    return {"problem": None}


# ---------------------------------------------------------------------------
# User-state hashing (upgrade's "did we touch anything of the founder's").

USER_STATE_GLOBS = ("auth.json", "installation_id", "memories*.sqlite",
                     "goals*.sqlite", "thread_history*.sqlite")


def user_state_snapshot(home):
    """{relpath: sha256-hex-or-None} for every named user-state file this
    tool must never change, plus every file under sessions/. A file that
    disappears between snapshots still shows up as a key whose value went
    from a digest to None, so a deletion is caught exactly like an edit."""
    snap = {}
    for pattern in USER_STATE_GLOBS:
        for path in sorted(glob.glob(os.path.join(home, pattern))):
            if os.path.isfile(path):
                snap[os.path.relpath(path, home)] = sha256_file(path)["digest"]
    sessions_dir = os.path.join(home, "sessions")
    if os.path.isdir(sessions_dir):
        for root, _dirs, files in os.walk(sessions_dir):
            for name in files:
                path = os.path.join(root, name)
                snap[os.path.relpath(path, home)] = sha256_file(path)["digest"]
    return snap


def diff_user_state(before, after):
    """Every relpath whose digest changed, appeared, or disappeared."""
    changed = []
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            changed.append(key)
    return changed


# ---------------------------------------------------------------------------
# The four verbs.


def do_install(codex_bin, home, marketplace, ref, product_skills=False, product_skills_export=None):
    steps = []
    plugin_id = "%s@%s" % (PLUGIN_NAME, marketplace_name_from_source(marketplace))
    bm_id = "%s@%s" % (STANDALONE_PLUGIN_NAME, marketplace_name_from_source(marketplace))

    mres = ensure_marketplace(codex_bin, home, marketplace, ref)
    print("%s: %s: marketplace at %s (%s)" % (PROG, mres["status"], ref, mres["detail"]))
    steps.append(mres["status"])

    pres = ensure_plugin_added(codex_bin, home, plugin_id)
    print("%s: %s: %s (%s)" % (PROG, pres["status"], plugin_id, pres["detail"]))
    steps.append(pres["status"])

    listing = plugin_list(codex_bin, home)
    if listing["problem"]:
        print("%s: FAIL: could not read plugin list to check for %s: %s"
              % (PROG, bm_id, listing["problem"]))
        steps.append("FAIL")
    elif any(e.get("pluginId") == bm_id for e in listing["installed"]):
        rres = ensure_plugin_removed(codex_bin, home, bm_id)
        print("%s: %s: removed stray %s" % (PROG, rres["status"], bm_id))
        steps.append(rres["status"])
    else:
        print("%s: NO-CHANGE: %s not present" % (PROG, bm_id))
        steps.append("NO-CHANGE")

    skill_dirs = find_brothermode_skill_dirs(home)
    if not skill_dirs:
        print("%s: NO-CHANGE: no standalone brothermode skill directory" % PROG)
        steps.append("NO-CHANGE")
    else:
        for path in skill_dirs:
            try:
                shutil.rmtree(path)
                print("%s: PASS: removed standalone skill directory %s" % (PROG, path))
                steps.append("PASS")
            except OSError as exc:
                print("%s: FAIL: could not remove %s: %s" % (PROG, path, exc))
                steps.append("FAIL")

    hooks_path = hooks_json_path(home)
    loaded = read_json_file(hooks_path)
    if loaded["missing"]:
        print("%s: NO-CHANGE: no hooks.json at %s" % (PROG, hooks_path))
        steps.append("NO-CHANGE")
    elif loaded["problem"]:
        print("%s: FAIL: %s" % (PROG, loaded["problem"]))
        steps.append("FAIL")
    else:
        pruned = prune_brother_hooks(loaded["data"], only_stale=True)
        if not pruned["removed"]:
            print("%s: NO-CHANGE: no stale Brother hooks in %s" % (PROG, hooks_path))
            steps.append("NO-CHANGE")
        else:
            written = write_or_remove_hooks_document(hooks_path, pruned["document"])
            if written["problem"]:
                print("%s: FAIL: %s" % (PROG, written["problem"]))
                steps.append("FAIL")
            else:
                print("%s: PASS: pruned %d stale Brother hook command(s) from %s"
                      % (PROG, len(pruned["removed"]), hooks_path))
                for event, command in pruned["removed"]:
                    print("%s:   %s: %s" % (PROG, event, command))
                steps.append("PASS")

    final_listing = plugin_list(codex_bin, home)
    if final_listing["problem"]:
        print("%s: FAIL: could not verify: %s" % (PROG, final_listing["problem"]))
        steps.append("FAIL")
        version = None
    else:
        version = next((e.get("version") for e in final_listing["installed"]
                         if e.get("pluginId") == plugin_id), None)
        has_bm = any(e.get("pluginId") == bm_id for e in final_listing["installed"])
        if version and not has_bm:
            print("%s: PASS: codex reports %s at %s, %s absent"
                  % (PROG, plugin_id, version, bm_id))
            steps.append("PASS")
        else:
            print("%s: FAIL: codex reports %s (version=%s), %s present=%s"
                  % (PROG, plugin_id, version, bm_id, has_bm))
            steps.append("FAIL")

    if product_skills and "FAIL" not in steps:
        companions = install_product_skills(codex_bin, home, marketplace, ref, mres, product_skills_export)
        steps.append(companions["status"])
        print("%s: %s: product skills (%s)" % (PROG, companions["status"], companions["detail"]))
    verdict = "FAIL" if "FAIL" in steps else "PASS"
    print("%s: %s: install at %s complete" % (PROG, verdict, ref))
    return {"verdict": verdict, "steps": steps, "plugin_id": plugin_id, "version": version}



def product_skills_state_path(home):
    return os.path.join(home, "brother", "product-skills.json")


def product_skills_status(home, listing):
    loaded = read_json_file(product_skills_state_path(home))
    if loaded["missing"]:
        return {"status": "NO-DATA", "detail": "no product skills installation record"}
    state = loaded["data"]
    if loaded["problem"] or not isinstance(state, dict) or listing.get("problem"):
        return {"status": "FAIL", "detail": "product skills installation state is unreadable"}
    try:
        problems = codex_product_skills.verify(state["marketplace_root"])
        versions = {entry.get("pluginId"): entry.get("version") for entry in listing["installed"]}
        with open(os.path.join(state["marketplace_root"], codex_product_skills.RECORD), encoding="utf-8") as handle:
            record = json.load(handle)
        for name in codex_product_skills.PRODUCTS:
            plugin_id = name + "@" + codex_product_skills.MARKETPLACE
            plugin = state["plugins"][plugin_id]
            if versions.get(plugin_id) != plugin["version"]:
                problems.append("installed product version mismatch: " + name)
            prefix = "plugins/" + name + "/"
            for rel, digest in record["files"].items():
                if rel.startswith(prefix):
                    got = sha256_file(os.path.join(plugin["installed_path"], rel[len(prefix):]))
                    if got["digest"] != digest:
                        problems.append("installed product content mismatch: " + name)
                        break
        return {"status": "FAIL" if problems else "PASS",
                "detail": "; ".join(problems) if problems else "all recorded product skills and support files present",
                "skills": state["skills"]}
    except (OSError, ValueError, KeyError, TypeError):
        return {"status": "FAIL", "detail": "product skills installation record is malformed"}


def install_product_skills(codex_bin, home, source, ref, marketplace_result, export_root=None):
    """Only the pinned public checkout is inferred. Local exports are explicit."""
    official = source.rstrip("/").removesuffix(".git") == MARKETPLACE_URL_DEFAULT
    if export_root is None and not official:
        return {"status": "FAIL", "detail": "local marketplace requires explicit --product-skills-export"}
    exported = export_root or marketplace_result.get("installed_root")
    if not exported:
        return {"status": "FAIL", "detail": "Codex returned no pinned marketplace installedRoot"}
    try:
        built = codex_product_skills.build(exported, os.path.join(home, "brother", "product-skills"))
    except (OSError, ValueError, KeyError) as exc:
        return {"status": "FAIL", "detail": "could not build product skill packages: " + str(exc)}
    return activate_product_skills(codex_bin, home, dict(built, source_ref=ref, source_marketplace=source))


def activate_product_skills(codex_bin, home, built):
    problems = codex_product_skills.verify(built["marketplace_root"])
    if problems:
        return {"status": "FAIL", "detail": "; ".join(problems)}
    mres = ensure_marketplace(codex_bin, home, built["marketplace_root"], None)
    if mres["status"] == "FAIL":
        return mres
    previous_plugins = built.get("plugins", {})
    state = dict(built, plugins={})
    for name in codex_product_skills.PRODUCTS:
        plugin_id = name + "@" + codex_product_skills.MARKETPLACE
        result = ensure_plugin_added(codex_bin, home, plugin_id)
        if result["status"] == "FAIL" or not result.get("installed_path"):
            return {"status": "FAIL", "detail": "product installation did not return a verified path: " + plugin_id}
        if plugin_id in previous_plugins and result["version"] != previous_plugins[plugin_id]["version"]:
            return {"status": "FAIL", "detail": "product rollback version mismatch: " + plugin_id}
        state["plugins"][plugin_id] = {"version": result["version"], "installed_path": result["installed_path"]}
    written = atomic_write(product_skills_state_path(home), json.dumps(state, indent=2, sort_keys=True) + "\n")
    if written["problem"]:
        return {"status": "FAIL", "detail": written["problem"]}
    return product_skills_status(home, plugin_list(codex_bin, home))


def snapshot_root(home):
    return os.path.join(home, "brother", "rollback")


def make_snapshot(codex_bin, home, marketplace, ref):
    """{"problem", "dir", "state"}: a durable pre-change record under
    `<CODEX_HOME>/brother/rollback/<timestamp>-<ref>/`, never under a temp
    directory, so a rollback survives a reboot exactly like the plugin cache
    it is a snapshot of."""
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    snap = os.path.join(snapshot_root(home), "%s-%s" % (stamp, ref))
    try:
        os.makedirs(snap)
    except OSError as exc:
        return {"problem": "could not create snapshot dir %s: %s" % (snap, exc),
                "dir": None, "state": None}
    for name in ("config.toml", "hooks.json"):
        src = os.path.join(home, name)
        if os.path.exists(src):
            try:
                shutil.copy2(src, os.path.join(snap, name))
            except OSError as exc:
                return {"problem": "could not copy %s into snapshot: %s" % (src, exc),
                        "dir": snap, "state": None}
    cache_root = os.path.join(home, "plugins", "cache")
    listing = {}
    if os.path.isdir(cache_root):
        for root, _dirs, files in os.walk(cache_root):
            for name in files:
                path = os.path.join(root, name)
                rel = os.path.relpath(path, cache_root)
                listing[rel] = sha256_file(path)["digest"]
    plugins = plugin_list(codex_bin, home)
    versions = {}
    if plugins["problem"] is None:
        versions = dict((e.get("pluginId"), e.get("version")) for e in plugins["installed"])
    state = {"ref": ref, "marketplace_source": marketplace,
             "plugin_versions": versions, "cache_listing": listing,
             "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    product_state = read_json_file(product_skills_state_path(home))
    if product_state["problem"]:
        return {"problem": product_state["problem"], "dir": snap, "state": None}
    state["product_skills"] = product_state["data"]
    written = atomic_write(os.path.join(snap, "state.json"),
                            json.dumps(state, indent=2, sort_keys=True) + "\n")
    if written["problem"]:
        return {"problem": written["problem"], "dir": snap, "state": None}
    return {"problem": None, "dir": snap, "state": state}


def do_upgrade(codex_bin, home, marketplace, from_ref, ref, product_skills=False, product_skills_export=None):
    before = user_state_snapshot(home)
    snap = make_snapshot(codex_bin, home, marketplace, from_ref)
    if snap["problem"]:
        print("%s: FAIL: could not snapshot pre-upgrade state: %s" % (PROG, snap["problem"]))
        return {"verdict": "FAIL"}
    print("%s: PASS: snapshotted pre-upgrade state (ref %s) to %s"
          % (PROG, from_ref, snap["dir"]))

    plugin_id = "%s@%s" % (PLUGIN_NAME, marketplace_name_from_source(marketplace))
    version_before = snap["state"]["plugin_versions"].get(plugin_id)

    result = do_install(codex_bin, home, marketplace, ref, product_skills, product_skills_export)

    after = user_state_snapshot(home)
    changed = diff_user_state(before, after)
    if changed:
        print("%s: FAIL: user state file(s) changed during upgrade: %s"
              % (PROG, ", ".join(changed)))
        return {"verdict": "FAIL"}
    print("%s: PASS: %d user state file(s) checked, none changed"
          % (PROG, len(set(before) | set(after))))

    if result["verdict"] != "PASS":
        print("%s: FAIL: upgrade %s -> %s did not pass install" % (PROG, from_ref, ref))
        return {"verdict": "FAIL"}
    version_after = result["version"]
    if version_before == version_after:
        print("%s: FAIL: version did not move (%s -> %s)"
              % (PROG, version_before, version_after))
        return {"verdict": "FAIL"}
    print("%s: PASS: upgrade %s -> %s, %s version %s -> %s"
          % (PROG, from_ref, ref, plugin_id, version_before, version_after))
    return {"verdict": "PASS"}


def find_snapshot(home, to=None):
    if to:
        return to if os.path.isdir(to) else None
    root = snapshot_root(home)
    if not os.path.isdir(root):
        return None
    entries = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
    return os.path.join(root, entries[-1]) if entries else None


def do_rollback(codex_bin, home, to=None):
    snap = find_snapshot(home, to)
    if not snap:
        print("%s: NO-DATA: no rollback snapshot under %s" % (PROG, snapshot_root(home)))
        return {"verdict": "NO-DATA"}
    loaded = read_json_file(os.path.join(snap, "state.json"))
    if loaded["problem"] or loaded["data"] is None:
        print("%s: FAIL: could not read snapshot state at %s: %s"
              % (PROG, snap, loaded["problem"] or "missing state.json"))
        return {"verdict": "FAIL"}
    state = loaded["data"]
    ref = state["ref"]
    source = state["marketplace_source"]

    mres = ensure_marketplace(codex_bin, home, source, ref)
    print("%s: %s: marketplace re-pointed to %s (%s)" % (PROG, mres["status"], ref, mres["detail"]))

    plugin_id = "%s@%s" % (PLUGIN_NAME, marketplace_name_from_source(source))
    pres = ensure_plugin_added(codex_bin, home, plugin_id)
    print("%s: %s: %s reinstalled (%s)" % (PROG, pres["status"], plugin_id, pres["detail"]))

    wanted_products = state.get("product_skills")
    if wanted_products:
        try:
            restored = activate_product_skills(codex_bin, home, wanted_products)
        except (KeyError, TypeError, ValueError, OSError):
            restored = {"status": "FAIL", "detail": "malformed product skills snapshot"}
        if restored["status"] == "FAIL":
            print("%s: FAIL: product skills rollback (%s)" % (PROG, restored["detail"]))
            return {"verdict": "FAIL"}
    else:
        current = plugin_list(codex_bin, home)
        if current["problem"]:
            return {"verdict": "FAIL"}
        present_ids = {p.get("pluginId") for p in current["installed"]}
        for product in codex_product_skills.PRODUCTS:
            product_id = product + "@" + codex_product_skills.MARKETPLACE
            if product_id in present_ids and ensure_plugin_removed(codex_bin, home, product_id)["status"] == "FAIL":
                return {"verdict": "FAIL"}
        try:
            if os.path.exists(product_skills_state_path(home)):
                os.remove(product_skills_state_path(home))
        except OSError:
            return {"verdict": "FAIL"}

    for name in ("config.toml", "hooks.json"):
        src = os.path.join(snap, name)
        if not os.path.exists(src):
            continue
        try:
            with io.open(src, encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            print("%s: FAIL: could not read snapshot %s: %s" % (PROG, src, exc))
            return {"verdict": "FAIL"}
        written = atomic_write(os.path.join(home, name), text)
        if written["problem"]:
            print("%s: FAIL: %s" % (PROG, written["problem"]))
            return {"verdict": "FAIL"}
        print("%s: PASS: restored %s from snapshot" % (PROG, name))

    if mres["status"] == "FAIL" or pres["status"] == "FAIL":
        print("%s: FAIL: rollback to %s did not complete cleanly" % (PROG, ref))
        return {"verdict": "FAIL"}

    listing = plugin_list(codex_bin, home)
    if listing["problem"]:
        print("%s: FAIL: could not verify after rollback: %s" % (PROG, listing["problem"]))
        return {"verdict": "FAIL"}
    got = next((e.get("version") for e in listing["installed"]
                if e.get("pluginId") == plugin_id), None)
    want = state["plugin_versions"].get(plugin_id)
    if got and got == want:
        print("%s: PASS: rolled back to %s, %s version %s matches snapshot"
              % (PROG, ref, plugin_id, got))
        return {"verdict": "PASS"}
    print("%s: FAIL: rollback version mismatch: got %s want %s" % (PROG, got, want))
    return {"verdict": "FAIL"}


def do_uninstall(codex_bin, home, marketplace):
    if not os.path.isdir(home):
        print("%s: NO-DATA: nothing of Brother's is installed under %s" % (PROG, home))
        return {"verdict": "NO-DATA"}

    did_something = False
    listing = plugin_list(codex_bin, home)
    installed_ids = set(e.get("pluginId") for e in listing["installed"]) \
        if listing["problem"] is None else set()
    marketplace_name = marketplace_name_from_source(marketplace)
    owned_ids = ["%s@%s" % (name, marketplace_name) for name in (STANDALONE_PLUGIN_NAME, PLUGIN_NAME)]
    owned_ids += [name + "@" + codex_product_skills.MARKETPLACE for name in codex_product_skills.PRODUCTS]
    for plugin_id in owned_ids:
        if plugin_id in installed_ids:
            res = ensure_plugin_removed(codex_bin, home, plugin_id)
            print("%s: %s: removed %s (%s)" % (PROG, res["status"], plugin_id, res["detail"]))
            if res["status"] == "FAIL":
                return {"verdict": "FAIL"}
            did_something = did_something or res["status"] == "PASS"
        else:
            print("%s: NO-CHANGE: %s not installed" % (PROG, plugin_id))

    mlist = run_codex(codex_bin, ["plugin", "marketplace", "list", "--json"], home)
    present = False
    if mlist["problem"] is None and mlist["returncode"] == 0:
        try:
            data = json.loads(mlist["stdout"])
            present = any(m.get("name") == marketplace_name for m in data.get("marketplaces", []))
        except ValueError:
            present = False
    if mlist["problem"] is None and mlist["returncode"] == 0:
        try:
            companion_present = any(m.get("name") == codex_product_skills.MARKETPLACE
                                    for m in json.loads(mlist["stdout"]).get("marketplaces", []))
        except ValueError:
            companion_present = False
        if companion_present:
            removed = run_codex(codex_bin, ["plugin", "marketplace", "remove", codex_product_skills.MARKETPLACE, "--json"], home)
            if removed["problem"] or removed["returncode"] != 0:
                return {"verdict": "FAIL"}
            did_something = True
    if present:
        rm = run_codex(codex_bin, ["plugin", "marketplace", "remove", marketplace_name, "--json"],
                        home)
        ok = rm["problem"] is None and rm["returncode"] == 0
        print("%s: %s: marketplace %s removed" % (PROG, "PASS" if ok else "FAIL", marketplace_name))
        if not ok:
            return {"verdict": "FAIL"}
        did_something = True
    else:
        print("%s: NO-CHANGE: marketplace %s not present" % (PROG, marketplace_name))

    skill_dirs = find_brothermode_skill_dirs(home)
    if not skill_dirs:
        print("%s: NO-CHANGE: no standalone brothermode skill directory" % PROG)
    for path in skill_dirs:
        try:
            shutil.rmtree(path)
            print("%s: PASS: removed skill directory %s" % (PROG, path))
            did_something = True
        except OSError as exc:
            print("%s: FAIL: could not remove %s: %s" % (PROG, path, exc))
            return {"verdict": "FAIL"}

    hooks_path = hooks_json_path(home)
    loaded = read_json_file(hooks_path)
    if loaded["missing"]:
        print("%s: NO-CHANGE: no hooks.json at %s" % (PROG, hooks_path))
    elif loaded["problem"]:
        print("%s: FAIL: %s" % (PROG, loaded["problem"]))
        return {"verdict": "FAIL"}
    else:
        pruned = prune_brother_hooks(loaded["data"], only_stale=False)
        if not pruned["removed"]:
            print("%s: NO-CHANGE: no Brother hooks in %s" % (PROG, hooks_path))
        else:
            written = write_or_remove_hooks_document(hooks_path, pruned["document"])
            if written["problem"]:
                print("%s: FAIL: %s" % (PROG, written["problem"]))
                return {"verdict": "FAIL"}
            print("%s: PASS: removed %d Brother hook command(s) from %s"
                  % (PROG, len(pruned["removed"]), hooks_path))
            did_something = True

    brother_dir = os.path.join(home, "brother")
    if os.path.isdir(brother_dir):
        kept = []
        for entry in sorted(os.listdir(brother_dir)):
            if entry in ("rollback", "runs", "product-skills"):
                kept.append(entry)
                continue
            path = os.path.join(brother_dir, entry)
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                did_something = True
            except OSError as exc:
                print("%s: FAIL: could not remove %s: %s" % (PROG, path, exc))
                return {"verdict": "FAIL"}
        if kept:
            print("%s: kept %s under %s: rollback sources or user data, retained by uninstall"
                  % (PROG, ", ".join(kept), brother_dir))

    if not did_something:
        print("%s: NO-DATA: nothing of Brother's is installed under %s" % (PROG, home))
        return {"verdict": "NO-DATA"}
    print("%s: PASS: uninstall complete" % PROG)
    return {"verdict": "PASS"}


def do_status(codex_bin, home, marketplace, product_skills=False):
    marketplace_name = marketplace_name_from_source(marketplace)
    plugin_id = "%s@%s" % (PLUGIN_NAME, marketplace_name)
    bm_id = "%s@%s" % (STANDALONE_PLUGIN_NAME, marketplace_name)
    report = {"codex_home": home, "marketplace": None, "ref": None,
              "brother_version": None, "brothermode_present": False,
              "standalone_skill_dirs": [], "stale_hooks": []}

    listing = plugin_list(codex_bin, home)
    if listing["problem"]:
        print("%s: NO-DATA: could not read plugin list: %s" % (PROG, listing["problem"]))
    else:
        for entry in listing["installed"]:
            if entry.get("pluginId") == plugin_id:
                report["marketplace"] = entry.get("marketplaceName")
                report["brother_version"] = entry.get("version")
            if entry.get("pluginId") == bm_id:
                report["brothermode_present"] = True

    config_path = os.path.join(home, "config.toml")
    if os.path.exists(config_path):
        try:
            with io.open(config_path, encoding="utf-8") as handle:
                report["ref"] = read_marketplace_ref(handle.read(), marketplace_name)
        except OSError as exc:
            print("%s: NO-DATA: could not read %s: %s" % (PROG, config_path, exc))

    report["standalone_skill_dirs"] = find_brothermode_skill_dirs(home)

    hooks_path = hooks_json_path(home)
    loaded = read_json_file(hooks_path)
    if loaded["data"] is not None:
        pruned = prune_brother_hooks(loaded["data"], only_stale=True)
        report["stale_hooks"] = pruned["removed"]

    end_state = (report["brother_version"] is not None
                 and not report["brothermode_present"]
                 and not report["standalone_skill_dirs"]
                 and not report["stale_hooks"])
    partial = (report["brother_version"] is not None or report["brothermode_present"]
               or report["standalone_skill_dirs"] or report["stale_hooks"])
    if product_skills:
        companion = product_skills_status(home, listing)
        report["product_skills"] = companion
        end_state = end_state and companion["status"] == "PASS"
        partial = partial or companion["status"] != "NO-DATA"
        print("%s: %s: product skills (%s)" % (PROG, companion["status"], companion["detail"]))
    verdict = "END-STATE" if end_state else ("PARTIAL" if partial else "ABSENT")

    print("%s: %s at %s" % (PROG, PLUGIN_NAME, home))
    print("%s:   %s: version=%s ref=%s" % (PROG, plugin_id, report["brother_version"], report["ref"]))
    print("%s:   %s present: %s" % (PROG, bm_id, report["brothermode_present"]))
    print("%s:   standalone skill dir(s): %s" % (PROG, report["standalone_skill_dirs"] or "none"))
    print("%s:   stale Brother hook(s): %d" % (PROG, len(report["stale_hooks"])))
    print("%s: %s" % (PROG, verdict))
    report["verdict"] = verdict
    return report


# ---------------------------------------------------------------------------


def build_argparser():
    parser = argparse.ArgumentParser(
        description="Take a Codex home to Brother's end state and back.")
    sub = parser.add_subparsers(dest="verb", required=True)

    def common(p):
        p.add_argument("--codex-home", default=None,
                        help="the Codex home to operate on (defaults to CODEX_HOME or ~/.codex)")
        p.add_argument("--codex-bin", default=CODEX_BIN_DEFAULT,
                        help="the Codex binary to drive")
        p.add_argument("--marketplace", default=MARKETPLACE_URL_DEFAULT,
                        help="marketplace URL or local path (default the public Brother repo)")
        p.add_argument("--allow-default-home", action="store_true",
                        help="permit writing the real ~/.codex")
        p.add_argument("--product-skills", action="store_true",
                       help="include the existing BrotherMode and BrotherSBE skills")
        p.add_argument("--product-skills-export", default=None,
                       help="explicit allowlisted export root for a local marketplace")
        p.add_argument("--json", action="store_true",
                        help="print a machine-readable result as the last line")

    p_install = sub.add_parser("install", help="install or repair brother@brother at --ref")
    common(p_install)
    p_install.add_argument("--ref", required=True, help="the tag to install")

    p_upgrade = sub.add_parser("upgrade", help="snapshot, then install at a new ref")
    common(p_upgrade)
    p_upgrade.add_argument("--from-ref", required=True, help="the ref being upgraded from")
    p_upgrade.add_argument("--ref", required=True, help="the ref being upgraded to")

    p_rollback = sub.add_parser("rollback", help="restore the newest (or named) snapshot")
    common(p_rollback)
    p_rollback.add_argument("--to", default=None, help="a specific snapshot directory")

    p_uninstall = sub.add_parser("uninstall", help="remove everything of Brother's")
    common(p_uninstall)

    p_status = sub.add_parser("status", help="read-only report")
    common(p_status)

    return parser


def main(argv):
    parser = build_argparser()
    args = parser.parse_args(argv[1:])

    resolved = resolve_home(args.codex_home, args.allow_default_home)
    if resolved["problem"]:
        print("%s: FAIL: %s" % (PROG, resolved["problem"]))
        return 1
    home = resolved["path"]

    if args.verb == "install":
        result = do_install(args.codex_bin, home, args.marketplace, args.ref, args.product_skills, args.product_skills_export)
    elif args.verb == "upgrade":
        result = do_upgrade(args.codex_bin, home, args.marketplace, args.from_ref, args.ref, args.product_skills, args.product_skills_export)
    elif args.verb == "rollback":
        result = do_rollback(args.codex_bin, home, args.to)
    elif args.verb == "uninstall":
        result = do_uninstall(args.codex_bin, home, args.marketplace)
    elif args.verb == "status":
        result = do_status(args.codex_bin, home, args.marketplace, args.product_skills)
    else:
        print("%s: FAIL: unknown verb %s" % (PROG, args.verb))
        return 1

    if args.json:
        print(json.dumps(result, sort_keys=True))

    verdict = result.get("verdict")
    if args.verb == "uninstall":
        return 0 if verdict in ("PASS", "NO-DATA") else 1
    if args.verb == "rollback":
        return 0 if verdict == "PASS" else (0 if verdict == "NO-DATA" else 1)
    if args.verb == "status":
        return 0 if verdict == "END-STATE" else (0 if verdict in ("PARTIAL", "ABSENT") else 1)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
