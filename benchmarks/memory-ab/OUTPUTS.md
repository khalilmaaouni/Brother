# Memory A/B raw outputs

The raw model outputs live byte-exact in outputs-2026-08-30.tar.gz. They are
packed rather than loose because they quote model text verbatim, dashes
included, and the push gate's dash rule governs what the readable tree says.
The canonical scored record is results-2026-08-30.json, whose JSON encoding
escapes those characters. Unpack with: tar xzf outputs-2026-08-30.tar.gz
