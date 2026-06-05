# Scenario 2: booking with loyalty discount

## Goal

Book a family doctor consultation for a loyalty program patient and apply the clinic promotional discount during payment.

## Preconditions

- A tester account is available.
- The patient has a loyalty program number.
- The central branch has family doctor appointments.

## Scenario steps

1. The tester signs in to the clinic portal.
   The session is used for the booking flow.

2. The tester selects the central Moscow branch and reviews services available there.
   A family doctor consultation is selected for the loyalty patient.

3. The tester searches for a free slot for the family doctor consultation.
   The selected slot belongs to the central branch and has a doctor assigned.

4. The tester creates a reservation and adds the patient's loyalty program number.
   The booking form also contains the clinic promotional code prepared for the current campaign.
   The loyalty discount and promotional adjustment are associated with the same reservation.

5. The tester confirms the reservation.
   The confirmed reservation shows the amount to pay after the loyalty and promo adjustments.

6. The tester pays the adjusted amount through the configured payment provider.
   The payment is linked to the confirmed reservation.

7. The tester creates and opens the appointment.
   The appointment card shows a paid booking for the loyalty patient and displays the applied discount together with the final amount.
