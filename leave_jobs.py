# leave_jobs.py: scheduled leave jobs. Run by a scheduler (cron), not by people.
#   python leave_jobs.py year-start          -> the reset for this year (safe to run every day: it only runs once)
#   python leave_jobs.py year-start 2027     -> a specific year
# Example crontab line (00:05 on 1 January):
#   5 0 1 1 *  cd /path/to/agent-hrm && HRM_DB=web_demo.db python leave_jobs.py year-start
import os
import sys
from datetime import date

import db
db.DB_PATH = os.getenv("HRM_DB", db.DB_PATH)
import leave_admin

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] != "year-start":
        sys.exit("Usage: python leave_jobs.py year-start [year]")
    year = int(sys.argv[2]) if len(sys.argv) > 2 else date.today().year
    leave_admin.init_leave_admin()
    result = leave_admin.run_year_start(year)
    print(result)
    # "already run" is a normal outcome for a daily job; anything else is a real failure
    sys.exit(0 if result.get("success") or "already run" in result.get("error", "") else 1)
