# recruitment_jobs.py: the daily candidate-data retention job. Run by a scheduler (cron), not by people.
#   python recruitment_jobs.py          -> erase the candidates whose day has come (retention.py), and prepare the reminders for interviews today or
#                                          tomorrow (interviews.py: they wait in the candidate outbox). Safe to run more than once a day.
# Example crontab line (03:00 every day):
#   0 3 * * *  cd /path/to/agent-hrm && HRM_DB=web_demo.db python recruitment_jobs.py
# What it erases and why is in retention.py (the CNIL's retention periods for recruitment). It prints counts only, never a name. Every erasure is
# logged in candidate_erasures, with no personal data. HR can run the same thing from the Recruitment page.
import os
import sys

import db
db.DB_PATH = os.getenv("HRM_DB", db.DB_PATH)
import careers
import interviews
import job_locks
import jobs


if __name__ == "__main__":
    jobs.init_jobs()
    # one run at a time, even from two copies at the same second (job_locks.py)
    with job_locks.only_one("recruitment-daily") as mine:
        if not mine:
            print("Another copy is running the recruitment job right now: nothing done.")
            sys.exit(0)
        done = careers.run_retention()
        reminders = interviews.run_reminders()
    print(f"{done['on']}: erased {done['erased']} candidate(s)" + "".join(f", {n} by '{rule}'" for rule, n in done["by_rule"].items()))
    print(f"interview reminders prepared: {reminders}")
    sys.exit(0)
