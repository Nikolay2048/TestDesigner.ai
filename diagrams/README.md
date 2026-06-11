# Демонстрационные схемы TestDesignerAI

Комплект отражает фактическую архитектуру текущей реализации. Синим обозначены
решения LLM-агентов, зелёным - детерминированная логика Python, жёлтым -
внешнее исполнение, фиолетовым - сохраняемые результаты.

## 1. Контекст системы

Показывает входы, основные подсистемы и результаты одного запуска.

```mermaid
flowchart LR
    analyst["Системный аналитик / QA"]
    scenario["Неструктурированная<br/>постановка Markdown"]
    openapi["OpenAPI / Swagger<br/>paths, schemas, responses"]
    testdata["Тестовые константы<br/>YAML / JSON"]
    llm["LLM backend<br/>Ollama или OpenRouter"]
    api["Тестируемый REST API"]

    subgraph system["TestDesignerAI"]
        cli["CLI и конфигурация запуска"]
        orchestration["Agentic Orchestrator"]
        agents["Специализированные<br/>LLM-агенты"]
        engine["Python planning,<br/>validation и execution"]
        registry["Generator Registry<br/>Python + Postman JS"]
        telemetry["Artifact Store<br/>prompts, state, traces, logs"]
    end

    cards["Карточки сценария<br/>и тест-кейсы"]
    postman["Postman collections<br/>и environment"]
    stable["Stable package<br/>для зависимых сценариев"]
    audit["Диагностика и<br/>аудит действий агентов"]

    analyst --> scenario
    scenario --> cli
    openapi --> cli
    testdata --> cli
    cli --> orchestration
    orchestration <--> agents
    agents <--> llm
    orchestration <--> engine
    engine <--> registry
    engine <--> api
    orchestration --> telemetry
    agents --> telemetry
    engine --> telemetry
    orchestration --> cards
    orchestration --> postman
    orchestration --> stable
    telemetry --> audit

    classDef human fill:#F8FAFC,stroke:#475569,color:#0F172A,stroke-width:1.5px;
    classDef input fill:#F1F5F9,stroke:#64748B,color:#0F172A;
    classDef llm fill:#E8F0FE,stroke:#2563EB,color:#172554,stroke-width:2px;
    classDef code fill:#ECFDF5,stroke:#059669,color:#064E3B,stroke-width:2px;
    classDef runtime fill:#FFFBEB,stroke:#D97706,color:#78350F,stroke-width:2px;
    classDef output fill:#F5F3FF,stroke:#7C3AED,color:#3B0764,stroke-width:2px;

    class analyst human;
    class scenario,openapi,testdata input;
    class llm,agents llm;
    class cli,orchestration,engine,registry,telemetry code;
    class api runtime;
    class cards,postman,stable,audit output;
```

## 2. Полный агентный pipeline

Система использует LLM для семантических решений, но не позволяет модели
непосредственно управлять HTTP или безусловно изменять план.

