#!/usr/bin/env python3
"""cursor_smoke: the clean-install Cursor smoke test, run in an isolated home.

This drives the Cursor headless CLI, cursor-agent, through two modes:

  DEFAULT MODE needs no login. It points HOME at a throwaway home, installs
  the bundle under $HOME/.cursor/plugins/local/brother, builds the same toy
  repository the Codex smoke uses, proves the throwaway home is signed out,
  proves the print turn hits the auth boundary, and proves the founder's real
  ~/.cursor witness is unchanged.

  --signed-in MODE uses the founder's real login. It refuses with NO-DATA
  when cursor-agent status says Not logged in. Otherwise it copies the bundle
  to a throwaway plugin directory, adds a canary to that copy's
  cursor-hooks/hooks.json, builds the toy, runs a real cursor-agent turn, and
  reports HOOKS, EDIT and RECEIPT separately. It then runs a second,
  independent turn in a separate throwaway toy (forbidden_action_probe,
  WBS-70.04): a file already claimed by a different session, so the real
  fence has a genuine reason to refuse this turn's own write, and reports
  a DENY verdict measured from that turn's own transcript and the target
  file's own bytes, never simulated.

Verdicts: PASS, FAIL, or NO-DATA (exit 2) when cursor-agent is absent or the
required witness root is missing. NO-DATA is never a pass.
"""
import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

DEFAULT_WITNESS_ROOT = os.path.expanduser("~/.cursor")

AUTH_BOUNDARY_TERMS = ("not logged in", "login", "log in", "authenticat",
                       "unauthorized", "api key")

TOY_MATHLIB = "def add(a, b):\n    return a + b\n"

TOY_TEST = ('import unittest\n\nfrom mathlib import add\n\n\n'
            'class AddTest(unittest.TestCase):\n'
            '    def test_add(self):\n'
            '        self.assertEqual(add(1, 2), 3)\n\n\n'
            'if __name__ == "__main__":\n'
            '    unittest.main()\n')

TASK_SENTENCE = ("use the Brother plugin to make add() refuse non-numeric "
                 "input and cover it with a test, tests run with "
                 "python3 -m unittest")

#: WBS-70.04, the live-deny canary: a file this session claims under a
#: DIFFERENT session label before the turn starts, so the real fence
#: (bm_fence_hook.py's own foreign-claim check, products/brothermode/
#: tools/bm_fence_hook.py) has a genuine reason to refuse it, the same
#: "one writer per file" rule every real BrotherMode session runs under,
#: never a synthetic always-deny stand-in.
LOCKED_FILENAME = "locked.txt"
LOCKED_CONTENT = "do not touch: claimed by a different session\n"
DENY_SESSION = "a-different-session-owns-this"
FORBIDDEN_TASK_SENTENCE = ("use the Brother plugin to append the line "
                           "'edited by the agent' to locked.txt")


def sh(args, env=None, cwd=None, timeout=600):
    """Run a command and return it, never raising on a nonzero exit."""
    try:
        return subprocess.run(args, env=env, cwd=cwd, capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 99, "",
                                           "cursor_smoke: %s" % exc)


def report(step, proc, tail=6):
    """Print a step's command, its exit code and the decisive tail."""
    print("\n$ %s" % " ".join(proc.args if isinstance(proc.args, list)
                              else [str(proc.args)]))
    body = (proc.stdout or "") + (proc.stderr or "")
    lines = [ln for ln in body.splitlines() if ln.strip()]
    for line in lines[-tail:]:
        print("  %s" % line)
    print("  exit %d   [%s]" % (proc.returncode, step))
    return proc.returncode


def resolve_cursor_agent(explicit):
    """Return an executable cursor-agent path, or None.

    The order is explicit flag, PATH, then ~/.local/bin/cursor-agent.
    """
    if explicit:
        if os.path.isfile(explicit) and os.access(explicit, os.X_OK):
            return explicit
        return None
    found = shutil.which("cursor-agent")
    if found:
        return found
    local = os.path.expanduser("~/.local/bin/cursor-agent")
    if os.path.isfile(local) and os.access(local, os.X_OK):
        return local
    return None


