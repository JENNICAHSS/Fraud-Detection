"""
Fraud detection engine + transaction processing.

Design notes
------------
* Rules live in the fraud_rules table (threshold / window / weight / on-off), so they are
  configurable without code changes.
* Every rule that fires adds its weight; the score is capped at 100 and every fired rule
  contributes a human-readable reason, so each alert is explainable.
* execute_transaction() is ONE database transaction (ACID):
    - both accounts are locked with SELECT ... FOR UPDATE, always in account_id order
      (prevents lost updates, double-spend and deadlocks),
    - the ledger row, the balance changes, the risk score and the alert are committed
      together or not at all.
* Alerts are *potential* fraud for a human analyst to review, never proof.
"""
from datetime import datetime, timezone
from decimal import Decimal

ALERT_THRESHOLD = 30   # score >= 30 -> alert raised, txn marked FLAGGED
BLOCK_THRESHOLD = 90   # score >= 90 -> txn BLOCKED, funds are NOT moved


class TxnError(Exception):
    """Business-rule rejection (bad account, frozen account, invalid input)."""


def severity_for(score):
    if score >= 90:
        return "CRITICAL"
    if score >= 70:
        return "HIGH"
    if score >= 50:
        return "MEDIUM"
    return "LOW"


def _inr(x):
    return f"₹{Decimal(x):,.0f}"


# ---------------------------------------------------------------------------
# Rule evaluation
# ---------------------------------------------------------------------------
def score_transaction(cur, txn_id):
    """Evaluate all active rules for a transaction already inserted in this DB transaction.
    Returns (score, [(rule_code, weight, reason), ...])."""
    cur.execute("SELECT *, COALESCE(from_account_id, to_account_id) AS subject_id "
                "FROM transactions WHERE txn_id = %s", (txn_id,))
    t = cur.fetchone()
    cur.execute("""SELECT a.account_id, a.opened_at, c.customer_id, c.country AS home_country
                   FROM accounts a JOIN customers c USING (customer_id)
                   WHERE a.account_id = %s""", (t["subject_id"],))
    subj = cur.fetchone()
    cur.execute("SELECT * FROM fraud_rules WHERE is_active")
    rules = {r["rule_code"]: r for r in cur.fetchall()}

    hits = []
    amount, at, sid = t["amount"], t["created_at"], t["subject_id"]

    def hit(code, reason):
        hits.append((code, rules[code]["weight"], reason))

    # 1. Unusually large amount
    r = rules.get("LARGE_AMOUNT")
    if r and amount >= r["threshold"]:
        hit("LARGE_AMOUNT", f"Amount {_inr(amount)} is at/above the {_inr(r['threshold'])} limit")

    # 2. Many transactions in a short window (velocity)
    r = rules.get("HIGH_VELOCITY")
    if r:
        cur.execute("""SELECT count(*) AS n FROM transactions
                       WHERE (from_account_id = %s OR to_account_id = %s)
                         AND created_at <= %s AND created_at > %s - make_interval(mins => %s)""",
                    (sid, sid, at, at, r["window_mins"]))
        n = cur.fetchone()["n"]
        if n >= r["threshold"]:
            hit("HIGH_VELOCITY", f"{n} transactions on this account within {r['window_mins']} minutes")

    # 3. Failed logins before the transaction
    r = rules.get("FAILED_LOGINS")
    if r:
        cur.execute("""SELECT count(*) AS n FROM login_attempts
                       WHERE customer_id = %s AND NOT success
                         AND attempted_at <= %s AND attempted_at > %s - make_interval(mins => %s)""",
                    (subj["customer_id"], at, at, r["window_mins"]))
        n = cur.fetchone()["n"]
        if n >= r["threshold"]:
            hit("FAILED_LOGINS", f"{n} failed logins in the {r['window_mins']} minutes before this transaction")

    # 4. Failed transaction attempts (e.g. probing with amounts the balance cannot cover)
    r = rules.get("FAILED_TXNS")
    if r:
        cur.execute("""SELECT count(*) AS n FROM transactions
                       WHERE from_account_id = %s AND status = 'FAILED'
                         AND created_at <= %s AND created_at > %s - make_interval(mins => %s)""",
                    (sid, at, at, r["window_mins"]))
        n = cur.fetchone()["n"]
        if n >= r["threshold"]:
            hit("FAILED_TXNS", f"{n} failed transaction attempts in the last {r['window_mins']} minutes")

    # 5. Spike versus the account's own outgoing history (incoming salary etc. is not "spending")
    r = rules.get("AMOUNT_SPIKE")
    if r and t["from_account_id"]:
        cur.execute("""SELECT count(*) AS n, avg(amount) AS avg_amt FROM transactions
                       WHERE from_account_id = %s
                         AND txn_id <> %s AND status IN ('COMPLETED','FLAGGED')
                         AND created_at < %s AND created_at > %s - make_interval(mins => %s)""",
                    (sid, txn_id, at, at, r["window_mins"]))
        h = cur.fetchone()
        if h["n"] >= 5 and h["avg_amt"] and amount > r["threshold"] * h["avg_amt"]:
            hit("AMOUNT_SPIKE", f"Amount is {amount / h['avg_amt']:.1f}x this account's average outgoing of {_inr(h['avg_amt'])}")

    # 6. Structuring: repeated amounts just under the reporting limit
    r = rules.get("STRUCTURING")
    if r and t["from_account_id"]:
        lo, hi = r["threshold"] * Decimal("0.9"), r["threshold"]
        if lo <= amount < hi:
            cur.execute("""SELECT count(*) AS n FROM transactions
                           WHERE from_account_id = %s AND status <> 'FAILED'
                             AND amount >= %s AND amount < %s
                             AND created_at <= %s AND created_at > %s - make_interval(mins => %s)""",
                        (sid, lo, hi, at, at, r["window_mins"]))
            n = cur.fetchone()["n"]
            if n >= 2:
                hit("STRUCTURING", f"{n} amounts just under the {_inr(r['threshold'])} limit within 24 hours")

    # 7. Unusual country
    r = rules.get("FOREIGN_COUNTRY")
    if r and t["country"] != subj["home_country"]:
        hit("FOREIGN_COUNTRY", f"Originated from {t['country']}, customer's home country is {subj['home_country']}")

    # 8. Odd hours (India time)
    r = rules.get("ODD_HOURS")
    if r:
        cur.execute("SELECT extract(hour FROM %s AT TIME ZONE 'Asia/Kolkata') AS h", (at,))
        h = int(cur.fetchone()["h"])
        if h < 5:
            hit("ODD_HOURS", f"Transaction at {h:02d}:xx India time")

    # 9. Large amount on a brand-new account
    r = rules.get("NEW_ACCOUNT_LARGE")
    if r and amount >= r["threshold"]:
        age_mins = (at - subj["opened_at"]).total_seconds() / 60
        if age_mins < r["window_mins"]:
            hit("NEW_ACCOUNT_LARGE", f"{_inr(amount)} on an account only {age_mins / 1440:.1f} days old")

    score = min(100, sum(w for _, w, _ in hits))
    return score, hits


