# memory-poisoning-heldout-v1

VN5c: the first-party, post-fix qualification held-out poison pack for the memory poisoning gauntlet (scripts/gauntlet_memory_poisoning.py, row LL-3). NEVER an independent or external benchmark: authored by the same estate, on the same night branch, against the same mechanisms the frozen fourteen already exercise. The frozen corpus at benchmarks/gauntlets/memory-poisoning.json stays byte-identical; nothing here is merged into it.

Families (33 total cases):
- A-verification-weakening: 4
- B-verdict-laundering: 4
- C-authority-escalation: 3
- D-evidence-laundering: 6
- E-scope-poisoning: 2
- F-conflict-poisoning: 5
- G-temporal: 3
- H-japanese-equivalent: 6

Every case body carries the fixture marker GAUNTLET-POISON-LL3-FIXTURE-DO-NOT-COPY-INTO-A-REAL-VAULT and describes an invented, clearly marked scenario: no real person, client, secret, or company. Each case follows the same PLANTED shape scripts/gauntlet_memory_poisoning.py's own load_corpus() reads (id, class, route, plus whatever write_note/admit/capture needs for that route). The cases themselves live in the sibling file memory-poisoning-heldout-v1.json, a plain JSON array (the shape load_corpus() requires); this file carries only the metadata that used to wrap them.

Run: `python3 scripts/gauntlet_memory_poisoning.py --corpus benchmarks/gauntlets/corpora/memory-poisoning-heldout-v1.json --out /tmp/<name>.json`
