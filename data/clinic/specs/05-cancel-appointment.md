# Scenario 5: cancel a paid appointment

## Goal

Cancel a paid clinic appointment when the patient changes plans before the visit.

## Preconditions

- Requires an appointment created by scenario 1; the appointment identifier is used throughout cancellation.
- The appointment is still active for patient service.
- The patient can receive cancellation notifications through the saved contact details.

## Scenario steps

1. The tester opens the existing appointment with `GET /appointments/{appointmentId}`.
   The appointment card contains patient, branch, doctor, service, slot, payment, and current status.

2. The tester reviews the appointment selected for cancellation.
   The appointment is associated with a future clinic visit and has not been completed by the doctor.

3. The tester cancels the appointment with `POST /appointments/{appointmentId}/cancel`.
   The cancellation request includes a short reason from the patient.

4. The clinic system updates the appointment lifecycle.
   The appointment keeps its identifier and payment link, while the visit slot becomes available for future booking.

5. The tester opens the appointment again.
   The appointment card shows the cancellation state and keeps the original patient, doctor, service, slot, and payment references for audit history.

6. The tester searches appointment slots for the same service and branch.
   The cancelled visit time can appear again as an available option for another booking.
