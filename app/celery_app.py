import os
from celery import Celery
from celery.schedules import crontab

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "pro_content_api",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["app.tasks"],
)

celery_app.conf.update(
    timezone=os.getenv("CELERY_TIMEZONE", "America/Chicago"),
    enable_utc=False,

    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    broker_transport_options={"visibility_timeout": 60 * 60 * 2},

    beat_schedule={
        "monthly-queue-logs-upload": {
            "task": "app.tasks.upload_previous_month_queue_logs",
            "schedule": crontab(minute=10, hour=0, day_of_month=1),
        }
    },
)
