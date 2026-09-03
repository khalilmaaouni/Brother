# The intake artifact template

LOAD WHEN: building an intake or kickoff options page per
references/visual-surface.md rule 4 and references/kickoff.md's decision-card
step. This exists so that step is fast: the design (palette, type, layout) is
decided once, here, and every kickoff fills content into it rather than
re-deriving a design system per intake. Landed 2026-08-29 after a founder-
scored 1 of 5 on generation speed; see the vault note
`intake-options-default-to-terminal-text-instead-of-an-artifact`.

## Why a fixed template does not violate "avoid templated designs"

The artifact-design skill's caution against templated design targets
EDITORIAL work: landing pages, tools, anything a distinctive point of view is
being paid for. A kickoff plan is UTILITARIAN by that same skill's own read
of the request: "a plan, a memo, a demo." A memo that looks the same each
time is not a defect, it is a house style, the same reasoning that gives
Brother one progress-page skin (petrol/paper/slate, Iowan Old Style over
Seravek) rather than a fresh one per project. Reuse the skeleton below;
change only its content.

## The skeleton

`assets/intake-artifact-skeleton.html` in this same `references/` folder is
the literal HTML/CSS to copy. It already carries Brother's own design tokens
(the `:root` and dark-mode blocks), so no palette or type decision is needed
per use. Fill these slots, in order, and do not add new top-level sections
without updating this template once, here, rather than per intake:

1. `<title>` and the eyebrow line: name the thing being planned, plain noun
   phrase, 2 to 4 words.
2. The ask block: the user's own words, verbatim, in quotes.
3. One `.decision` block per decision (kickoff.md step 4: one at a time in
   conversation, but they accumulate into one artifact at the close). Each
   decision needs: a recommended option marked `chosen`, one or more
   alternatives, a `Pros`/`Cons` list per option (short, 1 line each), and a
   one-line `Why` naming the reason the recommendation wins. Never state a
   decision with only the recommended option shown: the artifact's whole job
   is letting the user see what was NOT picked and why.
4. One `<pre class="mermaid">` block per workflow or architecture the plan
   depends on. Every node is a real step, never decorative.
5. A code-references section: real file paths in the estate that this plan
   reuses or depends on, each with a one-line reason. Verify every path
   exists (`ls` it) before writing it in; a citation to a file that is not
   there is worse than no citation.
6. A research-references section: the actual documents this plan is built
   from (design docs, field surveys, prior decisions), each with a one-line
   reason. Same rule: verify the path first.
7. The forecast block, in references/forecasting.md's exact shape (range,
   token range, confidence, Why lines, Reforecast-after line).
8. One closing line naming the single next action and what unblocks it.

## Speed discipline

- Reuse the skeleton file's CSS verbatim. Do not re-derive colors or type.
- Do not reload the full artifact-design skill for a routine intake once
  this template is in hand; that skill's process (design plan, editorial
  risk-taking) is for work this template has already settled. Load it only
  when a request is genuinely editorial (a landing page, a tool, something
  with an audience beyond the founder reading a plan).
- Verify code and research references with one batched set of existence
  checks, not one read per reference.
- Say the narration line from references/kickoff.md before publishing, every
  time, even though the build itself is now fast: the line is about the
  user's wait, not about how long the wait actually is.
