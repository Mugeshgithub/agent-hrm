# leave_admin.py: the HR side of leave. Public holidays, balance adjustments with a reason,
# the start-of-year reset (casual carry-over and new allowances, from hr_policy.md) and the monthly
# paid-leave (congés payés) accrual.
# Every balance change that isn't a leave request is written to leave_adjustments, so anyone can
# see why their balance moved. No AI here, so it's unit-tested in CI (leave_admin_tests.py).
import calendar
from datetime import date, timedelta

import db
import tools
from tools import PENDING, PAID, LEAVE_TYPES, get_employee, balance_of, clean_days

ALLOWANCE = {"casual": 12, "sick": 6}   # hr_policy.md: Casual leave, Sick leave
CASUAL_CARRY_MAX = 5                     # hr_policy.md: Carry forward (sick leave lapses)
MAX_ADJUSTMENT = 30                      # one adjustment can't move a balance by more than this

# paid leave (congés payés, hr_policy.md "Paid leave"): earned monthly, not given in January
PAID_PER_MONTH = 2.5                     # Code du travail L3141-3: 2.5 jours ouvrables per month worked
PAID_MONTH_MIN_DAYS = 28                 # L3141-4: four weeks of work count as a month
PAID_CARRY_MAX = None                    # None = unused paid days carry over in full. The policy sets no
                                         # limit, and losing days the employer never let someone take is risky

# ---------- holiday plans (like Keka's holiday calendars) ----------
# A plan is a list of holidays for a group of people, usually an office or region. Each holiday is
# "mandatory" (everyone is off; leave isn't counted) or "floater" (optional: each person may take up
# to the plan's floater_quota per year, with their manager's approval).

FRANCE = [   # jours fériés, the same everywhere in France
    ("2026-01-01", "New Year's Day"), ("2026-04-06", "Easter Monday"), ("2026-05-01", "Labour Day"),
    ("2026-05-08", "Victory in Europe Day"), ("2026-05-14", "Ascension Day"), ("2026-05-25", "Whit Monday"),
    ("2026-07-14", "Bastille Day"), ("2026-08-15", "Assumption Day"), ("2026-11-01", "All Saints' Day"),
    ("2026-11-11", "Armistice Day"), ("2026-12-25", "Christmas Day"),
    ("2027-01-01", "New Year's Day"), ("2027-03-29", "Easter Monday"), ("2027-05-01", "Labour Day"),
    ("2027-05-06", "Ascension Day"), ("2027-05-08", "Victory in Europe Day"), ("2027-05-17", "Whit Monday"),
    ("2027-07-14", "Bastille Day"), ("2027-08-15", "Assumption Day"), ("2027-11-01", "All Saints' Day"),
    ("2027-11-11", "Armistice Day"), ("2027-12-25", "Christmas Day"),
]
ALSACE_MOSELLE_EXTRA = [   # two extra public holidays in Bas-Rhin, Haut-Rhin and Moselle
    ("2026-04-03", "Good Friday"), ("2026-12-26", "St Stephen's Day"),
    ("2027-03-26", "Good Friday"), ("2027-12-26", "St Stephen's Day"),
]
COMPANY_FLOATERS = [   # optional days the company offers; people choose up to the plan's quota
    ("2026-12-24", "Christmas Eve"), ("2026-12-31", "New Year's Eve"),
    ("2027-01-06", "Epiphany"), ("2027-12-24", "Christmas Eve"), ("2027-12-31", "New Year's Eve"),
]
GOOD_FRIDAY_FLOATERS = [("2026-04-03", "Good Friday"), ("2027-03-26", "Good Friday")]
KINDS = ("mandatory", "floater")


