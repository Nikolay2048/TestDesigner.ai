# Scenario 5: invalid booking business rules

## Goal

Generate negative test cases around incorrect booking data and invalid state transitions.

## Preconditions

- Mock server can be reset with `POST /mock/reset`.
- Seed identifiers are available from `GET /mock/seed-ids`.
- Authentication token is available.

## Scenario steps

1. Try to reserve a diagnostics MRI service for a patient younger than 21.
2. Try to reserve a slot that belongs to another service.
3. Create a valid reservation and confirm it.
4. Repeat confirmation for the same reservation.
5. Try to pay with an amount different from `paymentRequired`.
6. Try to cancel a completed appointment.

## Business rules

- OpenAPI accepts patient age from 18, but server requires age 21 for MRI diagnostics.
- Doctor and slot must support the selected service.
- Reservation cannot be confirmed twice.
- Payment amount must match `paymentRequired`.
- Appointment status transitions are limited to:
  - `PAID -> RESCHEDULED`
  - `PAID -> CANCELLED`
  - `PAID -> COMPLETED`
  - `RESCHEDULED -> CANCELLED`
  - `RESCHEDULED -> COMPLETED`
- `COMPLETED -> CANCELLED` is impossible.

## Expected result

Each invalid operation returns a structured error:

```json
{
  "detail": {
    "code": "BUSINESS_ERROR_CODE",
    "message": "Human readable message",
    "hint": "Actionable hint for stabilization agent"
  }
}
```

## Negative conditions

- Underage MRI patient returns `422 AGE_RESTRICTED_SERVICE`.
- Incompatible slot returns `422 SLOT_SERVICE_MISMATCH`.
- Repeated confirmation returns `409 INVALID_RESERVATION_STATUS`.
- Wrong payment amount returns `409 PAYMENT_AMOUNT_MISMATCH`.
- Cancel completed appointment returns `409 INVALID_APPOINTMENT_STATUS`.

