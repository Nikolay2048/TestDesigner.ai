# Learning roadmap

The next branch is a guided rebuild of agent behavior on top of stable
deterministic infrastructure.

Student-owned work:

1. Read and explain each current agent contract.
2. Reimplement Documentation Analyst with focused eval cases.
3. Reimplement Endpoint Mapper and unmapped-step semantics.
4. Reimplement Dependency Resolver and Generation Binding.
5. Rework Diagnostician and Fixer using evidence and patch history.
6. Rework business Test Designer and assertion selection.
7. Migrate the proven nodes to LangGraph with human-review interrupts.

Mentor-owned infrastructure:

- OpenAPI parsing and compact operation views;
- JSON/Pydantic/semantic validation;
- deterministic dependency graph and plan assembly;
- HTTP executor and trace persistence;
- generator registry;
- patch application and safety checks;
- stable package storage;
- test execution and Postman export;
- benchmark harness and regression tests.

For every agent change:

1. State the input and output contract.
2. Add or update an eval case before prompt changes.
3. Test at least three seeds.
4. Inspect semantic failures, not only JSON validity.
5. Run the affected E2E scenario.
6. Record the decision and remaining limitation.
