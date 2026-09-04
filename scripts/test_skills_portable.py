#!/usr/bin/env python3
"""C4: every shipped skill is self contained and readable by a second client.

WHY THIS EXISTS. A skill file is the only thing an installed plugin hands a
client. Claude Code has been the only reader for this repository's whole
life, so the skills grew three habits that are invisible under Claude and
fatal anywhere else: they name `${CLAUDE_PLUGIN_ROOT}` with no vendor neutral
alternative, they point at `~/.claude/skills/brothermode` as if that were the
install location on every machine, and they instruct a reader to type a slash
command that only one client answers. None of those is a formatting nit. Each
one is an instruction that a Codex session reads, cannot act on, and has no
way to repair from inside the file.

WHAT IT ENFORCES, over every SKILL.md this repository ships (the bundle and
both products), seven rules:

1. frontmatter-yaml. The file opens with a closed YAML frontmatter block that
   parses to a mapping. The Codex plugin validator refuses a plugin outright
   when any skill fails this, and an unquoted description carrying a colon is
   exactly how it happens.
2. frontmatter-name. `name` is a non-empty string, hyphen case, no leading,
   trailing or doubled hyphen, at most 64 characters.
3. frontmatter-description. `description` is a non-empty string, at most 1024
   characters, carries no angle brackets, and is not a leftover TODO.
4. no-claude-home. No `~/.claude` path. That directory exists on one client.
5. plugin-root-alternative. A file naming `CLAUDE_PLUGIN_ROOT` also names
   `BROTHER_PLUGIN_ROOT`, the vendor neutral variable, so the reader learns
   the substitution from the file it is already reading.
6. shipped-paths. Every relative path the file names resolves inside the tree
   that ships with it (its plugin root, or the skill's own directory). A path
   that only resolves in a checkout of this repository is unreachable to
   anyone who installed the plugin.
7. plain-command. A file that tells the reader to run a slash command also
   carries at least one plain command a reader can run without that client.

Rules 2 and 3 are the canonical Codex authoring rules, read from the
installed validator at
~/.codex/skills/.system/skill-creator/scripts/quick_validate.py (allowed keys
name, description, license, allowed-tools, metadata; name hyphen case and at
most 64; description at most 1024 and no angle brackets). Rule 1 is the
canonical Codex PACKAGE rule, read from
~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py, which
refuses a plugin whose skill frontmatter is absent, unclosed, unparseable or
not a mapping. Unknown frontmatter keys are NOT failed here: the package
validator tolerates them, and the Claude only keys this repository ships
(`argument-hint`, `user-invocable`, `disable-model-invocation`) carry Claude
behaviour that deleting would regress. The one Codex package gap that remains
is named in AGENTS.md rather than hidden by this checker.

Exit 0 PASS, every shipped skill obeys all seven rules.
Exit 1 FAIL, at least one does not; every violation is named with its file.
Exit 2 NO-DATA, no shipped skill was found at all, which is never a pass.
No em or en dashes.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Interpreters to hand this file to when the one running it cannot import
#: PyYAML. The frontmatter rules are read from the Codex validators, which
#: parse real YAML, so a hand rolled key scanner would disagree with them on
#: exactly the case that matters (an unquoted description carrying a colon).
#: /usr/bin/python3 is the interpreter AGENTS.md already names for this
#: repository's unittest suite, and it ships PyYAML on macOS.
FALLBACK_INTERPRETERS = ("/usr/bin/python3",)

#: Where a shipped skill can live. Each glob's match has a plugin root two
#: directories above it (bundle/, or products/<name>/), which is the tree an
#: install actually copies.
SKILL_GLOBS = ("bundle/skills/*/SKILL.md", "products/*/skills/*/SKILL.md")

MAX_NAME = 64
MAX_DESCRIPTION = 1024
NAME_RE = re.compile(r"^[a-z0-9-]+$")

#: A backticked or fenced token is treated as a path when it carries a file
#: extension this repository actually ships. Anything else in backticks is
#: prose, a command word, or a variable, and is not this rule's business.
PATH_RE = re.compile(r"[A-Za-z0-9_./${}-]+\.(?:md|py|sh|json|html|txt|ya?ml)\b")

#: Directories a running install creates in the USER'S project, never in the
#: shipped tree. Exempt by shape, never by file name: naming individual files
#: here is how a scanner acquires a hole. `Documentation/` is the folder
#: `bm_docs.py generate` writes into the project it is run in, so a skill
#: naming a file under it is naming that tool's output, not a shipped file.
RUNTIME_PREFIXES = (".sbe/", ".brothermode/", ".brother/", ".git/",
                    "Documentation/")

#: A fenced block holds an example, a sample record or a command a reader
#: adapts, so the paths inside it are illustrations (`src/api.py` in a sample
#: finding) rather than pointers this repository promises to ship. Rule 6
#: reads prose only, which is where a pointer the reader is told to open
#: actually lives.
FENCE_RE = re.compile(r"^(```|~~~).*?^\1", re.MULTILINE | re.DOTALL)

SLASH_RE = re.compile(r"(?<![\w/])/(?:brother|brothermode|brothersbe|sbe)(?::[a-z0-9-]+)?\b")
PLAIN_RE = re.compile(
    r"(?:python3\s|/bin/sbe|\bsh\s+scripts/|\bbrothermode\s+[a-z]+\b|\bsbe\s+[a-z]+\b)"
)

#: The second client neutral route, for a skill that runs no command of its
#: own: it names the shipped file a reader can open and follow instead. A
#: named readable file is reachable from any client, which is the whole point
#: of rule 7; only a route that exists in one client alone is the defect.
ROUTE_RE = re.compile(r"skills/[a-z0-9-]+/SKILL\.md")


def shipped_skills():
    """Every SKILL.md this repository ships, sorted, as (path, plugin_root)."""
    found = []
    for pattern in SKILL_GLOBS:
        for path in ROOT.glob(pattern):
            found.append((path, path.parents[2]))
    return sorted(found)


def split_frontmatter(text):
    """Return (frontmatter_text, body) or (None, text) when there is none.

    Mirrors the Codex package validator: the block opens the file and closes
    on a line that is exactly three hyphens.
    """
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---", 4)
    if end == -1:
        return None, text
    return text[4:end], text[end + 4:]


def load_yaml(frontmatter_text):
    """Parse the frontmatter, or return the parse error as a string.

    PyYAML is not guaranteed present on a bare interpreter here, so a missing
    module is reported by name rather than crashing the run: the caller turns
    that into NO-DATA, never into a pass.
    """
    try:
        import yaml
    except ImportError:  # sbe: allow-silent the module name IS the report: the caller turns ("no-yaml-module") into NO-DATA, never into a pass
        return None, "no-yaml-module"
    try:
        data = yaml.safe_load(frontmatter_text)
    except Exception as exc:                      # yaml raises several types
        return None, f"invalid YAML in frontmatter ({exc.__class__.__name__})"
    if not isinstance(data, dict):
        return None, "frontmatter is not a mapping"
    return data, None


def check_frontmatter(front, problems):
    name = front.get("name")
    if not isinstance(name, str) or not name.strip():
        problems.append("frontmatter-name: missing or empty")
    else:
        name = name.strip()
        if not NAME_RE.match(name):
            problems.append(f"frontmatter-name: {name!r} is not hyphen case")
        elif name.startswith("-") or name.endswith("-") or "--" in name:
            problems.append(f"frontmatter-name: {name!r} has a stray hyphen")
        elif len(name) > MAX_NAME:
            problems.append(
                f"frontmatter-name: {len(name)} characters, at most {MAX_NAME}")

    description = front.get("description")
    if not isinstance(description, str) or not description.strip():
        problems.append("frontmatter-description: missing or empty")
        return
    description = description.strip()
    if description.startswith("[TODO:"):
        problems.append("frontmatter-description: still a TODO placeholder")
    if "<" in description or ">" in description:
        problems.append("frontmatter-description: carries an angle bracket")
    if len(description) > MAX_DESCRIPTION:
        problems.append(
            f"frontmatter-description: {len(description)} characters, at most "
            f"{MAX_DESCRIPTION}")


def named_paths(text):
    """Every relative path shape the text names, placeholders dropped.

    A token carrying `*` or an angle bracket is a glob or a stand in for a
    value the reader supplies, so it names no particular file and cannot be
    resolved. An absolute or home path is a machine location, which rule 4
    already governs.
    """
    prose = FENCE_RE.sub("", text)
    out = []
    for match in PATH_RE.finditer(prose):
        token = match.group(0)
        if "*" in token or "<" in token or ">" in token:
            continue
        # `<path/to/mistakes.json>` is a placeholder whose angle brackets sit
        # OUTSIDE the path shape, so the test above never sees them.
        before = prose[match.start() - 1: match.start()]
        after = prose[match.end(): match.end() + 1]
        if before == "<" or after == ">":
            continue
        if token.startswith(("/", "~")):
            continue
        # Both spellings of both variables, so a path written without braces
        # is RESOLVED like the braced one rather than skipped as a variable
        # by the test below. Skipping it would let the braceless form carry a
        # path that does not ship.
        for var in ("CLAUDE_PLUGIN_ROOT", "BROTHER_PLUGIN_ROOT"):
            token = token.replace("${%s}/" % var, "").replace("$%s/" % var, "")
        if token.startswith("$") or "/" not in token:
            continue
        if token.startswith(RUNTIME_PREFIXES):
            continue
        out.append(token)
    return out


def check_paths(path, plugin_root, text, problems):
    for token in sorted(set(named_paths(text))):
        if (plugin_root / token).exists() or (path.parent / token).exists():
            continue
        where = "only in a checkout of this repository" if (ROOT / token).exists() \
            else "nowhere in this repository"
        problems.append(
            f"shipped-paths: {token} does not exist under "
            f"{plugin_root.relative_to(ROOT)} ({where})")


def check_one(path, plugin_root):
    """Every rule this file breaks, as a list of strings. Empty means clean."""
    problems = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"unreadable: {exc.strerror}"]

    frontmatter_text, body = split_frontmatter(text)
    if frontmatter_text is None:
        problems.append("frontmatter-yaml: absent or never closed")
    else:
        front, error = load_yaml(frontmatter_text)
        if error == "no-yaml-module":
            raise RuntimeError("no-yaml-module")
        if front is None:
            problems.append(f"frontmatter-yaml: {error}")
        else:
            check_frontmatter(front, problems)

    if "~/.claude" in text:
        problems.append("no-claude-home: names ~/.claude, a path on one client only")

    if "CLAUDE_PLUGIN_ROOT" in text and "BROTHER_PLUGIN_ROOT" not in text:
        problems.append(
            "plugin-root-alternative: names CLAUDE_PLUGIN_ROOT with no "
            "BROTHER_PLUGIN_ROOT alternative")

    check_paths(path, plugin_root, text, problems)

    slash = sorted(set(SLASH_RE.findall(body)))
    if slash and not (PLAIN_RE.search(body) or ROUTE_RE.search(body)):
        problems.append(
            "plain-command: names " + ", ".join(slash[:4])
            + " and carries no plain command or readable skill file a reader "
              "can use instead")

    return problems


def reexec_with_yaml():
    """Run this file under an interpreter that has PyYAML, or return None.

    Returns that run's own exit code, captured from the child and never from
    a pipe. The child is told not to re-exec again, so a machine with no
    PyYAML anywhere ends in one honest NO-DATA rather than a loop.
    """
    for interpreter in FALLBACK_INTERPRETERS:
        if not os.path.exists(interpreter):
            continue
        try:
            probe = subprocess.run([interpreter, "-c", "import yaml"],
                                   capture_output=True)
        except OSError:  # sbe: allow-silent an interpreter that will not launch is not a fallback; the loop tries the next and the caller reports NO-DATA when none answers
            continue
        if probe.returncode != 0:
            continue
        try:
            run = subprocess.run([interpreter, str(Path(__file__).resolve()),
                                  "--no-reexec"])
        except OSError as exc:
            print(f"NO-DATA: could not run {interpreter}: {exc.strerror}")
            return 2
        return run.returncode
    return None


def main():
    if "--no-reexec" not in sys.argv:
        try:
            import yaml                                    # noqa: F401
        except ImportError:
            code = reexec_with_yaml()
            if code is not None:
                return code

    skills = shipped_skills()
    if not skills:
        print("NO-DATA: no shipped SKILL.md found under "
              + ", ".join(SKILL_GLOBS)
              + "; this checker read nothing, which is not a pass")
        return 2

    try:
        findings = [(p, check_one(p, root)) for p, root in skills]
    except RuntimeError as exc:
        if str(exc) != "no-yaml-module":
            raise
        print("NO-DATA: PyYAML is not importable by this interpreter, so the "
              "frontmatter rules could not run; install it or run this under "
              "`uv run --with pyyaml python3`, and do not read this as a pass")
        return 2

    broken = [(p, problems) for p, problems in findings if problems]
    if broken:
        for path, problems in broken:
            for problem in problems:
                print(f"FAILED: {path.relative_to(ROOT)}: {problem}")
        print(f"FAILED: {len(broken)} of {len(skills)} shipped skills are not "
              f"portable")
        return 1

    print(f"PASSED: all {len(skills)} shipped skills carry valid frontmatter, "
          f"name no ~/.claude path, offer a BROTHER_PLUGIN_ROOT alternative "
          f"wherever they name CLAUDE_PLUGIN_ROOT, name only paths that ship "
          f"with them, and give a plain command beside every slash command")
    return 0


if __name__ == "__main__":
    sys.exit(main())
