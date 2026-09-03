# Data contract registry

"A data contract must be registered before anything is built" is a policy
on a slide until something actually checks it. This is that check.

Not to be confused with `src/brothersbe/contracts.py` (LP-0201), which is a
different, internal thing: this project's own versioned JSON schema
registry for five surfaces THIS tool emits (`sbe status --json`, the task
registry, the work brief, the handover record, `sbe status --team --json`).
The two files share the word "contract" and nothing else. Nothing in this
document, and nothing in `src/brothersbe/data_contracts.py`, touches or
imports that module.

## One file, one table

Contracts live under `contracts/` at the repository root. **One file
describes one table.** The filename, minus its `.yaml` extension, IS the
table's fully qualified name: dots in the table name stay in the filename.

```
contracts/
  orders.yaml                    -> table "orders"
  analytics.events.users.yaml    -> table "analytics.events.users"
```

Matching between a contract and a table name found in a diff is
case-sensitive and exact: whatever a `CREATE TABLE`/`INSERT INTO`/etc.
statement names, byte for byte, is the filename this registry looks for.

A `contracts/` directory that does not exist, or exists and holds zero
`.yaml` files, is **NO-DATA**, never a pass. Nobody has registered anything
is not the same fact as nothing is wrong, and this registry never lets the
first read as the second.

## The format: a strict, tiny YAML subset

Standard library only, no third-party YAML dependency. The reader
(`brothersbe.data_contracts.read_contract_text`) supports exactly:

- blank lines and whole-line comments (`#` as the first non-space character)
- a flat `key: value` pair, at zero indentation
- a bare or quoted scalar string as a value (`freshness: daily by 06:00 UTC`,
  `freshness: "daily by 06:00 UTC"`)
- a simple list, one level deep: a key with no inline value, followed by
  `- item` lines at exactly 2 spaces of indentation

Nothing else. Anything outside that subset is **refused by name**: the
error names the exact line number and the construct it found, never a
generic parse failure and never something silently dropped. Refused
constructs include, by name: nested mappings, YAML anchors/aliases
(`&x`, `*x`), multi-line block scalars (`|`, `>`), flow collections
(`[...]`, `{...}`), YAML tags (`!!x`), document markers (`---`, `...`),
tab indentation, and a duplicate top-level key.

This is a deliberately narrower reader than `brothersbe.program`'s own YAML
subset (`load_yaml_file`, used for `PROGRAM.yaml`): that reader accepts real
nested mappings on purpose, because `PROGRAM.yaml` needs them. Reusing it
here would make every construct this format must refuse parse successfully
instead, so this module ships its own, smaller reader rather than reaching
for that one.

### Example

```yaml
row_meaning: "one row per placed order"
keys:
  - order_id
schema:
  - order_id: integer
  - customer_id: integer
  - status: string
freshness: "no more than 1 hour stale"
quality_thresholds:
  - "order_id is never null"
  - "status is one of: pending, shipped, cancelled"
allowed_readers:
  - order-service
  - analytics-readonly
change_notice: "30 days"
```

## The seven required fields

All seven, every time, for a contract to count as **registered**
(`validate_contract_fields`). A contract missing any of them is refused by
name, naming **every** missing field in one sentence, not only the first
one found. A field that is present but blank, whitespace-only, or a
placeholder ("TODO", "N/A", ...; the same vocabulary `sbe_checks.answered()`
already refuses for every other check in this project) is refused too, and
named separately from "missing": those are different findings, and an
author reading the message should not have to guess which one it is.

| Field | Type | Meaning |
|---|---|---|
| `row_meaning` | scalar | what one row of this table represents |
| `keys` | list | the column(s) that together identify one row |
| `schema` | list | the table's columns, one list item per column |
| `freshness` | scalar | how stale this table may be before it is late |
| `quality_thresholds` | list | the measurable quality rules this table must hold |
| `allowed_readers` | list | who (roles, not people) may read this table |
| `change_notice` | scalar | the notice period owed before a breaking change |

## Checking a change against the registry

Given a git range (`brothersbe.data_contracts.check_diff(contracts_dir, cwd,
base, head)`), this registry:

