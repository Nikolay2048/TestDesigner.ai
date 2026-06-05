# Debug findings

## Current status

- Clinic: 5/5 scenarios have stable happy paths.
- Carsharing: 5/5 scenarios have stable happy paths.
- Unit tests: 83 passed.

## Problems found while debugging

1. Dependency resolution must stay domain-neutral.
   - Removed keyword-based semantic guesses from code.
   - Stable scenario lookup now relies on explicit `required_data`.
   - Path placeholders from deterministic endpoint mentions can enrich dependency `required_data`.

2. Documentation Analyst output is not stable enough for dependency preflight.
   - It can classify an existing-object prerequisite as `requires_state` instead of `requires_scenario`.
   - Runner now treats any dependency with `required_data` as a setup dependency candidate.
   - Longer-term: preflight and main run should share the same analyst output instead of calling the LLM twice.

3. Endpoint Mapper can reorder steps.
   - Added deterministic ordering by Documentation Analyst business step order.
   - Longer-term: mapper output should include stable step ids.

4. OpenAPI parameter needs were incomplete.
   - Required query/header parameters must be included in the dependency graph, not only request bodies.

5. Generator defaults caused invalid data.
   - Email default domain `example.test` was rejected by realistic validators.
   - RU phone generator produced too few digits for `+7XXXXXXXXXX`.

6. Stabilization needs deterministic server-hint rules before LLM fixer.
   - Numeric hints like `fuelLevelPercent=100` should patch generated params.
   - Literal hints like `newReturnDate=2026-06-08` should patch request binding as reviewable literal.
   - Alternate-value hints should switch binding to a previous lookup response when the server says the value must differ.

7. Test design execution still needs stronger oracle/assertion logic.
   - Many generated cases end as `review_required`, `oracle_incomplete`, `weak_attack`, or `contract_mismatch`.
   - Next step should improve expected-status/oracle selection and separate invalid test generation from product defects.

8. Demo input artifacts matter.
   - Carsharing mock server was missing and had to be restored.
   - Some scenarios lacked explicit dependency data such as `rentalId`.
   - Carsharing scenario 5 originally described incident registration after return, but the server contract requires incident before final return.
