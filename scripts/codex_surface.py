#!/usr/bin/env python3
"""Generate the shipped skill surface: six verb skills and the door's references.

ONE DOOR, SEVEN SKILLS (release 1.1.1, docs/plan/specs/U1.md). The owner
withdrew C3 and C4 on 2026-10-10: one plugin only, no product installable
alone, and the 1.1.0 skill names are no longer frozen. The shipped surface is
`using-brother` (hand written, the door) plus six generated verb skills,
`brother-<verb>` for start, status, next, review, deliver and help. Every
host reads the same seven files under bundle/skills: Claude Code and Cursor
directly, Codex through the frontmatter stripped mirror codex_skills.py
writes, Antigravity through a symlink.

EVERY 1.1.0 NAME STILL ROUTES, for one release. The 48 skill names v1.1.0
shipped beside the door (15 brotherme-*, 19 brothermode-*, 14 brothersbe-*,
read from `git ls-tree origin/main bundle/skills` at that tag) are frozen in
RETIRED below with the verb each one routes to. They are written three
times: as the table bundle/skills/using-brother/references/retired-names.md,
which the door looks a name up in; as the "1.1.0 names that route here" list
at the foot of each verb skill; and as one MOVED COMMAND STUB each under
bundle/commands/<name>.md, so the 1.1.0 invocation `/brother:<name>` itself
still works on Claude Code (option B, orchestrator decision 2026-10-11 under
the owner's ruling: a supported entry point is not broken without his
explicit exception). A stub prints the one line pointer and follows the verb
skill with the arguments passed through; its description opens with MOVED so
surface_budget.py counts it as a pointer, never a second route. The stubs
are the cost of the ruling: the slash menu lists them beside the seven
skills until they retire in the next release. A name whose verb is not in
VERBS, or a name listed twice, refuses to generate.

WHAT THE RETIRED NAMES DID. 46 of the 48 were generated stubs whose whole
instruction was to run the engine with the outcome and read the receipt;
every verb skill carries that instruction, so routing the name to its verb is
the behaviour it had. The two Cursor skills carried real harness
instructions, so their bodies ship verbatim as references beside the door
(REAL_CONTENT_REFERENCES), where host_projection_parity.py keeps checking
them against their canonical source under products/.

GENERATED, NEVER HAND EDITED. `--check` exits 1 when any generated file is
missing or stale, when a file carrying MARKER is no longer expected, or when
a 1.1.0 name still ships as a skill directory. Python 3.9, standard library.
"""
import argparse
import re
from pathlib import Path

MARKER = "<!-- generated-codex-surface: v1 -->"
DOOR = "using-brother"
#: Paths below are relative to bundle/.
SKILLS = "skills"
COMMANDS = "commands"
REFERENCES = SKILLS + "/" + DOOR + "/references"
RETIRED_TABLE = REFERENCES + "/retired-names.md"
#: What a moved command's description opens with. surface_budget.py reads
#: it to tell a pointer from a route; a stub is never a second route.
MOVED = "Moved: now /brother "
#: Words the door passes with the verb for a 1.1.0 name whose meaning the
#: bare verb would invert (review B2, 2026-10-11: stop is not start).
RETIRED_WORDS = {"brotherme-stop": "stop", "brothermode-stop": "stop",
                 "brothermode-cursor-dispatch": "dispatch"}
#: The plugin root as a skill or command names it, with the vendor neutral
#: alternative the portability rule requires beside it.
ROOT_VAR = "${CLAUDE_PLUGIN_ROOT}"
ROOT_ALT = "`${BROTHER_PLUGIN_ROOT}` under Codex, `${PLUGIN_ROOT}` under Cursor"

