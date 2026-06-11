# Product goal

TestDesignerAI converts a system-analysis scenario and OpenAPI specification
into an executable, reviewed REST API test asset.

The intended pipeline is:

1. Understand the documented business flow.
2. Map business actions to real OpenAPI operations.
3. Build request/response data dependencies.
4. Execute and stabilize a reproducible happy path.
5. Derive deterministic and business test cases.
6. Execute test cases and preserve evidence.
7. Export the stable setup and cases as a Postman collection.

The system must not hide product defects. Every automatic correction is
recorded with evidence and marked for human review where appropriate.

Success means more than producing JSON. A result is accepted only when its
contracts validate and its executable parts pass against the target server.
