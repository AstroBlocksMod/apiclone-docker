#!/usr/bin/env bash
# Prepares /data and then runs the container's command.
set -euo pipefail

data_dir="${DATA_DIR:-/data}"
mkdir -p "$data_dir/files" "$data_dir/mail"

# APIClone ships default files (avatars, thumbnails and so on) in files/.
# Copy any that are missing without touching what users have uploaded.
cp -R --update=none /app/files/. "$data_dir/files/"

if [[ "${APICLONE_MIGRATE:-0}" == 1 ]]; then
	python manage.py migrate --noinput
fi

exec "$@"
