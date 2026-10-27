# PS-031 Business Rule and Test Matrix

**Project:** Dark-Store Order Allocation with 10-Minute SLA, Rider Batching and Substitutions\
**Document:** Rule-to-Test Traceability Matrix\
**Status:** DRAFT — Requirements and test scenarios documented; all rules and edge cases are pending automated test verification (`NOT TESTED`).

---

## 1. Business-Rule Matrix (BR-01 through BR-12)

The following matrix documents all 12 core business rules from the PS-031 assignment brief, establishing test scenarios, boundary conditions, expected results, responsible system components, and verification status.

| Rule ID | Requirement | Normal Scenario | Boundary Scenario | Expected Result | Related Component | Test Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **BR-01** | **Serviceability:** A store can serve an order only if straight-line distance &le; 3.0 km and the order time is inside store hours; distance uses the Haversine formula rounded to 2 decimals. | Order placed at 14:00 (store hours 08:00–22:00) with coordinates yielding Haversine distance 2.15 km from store. | Order placed with Haversine distance exactly 3.00 km (or 3.004 km rounding to 3.00 km) vs 3.006 km (rounding to 3.01 km); order placed at exactly opening time (08:00), closing time (22:00), or 1 second after closing. | Store is serviceable if rounded distance &le; 3.00 km and order timestamp is within store hours. Order is rejected / store unserviceable if rounded distance > 3.00 km or order timestamp is outside store hours. | `allocation_service.py`, `distance.py` | NOT TESTED |
| **BR-02** | **Store Choice:** Among serviceable stores pick the one with 100% stock first; tie-break by lowest distance, then by lowest load in the current 10-min picking slot; if none has 100% stock pick highest line-fill percent. | Two serviceable stores: Store A has 100% stock at 2.50 km; Store B has 80% stock at 1.10 km. Store A is selected. | Two stores tie on 100% stock: Store A is at 1.50 km (load 5), Store B is at 1.50 km (load 2) &rarr; Store B selected. If stock, distance, and load all tie, deterministic tie-break applies. If neither has 100% stock, store with higher line-fill % is selected. | 100% stock store prioritized over partial stock regardless of distance. Ties resolved sequentially by lowest distance, then lowest slot load, or highest line-fill % if all partial. Deterministic resolution on complete tie. | `allocation_service.py`, `inventory_service.py` | NOT TESTED |
| **BR-03** | **Picking Capacity:** Each store accepts at most its capacity (default 12 orders) per 10-min slot starting from the order time; when full the order rolls into the next slot and the ETA shifts +10 min. | Store with default capacity 12 receives order #5 in current 10-minute slot [10:00, 10:10). Slot load becomes 6; order scheduled in current slot with +0 min shift. | Current slot has exactly 12 orders booked. Order #13 arrives at 10:05. Alternatively, order placed at 21:59 near 22:00 closing when final slot [21:50, 22:00) is full (12/12). | Orders &le; 12 accepted in current slot. Order #13 overflows into next slot [10:10, 10:20) and promised ETA increases by +10 min. If rolled slot falls outside operating hours, order is rejected per store hours rule. | `picking_service.py` | NOT TESTED |
| **BR-04** | **ETA & Delayed Promise:** Promised ETA = 2 min pick + 1.5 min per km (rounded up to the minute) + 2 min per earlier drop in the same batch; if ETA > 10 min and customer did not opt into slow delivery, order is tagged `DELAYED_PROMISE` and SLA compensation is pre-armed. | Order at 2.0 km, standalone (0 earlier drops), standard delivery. Travel time = ceil(2.0 &times; 1.5) = 3 min. Total ETA = 2 + 3 + 0 = 5 min (&le; 10 min). | Order at 3.0 km with rolled slot (+10 min): total ETA = 17 min (> 10 min). Customer without slow delivery vs customer who opted into slow delivery. Fractional distance: 1.1 km &rarr; ceil(1.65) = 2 min travel. ETA exactly 10 min vs 11 min. | ETA calculated as `2 + ceil(1.5 * km) + 2 * earlier_drops + slot_delays`. If ETA > 10 min and `slow_delivery=False`, tagged `DELAYED_PROMISE` and SLA compensation pre-armed; if `slow_delivery=True`, tag is omitted. | `picking_service.py`, `allocation_service.py`, `batching_service.py` | NOT TESTED |
| **BR-05** | **Substitution:** When a line is short, `ALLOW` permits any SKU in same category with price within +15% (customer pays original price); `SIMILAR_ONLY` permits only same brand; `NONE` converts shortfall into a partial line; a substituted line never costs more than original. | Requested SKU out of stock (price ₹100, category "Dairy", brand "BrandA"). Preference `ALLOW`. Candidate SKU is "Dairy", "BrandB", price ₹110 (+10%). Candidate substituted; customer charged ₹100. | Candidate priced at exactly +15.00% (₹115.00 for ₹100 original) vs +15.01% (₹115.01, rejected). Candidate with different brand under `SIMILAR_ONLY`. Candidate in different category under `ALLOW`. Preference `NONE`. | Eligible substitute selected under `ALLOW` (&le; +15% price, same category) or `SIMILAR_ONLY` (&le; +15% price, same brand). Ineligible candidate rejected. Substituted line charged at min(original_price, substitute_price). `NONE` yields partial fulfillment. | `substitution_service.py`, `inventory_service.py` | NOT TESTED |
| **BR-06** | **Partial Fulfilment:** If the fulfilled value drops below 60% of the original order value, the order is cancelled before dispatch with a full refund and no delivery fee. | Original order value ₹500. Out-of-stock items reduce fulfilled value to ₹250 (50%, which is < 60%). Order cancelled before dispatch, ₹500 refunded, delivery fee ₹0. | Fulfilled value is exactly 60.0% of original value (e.g., ₹300.00 of ₹500.00) vs 59.9% (₹299.50 of ₹500.00). | At exactly 60.0% fulfilled value, order proceeds (not cancelled). Below 60.0% (< 60%), order is automatically cancelled before dispatch, 100% payment is refunded, and delivery fee is ₹0. | `cancellation_service.py`, `substitution_service.py` | NOT TESTED |
| **BR-07** | **Delivery Fee & Surge:** ₹0 for order value &ge; ₹199 after substitutions, else ₹25; surge +₹15 when a store's active riders < 3 at allocation time; fee is frozen at allocation and never recomputed. | Order value after substitutions is ₹250, store has 4 active riders at allocation. Delivery fee = ₹0. Active riders drop to 1 after allocation; fee remains ₹0. | Post-substitution order value is exactly ₹199.00 (fee ₹0) vs ₹198.99 (fee ₹25). Active riders at allocation: exactly 2 (< 3, surge +₹15 applies) vs exactly 3 (&ge; 3, no surge). | Delivery fee = (₹0 if post-sub value &ge; ₹199 else ₹25) + (₹15 if active riders < 3 else ₹0). Fee is frozen at allocation timestamp and never recalculated. | Order pricing / `allocation_service.py` | NOT TESTED |
| **BR-08** | **Rider Batching:** A rider may carry up to 3 orders whose drop points are within 1.0 km of each other and whose promised ETAs differ by at most 4 min; a batch dispatches when full or 3 min after its first order was picked, whichever is earlier. | 2 orders with drop points 0.5 km apart and promised ETAs differing by 2 min are assigned to a batch. Both picked; 3 minutes elapse after first order picking; batch dispatches with 2 orders. | Batch already has 3 orders (4th order cannot join). Drop points exactly 1.00 km apart vs 1.01 km apart. Promised ETAs differ by exactly 4 min 00 s vs 4 min 01 s. Dispatch triggered when 3rd order picked vs 3 min after 1st picked. | Orders batched only if capacity &le; 3, pairwise drop distances &le; 1.00 km, and promised ETA delta &le; 4 min. Dispatches immediately when 3rd order is picked, or 3 min after 1st order finishes picking. | `batching_service.py` | NOT TESTED |
| **BR-09** | **SLA Compensation:** Actual delivery later than promised ETA by 5–10 min credits ₹25 to the wallet; by more than 10 min credits ₹50 plus a free delivery coupon; no compensation when the order carried `DELAYED_PROMISE` and the delay is within 5 min of the longer promise. | Promised ETA 10:15 (standard promise). Delivered at 10:22 (7 min late). Customer receives ₹25 wallet credit. | Actual lateness relative to promise: exactly 4 min 59 s (₹0), exactly 5 min 00 s (₹25), exactly 10 min 00 s (₹25), and 10 min 01 s (₹50 + coupon). For `DELAYED_PROMISE`: delay exactly 5 min 00 s (₹0) vs 5 min 01 s late. | Lateness in [5m00s, 10m00s]: ₹25 wallet credit. Lateness > 10m00s: ₹50 wallet credit + free delivery coupon. Orders with `DELAYED_PROMISE` receive ₹0 compensation if lateness &le; 5 min beyond the extended promise. | `compensation_service.py` | NOT TESTED |
| **BR-10** | **Rider Penalty:** A rider who is late on 2 or more orders in a batch loses the ₹20 batch bonus; a rider late on an order by more than 15 min gets a ₹30 deduction, capped at ₹90 per day. | Rider delivers a 3-order batch: 1 order is 6 min late, 2 orders are on time. Rider retains ₹20 batch bonus and receives ₹0 penalty deduction. | Rider late on exactly 1 order in batch (bonus kept) vs exactly 2 orders (bonus lost). Lateness on single order is exactly 15 min 00 s (no deduction) vs 15 min 01 s (₹30 deduction). Cumulative deductions reach 3 &times; ₹30 = ₹90; 4th late order capped at ₹90 max. | Retain ₹20 batch bonus if < 2 orders late in batch; forfeit ₹20 bonus if &ge; 2 orders late. Deduct ₹30 for each order delivered > 15 min late, with cumulative deductions capped at ₹90/day per rider. | `compensation_service.py`, `rider.py` | NOT TESTED |
| **BR-11** | **Weather Hold:** A `WEATHER_HOLD` command for a store stops new allocations for 20 min; orders already allocated there get their ETA extended by 10 min without compensation. | `WEATHER_HOLD` issued at 14:00 for Store 1. Order arriving at 14:10 is not allocated to Store 1. Order already allocated at 13:58 with ETA 14:15 gets ETA extended to 14:25 without compensation. | Allocation attempted at 14:19:59 (rejected) vs 14:20:00 (hold expired, allocation permitted). Weather hold issued while orders in a batch are actively being picked in the store. | Store blocked from receiving new allocations during [T, T + 20 min). Pre-allocated orders receive +10 min promised ETA extension; SLA compensation evaluation excludes this 10-minute weather extension. | `allocation_service.py`, `picking_service.py` | NOT TESTED |
| **BR-12** | **Cancellation:** Customer cancellation before picking starts is free; after picking starts and before dispatch costs ₹20; after dispatch no refund except for lines that were substituted without `ALLOW`. | Customer cancels order while in `ALLOCATED` state (before picking starts). Cancellation processed with ₹0 fee and full refund. Customer cancels while in `PICKING` state: ₹20 fee deducted, balance refunded. | Cancellation at exact moment picking starts (fee ₹20 applies); cancellation at exact moment dispatch occurs (dispatched &rarr; non-refundable except unauthorized substitution lines). Cancellation after dispatch with substituted lines where preference was `SIMILAR_ONLY` or `NONE`. | Lifecycle cancellation fees enforced: Before picking = free (100% refund). After picking starts but before dispatch = ₹20 cancellation fee. After dispatch = ₹0 refund, except refund granted for lines substituted without customer `ALLOW` consent. | `cancellation_service.py` | NOT TESTED |

