# checkins.py: the daily check-ins of onboarding (PR 5). A scheduler runs onboarding_jobs.py every day, and for
# every new hire who has not been cancelled, this module prepares what is due, each in the app (a notification)
# and in the outbox (an email for HR to copy; nothing is sent from here):
#   - CHASE: the things the new hire still has to send, once their due date has come. At most one reminder
#     every 2 days, one message that lists everything missing. HR's and the manager's steps are not chased:
#     they are in the Needs you inbox.
#   - BEFORE DAY ONE: the practical information, 3 days before the start (or on the first run after hiring
#     someone who starts sooner than that).
#   - FIRST WEEK: a check-in message for the new hire a week after the start, and a reminder to the manager
#     (the checklist's "First-week check-in" step then waits for them in Needs you).
#   - TRIAL PERIOD: the manager is reminded 2 weeks before the trial period of an approved contract ends.
# Safe to run any number of times a day: each reminder is written in one transaction with its log row, and a
# kind that is sent once is never sent twice. Every reminder is in the person's change history ("system", or
# the HR person who ran it by hand). No AI and no third-party package, so it is unit-tested in CI.
from datetime import date, timedelta

import contracts
import db
import hr_queries
import onboarding
import outbox
from tools import get_employee

CHASE_EVERY_DAYS = 2
INFO_DAYS_BEFORE = 3
FIRST_WEEK_DAYS = onboarding.FIRST_WEEK_DAYS
FIRST_WEEK_WINDOW_DAYS = 7              # a first-week message is only sent in the week after the 7th day, never months late
TRIAL_REMINDER_DAYS = 14


