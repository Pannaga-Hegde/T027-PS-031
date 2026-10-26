# PS-031: Dark-Store Order Allocation with 10-Minute SLA, Rider Batching and Substitutions

## 1. Project Overview

**Problem Statement ID:** PS-031
**Domain:** Commerce — Quick Commerce
**Difficulty:** High
**Implementation Language:** Python 3.11
**Testing Framework:** pytest

### 1.1 Introduction

Quick-commerce platforms fulfil customer orders through dark stores, which are fulfilment locations dedicated to preparing online orders. These platforms must select suitable stores, check inventory, handle unavailable products, allocate picking capacity, assign delivery riders, and maintain delivery service-level commitments.

This project implements a command-driven backend engine that automates these operational decisions.

The system processes orders, evaluates store eligibility, applies inventory and substitution rules, schedules picking, batches eligible orders onto riders, records deliveries, calculates SLA compensation, and generates order traces.

### 1.2 Problem Statement

The system must accept customer orders and allocate them to appropriate dark stores while considering geographic distance, operating hours, stock availability, workload, and picking capacity.

It must also handle product substitutions, partial fulfilment, delivery fees, rider batching, weather-related delays, customer cancellations, and compensation according to the supplied business rules.

The application is operated through command files. A graphical user interface is outside the initial project scope.

## 2. Project Objectives

The objectives are to:

1. Register dark stores and maintain their operational information.
2. Accept customer orders containing product identifiers, quantities, timestamps, and substitution preferences.
3. Select suitable fulfilment stores according to serviceability, inventory, distance, and workload.
4. Calculate promised delivery times and identify delayed promises.
5. Resolve inventory shortfalls through eligible substitutions or partial fulfilment.
6. Apply fulfilment thresholds, delivery fees, and surge charges.
7. Batch compatible orders onto riders and determine dispatch timing.
8. Record actual delivery times and calculate customer compensation and rider penalties.
9. Process weather holds and customer cancellations.
10. Generate an order trace explaining allocation, fulfilment, delivery, and financial outcomes.
11. Verify the implementation through automated tests mapped to the business rules.

## 3. Scope

### 3.1 In Scope

- Dark-store registration.
- Product and inventory management.
- Customer order acceptance.
- Geographic serviceability checks.
- Store selection.
- Picking-slot capacity management.
- ETA calculation.
- Substitution and partial fulfilment.
- Order repricing and delivery fees.
- Rider allocation and batching.
- Delivery recording.
- SLA compensation and rider penalties.
- Weather holds.
- Cancellation processing.
- Command-file parsing.
- Order tracing.
- Unit and integration testing.

### 3.2 Out of Scope

The initial implementation does not require:

- A graphical user interface.
- A production payment gateway.
- Real-time GPS tracking.
- Live weather API integration.
- Live traffic API integration.
- Real-world rider dispatch integrations.
- A production database server.

These exclusions do not prevent the system from simulating operational decisions through timestamped commands and controlled test data.

## 4. Functional Requirements

**FR-01 — Store Management:** Register stores with geographic coordinates, operating hours, SKU inventory, active rider count, and picking capacity.

**FR-02 — Order Management:** Accept orders containing customer locations, timestamps, line items, quantities, and substitution preferences.

**FR-03 — Store Allocation:** Select an eligible store or reject an order while recording the applicable rule identifiers and allocation outcome.

**FR-04 — Picking and ETA:** Enforce picking capacity, manage slot overflow, and calculate promised ETAs.

**FR-05 — Substitution and Fulfilment:** Resolve stock shortages through eligible substitutions or partial fulfilment and recalculate order value.

**FR-06 — Pricing:** Apply delivery fees, surge charges, refunds, and other applicable financial adjustments.

**FR-07 — Rider Batching:** Group compatible orders, assign riders, calculate dispatch times, and establish drop sequences.

**FR-08 — Delivery and SLA:** Record actual delivery times and calculate customer compensation and rider penalties.

**FR-09 — Weather Holds:** Prevent new allocations to affected stores during a weather hold and apply the specified ETA extensions to existing allocations.

**FR-10 — Cancellation:** Apply cancellation rules according to the order's lifecycle stage.

**FR-11 — Order Trace:** Display the selected store, rule outcomes, substitutions, batch, ETA, actual delivery time, compensation, and final invoice.

**FR-12 — Automated Testing:** Provide tests for every business rule and the explicitly specified edge cases.

## 5. Non-Functional Requirements

**NFR-01 — Correctness:** Business decisions must follow the supplied rules and their documented interpretations.

**NFR-02 — Determinism:** Identical inputs and initial state should produce identical results.

**NFR-03 — Maintainability:** Separate domain models, business services, command parsing, and shared utilities.

**NFR-04 — Testability:** Business logic should be testable without requiring a graphical interface or external services.

**NFR-05 — Traceability:** Significant allocation, fulfilment, delivery, and compensation decisions should be explainable through rule identifiers and order records.

**NFR-06 — Input Validation:** Invalid commands and malformed data should produce clear errors.

**NFR-07 — Reproducibility:** The application should be executable using documented setup instructions and sample command files.

## 6. Proposed Architecture

The application uses a modular architecture with the following layers.

### 6.1 Command Interface

The command parser reads input records, validates their structure, and invokes the appropriate application operations.

The initial design uses JSON Lines, with one JSON object per line. This is a project design choice and may be adjusted if the evaluation requirements specify another format.

