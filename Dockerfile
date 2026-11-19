# syntax=docker/dockerfile:1.29

# Every image this repo builds comes from this file. Pick one with a target:
#
#   apiclone-dev  APIClone with Django's dev server, source bind-mounted
#   apiclone      APIClone for production (Daphne, ASGI)
#   www-dev       scratch-www's dev server, source bind-mounted
#   proxy         nginx with the built scratch-www and APIClone's static files
#   apiclone-lock writes docker/apiclone/requirements.txt (see scripts/lock)

# Base images, pinned to a version and a digest.
ARG PYTHON_IMAGE=python:3.14.10-slim-trixie@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f
# Must match .nvmrc at WWW_COMMIT. scripts/check-pins checks this.
ARG NODE_IMAGE=node:20.20.2-trixie-slim@sha256:abfbe12cc943141a0c9e8c0a57d710df1dadd95d35e8662cc02958b284d1f35b
ARG NGINX_IMAGE=nginx:1.30.7-alpine@sha256:bef154735288bc38d99fac41ae63730684bcc661503673a04a0f9bc526ba8598
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d

# Upstream sources, pinned to exact commits. Bump them on purpose.
ARG APICLONE_REPO=https://codeberg.org/heathercat123/apiclone.git
ARG APICLONE_COMMIT=c902dda888c6bfd5ef2cbb05834dea009f43beb4
ARG WWW_REPO=https://codeberg.org/heathercat123/scratch-www.git
ARG WWW_COMMIT=2d90f7ebb3728ed4ea2fdec03de2cdfd391aa6f5
# Production routing always comes from the fork's script, even when WWW_REPO
# points at another scratch-www.
ARG NGINX_ROUTES_SCRIPT=https://codeberg.org/heathercat123/scratch-www/raw/commit/2d90f7ebb3728ed4ea2fdec03de2cdfd391aa6f5/bin/configure-nginx.js

FROM ${UV_IMAGE} AS uv

# hadolint cannot see that these ADD sources are git repositories and a URL.

FROM scratch AS apiclone-src
ARG APICLONE_REPO
ARG APICLONE_COMMIT
# hadolint ignore=DL3020
ADD ${APICLONE_REPO}#${APICLONE_COMMIT} /

FROM scratch AS www-src
ARG WWW_REPO
ARG WWW_COMMIT
# hadolint ignore=DL3020
ADD ${WWW_REPO}#${WWW_COMMIT} /

# APIClone

FROM ${PYTHON_IMAGE} AS apiclone-base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    DJANGO_SETTINGS_MODULE=apiclone.settings \
    DATA_DIR=/data
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmariadb3 \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 1000 apiclone \
    && useradd --uid 1000 --gid apiclone --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin apiclone \
    && mkdir /data \
    && chown apiclone:apiclone /data
WORKDIR /app

FROM apiclone-base AS apiclone-build-base
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential pkg-config libmariadb-dev \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy

FROM apiclone-build-base AS apiclone-lock-build
WORKDIR /lock
COPY --from=apiclone-src /requirements.txt upstream.txt
COPY docker/apiclone/requirements-extra.in ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip compile --universal --generate-hashes --python-version 3.12 \
        --custom-compile-command scripts/lock \
        --output-file requirements.txt upstream.txt requirements-extra.in

FROM scratch AS apiclone-lock
COPY --from=apiclone-lock-build /lock/requirements.txt /

FROM apiclone-build-base AS apiclone-deps
COPY docker/apiclone/requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv --python /usr/local/bin/python3 /opt/venv \
    && uv pip install --python /opt/venv/bin/python --require-hashes -r /tmp/requirements.txt

FROM apiclone-base AS apiclone-runtime
COPY --from=apiclone-deps /opt/venv /opt/venv
COPY docker/apiclone/jobs.py /opt/apiclone-docker/
COPY --chmod=755 docker/apiclone/entrypoint.sh /opt/apiclone-docker/entrypoint.sh
ENTRYPOINT ["/opt/apiclone-docker/entrypoint.sh"]
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=6 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/', timeout=4)"]

FROM apiclone-runtime AS apiclone-dev
# The source and local_settings.py are bind-mounted over /app (see compose.yaml).
ENV APICLONE_MIGRATE=1
USER 1000:1000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

FROM apiclone-runtime AS apiclone
COPY --from=apiclone-src / /app
COPY docker/apiclone/local_settings.py /app/apiclone/local_settings.py
RUN python manage.py collectstatic --noinput --verbosity 0
ENV DJANGO_ENV=production
USER 1000:1000
CMD ["daphne", "--bind", "0.0.0.0", "--port", "8000", "--proxy-headers", "apiclone.asgi:application"]

# scratch-www

FROM ${NODE_IMAGE} AS www-dev
ENV npm_config_cache=/app/node_modules/.cache/npm \
    npm_config_update_notifier=false \
    WWW_VERSION=development
RUN mkdir -p /app/node_modules \
    && chown node:node /app/node_modules
WORKDIR /app
COPY --chmod=755 docker/www/dev-entrypoint.sh /usr/local/bin/dev-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/dev-entrypoint.sh"]
EXPOSE 8333
HEALTHCHECK --interval=15s --timeout=5s --start-period=20m --retries=4 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:8333/').then(r => process.exit(r.ok ? 0 : 1), () => process.exit(1))"]
CMD ["npm", "start"]

FROM ${NODE_IMAGE} AS www-build
ARG WWW_COMMIT
ARG NGINX_ROUTES_SCRIPT
WORKDIR /src
COPY --from=www-src /package.json /package-lock.json /.npmrc ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund
COPY --from=www-src / ./
# hadolint ignore=DL3020
ADD ${NGINX_ROUTES_SCRIPT} /opt/configure-nginx.js
RUN test -f bin/configure-nginx.js || cp /opt/configure-nginx.js bin/configure-nginx.js
# scratch-www bakes these hosts into the bundle, so they are build arguments.
ARG SITE_URL=http://localhost
RUN site="${SITE_URL%/}" \
    && export NODE_ENV=production \
        WWW_VERSION="${WWW_COMMIT}" \
        ROOT_URL="$site" \
        API_HOST="$site/hosts/api" \
        ASSET_HOST="$site/hosts/assets" \
        BACKPACK_HOST="$site/hosts/backpack" \
        PROJECT_HOST="$site/hosts/projects" \
        STATIC_HOST="$site/hosts/uploads" \
        CLOUDDATA_HOST="${site#*://}/hosts/clouddata" \
    && npm run build \
    && node bin/configure-nginx.js > /src/routes.conf

# Reverse proxy

FROM ${NGINX_IMAGE} AS proxy
COPY --from=www-build /src/build /usr/share/nginx/www
COPY --from=www-build /src/routes.conf /etc/nginx/apiclone/scratch-www-routes.conf
COPY --from=apiclone /app/static_prod /usr/share/nginx/apiclone-static
COPY docker/proxy/common.conf docker/proxy/site.conf docker/proxy/http.conf docker/proxy/https.conf /etc/nginx/apiclone/
COPY --chmod=755 docker/proxy/40-apiclone-site.sh /docker-entrypoint.d/
RUN rm /etc/nginx/conf.d/default.conf
EXPOSE 80 443
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=6 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1/health/"]
