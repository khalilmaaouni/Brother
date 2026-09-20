# How this repository was made, and what was deliberately left behind

This repository is a CLEAN EXTRACTION of the files of a private predecessor at commit
cb3393c, taken with `git archive` so that not one object of that repository's history
came with them. It is not a copy, not a fork, and not a scrubbed clone. That distinction
is the whole point: deleting a file from a repository does not remove its blobs, so the
only safe route from private to public is a fresh tree with no ancestry.

## What was excluded, and why

ONE FILE was excluded rather than published: a research sweep of a private knowledge vault
covering real client engagements. Every name in it had already been generalized to a role,
so every name-based scan passed it clean. It was excluded anyway, because it carried real
engagement FIGURES: order volumes, revenue amounts in yen, store counts, percentage
shares, buyer counts and an operational blackout window. A figure that only means
something with its owner attached is client content whether or not the owner is named, and
no name scan can see it.

That is the lesson worth keeping from this extraction: a clean private-terms scan is not
evidence that a document is publishable. Names are only one of the ways private content
travels, and figures are the way that survives anonymization.

The excluded file was not deleted from anywhere. The private predecessor repository still
holds it, untouched, which is where it belongs.

## What was changed

Five mentions of the host harness's product name were rewritten to "the host harness",
in the places where the sentence was describing this project's own plan, workflow or
plugin surface. Four mentions were KEPT as they were, in a competitive landscape survey
where named products with cited sources are the subject of the document: renaming a
surveyed product would destroy the research rather than protect anything. One passage
describing a first hour of use was generalized from a specific fiscal year and metric to
the archetype level.

## What was verified before this repository existed

The five delivery scans, run over this tree: secrets, assignment-shaped credentials,
attribution trailers, em and en dashes, and private terms under the length rule (terms of
five characters or fewer matched case-sensitively as whole words, longer terms matched
case-insensitively as substrings). The results are in the commit message of the first
commit, quoted rather than summarized.
