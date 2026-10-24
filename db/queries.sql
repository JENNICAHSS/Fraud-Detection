-- =====================================================================
-- Advanced SQL showcase: joins, CTEs, window functions, aggregations, EXPLAIN ANALYZE
-- Run:  psql fraud_monitor -f db/queries.sql
-- =====================================================================

-- 1. Rolling velocity detection with a WINDOW FUNCTION:
--    for every outgoing txn, how many txns did the same account make in the previous 10 minutes?
SELECT * FROM (
    SELECT txn_id, from_account_id, amount, created_at,
           count(*) OVER (PARTITION BY from_account_id
                          ORDER BY created_at
                          RANGE BETWEEN INTERVAL '10 minutes' PRECEDING AND CURRENT ROW) AS txns_last_10min
    FROM transactions
    WHERE from_account_id IS NOT NULL AND status <> 'FAILED'
) w
WHERE txns_last_10min >= 5
ORDER BY created_at;

-- 2. Amount versus each account's own history (CTE + window average)
WITH hist AS (
    SELECT txn_id, from_account_id, amount, created_at,
           avg(amount) OVER (PARTITION BY from_account_id ORDER BY created_at
                             ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior_avg
    FROM transactions WHERE from_account_id IS NOT NULL AND status IN ('COMPLETED','FLAGGED')
)
SELECT txn_id, from_account_id, amount, round(prior_avg, 2) AS prior_avg,
       round(amount / prior_avg, 1) AS times_avg
FROM hist
WHERE prior_avg IS NOT NULL AND amount > 10 * prior_avg
ORDER BY times_avg DESC;

-- 3. Customers ranked by total risk (JOINs + GROUP BY + RANK)
SELECT RANK() OVER (ORDER BY sum(a.risk_score) DESC) AS rnk,
       c.full_name, count(*) AS alerts, sum(a.risk_score) AS total_risk, max(a.severity) AS worst
FROM fraud_alerts a
JOIN transactions t ON t.txn_id = a.txn_id
JOIN accounts ac    ON ac.account_id = COALESCE(t.from_account_id, t.to_account_id)
JOIN customers c    ON c.customer_id = ac.customer_id
GROUP BY c.customer_id, c.full_name
ORDER BY rnk;

-- 4. Rule effectiveness: how often each rule fired and how analysts judged those alerts
SELECT rule, count(*) AS fired,
       count(*) FILTER (WHERE fa.status = 'CONFIRMED') AS confirmed,
       count(*) FILTER (WHERE fa.status = 'DISMISSED') AS dismissed
FROM risk_scores rs
CROSS JOIN LATERAL unnest(rs.triggered_rules) AS rule
LEFT JOIN fraud_alerts fa ON fa.txn_id = rs.txn_id
GROUP BY rule ORDER BY fired DESC;

-- 5. Daily trend with a 7-day moving average of alerts (VIEW + window)
SELECT day, txn_count, alerts,
       round(avg(alerts) OVER (ORDER BY day ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 2) AS alerts_7d_avg
FROM v_daily_summary;

-- 6. Subquery: accounts that received money from an account involved in a CRITICAL alert
SELECT DISTINCT ta.account_no AS recipient
FROM transactions t
JOIN accounts ta ON ta.account_id = t.to_account_id
WHERE t.from_account_id IN (
    SELECT COALESCE(t2.from_account_id, t2.to_account_id)
    FROM fraud_alerts a JOIN transactions t2 USING (txn_id)
    WHERE a.severity IN ('HIGH','CRITICAL'));

-- 7. Failed-login bursts per customer (HAVING)
SELECT customer_id, date_trunc('hour', attempted_at) AS hr, count(*) AS failures
FROM login_attempts WHERE NOT success
GROUP BY 1, 2 HAVING count(*) >= 3;

-- ---------------------------------------------------------------------
-- INDEX PROOF: compare plans with and without the index
-- ---------------------------------------------------------------------
-- With idx_txn_from_time: expect an Index/Bitmap scan on transactions
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM transactions
WHERE from_account_id = 4 AND created_at > now() - interval '30 days'
ORDER BY created_at DESC;

-- Drop the index, run again: expect a sequential scan, then recreate it
-- (on a tiny demo table Postgres may still prefer seq scan; generate more rows to see the gap:
--  INSERT INTO transactions(...) SELECT ... FROM generate_series(1, 200000);)
-- DROP INDEX idx_txn_from_time;
-- EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM transactions WHERE from_account_id = 4 AND created_at > now() - interval '30 days' ORDER BY created_at DESC;
-- CREATE INDEX idx_txn_from_time ON transactions (from_account_id, created_at DESC);

-- Partial index used by the "needs review" queue
EXPLAIN (ANALYZE)
SELECT * FROM fraud_alerts WHERE status IN ('OPEN','UNDER_REVIEW') ORDER BY created_at DESC;
