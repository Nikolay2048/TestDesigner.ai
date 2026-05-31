# Техническая реализация — подробное описание кода

## Стек технологий

| Компонент | Технология | Версия / Примечание |
|-----------|-----------|---------------------|
| Язык | Python | 3.11+ |
| Оркестрация | LangGraph | StateGraph с условными рёбрами |
| LLM-вызовы | LangChain | `with_structured_output` для строгого вывода |
| Модели данных | Pydantic v2 | Строгая типизация всех контрактов |
| HTTP-запросы | requests | Реальные вызовы, без мока |
| Mock-сервер | FastAPI | In-memory, поддерживает полный жизненный цикл UC_001–008 |
| Локальная LLM | Ollama / qwen2.5:14b-instruct | Для разработки и отладки |
| Тесты | pytest | 10 файлов, ~100+ тестов |

---

## Структура проекта

```
TestDesignerAI/
├── src/
│   ├── config.py               — RunConfig: base_url, env_vars, collection_name
│   ├── state.py                — GraphState (TypedDict) — единое состояние графа
│   ├── graph.py                — build_graph() — сборка LangGraph StateGraph
│   ├── main.py                 — точка входа, доменная конфигурация
│   ├── spec_parser.py          — парсинг OpenAPI YAML, резолв $ref
│   ├── executor.py             — execute_step, build_request, resolve_binding
│   ├── collection_builder.py   — PostmanStep, step_to_postman_item, export_collection
│   ├── llm.py                  — create_llm() — единая точка выбора модели
│   ├── models/
│   │   ├── spec.py             — Endpoint
│   │   ├── flow.py             — FlowCard, ScenarioStep, VariableBinding, VarSource
│   │   ├── test_design.py      — TestCase, TestTechnique
│   │   ├── diagnosis.py        — Diagnosis, DiagnosisCategory
│   │   ├── execution.py        — ExecResult
│   │   └── data_gen.py         — GenFunction, GenRequest, GenResponse (будущее)
│   ├── nodes/
│   │   ├── spec_parser.py      — узел графа: вызов parse_openapi_files
│   │   ├── scenario_analyst.py — узел: 2-phase LLM → FlowCard
│   │   ├── validator.py        — узел: статическая проверка FlowCard
│   │   ├── executor_stabilize.py — узел: фаза 1 + стабилизация
│   │   ├── stabilizer.py       — LLM-агент стабилизатора
│   │   ├── diagnosis.py        — узел: 2-layer диагностика
│   │   ├── test_designer.py    — узел: генерация тест-кейсов
│   │   ├── executor_run_all.py — узел: фаза 2 — прогон всех кейсов
│   │   ├── collection_builder.py — узел: сборка Postman-коллекции
│   │   └── reporter.py         — узел: метрики и отчёт
│   └── utils/
│       └── flow_flattener.py   — топологическая сортировка requires_flows
├── mock/
│   └── api.py                  — FastAPI mock-сервер UC_001–008
├── data/
│   ├── scenarios/              — постановки .md + OpenAPI .yaml
│   └── constants.json          — seed-константы
├── tests/                      — 10 тест-файлов
├── docs/                       — документация
├── output/                     — артефакты прогона
├── main.py / run_mock.py / run_verify.py / inspect_flow.py
```

---

## GraphState — единое состояние графа

`src/state.py` — TypedDict, единственная точка правды о данных между узлами:

```python
class GraphState(TypedDict):
    spec_paths: list[str]                      # входные пути к YAML
    raw_scenarios: str                          # текст постановок UC_001–UC_008
    endpoints: list[dict]                       # Endpoint.model_dump() — вывод Spec Parser
    flow_card: dict                             # FlowCard.model_dump() — вывод Scenario Analyst
    validation_errors: Annotated[list, add]    # накапливается из Validator
    stabilized_card: dict                       # FlowCard после стабилизации (Phase 1)
    all_stabilized_cards: Annotated[list, add] # все стабилизированные карточки (inter-flow)
    test_cases: list[dict]                      # TestCase.model_dump(mode='json')
    exec_results: list[dict]                    # результаты Phase 2
    diagnoses: list[dict]                       # Diagnosis.model_dump()
    collection: dict                            # Postman JSON
    metrics: dict                               # Reporter output
    trace: Annotated[list, add]                # лог прохождения узлов
```

`Annotated[list, add]` — стандартный LangGraph reducer: при обновлении узла новые элементы **добавляются** в список, а не перезаписывают его. Это позволяет накапливать ошибки валидации и карточки разных сценариев.

---

## Узел 1: Spec Parser

**Файлы:** `src/spec_parser.py`, `src/nodes/spec_parser.py`

