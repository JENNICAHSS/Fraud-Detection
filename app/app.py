"""Flask API + dashboard for the Financial Transaction Monitoring and Fraud Detection System."""
import os
import sys
from decimal import Decimal
from datetime import date, datetime

from flask import Flask, jsonify, request, send_from_directory
from flask.json.provider import DefaultJSONProvider

sys.path.insert(0, os.path.dirname(__file__))
from db import get_conn                                          # noqa: E402
from engine import execute_transaction, review_alert, TxnError   # noqa: E402


class Provider(DefaultJSONProvider):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        return super().default(o)


app = Flask(__name__, static_folder="static", static_url_path="/static")
app.json = Provider(app)


def q(sql, params=(), one=False):
    """Run a read-only query and return dict rows."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone() if one else cur.fetchall()
    finally:
        conn.close()


@app.errorhandler(TxnError)
def txn_error(e):
    return jsonify(error=str(e)), 400


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ---------------------------------------------------------------- dashboard
@app.get("/api/summary")
def summary():
    row = q("""
        SELECT
          (SELECT coalesce(sum(amount),0) FROM transactions
             WHERE status IN ('COMPLETED','FLAGGED') AND created_at > now() - interval '30 days')  AS volume_30d,
          (SELECT count(*) FROM transactions WHERE created_at > now() - interval '30 days')         AS txn_30d,
          (SELECT count(*) FROM fraud_alerts WHERE status IN ('OPEN','UNDER_REVIEW'))               AS open_alerts,
          (SELECT count(*) FROM fraud_alerts WHERE status = 'OPEN' AND severity IN ('HIGH','CRITICAL')) AS urgent_alerts,
          (SELECT coalesce(sum(amount),0) FROM transactions WHERE status = 'BLOCKED')               AS blocked_amount,
          (SELECT count(*) FROM transactions WHERE status = 'BLOCKED')                              AS blocked_count,
          (SELECT count(*) FROM fraud_alerts WHERE status = 'CONFIRMED')                            AS confirmed,
          (SELECT count(*) FROM fraud_alerts WHERE status = 'DISMISSED')                            AS dismissed,
          (SELECT count(*) FROM accounts WHERE status = 'FROZEN')                                   AS frozen_accounts
    """, one=True)
    return jsonify(row)


@app.get("/api/trends")
def trends():
    days = min(int(request.args.get("days", 14)), 60)
    rows = q("""
        WITH d AS (SELECT generate_series(current_date - (%s - 1), current_date, '1 day')::date AS day)
        SELECT d.day,
               count(t.txn_id)                              AS txn_count,
               coalesce(sum(t.amount) FILTER (WHERE t.status IN ('COMPLETED','FLAGGED')), 0) AS volume,
               count(a.alert_id)                            AS alerts
        FROM d
        LEFT JOIN transactions t ON t.created_at::date = d.day
        LEFT JOIN fraud_alerts a ON a.txn_id = t.txn_id
        GROUP BY d.day ORDER BY d.day""", (days,))
    return jsonify(rows)


@app.get("/api/severity")
def severity():
    return jsonify(q("""SELECT severity, count(*) AS n FROM fraud_alerts GROUP BY severity"""))


@app.get("/api/top-accounts")
def top_accounts():
    """CTE + window function: rank accounts by cumulative alert risk."""
    return jsonify(q("""
        WITH acct_alerts AS (
            SELECT COALESCE(t.from_account_id, t.to_account_id) AS account_id,
                   a.risk_score, a.status
            FROM fraud_alerts a JOIN transactions t USING (txn_id)
        ), agg AS (
            SELECT account_id, count(*) AS alerts, sum(risk_score) AS total_risk, max(risk_score) AS peak
            FROM acct_alerts GROUP BY account_id
        )
        SELECT RANK() OVER (ORDER BY total_risk DESC) AS rank,
               ac.account_no, c.full_name, g.alerts, g.total_risk, g.peak, ac.status AS account_status
        FROM agg g JOIN accounts ac USING (account_id) JOIN customers c USING (customer_id)
        ORDER BY rank, ac.account_no LIMIT 5"""))


# ---------------------------------------------------------------- alerts
@app.get("/api/alerts")
def alerts():
    where, p = [], []
    if request.args.get("status"):
        where.append("status = %s"); p.append(request.args["status"])
    if request.args.get("severity"):
        where.append("severity = %s"); p.append(request.args["severity"])
    sql = "SELECT * FROM v_alert_details"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY (status IN ('OPEN','UNDER_REVIEW')) DESC, risk_score DESC, created_at DESC LIMIT %s"
    p.append(min(int(request.args.get("limit", 100)), 500))
    return jsonify(q(sql, p))


@app.get("/api/alerts/<int:alert_id>")
def alert_detail(alert_id):
    a = q("SELECT * FROM v_alert_details WHERE alert_id = %s", (alert_id,), one=True)
    if not a:
        return jsonify(error="Alert not found"), 404
    t = q("""SELECT t.*, rs.triggered_rules,
                    COALESCE(t.from_account_id, t.to_account_id) AS subject_id
             FROM transactions t LEFT JOIN risk_scores rs USING (txn_id) WHERE t.txn_id = %s""", (a["txn_id"],), one=True)
    history = q("""SELECT txn_id, txn_type, amount, status, country, channel, created_at
                   FROM transactions WHERE (from_account_id = %s OR to_account_id = %s) AND txn_id <> %s
                   ORDER BY created_at DESC LIMIT 8""", (t["subject_id"], t["subject_id"], t["txn_id"]))
    acct = q("""SELECT ac.account_no, ac.balance, ac.status, ac.opened_at, c.full_name, c.email, c.country, c.kyc_status
                FROM accounts ac JOIN customers c USING (customer_id) WHERE ac.account_id = %s""",
             (t["subject_id"],), one=True)
    inv = q("SELECT * FROM investigations WHERE alert_id = %s ORDER BY created_at DESC", (alert_id,))
    return jsonify(alert=a, txn=t, account=acct, history=history, investigations=inv,
                   reasons=a["reasons"].split(" | "))


@app.post("/api/alerts/<int:alert_id>/review")
def alert_review(alert_id):
    d = request.get_json(force=True)
    conn = get_conn()
    try:
        review_alert(conn, alert_id, d.get("decision"), d.get("notes", ""), d.get("analyst", "admin"))
    finally:
        conn.close()
    return jsonify(ok=True)


# ---------------------------------------------------------------- transactions
@app.get("/api/transactions")
def transactions():
    where, p = [], []
    s = request.args.get("q", "").strip()
    if s:
        where.append("""(t.txn_id::text = %s OR fa.account_no ILIKE %s OR ta.account_no ILIKE %s
                         OR fc.full_name ILIKE %s OR tc.full_name ILIKE %s)""")
        p += [s, f"%{s}%", f"%{s}%", f"%{s}%", f"%{s}%"]
    for col, arg in (("t.txn_type", "type"), ("t.status", "status")):
        if request.args.get(arg):
            where.append(f"{col} = %s"); p.append(request.args[arg])
    if request.args.get("min"):
        where.append("t.amount >= %s"); p.append(request.args["min"])
    if request.args.get("max"):
        where.append("t.amount <= %s"); p.append(request.args["max"])
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    base = """FROM transactions t
              LEFT JOIN accounts fa ON fa.account_id = t.from_account_id
              LEFT JOIN accounts ta ON ta.account_id = t.to_account_id
              LEFT JOIN customers fc ON fc.customer_id = fa.customer_id
              LEFT JOIN customers tc ON tc.customer_id = ta.customer_id
              LEFT JOIN risk_scores rs ON rs.txn_id = t.txn_id""" + wsql
    page = max(int(request.args.get("page", 1)), 1)
    size = 15
    total = q("SELECT count(*) AS n " + base, p, one=True)["n"]
    rows = q("""SELECT t.txn_id, t.txn_type, t.amount, t.channel, t.country, t.status, t.created_at,
                       fa.account_no AS from_account, ta.account_no AS to_account,
                       COALESCE(fc.full_name, tc.full_name) AS customer_name, rs.score
                """ + base + " ORDER BY t.created_at DESC, t.txn_id DESC LIMIT %s OFFSET %s", p + [size, (page - 1) * size])
    return jsonify(rows=rows, total=total, page=page, pages=max(1, -(-total // size)))


@app.post("/api/transactions")
def new_transaction():
    d = request.get_json(force=True)
    conn = get_conn()
    try:
        res = execute_transaction(conn, d.get("txn_type"), d.get("from_id") or None, d.get("to_id") or None,
                                  d.get("amount"), d.get("channel", "ONLINE"), (d.get("country") or "IN").upper()[:2])
    finally:
        conn.close()
    return jsonify(res), 201


@app.get("/api/accounts")
def accounts():
    return jsonify(q("""SELECT ac.account_id, ac.account_no, ac.balance, ac.status, c.full_name
                        FROM accounts ac JOIN customers c USING (customer_id) ORDER BY c.full_name, ac.account_no"""))


@app.post("/api/login-attempts")
def login_attempt():
    """Record a login attempt (the demo 'Simulate' panel uses this to feed the FAILED_LOGINS rule)."""
    d = request.get_json(force=True)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO login_attempts (customer_id, success, ip_address)
                           SELECT customer_id, %s, %s FROM accounts WHERE account_id = %s""",
                        (bool(d.get("success")), d.get("ip", "203.0.113.7"), d.get("account_id")))
        conn.commit()
    finally:
        conn.close()
    return jsonify(ok=True), 201


