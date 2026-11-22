# job_locks.py: one run of a scheduled job at a time, even when two copies of the app start it at the same second (cron on each copy).
# The jobs are already safe to run again one after the other (each checks what was done), but those checks read first and write after:
# two runs at the same moment could both pass them. The first run inserts the job's row; the PRIMARY KEY refuses the second, which does
# nothing. The row is deleted when the run ends; a copy that died mid-run leaves it, and after STALE_MINUTES the next run takes over.
# The law sync does the same with a Postgres advisory lock (labour_code/sync.py).
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from db import get_conn

STALE_MINUTES = 60     # far longer than any of these jobs takes


def init_job_locks():
    conn = get_conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_locks (
                job TEXT PRIMARY KEY,        -- leave-year-start, leave-month-end, onboarding-daily, recruitment-daily
                started_at TEXT NOT NULL
            )
        """)
    conn.close()


def _stamp(moment):
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def take(job, now=None):
    """True for the one run that may go ahead now; False while another run of this job is going."""
    now = now or datetime.now(timezone.utc)
    init_job_locks()
    conn = get_conn()
    try:
        with conn:
            conn.execute("DELETE FROM job_locks WHERE job = ? AND started_at < ?", (job, _stamp(now - timedelta(minutes=STALE_MINUTES))))
            conn.execute("INSERT INTO job_locks (job, started_at) VALUES (?, ?)", (job, _stamp(now)))
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def release(job):
    conn = get_conn()
    with conn:
        conn.execute("DELETE FROM job_locks WHERE job = ?", (job,))
    conn.close()


@contextmanager
def only_one(job):
    """with only_one("onboarding-daily") as mine: if mine, do the work. The lock is given back however the run ends."""
    mine = take(job)
    try:
        yield mine
    finally:
        if mine:
            release(job)
