-- =====================================================================
-- Financial Transaction Monitoring and Fraud Detection System (PostgreSQL)
-- Schema: 9 tables, constraints, indexes, triggers, views
-- =====================================================================

DROP VIEW  IF EXISTS v_alert_details CASCADE;
DROP VIEW  IF EXISTS v_daily_summary CASCADE;
DROP TABLE IF EXISTS investigations, risk_scores, fraud_alerts, audit_logs,
                     login_attempts, transactions, accounts, customers, fraud_rules CASCADE;
DROP FUNCTION IF EXISTS fn_audit_alert_change() CASCADE;
DROP FUNCTION IF EXISTS fn_audit_account_change() CASCADE;
DROP FUNCTION IF EXISTS fn_block_txn_delete() CASCADE;

-- 1. customers ---------------------------------------------------------
CREATE TABLE customers (
    customer_id   SERIAL PRIMARY KEY,
    full_name     VARCHAR(100) NOT NULL,
    email         VARCHAR(120) NOT NULL UNIQUE,
    phone         VARCHAR(25),
    country       VARCHAR(2)   NOT NULL DEFAULT 'IN',
    kyc_status    VARCHAR(10)  NOT NULL DEFAULT 'VERIFIED'
                  CHECK (kyc_status IN ('PENDING','VERIFIED','REJECTED')),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 2. accounts ----------------------------------------------------------
CREATE TABLE accounts (
    account_id    SERIAL PRIMARY KEY,
    customer_id   INT NOT NULL REFERENCES customers(customer_id) ON DELETE RESTRICT,
    account_no    VARCHAR(20) NOT NULL UNIQUE,
    account_type  VARCHAR(10) NOT NULL DEFAULT 'SAVINGS'
                  CHECK (account_type IN ('SAVINGS','CURRENT')),
    balance       NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (balance >= 0),   -- no overdraft
    status        VARCHAR(10) NOT NULL DEFAULT 'ACTIVE'
                  CHECK (status IN ('ACTIVE','FROZEN','CLOSED')),
    opened_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3. transactions ------------------------------------------------------
CREATE TABLE transactions (
    txn_id          BIGSERIAL PRIMARY KEY,
    txn_type        VARCHAR(10) NOT NULL CHECK (txn_type IN ('DEPOSIT','WITHDRAWAL','TRANSFER')),
    from_account_id INT REFERENCES accounts(account_id),
    to_account_id   INT REFERENCES accounts(account_id),
    amount          NUMERIC(14,2) NOT NULL CHECK (amount > 0),
    channel         VARCHAR(10) NOT NULL DEFAULT 'ONLINE'
                    CHECK (channel IN ('ONLINE','ATM','BRANCH','MOBILE')),
    country         VARCHAR(2) NOT NULL DEFAULT 'IN',
    status          VARCHAR(10) NOT NULL DEFAULT 'COMPLETED'
                    CHECK (status IN ('COMPLETED','FAILED','FLAGGED','BLOCKED')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- the shape of each type is enforced by the database, not the app
    CONSTRAINT chk_txn_shape CHECK (
        (txn_type = 'DEPOSIT'    AND to_account_id   IS NOT NULL AND from_account_id IS NULL) OR
        (txn_type = 'WITHDRAWAL' AND from_account_id IS NOT NULL AND to_account_id   IS NULL) OR
        (txn_type = 'TRANSFER'   AND from_account_id IS NOT NULL AND to_account_id   IS NOT NULL
                                 AND from_account_id <> to_account_id)
    )
);

-- 4. fraud_rules (configurable) ---------------------------------------
CREATE TABLE fraud_rules (
    rule_id      SERIAL PRIMARY KEY,
    rule_code    VARCHAR(30) NOT NULL UNIQUE,
    description  TEXT NOT NULL,
    threshold    NUMERIC(14,2) NOT NULL,       -- meaning depends on rule (amount, count, multiplier)
    window_mins  INT NOT NULL DEFAULT 0,       -- time window where relevant
    weight       INT NOT NULL CHECK (weight BETWEEN 1 AND 100),
    is_active    BOOLEAN NOT NULL DEFAULT TRUE
);

-- 5. fraud_alerts ------------------------------------------------------
CREATE TABLE fraud_alerts (
    alert_id     BIGSERIAL PRIMARY KEY,
    txn_id       BIGINT NOT NULL REFERENCES transactions(txn_id),
    risk_score   INT NOT NULL CHECK (risk_score BETWEEN 0 AND 100),
    severity     VARCHAR(8) NOT NULL CHECK (severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    reasons      TEXT NOT NULL,                -- explains WHY it was flagged
    status       VARCHAR(15) NOT NULL DEFAULT 'OPEN'
                 CHECK (status IN ('OPEN','UNDER_REVIEW','CONFIRMED','DISMISSED')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (txn_id)
);

-- 6. risk_scores (every transaction is assessed, flagged or not) -------
CREATE TABLE risk_scores (
    score_id        BIGSERIAL PRIMARY KEY,
    txn_id          BIGINT NOT NULL REFERENCES transactions(txn_id),
    score           INT NOT NULL CHECK (score BETWEEN 0 AND 100),
    triggered_rules TEXT[] NOT NULL DEFAULT '{}',
    assessed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (txn_id)
);

-- 7. investigations ----------------------------------------------------
CREATE TABLE investigations (
    investigation_id SERIAL PRIMARY KEY,
    alert_id         BIGINT NOT NULL REFERENCES fraud_alerts(alert_id),
    analyst          VARCHAR(60) NOT NULL,
    decision         VARCHAR(15) NOT NULL CHECK (decision IN ('UNDER_REVIEW','CONFIRMED','DISMISSED')),
    notes            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 8. audit_logs --------------------------------------------------------
CREATE TABLE audit_logs (
    log_id      BIGSERIAL PRIMARY KEY,
    entity      VARCHAR(30) NOT NULL,
    entity_id   BIGINT,
    action      VARCHAR(30) NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    actor       VARCHAR(60) NOT NULL DEFAULT current_user,
    logged_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 9. login_attempts ----------------------------------------------------
CREATE TABLE login_attempts (
    attempt_id   BIGSERIAL PRIMARY KEY,
    customer_id  INT NOT NULL REFERENCES customers(customer_id),
    success      BOOLEAN NOT NULL,
    ip_address   VARCHAR(45),
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- INDEXES (verify with EXPLAIN ANALYZE, see db/queries.sql)
-- ---------------------------------------------------------------------
CREATE INDEX idx_txn_from_time   ON transactions (from_account_id, created_at DESC);
CREATE INDEX idx_txn_to_time     ON transactions (to_account_id,   created_at DESC);
CREATE INDEX idx_txn_created     ON transactions (created_at DESC);
CREATE INDEX idx_txn_status      ON transactions (status);
CREATE INDEX idx_alert_status    ON fraud_alerts (status, created_at DESC);
CREATE INDEX idx_alert_open      ON fraud_alerts (created_at DESC) WHERE status IN ('OPEN','UNDER_REVIEW'); -- partial
CREATE INDEX idx_login_cust_time ON login_attempts (customer_id, attempted_at DESC);
CREATE INDEX idx_audit_time      ON audit_logs (logged_at DESC);
CREATE INDEX idx_accounts_cust   ON accounts (customer_id);

-- ---------------------------------------------------------------------
-- TRIGGERS
-- ---------------------------------------------------------------------
-- (a) audit every alert status change
CREATE FUNCTION fn_audit_alert_change() RETURNS trigger AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        INSERT INTO audit_logs(entity, entity_id, action, old_value, new_value)
        VALUES ('fraud_alert', NEW.alert_id, 'STATUS_CHANGE', OLD.status, NEW.status);
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_audit_alert AFTER UPDATE ON fraud_alerts
    FOR EACH ROW EXECUTE FUNCTION fn_audit_alert_change();

-- (b) audit account freeze / close
CREATE FUNCTION fn_audit_account_change() RETURNS trigger AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        INSERT INTO audit_logs(entity, entity_id, action, old_value, new_value)
        VALUES ('account', NEW.account_id, 'STATUS_CHANGE', OLD.status, NEW.status);
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_audit_account AFTER UPDATE ON accounts
    FOR EACH ROW EXECUTE FUNCTION fn_audit_account_change();

-- (c) the ledger is append-only: transactions can never be deleted
CREATE FUNCTION fn_block_txn_delete() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Transactions are immutable and cannot be deleted (txn_id=%)', OLD.txn_id;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_block_txn_delete BEFORE DELETE ON transactions
    FOR EACH ROW EXECUTE FUNCTION fn_block_txn_delete();

-- ---------------------------------------------------------------------
-- VIEWS
-- ---------------------------------------------------------------------
CREATE VIEW v_alert_details AS
SELECT a.alert_id, a.txn_id, a.risk_score, a.severity, a.reasons, a.status, a.created_at,
       t.txn_type, t.amount, t.channel, t.country AS txn_country, t.created_at AS txn_time,
       fa.account_no AS from_account, ta.account_no AS to_account,
       COALESCE(fc.full_name, tc.full_name) AS customer_name
FROM fraud_alerts a
JOIN transactions t        ON t.txn_id = a.txn_id
LEFT JOIN accounts fa      ON fa.account_id = t.from_account_id
LEFT JOIN accounts ta      ON ta.account_id = t.to_account_id
LEFT JOIN customers fc     ON fc.customer_id = fa.customer_id
LEFT JOIN customers tc     ON tc.customer_id = ta.customer_id;

CREATE VIEW v_daily_summary AS
SELECT date_trunc('day', t.created_at)::date AS day,
       count(*)                                  AS txn_count,
       sum(t.amount)                             AS volume,
       count(a.alert_id)                         AS alerts
FROM transactions t
LEFT JOIN fraud_alerts a ON a.txn_id = t.txn_id
GROUP BY 1 ORDER BY 1;