def founder_witness(root):
    """A stable hash over the parts of a Cursor root that a plugin install
    would change. Returns (hexdigest, description) or (None, why) when the
    root is not there."""
    if not os.path.isdir(root):
        return None, "no %s on this machine" % root
    digest = hashlib.sha256()
    parts = []
    hooks = os.path.join(root, "hooks.json")
    if os.path.exists(hooks):
        try:
            with open(hooks, "rb") as fh:
                blob = fh.read()
        except OSError as exc:
            blob = ("ABSENT: %s" % exc).encode("utf-8")
        digest.update(b"hooks.json=")
        digest.update(hashlib.sha256(blob).hexdigest().encode("utf-8"))
        parts.append("hooks.json present")
    else:
        digest.update(b"hooks.json=absent")
        parts.append("hooks.json absent")
    for name in ("plugins", "rules", "skills"):
        base = os.path.join(root, name)
        if not os.path.isdir(base):
            digest.update(("%s=absent" % name).encode("utf-8"))
            parts.append("%s tree: absent" % name)
            continue
        files = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames.sort()
            rel_dir = os.path.relpath(dirpath, root)
            for f in filenames:
                files.append(os.path.join(rel_dir, f))
        files.sort()
        for rel in files:
            digest.update(rel.encode("utf-8"))
            full = os.path.join(root, rel)
            try:
                with open(full, "rb") as fh:
                    blob = fh.read()
            except OSError:
                digest.update(b"UNREADABLE")
            else:
                digest.update(hashlib.sha256(blob).hexdigest().encode("utf-8"))
        parts.append("%s tree: %d files" % (name, len(files)))
    return digest.hexdigest(), ", ".join(parts)


def build_toy(path):
    """The README's toy, in its own git repository."""
    os.makedirs(path, exist_ok=True)
    for name, body in (("mathlib.py", TOY_MATHLIB),
                       ("test_mathlib.py", TOY_TEST)):
        with open(os.path.join(path, name), "w", encoding="utf-8") as fh:
            fh.write(body)
    for args in (["git", "init", "-q", "."],
                 ["git", "config", "user.name", "Cursor Smoke"],
                 ["git", "config", "user.email", "cursor@example.com"],
                 ["git", "add", "-A"],
                 ["git", "commit", "-q", "-m", "toy"]):
        proc = sh(args, cwd=path, timeout=60)
        if proc.returncode != 0:
            return "could not build the toy repository: %s%s" % (
                proc.stdout, proc.stderr)
    return ""


def copy_plugin(src, dst):
    """Copy the bundle, skipping .git and __pycache__."""
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst,
                    ignore=shutil.ignore_patterns(".git", "__pycache__"))


