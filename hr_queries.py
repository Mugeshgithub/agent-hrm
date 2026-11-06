# hr_queries.py: read-only queries for the web app (directory, team, calendar, inboxes, audit log).
# Every change still goes through tools.py / payslips.py, so the business rules live in one place.
import db
from tools import PENDING, HOLIDAYS, balance_of, clean_days

# job details for the people directory (the employees table only holds what the agent needs)
SEED_PROFILES = [
    # id, title, department, location, start_date
    ("C001", "Chief Executive Officer", "Leadership", "Paris", "2021-03-01"),
    ("M001", "Engineering Manager", "Engineering", "Paris", "2022-01-10"),
    ("E001", "Software Engineer", "Engineering", "Paris", "2023-09-04"),
    ("E002", "Data Analyst", "Engineering", "Lyon", "2024-02-12"),
    ("E003", "Product Designer", "Engineering", "Paris", "2025-06-02"),
    ("H001", "HR Manager", "People", "Paris", "2022-05-16"),
]


def init_profiles():
    conn = db.get_conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS profiles (
                emp_id TEXT PRIMARY KEY REFERENCES employees(id),
                title TEXT NOT NULL,
                department TEXT NOT NULL,
                location TEXT NOT NULL,
                start_date TEXT NOT NULL
            )""")
        conn.executemany("INSERT OR IGNORE INTO profiles (emp_id, title, department, location, start_date) VALUES (?, ?, ?, ?, ?)", SEED_PROFILES)
    conn.close()


def _rows(sql, params=()):
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


PERSON = """SELECT e.id, e.name, e.role, e.email, e.manager_id, m.name AS manager_name,
                   p.title, p.department, p.location, p.start_date, p.contract_type, p.end_date,
                   (p.end_date IS NULL OR p.end_date >= date('now', 'localtime')) AS active
            FROM employees e
            LEFT JOIN profiles p ON p.emp_id = e.id
            LEFT JOIN employees m ON m.id = e.manager_id"""


def person(emp_id):
    rows = _rows(PERSON + " WHERE e.id = ?", (emp_id,))
    return rows[0] if rows else None


def people(include_balances=False):
    rows = _rows(PERSON + " ORDER BY p.department, e.name")
    if include_balances:                       # HR only: balances are personal data
        bal = {r["id"]: r for r in _rows("SELECT * FROM employees")}
        for r in rows:
            for kind, value in balance_of(bal[r["id"]]).items():
                r[f"{kind}_balance"] = value
    return rows


def balances(emp_id):
    # saved balance, days waiting for approval, and what is really available to request
    emp = _rows("SELECT * FROM employees WHERE id = ?", (emp_id,))[0]
    pending = {r["leave_type"]: r["days"] for r in _rows(
        """SELECT leave_type, SUM(days) AS days FROM leave_requests
           WHERE employee_id = ? AND status = ? GROUP BY leave_type""", (emp_id, PENDING))}
    out = {}
    for kind, saved in balance_of(emp).items():
        out[kind] = {"balance": saved, "pending": pending.get(kind, 0),
                     "available": clean_days(saved - pending.get(kind, 0))}
    return out


def my_requests(emp_id):
    return _rows("""SELECT id, leave_type, start_date, end_date, days, status, reason
                    FROM leave_requests WHERE employee_id = ? ORDER BY start_date DESC""", (emp_id,))


def team(manager_id):
    # people who have left (end date passed) are no longer on the team
    members = _rows(PERSON + " WHERE e.manager_id = ? AND (p.end_date IS NULL OR p.end_date >= date('now', 'localtime'))"
                             " ORDER BY e.name", (manager_id,))
    for m in members:
        m["balances"] = balances(m["id"])
    return members


def leave_between(start, end, employee_ids):
    # approved and pending leave overlapping [start, end], for a calendar
    if not employee_ids:
        return []
    marks = ",".join("?" * len(employee_ids))
    return _rows(f"""SELECT r.id, r.employee_id, e.name, r.leave_type, r.start_date, r.end_date,
                            r.days, r.status
                     FROM leave_requests r JOIN employees e ON e.id = r.employee_id
                     WHERE r.employee_id IN ({marks}) AND r.status IN (?, 'approved')
                       AND r.start_date <= ? AND r.end_date >= ?
                     ORDER BY r.start_date""",
                 (*employee_ids, PENDING, end, start))


def holidays():
    return sorted(d.isoformat() for d in HOLIDAYS)


def all_tickets(status=None):
    where = "WHERE t.status = ?" if status else ""
    rows = _rows(f"""SELECT t.id, t.employee_id, e.name AS employee_name, t.question, t.summary,
                            t.status, t.hr_reply, t.created_at
                     FROM hr_tickets t JOIN employees e ON e.id = t.employee_id
                     {where} ORDER BY t.status = 'closed', t.id DESC""", (status,) if status else ())
    for r in rows:
        r["ticket"] = db.ticket_number(r["id"])
    return rows


def payslip_months(emp_id):
    return [r["month"] for r in _rows(
        "SELECT DISTINCT month FROM payslip_lines WHERE emp_id = ? ORDER BY month DESC", (emp_id,))]


def access_log(limit=100):
    return _rows("""SELECT l.id, l.viewer_id, v.name AS viewer_name, l.emp_id, e.name AS employee_name,
                           l.month, l.viewed_at
                    FROM payslip_access_log l
                    LEFT JOIN employees v ON v.id = l.viewer_id
                    LEFT JOIN employees e ON e.id = l.emp_id
                    ORDER BY l.id DESC LIMIT ?""", (limit,))


def notifications(emp_id, limit=30):
    return _rows("""SELECT id, message, is_read, created_at FROM notifications
                    WHERE employee_id = ? ORDER BY id DESC LIMIT ?""", (emp_id, limit))


def mark_notifications_read(emp_id):
    conn = db.get_conn()
    with conn:
        conn.execute("UPDATE notifications SET is_read = 1 WHERE employee_id = ? AND is_read = 0", (emp_id,))
    conn.close()


def chat_history(emp_id, limit=40):
    rows = _rows("""SELECT id, role, content, created_at FROM chat_messages
                    WHERE employee_id = ? ORDER BY id DESC LIMIT ?""", (emp_id, limit))
    return list(reversed(rows))
