"""
Concurrency / ACID demonstration.

10 threads try to withdraw Rs.400 at the same moment from an account holding Rs.1,000.
Only 2 can succeed.  Because execute_transaction() locks the account row (SELECT ... FOR UPDATE)
the other 8 wait, see the updated balance and are recorded as FAILED - the balance never goes
negative and no money is spent twice.

Runs in a throw-away database (fraud_test), so your demo data is untouched:
    python tests/concurrency_test.py
"""
import os
import sys
import threading

import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
BASE = os.environ.get("DATABASE_URL", "dbname=fraud_monitor user=postgres host=localhost")
TEST_DSN = BASE.replace("dbname=fraud_monitor", "dbname=fraud_test")
ADMIN_DSN = BASE.replace("dbname=fraud_monitor", "dbname=postgres")
os.environ["DATABASE_URL"] = TEST_DSN

from db import get_conn                      # noqa: E402
from engine import execute_transaction       # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", "db")


def main():
    admin = psycopg2.connect(ADMIN_DSN)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("DROP DATABASE IF EXISTS fraud_test")
        cur.execute("CREATE DATABASE fraud_test")

    conn = get_conn()
    with conn.cursor() as cur:
        for f in ("schema.sql", "rules.sql"):
            cur.execute(open(os.path.join(ROOT, f), encoding="utf-8").read())
        cur.execute("INSERT INTO customers (full_name,email) VALUES ('Test User','t@example.com') RETURNING customer_id")
        cid = cur.fetchone()["customer_id"]
        cur.execute("""INSERT INTO accounts (customer_id, account_no, balance, opened_at)
                       VALUES (%s,'T001',1000, now() - interval '1 year') RETURNING account_id""", (cid,))
        acc = cur.fetchone()["account_id"]
    conn.commit()
    conn.close()

    results, barrier = [], threading.Barrier(10)

    def worker():
        c = get_conn()
        barrier.wait()                       # release all threads at once
        results.append(execute_transaction(c, "WITHDRAWAL", acc, None, 400)["status"])
        c.close()

    threads = [threading.Thread(target=worker) for _ in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    c = get_conn()
    with c.cursor() as cur:
        cur.execute("SELECT balance FROM accounts WHERE account_id = %s", (acc,))
        balance = cur.fetchone()["balance"]
    c.close()

    ok = sum(r in ("COMPLETED", "FLAGGED") for r in results)
    failed = results.count("FAILED")
    print(f"Succeeded: {ok}   Failed (insufficient funds): {failed}   Final balance: Rs.{balance}")
    assert ok == 2 and failed == 8 and balance == 200, "Concurrency control FAILED"
    print("PASS - no double-spend, balance never negative.")

    with admin.cursor() as cur:
        cur.execute("DROP DATABASE fraud_test")
    admin.close()


if __name__ == "__main__":
    main()