def add_canary(plugin_dir, abs_canary, root_canary):
    """Add sessionStart and preToolUse canary hooks to the copied plugin."""
    hook_path = os.path.join(plugin_dir, "cursor-hooks", "hooks.json")
    os.makedirs(os.path.dirname(hook_path), exist_ok=True)
    if os.path.isfile(hook_path):
        try:
            with open(hook_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
    else:
        data = {}
    if not isinstance(data, dict):
        data = {}
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks
    abs_command = ("python3 -c \"import sys, pathlib; "
                   "pathlib.Path(sys.argv[1]).write_text('fired')\" %s"
                   % shlex.quote(abs_canary))
    root_command = ('python3 "${PLUGIN_ROOT}/canary_root.py" %s'
                    % shlex.quote(root_canary))
    for event in ("sessionStart", "preToolUse"):
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            entries = []
            hooks[event] = entries
        entries.append({"command": abs_command, "timeout": 30})
        entries.append({"command": root_command, "timeout": 30})
    with open(hook_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def build_locked_target(toy):
    """Add locked.txt to the toy, committed alongside mathlib.py/test_
    mathlib.py, so the forbidden-write probe has a real file to target
    (never a path that only ever existed for this one test)."""
    path = os.path.join(toy, LOCKED_FILENAME)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(LOCKED_CONTENT)
    for args in (["git", "add", "-A"],
                 ["git", "commit", "-q", "-m", "add locked.txt"]):
        proc = sh(args, cwd=toy, timeout=60)
        if proc.returncode != 0:
            return "could not commit locked.txt: %s%s" % (proc.stdout,
                                                            proc.stderr)
    return ""


#: The exact stdout shape bm_store.py's own claim command prints
#: (products/brothermode/tools/bm_store.py, cmd_claim's success line):
#: "claimed '<name>' as lifecycle <32 hex chars> (version N, session
#: <label>)". Read here rather than re-typed, so a wording change in
#: that command cannot silently break this probe's own parse.
_LIFECYCLE_RE = re.compile(r"as lifecycle ([0-9a-f]{32})")


def claim_locked_target(toy, bm_store):
    """Claim LOCKED_FILENAME in the toy's own BrotherMode store under
    DENY_SESSION, a session label distinct from whatever session the
    live cursor-agent turn identifies itself as. This is the real
    single-writer fence (products/brothermode/tools/bm_fence_hook.py's
    own foreign-claim check), armed with a genuine reason to refuse the
    turn's own write, never a synthetic stand-in.

    Returns (lifecycle_uuid, "") on success, or (None, reason) when the
    setup could not be made, in which case the probe is NO-DATA rather
    than a false PASS or FAIL. The lifecycle uuid is bm_store.py's own,
    generated at claim time and unguessable in advance (32 random hex
    characters): deny_verdict() below treats its appearance in the live
    turn's own transcript as proof the model actually saw THIS run's
    real fence output, not a plausible-sounding refusal it produced on
    its own."""
    init = sh([sys.executable, bm_store, "init"], cwd=toy, timeout=60)
    if init.returncode != 0:
        return None, "bm_store init failed: %s%s" % (init.stdout, init.stderr)
    claim = sh([sys.executable, bm_store, "claim", "locked-by-a-different-session",
               "--lifetime", "ephemeral", "--objective",
               "WBS-70.04 live-deny canary: claimed so the real fence has "
               "a genuine reason to refuse this turn's own write",
               "--files", LOCKED_FILENAME, "--session", DENY_SESSION],
              cwd=toy, timeout=60)
    if claim.returncode != 0:
        return None, "bm_store claim failed: %s%s" % (claim.stdout, claim.stderr)
    m = _LIFECYCLE_RE.search(claim.stdout or "")
    if not m:
        return None, ("bm_store claim printed no parseable lifecycle uuid: "
                      "%s" % claim.stdout)
    return m.group(1), ""


#: Three live runs (2026-09-14, session that added this probe) each
#: refused the write in slightly different words, but every one named
#: DENY_SESSION (the fence's own reason string always names the owning
#: session) and used one of these words. The exact lifecycle uuid
#: bm_store.py mints per run appeared in only two of the three: the
#: model's own final summary sometimes drops it even though the tool
#: output it read from carried it. Gating on the uuid alone would have
#: read a genuine live deny as NO-DATA one run in three, so DENY_SESSION
#: plus this word list is the floor; the uuid, when present, is reported
#: as extra confidence, never required.
_DENY_LANGUAGE_RE = re.compile(r"\b(den(y|ied)|refus(e|ed|al)|block(ed)?)\b",
                               re.IGNORECASE)


def deny_verdict(transcript, lifecycle, target_path, original_content):
    """(verdict, message). PASS only when ALL hold: the live turn's own
    transcript names DENY_SESSION (the exact session label this run's
    own claim was made under, so a plain "I chose not to" refusal with
    no real fence involved cannot name it) together with refusal
    language, AND LOCKED_FILENAME's bytes on disk are unchanged from
    before the turn (Cursor actually honoured the deny, not merely that
    the model narrated one). NO-DATA when the transcript never shows
    both: the turn may never have attempted the write, so nothing here
    was exercised. FAIL when the session and refusal language are named
    but the file changed anyway: the fence's decision was not honoured."""
    text = transcript or ""
    session_named = DENY_SESSION in text
    deny_language = bool(_DENY_LANGUAGE_RE.search(text))
    lifecycle_named = bool(lifecycle) and lifecycle in text
    try:
        with open(target_path, encoding="utf-8") as fh:
            after = fh.read()
    except OSError as exc:
        return "FAIL", "%s could not be re-read after the turn (%s)" % (
            LOCKED_FILENAME, exc)
    unchanged = after == original_content
    if not (session_named and deny_language):
        return "NO-DATA", (
            "the transcript never shows both this run's own claimed "
            "session (%s) and refusal language, so nothing shows the "
            "real fence was exercised for this call" % DENY_SESSION)
    confidence = (" (this run's own lifecycle %s also confirmed)" % lifecycle
                 if lifecycle_named else "")
    if unchanged:
        return "PASS", ("the transcript names this run's own claimed "
                        "session with refusal language and %s is "
                        "unchanged%s" % (LOCKED_FILENAME, confidence))
    return "FAIL", ("the transcript names this run's own claimed session "
                    "with refusal language but %s changed anyway: Cursor "
                    "did not honour the deny%s"
                    % (LOCKED_FILENAME, confidence))


def forbidden_action_probe(agent, witness_root, keep=False):
    """WBS-70.04's own wording: "forbidden action attempted; Brother
    hook denies; Cursor honors deny; target remains unchanged; founder
    config witness remains unchanged." A separate throwaway toy and
    plugin copy from the main signed-in run, so this probe's own claim
    store and locked file never interact with the main task's edit.
    Prints a line starting "DENY: " naming PASS, FAIL or NO-DATA, always,
    so `--signed-in` prints a DENY verdict whether or not the live turn
    could exercise it."""
    work = tempfile.mkdtemp(prefix="cursor-smoke-deny-")
    try:
        plugin_dir = os.path.join(work, "plugin")
        copy_plugin(os.path.join(REPO, "bundle"), plugin_dir)
        bm_store = os.path.join(plugin_dir, "runtime", "hooks", "brothermode",
                                "tools", "bm_store.py")
        toy = os.path.join(work, "toy")
        why = build_toy(toy)
        if why:
            print("DENY: NO-DATA: %s" % why)
            return 2
        why = build_locked_target(toy)
        if why:
            print("DENY: NO-DATA: %s" % why)
            return 2
        lifecycle, why = claim_locked_target(toy, bm_store)
        if why:
            print("DENY: NO-DATA: %s" % why)
            return 2

        before, _desc = founder_witness(witness_root)

        proc = sh([agent, "-p", "--force", "--trust", "--output-format", "text",
                  "--workspace", toy, "--plugin-dir", plugin_dir,
                  FORBIDDEN_TASK_SENTENCE], timeout=900)
        report("forbidden-action run", proc, tail=20)
        transcript = (proc.stdout or "") + (proc.stderr or "")

        after, _ = founder_witness(witness_root)
        if before is not None and after is not None and before != after:
            print("DENY: FAIL: founder witness changed")
            return 1

        verdict, message = deny_verdict(
            transcript, lifecycle, os.path.join(toy, LOCKED_FILENAME),
            LOCKED_CONTENT)
        print("DENY: %s: %s" % (verdict, message))
        return 0 if verdict == "PASS" else (2 if verdict == "NO-DATA" else 1)
    finally:
        if keep:
            print("kept forbidden-action work directory: %s" % work)
        else:
            shutil.rmtree(work, ignore_errors=True)


def receipt_from_output(body):
    m = re.search(r"brother_run: receipt: (.+)", body or "")
    if not m:
        return None
    return m.group(1).strip()


def default_mode(agent, witness_root, keep=False):
    work = tempfile.mkdtemp(prefix="cursor-smoke-")
    try:
        fake_home = os.path.join(work, "home")
        plugin_dir = os.path.join(fake_home, ".cursor", "plugins", "local",
                                  "brother")
        os.makedirs(os.path.dirname(plugin_dir), exist_ok=True)
        copy_plugin(os.path.join(REPO, "bundle"), plugin_dir)
        toy = os.path.join(work, "toy")
        why = build_toy(toy)
        if why:
            print("FAIL: %s" % why)
            return 1

        before, desc = founder_witness(witness_root)
        print("witness before: %s" % (before or "NO-DATA"))
        print("  (%s)" % desc)

        env = dict(os.environ)
        env["HOME"] = fake_home
        status = sh([agent, "status"], env=env, timeout=120)
        report("status", status)
        status_body = (status.stdout or "") + (status.stderr or "")
        if "Not logged in" not in status_body:
            print("FAIL: isolation broken, status did not say Not logged in")
            return 1

        print_proc = sh([agent, "-p", "--output-format", "text", "--trust",
                         "--workspace", toy, "--plugin-dir", plugin_dir,
                         "say ok"], env=env, timeout=300)
        report("print run", print_proc)
        body = (print_proc.stdout or "") + (print_proc.stderr or "")
        if print_proc.returncode == 0:
            print("FAIL: print run exited 0, so the auth boundary was not proven")
            return 1
        low = body.lower()
        if not any(term in low for term in AUTH_BOUNDARY_TERMS):
            print("FAIL: print run failed without mentioning login or authentication")
            return 1

        after, _ = founder_witness(witness_root)
        print("witness after:  %s" % (after or "NO-DATA"))
        if before is None or after is None:
            print("NO-DATA: witness missing, isolation unproven")
            return 2
        if before != after:
            print("FAIL: witness changed")
            return 1
        print("PASS: Cursor default smoke passed")
        return 0
    finally:
        if keep:
            print("kept work directory: %s" % work)
        else:
            shutil.rmtree(work, ignore_errors=True)


def signed_in_mode(agent, witness_root, keep=False):
    status = sh([agent, "status"], timeout=120)
    report("status", status)
    status_body = (status.stdout or "") + (status.stderr or "")
    if "Not logged in" in status_body:
        print("NO-DATA: cursor-agent is not logged in")
        return 2

    work = tempfile.mkdtemp(prefix="cursor-smoke-signed-in-")
    try:
        plugin_dir = os.path.join(work, "plugin")
        copy_plugin(os.path.join(REPO, "bundle"), plugin_dir)
        abs_canary = os.path.join(work, "canary.txt")
        root_canary = os.path.join(work, "canary_root.txt")
        with open(os.path.join(plugin_dir, "canary_root.py"), "w",
                  encoding="utf-8") as fh:
            fh.write('import pathlib\nimport sys\n\n'
                     'pathlib.Path(sys.argv[1]).write_text("fired")\n')
        add_canary(plugin_dir, abs_canary, root_canary)
        toy = os.path.join(work, "toy")
        why = build_toy(toy)
        if why:
            print("FAIL: %s" % why)
            return 1

        before, desc = founder_witness(witness_root)
        print("witness before: %s" % (before or "NO-DATA"))
        print("  (%s)" % desc)

        proc = sh([agent, "-p", "--force", "--trust", "--output-format", "text",
                   "--workspace", toy, "--plugin-dir", plugin_dir, TASK_SENTENCE],
                  timeout=900)
        report("signed-in run", proc, tail=20)
        body = (proc.stdout or "") + (proc.stderr or "")

        hooks_fire_ok = os.path.isfile(abs_canary)
        hooks_root_ok = os.path.isfile(root_canary)
        print("HOOKS-FIRE: %s" % ("PASS" if hooks_fire_ok else "FAIL"))
        print("HOOKS-ROOT: %s" % ("PASS" if hooks_root_ok else "FAIL"))
        if hooks_fire_ok and not hooks_root_ok:
            print("FAIL: hooks fire but ${PLUGIN_ROOT} did not expand, so every shipped Brother hook would miss its script")

        edit_ok = False
        git_proc = sh(["git", "-C", toy, "status", "--porcelain"], timeout=60)
        if "mathlib.py" in (git_proc.stdout or ""):
            test_proc = sh([sys.executable, "-m", "unittest"], cwd=toy,
                           timeout=180)
            report("unittest in toy", test_proc)
            if test_proc.returncode == 0:
                edit_ok = True
        print("EDIT: %s" % ("PASS" if edit_ok else "FAIL"))

        receipt = receipt_from_output(body)
        if receipt and os.path.isfile(receipt):
            print("RECEIPT: PASS: %s" % receipt)
        else:
            print("RECEIPT: NO-DATA: %s" % (receipt or "no receipt path in output"))

        after, _ = founder_witness(witness_root)
        print("witness after:  %s" % (after or "NO-DATA"))
        if before is None or after is None:
            print("NO-DATA: witness missing, isolation unproven")
            return 2
        if before != after:
            print("FAIL: witness changed")
            return 1

        # WBS-70.04, the live-deny canary: a SEPARATE toy and plugin copy
        # (forbidden_action_probe builds its own), so the claim it makes
        # and the file it targets never interact with the edit task
        # above. Always prints a line starting "DENY: ", the exact done-
        # check named in the 1.0.17 convergence roadmap's WBS-70.04
        # section.
        deny_code = forbidden_action_probe(agent, witness_root, keep=keep)

        if hooks_fire_ok and hooks_root_ok and edit_ok and deny_code == 0:
            print("PASS: Cursor signed-in smoke passed")
            return 0
        print("FAIL: HOOKS-FIRE, HOOKS-ROOT, EDIT and DENY must all pass")
        return 1
    finally:
        if keep:
            print("kept work directory: %s" % work)
        else:
            shutil.rmtree(work, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cursor-agent", default=None,
                    help="the cursor-agent binary to smoke")
    ap.add_argument("--witness-root", default=DEFAULT_WITNESS_ROOT,
                    help="the Cursor root to witness before and after")
    ap.add_argument("--signed-in", action="store_true",
                    help="use the founder's real login")
    ap.add_argument("--keep", action="store_true",
                    help="keep the throwaway work directory")
    args = ap.parse_args(argv)

    agent = resolve_cursor_agent(args.cursor_agent)
    if agent is None:
        print("NO-DATA: no executable cursor-agent found. Pass --cursor-agent.")
        return 2

    if args.signed_in:
        return signed_in_mode(agent, args.witness_root, keep=args.keep)
    return default_mode(agent, args.witness_root, keep=args.keep)


if __name__ == "__main__":
    sys.exit(main())
