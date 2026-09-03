"""BrotherMode V2 library package.

WHY THIS PACKAGE EXISTS NEXT TO tools/
  The modules under tools/ are deliberately flat, path-loaded scripts (see
  pyproject.toml, choice 2): each one is a founder-facing CLI or hook. This
  package is the other half of the codebase: importable library code with no
  command line, starting with the canonical project protocol schema
  (docs/specs/canonical-project-protocol.md). Library code wants a normal
  import name, and a package gives it one without touching the loader the
  tools use.

Python 3.9, standard library only. No network. No subprocess.

No em or en dashes anywhere in this file, its comments, or its output.
"""
