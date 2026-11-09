# PS-031 – Dark-Store Order Allocation with 10-Minute SLA, Rider Batching and Substitutions — Sample Data Pack

Prepared by 10 SECONDS for Team T027.

## Files

| File | What it holds | Row count |
|---|---|---|
| `stores.csv` | 4 dark stores with location, hours, picking capacity and starting active-rider count | 4 data rows |
| `skus.csv` | 40-SKU catalogue with brand, category and selling price | 40 data rows |
| `stock.csv` | Opening stock per store per SKU (4 x 40) | 160 data rows |
| `riders.csv` | Rider roster per store | 15 data rows |
| `delivery_events.csv` | Actual dispatch log: rider, dispatch and delivery time per delivered order (25 late) | 55 data rows |
| `commands.txt` | 60 orders over 18:00–21:00, rider-count changes, a weather hold, cancellations and print requests | 130 command lines (excluding comments) |

Row numbers quoted below are 1-based data-row numbers (the header is row 0). Line numbers for `commands.txt` are physical line numbers including comment lines.

## Column reference

Conventions: dates `YYYY-MM-DD`; timestamps `YYYY-MM-DD HH:MM:SS` (24h, IST, no timezone suffix); money in Rs as plain decimals with 2 places (no commas, no symbol); percentages as plain numbers (12.5 means 12.5%); booleans `true`/`false`; IDs uppercase with a fixed prefix. An empty cell means "not applicable" and only appears in columns whose notes say so.

### `stores.csv`

| Column | Type | Units / format | Allowed values | Notes |
|---|---|---|---|---|
| store_id | string | DS-nn | — | primary key |
| store_name | string | — | — | — |
| lat | decimal | degrees, 6 dp | — | WGS84 |
| lon | decimal | degrees, 6 dp | — | WGS84 |
| open_time | time | HH:MM:SS | — | — |
| close_time | time | HH:MM:SS | — | no allocation at or after this time |
| picking_capacity_per_slot | int | orders per 10-min slot | — | default 12; DS-04 is a small store |
| active_riders | int | — | — | at the start of the day; changed by RIDERS commands |

### `skus.csv`

| Column | Type | Units / format | Allowed values | Notes |
|---|---|---|---|---|
| sku_id | string | SKU-nnn | — | primary key |
| sku_name | string | — | — | — |
| brand | string | — | — | SIMILAR_ONLY = same brand |
| category | enum | — | DAIRY, BAKERY, FRUITS_VEG, SNACKS, BEVERAGES, STAPLES, PERSONAL_CARE, HOUSEHOLD | ALLOW = same category |
| price | decimal | Rs | — | same at every store |

### `stock.csv`

| Column | Type | Units / format | Allowed values | Notes |
|---|---|---|---|---|
| store_id | string | — | stores.csv | — |
| sku_id | string | — | skus.csv | — |
| qty_on_hand | int | units | >= 0 | opening stock; decrement as you allocate |

### `riders.csv`

| Column | Type | Units / format | Allowed values | Notes |
|---|---|---|---|---|
| rider_id | string | RDR-nn | — | primary key |
| rider_name | string | — | — | — |
| store_id | string | — | stores.csv | home store |
| mobile | string | 10 digits | — | — |

### `delivery_events.csv`

| Column | Type | Units / format | Allowed values | Notes |
|---|---|---|---|---|
| event_id | string | DEL-nnn | — | file sorted by delivered_ts |
| order_id | string | ORD-nnnn | — | an ORDER in commands.txt |
| rider_id | string | — | riders.csv | rider who carried the order |
| dispatch_ts | timestamp | — | — | actual dispatch; orders with the same rider_id and dispatch_ts form one actual batch |
| delivered_ts | timestamp | — | — | actual delivery; lateness = delivered_ts - promised time |

## Command file format

`commands.txt` holds one command per line in the form `COMMAND_NAME|arg1|arg2|...`. Commands must be processed strictly in file order. Lines starting with `#` are comments and blank lines are ignored. An empty argument (two adjacent `|`) means "not supplied". Every command used in this pack:

