# Scenario 1: complete paid appointment booking

## Goal

Register a patient for a general practitioner consultation in the central Moscow branch and complete the booking with payment.

## Preconditions

- A tester account is available.
- The central Moscow branch accepts outpatient appointments.
- The patient is an adult and has contact details for notifications.

## Scenario steps

1. The tester signs in to the clinic portal with `POST /auth/login`.
   The session received after sign-in is used while working with branches, reservations, payments, and appointments.

2. The tester opens the list of clinic branches for Moscow with `GET /branches?city=Moscow`.
   The central branch is selected by its branch code `MSK-CENTRAL`.

3. The tester reviews services available in the selected branch.
   A general practitioner consultation is chosen because it is suitable for a regular adult patient visit.

4. The tester searches for appointment slots for the selected branch and service.
   The chosen slot belongs to the same branch and service, has a doctor assigned, and is available for booking.

5. The tester creates a reservation for the selected slot.
   The reservation contains patient name, birth date, email, phone, selected branch, service, and slot information.

6. The tester confirms the reservation with `POST /reservations/{reservationId}/confirm`.
   After confirmation, the reservation contains payment details for the selected service and slot.

7. The tester pays for the confirmed reservation with `POST /payments`.
   Payment is made using the payment data returned during reservation confirmation.

8. The tester creates the appointment with `POST /appointments`.
   The created appointment connects the reservation, payment, patient, doctor, service, and slot.

9. The tester opens the created appointment with `GET /appointments/{appointmentId}`.
   The appointment card shows the paid booking and the identifiers needed by later appointment scenarios.