1. Reads every contract under `contracts_dir` (`load_registry`).
2. Strips SQL line comments (`-- ...` to end of line) from the added text,
   so a commented-out statement is never read as a real one, then walks the
   changed files' **added** lines only (deleting a `CREATE TABLE` statement
   is not a change that writes that table) and looks for a small, named set
   of SQL constructs: `CREATE TABLE` (with optional
   `OR REPLACE`/`IF NOT EXISTS`), `INSERT INTO`, `MERGE INTO`,
   `UPDATE ... SET` (an optional table alias, `o` or `AS o`, between the
   table name and `SET` is recognized), `COPY INTO`. Case-insensitive
   keyword match, on the table name that immediately follows: bare, or
   wrapped in the one quoting style each SQL dialect uses (backtick,
   double quote, or a `[bracket]` pair).
3. For each table found this way, checks it against the registry.

**What this diff scanner does NOT do, stated here rather than implied.** It
reads SQL text only. It does not parse an ORM call, a dbt model file named
after its target table, a stored procedure, or any other non-SQL way of
defining or writing a table. A table written that way produces zero hits
here: not a crash, not a false "clean", just outside what this scanner
reads. It is a diff-line regex scan, not a SQL parser: complex statements
(CTEs that shadow a table name, dynamic SQL built from string
concatenation) are not guaranteed to resolve correctly. The comment
stripper is a textual heuristic, not a tokenizer: it does not know a `--`
sitting inside a quoted string literal is not a comment opener, so a value
genuinely containing `--` has everything after it, on that line, stripped
too.

### When a touched table has no contract, or an incomplete one

**Reported loudly, never blocking.** The table is named, the specific
problem is stated ("no contract is registered", or the exact missing/blank
fields on an existing-but-incomplete contract), and the exact command to
register one is printed:

```
create contracts/<table>.yaml with the seven required fields
(row_meaning, keys, schema, freshness, quality_thresholds, allowed_readers,
change_notice)
```

`check_diff`'s **verdict** stays `"PASS"` in every one of these cases. It
returns `"FAIL"` only for a genuine execution problem: the registry
directory exists but could not be listed (a permission problem), or the git
range itself could not be resolved. It is never `"FAIL"` because a table
lacks a contract. Any caller that maps this verdict to a process exit code
therefore never fails a build, a CI job, or anybody's pipeline over a
missing or incomplete contract; the finding is still there, in full, in the
returned list, for a human or a bot to read and act on.

### Field-by-field disagreement between a contract and a change

When a registered, COMPLETE contract's table is touched by a `CREATE TABLE`
statement whose parenthesized column list is fully present in the diff's
added lines (a partial statement, split across an added and an unchanged
line, is not guessed at), two of the seven fields are compared against
what that statement implies, and only two:

- **`keys`**, against a primary key stated EITHER of two ways: a table-level
  clause, named or not (`PRIMARY KEY (col1, col2)` or `CONSTRAINT pk_orders
  PRIMARY KEY (col1, col2)`), or an inline, column-level constraint with no
  parenthesized list at all (`order_id INTEGER PRIMARY KEY`). Only compared
  when the statement states a primary key at all, by either spelling; if it
  does not, `keys` is left uncompared, because "not stated in this diff" and
  "stated as no keys" are different facts.
- **`schema`**, against the statement's column NAME set (types are not
  compared; a rename or a type change that keeps the same name set is not
  caught here). A column definition is distinguished from a table-level
  constraint clause (`PRIMARY KEY (...)`, `FOREIGN KEY (...)`, `UNIQUE
  (...)`, `CHECK (...)`, `INDEX (...)`, `KEY (...)`, each optionally
  prefixed with `CONSTRAINT <name>`) by that clause's own parenthesized
  list: a bare keyword with no paren immediately after it, like a column
  genuinely named `key` or `check`, is read as a column, not a constraint.

Every disagreement is reported field by field: which field, what the
contract says, what the change implies. For example:

```
table 'orders': contract field 'keys' says ['order_id'], the change in
migrations/004_orders_rebuild.sql implies ['order_id', 'customer_id']
```

**The other five required fields are never compared, on any input, by
design**: `row_meaning`, `freshness`, `quality_thresholds`,
`allowed_readers`, and `change_notice` cannot be derived from a `CREATE
TABLE` statement or from any other diff content this scanner reads. A check
that implied it covered them anyway would be a control that oversells
itself; it does not, and this document says so rather than leaving it to be
discovered later.

## Tests

`tools/test_sbe_data_contracts.py`. Run: `python3
tools/test_sbe_data_contracts.py`
