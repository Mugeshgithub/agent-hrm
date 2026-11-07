# onboarding.py: the checklist for a new hire. When HR hires someone who starts in the future, the steps
# of TEMPLATE are created for them. Each step has an owner (the new hire, HR or the manager), a due date
# counted back from the start date, and a status. Every rule is checked here, in Python, and every
# change goes in the person's change history (who, what, why). No AI, so it is unit-tested in CI.
#
# Who may change a step: HR can complete, skip or reopen any step (HR can mark a new hire's step done
# for them, for example when a paper document arrives). A manager can complete the steps owned by
# "manager" for their own direct reports. The new hire's own steps get their own entry point in the
# new-hire link; they never go through this module's HR/manager rules.
from datetime import date, timedelta

import db
import hr_queries
from tools import get_employee

NEW_HIRE, HR, MANAGER = "new_hire", "hr", "manager"
TODO, DONE, SKIPPED = "todo", "done", "skipped"

# key, title, owner, days before the start date it is due, what it means
# The DPAE (déclaration préalable à l'embauche) comes before day one: Code du travail L1221-10 says a hire
# can only start after the employer has filed it (L1221-11 is the penalty when it isn't).
TEMPLATE = [
    ("contract", "Employment contract signed", HR, 7,
     "Prepare the contract, have it signed, and mark this done when HR has the signed copy."),
    ("id", "Identity document", NEW_HIRE, 7, "A copy of a passport or national ID card."),
    ("iban", "Bank details (IBAN)", NEW_HIRE, 7, "The IBAN of the account where the salary is paid."),
    ("social_security", "Social security number", NEW_HIRE, 7, "The 15-digit French social security number."),
    ("equipment", "Laptop and equipment ready", MANAGER, 2, "Order and prepare what the new hire needs on day one."),
    ("accounts", "Email and tool accounts created", HR, 2, "Work email and the tools the team uses."),
    ("welcome", "Welcome message sent", MANAGER, 2, "A short message from the manager before the first day."),
    ("dpae", "DPAE filed", HR, 1,
     "File the déclaration préalable à l'embauche before day one (Code du travail, L1221-10)."),
]


def init_onboarding():
    conn = db.get_conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS onboardings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id TEXT NOT NULL UNIQUE REFERENCES employees(id),
                start_date TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS onboarding_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                onboarding_id INTEGER NOT NULL REFERENCES onboardings(id),
                position INTEGER NOT NULL,
                step_key TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT NOT NULL,
                owner TEXT NOT NULL,
                due_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'todo',
                done_by TEXT,
                done_at TEXT,
                note TEXT NOT NULL DEFAULT ''
            )""")
    conn.close()


def _log(conn, emp_id, actor_id, field, old, new, reason):
    # the same change history as the employee record (employee_changes), so HR reads it in one place
    conn.execute("""INSERT INTO employee_changes (emp_id, changed_by, field, old_value, new_value, reason)
                    VALUES (?, ?, ?, ?, ?, ?)""", (emp_id, actor_id, field, old, new, reason))


def _due(start, days_before, today):
    # counted back from the start date, but never in the past on the day the hire is entered
    return min(start, max(start - timedelta(days=days_before), today)).isoformat()


def start(conn, emp_id, start_date, hr_id, today=None):
    # Called by employees.create_employee inside its own transaction, so the person and the checklist are
    # saved together or not at all. Someone who starts today or earlier has nothing to prepare.
    today = today or date.today()
    start_day = date.fromisoformat(start_date)
    if start_day <= today:
        return None
    onboarding_id = conn.execute("INSERT INTO onboardings (emp_id, start_date, created_by) VALUES (?, ?, ?)",
                                 (emp_id, start_date, hr_id)).lastrowid
    for position, (key, title, owner, days_before, detail) in enumerate(TEMPLATE, start=1):
        conn.execute("""INSERT INTO onboarding_steps (onboarding_id, position, step_key, title, detail, owner, due_date)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                     (onboarding_id, position, key, title, detail, owner, _due(start_day, days_before, today)))
    _log(conn, emp_id, hr_id, "onboarding", None, f"started with {len(TEMPLATE)} steps", "New hire")
    return onboarding_id


def _rows(sql, params=()):
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


STEP = """SELECT s.id, s.step_key, s.title, s.detail, s.owner, s.due_date, s.status, s.note, s.done_at,
                 d.name AS done_by_name, o.emp_id, o.start_date
          FROM onboarding_steps s
          JOIN onboardings o ON o.id = s.onboarding_id
          LEFT JOIN employees d ON d.id = s.done_by"""


def _shape(row, today):
    row["overdue"] = row["status"] == TODO and row["due_date"] < today.isoformat()
    return row


def _is_hr(actor_id):
    staff = get_employee(actor_id)
    person = hr_queries.person(actor_id) if staff else None
    return bool(person and person["active"] and person["role"] == "hr")


