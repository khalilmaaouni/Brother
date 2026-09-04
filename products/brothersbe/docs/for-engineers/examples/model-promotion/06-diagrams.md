# 06. Diagrams

## Entity relationship

```mermaid
erDiagram
  Customer ||--o{ FeatureSnapshot : describes
  FeatureSnapshot }o--o{ TrainingRun : trains
  TrainingRun ||--|| ModelVersion : produces
  ModelVersion ||--o{ PromotionDecision : evaluated_by
```

Customer, FeatureSnapshot, TrainingRun, ModelVersion and PromotionDecision are
all defined as entities in 05-data-model.md.

## Pipeline

The nodes below are runtime components declared as rows in 04-technology-map.md.

```mermaid
flowchart LR
  FeatureStore --> TrainingJob
  TrainingJob --> EvaluationJob
  EvaluationJob --> ModelRegistry
  ModelRegistry --> ServingSystem
```
