# Architecture

The architecture is hybrid. LLMs make bounded semantic decisions; ordinary
code owns parsing, validation, execution, state, retries, and export.

```text
scenario + OpenAPI + test data
  -> Documentation Analyst
  -> Endpoint Mapper
  -> deterministic dependency graph
  -> Dependency Resolver (small tasks)
  -> Generation Binding (small tasks)
  -> deterministic plan assembler
  -> Executor
  -> Diagnostician -> Fixer -> validated patch -> Executor
  -> stable package
  -> Test Designer
  -> test-case Executor
  -> Postman exporter
```

Key boundaries:

- OpenAPI is authoritative for operations and schemas.
- Scenario text is authoritative for order, intent, rules, and dependencies.
- Server responses are authoritative during stabilization.
- LLM output is never applied before schema and semantic validation.
- Stable packages, not arbitrary previous runs, may prepare dependencies.

LangGraph is a future orchestration option, not a substitute for domain
contracts. Nodes can move to LangGraph after their inputs, outputs, and
failure semantics are stable.
