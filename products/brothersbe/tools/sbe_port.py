#!/usr/bin/env python3
"""Generate the guided surface for a foreign runtime FROM skills/, never beside it.

Run:
    python3 tools/sbe_port.py codex --out <dir>     # write the Codex tree
    python3 tools/sbe_port.py codex --check <dir>   # verify it matches skills/

WHY GENERATION AND NOT A COPY. Two hosts already read the same skill format
this project ships: Codex and Cursor both use `skills/<name>/SKILL.md` with
`name:` and `description:` frontmatter between `---` markers, verified by
reading one of each rather than assuming. A hand-maintained second copy of
twelve skills drifts within a week, and the drift is invisible because nobody
reads both. So the port is a function of the source, and `--check` is the
doc-truth test that fails when the two disagree.

WHAT THIS DELIBERATELY DOES NOT CLAIM. Generating a skill tree makes the
guidance available in another host. It does not make anything ENFORCED there:
the refusals in this project live in Claude Code hooks, and no other host on
this machine has a pre-action hook that can stop a write. The generated
AGENTS.md says so in its own first paragraph, because a ported instruction
file that reads as if the gates travel with it would be the overclaim every
check here exists to refuse. docs/RUNTIMES.md carries the same statement as a
table.

WHAT IT READS. Only this repository. It never reads a runtime's config
directory, credential store or installed state: the format was established by
reading one public bundled example, and the generator needs nothing else. A
tool that reaches into a dot-folder to build a package is a tool nobody should
run, whatever it is for.
"""

import argparse
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SKILLS = os.path.join(ROOT, "skills")

#: The one sentence every generated surface opens with. It is the honest
#: difference between this host and Claude Code, and it is not optional.
NOT_ENFORCED = (
    "BrotherSBE's refusals live in Claude Code hooks and DO NOT travel to this "
    "runtime. Here the guidance is advisory: it is read and usually followed, "
    "and nothing refuses a write. What does travel is the command line. `sbe` "
    "returns real exit codes, so any check you actually run can still stop you."
)

RUNTIMES = {
    # name: (directory laid out under --out, human label)
    "codex": ("skills", "Codex"),
    "cursor": ("skills", "Cursor"),
}


def read_frontmatter(path):
    """(name, description, problem). Frontmatter only; the body is not parsed."""
    try:
        with io.open(path, encoding="utf-8") as handle:
            text = handle.read()
    except (IOError, OSError) as exc:
        return None, None, "unreadable: %s" % exc
    if not text.startswith("---"):
        return None, None, "no frontmatter"
    end = text.find("\n---", 3)
    if end == -1:
        return None, None, "frontmatter never closes"
    block = text[3:end]
    name = re.search(r"^name:\s*(.+)$", block, re.M)
    desc = re.search(r"^description:\s*(.+)$", block, re.M)
    if not name or not desc:
        return None, None, "frontmatter lacks name or description"
    return name.group(1).strip(), desc.group(1).strip('\'" \t'), None


def source_skills():
    """[(name, description, source_path)], sorted, or raises with the reason."""
    if not os.path.isdir(SKILLS):
        raise SystemExit("no skills/ directory at %s" % SKILLS)
    found, problems = [], []
    for entry in sorted(os.listdir(SKILLS)):
        path = os.path.join(SKILLS, entry, "SKILL.md")
        if not os.path.isfile(path):
            continue
        name, desc, problem = read_frontmatter(path)
        if problem:
            problems.append("%s: %s" % (entry, problem))
            continue
        found.append((name, desc, path))
    if problems:
        raise SystemExit("refusing to generate from unreadable sources: %s"
                         % "; ".join(problems))
    if not found:
        raise SystemExit("skills/ holds no readable SKILL.md, so there is nothing to port")
    return found


def render(name, desc, label, body):
    return ("---\nname: %s\ndescription: %s\n---\n\n"
            "# %s\n\n> Ported to %s from this project's own `skills/%s/SKILL.md` by\n"
            "> `tools/sbe_port.py`. Edit the source, never this copy: "
            "`sbe_port.py %s --check` fails when they disagree.\n\n"
            "**%s**\n\n%s"
            % (name, desc, name, label, name, label.lower(), NOT_ENFORCED, body))


def body_of(path):
    with io.open(path, encoding="utf-8") as handle:
        text = handle.read()
    end = text.find("\n---", 3)
    return text[end + 4:].lstrip("\n") if end != -1 else text


def generate(runtime, out):
    subdir, label = RUNTIMES[runtime]
    written = []
    for name, desc, path in source_skills():
        target_dir = os.path.join(out, subdir, name)
        if not os.path.isdir(target_dir):
            os.makedirs(target_dir)
        target = os.path.join(target_dir, "SKILL.md")
        with io.open(target, "w", encoding="utf-8") as handle:
            handle.write(render(name, desc, label, body_of(path)))
        written.append(target)
    return written


def check(runtime, out):
    """[] when the tree matches the source, else the disagreements by name."""
    subdir, label = RUNTIMES[runtime]
    problems = []
    for name, desc, path in source_skills():
        target = os.path.join(out, subdir, name, "SKILL.md")
        if not os.path.isfile(target):
            problems.append("%s: not generated at %s" % (name, target))
            continue
        with io.open(target, encoding="utf-8") as handle:
            actual = handle.read()
        if actual != render(name, desc, label, body_of(path)):
            problems.append("%s: generated copy differs from skills/%s/SKILL.md"
                            % (name, name))
    return problems


def main(argv):
    parser = argparse.ArgumentParser(prog="sbe_port")
    parser.add_argument("runtime", choices=sorted(RUNTIMES))
    parser.add_argument("--out", required=True, help="directory to write or check")
    parser.add_argument("--check", action="store_true",
                        help="verify rather than write; exits nonzero on any drift")
    args = parser.parse_args(argv)

    if args.check:
        problems = check(args.runtime, args.out)
        if problems:
            sys.stderr.write("sbe_port: %d generated file(s) disagree with skills/: %s\n"
                             % (len(problems), "; ".join(problems)))
            return 1
        sys.stdout.write("sbe_port: the %s tree at %s matches skills/ exactly\n"
                         % (args.runtime, args.out))
        return 0

    written = generate(args.runtime, args.out)
    sys.stdout.write("sbe_port: wrote %d skill(s) for %s into %s. Guidance only: %s\n"
                     % (len(written), args.runtime, args.out, NOT_ENFORCED))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
