Status: CURRENT.

# Keeping the comparison page honest

The comparison page, `docs/ECOSYSTEM.md`, makes dated claims about other
people's products. Prices change, products get renamed, and one of them was
already deprecated and replaced under a nearly identical name before the page
was first written. A comparison page that decays becomes the thing it was
meant to avoid: a confident description of a landscape that no longer exists.

This is the procedure that keeps it true. It runs weekly.

## What decays fastest, in order

The order below is a judgement about volatility, not a measurement. Nobody has
tracked how often each of these actually changes, and after a few months of
running this procedure the record itself should decide the order.

1. **Prices.** None of the four vendor pricing pages read on 2026-08-11 carries
   a visible last-updated date, and two independent fetches disagreed about one
   vendor's tiers. Every price on the page is a snapshot stamped with the date
   it was read, never a stable citation. Treat every price as wrong until
   re-read.
2. **Product identity.** One tool in the set was deprecated on a stated date
   and replaced by a different product with almost the same name. Check that
   each named product still exists under that name and still means what the
   page says it means.
3. **Licences.** Less volatile, and higher consequence when wrong, because the
   page uses open source status as a real point of difference.
4. **The handoff seams.** File formats, plugin mechanisms and integration
   surfaces get added and removed, and they are what the "using them together"
   section is built from, so a change there invalidates that section rather
   than just a line of it.
5. **Our own claims about ourselves.** The page states where each competitor
   beats us. If we fix one of those weaknesses, the page must stop claiming it,
   and if we introduce a new one, the page must say so.

## The weekly pass

For each of the six tools:

1. Open its pricing page and its main documentation page. Actually open them.
   A search result summary is not a source, and the research this page rests on
   was explicit about the difference.
2. Compare what you read against what the page says. Where they differ, update
   the page and update the date stamp on that line.
3. Where a claim cannot be confirmed on a page you opened, mark it UNVERIFIED
   in the same sentence rather than leaving it looking checked. The page
   already carries three such labels, and they are a feature.
4. Where two sources disagree, say so on the page rather than picking the one
   you prefer.

Then, once:

5. Re-read the "where it beats BrotherMode" line for each tool and ask whether
   it is still true in both directions.
6. Update the "last checked" line at the foot of the page. That line is the
   page's honesty: a reader can see for themselves how stale it is.
7. Record what changed. A week where nothing changed is a real result and is
   worth writing down as one, because it tells you the page is stable rather
   than unread.

## The rule that makes this worth doing

Never soften a competitor's advantage during a refresh. The temptation arrives
exactly when a competitor ships something good, which is the moment the page
becomes most useful to a reader and least comfortable to maintain. A page that
quietly stops mentioning what a rival does well is worth less than no page at
all.

## What enforces this

Nothing yet, and that is stated rather than hidden. As of 2026-08-11 this
procedure is UNENFORCED: no check fails when the page goes stale.

Two ways to change that, both ratified in principle and neither built:

- A scheduled weekly agent that performs the pass above and reports what
  changed. This needs the founder's authorisation because it spends tokens on
  their account every week.
- A staleness check in the test suite that fails when the page's last-checked
  date is more than a stated number of days old. This costs nothing to run and
  catches the week the schedule fails, but it only fires when somebody is
  already working.

The honest recommendation is both, because they fail in different ways: the
schedule does the work and the check notices when the schedule does not.

Last checked: 2026-08-11.
