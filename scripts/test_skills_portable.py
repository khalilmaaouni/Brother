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
   exactly how it happens. The block is read by a strict subset reader (one
   `key: value` per line, a value plain, double quoted, single quoted or a
   one line `[a, b]` list of plain values)
   whose every accepted input means the same as it does to YAML, and which
   REFUSES anything outside that subset as a finding. It needs no PyYAML.
   Measured 2026-10-31: the PyYAML this suite reached lived in one user's
   site packages under HOME, so under the pre-push gate's empty HOME the
   suite read NO-DATA and every branch whose history touched it was refused
   at push. A shipped skill outside the subset is quoted, never unread.
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
Exit 1 also when the subset reader's own cases (READER_CASES) disagree with
what they pin, so a reader that stopped refusing is a FAIL, never a pass.
Exit 2 NO-DATA, no shipped skill was found at all, which is never a pass.
No em or en dashes.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: The subset reader. A key is a bare word at column 0. A plain value may not
#: open with a YAML indicator, carry `: ` or ` #`, or end in `:`, since YAML
#: reads each of those as structure or a comment rather than text. A plain
#: value YAML would resolve to something other than a string (a number, a
#: date, null, yes/no/on/off in any case) is refused unless it is exactly
#: `true` or `false`; quote it instead.
KEY_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(?: +(.*))?$")
PLAIN_INDICATORS = tuple("-?:,[]{}#&*!|>'\"%@`+.=~0123456789")
NON_STRING_PLAIN = {
    w for base in ("yes", "no", "on", "off", "y", "n", "null", "true", "false")
    for w in (base, base.capitalize(), base.upper())
} | {"<<"}

#: (frontmatter, expected): the reader's contract, run before the skills on
#: every call. Each refusal case trips exactly one refusal, so deleting that
#: refusal changes this table's answer and the run FAILS. The values are what
#: yaml.safe_load returns for the same text (checked against PyYAML 6.0.3 on
#: 2026-10-31); an error case is pinned by the words of its refusal.
READER_CASES = (
    ('name: a-b\ndescription: "say \\"hi\\" \\\\ ok: #1"', {"name": "a-b", "description": 'say "hi" \\ ok: #1'}),
    ("description: 'it''s: #1'", {"description": "it's: #1"}),
    ("name: a\n\nuser-invocable: false\nx: true\nz: a:b c", {"name": "a", "user-invocable": False, "x": True, "z": "a:b c"}),
    ("metadata:", {"metadata": None}),
    ("", "error: frontmatter is not a mapping"),
    ("name: a\n  b", "error: line 2 is indented"),
    ("1a: b", "error: line 1 is not a `key: value` line"),
    ("name: a\nname: b", "error: line 2 repeats the key name"),
    ("name:\ta", "error: line 1 carries a tab"),
    ('description: "a" b', "error: line 1 opens a double quote it never closes"),
    ('description: "a\\nb"', "error: line 1 has an escape other than \\\" or \\\\"),
    ("description: 'it's'", "error: line 1 opens a single quote it never closes"),
    ("description: 'abc", "error: line 1 opens a single quote it never closes"),
    ("description: {a}", "error: line 1 opens an unquoted value with a reserved character"),
    ("argument-hint: [say what, true]\nh: <a b>\ne: []", {"argument-hint": ["say what", True], "h": "<a b>", "e": []}),
    ("argument-hint: [a", "error: line 1 opens a flow sequence it never closes"),
    ("argument-hint: [a, [b]]", "error: line 1 has a flow sequence item outside the subset"),
    ("argument-hint: [a,]", "error: line 1 has a flow sequence item outside the subset"),
    ("argument-hint: [Use when: ready]", "error: line 1 carries `: ` in an unquoted value"),
    ("description: Use when: ready", "error: line 1 carries `: ` in an unquoted value"),
    ("description: ends in a colon:", "error: line 1 ends an unquoted value in `:`"),
    ("description: a #b", "error: line 1 carries ` #` in an unquoted value"),
    ("name: Yes", "error: line 1 is a plain value YAML reads as a non string"),
    ("description: <<", "error: line 1 is a plain value YAML reads as a non string"),
    ("on: a", "error: line 1 has a key YAML reads as a non string"),
)

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