| Command | Arguments (in order) | Meaning |
|---|---|---|
| `ORDER` | `order_id|order_ts|customer_id|lat|lon|substitution_pref|slow_delivery_ok|items` | items = `SKU:qty` pairs joined by `,`; substitution_pref ALLOW, SIMILAR_ONLY or NONE; slow_delivery_ok true/false |
| `RIDERS` | `store_id|ts|active_riders` | Active rider count at the store from ts |
| `WEATHER_HOLD` | `store_id|ts` | Stop new allocations at the store for 20 min from ts |
| `CANCEL` | `order_id|ts` | Customer cancellation |
| `LOAD_DELIVERIES` | `file_name` | Load the actual dispatch/delivery log |
| `PRINT_TRACE` | `order_id` | Print the order trace |
| `PRINT_RIDER_PAYOUTS` | `date` | Print per-rider daily penalties and bonuses |

## Planted scenarios

Every edge case in the brief, mapped to the exact record where it occurs, followed by extra traps.

- **EC-01 Order at exactly 3.00 km from the only store**
  - Customer at haversine distance 3.00 km from DS-04 (rounded to 2 dp) and > 3 km from every other store — commands.txt line 9 (`ORDER|ORD-0006|2026-10-16 18:31:07|CUS-4109|12.932211|77.556156|ALLOW|false|SKU-010:2,SKU-011:1`)
- **EC-02 Two stores tie on stock, distance and load**
  - Customer exactly midway between DS-01 and DS-03 (both 1.81 km), both stores hold every line, both slots empty at 18:04 — your tie-break must be deterministic — commands.txt line 3 (`ORDER|ORD-0001|2026-10-16 18:04:30|CUS-3657|12.923400|77.635950|ALLOW|false|SKU-008:1,SKU-009:2`)
- **EC-03 Order placed in the last minute before store closing with picking slot overflow**
  - DS-04 (capacity 4 per 10-min slot, closes 20:30:00): 4 orders fill the 20:20 slot, the order at 20:29:30 is in the last minute before closing and must roll into the 20:30 slot (+10 min ETA) after the store has closed — commands.txt line 44 (`ORDER|ORD-0036|2026-10-16 20:20:10|CUS-7367|12.919926|77.566621|NONE|false|SKU-021:1`); commands.txt line 45 (`ORDER|ORD-0037|2026-10-16 20:21:40|CUS-8864|12.934373|77.575939|ALLOW|false|SKU-037:3`); commands.txt line 46 (`ORDER|ORD-0038|2026-10-16 20:22:55|CUS-1483|12.922096|77.564650|ALLOW|false|SKU-033:3`); commands.txt line 47 (`ORDER|ORD-0039|2026-10-16 20:24:20|CUS-2911|12.930655|77.575312|ALLOW|false|SKU-015:1`); commands.txt line 50 (`ORDER|ORD-0041|2026-10-16 20:29:30|CUS-5065|12.923072|77.575297|ALLOW|false|SKU-029:3`)
- **EC-04 Substitute candidate priced exactly 15% above original**
  - SKU-001 (Rs 200.00) is out of stock at DS-03; the only same-category candidate SKU-002 costs Rs 230.00 = exactly +15% — customer pays the original price — commands.txt line 17 (`ORDER|ORD-0013|2026-10-16 18:47:12|CUS-2741|12.903848|77.657578|ALLOW|false|SKU-001:1,SKU-012:2`)
- **EC-05 Fulfilled value exactly 60% of original**
  - Order value 500.00 = SKU-005 300.00 + SKU-006 200.00; SKU-006 has zero stock at DS-02 (only serviceable store), preference NONE -> fulfilled 300.00 = exactly 60% — commands.txt line 31 (`ORDER|ORD-0024|2026-10-16 19:33:18|CUS-2198|12.985732|77.645270|NONE|false|SKU-005:1,SKU-006:1`)
