# leave_admin.py: the HR side of leave. Public holidays, balance adjustments with a reason,
# and the start-of-year reset (casual carry-over and new allowances, from hr_policy.md).
# Every balance change that isn't a leave request is written to leave_adjustments, so anyone can
# see why their balance moved. No AI here, so it's unit-tested in CI (leave_admin_tests.py).
from datetime import date

import db
import tools
from tools import PENDING, LEAVE_TYPES, get_employee

ALLOWANCE = {"casual": 12, "sick": 6}   # hr_policy.md: Casual leave, Sick leave
CASUAL_CARRY_MAX = 5                     # hr_policy.md: Carry forward (sick leave lapses)
MAX_ADJUSTMENT = 30                      # one adjustment can't move a balance by more than this

# French public holidays (jours fériés) for the demo years
FRENCH_HOLIDAYS = [
    ("2026-01-01", "New Year's Day"), ("2026-04-06", "Easter Monday"), ("2026-05-01", "Labour Day"),
    ("2026-05-08", "Victory in Europe Day"), ("2026-05-14", "Ascension Day"), ("2026-05-25", "Whit Monday"),
    ("2026-07-14", "Bastille Day"), ("2026-08-15", "Assumption Day"), ("2026-11-01", "All Saints' Day"),
    ("2026-11-11", "Armistice Day"), ("2026-12-25", "Christmas Day"),
    ("2027-01-01", "New Year's Day"), ("2027-03-29", "Easter Monday"), ("2027-05-01", "Labour Day"),
    ("2027-05-06", "Ascension Day"), ("2027-05-08", "Victory in Europe Day"), ("2027-05-17", "Whit Monday"),
    ("2027-07-14", "Bastille Day"), ("2027-08-15", "Assumption Day"), ("2027-11-01", "All Saints' Day"),
    ("2027-11-11", "Armistice Day"), ("2027-12-25", "Christmas Day"),
]


