# Scenario 3: reschedule an existing appointment

## Goal

Move an already paid appointment to another suitable time while keeping the original appointment and payment relationship.

## Preconditions

- Requires an appointment created by scenario 1.
- The appointment is still active for patient service.
- The same branch has another suitable slot for the appointment service.

## Scenario steps

1. The tester opens the existing appointment with `GET /appointments/{appointmentId}`.
   The appointment card contains the current service, branch, doctor, slot, payment, and appointment status.

2. The tester searches for another slot for the same service and branch.
   The replacement slot is different from the current slot and can be used for the same type of consultation.

3. The tester changes the appointment time with `PATCH /appointments/{appointmentId}/reschedule`.
   The request contains the replacement slot selected in the previous step.

4. The clinic system updates the appointment schedule.
   The appointment keeps its original appointment identifier and payment link, while the visit time and doctor assignment may change according to the selected slot.

5. The tester opens the appointment again.
   The appointment card shows the new slot information and remains available for later visit completion.
