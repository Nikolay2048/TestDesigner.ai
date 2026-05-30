# Demo Guide — Система автоматического тест-дизайна и генерации коллекций

## Быстрый запуск

### 1. Запустить mock-сервер (каршеринг)
```bash
python run_mock.py
# → сервер поднимется на http://localhost:8000
```

### 2. Запустить полный прогон
```bash
python run_all_scenarios.py
# или через unified runner:
python run.py carsharing --no-interactive
```

### 3. Посмотреть артефакты
Все файлы в папке `output/`:
```
output/
├── full_test_suite.json          ← Postman коллекция (импорт File → Import)
├── coverage_report.md            ← Сводный отчёт покрытия
├── test_cases_full.md            ← Все тест-кейсы с шагами
├── all_results.json              ← Полный JSON дамп
├── run.log                       ← Лог выполнения
│
├── Carsharing_-_Booking_Flow/
│   ├── test_plan.md              ← Тест-план
│   ├── test_cases_full.md        ← Тест-кейсы с шагами и проверками
│   ├── test_cases_tms.csv        ← CSV для импорта в TestRail/Xray/Zephyr
│   ├── coverage_report.md        ← Покрытие endpoint'ов
│   ├── execution_report.md       ← Детальный отчёт о прогоне
│   ├── defect_report.md          ← Обнаруженные дефекты
│   ├── stabilization_trace.md    ← Трейс стабилизации happy-path
│   └── allure-results/           ← JSON для Allure Report
│
├── Carsharing_-_Cancel_Reservation/
│   └── (те же артефакты для UC_008)
│
└── Carsharing_-_Bug_Detection_Demo/
    └── (те же артефакты для демо-сценария)
```

---

## Демонстрационные сценарии

### Демо 1: UC_001-003 — Основной поток бронирования

**Что показывает:** полный цикл тест-дизайна на многошаговом сценарии.

**Запуск:**
```bash
python run.py carsharing --uc UC_001 UC_002 UC_003
```

**На что смотреть:**
1. Scenario Analyst правильно выстраивает цепочку операций и передаёт `carId`, `draftId` через шаги
2. Executor успешно проходит все 3 шага happy-path без единого фикса
3. Test Designer генерирует **32 тест-кейса** по 5 техникам автоматически
4. Coverage Report показывает **100% endpoint coverage** (все 3 операции покрыты)
5. В `test_cases_tms.csv` — готовый файл для импорта в TestRail

**Открыть в Postman:** `output/full_test_suite.json` → File → Import

---

### Демо 2: UC_008 — Отмена бронирования с seed-данными

**Что показывает:** самодостаточный запуск сценария с предпосылками из env_vars.

**Запуск:**
```bash
python run.py carsharing --uc UC_008
```

**На что смотреть:**
1. Сценарий использует seed бронирование `cccccccc-0000-0000-0000-cccccccccccc` из `constants.json`
2. Все 4 шага (GET резервация → GET политика → POST отмена → GET возврат) проходят с первой попытки
3. Полный coverage всех 4 endpoints UC_008

---

### Демо 3: UC_DEMO — Обнаружение багов и расхождений со спекой ⭐

**Что показывает:** интеллект системы — автоматическое выявление дефектов **без подсказок в постановке**.

Постановка описывает нормальное поведение сервиса. Баги живут только в реализации mock.
Система проходит цикл: стабилизация → генерация тест-кейсов → прогон → диагностика.

**Запуск:**
```bash
python run.py carsharing --uc UC_DEMO
```

**На что смотреть:**

#### 3а. SPEC_GAP — Пробел в спецификации (обнаруживается самостоятельно)
Сервер принимает только `currency=RUB`, хотя спека явно разрешает USD и EUR.
Постановка НЕ упоминает это ограничение — система обнаруживает его через тест-кейсы.

Ожидаемое поведение системы:
1. Happy path стабилизируется с `currency=RUB` (единственная рабочая валюта)
2. Test Designer генерирует equivalence-кейсы с `currency=USD` и `currency=EUR`
3. Эти кейсы **падают** в Phase 2 с неожиданными статусами
4. `output/Carsharing_-_Bug_Detection_Demo/defect_report.md` содержит описание проблемы

Как интерпретировать: **SPEC_GAP** — документация говорит одно, сервер делает другое. Нужно либо расширить mock, либо обновить спеку.

