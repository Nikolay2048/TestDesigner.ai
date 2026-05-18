# TestDesignerAI

REST-only LLM agent system for generating, executing, repairing, and exporting API test scenarios.

Input:

- system-analysis scenario markdown;
- Swagger/OpenAPI specification;
- constants JSON.

Output:

- normalized scenario card with executable REST steps;
- agent action log;
- Postman collection;
- optional execution report and Postman environment.

## Agents

- `ScenarioParser` extracts raw scenario facts from non-structured system-analysis text.
- `OpenApiReader` resolves local and external `$ref` links and builds a compact REST contract.
- `PlannerAgent` asks the LLM to create a scenario card from the scenario text and OpenAPI contract.
- `DataAgent` asks the LLM to generate missing values, then stores constants, generated values, and extracted response values.
- `ExecutorAgent` executes REST requests and checks only HTTP status plus required response extractions.
- `CriticAgent` asks the LLM to repair failed steps from server responses and execution traces.
- `RestTestDesigner` orchestrates planning, execution rounds, repair, and artifact writing.

The generated Postman collection contains collection variables, request bodies, status checks, and response extraction scripts for chained requests. Business assertions are intentionally not generated yet.

## LLM

Default configuration uses Ollama:

```powershell
ollama pull qwen2.5:14b-instruct
ollama serve
```

For a 16 GB GPU, start with `qwen2.5:14b-instruct`. If it is too slow, use `qwen2.5:7b-instruct` and update `config.yaml`.

## Run

```powershell
pip install -r requirements.txt
ollama serve
python main.py --plan-only
python src/mock.py
python main.py
python main.py --scenario .\data\scenarios\UC-011_dtp_duplicate_payment.md
```

Artifacts are written to `output/<scenario>/`.