### 6.2 Domain Models

Domain models represent stores, products, orders, order lines, riders, batches, substitutions, and delivery records.

They hold the data required to evaluate business rules and maintain operational state.

### 6.3 Business Services

Business services implement store registration, allocation, inventory checks, substitutions, picking, batching, delivery, compensation, and cancellation.

Business rules should not be concentrated in the command parser.

### 6.4 Utility Functions

Shared utilities handle geographic distance calculations, time calculations, and other reusable operations.

### 6.5 Testing Layer

Automated tests validate individual rules, boundary conditions, service interactions, and complete command-driven flows.

## 7. Proposed Module Responsibilities

| Module                    | Responsibility                                              |
| ------------------------- | ----------------------------------------------------------- |
| `main.py`                 | Application entry point and command-processing coordination |
| `command_parser.py`       | Read and validate command-file input                        |
| `store.py`                | Represent a dark store                                      |
| `product.py`              | Represent product and SKU information                       |
| `order.py`                | Represent orders, lines, preferences, and lifecycle state   |
| `rider.py`                | Represent riders and daily penalty information              |
| `batch.py`                | Represent a group of orders assigned for delivery           |
| `store_service.py`        | Register and retrieve stores                                |
| `allocation_service.py`   | Evaluate serviceability and select stores                   |
| `inventory_service.py`    | Check and update available inventory                        |
| `substitution_service.py` | Select eligible replacement products                        |
| `picking_service.py`      | Manage picking capacity and scheduling                      |
| `batching_service.py`     | Form batches and schedule dispatch                          |
| `delivery_service.py`     | Record deliveries and delivery outcomes                     |
| `compensation_service.py` | Calculate SLA compensation and rider penalties              |
| `cancellation_service.py` | Process cancellation requests                               |
| `distance.py`             | Calculate geographic distance                               |
| `time_utils.py`           | Support consistent time calculations                        |

This table describes the intended architecture. It must be updated if the implementation changes.

## 8. Business Rules

The project implements twelve business rules, identified as BR-01 through BR-12.

Their detailed definitions, implementing modules, test mappings, and statuses are maintained in `business-rules.md`.

Each implemented rule must have corresponding automated tests. Boundary conditions must be tested explicitly where required.

## 9. Order Lifecycle

An order progresses through defined lifecycle states, from receipt and allocation through picking, dispatch, and delivery.

Alternative paths include rejection, cancellation, and pre-dispatch fulfilment cancellation.

The state diagram is maintained in `state-diagram.mmd`.

The `DELAYED_PROMISE` condition is tracked separately from the primary lifecycle state because it affects SLA treatment rather than necessarily representing a distinct lifecycle stage.

## 10. Data and Financial Handling

The initial implementation uses in-memory application state for stores, products, orders, riders, and batches.

Monetary values are represented using decimal arithmetic to avoid common binary floating-point errors.

Timestamps are handled consistently so that picking slots, weather holds, dispatch deadlines, promised ETAs, and actual delivery times can be evaluated reliably.

Financial calculations must distinguish between item value, delivery fees, surge charges, refunds, wallet credits, coupons, and rider penalties.

## 11. Error Handling

The application must handle invalid commands, missing identifiers, duplicate registrations, invalid coordinates, unavailable products, and invalid lifecycle transitions.

Errors should be explicit enough to identify the rejected operation and its reason.

The application should not silently change a business rule to accommodate invalid input.

## 12. Testing Strategy

Testing is organized into:

1. Unit tests for individual calculations and rules.
2. Service tests for store allocation, substitutions, batching, and compensation.
3. Integration tests for complete order-processing flows.
4. Boundary tests for exact thresholds.
5. Regression tests for previously discovered defects.

The detailed mapping between business rules and tests is maintained in `test-plan.md`.

## 13. Assumptions and Open Questions

The specification does not define every possible interaction between business rules.

Ambiguities must be recorded in `decision-log.md`, including:

- Picking-slot overflow near store closing time.
- The precise line-fill percentage calculation.
- Substitution candidate selection when multiple candidates qualify.
- Batch eligibility and dispatch timing interactions.
- Weather holds issued while a batch is being picked.
- The exact financial treatment of post-dispatch cancellation exceptions.
- The representation of promised ETA and actual delivery timestamps.

Where an interpretation is necessary, it must be documented and tested. Any clarification provided by the project organizers takes precedence over an assumption.

## 14. Project Milestones

| Milestone                | Target date     | Expected outcome                                                       |
| ------------------------ | --------------- | ---------------------------------------------------------------------- |
| Design catch-up          | 27 October 2026 | Design document, glossary, diagrams, and business-rule register        |
| Core flows               | 30 October 2026 | Working allocation flow, sample commands, tests, and shared repository |
| Full rule implementation | 6 November 2026 | All business rules and required edge cases implemented and tested      |
| Final submission         | To be announced | Final documentation, decision log, test suite, and demonstration       |
| Viva                     | To be announced | Individual explanation and live code modification                      |

## 15. Completion Criteria

The implementation is considered complete when:

- All twelve business rules are implemented.
- All specified edge cases are addressed.
- The automated test suite passes.
- The command-file interface supports the required operations.
- The order trace contains the required information.
- The README provides working setup and execution instructions.
- The design documentation reflects the final implementation.
- The developer can explain and modify the code during evaluation.