#### 3б. SUSPECTED_SERVICE_BUG — Баг сервера (обнаруживается самостоятельно)
Суммы, кратные 100 (1000, 2000, ...), вызывают HTTP 500.
Постановка НЕ упоминает об этом — только говорит что диапазон 1–999999.

Ожидаемое поведение системы:
1. Test Designer генерирует boundary-кейсы с конкретными значениями (в т.ч. 1000)
2. Phase 2: кейс с `amount=1000` падает с 500
3. Diagnosis: данные валидны по JSON Schema (minimum=1, maximum=999999, value=1000 ✓) → КРАСНАЯ ЗОНА → Layer 2 LLM → `SUSPECTED_SERVICE_BUG`
4. `defect_report.md` содержит описание: "valid data rejected by server"

Как интерпретировать: **SERVICE_BUG** — данные валидны по схеме, сервер отклоняет → баг реализации.

---

### Демо 4: Human-in-the-loop (интерактивный режим)

**Что показывает:** система запрашивает решение человека перед тест-дизайном.

**Требует:** сценарий с failing stabilization + LangGraph checkpointer.

**Запуск** (без --no-interactive):
```bash
python run.py carsharing  # будет ждать ввода если обнаружены подозрительные диагнозы
```

Если в ходе стабилизации `Diagnosis` классифицирует проблему как `SUSPECTED_SERVICE_BUG` или `needs_human=True`, граф **прерывается** и ждёт решения:
```
HUMAN REVIEW REQUIRED
[case_id] step=step_01
category=service_bug, confidence=0.85
evidence: ["data valid per schema", "server returned 500"]
Decision [approve/reclassify/fix_card/skip] (default=approve): 
```

---

### Демо 5: CLI — Выбор конкретного UC с зависимостями

```bash
# Запустить только UC_008
python run.py carsharing --uc UC_008

# Запустить UC_003 с его зависимостями (UC_001, UC_002)
python run.py carsharing --uc-with-deps UC_003

# Запустить именованную группу
python run.py carsharing --group "UC_001-003: Booking Flow"

# Запустить доску объявлений
python run_mock_bulletin.py  # в отдельном терминале
python run.py bulletin_board
```

---

## Выходные артефакты — полный список

| Файл | Назначение | Где используется |
|------|-----------|-----------------|
| `full_test_suite.json` | Postman коллекция | Postman, Newman CI/CD |
| `test_cases_full.md` | Тест-кейсы со шагами | Ревью, документация |
| `test_cases_tms.csv` | Импорт в TMS | TestRail, Xray, Zephyr |
| `allure-results/*.json` | Allure Report | CI/CD (Jenkins, GitLab CI) |
| `coverage_report.md` | Покрытие тестами | Ревью качества |
| `test_plan.md` | Тест-план | Документация |
| `execution_report.md` | Результаты прогона | Отчётность |
| `defect_report.md` | Список дефектов | Баг-трекер |
| `stabilization_trace.md` | Трейс стабилизации | Отладка, анализ |
| `run.log` | Полный лог | Разработчики, DevOps |

---

## Конфигурация

Все параметры в `constants.json`:
```json
{
  "domains": {
    "carsharing": {
      "base_url": "http://localhost:8000",
      "env_vars": { "customerId": "...", "paymentId": "..." },
      "scenarios": { "UC_001": { "files": [...], "requires": [] }, ... },
      "groups": [...]
    }
  }
}
```

Сменить предметную область — одна строка:
```bash
python run.py bulletin_board
```

Сменить модель — одна строка в `src/llm.py`:
```python
MODEL = "qwen2.5:14b-instruct"  # или claude-opus-4-8, gpt-4o и т.д.
```

---

## Что система умеет автоматически

1. **Парсинг OpenAPI** с резолвингом `$ref`
2. **Анализ сценариев** и построение FlowCard с правильными биндингами переменных
3. **Стабилизация** — самостоятельно фиксит данные если шаг падает
4. **Диагностика** двух уровней:
   - Код: нарушает ли данные jsonschema endpoint'а?
   - LLM: соответствует ли данные постановке?
5. **Human-in-the-loop** при обнаружении подозрительных дефектов
6. **Генерация тест-кейсов** по 5 техникам (happy path, boundary, negative, equivalence, state-based)
7. **Исполнение** всех кейсов с реальными HTTP-запросами
8. **Генерация Postman коллекции** с pre-request scripts и test scripts
9. **Экспорт** во все форматы тестового отдела
