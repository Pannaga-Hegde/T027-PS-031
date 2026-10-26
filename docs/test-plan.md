# PS-031 Test Plan

## 1. Objective

Verify that the order-allocation engine implements all twelve business rules and handles the explicitly specified edge cases.

## 2. Testing Levels

### 2.1 Unit Testing

Unit tests validate individual functions and business rules independently.

Examples include geographic distance calculations, ETA calculations, fulfilment thresholds, and compensation amounts.

### 2.2 Service Testing

Service tests verify interactions between domain models and business services.

Examples include store allocation, inventory resolution, substitution, batching, and cancellation.

### 2.3 Integration Testing

Integration tests execute complete order-processing flows through the command interface.

### 2.4 Regression Testing

Previously passing tests are rerun after code changes to detect unintended behavior changes.

## 3. Business-Rule Coverage

| Business Rule | Test Area                       | Test File                                |
| ------------- | ------------------------------- | ---------------------------------------- |
| BR-01         | Serviceability and distance     | `test_distance.py`, `test_allocation.py` |
| BR-02         | Store selection                 | `test_allocation.py`                     |
| BR-03         | Picking capacity                | `test_picking.py`                        |
| BR-04         | ETA and delayed promise         | `test_picking.py`, `test_allocation.py`  |
| BR-05         | Substitution eligibility        | `test_substitution.py`                   |
| BR-06         | Partial-fulfilment cancellation | `test_cancellation.py`                   |
| BR-07         | Delivery fee and surge          | `test_pricing.py`                        |
| BR-08         | Rider batching                  | `test_batching.py`                       |
| BR-09         | SLA compensation                | `test_compensation.py`                   |
| BR-10         | Rider penalties                 | `test_compensation.py`                   |
| BR-11         | Weather holds                   | `test_weather_hold.py`                   |
| BR-12         | Customer cancellation           | `test_cancellation.py`                   |

The listed test files are planned artifacts. Create them when their corresponding modules are implemented.

## 4. Required Edge Cases

The following cases must have explicit tests:

1. Order exactly 3.00 km from the only store.
2. Two stores tied on stock, distance, and load.
3. Order placed in the final minute before store closing with picking-slot overflow.
4. Substitute candidate priced exactly 15% above the original.
5. Fulfilled value exactly 60% of the original order value.
6. Third order arrives 4 minutes and 1 second after the first.
7. Delivery is exactly 10 minutes late.
8. Weather hold issued while a batch is being picked.

## 5. Test Execution

Run tests from the project root with:

```powershell
python -m pytest -v
```

Use the project's virtual environment so the installed test dependencies are available.

## 6. Completion Criteria

Testing is complete when:

- Every business rule has associated tests.
- All eight specified edge cases are covered.
- Tests pass in the documented environment.
- Integration tests cover the principal order lifecycle.
- Known limitations and unresolved ambiguities are documented.
