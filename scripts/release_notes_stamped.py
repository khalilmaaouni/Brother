"""release_notes_stamped.py: refuses while any shipped release note still
carries the exporter's placeholder stamp.

FINDING 2, a zero context auditor, 2026-09-02: the shipped docs/releases/0.9.11.md
still reads "Stamped by the exporter at release time." because the exporter's
own stamp_source_revision() could not fire on that cut (fixed for future cuts
already), and nothing refused a cut while a placeholder note stood. This is
the guard scripts/cut_v1.0.0.sh calls, after regenerating the 1.0.0 note and
before its tag step, so a placeholder note can never reach a public tag again.

The check is the exact block export_public.py's own stamp_source_revision()
seeds and replaces: SOURCE_REVISION_HEADER immediately followed by a blank
line, SOURCE_REVISION_PLACEHOLDER, and a blank line. A plain substring search
for the placeholder text would also flag a note that merely QUOTES the
placeholder while describing why an earlier note is unusable (docs/releases/
1.0.0.md's own "Source revision" section does exactly that about 0.9.11.md,
and is itself correctly stamped with a real hub commit); the block match
does not, because the quote sits inside a longer sentence, never right after
the header. Reads SOURCE_REVISION_HEADER and SOURCE_REVISION_PLACEHOLDER from
scripts/export_public.py itself, never retyped, so the two can never drift.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RELEASES_DIR = os.path.join(ROOT, "docs", "releases")

sys.path.insert(0, HERE)
import export_public as EXP  # noqa: E402

PLACEHOLDER_BLOCK = "%s\n\n%s\n\n" % (EXP.SOURCE_REVISION_HEADER,
                                       EXP.SOURCE_REVISION_PLACEHOLDER)


def offending_notes(releases_dir=None):
    """Every docs/releases/*.md file whose own Source revision section is
    still the unstamped placeholder block, as relative-to-releases-dir
    filenames, sorted. [] means every shipped note is stamped. An unreadable
    directory returns [] (nothing to refuse), matching how the release note
    generator treats a missing releases dir elsewhere in this tree. A file
    the directory lists but this cannot open counts as offending too: this
    gate cannot certify a note it cannot read, so it refuses rather than
    passing that file through in silence."""
    d = releases_dir if releases_dir is not None else RELEASES_DIR
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".md"))
    except OSError:
        return []
    bad = []
    for name in names:
        path = os.path.join(d, name)
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            # A note this gate cannot open is a note it cannot certify as
            # stamped, so it counts as offending too: refuse rather than
            # pass an unreadable file through in silence.
            bad.append(name)
            continue
        if PLACEHOLDER_BLOCK in text:
            bad.append(name)
    return bad


def main(argv=None):
    bad = offending_notes()
    if bad:
        print("REFUSED: the placeholder \"%s\" still stands, unreplaced, in: %s"
              % (EXP.SOURCE_REVISION_PLACEHOLDER, ", ".join(bad)), file=sys.stderr)
        return 1
    print("release notes stamped: no placeholder Source revision section in docs/releases/*.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