#: The six verbs, in the door's order. Each entry: (verb, description, body).
#: The description is the frontmatter a host lists; the body is the
#: instruction a host reads once the skill is invoked.
VERBS = (
    ("start",
     "Use when someone wants to begin, resume or stop checked work: a project, an outcome, a change "
     "someone else must later trust, or an incident that is broken right now. Writes the outcome contract "
     "and runs the first check through the engine; never asks for a product name, an autonomy code, a plan "
     "format or a run id. Invoke as /brother start, or by this skill's name where there is no slash command.",
     "Begin, resume or stop checked work. Bare, with unfinished work in this repository, resume it by its "
     "plain language name (the door's Step 1 finds it); with nothing unfinished, take the outcome the person "
     "wrote and start. An incident (down, outage, hotfix) starts in assurance mode with the incident named "
     "verbatim. Stop means the running controller drains in flight work and releases every held claim; say "
     "what was released. The person answers at most one blocking question and never chooses an internal "
     "mode: execution provenance for a change someone must later accept, assurance when the work touches "
     "money, personal data, auth, a migration, a production path or a figure reaching a decision. "
     "`/brother start stop` ENDS the running controller run rather than starting one. A run has one "
     "driver, so a stop must speak as that driver: read the driver's session id from `python3 "
     "\"${BROTHER_PLUGIN_ROOT}/runtime/hooks/brothermode/tools/bm_controller.py\" status --project "
     "<project id> --json --raw` (the `run.session_id` field; `${CLAUDE_PLUGIN_ROOT}` under Claude Code), "
     "then run `python3 \"${BROTHER_PLUGIN_ROOT}/runtime/hooks/brothermode/tools/bm_controller.py\" stop "
     "--project <project id> --controller-id <controller id> --session-id <driver session id> --actor-name "
     "<your name>`. Without `--session-id` the command speaks as a fresh session and is refused (\"a run "
     "has one driver\"). If that driver is gone or the takeover is deliberate, first `python3 "
     "\"${BROTHER_PLUGIN_ROOT}/runtime/hooks/brothermode/tools/bm_controller.py\" adopt --project "
     "<project id> --session-id <your session id> --actor-name <your name>`, then stop with that same "
     "`--session-id`. The stop drains any in flight unit, releases every held claim and reports the state "
     "it moved from and to; open work left behind is still owed. A vague ask first becomes a measurable "
     "specification with acceptance criteria (the outcome contract), never a build. `/brother start "
     "dispatch` writes a packet for Cursor to execute, following "
     "`${CLAUDE_PLUGIN_ROOT}/skills/using-brother/references/cursor-dispatch.md`; it REPLACES the engine "
     "run for that work and does not run brother_run.py."),
    ("status",
     "Use when someone asks where things stand, what happened, what a run did, what it cost, or what is "
     "waiting on them. Reads the run's receipt and answers in plain language with PASS, FAIL and NO-DATA kept "
     "distinct, never from memory. Invoke as /brother status, or by this skill's name where there is no slash "
     "command.",
     "Where things stand, read from evidence. \"Show me what happened\" is this verb: run the engine's own "
     "discovery, `brother_run.py --continue --cwd <repo>`, which names the unfinished outcome or prints `no "
     "unfinished run found`, then open the newest receipt the engine wrote under the runs root (its last line "
     "names the path, `brother_run: receipt: <path>`) and report every "
     "entry: the file, the check, the exit code that decided it. There is no second run database and no "
     "second registry; the receipt is the record. A missing receipt is NO-DATA, said as such, never a pass. "
     "Never show a run id or a run directory; name the outcome. The page that shows where a project "
     "stands is written by `python3 \"${BROTHER_PLUGIN_ROOT}/runtime/hooks/brothermode/tools/bm_view.py\"` "
     "(`${CLAUDE_PLUGIN_ROOT}` under Claude Code) from the project's own records; offer it, never retype it."),
    ("next",
     "Use when someone asks what to do next, which decision is waiting on them, or which packet to pick up. "
     "Returns exactly one recommended action with a one sentence reason, never a menu. Invoke as /brother "
     "next, or by this skill's name where there is no slash command.",
     "The one recommended action. Read the state the receipt and the repository show, then name one action "
     "and why it is first; a decision waiting on the person is shown highest stakes first with a recommended "
     "option. Never a list of options with no ranking. Inside Cursor, the next action may be the next harness "
     "packet: `${CLAUDE_PLUGIN_ROOT}/skills/using-brother/references/cursor-execute.md` carries those "
     "instructions."),
    ("review",
     "Use when someone asks to review, verify, check or prove a change before it ships or before it is called "
     "done: a diff, a pull request, a migration, a number, a design, a contract, a production path. Judges the "
     "work against its bar and reports PASS, FAIL or NO-DATA per check with the evidence read. Invoke as "
     "/brother review, or by this skill's name where there is no slash command.",
     "Judge the work against its bar. Ceremony scales with risk, read from the work: a typo fix gets the "
     "definition of done and nothing else; a migration, a money or partner path, personal data, auth, a "
     "production path or a figure about to be claimed true runs the five assurance gates (numbers, migration, "
     "approval, ran, proof), each answering PASS, FAIL or NO-DATA on its own evidence. A gate with nothing to "
     "read is NO-DATA and never a pass. Report what passes, what does not, and the one next action. A design "
     "review judges the shape of a system before it is built: what it is for, how the process runs, the "
     "architecture and the data model, each against the law for that phase and nothing else."),
    ("deliver",
     "Use when work is finished and must close with evidence in hand: a delivery summary, a handover to "
     "another person, taking a decision back, or turning a lesson into a rule. Reads the receipt and refuses "
     "to call anything done without it. Invoke as /brother deliver, or by this skill's name where there is no "
     "slash command.",
     "Close with evidence in hand. Package the finished work into one delivery summary built from the "
     "receipt: every changed file, every check with its command and exit code. A handover names the receiver "
     "and what they accept or reject; a handback returns a decision and its work to the person with nothing "
     "lost; a lesson becomes a shared rule with its symptom written as what a reader would observe. The "
     "handover pages another person takes a project over from are written by `python3 "
     "\"${BROTHER_PLUGIN_ROOT}/runtime/hooks/brothermode/tools/bm_handover.py\"` (`${CLAUDE_PLUGIN_ROOT}` "
     "under Claude Code) from the records, never by hand. No receipt, or a refused entry, is a NOT DONE "
     "report."),
    ("help",
     "Use when someone asks what Brother does, how to use it, whether the install is healthy, or whether a "
     "newer release exists. Explains in plain language and checks the install; never prints a menu of skills. "
     "Invoke as /brother help, or by this skill's name where there is no slash command.",
     "Orient in plain language: Brother turns a plain language outcome into checked work with a rerunnable "
     "receipt, and the door is `/brother <what you are trying to do>` (on a host without slash commands, the "
     "`using-brother` skill). Say which verb fits the question asked, never a menu of all of them. A doubt "
     "about the install is answered by checking it with the install doctor, `python3 "
     "\"${BROTHER_PLUGIN_ROOT}/runtime/hooks/brothermode/tools/brothermode_cli.py\" doctor` (the eleven "
     "install health checks; `${CLAUDE_PLUGIN_ROOT}` under Claude Code), and relaying its verdicts. A "
     "version question is answered from the "
     "installed manifest, never from memory."),
)

