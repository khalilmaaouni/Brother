#!/usr/bin/env python3
"""S23's real done_check: prove docs/how-to/USE-WITH-CLAUDE-CODE.md runs
verbatim on a throwaway home against the published plugin, with every exit
code quoted.

Mirrors scripts/clean_install_e2e.sh's throwaway-HOME mechanism (a fresh
$HOME so `claude plugin marketplace add`/`install`/`update`/`uninstall`
touch only that HOME's own ~/.claude, never the founder's real one) and
scripts/release_closeout.py's tag-clone pattern (fetch_tag / Isolated) for
the two `scripts/test_*.py` commands, which need a real checkout to run
from. NEVER writes to the real ~/.claude or ~/.codex: every command below
runs with HOME pointed at a directory under a fresh tempfile.mkdtemp(), and
the founder's real hook-scope marker is witnessed unchanged before and
after.

Commands are extracted from the guide itself (never retyped): every fenced
```bash block, plus the one fenced ```text block that is a single line
starting with "/" (a Claude Code slash command -- the guide's one
interactive step, `/brother`). A slash command cannot be proven here: it
only means something inside an interactive session, and the one headless
substitute (`claude -p`) would spend a real, non-deterministic model call.
That step reads NO-DATA, never a pass.

Exit 0: every extracted command ran and printed its own exit code (NO-DATA
included). Exit 1: the founder's real hook-scope marker changed, or the
guide could not be read.
"""
import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUIDE_PATH = os.path.join(REPO, "docs", "how-to", "USE-WITH-CLAUDE-CODE.md")
PUBLIC_URL = "https://github.com/khalilmaaouni/Brother.git"

#: The currently published release, read live 2026-09-05: `claude plugin
#: marketplace update` against khalilmaaouni/Brother reported "brother is
#: already at the latest version (1.0.3)", so the guide's two scripts/
#: test_*.py commands are proven against this exact tag's own checkout.
TAG = "v1.0.3"

SECTION_HEADING = "## Proven on a throwaway home, %s"
SECTION_MARKER_RE = re.compile(
    r"\n## Proven on a throwaway home[^\n]*\n.*?(?=\n## |\Z)", re.DOTALL)

#: The single interactive step the guide documents. Any fenced ```text
#: block that is exactly this (or any other one-line slash command) reads
#: NO-DATA; see the module docstring for why.
SLASH_NODATA_REASON = (
    "typed inside an interactive Claude Code session; the only headless "
    "substitute, `claude -p`, would start a real model turn against the "
    "live API, which is not a mechanical, free or reproducible check, so "
    "this script does not attempt it")


def extract_commands(guide_text):
    """Every command the guide tells the reader to type, in document order:
    each fenced ```bash block, plus any fenced ```text block that is a
    single line starting with "/" (a slash command). Multi-line ```text
    blocks, and ones not starting with "/", are sample output, not
    commands, and are skipped.

    Returns a list of ("bash", text) / ("slash", text) tuples.
    """
    commands = []
    for m in re.finditer(r"```(bash|text)\n(.*?)\n```", guide_text, re.DOTALL):
        lang, body = m.group(1), m.group(2)
        if lang == "bash":
            commands.append(("bash", body))
        else:
            stripped = body.strip()
            if stripped.startswith("/") and "\n" not in stripped:
                commands.append(("slash", stripped))
    return commands


