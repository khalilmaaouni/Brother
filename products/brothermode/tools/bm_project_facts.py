#!/usr/bin/env python3
"""Print the project facts that documentation is allowed to state, as JSON.

WHY THIS EXISTS
  Documentation kept carrying facts that had already moved: a test count from
  three loops ago, a hook count from before the fence hook was wired, a release
  tag from the previous candidate. Every one of those numbers already exists
  somewhere in the code or in a one-line file. This tool reads them from there,
  so a page can be checked against the tree instead of against someone's memory.

WHAT IT DELIBERATELY DOES NOT PRINT
  A total test count. It changes on every test that lands, which makes every
  page that quotes it wrong within a day, and a reader who sees a mismatch
  cannot tell a stale page from a broken install. The gate command and its
  expected verdict are printed instead: run it, expect ALL GREEN. Exact counts
  belong in dated release evidence (CHANGELOG.md, the dated baseline files),
  where the date says what the number was true of.

WHERE EACH FACT COMES FROM
  version, is_development,
  release_tag                 VERSION (one line)
  schema_version              tools/bm_store.py, SCHEMA_VERSION
  test_suites, suite files    tools/test_all.py, the SUITES tuple
  hook_events, hook_count     scripts/install.py, HOOK_EVENTS
  supported_python_floor      scripts/install.py, its version_info guard
  retrieval_modes             probed in tools/bm_store.py and tools/bm_learning.py
  default_branch              declared below; git cannot be asked without a
                              subprocess, and shipping tools run none
  repo_url, primary_skill_dir,
  dev_skill_dir                declared below, for the same reason
                                default_branch is: no subprocess
  install_command_plugin       plugin id and marketplace id read from
                                product.identity.json, the owner/name of the
                                marketplace source derived from REPO_URL, and
                                the ref it is pinned to from install_target_tag.
                                Nothing about it is typed twice: this field
                                spent a release naming the pre-v3 ids
                                (brotherme@brotherme-marketplace) because it
                                held its own copy of them
  install_command_pinned,      computed from install_target_tag
  install_command_dev          (PUBLIC_INSTALL_TAG below), never from
                                release_tag: a development identity's
                                release_tag would name a tag that must never
                                exist, so the pinned install command is built
                                from the tag known to actually resolve in
                                git instead. An onboarding page states this
                                command by copying what this tool prints,
                                never by typing a tag from memory, and
                                tools/test_bm_docs.py fails a page that
                                disagrees with it
  is_development                true when version contains ".dev"
  install_target_tag           PUBLIC_INSTALL_TAG below; see docs/RELEASE.md,
                                "The version law"

Python 3.9, standard library only. No network, no subprocess. Reads files,
writes none. No em or en dashes anywhere in this file or its output.

Usage:
  python3 tools/bm_project_facts.py            # JSON on stdout
  python3 tools/bm_project_facts.py --field version
"""

import ast
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# The branch a fresh clone lands on. Declared, not measured: asking git would
# need a subprocess, which every shipping tool in tools/ is banned from. If the
# default branch ever changes, this line changes with it and the documentation
# consistency suite (tools/test_bm_docs.py) fails until the pages agree.
DEFAULT_BRANCH = "main"

# The repository's own clone URL and the two install targets. Declared, not
# measured, for the same reason DEFAULT_BRANCH is: reading them from git would
# need a subprocess, which every shipping tool in tools/ is banned from
# running. If this repository is ever renamed or moved, these three lines
# change together and the documentation consistency suite
# (tools/test_bm_docs.py) fails until every onboarding page agrees.
REPO_URL = "https://github.com/khalilmaaouni/BrotherModeUp.git"
PRIMARY_SKILL_DIR = "~/.claude/skills/brothermode"
DEV_SKILL_DIR = "~/.claude/skills/brothermode-dev"

# The identity register: the file that DECLARES what this product's ids are.
# Read, never restated here, because a second copy of an id is exactly what
# rotted: this module carried its own literal plugin id and marketplace id
# through the v3 rename and kept printing the old pair long after the tree
# had moved, which is the worst place for a stale fact, since a page or a
# script that reads this tool to learn the install command would have
# installed a plugin id that no longer exists. tools/bm_docs.py's
# identity_manifest_offenders holds .claude-plugin/marketplace.json and
# .claude-plugin/plugin.json equal to this register, so reading the register
# is reading what the plugin host actually resolves.
IDENTITY_REGISTER = "product.identity.json"

# The one command this project gates on, and the verdict a healthy run ends on.
GATE_COMMAND = "python3 tools/test_all.py"
GATE_EXPECTATION = "ALL GREEN"