---

## 2. Explicitly Required Edge Cases

The PS-031 specification mandates automated testing for eight explicit edge cases. Each case is documented below with its identifier, starting conditions, expected behavior, governing business rule, and test verification status.

### EC-01: Order at exactly 3.00 km from the only store

* **Case ID:** EC-01
* **Input / Starting Conditions:** A single dark store is registered. An incoming customer order is placed inside store operating hours. The Haversine distance from the dark store to the order delivery coordinates evaluates to exactly 3.00 km after rounding to two decimal places.
* **Expected Behavior:** Because the serviceability threshold is straight-line distance &le; 3.0 km, the store is evaluated as serviceable. The order is accepted and allocated to this store.
* **Relevant Business Rule:** BR-01 (Serviceability)
* **Automated Test Exists Yet:** No (`NOT TESTED`)

---

### EC-02: Two stores tie on stock, distance, and load

* **Case ID:** EC-02
* **Input / Starting Conditions:** An order arrives. Two serviceable dark stores (Store A and Store B) both have 100% stock for all requested items, identical straight-line distance to the customer (e.g., exactly 2.00 km), and identical picking load in the current 10-minute picking slot (e.g., both have 4 orders allocated).
* **Expected Behavior:** The engine must apply a deterministic tie-breaking policy (e.g., lower store ID alphanumerically or earliest store registration) so that identical inputs consistently produce the same store allocation without random selection or runtime failure.
* **Relevant Business Rule:** BR-02 (Store Choice)
* **Automated Test Exists Yet:** No (`NOT TESTED`)