# ---------------------------------------------------------------------------
# Transaction processing (ACID + row locking)
# ---------------------------------------------------------------------------
def execute_transaction(conn, txn_type, from_id, to_id, amount, channel="ONLINE",
                        country="IN", created_at=None):
    """Create a transaction, move money, score it, raise an alert if needed.
    Everything happens in a single database transaction."""
    try:
        amount = Decimal(str(amount))
    except Exception:
        raise TxnError("Invalid amount")
    if amount <= 0:
        raise TxnError("Amount must be greater than zero")
    if txn_type not in ("DEPOSIT", "WITHDRAWAL", "TRANSFER"):
        raise TxnError("Unknown transaction type")
    if txn_type == "DEPOSIT":
        from_id = None
    elif txn_type == "WITHDRAWAL":
        to_id = None
    if txn_type != "DEPOSIT" and not from_id:
        raise TxnError("Source account required")
    if txn_type != "WITHDRAWAL" and not to_id:
        raise TxnError("Destination account required")
    if txn_type == "TRANSFER" and from_id == to_id:
        raise TxnError("Cannot transfer to the same account")
    created_at = created_at or datetime.now(timezone.utc)

    try:
        with conn.cursor() as cur:
            ids = sorted(i for i in (from_id, to_id) if i)
            # Concurrency control: lock rows in a fixed order -> no deadlocks, no double-spend
            cur.execute("""SELECT account_id, balance, status FROM accounts
                           WHERE account_id = ANY(%s) ORDER BY account_id FOR UPDATE""", (ids,))
            accts = {r["account_id"]: r for r in cur.fetchall()}
            for i in ids:
                if i not in accts:
                    raise TxnError(f"Account {i} does not exist")
                if accts[i]["status"] != "ACTIVE":
                    raise TxnError(f"Account {i} is {accts[i]['status'].lower()}")

            # Insufficient funds -> recorded as FAILED (feeds the FAILED_TXNS rule)
            if from_id and accts[from_id]["balance"] < amount:
                cur.execute("""INSERT INTO transactions
                               (txn_type, from_account_id, to_account_id, amount, channel, country, status, created_at)
                               VALUES (%s,%s,%s,%s,%s,%s,'FAILED',%s) RETURNING txn_id""",
                            (txn_type, from_id, to_id, amount, channel, country, created_at))
                tid = cur.fetchone()["txn_id"]
                conn.commit()
                return {"txn_id": tid, "status": "FAILED", "score": 0, "severity": None,
                        "reasons": ["Insufficient funds"], "alert_id": None}

            cur.execute("""INSERT INTO transactions
                           (txn_type, from_account_id, to_account_id, amount, channel, country, status, created_at)
                           VALUES (%s,%s,%s,%s,%s,%s,'COMPLETED',%s) RETURNING txn_id""",
                        (txn_type, from_id, to_id, amount, channel, country, created_at))
            tid = cur.fetchone()["txn_id"]

            score, hits = score_transaction(cur, tid)
            status = "COMPLETED"
            if score >= BLOCK_THRESHOLD:
                status = "BLOCKED"
            elif score >= ALERT_THRESHOLD:
                status = "FLAGGED"

            if status != "BLOCKED":   # blocked transactions never move funds
                if from_id:
                    cur.execute("UPDATE accounts SET balance = balance - %s WHERE account_id = %s", (amount, from_id))
                if to_id:
                    cur.execute("UPDATE accounts SET balance = balance + %s WHERE account_id = %s", (amount, to_id))
            if status != "COMPLETED":
                cur.execute("UPDATE transactions SET status = %s WHERE txn_id = %s", (status, tid))

            cur.execute("""INSERT INTO risk_scores (txn_id, score, triggered_rules, assessed_at)
                           VALUES (%s,%s,%s,%s)""", (tid, score, [h[0] for h in hits], created_at))
            alert_id, sev = None, None
            if score >= ALERT_THRESHOLD:
                sev = severity_for(score)
                cur.execute("""INSERT INTO fraud_alerts (txn_id, risk_score, severity, reasons, created_at)
                               VALUES (%s,%s,%s,%s,%s) RETURNING alert_id""",
                            (tid, score, sev, " | ".join(h[2] for h in hits), created_at))
                alert_id = cur.fetchone()["alert_id"]
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"txn_id": tid, "status": status, "score": score, "severity": sev,
            "reasons": [h[2] for h in hits], "alert_id": alert_id}


def review_alert(conn, alert_id, decision, notes="", analyst="admin"):
    """Analyst decision on an alert. CONFIRMED freezes the originating account.
    Status changes are written to audit_logs by database triggers."""
    if decision not in ("UNDER_REVIEW", "CONFIRMED", "DISMISSED"):
        raise TxnError("Invalid decision")
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT a.alert_id, a.status, COALESCE(t.from_account_id, t.to_account_id) AS acct
                           FROM fraud_alerts a JOIN transactions t USING (txn_id)
                           WHERE a.alert_id = %s FOR UPDATE OF a""", (alert_id,))
            a = cur.fetchone()
            if not a:
                raise TxnError("Alert not found")
            cur.execute("UPDATE fraud_alerts SET status = %s WHERE alert_id = %s", (decision, alert_id))
            cur.execute("""INSERT INTO investigations (alert_id, analyst, decision, notes)
                           VALUES (%s,%s,%s,%s)""", (alert_id, analyst, decision, notes or None))
            if decision == "CONFIRMED":
                cur.execute("UPDATE accounts SET status = 'FROZEN' WHERE account_id = %s AND status = 'ACTIVE'",
                            (a["acct"],))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
