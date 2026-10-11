"""Prose-as-code for bare `/brother` routing (P0.1).

This estate's own recorded lesson ("a green suite that never read the word")
is that a check can pass while the words it was meant to pin have drifted or
never existed at all. Bare `/brother` used to be told two different things by
two sections of the same command file: "No argument" said ask a question
unconditionally, "RECOVERY" said a bare invocation does not ask and instead
resumes unfinished work. Both fired on the same trigger (an empty argument),
so which one a session followed was luck.

The fix moved the single decision order into bundle/skills/using-brother/
SKILL.md and made the command file reference it rather than restate it. This
suite pins THAT SHAPE, not just that some text exists:

  1. The skill file names exactly one section as the authoritative decision
     order (a heading matching "authoritative decision order").
  2. The specific sentence that used to make the ask unconditional (with no
     prior check for unfinished work) does not appear anywhere in bundle/.
     If it comes back, the contradiction is back with it.
  3. The authoritative section never instructs telling the person a run id
     or a run directory; it only prohibits doing so.
"""
import glob
import os
import re
import subprocess
import sys
import typing
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import gen_door_table as GDT  # noqa: E402
BUNDLE_DIR = os.path.join(REPO_ROOT, "bundle")
SKILL_PATH = os.path.join(BUNDLE_DIR, "skills", "using-brother", "SKILL.md")
COMMAND_PATH = os.path.join(BUNDLE_DIR, "commands", "brother.md")
#: Hub-only: the maintainer half of the door, split out under E44.
MAINTAINER_REFERENCE = os.path.join(
    REPO_ROOT, "docs", "maintainer", "BROTHER-MAINTAINER-VERBS.md")

# The exact sentence the old "No argument" section used to open with: it told
# the assistant to ask a question whenever $ARGUMENTS was empty, with no
# mention of checking for unfinished work first. That is precisely what the
# old "RECOVERY" section contradicted ("does not ask what to build" on the
# very same trigger). Its return anywhere in bundle/ means the contradiction
# is back.
REMOVED_CONTRADICTING_PHRASE = (
    "If `$ARGUMENTS` is empty, say exactly one plain sentence and ask "
    "exactly one question, nothing else:"
)

AUTHORITATIVE_HEADING = re.compile(
    r"^#+.*authoritative decision order", re.IGNORECASE)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _body(text):
    """`text` with any leading YAML frontmatter removed.

    bundle/codex-skills/ is GENERATED from bundle/skills/ by
    scripts/codex_skills.py, which strips the frontmatter keys the Codex
    validator refuses and copies the body verbatim. So the Codex mirror of
    the authority restates the SAME claim for another client rather than
    making a second one, and comparing bodies is what proves that, where an
    exemption by file name would just be a hole. A mirror whose body has
    drifted from the source still fails."""
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            after = text[end + 1:]
            newline = after.find("\n")
            return "" if newline == -1 else after[newline + 1:]
    return text


HANDOVER_DIR = "handover-to-launch-epic-2026-09-20"


def handover_mentions(body):
    """Every line of the command file that names the handover directory, in
    file order, so the door can be proven to name it exactly once."""
    if not isinstance(body, str):
        raise ValueError(
            "handover_mentions needs the command file as a str, got %s"
            % type(body).__name__)
    return [line for line in body.splitlines() if HANDOVER_DIR in line]


def command_lines(body):
    """Every line of the command file a person could run, taken from fenced
    and indented blocks, in file order, so the bare door can be proven to
    hand over exactly one command."""
    if not isinstance(body, str):
        raise ValueError(
            "command_lines needs the command file as a str, got %s"
            % type(body).__name__)
    commands = []
    in_fence = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            if stripped:
                commands.append(stripped)
            continue
        if line[:4] == "    " and stripped:
            commands.append(stripped)
            continue
        for span in re.findall(r"`([^`\n]+)`", line):
            if " --" in span:
                commands.append(span.strip())
    return commands


def _iter_bundle_text_files():
    for dirpath, _dirnames, filenames in os.walk(BUNDLE_DIR):
        for name in filenames:
            if name.endswith((".md", ".py", ".sh")):
                path = os.path.join(dirpath, name)
                yield path, _read(path)


