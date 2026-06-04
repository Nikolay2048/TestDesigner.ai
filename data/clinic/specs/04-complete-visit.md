# Scenario 4: complete visit and issue medical record

## Goal

Finish a paid clinic visit and make the medical record available in the appointment history.

## Preconditions

- Requires a paid or rescheduled appointment from an earlier booking scenario.
- The appointment has an assigned doctor.
- The patient visit has not been cancelled.

## Scenario steps

1. The tester opens the appointment with `GET /appointments/{appointmentId}`.
   The appointment card contains patient, doctor, service, slot, payment, and current visit status.

2. The clinic employee completes the visit with `POST /appointments/{appointmentId}/complete`.
   Completion is performed for the appointment that the patient actually attended.

3. The clinic system creates a medical record for the completed visit.
   The record is connected to the appointment, patient, and doctor.

4. The tester opens the medical record for the appointment.
   The record contains a short diagnosis, doctor identifier, patient identifier, appointment identifier, and issue timestamp.

5. The tester returns to the appointment card.
   The appointment is shown as completed and remains linked to the created medical record.