**Задача:** парсит один или несколько OpenAPI YAML → плоский список `Endpoint`.

**Алгоритм:**
1. Читает каждый YAML через `yaml.safe_load`
2. Рекурсивно разворачивает все локальные `$ref` (intra-file)
3. Для каждого `path × method` создаёт `Endpoint`:
   - `operation_id` — из `operationId` или генерируется как `method_path_slug`
   - `path_params` — из `parameters` с `in: path`
   - `query_params` — из `parameters` с `in: query` (с флагом `required`)
   - `request_schema` — из `requestBody.content.application/json.schema`
   - `response_schemas` — словарь `{"200": schema, "400": schema, ...}`
   - `required_fields` — из `request_schema.required`
   - `constraints` — извлекаются рекурсивно из схем: `enum`, `minimum`, `maximum`, `minLength`, `maxLength`
4. Добавляет endpoint в общий список (дедупликация не применяется)

**Особенность constraints-парсера:** работает рекурсивно по всем свойствам схемы, не только верхнего уровня. Для вложенных объектов ключ — это имя поля (без пути), чтобы Test Designer мог искать по имени binding.

**Тесты:** `tests/test_spec_parser.py` — 100+ тестов на UC_001–008.

---

## Узел 2: Scenario Analyst

**Файл:** `src/nodes/scenario_analyst.py`

**Задача:** понимает текст постановки → создаёт `FlowCard` с шагами и биндингами.

**Два узких LLM-вызова:**

### Вызов 1 — выбор и порядок операций

Промпт получает: список всех `operation_id` из endpoints + текст постановок.
Выход: `OrderedOperations` — список `operation_id` в порядке вызова.

Python-валидация после LLM:
- Все operation_id существуют в endpoints → иначе retry (до 3 раз)
- Нет дубликатов (каждая операция только один раз)

### Вызов 2 — биндинг параметров (по одному шагу)

Для каждой операции LLM получает:
- спецификацию endpoint (поля, типы, constraints, required)
- список шагов, которые уже будут выполнены до этого (доступный FROM_STEP контекст)
- текст постановки

Выход: `StepInputs` — список `VariableBinding` и `produces` (JSONPath-полей из ответа).

Python-валидация после каждого шага:
- Все биндинги имеют `target_location` → если нет, инициализируется из `name`
- `FROM_STEP` биндинги ссылаются на предыдущий (уже выполненный) шаг
- `source_ref` и `source_field` заполнены для FROM_STEP

### VariableBinding — ключевая модель

```python
class VariableBinding(BaseModel):
    name: str                       # имя параметра ("carId", "dateFrom")
    source: VarSource               # GENERATED | FROM_STEP | STATIC | ENV | FROM_FLOW
    generator: str | None           # "uuid4" | "future_datetime" | "decimal_amount"
    source_ref: str | None          # step_id источника (для FROM_STEP)
    source_field: str | None        # JSONPath: "$.items[0].carId"
    value: str | None               # для STATIC и ENV
    target_location: str | None     # "query.cityId" | "body.carId" | "path.id"
```

---

## Узел 3: Validator

**Файл:** `src/nodes/validator.py`

**Задача:** статическая проверка FlowCard без LLM.

Проверки:
- operation_id каждого шага существует в endpoints
- `depends_on` ссылается только на предшествующие шаги (нет циклов, нет forward-ref)
- FROM_STEP биндинги ссылаются на `source_ref` — существующий предшествующий шаг
- FROM_FLOW биндинги: `source_ref` входит в `requires_flows`
- Все биндинги имеют `target_location`

Результат: список `validation_errors` (строки). Если пустой — граф идёт дальше.

---

## Узел 4: Executor Stabilize (Phase 1)

**Файлы:** `src/nodes/executor_stabilize.py`, `src/executor.py`

**Задача:** прогнать happy-path FlowCard на реальном сервере, стабилизировать данные при падениях.

### Цикл исполнения (CODE)

```
для каждого step в flow_card.steps:
    попытка = 0
    пока попытка < MAX_STEP_ATTEMPTS (3):
        лог = execute_step(step, ep, step_context, generated_cache, env, base_url)
        если passed:
            step_context[step_id] = response_json
            break
        иначе:
            если total_fixes >= MAX_TOTAL_FIXES (9): ABORT
            fix = stabilizer_agent(step, ep, лог_ошибки)
            применить fix к step.inputs
            попытка += 1
    если не passed после MAX_STEP_ATTEMPTS: is_stabilized = False, break
is_stabilized = True если все шаги прошли
```

### execute_step

