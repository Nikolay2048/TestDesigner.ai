# Agentic Architecture Roadmap

We build the system step by step.

## Stage 1: Documentation Analyst

Implemented now.

Input:

- one scenario markdown file.

Output:

- business goal;
- actors;
- preconditions;
- ordered business steps;
- business rules;
- success criteria;
- negative conditions;
- endpoint mentions found directly in the document;
- open questions.

## Future Stages

These are stubs for now.

1. Flow Designer
   - Input: business steps and a small API context.
   - Output: ordered REST flow draft.

2. Data Binding
   - Input: one flow step, request schema, previous response schemas, available variables.
   - Output: request fields and variable extraction rules.

3. FlowExecutor
   - Deterministic code, not LLM.
   - Sends REST requests and saves traces.

4. Stabilization Diagnostician
   - Input: failed step, request, response, operation schema, related business rule.
   - Output: diagnosis and proposed fix for human review.

5. Stabilization Fixer
   - Not implemented.
   - Later may propose a candidate fix and rerun executor.

6. Test Designer
   - Not implemented.
   - Runs only after stable happy path and human review.

