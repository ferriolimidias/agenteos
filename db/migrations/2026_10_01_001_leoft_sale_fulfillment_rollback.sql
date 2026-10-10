BEGIN;

-- Execute only after disabling the fulfillment handler and exporting the
-- fulfillment/configuration tables. This intentionally removes only the
-- additive V1 contract, never students, accesses, programs or payments.
DROP TABLE IF EXISTS leoft_sale_fulfillments;
DROP TABLE IF EXISTS leoft_offer_fulfillments;

COMMIT;
