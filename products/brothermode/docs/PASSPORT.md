# The change passport

The change passport is the one object that travels from BrotherMode
(execution provenance) to BrotherSBE (assurance). It never travels back. If
the assurance side needs something the passport does not carry, that is a
defect in the passport, not permission to reach into execution state. Full
spec authority: `docs/NORTH-STAR-CHAIN.md`, section "The change passport, in
full".

This page is the plain reading: what the five fields mean, the shape a
producer actually writes, how to generate one, how to validate one on its
own, and what this first version deliberately does not establish.

## The five fields

1. WHAT WAS DONE. The change identity, the commit range, the files touched.
2. WHO DID IT. Which sessions wrote, which claims they held, and the human
   accountable for the result, by name. Accountability is a name, never a
   role.
3. WHAT WAS RUN. Every verification executed after the last edit, its
   result, and whether a build system or a laptop produced it.
4. WHAT WAS NOT ESTABLISHED. The executing side's own honest list of what it
   did not check. MANDATORY, never empty. Regression, performance,
   cross-device, interface behaviour and translated copy belong here by
   default unless something actually examined them.
5. WHERE IT CAME FROM. The development method used, named and not judged.

## The worked shape

A passport is one JSON document. The five fields above sit at the top level
as plain-language string lists, so a reader who only wants the fast version
can read those five keys and stop:

```json
{
  "schema": "change-passport/v1",
  "generatedAt": "2026-08-20T10:00:00Z",
  "sensitivity": "redacted",
  "whatWasDone": [
    "change in <repo>, commit range <base>..<head>",
    "1 file(s) touched: a.txt"
  ],
  "whoDidIt": [
    "accountable: Fixture Person",
    "session sess-xyz: claims Do the thing, upsert_project"
  ],
  "whatWasRun": [
    "`python3 tools/test_all.py` returned green (local, 2026-08-20T09:30:00Z)"
  ],
  "whatWasNotEstablished": [
    "no regression pass is recorded in the store for this change",
    "..."
  ],
  "whereItCameFrom": [
    "no method plugin detected; native flow"
  ],
  "change": { "repo": "...", "projectId": "...", "baseCommit": "...", "headCommit": "...", "filesTouched": ["a.txt"] },
  "details": { "who": {...}, "evidence": [...], "method": {...}, "notEstablished": {...} }
}
```

`change` and `details` are where the five top level fields come from: `change`
holds the commit identity, `details` holds the session, evidence, method and
gap records the five fields are derived from. The full, formal shape is
`schema/change-passport.v1.json`, a JSON Schema draft 2020-12 document any
third party can read without installing anything from this repository.

## Generating one

```
python3 tools/bm_passport.py generate --project-id ID --base SHA --head SHA \
    [--accountable NAME] [--method NAME] [--now ISO8601] [--out PATH] \
    [--include-sensitive] [--allow-missing-project]
```

Prints the passport to stdout by default, or writes it to `--out` when
given. `--project-id`, `--base` and `--head` are required; `--base` and
`--head` are resolved with `git rev-parse --verify <value>^{commit}` (any
short hash, branch name or `HEAD~N` spelling works) and the passport
carries the FULL, canonical hash, never the caller's own spelling. The
generator reads only public record surfaces: `tools/bm_store.py`'s own read
accessors (`get_project`, `list_tasks`, `list_evidence`, `list_attribution`,
`open_key_decisions`), plain `git` (run in a sanitized environment: every
inherited `GIT_*` variable is stripped, `GIT_CONFIG_NOSYSTEM=1` is set, and
`LC_ALL`/`LANG` are pinned to `C`, so an inherited `GIT_DIR` or a machine
config cannot redirect which repository is actually read) for the commit
range and the files touched, and the accountable and method flags. It never
reads raw SQLite and never reads the sentinel surface, which is private by
design.

SENSITIVITY. Every store-derived field is REDACTED by default: the five
reads above run through `tools/bm_store.py`'s own export policy
(`raw=False`), the same policy `bm_store.py dump` applies, which withholds
founder-typed free text (a task title, a check command or verdict, a
session's claimed artifacts, an attributed actor's name, a decision's
subject or claim). Where the policy withholds a value, the affected line is
DROPPED from its field, never shown as the `[WITHHELD: ...]` placeholder,
and one named line is added to field 4 saying so. The top-level
`"sensitivity"` key names which mode produced the document: `"redacted"`
for the default, `"raw"` for `--include-sensitive`. Pass `--include-sensitive`
to restore the unredacted store text; it prints a one-line warning to
stderr naming what it exposes. A raw passport carries the same class of
content the store itself holds (real names, real prose) and needs the same
handling: never publish one, never attach one to a public issue or PR,
treat it exactly like a `bm_store.py dump --raw`.

`change.projectId` always carries the requested `--project-id`, whether or
not that project was actually found in the store. A project genuinely
absent from an otherwise-readable store is REFUSED by default (exit 1,
nothing written); pass `--allow-missing-project` to generate the degraded
passport anyway, with the gap named in field 4 exactly as before.

