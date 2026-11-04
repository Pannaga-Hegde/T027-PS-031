# PS-031 Decision Log

This document records technical decisions, design choices, requirement interpretations, and significant changes made during development.

Decisions must reflect the actual choices made by the team. Unresolved questions remain open until a decision is made.

## DEC-01 — Programming Language

**Date:** 26 October 2026
**Decision:** Use Python 3.11.15.
**Reason:** Python is available in the development environment and supports the required business logic, command parsing, and automated testing.
**Alternatives:** Java 17 or a newer Python version.
**Status:** Accepted.

## DEC-02 — Automated Testing

**Date:** 26 October 2026
**Decision:** Use pytest for automated tests.
**Reason:** pytest supports readable tests for business rules, boundary conditions, and service interactions.
**Alternatives:** Python's built-in unittest framework.
**Status:** Accepted (pytest 9.1.1 verified in development virtual environment with active automated test suite).

## DEC-03 — Application Architecture

**Date:** 26 October 2026
**Decision:** Use separate modules for domain models, business services, command parsing, and shared utilities under `src/ps031/`.
**Reason:** Separation of responsibilities makes the implementation easier to test, maintain, and explain during the viva.
**Alternatives:** Implement all functionality in a single file or combine parsing and business logic.
**Status:** Accepted.

## DEC-04 — Initial Data Storage

**Date:** 26 October 2026
**Decision:** Use in-memory application state for the initial implementation unless project requirements require persistence.
**Reason:** The project is command-file driven, and the supplied requirements do not explicitly require a production database.
**Alternatives:** SQLite or another database.
**Status:** Proposed; confirm against evaluation requirements.

## DEC-05 — Geographic Distance

**Date:** 26 October 2026
**Decision:** Implement the Haversine formula in a shared utility module (`src/ps031/utils/distance.py`) and apply the specified two-decimal-place rounding consistently.
**Reason:** The distance calculation is reused by serviceability (BR-01) and batching (BR-08).
**Alternatives:** Duplicate the formula in each service.
**Status:** Accepted.

## DEC-06 — Monetary Arithmetic

**Date:** 26 October 2026
**Decision:** Use Python Decimal for monetary calculations.
**Reason:** Decimal arithmetic avoids common binary floating-point representation errors in financial calculations.
**Alternatives:** Use binary floating-point numbers for all amounts.
**Status:** Accepted (implemented in `OrderItem` model with finite Decimal validation).

## DEC-07 — Command-File Format

**Date:** 26 October 2026
**Decision:** JSON Lines is the proposed initial command-file format.
**Reason:** Each command can be represented as a separate JSON object, making parsing and input validation straightforward.
**Alternatives:** Custom text commands or a single JSON document.
**Status:** Proposed; confirm that the format satisfies the evaluation requirements.

## DEC-08 — Unresolved Interpretations and Ambiguities

**Date:** 29 October 2026
**Topic:** Clarification of operational formulas and tie-breaking rules.
**Items Requiring Mentor Guidance:**
1. **Line-Fill Calculation (BR-02):** The glossary states line-fill requires an exact operational definition. Candidates are:
   - SKU line count ratio: $\frac{\text{fulfilled distinct SKUs}}{\text{requested distinct SKUs}}$
   - Unit quantity ratio: $\frac{\text{fulfilled units}}{\text{requested units}}$
   *(Provisional implementation assumption adopted in DEC-09 pending mentor confirmation: unit quantity ratio with per-SKU line aggregation).*
2. **Deterministic Tie-Breaking (BR-02):** When stores tie on stock, distance, and picking load, confirm if lexicographical `store_id` is the final tie-breaker.
   *(Provisional implementation assumption adopted in DEC-09 pending mentor confirmation: lexicographical `store_id` ascending).*
3. **Dispatch of Single Orders (BR-08):** If an order cannot be batched with any nearby order, confirm whether it dispatches immediately upon picking completion or after the 3-minute waiting timer.
   *(Provisional implementation assumption adopted in DEC-11: a 3-order batch dispatches immediately, while a partial batch dispatches 3 minutes after the first order reaches READY_FOR_DISPATCH).*
