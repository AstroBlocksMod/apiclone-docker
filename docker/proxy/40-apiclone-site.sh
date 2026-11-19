#!/bin/sh
# Picks the HTTP or HTTPS site config when nginx starts. POSIX sh, because the
# nginx image is Alpine and has no Bash.
set -eu

certs=/etc/nginx/certs
if [ -f "$certs/fullchain.pem" ] && [ -f "$certs/privkey.pem" ]; then
	mode=https
else
	mode=http
fi

: "${SITE_URL:?SITE_URL must be set}"
SITE_URL="${SITE_URL%/}"
export SITE_URL
# shellcheck disable=SC2016 # envsubst takes the variable name literally
envsubst '${SITE_URL}' <"/etc/nginx/apiclone/$mode.conf" >/etc/nginx/conf.d/apiclone.conf
echo "$0: serving over $mode"
