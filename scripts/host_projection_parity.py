#!/usr/bin/env python3
"""host_projection_parity: WBS-70.02, proves the 3 real host-projection
generators (scripts/codex_surface.py: canonical product skills to the
umbrella's bundle/skills; scripts/codex_skills.py: bundle/skills to
bundle/codex-skills; scripts/codex_product_skills.py: an export's product
skills to an opt-in Codex product package) agree on the one invariant the
roadmap names for this architecture:

    Host projection may alter syntax/frontmatter. It may not alter:
    evidence semantics; authority; lifecycle; capability meaning.

WHAT "CAPABILITY MEANING" IS HERE, mechanically: the body of a SKILL.md
after its closing frontmatter delimiter. Every one of the 3 generators
rewrites frontmatter (name, description, disable-model-invocation) but
never edits the body: codex_skills.render() and codex_product_skills._adapt()
both pass the body straight through untouched, and codex_surface.py's
mirror_real() copies the source body verbatim for the skills it mirrors
rather than stubbing them. A body that differs across a projection boundary
is generator-introduced content drift, which the roadmap note forbids.

WHAT THIS CHECKS, on the live generated trees (read-only, writes nothing):

  1. bundle/skills/<name>/SKILL.md body == bundle/codex-skills/<name>/SKILL.md
     body, for every <name> present in both. This is the codex_skills.py
     projection boundary.
  2. For each entry in codex_surface.REAL_CONTENT_SKILLS, the
     bundle/skills body == the canonical products/<product>/skills/<dir>/
     SKILL.md body it claims to mirror. This is the codex_surface.py
     projection boundary, closing the loop back to the canonical capability
     the roadmap diagram names as the source node.

WHAT THIS DOES NOT CHECK: codex_product_skills.py's built export packages.
That generator writes into a caller-supplied output directory outside this
tree from a caller-supplied export root (see build() in that module), so
there is no committed instance of its output to compare here. That gap is
printed as NO-DATA rather than silently skipped or invented.

Python 3.9, standard library only. No network.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import codex_skills  # noqa: E402
import codex_surface  # noqa: E402

BUNDLE_SKILLS = os.path.join(REPO_ROOT, 'bundle', 'skills')
CODEX_SKILLS_DIR = os.path.join(REPO_ROOT, 'bundle', 'codex-skills')


def _body(path):
    """(body, problem). problem is None on success, body is None on
    failure."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
    except OSError as exc:
        return None, '%s: %s' % (path, exc)
    _fields, body = codex_skills.split_frontmatter(text)
    if body is None:
        return None, '%s: no closed frontmatter' % path
    return body, None


def check_codex_skills_boundary(bundle_skills=None, codex_skills_dir=None):
    """(problems, checked_count) for invariant 1. A missing tree is one
    NO-DATA problem, never zero problems standing in for zero coverage."""
    bundle_skills = BUNDLE_SKILLS if bundle_skills is None else bundle_skills
    codex_skills_dir = CODEX_SKILLS_DIR if codex_skills_dir is None else codex_skills_dir
    if not os.path.isdir(bundle_skills):
        return ['NO-DATA: %s not found' % bundle_skills], 0
    if not os.path.isdir(codex_skills_dir):
        return ['NO-DATA: %s not found' % codex_skills_dir], 0
    problems = []
    checked = 0
    for name in sorted(os.listdir(bundle_skills)):
        bundle_md = os.path.join(bundle_skills, name, 'SKILL.md')
        codex_md = os.path.join(codex_skills_dir, name, 'SKILL.md')
        if not os.path.isfile(bundle_md) or not os.path.isfile(codex_md):
            continue
        bundle_body, err1 = _body(bundle_md)
        codex_body, err2 = _body(codex_md)
        checked += 1
        if err1 or err2:
            problems.append(err1 or err2)
        elif bundle_body != codex_body:
            problems.append(
                '%s: body differs between bundle/skills and bundle/codex-skills, '
                'a frontmatter-only projection changed capability meaning' % name)
    return problems, checked


def check_real_content_boundary(repo_root=None, bundle_skills=None):
    """(problems, checked_count) for invariant 2."""
    repo_root = REPO_ROOT if repo_root is None else repo_root
    bundle_skills = BUNDLE_SKILLS if bundle_skills is None else bundle_skills
    problems = []
    checked = 0
    for canonical, (product, skill_dir) in sorted(
            codex_surface.REAL_CONTENT_SKILLS.items()):
        source_path = os.path.join(repo_root, 'products', product, 'skills',
                                    skill_dir, 'SKILL.md')
        bundle_md = os.path.join(bundle_skills, canonical, 'SKILL.md')
        if not os.path.isfile(source_path):
            problems.append('NO-DATA: %s not found' % source_path)
            continue
        if not os.path.isfile(bundle_md):
            problems.append('NO-DATA: %s not found' % bundle_md)
            continue
        source_body, err1 = _body(source_path)
        bundle_body, err2 = _body(bundle_md)
        checked += 1
        if err1 or err2:
            problems.append(err1 or err2)
        elif source_body != bundle_body:
            problems.append(
                '%s: body differs between the canonical product skill and its '
                'bundle/skills mirror, capability meaning changed' % canonical)
    return problems, checked


def run_check():
    """(exit_code, lines). Pure enough to drive from tests without going
    through main()'s argv handling."""
    problems1, checked1 = check_codex_skills_boundary()
    problems2, checked2 = check_real_content_boundary()
    all_problems = problems1 + problems2
    no_data = [p for p in all_problems if p.startswith('NO-DATA')]
    real_failures = [p for p in all_problems if not p.startswith('NO-DATA')]
    lines = list(all_problems)
    if no_data and not real_failures:
        lines.append('NO-DATA: %d generated tree(s) or source file(s) missing'
                      % len(no_data))
        return 2, lines
    if real_failures:
        lines.append('FAIL: %d body(ies) diverged across a host projection '
                      'boundary' % len(real_failures))
        return 1, lines
    lines.append(
        'PASS: %d bundle/skills<->codex-skills bodies identical, '
        '%d REAL_CONTENT_SKILLS mirror(s) identical to their canonical source'
        % (checked1, checked2))
    return 0, lines


def main(argv=None):
    del argv
    code, lines = run_check()
    for line in lines:
        print(line)
    return code


if __name__ == '__main__':
    sys.exit(main())
