# tools.py
import calendar
import os
import re
from datetime import date, timedelta
from db import get_conn, init_db, ticket_number
import app_clock
import policy_store

# public holidays of the default plan; leave_admin.load_holidays() refills this set from the database
HOLIDAYS = {date(2026, 11, 11), date(2026, 12, 25)}
HOLIDAYS_FOR = None        # set by leave_admin: employee id -> that person's holidays (their holiday plan)
PAID = "paid"                      # congés payés: earned 2.5 days a month, counted Monday to Saturday
LEAVE_TYPES = ("casual", "sick", PAID)   # leave with a balance
FLOATER = "floater"                # a floating holiday the employee picks; counted against a yearly quota
PENDING = "pending_manager_approval"


def paid_policy():
    """((month, day), (month, day)) of the usual paid-leave window, and the note shown with paid leave. Both come from the current policy
    text (policy_store.py, which HR edits in the Handbook), so that text is the only place they are written. The editor refuses a save that
    would leave no such sentences, so this cannot fail on an edited policy."""
    return policy_store.paid_policy()




def get_employee(employee_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM employees WHERE id = ?", (employee_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def clean_days(n):
    # paid leave comes in halves (2.5 a month): show 12 instead of 12.0, but keep 7.5
    n = round(n, 2)                   # pro-rated paid leave has up to 2 decimals; no 2.3399999
    return int(n) if n == int(n) else n


def balance_of(emp):
    # the three balances of one employee row, the same shape everywhere
    return {kind: clean_days(emp[f"{kind}_balance"]) for kind in LEAVE_TYPES}


def paid_policy_note(start, end):
    # the note to attach when paid leave falls, even partly, outside the usual window; None if inside it
    window, note = paid_policy()
    def inside(d):
        return window[0] <= (d.month, d.day) <= window[1]
    if start.year != end.year or not (inside(start) and inside(end)):
        return note
    return None


def pending_days_by_type(employee_id):
    # days already requested and waiting for approval, per leave type that has a balance
    conn = get_conn()
    rows = conn.execute(
        "SELECT leave_type, SUM(days) FROM leave_requests WHERE employee_id = ? AND status = ? GROUP BY leave_type",
        (employee_id, PENDING)).fetchall()
    conn.close()
    return {r[0]: clean_days(r[1]) for r in rows if r[0] in LEAVE_TYPES}


def days_figures(employee_id, leave_type, days):
    """The figures that go with a number of days off, so the reply never works them out: the days available (the balance minus what already
    waits for approval), and then how many would be left, or how many short."""
    emp = get_employee(employee_id)
    available = clean_days(emp[f"{leave_type}_balance"] - pending_days_by_type(employee_id).get(leave_type, 0))
    return {"available": available, ("short_by" if days > available else "available_after"): clean_days(abs(available - days))}


def get_leave_balance(employee_id, leave_type=None):
    emp = get_employee(employee_id)
    if emp is None:
        return {"error": "Employee not found"}
    result = {
        "name": emp["name"],
        "leave_balance": balance_of(emp),
    }
    # the figure a person asks for next ("how many days will I have once my request is approved?") comes from here, not from arithmetic
    if pending := pending_days_by_type(employee_id):
        result["pending_days"] = pending
        result["available_after_pending"] = {kind: clean_days(emp[f"{kind}_balance"] - days) for kind, days in pending.items()}
    if leave_type == PAID:       # asked about paid leave: no dates yet, so always remind of the usual window
        result["policy_note"] = paid_policy()[1]
    return result


def holidays_of(employee_id):
    # each person's public holidays come from their holiday plan (default plan if none)
    return HOLIDAYS_FOR(employee_id) if HOLIDAYS_FOR and employee_id else HOLIDAYS


def count_working_days(start, end, holidays=None):
    holidays = HOLIDAYS if holidays is None else holidays
    days = 0
    current = start
    while current <= end:
        if current.weekday() < 5 and current not in holidays:
            days += 1
        current += timedelta(days=1)
    return days


def count_ouvrable_days(start, end, holidays=None):
    # jours ouvrables, how paid leave is counted (hr_policy.md): Monday to Saturday, so a week off
    # is 6 days. Sundays and public holidays are not counted.
    holidays = HOLIDAYS if holidays is None else holidays
    days = 0
    current = start
    while current <= end:
        if current.weekday() < 6 and current not in holidays:
            days += 1
        current += timedelta(days=1)
    return days


def days_for(leave_type, start, end, holidays=None):
    # how many days a request for this kind of leave uses
    counter = count_ouvrable_days if leave_type == PAID else count_working_days
    return counter(start, end, holidays)


def _day_label(d):
    return f"{d:%A} {d.day} {d:%B}"          # Saturday 21 November


def today_line(today=None):
    """The date line of the prompt, built on every call: the weekday is part of it (the model gets it wrong when it has to work it out from 2026-11-10)."""
    today = today or app_clock.today()
    return f"Today is {today:%A} {today.isoformat()}."


def no_days_reason(leave_type, start, end, holidays=None):
    """Why these dates use no days of this leave, worded for the person, or None when they use some. Weekend days and public holidays are not
    counted (paid leave counts Saturdays), so a request for only those is impossible: it is refused BEFORE the confirmation card is shown."""
    holidays = HOLIDAYS if holidays is None else holidays
    if days_for(leave_type, start, end, holidays):
        return None
    counts = "Monday to Saturday" if leave_type == PAID else "Monday to Friday"
    if start == end:
        what = (f"{_day_label(start)} is a public holiday" if start in holidays
                else f"{_day_label(start)} is not a working day ({leave_type} leave counts {counts})")
    else:
        what = f"every day from {_day_label(start)} to {_day_label(end)} is a weekend day or a public holiday ({leave_type} leave counts {counts})"
    return f"{what}, so it uses no days and cannot be booked."


def when_label(start, end=None):
    """Dates with their weekdays, for a card or a reply: 'Fri 20 Nov 2026', or 'Mon 23 Nov to Tue 24 Nov 2026' for a range."""
    start = date.fromisoformat(start) if isinstance(start, str) else start
    end = start if end is None else (date.fromisoformat(end) if isinstance(end, str) else end)

    def one(d, year=True):
        return f"{d:%a} {d.day} {d:%b}" + (f" {d.year}" if year else "")
    if start == end:
        return one(start)
    return f"{one(start, year=start.year != end.year)} to {one(end)}"


def request_summary(request_id):
    """One leave request as a card or a reply shows it: who, which kind of leave, the dates with their weekdays, the days. None if it does not exist."""
    conn = get_conn()
    try:
        row = conn.execute("""SELECT r.*, e.name AS employee_name FROM leave_requests r
                              JOIN employees e ON e.id = r.employee_id WHERE r.id = ?""", (request_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"request_id": row["id"], "employee_name": row["employee_name"], "leave_type": row["leave_type"], "start_date": row["start_date"],
            "end_date": row["end_date"], "days": clean_days(row["days"]), "status": row["status"], "when": when_label(row["start_date"], row["end_date"])}


def existing_request(employee_id, start_date, end_date):
    """The pending or approved request that already covers any of these dates (ISO strings), or None: a cancelled or rejected one does not count.
    apply_leave refuses with it, and so does the confirmation card BEFORE it is shown (a test keeps the two in step)."""
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT id, leave_type, status, start_date, end_date FROM leave_requests
               WHERE employee_id = ? AND status IN (?, 'approved')
                 AND start_date <= ? AND end_date >= ?
               ORDER BY id""",
            (employee_id, PENDING, end_date, start_date),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def already_requested(row):
    """An existing request, as the tools report it: what the reply may quote (its id, kind, status and dates, and nothing else)."""
    return {"request_id": row["id"], "leave_type": row["leave_type"], "status": row["status"],
            "when": when_label(row["start_date"], row["end_date"])}


def apply_leave(employee_id, leave_type, start_date, end_date):
    emp = get_employee(employee_id)
    if emp is None:
        return {"error": "Employee not found"}
    if emp["manager_id"] is None:
        return {"error": "No manager is set up to approve your leave. Please contact HR."}
    if leave_type not in LEAVE_TYPES:
        return {"error": f"Unknown leave type '{leave_type}'"}

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        return {"error": "End date is before start date"}
    if start < app_clock.today():
        return {"error": "Start date is in the past"}

    conn = get_conn()
    try:
        # block duplicates: no overlap with an existing pending or approved request
        overlap = existing_request(employee_id, start.isoformat(), end.isoformat())
        if overlap:
            return {"error": f"You already have request {overlap['id']} ({overlap['status']}) "
                             f"covering {overlap['start_date']} to {overlap['end_date']}. "
                             f"No new request was created."}

        days = days_for(leave_type, start, end, holidays_of(employee_id))
        if days == 0:
            return {"error": f"{no_days_reason(leave_type, start, end, holidays_of(employee_id))} Nothing was submitted."}

        # days already requested but not yet approved also count against the balance
        pending_days = conn.execute(
            """SELECT COALESCE(SUM(days), 0) FROM leave_requests
               WHERE employee_id = ? AND leave_type = ? AND status = ?""",
            (employee_id, leave_type, PENDING),
        ).fetchone()[0]
        balance = clean_days(emp[f"{leave_type}_balance"] - pending_days)
        if days > balance:
            return {"error": f"Not enough {leave_type} leave: requested {days}, available {balance}. "
                             f"Saved balance is {clean_days(emp[f'{leave_type}_balance'])}, but {pending_days} days "
                             f"are already in pending requests waiting for approval.",
                    "short_by": clean_days(days - balance)}

        with conn:  # saves (commits) only if everything inside succeeds
            cur = conn.execute(
                """INSERT INTO leave_requests
                   (employee_id, manager_id, leave_type, start_date, end_date, days, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (employee_id, emp["manager_id"], leave_type,
                 start.isoformat(), end.isoformat(), days, PENDING),
            )
        return {
            "success": True,
            "request": {
                "id": cur.lastrowid,
                "leave_type": leave_type,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "days": days,
                "status": PENDING,
            },
            "current_balance": balance_of(emp),
            "balance_after_approval": {leave_type: clean_days(balance - days)},
            **({"policy_note": note} if leave_type == PAID and (note := paid_policy_note(start, end)) else {}),
        }
    finally:
        conn.close()


def get_my_requests(employee_id):
    conn = get_conn()
    rows = conn.execute(
        """SELECT id AS request_id, leave_type, start_date AS start, end_date AS end,
                  days, status, reason
           FROM leave_requests WHERE employee_id = ? ORDER BY id""",
        (employee_id,),
    ).fetchall()
    conn.close()
    return {"my_requests": [dict(r) for r in rows]}


def get_pending_requests(manager_id):
    manager = get_employee(manager_id)
    if manager is None:
        return {"error": "Employee not found"}
    if manager["role"] != "manager":
        return {"error": "Only managers can view team leave requests"}
    conn = get_conn()
    rows = conn.execute(
        """SELECT r.id AS request_id, e.name AS employee_name, r.employee_id,
                  r.leave_type, r.start_date AS start, r.end_date AS end, r.days,
                  e.casual_balance, e.sick_balance, e.paid_balance
           FROM leave_requests r JOIN employees e ON e.id = r.employee_id
           WHERE r.manager_id = ? AND r.status = ?
           ORDER BY r.id""",
        (manager_id, PENDING),
    ).fetchall()
    conn.close()
    requests = [dict(r) for r in rows]
    for r in requests:
        if r["leave_type"] in LEAVE_TYPES:           # floating holidays have no balance
            r["balance_after_approval"] = clean_days(r[f"{r['leave_type']}_balance"] - r["days"])
    return {"pending_requests": requests}


def decide_leave(manager_id, request_id, decision, reason=""):
    if decision not in ("approve", "reject"):
        return {"error": "Decision must be 'approve' or 'reject'"}
    if decision == "reject" and not reason:
        return {"error": "A reason is required to reject a request"}

    conn = get_conn()
    try:
        req = conn.execute("SELECT * FROM leave_requests WHERE id = ?", (request_id,)).fetchone()
        if req is None:
            return {"error": f"Request {request_id} not found"}
        # only this employee's own manager can decide
        if req["manager_id"] != manager_id:
            return {"error": "You are not the manager for this request"}
        # only pending requests can be decided, so nothing is approved twice
        if req["status"] != PENDING:
            return {"error": f"Request is already {req['status']}"}

        if decision == "reject":
            with conn:
                conn.execute("UPDATE leave_requests SET status = 'rejected', reason = ? WHERE id = ?",
                             (reason, request_id))
            return {"success": True, "request_id": request_id, "status": "rejected", "reason": reason}

        if req["leave_type"] == FLOATER:   # floating holidays use a yearly quota, not a balance
            with conn:
                conn.execute("UPDATE leave_requests SET status = 'approved' WHERE id = ?", (request_id,))
            return {"success": True, "request_id": request_id, "status": "approved"}

        # approve: deduct the balance and mark approved together, or not at all
        column = f"{req['leave_type']}_balance"  # safe: leave_type was checked when the request was created
        with conn:
            cur = conn.execute(
                f"UPDATE employees SET {column} = ROUND({column} - ?, 2) WHERE id = ? AND {column} >= ?",
                (req["days"], req["employee_id"], req["days"]),
            )
            if cur.rowcount == 0:
                return {"error": f"Employee no longer has enough {req['leave_type']} leave"}
            conn.execute("UPDATE leave_requests SET status = 'approved' WHERE id = ?", (request_id,))
        new_balance = conn.execute(f"SELECT {column} FROM employees WHERE id = ?",
                                   (req["employee_id"],)).fetchone()[0]
        return {"success": True, "request_id": request_id, "status": "approved",
                "employee_new_balance": clean_days(new_balance)}
    finally:
        conn.close()


def get_team_member_balance(manager_id, employee_id):
    emp = get_employee(employee_id)
    # same error whether the person doesn't exist or isn't on your team,
    # so nobody can use this tool to discover who works here
    if emp is None or emp["manager_id"] != manager_id:
        return {"error": "You can only view balances for people who report to you"}
    return {
        "employee_id": employee_id,
        "name": emp["name"],
        "leave_balance": balance_of(emp),
    }


def cancel_leave(employee_id, request_id):
    conn = get_conn()
    try:
        req = conn.execute("SELECT * FROM leave_requests WHERE id = ?", (request_id,)).fetchone()
        # same answer whether it doesn't exist or isn't hers, so nothing leaks
        if req is None or req["employee_id"] != employee_id:
            return {"cancelled": False, "message": "No such request found for you."}

        if req["status"] == PENDING:
            with conn:
                conn.execute("UPDATE leave_requests SET status = 'cancelled' WHERE id = ?", (request_id,))
            return {"cancelled": True, "days_returned": 0, "message": "Pending request withdrawn."}

        if req["status"] == "approved":
            if date.fromisoformat(req["start_date"]) <= app_clock.today():
                return {"cancelled": False,
                        "message": "This leave has already started. Please contact your manager or HR."}
            if req["leave_type"] == FLOATER:
                with conn:
                    conn.execute("UPDATE leave_requests SET status = 'cancelled' WHERE id = ?", (request_id,))
                return {"cancelled": True, "days_returned": 0,
                        "message": "Floating holiday cancelled. You can choose another one."}
            column = f"{req['leave_type']}_balance"
            with conn:  # give the days back and cancel together, or not at all
                conn.execute("UPDATE leave_requests SET status = 'cancelled' WHERE id = ?", (request_id,))
                conn.execute(f"UPDATE employees SET {column} = ROUND({column} + ?, 2) WHERE id = ?",
                             (req["days"], employee_id))
            balance_after = clean_days(conn.execute(f"SELECT {column} FROM employees WHERE id = ?", (employee_id,)).fetchone()[0])
            return {"cancelled": True, "days_returned": clean_days(req["days"]), "balance_after": balance_after,
                    "message": "Approved leave cancelled and days returned to your balance."}

        return {"cancelled": False, "message": f"This request is already {req['status']}."}
    finally:
        conn.close()


def cancel_problem(employee_id, request_id):
    """Why this person cannot cancel this request, or None when they can: the same rules as cancel_leave, without changing anything. The
    confirmation card is checked with it BEFORE it is shown, so a request that would fail is never on a card (a test keeps the two in step)."""
    conn = get_conn()
    try:
        req = conn.execute("SELECT * FROM leave_requests WHERE id = ?", (request_id,)).fetchone()
    finally:
        conn.close()
    if req is None or req["employee_id"] != employee_id:
        return "No such request found for you."
    if req["status"] == PENDING:
        return None
    if req["status"] == "approved":
        return "This leave has already started. Please contact your manager or HR." if date.fromisoformat(req["start_date"]) <= app_clock.today() else None
    return f"This request is already {req['status']}."


def decide_problem(manager_id, request_id, decision, reason=""):
    """Why this manager cannot make this decision, or None when they can: the same rules as decide_leave, without changing anything."""
    if decision not in ("approve", "reject"):
        return "Decision must be 'approve' or 'reject'"
    if decision == "reject" and not reason:
        return "A reason is required to reject a request"
    conn = get_conn()
    try:
        req = conn.execute("SELECT * FROM leave_requests WHERE id = ?", (request_id,)).fetchone()
        if req is None:
            return f"Request {request_id} not found"
        if req["manager_id"] != manager_id:
            return "You are not the manager for this request"
        if req["status"] != PENDING:
            return f"Request is already {req['status']}"
        if decision == "approve" and req["leave_type"] != FLOATER:
            column = f"{req['leave_type']}_balance"
            if conn.execute(f"SELECT {column} FROM employees WHERE id = ?", (req["employee_id"],)).fetchone()[0] < req["days"]:
                return f"Employee no longer has enough {req['leave_type']} leave"
    finally:
        conn.close()
    return None


# ---------- NEW: HR tickets ----------

def create_hr_ticket(employee_id, question, summary):
    question, summary = (question or "").strip(), (summary or "").strip()
    if not question or not summary:
        return {"error": "A ticket needs both the question and a short summary"}
    if get_employee(employee_id) is None:
        return {"error": "Employee not found"}
    conn = get_conn()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO hr_tickets (employee_id, question, summary) VALUES (?, ?, ?)",
                (employee_id, question, summary),
            )
        return {"success": True, "id": cur.lastrowid, "ticket": ticket_number(cur.lastrowid),
                "status": "open", "message": "Ticket sent to HR. They will reply here."}
    finally:
        conn.close()


def get_my_tickets(employee_id):
    conn = get_conn()
    rows = conn.execute(
        """SELECT id, question, summary, status, hr_reply, created_at
           FROM hr_tickets WHERE employee_id = ? ORDER BY id""",
        (employee_id,),
    ).fetchall()
    conn.close()
    return {"tickets": [{**dict(r), "ticket": ticket_number(r["id"])} for r in rows]}


def get_open_tickets(hr_id):
    staff = get_employee(hr_id)
    if staff is None or staff["role"] != "hr":
        return {"error": "Only HR can view tickets"}
    conn = get_conn()
    rows = conn.execute(
        """SELECT t.id, e.name AS employee_name, t.employee_id, t.question, t.summary, t.created_at
           FROM hr_tickets t JOIN employees e ON e.id = t.employee_id
           WHERE t.status = 'open' ORDER BY t.id""",
    ).fetchall()
    conn.close()
    return {"tickets": [{**dict(r), "ticket": ticket_number(r["id"])} for r in rows]}


def reply_to_ticket(hr_id, ticket_id, reply):
    staff = get_employee(hr_id)
    if staff is None or staff["role"] != "hr":
        return {"error": "Only HR can reply to tickets"}
    reply = (reply or "").strip()
    if not reply:
        return {"error": "The reply cannot be empty"}
    conn = get_conn()
    try:
        with conn:
            # only an open ticket can be answered, so nothing is replied to twice
            cur = conn.execute(
                "UPDATE hr_tickets SET status = 'closed', hr_reply = ? WHERE id = ? AND status = 'open'",
                (reply, ticket_id),
            )
        if cur.rowcount == 0:
            return {"error": f"Ticket {ticket_number(ticket_id)} is not open or does not exist"}
        return {"success": True, "ticket": ticket_number(ticket_id), "status": "closed"}
    finally:
        conn.close()


# test block: resets the database, then runs the full flow
if __name__ == "__main__":
    init_db(reset=True)
    print(apply_leave("E001", "casual", "2026-11-17", "2026-11-18"))
    print(apply_leave("E001", "casual", "2026-11-17", "2026-11-18"))  # duplicate: should be blocked
    print(get_pending_requests("M001"))
    print(decide_leave("M001", 1, "approve"))
    print(decide_leave("M001", 1, "approve"))  # second approval: should be blocked
    print(get_my_requests("E001"))
    print(cancel_leave("E001", 1))  # approved, not started: days come back
    print(get_leave_balance("E001"))  # casual should be back to 8