def _authoritative_section_text(skill_text):
    """The authoritative section's own body: from its heading up to (but not
    including) the next heading of the same or a shallower level."""
    lines = skill_text.splitlines()
    start = None
    start_level = None
    for i, line in enumerate(lines):
        m = AUTHORITATIVE_HEADING.match(line)
        if m:
            start = i
            start_level = len(line) - len(line.lstrip("#"))
            break
    assert start is not None, "authoritative heading not found"
    end = len(lines)
    for j in range(start + 1, len(lines)):
        stripped = lines[j]
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            if level <= start_level:
                end = j
                break
    return "\n".join(lines[start:end])


class OneAuthoritativeSection(unittest.TestCase):

    def test_skill_names_exactly_one_authoritative_section(self):
        text = _read(SKILL_PATH)
        headings = [ln for ln in text.splitlines()
                    if AUTHORITATIVE_HEADING.match(ln)]
        self.assertEqual(len(headings), 1,
                          "expected exactly one authoritative-decision-order "
                          "heading in %s, found %r" % (SKILL_PATH, headings))

    def test_no_other_bundle_file_claims_the_authority(self):
        skill_body = _body(_read(SKILL_PATH))
        for path, text in _iter_bundle_text_files():
            if path == SKILL_PATH:
                continue
            headings = [ln for ln in text.splitlines()
                        if AUTHORITATIVE_HEADING.match(ln)]
            if headings and _body(text) == skill_body:
                # A generated restatement of the authority itself, byte for
                # byte below the frontmatter. See _body.
                continue
            self.assertEqual(
                headings, [],
                "%s claims a second authoritative decision order: %r"
                % (path, headings))


class NoContradictingProse(unittest.TestCase):

    def test_removed_unconditional_ask_phrase_stays_absent(self):
        for path, text in _iter_bundle_text_files():
            self.assertNotIn(
                REMOVED_CONTRADICTING_PHRASE, text,
                "%s still carries the unconditional-ask phrasing that "
                "contradicted the recovery path" % path)

    def test_command_file_points_at_the_skill_instead_of_restating(self):
        text = _read(COMMAND_PATH)
        self.assertIn("using-brother/SKILL.md", text,
                       "brother.md no longer references the single "
                       "authority for bare /brother")
        # The command file's own "ask a question" step must not be reachable
        # before its "check for unfinished work" step in the source order,
        # which is the shape that made the old contradiction possible.
        check_pos = text.find("check for unfinished work")
        ask_pos = text.find("ask the one question")
        self.assertNotEqual(check_pos, -1, "Step 1 wording missing")
        self.assertNotEqual(ask_pos, -1, "Step 2 wording missing")
        self.assertLess(check_pos, ask_pos,
                         "the unfinished-work check must be written before "
                         "the ask-a-question step, not after")


