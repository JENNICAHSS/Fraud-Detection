-- Default fraud rules. Fully configurable from the dashboard (Rules page) or with plain UPDATEs.
-- threshold meaning depends on the rule; window_mins is the look-back window where relevant.
INSERT INTO fraud_rules (rule_code, description, threshold, window_mins, weight) VALUES
('LARGE_AMOUNT',      'Single transaction at or above the amount threshold (INR)',                       200000, 0,     35),
('HIGH_VELOCITY',     'Number of transactions on one account inside the window reaches the threshold',   5,      10,    35),
('FAILED_LOGINS',     'Failed login attempts by the account owner before the transaction',               3,      30,    20),
('FAILED_TXNS',       'Failed transaction attempts (e.g. insufficient funds) from the account',          2,      30,    15),
('AMOUNT_SPIKE',      'Outgoing amount is a multiple of the account''s 30-day outgoing average (5+ prior)', 10,   43200, 25),
('STRUCTURING',       'Repeated amounts just under the reporting limit (90-100% of limit) in 24 hours',  50000,  1440,  30),
('FOREIGN_COUNTRY',   'Transaction originates from a country other than the customer''s home country',   0,      0,     20),
('ODD_HOURS',         'Transaction between 00:00 and 05:00 India time',                                  0,      0,     10),
('NEW_ACCOUNT_LARGE', 'Large amount on an account opened recently (window = account age in minutes)',    100000, 10080, 25);
