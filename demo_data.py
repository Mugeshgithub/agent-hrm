# demo_data.py: fills the database with realistic activity for the web demo
# (leave requests, approvals, HR tickets, notifications). Everything goes through the REAL
# code paths in tools.py, so the demo obeys the same rules as the agent.
#   python demo_data.py          -> adds demo activity if there is none yet
#   python demo_data.py --reset  -> rebuilds hr.db from scratch first (chat history is kept)
import os
import sys
from datetime import date, timedelta

import db
db.DB_PATH = os.getenv("HRM_DB", db.DB_PATH)   # same database as server.py (HRM_DB=web_demo.db keeps hr.db untouched)
import payslips
import hr_queries
import employees
from memory import init_memory, init_summaries
from notifications import init_notifications, notify_decision, notify_ticket_reply
from tools import apply_leave, decide_leave, create_hr_ticket, reply_to_ticket


def init_all(reset=False):
    db.init_db(reset=reset)
    init_memory()
    init_summaries()
    init_notifications()
    payslips.init_payslips()
    payslips.seed_payslips()
    hr_queries.init_profiles()
    employees.init_records()


def weekday(days_ahead):
    # the first Monday-to-Friday date at least `days_ahead` days from today
    d = date.today() + timedelta(days=days_ahead)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()


def leave(emp, kind, start_in, length, approver=None):
    start = date.fromisoformat(weekday(start_in))
    end = start + timedelta(days=length - 1)
    while end.weekday() >= 5:
        end += timedelta(days=1)
    result = apply_leave(emp, kind, start.isoformat(), end.isoformat())
    if "error" in result:
        print("  skipped:", result["error"])
        return
    if approver:
        rid = result["request"]["id"]
        decide_leave(approver, rid, "approve")
        notify_decision(rid, "approve", "", db.get_conn().execute(
            "SELECT name FROM employees WHERE id = ?", (approver,)).fetchone()["name"])


def seed_activity():
    conn = db.get_conn()
    has_requests = conn.execute("SELECT COUNT(*) FROM leave_requests").fetchone()[0]
    conn.close()
    if has_requests:
        print("Demo activity already exists; nothing added. Use --reset to rebuild.")
        return

    leave("E001", "casual", 16, 2, approver="M001")   # Priya: approved, coming up
    leave("E001", "casual", 48, 2)                     # Priya: waiting for Arjun
    leave("E002", "casual", 9, 1)                      # Karthik: waiting for Arjun
    leave("E003", "sick", 6, 1)                        # Divya: waiting for Arjun
    leave("M001", "casual", 21, 3, approver="C001")   # Arjun: approved by Mugesh
    leave("H001", "casual", 30, 2)                     # Lakshmi: waiting for Mugesh

    create_hr_ticket("E001", "If my project runs late, can I carry over more than 5 casual days into next year?",
                     "Asks for an exception to the 5-day casual carry-over limit.")
    t = create_hr_ticket("E002", "Is there a parking allowance for people who drive to the Lyon office?",
                         "Asks about a parking allowance in Lyon; not covered by the policy.")
    reply_to_ticket("H001", t["id"], "Not at the moment, but we're reviewing commuting benefits for "
                                     "Lyon next quarter. I'll let you know.")
    notify_ticket_reply(t["id"], "Lakshmi")
    create_hr_ticket("E003", "Do we have a budget for a second monitor when working from home?",
                     "Asks about a home-office equipment budget.")
    print("Demo activity added.")


if __name__ == "__main__":
    init_all(reset="--reset" in sys.argv)
    seed_activity()