# The last tag actually cut and known to resolve in git. Declared, not
# derived from VERSION: VERSION can carry a development identity (a version
# containing ".dev") whose own "v" + version name must never become a tag, so
# the public install command is pinned to THIS constant instead, never to
# release_tag. Bump it by hand, in the same change that cuts and pushes a new
# tag, never before. See docs/RELEASE.md, "The version law".
PUBLIC_INSTALL_TAG = "v3.4.2"


class FactError(Exception):
    """A fact could not be read from the tree. Never guessed, always raised."""


def _read(path):
    try:
        with io.open(path, encoding="utf-8") as fh:
            return fh.read()
    except (IOError, OSError) as exc:
        raise FactError("cannot read %s: %s" % (path, exc))


def _search(pattern, text, path, what):
    m = re.search(pattern, text, re.MULTILINE)
    if not m:
        raise FactError(
            "%s no longer states %s in the form this tool reads (%s). "
            "Fix the pattern here rather than hand-writing the fact into a page."
            % (path, what, pattern))
    return m


def _tuple_strings(text, name, path):
    """Return the string literals of a top level NAME = ( ... ) tuple."""
    m = _search(r"^%s\s*=\s*\(" % re.escape(name), text, path, name)
    depth, i = 0, m.end() - 1
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    if depth != 0:
        raise FactError("%s: unbalanced parentheses after %s" % (path, name))
    items = re.findall(r'["\']([^"\']+)["\']', text[m.end():i])
    if not items:
        raise FactError("%s: %s holds no string entries" % (path, name))
    return items


def _suites(text, path):
    """The SUITES tuple in tools/test_all.py, parsed with ast rather than
    re.findall over the raw source text.

    SUITES carries prose comments beside each entry, and a plain
    quote-to-quote regex over the source text reads every quote in those
    comments too: a stray apostrophe opens a fake quoted region that
    swallows real suite names. That is exactly what happened here before
    this fix, which is why tools/test_all.py's own SUITES tuple carries
    nine "NO APOSTROPHE ANYWHERE IN THIS COMMENT" warnings today, and why
    this suite has already gone red once for exactly that. ast.parse only
    ever sees the real syntax tree, so a comment's apostrophes never reach
    it, and ast.literal_eval refuses anything that is not a literal, so a
    computed suite name fails loudly here rather than being silently
    dropped or swallowed.
    """
    tree = ast.parse(text, filename=path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "SUITES"
                   for t in node.targets):
            continue
        value = ast.literal_eval(node.value)
        if not isinstance(value, tuple) or not all(
                isinstance(v, str) for v in value):
            raise FactError("%s: SUITES is not a tuple of string literals"
                            % path)
        if not value:
            raise FactError("%s: SUITES holds no entries" % path)
        return value
    raise FactError(
        "%s no longer states a module level SUITES = ( ... ) tuple in the "
        "form this tool reads. Fix the pattern here rather than "
        "hand-writing the fact into a page." % path)


def _identity(root):
    """The two declared ids the plugin install command names.

    A missing, empty or non-string id raises rather than falling back to a
    literal: a guessed plugin id prints a command that installs nothing, and
    a reader cannot tell that command from a working one until it fails.
    """
    try:
        data = json.loads(_read(os.path.join(root, IDENTITY_REGISTER)))
    except ValueError as exc:
        raise FactError("%s does not parse as JSON: %s"
                        % (IDENTITY_REGISTER, exc))
    out = {}
    for key in ("plugin_id", "marketplace_id"):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise FactError(
                "%s no longer states %s as a non-empty string. Fix the "
                "register rather than hand-writing the id into a page."
                % (IDENTITY_REGISTER, key))
        out[key] = value.strip()
    return out


def _owner_repo(url):
    """The `owner/name` a plugin marketplace source names, taken from `url`.

    Derived from REPO_URL rather than declared a second time: the clone
    command and the marketplace add command must always name the same
    repository, and two hand typed copies of one slug are two chances to
    disagree.
    """
    m = re.search(r"github\.com[:/]+([^/\s]+/[^/\s]+?)(?:\.git)?/?$", url)
    if not m:
        raise FactError(
            "REPO_URL (%s) is not a github owner/name URL, so the plugin "
            "marketplace source cannot be derived from it" % url)
    return m.group(1)