4. **Command Schema & Output (FR-11):** Confirm exact command names and whether the order trace is expected on standard output or written to an output file.
**Status:** Open; documented for mentor review.

## DEC-09 — Store Allocation Line-Fill and Deterministic Tie-Breaking

**Date:** 3 November 2026
**Decision:** For BR-02 store selection:
1. Calculate line-fill ratio using the unit quantity formula $\text{line\_fill\_ratio} = \frac{\text{total\_fulfillable\_units}}{\text{total\_requested\_units}}$, aggregating requested quantities per SKU across order lines before evaluating against store inventory.
2. Break ties using:
   - Shortest rounded Haversine distance (ascending),
   - Then lowest current slot load (ascending, defaulting missing store entries to 0),
   - Finally, lexicographically smallest `store_id` (ascending).
**Reason:** Provides deterministic, reproducible order allocation (NFR-02) and prevents duplicate order lines from multi-counting available stock.
**Alternatives:** SKU line-count ratio; arbitrary non-deterministic selection.
**Status:** Provisional implementation assumption pending mentor confirmation (does not claim mentor approval).

## DEC-10 — Picking Slot Scheduling and ETA Calculation

**Date:** 3 November 2026
**Decision:** For BR-03 (Picking Capacity) and BR-04 (ETA Calculation):
1. **Clock-Aligned Slots:** Picking capacity is managed across clock-aligned 10-minute intervals $[t_{\text{start}}, t_{\text{end}})$ with capacity defaulting to `Store.picking_capacity` (12 orders).
2. **Discrete Orders Counted:** Slot occupancy tracks discrete order counts rather than item line or item unit quantities.
3. **Cascading Overflow:** When a slot's capacity is reached, orders roll forward by 10 minutes into subsequent slots until capacity is found, accumulating a 10-minute delay per overflow iteration.
4. **ETA Calculation Base & Timestamp Precedence:** When explicitly provided, evaluation `reference_time` takes precedence over `order.order_time` as the base timestamp for slot scheduling and promised delivery ETA calculation. Neither calculation ever uses `assigned_slot_start` as base.
5. **Operating Hours vs. Slot Overflow:** Store operating hours govern initial order acceptance. An already accepted order experiencing picking overflow is permitted to be scheduled into a picking slot beginning after store closing.
6. **Global Order Uniqueness:** An order has at most one active picking assignment globally across all dark stores; attempting to schedule an already-scheduled order at a different store is rejected, while re-scheduling at the same store is idempotent.
**Reason:** Ensures deterministic, reproducible scheduling and delivery promises across all dark stores while maintaining SLA calculation consistency.
**Alternatives:** Counting item lines or total unit quantities toward picking capacity; resetting slot overflow at closing time and rejecting orders.
**Status:** Provisional implementation assumption pending mentor confirmation (does not claim mentor approval).

## DEC-11 — Rider Batching Operational Rules

**Date:** 3 November 2026
**Decision:** For BR-08 (Rider Batching):
1. **Batch Capacity:** A delivery batch accommodates a maximum of 3 orders ($N \le 3$).
2. **Pairwise Geographic Proximity:** Delivery locations for all orders in a batch must satisfy an all-pairs distance constraint: the unrounded Haversine distance ($R = 6371.0\text{ km}$) between every pair of drop coordinates must be $\le 1.00\text{ km}$ (inclusive). Unrounded distances prevent rounding artifacts from admitting drops beyond 1.00 km.
3. **Promised ETA Compatibility:** The maximum difference between the earliest and latest initial promised delivery ETAs across all orders in a batch must be $\le 4\text{ minutes}$ ($240\text{ seconds}$, inclusive). Orders with missing promised ETAs are rejected.
4. **Deterministic Drop Sequencing:** Drops are ordered deterministically by:
   - Shortest distance from the dark store location to customer delivery location (ascending),
   - Then earliest `order.promised_eta` (ascending),
   - Finally, lexicographically smallest `order.order_id` (ascending).
