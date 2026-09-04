#!/usr/bin/env python3
"""C3: wire Brother's hooks into Codex's OWN hooks configuration.

WHY THIS EXISTS. Codex 0.153.0-alpha.5 has hooks (`codex features list` prints
"hooks stable true") but plugin-delivered hooks were removed from it
("plugin_hooks removed false"), and the canonical plugin validator refuses a
manifest carrying a `hooks` key. So installing the Brother Codex plugin
installs skills and commands and NO hooks, and every Brother control that
rides on a hook (the single-writer fence above all) is simply absent there.
See docs/codex/HOOKS-MAPPING.md for that measurement.

What Codex does have is a USER-scope hooks file, and this script writes it.
Both halves of the mechanism were driven live against the app-bundled binary
on 2026-09-04 and are quoted in docs/codex/HOOKS-MAPPING.md:

  1. `<CODEX_HOME>/hooks.json` holds {"description": ..., "hooks": {Event:
     [{"matcher": ..., "hooks": [{"type": "command", "command": ...,
     "async": false, "timeoutSec": N, "statusMessage": ...}]}]}}. Codex's own
     `hooks/list` app-server method reads it back, which is how this script
     verifies its work rather than asserting it.
  2. A hook Codex has read is `untrusted` until `<CODEX_HOME>/config.toml`
     carries [hooks.state."<key>"] with enabled = true and the trusted_hash
     Codex itself reported for that hook. A wrong hash reads back as
     `modified`, never as trusted, so the trust half cannot be faked.

THE TRANSLATION, and where it is honest about a gap. Brother's shipped
hooks.json already uses Codex's own event names and its {"hooks": {...}}
envelope, so the translation is small and every difference is named:

  - `timeout` (Claude, seconds) becomes `timeoutSec` (Codex).
  - `async` is written explicitly as false: Brother's PreToolUse hooks are
    refusals, and a refusal that runs asynchronously cannot refuse anything.
  - `${CLAUDE_PLUGIN_ROOT}` is expanded to the product's own directory. A
    user-scope hooks file is not inside a plugin, so nothing would substitute
    it.
  - An event Codex does not know is SKIPPED with a printed NO-DATA naming it.
    Nothing is silently dropped and nothing is silently renamed.

SAFETY. This never writes into the founder's own ~/.codex unless
--allow-default-home is passed: the default is a refusal, because a hooks file
is the one file that can stop every edit on a machine. Boundary calls (every
file read, every write, the codex subprocess) have an explicit failure path.

Python 3.9, standard library only. No em or en dashes anywhere in this file or
its output.
"""

import argparse
import io
import json
import os
import queue
import subprocess
import sys
import threading
import time

#: Every hook event name Codex 0.153.0-alpha.5 knows, read from its own
#: app-server protocol schema (`codex app-server generate-json-schema --out
#: <dir>`, definition HookEventName) and cross-checked against the enum in the
#: shipped binary's string table. An event outside this set has no Codex
#: carrier and is reported NO-DATA rather than guessed at.
CODEX_EVENTS = (
    "PreToolUse", "PermissionRequest", "PostToolUse", "PreCompact",
    "PostCompact", "SessionStart", "SessionEnd", "UserPromptSubmit",
    "SubagentStart", "SubagentStop", "Stop", "Interrupt",
)

#: The products whose hooks.json this wires when no --product is given.
DEFAULT_PRODUCTS = ("products/brothermode", "products/brothersbe")

PLUGIN_ROOT_CITATION = "${CLAUDE_PLUGIN_ROOT}"

TRUST_BEGIN = "# >>> brother codex hook trust, written by scripts/codex_hooks_install.py"
TRUST_END = "# <<< brother codex hook trust"

CODEX_BIN_DEFAULT = "/Applications/ChatGPT.app/Contents/Resources/codex"


