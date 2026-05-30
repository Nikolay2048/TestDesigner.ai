# Возможные улучшения системы

Этот документ описывает потенциальные улучшения, сгруппированные по приоритету и сложности реализации. Для каждого улучшения указаны мотивация, трейдоффы и точки изменения в коде.

---

## Приоритет 1: Критичные для надёжности

### 1.1 Поддержка cross-file $ref в Spec Parser

**Текущее состояние:** `src/spec_parser.py` резолвит только локальные (intra-file) `$ref`. При встрече inter-file ссылки (`$ref: "./common.yaml#/components/schemas/Error"`) падает с `ValueError`.

**Мотивация:** реальные OpenAPI проекты активно используют общие схемы в отдельных файлах (common.yaml, errors.yaml, components.yaml).

**Реализация:**
```python
# В _resolve_refs() добавить:
if ref.startswith("./") or ref.startswith("../"):
    ext_path = (spec_dir / ref_path).resolve()
    with open(ext_path) as f:
        ext_doc = yaml.safe_load(f)
    return _resolve_refs(navigate(ext_doc, anchor), ext_path.parent, visited)
```

**Сложность:** средняя. Нужно передавать `spec_dir` рекурсивно + кешировать загруженные файлы.

**Где менять:** `src/spec_parser.py` — функция `_resolve_refs`.

---

### 1.2 Human Review interrupt (LangGraph pause)

**Текущее состояние:** диагнозы с `needs_human=True` записываются в state, но граф не останавливается — продолжает работу без участия человека.

**Мотивация:** принцип системы — «подозрение на баг сервиса → всегда к человеку». Сейчас этот принцип декларирован, но не реализован.

**Реализация:**
```python
# В graph.py:
graph.add_node("human_review", human_review_node)
graph.add_conditional_edges(
    "diagnosis",
    lambda state: "human_review" if any(
        d.get("needs_human") for d in state.get("diagnoses", [])
    ) else "test_designer"
)

# human_review_node:
def human_review_node(state):
    return interrupt({"diagnoses_for_review": [d for d in state["diagnoses"] if d["needs_human"]]})
```

После interrupt человек вводит `human_decision` для каждого диагноза и граф возобновляется.

**Сложность:** низкая (LangGraph interrupt — встроенная фича). Нужен CLI/UI для ввода решения.

---

### 1.3 Retry-стратегия для LLM с exponential backoff

**Текущее состояние:** LLM-вызовы в `stabilizer.py` и `scenario_analyst.py` делают retry через простой цикл без задержки.

**Мотивация:** локальная Ollama модель может временно не отвечать. Remote API может выдать rate-limit.

**Реализация:**
```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def _call_llm_with_retry(chain, variables):
    return chain.invoke(variables)
```

**Где менять:** `src/nodes/stabilizer.py`, `src/nodes/scenario_analyst.py`, `src/nodes/test_designer.py`.

---

## Приоритет 2: Расширение возможностей

### 2.1 Pairwise testing (Test Designer)

**Текущее состояние:** в Test Designer нет техники Pairwise (комбинаторное покрытие параметров).

**Мотивация:** endpoints с несколькими независимыми параметрами требуют pairwise-покрытия. Пример: `GET /cars/availability?cityId&carClass&driverAge` — комбинации значений.

**Реализация:**
```python
# Добавить в test_designer.py:
from itertools import product

def _generate_pairwise(flow_card, ep_map):
    # Найти step с 3+ независимыми STATIC/GENERATED параметрами с constraints
    # Применить алгоритм allpairs или IPOG для генерации покрывающего набора
    # Сгенерировать TestCase для каждой пары
```

Существуют готовые библиотеки: `allpairspy`, `pairwise`.

---

### 2.2 Data Generator как полноценный сервис

**Текущее состояние:** `src/executor.py` содержит 4 захардкоженных генератора (uuid4, future_datetime, future_datetime_end, decimal_amount). Модели `GenFunction/GenRequest/GenResponse` определены, но не используются.

**Мотивация:** по мере роста числа API потребуются новые типы данных — адреса, телефоны, VIN-коды, номера водительских удостоверений.

**Реализация:**
1. JSON/SQLite-хранилище `GenFunction` (name, python_impl, postman_snippet, status)
2. `DataGeneratorService.generate(request: GenRequest) → GenResponse`
3. Если approved-функция найдена — используется сразу
4. Если нет — LLM пишет новую функцию (python_impl + postman_snippet), статус `pending_review`
5. Human Review interrupt: человек одобряет/отклоняет новую функцию

**Важно:** сгенерированный Python исполнять в sandbox (`RestrictedPython` или subprocess с таймаутом).

---

### 2.3 Postman коллекции для нескольких сценариев

**Текущее состояние:** `collection_builder` строит коллекцию для одного `stabilized_card`. При запуске нескольких сценариев коллекции пишутся отдельно.

**Мотивация:** один JSON-файл со всеми сценариями удобнее для команды QA.

**Реализация:**
```python
# Изменить collection_builder:
# all_stabilized_cards → итерировать, строить scenario_folder для каждой
# Один top-level collection со всеми scenario folders внутри
collection = {
    "item": [scenario_folder_1, scenario_folder_2, ...]
}
```

**Где менять:** `src/nodes/collection_builder.py` — `collection_builder()`, добавить итерацию по `all_stabilized_cards`.

---

### 2.4 Teardown steps в Executor

**Текущее состояние:** `FlowCard.teardown_steps` определён в модели, но Executor его не выполняет.

**Мотивация:** сценарии меняют состояние системы (авто блокируется). Без teardown тесты не идемпотентны — при повторном прогоне авто уже занято.