# ---------------------------------------------------------------- rules / audit
@app.get("/api/rules")
def rules():
    return jsonify(q("SELECT * FROM fraud_rules ORDER BY rule_id"))


@app.put("/api/rules/<int:rule_id>")
def update_rule(rule_id):
    d = request.get_json(force=True)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM fraud_rules WHERE rule_id = %s FOR UPDATE", (rule_id,))
            old = cur.fetchone()
            if not old:
                return jsonify(error="Rule not found"), 404
            weight = int(d.get("weight", old["weight"]))
            if not 1 <= weight <= 100:
                raise TxnError("Weight must be between 1 and 100")
            new = (Decimal(str(d.get("threshold", old["threshold"]))), int(d.get("window_mins", old["window_mins"])),
                   weight, bool(d.get("is_active", old["is_active"])))
            cur.execute("""UPDATE fraud_rules SET threshold=%s, window_mins=%s, weight=%s, is_active=%s
                           WHERE rule_id=%s""", (*new, rule_id))
            cur.execute("""INSERT INTO audit_logs (entity, entity_id, action, old_value, new_value)
                           VALUES ('fraud_rule', %s, 'RULE_UPDATE', %s, %s)""",
                        (rule_id,
                         f"{old['rule_code']}: thr={old['threshold']} win={old['window_mins']} w={old['weight']} on={old['is_active']}",
                         f"thr={new[0]} win={new[1]} w={new[2]} on={new[3]}"))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return jsonify(ok=True)


@app.get("/api/audit")
def audit():
    return jsonify(q("SELECT * FROM audit_logs ORDER BY logged_at DESC, log_id DESC LIMIT 100"))


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
