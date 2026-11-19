"""
APIClone settings for apiclone-docker.

APIClone's settings.py imports this module partway through, which is where it
expects overrides to go. Every value here comes from the environment; .env.example
documents them.
"""

import os
from pathlib import Path
from urllib.parse import urlsplit

from .settings import *


def _fail(message):
    # Not ImproperlyConfigured: manage.py catches that while loading settings
    # and carries on with half-loaded ones, which hides the message behind an
    # unrelated error later.
    raise SystemExit(f"apiclone-docker: {message}")


def _env(name, default=""):
    return os.environ.get(name, "").strip() or default


def _env_bool(name, default=False):
    value = _env(name).lower()
    if not value:
        return default
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    _fail(f"{name} must be true or false, not {value!r}")


def _env_list(name):
    return [item.strip() for item in _env(name).split(",") if item.strip()]


PRODUCTION = _env("DJANGO_ENV", "development") == "production"
DATA_DIR = Path(_env("DATA_DIR", "/data"))

DEBUG = _env_bool("DEBUG", not PRODUCTION)

if _env("SECRET_KEY"):
    SECRET_KEY = _env("SECRET_KEY")
elif PRODUCTION:
    _fail(
        "SECRET_KEY is not set. Generate one with: "
        'python3 -c "import secrets; print(secrets.token_urlsafe(50))"'
    )

# Hosts
SITE_URL = _env("SITE_URL", "http://localhost").rstrip("/")
_site = urlsplit(SITE_URL)
if _site.scheme not in ("http", "https") or not _site.hostname:
    _fail(f"SITE_URL must look like https://example.com, not {SITE_URL!r}")

for _host in [_site.hostname, *_env_list("EXTRA_ALLOWED_HOSTS")]:
    if _host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_host)
    if _host not in SCRATCHR2_SAFE_HOSTS:
        SCRATCHR2_SAFE_HOSTS.append(_host)

# After this file runs, APIClone sets CORS_ALLOWED_ORIGINS and
# CSRF_TRUSTED_ORIGINS from ALLOWED_HOSTS, adding a fixed list of ports. To
# allow an origin on any other port, add it to ALLOWED_HOSTS with its port
# ("localhost:9000"). Django ignores entries with a port when it checks the Host
# header, so they only affect the origin lists.
for _origin in [SITE_URL, *_env_list("EXTRA_ORIGINS")]:
    _parts = urlsplit(_origin)
    for _host in (_parts.hostname, _parts.netloc):
        if _host and _host not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(_host)

if PRODUCTION:
    # nginx sets X-Forwarded-Proto. Django sees plain HTTP from it otherwise.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
if _site.scheme == "https":
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# Data that has to survive the container lives under DATA_DIR.
MEDIA_ROOT = DATA_DIR / "files"

# Database
DB_ENGINE = _env("DB_ENGINE", "sqlite")
if DB_ENGINE == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": DATA_DIR / "db.sqlite3",
        }
    }
elif DB_ENGINE in ("mysql", "mariadb"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "HOST": _env("DB_HOST", DB_ENGINE),
            "PORT": _env("DB_PORT"),
            "NAME": _env("DB_NAME", "apiclone"),
            "USER": _env("DB_USER", "apiclone"),
            "PASSWORD": _env("DB_PASSWORD"),
            "OPTIONS": {"charset": "utf8mb4"},
            "CONN_MAX_AGE": 60,
            "CONN_HEALTH_CHECKS": True,
        }
    }
elif DB_ENGINE in ("postgres", "postgresql"):
    _fail(
        "PostgreSQL is not supported: APIClone's migration "
        "hosts/backpack/migrations/0004_alter_backpackitem3_id.py turns an integer "
        "column into a UUID, which PostgreSQL refuses. Use mysql, mariadb or sqlite."
    )
else:
    _fail(f"DB_ENGINE must be mysql, mariadb or sqlite, not {DB_ENGINE!r}")

# Cache and cloud variables. Without Valkey, cloud variables only work within
# one process and the cache does nothing.
REDIS_URL = _env("REDIS_URL")
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "KEY_PREFIX": "apiclone",
        }
    }
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [REDIS_URL], "prefix": "apiclone:channels"},
        }
    }

# In production the jobs service runs CRON_POLYFILL_JOBS on a schedule, so the
# request-driven polyfill is off. APIClone already skips it without a cache.
USE_CRON_POLYFILL = _env_bool("USE_CRON_POLYFILL", not PRODUCTION)

# Mail. Without an SMTP server, mail is written to files in DATA_DIR/mail.
if _env("EMAIL_HOST"):
    MAILERS = {
        "default": {
            "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
            "OPTIONS": {
                "host": _env("EMAIL_HOST"),
                "port": int(_env("EMAIL_PORT", "587")),
                "username": _env("EMAIL_USER"),
                "password": _env("EMAIL_PASSWORD"),
                "use_tls": _env_bool("EMAIL_USE_TLS", True),
                "timeout": 10,
            },
        },
    }
else:
    MAILERS = {
        "default": {
            "BACKEND": "django.core.mail.backends.filebased.EmailBackend",
            "OPTIONS": {"file_path": str(DATA_DIR / "mail")},
        },
    }
DEFAULT_FROM_EMAIL = _env("DEFAULT_FROM_EMAIL", DEFAULT_FROM_EMAIL)
REQUIRE_EMAIL_VERIFICATION = _env_bool("REQUIRE_EMAIL_VERIFICATION", REQUIRE_EMAIL_VERIFICATION)

# Look and feel
USE_THEME_SELECTOR = _env_bool("USE_THEME_SELECTOR", USE_THEME_SELECTOR)
THEME_SELECTION_DEFAULT = _env("THEME_SELECTION_DEFAULT", THEME_SELECTION_DEFAULT)

# Log to the console so `docker compose logs` shows errors. The dev server
# already does this itself.
if PRODUCTION:
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {"console": {"class": "logging.StreamHandler"}},
        "root": {"handlers": ["console"], "level": _env("LOG_LEVEL", "INFO")},
    }