---

### EC-03: Order placed in the last minute before store closing with picking slot overflow

* **Case ID:** EC-03
* **Input / Starting Conditions:** Store operating hours are 08:00–22:00. An order is submitted at 21:59:00 (inside operating hours). The store's final picking slot [21:50, 22:00) is already at maximum capacity (12 orders booked).
* **Expected Behavior:** Under BR-03, slot capacity overflow causes the order to roll into the next slot [22:00, 22:10) with an ETA shift of +10 min. However, the rolled slot falls entirely outside store operating hours. Under BR-01, a store can serve an order only inside store hours. The system must deterministically handle this boundary: either reject the order as unserviceable due to picking outside store hours or reject slot allocation, preventing an unfulfillable commitment.
* **Relevant Business Rule:** BR-01 (Serviceability), BR-03 (Picking Capacity)
* **Automated Test Exists Yet:** No (`NOT TESTED`)

---

### EC-04: Substitute candidate priced exactly 15% above original

* **Case ID:** EC-04
* **Input / Starting Conditions:** An allocated order requested a product (original price ₹100.00) that is out of stock. The customer's substitution preference is `ALLOW`. An available candidate SKU in the same category is priced at exactly ₹115.00 (+15.00% of ₹100.00).
* **Expected Behavior:** Because the price difference is &le; +15%, the candidate SKU is deemed eligible. The substitution is applied, and the line item is charged to the customer at the original price of ₹100.00 (the substitute never costs more than the original).
* **Relevant Business Rule:** BR-05 (Substitution)
* **Automated Test Exists Yet:** No (`NOT TESTED`)