def facts(root=ROOT):
    tools = os.path.join(root, "tools")
    scripts = os.path.join(root, "scripts")

    version = _read(os.path.join(root, "VERSION")).strip()
    if not version:
        raise FactError("VERSION is empty")

    store_src = _read(os.path.join(tools, "bm_store.py"))
    schema = int(_search(r"^SCHEMA_VERSION\s*=\s*(\d+)", store_src,
                         "tools/bm_store.py", "SCHEMA_VERSION").group(1))

    all_src = _read(os.path.join(tools, "test_all.py"))
    suites = _suites(all_src, "tools/test_all.py")

    install_src = _read(os.path.join(scripts, "install.py"))
    hooks = _tuple_strings(install_src, "HOOK_EVENTS", "scripts/install.py")
    floor = _search(r"sys\.version_info\s*<\s*\((\d+),\s*(\d+)\)", install_src,
                    "scripts/install.py", "a Python floor guard")
    python_floor = "%s.%s" % (floor.group(1), floor.group(2))

    # Retrieval is lexical today. fts5 counts as available only when the store
    # actually creates the virtual table, never because a docstring mentions it
    # as a plan: tools/bm_learning.py discusses FTS5 as future work, and a
    # keyword probe over prose would advertise a mode nobody built.
    modes = ["lexical"]
    if re.search(r"USING\s+fts5", store_src, re.IGNORECASE):
        modes.append("fts5")

    # is_development: true when the tree carries a development identity
    # rather than a released one. A dev identity's own "v" + version is a
    # tag that must never exist (a late tag on a superseded commit is the
    # exact two-trees ambiguity this project ratified against), so
    # release_tag is honest about that instead of pretending one is coming:
    # present only for a released identity, None for a development one.
    is_development = ".dev" in version
    release_tag = None if is_development else ("v" + version)

    install_target_tag = PUBLIC_INSTALL_TAG

    # THE ONE PLACE THE PRIMARY INSTALL COMMAND IS ASSEMBLED. Every onboarding
    # page states this command by copying what this tool prints, never by
    # typing the tag from memory. Built from install_target_tag, never from
    # release_tag: the public install target is the tag known to actually
    # resolve in git, which is a fact independent of what this tree's own
    # VERSION currently claims. REPO_URL and PRIMARY_SKILL_DIR are declared
    # constants, same as DEFAULT_BRANCH.
    install_command_pinned = ("git clone --branch %s --depth 1 %s %s"
                              % (install_target_tag, REPO_URL, PRIMARY_SKILL_DIR))
    # The separate, clearly labeled development command: tracks the moving
    # default branch on purpose, into its OWN directory, so a reader can never
    # confuse it with the pinned install above.
    install_command_dev = (
        "# Development branch (changes over time)\n"
        "git clone --branch %s %s %s"
        % (DEFAULT_BRANCH, REPO_URL, DEV_SKILL_DIR))
    # The boring install: two plain shell commands through the Claude Code
    # client's own plugin manager, proven end to end by
    # scripts/release-smoke-install.sh. Every part of it is read from
    # somewhere that already had to be right: the ids from the identity
    # register, the owner/name from REPO_URL, and the ref from
    # install_target_tag, the same fact the pinned clone above uses.
    #
    # The marketplace add carries the @<tag> pin on purpose, and it is the
    # pin, not a default branch, that this line is built on: an owner/repo@ref
    # marketplace source resolves to that exact tag on every add and every
    # later refresh, so the easiest install path is as auditable as the clone.
    # An unpinned line here would also print a command no page is allowed to
    # carry (tools/test_bm_docs.py fails an active page whose marketplace add
    # has no ref) and would silently drop out of step with docs/RELEASE.md
    # step 2b, which re-pins this ref at every release by bumping
    # PUBLIC_INSTALL_TAG above.
    identity = _identity(root)
    install_command_plugin = (
        "claude plugin marketplace add %s@%s\n"
        "claude plugin install %s@%s"
        % (_owner_repo(REPO_URL), install_target_tag,
           identity["plugin_id"], identity["marketplace_id"]))

    return {
        "version": version,
        "is_development": is_development,
        "release_tag": release_tag,
        "install_target_tag": install_target_tag,
        "repo_url": REPO_URL,
        "primary_skill_dir": PRIMARY_SKILL_DIR,
        "dev_skill_dir": DEV_SKILL_DIR,
        "install_command_pinned": install_command_pinned,
        "install_command_dev": install_command_dev,
        "install_command_plugin": install_command_plugin,
        "schema_version": schema,
        "test_suites": len(suites),
        "test_suite_files": suites,
        "gate_command": GATE_COMMAND,
        "gate_expectation": GATE_EXPECTATION,
        "hook_count": len(hooks),
        "hook_events": hooks,
        "retrieval_modes": modes,
        "supported_python_floor": python_floor,
        "default_branch": DEFAULT_BRANCH,
    }


def main(argv):
    field = None
    if argv:
        if argv[0] != "--field" or len(argv) != 2:
            print("usage: bm_project_facts.py [--field NAME]", file=sys.stderr)
            return 2
        field = argv[1]
    try:
        data = facts()
    except FactError as exc:
        print("bm_project_facts: %s" % exc, file=sys.stderr)
        return 2
    if field is None:
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0
    if field not in data:
        print("bm_project_facts: no such fact %r; known: %s"
              % (field, ", ".join(sorted(data))), file=sys.stderr)
        return 2
    value = data[field]
    print(value if not isinstance(value, list) else " ".join(map(str, value)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
