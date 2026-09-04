# 06. Diagrams

## Pipeline

The nodes below are declared as rows in 04-technology-map.md.

```mermaid
flowchart LR
  LandingZone --> ExtractStep
  ExtractStep --> TransformStep
  TransformStep --> LoadStep
  LoadStep --> ReconciliationStep
```

## Batch lifecycle

The states below are declared under the States heading in 05-data-model.md.

```mermaid
stateDiagram-v2
  received --> accepted
  received --> quarantined
  accepted --> superseded
```

## Entity relationship

```mermaid
erDiagram
  Partner ||--o{ SettlementFile : sends
  SettlementFile ||--o| SettlementBatch : loaded_as
  SettlementBatch ||--o{ SettlementRecord : contains
```
