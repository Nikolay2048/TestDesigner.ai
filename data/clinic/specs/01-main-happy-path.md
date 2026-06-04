# Scenario 1: complete paid appointment booking

## Goal

Create a real appointment for a patient in the central Moscow clinic branch, confirm the reservation, pay it, and receive a stable appointment identifier.

## Preconditions

- Mock server state is reset with `POST /mock/reset`.
- Tester credentials are available in `test-data.yaml`.
- The patient is at least 21 years old.
- The selected service has an available slot.

## Scenario steps

1. Authenticate the tester with `POST /auth/login` and save `accessToken`.
2. Load branches with `GET /branches?city=Moscow` and save `branchId` for branch code `MSK-CENTRAL`.
3. Load available services for the branch and choose a general practitioner consultation.
4. Search available slots for the selected `serviceId`, `branchId`, and preferred date.
5. Create a temporary reservation using the selected `slotId`, patient profile, and contact data.
6. Confirm the reservation with `POST /reservations/{reservationId}/confirm`; save `paymentRequired`, `paymentToken`, `amount`, and `status`.
7. Pay the appointment amount with `POST /payments`.
8. Create the final appointment with `POST /appointments` using the paid `paymentId` and confirmed `reservationId`.
9. Read the appointment with `GET /appointments/{appointmentId}` and verify status `PAID`.

## Business rules

- Required patient fields: `firstName`, `lastName`, `birthDate`, `email`, `phone`.
- `birthDate` must be a valid date and must represent an age of at least 18 according to OpenAPI.
- Server requires age at least 21 for diagnostics services.
- Reservation status transitions: `HELD -> CONFIRMED -> PAID`.
- A slot can be reserved only once while the reservation is active.
- Confirmation is not allowed for expired or already confirmed reservations.
- Payment must use `paymentRequired` and `paymentToken` returned by confirmation.

## Expected result

An appointment is created with status `PAID`; response includes `appointmentId`, `reservationId`, `paymentId`, `doctorId`, `slotId`, `amount`, and `status`.

## Negative conditions

- Missing `accessToken` returns `401`.
- Unknown `slotId` returns `404`.
- Already reserved slot returns `409`.
- Invalid email or birth date returns `422`.
- Payment with a wrong amount or token returns `409`.

