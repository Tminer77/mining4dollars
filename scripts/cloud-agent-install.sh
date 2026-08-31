#!/usr/bin/env bash
# Idempotent Cloud Agent install: system deps (PostgreSQL 16), uv toolchain,
# Python venv, databases, migrations, and local .env bootstrap.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$PWD"

# ---- uv (Python package manager) -------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="${HOME}/.local/bin:${PATH}"

# ---- PostgreSQL 16 (system package; no Docker daemon in Cloud Agent VMs) ---
if ! command -v psql >/dev/null 2>&1; then
  sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql postgresql-contrib
fi

# Trust local TCP connections so postgres@127.0.0.1 needs no password (matches .env.example).
PG_HBA="/etc/postgresql/16/main/pg_hba.conf"
if [[ -f "$PG_HBA" ]] && sudo grep -q '127.0.0.1/32.*scram-sha-256' "$PG_HBA"; then
  sudo sed -i \
    -e 's/host    all             all             127.0.0.1\/32            scram-sha-256/host    all             all             127.0.0.1\/32            trust/' \
    -e 's/host    all             all             ::1\/128                 scram-sha-256/host    all             all             ::1\/128                 trust/' \
    "$PG_HBA"
fi

sudo service postgresql start
# Reload after start so trust auth is active for TCP connections.
if [[ -f "$PG_HBA" ]] && sudo grep -q '127.0.0.1/32.*trust' "$PG_HBA"; then
  sudo service postgresql reload || true
fi

# Databases for the app and integration tests.
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='m4d'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE DATABASE m4d OWNER postgres;"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='m4d_test'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE DATABASE m4d_test OWNER postgres;"

# ---- Python project --------------------------------------------------------
uv venv --python 3.12 --allow-existing .venv
uv pip install --python .venv/bin/python -e ".[dev]"

# Bootstrap .env from example; drop empty M4D_CORS_ORIGINS (pydantic cannot parse "").
if [[ ! -f .env ]]; then
  grep -v '^M4D_CORS_ORIGINS=$' .env.example > .env
fi

.venv/bin/alembic upgrade head
