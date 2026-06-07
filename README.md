# TestDesignerAI

Учебный проект агентной системы для автоматического тест-дизайна REST API.

Мы развиваем систему поэтапно. Сейчас реализованы первые два агента, остальные этапы оставлены заглушками, чтобы архитектура была понятна и не разрасталась раньше времени.

## Текущий этап

### Documentation Analyst Agent

Файл: `src/agents/documentation_analyst.py`

Задача агента:

- прочитать постановку;
- выделить бизнес-цель;
- выделить предусловия;
- выделить ordered business steps;
- выделить бизнес-правила;
- выделить критерии успеха и негативные условия;
- классифицировать endpoint-упоминания, если они прямо написаны в постановке;
- зафиксировать зависимости сценария, если для выполнения нужен другой сценарий, существующее состояние или подготовленные данные.

Endpoint-упоминания сначала извлекает обычный код по регулярному выражению. LLM не должна придумывать endpoint'ы, она только классифицирует найденные.

### Endpoint Mapper Agent

Файл: `src/agents/endpoint_mapper.py`

Задача агента:

- взять `business_steps` из результата Documentation Analyst;
- взять `endpoint_mentions` из постановки;
- взять компактный список OpenAPI operations;
- сопоставить каждый бизнес-шаг с одним или несколькими endpoint'ами.

Агент не получает request/response schema. Это важно, чтобы не перегружать маленькую модель.

После агента обычный код проверяет, что выбранные endpoint'ы реально есть в OpenAPI. Выдуманные endpoint'ы удаляются и попадают в risks.

Если для бизнес-шага нет отдельного REST endpoint'а, агент не должен выдумывать его. Такой шаг попадает в `unmapped_steps` с причиной.

## Будущие этапы

Пока это заглушки:

- `Data Binding` - заполнит body/path/query и связи переменных.
- `FlowExecutor` - выполнит HTTP-запросы.
- `Stabilization Diagnostician` - объяснит падения.
- `Stabilization Fixer` - позже попробует предложить исправление.
- `Test Designer` - позже сгенерирует тест-кейсы по стабилизированному happy path.

## Запуск без LLM

Генерирует prompt первого агента:

```bash
python src/main.py --scenario data/carsharing/specs/01-basic-economy-rental.md --openapi data/carsharing/openapi/openapi.yaml --out runs/latest --llm none
```

## Запуск через Ollama

```bash
python src/main.py --scenario data/carsharing/specs/01-basic-economy-rental.md --openapi data/carsharing/openapi/openapi.yaml --out runs/latest --llm ollama
```

По умолчанию используется `qwen3:14b` для Ollama и `qwen/qwen3-32b` для OpenRouter.
Модель можно переопределить единым параметром `--model`.

Артефакты:

- `runs/latest/documentation_analyst.prompt.md`
- `runs/latest/documentation_analyst.run.json`
- `runs/latest/endpoint_mapper.prompt.md`
- `runs/latest/endpoint_mapper.run.json`
- `runs/latest/state.json`
