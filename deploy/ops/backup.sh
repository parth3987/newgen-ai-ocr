#!/bin/sh
# Backup (ADR-0031), run by `make backup`: the database and the document volume together,
# because the database names files on the volume. Runs in the PostgreSQL image on the
# internal network; the volume is mounted read-only.
#
# Output: /backups/<UTC timestamp>/ with db.dump (pg_dump custom format), data.tar.gz,
# MANIFEST and SHA256SUMS. Stop the API and workers first for a consistent copy, or run it
# live: a document committed during the copy may then be missing from one half, which
# `python -m newgen_ocr.ops verify` reports after a restore.
set -eu
umask 077
stamp=$(date -u +%Y%m%dT%H%M%SZ)
# Written under a .partial name and renamed only when complete: a failed run never looks
# like a backup.
out="/backups/$stamp.partial"
mkdir -p "$out"
PGPASSWORD=$(cat /run/secrets/postgres_password)
export PGPASSWORD
pg_dump --host=postgres --username=ngocr --dbname=ngocr --format=custom --no-owner \
  --file="$out/db.dump"
tar -C /data -czf "$out/data.tar.gz" .
{
  echo "created=$stamp"
  echo "postgres=$(pg_dump --version)"
  echo "schema=$(psql --host=postgres --username=ngocr --dbname=ngocr -Atc 'select version_num from alembic_version')"
  echo "documents=$(psql --host=postgres --username=ngocr --dbname=ngocr -Atc 'select count(*) from documents')"
} > "$out/MANIFEST"
(cd "$out" && sha256sum db.dump data.tar.gz MANIFEST > SHA256SUMS)
mv "$out" "/backups/$stamp"
echo "backup written: $stamp"
