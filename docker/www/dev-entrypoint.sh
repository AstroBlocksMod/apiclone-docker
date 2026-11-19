#!/usr/bin/env bash
# Starts scratch-www's dev server from the checkout mounted at /app.
#
# node_modules lives in a volume. Dependencies and translations are installed
# again only when package-lock.json changes.
set -euo pipefail
cd /app

if [[ ! -f package.json ]]; then
	echo "No scratch-www checkout at /app. Run scripts/fetch or set WWW_SRC." >&2
	exit 1
fi

# Run as whoever owns the checkout, so files written into it (translations)
# belong to the person who cloned it.
if [[ "$(id -u)" == 0 ]]; then
	uid="$(stat -c %u package.json)"
	gid="$(stat -c %g package.json)"
	if [[ "$uid" != 0 ]]; then
		if [[ "$(stat -c %u node_modules)" != "$uid" ]]; then
			chown -R "$uid:$gid" node_modules
		fi
		exec setpriv --reuid="$uid" --regid="$gid" --clear-groups -- env HOME=/tmp "$0" "$@"
	fi
fi

lock_hash="$(sha256sum package-lock.json | cut -d ' ' -f 1)"
marker=node_modules/.apiclone-docker-installed

if [[ "$(cat "$marker" 2>/dev/null || true)" != "$lock_hash" ]]; then
	echo "Installing scratch-www dependencies. The first run takes a few minutes."
	# npm ci would try to delete node_modules itself, which is a mount point.
	find node_modules -mindepth 1 -maxdepth 1 ! -name .cache -exec rm -rf {} +
	npm install --no-audit --no-fund --no-save
	echo "Building translations."
	npm run translate
	echo "$lock_hash" >"$marker"
fi

exec "$@"
