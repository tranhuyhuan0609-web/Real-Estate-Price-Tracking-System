#!/bin/sh
# Periodic database backup. Usage: backup.sh postgres|mongo [once]
#   postgres -> /backups/postgres/<db>_<UTC time>.dump   (pg_dump custom format; restore with pg_restore)
#   mongo    -> /backups/mongo/<db>_<UTC time>.archive.gz (mongodump --archive --gzip; restore with mongorestore)
# A dump is written to *.tmp and renamed only when it succeeded, so the newest file is always a
# complete backup. The scheduler exports its age as realestate_backup_last_success_timestamp_seconds.
set -eu
KIND="$1"
MODE="${2:-loop}"
INTERVAL="${BACKUP_INTERVAL_SECONDS:-86400}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-7}"
DIR="/backups/$KIND"
mkdir -p "$DIR"

run_once() {
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    case "$KIND" in
        postgres)
            out="$DIR/${POSTGRES_DB}_${stamp}.dump"
            PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h postgres -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f "$out.tmp" ;;
        mongo)
            out="$DIR/${MONGO_DB}_${stamp}.archive.gz"
            mongodump --host mongo --username "$MONGO_USER" --password "$MONGO_PASSWORD" \
                --authenticationDatabase admin --db "$MONGO_DB" --archive="$out.tmp" --gzip --quiet ;;
        *) echo "unknown backup kind: $KIND" >&2; exit 2 ;;
    esac
    mv "$out.tmp" "$out"
    find "$DIR" -type f -mtime +"$KEEP_DAYS" -delete
    find "$DIR" -type f -name '*.tmp' -mmin +360 -delete
    echo "backup $KIND ok: $out ($(du -h "$out" | cut -f1))"
}

if [ "$MODE" = once ]; then run_once; exit 0; fi
while true; do
    if run_once; then
        sleep "$INTERVAL"
    else
        echo "backup $KIND FAILED, retrying in 1h" >&2
        sleep 3600
    fi
done
