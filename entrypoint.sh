#!/bin/sh
set -e

echo "Waiting for PostgreSQL at ${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}..."
while ! nc -z "${POSTGRES_HOST:-db}" "${POSTGRES_PORT:-5432}"; do
  sleep 0.5
done
echo "PostgreSQL is up."

echo "Applying migrations..."
python manage.py migrate --noinput

echo "Seeding demo data..."
python manage.py seed_demo

echo "Starting server on 0.0.0.0:8000..."
exec python manage.py runserver 0.0.0.0:8000
