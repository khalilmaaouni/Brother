
# Brother 1.1.0: U5 Migration Plan: Absorb BrotherDS into brother.data

## 1. Summary and recommendation

U5 absorbs BrotherDS into `brother.data`. The highest-risk central file is `products/brotherds/bds.py`. BrotherDS is structurally simpler than BrotherSBE: it has a single obvious entry point, `bds.py`, with many small sibling modules. That makes the U3 pattern a better fit here, not a worse one.

Recommendation: follow U3's pattern for `bds.py`.

Use a thin, runtime-resolving facade over the original, untouched `bds.py`. Do not copy `bds.py`. Do not copy its sibling modules. Do not re-implement gates, verdicts, origin handling, or CLI dispatch. The facade should resolve the original file at runtime, load it in an environment where its bare sibling imports still resolve against the original BrotherDS directory, and re-export the public surface needed by `brother.data`. The original `bds.py` remains untouched until a later unit explicitly retires it.

This is the same discipline U3 used for Brother Mode's central file. The difference is that BrotherDS's central file is smaller and easier to bound, but it still has the sibling-import hazard described below.

## 2. Confirmed facts versus inference

### Confirmed from the provided material

- `bds.py` is the BrotherDS entry point.
- Its CLI surface includes: `new`, `author`, `check`, `receipt`, `register`, `score`, `ledger`, `mdm-eval`, `mdm-audit`, `chain`, `stage`, `passport`, `handoff`, `backlog`, and `selftest`.
- Its docstring says verdicts are `PASS`, `FAIL`, and `NO-DATA`, matching the BrotherSBE tuple exactly.
- It states that `NO-DATA` is never a pass and never a block.
- It states that duckdb is required only for claims whose origin is `SYSTEM`; every other origin runs on the standard library alone.
- It imports sibling modules directly by bare import: `vault_bridge`, `forecast_score`, `lessons`, and `packs`.
- `vault_bridge` is advisory only; no gate reads it.
- `forecast_score` provides weighted interval score and band coverage.
- `lessons` is optional and ImportError-guarded.
- `packs` is the EXPERIMENT/DETECTION/MASTER_DATA/PIPELINE pack registry; `packs.py` never imports `bds`, so there is no circular import.
- `bds.py` defines `PASS`, `FAIL`, `NODATA = "PASS", "FAIL", "NO-DATA"` as its own module-level constants.
- The docstring states claims are organized into five named "origins", each with different questions/gates.
- The docstring does not enumerate the five origin names.
- The quoted text names `SYSTEM` as an origin in the duckdb requirement.
- `bds.py` is 3067 lines.
- The BrotherDS directory contains the sibling modules listed in the prompt, including `mdm_*`, `pack_*`, `packs.py`, `vault_bridge.py`, `forecast_score.py`, and `lessons.py`.

### Inference from the provided material

- The bare sibling imports create the same class of "sibling files found by directory, not by package path" hazard that U3 found in `bm_store.py`.
- A copy-based absorption of `bds.py` into `brother.data` would break unless all sibling modules were also copied, which would violate the "never a copy" discipline and invite divergence.
- The facade should be lazy: it should not import `bds.py` at `brother.data` package import time unless needed.
- `SYSTEM` is one of the five origins, but the other four names are not provided.
- Core's `Evidence.status` uses the same three strings as `bds.py`, but the exact Core enum/member API is not quoted here.

## 3. Facade decision for `bds.py`

### Recommendation

Use the same runtime-resolving facade pattern U3 used: a thin loader over the untouched original file, plus a parity test suite proving the facade behaves identically for the covered surface.

### Why this fits BrotherDS

`bds.py` is a single entry point. That makes the facade boundary clearer than BrotherSBE's distributed tools. There is one central file to load, one CLI dispatcher to delegate to, and one primary module namespace to re-export.

However, `bds.py` is not self-contained. It imports sibling modules by bare name. That means the original BrotherDS directory is a de facto import root. The facade must preserve that import root or reproduce it in a controlled way. It cannot simply copy `bds.py` into `brother/data` and expect `import vault_bridge`, `import forecast_score`, `import lessons`, and `import packs` to keep working.

### Does the U3 `bm_store.py` hazard apply here?

Yes. The same class of hazard applies.

In U3, `bm_store.py` was found to rely on sibling files being discoverable by directory rather than by package path. A copy-based approach broke because the copied file no longer sat beside its siblings. Here, `bds.py` has the same structural property: it imports siblings by bare module name. If `bds.py` is moved or copied without its siblings, those imports fail or, worse, resolve to same-named modules from elsewhere in `sys.path`.

This makes the facade approach more strongly indicated for U5. The facade should load `bds.py` at its original location and ensure that the original BrotherDS directory is the resolution root for its sibling imports. If U3 already has a loader helper for this class of problem, reuse it. If not, implement a scoped loader or finder that serves BrotherDS sibling names from the original directory.

The loader must handle at least the top-level imports named in the prompt. It should also be checked against `mdm_*` and `pack_*` modules if the CLI subcommands import them lazily. The Group 0 deliverable below is designed to prove the basic case before the facade grows.

### What the facade should do

- Resolve the original BrotherDS root at runtime.
- Load the original `bds.py` by file path, without copying it.
- Preserve the original file's `__file__` identity.
- Make sibling imports resolve to the original BrotherDS directory.
- Re-export only the public surface needed by `brother.data`.
- Delegate CLI behavior to the original `bds.py` rather than reimplementing it.
- Keep the original `bds.py` and its siblings untouched.

### What the facade should not do

- It should not copy `bds.py`.
- It should not copy sibling modules.
- It should not reimplement gates.
- It should not reimplement verdict mapping.
- It should not hard-code origin names.
- It should not eagerly import duckdb.
- It should not retire the original file in U5's first slice.

## 4. Verdict vocabulary and Core's `Evidence.status`

`bds.py` defines its own module-level constants:

- `PASS`
- `FAIL`
- `NODATA`

Their values are `"PASS"`, `"FAIL"`, and `"NO-DATA"`.

Core's `Evidence.status` uses the exact same three strings. That is a strong contract, but it is not a substitute for a test.

Recommendation: re-export `bds.py`'s constants as-is, and explicitly validate that they never diverge from Core's accepted status strings.

Concretely:

- Do not create new string literals in `brother.data` for these verdicts.
- Do not define a second `PASS`/`FAIL`/`NO-DATA` vocabulary in the facade.
- Expose the loaded `bds.py` module's constants through the facade.
- Add a parity test that asserts `bds.PASS == "PASS"`, `bds.FAIL == "FAIL"`, and `bds.NODATA == "NO-DATA"`.
- Add a Core contract test that confirms Core's public `Evidence` status API accepts exactly those three strings and rejects anything else, according to Core's closed-set behavior.
- If Core exposes an enum, compare the enum values or

[TRUNCATED: this document was generated under a 10000-token
completion budget and ended mid-sentence exactly at that ceiling. The
core recommendation (facade pattern over bds.py, the sibling-import
hazard shared with U3's bm_store.py finding, and re-exporting the
verdict vocabulary as-is rather than redefining it) is complete and
usable. Section 5 (bounded first deliverable, analogous to U3's Group
0) was likely cut short or never reached; a follow-up pass should
either re-run with a larger budget or write that section directly
before U5's actual Group 0 work begins.]
