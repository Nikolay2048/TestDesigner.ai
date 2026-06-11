# Current limitations

- Correctness still depends on the quality and completeness of scenario text
  and OpenAPI schemas.
- Endpoint retrieval does not yet use RAG for very large specifications.
- Semantic validators cover known high-risk contracts but are not exhaustive.
- The Fixer has a constrained patch vocabulary and cannot repair every valid
  flow defect.
- Business test diversity depends on explicit rules and available mutation
  primitives.
- Assertion synthesis is still shallower than the request-generation pipeline.
- Stable package compatibility uses file hashes rather than semantic versioned
  contracts.
- Parallel scenario execution is unsafe when tests share mutable server state.
- The current orchestrator is procedural; durable pause/resume and human
  interrupts are future LangGraph work.
- Local E2E runs are slow because many fields are handled as separate LLM
  calls.

The system is suitable for controlled demonstrations and continued
development. It is not yet an unattended production test-design service.
