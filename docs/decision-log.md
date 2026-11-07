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


## DEC-13 — Weather Hold Operational Rules

**Date:** 5 November 2026
**Decision:** For BR-11 (Weather Hold):
1. **Hold Duration:** A weather hold blocks new order allocations at the affected dark store for exactly 20 minutes (`HOLD_DURATION_MINUTES = 20`), expiring at $T_{\text{hold\_until}} = T_{\text{start}} + 20\text{ minutes}$.
2. **Evaluation Boundary:** In accordance with BR-01, an order allocation is blocked while $T_{\text{hold\_until}} > T_{\text{eval}}$. At the exact expiration boundary ($T_{\text{eval}} == T_{\text{hold\_until}}$), the hold is no longer active, and allocation is permitted.
3. **Overlapping Holds:** If a subsequent weather hold event is triggered for a store whose existing `weather_hold_until` expires later than the proposed expiration ($T_{\text{existing}} > T_{\text{new}}$), the existing later expiration is preserved and never shortened.
4. **Order Eligibility for ETA Extension:** Orders allocated to the affected store receive a 10-minute delivery promise extension (`ETA_EXTENSION_MINUTES = 10`) if and only if:
   - `order.allocated_store_id == store.store_id`
   - `order.status` is in `{ALLOCATED, PICKING, READY_FOR_DISPATCH}`
   Orders in states `RECEIVED`, `REJECTED`, `DISPATCHED`, `DELIVERED`, or `CANCELLED` are not extended.
5. **Complete Atomicity & Explicit Datetime Validation:** Every eligible order must have a valid `datetime` `promised_eta` and matching timezone parity with the event timestamp. Store `weather_hold_until` is validated as a `datetime` or `None`. Hold expiration and all order ETA extensions are pre-calculated before applying any mutations; if any calculation fails (including `OverflowError`), the operation aborts with zero mutations to the store or any candidate orders.
6. **SLA Compensation Exemption:** The 10-minute weather extension modifies `order.promised_eta` directly. Because BR-09 SLA compensation is evaluated against the order's final `promised_eta`, deliveries made within the extended delivery window do not incur SLA compensation penalties.
7. **Idempotency & Payload Consistency:** `WeatherHoldService` maintains an event registry (`_processed_events`) keyed by explicit `event_id` or deterministic fallback (`{store_id}:{started_at.isoformat()}`). If an existing event ID is reused, both `store_id` and `started_at` are verified against the original event (raising `ValueError` if either differs), preventing corrupted event reuse while safely preserving idempotency for exact duplicate invocations.
**Reason:** Ensures customer safety, rider safety, and realistic delivery commitments during severe weather disruptions without generating false SLA compensation claims or corrupted scheduling state.
**Alternatives:** Dynamic hold durations; extending dispatched orders in transit; shifting SLA compensation calculation baselines.
**Status:** Accepted.

## DEC-14 — Picking-Start and ETA Calculation (BR-04 Alignment with Mentor Specification)

**Date:** 5 November 2026
**Decision:** For BR-04 (ETA Calculation) and BR-03 (Picking Capacity):
1. **Picking-Start Definition:** In adherence to the mentor sample-data specification ("picking of an order starts at the later of its allocation time and its slot start"), picking start is defined as:
   $$T_{\text{picking\_start}} = \max(T_{\text{base}}, T_{\text{assigned\_slot\_start}})$$
   where $T_{\text{base}}$ is the effective order arrival/reference timestamp.
2. **Promised Delivery ETA:**
   $$T_{\text{promised\_eta}} = T_{\text{picking\_start}} + t_{\text{picking}} + t_{\text{travel}} + t_{\text{earlier\_drops}}$$
   where $t_{\text{picking}} = 2\text{ min}$, $t_{\text{travel}} = \lceil 1.5 \times \text{distance\_km} \rceil\text{ min}$, and $t_{\text{earlier\_drops}} = 2 \times N_{\text{earlier\_drops}}\text{ min}$.
