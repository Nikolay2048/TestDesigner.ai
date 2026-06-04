# Demo domain: online clinic booking

This repository contains input artifacts for demonstrating automatic REST API test design on a realistic, small business domain: online booking for a private clinic.

The demo covers how an agent can analyze natural-language system specs, map steps to OpenAPI endpoints, build REST API chains, reuse response variables, diagnose mismatches, stabilize happy paths, generate test designs, execute cases, and export Postman collections.

## Scenarios

1. `specs/01-main-happy-path.md` - create a complete clinic appointment from authentication to paid booking.
2. `specs/02-loyalty-discount.md` - book a consultation with a loyalty discount.
3. `specs/03-reschedule-appointment.md` - reschedule an existing paid appointment.
4. `specs/04-complete-visit.md` - complete a visit and issue a medical record.
5. `specs/05-cancel-appointment.md` - cancel an existing paid appointment before the visit.

## Scenario independence and dependencies

`01-main-happy-path` is fully independent after `POST /mock/reset`.

`02-loyalty-discount` is independent from scenario state but depends on stable tester constants from `test-data.yaml`.

`03-reschedule-appointment` requires a paid active appointment created by scenario 1.

`04-complete-visit` requires a paid or rescheduled appointment created by scenario 1 or 3.

`05-cancel-appointment` requires a paid active appointment created by scenario 1.

## Demonstrated system capabilities

Scenario 1 demonstrates endpoint mapping, response-variable extraction, status transitions, payment dependency handling, and a complete happy path.

Scenario 2 demonstrates generated test data, optional discount logic, error diagnosis for a changed loyalty code, and reuse of reservation/payment fields.

Scenario 3 demonstrates dependency on stabilized state, appointment mutation, slot replacement, and idempotency constraints.

Scenario 4 demonstrates state-dependent workflow completion and downstream artifact creation.

Scenario 5 demonstrates dependency on an existing paid appointment, cancellation workflow, slot release, and state verification after cancellation.

## Run the mock server

```bash
cd server
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8080
```

Useful helper endpoints:

- `POST /mock/reset` resets in-memory state.
- `GET /mock/state` returns current reservations, payments, appointments, and records.
- `GET /mock/seed-ids` returns stable branch, doctor, service, slot, and promo identifiers.

## Intentional discrepancies for diagnosis and stabilization

1. `specs/02-loyalty-discount.md` mentions the old loyalty code `CLINIC-OLD-10`; the mock server rejects it and returns a hint with the valid value `CLINIC-TEST-15`.
2. Some specs say "pay the appointment amount", while the server requires the `paymentRequired` value and `paymentToken` returned by reservation confirmation.
3. `specs/03-reschedule-appointment.md` keeps the paid-state dependency in natural language; the server returns a stabilization hint if the appointment is not in `PAID` or `RESCHEDULED` status.
4. `specs/04-complete-visit.md` describes issuing a record after the visit, while the server requires a prior explicit completion call before the record is available.
5. `specs/05-cancel-appointment.md` checks that cancellation releases the appointment slot for future booking.