def init_leave_admin():
    conn = db.get_conn()
    with conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS public_holidays (
                            day TEXT PRIMARY KEY, name TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS leave_adjustments (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            emp_id TEXT NOT NULL,
                            leave_type TEXT NOT NULL,
                            days INTEGER NOT NULL,          -- + adds to the balance, - takes away
                            kind TEXT NOT NULL,             -- manual, year_start, lapse
                            reason TEXT NOT NULL,
                            changed_by TEXT NOT NULL,       -- an HR id, or 'system' for the yearly job
                            changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS leave_runs (
                            key TEXT PRIMARY KEY, ran_by TEXT NOT NULL, people INTEGER NOT NULL,
                            ran_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        if conn.execute("SELECT COUNT(*) FROM public_holidays").fetchone()[0] == 0:
            conn.executemany("INSERT INTO public_holidays VALUES (?, ?)", FRENCH_HOLIDAYS)
    conn.close()
    load_holidays()


def load_holidays():
    # tools.HOLIDAYS is shared by leave counting, payslip dates and the calendars;
    # refill the SAME set so every module sees the change straight away
    conn = db.get_conn()
    days = {date.fromisoformat(r["day"]) for r in conn.execute("SELECT day FROM public_holidays")}
    conn.close()
    tools.HOLIDAYS.clear()
    tools.HOLIDAYS.update(days)


def _not_hr(hr_id):
    staff = get_employee(hr_id)
    if staff is None or staff["role"] != "hr":
        return {"error": "Only HR can do this."}
    return None


# ---------- public holidays ----------

def list_holidays():
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute("SELECT day, name FROM public_holidays ORDER BY day")]
    conn.close()
    for r in rows:
        r["weekend"] = date.fromisoformat(r["day"]).weekday() >= 5
    return rows


def add_holiday(hr_id, day, name):
    if (err := _not_hr(hr_id)):
        return err
    try:
        d = date.fromisoformat(day)
    except (TypeError, ValueError):
        return {"error": "Use a real date."}
    if not (name or "").strip():
        return {"error": "Give the holiday a name."}
    conn = db.get_conn()
    try:
        if conn.execute("SELECT 1 FROM public_holidays WHERE day = ?", (d.isoformat(),)).fetchone():
            return {"error": "That date is already a public holiday."}
        with conn:
            conn.execute("INSERT INTO public_holidays VALUES (?, ?)", (d.isoformat(), name.strip()))
    finally:
        conn.close()
    load_holidays()
    note = "It falls on a weekend, so it doesn't change anyone's working days." if d.weekday() >= 5 else \
           "Requests already sent keep the days they were counted with."
    return {"success": True, "day": d.isoformat(), "name": name.strip(), "note": note}


def remove_holiday(hr_id, day):
    if (err := _not_hr(hr_id)):
        return err
    conn = db.get_conn()
    with conn:
        cur = conn.execute("DELETE FROM public_holidays WHERE day = ?", (day,))
    conn.close()
    if cur.rowcount == 0:
        return {"error": "That date isn't a public holiday."}
    load_holidays()
    return {"success": True, "day": day}


# ---------- balance adjustments ----------

def _pending(conn, emp_id, leave_type):
    return conn.execute("""SELECT COALESCE(SUM(days), 0) FROM leave_requests
                           WHERE employee_id = ? AND leave_type = ? AND status = ?""",
                        (emp_id, leave_type, PENDING)).fetchone()[0]


def adjust_balance(hr_id, emp_id, leave_type, days, reason):
    if (err := _not_hr(hr_id)):
        return err
    emp = get_employee(emp_id)
    if emp is None:
        return {"error": "No such person."}
    if emp_id == hr_id:
        return {"error": "You can't change your own balance. Ask another HR person."}
    if leave_type not in LEAVE_TYPES:
        return {"error": "Choose casual or sick leave."}
    try:
        days = int(days)
    except (TypeError, ValueError):
        return {"error": "Give a whole number of days, like 2 or -1."}
    if days == 0:
        return {"error": "The change can't be 0 days."}
    if abs(days) > MAX_ADJUSTMENT:
        return {"error": f"One adjustment can change a balance by at most {MAX_ADJUSTMENT} days."}
    if not (reason or "").strip():
        return {"error": "Give a reason. The person will see it in their balance history."}

    column = f"{leave_type}_balance"            # safe: leave_type is one of LEAVE_TYPES
    conn = db.get_conn()
    try:
        current = emp[column]
        pending = _pending(conn, emp_id, leave_type)
        if current + days < pending:
            return {"error": f"{emp['name']} has {pending} {leave_type} "
                             f"{'day' if pending == 1 else 'days'} waiting for approval, so the balance "
                             f"can't go below {pending}."}
        with conn:   # the balance and its history line change together, or not at all
            conn.execute(f"UPDATE employees SET {column} = {column} + ? WHERE id = ?", (days, emp_id))
            conn.execute("""INSERT INTO leave_adjustments (emp_id, leave_type, days, kind, reason, changed_by)
                            VALUES (?, ?, ?, 'manual', ?, ?)""", (emp_id, leave_type, days, reason.strip(), hr_id))
    finally:
        conn.close()
    return {"success": True, "id": emp_id, "leave_type": leave_type, "change": days,
            "balance_before": current, "balance_after": current + days}


def balance_history(emp_id, year=None):
    # why the balance is what it is: HR adjustments and the yearly reset, plus leave taken
    year = year or date.today().year
    conn = db.get_conn()
    adjustments = [dict(r) for r in conn.execute(
        """SELECT a.leave_type, a.days, a.kind, a.reason, a.changed_at,
                  COALESCE(e.name, 'Automatic yearly update') AS changed_by
           FROM leave_adjustments a LEFT JOIN employees e ON e.id = a.changed_by
           WHERE a.emp_id = ? ORDER BY a.id DESC""", (emp_id,))]
    taken = [dict(r) for r in conn.execute(
        """SELECT leave_type, start_date, end_date, days FROM leave_requests
           WHERE employee_id = ? AND status = 'approved' AND substr(start_date, 1, 4) = ?
           ORDER BY start_date DESC""", (emp_id, str(year)))]
    emp = conn.execute("SELECT casual_balance, sick_balance FROM employees WHERE id = ?", (emp_id,)).fetchone()
    conn.close()
    if emp is None:
        return {"error": "No such person."}
    return {"balance": {"casual": emp["casual_balance"], "sick": emp["sick_balance"]},
            "adjustments": adjustments,
            f"approved_leave_in_{year}": taken,
            "rules": f"Each year starts with {ALLOWANCE['casual']} casual and {ALLOWANCE['sick']} sick days. "
                     f"Up to {CASUAL_CARRY_MAX} unused casual days carry over; unused sick days lapse."}


# ---------- start of the year ----------

def _year_plan(year):
    # what the yearly reset would do for each person still employed
    import employees                         # imported here: employees.py imports tools too
    conn = db.get_conn()
    people = [dict(r) for r in conn.execute(
        "SELECT id, name, casual_balance, sick_balance FROM employees ORDER BY name")]
    conn.close()
    plan = []
    for p in people:
        if not employees.is_active(p["id"], on=date(year, 1, 1)):
            continue
        carry = min(p["casual_balance"], CASUAL_CARRY_MAX)
        plan.append({"id": p["id"], "name": p["name"],
                     "casual_lapsing": p["casual_balance"] - carry, "casual_carried": carry,
                     "sick_lapsing": p["sick_balance"],
                     "casual_after": carry + ALLOWANCE["casual"], "sick_after": ALLOWANCE["sick"]})
    return plan


def year_start_preview(year):
    plan = _year_plan(year)
    conn = db.get_conn()
    done = conn.execute("SELECT ran_by, ran_at FROM leave_runs WHERE key = ?", (f"year-start-{year}",)).fetchone()
    conn.close()
    return {"year": year, "people": len(plan), "already_done": dict(done) if done else None,
            "runs_on": f"{year}-01-01",
            "casual_days_carried": sum(p["casual_carried"] for p in plan),
            "casual_days_lapsing": sum(p["casual_lapsing"] for p in plan),
            "sick_days_lapsing": sum(p["sick_lapsing"] for p in plan),
            "people_losing_casual_days": sum(1 for p in plan if p["casual_lapsing"] > 0)}


def run_year_start(year, ran_by="system", today=None):
    # runs once per year, on or after 1 January (scheduled: python leave_jobs.py year-start)
    today = today or date.today()
    if ran_by != "system" and (err := _not_hr(ran_by)):
        return err
    if today < date(year, 1, 1):
        return {"error": f"The {year} reset can only run from 1 January {year}."}
    key = f"year-start-{year}"
    plan = _year_plan(year)
    conn = db.get_conn()
    try:
        if conn.execute("SELECT 1 FROM leave_runs WHERE key = ?", (key,)).fetchone():
            return {"error": f"The {year} reset has already run."}
        with conn:   # everyone or no one, and the run is recorded in the same transaction
            for p in plan:
                rows = []
                if p["casual_lapsing"]:
                    rows.append(("casual", -p["casual_lapsing"], "lapse",
                                 f"Unused casual days above the {CASUAL_CARRY_MAX}-day carry-over limit"))
                rows.append(("casual", ALLOWANCE["casual"], "year_start", f"{year} casual allowance"))
                if p["sick_lapsing"]:
                    rows.append(("sick", -p["sick_lapsing"], "lapse", "Unused sick days lapse at year end"))
                rows.append(("sick", ALLOWANCE["sick"], "year_start", f"{year} sick allowance"))
                for leave_type, days, kind, reason in rows:
                    conn.execute("""INSERT INTO leave_adjustments (emp_id, leave_type, days, kind, reason, changed_by)
                                    VALUES (?, ?, ?, ?, ?, ?)""", (p["id"], leave_type, days, kind, reason, ran_by))
                conn.execute("UPDATE employees SET casual_balance = ?, sick_balance = ? WHERE id = ?",
                             (p["casual_after"], p["sick_after"], p["id"]))
            conn.execute("INSERT INTO leave_runs (key, ran_by, people) VALUES (?, ?, ?)", (key, ran_by, len(plan)))
    finally:
        conn.close()
    return {"success": True, "year": year, "people": len(plan)}
