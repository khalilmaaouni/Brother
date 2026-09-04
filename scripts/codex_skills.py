#!/usr/bin/env python3
"""codex_skills: generate bundle/codex-skills/ from bundle/skills/, with the
frontmatter keys Codex does not accept stripped out.

THE PROBLEM. Claude Code and Codex read the same SKILL.md frontmatter and
disagree about it. Claude uses `disable-model-invocation: true` to keep a
side-effecting skill (deliver, update, stop, handback, handover-pack, auto,
start) off model auto-invocation; seven skills under products/brothermode
carry it for exactly that reason, recorded in each one's own v3 note. The
canonical Codex validator REFUSES it:

    if disable_model_invocation not in (None, False):
        errors.append(... `disable-model-invocation` must be false)

(validate_plugin.py, validate_skill_manifest). So the same bytes cannot serve
both clients, and neither client's behaviour may be sacrificed for the other:
dropping the key would let a Codex-shaped Claude session auto-invoke a
delivery, and keeping it refuses the whole Codex package at gate 2.

THE ANSWER: two directories, one source. bundle/skills/ stays exactly what
Claude reads, hand maintained, untouched by this script. bundle/codex-skills/
is GENERATED from it, never hand edited, with every frontmatter key the Codex
validator does not read removed and the removal written down.

WHICH KEYS SURVIVE, read from validate_plugin.py rather than assumed. Its
validate_skill_manifest reads exactly three frontmatter fields: `name` (must
be a non-empty string), `description` (must be a non-empty string), and
`disable-model-invocation` (must be absent or false). There is no allowed-key
set for skill frontmatter the way there is for plugin.json, so "accepted" here
means "a field the validator actually reads and a Codex install acts on",
which is `name` and `description`. Everything else is stripped, including
`disable-model-invocation` itself: absent is the only value Codex accepts, and
absent is what stripping produces.

STRIPPED.json is the record. Every key this script removed, per skill, with
the source line it removed, so a reader can see what Codex is not being told
without diffing two trees. It is generated, sorted, and carries no timestamp,
so an unchanged source regenerates byte-identical output and --check is a real
drift check rather than a clock.

NO YAML. The bare interpreter that runs `scripts/bundle_runtime.py --check`
has no pyyaml (that is why the validator itself is invoked through `uv run
--with pyyaml`). Frontmatter is therefore split on lines, not parsed: a
top-level key is a line starting at column zero matching `<key>:`, and every
line after it that is not itself a top-level key belongs to it. That covers
the block scalars and folded values a SKILL.md description uses, and it never
reformats a key it keeps, since kept keys are copied verbatim.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
SOURCE_DIR = os.path.join(REPO_ROOT, "bundle", "skills")
DEST_DIR = os.path.join(REPO_ROOT, "bundle", "codex-skills")

#: The two frontmatter fields validate_plugin.py's validate_skill_manifest
#: actually reads and requires. Everything else is stripped.
ACCEPTED_KEYS = ("name", "description")

RECORD_NAME = "STRIPPED.json"

_KEY_LINE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.-]*):(\s|$)")


def split_frontmatter(text):
    """(frontmatter_lines, body) or (None, None) when `text` does not open
    with a closed YAML frontmatter block. Never raises: a SKILL.md without
    frontmatter is a source defect this script reports, not one it fixes."""
    if not text.startswith("---\n"):
        return None, None
    rest = text[4:]
    end = rest.find("\n---")
    if end == -1:
        return None, None
    # The closing marker line, plus its newline when there is one.
    after = rest[end + 1:]
    newline = after.find("\n")
    body = "" if newline == -1 else after[newline + 1:]
    return rest[:end].split("\n"), body


def strip_frontmatter(lines, accepted=ACCEPTED_KEYS):
    """(kept_lines, stripped) where `stripped` maps each removed top-level key
    to the verbatim source lines it owned. Order of kept keys is the source
    order: this rewrites nothing it keeps."""
    kept, stripped, current = [], {}, None
    for line in lines:
        match = _KEY_LINE.match(line)
        if match:
            current = match.group(1)
        if current is None or current in accepted:
            kept.append(line)
        else:
            stripped.setdefault(current, []).append(line)
    return kept, {k: "\n".join(v) for k, v in stripped.items()}


def render(kept_lines, body):
    return "---\n" + "\n".join(kept_lines) + "\n---\n" + body


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def _skill_dirs(source_dir):
    try:
        names = os.listdir(source_dir)
    except OSError:  # sbe: allow-silent None is the reader's NO-DATA here and every caller checks for it, rather than treating an unreadable directory as an empty one
        return None
    return sorted(n for n in names
                  if not n.startswith(".")
                  and os.path.isdir(os.path.join(source_dir, n)))


def _files_under(root):
    """Every file under `root`, as sorted posix relative paths."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            full = os.path.join(dirpath, name)
            out.append(os.path.relpath(full, root).replace(os.sep, "/"))
    return sorted(out)


