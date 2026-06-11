# TestDesignerAI

Учебный проект агентной системы для автоматического тест-дизайна REST API.

Система принимает постановку системного анализа, OpenAPI-спецификацию и
константы тестировщика. На выходе она сохраняет:

- стабилизированный happy path;
- трассы HTTP-запросов, диагностик и исправлений;
- исполняемые тест-кейсы;
- Postman-коллекцию;
- stable package для зависимых сценариев.

Архитектура гибридная: LLM решает небольшие семантические задачи, а обычный
код отвечает за OpenAPI, граф данных, валидацию, HTTP-выполнение, хранение
состояния, применение патчей и экспорт.

Актуальная документация и план следующего этапа:
[docs/system/README.md](docs/system/README.md).

## Модели

Рекомендуемые локальные модели:

- `qwen3.5:27b` для основной разработки и сложных агентных задач;
- `qwen3:14b` для быстрых регрессий и итераций над промптами.

Настройки Ollama можно переопределить переменными:

- `OLLAMA_TIMEOUT`;
- `OLLAMA_TEMPERATURE`;
- `OLLAMA_NUM_CTX`;
- `OLLAMA_THINK`;
- `OLLAMA_SEED`.

## Один сценарий

```powershell
python .\src\main.py `
  --scenario .\data\carsharing\specs\01-basic-economy-rental.md `
  --openapi .\data\carsharing\openapi\openapi.yaml `
  --test-data .\data\carsharing\test-data.yaml `
  --out .\runs\example `
  --stable-dir .\runs\example\stable `
  --base-url http://127.0.0.1:8080 `
  --llm ollama `
  --model qwen3.5:27b `
  --resolve-dependencies `
  --run-test-cases `
  --export-postman
```

## Все сценарии

Скрипты сами поднимают и останавливают соответствующий mock-сервер:

```powershell
.\scripts\run_carsharing.ps1 -Model qwen3.5:27b
.\scripts\run_clinic.ps1 -Model qwen3.5:27b
```

## Тесты

Большая часть suite выполняется без внешних сервисов:

```powershell
python -m pytest -q
```

`tests/test_chains_requests.py` ожидает carsharing mock на
`http://127.0.0.1:8080`.

## Оценка моделей

```powershell
python .\evals\agent_benchmark.py --repetitions 3 `
  --models qwen3:14b qwen3.5:27b qwen3.5:35b `
  --out .\evals\results\agent_benchmark
```

Сводка испытаний от 11 июня 2026 года:
[model benchmark](docs/system/13-model-benchmark-2026-06-11.md).
