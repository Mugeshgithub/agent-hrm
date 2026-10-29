# tools.py
from datetime import date, timedelta
from db import get_conn, init_db, ticket_number

HOLIDAYS = {date(2026, 11, 8), date(2026, 12, 25)}  # sample public holidays
LEAVE_TYPES = ("casual", "sick")
PENDING = "pending_manager_approval"


def get_employee(employee_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM employees WHERE id = ?", (employee_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_leave_balance(employee_id):
    emp = get_employee(employee_id)
    if emp is None:
        return {"error": "Employee not found"}
    return {
        "name": emp["name"],
        "leave_balance": {"casual": emp["casual_balance"], "sick": emp["sick_balance"]},
    }


def count_working_days(start, end):
    days = 0
    current = start
    while current <= end:
        if current.weekday() < 5 and current not in HOLIDAYS:
            days += 1
        current += timedelta(days=1)
    return days


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
    if start < date.today():
        return {"error": "Start date is in the past"}

    conn = get_conn()
    try:
        # block duplicates: no overlap with an existing pending or approved request
        overlap = conn.execute(
            """SELECT id, status, start_date, end_date FROM leave_requests
               WHERE employee_id = ? AND status IN (?, 'approved')
                 AND start_date <= ? AND end_date >= ?""",
            (employee_id, PENDING, end.isoformat(), start.isoformat()),
        ).fetchone()
        if overlap:
            return {"error": f"You already have request {overlap['id']} ({overlap['status']}) "
                             f"covering {overlap['start_date']} to {overlap['end_date']}. "
                             f"No new request was created."}

        days = count_working_days(start, end)
        if days == 0:
            return {"error": "No working days in that range"}

        # days already requested but not yet approved also count against the balance
        pending_days = conn.execute(
            """SELECT COALESCE(SUM(days), 0) FROM leave_requests
               WHERE employee_id = ? AND leave_type = ? AND status = ?""",
            (employee_id, leave_type, PENDING),
        ).fetchone()[0]
        balance = emp[f"{leave_type}_balance"] - pending_days
        if days > balance:
            return {"error": f"Not enough {leave_type} leave: requested {days}, available {balance}. "
                             f"Saved balance is {emp[f'{leave_type}_balance']}, but {pending_days} days "
                             f"are already in pending requests waiting for approval."}

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
            "current_balance": {"casual": emp["casual_balance"], "sick": emp["sick_balance"]},
            "balance_after_approval": {leave_type: balance - days},
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
                  e.casual_balance, e.sick_balance
           FROM leave_requests r JOIN employees e ON e.id = r.employee_id
           WHERE r.manager_id = ? AND r.status = ?
           ORDER BY r.id""",
        (manager_id, PENDING),
    ).fetchall()
    conn.close()
    return {"pending_requests": [dict(r) for r in rows]}


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

        # approve: deduct the balance and mark approved together, or not at all
        column = f"{req['leave_type']}_balance"  # safe: leave_type was checked when the request was created
        with conn:
            cur = conn.execute(
                f"UPDATE employees SET {column} = {column} - ? WHERE id = ? AND {column} >= ?",
                (req["days"], req["employee_id"], req["days"]),
            )
            if cur.rowcount == 0:
                return {"error": f"Employee no longer has enough {req['leave_type']} leave"}
            conn.execute("UPDATE leave_requests SET status = 'approved' WHERE id = ?", (request_id,))
        new_balance = conn.execute(f"SELECT {column} FROM employees WHERE id = ?",
                                   (req["employee_id"],)).fetchone()[0]
        return {"success": True, "request_id": request_id, "status": "approved",
                "employee_new_balance": new_balance}
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
        "leave_balance": {"casual": emp["casual_balance"], "sick": emp["sick_balance"]},
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
            if date.fromisoformat(req["start_date"]) <= date.today():
                return {"cancelled": False,
                        "message": "This leave has already started. Please contact your manager or HR."}
            column = f"{req['leave_type']}_balance"
            with conn:  # give the days back and cancel together, or not at all
                conn.execute("UPDATE leave_requests SET status = 'cancelled' WHERE id = ?", (request_id,))
                conn.execute(f"UPDATE employees SET {column} = {column} + ? WHERE id = ?",
                             (req["days"], employee_id))
            return {"cancelled": True, "days_returned": req["days"],
                    "message": "Approved leave cancelled and days returned to your balance."}

        return {"cancelled": False, "message": f"This request is already {req['status']}."}
    finally:
        conn.close()


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