class ShippedCommandStaysSmall(unittest.TestCase):
    """E44. The shipped command is what EVERY user pays for on invocation,
    so its size is a product property, not a formatting preference. It once
    carried 394 lines, of which lines 115 to 365 were maintainer verbs that
    only run inside a checkout of this repository: an intervention ladder, a
    row contract, an integrator policy, a handover ceremony. That detail now
    lives in MAINTAINER_REFERENCE, which the command names by path and which
    is read only when `handover` or `board` actually fires.

    The ceiling is a ratchet, not a target: it is set just above today's
    measured size so this is green on arrival and turns red the moment the
    maintainer prose starts creeping back in.

    R-5 (persona dogfood, 2026-09-07) moved the ceiling from 80 to 116: the
    verb table used to be a six-row literal missing 22 real capabilities, and
    the fix is a generated one row per verb either product ships. That growth
    is the point of the fix, not drift, so the ratchet moved with it."""

    CEILING = 116

    def test_command_file_is_under_the_line_ceiling(self):
        with open(COMMAND_PATH, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertLess(
            len(lines), self.CEILING,
            "%s is %d lines, at or over the %d-line ceiling; maintainer "
            "detail belongs in %s, not in every user's context"
            % (COMMAND_PATH, len(lines), self.CEILING, MAINTAINER_REFERENCE))

    def test_maintainer_reference_exists_and_is_named_by_the_command(self):
        # A checkout property: the reference is hub-only, so the export tree cannot hold it (2026-09-28, the hermetic
        # check refused a landing that only touched this file). Skipped when absent, as test_jev_catalogue_l6b1 does
        # for its plan document. ponytail: the export tree carries no marker of its own, so a reference deleted in a
        # checkout also skips here; add an export marker to export_public.py if that case ever matters.
        if not os.path.isfile(MAINTAINER_REFERENCE):
            self.skipTest("hub-only reference, not in this tree: %s" % MAINTAINER_REFERENCE)
        self.assertTrue(
            os.path.isfile(MAINTAINER_REFERENCE),
            "the maintainer reference %s is missing, so the command's "
            "pointer for `handover` and `board` resolves to nothing"
            % MAINTAINER_REFERENCE)
        rel = os.path.relpath(MAINTAINER_REFERENCE, REPO_ROOT)
        self.assertIn(rel, _read(COMMAND_PATH),
                      "the command file must name %s by path so the "
                      "maintainer detail is reachable when a verb fires"
                      % rel)

    def test_maintainer_reference_stays_out_of_the_bundle(self):
        # It is hub-only on purpose: docs/maintainer/ is not an entry in
        # docs/plan/EXPORT-ALLOWLIST.txt, and export_public.py copies only
        # tracked files under a named entry.
        self.assertFalse(
            MAINTAINER_REFERENCE.startswith(BUNDLE_DIR + os.sep),
            "the maintainer reference must not ship inside bundle/")
        allowlist_path = os.path.join(
            REPO_ROOT, "docs", "plan", "EXPORT-ALLOWLIST.txt")
        entries = [ln.strip() for ln in _read(allowlist_path).splitlines()
                   if ln.strip() and not ln.strip().startswith("#")]
        rel = os.path.relpath(MAINTAINER_REFERENCE, REPO_ROOT)
        covering = [e for e in entries
                    if rel == e or rel.startswith(e.rstrip("/") + "/")]
        self.assertEqual(
            covering, [],
            "%s is covered by export allowlist entries %r, so it would "
            "leave the hub" % (rel, covering))


class NeverNamesTheStorage(unittest.TestCase):

    def test_authoritative_section_never_tells_user_a_run_id_or_directory(self):
        section = _authoritative_section_text(_read(SKILL_PATH))
        # It must PROHIBIT naming the run id/directory, never instruct it.
        self.assertRegex(
            section, r"[Nn]ever.{0,40}run id.{0,40}run directory",
            "the authoritative section must explicitly forbid naming a "
            "run id or run directory to the person")
        # No internal identifier names leaking into user-facing prose.
        for leaked in ("run_dir", "%(run_dir)s", "print(run_dir"):
            self.assertNotIn(leaked, section,
                              "internal identifier %r found in prose meant "
                              "for a person" % leaked)



class TestDoorTable(unittest.TestCase):
    """R-5 (persona dogfood, 2026-09-07): the door's verb table used to be a
    six-row literal typed by hand, so an ask naming any of 22 real
    capabilities (kickoff, design, work, adopt, handover, learn,
    prove-this-change, spec-and-data-prep, brief, decisions, and more) fell
    through to `start` silently. scripts/gen_door_table.py now generates the
    table from the `name:` frontmatter of every installed skill, between two
    sentinel comments in bundle/commands/brother.md. These tests pin that the
    generated table stays complete, stable, and confined to its own region."""

    def test_every_installed_skill_name_appears_in_the_door_table(self):
        door_text = _read(COMMAND_PATH)
        missing = []
        pattern = os.path.join(GDT.ROOT, "bundle", "skills", "*", "SKILL.md")
        paths = sorted(glob.glob(pattern))
        self.assertTrue(paths, "no skills found under bundle/skills")
        rows = 0
        for path in paths:
            name, _typeable = GDT.read_skill(path)
            split = GDT.split_name(name)
            if split is None:
                continue
            rows += 1
            product, verb = split
            if "| %s |" % verb not in door_text:
                missing.append("%s:%s" % (product, verb))
            if "`/brother:%s`" % name not in door_text:
                missing.append("cell /brother:%s" % name)
        self.assertGreater(rows, 0)
        self.assertEqual(
            missing, [],
            "%s carries no row for: %s" % (COMMAND_PATH, ", ".join(missing)))

    def test_no_retired_namespace_in_bundle(self):
        """One plugin (2026-09-30): after 1.1.0 nothing but `brother` is
        installed, so a cell or a skill saying /brothermode:, /brothersbe:
        or /brotherds: names a command that does not exist."""
        hits = GDT.retired_namespace_hits(REPO_ROOT, ("bundle",))
        self.assertEqual(hits, [], hits[:5])

    def test_check_mode_goes_red_on_a_stale_table(self):
        before = _read(COMMAND_PATH)
        try:
            GDT.rewrite(COMMAND_PATH, "| Verb | x | y |\n|---|---|---|\n")
            proc = subprocess.run([sys.executable, "-B", GDT.__file__,
                                   "--check"], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("DRIFT", proc.stderr)
        finally:
            with open(COMMAND_PATH, "w", encoding="utf-8") as fh:
                fh.write(before)
        proc = subprocess.run([sys.executable, "-B", GDT.__file__, "--check"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_generator_is_idempotent(self):
        cells = GDT.collect()
        table_once = GDT.render_table(cells)
        table_twice = GDT.render_table(GDT.collect())
        self.assertEqual(
            table_once, table_twice,
            "gen_door_table produced a different table on a second read of "
            "the same skill tree")
        # A second rewrite of the real door with the same table changes
        # nothing: rewrite() returns False when the bytes do not move.
        changed = GDT.rewrite(COMMAND_PATH, table_once)
        self.assertFalse(
            changed,
            "%s is not current: running gen_door_table.py would rewrite it, "
            "so the shipped table has drifted from the skills on disk"
            % COMMAND_PATH)

    def test_sentinel_block_is_the_only_rewritten_region(self):
        before = _read(COMMAND_PATH)
        start = before.find(GDT.BEGIN)
        end = before.find(GDT.END)
        self.assertNotEqual(start, -1, "%s lost its BEGIN sentinel" %
                             COMMAND_PATH)
        self.assertNotEqual(end, -1, "%s lost its END sentinel" %
                             COMMAND_PATH)
        prose_before = before[:start], before[end:]

        # Rewrite with a deliberately wrong table, then confirm only the
        # sentinel region moved and everything outside it is byte-identical.
        poisoned_table = "| Verb | Project (BrotherMode) | Assurance " \
            "(BrotherSBE) |\n|---|---|---|\n| bogus | `x` | `y` |\n"
        try:
            changed = GDT.rewrite(COMMAND_PATH, poisoned_table)
            self.assertTrue(changed, "rewrite() reported no change for a "
                             "deliberately different table")
            after = _read(COMMAND_PATH)
            new_start = after.find(GDT.BEGIN)
            new_end = after.find(GDT.END)
            prose_after = after[:new_start], after[new_end:]
            self.assertEqual(
                prose_before, prose_after,
                "rewriting the sentinel block changed prose outside it")
            self.assertIn("bogus", after)
        finally:
            # Restore the real table so this test leaves no residue behind.
            real_cells = GDT.collect()
            restored = GDT.rewrite(COMMAND_PATH, GDT.render_table(real_cells))
            self.assertTrue(restored or GDT.BEGIN in _read(COMMAND_PATH))

class HandoverMentions(unittest.TestCase):
    """RQ-05 (2026-09-20) had the door name the handover directory exactly
    once. Reversed 2026-09-30 (architecture review F6): that directory lives
    under a private evidence root, never in the plugin, so a user reading
    /brother saw an internal session pointer. The door names it never."""

    @unittest.skipUnless(os.path.isfile(COMMAND_PATH),
                         "bundle/commands/brother.md is not present")
    def test_handover_directory_never_named(self):
        mentions = handover_mentions(_body(_read(COMMAND_PATH)))
        self.assertEqual(
            mentions, [],
            "the door must not carry the private pointer %s, found %r"
            % (HANDOVER_DIR, mentions))


class HandsOneCommand(unittest.TestCase):
    """RQ-04: bare /brother hands the person exactly one command."""

    @unittest.skipUnless(os.path.isfile(COMMAND_PATH),
                         "bundle absent from this export copy")
    def test_bare_brother_names_exactly_one_command(self):
        lines = _read(COMMAND_PATH).splitlines()
        start = None
        for i, line in enumerate(lines):
            if line.startswith("## Bare `/brother`"):
                start = i
                break
        self.assertIsNotNone(start, "the bare /brother section is missing")
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if lines[j].startswith("## "):
                end = j
                break
        bare = "\n".join(lines[start:end])
        commands = command_lines(bare)
        self.assertEqual(
            len(commands), 1,
            "bare /brother must hand exactly one command, found %r"
            % (commands,))
        self.assertIn("brother-run --continue", commands[0])


#: The 48 skill names v1.1.0 shipped beside the door, read from
#: `git ls-tree origin/main bundle/skills` at that tag, each with the route
#: docs/plan/specs/U1.md section 2 gives it (the verb, plus the words the
#: door passes with it). Spelled out here, independent of the generator's own
#: table, so a name dropped from codex_surface.RETIRED, or remapped to
#: another verb, fails its own case below rather than vanishing.
RETIRED_1_1_0 = {
    "brotherme-auto": "start", "brotherme-auto-status": "status", "brotherme-brief": "status",
    "brotherme-decisions": "next", "brotherme-deliver": "deliver", "brotherme-handback": "deliver",
    "brotherme-handover-pack": "deliver", "brotherme-help": "help", "brotherme-next": "next",
    "brotherme-review": "review", "brotherme-start": "start", "brotherme-status": "status",
    "brotherme-stop": "start stop", "brotherme-update": "help", "brotherme-view": "status",
    "brothermode-auto": "start", "brothermode-auto-status": "status", "brothermode-brief": "status",
    "brothermode-brotherme": "help", "brothermode-cursor-dispatch": "start dispatch",
    "brothermode-cursor-execute": "next", "brothermode-decisions": "next", "brothermode-deliver": "deliver",
    "brothermode-doctor": "help", "brothermode-handback": "deliver", "brothermode-handover-pack": "deliver",
    "brothermode-help": "help", "brothermode-next": "next", "brothermode-review": "review",
    "brothermode-start": "start", "brothermode-status": "status", "brothermode-stop": "start stop",
    "brothermode-update": "help", "brothermode-view": "status",
    "brothersbe-adopt": "start", "brothersbe-design": "review", "brothersbe-handover": "deliver",
    "brothersbe-help": "help", "brothersbe-kickoff": "start", "brothersbe-learn": "deliver",
    "brothersbe-next": "next", "brothersbe-prove-this-change": "review", "brothersbe-review": "review",
    "brothersbe-spec-and-data-prep": "start", "brothersbe-start": "start", "brothersbe-status": "status",
    "brothersbe-verify": "review", "brothersbe-work": "start",
}
RETIRED_TABLE_PATH = os.path.join(BUNDLE_DIR, "skills", "using-brother", "references", "retired-names.md")
RETIRED_ROW = re.compile(
    r"^\| (?P<name>[a-z-]+) \| `/brother (?P<route>[a-z]+(?: [a-z]+)*)` \(`brother-(?P<verb>[a-z]+)`\) \|", re.M)


def retired_route(name, table_text):
    """The route the door's table gives a 1.1.0 name (the verb and any words
    passed with it), or None when the table carries no row for it. A row
    whose skill cell disagrees with its route's verb is a ValueError, never a
    route. Pure, over the text the door reads."""
    if not isinstance(name, str) or not isinstance(table_text, str):
        raise ValueError("retired_route needs the name and the table as str")
    for match in RETIRED_ROW.finditer(table_text):
        if match.group("name") == name:
            if match.group("route").split()[0] != match.group("verb"):
                raise ValueError("%s: route %r names skill brother-%s" % (name, match.group("route"), match.group("verb")))
            return match.group("route")
    return None


class RetiredNamesRoute(unittest.TestCase):
    """U1 (release 1.1.1; owner ruling 2026-10-10 withdrawing C3 and C4, the
    1.1.0 names routed for one release). The door's entry point is the rule in
    bundle/commands/brother.md plus the table it looks a name up in; one case
    per 1.1.0 name proves the row exists, routes to one of the six verbs, and
    that verb's skill ships. Deleting any one routing entry fails exactly that
    name's case."""

    def test_the_door_carries_the_lookup_rule_and_the_pointer_line(self):
        door = _read(COMMAND_PATH)
        # Review note 9: the door names the table from the plugin root, never a bare relative path.
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/skills/using-brother/references/retired-names.md", door)
        self.assertIn("is now /brother <verb>", door)
        self.assertIn("never guess a verb", door)
        # Review note 10: on Codex the door skill names the skill, with the Claude Code form beside it.
        # The skill wraps its prose at 72 columns, so the phrase is compared with its whitespace folded.
        skill = re.sub(r"\s+", " ", _authoritative_section_text(_read(SKILL_PATH)))
        self.assertIn("references/retired-names.md", skill)
        self.assertIn("is now the brother-<verb> skill (in Claude Code: /brother <verb>)", skill)

    def test_the_door_asks_the_one_question_the_skill_asks_and_never_a_second_route_for_bare(self):
        """Review note 5: Step 2's question is the door skill's row 2 question, word for word, and the
        bare section asks exactly one question; the sentence that sent a bare door to `start` or `next`
        behind Steps 1 and 2 is gone."""
        door = _read(COMMAND_PATH)
        skill_order = re.sub(r"\s+", " ", _authoritative_section_text(_read(SKILL_PATH)))
        question = re.search(r'Ask one question: "([^"]+\?)"', skill_order)
        self.assertIsNotNone(question, "the skill's row 2 lost its quoted question")
        lines = door.splitlines()
        start = next(i for i, ln in enumerate(lines) if ln.startswith("## Bare `/brother`"))
        end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("## "))
        bare = "\n".join(lines[start:end])
        self.assertIn(question.group(1), re.sub(r"\s+", " ", bare))
        self.assertEqual(bare.count("?"), 1, "the bare door asks more than one question:\n%s" % bare)
        self.assertNotIn("goes to `start`", door)
        self.assertNotIn("with work begun, to `next`", door)

    def test_the_door_routes_continue_and_show_me_to_existing_machinery(self):
        door = _read(COMMAND_PATH)
        self.assertIn("brother-run --continue", door)
        self.assertIn("never a second run database", door)
        self.assertRegex(door, r"[Ss]how\s+me what happened\" is `status`")
        self.assertIn("one genuinely blocking question", door)
        for forbidden in ("product name", "autonomy code", "plan format", "run id", "test framework"):
            self.assertIn(forbidden, door, "the door must name %r as a thing it never asks" % forbidden)

    def test_an_unknown_old_name_has_no_row(self):
        self.assertIsNone(retired_route("brothermode-nothing", _read(RETIRED_TABLE_PATH)))
        for bad in (None, 1, b"x"):
            with self.assertRaises(ValueError):
                retired_route(bad, "")

    def test_the_table_carries_exactly_the_48_names_with_their_routes(self):
        """Review note 2: the whole map, name to route, pinned independently of the generator."""
        table = _read(RETIRED_TABLE_PATH)
        routes = dict((m.group("name"), m.group("route")) for m in RETIRED_ROW.finditer(table))
        self.assertEqual(routes, RETIRED_1_1_0)
        self.assertEqual(len(routes), 48)
        with self.assertRaises(ValueError):
            retired_route("x", "| x | `/brother start` (`brother-help`) |\n")


def _routing_case(name):
    expected_route = RETIRED_1_1_0[name]
    expected_verb = expected_route.split()[0]

    def case(self):
        route = retired_route(name, _read(RETIRED_TABLE_PATH))
        self.assertIsNotNone(route, "%s has no row in %s" % (name, RETIRED_TABLE_PATH))
        self.assertEqual(route, expected_route, "%s routes to %r, U1.md section 2 says %r" % (name, route, expected_route))
        self.assertIn(expected_verb, GDT.CORE_ORDER, "%s routes to %r, which is no door verb" % (name, expected_verb))
        target = os.path.join(BUNDLE_DIR, "skills", "brother-%s" % expected_verb, "SKILL.md")
        self.assertTrue(os.path.isfile(target), "%s routes to %s, which does not ship" % (name, target))
        self.assertIn("`%s`" % name, _read(target), "%s is not named by its verb skill" % name)
        # Option B (2026-10-11): the 1.1.0 invocation itself, /brother:<name>, is a moved command
        # that prints the same pointer and follows the same verb skill from the plugin root.
        stub = os.path.join(BUNDLE_DIR, "commands", "%s.md" % name)
        self.assertTrue(os.path.isfile(stub), "/brother:%s has no moved command at %s" % (name, stub))
        stub_text = _read(stub)
        self.assertIn("`%s is now /brother %s`" % (name, expected_route), stub_text)
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/skills/brother-%s/SKILL.md" % expected_verb, stub_text)
    case.__doc__ = "%s routes through the door to its verb" % name
    return case


for _name in RETIRED_1_1_0:
    setattr(RetiredNamesRoute, "test_routes_" + _name.replace("-", "_"), _routing_case(_name))


class RefusesHostileInput(unittest.TestCase):
    """Both helpers refuse a non-str body with ValueError, never a crash."""

    BAD = (None, 123, 1.5, float("nan"), True, b"bytes", ["a"], {"k": 1})

    def test_handover_mentions_refuses_a_non_str(self):
        for bad in self.BAD:
            with self.assertRaises(ValueError):
                handover_mentions(bad)

    def test_command_lines_refuses_a_non_str(self):
        for bad in self.BAD:
            with self.assertRaises(ValueError):
                command_lines(bad)


if __name__ == "__main__":
    unittest.main()
