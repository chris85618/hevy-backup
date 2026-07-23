from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .services.sync import scheduled_sync

DEFAULT_CRON = "0 * * * *"

scheduler = BackgroundScheduler()


def apply_cron(expr: str) -> None:
    """(Re)schedule the sync job. Raises ValueError on invalid crontab expression."""
    trigger = CronTrigger.from_crontab(expr)
    scheduler.add_job(scheduled_sync, trigger, id="hevy_sync",
                      coalesce=True, max_instances=1, replace_existing=True)