Exit 0 on a written passport, whatever it could and could not establish.
Exit 1 when no accountable human could be established at all (no
`--accountable`, nothing in the store, and `git config user.name` empty),
when `--base` or `--head` did not resolve to a commit, when the requested
project is missing and `--allow-missing-project` was not given, when the
store stayed busy or locked through three short retries (a lock is refused,
never silently reported as an absent or corrupt store), when the assembled
document fails its own self-validation against `bm_passport_validator.py`,
or when `--out` could not be written. Exit 2 for a usage error or a project
root that could not be resolved.

DETERMINISM. Two generate calls against the SAME store snapshot, the SAME
git state, the SAME `--now`, the SAME flags, on ONE machine, with this
tool's own sanitized git environment, produce byte-identical output.
Without `--now`, only `generatedAt` differs. Everything else, the sorted
file list, the sorted session claims, the gap lines, is assembled in a
fixed order rather than left to depend on iteration order. Cross-machine
byte identity is NOT promised in v1: a different git version, a different
locale's `LC_ALL=C` message catalog, or a different filesystem's newline
handling are outside what this generator controls for. Every line this
generator assembles is capped at 2000 characters (with a literal
`" [truncated by generator]"` suffix on the ones that were), so one
enormous founder-typed field cannot produce one unbounded line.

## Validating one, standalone

```
python3 tools/bm_passport_validator.py <file>
```

No dependency on this repository beyond the standard library, so a reader
who never installed BrotherMode can still check a passport by hand. Prints
`VALID` and exits 0 when every rule holds. Prints one named reason per line
and exits 1 when the document is well formed JSON but breaks a rule. Exits 2
with a named error when the file cannot be read, is not valid JSON, carries
a duplicate object key, carries a non-finite numeric constant
(`NaN`/`Infinity`/`-Infinity`, which Python's `json` module accepts by
default but the JSON grammar does not define), or is over the 16 MiB size
this validator reads.

The rules: the `schema` key equals `change-passport/v1`; `generatedAt` is a
non-blank ISO 8601 UTC timestamp of the generator's own seconds-precision
shape; `sensitivity` equals `redacted` or `raw`; the five fields above are
each present and answered, meaning a non-empty list of non-blank strings;
field 4's own record (`details.notEstablished`) carries a non-empty `items`
list, or an empty list paired with a non-blank `noneClaimJustification`,
because a literal claim that nothing was left unestablished needs its own
justification; `change.baseCommit` and `change.headCommit` are present and
hex shaped, `change.repo` and `change.projectId` are non-blank, and
`change.filesTouched` is a list of strings (may be empty);
`details.who.accountableHuman` is a non-blank name and `details.who.sessions`
is a list of `{label, claims}` objects with a non-blank label;
`details.method.name` is non-blank; every entry in `details.evidence`
carries an `origin` of `local` or `ci` and a `timestamp`; and, as a
consistency check across the two copies of the accountable name, when
`details.who.accountableHuman` is answered it must appear in at least one
`whoDidIt` line.

`schema/fixtures/` carries one valid document
(`change-passport.v1.canonical.json`) and three invalid ones, each breaking
exactly one rule by name: `invalid-hollow-field4.json`,
`invalid-missing-accountable.json`, `invalid-evidence-no-origin.json`.

CROSS-HOST CONSUMER PROOF, parked by orchestrator decision (F13, cross-family
review 2026-08-20): the sibling-checkout path for the BrotherSBE round-trip
test is overridable via the `SBE_SIBLING_ROOT` environment variable
(`tools/test_bm_passport.py`), so the check can run as an explicit CI input
rather than a hardcoded path. The reviewer's other ask, a required
(non-skippable) CI job enforcing that check on every merge, is NOT
implemented: this estate's founder law forbids self-firing CI on any
trigger. Verification stays local, run by hand or by this repo's own gate
(`tools/test_all.py`), never by an automatic cloud job.

## What v1 does not establish

The fourth field is the honest list this producer could assemble from its
own store and from git alone. Two limits sit above that list, worth stating
once here rather than leaving a reader to discover them:

Evidence origin. `details.evidence[].origin` is always `local` today,
because BrotherMode's own evidence table records a check's command and note
but not whether a build system ran it. Every entry generated by this tool
carries a line in field 4 saying so, rather than guessing `ci` for anything.

Trust of `ciRunId` on the consuming side is disclosure level only. BrotherSBE's
own passport reader, `tools/sbe_passport.py`, states this about the value
plainly in its own docstring: a truthy `ciRunId` means the receipt CLAIMS a
build system produced it, never that one actually did, because
`SBE_CI_RUN_ID` is an environment variable any local shell can export. A
forged id reaches the same reading a real one would. This passport format
inherits that same limit wherever a `ciRunId` appears; it is a claim, not
proof.

Not production ready beyond what the tests in `tools/test_bm_passport.py`
actually exercise: a fixture store built through the same read accessors the
generator uses, and the round trip against BrotherSBE's own consumer where
that sibling repository is present on the machine running the suite.