---

### EC-05: Fulfilled value exactly 60% of original

* **Case ID:** EC-05
* **Input / Starting Conditions:** A customer places an order with an original total item value of ₹500.00. One or more items are out of stock and cannot be substituted. After all substitution attempts, the total fulfilled item value is exactly ₹300.00 (which is exactly 60.0% of ₹500.00).
* **Expected Behavior:** BR-06 specifies cancellation only if the fulfilled value "drops below 60%". Since the fulfilled value is exactly 60.0% (not strictly less than 60%), the order is NOT cancelled. The order proceeds to picking and dispatch with the fulfilled items.
* **Relevant Business Rule:** BR-06 (Partial Fulfilment)
* **Automated Test Exists Yet:** No (`NOT TESTED`)

---

### EC-06: Batch where the third order arrives 4 min 01 s after the first

* **Case ID:** EC-06
* **Input / Starting Conditions:** A batch has 2 orders already assigned. A 3rd compatible order (drop point within 1.0 km) arrives or has a promised ETA that differs by 4 minutes and 1 second (241 seconds) from the first order.
* **Expected Behavior:** BR-08 requires promised ETAs in a batch to differ by "at most 4 min" (&le; 240 seconds). Because the time difference exceeds 4 minutes, the 3rd order cannot join this batch. If 3 minutes have elapsed since the first order was picked, the 2-order batch dispatches immediately, while the 3rd order is held for a subsequent batch.
* **Relevant Business Rule:** BR-08 (Rider Batching)
* **Automated Test Exists Yet:** No (`NOT TESTED`)

---

### EC-07: Delivery exactly 10 min 00 s late

* **Case ID:** EC-07
* **Input / Starting Conditions:** An order without `DELAYED_PROMISE` has a promised ETA of 11:00:00. The rider completes delivery at exactly 11:10:00, making the actual delivery time exactly 10 minutes and 00 seconds later than the promised ETA.
* **Expected Behavior:** BR-09 defines two brackets: "5–10 min" late receives ₹25 wallet credit; "more than 10 min" late receives ₹50 wallet credit plus a free delivery coupon. At exactly 10 min 00 s (the inclusive upper bound of the 5–10 min bracket), the customer receives ₹25 wallet credit and does not receive the > 10 min coupon.
* **Relevant Business Rule:** BR-09 (SLA Compensation)
* **Automated Test Exists Yet:** No (`NOT TESTED`)

---

### EC-08: Weather hold issued while a batch is being picked

* **Case ID:** EC-08
* **Input / Starting Conditions:** A dark store is actively picking a batch of orders. At timestamp $T$, an administrative `WEATHER_HOLD` command is issued for that store.
* **Expected Behavior:** The store immediately halts accepting new order allocations for 20 minutes (until $T + 20\text{ min}$). All orders already allocated to the store—including those currently in the picking batch—have their promised ETA extended by 10 minutes without customer SLA compensation for that extension. The system must maintain consistent state regarding whether picking finishes and whether dispatch is paused during the hold.
* **Relevant Business Rule:** BR-08 (Rider Batching), BR-11 (Weather Hold)
* **Automated Test Exists Yet:** No (`NOT TESTED`)

---

## 3. Open Questions and Ambiguities

This section records conflicting, underspecified, or ambiguous requirements identified across BR-01 through BR-12 and the edge cases. Per project governance, these are recorded without silently assuming an unverified interpretation.