```mermaid
flowchart TB
    start(["Запуск сценария"])
    load["Загрузка Markdown, OpenAPI,<br/>test data и external context"]

    subgraph understanding["Понимание сценария"]
        analyst["Documentation Analyst<br/>цель, шаги, правила, зависимости"]
        analyst_contract{"Pydantic validation"}
        mapper["Endpoint Mapper<br/>по одному вызову на business step<br/>все операции: method/path/summary/status"]
        mapping_guard["Проверка method/path,<br/>порядка и дублей"]
    end

    subgraph planning["Планирование данных"]
        graph["Data Dependency Graph<br/>request needs + response producers"]
        resolver["Dependency Resolver<br/>выбор candidate_id"]
        deterministic_sources["Static и external bindings"]
        generation["Generation Binding<br/>выбор генератора или источника"]
        fallback["Безопасные fallback bindings"]
        assembly["Сборка DataBindingPlan"]
        plan_guard{"Проверка каждого binding"}
    end

    subgraph runtime["Исполнение и обучение на ответах API"]
        execute["Flow Executor<br/>последовательные REST-запросы"]
        passed{"Все шаги 2xx?"}
        diagnose["Stabilization Diagnostician"]
        hint["Детерминированный<br/>server-hint parser"]
        fixer["Stabilization Fixer"]
        patch_guard{"Patch validator"}
        patch["Ограниченное изменение<br/>DataBindingPlan"]
    end

    subgraph delivery["Формирование результата"]
        testbasis["Python Test Basis<br/>поля, schemas, правила"]
        attacks["Test Designer<br/>business-rule attacks"]
        cases["Карточки тест-кейсов<br/>assertions + traceability"]
        tcrun["Опциональное HTTP-исполнение<br/>тест-кейсов"]
        export["Postman Exporter"]
        stable["Публикация stable package"]
    end

    finish(["Результат запуска"])
    stop(["Остановка с диагностикой"])

    start --> load --> analyst --> analyst_contract
    analyst_contract -->|валидно| mapper
    analyst_contract -->|ошибка| stop
    mapper --> mapping_guard
    mapping_guard -->|есть операции| graph
    mapping_guard -->|нет операций| stop
    graph --> resolver --> deterministic_sources --> generation --> fallback --> assembly --> plan_guard
    plan_guard -->|валидно| execute
    plan_guard -->|критическая ошибка| stop
    execute --> passed
    passed -->|да| testbasis
    passed -->|нет| diagnose
    diagnose --> hint
    hint -->|патч найден| patch_guard
    hint -->|патча нет| fixer
    fixer --> patch_guard
    patch_guard -->|разрешён| patch --> execute
    patch_guard -->|отклонён, есть try| fixer
    patch_guard -->|исчерпаны try| stop
    testbasis --> attacks --> cases
    cases -. при --run-test-cases .-> tcrun
    cases --> export
    cases --> stable
    tcrun --> finish
    export --> finish
    stable --> finish

    classDef llm fill:#E8F0FE,stroke:#2563EB,color:#172554,stroke-width:2px;
    classDef code fill:#ECFDF5,stroke:#059669,color:#064E3B,stroke-width:2px;
    classDef decision fill:#FFF7ED,stroke:#EA580C,color:#7C2D12,stroke-width:2px;
    classDef output fill:#F5F3FF,stroke:#7C3AED,color:#3B0764,stroke-width:2px;
    classDef terminal fill:#F8FAFC,stroke:#334155,color:#0F172A,stroke-width:2px;
    classDef failed fill:#FEF2F2,stroke:#DC2626,color:#7F1D1D,stroke-width:2px;

    class analyst,mapper,resolver,generation,diagnose,fixer,attacks llm;
    class load,mapping_guard,graph,deterministic_sources,fallback,assembly,execute,hint,patch,testbasis,tcrun,export,stable code;
    class analyst_contract,plan_guard,passed,patch_guard decision;
    class cases output;
    class start,finish terminal;
    class stop failed;
```

## 3. Цикл стабилизации

Это центральный агентный контур: сервер выступает источником наблюдений, а
Diagnostician и Fixer корректируют только проверяемую часть плана.

```mermaid
sequenceDiagram
    autonumber
    participant O as Orchestrator
    participant E as Flow Executor
    participant API as REST API
    participant D as Diagnostician LLM
    participant H as Server-hint rules
    participant F as Fixer LLM
    participant V as Patch validator
    participant S as Artifact Store

    loop attempt 1..max_attempts
        O->>E: execute current DataBindingPlan
        loop REST steps in order
            E->>API: HTTP request
            API-->>E: status + JSON body
            E->>E: extract scenario variables
        end
        E-->>O: ExecutorTrace
        O->>S: save trace and state

        alt all steps passed
            O->>O: freeze stable plan
            O->>S: publish success artifacts
        else first failed step
            O->>D: failed trace + plan context
            D-->>O: diagnosis + suspected bindings
            O->>S: save diagnosis prompt/output
            O->>H: parse explicit server hints

            alt deterministic patch available
                H-->>O: narrow BindingPatch
                O->>V: validate target, source and parameters
            else no deterministic patch
                loop fixer try 1..max_fixer_tries
                    O->>F: diagnosis + allowed targets + rejections
                    F-->>O: one proposed patch
                    O->>S: save fixer prompt/output
                    O->>V: validate patch
                    alt patch accepted
                        V-->>O: applied patch
                    else patch rejected
                        V-->>O: exact rejection reason
                    end
                end
            end

            alt patch applied
                O->>O: start next attempt from step 1
            else no valid patch
                O->>S: save review notes
                O->>O: stop as failed
            end
        end
    end
```

## 4. Происхождение и движение данных

Схема показывает, как значение попадает в запрос и затем становится переменной
для следующих шагов и Postman.

