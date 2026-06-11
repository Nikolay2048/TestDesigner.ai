# Design decisions

## Hybrid over fully agentic

LLMs are used where semantic interpretation is necessary. Deterministic code
owns mechanics and invariants.

## Bounded tasks over one large Data Binding prompt

One request need per decision is slower but substantially more reliable and
debuggable for local models.

## Typed patches over ReAct with unrestricted tools

The Fixer proposes a typed patch. Code validates and applies it. This preserves
auditability and prevents accidental mutation of unrelated state.

## Stable packages over automatic recursive stabilization

A dependent scenario never silently stabilizes its prerequisite. The
prerequisite must already have a reviewed, successful package.

## Registry functions over generated code

Generators are reviewed Python/JavaScript pairs. Agents select functions and
parameters but do not author executable code during a run.

## Current orchestration before LangGraph migration

The explicit orchestrator remains until node contracts are stable. LangGraph
will later provide routing, persistence, and human interrupts without changing
the domain model.

## Human review as a contract

Review is required for ambiguous mappings, automatic stabilization patches,
unsupported business mutations, and changes to stable scenario contracts.