1. Разрешить все биндинги через `resolve_binding()`
2. Собрать запрос через `build_request()` → (method, url, query_params, body)
3. Отправить через `requests.request(...)`
4. Распарсить JSON-ответ
5. Если `passed` (2xx): сохранить ответ в `step_context[step_id]`, извлечь `produces`
6. Вернуть лог: status_code, response, produces_extracted, passed

### resolve_binding

| source | Действие |
|--------|----------|
| STATIC | вернуть `binding.value` |
| ENV | найти в `env` dict (camelCase, snake_CASE — нечувствительно к регистру) |
| GENERATED | генерировать через `generate_value()`, кешировать по имени поля |
| FROM_STEP | взять из `step_context[source_ref]`, резолвить JSONPath через `resolve_jsonpath()` |

### generate_value

```python
"uuid4"             → str(uuid.uuid4())
"future_datetime"   → завтра 10:00 в ISO формате
"future_datetime_end" → послезавтра 10:00
"decimal_amount"    → "1000.00"
```

### generated_cache

Кэш по имени поля (не по step_id). Гарантирует, что `dateFrom` и `dateTo` используют разные записи кэша, а `carId` не генерируется заново в каждом шаге.

### resolve_jsonpath

Поддерживаемые паттерны (созданные Scenario Analyst):
- `$.field` — поле словаря
- `$.arr[0].field` — элемент массива по индексу
- `$.arr[*].field` — первый элемент массива

---

## Узел 4а: Stabilizer (LLM-агент)

**Файл:** `src/nodes/stabilizer.py`

**Задача:** узкий LLM — предложить конкретные изменения значений для упавшего шага.

Вход: `StabRequest` — упавший шаг, endpoint-спека, тело запроса, ответ сервера, текст постановки.
Выход: `StepFix` — список `InputFix(name, new_source, new_value, new_generator)`.

Python-валидация после LLM:
- Поле реально существует в `step.inputs`
- Нельзя изменить FROM_STEP/FROM_FLOW/ENV биндинги (они зависят от контекста)
- Фикс не пустой

Лимит LLM-retry: 2 попытки на вызов.

---

## Узел 5: Diagnosis

**Файл:** `src/nodes/diagnosis.py`

**Задача:** для каждого падения при стабилизации — классифицировать причину.

### Layer 1 (CODE: jsonschema)

Проверяет: нарушали ли **исходные** данные (до стабилизации) constraints из спеки?
- `enum` — значение вне разрешённого списка
- `minimum/maximum` — число вне диапазона
- `minLength/maxLength` — строка вне диапазона

Если нарушение найдено → `TEST_DATA_ISSUE` (или `CARD_ERROR` если binding FROM_STEP). Конец.

### Layer 2 (LLM)

Вызывается ТОЛЬКО если данные прошли Layer 1 (валидны по схеме), но сервер всё равно отверг.

Вопрос к LLM: «нарушают ли данные явные ограничения из постановки, которых нет в схеме?»

Ответ:
- Да → `SPEC_GAP`
- Нет, данные корректны и по схеме, и по постановке → `SUSPECTED_SERVICE_BUG`
- Недостаточно данных → `UNCERTAIN`

### Python-постпроцессинг (Принцип 3.5)

Форсирует после LLM:
- `SUSPECTED_SERVICE_BUG` → `needs_human=True`
- `confidence < 0.6` → `needs_human=True`
- Пустой `evidence[]` → понизить confidence, `needs_human=True`
- «Помогла» замена валидных данных → `needs_human=True`, статус `passed_with_suspicion`

---

## Узел 6: Test Designer

**Файл:** `src/nodes/test_designer.py`

**Задача:** «размножить» тест-кейсы от стабилизированной карточки по шести техникам.

### CODE-техники (детерминированные)

**Happy Path** (`_generate_happy_path`)
- 1 кейс, target = последний шаг
- setup_chain = все предыдущие шаги
- expected_status = 200 (или 201 если в спеке)

**Boundary** (`_generate_boundary`)
Итерирует по всем шагам и биндингам. Для каждого constraint:
- `enum` → 1 кейс с невалидным значением (не из enum), expected_status = 400/422
- `minimum` → 2 кейса: `min-1` (invalid), `min` (valid)
- `maximum` → 2 кейса: `max` (valid), `max+1` (invalid)
- `maxLength` → 2 кейса: `maxLength chars` (valid), `maxLength+1 chars` (invalid)
- `minLength` → 2 кейса: `minLength chars` (valid), `minLength-1 chars` (invalid)

Пропускает FROM_STEP/FROM_FLOW/ENV биндинги (не контролируемые тест-дизайном).

