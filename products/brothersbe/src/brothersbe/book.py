"""`sbe book`: the booklet, with its reference tables generated from canonical state.

WHY THIS EXISTS. The feature list, the roles, the checks with their severities
and the honest limits used to be written out by hand across several
documentation surfaces, free to disagree, with nothing detecting it when they
did. This project's whole argument is that a claim earns trust in proportion to
how mechanically it can be checked, so a hand-typed feature list inside a
document arguing for derived evidence is the product contradicting itself in
public.

This module makes those tables derived: parsed from the files that already
define them, spliced between explicit markers in the booklet, and bound to the
SHA-256 of every source read, so a source that moves without a rebuild is a
FAIL naming the section and the file.

WHY THE BOOKLET IS HTML AND NOT MARKDOWN. It carries inline SVG figures, pull
quotes, scene boxes, capability stamps and panels, none of which survive a
markdown subset. The acts are hand-authored HTML under `docs/fieldbook/booklet/`
and concatenated in filename order; this module owns only the generated blocks
and never rewrites a byte outside a marker pair. The design is in
`design/field-book/`, tier T2, and `tools/sbe_design.py` passes on it.

WHAT IS DERIVED AND WHAT IS NOT, because the distinction is the product. Five
sections are generated: the guided commands, the CLI commands, the reviewer
roles, the registered checks with their severities, and the honest limits. A
sixth, the provenance block, is generated from the other five. Everything else
is prose a human wrote. The cover's declared version is the one freshness stamp
over that prose, and a stale one reports NO-DATA rather than FAIL, because
nobody can compute whether prose went wrong, only that nobody has looked since.

DERIVATION BY IMPORT WHERE THE SOURCE ALLOWS IT. The commands renderer imports
`brothersbe.cli` and reads its `COMMANDS` table as the object it is. The checks
renderer calls `brothersbe.decisions.registries()`, the discovery this
repository already uses in `evals/test_no_data_class.py`, so a check registered
next year appears on the day it is registered rather than the day somebody
remembers to list it. Only `docs/KNOWN-LIMITS.md`, which is prose, is parsed as
text, and it reports NO-DATA naming the file when the parse recovers nothing.

DETERMINISM. No wall-clock is read anywhere here and no absolute path is written
into any output, so the same tree built twice produces byte-identical files.
`bindings.json` therefore records no timestamp: the question `--check` asks is
whether the booklet matches the sources NOW, and git already holds the history
of when each binding moved.

FAIL, NO-DATA AND PASS ARE NOT INTERCHANGEABLE. A source that cannot be read or
parsed is a FAIL carrying the exception. A source that reads cleanly and yields
zero items is NO-DATA naming why, never an empty table a reader would take to
mean "there are none". Under `--check --strict`, only FAIL decides the exit
code, consistent with every other check this repository ships.
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys

from . import cwd as cwd_mod
from .markdown import esc

#: Everything this module reads and writes lives under here, relative to the
#: repository root. One directory, so removing the feature is one `git rm -r`.
BOOK_REL = os.path.join("docs", "fieldbook")
PARTS_REL = os.path.join(BOOK_REL, "booklet")
BINDINGS_REL = os.path.join(BOOK_REL, "bindings.json")
HTML_NAME = "BrotherSBE-Booklet.html"
#: The same booklet without a document wrapper, for a host that supplies its own
#: doctype, head and body. Written by the same run as the standalone page so the
#: published copy and the committed page can never be two different booklets.
FRAGMENT_NAME = "BrotherSBE-Booklet.fragment.html"

#: The marker convention `program/STATUS.md` already uses, so a reader who has
#: seen one generated file in this repository recognises the second.
BEGIN_FMT = "<!-- BEGIN GENERATED FIELDBOOK %s -->"
END_FMT = "<!-- END GENERATED FIELDBOOK %s -->"

PART_NAME_RE = re.compile(r"^\d{2}-.*\.html$")
FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
#: The cover's declared version, which is the booklet's one prose stamp.
# Accepts the old "Describes" label and the current one. The wording changed on
# 2026-08-10 because "Describes v1.0.0-rc.28" on a tree at rc.37 reads to an
# outside reader as an abandoned document, when what the field actually records
# is the last version at which a HUMAN read the prose. That is a deliberate
# declaration, not a generated stamp, which is why nothing bumps it
# automatically: doing so would manufacture the claim that somebody read it.
# Both spellings are matched so an older booklet still parses rather than
# reporting that no version was declared at all.
DESCRIBES_RE = re.compile(
    r"<b>(?:Describes|Prose last read by a human at)</b>\s*v?([0-9][0-9A-Za-z.\-+]*)")


class SourceUnreadable(Exception):
    """A bound source that could not be read or parsed.

    Raised, never defaulted. A renderer that swallows this has two options and
    both are the defect this project exists to prevent: render an empty table a
    reader takes for "there are none", or leave the previous build's content in
    place and call it current.
    """


class Section(object):
    """One generated section: its HTML, the sources it read, and its verdict.

    `verdict` is PASS when a source was read and items were recovered, and
    NO-DATA when a source was read cleanly and recovered nothing. A FAIL never
    reaches this class: it is raised as `SourceUnreadable`, so a section cannot
    quietly become an empty one.
    """

    def __init__(self, name, html, sources, item_count, reason):
        self.name = name
        self.html = html
        self.sources = tuple(sources)
        self.item_count = item_count
        self.reason = reason
        self.verdict = "PASS" if item_count > 0 else "NO-DATA"


def _read(path):
    try:
        return io.open(path, encoding="utf-8").read()
    except (IOError, OSError, UnicodeDecodeError) as exc:
        raise SourceUnreadable("%s could not be read (%s: %s)"
                               % (path, type(exc).__name__, exc))


def _sha256(path):
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except (IOError, OSError) as exc:
        raise SourceUnreadable("%s could not be hashed (%s: %s)"
                               % (path, type(exc).__name__, exc))


def _rel(root, path):
    """A repository-relative path with forward slashes, on every platform.

    Absolute paths never reach a rendered file: they leak the machine that
    built it and they break determinism across two checkouts.
    """
    return os.path.relpath(path, root).replace(os.sep, "/")


def _table(headers, rows, tight=False):
    """An HTML table in the booklet's own markup, scrollable inside its own
    container so the page body never scrolls sideways.

    Empty `rows` returns the empty string, and it is the caller's job to render
    a NO-DATA sentence instead of an empty table.
    """
    if not rows:
        return ""
    out = ['<div class="tw">', '<table%s>' % (' class="tight"' if tight else ""),
           "<thead><tr>%s</tr></thead>"
           % "".join("<th>%s</th>" % esc(h) for h in headers), "<tbody>"]
    for row in rows:
        cells = ["<th>%s</th>" % row[0]]
        cells += ["<td>%s</td>" % cell for cell in row[1:]]
        out.append("<tr>%s</tr>" % "".join(cells))
    out.append("</tbody></table></div>")
    return "\n".join(out)


def _no_data(reason):
    return '<p class="evidence"><strong>NO-DATA.</strong> %s</p>' % esc(reason)


def _code(text):
    return "<code>%s</code>" % esc(text)


# ---------------------------------------------------------------------------
# Renderers. Each returns a Section and declares the sources it read.
# ---------------------------------------------------------------------------

def render_cli_commands(root):
    """Every `sbe` subcommand, from `brothersbe.cli.COMMANDS` itself.

    Imported rather than regex-parsed: the table is a Python object in the same
    package, so reading it as one removes a parser that could be wrong about a
    line continuation or a nested parenthesis.
    """
    source = os.path.join(root, "src", "brothersbe", "cli.py")
    sha = _sha256(source)
    try:
        from . import cli as cli_mod
        commands = list(cli_mod.COMMANDS)
    except Exception as exc:
        raise SourceUnreadable("src/brothersbe/cli.py did not yield a COMMANDS table "
                               "(%s: %s)" % (type(exc).__name__, exc))
    rows = [(_code("sbe %s" % name), esc(" ".join(help_.split())))
            for name, help_ in sorted((e[0], e[1]) for e in commands)]
    html = _table(("Command", "What it does"), rows)
    if not html:
        html = _no_data("brothersbe.cli.COMMANDS holds no command, so this table is "
                        "empty rather than short. Read src/brothersbe/cli.py.")
    reason = ("%d subcommand(s) read from brothersbe.cli.COMMANDS" % len(rows)) if rows \
        else "brothersbe.cli.COMMANDS is empty"
    return Section("cli-commands", html, [(_rel(root, source), sha)], len(rows), reason)


def render_guided_commands(root):
    """The slash commands, one per directory under `skills/`, with the first
    sentence of each SKILL.md description.

    A skills directory holding no skill is NO-DATA naming the directory, never
    an empty list a reader would take to mean the guided surface does not exist.
    """
    skills_dir = os.path.join(root, "skills")
    if not os.path.isdir(skills_dir):
        raise SourceUnreadable("skills/ is not a directory, so the guided command "
                               "surface could not be read")
    rows, sources = [], []
    for name in sorted(os.listdir(skills_dir)):
        skill_file = os.path.join(skills_dir, name, "SKILL.md")
        if not os.path.isfile(skill_file):
            continue
        text = _read(skill_file)
        sources.append((_rel(root, skill_file), _sha256(skill_file)))
        match = FRONT_MATTER_RE.match(text)
        description = ""
        if match:
            for line in match.group(1).split("\n"):
                if line.startswith("description:"):
                    description = line.split(":", 1)[1].strip().strip("'\"")
                    break
        first = description.split(". ")[0].strip()
        rows.append((_code("/brothersbe:%s" % name),
                     esc(first or "(no description declared)")))
    html = _table(("Command", "When to reach for it"), rows)
    if not html:
        html = _no_data("skills/ holds no directory carrying a SKILL.md, so no guided "
                        "command could be read. This is the absence of evidence, not "
                        "evidence that the guided surface is empty.")
    reason = ("%d guided command(s) read from skills/*/SKILL.md" % len(rows)) if rows \
        else "skills/ holds no SKILL.md"
    return Section("guided-commands", html, sources, len(rows), reason)


def render_roles(root):
    """The reviewer roles, from the YAML front matter of `agents/*.md`."""
    agents_dir = os.path.join(root, "agents")
    if not os.path.isdir(agents_dir):
        raise SourceUnreadable("agents/ is not a directory, so no role could be read")
    rows, sources = [], []
    for name in sorted(os.listdir(agents_dir)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(agents_dir, name)
        text = _read(path)
        sources.append((_rel(root, path), _sha256(path)))
        match = FRONT_MATTER_RE.match(text)
        if not match:
            raise SourceUnreadable("agents/%s carries no YAML front matter, so its "
                                   "role could not be read. A role rendered from a "
                                   "guess is worse than a missing one" % name)
        fields = {}
        for line in match.group(1).split("\n"):
            if ":" in line and not line.startswith(" "):
                key, value = line.split(":", 1)
                fields[key.strip()] = value.strip().strip("'\"")
        sentences = [s.strip() for s in fields.get("description", "").split(". ")
                     if s.strip()]
        purpose = sentences[0] if sentences else "(no description declared)"
        covers = ". ".join(sentences[1:]) if len(sentences) > 1 else ""
        rows.append((_code(fields.get("name", name[:-3])), esc(purpose),
                     esc(covers or "(not stated)"),
                     _code(fields.get("tools", "(not stated)"))))
    html = _table(("Role", "What it is for", "What it covers", "Tools it may use"), rows)
    if not html:
        html = _no_data("agents/ holds no markdown file, so no role could be read.")
    reason = ("%d role(s) read from agents/*.md" % len(rows)) if rows \
        else "agents/ holds no markdown file"
    return Section("roles", html, sources, len(rows), reason)


def render_checks(root):
    """Every registered check, its severity and the registry that declares it.

    Discovery is `brothersbe.decisions.registries()`, the same rule
    `evals/test_no_data_class.py` applies, so this table is not a written list
    and cannot fall behind one.
    """
    try:
        from . import decisions as decisions_mod
        found = decisions_mod.registries()
    except Exception as exc:
        raise SourceUnreadable("the check registries could not be discovered "
                               "(%s: %s)" % (type(exc).__name__, exc))
    declarations = found.get("declarations", {})
    problems = found.get("problems", [])
    rows, sources, seen = [], [], set()
    stamp = {"gate": "s-certified", "soft": "s-beta"}
    for name in sorted(declarations):
        for entry in declarations[name]:
            check = entry.get("check")
            absolute = entry.get("path")
            # `source` is tools-relative in some entries and already carries the
            # `tools/` prefix in others, so it is not safe to prefix blindly:
            # doing that printed `tools/tools/sbe_plan.py`. The absolute path
            # relativized against the root is unambiguous.
            source_rel = (_rel(root, absolute) if absolute
                          else str(entry.get("source", "(not recorded)")))
            severity = getattr(check, "severity", None) or "(not declared)"
            empty = getattr(check, "empty_expect", None) or "(not declared)"
            rows.append((
                _code(name), _code(source_rel),
                '<span class="stamp %s">%s</span>'
                % (stamp.get(severity, "s-experimental"), esc(severity)),
                '<span class="stamp %s">%s</span>'
                % ("s-unsupported" if empty == "FAIL" else "s-experimental", esc(empty))))
            if absolute and absolute not in seen and os.path.isfile(absolute):
                seen.add(absolute)
                sources.append((_rel(root, absolute), _sha256(absolute)))
    outside = sorted(path for path, _ in sources if path.startswith(".."))
    if outside:
        # The registries were discovered against a DIFFERENT tree than the one
        # being documented, which happens when the package is imported from an
        # installation while `sbe book` is pointed somewhere else. Recording a
        # path that climbs out of the root would bind this booklet to files the
        # reader's repository does not contain, and the binding would then be
        # unreproducible on any other checkout. That is a FAIL, not a path.
        raise SourceUnreadable(
            "the check registries resolved to %d file(s) outside the repository "
            "being documented (%s). The booklet would be describing another "
            "tree's checks. Run `sbe book` from inside the repository you mean."
            % (len(outside), ", ".join(outside[:3])))
    html = _table(("Check", "Declared in", "Severity", "When its evidence is absent"),
                  rows, tight=True)
    if not html:
        html = _no_data("no registry under tools/ declared a check. That is the absence "
                        "of a reading, not a finding that this project registers none.")
    reason = "%d check declaration(s) across %d registry file(s)" % (len(rows), len(sources))
    if problems:
        reason += "; %d registry problem(s) reported by the discovery" % len(problems)
    return Section("checks", html, sources, len(rows), reason)


def render_limits(root):
    """The honest limits, one row per `##` heading in `docs/KNOWN-LIMITS.md`.

    The headings are the limits; the body under each stays in that file, and the
    booklet points at it rather than restating it, so there is one copy.
    """
    source = os.path.join(root, "docs", "KNOWN-LIMITS.md")
    text = _read(source)
    sha = _sha256(source)
    rows = []
    for line in text.split("\n"):
        if not line.startswith("## "):
            continue
        heading = line[3:].strip()
        law = ""
        match = re.search(r"\((L\d+)\)\s*$", heading)
        if match:
            law, heading = match.group(1), heading[:match.start()].strip()
        rows.append((esc(heading), _code(law) if law else "<em>no law named</em>"))
    html = _table(("The limit, as the project states it", "Law it qualifies"),
                  rows, tight=True)
    if not html:
        html = _no_data("docs/KNOWN-LIMITS.md holds no level-two heading, so no limit "
                        "could be read from it. Read the file directly.")
    reason = ("%d limit(s) read from docs/KNOWN-LIMITS.md" % len(rows)) if rows \
        else "docs/KNOWN-LIMITS.md holds no level-two heading"
    return Section("limits", html, [(_rel(root, source), sha)], len(rows), reason)


#: The renderers, in a fixed order so two builds render the same sections in the
#: same sequence. A renderer added here is picked up by generation, by the drift
#: check and by the provenance block without a second registration.
RENDERERS = (
    ("guided-commands", render_guided_commands),
    ("cli-commands", render_cli_commands),
    ("roles", render_roles),
    ("checks", render_checks),
    ("limits", render_limits),
)

#: The provenance block is generated FROM the other sections rather than from a
#: file, so it is built after them and binds to VERSION, which is the one file
#: whose movement should always force a rebuild.
PROVENANCE = "provenance"


def render_provenance(root, sections):
    """One line per generated section, naming every file it was read from.

    This is the booklet's own evidence line. A reader who disputes a row in any
    generated table settles it by opening a path from here, rather than by
    asking the maintainer.
    """
    version_file = os.path.join(root, "VERSION")
    rows = []
    for section in sections:
        chip = "s-certified" if section.verdict == "PASS" else "s-experimental"
        rows.append((
            _code(section.name),
            '<span class="stamp %s">%s</span>' % (chip, esc(section.verdict)),
            "%d" % section.item_count,
            " ".join(_code(path) for path, _ in section.sources) or "<em>none</em>"))
    html = _table(("Section", "Verdict", "Items", "Read from"), rows, tight=True)
    if not html:
        html = _no_data("no section was generated, so there is no provenance to print.")
    return Section(PROVENANCE, html,
                   [(_rel(root, version_file), _sha256(version_file))],
                   len(rows), "%d generated section(s) accounted for" % len(rows))


# ---------------------------------------------------------------------------
# The booklet parts
# ---------------------------------------------------------------------------

def read_parts(root):
    """Every act file, in filename order, as `(path, text)`.

    A parts directory that does not exist, or holds no act, is raised rather
    than built into an empty booklet.
    """
    parts_dir = os.path.join(root, PARTS_REL)
    if not os.path.isdir(parts_dir):
        raise SourceUnreadable("%s is not a directory, so the booklet has no parts"
                               % PARTS_REL.replace(os.sep, "/"))
    names = sorted(n for n in os.listdir(parts_dir) if PART_NAME_RE.match(n))
    if not names:
        raise SourceUnreadable("%s holds no file matching NN-name.html, so there is no "
                               "booklet to build" % PARTS_REL.replace(os.sep, "/"))
    return [(os.path.join(parts_dir, n), _read(os.path.join(parts_dir, n)))
            for n in names]


def splice(body, name, rendered):
    """Replace the content between a section's markers, leaving everything else
    byte-identical.

    Returns `(new_body, found)`. Author prose outside the markers is never
    touched: the whole point of a marker pair is that a rebuild is safe to run
    over a file somebody is in the middle of writing.
    """
    begin, end = BEGIN_FMT % name, END_FMT % name
    start = body.find(begin)
    if start < 0:
        return body, False
    stop = body.find(end, start)
    if stop < 0:
        raise SourceUnreadable("a part opens the generated section %r and never closes "
                               "it; an unterminated marker would swallow the rest of "
                               "the act" % name)
    return body[:start] + begin + "\n" + rendered.rstrip() + "\n" + body[stop:], True


def declared_version(parts):
    """The version the cover says it describes, or None if no part declares one."""
    for _, text in parts:
        match = DESCRIBES_RE.search(text)
        if match:
            return match.group(1)
    return None


# ---------------------------------------------------------------------------
# Build and check
# ---------------------------------------------------------------------------

def generate(root):
    """Render every section into its part file and write the bindings.

    Returns `(sections, parts, notes)`. Raises `SourceUnreadable` on any FAIL,
    which the caller reports; nothing here degrades to a partial booklet.
    """
    parts = read_parts(root)
    sections = []
    for name, renderer in RENDERERS:
        sections.append(renderer(root))
    sections.append(render_provenance(root, sections))

    notes = []
    for section in sections:
        placed = 0
        for index, (path, text) in enumerate(parts):
            new_text, found = splice(text, section.name, section.html)
            if found:
                parts[index] = (path, new_text)
                placed += 1
        if placed == 0:
            raise SourceUnreadable("no part carries the markers for the generated "
                                   "section %r, so it was rendered and had nowhere to "
                                   "go. A section nobody displays is a section nobody "
                                   "checks" % section.name)
        if placed > 1:
            raise SourceUnreadable("%d parts carry the markers for the generated "
                                   "section %r; one section has one home, or the two "
                                   "copies can disagree" % (placed, section.name))
        notes.append("%s: %s (%s)" % (section.name, section.verdict, section.reason))

    for path, text in parts:
        io.open(path, "w", encoding="utf-8").write(text)

    bindings = {}
    for section in sections:
        bindings[section.name] = {
            "sources": [{"path": path, "sha256": sha} for path, sha in section.sources],
            "item_count": section.item_count,
            "verdict": section.verdict,
        }
    io.open(os.path.join(root, BINDINGS_REL), "w", encoding="utf-8").write(
        json.dumps(bindings, indent=2, sort_keys=True) + "\n")
    return sections, parts, notes


def check(root, version):
    """Compare every binding against its source, and the cover against VERSION.

    Returns a list of `(verdict, name, sentence)`. A moved source is a FAIL. A
    stale cover version is NO-DATA, because nobody can compute whether the prose
    went wrong, only that nobody has looked since.
    """
    results = []
    bindings_path = os.path.join(root, BINDINGS_REL)
    if not os.path.isfile(bindings_path):
        return [("NO-DATA", "bindings",
                 "%s does not exist, so no generated section could be compared against "
                 "its source. Run `sbe book` to create it."
                 % BINDINGS_REL.replace(os.sep, "/"))]
    try:
        bindings = json.loads(_read(bindings_path))
    except ValueError as exc:
        return [("FAIL", "bindings",
                 "%s exists and is not valid JSON (%s). A broken claim is not an absent "
                 "one." % (BINDINGS_REL.replace(os.sep, "/"), exc))]
    if not bindings:
        results.append(("NO-DATA", "bindings",
                        "%s records no section, so nothing was compared."
                        % BINDINGS_REL.replace(os.sep, "/")))

    declared = set(name for name, _ in RENDERERS) | {PROVENANCE}
    recorded = set(bindings)
    for missing in sorted(declared - recorded):
        results.append(("FAIL", missing,
                        "the section %r is declared by a renderer and recorded in no "
                        "binding, so the booklet is missing a section it claims to "
                        "generate. Run `sbe book`." % missing))
    for extra in sorted(recorded - declared):
        results.append(("FAIL", extra,
                        "the binding records the section %r, which no renderer declares "
                        "any more. Run `sbe book`." % extra))

    for name in sorted(recorded & declared):
        entry = bindings[name]
        sources = entry.get("sources") or []
        if not sources:
            results.append(("NO-DATA", name,
                            "the section %r records no source, so nothing about it "
                            "could be compared." % name))
            continue
        moved, absent = [], []
        for item in sources:
            path = os.path.join(root, item.get("path", "").replace("/", os.sep))
            if not os.path.isfile(path):
                absent.append(item.get("path", ""))
                continue
            if _sha256(path) != item.get("sha256"):
                moved.append(item.get("path", ""))
        if absent:
            results.append(("FAIL", name,
                            "the section %r was rendered from %s, which no longer "
                            "exist(s). The booklet is describing a file that is gone."
                            % (name, ", ".join(absent))))
        elif moved:
            results.append(("FAIL", name,
                            "the section %r was rendered from %s, which has changed "
                            "since. Run `sbe book` and commit the result."
                            % (name, ", ".join(moved))))
        elif entry.get("item_count", 0) == 0:
            results.append(("NO-DATA", name,
                            "the section %r matches its %d source(s) and recovered no "
                            "item, so it renders NO-DATA rather than an empty table."
                            % (name, len(sources))))
        else:
            results.append(("PASS", name,
                            "the section %r matches all %d of its source(s), %d item(s) "
                            "rendered." % (name, len(sources), entry.get("item_count", 0))))

    stamp = declared_version(read_parts(root))
    if stamp is None:
        results.append(("NO-DATA", "cover-version",
                        "no part declares the version this booklet describes, so "
                        "nothing records when a human last read the prose."))
    elif stamp != version:
        results.append(("NO-DATA", "cover-version",
                        "the cover says it describes %s and this tree is %s. Prose "
                        "staleness is reported, never enforced: no tool computes "
                        "whether the words went wrong." % (stamp, version)))
    else:
        results.append(("PASS", "cover-version",
                        "the cover declares %s, matching this tree. That records when a "
                        "human last read the prose, not that they were right." % version))
    return results


def assemble(parts):
    """Concatenate the act files in order. They are already HTML; nothing here
    reformats them, so what an author wrote is what a reader sees."""
    return "\n".join(text.rstrip() for _, text in parts) + "\n"


def render_html(parts):
    """One self-contained page. No external host is contacted, so it opens from
    a file path with no network."""
    head, body = _head_and_body(parts)
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            "%s\n</head>\n<body>\n%s</body>\n</html>\n" % (head, body))


def _head_and_body(parts):
    """Split the assembled booklet into what belongs in `<head>` and what belongs
    in `<body>`.

    The first act file carries the title and the stylesheet, which a standalone
    page needs inside `<head>` and a host-wrapped fragment needs inline. Rather
    than maintain two copies, the split is computed from the one source.
    """
    assembled = assemble(parts)
    marker = "</style>"
    cut = assembled.find(marker)
    if cut < 0:
        raise SourceUnreadable("no part carries a </style> tag, so the head and the "
                               "body of the booklet could not be separated")
    cut += len(marker)
    return assembled[:cut], assembled[cut:]


def render_fragment(parts):
    """The same booklet without the document wrapper, for a host that supplies
    its own doctype, head and body."""
    head, body = _head_and_body(parts)
    return head + "\n" + body


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def repo_root(start):
    """The repository root: the nearest ancestor holding a VERSION file and a
    src/brothersbe directory. Never a silently substituted git worktree top."""
    current = os.path.abspath(start)
    while True:
        if (os.path.isfile(os.path.join(current, "VERSION"))
                and os.path.isdir(os.path.join(current, "src", "brothersbe"))):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            raise SourceUnreadable("no repository root above %s carries both a VERSION "
                                   "file and src/brothersbe" % os.path.abspath(start))
        current = parent


def _parser():
    parser = argparse.ArgumentParser(
        prog="sbe book",
        description="Build the booklet from canonical state, or check it for drift. "
                    "Absent evidence is NO-DATA and never a pass.")
    parser.add_argument("path", nargs="?", default=None,
                        help="the repository to read (default: the current one, or --cwd "
                             "when given)")
    cwd_mod.add_cwd_argument(parser)
    parser.add_argument("--check", action="store_true",
                        help="do not write; compare every generated section against "
                             "its source and the cover against VERSION")
    parser.add_argument("--strict", action="store_true",
                        help="with --check, exit nonzero on a FAIL. NO-DATA never "
                             "decides the exit code")
    return parser


def main(rest, exit_ok=0, exit_failed=1, exit_usage=2):
    args = _parser().parse_args(rest)
    try:
        root = repo_root(cwd_mod.resolve(args.path, args.cwd))
        version = _read(os.path.join(root, "VERSION")).strip()
    except SourceUnreadable as exc:
        sys.stderr.write("sbe book: %s\n" % exc)
        return exit_usage

    if args.check:
        try:
            results = check(root, version)
        except SourceUnreadable as exc:
            sys.stdout.write("  %-18s %-8s %s\n" % ("booklet", "FAIL", exc))
            return exit_failed if args.strict else exit_ok
        sys.stdout.write("BROTHERSBE BOOKLET DRIFT CHECK  (advisory unless --strict; "
                         "NO-DATA is never a pass)\n")
        failed = 0
        for verdict, name, sentence in results:
            if verdict == "FAIL":
                failed += 1
            sys.stdout.write("  %-18s %-8s %s\n" % (name, verdict, sentence))
        sys.stdout.write("  %d section verdict(s), %d FAIL\n" % (len(results), failed))
        return exit_failed if (failed and args.strict) else exit_ok

    try:
        sections, parts, notes = generate(root)
        html = render_html(parts)
        fragment = render_fragment(parts)
    except SourceUnreadable as exc:
        sys.stderr.write("sbe book: %s\n" % exc)
        return exit_failed
    out_path = os.path.join(root, BOOK_REL, HTML_NAME)
    io.open(out_path, "w", encoding="utf-8").write(html)
    fragment_path = os.path.join(root, BOOK_REL, FRAGMENT_NAME)
    io.open(fragment_path, "w", encoding="utf-8").write(fragment)
    for note in notes:
        sys.stdout.write("  %s\n" % note)
    sys.stdout.write("wrote %s and %s from %d part(s) and %d generated section(s)\n"
                     % (_rel(root, out_path), _rel(root, fragment_path),
                        len(parts), len(sections)))
    return exit_ok
