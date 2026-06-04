# Scenario 3: reschedule an existing appointment

## Goal

Move a paid appointment to another available slot for the same service.

## Preconditions

- Requires an appointment created by scenario 1.
- Required state: appointment status is `PAID` or `RESCHEDULED`.
- The replacement slot belongs to the same branch and supports the same service.

## Scenario steps

1. Read the existing appointment using `GET /appointments/{appointmentId}`.
2. Search for another available slot with the same `serviceId` and `branchId`.
3. Reschedule with `PATCH /appointments/{appointmentId}/reschedule`, passing the new `slotId`.
4. Save the new `slotId`, `doctorId`, `startsAt`, and `status`.
5. Read the appointment again and verify status `RESCHEDULED`.

## Business rules

- Appointment can be rescheduled only from `PAID` or `RESCHEDULED`.
- New slot must not be the same as the current slot.
- New slot must be available and compatible with the appointment service.
- Rescheduling is not allowed after appointment completion or cancellation.
- The payment remains attached to the appointment.

## Expected result

The appointment keeps the same `appointmentId` and `paymentId`, receives the new `slotId`, and has status `RESCHEDULED`.

## Negative conditions

- Unpaid appointment returns `409 APPOINTMENT_NOT_PAID`.
- Occupied slot returns `409 SLOT_NOT_AVAILABLE`.
- Slot for another service returns `422 SLOT_SERVICE_MISMATCH`.
- Completed appointment returns `409 INVALID_APPOINTMENT_STATUS`.

