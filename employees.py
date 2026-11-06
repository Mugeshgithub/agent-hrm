# employees.py: employee records. New hire, edit, offboard, and a change log.
# Only HR can change records, and every rule is checked here.
import re
from datetime import date

import db
from tools import PENDING, get_employee

ROLES = ("employee", "manager", "hr")
CONTRACTS = ("CDI", "CDD", "Apprenticeship", "Internship")
EMPLOYEE_FIELDS = {"name", "email", "role", "manager_id"}
PROFILE_FIELDS = {"title", "department", "location", "contract_type"}
EDITABLE = EMPLOYEE_FIELDS | PROFILE_FIELDS
PREFIX = {"employee": "E", "manager": "M", "hr": "H"}
DEFAULT_CASUAL, DEFAULT_SICK = 12, 6

def init_records():
    conn = db.get_conn()
    with conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(profiles)")}
        if "contract_type" not in cols:
            conn.execute("ALTER TABLE profiles ADD COLUMN contract_type TEXT NOT NULL DEFAULT 'CDI'")
        if "end_date" not in cols:
            conn.execute("ALTER TABLE profiles ADD COLUMN end_date TEXT")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS employee_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id TEXT NOT NULL,
                changed_by TEXT NOT NULL,
                field TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                reason TEXT NOT NULL,
                changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
    conn.close()

def _profile(emp_id):
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM profiles WHERE emp_id = ?", (emp_id,)).fetchone()
    conn.close()
    return dict(row) if row else {}


def is_active(emp_id, on=None):
    # still employed until the end date; no end date means still here
    end = _profile(emp_id).get("end_date")
    return end is None or end >= (on or date.today()).isoformat()


def _not_hr(hr_id):
    staff = get_employee(hr_id)
    if staff is None or staff["role"] != "hr" or not is_active(hr_id):
        return {"error": "Only HR can change employee records."}
    return None


def _valid_date(text):
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return None

