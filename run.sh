#!/usr/bin/env bash
# One-command setup & launch (Linux / macOS).  Needs PostgreSQL running locally.
set -e
DB=${DB_NAME:-fraud_monitor}
export DATABASE_URL=${DATABASE_URL:-"dbname=$DB user=${PGUSER:-postgres} host=${PGHOST:-localhost} password=${PGPASSWORD:-postgres}"}
python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -q -r requirements.txt
createdb -h "${PGHOST:-localhost}" -U "${PGUSER:-postgres}" "$DB" 2>/dev/null || true
python seed.py
python app/app.py