**Реализация:**
```python
# В executor_stabilize и executor_run_all добавить:
finally:
    for step in flow_card.teardown_steps:
        try:
            execute_step(step, ep_map[step.operation_id], ...)
        except:
            pass  # teardown best-effort
```

---

### 2.5 Параллельное исполнение независимых тест-кейсов

**Текущее состояние:** Executor Run All прогоняет кейсы последовательно.

**Мотивация:** при 30–50 кейсах и времени ответа ~100ms — прогон займёт 3–5 секунд. При росте числа кейсов это станет заметным.

**Проблема:** mock-сервер использует in-memory state — параллельные тесты будут мешать друг другу.

**Решение:** параллелизм только для кейсов с `setup_chain=[]` (нет состояния). Или: каждый parallel worker получает свой экземпляр mock-сервера на отдельном порту.

---

### 2.6 По кодам ответов: кейс на каждый задокументированный код

**Текущее состояние:** система генерирует кейсы по техникам, но не гарантирует покрытие каждого задокументированного кода ответа (400, 401, 404, 422).

**Мотивация:** если в спеке задокументирован `401 Unauthorized`, для него должен быть кейс с невалидным токеном.

**Реализация:**
```python
def _generate_per_response_code(flow_card, ep_map):
    for step in flow_card.steps:
        ep = ep_map[step.operation_id]
        for code, schema in ep["response_schemas"].items():
            if int(code) >= 400:
                # Сгенерировать кейс, провоцирующий этот код
                # 401 → убрать auth header
                # 404 → несуществующий ID
                # 422 → неверный тип данных
```

---

### 2.7 Валидация тела ответа (не только статус-код)

**Текущее состояние:** assertions содержат только `status_code`. Структура ответа не проверяется.

**Мотивация:** сервер может вернуть 200, но с неправильным телом.

**Реализация:** в `_gen_test_script` добавить проверки из `response_schemas[200]`:
```javascript
pm.test('Response schema', () => {
    const body = pm.response.json();
    pm.expect(body).to.have.property('carId');
    pm.expect(body.carId).to.be.a('string');
});
```

---

## Приоритет 3: Инфраструктура и DevEx

### 3.1 Prompt caching (Anthropic API)

**Мотивация:** при использовании Claude через Anthropic API кешировать system-промпты и статичный контекст (текст спеки) между вызовами. Экономия ~90% токенов на повторяющийся контекст.

**Реализация:** добавить `cache_control: {"type": "ephemeral"}` к system-промптам в `ChatPromptTemplate`.

---

### 3.2 Пакетный режим (несколько сценариев за прогон)

**Текущее состояние:** `graph.invoke()` обрабатывает один сценарий за раз.

**Мотивация:** при 8 UC нужно вызывать граф 8 раз вручную.

**Реализация:**
```python
# В main.py:
scenarios = parse_scenarios(raw_scenarios)  # разбить по UC_001–UC_008
for scenario in scenarios:
    state = graph.invoke({"raw_scenarios": scenario.text, ...})
    all_cards.append(state["stabilized_card"])
```

Или: превратить граф в Map-Reduce с LangGraph's `Send` API.

---

### 3.3 Визуализация зависимостей сценариев

**Мотивация:** при росте числа сценариев (20+) межсценарные зависимости (`requires_flows`) станет трудно отслеживать вручную.

**Вариант A — graphviz (простой):**
```python
from graphviz import Digraph
g = Digraph()
for flow_id, flow in all_flows.items():
    for dep in flow.requires_flows:
        g.edge(dep, flow_id)
g.render("docs/scenario_deps", format="png")
```

**Вариант B — Neo4j (если сценариев 50+):**
- Nodes: FlowCard (flow_id, name, step_count)
- Relationships: REQUIRES, PRODUCES, CONSUMES
- Запросы: найти все транзитивные предусловия, найти циклы, найти изолированные сценарии

Neo4j оправдан, если нужен интерактивный граф с навигацией и поиском по зависимостям. Для документирования достаточно graphviz.

---

### 3.4 Экспорт тест-кейсов в TestRail / Jira

**Мотивация:** команды QA часто ведут тест-кейсы в TestRail или Jira Zephyr.

**Реализация:** маппинг `TestCase` → TestRail API:
- `title` → `title`
- `technique` → section
- `assertions` → expected_result
- `steps_log` → steps

---

### 3.5 Мониторинг и алерты при деградации качества

**Мотивация:** если pass_rate упал с 95% до 70% после деплоя — нужен автоматический алерт.

**Реализация:**
```python
if metrics["execution"]["pass_rate_pct"] < THRESHOLD:
    send_alert(f"Pass rate: {pass_rate}%")
```

Интеграция с Slack webhook / Grafana / PagerDuty.

---

## Резюме по приоритетам

| Улучшение | Приоритет | Сложность | Impact |
|-----------|-----------|-----------|--------|
| Cross-file $ref | Критичный | Средняя | Поддержка реальных проектов |
| Human Review interrupt | Критичный | Низкая | Принцип безопасности |
| Teardown steps | Высокий | Низкая | Изоляция тестов |
| Multi-scenario collection | Высокий | Низкая | UX для QA |
| Response body validation | Высокий | Средняя | Качество тестов |
| Per-response-code кейсы | Средний | Низкая | Полнота покрытия |
| Data Generator сервис | Средний | Высокая | Масштабируемость |
| Pairwise testing | Средний | Средняя | Комбинаторное покрытие |
| Prompt caching | Средний | Низкая | Стоимость API |
| Параллельное исполнение | Низкий | Высокая | Скорость |
| Neo4j визуализация | Низкий | Высокая | Документирование |
