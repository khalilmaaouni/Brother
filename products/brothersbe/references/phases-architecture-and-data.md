# Phases 3 and 4: architecture and data

LOAD WHEN: the shape of the system is being decided, a technology map is being written, or a data model is being taken from conceptual to logical to physical.

(Extracted verbatim from SKILL.md, Phases 3 and 4. The routing table in SKILL.md names when to load this file.)

## Phase 3. Architecture

Shape is decided against named criteria, not preference. The decision tables live in
`tables/architecture.json` and are scored by `tools/sbe_decide.py`; the consultation with
the operator is the intake to the table, not a replacement for it. The shape question
(monolith, modular monolith, services, event-driven) scores independently deploying teams,
consistency requirement, operational maturity (on-call, tracing, CI), and failure
isolation. That shape table is the one that ships. Integration, storage, consistency and
failover decisions are human review until their tables are written and land with fixtures,
and asking `sbe_decide.py` for one of them says so by name rather than crashing. Thresholds ship as defaults measured on one estate and are re-measured on
yours, changed in a reviewed pull request.

Every table returns the same shape: a recommendation, up to two alternatives, the criteria
that separated them, and what would flip the decision. There is one table today. That output is the body of
`03-adr.md`. Alongside it, `04-technology-map.md` names, per component, the technology, the
owner, the failure mode, and the recovery path, plus the source systems, their availability
expectations, their failover, and the recovery time and recovery point objectives with the
drill that proves them. Reliability, repeatability, and coherence are chosen at this phase.

## Phase 4. Data

Conceptual, then logical, then physical, in that order, in `05-data-model.md`.

- **Conceptual**: entities, meanings, identity, business rules, in plain language, no
  technology. Derived from the purpose brief and the process map.
- **Logical**: relationships with explicit cardinality and optionality, keys and identity
  strategy, attribute roles (identifier, descriptor, measure, foreign key, temporal,
  status), normalization decisions with their reasons, historization, and the source
  system map naming for every entity its system of record, its refresh contract, and what
  happens when that source is unavailable.
- **Physical**: engine-specific types, indexes, partitioning, clustering, constraints, and
  the migration path with its reverse.

Three lenses apply at the logical gate, in this order: the engineer (can this load
reliably, idempotently, at volume, and recover after failure), the analyst (can the real
questions be answered without heroic joins, is every grain and metric unambiguous), the
scientist (is history preserved, is leakage prevented, are features derivable).

The gate between logical and physical is mechanical, and it is L4.