def build(source_dir=None, accepted=ACCEPTED_KEYS):
    """(files, problems): the whole intended content of bundle/codex-skills as
    {relative posix path: bytes}, computed from the source and nothing else.
    `problems` names every source SKILL.md this script could not read or whose
    frontmatter it could not find; a problem never silently yields a file."""
    src = SOURCE_DIR if source_dir is None else source_dir
    files, problems, record = {}, [], {}
    names = _skill_dirs(src)
    if names is None:
        return {}, ["%s: cannot be listed, so no Codex skills can be "
                    "generated" % src]
    for name in names:
        skill_dir = os.path.join(src, name)
        for rel in _files_under(skill_dir):
            source_path = os.path.join(skill_dir, *rel.split("/"))
            try:
                data = _read(source_path)
            except OSError as exc:
                problems.append("%s/%s: unreadable (%s)" % (name, rel, exc))
                continue
            if rel != "SKILL.md":
                files["%s/%s" % (name, rel)] = data
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                problems.append("%s/SKILL.md: not UTF-8 (%s)" % (name, exc))
                continue
            lines, body = split_frontmatter(text)
            if lines is None:
                problems.append("%s/SKILL.md: no closed YAML frontmatter, so "
                                "Codex would refuse it and this script will "
                                "not invent one" % name)
                continue
            kept, stripped = strip_frontmatter(lines, accepted)
            if not any(_KEY_LINE.match(l) for l in kept):
                problems.append("%s/SKILL.md: stripping left no frontmatter "
                                "key at all, so it carries neither name nor "
                                "description" % name)
                continue
            files["%s/SKILL.md" % name] = render(kept, body).encode("utf-8")
            if stripped:
                record[name] = stripped
    files[RECORD_NAME] = (json.dumps(
        {"generated_by": "scripts/codex_skills.py",
         "source": "bundle/skills",
         "accepted_frontmatter_keys": list(accepted),
         "skills": record},
        indent=2, sort_keys=True) + "\n").encode("utf-8")
    return files, problems


def generate(source_dir=None, dest_dir=None):
    """(changed, problems). Writes only what differs, and removes anything
    under dest that build() did not produce, so a skill deleted from
    bundle/skills does not linger in the Codex copy."""
    dest = DEST_DIR if dest_dir is None else dest_dir
    files, problems = build(source_dir)
    if problems:
        return [], problems
    changed = []
    for rel, data in sorted(files.items()):
        path = os.path.join(dest, *rel.split("/"))
        if os.path.isfile(path) and _read(path) == data:
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        changed.append(rel)
    if os.path.isdir(dest):
        for rel in _files_under(dest):
            if rel not in files:
                os.remove(os.path.join(dest, *rel.split("/")))
                changed.append("removed %s" % rel)
        for dirpath, dirnames, filenames in os.walk(dest, topdown=False):
            if dirpath != dest and not dirnames and not filenames:
                os.rmdir(dirpath)
    return changed, []


def check(source_dir=None, dest_dir=None):
    """(ok, problems): does bundle/codex-skills match a fresh generation right
    now? Read-only, writes nothing."""
    dest = DEST_DIR if dest_dir is None else dest_dir
    files, problems = build(source_dir)
    if problems:
        return False, problems
    for rel, data in sorted(files.items()):
        path = os.path.join(dest, *rel.split("/"))
        if not os.path.isfile(path):
            problems.append("bundle/codex-skills/%s: missing" % rel)
        elif _read(path) != data:
            problems.append("bundle/codex-skills/%s: does not match a fresh "
                            "generation from bundle/skills" % rel)
    if os.path.isdir(dest):
        for rel in _files_under(dest):
            if rel not in files:
                problems.append("bundle/codex-skills/%s: not produced by "
                                "bundle/skills, so it is stale" % rel)
    else:
        problems.append("bundle/codex-skills: missing entirely")
    return (not problems), problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="report drift without writing anything; exit 1 if "
                         "bundle/codex-skills does not match bundle/skills")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.check:
        ok, problems = check()
        if ok:
            print("codex_skills: bundle/codex-skills matches bundle/skills")
            return 0
        for problem in problems:
            print("codex_skills: DRIFT: %s" % problem, file=sys.stderr)
        return 1
    changed, problems = generate()
    if problems:
        for problem in problems:
            print("codex_skills: FAIL: %s" % problem, file=sys.stderr)
        return 1
    if changed:
        print("codex_skills: wrote %d file(s): %s"
              % (len(changed), ", ".join(changed)))
    else:
        print("codex_skills: no changes; bundle/codex-skills already matches "
              "bundle/skills")
    return 0


if __name__ == "__main__":
    sys.exit(main())
