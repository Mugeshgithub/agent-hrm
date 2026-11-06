# leave_jobs.py: scheduled leave jobs. Run by a scheduler (cron), not by people.
#   python leave_jobs.py year-start          -> the reset for this year (safe to run every day: it only runs once)
#   python leave_jobs.py year-start 2027     -> a specific year
#   python leave_jobs.py month-end           -> credit paid leave (2.5 days) for the month that just ended
#   python leave_jobs.py month-end 2026-10   -> a specific month (to catch up on one that was missed)
# Example crontab lines (00:05 on 1 January, and 00:10 on the 1st of every month):
#   5 0 1 1 *   cd /path/to/agent-hrm && HRM_DB=web_demo.db python leave_jobs.py year-start
#   10 0 1 * *  cd /path/to/agent-hrm && HRM_DB=web_demo.db python leave_jobs.py month-end
import os
import sys
from datetime import date

import db
db.DB_PATH = os.getenv("HRM_DB", db.DB_PATH)
import leave_admin

USAGE = "Usage: python leave_jobs.py year-start [year]  |  python leave_jobs.py month-end [YYYY-MM]"


def parse_month(text):
    year, month = text.split("-")
    return int(year), int(month)


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("year-start", "month-end"):
        sys.exit(USAGE)
    leave_admin.init_leave_admin()
    if sys.argv[1] == "year-start":
        year = int(sys.argv[2]) if len(sys.argv) > 2 else date.today().year
        result = leave_admin.run_year_start(year)
    else:
        try:
            year, month = parse_month(sys.argv[2]) if len(sys.argv) > 2 else leave_admin.last_finished_month()
            result = leave_admin.run_month_accrual(year, month)
        except ValueError:
            sys.exit(USAGE)
    print(result)
    # "already run" is a normal outcome for a daily job; anything else is a real failure
    ok = result.get("success") or "already run" in result.get("error", "") \
        or "already been credited" in result.get("error", "")
    sys.exit(0 if ok else 1)