```mermaid
flowchart LR
    subgraph sources["Источники request values"]
        static["static<br/>test-data.yaml"]
        generated["generated<br/>Generator Registry"]
        response["response<br/>предыдущий JSON"]
        external["external_context<br/>stable dependency"]
        computed["computed<br/>безопасное выражение"]
        literal["literal<br/>подтверждённый patch"]
    end

    binding["RequestValueBinding<br/>target + location + source<br/>scope + provenance"]

    subgraph request["Сборка HTTP-запроса"]
        path["Path parameters"]
        query["Query parameters"]
        headers["Headers"]
        body["JSON body"]
    end

    api["REST API"]
    response_body["JSON response"]
    extraction["ResponseExtraction<br/>variable + JSONPath + scope"]
    variables["Runtime variables<br/>step / scenario"]

    next["Следующий REST-шаг"]
    state["state.json и trace"]
    postman["Postman collection variables<br/>pm.collectionVariables"]
    provided["provided_state.json<br/>для зависимых сценариев"]

    static --> binding
    generated --> binding
    response --> binding
    external --> binding
    computed --> binding
    literal --> binding

    binding --> path
    binding --> query
    binding --> headers
    binding --> body
    path --> api
    query --> api
    headers --> api
    body --> api
    api --> response_body --> extraction --> variables
    variables --> response
    variables --> next
    variables --> state
    variables --> postman
    variables --> provided

    classDef source fill:#F1F5F9,stroke:#64748B,color:#0F172A;
    classDef contract fill:#E8F0FE,stroke:#2563EB,color:#172554,stroke-width:2px;
    classDef code fill:#ECFDF5,stroke:#059669,color:#064E3B,stroke-width:2px;
    classDef runtime fill:#FFFBEB,stroke:#D97706,color:#78350F,stroke-width:2px;
    classDef output fill:#F5F3FF,stroke:#7C3AED,color:#3B0764,stroke-width:2px;

    class static,generated,response,external,computed,literal source;
    class binding,extraction contract;
    class path,query,headers,body,variables code;
    class api,response_body,next runtime;
    class state,postman,provided output;
```

## 5. Зависимые сценарии и stable packages

Предварительно стабилизированный сценарий может воспроизводимо подготовить
состояние для следующего сценария.

```mermaid
flowchart TB
    target["Целевой сценарий B"]
    preflight["Documentation Analyst preflight<br/>scenario_dependencies"]
    dependency{"Есть setup-зависимости?"}
    resolve["Разрешение ссылки<br/>на сценарий A"]
    package["Stable package сценария A"]
    validate{"Hashes и status валидны?"}
    checkpoint["Выбор setup checkpoint<br/>по required_data"]
    setup["Выполнение prefix stable plan"]
    setup_result{"Setup прошёл?"}
    context["External context<br/>ID и semantic aliases"]
    prune["Адаптация mapping B<br/>с учётом готового состояния"]
    orchestrator["Обычный pipeline сценария B"]
    combined["Dependency setup plan<br/>для Postman"]
    blocked["Blocked result<br/>с причиной"]

    package_files["metadata.json<br/>stable_plan.json<br/>provided_state.json<br/>dependency_setup_plan.json<br/>last_success_trace.json"]

    target --> preflight --> dependency
    dependency -->|нет| orchestrator
    dependency -->|да| resolve --> package
    package_files --> package
    package --> validate
    validate -->|нет| blocked
    validate -->|да| checkpoint --> setup --> setup_result
    setup_result -->|нет| blocked
    setup_result -->|да| context
    context --> prune --> orchestrator
    package --> combined
    combined --> orchestrator
    orchestrator -->|успех| package_files

    classDef llm fill:#E8F0FE,stroke:#2563EB,color:#172554,stroke-width:2px;
    classDef code fill:#ECFDF5,stroke:#059669,color:#064E3B,stroke-width:2px;
    classDef decision fill:#FFF7ED,stroke:#EA580C,color:#7C2D12,stroke-width:2px;
    classDef output fill:#F5F3FF,stroke:#7C3AED,color:#3B0764,stroke-width:2px;
    classDef failed fill:#FEF2F2,stroke:#DC2626,color:#7F1D1D,stroke-width:2px;

    class preflight llm;
    class resolve,checkpoint,setup,context,prune,orchestrator,combined code;
    class dependency,validate,setup_result decision;
    class target,package,package_files output;
    class blocked failed;
```

## 6. Формирование карточек и Postman

Один и тот же stable plan является основой как карточек тест-кейсов, так и
исполняемых Postman-артефактов.

