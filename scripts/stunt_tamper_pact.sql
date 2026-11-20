-- Judge stunt: "Tamper with the database".  Paste into the SingleStore console while the demo is running.
-- (Under the hood is read-only and never runs this.)
--
-- 1) See which Pact and version is live (the highest version of your pact_id):
SELECT pact_id, version, status, created_at FROM pacts ORDER BY pact_id, version DESC;

-- 2) Raise the limit directly in the database (replace the two values with that pact_id and version):
UPDATE pacts
SET pact_text = REPLACE(pact_text, '"max_rebuy_price_usd":250', '"max_rebuy_price_usd":1000')
WHERE pact_id = 'PACT_ID' AND version = VERSION;

-- Expected: the next decision stops on R1 (the Pact's signature no longer verifies), and
-- Under the hood -> "Your Pact" shows a red X for that version.

-- 3) Put it back after the stunt:
UPDATE pacts
SET pact_text = REPLACE(pact_text, '"max_rebuy_price_usd":1000', '"max_rebuy_price_usd":250')
WHERE pact_id = 'PACT_ID' AND version = VERSION;
