# Failure catalog

Observed failure classes:

- LLM returns prose or malformed JSON.
- JSON validates syntactically but violates the Pydantic schema.
- Output is schema-valid but semantically wrong.
- Endpoint Mapper maps a displayed outcome to a duplicate REST call.
- A small model loses actions when one business step contains several calls.
- Generic `$.id` variables collide between resources.
- Dependency is classified as data instead of required state.
- Stable dependency executes too far and destroys the required state.
- Generated dates satisfy format but violate ordering.
- Generated numeric values satisfy schema but violate server rules.
- Fixer repeats an already rejected patch.
- Fixer invents constants, endpoints, variables, or unsupported mutations.
- Postman references variables that were never initialized or extracted.
- Mock server is unavailable, making every execution result misleading.
- Windows PowerShell treats native stderr as an error or fails to launch a
  process because environment keys differ only by case.

Mitigations:

- small prompts and one decision per task;
- structured output plus semantic validators;
- deterministic candidate lists and patch catalogs;
- resource-aware variable naming;
- stable checkpoints for dependencies;
- full attempt and rejection history;
- executable E2E tests in at least two domains.