def init_checkins():
    conn = db.get_conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS onboarding_reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id TEXT NOT NULL REFERENCES employees(id),
                kind TEXT NOT NULL,
                key TEXT NOT NULL,                 -- the day (chase) or what it is about (trial end date): one per emp, kind and key
                sent_on TEXT NOT NULL,
                ran_by TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (emp_id, kind, key)
            )""")
    conn.close()


def _rows(sql, params=()):
    return onboarding._rows(sql, params)


def _first(name):
    return name.split()[0]


def _hr_contact():
    rows = _rows("SELECT id, name, email FROM employees WHERE role = 'hr' ORDER BY id")
    return next((r for r in rows if hr_queries.person(r["id"])["active"]), None)


def _already(emp_id, kind, key=None):
    sql = "SELECT 1 FROM onboarding_reminders WHERE emp_id = ? AND kind = ?" + (" AND key = ?" if key is not None else "")
    return bool(_rows(sql, (emp_id, kind) + ((key,) if key is not None else ())))


def _send(emp_id, kind, key, today, ran_by, to, subject, body, notes, what):
    """One reminder, all or nothing: the log row, the outbox email, the in-app notifications (a list of
    (who, message): each person is told only what is theirs) and the history line."""
    conn = db.get_conn()
    try:
        with conn:
            conn.execute("INSERT INTO onboarding_reminders (emp_id, kind, key, sent_on, ran_by) VALUES (?, ?, ?, ?, ?)",
                         (emp_id, kind, key, today.isoformat(), ran_by))
            outbox.queue(conn, emp_id, to, subject, body, kind, None)
            for who, message in notes:
                conn.execute("INSERT INTO notifications (employee_id, message) VALUES (?, ?)", (who, message))
            onboarding._log(conn, emp_id, ran_by or "system", "reminder", None, kind, what)
    finally:
        conn.close()


def _missing(steps, today):
    return [s for s in steps if s["owner"] == onboarding.NEW_HIRE and s["status"] == onboarding.TODO and s["due_date"] <= today.isoformat()]


def _list(steps):
    return "\n".join(f"- {s['title']} (due {date.fromisoformat(s['due_date']):%d %B})" for s in steps)


def _chase(hire, steps, today, ran_by):
    missing = _missing(steps, today)
    if not missing:
        return False
    last = _rows("SELECT MAX(sent_on) AS d FROM onboarding_reminders WHERE emp_id = ? AND kind = 'chase'", (hire["id"],))[0]["d"]
    if last and (today - date.fromisoformat(last)).days < CHASE_EVERY_DAYS:
        return False
    hr = _hr_contact()
    names = ", ".join(s["title"][0].lower() + s["title"][1:] for s in missing)           # "bank details (IBAN)", not "(iban)"
    _send(hire["id"], "chase", today.isoformat(), today, ran_by, hire["email"],
          "A few things we still need from you",
          f"Hi {_first(hire['name'])},\n\nTo get everything ready for your first day we still need:\n\n{_list(missing)}\n\n"
          f"Open your personal page (the link in your welcome email) and send them there, or ask the assistant what is next."
          + (f" If the link doesn't work, write to {hr['name']} ({hr['email']})." if hr else "") +
          "\n\nThank you,\nThe CognivexAI HR team\n",
          [(hire["id"], f"Reminder: we still need your {names}. Your checklist shows what to send.")],
          f"Reminded the new hire of {len(missing)} missing {'item' if len(missing) == 1 else 'items'}")
    return True


def _before_day_one(hire, steps, start, today, ran_by):
    if not (0 <= (start - today).days <= INFO_DAYS_BEFORE) or _already(hire["id"], "before_day_one"):
        return False
    hr = _hr_contact()
    missing = _missing(steps, today)
    _send(hire["id"], "before_day_one", "", today, ran_by, hire["email"], f"Your first day at CognivexAI: {start:%A %d %B}",
          f"Hi {_first(hire['name'])},\n\nYour first day is {start:%A %d %B}, as {hire['title']} in our {hire['location']} office."
          + (f" {hire['manager_name']} will welcome you." if hire["manager_name"] else "") + "\n\n"
          + (f"We still need these from you before then:\n\n{_list(missing)}\n\n" if missing else "Everything we asked for is in. Thank you.\n\n")
          + (f"If anything is unclear before then, write to {hr['name']} ({hr['email']}).\n\n" if hr else "")
          + "See you soon,\nThe CognivexAI HR team\n",
          [(hire["id"], f"Your first day is {start:%A %d %B}. We sent you the practical information.")],
          f"Sent the information for the first day, {(start - today).days} days ahead")
    return True


def _first_week(hire, start, today, ran_by):
    days = (today - start).days
    if not (FIRST_WEEK_DAYS <= days <= FIRST_WEEK_DAYS + FIRST_WEEK_WINDOW_DAYS) or _already(hire["id"], "first_week"):
        return False
    manager = get_employee(hire["manager_id"]) if hire["manager_id"] else None
    manager = manager if manager and hr_queries.person(manager["id"])["active"] else None
    hr = _hr_contact()
    notes = [(hire["id"], "It has been a week: how is it going? " + (f"{manager['name']} will check in with you." if manager else ""))]
    if manager:
        notes.append((manager["id"], f"{hire['name']} started a week ago: time for the first-week check-in."))
    _send(hire["id"], "first_week", "", today, ran_by, hire["email"], "How is your first week going?",
          f"Hi {_first(hire['name'])},\n\nYou have been with us for a week. How is it going? "
          + (f"{manager['name']} will check in with you, and " if manager else "")
          + (f"you can always write to {hr['name']} ({hr['email']}) about anything." if hr else "you can always write to HR about anything.")
          + "\n\nThe CognivexAI HR team\n",
          notes, "Sent the first-week check-in" + (f" and reminded {manager['name']}" if manager else ""))
    return True


def _trial_reminders(today, ran_by):
    sent = []
    for row in _rows("SELECT id FROM contract_drafts WHERE status IN ('approved', 'signed') ORDER BY id"):
        draft = contracts._draft(row["id"])
        hire = hr_queries.person(draft["emp_id"])
        trial = draft["fields"].get("trial", 0)
        if not hire or not hire["active"] or not trial:
            continue
        end = contracts.trial_end(hire["contract_type"], date.fromisoformat(hire["start_date"]), trial)
        if not (0 <= (end - today).days <= TRIAL_REMINDER_DAYS) or _already(hire["id"], "trial_end", end.isoformat()):
            continue
        manager = get_employee(hire["manager_id"]) if hire["manager_id"] else None
        if manager is None or not hr_queries.person(manager["id"])["active"]:
            continue
        days = (end - today).days
        _send(hire["id"], "trial_end", end.isoformat(), today, ran_by, manager["email"],
              f"{hire['name']}'s trial period ends on {end:%d %B}",
              f"Hi {_first(manager['name'])},\n\n{hire['name']}'s trial period ends on {end:%A %d %B %Y}, in {days} {'day' if days == 1 else 'days'}. "
              f"Please decide whether to confirm {_first(hire['name'])} and tell HR.\n\n"
              f"If you want to end the trial period instead, the employee has to be given notice first (Code du travail, L1221-25): "
              f"for example two weeks after a month of presence, and a month after three months.\n\nThe CognivexAI HR team\n",
              [(manager["id"], f"{hire['name']}'s trial period ends on {end:%d %B}, in {days} {'day' if days == 1 else 'days'}. Decide whether to confirm them.")],
              f"Reminded {manager['name']} that the trial period ends on {end.isoformat()}")
        sent.append(hire["name"])
    return sent


def run_daily(today=None, ran_by=None):
    """Prepare everything that is due today. Returns the names of the people each kind of reminder was prepared for."""
    today = today or date.today()
    done = {"chase": [], "before_day_one": [], "first_week": [], "trial_end": []}
    for row in _rows("SELECT emp_id FROM onboardings WHERE cancelled_at IS NULL ORDER BY id"):
        hire = hr_queries.person(row["emp_id"])
        if not hire or not hire["active"]:
            continue
        start = date.fromisoformat(hire["start_date"])
        steps = _rows(onboarding.STEP + " WHERE o.emp_id = ? ORDER BY s.position", (hire["id"],))
        for kind, sent in (("chase", _chase(hire, steps, today, ran_by)), ("before_day_one", _before_day_one(hire, steps, start, today, ran_by)),
                           ("first_week", _first_week(hire, start, today, ran_by))):
            if sent:
                done[kind].append(hire["name"])
    done["trial_end"] = _trial_reminders(today, ran_by)
    return done


# ---------- the new hire's own reminders (the notifications that wait on their welcome page) ----------

def reminders_for(emp_id, limit=5):
    return _rows("SELECT id, message, created_at FROM notifications WHERE employee_id = ? AND is_read = 0 ORDER BY id DESC LIMIT ?", (emp_id, limit))


def mark_read(emp_id):
    conn = db.get_conn()
    with conn:
        conn.execute("UPDATE notifications SET is_read = 1 WHERE employee_id = ? AND is_read = 0", (emp_id,))
    conn.close()