**Negative / Missing Field** (`_generate_missing_field`)
Для каждого required-поля: 1 кейс без этого поля, expected_status = 400/422.
Пропускает FROM_STEP/ENV (они из контекста, не из тест-данных).

### LLM-техники (по одному вызову каждая)

**Equivalence** (`_generate_equivalence_llm`)
Промпт получает текст сценария + описание шагов с полями и constraints.
Просит найти семантические классы, которые JSON Schema НЕ различает:
- роли пользователей (premium/standard клиент)
- состояния ресурса (активный/заблокированный автомобиль)
- типы операций (короткая/длинная аренда)

Ограничения промпта: не генерировать boundary-кейсы, не генерировать missing-field, не повторять очевидные enum-нарушения. Максимум 6 кейсов.

**Negative Semantic** (`_generate_negative_llm`)
Бизнес-нарушения из постановки:
- несуществующие или удалённые ID (fake UUID)
- конфликты (уже подтверждённое, уже отменённое)
- нарушения доменных правил (просроченный срок, чужой ID)

Важно: разрешает `allow_context_override=True` — LLM может заменить FROM_STEP биндинги fake-значениями для симуляции несуществующих ресурсов.

**State Based** (`_generate_state_based_llm`)
Только для флоу с 2+ шагами. Нарушения порядка:
1. Пропустить предусловие + использовать fake ID
2. Повторить шаг, который должен выполниться только один раз
3. Выполнить шаги в неправильном порядке

Отдельная Pydantic-модель `_StateCase` с `setup_step_ids` — только те шаги happy-path, которые нужны для создания контекста.

### Валидация LLM-вывода (Принцип 3.5)

После каждого LLM-вызова:
- `step_id` нормализуется: `step_02_createDraft` → `step_02` (strip suffix через `startswith`)
- `field_name` нормализуется: `query.cityId` → `cityId` (split на `.`, взять последнее)
- Неизвестные step_id и field_name → предупреждение + пропуск кейса

### _apply_mutations

```python
def _apply_mutations(step, changes, to_remove, allow_context_override=False):
    # changes: {field_name: new_value}
    # to_remove: set of field_names to omit
    # allow_context_override: если True, можно заменять FROM_STEP/ENV биндинги
```

По умолчанию FROM_STEP/ENV биндинги защищены — их значения приходят из контекста исполнения.

---

## Узел 7: Executor Run All (Phase 2)

**Файл:** `src/nodes/executor_run_all.py`

**Задача:** прогнать все TestCase, зафиксировать результаты.

Для каждого TestCase:
1. Сбросить mock-сервер (`POST /api/v1/mock/reset`)
2. Прогнать `setup_chain` — накопить контекст (step_context)
3. Прогнать target-шаг с `modified_inputs`
4. Проверить assertions:
   - `status_code / expected` — точное совпадение
   - `status_code / expected_range` — диапазон [lo, hi]
5. Для негативных кейсов: `passed = (actual_status == expected_status)` — ожидали ошибку, получили её

Стабилизатор **не вызывается** — падения в Phase 2 — это факт, а не проблема.

---

## Узел 8: Collection Builder

**Файлы:** `src/nodes/collection_builder.py`, `src/collection_builder.py`

**Задача:** собрать Postman-коллекцию из данных пайплайна.

### Структура коллекции

```json
{
  "info": {"name": "Test Collection — Flow", "schema": "...v2.1.0..."},
  "variable": [
    {"key": "baseUrl", "value": "http://localhost:8000"},
    {"key": "customerId", "value": "aaa-bbb-ccc"}
  ],
  "item": [
    {
      "name": "[flow_id] Flow Name",
      "item": [
        {"name": "Happy Path", "item": [...]},
        {"name": "Boundary", "item": [...]},
        {"name": "Negative", "item": [...]},
        {"name": "Equivalence", "item": [...]},
        {"name": "State Based", "item": [...]}
      ]
    }
  ]
}
```

Один `item` на сценарий, внутри папки по техникам в фиксированном порядке.
Каждый тест-кейс — subfolder с `[setup]` шагами и `[target]` шагом.

### Маппинг VariableBinding → Postman

| source | В Postman |
|--------|-----------|
| STATIC | literal value ("77", "economy") |
| ENV | `{{customerId}}` |
| GENERATED | `{{carId}}` — pre-request JS генерирует и пишет в environment |
| FROM_STEP | `{{step_01__items__0__carId}}` — test script предыдущего шага пишет переменную |

### _safe_varname

Конвертирует (step_id, JSONPath) в имя Postman-переменной:
- `("step_01", "$.items[0].carId")` → `"step_01__items__0__carId"`
- `("step_02", "$.reservationDraftId")` → `"step_02__reservationDraftId"`