- **EC-06 Batch where the third order arrives 4 min 01 s after the first**
  - Three DS-01 orders with drops within 1.0 km of each other at 19:40:00, 19:41:00 and 19:44:01 — the third arrives 4 min 01 s after the first — commands.txt line 32 (`ORDER|ORD-0025|2026-10-16 19:40:00|CUS-5939|12.944226|77.614214|ALLOW|false|SKU-018:1`); commands.txt line 33 (`ORDER|ORD-0026|2026-10-16 19:41:00|CUS-2925|12.944226|77.617905|ALLOW|false|SKU-030:2`); commands.txt line 34 (`ORDER|ORD-0027|2026-10-16 19:44:01|CUS-8772|12.939729|77.614214|ALLOW|false|SKU-021:1`)
- **EC-07 Delivery exactly 10 min 00 s late**
  - Isolated DS-02 order 2.20 km away (ETA 6 min, no batch partner): delivered exactly 10 min 00 s after the promised time — commands.txt line 6 (`ORDER|ORD-0003|2026-10-16 18:15:00|CUS-9502|12.991610|77.639430|ALLOW|false|SKU-013:2,SKU-027:1`); delivery_events.csv row 4 (DEL-004)
- **EC-08 Weather hold issued while a batch is being picked**
  - DS-02 orders at 19:10:00 and 19:11:00 are being picked when WEATHER_HOLD|DS-02 arrives at 19:12:00 -> their ETA +10 min with no compensation (they are delivered 7–8 min after the original promise); the 19:20:00 order midway between DS-01 and DS-02 cannot go to DS-02 during the hold — commands.txt line 23 (`ORDER|ORD-0018|2026-10-16 19:10:00|CUS-6876|12.975947|77.648393|ALLOW|false|SKU-017:1`); commands.txt line 24 (`ORDER|ORD-0019|2026-10-16 19:11:00|CUS-8470|12.974693|77.651897|ALLOW|false|SKU-031:1`); commands.txt line 25 (`WEATHER_HOLD|DS-02|2026-10-16 19:12:00`); commands.txt line 27 (`ORDER|ORD-0021|2026-10-16 19:20:00|CUS-6654|12.953550|77.632850|ALLOW|false|SKU-008:1`); delivery_events.csv row 15 (DEL-015); delivery_events.csv row 17 (DEL-017)
- **Extra traps (not in the brief, but realistic):**
  - Order at 20:25:30 also rolls to the 20:30 slot; it is CANCELLED at 20:27:00, before its picking starts -> free cancellation — commands.txt line 48 (`ORDER|ORD-0040|2026-10-16 20:25:30|CUS-2042|12.940007|77.573308|ALLOW|false|SKU-036:2`); commands.txt line 49 (`CANCEL|ORD-0040|2026-10-16 20:27:00`)
  - Same shortage of SKU-001 under SIMILAR_ONLY: the only candidate is a different brand -> no substitution, line becomes partial — commands.txt line 36 (`ORDER|ORD-0029|2026-10-16 19:52:40|CUS-8939|12.908216|77.656937|SIMILAR_ONLY|false|SKU-001:1,SKU-013:1`)
  - Order value exactly Rs 199.00 (SKU-007) -> delivery fee Rs 0; it is placed at 20:05:10 after RIDERS drops DS-01 to 2 active riders, so the +Rs 15 surge still applies — commands.txt line 40 (`ORDER|ORD-0032|2026-10-16 20:05:10|CUS-2131|12.944904|77.614885|ALLOW|false|SKU-007:1`)
  - SIMILAR_ONLY order: SKU-003 short, same-brand SKU-004 substituted (+12.5%); order is cancelled at 18:35:00 after dispatch -> only the substituted line is refunded (it was substituted without ALLOW) — commands.txt line 7 (`ORDER|ORD-0004|2026-10-16 18:22:00|CUS-2439|12.904839|77.649924|SIMILAR_ONLY|false|SKU-003:2,SKU-034:1`); commands.txt line 10 (`CANCEL|ORD-0004|2026-10-16 18:35:00`)
  - Order cancelled at 19:06:30, 90 s after allocation — picking has started, not yet dispatched -> Rs 20 cancellation charge — commands.txt line 21 (`ORDER|ORD-0017|2026-10-16 19:05:00|CUS-6112|12.943651|77.627656|ALLOW|false|SKU-035:1,SKU-025:1`); commands.txt line 22 (`CANCEL|ORD-0017|2026-10-16 19:06:30`)
  - Customer > 3 km from every store -> REJECTED (no serviceable store) — commands.txt line 19 (`ORDER|ORD-0015|2026-10-16 18:55:45|CUS-4703|12.995100|77.540300|ALLOW|false|SKU-018:1`)
  - Order line for SKU-099 which is not in the catalogue -> reject the order with a reason — commands.txt line 28 (`ORDER|ORD-0022|2026-10-16 19:25:10|CUS-6324|12.916096|77.639409|ALLOW|false|SKU-099:1,SKU-015:1`)
  - Active riders: DS-03 starts with 2 (< 3, surge +Rs 15) and goes to 3 at 19:30:00; DS-01 drops to 2 at 20:00:00 — fees already frozen at allocation must not change — commands.txt line 30 (`RIDERS|DS-03|2026-10-16 19:30:00|3`); commands.txt line 38 (`RIDERS|DS-01|2026-10-16 20:00:00|2`)
  - ORD-0001 is re-sent 7 s later by the app (identical ORDER line) — idempotent: ignore the duplicate — commands.txt line 3 (`ORDER|ORD-0001|2026-10-16 18:04:30|CUS-3657|12.923400|77.635950|ALLOW|false|SKU-008:1,SKU-009:2`); commands.txt line 4 (`ORDER|ORD-0001|2026-10-16 18:04:30|CUS-3657|12.923400|77.635950|ALLOW|false|SKU-008:1,SKU-009:2`)
  - RDR-01 carries four DS-01 orders delivered more than 15 min late -> Rs 30 each = Rs 120, capped at Rs 90 for the day — delivery_events.csv row 32 (DEL-032); delivery_events.csv row 53 (DEL-053); delivery_events.csv row 30 (DEL-030); delivery_events.csv row 28 (DEL-028)
  - Actual batch of RDR-04 dispatched 2026-10-16 20:41:03 has two late drops -> the Rs 20 batch bonus is lost — delivery_events.csv row 41 (DEL-041); delivery_events.csv row 45 (DEL-045)

