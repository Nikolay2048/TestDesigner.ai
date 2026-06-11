# Stabilization loop

The loop has a maximum attempt count and a separate maximum Fixer tries per
diagnosis.

1. Executor runs the current plan from the beginning.
2. On failure, the complete attempt is persisted.
3. Diagnostician receives the failed exchange and relevant history.
4. Its output is schema-validated and checked against observed evidence.
5. Fixer proposes one minimal legal patch.
6. The patch is rejected if invalid, duplicate, unsupported, or unsafe.
7. An accepted patch is applied to a copied plan and execution restarts.
8. Success publishes a stable package; exhaustion requests human review.

The Fixer may change bindings, generator parameters, extraction paths, and
insert an available OpenAPI operation when the contract permits it. It must
not invent endpoints, identifiers, or arbitrary constants.

Every attempt, diagnosis, proposal, rejection, applied patch, request, and
response belongs in the run artifacts. A retry without history is not an
agentic correction; it is random repetition.
