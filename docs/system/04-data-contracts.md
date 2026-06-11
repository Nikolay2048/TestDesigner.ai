# Data contracts

The central contracts live in `src/domain.py`.

- `ScenarioUnderstanding`: semantic interpretation of the source document.
- `EndpointMappingResult`: business-step to OpenAPI mapping.
- `DataDependencyGraph`: ordered request needs and response producers.
- `DependencyResolution`: selected earlier response field for one need.
- `GenerationBindingDecision`: source policy for one unresolved need.
- `DataBindingPlan`: executable requests, bindings, and extractions.
- `ExecutionTrace`: requests, responses, extracted variables, and failure.
- `StabilizationDiagnosis`: evidence-backed failure explanation.
- `StabilizationFix`: one proposed constrained mutation.
- `ScenarioRunOutput`: published stable scenario and provided state.
- `TestCase` and `TestCaseExecutionRecord`: design and execution evidence.

Variable identity must include resource semantics. Generic response fields such
as `$.id` cannot share a global variable named `id`; they become names such as
`vehicle_id`, `reservation_id`, or `appointment_id`.

Each request binding records:

- target and location;
- source policy;
- variable/static key/generator/expression;
- source step and JSONPath where relevant;
- scope, reason, and review flag.

This metadata is required for execution, review, dependency setup, and Postman
scripts. Values alone are insufficient.
