"""Database connection helper."""
import os
import psycopg2
import psycopg2.extras

DSN = os.environ.get("DATABASE_URL", "dbname=fraud_monitor user=postgres host=localhost")


def get_conn():
    """New connection; rows come back as dicts."""
    return psycopg2.connect(DSN, cursor_factory=psycopg2.extras.RealDictCursor)