3. **Slot Overflow Behavior:** For orders overflowing into subsequent 10-minute slots due to picking capacity exhaustion (such as EC-03), picking begins at the start of that assigned slot rather than shifting the order arrival time by 10 minutes.
4. **Non-Overflow Consistency:** For orders assigned to their current slot without overflow, $T_{\text{assigned\_slot\_start}} \le T_{\text{base}}$, resulting in $T_{\text{picking\_start}} = T_{\text{base}}$, preserving identical behavior with prior calculations.
5. **Backward Compatibility:** `calculate_eta` accepts optional `assigned_slot_start: datetime | None = None`. When omitted, it safely falls back to $T_{\text{picking\_start}} = T_{\text{base}} + \Delta t_{\text{slot\_delay}}$.
6. **Supersession:** This decision clarifies and supersedes item 4 of DEC-10 regarding the calculation base for slot-overflowed orders.
**Reason:** Matches the mentor's official sample-data specification and real dark store physical operations where store staff pick orders during their scheduled slot.
**Alternatives:** Adding 10 minutes to the order arrival timestamp for each slot overflow; starting picking before the assigned slot window.
**Status:** Accepted.

## DEC-15 — Progressive Individual-Order Delivery and Post-Dispatch Cancellation

**Date:** 5 November 2026
**Decision:** For BR-08 (Rider Batching) and BR-12 (Cancellation):
1. **Progressive Delivery Execution:** A dispatched delivery batch can have its individual orders delivered progressively via `complete_order_delivery(batch, order, rider, delivered_at)`.
2. **Intermediate Batch and Rider State:** Delivering an intermediate drop transitions only that order to `OrderState.DELIVERED` and records its `actual_delivery_time`. The batch remains in `BatchStatus.DISPATCHED`, the rider remains in `RiderStatus.BUSY`, and active batch tracking is retained.
3. **Terminal Batch State and Rider Release:** The batch status transitions to `BatchStatus.DELIVERED` and the assigned rider transitions to `RiderStatus.AVAILABLE` (with tracking mappings removed) if and only if all orders in `batch.orders` have reached terminal states (`DELIVERED` or `CANCELLED`).
4. **Post-Dispatch Cancellation:** When an individual order within a dispatched batch is cancelled via `cancel_order_in_batch(batch, order)`, the cancelled order transitions to `OrderState.CANCELLED`, but remains in `batch.orders` for dispatch history. The rider is NOT released and the batch is NOT cancelled while other active orders remain on the bike.
5. **Pre-Dispatch Cancellation:** If an order in a `CREATED` batch is cancelled prior to dispatch, it is removed from `batch.orders` and its original promised ETA is restored. If the batch becomes empty, the batch is cancelled and the rider is released.
6. **Backward Compatibility:** `complete_delivery(batch, rider, delivered_at)` continues to deliver all remaining active orders in the batch simultaneously, fully supporting batch-level completion.
7. **Cancellation Idempotency and Object Identity:** `cancel_order_in_batch()` caches the original `OrderCancellationResult` per `(batch_id, order_id)`. Subsequent cancellation invocations return the exact cached result (preserving `rider_released`, reason, and batch status) after strictly validating batch and order object identity, preventing different objects with matching identifiers from receiving cached cancellation results.
**Reason:** Adheres strictly to the mentor's actual dispatch and delivery logs (`delivery_events.csv`), where riders deliver multiple batched drops at distinct timestamps sequentially without releasing the rider between drops.
**Alternatives:** Releasing rider on the first delivered drop; failing on individual delivery timestamps; cancelling entire batch when a single order is cancelled post-dispatch.
**Status:** Accepted.

## DEC-16 — Customer Cancellation (BR-12) Architecture, Refund Ledger, and Reservation Safety

