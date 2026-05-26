"""
APScheduler wrapper for the Snipe Finder.

Runs two recurring jobs:
  • hourly_job  — triggers all active searches with frequency='hourly'
  • daily_job   — triggers all active searches with frequency='daily'

Start the scheduler by calling start() from app.py's __main__ block.
The scheduler runs in a daemon thread and stops automatically when the
process exits.
"""

import logging
import os
import sqlite3

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import snipe

logger = logging.getLogger(__name__)

_data_dir = ".data" if os.path.isdir(".data") else "."
DB_PATH   = os.path.join(_data_dir, "sports_cards.db")

_scheduler: BackgroundScheduler | None = None


def _active_searches(frequency: str) -> list[int]:
    """Return IDs of active saved searches with the given frequency."""
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT id FROM saved_searches WHERE is_active=1 AND frequency=?",
            (frequency,),
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception as exc:
        logger.error("Could not load searches: %s", exc)
        return []


def _run_batch(frequency: str):
    ids = _active_searches(frequency)
    if not ids:
        return
    logger.info("Scheduler: running %d %s search(es)", len(ids), frequency)
    for sid in ids:
        try:
            snipe.run_search(sid)
        except Exception as exc:
            logger.error("Scheduled scan failed for search %s: %s", sid, exc)


def hourly_job():
    _run_batch("hourly")


def daily_job():
    _run_batch("daily")


def start():
    global _scheduler
    if _scheduler and _scheduler.running:
        return

    _scheduler = BackgroundScheduler(daemon=True, timezone="UTC")

    # Hourly searches: run every 60 minutes
    _scheduler.add_job(
        hourly_job,
        trigger=IntervalTrigger(hours=1),
        id="hourly_scans",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Daily searches: run once per day at 06:00 UTC
    _scheduler.add_job(
        daily_job,
        trigger=CronTrigger(hour=6, minute=0, timezone="UTC"),
        id="daily_scans",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    _scheduler.start()
    logger.info(
        "Scheduler started — hourly: every 60 min | daily: 06:00 UTC"
    )


def stop():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


def next_runs() -> dict:
    """Return next scheduled run times for the UI status display."""
    if not _scheduler or not _scheduler.running:
        return {}
    result = {}
    for job in _scheduler.get_jobs():
        nf = job.next_run_time
        result[job.id] = nf.isoformat() if nf else None
    return result