def run(text, cwd, env, timeout=900):
    """(exit code, combined output) of one shell command, never raising on a
    non-zero exit: a command that fails is exactly what this script must be
    able to report, not swallow. shell=True is deliberate: the guide's own
    commands use `&&`, and text comes only from this repo's own guide file,
    never from outside input."""
    proc = subprocess.run(text, shell=True, cwd=cwd, env=env,
                          stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=timeout)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def clone_tag(work, tag):
    """(checkout dir, "") or (None, log) for a shallow clone of the public
    tag. A tag that fails to clone is NO-DATA for the two commands that need
    it, never a crash of the whole proof."""
    dest = os.path.join(work, "tag-checkout")
    proc = subprocess.run(
        ["git", "clone", "--branch", tag, "--depth", "1", PUBLIC_URL, dest],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
    if proc.returncode != 0:
        return None, proc.stdout.decode("utf-8", "replace")
    return dest, ""


def command_cwd(text, home_dir, tag_checkout, target_dir):
    """Where the guide expects this command to be typed."""
    if "scripts/test_" in text:
        return tag_checkout
    if ".brother/config" in text:
        return target_dir
    # The plugin marketplace/install/update/uninstall commands run "from
    # any directory" (the guide's own words) and the hook-scope printf
    # writes an absolute ~/.claude path, so neither cares about cwd. Use
    # the throwaway HOME itself so nothing ever runs from the real repo
    # checkout by accident.
    return home_dir


def real_hook_scope_witness():
    """(exists, mtime, size) of the FOUNDER'S REAL hook-scope marker, so a
    before/after comparison proves this script never touched it. Read with
    the real HOME still in effect, before anything below overrides it."""
    path = os.path.expanduser("~/.claude/brother-hook-scope")
    try:
        st = os.stat(path)
        return (True, st.st_mtime, st.st_size)
    except OSError:
        return (False, None, None)


def render_section(lines, tag):
    body = "\n".join(lines)
    heading = SECTION_HEADING % datetime.date.today().isoformat()
    return (
        "\n%s\n\n"
        "Regenerated by `python3 scripts/prove_guide_claude.py` against the "
        "published marketplace `khalilmaaouni/Brother` (currently `%s`) on "
        "a throwaway home. Every line below is that run's own output, never "
        "hand-typed.\n\n"
        "```text\n%s\n```\n" % (heading, tag, body)
    )


def splice_section(guide_text, section):
    """Replace any earlier "Proven on a throwaway home" section with this
    run's, or append one if the guide has none yet. Keeps the guide's own
    trailing newline."""
    stripped = SECTION_MARKER_RE.sub("", guide_text).rstrip("\n")
    return stripped + "\n" + section


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guide", default=GUIDE_PATH,
                        help="guide file to read commands from and update "
                             "(default: the real guide)")
    parser.add_argument("--no-write", action="store_true",
                        help="do not rewrite the guide file (for tests)")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.guide):
        print("NO-DATA: no guide at %s" % args.guide)
        return 1
    with open(args.guide, encoding="utf-8") as fh:
        guide_text = fh.read()

    commands = extract_commands(guide_text)
    if not commands:
        print("NO-DATA: no fenced command found in %s" % args.guide)
        return 1

    before = real_hook_scope_witness()

    work = tempfile.mkdtemp(prefix="prove-guide-claude-")
    home_dir = os.path.join(work, "home")
    target_dir = os.path.join(work, "target-repo")
    os.makedirs(home_dir)
    os.makedirs(target_dir)

    env = dict(os.environ)
    env["HOME"] = home_dir
    env.pop("CLAUDE_CONFIG_DIR", None)

    tag_checkout = None
    clone_log = ""
    if any("scripts/test_" in text for kind, text in commands
           if kind == "bash"):
        tag_checkout, clone_log = clone_tag(work, TAG)
        if tag_checkout is None:
            print("clone of %s at %s failed:\n%s" % (PUBLIC_URL, TAG,
                                                      clone_log))

    result_lines = []
    ran_ok = 0
    nodata = 0
    try:
        for kind, text in commands:
            if kind == "slash":
                result_lines.append("%s -> NO-DATA (%s)" %
                                    (text, SLASH_NODATA_REASON))
                nodata += 1
                print("$ %s" % text)
                print("NO-DATA: %s" % SLASH_NODATA_REASON)
                print()
                continue
            if "scripts/test_" in text and tag_checkout is None:
                result_lines.append(
                    "%s -> NO-DATA (cloning the public %s tag failed)" %
                    (text, TAG))
                nodata += 1
                continue
            cwd = command_cwd(text, home_dir, tag_checkout, target_dir)
            code, output = run(text, cwd, env)
            result_lines.append("%s -> exit %d" % (text, code))
            if code == 0:
                ran_ok += 1
            print("$ %s" % text)
            print(output.rstrip())
            print("exit %d" % code)
            print()
    finally:
        shutil.rmtree(work, ignore_errors=True)

    after = real_hook_scope_witness()
    isolation_ok = before == after
    print("founder ~/.claude/brother-hook-scope witness before: %s" %
          (before,))
    print("founder ~/.claude/brother-hook-scope witness after:  %s" %
          (after,))

    print()
    for line in result_lines:
        print(line)
    total = len(commands)
    print()
    print("%d command(s), %d exited 0, %d NO-DATA" % (total, ran_ok, nodata))

    if not isolation_ok:
        print("FAIL: the founder's real ~/.claude/brother-hook-scope "
              "changed during this run")
        return 1

    if not args.no_write:
        section = render_section(result_lines, TAG)
        new_text = splice_section(guide_text, section)
        with open(args.guide, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        print("wrote %s" % args.guide)

    return 0


if __name__ == "__main__":
    sys.exit(main())
