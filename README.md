# TestDesignerAI

Учебный проект агентной системы для автоматического тест-дизайна REST API.

Мы развиваем систему поэтапно. Сейчас реализован только первый агент, остальные этапы оставлены заглушками, чтобы архитектура была понятна и не разрасталась раньше времени.

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
- классифицировать endpoint-упоминания, если они прямо написаны в постановке.
- зафиксировать зависимости сценария, если для выполнения нужен другой сценарий, существующее состояние или подготовленные данные.

Endpoint-упоминания сначала извлекает обычный код по регулярному выражению. LLM не должна придумывать endpoint'ы, она только классифицирует найденные.

Endpoint может быть:

- в шапке файла;
- в конкретном шаге;
- в бизнес-правиле;
- без понятного места использования.

Поэтому агент возвращает `endpoint_mentions`.

Если сценарий зависит от другого сценария или состояния системы, агент возвращает `scenario_dependencies`.

## Будущие этапы

Пока это заглушки:

- `Flow Designer` - выберет REST operations для бизнес-шагов.
- `Data Binding` - заполнит body/path/query и связи переменных.
- `FlowExecutor` - выполнит HTTP-запросы.
- `Stabilization Diagnostician` - объяснит падения.
- `Stabilization Fixer` - позже попробует предложить исправление.
- `Test Designer` - позже сгенерирует тест-кейсы по стабилизированному happy path.

## Запуск без LLM

Генерирует prompt первого агента:

```bash
python src/main.py --scenario data/carsharing/specs/01-basic-economy-rental.md --out runs/latest --llm none
```

## Запуск через Ollama

```bash
python src/main.py --scenario data/carsharing/specs/01-basic-economy-rental.md --out runs/latest --llm ollama --model qwen3:14b
```

Артефакты:

- `runs/latest/documentation_analyst.prompt.md`
- `runs/latest/documentation_analyst.run.json`
- `runs/latest/state.json`
