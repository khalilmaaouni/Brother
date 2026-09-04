# Seeded review-depth fixtures (row S32)

Three frozen toy deliveries that reconstruct what a competitor's own review
layer caught during the raced rounds and Brother's did not
(`docs/plan/REVIEW-DEPTH-DESIGN-2026-09-05.md`, section 4.1). They are read
by `scripts/test_review_pass.py` and by nothing else.

Each fixture holds:

| Path | What it is |
| --- | --- |
| `ground-truth.json` | the unit row, the expected tier, class, reviewer and file, and the score the rule in the design's section 4.2 must produce. Held by the harness and never shown to any reviewer. |
| `base/` | the tree at the revision BEFORE the unit landed: the unit's own delivered check, and the discriminating check the finding carries. |
| `seeded/` | what the unit delivered, defect and all. |
| `fixed/` | the same delivery with the defect repaired, one line different. |
| `reviewer.json` | the canned finding a stubbed reviewer returns. |

## Driven both ways, by the mechanism rather than by the stub

The SAME canned finding is fed to the seeded tree and to the fixed one. What
separates them is the finding's own verification command, re-executed by the
pass at the delivered revision: it exits nonzero on the seeded tree
(`confirmed`) and 0 on the fixed one (`not_reproduced`, which scores
nothing). A fixture that scored the same on both trees would be measuring
the reviewer's vocabulary rather than the defect.

## Where these deviate from the design page, and why

The design describes the byte order mark defect as an invisible character in
the CSV header. That symptom is not reachable in Python: `json.loads` on a
string carrying a byte order mark raises rather than parsing the mark into
the first key (measured, 2026-09-05: `Unexpected UTF-8 BOM (decode using
utf-8-sig)`). The seeded converter here reproduces the same defect at the
same boundary with the symptom Python actually produces: the first record is
dropped without a word, silently, on exactly the files most likely to arrive
from a spreadsheet tool. The class, the file, the reviewer and the binding
between the finding and its check are unchanged.