**Date:** 6 November 2026
**Decision:** For customer-initiated order cancellation under BR-12:
1. **Ownership Decoupling:** `CancellationService` owns BR-12 eligibility, cancellation fees, refund calculation, and multi-service cancellation coordination. `LifecycleTransitionService` owns state transitions. `InventoryService` remains the sole authority for inventory releases. `BatchingService` owns batch drop removal and rider tracking.
2. **Refund Ledger & Failure Preservation:** `_refund_ledger` stores immutable `RefundRecord` objects keyed by `order_id`. A calculated and recorded refund is preserved even if downstream inventory, lifecycle, or batch operations fail. Retries reuse the recorded refund without duplication.
3. **In-Memory Limitations:** The refund ledger and operational retry cache are in-memory data structures. They are not persistent across application restarts or crash-safe. External payment gateway integration is outside the capstone scope.
4. **Operation Fingerprinting:** `CancellationOperationState` tracks granular workflow completion and request fingerprints (order instance, batch instance, amount_paid, reservation IDs, reason, cancelled_at, and picking_start). Retries with altered financial or operational inputs are rejected.
5. **Reservation Handling & Discovery:** `InventoryService.get_reservations_for_order(order_id)` discovers reservations when IDs are omitted. Releasing distinguishes `COMMITTED` (never released), `ALREADY_RELEASED` (skipped without duplicate stock restoration), and `UNKNOWN` (missing reservation ID). For active inventory-holding states (`ALLOCATED`, `PICKING`, `READY_FOR_DISPATCH`), an error is raised if no reservations are found and none are provided, preventing silent skip. Dark store reservations are not released for `DISPATCHED` orders.
6. **Lifecycle and Batching Execution Order:** The execution sequence is: (1) Validate & Fingerprint, (2) Calculate, (3) Record in `_refund_ledger`, (4) Release eligible reservations, (5) Transition to `CANCELLED` via `LifecycleTransitionService`, (6) Update batch via `BatchingService.cancel_order_in_batch()`, (7) Mark `COMPLETED` and return immutable `CancellationResult`. Because both services check `order.status != OrderState.CANCELLED`, duplicate transitions are avoided.
7. **SIMILAR_ONLY Catalog Metadata Limitation:** `OrderItem` records only the substitute SKU string, not its catalog brand or category. When catalog metadata is provided, `CancellationService` verifies similarity violations. When catalog metadata is unavailable, compliance is presumed because `SubstitutionService` enforces `SIMILAR_ONLY` during fulfillment.
**Reason:** Strictly satisfies mentor BR-12 requirements, ensures complete financial and inventory safety, prevents premature rider release, and supports safe retry resumption.
**Alternatives:** Direct inventory manipulation in CancellationService; duplicate lifecycle transitions; classifying all SIMILAR_ONLY substitutions as unconsented; hardcoding rider release on single drop cancel.
**Status:** Accepted.

## DEC-17 — Rider Penalties (BR-10) Architecture, Date-Aware Cap Ledger, and Bonus Loss

**Date:** 6 November 2026
**Decision:** For BR-10 (Rider Penalties):
1. **Service Decoupling:** Implemented a dedicated `RiderPenaltyService` implementing `RiderPenaltyServiceInterface` (Protocol), completely separate from customer-facing BR-09 `CompensationService`. `CompensationService` mutates customer order wallet credits and free coupons, whereas `RiderPenaltyService` evaluates rider batch bonus eligibility and disciplinary deductions.
2. **Batch Bonus Treatment as Forfeited Incentive:** A completed batch with $\ge 2$ late delivered orders loses its ₹20 batch bonus (`bonus_lost = True`, `batch_bonus_awarded = Decimal("0.00")`). This is an unearned incentive forfeiture, not a disciplinary deduction, and is never added to `Rider.daily_penalty`. A batch with 3 late orders loses the single ₹20 bonus once.
3. **Threshold Exactness:** An order is late when $\Delta t = (T_{\text{actual}} - T_{\text{promised\_eta}}) > 0\text{ s}$. An order incurs a ₹30 deduction only when strictly more than 15 minutes late ($\Delta t > 900.0\text{ s}$). Exactly 15 minutes late ($\Delta t = 900.0\text{ s}$) does not incur the ₹30 deduction.
4. **Delivery Date Grouping and Midnight Crossings:** Deductions are strictly keyed by each order's own `actual_delivery_time.date()`. In batches crossing midnight, orders delivered before midnight are capped and recorded under that date, while orders delivered after midnight are capped and recorded under the next date.
5. **Date-Aware Rider Model Extension & Date Regression Prevention:** Added `daily_penalty_date: date | None = None` to the `Rider` dataclass. The service seeds its daily ledger from an existing `rider.daily_penalty` if and only if `rider.daily_penalty_date` matches the order's delivery date, ensuring penalties from prior days do not improperly exhaust subsequent daily caps. Furthermore, `Rider.daily_penalty` and `Rider.daily_penalty_date` are only updated when `rider.daily_penalty_date is None` or the batch's latest affected delivery date is $\ge \text{rider.daily\_penalty\_date}$, preserving Rider fields unchanged when processing older batches out of chronological order.
6. **Cancelled Order Exclusion:** Cancelled orders (`OrderState.CANCELLED`) have no delivery timestamps and do not count as delivered, late, or penalty-incurring drops.
7. **Idempotency and Strengthened Input Fingerprinting:** Repeated calls to `apply_batch_penalties()` for the same batch ID return the cached `BatchPenaltyResult` without increasing the ledger or mutating rider state. Reusing a batch ID with altered parameters is strictly rejected via input fingerprinting covering `batch.created_at`, `batch.dispatched_at`, `delivered_at` argument, `batch_id`, `rider_id`, `batch.status`, and sorted order details with stable ISO 8601 formatting. Mutable rider penalty fields are excluded from the fingerprint to preserve legitimate idempotency across calls.
8. **Atomic Validation and Duplicate Order Detection:** All batch orders, lifecycle statuses (must be `DELIVERED` or `CANCELLED` within a delivered batch), timestamps, timezone parity, duplicate order IDs, and pre-mutation Rider penalty state (finite non-negative Decimal and date/None) are validated upfront. Any failure aborts processing before mutating `Rider` or the in-memory daily ledger. Thread-safety across concurrent processes is not claimed and is outside the in-memory capstone scope.
**Reason:** Satisfies BR-10 requirements with complete mathematical and financial rigor, preserves domain model separation of concerns, and prevents double-penalizing or multi-day leakage.
**Alternatives:** Adding rider disciplinary penalties to CompensationService; treating batch bonus loss as an added ₹20 deduction; anchoring all orders in a batch to the batch creation date.
**Status:** Accepted.

