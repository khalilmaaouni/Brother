#!/usr/bin/env python3
"""Turn a session's recorded lessons into vault notes the archive can search.

WHY THIS EXISTS. A lesson written only into a session log is findable by whoever
already knows it happened. The vault's architecture makes a note findable by
SYMPTOM: `tools/bm_vault_catalog.py bake` reads the frontmatter of every note
under 40-Failures and generates Failures-by-Symptom.md from the `symptom` line.
So a lesson is searchable exactly when it carries a symptom phrased as what a
reader would OBSERVE, not as what the cause turned out to be. Prose in a
handover cannot do that; a note with frontmatter can.

The source of truth is docs/wisdom/lessons.json in this repository, so the
lessons are versioned, reviewable in a pull request, and re-emittable. This
script only projects them into the vault. It never invents one.

IDEMPOTENT BY CONSTRUCTION. A note whose body already matches is left alone and
reported as unchanged, so running this twice is a no-op and running it after
editing one lesson rewrites only that lesson. It refuses to overwrite a note it
did not write (one lacking the generator marker) unless --force is given, because
a hand-written note in the same directory is somebody's work.

NO-DATA IS NEVER A PASS. A run that found no vault, or an empty lessons file,
says so and exits non-zero rather than reporting success over nothing.
"""
import argparse
import json
import os
import pathlib
import sys

MARKER = "generated-by: scripts/wisdom_capture.py"
REQUIRED = ("name", "symptom", "description", "body")


def vault_root(explicit=None):
    """The vault, from the flag, then the environment, then the documented default."""
    for candidate in (explicit, os.environ.get("BROTHERSBE_VAULT")):
        if candidate:
            return pathlib.Path(os.path.expanduser(candidate))
    return pathlib.Path(os.path.expanduser("~/Documents/Kay Vault"))


def render(lesson, created, project):
    """One note, in the exact frontmatter shape bake already reads."""
    tags = ", ".join(lesson.get("tags") or ["lesson"])
    verified = lesson.get("verified_by", "").strip()
    return (
        "---\n"
        "type: failure\n"
        f"project: {project}\n"
        "status: standing\n"
        f"created: {created}\n"
        f"tags: [{tags}]\n"
        f"verified-by: {verified}\n"
        f"description: {lesson['description'].strip()}\n"
        f"symptom: {lesson['symptom'].strip()}\n"
        f"{MARKER}\n"
        "---\n\n"
        f"# {lesson.get('title') or lesson['name'].replace('-', ' ').capitalize()}\n\n"
        f"{lesson['body'].strip()}\n"
    )


def validate(lessons):
    """Every field bake depends on must be present and non-empty, named if not."""
    problems = []
    seen = set()
    for i, lesson in enumerate(lessons):
        for field in REQUIRED:
            if not str(lesson.get(field, "")).strip():
                problems.append("lesson %d: %r is missing or empty" % (i, field))
        name = lesson.get("name", "")
        if name in seen:
            problems.append("lesson %d: duplicate name %r" % (i, name))
        seen.add(name)
    return problems


def capture(lessons, root, created, project, force=False):
    """Write each lesson, reporting written, unchanged, or refused, never a bare count."""
    target = root / "40-Failures"
    results = []
    for lesson in lessons:
        path = target / ("%s.md" % lesson["name"])
        body = render(lesson, created, project)
        if path.exists():
            existing = path.read_text()
            if existing == body:
                results.append(("unchanged", lesson["name"]))
                continue
            if MARKER not in existing and not force:
                results.append(("REFUSED-hand-written", lesson["name"]))
                continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        results.append(("written", lesson["name"]))
    return results


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lessons", default="docs/wisdom/lessons.json")
    ap.add_argument("--vault", default=None)
    ap.add_argument("--created", required=True, help="YYYY-MM-DD, passed in so a rerun is reproducible")
    ap.add_argument("--project", default="brother")
    ap.add_argument("--force", action="store_true", help="overwrite notes this script did not write")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    src = pathlib.Path(args.lessons)
    if not src.exists():
        print("wisdom-capture: NO-DATA: %s does not exist; nothing was read" % src)
        return 2
    lessons = json.loads(src.read_text()).get("lessons", [])
    if not lessons:
        print("wisdom-capture: NO-DATA: %s carries no lessons; a run that wrote "
              "nothing is not a clean run" % src)
        return 2

    problems = validate(lessons)
    if problems:
        for p in problems:
            print("wisdom-capture: REFUSED: %s" % p)
        print("wisdom-capture: %d problem(s); bake reads these fields, so a note "
              "missing one is a note nobody can find" % len(problems))
        return 1

    root = vault_root(args.vault)
    if not root.exists():
        print("wisdom-capture: NO-DATA: no vault at %s; set BROTHERSBE_VAULT or "
              "pass --vault" % root)
        return 2

    if args.dry_run:
        for lesson in lessons:
            print("wisdom-capture: would write %s.md" % lesson["name"])
        print("wisdom-capture: dry run, %d lesson(s), nothing written" % len(lessons))
        return 0

    results = capture(lessons, root, args.created, args.project, args.force)
    for verdict, name in results:
        print("wisdom-capture: %-20s %s" % (verdict, name))
    refused = [n for v, n in results if v.startswith("REFUSED")]
    written = [n for v, n in results if v == "written"]
    print("wisdom-capture: %d written, %d unchanged, %d refused"
          % (len(written), len(results) - len(written) - len(refused), len(refused)))
    # The remedy MUST name a command the reader is allowed to run. Pointing at
    # BrotherModeUp's own copy is the obvious phrasing and it is wrong: this
    # estate's write guard refuses a Brother session running another project's
    # tool, so that instruction is unexecutable for exactly the reader who sees
    # it. Third recorded instance of a control naming a recovery its reader
    # cannot perform, which is why this line names the shared snapshot instead.
    print("wisdom-capture: these are not searchable yet. COMMIT them, then bake, "
          "then commit the index, in that order, because the baker reads HEAD "
          "rather than the working tree:")
    print("  BM_TOOLS=\"$HOME/.claude/vault-tools\" python3 "
          "\"$HOME/.claude/vault-tools/tools/bm_vault_catalog.py\" bake")
    print("wisdom-capture: that path is the shared snapshot on purpose. The "
          "BrotherModeUp copy is the same tool, and a Brother session is refused "
          "when it runs it, so naming that one would print a fix nobody here can "
          "execute.")
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