def checklist(viewer_id, emp_id, today=None):
    # HR's view on a person's profile. None when the person has no onboarding.
    if not _is_hr(viewer_id):
        return {"error": "Only HR can see an onboarding checklist."}
    today = today or date.today()
    steps = _rows(STEP + " WHERE o.emp_id = ? ORDER BY s.position", (emp_id,))
    if not steps:
        return None
    steps = [_shape(s, today) for s in steps]
    person = hr_queries.person(emp_id)
    done = sum(s["status"] == DONE for s in steps)
    skipped = sum(s["status"] == SKIPPED for s in steps)
    return {"emp_id": emp_id, "name": person["name"], "title": person["title"], "start_date": person["start_date"],
            "manager_name": person["manager_name"], "steps": steps,
            "progress": {"done": done, "skipped": skipped, "total": len(steps),
                         "complete": done + skipped == len(steps)}}


def open_steps(actor_id, today=None):
    # the steps waiting on this person, most urgent first: HR's own steps, or the manager's steps for the
    # people who report to them. Used by the Needs you inbox. People who have left are not waiting on anyone.
    today = today or date.today()
    staff = get_employee(actor_id)
    person = hr_queries.person(actor_id) if staff else None
    if not person or not person["active"]:
        return []
    if staff["role"] == "hr":
        rows = _rows(STEP + " WHERE s.status = ? AND s.owner = ?", (TODO, HR))
    elif staff["role"] == "manager":
        rows = _rows(STEP + " JOIN employees h ON h.id = o.emp_id WHERE s.status = ? AND s.owner = ? AND h.manager_id = ?",
                     (TODO, MANAGER, actor_id))
    else:
        return []
    items = []
    for r in rows:
        hire = hr_queries.person(r["emp_id"])
        if not hire["active"]:
            continue
        items.append({"step_id": r["id"], "employee_id": r["emp_id"], "employee_name": hire["name"],
                      "job_title": hire["title"], "start_date": r["start_date"], "step_title": r["title"],
                      "detail": r["detail"], "owner": r["owner"], "due_date": r["due_date"],
                      "overdue": r["due_date"] < today.isoformat()})
    return sorted(items, key=lambda i: (i["due_date"], i["step_id"]))


def _step_for(actor_id, step_id, action):
    # the step and its rule check, shared by complete, skip and reopen
    rows = _rows(STEP + " WHERE s.id = ?", (step_id,))
    if not rows:
        return None, {"error": "No such step."}
    step = rows[0]
    hire = hr_queries.person(step["emp_id"])
    if not hire["active"]:
        return None, {"error": f"{hire['name']} no longer works here."}
    if _is_hr(actor_id):
        return step, None
    staff = get_employee(actor_id)
    own_manager_step = (action == "complete" and staff is not None and staff["role"] == "manager"
                        and hr_queries.person(actor_id)["active"]
                        and step["owner"] == MANAGER and hire["manager_id"] == actor_id)
    if own_manager_step:
        return step, None
    if action == "complete":
        return None, {"error": "Only HR, or the manager of the new hire for the manager's own steps, can mark this done."}
    return None, {"error": "Only HR can do this."}


def _change(actor_id, step_id, action, new_status, allowed_from, reason, needs_reason):
    step, err = _step_for(actor_id, step_id, action)
    if err:
        return err
    reason = (reason or "").strip()
    if needs_reason and not reason:
        return {"error": "Give a reason. It goes in the change history."}
    if step["status"] not in allowed_from:
        return {"error": f"This step is already {step['status']}." if new_status != TODO else "This step is not done."}
    conn = db.get_conn()
    try:
        with conn:
            if new_status == TODO:
                conn.execute("UPDATE onboarding_steps SET status = ?, done_by = NULL, done_at = NULL, note = ? WHERE id = ?",
                             (TODO, "", step_id))
            else:
                conn.execute("""UPDATE onboarding_steps SET status = ?, done_by = ?, done_at = CURRENT_TIMESTAMP, note = ?
                                WHERE id = ?""", (new_status, actor_id, reason, step_id))
            _log(conn, step["emp_id"], actor_id, f"onboarding: {step['title']}", step["status"], new_status,
                 reason or "Marked done")
    finally:
        conn.close()
    return {"success": True, "step_id": step_id, "status": new_status}


def complete_step(actor_id, step_id, note=""):
    return _change(actor_id, step_id, "complete", DONE, (TODO,), note, needs_reason=False)


def skip_step(actor_id, step_id, reason):
    return _change(actor_id, step_id, "skip", SKIPPED, (TODO,), reason, needs_reason=True)


def reopen_step(actor_id, step_id, reason):
    return _change(actor_id, step_id, "reopen", TODO, (DONE, SKIPPED), reason, needs_reason=True)
