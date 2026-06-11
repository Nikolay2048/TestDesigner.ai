# Test design

Test design starts only from a stabilized happy path.

Deterministic cases cover OpenAPI facts such as required fields, types,
formats, enum values, numeric boundaries, and path/query requirements.

Business cases are proposed from scenario rules and context. The LLM chooses
from executable mutation primitives and explains the relation between rule,
mutation, and expected behavior. This allows domain-independent reasoning
without allowing arbitrary, non-executable prose.

Each case should contain:

- source rule or schema constraint;
- stable setup and target step;
- one primary mutation;
- expected status and response assertions;
- cleanup or isolation requirements;
- provenance and human-review state.

Assertions should combine:

- expected HTTP status;
- explicit response schema fields;
- values observed during the reference execution;
- business expectations stated in the scenario;
- identifiers or state transitions required by later steps.

Heuristic assertions based only on field-name patterns are intentionally
excluded until supported by stronger evidence.
