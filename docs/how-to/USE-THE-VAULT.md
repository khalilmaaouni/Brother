# Use the Vault

Use the Vault when a decision, failure, constraint, or approved lesson should still matter after the current session is gone.

The goal is not to save everything. The goal is to preserve the small amount of knowledge that will make future work safer or faster.

## Before anything is written

On first setup, Brother asks where private memory should live and explains what it will write. Choose a local folder you control.

Brother should not write private project memory before you consent. If no Vault is configured, it should say so rather than guess a location.

You can open the chosen folder in Obsidian with **Open folder as vault**, or use any normal Markdown editor.

## What belongs in the Vault

A good Vault note answers a future question.

Keep:

- a decision and the reason behind it;
- a failure worth not repeating;
- a compatibility or security constraint;
- an assumption future work must recheck;
- an approved lesson from a human correction;
- a superseded decision whose history still matters.

Do not keep:

- passwords, keys, or credentials;
- raw sensitive data;
- full chat transcripts;
- temporary debugging noise;
- every test output;
- speculative rules nobody approved.

## Write for the future task

A durable note can be simple:

```markdown
# Public API compatibility

Decision: Existing response fields remain stable for current clients.

Why: Several integrations parse the current response directly.

Applies to: Public API changes and migrations.

Revisit when: A versioned API is available.

Evidence: Link to the decision or change that established this boundary.
```

The exact metadata may be managed by Brother. What matters to the reader is that the note states the decision, scope, reason, and point at which it should be revisited.

## Add or edit a note

The universal path is direct: open the chosen Vault folder and create or edit a normal Markdown file. The knowledge stays readable even when Brother is not running.

Brother may also create or update durable notes through its capture and learning flows. Do not assume that a casual instruction such as `remember this forever` automatically creates an approved rule. A proposed behavioral lesson still needs human approval.

Keep the note content simple. Do not invent private metadata fields from old examples. Use the current release's generated structure when Brother creates the note, and edit the human-readable decision or lesson in place.

## Connect the note to the work

The strongest memory is tied to something concrete:

- a file or area it applies to;
- a decision record;
- a failure and its cause;
- a check or source that supports it;
- another note it supersedes, relates to, or contradicts.

Plain Markdown links and `[[wikilinks]]` make those relationships readable in Obsidian without hiding the knowledge in a proprietary store.

## Let Brother recall memory at the right moment

Brother should not dump the whole Vault into every session.

Relevant memory is most useful at the point of need. A previous failure related to a file should appear when that file is being changed. A compatibility decision should appear when a new task reaches the affected interface.

This keeps context focused and makes the lesson actionable.

## Treat recalled memory as context

A stored note can be stale, incomplete, misunderstood, or even written in a way that looks like an instruction.

Brother therefore treats recalled memory as untrusted context:

1. identify where the note came from;
2. consider whether it is still current;
3. compare it with the repository and present evidence;
4. use it to inform the work;
5. never let it silently override a current human decision.

The Vault can warn. Evidence still decides.

## Turn corrections into approved lessons

A human correction should not automatically become a permanent rule.

The safe learning loop is:

1. capture what was corrected;
2. interpret the narrow lesson;
3. ask a human to approve it;
4. apply it when relevant;
5. observe whether it helped.

Capture is not approval. One misunderstood sentence should not reshape future behavior without a human decision.

## Keep the Vault healthy

Review it periodically:

- close or supersede obsolete decisions;
- remove generated noise;
- keep sensitive information out;
- add links where two notes affect the same work;
- mark uncertainty instead of polishing it away;
- preserve contradictions until evidence resolves them.

The best Vault is not the largest one. It is the one that brings back the right knowledge at the right time.

## The rule to remember

**Brother keeps the work accountable. The Vault keeps the useful memory durable. Current evidence remains the source of truth.**