5. **Drop Delay & ETA Recalculation:** Drop sequence indices determine `earlier_drops` ($0, 1, 2$). Each drop's ETA is updated using `PickingService.recalculate_eta(store, order, earlier_drops)` to preserve slot delay while applying the +2-minute incremental drop penalty. If a candidate order or batch proposal is rejected, original ETAs are preserved/restored.
6. **Dispatch Timing Rules:**
   - A batch of 3 orders dispatches immediately upon picking completion / `READY_FOR_DISPATCH` without a waiting timer.
   - A partial batch (1 or 2 orders) must wait until 3 minutes after the first order reaches `READY_FOR_DISPATCH` ($t_{\text{dispatch}} \ge t_{\text{first\_ready}} + 3\text{ minutes}$).
7. **Single Store & Global Active Uniqueness:** All orders in a batch must share the same `allocated_store_id`. An order or rider may belong to at most one active (`CREATED` or `DISPATCHED`) batch globally.
8. **Atomic Lifecycle Transitions:** Dispatch validates all order transitions (`READY_FOR_DISPATCH` $\to$ `DISPATCHED`), rider availability, exact assigned rider object identity, and timestamps before mutating any domain entity. Upon delivery completion, orders transition to `DELIVERED` with `actual_delivery_time`, the batch becomes `DELIVERED`, and the rider is released to `AVAILABLE`. Exact rider object identity is enforced across dispatch, delivery completion, and cancellation when tracked. `Store.active_riders` is left unmodified pending coordination with BR-07.
**Reason:** Ensures safe, predictable, reproducible rider dispatching and SLA guarantees without violating dark store allocation, picking capacity, or vehicle capacity constraints.
**Alternatives:** TSP route optimization; dynamic re-clustering; rounded Haversine distance checks.
**Status:** Accepted.

## DEC-12 — SLA Compensation and Lateness Rules

**Date:** 4 November 2026
**Decision:** For BR-09 (SLA Compensation):
1. **Lateness Determination:** Lateness is measured as $\Delta t = T_{\text{actual}} - T_{\text{promise}}$, calculated against the order's final `promised_eta` using `actual_delivery_time`.
2. **Compensation Tiers:**
   - On-time or less than 5 minutes late ($\Delta t < 300\text{ s}$): ₹0 wallet credit, no coupon.
   - Exactly 5 through exactly 10 minutes late ($300\text{ s} \le \Delta t \le 600\text{ s}$): ₹25 wallet credit (`Decimal("25.00")`), no coupon.
   - More than 10 minutes late ($\Delta t > 600\text{ s}$): ₹50 wallet credit (`Decimal("50.00")`) and a free-delivery coupon (`free_coupon = True`).
3. **Delayed-Promise Exemption:** Orders flagged with `delayed_promise = True` receive no compensation when lateness is $\le 5\text{ minutes}$ ($\Delta t \le 300\text{ s}$). When lateness strictly exceeds 5 minutes, standard compensation brackets apply without shifting the brackets ($5\text{m} < \Delta t \le 10\text{m} \to ₹25$; $\Delta t > 10\text{m} \to ₹50\text{ + coupon}$).
4. **Preservation of Existing Wallet Credits:** When compensation is awarded, the credit is added to `order.wallet_credit` rather than overwriting existing balances.
5. **Idempotency and Audit Ledger:** `CompensationService` maintains an order-ID-based ledger (`_compensations`) preventing duplicate compensation on repeated invocations.
6. **Preconditions & Immutability:** Calculating compensation does not mutate the `Order`. Applying compensation strictly requires `OrderState.DELIVERED`, validated timestamps, and timezone parity.
7. **Monetary Precision:** All compensation calculations and wallet adjustments strictly use Python `Decimal`.
**Reason:** Ensures customer SLA guarantees, accurate financial adjustments, and reproducible order traces without double-crediting or violating domain lifecycle boundaries.
**Alternatives:** Floating-point monetary arithmetic; shifting brackets for delayed promises; overwriting wallet balances; calculating lateness against unadjusted initial ETAs.
**Status:** Accepted.
