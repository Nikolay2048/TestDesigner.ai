# Scenario dependencies

A scenario may require an object or system state created by another scenario.
Only a published stable package may be used as setup.

Dependency execution:

1. Documentation Analyst identifies required state and concrete data names.
2. Deterministic code resolves a matching stable package.
3. Package hashes are checked against scenario, OpenAPI, and test-data inputs.
4. The stable plan is executed only to the earliest checkpoint that provides
   the required state.
5. Produced values are exposed through semantic aliases.
6. The current scenario is planned and executed with this external context.

Example: a loyalty scenario needs `reservationId`. The basic rental setup must
stop after reservation creation, not continue through vehicle return.

`requires_data` is ordinary input unless it also names a source scenario or an
existing required state. This distinction prevents every request field from
being treated as a scenario dependency.

Stable packages store the executable plan, dependency setup plan, metadata,
provided state, review notes, and last successful trace.