## What is NOT in this pack

Expected outputs are not provided for this set. Validate your engine with your own test suite against the rules in your brief (BR-01 … BR-nn). A separate hidden input set with the same schema will be used at final evaluation; outputs are compared field-for-field, so follow the column formats above exactly in your own output.

## Output format expected at evaluation

Produce these CSV files (header row, same conventions as above, rows sorted as stated). Where the brief is silent on a format detail the choice below is binding for evaluation.

### `order_trace.csv`

One row per PRINT_TRACE, in command order.

Columns, in order: `order_id`, `status`, `store_id`, `distance_km`, `slot_start`, `batch_id`, `drop_seq`, `promised_eta_min`, `promised_ts`, `dispatch_ts`, `delivered_ts`, `delay_min`, `original_value`, `fulfilled_value`, `delivery_fee`, `surge_fee`, `cancellation_charge`, `refund_amount`, `wallet_credit`, `free_delivery_coupon`, `tags`, `rule_ids`

### `order_lines.csv`

One row per order line in each traced order.

Columns, in order: `order_id`, `line_no`, `ordered_sku`, `ordered_qty`, `fulfilled_sku`, `fulfilled_qty`, `line_status`, `unit_price_charged`, `line_amount`

### `rider_payouts.csv`

One row per rider per PRINT_RIDER_PAYOUTS.

Columns, in order: `date`, `rider_id`, `batches`, `late_orders`, `batch_bonus_lost`, `late_deductions`, `late_deduction_capped`

Binding choices: haversine with Earth radius 6371.0 km, rounded to 2 dp; picking slots are clock-aligned 10-minute windows (18:00:00–18:09:59, …); picking of an order starts at the later of its allocation time and its slot start; `status` is DELIVERED, CANCELLED, REJECTED or CANCELLED_PARTIAL (< 60% fulfilled); `line_status` is FULL, SUBSTITUTED, PARTIAL or SHORT; `tags` joins DELAYED_PROMISE, SURGE, WEATHER_EXTENDED with `;`; `delay_min` is (delivered_ts - promised_ts) in minutes with 2 decimals; `batch_id` is your own `B-<store_id>-nnn`; `free_delivery_coupon` is true/false.
