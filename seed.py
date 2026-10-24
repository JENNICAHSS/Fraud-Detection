"""
Creates the schema, loads the default rules and generates ~30 days of realistic demo data
with planted fraud scenarios.  Every transaction goes through the real engine, so the alerts
you see in the dashboard are produced by the same rules a live transaction would hit.

    python seed.py
"""
import os
import random
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))
from db import get_conn                       # noqa: E402
from engine import execute_transaction, review_alert   # noqa: E402

random.seed(42)
ROOT = os.path.dirname(os.path.abspath(__file__))
IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime.now(timezone.utc)

FIRST = ["Aarav", "Vihaan", "Aditya", "Arjun", "Ishaan", "Rohan", "Karthik", "Suresh", "Naveen", "Vikram",
         "Ananya", "Diya", "Meera", "Priya", "Kavya", "Sneha", "Lakshmi", "Divya", "Pooja", "Nisha",
         "Rahul", "Manoj", "Deepak", "Ramesh", "Harish", "Sanjay", "Anita", "Revathi", "Swathi", "Geetha"]
LAST = ["Kumar", "Sharma", "Iyer", "Nair", "Reddy", "Patel", "Menon", "Pillai", "Das", "Singh",
        "Gupta", "Rao", "Joshi", "Varma", "Krishnan"]


def run_sql_file(cur, name):
    with open(os.path.join(ROOT, "db", name), encoding="utf-8") as f:
        cur.execute(f.read())


def at_ist(days_ago, hour, minute=0):
    """A timestamp roughly `days_ago` whole days back at hh:mm India time (never in the future)."""
    d = (NOW.astimezone(IST) - timedelta(days=int(days_ago))).replace(hour=hour, minute=minute, second=random.randint(0, 59))
    if d > NOW.astimezone(IST):
        d -= timedelta(days=1)
    return d.astimezone(timezone.utc)


