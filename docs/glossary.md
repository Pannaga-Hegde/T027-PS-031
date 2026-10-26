# PS-031 Glossary

| Term                 | Definition                                                                                                                                                     |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Actual Delivery Time | The timestamp at which an order is recorded as delivered.                                                                                                      |
| Allocation           | The process of selecting a store to fulfil a customer order.                                                                                                   |
| Batch                | A group of eligible orders assigned to a rider for delivery.                                                                                                   |
| Batch Bonus          | The ₹20 bonus associated with a batch, subject to the specified rider penalty rule.                                                                            |
| Cancellation Fee     | A fee charged when a cancellation occurs after picking starts but before dispatch, according to BR-12.                                                         |
| Compensation         | A benefit provided to a customer according to the SLA rules, such as wallet credit or a delivery coupon.                                                       |
| Dark Store           | A fulfilment location dedicated to processing online orders.                                                                                                   |
| Delivery Fee         | The order delivery charge calculated according to BR-07 and frozen at allocation.                                                                              |
| Dispatch             | The point at which an order or batch is released for delivery.                                                                                                 |
| Drop Point           | The geographic destination where an order is delivered.                                                                                                        |
| ETA                  | Estimated Time of Arrival; the expected delivery time.                                                                                                         |
| Fulfilled Value      | The value of the items fulfilled for an order after applicable substitutions and partial fulfilment.                                                           |
| Haversine Formula    | A geographic calculation used to estimate straight-line distance between two latitude/longitude coordinates.                                                   |
| Line Fill            | A measure of how much of an order's requested items or quantities can be fulfilled. The exact percentage calculation must be documented in the implementation. |
| Order Line           | A product and quantity requested as part of an order.                                                                                                          |
| Partial Fulfilment   | Fulfilling less than the originally requested order because some items or quantities are unavailable.                                                          |
| Picking              | The process of collecting items from a store to prepare an order.                                                                                              |
| Picking Capacity     | The maximum number of orders a store can accept in a specified 10-minute picking slot.                                                                         |
| Picking Slot         | A 10-minute interval used to enforce a store's picking capacity.                                                                                               |
| Rider Penalty        | A deduction applied to a rider according to the late-delivery and batch rules.                                                                                 |
| Serviceability       | Whether a store is eligible to serve an order based on the specified operating and distance conditions.                                                        |
| SKU                  | Stock Keeping Unit; an identifier used to distinguish a product or inventory item.                                                                             |
| SLA                  | Service Level Agreement; the delivery-time commitment used to determine lateness and compensation.                                                             |
| Slow Delivery        | A customer preference that permits a promise longer than the standard 10-minute target without the same delayed-promise treatment.                             |
| Substitution         | Replacing an unavailable requested product with another eligible product.                                                                                      |
| Surge Charge         | The additional ₹15 delivery charge applied when the store has fewer than three active riders at allocation time.                                               |
| Weather Hold         | A temporary 20-minute restriction on new allocations to a store, with the specified ETA effect on existing allocations.                                        |
| Wallet Credit        | A monetary credit applied to a customer's wallet as SLA compensation.                                                                                          |
| Order Trace          | A record explaining an order's allocation, substitutions, batching, ETA, delivery, compensation, and invoice.                                                  |
| Business Rule ID     | A stable identifier, such as BR-01, used to refer to a specific business requirement.                                                                          |

## Notes

1. The term _line fill_ requires a precise operational definition before implementation.
2. Promised ETA and actual delivery time must use consistent timestamp representations.
3. Wallet credits, coupons, refunds, delivery fees, and rider penalties are distinct financial records and must not be treated as interchangeable values.
