# <Project name>, requirements

What this is: what the project must actually do, as testable, numbered
statements, each traced back to a real source. Who reads it: the founder
deciding whether the scope is right, and anyone implementing or reviewing a
change, who should be able to point at one line here and one line in the
source (usually `../INTAKE.md` or a decision file) for every change they
make.

Each requirement gets an ID, a plain statement, a Given, When, Then
acceptance line, and a source citation. Requirements describe what must be
true, not whether it is already true today. Whether a requirement is actually
met yet is a QA question, see `QA-GATES.md`.

Group requirements by the real decisions that produced them, the way this
worked example does, rather than dumping them in one flat, unordered list.

---

## Group A: <name of the first real decision or capability this covers>

Source: `<the file and, if useful, the line range this group traces back to>`.

**R-01. <One short, plain title.>**
Given <the starting condition>, when <the action happens>, then <the
required, observable outcome>.

Worked example:

**R-01. A member can only export their own workouts.**
Given a request for an export is made, when the script builds the file, then
it includes only rows belonging to the member id that was requested, and no
other member's rows appear in the output under any circumstance.

**R-02. <Next requirement in this group.>**
Given <...>, when <...>, then <...>.

---

## Group B: <name of the next real decision or capability>

Source: `<file and line range>`.

**R-0N. <title>**
Given <...>, when <...>, then <...>.

---

<Add or remove groups to match your project's real decisions. Delete this
line and the worked example above once your own requirements replace them;
keep the ID numbering continuous even across groups, the way R-01 through
R-47 run continuously across five groups in BrotherMode's own
`docs/ba/REQUIREMENTS.md`.>