## DEC-18 — Command-File Schema, Application Orchestrator, and Execution Trace Integration

**Date:** 6 November 2026
**Decision:** For GitHub Issue #27 (JSONL Command Processor):
1. **Envelope Specification and Timestamp Semantics:** All input lines must be valid JSON objects with the standard envelope structure: `{"command": "<VERB>", "timestamp": "<ISO-8601>", "payload": { ... }}`. The envelope `timestamp` serves as the authoritative default event timestamp for any time-sensitive command (`order_time`, `created_at`, `current_time`, `dispatched_at`, `delivered_at`, `hold_start`, `cancelled_at`). Explicit payload timestamps override the envelope timestamp. This eliminates uncontrolled `datetime.now()` non-determinism during offline log replay. Results are emitted as single-line JSON objects: `{"line_number": int, "command": str, "status": "SUCCESS" | "ERROR", "result": dict | None, "error": dict | None}`.
2. **Physical Line Number Tracking and Blank Line Skipping:** Each input physical line is enumerated starting from 1. Completely blank or whitespace-only lines are ignored without producing output records, but they advance the physical line counter so error and success reports match the input file's line numbering exactly.
3. **Fault Isolation and Stream Continuity:** An error encountered on line $N$ (e.g., malformed JSON syntax, invalid envelope schema, missing payload arguments, or business rule validation errors) generates an `ERROR` record for line $N$ with structured error codes (`MALFORMED_JSON`, `INVALID_ENVELOPE`, `UNKNOWN_COMMAND`, `INVALID_ARGUMENT`, `VALIDATION_ERROR`, `LIFECYCLE_ERROR`, `ENTITY_NOT_FOUND`, `EXECUTION_ERROR`) and diagnostics. Processing continues uninterrupted for line $N+1$, and previous valid domain state is preserved.
4. **Decimal-Safe and Deterministic Serialization:** Monetary values in command results are serialized as fixed two-decimal strings (e.g., `"50.00"`) via `serialize_record()` using a custom `DecimalSerializer` with `sort_keys=True`. Floating point precision issues and standard JSON parser rejections of Python `Decimal` objects are prevented.
5. **Architectural Separation of Concerns:** Kept command stream parsing (`JSONLCommandProcessor`), workflow orchestration (`ApplicationOrchestrator`), and domain services strictly decoupled:
   - `JSONLCommandProcessor` is solely responsible for reading streams, parsing JSON envelopes, validating payload schemas via `sig.bind()`, formatting structured error records, and serializing output records. User argument binding errors are classified as `INVALID_ARGUMENT`, domain validation errors as `VALIDATION_ERROR`, missing entities as `ENTITY_NOT_FOUND`, lifecycle transition failures as `LIFECYCLE_ERROR`, and unexpected internal programming `TypeError`s or faults as `EXECUTION_ERROR` with full tracebacks on stderr.
   - `ApplicationOrchestrator` centralizes the lifecycle of domain entities (`Product`, `Store`, `Rider`, `Order`, `Batch`) and coordinates workflows across existing business services without reimplementing or altering domain rules.
   - Domain services (`InventoryService`, `AllocationService`, `PickingService`, `PricingService`, `FulfilmentOrchestrator`, `WeatherHoldService`, `BatchingService`, `CompensationService`, `RiderPenaltyService`, `CancellationService`, `LifecycleTransitionService`) remain the authoritative executors for their respective business rules (BR-01 through BR-12).