def main():
    conn = get_conn()
    with conn.cursor() as cur:
        run_sql_file(cur, "schema.sql")
        run_sql_file(cur, "rules.sql")
    conn.commit()

    # ---- customers & accounts -------------------------------------------------------
    names = set()
    while len(names) < 36:
        names.add(f"{random.choice(FIRST)} {random.choice(LAST)}")
    names = sorted(names)
    customers, accounts = [], []          # accounts: dict(id, customer_id, balance)
    with conn.cursor() as cur:
        for i, n in enumerate(names, 1):
            cur.execute("""INSERT INTO customers (full_name, email, phone, country, kyc_status, created_at)
                           VALUES (%s,%s,%s,'IN','VERIFIED',%s) RETURNING customer_id""",
                        (n, f"{n.lower().replace(' ', '.')}{i}@mail.example", f"+9198{random.randint(10000000, 99999999)}",
                         NOW - timedelta(days=random.randint(120, 900))))
            cid = cur.fetchone()["customer_id"]
            customers.append(cid)
            for k in range(random.choice([1, 1, 2])):
                cur.execute("""INSERT INTO accounts (customer_id, account_no, account_type, balance, opened_at)
                               VALUES (%s,%s,%s,%s,%s) RETURNING account_id""",
                            (cid, f"AC{1000000 + len(accounts) + 1}", random.choice(["SAVINGS", "SAVINGS", "CURRENT"]),
                             random.randint(40000, 400000), NOW - timedelta(days=random.randint(100, 800))))
                accounts.append({"id": cur.fetchone()["account_id"], "cid": cid})
    conn.commit()
    ids = [a["id"] for a in accounts]

    # Scenario actors (taken from the generated customers)
    victim_acc, burst_acc, struct_acc, probe_acc, spike_acc = ids[0], ids[3], ids[6], ids[9], ids[12]
    cust_of = {a["id"]: a["cid"] for a in accounts}

    with conn.cursor() as cur:
        cur.execute("UPDATE accounts SET balance = 950000 WHERE account_id = %s", (victim_acc,))
        cur.execute("UPDATE accounts SET balance = 1200 WHERE account_id = %s", (probe_acc,))
        cur.execute("UPDATE accounts SET balance = 300000 WHERE account_id = %s", (struct_acc,))
        # Brand-new customer + account for the "mule account" scenario
        cur.execute("""INSERT INTO customers (full_name, email, country, kyc_status, created_at)
                       VALUES ('Rakesh Bose','rakesh.bose@mail.example','IN','PENDING',%s) RETURNING customer_id""",
                    (NOW - timedelta(days=2),))
        mule_cid = cur.fetchone()["customer_id"]
        cur.execute("""INSERT INTO accounts (customer_id, account_no, account_type, balance, opened_at)
                       VALUES (%s,'AC1999001','SAVINGS',0,%s) RETURNING account_id""",
                    (mule_cid, NOW - timedelta(days=2)))
        mule_acc = cur.fetchone()["account_id"]
    conn.commit()

    events = []   # (timestamp, kind, payload)

    # ---- normal behaviour ------------------------------------------------------------
    for acc in ids:
        for _ in range(random.randint(9, 15)):
            ts = at_ist(random.randint(0, 30), random.randint(9, 21), random.randint(0, 59))
            kind = random.choices(["DEPOSIT", "WITHDRAWAL", "TRANSFER"], [3, 4, 5])[0]
            if kind == "DEPOSIT":
                p = dict(txn_type="DEPOSIT", from_id=None, to_id=acc, amount=random.choice([5000, 12000, 25000, 40000, 65000]),
                         channel=random.choice(["BRANCH", "ONLINE"]))
            elif kind == "WITHDRAWAL":
                p = dict(txn_type="WITHDRAWAL", from_id=acc, to_id=None, amount=random.choice([500, 1000, 2000, 3500, 5000, 8000]),
                         channel=random.choice(["ATM", "ATM", "BRANCH"]))
            else:
                other = random.choice([x for x in ids if x != acc])
                p = dict(txn_type="TRANSFER", from_id=acc, to_id=other, amount=random.choice([800, 1500, 3000, 6000, 12000, 20000]),
                         channel=random.choice(["MOBILE", "ONLINE"]))
            events.append((ts, "txn", p))
    # a few harmless late-night transactions (ODD_HOURS alone should NOT raise an alert)
    for _ in range(6):
        acc = random.choice(ids[15:])
        events.append((at_ist(random.randint(1, 25), random.choice([1, 2, 3]), random.randint(0, 59)), "txn",
                       dict(txn_type="WITHDRAWAL", from_id=acc, to_id=None, amount=2000, channel="ATM")))
    # normal login history
    for cid in customers:
        for _ in range(random.randint(3, 8)):
            events.append((at_ist(random.randint(0, 30), random.randint(8, 22), random.randint(0, 59)), "login",
                           dict(customer_id=cid, success=True, ip="49.37.%d.%d" % (random.randint(1, 250), random.randint(1, 250)))))

    # ---- planted fraud scenarios -----------------------------------------------------
    # 1. Velocity burst: 8 small transfers in ~6 minutes
    base = at_ist(1, 15, 10)
    for i in range(8):
        events.append((base + timedelta(minutes=i * 0.8), "txn",
                       dict(txn_type="TRANSFER", from_id=burst_acc, to_id=random.choice([x for x in ids if x != burst_acc]),
                            amount=4000 + i * 150, channel="ONLINE")))
    # 2. Structuring: three withdrawals just under the 50,000 limit
    for i, amt in enumerate([48500, 49200, 49800]):
        events.append((at_ist(1, 11 + i * 3, 20), "txn",
                       dict(txn_type="WITHDRAWAL", from_id=struct_acc, to_id=None, amount=amt, channel="BRANCH")))
    # 3. Account takeover: failed logins, then a big foreign transfer at 02:xx IST
    ato = at_ist(0, 2, 14)
    for i in range(5):
        events.append((ato - timedelta(minutes=9 - i * 2), "login",
                       dict(customer_id=cust_of[victim_acc], success=False, ip="185.220.101.%d" % (40 + i))))
    for i in range(3):   # give the victim account a baseline so AMOUNT_SPIKE can fire
        events.append((at_ist(8 + i, 12, 5), "txn", dict(txn_type="WITHDRAWAL", from_id=victim_acc, to_id=None, amount=4000, channel="ATM")))
    for i in range(4):
        events.append((at_ist(12 + i, 13, 30), "txn", dict(txn_type="DEPOSIT", from_id=None, to_id=victim_acc, amount=15000, channel="BRANCH")))
    events.append((ato, "txn", dict(txn_type="TRANSFER", from_id=victim_acc, to_id=ids[20], amount=260000,
                                    channel="ONLINE", country="RU")))
    # 4. Mule account: fresh account receives a lump sum and immediately moves it abroad
    m = at_ist(1, 17, 5)
    events.append((m, "txn", dict(txn_type="DEPOSIT", from_id=None, to_id=mule_acc, amount=250000, channel="ONLINE")))
    events.append((m + timedelta(minutes=35), "txn", dict(txn_type="TRANSFER", from_id=mule_acc, to_id=ids[25],
                                                          amount=240000, channel="MOBILE", country="AE")))
    # 5. Probing: repeated attempts the balance cannot cover, then a smaller success
    p = at_ist(0, 9, 30)
    for i in range(3):
        events.append((p + timedelta(minutes=i * 3), "txn", dict(txn_type="WITHDRAWAL", from_id=probe_acc, to_id=None,
                                                                  amount=1200000, channel="ATM")))
    events.append((p + timedelta(minutes=12), "txn", dict(txn_type="WITHDRAWAL", from_id=probe_acc, to_id=None,
                                                          amount=1000, channel="ATM", country="GB")))
    # 6. Spike from an abroad session on an otherwise quiet account
    events.append((at_ist(1, 3, 30), "txn", dict(txn_type="TRANSFER", from_id=spike_acc, to_id=ids[30],
                                                 amount=90000, channel="ONLINE", country="NG")))
    # fresh deposits so the spike & structuring accounts can afford their transactions
    events.append((at_ist(2, 9, 0), "txn", dict(txn_type="DEPOSIT", from_id=None, to_id=spike_acc, amount=150000, channel="BRANCH")))

    # ---- replay in chronological order through the real engine ----------------------
    events.sort(key=lambda e: e[0])
    n_txn = 0
    for ts, kind, payload in events:
        if kind == "login":
            with conn.cursor() as cur:
                cur.execute("INSERT INTO login_attempts (customer_id, success, ip_address, attempted_at) VALUES (%s,%s,%s,%s)",
                            (payload["customer_id"], payload["success"], payload["ip"], ts))
            conn.commit()
        else:
            execute_transaction(conn, created_at=ts, country=payload.pop("country", "IN"), **payload)
            n_txn += 1

    # ---- resolve some older alerts so the review workflow has history ---------------
    with conn.cursor() as cur:
        cur.execute("""SELECT alert_id, risk_score FROM fraud_alerts
                       WHERE created_at < now() - interval '2 days' ORDER BY created_at LIMIT 6""")
        old = cur.fetchall()
    for i, a in enumerate(old):
        review_alert(conn, a["alert_id"], "DISMISSED", "Verified with customer - legitimate activity.", "analyst.meera")
    with conn.cursor() as cur:
        cur.execute("""SELECT alert_id FROM fraud_alerts WHERE status='OPEN' AND risk_score BETWEEN 50 AND 89
                       ORDER BY created_at LIMIT 1""")
        row = cur.fetchone()
    if row:
        review_alert(conn, row["alert_id"], "UNDER_REVIEW", "Requested transaction proof from the customer.", "analyst.karthik")

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM transactions"); t = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM fraud_alerts"); a = cur.fetchone()["n"]
        cur.execute("SELECT status, count(*) AS n FROM transactions GROUP BY 1 ORDER BY 1"); st = cur.fetchall()
    print(f"Seeded: {len(customers) + 1} customers, {len(accounts) + 1} accounts, {t} transactions, {a} alerts")
    print("Transaction status:", {r['status']: r['n'] for r in st})
    conn.close()


if __name__ == "__main__":
    main()
