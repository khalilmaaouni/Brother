# Intake: <project or feature name>

What this is: the problem-first gate this project or feature must pass before
any design or code starts. Who reads it: the founder deciding whether this is
worth building, and any AI session about to build it. Do not start building
until sections 9 and 11 (kill criteria and the sunset plan) are both filled
in. An empty kill criteria section is not a finished intake. It is a wish.

The full version of this template, with the reasoning behind each question
and a worked example filled end to end, lives at
`docs/INTAKE-TEMPLATE.md` in the BrotherMode repository, if you have access to
it. This copy is meant to stand on its own even if you do not: fill in every
section below in your own words.

## 1. The problem
<State the actual thing that is broken or missing right now, as an observed
fact, not a solution. "We need a better X" is a solution wearing a problem's
clothes.>

## 2. Who has it, and how you know
<Name real people, a specific number, or a specific event. Not "some users."
If you cannot say how you found out, you may be inventing the problem.>

## 3. The why
<One plain sentence: why do those specific people need this now, not
eventually?>

## 4. The why behind that why
<Ask why again. What deeper goal does answering the first why actually serve?
If this just restates section 3, go deeper.>

## 5. What happens if nobody does this
<The actual cost of doing nothing, in a unit you can compare against the cost
of building it. "It would be nice" is not a cost.>

## 6. Value analysis
<Who benefits, roughly how much, and what observable change would tell you
the value showed up? A number or a named behavior change, not a feeling.>

## 7. Feasibility
<The hardest part of building this, and whether anyone has actually tried a
piece of it, or whether this is still a guess dressed up as a plan.>

## 8. The simplest thing that could work
<Before the full version: the smallest thing you could ship in a day or less
that tests the same underlying idea.>

## 9. What would make us stop (kill criteria, written before starting)
<The specific, observable signal that would make you abandon this. Decide it
now, before you have any attachment to the work.>

## 10. Accessibility and cost considerations
<Who is excluded if you build it the obvious way, and what this will cost to
run every month, not just to build once.>

## 11. The sunset plan
<If this works, how will you know it is done growing? If it does not, how do
you turn it off without breaking whoever depends on it? Name the earliest
signal you would watch for. See `docs/SUNSET.md` for the full retirement
process once that signal shows up.>