def repo_root():
    """This checkout's root: the parent of the directory holding this file."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_json(path):
    """{"data": ..., "problem": None} or {"data": None, "problem": "..."}.
    Never raises: a broken input is a reported refusal, not a traceback."""
    if not os.path.exists(path):
        return {"data": None, "problem": "no file at %s" % path}
    try:
        with io.open(path, encoding="utf-8") as handle:
            return {"data": json.load(handle), "problem": None}
    except (ValueError, OSError) as exc:
        return {"data": None, "problem": "%s could not be read: %s" % (path, exc)}


def translate(shipped, product_root):
    """Codex-shaped {"hooks": {...}} for one product, plus the events skipped.

    Returns {"hooks": {...}, "skipped": [(event, reason), ...]}. Never a bare
    tuple: this project reads a literal (x, y) return as an unregistered
    (verdict, evidence) pair.
    """
    hooks = {}
    skipped = []
    for event, blocks in (shipped or {}).get("hooks", {}).items():
        if event not in CODEX_EVENTS:
            skipped.append((event, "Codex 0.153 declares no hook event of that name"))
            continue
        translated_blocks = []
        for block in blocks:
            entries = []
            for hook in block.get("hooks", []):
                command = hook.get("command", "")
                if not command.strip():
                    skipped.append((event, "a hook block carried an empty command"))
                    continue
                entry = {
                    "type": "command",
                    "command": command.replace(PLUGIN_ROOT_CITATION, product_root),
                    "async": False,
                }
                if hook.get("timeout") is not None:
                    entry["timeoutSec"] = hook["timeout"]
                if hook.get("statusMessage"):
                    entry["statusMessage"] = hook["statusMessage"]
                entries.append(entry)
            if not entries:
                continue
            translated_block = {"hooks": entries}
            if block.get("matcher"):
                translated_block["matcher"] = block["matcher"]
            translated_blocks.append(translated_block)
        if translated_blocks:
            hooks.setdefault(event, []).extend(translated_blocks)
    return {"hooks": hooks, "skipped": skipped}


def build(products):
    """The whole Codex hooks document for `products` (a list of directories),
    plus every skip. Products are merged event by event in the order given."""
    merged = {}
    skipped = []
    problems = []
    for product in products:
        root = os.path.abspath(product)
        loaded = read_json(os.path.join(root, "hooks", "hooks.json"))
        if loaded["problem"]:
            problems.append(loaded["problem"])
            continue
        result = translate(loaded["data"], root)
        for event, blocks in result["hooks"].items():
            merged.setdefault(event, []).extend(blocks)
        skipped.extend((os.path.basename(root), event, why)
                       for event, why in result["skipped"])
    document = {
        "description": "Brother hooks, written by scripts/codex_hooks_install.py",
        "hooks": dict((event, merged[event])
                      for event in CODEX_EVENTS if event in merged),
    }
    return {"document": document, "skipped": skipped, "problems": problems}


def resolve_home(named, allow_default):
    """{"path": ..., "problem": None} or a refusal. The founder's own
    ~/.codex is refused unless it was asked for by name AND allowed.

    The path is RESOLVED, not merely made absolute. On macOS /tmp and /var are
    symlinks and Codex canonicalizes CODEX_HOME before it reports a hook's
    sourcePath, so a hooks file written under the un-resolved spelling is read
    back under the resolved one: the sourcePath filter in main() then matches
    nothing, hooks/list reports 0 hook(s), the trust block comes out empty, and
    every hook stays untrusted. Measured 2026-09-04 against the app-bundled
    codex with an isolated CODEX_HOME under mktemp -d, which hands out
    /var/folders/... for /private/var/folders/...
    """
    path = named or os.environ.get("CODEX_HOME") or ""
    if not path:
        return {"path": None, "problem":
                "no Codex home given: pass --codex-home <dir> or set CODEX_HOME"}
    path = os.path.realpath(os.path.expanduser(path))
    default_home = os.path.realpath(os.path.expanduser(os.path.join("~", ".codex")))
    if path == default_home and not allow_default:
        return {"path": None, "problem":
                "refusing to write %s, the real Codex home: a hooks file can "
                "refuse every edit on this machine, so pass "
                "--allow-default-home to mean it" % path}
    return {"path": path, "problem": None}


def hooks_json_path(home):
    return os.path.join(home, "hooks.json")


def dump(document):
    return json.dumps(document, indent=2, sort_keys=False) + "\n"


def write_hooks_json(home, document):
    """{"problem": None} on success. Creates the home if it is missing."""
    try:
        if not os.path.isdir(home):
            os.makedirs(home)
        with io.open(hooks_json_path(home), "w", encoding="utf-8") as handle:
            handle.write(dump(document))
    except OSError as exc:
        return {"problem": "could not write %s: %s" % (hooks_json_path(home), exc)}
    return {"problem": None}


def list_hooks(codex_bin, home, cwd):
    """Codex's own reading of what it now has wired, via the app-server
    `hooks/list` method. {"entries": [...], "problem": None} or a refusal.

    This is the verification seam: the script never claims a hook is wired on
    the strength of having written a file, it asks the client.
    """
    if not os.path.exists(codex_bin):
        return {"entries": None,
                "problem": "no Codex binary at %s" % codex_bin}
    requests = [
        json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"clientInfo": {"name": "brother-codex-hooks",
                                              "title": "Brother",
                                              "version": "1"}}}),
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "hooks/list",
                    "params": {"cwds": [os.path.abspath(cwd)]}}),
    ]
    env = dict(os.environ)
    env["CODEX_HOME"] = home
    # The app server exits as soon as its stdin closes, so subprocess.run's
    # write-then-close would race the answer away. Hold stdin open, read
    # replies on a thread, and stop at the first one carrying our id.
    try:
        proc = subprocess.Popen([codex_bin, "app-server"], env=env, text=True,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
    except OSError as exc:
        return {"entries": None, "problem": "codex app-server did not start: %s" % exc}
    replies = queue.Queue()

    def pump():
        try:
            for line in proc.stdout:
                replies.put(line)
        except (OSError, ValueError):
            pass
        replies.put(None)

    reader = threading.Thread(target=pump)
    reader.daemon = True
    reader.start()
    try:
        proc.stdin.write("\n".join(requests) + "\n")
        proc.stdin.flush()
    except OSError as exc:
        proc.kill()
        return {"entries": None, "problem": "codex app-server closed stdin: %s" % exc}
    deadline = time.time() + 120
    lines = []
    while time.time() < deadline:
        try:
            line = replies.get(timeout=1)
        except queue.Empty:  # sbe: allow-silent reader-only: an empty second is not a reply, and the deadline above ends the wait; nothing is dropped
            continue
        if line is None:
            break
        lines.append(line)
        if '"id":1' in line.replace(" ", ""):
            break
    proc.kill()
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            message = json.loads(line)
        except ValueError:  # sbe: allow-silent reader-only: a stream line that is not JSON carries no reply id, and the caller reports a problem when no id-1 result is found
            continue
        if message.get("id") != 1:
            continue
        result = message.get("result") or {}
        data = result.get("data") or []
        entries = []
        warnings = []
        for block in data:
            entries.extend(block.get("hooks") or [])
            warnings.extend(block.get("warnings") or [])
        if warnings:
            return {"entries": entries,
                    "problem": "codex reported: " + "; ".join(warnings)}
        return {"entries": entries, "problem": None}
    return {"entries": None,
            "problem": "codex app-server returned no hooks/list result"}


def trust_block(entries, source_path):
    """The [hooks.state."..."] TOML for every hook Codex sourced from
    `source_path`, using the hash Codex itself reported."""
    lines = [TRUST_BEGIN]
    for entry in entries:
        if entry.get("sourcePath") != source_path:
            continue
        key = entry.get("key")
        current = entry.get("currentHash")
        if not key or not current:
            continue
        lines.append('[hooks.state."%s"]' % key)
        lines.append("enabled = true")
        lines.append('trusted_hash = "%s"' % current)
    lines.append(TRUST_END)
    return "\n".join(lines) + "\n"


def write_trust(home, block):
    """Replace this script's own trust section in config.toml, leaving every
    other line alone. Refuses when a [hooks.state. table exists outside the
    markers: that is somebody else's trust decision, not ours to rewrite."""
    path = os.path.join(home, "config.toml")
    existing = ""
    if os.path.exists(path):
        try:
            with io.open(path, encoding="utf-8") as handle:
                existing = handle.read()
        except OSError as exc:
            return {"problem": "could not read %s: %s" % (path, exc)}
    before, ours, after = _split_trust(existing)
    if "[hooks.state." in before + after:
        return {"problem": "%s already carries a [hooks.state. table outside "
                           "this script's markers; resolve it by hand" % path}
    try:
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write(before + block + after)
    except OSError as exc:
        return {"problem": "could not write %s: %s" % (path, exc)}
    return {"problem": None}


def _split_trust(text):
    """(before, ours, after) around this script's marker pair. `ours` is ""
    when the markers are absent."""
    start = text.find(TRUST_BEGIN)
    if start < 0:
        prefix = text if text.endswith("\n") or not text else text + "\n"
        return (prefix, "", "")
    end = text.find(TRUST_END, start)
    if end < 0:
        return (text[:start], text[start:], "")
    end += len(TRUST_END)
    if text[end:end + 1] == "\n":
        end += 1
    return (text[:start], text[start:end], text[end:])


def brother_commands(document):
    """Every command string this script writes for the named products. This is
    the identity used by --uninstall: a hook is Brother's because Brother wrote
    that exact command, never because it sits in a file Brother also wrote."""
    return set(hook.get("command")
               for blocks in document.get("hooks", {}).values()
               for block in blocks
               for hook in block.get("hooks", [])
               if hook.get("command"))


def strip_hooks(existing, commands):
    """{"document": ..., "removed": [(event, command), ...]}: the same hooks
    document with Brother's own commands taken out and everything else left
    exactly where it was, empty blocks and empty events dropped."""
    removed = []
    kept = {}
    for event, blocks in (existing or {}).get("hooks", {}).items():
        kept_blocks = []
        for block in blocks:
            entries = []
            for hook in block.get("hooks", []):
                if hook.get("command") in commands:
                    removed.append((event, hook.get("command")))
                    continue
                entries.append(hook)
            if entries:
                kept_block = dict(block)
                kept_block["hooks"] = entries
                kept_blocks.append(kept_block)
        if kept_blocks:
            kept[event] = kept_blocks
    document = dict(existing or {})
    document["hooks"] = kept
    return {"document": document, "removed": removed}


def uninstall(home, commands):
    """Remove Brother's hooks and its trust section from `home`, printing what
    went. 0 when something was removed, 0 with a NO-DATA line when there was
    nothing of Brother's to remove, 1 on a refusal."""
    path = hooks_json_path(home)
    removed = []
    hooks_left = None
    if os.path.exists(path):
        loaded = read_json(path)
        if loaded["problem"]:
            print("codex_hooks_install: FAIL: %s" % loaded["problem"])
            return 1
        stripped = strip_hooks(loaded["data"], commands)
        removed = stripped["removed"]
        hooks_left = stripped["document"]["hooks"]
        if removed:
            try:
                if hooks_left:
                    with io.open(path, "w", encoding="utf-8") as handle:
                        handle.write(dump(stripped["document"]))
                else:
                    os.remove(path)
            except OSError as exc:
                print("codex_hooks_install: FAIL: could not rewrite %s: %s"
                      % (path, exc))
                return 1

    config_path = os.path.join(home, "config.toml")
    trust_removed = False
    if os.path.exists(config_path):
        try:
            with io.open(config_path, encoding="utf-8") as handle:
                existing = handle.read()
        except OSError as exc:
            print("codex_hooks_install: FAIL: could not read %s: %s"
                  % (config_path, exc))
            return 1
        before, ours, after = _split_trust(existing)
        if ours:
            try:
                with io.open(config_path, "w", encoding="utf-8") as handle:
                    handle.write(before + after)
            except OSError as exc:
                print("codex_hooks_install: FAIL: could not rewrite %s: %s"
                      % (config_path, exc))
                return 1
            trust_removed = True

    if not removed and not trust_removed:
        print("codex_hooks_install: NO-DATA: nothing of Brother's to remove "
              "from %s" % home)
        return 0
    if removed:
        events = sorted(set(event for event, _command in removed))
        print("codex_hooks_install: removed %d Brother hook command(s) across "
              "%s from %s" % (len(removed), ", ".join(events), path))
        for event, command in removed:
            print("codex_hooks_install:   %s: %s" % (event, command))
        if hooks_left:
            print("codex_hooks_install: kept %d event(s) of other hooks in %s"
                  % (len(hooks_left), path))
        else:
            print("codex_hooks_install: removed %s: no other hooks were in it"
                  % path)
    if trust_removed:
        print("codex_hooks_install: removed Brother's trust section from %s"
              % config_path)
    return 0


def check(home, document):
    """PASS, FAIL or NO-DATA on what is on disk against what would be written.
    NO-DATA when no Codex hooks file exists at all: unexamined is never clean.
    """
    path = hooks_json_path(home)
    loaded = read_json(path)
    if loaded["problem"]:
        if not os.path.exists(path):
            return ("NO-DATA", "no Codex hooks file at %s: Brother's hooks are "
                               "not wired into this Codex home" % path)
        return ("FAIL", loaded["problem"])
    if loaded["data"] == document:
        return ("PASS", "%s matches the shipped hooks of every named product" % path)
    return ("FAIL", "%s differs from the shipped hooks of the named products; "
                    "re-run without --check to rewrite it" % path)


def main(argv):
    parser = argparse.ArgumentParser(
        description="Wire Brother's hooks into a Codex home's own hooks.json.")
    parser.add_argument("--codex-home", default=None,
                        help="the Codex home to write (defaults to CODEX_HOME)")
    parser.add_argument("--allow-default-home", action="store_true",
                        help="permit writing the real ~/.codex")
    parser.add_argument("--product", action="append", default=None,
                        help="a product directory holding hooks/hooks.json "
                             "(repeatable; defaults to both Brother products)")
    parser.add_argument("--check", action="store_true",
                        help="report PASS, FAIL or NO-DATA and write nothing")
    parser.add_argument("--uninstall", action="store_true",
                        help="remove Brother's own hook commands and its trust "
                             "section, leaving every other hook in place")
    parser.add_argument("--trust", action="store_true",
                        help="also persist Codex's hook trust in config.toml, "
                             "using the hash Codex itself reports")
    parser.add_argument("--codex-bin", default=CODEX_BIN_DEFAULT,
                        help="the Codex binary used to read the wiring back")
    parser.add_argument("--cwd", default=None,
                        help="the working directory hooks/list is asked about")
    args = parser.parse_args(argv[1:])

    products = args.product or [os.path.join(repo_root(), rel)
                                for rel in DEFAULT_PRODUCTS]
    built = build(products)
    for problem in built["problems"]:
        print("codex_hooks_install: FAIL: %s" % problem)
    if built["problems"]:
        return 1
    for product, event, why in built["skipped"]:
        print("codex_hooks_install: NO-DATA: %s event %s not wired: %s"
              % (product, event, why))

    resolved = resolve_home(args.codex_home, args.allow_default_home)
    if resolved["problem"]:
        print("codex_hooks_install: FAIL: %s" % resolved["problem"])
        return 1
    home = resolved["path"]

    if args.uninstall:
        if args.check:
            print("codex_hooks_install: FAIL: --check reports and --uninstall "
                  "writes; run one or the other")
            return 1
        return uninstall(home, brother_commands(built["document"]))

    if args.check:
        verdict, detail = check(home, built["document"])
        print("codex_hooks_install: %s: %s" % (verdict, detail))
        return 0 if verdict == "PASS" else 1

    written = write_hooks_json(home, built["document"])
    if written["problem"]:
        print("codex_hooks_install: FAIL: %s" % written["problem"])
        return 1
    events = sorted(built["document"]["hooks"])
    count = sum(len(block["hooks"])
                for blocks in built["document"]["hooks"].values()
                for block in blocks)
    print("codex_hooks_install: wrote %s: %d command(s) across %s"
          % (hooks_json_path(home), count, ", ".join(events)))

    listing = list_hooks(args.codex_bin, home, args.cwd or os.getcwd())
    if listing["problem"]:
        print("codex_hooks_install: NO-DATA: could not read the wiring back "
              "from Codex: %s" % listing["problem"])
        return 1
    entries = listing["entries"] or []
    print("codex_hooks_install: codex hooks/list reports %d hook(s) from %s"
          % (len([e for e in entries
                  if e.get("sourcePath") == hooks_json_path(home)]),
             hooks_json_path(home)))
    if not args.trust:
        untrusted = [e for e in entries if e.get("trustStatus") != "trusted"]
        if untrusted:
            print("codex_hooks_install: NO-DATA: %d hook(s) read back as "
                  "untrusted; re-run with --trust, or pass "
                  "--dangerously-bypass-hook-trust to codex" % len(untrusted))
        return 0

    block = trust_block(entries, hooks_json_path(home))
    trusted = write_trust(home, block)
    if trusted["problem"]:
        print("codex_hooks_install: FAIL: %s" % trusted["problem"])
        return 1
    confirm = list_hooks(args.codex_bin, home, args.cwd or os.getcwd())
    if confirm["problem"]:
        print("codex_hooks_install: NO-DATA: trust written but not confirmed: "
              "%s" % confirm["problem"])
        return 1
    states = [e.get("trustStatus") for e in (confirm["entries"] or [])]
    if states and all(state == "trusted" for state in states):
        print("codex_hooks_install: PASS: codex reports all %d hook(s) trusted "
              "and enabled" % len(states))
        return 0
    print("codex_hooks_install: FAIL: codex reports trust states %s"
          % sorted(set(states)))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
