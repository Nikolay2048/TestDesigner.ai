# UC-009. ДТП с тремя участниками: один виновник и два пострадавших

## Описание
Регистрация ДТП с тремя участниками: один виновник (CULPRIT, 100% вины) и два пострадавших (VICTIM). Для каждого участника добавляется транспортное средство. Каждый из пострадавших подаёт отдельное страховое дело, по каждому проводится независимая экспертиза, создаётся и подтверждается выплата. Финальный шаг проверяет совокупный ущерб и количество участников.

## Предусловия
- Система ДТП доступна
- base_url настроен
- Переменные {{startDate}}, {{startDate}} доступны в контексте

## Переменные сценария

| Переменная      | Источник | Описание                                      |
|----------------|----------|-----------------------------------------------|
| accidentId      | Шаг 1    | ID записи о ДТП                               |
| participant1Id  | Шаг 2    | ID виновника                                  |
| participant2Id  | Шаг 3    | ID первого пострадавшего                      |
| participant3Id  | Шаг 4    | ID второго пострадавшего                      |
| accVehicle1Id   | Шаг 5    | accidentVehicleId ТС виновника                |
| accVehicle2Id   | Шаг 6    | accidentVehicleId ТС первого пострадавшего    |
| accVehicle3Id   | Шаг 7    | accidentVehicleId ТС второго пострадавшего    |
| claim2Id        | Шаг 8    | ID страхового дела первого пострадавшего      |
| claim3Id        | Шаг 9    | ID страхового дела второго пострадавшего      |
| assessment2Id   | Шаг 10   | ID экспертизы по делу claim2                  |
| assessment3Id   | Шаг 12   | ID экспертизы по делу claim3                  |
| payment2Id      | Шаг 14   | ID выплаты по делу claim2                     |
| payment3Id      | Шаг 16   | ID выплаты по делу claim3 (шаг создания)      |

## Шаги сценария

### Шаг 1 — HTTP POST, ожидаемый статус 201
Создать запись о ДТП. Передать: дату ({{startDate}}), адрес происшествия («пр. Мира, 15»), описание («Столкновение трёх транспортных средств на регулируемом перекрёстке»). Сохранить accidentId из поля `accidentId` ответа.

### Шаг 2 — HTTP POST, ожидаемый статус 201
Добавить первого участника — виновника. Передать: accidentId, имя («Козлов Дмитрий Сергеевич»), телефон, номер страхового полиса, role=CULPRIT, faultPercentage=100. Сохранить participant1Id из поля `participantId` ответа.

### Шаг 3 — HTTP POST, ожидаемый статус 201
Добавить второго участника — первого пострадавшего. Передать: accidentId, имя («Новикова Елена Александровна»), телефон, номер страхового полиса, role=VICTIM, faultPercentage=0. Сохранить participant2Id из поля `participantId` ответа.

### Шаг 4 — HTTP POST, ожидаемый статус 201
Добавить третьего участника — второго пострадавшего. Передать: accidentId, имя («Морозов Андрей Викторович»), телефон, номер страхового полиса, role=VICTIM, faultPercentage=0. Сохранить participant3Id из поля `participantId` ответа.

### Шаг 5 — HTTP POST, ожидаемый статус 201
Добавить транспортное средство виновника. Передать: accidentId, participant1Id, licensePlate («О777КК77»), brand («BMW»), model («X5»), damageAmount=25000. Сохранить accVehicle1Id из поля `accidentVehicleId` ответа.

### Шаг 6 — HTTP POST, ожидаемый статус 201
Добавить транспортное средство первого пострадавшего. Передать: accidentId, participant2Id, licensePlate («Е456НН77»), brand («Volkswagen»), model («Passat»), damageAmount=65000. Сохранить accVehicle2Id из поля `accidentVehicleId` ответа.

### Шаг 7 — HTTP POST, ожидаемый статус 201
Добавить транспортное средство второго пострадавшего. Передать: accidentId, participant3Id, licensePlate («Х321РР77»), brand («Kia»), model («Optima»), damageAmount=120000. Сохранить accVehicle3Id из поля `accidentVehicleId` ответа.

### Шаг 8 — HTTP POST, ожидаемый статус 201
Создать страховое дело от имени первого пострадавшего (participant2Id). Передать: accidentId, claimantParticipantId=participant2Id, claimedAmount=65000. Сохранить claim2Id из поля `claimId` ответа.

### Шаг 9 — HTTP POST, ожидаемый статус 201
Создать страховое дело от имени второго пострадавшего (participant3Id). Передать: accidentId, claimantParticipantId=participant3Id, claimedAmount=120000. Сохранить claim3Id из поля `claimId` ответа.

### Шаг 10 — HTTP POST, ожидаемый статус 201
Создать запрос на экспертную оценку ущерба по делу claim2. Передать: accidentId, claim2Id, expertName=«Власов Игорь Петрович», scheduledDate={{startDate}}. Сохранить assessment2Id из поля `assessmentId` ответа.

### Шаг 11 — HTTP PATCH, ожидаемый статус 200
Внести результаты экспертизы по делу claim2. Передать: accidentId, claim2Id, assessment2Id. Тело: assessedAmount=60000, report=«Повреждения кузовных элементов. Стоимость ремонта 60 000 руб.», status=COMPLETED. После успешного ответа claim2 автоматически переходит в статус UNDER_REVIEW с approvedAmount=60000.

### Шаг 12 — HTTP POST, ожидаемый статус 201
Создать запрос на экспертную оценку ущерба по делу claim3. Передать: accidentId, claim3Id, expertName=«Лебедев Сергей Юрьевич», scheduledDate={{startDate}}. Сохранить assessment3Id из поля `assessmentId` ответа.

### Шаг 13 — HTTP PATCH, ожидаемый статус 200
Внести результаты экспертизы по делу claim3. Передать: accidentId, claim3Id, assessment3Id. Тело: assessedAmount=115000, report=«Повреждения передней части автомобиля, деформация капота и крыла. Стоимость ремонта 115 000 руб.», status=COMPLETED. После успешного ответа claim3 автоматически переходит в статус UNDER_REVIEW с approvedAmount=115000.

### Шаг 14 — HTTP POST, ожидаемый статус 201
Создать выплату по делу claim2 (первый пострадавший). Передать: accidentId, claim2Id, amount=60000, recipientParticipantId=participant2Id. Сохранить payment2Id из поля `paymentId` ответа. Начальный статус — PENDING.

### Шаг 15 — HTTP POST, ожидаемый статус 200
Подтвердить выплату payment2. Передать: accidentId, claim2Id, payment2Id. Проверить: статус стал CONFIRMED, amount=60000.

### Шаг 16 — HTTP GET, ожидаемый статус 200
Получить сводную информацию о ДТП. Передать: accidentId. Проверить: participantsCount=3, totalDamageAmount > 0 (ожидается не менее 175000, т.е. сумма одобренных выплат по обоим делам).

## Ожидаемые результаты
- ДТП содержит трёх участников и три транспортных средства
- Каждый пострадавший подал отдельное страховое дело
- По каждому делу проведена экспертиза: claim2 — 60 000 руб., claim3 — 115 000 руб.
- Оба дела переведены в статус UNDER_REVIEW
- Выплата по claim2 подтверждена (CONFIRMED, 60 000 руб.)
- Итоговое поле totalDamageAmount отражает совокупный задокументированный ущерб, participantsCount=3
