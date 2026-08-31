#!/usr/bin/env bash
# Per-boot service init: ensure PostgreSQL is running before agents work.
set -euo pipefail

if command -v pg_isready >/dev/null 2>&1; then
  if ! pg_isready -h 127.0.0.1 -p 5432 -U postgres -q 2>/dev/null; then
    sudo service postgresql start
  fi
else
  sudo service postgresql start
fi

# Wait until PostgreSQL accepts connections (up to ~30s).
for _ in $(seq 1 30); do
  if pg_isready -h 127.0.0.1 -p 5432 -U postgres -q 2>/dev/null; then
    exit 0
  fi
  sleep 1
done

echo "PostgreSQL did not become ready in time" >&2
exit 1
