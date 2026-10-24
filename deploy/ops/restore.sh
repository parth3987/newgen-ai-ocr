#!/bin/sh
# Restore (ADR-0031), run by `make restore BACKUP=<timestamp>` with the API and workers
# stopped. Checks the backup's SHA-256 sums, replaces the database contents and the
# document volume, and leaves the result for `python -m newgen_ocr.ops verify`.
set -eu
if [ -z "${BACKUP:-}" ]; then
  echo "set BACKUP=<timestamp directory under the backup dir>" >&2
  exit 2
fi
src="/backups/$BACKUP"
(cd "$src" && sha256sum -c SHA256SUMS)
PGPASSWORD=$(cat /run/secrets/postgres_password)
export PGPASSWORD
psql --host=postgres --username=ngocr --dbname=ngocr -v ON_ERROR_STOP=1 -q \
  -c 'DROP SCHEMA public CASCADE' -c 'CREATE SCHEMA public'
pg_restore --host=postgres --username=ngocr --dbname=ngocr --no-owner --exit-on-error "$src/db.dump"
find /data -mindepth 1 -delete
tar -C /data -xzpf "$src/data.tar.gz" --numeric-owner
echo "restored: $BACKUP"
