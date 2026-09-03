#!/usr/bin/env python3
"""The entity layer: an ontology of THINGS, not a taxonomy of pages.

WHY THIS EXISTS. Benchmark row D14, measured 2026-08-30: no note declared an
entity, and every typed edge in the vault ran document to document. The type:
vocabulary is a taxonomy of PAGES (failure, finding, decision, session-log),
so nothing in the corpus could be said ABOUT a system, a project or a metric.
"What depends on the Kay Vault" had no answer that was not a text search over
documents, and a text search returns pages, never the thing itself.

THE SHAPE ON DISK, and why it rides inside ordinary notes. An entity is a
normal vault note (allowed type:, status:, a stable id) that ADDITIONALLY
declares `entity: <type>` in its frontmatter. No parallel store, no second
format: the graph gate, the id tooling and the catalog all keep working on
entity notes unchanged, because an entity note IS a note. The extra fields:

  entity: <type>       what kind of thing this is, from ENTITY_TYPES
  <relation>: [[x]]    a typed edge to ANOTHER ENTITY, from RELATIONS
  described_by: [[d]]  provenance: the document notes that describe this
                       thing. Deliberately separate from RELATIONS, because
                       an edge to a document is exactly what D14 says an
                       ontology is not, and mixing the two vocabularies is
                       how document edges would creep back in wearing a
                       relation's name.

AN UNKNOWN VALUE IS A FINDING, never a guess, copied from the authority
contract for the same reason: silently accepting `entity: servce` invents a
type nobody declared, and silently dropping it hides a real entity. Both are
wrong in different directions, so check names it and exits 1.

ZERO ENTITIES IS NO-DATA, never a pass. A vault with no entity layer has
nothing for this tool to verify, and reporting that as clean is the
absence-reads-as-success shape this estate's benchmark caught three times in
one evening. An empty layer exits 2 and says so.

QUERIES ANSWER ABOUT THE THING. `query --entity kay-vault` returns the
entity, its type, and its typed edges in both directions, each endpoint an
entity with its own type. It never returns a list of documents; the only
document paths it prints are labeled provenance.

Exit 0 clean or answered, 1 check findings, 2 NO-DATA (empty vault or no such
entity, matching the NO-DATA convention rather than an ordinary 1). Python
3.9 floor, standard library only, writes nothing anywhere.

CORPUS COVERAGE. The 10 real entity notes in this vault are not exercised by
any test here on purpose: a test that opens this checkout's own vault couples
the suite to machine state rather than to the code, the estate's own
recorded lesson. That coverage lives in the benchmark's D14 and D06 probes
instead, which read the corpus mechanically and are re-run against it, not
against a tempdir fixture.
"""
import argparse
import os
import re
import sys

ENTITY_TYPES = ("project", "system", "tool", "repository", "metric", "person-role")
RELATIONS = ("part_of", "depends_on", "measures", "derives_from", "hosted_in")
PROVENANCE = "described_by"
WIKILINK = re.compile(r"\[\[([^\]|#]+)")
SKIP_DIRS = {".git", ".trash", ".obsidian"}


def _frontmatter(text):
    """The YAML frontmatter block, or None. Mirrors ids.frontmatter: a note
    that opens with a bare "---" horizontal rule and never closes one (no
    second "\\n---") has no frontmatter at all, and that is a distinct state
    from an empty-but-real block, so it is None rather than a same-looking
    empty string."""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    return text[3:end]


def _field(block, name):
    m = re.search(r"(?m)^%s:\s*(.*)$" % re.escape(name), block or "")
    return m.group(1).strip() if m else None


def _field_values(block, name):
    """Raw text carrying a frontmatter field's targets, covering both the
    inline scalar form (`name: [[x]]`) and the YAML list form Obsidian
    writes for a multi-value field:

        depends_on:
          - "[[x]]"
          - "[[y]]"

    _field alone only ever sees the empty string after "name:" in the list
    form, so every list-form edge was silently dropped, the exact failure
    the module docstring forbids. Returns text ready for _targets(), never
    None, so a missing field still yields no wikilinks rather than an error.
    """
    lines = (block or "").splitlines()
    pat = re.compile(r"^%s:\s*(.*)$" % re.escape(name))
    for i, line in enumerate(lines):
        m = pat.match(line)
        if not m:
            continue
        inline = m.group(1).strip()
        if inline:
            return inline
        # list form: gather indented "- ..." lines (and blanks between them)
        # directly below, stopping at the first line that is neither.
        rest = lines[i + 1:]
        cut = next((j for j, follow in enumerate(rest)
                    if not (re.match(r"^\s*-\s", follow) or follow.strip() == "")),
                   len(rest))
        return "\n".join(rest[:cut])
    return ""


def _targets(value):
    """Wikilink targets in a field value, reduced to their basename stem, so
    [[30-Entities/kay-vault]] and [[kay-vault]] name the same thing."""
    return [t.strip().split("/")[-1] for t in WIKILINK.findall(value or "")]


def walk(vault):
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            if fn.endswith(".md"):
                yield os.path.join(dirpath, fn)


