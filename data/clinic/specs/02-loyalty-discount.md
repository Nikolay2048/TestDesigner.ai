# Scenario 2: booking with loyalty discount

## Goal

Book a consultation with a loyalty program number and a fixed promo code so the final amount is discounted.

## Preconditions

- Mock server state is reset or no active reservation uses the selected slot.
- Tester is authenticated.
- Loyalty program number is stable: `LP-0004242`.
- The business spec still mentions old promo code `CLINIC-OLD-10`, but the active test value is returned by the server hint if validation fails.

## Scenario steps

1. Authenticate and save `accessToken`.
2. Find a family doctor consultation slot in the central branch.
3. Create a reservation with `loyaltyProgramNumber` and promo code `CLINIC-OLD-10`.
4. If the server rejects the promo code, stabilize the data using the hint and retry with the valid promo code.
5. Confirm the reservation and save `paymentRequired` and `paymentToken`.
6. Pay the discounted amount through the mock payment provider.
7. Create the appointment and verify the `discountAmount` is greater than zero.

## Business rules

- Promo code can be applied only once per reservation.
- Valid promo code gives a 15 percent discount.
- `loyaltyProgramNumber` must start with `LP-`.
- Discount cannot reduce the payable amount below 500.
- Reservation cannot be confirmed before a valid service, doctor, and slot are selected.

## Expected result

Appointment is created with status `PAID`; amount is lower than the base service price and response includes `discountAmount`.

## Negative conditions

- Old promo code returns `422 PROMO_CODE_EXPIRED` with a hint containing `CLINIC-TEST-15`.
- Reapplying the same promo code returns `409`.
- Loyalty number with an invalid format returns `422`.

