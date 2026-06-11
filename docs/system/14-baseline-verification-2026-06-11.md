# Baseline verification: 2026-06-11

Preferred model: `qwen3.5:27b`.

Verification completed:

- focused benchmark: 33/33 semantic passes across three seeds;
- clinic E2E: 5/5 scenarios passed;
- carsharing initial E2E: 4/5 scenarios passed;
- failed carsharing dependency exposed generic deterministic defects;
- after fixing dependency classification and resource-aware ID variables,
  scenario 1 and dependent scenario 2 both passed;
- full pytest with carsharing mock: 107 passed, 1 skipped;
- additional resource-aware ID regression test: passed;
- Python compile check: passed;
- PowerShell parser check for both batch scripts: passed.

Generated evidence is stored locally under `evals/results/` and intentionally
ignored by Git.

Baseline tag: `agent-system-baseline-2026-06-11`.

Next development branch: `codex/agent-system-v2`.
