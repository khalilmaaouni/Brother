# Dossier templates

Copy these into `design/<project>/` at the start of an engagement, then run
`python3 tools/sbe_intake.py design/<project>` there: the tier it computes
decides which templates are required, and without its `00-intake.json` the
design check FAILs the directory by name. `sbe_design.py` checks completeness:
run it advisory while you work, and in CI with `--strict` to block a merge.

This directory itself carries a `.sbe-exempt` file. Seven dossier-shaped files in
one directory ARE a dossier as far as the walk is concerned, and these are the
templates rather than anybody's design, so the exemption says that in words the
report prints on every run. An exemption nobody can see is not one this project
will ship.

Order: purpose, process, architecture decision, technology map, data model,
diagrams, verification plan. Each is approved before the next begins.

## The unfilled marker

Every template carries one `SBE-TEMPLATE-UNFILLED` comment under its title. While
it is there, `sbe_design.py placeholder` FAILs and names the file. Delete it when
the section is your own design.

The check matches the marker inside an HTML comment, which is how the templates
ship it. A verification plan that legitimately CITES the marker ("07 asserts no
file still contains SBE-TEMPLATE-UNFILLED") is not called unedited, because a
plain substring match called it that, in the false words "still the shipped
template, unedited".

That is deliberate. The example content is a coherent order-fulfilment system, and
it passes the other four checks as written, so without the marker the fastest route
to a green `--strict` run was to copy seven files describing someone else's system
and change nothing. A copied, unedited dossier is not a design, and the gate now
says so instead of blessing it.

```
$ cp templates/dossier/*.md design/my-project/
$ python3 tools/sbe_design.py placeholder design/my-project
BROTHERSBE DESIGN CHECKS  (advisory unless --strict; NO-DATA is never a pass; WAIVED is not a pass either)
  placeholder FAIL     still the shipped template, unedited: 01-purpose.md, 02-process.md, 03-adr.md, 04-technology-map.md, 05-data-model.md, 06-diagrams.md, 07-verification.md; each carries its SBE-TEMPLATE-UNFILLED marker comment, which the template says to delete once the section is your own design
```
