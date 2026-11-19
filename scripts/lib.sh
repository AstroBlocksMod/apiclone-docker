# shellcheck shell=bash
# Sourced by the other scripts. Keep everything here working on Bash 3.2, which
# is what macOS ships.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

die() {
	echo "$(basename "$0"): $*" >&2
	exit 1
}

# Prints a setting: from the environment, else from a NAME=value line in .env,
# else the given default. .env values are taken literally.
env_value() {
	local name="$1" default="${2:-}" value=""
	if [[ -n "${!name:-}" ]]; then
		value="${!name}"
	elif [[ -f .env ]]; then
		value="$(sed -n "s/^${name}=//p" .env | tail -n 1)"
	fi
	printf '%s\n' "${value:-$default}"
}

# Prints the default of an ARG in the Dockerfile, which is where pins live.
pin() {
	local value
	value="$(sed -n "s/^ARG $1=//p" Dockerfile | head -n 1)"
	[[ -n "$value" ]] || die "no default for ARG $1 in the Dockerfile"
	printf '%s\n' "$value"
}

# Prints the URL of a file at a commit in a Codeberg or GitHub repository.
raw_url() {
	local repo="${1%.git}" commit="$2" path="$3"
	case "$repo" in
	https://github.com/*) printf 'https://raw.githubusercontent.com/%s/%s/%s\n' "${repo#https://github.com/}" "$commit" "$path" ;;
	*) printf '%s/raw/commit/%s/%s\n' "$repo" "$commit" "$path" ;;
	esac
}

prod_compose() {
	[[ -f .env ]] || die "no .env. Copy .env.example to .env and fill it in first."
	case "$(env_value DB_ENGINE mysql)" in
	mysql | mariadb)
		# The database images refuse to set up a user with an empty password,
		# and would only show that as a database that never gets healthy.
		[[ -n "$(env_value DB_PASSWORD)" ]] || die "set DB_PASSWORD in .env (needed for $(env_value DB_ENGINE mysql))"
		;;
	esac
	# Docker would create a missing bind-mount directory owned by root, which
	# makes it awkward to put certificates in later.
	mkdir -p "$(env_value TLS_CERT_DIR ./certs)"
	docker compose -f compose.prod.yaml "$@"
}