### OQ-01: Slot overflow past store closing hours (BR-01 vs BR-03, EC-03)
* **Conflict / Ambiguity:** BR-01 dictates that a store can serve an order only if the order time is inside store hours. BR-03 states that when a 10-minute picking slot is full, the order rolls into the next slot and the ETA shifts by +10 minutes. In EC-03, an order is placed at 21:59 (store closes at 22:00) and the 21:50–22:00 slot is full.
* **Question:** Does the order get rejected because its assigned picking slot [22:00, 22:10) falls outside operating hours, or does the store permit picking after closing for orders received before closing?

### OQ-02: Deterministic tie-breaking mechanism for store selection (BR-02, EC-02)
* **Conflict / Ambiguity:** BR-02 defines tie-breaking by lowest distance, then by lowest load in the current picking slot. When two or more stores tie across stock (both 100%), distance, and slot load, BR-02 does not specify the final criterion.
* **Question:** What is the authoritative final tie-breaker (e.g., alphanumeric store ID, registration sequence, or earliest timestamp)?

### OQ-03: Definition and formula for "line-fill percent" (BR-02)
* **Conflict / Ambiguity:** BR-02 states: "if none has 100% stock pick highest line-fill percent".
* **Question:** Is line-fill percentage calculated as:
  1. Percentage of distinct requested SKU line items completely fulfilled (e.g., 3 out of 4 distinct lines = 75.0%),
  2. Percentage of total individual item units fulfilled across all lines (e.g., 8 out of 10 units = 80.0%), or
  3. Percentage of requested monetary order value fulfilled prior to substitutions?

### OQ-04: Candidate selection priority among multiple eligible substitutes (BR-05)
* **Conflict / Ambiguity:** Under `ALLOW` and `SIMILAR_ONLY`, any SKU within +15% price in the category/brand is eligible. If multiple candidates qualify (e.g., Candidate A at +5% price, Candidate B at +10% price, Candidate C at -5% price), the rule does not specify selection priority.
* **Question:** What determines the selected substitute when multiple candidates qualify (e.g., lowest price, price closest to original, highest inventory, or alphanumeric SKU ID)?

### OQ-05: Pairwise vs anchor drop-point distance in batching (BR-08)
* **Conflict / Ambiguity:** BR-08 specifies that orders in a batch must have drop points "within 1.0 km of each other".
* **Question:** Does this require all pairwise distances between all orders in the batch to be &le; 1.0 km (clique / maximum diameter &le; 1.0 km), or must each subsequent order be &le; 1.0 km from the first order's drop point, or must consecutive stops in the delivery route be &le; 1.0 km apart?

### OQ-06: Promised ETA calculation timing vs batch assignment (BR-04 vs BR-08)
* **Conflict / Ambiguity:** BR-04 defines promised ETA as `2 min pick + 1.5 min per km + 2 min per earlier drop in the same batch` and tags `DELAYED_PROMISE` if ETA > 10 min. However, at order allocation time, the batch composition and drop sequence are not yet established.
* **Question:** Is the initial promised ETA calculated assuming 0 earlier drops and frozen at allocation, or is the promised ETA recalculated when the batch is formed? If recalculated, does adding an earlier drop retroactively arm SLA compensation?

### OQ-07: SLA compensation calculation for DELAYED_PROMISE orders (BR-09)
* **Conflict / Ambiguity:** BR-09 states: "no compensation when the order carried DELAYED_PROMISE and the delay is within 5 min of the longer promise".
* **Question:** If a `DELAYED_PROMISE` order exceeds its extended promise by more than 5 minutes (e.g., 7 minutes late past the longer promise), is compensation evaluated using the standard brackets against the longer promise (i.e., ₹25 for 5–10 min past the longer promise), or does lateness revert to the original 10-minute SLA commitment?

### OQ-08: Scope of post-dispatch cancellation refund exception (BR-12)
* **Conflict / Ambiguity:** BR-12 states: "after dispatch no refund except for lines that were substituted without ALLOW".
* **Question:** Does "substituted without ALLOW" refer strictly to lines substituted when the customer selected `SIMILAR_ONLY` or `NONE`, or invalid/unauthorized substitutions? Furthermore, when such a line is refunded, is the delivery fee also refunded or retained?

### OQ-09: Batch dispatch behavior during a weather hold (BR-08 vs BR-11, EC-08)
* **Conflict / Ambiguity:** BR-11 stops new allocations and extends ETAs by 10 minutes. BR-08 states that a batch dispatches when full or 3 minutes after its first order was picked.
* **Question:** If a weather hold is active while a batch finishes picking, does the 3-minute dispatch timer continue to dispatch the rider into hazardous weather, or is rider dispatch paused for the 20-minute duration of the weather hold?
