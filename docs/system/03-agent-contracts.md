# Agent contracts

## Documentation Analyst

Input: one scenario and deterministically extracted endpoint mentions.

Output: ordered business steps, rules, criteria, dependencies, and questions.
It must not invent endpoints.

## Endpoint Mapper

Input: one business step, explicit mentions, and compact OpenAPI operation
metadata. Output: zero or more existing operations plus confidence and reason.
An outcome or non-REST action may remain unmapped.

## Dependency Resolver

Input: one request need and compatible producers from earlier steps. Output:
one candidate or `missing`. It does not generate values.

## Generation Binding

Input: one unresolved request field, schema, static keys, external-context
keys, and registered generators. Output: static, generated, literal, computed,
external-context, or missing binding.

## Stabilization Diagnostician

Input: failed request/response, current plan, relevant previous trace, and
patch history. Output: evidence-based cause and suspected bindings or missing
operations. It does not mutate the plan.

## Stabilization Fixer

Input: accepted diagnosis, legal patch types, available operations, variables,
generators, and rejected patch history. Output: one minimal patch or
`no_patch`. Deterministic code validates and applies it.

## Test Designer

Input: stable happy path, scenario rules, OpenAPI constraints, and executable
mutation catalog. Output: traceable cases with setup, mutation, expected
checks, and provenance. Unsupported ideas remain review items, not executable
tests.

Agent quality is measured by JSON validity, schema validity, semantic
validity, and executable outcome. Prompt fluency is not a quality metric.
