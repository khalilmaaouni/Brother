Status: UNAUDITED reference as of 2026-08-06. This page sits outside the mechanical truth guards and is scheduled for the documentation sweep; treat any dated or numeric claim inside with care until then.

# The intake template

What this is: the problem-first gate every new project or new feature answers
before any design or code starts. Who reads it: the founder deciding whether
this is worth building, and any AI session about to build it. This is founder
item F, verbatim intent: always start from the problem to solve, double down
on why, then ask why again.

How to use this file: copy the blank template below (it also lives, ready to
fill, in `project-template/INTAKE.md`), answer every section in your own
words, and do not start building until the kill criteria and the sunset plan
are both written down. An intake with an empty kill criteria section is not
finished. It is a wish.

Each question below is written so a one-word or feel-good answer cannot
satisfy it. If your answer sounds like an advertisement for the idea, you have
not actually answered the question yet.

---

## The blank template

### 1. The problem
State the actual thing that is broken or missing right now, as an observed
fact, not as a solution. "We need a better X" is a solution wearing a
problem's clothes. Name what you actually saw happen instead.

### 2. Who has it, and how you know
Name real people, a specific number, or a specific event (a message, a
support ticket, a repeated request), not "some users" or "people." If you
cannot name how you found out, you may be inventing the problem.

### 3. The why
In one plain sentence, why do those specific people need this now, not
eventually?

### 4. The why behind that why
Ask why again. What deeper goal does answering the first why actually serve?
If this section just restates section 3 in different words, you have not
gone deeper yet. Try again.

### 5. What happens if nobody does this
Name the actual cost of doing nothing, in a unit you can compare against the
cost of building it (time lost, money lost, trust lost, people who leave).
"It would be nice to have" is not a cost.

### 6. Value analysis
Who benefits, roughly how much, and what observable change would tell you the
value actually showed up? A number or a named behavior change, not "it
improves the experience."

### 7. Feasibility
What is the hardest part of building this, and has anyone actually tried a
piece of it (a prototype, a working comparable, a spike), or is this still a
guess dressed up as a plan?

### 8. The simplest thing that could work
Before designing the full version, what is the smallest thing you could ship
in a day, or less, that tests the same underlying idea?

### 9. What would make us stop (kill criteria, written before starting)
Name the specific, observable signal that would make you abandon this.
Decide it now, while you have no attachment to the work, not later when you
do.

### 10. Accessibility and cost considerations
Who is excluded if you build it the obvious way (someone using a screen
reader, someone on a slow connection, someone who does not read the primary
language, someone who cannot pay), and what will this cost to run every
month, not just to build once?

### 11. The sunset plan
If this works, how will you know it is done growing? If it does not, how do
you turn it off without breaking whoever depends on it? See `docs/SUNSET.md`
for the full retirement process. Here, just name the earliest signal you
would watch for.

---

## Worked example: a small, realistic intake

Project: let members of a small fitness app export their own workout history
as a CSV file.

### 1. The problem
Members cannot get their own logged workouts out of the app in any form. The
only way to see the full history is to scroll the in-app list, one workout at
a time.

### 2. Who has it, and how you know
Six support emails in the last two months, each asking some version of "how
do I get my data out," three of them explicitly mentioning wanting to bring
it into a spreadsheet before switching to a different tracker.

### 3. The why
People want to keep their own training history even if they stop using this
app, and they want to analyze it in tools we do not build (a spreadsheet, a
coach's own system).

### 4. The why behind that why
The deeper goal is trust: people log years of personal data into an app, and
if they believe that data is trapped, they hesitate to keep logging in the
first place. Solving export is really about removing a reason not to trust us
with the data at all.

### 5. What happens if nobody does this
At minimum, six known people stay frustrated and some churn to a competitor
that already offers export. The unmeasured cost is larger: people who never
emailed but quietly log less because they assume the same trap applies to
them too.

### 6. Value analysis
Benefit: every active member with more than one workout logged, roughly 800
of 1,200 accounts, gains a way to leave with their data, which is also the
entire audience for the "does this app respect my data" question, not just
the six who complained. We would know it worked if support tickets asking for
export drop to zero within a month of shipping, and the export button gets
used by more than a trickle of accounts.

### 7. Feasibility
The hardest part is not technical. The workout table already has every field
needed. The actual hard part is deciding a stable CSV column order so a
future schema change does not silently break someone's spreadsheet formulas.
Nobody has prototyped this yet. It is a guess that it is easy, based on
reading the existing data model, not on having tried it.

### 8. The simplest thing that could work
A single reply that emails the requester a CSV attachment generated by a
one-off script run by hand, offered to the six people who already asked,
before building any in-app button or self-service flow.

### 9. What would make us stop (kill criteria, written before starting)
If, after offering the manual CSV to the six people who asked, fewer than
half actually open the file (checked by a read receipt or a simple follow-up
question), the demand is not real enough to justify a self-service feature,
and this stops at the manual, one-off stage.

### 10. Accessibility and cost considerations
A plain CSV file is screen-reader friendly and opens on any device with any
spreadsheet tool, including free ones, so this does not exclude anyone by
format. Cost to run is near zero: this is a read-only query with no ongoing
infrastructure. The only ongoing cost is engineering time if requests for new
columns start arriving.

### 11. The sunset plan
This is done growing when it exports every field a member could reasonably
want, and support tickets asking for more columns stop arriving. If usage
stays near zero after the manual offer to the six known requesters, drop it
back to a manual, on-request script and remove the in-app button rather than
maintaining unused UI. See `docs/SUNSET.md`.