### _gen_prerequest (JS)

Для GENERATED биндингов генерирует JavaScript:
```javascript
// uuid4
const __uuid4 = () => '...'; pm.environment.set('carId', __uuid4());
// future_datetime
(function() { const d = new Date(); d.setDate(d.getDate()+1); ... pm.environment.set('dateFrom', d.toISOString()); })();
```

### _gen_test_script (JS)

```javascript
pm.test('Status 200', () => { pm.response.to.have.status(200); });
const _body = pm.response.json();
try { pm.environment.set('step_01__items__0__carId', _body.items[0].carId); } catch(e) {}
```

---

## Узел 9: Reporter

**Файл:** `src/nodes/reporter.py`

**Задача:** подсчёт метрик и формирование отчёта для человека.

Метрики:
- **Endpoint coverage:** `covered_endpoints / total_endpoints × 100%` (из `test_cases[].group`)
- **Test case breakdown:** по технике, по endpoint
- **Execution summary Phase 2:** total_run, passed, failed, pass_rate_pct, failed_cases[]
- **Diagnosis summary:** by_category{}, needs_human_count
- **Stabilization summary:** is_stabilized, total_fixes_applied

Вывод — ASCII-таблица в stdout + dict в state.

---

## Inter-flow composition (flow_flattener)

**Файл:** `src/utils/flow_flattener.py`

**Задача:** когда FlowCard имеет `requires_flows`, разворачивает цепочку зависимостей в плоский список шагов-предусловий.

Алгоритм:
1. `topological_order(flow_id, all_flows)` — DFS post-order по `requires_flows`
2. Для каждого зависимого флоу: добавить его шаги в `setup_chain`
3. Обнаружение циклов через `visiting` set
4. Исключение из setup шагов текущего флоу (чтобы не дублировать)

Используется в Test Designer: prepend inter_flow_setup к setup_chain каждого TestCase.

---

## Mock-сервер

**Файл:** `mock/api.py`

FastAPI-сервер с in-memory базой данных. Реализует все UC_001–008 endpoints:
- `/api/v1/cars/availability` — поиск доступных авто с проверкой дат и города
- `/api/v1/reservations/drafts` — создание черновика с проверкой занятости авто
- `/api/v1/reservations/{id}/confirm` — подтверждение, блокировка авто
- `/api/v1/rentals/{id}/start` — начало аренды
- `/api/v1/rentals/{id}/extend` — продление
- `/api/v1/rentals/{id}/incidents` — инцидент-репорт
- `/api/v1/rentals/{id}/complete` — завершение, расчёт стоимости
- `/api/v1/reservations/{id}/cancel` — отмена

Специальный endpoint:
- `POST /api/v1/mock/reset` — сброс в начальное состояние (для изоляции тестов)

Валидации в mock-сервере:
- Пересечение дат (авто уже занято)
- TTL черновика (истёк через 15 минут)
- Статус клиента (заблокированный не может бронировать)
- Двойное подтверждение (нельзя подтвердить дважды)

---

## Конфигурация

`src/config.py` — единственная точка настройки:

```python
@dataclass
class RunConfig:
    base_url: str = "http://localhost:8000"
    env_vars: dict = field(default_factory=dict)
    collection_name: str = "API Test Collection"

CONFIG = RunConfig()
```

Устанавливается в `main.py` перед запуском графа:
```python
CONFIG.base_url = "http://localhost:8000"
CONFIG.env_vars = {
    "customerId": "aaa-bbb-ccc-ddd",
    "paymentId":  "999-888-777-666",
}
CONFIG.collection_name = "Carsharing API — Full Test Suite"
```

---

## Выбор LLM

`src/llm.py` — единая точка, переключение одной строкой:

```python
def create_llm():
    from langchain_ollama import ChatOllama
    return ChatOllama(model="qwen2.5:14b-instruct", temperature=0)
```

Для финальной проверки — замена на `ChatAnthropic` с моделью claude-sonnet.

---

## Запуск системы

```bash
# 1. Запустить mock-сервер
python run_mock.py       # FastAPI на http://localhost:8000

# 2. Запустить полный пайплайн
python src/main.py
# → output/carsharing_test_suite.json  (Postman коллекция)
# → output/test_cases.json             (тест-кейсы JSON)
# → output/test_cases.md               (тест-кейсы Markdown)

# 3. Верификация на UC_001–003
python run_verify.py
# → output/run_result.json
# → output/verify_collection.json
# → output/verify_test_cases.md

# 4. Тесты
pytest tests/ -v
```
