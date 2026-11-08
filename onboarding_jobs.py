# onboarding_jobs.py: the daily onboarding check. Run by a scheduler (cron), not by people.
#   python onboarding_jobs.py          -> prepare what is due today: reminders, the first-day information, first-week
#                                          check-ins, trial period reminders (safe to run more than once a day)
# Example crontab line (08:00 every day):
#   0 8 * * *  cd /path/to/agent-hrm && HRM_DB=web_demo.db python onboarding_jobs.py
# Nothing is emailed from here: each message is put in the outbox for HR to copy, and a notification is added in the app.
import os
import sys

import db
db.DB_PATH = os.getenv("HRM_DB", db.DB_PATH)
import checkins


if __name__ == "__main__":
    checkins.init_checkins()
    done = checkins.run_daily()
    for kind, names in done.items():
        print(f"{kind}: {', '.join(names) if names else 'nobody'}")
    sys.exit(0)
