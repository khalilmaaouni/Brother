Status: UNAUDITED reference as of 2026-08-06. This page sits outside the mechanical truth guards and is scheduled for the documentation sweep; treat any dated or numeric claim inside with care until then.

# BrotherMode V2: a colleague you can audit

Version 2 draft, 2026-07-26. Written for a founder to read end to end, not for a
compiler. Every number in here came from a command run on this machine, and where
something is unproven it says so in the same sentence.

**If you read only this paragraph.** An external audit rated version 1 at 7.2 out of 10
and said patching individual defects would keep producing new ones. We tested twenty two
of its claims by running them: twenty one reproduced. So the mechanical core was rebuilt
around four decisions, then attacked for eight rounds. The worst things found were a
folder name that made one project read another project's private database, a file lock
that could be silently released, and a backup system that could publish an empty snapshot
over a good one. All are fixed and each fix has a test proven to catch it. The new engine
is not yet connected to the tools you run daily, the recovery rebuild is in flight, and
the continuous integration has never executed. Those three are the honest gaps.

---

## 1. The problem, and the why behind the why

An AI assistant that forgets everything between sessions is a talented contractor with
amnesia. You brief them, they do good work, and tomorrow you brief them again from
scratch. The obvious fix is memory, and memory is where most attempts stop.

Ask why that is not enough and you get to the real problem. A contractor with a perfect
memory of what was said is still not a colleague. A colleague knows which decisions are
yours, notices when you are about to repeat a mistake, tells you bad news first, and can
prove what they claim. What was missing was never storage. It was accountability.

Ask why once more and you reach the thing this project is actually about. An assistant
can only be accountable if its claims can be checked. Everything below follows from
that one requirement.

## 2. What BrotherMode is

Two parts, and the split matters.

A written constitution, `SKILL.md`, that a session loads before any sizable task. It
covers how work gets classified, how it gets delegated, how files get locked so two
workers never collide, how research is verified, how memory is written, and what must
never be done without asking you. It is prose because it governs judgment, and judgment
does not compile.

A small toolchain in Python that holds the session to the parts a machine can check. It
records who owns which files, snapshots your work before a crash, and writes memory to
your own vault. It makes no network calls, has no account, and runs entirely on your
machine. You can verify both claims with one command each, and a test now fails if
either stops being true.

## 3. What changed in V2, and why it was necessary

An external audit of version 1 rated it 7.2 out of 10 and said something more useful
than the score: continuing to patch individual defects would keep producing one new
edge case per round, and the answer was architectural.

Before accepting that, we tested it. Twenty-two of its load-bearing claims were
reproduced by execution rather than by reading, in isolated copies of the code. Twenty
one reproduced, one partly. The audit was right, and the rebuild proceeded on four
decisions, each of which deletes a whole family of defects rather than one instance.

**One project root.** Every operation resolves where the project starts, once, and
derives everything from it. Before, running a command from a subfolder created a second
independent registry, and two sessions could then lock the same file while each
believed it was alone.

**One transactional store.** A single SQLite database is the only authority on who owns
what. Human-readable status files are generated views of it. Prose is never ownership.
Before, a lock could live in prose in one file and in JSON in another, and the two could
disagree with nothing to catch it.

**One immutable identity.** Every piece of work has an identifier that is never reused,
and every change carries the version it expects. A stale session updates nothing and is
told so. Before, work was identified by a name you could reuse, so starting a task with
an existing name silently replaced its file locks and reported success.

**Two failure policies, named.** Advisory things (telemetry, hints) fail open and never
block your work. Ownership, lifecycle, and recovery fail closed: they refuse rather
than guess. Before, one policy was applied to both, so a corrupt state file was quietly
replaced with a clean empty one and the tool reported health.

## 4. How it was hardened, and what that cost

Eight rounds of fixes, each one triggered by a defect reproduced by running it, never
by an opinion. Seven adversarial review rounds, four independent lenses each. One
independent code review. One systematic mutation audit of the tests themselves.

Gate-level defects per round ran nine, then four, then two, then two, with the findings
narrowing from structural flaws to injection edges. Some of what was found is worth
naming plainly, because it shows what "hardened" actually means here.

A folder name containing a percent sign made every read-only command open a **different
project's database** and report it healthy. A file lock could be silently released by
updating a description. A truncated database read as an empty one, so a crash looked
identical to a fresh start. Editing the status file by hand and adding your own notes
caused those notes to be destroyed on the next write. And the backup system, the thing
you would reach for on your worst day, could publish an empty snapshot over a good one
and could delete a tracked file during recovery.

Three of the failures were in the checking, not the code. A review fleet spent an entire
round on a stale copy and reported confident findings about code that no longer existed.
Fifteen tests turned out to be checking their own private copies of old code and could
never have failed. A test guarding against destroyed data passed while mutations that
deleted the data survived. Those are now a named defect class: the verifier was itself
unverified.

## 5. How it learns, and why the first design was wrong

The first version of the learning system measured itself. It had nine scored metrics, a
weekly review, and a rule that a rule which did not help would be reverted.

Audited against its own data, most of it was theatre. One metric was a hardcoded line of
text that could never change. Five of the nine had no number behind them. Thirteen rule
changes had landed against one review, so the revert rule had fired zero times. Three of
fourteen quality ratings were fractional numbers a human answering "one to five" cannot
produce, meaning the system had rated itself.

The research explains why that was doomed rather than merely sloppy. Models that
critique themselves with no outside signal get measurably worse: in one study reasoning
accuracy fell from 75.9 to 74.7 percent, and a commonsense benchmark collapsed from 75.8
to 38.1. The same models fix an error presented as someone else's and fail on the
identical error framed as their own. Self-correction only produced real gains when
trained against a verifiable external reward.

The founder's correction fixed the frame. The target is not the system's scorecard. It
is the founder: their habits, their preferences, their nature, and the right division of
labour to complement them. That turns self-grading into supervised learning from a
teacher, and it works at one user where population statistics cannot. Detecting a twenty
percent efficiency change at this volume would need roughly 1,121 sessions per arm. A
single correction is a discrete fact usable on the next task.

Four loops replaced the nine metrics. Corrections, captured immediately with the reason
behind them so the taste generalizes instead of the rule being memorized. Revealed
taste, measured by how much you change in what arrives. Calibration, where a prediction
of your choice is sealed before the recommendation is formed and counts only when the
two disagreed, because scoring agreement rewards flattery. And the division of labour,
so the system stops asking about things you have shown you do not want to decide, and
stops deciding things you want to hold.

One structural consequence: a session may now propose a change to the constitution and
may not make one. That is the reason Constitutional AI works at all, and the measured
record here said the same thing.

## 6. What is honestly not done

The engine is built, hardened, and **connected to nothing**. The tools that run today
still use the old registries with every original defect. Until that rewiring lands,
every original operating restriction still applies to daily work.

The recovery system is in flight at the time of writing. Continuous integration has
never once executed, because nothing has been pushed. Windows is ratified scope and is
designed for rather than proven, verified locally by substituting the platform's path
behavior. The install path clones a moving branch into code that runs automatically on
every session, which for a tool that reads transcripts is the weakest link in the design
and needs tagged, checksummed releases.

`docs/KNOWN-LIMITS.md` holds the full list and is the first file to read before trusting
anything here.

## 7. What it is for

Not an org. One person who wants their standards carried with machine discipline: never
tired, always verifying, never forgetting the ledger, immune to sunk cost, and willing
to say the uncomfortable thing first.

The honest summary of the work so far is that a tool built to make claims checkable has
been used, mostly, to check its own claims and find them wanting. That is the right
first customer.
