# PS-031 Business-Rule Register

**Project:** Dark-Store Order Allocation with 10-Minute SLA, Rider Batching and Substitutions

**Status definitions:**

* `NOT IMPLEMENTED` — The rule is documented but not implemented.
* `IN PROGRESS` — Implementation has started.
* `IMPLEMENTED` — The code has been written.
* `TESTED` — Required tests pass for the rule.
* `VERIFIED` — Implementation, tests, and documentation have been reviewed together.

## BR-01 — Serviceability

**Requirement:** A store is serviceable only when the order is within 3.0 km and the order time is inside the store's operating hours. Calculate distance using the Haversine formula and round to two decimal places.

**Implementation module:** `allocation_service.py`, `distance.py`

**Required tests:**

* Order at exactly 3.00 km.
* Order beyond 3.00 km after rounding.
* Order inside operating hours.
* Order outside operating hours.
* Invalid geographic coordinates.

**Status:** NOT IMPLEMENTED

## BR-02 — Store Selection

**Requirement:** Prefer serviceable stores with 100% stock. Break ties by lowest distance and then lowest current-slot load. If no serviceable store has 100% stock, select the store with the highest line-fill percentage.

**Implementation module:** `allocation_service.py`

**Required tests:**

* Complete stock versus incomplete stock.
* Distance tie-break.
* Picking-load tie-break.
* Two stores tied on stock, distance, and load.
* No store with complete stock.
* Deterministic final tie-break.

**Status:** NOT IMPLEMENTED

## BR-03 — Picking Capacity

**Requirement:** A store accepts at most its configured picking capacity per 10-minute slot, defaulting to 12 orders. When the slot is full, the order rolls into the next slot and its ETA shifts by 10 minutes.

**Implementation module:** `picking_service.py`

**Required tests:**

* Default capacity.
* Configured capacity.
* Capacity reached exactly.
* Slot overflow.
* Order placed near store closing time.
* Correct ETA adjustment.

**Status:** NOT IMPLEMENTED

## BR-04 — ETA and Delayed Promise

**Requirement:** Promised ETA is calculated using two minutes for picking, 1.5 minutes per kilometre rounded up to a minute, and two minutes per earlier drop in the same batch. If ETA exceeds 10 minutes and the customer has not opted into slow delivery, set `DELAYED_PROMISE` and pre-arm SLA compensation.

**Implementation module:** `picking_service.py`, `allocation_service.py`, `batching_service.py`

**Required tests:**

* ETA calculation at zero distance.
* Fractional travel-time rounding.
* ETA exactly 10 minutes.
* ETA greater than 10 minutes.
* Slow-delivery preference.
* Earlier-drop adjustment.

**Status:** NOT IMPLEMENTED

## BR-05 — Substitution

**Requirement:** `ALLOW` permits an eligible product in the same category with a price within +15% of the original. The customer pays the original price. `SIMILAR_ONLY` permits only the same brand. `NONE` converts the shortfall into a partial line. A substituted line never costs more than the original.

**Implementation module:** `substitution_service.py`, `inventory_service.py`

**Required tests:**

* Candidate priced exactly 15% above original.
* Candidate above the allowed threshold.
* Candidate in the wrong category.
* Different brand under `SIMILAR_ONLY`.
* `NONE` preference.
* Substitute never charged above original price.
* Multiple eligible candidates.

**Status:** NOT IMPLEMENTED

## BR-06 — Partial Fulfilment

**Requirement:** If fulfilled value drops below 60% of original order value, cancel before dispatch and issue a full refund with no delivery fee.

**Implementation module:** `cancellation_service.py`, `substitution_service.py`

**Required tests:**

* Fulfilled value below 60%.
* Fulfilled value exactly 60%.
* Fulfilled value above 60%.
* Full refund.
* No delivery fee on threshold cancellation.
* Cancellation occurs before dispatch.

**Status:** NOT IMPLEMENTED

## BR-07 — Delivery Fee and Surge

**Requirement:** Delivery fee is ₹0 when post-substitution order value is at least ₹199; otherwise ₹25. Add a ₹15 surge when active riders are fewer than three at allocation time. Freeze the fee at allocation.

**Implementation module:** Pricing logic within the order or allocation service.

**Required tests:**

* Post-substitution value exactly ₹199.
* Value below ₹199.
* Fewer than three active riders.
* Exactly three active riders.
* Fee remains frozen after allocation.

**Status:** NOT IMPLEMENTED

## BR-08 — Rider Batching

**Requirement:** A rider may carry up to three orders whose drop points are within 1.0 km of each other and whose promised ETAs differ by no more than four minutes. A batch dispatches when full or three minutes after its first order is picked, whichever is earlier.

**Implementation module:** `batching_service.py`

**Required tests:**

* One-, two-, and three-order batches.
* Fourth order cannot join a full batch.
* Drop points exactly 1.0 km apart.
* Promised ETAs exactly four minutes apart.
* Third order arrives 4 minutes and 1 second after the first.
* Dispatch after three minutes from first picking.
* Dispatch when the batch becomes full.

**Status:** NOT IMPLEMENTED

## BR-09 — SLA Compensation

**Requirement:** Delivery later than promised ETA by 5–10 minutes receives ₹25 wallet credit. More than 10 minutes late receives ₹50 wallet credit plus a free delivery coupon. A `DELAYED_PROMISE` order receives no compensation when lateness is within five minutes of the longer promise.

**Implementation module:** `compensation_service.py`

**Required tests:**

* Exactly five minutes late.
* Exactly ten minutes late.
* More than ten minutes late.
* Delayed-promise exception.
* Wallet credit and coupon are recorded separately.
* Weather-hold extension is not incorrectly treated as customer-compensable lateness.

**Status:** NOT IMPLEMENTED

## BR-10 — Rider Penalty

**Requirement:** A rider who is late on two or more orders in a batch loses the ₹20 batch bonus. A rider more than 15 minutes late on an order receives a ₹30 deduction, capped at ₹90 per day.

**Implementation module:** `compensation_service.py`

**Required tests:**

* Zero or one late order in a batch.
* Two late orders in a batch.
* Exactly 15 minutes late.
* More than 15 minutes late.
* Daily penalty cap.
* Multiple batches for the same rider.

**Status:** NOT IMPLEMENTED

## BR-11 — Weather Hold

**Requirement:** A weather hold stops new allocations to a store for 20 minutes. Orders already allocated there receive a 10-minute ETA extension without compensation for that extension.

**Implementation module:** `allocation_service.py`, `picking_service.py`

**Required tests:**

* New allocation during a hold.
* Allocation after hold expiry.
* Existing order receives ETA extension.
* No compensation for the specified extension.
* Weather hold issued while a batch is being picked.
* Hold expiry calculation.

**Status:** NOT IMPLEMENTED

## BR-12 — Cancellation

**Requirement:** Cancellation before picking starts is free. Cancellation after picking starts but before dispatch costs ₹20. After dispatch, no refund is provided except for lines substituted without `ALLOW`.

**Implementation module:** `cancellation_service.py`

**Required tests:**

* Cancellation before picking.
* Cancellation after picking but before dispatch.
* Cancellation after dispatch.
* Refund eligibility for substituted lines without `ALLOW`.
* Cancellation fee.
* Invalid lifecycle transition.

**Status:** NOT IMPLEMENTED

## Implementation Policy

A rule is not considered complete merely because its code exists.

For each rule:

1. Implement the behavior.
2. Add tests for the normal case.
3. Add tests for the specified boundaries.
4. Run the tests.
5. Review the implementation against the specification.
6. Update the status only when the required evidence exists.