#: Every skill name v1.1.0 shipped beside the door, with the verb it routes
#: to. Frozen from the v1.1.0 tree, never recomputed from products/.
RETIRED = (
    ("brotherme-auto", "start"),
    ("brotherme-auto-status", "status"),
    ("brotherme-brief", "status"),
    ("brotherme-decisions", "next"),
    ("brotherme-deliver", "deliver"),
    ("brotherme-handback", "deliver"),
    ("brotherme-handover-pack", "deliver"),
    ("brotherme-help", "help"),
    ("brotherme-next", "next"),
    ("brotherme-review", "review"),
    ("brotherme-start", "start"),
    ("brotherme-status", "status"),
    ("brotherme-stop", "start"),
    ("brotherme-update", "help"),
    ("brotherme-view", "status"),
    ("brothermode-auto", "start"),
    ("brothermode-auto-status", "status"),
    ("brothermode-brief", "status"),
    ("brothermode-brotherme", "help"),
    ("brothermode-cursor-dispatch", "start"),
    ("brothermode-cursor-execute", "next"),
    ("brothermode-decisions", "next"),
    ("brothermode-deliver", "deliver"),
    ("brothermode-doctor", "help"),
    ("brothermode-handback", "deliver"),
    ("brothermode-handover-pack", "deliver"),
    ("brothermode-help", "help"),
    ("brothermode-next", "next"),
    ("brothermode-review", "review"),
    ("brothermode-start", "start"),
    ("brothermode-status", "status"),
    ("brothermode-stop", "start"),
    ("brothermode-update", "help"),
    ("brothermode-view", "status"),
    ("brothersbe-adopt", "start"),
    ("brothersbe-design", "review"),
    ("brothersbe-handover", "deliver"),
    ("brothersbe-help", "help"),
    ("brothersbe-kickoff", "start"),
    ("brothersbe-learn", "deliver"),
    ("brothersbe-next", "next"),
    ("brothersbe-prove-this-change", "review"),
    ("brothersbe-review", "review"),
    ("brothersbe-spec-and-data-prep", "start"),
    ("brothersbe-start", "start"),
    ("brothersbe-status", "status"),
    ("brothersbe-verify", "review"),
    ("brothersbe-work", "start"),
)

