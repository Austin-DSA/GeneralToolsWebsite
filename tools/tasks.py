"""Huey background tasks for the tools app.

huey.contrib.djhuey autodiscovers this module (tasks.py in each installed
app), so tasks defined here are registered on both the web process (for
enqueueing) and the consumer (`manage.py run_huey` - the `worker` service in
docker-compose.yml).

In dev and under tests Huey runs in "immediate" mode (see HUEY in
settings.py): tasks execute inline and periodic schedules do NOT fire - run
the underlying management commands manually instead.
"""

import logging

from django.core.management import call_command
from huey import crontab
from huey.contrib.djhuey import db_periodic_task

logger = logging.getLogger(__name__)


# Crontab times use the consumer's clock, which is UTC in the containers
# (never give a container a TZ - it silently shifts published event times).
# 11:00 UTC is 5/6am Central: refreshed before the workday, after any
# late-night wiki edits.
@db_periodic_task(crontab(hour="11", minute="0"))
def syncLinkTreeWiki():
    """Daily wiki-link resolution for Link Tree WIKI items.

    The management command stays the imperative core (manual runs and
    --dry-run keep working); this is just its schedule.
    """
    # The command raises SystemExit(1) when items errored so host schedulers
    # see a non-zero exit; inside the consumer that must become a logged
    # failure, not an exit attempt.
    try:
        call_command("sync_link_tree_wiki", quiet=True)
    except SystemExit as e:
        if e.code:
            logger.error(
                "sync_link_tree_wiki reported errors (exit code %s)", e.code
            )