def load(vault):
    """(entities, note_stems, findings).

    entities: {stem: {"path", "etype", "edges": {relation: [target stems]},
    "provenance": [target stems]}}. findings carry (relpath, problem) for a
    declared entity whose type is outside the vocabulary; the note is still
    excluded from the graph, visibly, rather than half-included."""
    entities, note_stems, findings = {}, set(), []
    for path in walk(vault):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:  # sbe: allow-silent unreadable note excluded from the graph; a real edge to it surfaces as dangling downstream
            continue
        rel = os.path.relpath(path, vault)
        stem = os.path.splitext(os.path.basename(path))[0]
        note_stems.add(stem)
        block = _frontmatter(text)
        etype = _field(block, "entity")
        if etype is None:
            continue
        etype = etype.strip().strip('"').strip("'")
        if etype not in ENTITY_TYPES:
            findings.append((rel, "unknown entity type %r, not in %s"
                             % (etype, "/".join(ENTITY_TYPES))))
            continue
        edges = {}
        for relation in RELATIONS:
            hits = _targets(_field_values(block, relation))
            if hits:
                edges[relation] = hits
        entities[stem] = {"path": rel, "etype": etype, "edges": edges,
                          "provenance": _targets(_field_values(block, PROVENANCE))}
    return entities, note_stems, findings


def incoming(entities, stem):
    """[(source stem, relation)] for every typed edge pointing AT stem."""
    hits = []
    for src, ent in sorted(entities.items()):
        for relation, targets in sorted(ent["edges"].items()):
            if stem in targets:
                hits.append((src, relation))
    return hits


def cmd_check(vault):
    entities, note_stems, findings = load(vault)
    if not entities and not findings:
        print("NO-DATA: no note in %s declares an entity, so there is no "
              "entity layer to check. Absence is not a pass." % vault)
        return 2
    edge_count = 0
    linked = set()
    for stem, ent in sorted(entities.items()):
        for relation, targets in sorted(ent["edges"].items()):
            for target in targets:
                edge_count += 1
                if target not in entities:
                    kind = ("a document, not an entity" if target in note_stems
                            else "nothing in the vault")
                    findings.append((ent["path"], "%s -> [[%s]] points at %s: "
                                     "relations run entity to entity only"
                                     % (relation, target, kind)))
                else:
                    linked.add(stem)
                    linked.add(target)
        for target in ent["provenance"]:
            if target not in note_stems:
                findings.append((ent["path"], "described_by [[%s]] resolves to "
                                 "no note" % target))
    for stem in sorted(set(entities) - linked):
        findings.append((entities[stem]["path"],
                         "isolated: no typed edge in either direction, so "
                         "nothing can be said about it through the graph"))
    by_type = {}
    for ent in entities.values():
        by_type[ent["etype"]] = by_type.get(ent["etype"], 0) + 1
    print("vault: %s" % vault)
    print("entities: %d (%s)" % (len(entities),
          ", ".join("%s=%d" % kv for kv in sorted(by_type.items())) or "none"))
    print("typed edges: %d" % edge_count)
    if findings:
        print("FINDINGS: %d" % len(findings))
        for rel, problem in findings:
            print("  %s: %s" % (rel, problem))
    return 1 if findings else 0


def cmd_query(vault, name, relation=None):
    entities, _, findings = load(vault)
    if not entities:
        if findings:
            print("findings (excluded from the entity layer):", file=sys.stderr)
            for rel, problem in findings:
                print("  %s: %s" % (rel, problem), file=sys.stderr)
        print("NO-DATA: no entity layer in %s" % vault)
        return 2
    stem = name.strip().split("/")[-1]
    if stem not in entities:
        print("NO-DATA: no entity named %r. Known: %s"
              % (name, ", ".join(sorted(entities))))
        return 2
    ent = entities[stem]
    print("%s (%s)" % (stem, ent["etype"]))
    shown = 0
    for rel_name, targets in sorted(ent["edges"].items()):
        if relation and rel_name != relation:
            continue
        for target in targets:
            other = entities.get(target)
            print("  %s -%s-> %s (%s)" % (stem, rel_name, target,
                                          other["etype"] if other else "?"))
            shown += 1
    for src, rel_name in incoming(entities, stem):
        if relation and rel_name != relation:
            continue
        print("  %s (%s) -%s-> %s" % (src, entities[src]["etype"], rel_name, stem))
        shown += 1
    if not shown:
        print("  no typed edge%s" % (" of relation %r" % relation if relation else ""))
    if ent["provenance"]:
        print("  provenance: %s" % ", ".join(ent["provenance"]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("check", "query"))
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    ap.add_argument("--entity", help="for query: the entity to answer about")
    ap.add_argument("--relation", choices=RELATIONS,
                    help="for query: show only this relation")
    args = ap.parse_args(argv)
    if not args.vault or not os.path.isdir(args.vault):
        print("bm_vault_entity: NO-DATA, no readable vault at %r" % args.vault,
              file=sys.stderr)
        return 2
    if args.command == "check":
        return cmd_check(args.vault)
    if not args.entity:
        print("bm_vault_entity: query needs --entity", file=sys.stderr)
        return 2
    return cmd_query(args.vault, args.entity, args.relation)


if __name__ == "__main__":
    sys.exit(main())