def _reports(emp_id):
    # people who report to emp_id and still work here
    conn = db.get_conn()
    rows = conn.execute("SELECT id, name FROM employees WHERE manager_id = ?", (emp_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows if is_active(r["id"])]


def _email_taken(email, except_id=None):
    conn = db.get_conn()
    row = conn.execute("SELECT id FROM employees WHERE lower(email) = lower(?) AND id != ?",
                       (email, except_id or "")).fetchone()
    conn.close()
    return row is not None


def _manager_problem(manager_id, emp_id=None):
    if manager_id in (None, ""):
        return None                                   # top of the company, no manager
    mgr = get_employee(manager_id)
    if mgr is None or not is_active(manager_id):
        return "Choose a manager who works here."
    if mgr["role"] != "manager":
        return f"{mgr['name']} isn't a manager. Change their role to manager first."
    if emp_id and manager_id == emp_id:
        return "Someone can't be their own manager."
    # walk up the chain: if we meet emp_id, the change would make a loop
    seen, current = set(), manager_id
    while current and current not in seen:
        if current == emp_id:
            return f"That would create a loop: {mgr['name']} already reports to this person."
        seen.add(current)
        current = (get_employee(current) or {}).get("manager_id")
    return None

def _log(conn, emp_id, hr_id, field, old, new, reason):
    conn.execute("""INSERT INTO employee_changes (emp_id, changed_by, field, old_value, new_value, reason)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                 (emp_id, hr_id, field,
                  None if old is None else str(old),
                  None if new is None else str(new), reason))


def _next_id(role):
    prefix = PREFIX[role]
    conn = db.get_conn()
    nums = [int(r["id"][1:]) for r in conn.execute("SELECT id FROM employees WHERE id LIKE ?", (f"{prefix}%",))
            if r["id"][1:].isdigit()]
    conn.close()
    return f"{prefix}{(max(nums) + 1) if nums else 1:03d}"


def create_employee(hr_id, data):
    # ---- part 1: checks ----
    if (err := _not_hr(hr_id)):
        return err
    d = {k: (v.strip() if isinstance(v, str) else v) for k, v in data.items()}
    missing = [f for f in ("name", "email", "role", "title", "department", "location", "start_date") if not d.get(f)]
    if missing:
        return {"error": f"Fill in: {', '.join(missing)}."}
    if d["role"] not in ROLES:
        return {"error": "Role must be employee, manager or HR."}
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", d["email"]):
        return {"error": "That email address doesn't look right."}
    if _email_taken(d["email"]):
        return {"error": "Someone already uses that email address."}
    if not _valid_date(d["start_date"]):
        return {"error": "Use a real start date."}
    contract = d.get("contract_type") or "CDI"
    if contract not in CONTRACTS:
        return {"error": f"Contract must be one of: {', '.join(CONTRACTS)}."}
    if (problem := _manager_problem(d.get("manager_id"))):
        return {"error": problem}
    casual = int(d.get("casual_balance", DEFAULT_CASUAL))
    sick = int(d.get("sick_balance", DEFAULT_SICK))
    if casual < 0 or sick < 0:
        return {"error": "Balances can't be negative."}

    # ---- part 2: save ----
    emp_id = _next_id(d["role"])
    conn = db.get_conn()
    try:
        with conn:   # person, profile and log line are saved together, or not at all
            conn.execute("INSERT INTO employees VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (emp_id, d["name"], d["role"], d.get("manager_id") or None, d["email"], casual, sick))
            conn.execute("""INSERT INTO profiles (emp_id, title, department, location, start_date, contract_type)
                            VALUES (?, ?, ?, ?, ?, ?)""",
                         (emp_id, d["title"], d["department"], d["location"], d["start_date"], contract))
            _log(conn, emp_id, hr_id, "record", None, "created", d.get("reason") or "New hire")
    finally:
        conn.close()
    return {"success": True, "id": emp_id, "name": d["name"]}


def update_employee(hr_id, emp_id, changes, reason):
    # ---- part 1: who, what, why ----
    if (err := _not_hr(hr_id)):
        return err
    emp = get_employee(emp_id)
    if emp is None:
        return {"error": "No such person."}
    if not (reason or "").strip():
        return {"error": "Give a reason for the change. It goes in the change history."}
    unknown = set(changes) - EDITABLE
    if unknown:
        return {"error": f"These can't be edited here: {', '.join(sorted(unknown))}."}

    # ---- part 2: keep only what really changed ----
    current = {**emp, **_profile(emp_id)}
    new = {k: (v.strip() if isinstance(v, str) else v) for k, v in changes.items()}
    if "manager_id" in new and new["manager_id"] == "":
        new["manager_id"] = None
    diff = {k: v for k, v in new.items() if current.get(k) != v}
    if not diff:
        return {"error": "Nothing changed."}

    # ---- part 3: rules for each field ----
    if "role" in diff:
        if diff["role"] not in ROLES:
            return {"error": "Role must be employee, manager or HR."}
        if emp_id == hr_id:
            return {"error": "You can't change your own role. Ask another HR person."}
        if emp["role"] == "manager" and (reports := _reports(emp_id)):
            names = ", ".join(r["name"] for r in reports)
            return {"error": f"{emp['name']} still manages {names}. Give them a new manager first."}
    if "name" in diff and not diff["name"]:
        return {"error": "The name can't be empty."}
    if "email" in diff:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", diff["email"] or ""):
            return {"error": "That email address doesn't look right."}
        if _email_taken(diff["email"], except_id=emp_id):
            return {"error": "Someone already uses that email address."}
    if "contract_type" in diff and diff["contract_type"] not in CONTRACTS:
        return {"error": f"Contract must be one of: {', '.join(CONTRACTS)}."}
    if "manager_id" in diff and (problem := _manager_problem(diff["manager_id"], emp_id)):
        return {"error": problem}
    for f in ("title", "department", "location"):
        if f in diff and not diff[f]:
            return {"error": f"The {f} can't be empty."}

    # ---- part 4: save each field with its log line ----
    conn = db.get_conn()
    try:
        with conn:
            for field, value in diff.items():
                table, key = ("employees", "id") if field in EMPLOYEE_FIELDS else ("profiles", "emp_id")
                conn.execute(f"UPDATE {table} SET {field} = ? WHERE {key} = ?", (value, emp_id))
                _log(conn, emp_id, hr_id, field, current.get(field), value, reason.strip())
    finally:
        conn.close()
    return {"success": True, "id": emp_id, "changed": sorted(diff)}

def offboard_employee(hr_id, emp_id, end_date, reason):
    # ---- part 1: basic checks ----
    if (err := _not_hr(hr_id)):
        return err
    emp = get_employee(emp_id)
    if emp is None:
        return {"error": "No such person."}
    if emp_id == hr_id:
        return {"error": "You can't offboard yourself. Ask another HR person."}
    if not (reason or "").strip():
        return {"error": "Give a reason. It goes in the change history."}
    end = _valid_date(end_date)
    if end is None:
        return {"error": "Use a real end date."}

    # ---- part 2: rules about the person ----
    profile = _profile(emp_id)
    if profile.get("end_date"):
        return {"error": f"{emp['name']} already has an end date ({profile['end_date']})."}
    if end.isoformat() < profile.get("start_date", ""):
        return {"error": "The end date is before their start date."}
    if (reports := _reports(emp_id)):
        names = ", ".join(r["name"] for r in reports)
        return {"error": f"{emp['name']} still manages {names}. Give them a new manager first."}

    # ---- part 3: leave booked after they go, then save ----
    conn = db.get_conn()
    try:
        later = conn.execute("""SELECT start_date, end_date FROM leave_requests
                                WHERE employee_id = ? AND status IN (?, 'approved') AND end_date > ?""",
                             (emp_id, PENDING, end.isoformat())).fetchall()
        if later:
            booked = ", ".join(f"{r['start_date']} to {r['end_date']}" for r in later)
            return {"error": f"{emp['name']} has leave booked after the end date ({booked}). "
                             f"Cancel or move it first."}
        with conn:
            conn.execute("UPDATE profiles SET end_date = ? WHERE emp_id = ?", (end.isoformat(), emp_id))
            _log(conn, emp_id, hr_id, "end_date", None, end.isoformat(), reason.strip())
    finally:
        conn.close()
    # what payroll needs for the final payslip: unused paid leave is paid out (L3141-28)
    return {"success": True, "id": emp_id, "end_date": end.isoformat(),
            "final_balance": {"casual": emp["casual_balance"], "sick": emp["sick_balance"]}}

def history(viewer_id, emp_id):
    viewer = get_employee(viewer_id)
    if viewer is None or (viewer["role"] != "hr" and viewer_id != emp_id):
        return {"error": "Only HR or the person themselves can see this history."}
    conn = db.get_conn()
    rows = conn.execute("""SELECT c.field, c.old_value, c.new_value, c.reason, c.changed_at,
                                  e.name AS changed_by
                           FROM employee_changes c LEFT JOIN employees e ON e.id = c.changed_by
                           WHERE c.emp_id = ? ORDER BY c.id DESC""", (emp_id,)).fetchall()
    conn.close()
    return {"id": emp_id, "changes": [dict(r) for r in rows]}


def directory_entry(emp_id):
    # the safe, public view: what any colleague may see (no balances, no pay)
    emp = get_employee(emp_id)
    if emp is None:
        return None
    p = _profile(emp_id)
    mgr = get_employee(emp["manager_id"]) if emp["manager_id"] else None
    return {"id": emp_id, "name": emp["name"], "email": emp["email"],
            "title": p.get("title"), "department": p.get("department"),
            "location": p.get("location"), "manager": mgr["name"] if mgr else None,
            "active": is_active(emp_id)}