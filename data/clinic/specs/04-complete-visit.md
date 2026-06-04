# Scenario 4: complete visit and issue medical record

## Goal

Mark a paid appointment as completed and create a short medical record for the patient.

## Preconditions

- Requires a paid or rescheduled appointment from scenario 1 or 3.
- Required state: appointment status is `PAID` or `RESCHEDULED`.
- The doctor assigned to the appointment exists in the mock seed data.

## Scenario steps

1. Read appointment details and save `appointmentId`, `doctorId`, `patientId`, and `status`.
2. Complete the visit with `POST /appointments/{appointmentId}/complete`.
3. Save `recordId` returned by completion.
4. Read the medical record for the appointment.
5. Verify that the record contains diagnosis text, doctor identifier, and appointment identifier.

## Business rules

- Only paid or rescheduled appointments can be completed.
- Completion is idempotent only when the same appointment was already completed and record exists.
- Cancelled appointments cannot be completed.
- Medical record is available only after explicit completion.

## Expected result

Appointment status becomes `COMPLETED`; a medical record is returned with `recordId`, `appointmentId`, `doctorId`, `patientId`, `diagnosis`, and `issuedAt`.

## Negative conditions

- Reading a record before completion returns `404`.
- Completing a cancelled appointment returns `409`.
- Unknown appointment returns `404`.

