#!/usr/bin/env python3
"""Generate Codex-visible aliases for Brother's product skills and commands.

Claude Code has slash commands and separate product directories. Codex has a
skill surface, so the umbrella package needs stable, namespaced aliases. The
aliases keep the source product names visible without copying Claude-only
frontmatter or exposing a second router.
"""
import argparse
import json
import re
from pathlib import Path

MARKER = "<!-- generated-codex-surface: v1 -->"
PRODUCTS = ("brothermode", "brothersbe")

#: Skills whose bundle alias is a verbatim mirror of the real product skill,
#: never the generic brother_run.py stub. WBS-70 U4: Cursor and Codex must
#: read the actual mailbox harness instructions here, not an engine stub
#: pointing at a Claude-only runtime script. Keyed by the canonical bundle
#: name (`<product>-<skill dir>`); value is (product, skill_dir_name).
REAL_CONTENT_SKILLS = {
    "brothermode-cursor-execute": ("brothermode", "cursor-execute"),
    "brothermode-cursor-dispatch": ("brothermode", "cursor-dispatch"),
}


def split_frontmatter(text):
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    fields = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip().strip('"')
    return fields, text[end + 4:].lstrip("\n")


def command_target(text):
    match = re.search(r"Follow skills/([a-z0-9-]+)/SKILL\.md", text)
    return match.group(1) if match else None


def render(name, description, product, canonical, kind, command_name=None):
    description = re.sub(r"\s+", " ", description).strip()
    if len(description) > 1024:
        description = description[:1021].rstrip() + "..."
    command_line = ""
    if command_name:
        command_line = (
            "This is the Codex route for the existing Claude command "
            "`/%s:%s`.\n\n" % (product, command_name)
        )
    else:
        command_line = "This is the Codex route for the `%s` product skill.\n\n" % product
    return (
        "---\nname: %s\ndescription: %s\n---\n\n%s"
        "Codex has no slash command surface. Invoke this skill by name in a "
        "bounded outcome. The shared `using-brother` route remains the entry "
        "point when the outcome is ambiguous.\n\n"
        "The Brother engine owns execution, assurance and the receipt:\n\n"
        "```bash\npython3 \"${BROTHER_PLUGIN_ROOT}/runtime/brother_run.py\" "
        "\"<outcome>\" --cwd <repo>\n```\n\n"
        "The canonical sibling route is `skills/%s/SKILL.md`. Keep PASS, FAIL "
        "and NO-DATA distinct, and read the emitted receipt before claiming "
        "completion.\n%s\n" % (name, json.dumps(description), command_line, canonical, MARKER)
    )


def mirror_real(root, canonical):
    """The verbatim source SKILL.md for a REAL_CONTENT_SKILLS entry, with only
    the frontmatter `name` rewritten to the canonical bundle name. No MARKER:
    this is real harness content, not a generated stub, so codex_skills.py
    ships it through unchanged (beyond its own key stripping)."""
    product, skill_dir = REAL_CONTENT_SKILLS[canonical]
    source_path = root / "products" / product / "skills" / skill_dir / "SKILL.md"
    text = source_path.read_text(encoding="utf-8")
    fields, body = split_frontmatter(text)
    fields["name"] = canonical
    frontmatter = "\n".join("%s: %s" % (k, v) for k, v in fields.items())
    return "---\n%s\n---\n\n%s" % (frontmatter, body)


def expected(root):
    files = {}
    for product in PRODUCTS:
        source = root / "products" / product
        skills = source / "skills"
        if not skills.is_dir():
            continue
        for skill_path in sorted(skills.glob("*/SKILL.md")):
            fields, _ = split_frontmatter(skill_path.read_text(encoding="utf-8"))
            canonical = "%s-%s" % (product, skill_path.parent.name)
            if canonical in REAL_CONTENT_SKILLS:
                files["%s/SKILL.md" % canonical] = mirror_real(root, canonical)
                continue
            files["%s/SKILL.md" % canonical] = render(
                canonical, fields.get("description", canonical), product,
                canonical, "skill")
        commands = source / "commands"
        for command_path in sorted(commands.glob("*.md")):
            fields, body = split_frontmatter(command_path.read_text(encoding="utf-8"))
            target = command_target(body) or command_path.stem
            canonical = "%s-%s" % (product, target)
            name = command_path.stem
            files["%s/SKILL.md" % name] = render(
                name, fields.get("description", name), product, canonical,
                "command", name)
    return files


def generated_paths(root):
    return root / "bundle" / "skills"


def generate(root):
    dest = generated_paths(root)
    dest.mkdir(parents=True, exist_ok=True)
    expected_files = expected(root)
    changed = []
    for rel, content in expected_files.items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        if not path.is_file() or path.read_bytes() != data:
            path.write_bytes(data)
            changed.append(rel)
    for path in sorted(dest.glob("*/SKILL.md")):
        if MARKER in path.read_text(encoding="utf-8") and str(path.relative_to(dest)) not in expected_files:
            path.unlink()
            changed.append("removed %s" % path.relative_to(dest))
    return changed


def check(root):
    dest = generated_paths(root)
    expected_files = expected(root)
    problems = []
    for rel, content in expected_files.items():
        path = dest / rel
        if not path.is_file():
            problems.append("bundle/skills/%s: missing" % rel)
        elif path.read_text(encoding="utf-8") != content:
            problems.append("bundle/skills/%s: stale" % rel)
    for path in sorted(dest.glob("*/SKILL.md")):
        rel = str(path.relative_to(dest))
        if MARKER in path.read_text(encoding="utf-8") and rel not in expected_files:
            problems.append("bundle/skills/%s: stale generated alias" % rel)
    return problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.check:
        problems = check(root)
        if problems:
            for problem in problems:
                print("codex_surface: DRIFT: %s" % problem)
            return 1
        count = len(expected(root))
        print("codex_surface: bundle/skills carries %d product and command aliases" % count)
        return 0
    changed = generate(root)
    print("codex_surface: wrote %d alias file(s)" % len(changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