```mermaid
flowchart TB
    stable["Stable happy-path plan"]
    trace["Последний успешный trace"]
    schemas["OpenAPI request/response schemas"]
    rules["Business rules и negative conditions"]

    basis["Test Basis<br/>поля, constraints, rules, risks"]
    deterministic["Детерминированные идеи<br/>required, bounds, boolean, lifecycle"]
    attacks["LLM business-rule attacks"]
    refine["LLM wording refinement"]
    cards["DesignedTestCase cards"]

    assertions["Assertions<br/>status, JSONPath exists/type,<br/>manual review"]
    execution["Опциональное выполнение<br/>каждой мутации"]

    exporter["Postman Exporter"]
    happy["Happy Path collection"]
    tests["Test Cases collection<br/>одна folder на кейс"]
    env["Environment<br/>baseUrl, static, external"]
    scripts["Pre-request scripts<br/>генерация переменных"]
    extracts["Post-response scripts<br/>extraction variables"]
    summary["Export summary<br/>unsupported + review"]

    stable --> basis
    trace --> basis
    schemas --> basis
    rules --> basis
    basis --> deterministic
    basis --> attacks
    deterministic --> cards
    attacks --> refine --> cards
    cards --> assertions
    cards -. --run-test-cases .-> execution

    stable --> exporter
    cards --> exporter
    assertions --> exporter
    exporter --> happy
    exporter --> tests
    exporter --> env
    exporter --> scripts
    exporter --> extracts
    exporter --> summary

    classDef llm fill:#E8F0FE,stroke:#2563EB,color:#172554,stroke-width:2px;
    classDef code fill:#ECFDF5,stroke:#059669,color:#064E3B,stroke-width:2px;
    classDef input fill:#F1F5F9,stroke:#64748B,color:#0F172A;
    classDef output fill:#F5F3FF,stroke:#7C3AED,color:#3B0764,stroke-width:2px;
    classDef runtime fill:#FFFBEB,stroke:#D97706,color:#78350F,stroke-width:2px;

    class attacks,refine llm;
    class basis,deterministic,assertions,exporter,scripts,extracts code;
    class stable,trace,schemas,rules input;
    class cards,happy,tests,env,summary output;
    class execution runtime;
```

## 7. Реальная трасса `demo_01`

Схема построена по запуску от 9 июня 2026 года. Она демонстрирует обнаружение
ошибок данных, применение двух патчей и успешное завершение.

```mermaid
flowchart LR
    input["Scenario 01<br/>Basic economy rental"]
    planning["Анализ и planning<br/>8 REST-шагов"]

    a1["Attempt 1"]
    e1["s02 POST /vehicles/search<br/>pickupDate = returnDate"]
    r1["400 INVALID_DATES"]
    d1["Diagnostician<br/>returnDate must be later"]
    f1["Fixer patch<br/>s02 returnDate: days 1 -> 2"]

    a2["Attempt 2"]
    e2["s07 POST /pickup<br/>fuelLevelPercent != 100"]
    r2["400 FUEL_LEVEL_NOT_FULL"]
    d2["Diagnostician + server hint"]
    f2["Deterministic patch<br/>fuelLevelPercent = 100"]

    a3["Attempt 3"]
    ok["8 из 8 REST-шагов<br/>успешно"]
    design["47 карточек тест-кейсов<br/>execution status: not_run"]
    postman["2 Postman collections<br/>225 test-case requests"]
    stable["Stable package published"]
    review["review_required = true<br/>патчи требуют проверки"]

    input --> planning --> a1 --> e1 --> r1 --> d1 --> f1
    f1 --> a2 --> e2 --> r2 --> d2 --> f2
    f2 --> a3 --> ok
    ok --> design
    ok --> postman
    ok --> stable
    f1 --> review
    f2 --> review

    classDef input fill:#F1F5F9,stroke:#64748B,color:#0F172A;
    classDef code fill:#ECFDF5,stroke:#059669,color:#064E3B,stroke-width:2px;
    classDef failed fill:#FEF2F2,stroke:#DC2626,color:#7F1D1D,stroke-width:2px;
    classDef llm fill:#E8F0FE,stroke:#2563EB,color:#172554,stroke-width:2px;
    classDef patch fill:#FFF7ED,stroke:#EA580C,color:#7C2D12,stroke-width:2px;
    classDef output fill:#F5F3FF,stroke:#7C3AED,color:#3B0764,stroke-width:2px;
    classDef warning fill:#FEFCE8,stroke:#CA8A04,color:#713F12,stroke-width:2px;

    class input input;
    class planning,a1,a2,a3,ok code;
    class e1,r1,e2,r2 failed;
    class d1,d2 llm;
    class f1,f2 patch;
    class design,postman,stable output;
    class review warning;
```

## Рекомендуемый порядок демонстрации

1. Контекст системы: какие входы получает система и что выдаёт.
2. Полный pipeline: где принимает решения LLM, а где работает Python.
3. Цикл стабилизации: как ответы сервера превращаются в корректировки.
4. Движение данных: генерация и извлечение переменных между запросами.
5. Stable dependencies: как сценарии подготавливают состояние друг для друга.
6. Карточки и Postman: два итоговых формата одного плана.
7. `demo_01`: реальный пример прохождения после двух исправлений.