def _value(raw, n):
    """(value, None) or (None, refusal) for one line's value text."""
    if raw.startswith('"'):
        out, i = [], 1
        while i < len(raw) and raw[i] != '"':
            if raw[i] == "\\":
                if raw[i + 1:i + 2] not in ('"', "\\"):
                    return None, f"line {n} has an escape other than \\\" or \\\\"
                i += 1
            out.append(raw[i])
            i += 1
        if i != len(raw) - 1:
            return None, f"line {n} opens a double quote it never closes"
        return "".join(out), None
    if raw.startswith("'"):
        inner = raw[1:-1] if len(raw) > 1 and raw.endswith("'") else None
        if inner is None or "'" in inner.replace("''", ""):
            return None, f"line {n} opens a single quote it never closes"
        return inner.replace("''", "'"), None
    if raw.startswith("["):
        if not raw.endswith("]"):
            return None, f"line {n} opens a flow sequence it never closes"
        items = []
        for item in raw[1:-1].split(",") if raw[1:-1].strip() else ():
            item = item.strip()
            if not item or any(c in item for c in "[]{}\"'"):
                return None, f"line {n} has a flow sequence item outside the subset"
            value, error = _value(item, n)
            if error:
                return None, error
            items.append(value)
        return items, None
    if raw in ("true", "false"):
        return raw == "true", None
    if raw.startswith(PLAIN_INDICATORS):
        return None, f"line {n} opens an unquoted value with a reserved character"
    if ": " in raw:
        return None, f"line {n} carries `: ` in an unquoted value"
    if raw.endswith(":"):
        return None, f"line {n} ends an unquoted value in `:`"
    if " #" in raw:
        return None, f"line {n} carries ` #` in an unquoted value"
    if raw in NON_STRING_PLAIN:
        return None, f"line {n} is a plain value YAML reads as a non string"
    return raw, None


def load_yaml(frontmatter_text):
    """Read the frontmatter with the strict subset reader (see rule 1), or
    return why not as a string. Needs no module beyond the standard library,
    so it answers the same under any HOME and any interpreter."""
    data = {}
    for n, line in enumerate(frontmatter_text.split("\n"), 1):
        if not line.strip():
            continue
        if "\t" in line:
            return None, f"line {n} carries a tab"
        if line[0] == " ":
            return None, f"line {n} is indented"
        match = KEY_LINE_RE.match(line.rstrip())
        if not match:
            return None, f"line {n} is not a `key: value` line"
        key, raw = match.group(1), match.group(2)
        if key in NON_STRING_PLAIN:
            return None, f"line {n} has a key YAML reads as a non string"
        if key in data:
            return None, f"line {n} repeats the key {key}"
        value, error = _value(raw, n) if raw else (None, None)
        if error:
            return None, error
        data[key] = value
    if not data:
        return None, "frontmatter is not a mapping"
    return data, None


def reader_disagreements():
    """Every READER_CASES row the reader answers differently, as strings."""
    out = []
    for text, expected in READER_CASES:
        data, error = load_yaml(text)
        got = "error: " + error if error else data
        if got != expected:
            out.append(f"{text!r}: expected {expected!r}, got {got!r}")
    return out


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


def main():
    disagree = reader_disagreements()
    if disagree:
        for line in disagree:
            print(f"FAILED: frontmatter reader: {line}")
        print(f"FAILED: the frontmatter reader disagrees with {len(disagree)} "
              f"of its {len(READER_CASES)} pinned cases, so no skill was judged")
        return 1

    skills = shipped_skills()
    if not skills:
        print("NO-DATA: no shipped SKILL.md found under "
              + ", ".join(SKILL_GLOBS)
              + "; this checker read nothing, which is not a pass")
        return 2

    findings = [(p, check_one(p, root)) for p, root in skills]

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
