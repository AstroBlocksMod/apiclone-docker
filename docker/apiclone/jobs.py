"""
Runs APIClone's scheduled jobs (CRON_POLYFILL_JOBS in its settings.py).

APIClone can run them from incoming requests, which it does not recommend for
production. The jobs service runs this instead: each job runs once at startup
and then again whenever its interval has passed.
"""

import logging
import time
from pathlib import Path

import django
from django.conf import settings
from django.core.management import call_command

django.setup()
log = logging.getLogger("apiclone_docker.jobs")
# Touched on every pass of the loop. The container healthcheck looks at its age.
# The container's /tmp is its own, so a fixed name is safe.
heartbeat = Path("/tmp/jobs-heartbeat")  # noqa: S108

last_run = {}
while True:
    heartbeat.touch()
    for command, interval in settings.CRON_POLYFILL_JOBS:
        if time.monotonic() - last_run.get(command, -interval) < interval:
            continue
        log.info("Running %s", command)
        try:
            call_command(command)
        except Exception:  # One failing job must not stop the others.
            log.exception("%s failed, trying again in %s seconds", command, interval)
        last_run[command] = time.monotonic()
    time.sleep(60)