#: The two 1.1.0 skills that carried real harness instructions rather than an
#: engine stub. Their bodies ship verbatim as references beside the door.
#: Keyed by the reference file stem; value is (product, skill dir).
REAL_CONTENT_REFERENCES = {
    "cursor-dispatch": ("brothermode", "cursor-dispatch"),
    "cursor-execute": ("brothermode", "cursor-execute"),
}

#: Which product's skill folder described a retired name in 1.1.0, so the
#: table's "what it did" column is that skill's own description.
_SOURCE_PRODUCT = {"brotherme": "brothermode", "brothermode": "brothermode",
                   "brothersbe": "brothersbe"}


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


def retired_verb(name):
    """The verb a 1.1.0 name routes to, or None when the name is not one."""
    return dict(RETIRED).get(name)


def retired_route(name):
    """The words after `/brother` for a 1.1.0 name: the verb, plus the words
    in RETIRED_WORDS when the bare verb would say the wrong thing."""
    verb = retired_verb(name)
    if verb is None:
        return None
    words = RETIRED_WORDS.get(name)
    return "%s %s" % (verb, words) if words else verb


def _validate():
    verbs = [v for v, _d, _b in VERBS]
    if len(set(verbs)) != len(verbs):
        raise ValueError("a verb is listed twice in VERBS")
    names = [n for n, _v in RETIRED]
    if len(set(names)) != len(names):
        raise ValueError("a 1.1.0 name is listed twice in RETIRED")
    for name, verb in RETIRED:
        if verb not in verbs:
            raise ValueError("%s routes to %r, which is no verb" % (name, verb))
        if name.split("-", 1)[0] not in _SOURCE_PRODUCT:
            raise ValueError("%s carries no known 1.1.0 prefix" % name)
    for stem, (product, skill_dir) in REAL_CONTENT_REFERENCES.items():
        if retired_verb("%s-%s" % (product, skill_dir)) is None:
            raise ValueError("%s mirrors %s-%s, which is no 1.1.0 name" % (stem, product, skill_dir))
    for name in RETIRED_WORDS:
        if retired_verb(name) is None:
            raise ValueError("%s carries route words but is no 1.1.0 name" % name)


def _what_it_did(root, name):
    """The 1.1.0 skill's own description, first sentence, from the product
    skill folder that generated it. A missing folder is an error: the table
    would otherwise print an empty column as if the name did nothing."""
    prefix, skill_dir = name.split("-", 1)
    path = root / "products" / _SOURCE_PRODUCT[prefix] / "skills" / skill_dir / "SKILL.md"
    if not path.is_file():
        raise ValueError("%s: no source skill at %s" % (name, path))
    fields, _ = split_frontmatter(path.read_text(encoding="utf-8"))
    text = re.sub(r"\s+", " ", fields.get("description", "")).strip()
    if not text:
        raise ValueError("%s: %s carries no description" % (name, path))
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    if len(first) > 140:
        first = first[:137].rstrip() + "..."
    return first.replace("|", ",")


