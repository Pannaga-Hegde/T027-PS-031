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
**Status:** Accepted, pending installation verification.

## DEC-03 — Application Architecture

**Date:** 26 October 2026
**Decision:** Use separate modules for domain models, business services, command parsing, and shared utilities.
**Reason:** Separation of responsibilities makes the implementation easier to test, maintain, and explain during the viva.
**Alternatives:** Implement all functionality in a single file or combine parsing and business logic.
**Status:** Proposed; verify against the implemented architecture.

## DEC-04 — Initial Data Storage

**Date:** 26 October 2026
**Decision:** Use in-memory application state for the initial implementation unless project requirements require persistence.
**Reason:** The project is command-file driven, and the supplied requirements do not explicitly require a production database.
**Alternatives:** SQLite or another database.
**Status:** Proposed; confirm against evaluation requirements.

## DEC-05 — Geographic Distance

**Date:** 26 October 2026
**Decision:** Implement the Haversine formula in a shared utility module and apply the specified two-decimal-place rounding consistently.
**Reason:** The distance calculation is reused by serviceability and batching.
**Alternatives:** Duplicate the formula in each service.
**Status:** Required design approach; implementation pending.

## DEC-06 — Monetary Arithmetic

**Date:** 26 October 2026
**Decision:** Use Python Decimal for monetary calculations.
**Reason:** Decimal arithmetic avoids common binary floating-point representation errors in financial calculations.
**Alternatives:** Use binary floating-point numbers for all amounts.
**Status:** Proposed; implementation pending.

## DEC-07 — Command-File Format

**Date:** 26 October 2026
**Decision:** JSON Lines is the proposed initial command-file format.
**Reason:** Each command can be represented as a separate JSON object, making parsing and input validation straightforward.
**Alternatives:** Custom text commands or a single JSON document.
**Status:** Proposed; confirm that the format satisfies the evaluation requirements.

## Open Decisions

Additional decisions must be recorded when requirements are ambiguous or implementation choices affect business-rule outcomes.

Each entry should identify the problem, alternatives, chosen interpretation, rationale, and any impact on tests or documentation.
