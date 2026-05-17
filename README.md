# TestDesignerAI

Новая мультиагентная система для генерации Postman коллекций из постановок системного анализа и OpenAPI.

## Состав

- `src/mock.py` - сохраненный mock server.
- `src/testdesigner/openapi_parser.py` - OpenAPI parser с разрешением `$ref`.
- `src/testdesigner/agents.py` - Agent 1, Agent 2, Agent 3.
- `src/testdesigner/tools.py` - deterministic tools для HTTP, шаблонов, JSONPath и проверок.
- `src/testdesigner/postman.py` - генерация Postman collection/environment.
- `src/testdesigner/pipeline.py` - полный pipeline.

## Запуск

```powershell
pip install -r requirements.txt
python main.py --plan-only
python src/mock.py
python main.py
```

Артефакты пишутся в `output/`:

- `openapi_catalog.json`
- `scenario_card.json`
- `execution_report.json`
- `postman_collection_plan.json`
- `postman_collection_execution.json`
- `postman_environment.json`

## Архитектура

Agent 1 строит сценарную карточку. Если LLM настроен, используется structured output; если нет, работает deterministic fallback для типовых сценариев из тестовых данных.

Agent 2 исполняет сценарий итеративно: подставляет переменные, вызывает Agent 3, делает REST-запросы, проверяет статус и assertions, извлекает JSONPath-переменные и повторяет шаг до лимита попыток.

Agent 3 генерирует данные по policy registry. Для дат, id, requestId и телефонов есть встроенные политики. Каждая generated variable сохраняет значение, имя генератора, параметры и код policy.
