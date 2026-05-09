#!/bin/bash
# MegaCorp Credential Recovery Queue Worker
# Processes internal maintenance requests and prepares exportable snapshots.

set -e

FLAG4_SRC="/opt/.credentials/flag4.txt"
REPORTS_DIR="/app/reports"
REQUEST_DIR="${REPORTS_DIR}/.maintenance-queue"

mkdir -p "$REPORTS_DIR" "$REQUEST_DIR"
chown root:webops "$REQUEST_DIR"
chmod 770 "$REQUEST_DIR"

found_request=false

for request_file in "$REQUEST_DIR"/credential_sync_*.req; do
    if [ ! -f "$request_file" ]; then
        continue
    fi

    found_request=true
    export_name="$(tr -cd 'A-Za-z0-9._-' < "$request_file")"

    if [ -z "$export_name" ]; then
        rm -f "$request_file"
        continue
    fi

    export_path="${REPORTS_DIR}/${export_name}"

    if [ -f "$FLAG4_SRC" ] && [ -r "$FLAG4_SRC" ]; then
        cp "$FLAG4_SRC" "$export_path"
        chmod 640 "$export_path"
        chown root:webops "$export_path"
    fi

    rm -f "$request_file"
done

if [ "$found_request" = false ]; then
    exit 0
fi