def render_skill(verb, description, body, root):
    """One verb skill. The frontmatter is what a host lists; the body is read
    on invocation; the foot names every 1.1.0 name that routes here."""
    routed = [name for name, v in RETIRED if v == verb]
    foot = "\n".join(
        "- `%s`: %s%s" % (name, _what_it_did(root, name),
                          " (say `/brother %s`)" % retired_route(name) if name in RETIRED_WORDS else "")
        for name in routed)
    return (
        "---\nname: brother-%s\ndescription: \"%s\"\n---\n\n"
        "# brother %s\n\n%s\n\n"
        "This is one of the six verbs behind the one door. Under Claude Code and Cursor the door is "
        "`/brother <what you are trying to do>`, and the verb word is optional: the door's routing order in "
        "`%s/skills/using-brother/SKILL.md` decides (%s). Codex and Antigravity have no slash command "
        "surface: there this skill is reached by its name, or through the `using-brother` skill. Never ask "
        "the person to choose a product, an autonomy code, a plan format, a run id or a test framework.\n\n"
        "The engine owns execution, assurance and the receipt:\n\n"
        "```bash\npython3 \"${BROTHER_PLUGIN_ROOT}/runtime/brother_run.py\" \"<outcome>\" --cwd <repo>\n```\n\n"
        "(`${CLAUDE_PLUGIN_ROOT}` under Claude Code, `${PLUGIN_ROOT}` under Cursor.) Its last line is "
        "`brother_run: receipt: <path>`; open that receipt and report every entry before claiming anything. "
        "Keep PASS, FAIL and NO-DATA distinct.\n\n"
        "## 1.1.0 names that route here\n\n%s\n\n"
        "A request opening with one of them gets one line, `<old name> is now /brother %s` (with the words "
        "the table gives it), then this verb with the rest of the words. The whole table: "
        "`%s/skills/using-brother/references/retired-names.md`.\n"
        "%s\n" % (verb, description, verb, body, ROOT_VAR, ROOT_ALT, foot, verb, ROOT_VAR, MARKER)
    )


def render_stub(name, verb, root):
    """One moved command, bundle/commands/<name>.md: the 1.1.0 invocation
    `/brother:<name>` prints the pointer and routes exactly as the door does
    for that verb, arguments passed through. The description is the shortest
    the host accepts, since every command's frontmatter is loaded into every
    session."""
    mode = "assurance" if name.startswith("brothersbe-") else "execution"
    route = retired_route(name)
    words = RETIRED_WORDS.get(name)
    reference = ""
    for stem, (product, skill_dir) in REAL_CONTENT_REFERENCES.items():
        if name == "%s-%s" % (product, skill_dir):
            reference = (" Its instructions ship as `%s/skills/using-brother/references/%s.md`; read them."
                         % (ROOT_VAR, stem))
    return (
        "---\ndescription: \"%s%s\"\n---\n\n"
        "`%s` is a 1.1.0 name. Say exactly one line, `%s is now /brother %s`, then follow "
        "`%s/skills/brother-%s/SKILL.md` (%s) in %s mode with %s`$ARGUMENTS` as the request (in 1.1.0 "
        "this name did: %s).%s\n%s\n"
        % (MOVED, route, name, name, route, ROOT_VAR, verb, ROOT_ALT, mode,
           "the word `%s` before " % words if words else "", _what_it_did(root, name), reference, MARKER)
    )


def render_retired_table(root):
    lines = [
        "# The 1.1.0 names and where each one routes",
        "",
        "Release 1.1.1 ships one door and six verbs (`brother-start`, `brother-status`, `brother-next`, "
        "`brother-review`, `brother-deliver`, `brother-help`). Every skill name 1.1.0 shipped still works "
        "for one release: typed as it was under Claude Code (`/brother:<old name>`, a moved command that "
        "prints the pointer and routes), as an argument to the door (`/brother <old name> <outcome>`), or "
        "as a word in the request under Codex and Antigravity. The door says one line, `<old name> is now "
        "/brother <verb>` (on a host without slash commands: `<old name> is now the brother-<verb> skill "
        "(in Claude Code: /brother <verb>)`), then routes as that verb with the words the Now column gives. "
        "A name with one of these prefixes that is not in this table is unknown: the door says so and asks "
        "its one question, never guessing a verb. This file is generated by the codex_surface generator in "
        "the Brother repository; regenerate it, never edit it.",
        "",
        "| 1.1.0 name | Now | Mode | What it did |",
        "|---|---|---|---|",
    ]
    for name, verb in RETIRED:
        mode = "assurance" if name.startswith("brothersbe-") else "execution"
        lines.append("| %s | `/brother %s` (`brother-%s`) | %s | %s |"
                     % (name, retired_route(name), verb, mode, _what_it_did(root, name)))
    lines += [
        "",
        "`brothermode-cursor-dispatch` and `brothermode-cursor-execute` carried real harness instructions; "
        "those ship beside this file as `cursor-dispatch.md` and `cursor-execute.md`.",
        MARKER,
        "",
    ]
    return "\n".join(lines)


