# Model benchmark: 2026-06-11

Hardware:

- RTX 3090 24 GB;
- RTX 4070 Ti Super 16 GB;
- Ollama on Windows;
- context 32768, temperature 0.1, thinking disabled.

Focused agent benchmark, 11 cases and three seeds:

| Model | Passed | JSON | Schema | Semantic | Time | Tokens/s |
|---|---:|---:|---:|---:|---:|---:|
| qwen3:14b | 33/33 | 33/33 | 33/33 | 33/33 | 196.6 s | 74.9 |
| qwen3.5:27b | 33/33 | 33/33 | 33/33 | 33/33 | 347.9 s | 40.5 |
| qwen3.5:35b | 29/33 | 32/33 | 32/33 | 29/33 | 148.0 s | 131.7 |

`qwen3:30b-a3b` was rejected in the initial pass because current structured
prompts frequently produced reasoning fragments instead of the requested
object.

Findings:

- `qwen3.5:27b` is the preferred development model: most reliable new model,
  fully resident on the RTX 3090 at this context size.
- `qwen3:14b` remains an effective fast baseline for narrow tasks.
- `qwen3.5:35b` is fast but split across both GPUs and systematically mapped a
  non-REST outcome to a duplicate endpoint; its Fixer also produced malformed
  JSON in one seed.
- Model size did not compensate for weak contracts. Semantic validation and
  bounded tasks remain mandatory.

E2E evidence for `qwen3.5:27b`:

- clinic: 5/5 scenarios passed, all on first attempt;
- carsharing: 4/5 passed in the first full run;
- the failed scenario exposed two deterministic defects: dependency
  classification and cross-resource `$.id` collisions;
- after generic fixes, the scenario 1 -> scenario 2 dependency chain passed.

Recommended defaults:

- development and difficult agent work: `qwen3.5:27b`;
- quick regression and prompt iteration: `qwen3:14b`;
- keep `qwen3.5:35b` experimental until its semantic failure cases pass.

Reproduce focused evaluation:

```powershell
python .\evals\agent_benchmark.py --repetitions 3 `
  --models qwen3:14b qwen3.5:27b qwen3.5:35b `
  --out .\evals\results\agent_benchmark
```