def init_leave_admin():
    conn = db.get_conn()
    with conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS holiday_plans (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            name TEXT NOT NULL UNIQUE,
                            description TEXT NOT NULL DEFAULT '',
                            floater_quota INTEGER NOT NULL DEFAULT 0,   -- floating holidays per person per year
                            is_default INTEGER NOT NULL DEFAULT 0)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS holidays (
                            plan_id INTEGER NOT NULL REFERENCES holiday_plans(id),
                            day TEXT NOT NULL,
                            name TEXT NOT NULL,
                            kind TEXT NOT NULL DEFAULT 'mandatory',      -- mandatory or floater
                            PRIMARY KEY (plan_id, day))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS leave_adjustments (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            emp_id TEXT NOT NULL,
                            leave_type TEXT NOT NULL,
                            days REAL NOT NULL,             -- + adds to the balance, - takes away (paid leave comes in halves)
                            kind TEXT NOT NULL,             -- manual, year_start, lapse, accrual
                            reason TEXT NOT NULL,
                            changed_by TEXT NOT NULL,       -- an HR id, or 'system' for the yearly job
                            changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS leave_runs (
                            key TEXT PRIMARY KEY, ran_by TEXT NOT NULL, people INTEGER NOT NULL,
                            ran_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(profiles)")}
        if cols and "holiday_plan_id" not in cols:
            conn.execute("ALTER TABLE profiles ADD COLUMN holiday_plan_id INTEGER")
        if conn.execute("SELECT COUNT(*) FROM holiday_plans").fetchone()[0] == 0:
            _seed_plans(conn)
        conn.execute("DROP TABLE IF EXISTS public_holidays")   # the first version had one list for everyone
    conn.close()
    load_holidays()


def _seed_plans(conn):
    def plan(name, description, quota, default, rows):
        cur = conn.execute("INSERT INTO holiday_plans (name, description, floater_quota, is_default) VALUES (?, ?, ?, ?)",
                           (name, description, quota, default))
        conn.executemany("INSERT INTO holidays (plan_id, day, name, kind) VALUES (?, ?, ?, ?)",
                         [(cur.lastrowid, d, n, k) for d, n, k in rows])
        return cur.lastrowid
    m = lambda rows: [(d, n, "mandatory") for d, n in rows]
    f = lambda rows: [(d, n, "floater") for d, n in rows]
    plan("France", "French public holidays. For every office except Strasbourg.", 2, 1,
         m(FRANCE) + f(COMPANY_FLOATERS + GOOD_FRIDAY_FLOATERS))
    alsace = plan("Alsace-Moselle", "French public holidays plus Good Friday and St Stephen's Day. For the Strasbourg office.",
                  2, 0, m(FRANCE + ALSACE_MOSELLE_EXTRA) + f(COMPANY_FLOATERS))
    conn.execute("UPDATE profiles SET holiday_plan_id = ? WHERE location = 'Strasbourg'", (alsace,))


def _default_plan_id(conn):
    return conn.execute("SELECT id FROM holiday_plans WHERE is_default = 1").fetchone()["id"]


def plan_id_of(emp_id, conn=None):
    own = conn is None
    conn = conn or db.get_conn()
    row = conn.execute("SELECT holiday_plan_id FROM profiles WHERE emp_id = ?", (emp_id,)).fetchone()
    pid = row["holiday_plan_id"] if row and row["holiday_plan_id"] else _default_plan_id(conn)
    if own:
        conn.close()
    return pid


def _mandatory_days(conn, plan_id):
    return {date.fromisoformat(r["day"]) for r in conn.execute(
        "SELECT day FROM holidays WHERE plan_id = ? AND kind = 'mandatory'", (plan_id,))}


def holidays_for(emp_id):
    conn = db.get_conn()
    days = _mandatory_days(conn, plan_id_of(emp_id, conn))
    conn.close()
    return days


def load_holidays():
    # tools.HOLIDAYS (the default plan) is used for payslip dates; tools.HOLIDAYS_FOR gives each
    # person their own plan, so leave is always counted with that person's holidays
    conn = db.get_conn()
    days = _mandatory_days(conn, _default_plan_id(conn))
    conn.close()
    tools.HOLIDAYS.clear()
    tools.HOLIDAYS.update(days)
    tools.HOLIDAYS_FOR = holidays_for


def _not_hr(hr_id):
    staff = get_employee(hr_id)
    if staff is None or staff["role"] != "hr":
        return {"error": "Only HR can do this."}
    return None


def _plan(conn, plan_id):
    row = conn.execute("SELECT * FROM holiday_plans WHERE id = ?", (plan_id,)).fetchone()
    return dict(row) if row else None


def list_plans():
    conn = db.get_conn()
    default = _default_plan_id(conn)
    plans = [dict(r) for r in conn.execute("SELECT * FROM holiday_plans ORDER BY is_default DESC, name")]
    for p in plans:
        p["people"] = conn.execute(
            """SELECT COUNT(*) FROM profiles WHERE (end_date IS NULL OR end_date >= date('now', 'localtime'))
               AND COALESCE(holiday_plan_id, ?) = ?""", (default, p["id"])).fetchone()[0]
        p["holidays"] = conn.execute("SELECT COUNT(*) FROM holidays WHERE plan_id = ?", (p["id"],)).fetchone()[0]
        p["is_default"] = bool(p["is_default"])
    conn.close()
    return plans


def plan_holidays(plan_id, year=None):
    conn = db.get_conn()
    plan = _plan(conn, plan_id)
    if plan is None:
        conn.close()
        return {"error": "No such holiday plan."}
    sql, args = "SELECT day, name, kind FROM holidays WHERE plan_id = ?", [plan_id]
    if year:
        sql, args = sql + " AND substr(day, 1, 4) = ?", args + [str(year)]
    rows = [dict(r) for r in conn.execute(sql + " ORDER BY day", args)]
    taken = {r["day"]: r["n"] for r in conn.execute(
        """SELECT start_date AS day, COUNT(*) AS n FROM leave_requests
           WHERE leave_type = ? AND status IN (?, 'approved') GROUP BY start_date""", (tools.FLOATER, PENDING))}
    conn.close()
    today = date.today().isoformat()
    for r in rows:
        r["weekend"] = date.fromisoformat(r["day"]).weekday() >= 5
        r["past"] = r["day"] < today
        r["floaters_taken"] = taken.get(r["day"], 0) if r["kind"] == "floater" else 0
    years = sorted({r["day"][:4] for r in rows})
    return {"plan": {**plan, "is_default": bool(plan["is_default"])}, "holidays": rows, "years": years}


def create_plan(hr_id, name, description="", floater_quota=0, copy_from=None):
    if (err := _not_hr(hr_id)):
        return err
    name = (name or "").strip()
    if not name:
        return {"error": "Give the plan a name, for example the office or region it's for."}
    if not 0 <= int(floater_quota) <= 10:
        return {"error": "Floating holidays per year must be between 0 and 10."}
    conn = db.get_conn()
    try:
        if conn.execute("SELECT 1 FROM holiday_plans WHERE lower(name) = lower(?)", (name,)).fetchone():
            return {"error": "A plan with that name already exists."}
        if copy_from and _plan(conn, copy_from) is None:
            return {"error": "The plan to copy from doesn't exist."}
        with conn:
            cur = conn.execute("INSERT INTO holiday_plans (name, description, floater_quota) VALUES (?, ?, ?)",
                               (name, (description or "").strip(), int(floater_quota)))
            if copy_from:
                conn.execute("""INSERT INTO holidays (plan_id, day, name, kind)
                                SELECT ?, day, name, kind FROM holidays WHERE plan_id = ?""", (cur.lastrowid, copy_from))
    finally:
        conn.close()
    return {"success": True, "id": cur.lastrowid, "name": name}


def update_plan(hr_id, plan_id, name=None, description=None, floater_quota=None):
    if (err := _not_hr(hr_id)):
        return err
    conn = db.get_conn()
    try:
        plan = _plan(conn, plan_id)
        if plan is None:
            return {"error": "No such holiday plan."}
        new_name = (name if name is not None else plan["name"]).strip()
        if not new_name:
            return {"error": "The name can't be empty."}
        if conn.execute("SELECT 1 FROM holiday_plans WHERE lower(name) = lower(?) AND id != ?", (new_name, plan_id)).fetchone():
            return {"error": "A plan with that name already exists."}
        quota = plan["floater_quota"] if floater_quota is None else int(floater_quota)
        if not 0 <= quota <= 10:
            return {"error": "Floating holidays per year must be between 0 and 10."}
        with conn:
            conn.execute("UPDATE holiday_plans SET name = ?, description = ?, floater_quota = ? WHERE id = ?",
                         (new_name, plan["description"] if description is None else description.strip(), quota, plan_id))
    finally:
        conn.close()
    return {"success": True, "id": plan_id}


def delete_plan(hr_id, plan_id):
    if (err := _not_hr(hr_id)):
        return err
    plan = next((p for p in list_plans() if p["id"] == plan_id), None)
    if plan is None:
        return {"error": "No such holiday plan."}
    if plan["is_default"]:
        return {"error": "The default plan can't be deleted. People without a plan use it."}
    if plan["people"]:
        return {"error": f"{plan['people']} {'person uses' if plan['people'] == 1 else 'people use'} this plan. "
                         f"Move them to another plan first."}
    conn = db.get_conn()
    with conn:
        conn.execute("DELETE FROM holidays WHERE plan_id = ?", (plan_id,))
        conn.execute("UPDATE profiles SET holiday_plan_id = NULL WHERE holiday_plan_id = ?", (plan_id,))   # people who left
        conn.execute("DELETE FROM holiday_plans WHERE id = ?", (plan_id,))
    conn.close()
    return {"success": True}


def _check_holiday(conn, plan_id, day, name, kind):
    try:
        d = date.fromisoformat(day)
    except (TypeError, ValueError):
        return None, "Use a real date (YYYY-MM-DD)."
    if not (name or "").strip():
        return None, "Give the holiday a name."
    if kind not in KINDS:
        return None, "Choose mandatory or floater."
    if d < date.today():
        return None, "That date has passed. Past holidays can't be added or changed."
    if conn.execute("SELECT 1 FROM holidays WHERE plan_id = ? AND day = ?", (plan_id, d.isoformat())).fetchone():
        return None, "That date is already in this plan."
    return d, None


def add_holiday(hr_id, plan_id, day, name, kind="mandatory"):
    if (err := _not_hr(hr_id)):
        return err
    conn = db.get_conn()
    try:
        if _plan(conn, plan_id) is None:
            return {"error": "No such holiday plan."}
        d, problem = _check_holiday(conn, plan_id, day, name, kind)
        if problem:
            return {"error": problem}
        with conn:
            conn.execute("INSERT INTO holidays (plan_id, day, name, kind) VALUES (?, ?, ?, ?)",
                         (plan_id, d.isoformat(), name.strip(), kind))
    finally:
        conn.close()
    load_holidays()
    note = ("It falls on a weekend, so it doesn't change anyone's working days." if d.weekday() >= 5 else
            "Requests already sent keep the days they were counted with.")
    return {"success": True, "day": d.isoformat(), "name": name.strip(), "kind": kind, "note": note}


def _floaters_booked(conn, plan_id, day):
    # pending or approved floating-holiday requests on this day, by people on this plan
    default = _default_plan_id(conn)
    return conn.execute(
        """SELECT COUNT(*) FROM leave_requests r JOIN profiles p ON p.emp_id = r.employee_id
           WHERE r.leave_type = ? AND r.status IN (?, 'approved') AND r.start_date = ?
             AND COALESCE(p.holiday_plan_id, ?) = ?""", (tools.FLOATER, PENDING, day, default, plan_id)).fetchone()[0]


def change_holiday_kind(hr_id, plan_id, day, kind):
    if (err := _not_hr(hr_id)):
        return err
    if kind not in KINDS:
        return {"error": "Choose mandatory or floater."}
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT kind FROM holidays WHERE plan_id = ? AND day = ?", (plan_id, day)).fetchone()
        if row is None:
            return {"error": "That date isn't in this plan."}
        if day < date.today().isoformat():
            return {"error": "That date has passed. Past holidays can't be changed."}
        if row["kind"] == kind:
            return {"error": f"It's already {kind}."}
        if row["kind"] == "floater" and (n := _floaters_booked(conn, plan_id, day)):
            return {"error": f"{n} {'person has' if n == 1 else 'people have'} chosen this floating holiday. "
                             f"It can't become mandatory until those requests are withdrawn or rejected."}
        with conn:
            conn.execute("UPDATE holidays SET kind = ? WHERE plan_id = ? AND day = ?", (kind, plan_id, day))
    finally:
        conn.close()
    load_holidays()
    return {"success": True, "day": day, "kind": kind}


def remove_holiday(hr_id, plan_id, day):
    if (err := _not_hr(hr_id)):
        return err
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT kind FROM holidays WHERE plan_id = ? AND day = ?", (plan_id, day)).fetchone()
        if row is None:
            return {"error": "That date isn't in this plan."}
        if day < date.today().isoformat():
            return {"error": "That date has passed. Past holidays stay in the plan as a record."}
        if row["kind"] == "floater" and (n := _floaters_booked(conn, plan_id, day)):
            return {"error": f"{n} {'person has' if n == 1 else 'people have'} chosen this floating holiday. "
                             f"Those requests need to be withdrawn or rejected first."}
        with conn:
            conn.execute("DELETE FROM holidays WHERE plan_id = ? AND day = ?", (plan_id, day))
    finally:
        conn.close()
    load_holidays()
    return {"success": True, "day": day}


def import_holidays(hr_id, plan_id, csv_text, dry_run=True):
    # CSV with a header row: date,name,kind  (kind is optional: mandatory or floater)
    # dry_run=True only checks every line, so HR sees what will happen before anything is saved
    import csv
    import io
    if (err := _not_hr(hr_id)):
        return err
    conn = db.get_conn()
    try:
        if _plan(conn, plan_id) is None:
            return {"error": "No such holiday plan."}
        reader = csv.DictReader(io.StringIO((csv_text or "").strip()))
        fields = {(f or "").strip().lower() for f in (reader.fieldnames or [])}
        if not {"date", "name"} <= fields:
            return {"error": "The first line must be the column names: date,name,kind"}
        rows, seen = [], set()
        for n, raw in enumerate(reader, start=2):
            line = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            kind = (line.get("kind") or "mandatory").lower()
            kind = "floater" if kind in ("floater", "floating", "optional", "yes") else                    "mandatory" if kind in ("mandatory", "no", "") else kind
            d, problem = _check_holiday(conn, plan_id, line.get("date"), line.get("name"), kind)
            if not problem and d.isoformat() in seen:
                problem = "This date appears twice in the file."
            if d:
                seen.add(d.isoformat())
            rows.append({"line": n, "date": line.get("date"), "name": line.get("name"), "kind": kind,
                         "ok": problem is None, "problem": problem})
        if not rows:
            return {"error": "The file has no holidays in it."}
        good = [r for r in rows if r["ok"]]
        if not dry_run and good:
            with conn:
                conn.executemany("INSERT INTO holidays (plan_id, day, name, kind) VALUES (?, ?, ?, ?)",
                                 [(plan_id, r["date"], r["name"], r["kind"]) for r in good])
    finally:
        conn.close()
    if not dry_run:
        load_holidays()
    return {"dry_run": dry_run, "rows": rows, "will_add" if dry_run else "added": len(good),
            "skipped": len(rows) - len(good)}


def assign_plan(hr_id, plan_id, emp_ids=None, location=None):
    # move people onto a plan: one by one from their profile, or a whole office at once
    if (err := _not_hr(hr_id)):
        return err
    import employees
    conn = db.get_conn()
    try:
        plan = _plan(conn, plan_id)
        if plan is None:
            return {"error": "No such holiday plan."}
        if location:
            ids = [r["emp_id"] for r in conn.execute("SELECT emp_id FROM profiles WHERE location = ?", (location,))]
        else:
            ids = list(emp_ids or [])
        ids = [i for i in ids if employees.is_active(i)]
        if not ids:
            return {"error": "No current employees to move."}
        default = _default_plan_id(conn)
        names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM holiday_plans")}
        moved = 0
        with conn:   # each move is logged in the person's change history
            for emp_id in ids:
                old = conn.execute("SELECT COALESCE(holiday_plan_id, ?) AS p FROM profiles WHERE emp_id = ?",
                                   (default, emp_id)).fetchone()
                if old is None or old["p"] == plan_id:
                    continue
                conn.execute("UPDATE profiles SET holiday_plan_id = ? WHERE emp_id = ?", (plan_id, emp_id))
                conn.execute("""INSERT INTO employee_changes (emp_id, changed_by, field, old_value, new_value, reason)
                                VALUES (?, ?, 'holiday_plan', ?, ?, ?)""",
                             (emp_id, hr_id, names[old["p"]], plan["name"],
                              f"Moved with the {location} office" if location else "Holiday plan changed"))
                moved += 1
    finally:
        conn.close()
    return {"success": True, "plan": plan["name"], "moved": moved, "already_on_plan": len(ids) - moved}


# ---------- floating holidays (employees choose) ----------

def my_holidays(emp_id, year=None):
    # the person's own plan: holidays, and how many floating holidays they've used this year
    year = year or date.today().year
    conn = db.get_conn()
    pid = plan_id_of(emp_id, conn)
    plan = _plan(conn, pid)
    rows = [dict(r) for r in conn.execute(
        "SELECT day, name, kind FROM holidays WHERE plan_id = ? AND substr(day, 1, 4) = ? ORDER BY day", (pid, str(year)))]
    mine = {r["start_date"]: dict(r) for r in conn.execute(
        """SELECT id, start_date, status FROM leave_requests WHERE employee_id = ? AND leave_type = ?
           AND status IN (?, 'approved', 'rejected')""", (emp_id, tools.FLOATER, PENDING))}
    years = sorted({r["day"][:4] for r in conn.execute("SELECT day FROM holidays WHERE plan_id = ?", (pid,))})
    conn.close()
    used = sum(1 for r in mine.values() if r["start_date"][:4] == str(year) and r["status"] in (PENDING, "approved"))
    today = date.today().isoformat()
    for r in rows:
        r["weekend"] = date.fromisoformat(r["day"]).weekday() >= 5
        r["past"] = r["day"] < today
        if r["kind"] == "floater" and r["day"] in mine:
            r["my_request"] = {"id": mine[r["day"]]["id"], "status": mine[r["day"]]["status"]}
    return {"plan": {"id": plan["id"], "name": plan["name"], "description": plan["description"]},
            "year": year, "years": years, "holidays": rows,
            "floaters": {"quota": plan["floater_quota"], "used": used, "left": max(plan["floater_quota"] - used, 0)}}


def request_floater(emp_id, day):
    emp = get_employee(emp_id)
    if emp is None:
        return {"error": "No such person."}
    if emp["manager_id"] is None:
        return {"error": "No manager is set up to approve your leave. Please contact HR."}
    try:
        d = date.fromisoformat(day)
    except (TypeError, ValueError):
        return {"error": "Use a real date."}
    conn = db.get_conn()
    try:
        pid = plan_id_of(emp_id, conn)
        plan = _plan(conn, pid)
        hol = conn.execute("SELECT name, kind FROM holidays WHERE plan_id = ? AND day = ?", (pid, d.isoformat())).fetchone()
        if hol is None or hol["kind"] != "floater":
            return {"error": f"That day isn't a floating holiday in your plan ({plan['name']})."}
        if d <= date.today():
            return {"error": "Choose a floating holiday that's still to come."}
        if d.weekday() >= 5:
            return {"error": "That day is on a weekend, so you don't need to take it."}
        used = conn.execute("""SELECT COUNT(*) FROM leave_requests WHERE employee_id = ? AND leave_type = ?
                               AND status IN (?, 'approved') AND substr(start_date, 1, 4) = ?""",
                            (emp_id, tools.FLOATER, PENDING, str(d.year))).fetchone()[0]
        if used >= plan["floater_quota"]:
            return {"error": f"You've already used your {plan['floater_quota']} floating "
                             f"{'holiday' if plan['floater_quota'] == 1 else 'holidays'} for {d.year}. "
                             f"Withdraw one to choose another."}
        clash = conn.execute("""SELECT leave_type FROM leave_requests WHERE employee_id = ? AND status IN (?, 'approved')
                                AND start_date <= ? AND end_date >= ?""", (emp_id, PENDING, d.isoformat(), d.isoformat())).fetchone()
        if clash:
            return {"error": "You already have leave on that day."}
        with conn:
            cur = conn.execute("""INSERT INTO leave_requests (employee_id, manager_id, leave_type, start_date, end_date, days, status)
                                  VALUES (?, ?, ?, ?, ?, 1, ?)""",
                               (emp_id, emp["manager_id"], tools.FLOATER, d.isoformat(), d.isoformat(), PENDING))
    finally:
        conn.close()
    return {"success": True, "request_id": cur.lastrowid, "day": d.isoformat(), "holiday": hol["name"],
            "status": PENDING, "floaters_left": plan["floater_quota"] - used - 1}


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
        return {"error": "Choose casual, sick or paid leave."}
    try:
        days = float(days)
    except (TypeError, ValueError):
        return {"error": "Give a number of days, like 2 or -1."}
    step = 0.5 if leave_type == PAID else 1       # paid leave is earned 2.5 at a time
    if days % step:
        return {"error": "Give a whole number of days, like 2 or -1." if step == 1
                         else "Paid leave moves in half days, like 2.5 or -1."}
    days = clean_days(days)
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
            return {"error": f"{emp['name']} has {clean_days(pending)} {leave_type} "
                             f"{'day' if pending == 1 else 'days'} waiting for approval, so the balance "
                             f"can't go below {pending}."}
        with conn:   # the balance and its history line change together, or not at all
            conn.execute(f"UPDATE employees SET {column} = {column} + ? WHERE id = ?", (days, emp_id))
            conn.execute("""INSERT INTO leave_adjustments (emp_id, leave_type, days, kind, reason, changed_by)
                            VALUES (?, ?, ?, 'manual', ?, ?)""", (emp_id, leave_type, days, reason.strip(), hr_id))
    finally:
        conn.close()
    return {"success": True, "id": emp_id, "leave_type": leave_type, "change": days,
            "balance_before": clean_days(current), "balance_after": clean_days(current + days)}


def balance_history(emp_id, year=None):
    # why the balance is what it is: HR adjustments and the yearly reset, plus leave taken
    year = year or date.today().year
    conn = db.get_conn()
    adjustments = [dict(r) for r in conn.execute(
        """SELECT a.leave_type, a.days, a.kind, a.reason, a.changed_at,
                  COALESCE(e.name, 'Automatic update') AS changed_by
           FROM leave_adjustments a LEFT JOIN employees e ON e.id = a.changed_by
           WHERE a.emp_id = ? ORDER BY a.id DESC""", (emp_id,))]
    taken = [dict(r) for r in conn.execute(
        """SELECT leave_type, start_date, end_date, days FROM leave_requests
           WHERE employee_id = ? AND status = 'approved' AND substr(start_date, 1, 4) = ?
           ORDER BY start_date DESC""", (emp_id, str(year)))]
    emp = conn.execute("SELECT * FROM employees WHERE id = ?", (emp_id,)).fetchone()
    conn.close()
    if emp is None:
        return {"error": "No such person."}
    return {"balance": balance_of(emp),
            "adjustments": [{**a, "days": clean_days(a["days"])} for a in adjustments],
            f"approved_leave_in_{year}": taken,
            "rules": f"Each year starts with {ALLOWANCE['casual']} casual and {ALLOWANCE['sick']} sick days. "
                     f"Up to {CASUAL_CARRY_MAX} unused casual days carry over; unused sick days lapse. "
                     f"Paid leave is earned at {PAID_PER_MONTH} days a month and unused days carry over."}


# ---------- start of the year ----------

def _year_plan(year):
    # what the yearly reset would do for each person still employed
    import employees                         # imported here: employees.py imports tools too
    conn = db.get_conn()
    people = [dict(r) for r in conn.execute(
        "SELECT id, name, casual_balance, sick_balance, paid_balance FROM employees ORDER BY name")]
    conn.close()
    plan = []
    for p in people:
        if not employees.is_active(p["id"], on=date(year, 1, 1)):
            continue
        carry = min(p["casual_balance"], CASUAL_CARRY_MAX)
        paid_carry = p["paid_balance"] if PAID_CARRY_MAX is None else min(p["paid_balance"], PAID_CARRY_MAX)
        plan.append({"id": p["id"], "name": p["name"],
                     "paid_carried": paid_carry, "paid_lapsing": p["paid_balance"] - paid_carry,
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
            "paid_days_carried": clean_days(sum(p["paid_carried"] for p in plan)),
            "paid_days_lapsing": clean_days(sum(p["paid_lapsing"] for p in plan)),
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
                # paid leave has no January allowance (it is earned monthly): it only carries over,
                # and only the part above PAID_CARRY_MAX (if the company ever sets one) lapses
                if p["paid_lapsing"]:
                    rows.append((PAID, -p["paid_lapsing"], "lapse",
                                 f"Unused paid days above the {PAID_CARRY_MAX}-day carry-over limit"))
                for leave_type, days, kind, reason in rows:
                    conn.execute("""INSERT INTO leave_adjustments (emp_id, leave_type, days, kind, reason, changed_by)
                                    VALUES (?, ?, ?, ?, ?, ?)""", (p["id"], leave_type, days, kind, reason, ran_by))
                conn.execute("UPDATE employees SET casual_balance = ?, sick_balance = ?, paid_balance = ? WHERE id = ?",
                             (p["casual_after"], p["sick_after"], p["paid_carried"], p["id"]))
            conn.execute("INSERT INTO leave_runs (key, ran_by, people) VALUES (?, ?, ?)", (key, ran_by, len(plan)))
    finally:
        conn.close()
    return {"success": True, "year": year, "people": len(plan)}


# ---------- paid leave: earned every month ----------

def _month_label(year, month):
    return date(year, month, 1).strftime("%B %Y")


def _days_employed_in_month(start, end, year, month):
    # start and end are the person's first and last day (end None = still here), as ISO text
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    if start:
        first = max(first, date.fromisoformat(start))
    if end:
        last = min(last, date.fromisoformat(end))
    return max(0, (last - first).days + 1)


def _accrual_plan(year, month):
    # who earns this month. L3141-4: four weeks of work count as a month, so someone who joined or
    # left part way through earns it only if they were employed for at least 28 days of the month.
    conn = db.get_conn()
    people = [dict(r) for r in conn.execute(
        """SELECT e.id, e.name, e.paid_balance, p.start_date, p.end_date
           FROM employees e LEFT JOIN profiles p ON p.emp_id = e.id ORDER BY e.name""")]
    conn.close()
    plan = []
    for p in people:
        worked = _days_employed_in_month(p["start_date"], p["end_date"], year, month)
        plan.append({"id": p["id"], "name": p["name"], "days_employed": worked,
                     "earns": worked >= PAID_MONTH_MIN_DAYS, "paid_before": p["paid_balance"]})
    return plan


def month_accrual_preview(year, month):
    plan = _accrual_plan(year, month)
    conn = db.get_conn()
    done = conn.execute("SELECT ran_by, ran_at FROM leave_runs WHERE key = ?",
                        (f"accrual-{year}-{month:02d}",)).fetchone()
    conn.close()
    earners = [p for p in plan if p["earns"]]
    return {"month": _month_label(year, month), "people_earning": len(earners),
            "days_each": PAID_PER_MONTH, "days_in_total": clean_days(len(earners) * PAID_PER_MONTH),
            "not_earning": [p["name"] for p in plan if not p["earns"]],
            "already_done": dict(done) if done else None}


def run_month_accrual(year, month, ran_by="system", today=None):
    # credits 2.5 paid days to everyone who earned the month; once per month, from the month's
    # last day (scheduled: python leave_jobs.py month-end)
    today = today or date.today()
    if ran_by != "system" and (err := _not_hr(ran_by)):
        return err
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    if today < last_day:
        return {"error": f"{_month_label(year, month)} can only be credited from {last_day.isoformat()}."}
    key = f"accrual-{year}-{month:02d}"
    plan = [p for p in _accrual_plan(year, month) if p["earns"]]
    conn = db.get_conn()
    try:
        if conn.execute("SELECT 1 FROM leave_runs WHERE key = ?", (key,)).fetchone():
            return {"error": f"{_month_label(year, month)} has already been credited."}
        with conn:   # everyone or no one, and the run is recorded in the same transaction
            for p in plan:
                conn.execute("UPDATE employees SET paid_balance = paid_balance + ? WHERE id = ?",
                             (PAID_PER_MONTH, p["id"]))
                conn.execute("""INSERT INTO leave_adjustments (emp_id, leave_type, days, kind, reason, changed_by)
                                VALUES (?, ?, ?, 'accrual', ?, ?)""",
                             (p["id"], PAID, PAID_PER_MONTH,
                              f"Paid leave earned in {_month_label(year, month)}", ran_by))
            conn.execute("INSERT INTO leave_runs (key, ran_by, people) VALUES (?, ?, ?)", (key, ran_by, len(plan)))
    finally:
        conn.close()
    return {"success": True, "month": _month_label(year, month), "people": len(plan),
            "days_each": PAID_PER_MONTH}


def last_finished_month(today=None):
    # the month the scheduled job credits: the one that ended most recently
    today = today or date.today()
    last = date(today.year, today.month, 1) - timedelta(days=1)
    return last.year, last.month
