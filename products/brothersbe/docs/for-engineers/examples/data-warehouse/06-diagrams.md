# 06. Diagrams

## Entity relationship

```mermaid
erDiagram
  Customer ||--o{ Subscription : holds
  Subscription ||--o{ Invoice : bills
  Invoice ||--o{ Refund : refunded_by
  Invoice ||--o{ RevenueEvent : recognises
```

Customer, Subscription, Invoice, Refund and RevenueEvent are all defined as
entities in 05-data-model.md.

## Pipeline

The nodes below are runtime components declared as rows in 04-technology-map.md.

```mermaid
flowchart LR
  BillingExport --> IngestionJob
  IngestionJob --> StagingLayer
  StagingLayer --> MartBuild
  MartBuild --> RevenueMart
```