6. **Atomic Entity Registration and Order Submission Rollback:**
   - **Duplicate Registration Safety:** In `register_product()` and `register_store()`, boolean mutation-tracking flags (`inv_registered`, `pricing_registered`, `orch_registered`) ensure that rollback blocks strictly undo mutations made during the *current* invocation. If duplicate registration is rejected (either upfront or by underlying service), rollback never deletes or corrupts pre-existing products, stores, or their configured attributes.
   - **Atomic Order Submission & Allocation Rollback:** Entity duplicate checks (`sku`, `store_id`, `rider_id`, `order_id`, `batch_id`) and item catalog validations run upfront before mutating state. If an error occurs during stock reservation or picking/ETA computation after store allocation, all created reservations are released in reverse order via `InventoryService.release_reservation()`, picking slot schedules are reversed via `release_order_schedule()`, allocation attributes (`allocated_store_id`, `pricing_frozen`, `delivery_fee`, `surge_fee`, `promised_eta`, `delayed_promise`) are cleared, the order status is set to `OrderState.REJECTED`, and the order is removed from `self.orders`. `AllocationService` maintains no internal order registry, leaving no orphan orders or stale allocation state anywhere in the system.
   - **Rollback Transparency:** If any release fails during reservation rollback, failures are recorded and `ReservationRollbackError` is raised chaining the primary cause and logging critical diagnostics to stderr. Compensation limits: Rollback is in-memory and non-distributed; it protects local dark store stock consistency within single-process execution.
7. **Reservation Commit Strictness and Retry Recovery:** Removed blanket exception swallowing around `commit_reservation()`. Reservations are queried across active statuses (`PENDING` and `COMMITTED`), ignoring released reservations. If a commit fails mid-order, the order remains in `PICKING` and can be retried safely without re-releasing or duplicating already-committed lines.
8. **Authoritative Batch Store Ownership and Contamination Prevention:** `ApplicationOrchestrator.batch_stores` is the sole source of truth for batch store ownership, with no fallback to private batching service internals. Empty batches, batches referencing unregistered stores, and cross-store order contamination are strictly rejected before dispatch.
9. **Delivery Timestamp Preservation and Compensation Idempotency:** Delivery completion commands (`COMPLETE_ORDER_DELIVERY` and `COMPLETE_BATCH_DELIVERY`) are fully idempotent. Retrying delivery preserves the original `actual_delivery_time` recorded during the first successful drop, never overwriting it with the retry timestamp. SLA compensations recover cleanly on retry without duplicating wallet credits or free coupons.
10. **FR-11 Execution Trace Integration Point:** Implemented `GET_ORDER_TRACE` as the baseline integration hook exposing order lifecycle state, allocated store, promised ETA, actual delivery time, order values, delivery fees, cancellation fees, refunds, and SLA compensations. Full event-driven execution trace reporting remains scoped to Issue #28.
11. **CLI Standard Stream and File Handling:** `main.py` supports input from a file or stdin and output to a file or stdout (`-o` / `--output`). Informational diagnostics and unexpected execution errors are routed strictly to stderr, leaving stdout clean for pipeable JSONL streaming.
**Reason:** Satisfies FR-01 and Issue #27 requirements, enables headless pipeline execution and automated testing, provides fault tolerance for corrupted input streams, and cleanly integrates with existing domain architecture.
**Alternatives:** Embedding JSON parsing directly into domain services; halting execution upon first syntax error; using float for monetary currency output; mutating global service singletons.
**Status:** Accepted.