def mirror_real(root, stem):
    """The verbatim body of a REAL_CONTENT_REFERENCES source skill, under a
    heading naming the 1.1.0 skill it was. No MARKER: the body is canonical
    product content, checked byte for byte by host_projection_parity.py."""
    product, skill_dir = REAL_CONTENT_REFERENCES[stem]
    source_path = root / "products" / product / "skills" / skill_dir / "SKILL.md"
    text = source_path.read_text(encoding="utf-8")
    fields, body = split_frontmatter(text)
    return "---\n%s\n---\n\n%s" % (
        "\n".join("%s: %s" % (k, v) for k, v in fields.items() if k != "name"), body)


def expected(root):
    """{relative path under bundle/: content}."""
    _validate()
    files = {}
    for verb, description, body in VERBS:
        files["%s/brother-%s/SKILL.md" % (SKILLS, verb)] = render_skill(verb, description, body, root)
    files[RETIRED_TABLE] = render_retired_table(root)
    for stem in sorted(REAL_CONTENT_REFERENCES):
        files["%s/%s.md" % (REFERENCES, stem)] = mirror_real(root, stem)
    for name, verb in RETIRED:
        files["%s/%s.md" % (COMMANDS, name)] = render_stub(name, verb, root)
    return files


def generated_paths(root):
    return root / "bundle"


def _candidates(dest):
    """Every file this generator may own: skill files, door references and
    command files (the hand written door, commands/brother.md, carries no
    MARKER and is never touched)."""
    return sorted(list(dest.glob(SKILLS + "/*/SKILL.md")) + list(dest.glob(REFERENCES + "/*.md"))
                  + list(dest.glob(COMMANDS + "/*.md")))


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
    for path in _candidates(dest):
        rel = path.relative_to(dest).as_posix()
        if rel not in expected_files and MARKER in path.read_text(encoding="utf-8"):
            path.unlink()
            changed.append("removed %s" % rel)
            if path.parent != dest and not any(path.parent.iterdir()):
                path.parent.rmdir()
    return changed


def check(root):
    dest = generated_paths(root)
    expected_files = expected(root)
    problems = []
    for rel, content in expected_files.items():
        path = dest / rel
        if not path.is_file():
            problems.append("bundle/%s: missing" % rel)
        elif path.read_text(encoding="utf-8") != content:
            problems.append("bundle/%s: stale" % rel)
    for path in _candidates(dest):
        rel = path.relative_to(dest).as_posix()
        if rel not in expected_files and MARKER in path.read_text(encoding="utf-8"):
            problems.append("bundle/%s: stale generated file" % rel)
    for name, _verb in RETIRED:
        if (dest / SKILLS / name / "SKILL.md").is_file():
            problems.append("bundle/skills/%s: a 1.1.0 name still ships as a skill; it routes "
                            "through the door now" % name)
    # Review note 3 (2026-10-11): any skill directory outside the seven,
    # marked or not, is drift, so an unrouted skill cannot slip in.
    retired = set(name for name, _verb in RETIRED)
    for path in sorted(dest.glob(SKILLS + "/*/SKILL.md")):
        rel = path.relative_to(dest).as_posix()
        if rel not in expected_files and path.parent.name not in retired and path.parent.name != DOOR:
            problems.append("bundle/%s: not one of the seven shipped skills" % rel)
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
        print("codex_surface: bundle carries %d verb skills, %d door references and %d moved commands, "
              "%d 1.1.0 names routed" % (len(VERBS), 1 + len(REAL_CONTENT_REFERENCES), len(RETIRED),
                                         len(RETIRED)))
        return 0
    changed = generate(root)
    print("codex_surface: wrote %d file(s)" % len(changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
